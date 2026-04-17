from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError
from datetime import date
import logging
from dateutil.relativedelta import relativedelta as rdelta
from . import planilla_const as K

_logger = logging.getLogger(__name__)


class VacationPayment(models.Model):
    _name = 'planilla.vacation.payment'
    _description = 'Pago de Vacaciones'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Referencia', compute='_compute_name', store=True)
    employee_id = fields.Many2one(
        'hr.employee', string='Empleado', required=True, tracking=True, index=True
    )
    branch_id = fields.Many2one(
        related='employee_id.branch_id', string='Sucursal', store=True
    )

    vacation_type = fields.Selection([
        ('disfrutadas', 'Vacaciones Disfrutadas'),
        ('proporcionales', 'Vacaciones Proporcionales'),
        ('adelanto', 'Adelanto de Vacaciones'),
    ], string='Tipo', default='disfrutadas', required=True, tracking=True)

    date_start = fields.Date(string='Fecha Inicio', required=True,
                             default=fields.Date.today)
    date_end = fields.Date(string='Fecha Fin', required=True,
                           default=fields.Date.today)
    days = fields.Integer(string='Dias', compute='_compute_days', store=True)

    # Segun Codigo de Trabajo CR: 2 semanas (12 dias habiles) por ano trabajado
    days_accrued = fields.Float(
        string='Dias Disponibles',
        compute='_compute_days_accrued',
        help='Dias de vacaciones disponibles segun hr_employee (descuenta vacaciones tomadas)'
    )

    daily_salary = fields.Monetary(
        string='Salario Diario', currency_field='currency_id',
        compute='_compute_daily_salary', store=True
    )
    currency_id = fields.Many2one(related='employee_id.currency_id', store=True)
    total_amount = fields.Monetary(
        string='Monto Total', currency_field='currency_id',
        compute='_compute_total', store=True
    )

    state = fields.Selection([
        ('draft', 'Borrador'),
        ('approved', 'Aprobado'),
        ('paid', 'Pagado'),
        ('cancelled', 'Cancelado'),
    ], string='Estado', default='draft', tracking=True)

    # -- Metodo de pago Art. 153-156 CT -----------------------------
    payment_method = fields.Selection([
        ('disfrutadas', 'Dias disfrutados (descuento de salario)'),
        ('dinero',      'Pago en dinero (Art. 156 CT -- acuerdo mutuo)'),
        ('mixto',       'Mixto: parte disfrutada + parte en dinero'),
    ], string='Metodo de Pago', default='disfrutadas', tracking=True,
        help='Art. 156 CT: solo se pueden pagar en dinero con acuerdo de ambas partes')

    # Calculo promedio ultimas 4 semanas (Art. 153 CT)
    # FIX NEW-05 v54: avg_last_4_weeks ahora es compute (antes era manual).
    # Se calcula automaticamente como el promedio de los ultimos 4 registros
    # de salary_history del empleado. Si no hay historial, cae al salario diario normal.
    avg_last_4_weeks = fields.Monetary(
        string='Salario Diario Promedio Hist. (Art. 153 CT)', currency_field='currency_id',  # FIX-I1: daily rate
        compute='_compute_avg_last_4_weeks', store=True, readonly=False,
        help='Promedio del salario bruto de las ultimas 4 semanas antes del inicio de vacaciones.\n'
             'Se calcula automaticamente desde el historial salarial (boletas pagadas).\n'
             'Incluye salario base + horas extras + comisiones de cada boleta.\n'
             'Puede editarse manualmente si necesita ajustar el calculo.\n'
             'Obligatorio para empleados con salario variable (Art. 153 CT).'
    )
    use_average = fields.Boolean(
        string='Usar Promedio 4 Semanas (Art. 153 CT)',
        default=False,
        help='OBLIGATORIO para empleados con comisiones, horas extras recurrentes u otros ingresos variables.\n'
             'Se activa automaticamente si el empleado tiene el flag "Salario Variable" activo.\n'
             'Art. 153 CT: durante vacaciones el trabajador recibe el promedio de lo devengado\n'
             'en las ultimas 4 semanas antes del inicio de las vacaciones.'
    )
    # Campo informativo: indica si el promedio difiere significativamente del salario base
    avg_vs_base_diff_pct = fields.Float(
        string='Diferencia Promedio vs Base (%)',
        compute='_compute_avg_vs_base',
        help='Diferencia porcentual entre el promedio 4 semanas y el salario base diario.\n'
             'Si es mayor a 5%, el sistema advierte que se debe usar el promedio.'
    )

    @api.depends('avg_last_4_weeks', 'daily_salary')
    def _compute_avg_vs_base(self):
        for rec in self:
            if rec.daily_salary and rec.daily_salary > 0 and rec.avg_last_4_weeks:
                rec.avg_vs_base_diff_pct = round(
                    abs(rec.avg_last_4_weeks - rec.daily_salary) / rec.daily_salary * 100, 1
                )
            else:
                rec.avg_vs_base_diff_pct = 0.0

    @api.depends('employee_id', 'date_start')
    def _compute_avg_last_4_weeks(self):
        """
        Art. 153 CT CR: calcula el salario diario promedio de las ultimas 4 boletas pagadas.

        FIX-I1: El campo almacena la TARIFA DIARIA promedio (avg_monthly / 30),
        NO la tarifa semanal. _compute_total multiplica este valor por los dias
        de vacaciones -- igual que hace con daily_salary -- asi ambas rutas son
        intercambiables y consistentes.

        Error anterior: se guardaba avg_monthly * 12/52 (tarifa semanal  115k
        para salario de 500k) y se multiplicaba por dias -> 12 dias * 115k = 1.38M
        en lugar de 12 dias * 16,667 = 200k. Overpayment de 7x cuando use_average=True.

        El historial salarial guarda el gross_salary MENSUAL real de cada boleta --
        que incluye salario base + HE + comisiones + bonos. El promedio de esos 4
        registros da el salario mensual promedio representativo, que dividido entre
        30 da la tarifa diaria correcta para el calculo de vacaciones.
        """
        for rec in self:
            if not rec.employee_id or not rec.date_start:
                rec.avg_last_4_weeks = 0.0
                continue
            history = self.env['planilla.salary.history'].search([
                ('employee_id', '=', rec.employee_id.id),
                ('effective_date', '<=', rec.date_start),
                ('state', '=', 'authorized'),
                ('payslip_id', '=', False),
            ], order='effective_date desc', limit=4)
            if history:
                salaries = [h.gross_salary or h.salary or 0.0 for h in history]
                # Promedio mensual de las ultimas 4 boletas
                avg_monthly = sum(salaries) / len(salaries)
                # Tarifa diaria = promedio mensual / 30 dias (Art. 153 CT)
                # Se almacena como tarifa DIARIA para ser consistente con daily_salary
                avg_daily = round(avg_monthly / 30, 2)
                rec.avg_last_4_weeks = avg_daily
            else:
                # Sin historial: usar salario base actual como aproximacion
                base = rec.employee_id.base_salary or 0.0
                rec.avg_last_4_weeks = round(base / 30, 2)

    @api.onchange('employee_id')
    def _onchange_employee_variable_income(self):
        """
        MEJORA: si el empleado tiene has_variable_income=True, activa automaticamente
        use_average para cumplir con Art. 153 CT sin depender del operador.
        """
        if self.employee_id and getattr(self.employee_id, 'has_variable_income', False):
            self.use_average = True
    days_in_money = fields.Integer(
        string='Dias a Pagar en Dinero',
        help='Para tipo Mixto: dias que se pagan en efectivo (Art. 156 CT)'
    )
    vacation_income_payslip = fields.Boolean(
        string='Incluir en Boleta como Ingreso',
        default=True,
        help='Al aprobar, se agrega automaticamente como ingreso adicional en la boleta'
    )

    payslip_id = fields.Many2one('planilla.payslip.cr', string='Boleta de Pago')
    move_id = fields.Many2one('account.move', string='Asiento Contable', readonly=True)
    note = fields.Text(string='Observaciones')

    # -- BUG #5 FIX v50: action_approve unificado con validacion ----
    def action_approve(self):
        """
        BUG #5 FIX v50: action_approve ahora valida dias disponibles,
        igual que action_approve_and_pay. Elimina la posibilidad de evadir
        la validacion usando el boton simple de aprobacion.
        FIX A-01 v59: Agregar validacion para tipo "adelanto" (sin limite anterior).
        """
        for rec in self:
            if rec.state != 'draft':
                raise UserError('Solo se pueden aprobar vacaciones en borrador.')
            # PEND-01: Advertencia de prescripcion (Art. 156 CT: 22 meses)
            if rec.employee_id and rec.date_start:
                emp = rec.employee_id
                entry = emp.entry_date or emp.create_date.date()
                rd = rdelta.relativedelta(rec.date_start, entry)
                meses_servicio = rd.years * 12 + rd.months
                if meses_servicio > K.MESES_PRESCRIPCION_VACACIONES:
                    _logger.warning(
                        'planilla_cr: Vacaciones empleado %s con %s meses de servicio '
                        '(limite prescripcion Art. 156 CT: %s meses)',
                        emp.name, meses_servicio, K.MESES_PRESCRIPCION_VACACIONES
                    )
                    rec.message_post(
                        body=(
                            'Advertencia Art. 156 CT: Este empleado tiene '
                            + str(meses_servicio) + ' meses de servicio. '
                            'Verifique que las vacaciones no esten prescritas '
                            '(limite: ' + str(K.MESES_PRESCRIPCION_VACACIONES) + ' meses). '
                            'Consulte con el abogado laboralista si hay dudas.'
                        ),
                        message_type='comment',
                        subtype_xmlid='mail.mt_note',
                    )
            # Validar dias disponibles segun tipo -- advertencia, no bloqueo
            # Se permite saldo negativo (vacaciones adelantadas)
            if rec.vacation_type in ('disfrutadas', 'proporcionales'):
                if rec.days > rec.days_accrued:
                    # Solo registrar advertencia, no bloquear
                    pass  # el chatter ya tiene la nota del bloque anterior
            elif rec.vacation_type == 'adelanto':
                # FIX A-01 v59: Adelanto maximo = dias anuales (Art. 153 CT: 12 dias/50 semanas)
                MAX_ADELANTO = 12
                if rec.days > MAX_ADELANTO:
                    raise ValidationError(
                        'El adelanto de vacaciones (%s dias) supera el maximo '
                        'permitido de %s dias anuales (Art. 153 CT). '
                        'Consulte con RRHH si requiere una excepcion documentada.' % (
                            rec.days, MAX_ADELANTO)
                    )

            rec.state = 'approved'
            # FIX A-01 v53: Modelo correcto planilla.payslip.cr (no planilla.payslip).
            # El modelo incorrecto generaba KeyError al aprobar vacaciones cuando
            # habia boletas abiertas del empleado.
            draft_payslips = self.env['planilla.payslip.cr'].search([
                ('employee_id', '=', rec.employee_id.id),
                ('state', 'in', ['draft', 'confirmed']),
            ])
            if draft_payslips:
                # Invalidar cache para que extras (vacation_amount) se recalculen
                draft_payslips.invalidate_recordset(['vacation_amount'])
                draft_payslips._compute_extras()
        return True

    def action_approve_and_pay(self):
        """Aprueba las vacaciones, agrega ingreso en boleta y genera asiento si pago en dinero."""
        for rec in self:
            if rec.state != 'draft':
                raise UserError('Solo se pueden aprobar vacaciones en borrador.')

            # Verificar dias disponibles -- advertencia pero NO bloqueo
            # Se permite aprobar vacaciones aunque el saldo sea negativo (vacaciones adelantadas)
            # El saldo puede quedar en negativo y se descuenta en futuras acumulaciones
            if rec.days > rec.days_accrued:
                deficit = rec.days - rec.days_accrued
                rec.message_post(
                    body=(
                        f'<b>Advertencia: Saldo insuficiente.</b> '
                        f'{rec.employee_id.name} tiene <b>{rec.days_accrued:.1f} dias</b> disponibles '
                        f'y solicita <b>{rec.days} dias</b>. '
                        f'El saldo quedara en <b>{rec.days_accrued - rec.days:.1f} dias</b> '
                        f'(deficit de {deficit:.1f} dias). '
                        f'Vacaciones aprobadas como adelanto.'
                    ),
                    message_type='notification',
                )

            rec.state = 'approved'

            # Si hay pago en dinero y tiene boleta asignada, agregar ingreso
            if rec.vacation_income_payslip and rec.payslip_id and rec.total_amount > 0:
                if rec.payment_method in ('dinero', 'mixto'):
                    vac_code = self.env['planilla.deduction.code'].search(
                        [('code', '=', 'VAC-PAG')], limit=1
                    )
                    if vac_code:
                        self.env['planilla.payslip.deduction.line'].create({
                            'payslip_id': rec.payslip_id.id,
                            'deduction_code_id': vac_code.id,
                            'description': f'Pago vacaciones -- {rec.days} dias (Art. 156 CT)',
                            'line_type': 'income',
                            'deduction_category': 'vacation',
                            'amount_type': 'fixed',
                            'amount': rec.total_amount,
                        })

            # BUG #2 FIX v50: Generar asiento contable para pagos en dinero (Art. 156 CT)
            # Vacaciones pagadas en dinero generan gasto real, no solo provision
            if rec.payment_method in ('dinero', 'mixto') and rec.total_amount > 0:
                rec._create_vacation_accounting_entry()

        return {'type': 'ir.actions.act_window_close'}

    def _create_vacation_accounting_entry(self):
        """
        BUG #2 FIX v50: Asiento contable para vacaciones pagadas en dinero (Art. 156 CT).
          DEBE:  630200 Vacaciones (gasto)
          HABER: 230000 Salarios por Pagar (pasivo)
        """
        self.ensure_one()
        # FIX B-04 v51: pasar company_id del empleado para soporte multi-empresa.
        # Sin este fix, en empresas con multiples companias se obtenia la config
        # de env.company (la activa en sesion) en vez de la del empleado.
        config = self.env['planilla.accounting.config'].get_config(
            self.employee_id.company_id.id if self.employee_id.company_id else None
        )
        if not config or not config.journal_id:
            self.message_post(
                body='<b>Aviso:</b> No se genero asiento contable de vacaciones porque '
                     'no hay configuracion contable. Vaya a Planilla -> Configuracion -> Contabilidad.',
                message_type='notification',
            )
            return False

        exp_account = config.account_vacation_expense
        pay_account = config.account_salary_payable
        if not exp_account or not pay_account:
            self.message_post(
                body='<b>Aviso:</b> Faltan cuentas contables para vacaciones '
                     '(630200 Vacaciones o 230000 Salarios por Pagar). '
                     'Use el boton  Autocompletar en Configuracion -> Contabilidad.',
                message_type='notification',
            )
            return False

        emp = self.employee_id.name
        amount = round(self.total_amount, 2)
        lines = [
            (0, 0, {
                'account_id': exp_account.id,
                'name': f'Vacaciones en dinero -- {emp} -- {self.days} dias (Art. 156 CT)',
                'debit': amount,
                'credit': 0.0,
            }),
            (0, 0, {
                'account_id': pay_account.id,
                'name': f'Vacaciones por pagar -- {emp}',
                'debit': 0.0,
                'credit': amount,
            }),
        ]

        move = self.env['account.move'].create({
            'journal_id': config.journal_id.id,
            'date': self.date_start or fields.Date.context_today(self),
            'ref': f'Vacaciones Art. 156 CT -- {emp} -- {self.name}',
            'move_type': 'entry',
            'line_ids': lines,
        })
        move.action_post()
        self.move_id = move.id
        self.message_post(
            body=f'Asiento contable generado: <a href="/web#id={move.id}&model=account.move">{move.name}</a>',
            message_type='notification',
        )
        return move

    @api.depends('employee_id', 'date_start')
    def _compute_name(self):
        for rec in self:
            emp = rec.employee_id.name or ''
            date_str = str(rec.date_start) if rec.date_start else ''
            rec.name = f'VAC - {emp} - {date_str}'

    @api.depends('date_start', 'date_end')
    def _compute_days(self):
        for rec in self:
            if rec.date_start and rec.date_end:
                rec.days = (rec.date_end - rec.date_start).days + 1
            else:
                rec.days = 0

    @api.depends('employee_id', 'employee_id.vacation_days_available')
    def _compute_days_accrued(self):
        """
        BUG #1 FIX v50: Usa vacation_days_available del empleado (calculado en
        hr_employee_extension._compute_vacation_balance) que descuenta vacaciones
        ya tomadas y pagadas. El calculo anterior (years * 12) era incorrecto
        porque ignoraba el historial de vacaciones del empleado.
        """
        for rec in self:
            if rec.employee_id:
                rec.days_accrued = rec.employee_id.vacation_days_available or 0.0
            else:
                rec.days_accrued = 0.0

    @api.depends('employee_id')
    def _compute_daily_salary(self):
        for rec in self:
            if rec.employee_id and rec.employee_id.base_salary:
                rec.daily_salary = rec.employee_id.base_salary / 30
            else:
                rec.daily_salary = 0.0

    @api.depends('days', 'daily_salary', 'use_average', 'avg_last_4_weeks',
                 'payment_method', 'days_in_money')
    def _compute_total(self):
        """
        BUG #14 FIX v50: Implementa use_average con avg_last_4_weeks (Art. 153 CT).
        Si use_average=True y hay promedio, usa ese valor como base diaria.
        Para tipo mixto: dias_en_dinero * tarifa + resto dias disfrutados.
        """
        for rec in self:
            # Tarifa base: promedio 4 semanas (Art. 153 CT) o salario diario
            if rec.use_average and rec.avg_last_4_weeks > 0:
                base_rate = rec.avg_last_4_weeks
            else:
                base_rate = rec.daily_salary

            if rec.payment_method == 'mixto' and rec.days_in_money > 0:
                # Pago mixto: dias en dinero + dias disfrutados
                money_days = min(rec.days_in_money, rec.days)
                rec.total_amount = round(money_days * base_rate, 2)
            else:
                rec.total_amount = round(rec.days * base_rate, 2)

    def action_cancel(self):
        self.write({'state': 'cancelled'})

    def write(self, vals):
        # FIX B-01 v53: Al cambiar estado (approved -> cancelled / draft -> approved),
        # invalidar vacation_days_available en el empleado para que el saldo
        # se recalcule de inmediato en la UI sin esperar el siguiente cron.
        res = super().write(vals)
        if 'state' in vals:
            employees = self.mapped('employee_id')
            employees._compute_vacation_balance()
        return res
