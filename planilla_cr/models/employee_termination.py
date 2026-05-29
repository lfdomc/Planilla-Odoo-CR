import logging
from odoo import models, fields, api
from . import planilla_const as K
from odoo.exceptions import UserError, ValidationError
from datetime import date
from dateutil.relativedelta import relativedelta
from .closed_period import PlanillaClosedPeriod

_logger = logging.getLogger(__name__)


class EmployeeTermination(models.Model):
    _name = 'planilla.termination'
    _description = 'Liquidacion / Finiquito de Empleado'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'termination_date desc'

    name = fields.Char(
        string='Referencia', compute='_compute_name', store=True
    )
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company
    )
    employee_id = fields.Many2one(
        'hr.employee', string='Empleado', required=True, index=True,
        domain=[('active', 'in', [True, False])]
    )
    entry_date = fields.Date(
        string='Fecha de Ingreso', required=True
    )
    termination_date = fields.Date(
        string='Fecha de Salida', required=True, default=fields.Date.today
    )
    termination_reason = fields.Selection([
        ('renuncia',       'Renuncia voluntaria'),
        ('despido_justif', 'Despido con causa justificada'),
        ('despido_injust', 'Despido sin causa justificada'),
        ('mutuo',          'Mutuo acuerdo'),
        ('contrato_vence', 'Vencimiento de contrato'),
        ('fallecimiento',  'Fallecimiento'),
    ], string='Motivo de Salida', required=True)

    # -- Salario base --------------------------------------------
    last_salary = fields.Monetary(
        string='Salario Bruto Mensual', currency_field='currency_id',
        required=True
    )
    use_salary_average = fields.Boolean(
        string='Usar Promedio Manual de Salarios',
        default=False,
        help='Active para ingresar el promedio de los ultimos 6 salarios (Art. 153 CT). '
             'Se usara en lugar del salario actual para cesantia, preaviso y vacaciones.'
    )
    salary_average_manual = fields.Monetary(
        string='Promedio 6 Meses (CRC)',
        currency_field='currency_id',
        help='Promedio mensual de los ultimos 6 salarios brutos (Art. 153 CT). '
             'NOTA: el app paga quincenalmente, sume las dos quincenas de cada mes.'
    )
    # Campos para ingresar los 6 salarios mensuales (suma de 2 quincenas cada uno)
    sal_m1 = fields.Monetary(string='Salario Mes 1 (mas reciente)',
        currency_field='currency_id',
        help='Suma de las 2 quincenas del mes mas reciente antes de la salida.')
    sal_m2 = fields.Monetary(string='Salario Mes 2', currency_field='currency_id')
    sal_m3 = fields.Monetary(string='Salario Mes 3', currency_field='currency_id')
    sal_m4 = fields.Monetary(string='Salario Mes 4', currency_field='currency_id')
    sal_m5 = fields.Monetary(string='Salario Mes 5', currency_field='currency_id')
    sal_m6 = fields.Monetary(string='Salario Mes 6 (mas antiguo)',
        currency_field='currency_id')
    sal_promedio_calc = fields.Monetary(
        string='Promedio Calculado (CRC)',
        currency_field='currency_id',
        compute='_compute_sal_promedio_term', store=False,
        help='Promedio de los meses con valor > 0 (igual que formula Excel RRHH)'
    )
    sal_meses_con_valor = fields.Integer(
        string='Meses con salario',
        compute='_compute_sal_promedio_term', store=False
    )

    @api.depends('sal_m1','sal_m2','sal_m3','sal_m4','sal_m5','sal_m6')
    def _compute_sal_promedio_term(self):
        for rec in self:
            vals = [rec.sal_m1 or 0, rec.sal_m2 or 0, rec.sal_m3 or 0,
                    rec.sal_m4 or 0, rec.sal_m5 or 0, rec.sal_m6 or 0]
            nz = [v for v in vals if v > 0]
            if nz:
                rec.sal_promedio_calc  = round(sum(nz) / len(nz), 2)
                rec.sal_meses_con_valor = len(nz)
            else:
                rec.sal_promedio_calc  = 0.0
                rec.sal_meses_con_valor = 0

    @api.onchange('sal_m1','sal_m2','sal_m3','sal_m4','sal_m5','sal_m6')
    def _onchange_sal_term(self):
        vals = [self.sal_m1 or 0, self.sal_m2 or 0, self.sal_m3 or 0,
                self.sal_m4 or 0, self.sal_m5 or 0, self.sal_m6 or 0]
        nz = [v for v in vals if v > 0]
        if nz:
            avg = round(sum(nz) / len(nz), 2)
            self.salary_average_manual = avg
            self.use_salary_average = True
            self.last_salary = avg
    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.ref('base.CRC')
    )

    # -- Anos y dias de servicio (computed) ----------------------
    years_service = fields.Float(
        string='Anos de Servicio', compute='_compute_service_time', store=True
    )
    months_service = fields.Integer(
        string='Meses de Servicio', compute='_compute_service_time', store=True
    )
    days_service = fields.Integer(
        string='Dias de Servicio', compute='_compute_service_time', store=True
    )

    # -- Componentes liquidacion ----------------------------------
    preaviso_days = fields.Integer(
        string='Dias de Preaviso', compute='_compute_preaviso', store=True
    )
    preaviso_amount = fields.Monetary(
        string='Monto Preaviso (CRC)', currency_field='currency_id',
        compute='_compute_amounts', store=True
    )
    preaviso_applies = fields.Boolean(
        string='Aplica Preaviso', default=True,
        help='Desmarcar si el empleado ya trabajo el periodo de preaviso.'
    )

    cesantia_amount = fields.Monetary(
        string='Cesantia (CRC)', currency_field='currency_id',
        compute='_compute_amounts', store=True
    )
    cesantia_applies = fields.Boolean(
        string='Aplica Cesantia', compute='_compute_cesantia_applies', store=True
    )

    # Vacaciones
    vacation_days_accrued = fields.Float(
        string='Dias Vacaciones Acumulados', compute='_compute_amounts', store=True
    )
    vacation_amount = fields.Monetary(
        string='Vacaciones Proporcionales (CRC)', currency_field='currency_id',
        compute='_compute_amounts', store=True
    )

    # Aguinaldo proporcional
    aguinaldo_amount = fields.Monetary(
        string='Aguinaldo Proporcional (CRC)', currency_field='currency_id',
        compute='_compute_amounts', store=True
    )
    aguinaldo_months = fields.Integer(
        string='Meses para Aguinaldo', compute='_compute_amounts', store=True
    )

    # Otros
    other_payments = fields.Monetary(
        string='Otros Pagos (CRC)', currency_field='currency_id',
        help='Bonos, comisiones pendientes u otros conceptos.'
    )
    other_payments_note = fields.Char(string='Descripcion otros pagos')

    deductions = fields.Monetary(
        string='Deducciones (CRC)', currency_field='currency_id',
        help='Adelantos, prestamos u otras deducciones pendientes.'
    )
    deductions_note = fields.Char(string='Descripcion deducciones')

    # -- Totales --------------------------------------------------
    # FIX A-03 v53: CCSS obrero sobre base liquidable (preaviso + vacaciones proporcionales)
    ccss_employee_on_termination = fields.Monetary(
        string='CCSS Obrero Retenido (CRC)', currency_field='currency_id',
        compute='_compute_total', store=True,
        help='10.83% sobre preaviso + vacaciones proporcionales (Art. 26 Reglamento CCSS). '
             'Se retiene del empleado y se deposita a la CCSS.'
    )
    # FIX NEW-02 v54: Impuesto de Renta sobre la liquidacion.
    # Art. 35 Ley ISR: preaviso y vacaciones proporcionales son ingreso gravable.
    # Se calcula sobre el total_gross aplicando los tramos vigentes.
    income_tax_on_termination = fields.Monetary(
        string='Impuesto Renta Retenido (CRC)', currency_field='currency_id',
        compute='_compute_total', store=True,
        help='Retencion de impuesto sobre la renta calculada sobre el total bruto de la liquidacion '
             '(Art. 35 Ley ISR). Se retiene del empleado y se deposita a Hacienda.'
    )
    total_gross = fields.Monetary(
        string='Total Bruto Liquidacion (CRC)', currency_field='currency_id',
        compute='_compute_total', store=True
    )
    total_net = fields.Monetary(
        string='Total Neto a Pagar (CRC)', currency_field='currency_id',
        compute='_compute_total', store=True
    )

    move_id = fields.Many2one('account.move', string='Asiento Contable', readonly=True)
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('confirmed','Confirmado'),
        ('paid',     'Pagado'),
        ('cancelled','Cancelado'),
    ], default='draft')

    note = fields.Text(string='Observaciones')

    # -- Computes -------------------------------------------------

    @api.depends('employee_id', 'termination_date')
    def _compute_name(self):
        for rec in self:
            if rec.employee_id and rec.termination_date:
                rec.name = f'Liquidacion - {rec.employee_id.name} - {rec.termination_date}'
            else:
                rec.name = 'Nueva Liquidacion'

    @api.depends('entry_date', 'termination_date')
    def _compute_service_time(self):
        for rec in self:
            if rec.entry_date and rec.termination_date:
                delta = relativedelta(rec.termination_date, rec.entry_date)
                rec.years_service = delta.years + delta.months / 12
                rec.months_service = delta.years * 12 + delta.months
                rec.days_service = (rec.termination_date - rec.entry_date).days
            else:
                rec.years_service = 0
                rec.months_service = 0
                rec.days_service = 0

    @api.depends('months_service', 'termination_reason')
    def _compute_preaviso(self):
        """
        Codigo de Trabajo CR Art. 28:
        < 3 meses: 1 semana
        3-6 meses: 2 semanas
        6-12 meses: 1 mes
        > 12 meses: 1 mes
        FIX C-10 v53: fallecimiento no genera preaviso (Art. 85 CT -- extincion por muerte).
        """
        for rec in self:
            # Fallecimiento: sin preaviso (Art. 85 CT)
            if rec.termination_reason == 'fallecimiento':
                rec.preaviso_days = 0
                continue
            m = rec.months_service
            # Art. 28 CT — escala oficial por meses exactos:
            # < 3 meses:        7 dias (periodo prueba)
            # 3 a < 6 meses:    7 dias (1 semana)
            # 6 a < 12 meses:  15 dias (Art. 28 inciso b)
            # >= 12 meses:     30 dias (1 mes)
            if m < 3:
                rec.preaviso_days = 7
            elif m < 6:
                rec.preaviso_days = 7
            elif m < 12:
                rec.preaviso_days = 15
            else:
                rec.preaviso_days = 30

    @api.onchange('termination_reason')
    def _onchange_termination_reason_preaviso(self):
        """FIX C-10 v53: Al seleccionar fallecimiento, desmarcar preaviso automaticamente."""
        if self.termination_reason == 'fallecimiento':
            self.preaviso_applies = False

    @api.depends('termination_reason')
    def _compute_cesantia_applies(self):
        """
        Cesantia aplica en despido injustificado o mutuo acuerdo.
        No aplica en renuncia voluntaria ni despido con justa causa.
        """
        for rec in self:
            rec.cesantia_applies = rec.termination_reason in (
                'despido_injust', 'mutuo', 'contrato_vence', 'fallecimiento'
            )

    @api.depends('last_salary', 'years_service', 'months_service', 'days_service',
                 'termination_date', 'entry_date', 'termination_reason', 'preaviso_days',
                 'employee_id')
    def _compute_amounts(self):
        for rec in self:
            if not rec.last_salary or not rec.entry_date or not rec.termination_date:
                rec.preaviso_amount = 0
                rec.cesantia_amount = 0
                rec.vacation_days_accrued = 0
                rec.vacation_amount = 0
                rec.aguinaldo_amount = 0
                rec.aguinaldo_months = 0
                continue

            # Usar promedio manual si el usuario lo activo (Art. 153 CT)
            if rec.use_salary_average and rec.salary_average_manual > 0:
                daily_salary = rec.salary_average_manual / 30
            else:
                daily_salary = rec.last_salary / 30
            monthly_salary_eff = daily_salary * 30
            # monthly_salary se deriva de daily_salary_eff

            # -- Preaviso ------------------------------------------
            rec.preaviso_amount = daily_salary * rec.preaviso_days if rec.preaviso_applies else 0

            # -- Cesantia (Art. 29 Codigo de Trabajo) -------------
            # Tabla de dias por ano trabajado:
            # Ano 1: 19.5 dias, Ano 2: 20 dias, Ano 3: 20.5 dias...
            # Maximo 8 anos = 22 dias/ano
            if rec.cesantia_applies:
                # FIX CALC-01: usar tabla centralizada K.CESANTIA_TABLA (Art. 29 CT)
                # Eliminada tabla local duplicada e inconsistente
                cesantia_days_table = K.CESANTIA_TABLA
                years = min(int(rec.years_service), 8)
                fraction = rec.years_service - int(rec.years_service)
                cesantia_days = 0
                for y in range(1, years + 1):
                    cesantia_days += cesantia_days_table.get(y, 22.0)
                # Fraccion del ano en curso
                if years < 8:
                    days_this_year = cesantia_days_table.get(years + 1, 22.0)
                    cesantia_days += days_this_year * fraction
                rec.cesantia_amount = round(daily_salary * cesantia_days, 2)
            else:
                rec.cesantia_amount = 0

            # -- Vacaciones proporcionales (Art. 153 CT) ---------------
            # FIX: Si el empleado tiene saldo inicial con fecha de corte,
            # usar ese saldo como base y acumular solo desde la fecha de corte
            # hasta la fecha de terminacion. Evita sobrecontar dias pre-sistema.
            import math
            from datetime import date as _date
            exit_d = rec.termination_date or _date.today()

            # Dias tomados en el sistema (vacaciones aprobadas/pagadas)
            vacation_days_taken = 0.0
            if rec.employee_id:
                taken_payments = self.env['planilla.vacation.payment'].search([
                    ('employee_id', '=', rec.employee_id.id),
                    ('state', 'in', ('approved', 'paid')),
                ])
                vacation_days_taken = sum(taken_payments.mapped('days'))

            vac_init    = rec.employee_id.vacation_initial_balance if rec.employee_id else 0.0
            vac_cutoff  = rec.employee_id.vacation_initial_balance_date if rec.employee_id else False
            has_initial = bool(vac_cutoff)

            if has_initial:
                # Acumular solo desde la fecha de corte hasta la fecha de salida
                if vac_cutoff >= exit_d:
                    accrued_since_cutoff = 0.0
                else:
                    days_since = (exit_d - vac_cutoff).days
                    accrued_since_cutoff = math.floor(days_since / 29)
                vacation_days_gross = math.floor(vac_init + accrued_since_cutoff)  # solo días completos
            else:
                # Calculo normal desde fecha de ingreso
                weeks_worked = rec.days_service / 7
                # FIX: solo días COMPLETOS (floor) — 1 día por cada 29 días calendario.
                # round() redondeaba 0.96 → 1, pagando un día que no se completó.
                # floor() garantiza que solo se pagan días acumulados completos.
                vacation_days_gross = math.floor(rec.days_service / 29)

            vacation_days_net = max(vacation_days_gross - vacation_days_taken, 0.0)
            rec.vacation_days_accrued = round(vacation_days_net, 2)
            rec.vacation_amount = round(daily_salary * vacation_days_net, 2)

            # -- Aguinaldo proporcional (Art. 228 CT) ----------------------
            # Periodo: 1 diciembre (ano anterior) al 30 noviembre (ano actual)
            exit_month = rec.termination_date.month
            exit_year  = rec.termination_date.year
            from datetime import date as _date
            # Meses en período aguinaldo: Dic=1, Ene=2, ..., Nov=12
            # Fórmula: (calendar_month % 12) + 1
            if exit_month == 12:
                period_start = _date(exit_year, 12, 1)
            else:
                period_start = _date(exit_year - 1, 12, 1)
            total_months = (exit_month % 12) + 1

            # FIX: Si hay acumulado inicial, descontar los meses ya cubiertos
            ag_init_amount = rec.employee_id.aguinaldo_initial_amount or 0.0
            ag_init_date   = rec.employee_id.aguinaldo_initial_date
            if ag_init_amount and ag_init_date and ag_init_date >= period_start:
                # Meses cubiertos por el acumulado inicial (incluye el mes del corte)
                months_covered = (
                    (ag_init_date.year * 12 + ag_init_date.month) -
                    (period_start.year * 12 + period_start.month) + 1
                )
                months_from_system = max(0, total_months - months_covered)
                aguinaldo_system   = round(monthly_salary_eff / 12 * months_from_system, 2)
                rec.aguinaldo_months = total_months
                rec.aguinaldo_amount = round(ag_init_amount + aguinaldo_system, 2)
            else:
                rec.aguinaldo_months = total_months
                rec.aguinaldo_amount = round(monthly_salary_eff / 12 * total_months, 2)

    def _calc_income_tax(self, gross):
        """FIX NEW-02 v54: calcula renta sobre el total bruto de la liquidacion.
        Reutiliza la misma logica progresiva por tramos que payslip_cr._calc_income_tax.
        La liquidacion se trata como pago unico mensual (freq = monthly).
        """
        brackets = self.env['planilla.income.tax.bracket'].search(
            # FIX-R12: mismo fix que payslip_compute_mixin -- filtrar por empresa
            # para evitar mezcla de tramos en entornos multi-empresa.
            ['|',
             ('company_id', '=', self.company_id.id),
             ('company_id', '=', False),
             ('active', '=', True)],
            order='sequence asc'
        )
        g = gross
        if not brackets:
            # Fallback tramos 2026 (DGT-R-016-2026)
            # FIX v56: usar K.constants (planilla_const.py)
            if g <= K.RENTA_EXENTO:
                return 0.0
            elif g <= K.RENTA_TOPE_10:
                return (g - K.RENTA_EXENTO) * K.RENTA_TASA_1
            elif g <= K.RENTA_TOPE_15:
                return ((K.RENTA_TOPE_10 - K.RENTA_EXENTO) * K.RENTA_TASA_1
                        + (g - K.RENTA_TOPE_10) * K.RENTA_TASA_2)
            elif g <= K.RENTA_TOPE_20:
                return ((K.RENTA_TOPE_10 - K.RENTA_EXENTO) * K.RENTA_TASA_1
                        + (K.RENTA_TOPE_15 - K.RENTA_TOPE_10) * K.RENTA_TASA_2
                        + (g - K.RENTA_TOPE_15) * K.RENTA_TASA_3)
            else:
                return ((K.RENTA_TOPE_10 - K.RENTA_EXENTO) * K.RENTA_TASA_1
                        + (K.RENTA_TOPE_15 - K.RENTA_TOPE_10) * K.RENTA_TASA_2
                        + (K.RENTA_TOPE_20 - K.RENTA_TOPE_15) * K.RENTA_TASA_3
                        + (g - K.RENTA_TOPE_20) * K.RENTA_TASA_4)
        tax = 0.0
        for bracket in brackets:
            if g <= bracket.limit_from:
                break
            limit_to = bracket.limit_to if bracket.limit_to else float('inf')
            taxable = min(g, limit_to) - bracket.limit_from
            if taxable > 0:
                tax += taxable * (bracket.rate / 100)
        return tax

    @api.depends('preaviso_amount', 'preaviso_applies', 'cesantia_amount',
                 'vacation_amount', 'aguinaldo_amount', 'other_payments', 'deductions')
    def _compute_total(self):
        # FIX A-03 v53: calcular CCSS obrero sobre base liquidable (preaviso + vacaciones prop.)
        # FIX NEW-02 v54: agregar retencion de renta sobre total_gross (Art. 35 Ley ISR)
        rh = self.env['planilla.rate.helper']
        ccss_employee_rate = rh.get_ccss_employee_rate()
        for rec in self:
            gross = (
                (rec.preaviso_amount if rec.preaviso_applies else 0) +
                rec.cesantia_amount +
                rec.vacation_amount +
                rec.aguinaldo_amount +
                rec.other_payments
            )
            # Base cotizable CCSS obrero: preaviso + vacaciones proporcionales
            liquidable_base = (
                (rec.preaviso_amount if rec.preaviso_applies else 0) +
                rec.vacation_amount
            )
            # Verificar si la empresa omite CCSS en liquidaciones
            _config = rec.env['planilla.accounting.config'].search(
                [('company_id', '=', rec.company_id.id)], limit=1)
            _skip_ccss = _config.skip_ccss_on_termination if _config else False
            ccss_emp = 0.0 if _skip_ccss else round(liquidable_base * ccss_employee_rate, 2)
            # Cesantia (Art.29 CT) y Aguinaldo (Art.228 CT) exentos de CCSS y Renta
            renta_base = liquidable_base  # preaviso + vacaciones unicamente
            income_tax = round(rec._calc_income_tax(renta_base), 2)
            rec.total_gross = round(gross, 2)
            rec.ccss_employee_on_termination = ccss_emp
            rec.income_tax_on_termination = income_tax
            # total_net = bruto - CCSS obrero - renta - otras deducciones
            rec.total_net = round(gross - ccss_emp - income_tax - rec.deductions, 2)

    # -- Onchange para autocompletar desde empleado ----------------

    @api.onchange('employee_id')
    def _onchange_employee(self):
        if self.employee_id:
            emp = self.employee_id
            self.entry_date = emp.entry_date or False

            # MEJORA: para empleados con salario variable (comisiones, HE recurrentes),
            # calcular el salario bruto promedio de los ultimos 4 meses del historial
            # en lugar de usar solo el salario base fijo.
            # Art. 153 CT: la liquidacion debe basarse en el salario real percibido.
            if getattr(emp, 'has_variable_income', False):
                # Usar salario mensual del empleado directamente
                avg_monthly = emp.base_salary or 0
                self.last_salary = emp.base_salary or 0

    # -- Actions --------------------------------------------------

    def action_confirm(self):
        self.ensure_one()
        if not self.employee_id or not self.last_salary:
            raise UserError('Complete los datos del empleado y salario antes de confirmar.')
        # Verificar boletas pendientes del empleado
        pending_slips = self.env['planilla.payslip.cr'].search([
            ('employee_id', '=', self.employee_id.id),
            ('state', 'in', ('draft', 'confirmed')),
        ])
        if pending_slips:
            names = ', '.join(pending_slips.mapped('name'))
            raise UserError(
                f'El empleado {self.employee_id.name} tiene boletas pendientes sin pagar:\n'
                f'{names}\n\n'
                f'Pague o cancele esas boletas antes de confirmar la liquidacion.'
            )
        # Verificar periodo cerrado
        termination_date = self.termination_date or fields.Date.context_today(self)
        closed = PlanillaClosedPeriod.is_period_closed(
            self.env, self.company_id.id,
            termination_date, termination_date,
            self.employee_id.branch_id.id if self.employee_id.branch_id else False
        )
        if closed:
            raise UserError(
                f'No se puede confirmar la liquidacion: el periodo que incluye la fecha '
                f'{termination_date.strftime("%d/%m/%Y")} esta cerrado '
                f'("{closed.name}", cerrado el {closed.closed_date.strftime("%d/%m/%Y")} '
                f'por {closed.closed_by.name}).'
            )

        # BUG #3 FIX v50: Integracion automatica de prestamos activos
        # Busca prestamos/adelantos activos o aprobados con saldo pendiente
        active_loans = self.env['planilla.employee.loan'].search([
            ('employee_id', '=', self.employee_id.id),
            ('state', 'in', ('approved', 'active')),
            ('amount_pending', '>', 0),
        ])
        if active_loans:
            total_loan_balance = sum(active_loans.mapped('amount_pending'))
            loan_details = ', '.join(
                f'{l.name} (CRC{l.amount_pending:,.2f})' for l in active_loans
            )
            # Pre-llenar campo deductions si esta vacio
            if not self.deductions:
                self.deductions = round(total_loan_balance, 2)
                self.deductions_note = f'Saldo prestamos activos: {loan_details}'
            else:
                # Ya tiene deducciones manuales: mostrar advertencia
                self.message_post(
                    body=f'<b>WARN Aviso:</b> El empleado tiene prestamos activos con saldo '
                         f'pendiente de CRC{total_loan_balance:,.2f} ({loan_details}). '
                         f'Verifique que el campo <b>Deducciones</b> ya lo contempla.',
                    message_type='notification',
                )

        self.state = 'confirmed'

    def action_pay(self):
        self.ensure_one()
        if self.state != 'confirmed':
            raise UserError('Solo se pueden pagar liquidaciones confirmadas.')

        # BUG #7 FIX v50: Savepoint para atomicidad -- si el asiento falla,
        # el empleado NO queda inactivo sin reversion contable
        with self.env.cr.savepoint():
            move = self._create_termination_accounting_entry()
            # Inactivar empleado SOLO si el asiento se creo correctamente
            if move:
                self.employee_id.write({'active': False})
            self.write({
                'state': 'paid',
                'move_id': move.id if move else False,
            })
            # Registrar movimiento de salida
            self.env['planilla.employee.movement'].create({
                'employee_id':    self.employee_id.id,
                'movement_date':  self.termination_date or fields.Date.today(),
                'movement_type':  'salida',
                'reason':         dict(self._fields['termination_reason'].selection).get(
                    self.termination_reason, self.termination_reason
                ),
                'salary_before':  self.last_salary,
                'company_id':     self.company_id.id,
                'termination_id': self.id,
                'note':           self.note or False,
            })
            # FIX-AUD-08: cancelar prestamos activos del empleado al pagar la liquidacion.
            # Si las deducciones ya contemplaban el saldo, los prestamos deben cerrarse
            # para que no sigan apareciendo como activos ni generen cuotas futuras.
            active_loans = self.env['planilla.employee.loan'].search([
                ('employee_id', '=', self.employee_id.id),
                ('state', 'in', ('approved', 'active')),
            ])
            if active_loans:
                active_loans.write({'state': 'cancelled'})
                _logger.info(
                    'planilla_cr.termination.action_pay: %d prestamo(s) cancelados '
                    'automaticamente al pagar liquidacion de %s.',
                    len(active_loans), self.employee_id.name
                )

    def _create_termination_accounting_entry(self):
        # FIX BUG-N01 v52: pasar company_id del registro de liquidacion, no la compania
        # activa en sesion. En multi-empresa esto asegura usar la config correcta.
        config = self.env['planilla.accounting.config'].get_config(self.company_id.id)
        if not config:
            return False

        emp = self.employee_id.name
        journal = config.journal_id
        if not journal:
            return False

        lines = []

        def add_line(account, debit=0.0, credit=0.0, name=''):
            if not account or (debit == 0 and credit == 0):
                return
            lines.append((0, 0, {
                'account_id': account.id,
                'name': name,
                'debit': round(debit, 2),
                'credit': round(credit, 2),
            }))

        # -- DEBITOS (gastos) -----------------------------------------
        if self.preaviso_applies and self.preaviso_amount:
            add_line(
                config.account_preaviso_expense or config.account_salary_expense,
                debit=self.preaviso_amount,
                name=f'Preaviso -- {emp}'
            )
        if self.cesantia_applies and self.cesantia_amount:
            add_line(
                config.account_cesantia_expense,
                debit=self.cesantia_amount,
                name=f'Cesantia -- {emp}'
            )
        if self.vacation_amount:
            add_line(
                config.account_vacation_expense,
                debit=self.vacation_amount,
                name=f'Vacaciones proporcionales -- {emp}'
            )
        if self.aguinaldo_amount:
            add_line(
                config.account_aguinaldo_expense,
                debit=self.aguinaldo_amount,
                name=f'Aguinaldo proporcional -- {emp}'
            )
        if self.other_payments:
            add_line(
                config.account_salary_expense,
                debit=self.other_payments,
                name=f'Otros pagos -- {emp}: {self.other_payments_note or ""}'
            )

        # BUG #4 FIX v50: CCSS patronal sobre preaviso + vacaciones proporcionales
        # Art. 26 Reglamento CCSS: cargas sociales aplican sobre estos componentes
        # de la liquidacion (preaviso y vacaciones proporcionales son salario ordinario
        # para efectos de cotizacion CCSS segun Reglamento del Seguro Social).
        rh = self.env['planilla.rate.helper']
        ccss_employer_rate = rh.get_ccss_employer_rate()  # 26.83%
        # FIX A-03 v53: CCSS obrero e Impuesto de Renta sobre la liquidacion.
        # Art. 26 RCCSS y Art. 29 CT: preaviso y vacaciones proporcionales
        # son base de cotizacion. La cesantia y el aguinaldo proporcional
        # NO son base imponible (CCSS Resolucion Nro. 5 del 24/5/1994).
        # La renta aplica sobre el total bruto segun criterio del Ministerio de Hacienda.
        ccss_employee_rate = rh.get_ccss_employee_rate()  # 10.83%
        liquidable_base = (
            (self.preaviso_amount if self.preaviso_applies else 0) +
            self.vacation_amount
        )
        ccss_on_termination = round(liquidable_base * ccss_employer_rate, 2)
        ccss_emp_on_termination = round(liquidable_base * ccss_employee_rate, 2)
        if ccss_on_termination > 0:
            add_line(
                config.account_social_charges_expense,
                debit=ccss_on_termination,
                name=f'CCSS Patronal sobre liquidacion -- {emp} ({ccss_employer_rate*100:.2f}%)'
            )
            add_line(
                config.account_ccss_payable,
                credit=ccss_on_termination,
                name=f'CCSS Patronal liquidacion por pagar -- {emp}'
            )
        # CCSS obrero: se retiene del empleado (reduce el neto a pagar)
        if ccss_emp_on_termination > 0:
            add_line(
                config.account_ccss_payable,
                credit=ccss_emp_on_termination,
                name=f'CCSS Obrero retenido en liquidacion -- {emp} ({ccss_employee_rate*100:.2f}%)'
            )

        # FIX NEW-02 v54: Impuesto de Renta retenido sobre la liquidacion (Art. 35 Ley ISR)
        income_tax_liq = round(self.income_tax_on_termination or 0.0, 2)
        if income_tax_liq > 0:
            add_line(
                config.account_income_tax_payable,
                credit=income_tax_liq,
                name=f'Retencion Renta en liquidacion -- {emp}'
            )

        # -- CREDITO (pasivo por pagar) -------------------------------
        # FIX A-03 v53: neto = total_gross - CCSS obrero.
        # FIX NEW-02 v54: neto = total_gross - CCSS obrero - renta.
        # FIX-H1: las deducciones (prestamos) reducen el neto a depositar al empleado
        # pero NO son un gasto adicional para la empresa -- la empresa ya presto ese dinero
        # antes. El asiento correcto es: el pasivo por pagar al empleado se reduce en el
        # monto de las deducciones. Se registra UN SOLO credito con el neto real a depositar.
        # La version anterior creaba un DEBE adicional (payable_account, debit=deductions)
        # sin contrapartida en HABER, lo que hacia el asiento no cuadrar por ese monto.
        payable_account = config.account_termination_payable or config.account_salary_payable
        net_to_pay = round(
            self.total_gross - ccss_emp_on_termination - income_tax_liq - (self.deductions or 0.0),
            2
        )
        add_line(
            payable_account,
            credit=max(net_to_pay, 0.0),
            name=f'Liquidacion por pagar (neto a depositar) -- {emp}'
        )

        # Si las deducciones superan el neto, registrar como DEBE en la cuenta de liquidacion
        # (el empleado queda a deber; contablemente reduce el pasivo a cero y registra
        # el saldo como cuenta por cobrar, pero en la practica esto es inusual).
        if self.deductions and net_to_pay < 0:
            loans_receivable = (
                getattr(config, 'account_loans_receivable', None)
                or config.account_loans_payable
                or config.account_salary_payable
            )
            add_line(
                loans_receivable,
                debit=abs(net_to_pay),
                name=f'Deduccion supera liquidacion -- {emp}: {self.deductions_note or ""}'
            )

        if not lines:
            return False

        # H1 FIX -- Verificar cuadre antes de postear
        total_debit  = round(sum(l[2]['debit']  for l in lines), 2)
        total_credit = round(sum(l[2]['credit'] for l in lines), 2)
        if abs(total_debit - total_credit) > 0.02:  # FIX BUG-N02 v52: tolerancia reducida a CRC0.02
            raise UserError(
                f'El asiento de liquidacion no cuadra para {emp}:\n'
                f'  Debitos:  CRC{total_debit:,.2f}\n'
                f'  Creditos: CRC{total_credit:,.2f}\n\n'
                f'Verifique que todas las cuentas contables esten configuradas '
                f'(Planilla -> Configuracion -> Contabilidad ->  Autocompletar).'
            )

        move = self.env['account.move'].create({
            'journal_id': journal.id,
            'date': self.termination_date or fields.Date.context_today(self),
            'ref': f'Liquidacion -- {emp} -- {self.termination_date}',
            'move_type': 'entry',
            'line_ids': lines,
        })
        move.action_post()
        return move



    def action_cancel(self):
        self.ensure_one()
        if self.state == 'paid':
            raise UserError('No se puede cancelar una liquidacion ya pagada.')
        self.state = 'cancelled'
        # Reactivar empleado si fue inactivado por esta liquidacion
        if self.employee_id and not self.employee_id.active:
            self.employee_id.write({'active': True})

    def action_reset_to_draft(self):
        self.ensure_one()
        if self.state not in ('cancelled', 'confirmed'):
            raise UserError('Solo se puede resetear desde Cancelado o Confirmado.')
        self.state = 'draft'

    def action_view_accounting_entry(self):
        self.ensure_one()
        if not self.move_id:
            raise UserError('No hay asiento contable asociado.')
        return {
            'type': 'ir.actions.act_window',
            'name': 'Asiento Contable',
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': self.move_id.id,
        }

    def action_print_termination(self):
        return self.env.ref('planilla_cr.action_report_termination').report_action(self)

    def action_print_carta_despido(self):
        return self.env.ref('planilla_cr.action_report_carta_despido').report_action(self)
