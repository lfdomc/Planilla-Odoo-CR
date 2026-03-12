from odoo import models, fields, api
from datetime import date


class VacationPayment(models.Model):
    _name = 'planilla.vacation.payment'
    _description = 'Pago de Vacaciones'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Referencia', compute='_compute_name', store=True)
    employee_id = fields.Many2one(
        'hr.employee', string='Empleado', required=True, tracking=True
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
        string='Días Acumulados',
        compute='_compute_days_accrued',
        help='Días de vacaciones acumulados según antigüedad'
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

    # Cálculo promedio últimas 4 semanas (Art. 153 CT)
    avg_last_4_weeks = fields.Monetary(
        string='Promedio Últimas 4 Semanas', currency_field='currency_id',
        help='Promedio del salario de las últimas 4 semanas trabajadas (Art. 153 CT). '
             'Incluye HE, comisiones y otros ingresos variables.'
    )
    use_average = fields.Boolean(
        string='Usar Promedio 4 Semanas',
        default=False,
        help='Si el empleado tuvo HE, comisiones u otros ingresos variables, '
             'activar para calcular con el promedio Art. 153 CT'
    )
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
    note = fields.Text(string='Observaciones')

    def action_approve_and_pay(self):
        """Aprueba las vacaciones y opcionalmente agrega el monto a la boleta."""
        for rec in self:
            if rec.state != 'draft':
                raise UserError('Solo se pueden aprobar vacaciones en borrador.')

            # Verificar días disponibles
            if rec.days > rec.days_accrued:
                raise ValidationError(
                    f'El empleado tiene {rec.days_accrued:.1f} días disponibles '
                    f'pero solicita {rec.days} días.'
                )

            rec.state = 'approved'

            # Si hay pago en dinero y tiene boleta asignada, agregar ingreso
            if rec.vacation_income_payslip and rec.payslip_id and rec.total_amount > 0:
                if rec.payment_method in ('dinero', 'mixto'):
                    # Buscar o crear código de deducción para vacaciones
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

        return {'type': 'ir.actions.act_window_close'}

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

    @api.depends('employee_id')
    def _compute_days_accrued(self):
        for rec in self:
            if rec.employee_id and rec.employee_id.entry_date:
                today = date.today()
                years = (today - rec.employee_id.entry_date).days / 365
                # 12 días hábiles por año trabajado (mínimo legal CR)
                rec.days_accrued = years * 12
            else:
                rec.days_accrued = 0

    @api.depends('employee_id')
    def _compute_daily_salary(self):
        for rec in self:
            if rec.employee_id and rec.employee_id.base_salary:
                rec.daily_salary = rec.employee_id.base_salary / 30
            else:
                rec.daily_salary = 0.0

    @api.depends('days', 'daily_salary')
    def _compute_total(self):
        for rec in self:
            rec.total_amount = rec.days * rec.daily_salary

    def action_approve(self):
        self.write({'state': 'approved'})

    def action_cancel(self):
        self.write({'state': 'cancelled'})
