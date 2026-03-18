from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError
from datetime import date


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
    days = fields.Integer(string='Días', compute='_compute_days', store=True)

    # Según Código de Trabajo CR: 2 semanas (12 días hábiles) por año trabajado
    days_accrued = fields.Float(
        string='Días Disponibles',
        compute='_compute_days_accrued',
        help='Días de vacaciones disponibles según hr_employee (descuenta vacaciones tomadas)'
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

    # ── Método de pago Art. 153-156 CT ─────────────────────────────
    payment_method = fields.Selection([
        ('disfrutadas', 'Días disfrutados (descuento de salario)'),
        ('dinero',      'Pago en dinero (Art. 156 CT — acuerdo mutuo)'),
        ('mixto',       'Mixto: parte disfrutada + parte en dinero'),
    ], string='Método de Pago', default='disfrutadas', tracking=True,
        help='Art. 156 CT: solo se pueden pagar en dinero con acuerdo de ambas partes')

    # Calculo promedio ultimas 4 semanas (Art. 153 CT)
    # FIX NEW-05 v54: avg_last_4_weeks ahora es compute (antes era manual).
    # Se calcula automaticamente como el promedio de los ultimos 4 registros
    # de salary_history del empleado. Si no hay historial, cae al salario diario normal.
    avg_last_4_weeks = fields.Monetary(
        string='Promedio Ultimas 4 Semanas', currency_field='currency_id',
        compute='_compute_avg_last_4_weeks', store=True, readonly=False,
        help='Promedio del salario de las ultimas 4 semanas (Art. 153 CT). '
             'Se calcula automaticamente desde el historial salarial. '
             'Puede editarse manualmente si incluye HE, comisiones u otros ingresos variables.'
    )
    use_average = fields.Boolean(
        string='Usar Promedio 4 Semanas',
        default=False,
        help='Si el empleado tuvo HE, comisiones u otros ingresos variables, '
             'activar para calcular con el promedio Art. 153 CT'
    )

    @api.depends('employee_id', 'date_start')
    def _compute_avg_last_4_weeks(self):
        """FIX NEW-05 v54: calcula el promedio salarial de las ultimas 4 semanas
        desde salary_history. Toma los 4 registros autorizados mas recientes antes
        de la fecha de inicio de vacaciones.
        """
        for rec in self:
            if not rec.employee_id or not rec.date_start:
                rec.avg_last_4_weeks = 0.0
                continue
            history = self.env['planilla.salary.history'].search([
                ('employee_id', '=', rec.employee_id.id),
                ('effective_date', '<=', rec.date_start),
                ('state', '=', 'authorized'),
            ], order='effective_date desc', limit=4)
            if history:
                salaries = [h.gross_salary or h.salary or 0.0 for h in history]
                # Promedio semanal = (salario mensual * 12 meses) / 52 semanas
                avg_monthly = sum(salaries) / len(salaries)
                avg_weekly = avg_monthly * 12 / 52
                rec.avg_last_4_weeks = round(avg_weekly, 2)
            else:
                # Sin historial: usar el salario base actual como referencia
                base = rec.employee_id.base_salary or 0.0
                rec.avg_last_4_weeks = round(base * 12 / 52, 2)
    days_in_money = fields.Integer(
        string='Días a Pagar en Dinero',
        help='Para tipo Mixto: días que se pagan en efectivo (Art. 156 CT)'
    )
    vacation_income_payslip = fields.Boolean(
        string='Incluir en Boleta como Ingreso',
        default=True,
        help='Al aprobar, se agrega automáticamente como ingreso adicional en la boleta'
    )

    payslip_id = fields.Many2one('planilla.payslip.cr', string='Boleta de Pago')
    move_id = fields.Many2one('account.move', string='Asiento Contable', readonly=True)
    note = fields.Text(string='Observaciones')

    # ── BUG #5 FIX v50: action_approve unificado con validación ────
    def action_approve(self):
        """
        BUG #5 FIX v50: action_approve ahora valida días disponibles,
        igual que action_approve_and_pay. Elimina la posibilidad de evadir
        la validación usando el botón simple de aprobación.
        FIX A-01 v59: Agregar validación para tipo "adelanto" (sin límite anterior).
        """
        for rec in self:
            if rec.state != 'draft':
                raise UserError('Solo se pueden aprobar vacaciones en borrador.')
            # Validar días disponibles según tipo
            if rec.vacation_type in ('disfrutadas', 'proporcionales'):
                if rec.days > rec.days_accrued:
                    raise ValidationError(
                        f'El empleado {rec.employee_id.name} tiene {rec.days_accrued:.1f} días '
                        f'disponibles pero solicita {rec.days} días.'
                    )
            elif rec.vacation_type == 'adelanto':
                # FIX A-01 v59: Adelanto máximo = días anuales (Art. 153 CT: 12 días/50 semanas)
                MAX_ADELANTO = 12
                if rec.days > MAX_ADELANTO:
                    raise ValidationError(
                        f'El adelanto de vacaciones ({rec.days} días) supera el máximo '
                        f'permitido de {MAX_ADELANTO} días anuales (Art. 153 CT). '
                        f'Consulte con RRHH si requiere una excepción documentada.'
                    )

            rec.state = 'approved'
            # FIX A-01 v53: Modelo correcto planilla.payslip.cr (no planilla.payslip).
            # El modelo incorrecto generaba KeyError al aprobar vacaciones cuando
            # había boletas abiertas del empleado.
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

            # BUG #1 FIX v50: Verificar días disponibles usando vacation_days_available
            # (campo calculado en hr_employee que descuenta vacaciones ya tomadas)
            if rec.days > rec.days_accrued:
                raise ValidationError(
                    f'El empleado {rec.employee_id.name} tiene {rec.days_accrued:.1f} días '
                    f'disponibles pero solicita {rec.days} días.'
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
                            'description': f'Pago vacaciones — {rec.days} días (Art. 156 CT)',
                            'line_type': 'income',
                            'deduction_category': 'vacation',
                            'amount_type': 'fixed',
                            'amount': rec.total_amount,
                        })

            # BUG #2 FIX v50: Generar asiento contable para pagos en dinero (Art. 156 CT)
            # Vacaciones pagadas en dinero generan gasto real, no solo provisión
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
        # Sin este fix, en empresas con múltiples compañías se obtenía la config
        # de env.company (la activa en sesión) en vez de la del empleado.
        config = self.env['planilla.accounting.config'].get_config(
            self.employee_id.company_id.id if self.employee_id.company_id else None
        )
        if not config or not config.journal_id:
            self.message_post(
                body='<b>Aviso:</b> No se generó asiento contable de vacaciones porque '
                     'no hay configuración contable. Vaya a Planilla → Configuración → Contabilidad.',
                message_type='notification',
            )
            return False

        exp_account = config.account_vacation_expense
        pay_account = config.account_salary_payable
        if not exp_account or not pay_account:
            self.message_post(
                body='<b>Aviso:</b> Faltan cuentas contables para vacaciones '
                     '(630200 Vacaciones o 230000 Salarios por Pagar). '
                     'Use el botón ⚡ Autocompletar en Configuración → Contabilidad.',
                message_type='notification',
            )
            return False

        emp = self.employee_id.name
        amount = round(self.total_amount, 2)
        lines = [
            (0, 0, {
                'account_id': exp_account.id,
                'name': f'Vacaciones en dinero — {emp} — {self.days} días (Art. 156 CT)',
                'debit': amount,
                'credit': 0.0,
            }),
            (0, 0, {
                'account_id': pay_account.id,
                'name': f'Vacaciones por pagar — {emp}',
                'debit': 0.0,
                'credit': amount,
            }),
        ]

        move = self.env['account.move'].create({
            'journal_id': config.journal_id.id,
            'date': self.date_start or fields.Date.today(),
            'ref': f'Vacaciones Art. 156 CT — {emp} — {self.name}',
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
        ya tomadas y pagadas. El cálculo anterior (years * 12) era incorrecto
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
        Para tipo mixto: días_en_dinero * tarifa + resto días disfrutados.
        """
        for rec in self:
            # Tarifa base: promedio 4 semanas (Art. 153 CT) o salario diario
            if rec.use_average and rec.avg_last_4_weeks > 0:
                base_rate = rec.avg_last_4_weeks
            else:
                base_rate = rec.daily_salary

            if rec.payment_method == 'mixto' and rec.days_in_money > 0:
                # Pago mixto: días en dinero + días disfrutados
                money_days = min(rec.days_in_money, rec.days)
                rec.total_amount = round(money_days * base_rate, 2)
            else:
                rec.total_amount = round(rec.days * base_rate, 2)

    def action_cancel(self):
        self.write({'state': 'cancelled'})

    def write(self, vals):
        # FIX B-01 v53: Al cambiar estado (approved → cancelled / draft → approved),
        # invalidar vacation_days_available en el empleado para que el saldo
        # se recalcule de inmediato en la UI sin esperar el siguiente cron.
        res = super().write(vals)
        if 'state' in vals:
            employees = self.mapped('employee_id')
            employees._compute_vacation_balance()
        return res
