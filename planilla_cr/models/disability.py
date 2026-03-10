from odoo import models, fields, api


class Disability(models.Model):
    _name = 'planilla.disability'
    _description = 'Incapacidad'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Referencia', compute='_compute_name', store=True)
    employee_id = fields.Many2one(
        'hr.employee', string='Empleado', required=True, tracking=True
    )
    branch_id = fields.Many2one(
        related='employee_id.branch_id', string='Sucursal', store=True
    )

    disability_type = fields.Selection([
        ('ccss', 'CCSS - Enfermedad'),
        ('ccss_accident', 'CCSS - Accidente Laboral'),
        ('ins', 'INS - Riesgo Laboral'),
        ('maternity', 'Maternidad'),
        ('other', 'Otra'),
    ], string='Tipo de Incapacidad', required=True, default='ccss', tracking=True)

    date_start = fields.Date(string='Fecha Inicio', required=True, tracking=True,
                             default=fields.Date.today)
    date_end = fields.Date(string='Fecha Fin', required=True, tracking=True,
                           default=fields.Date.today)
    days = fields.Integer(
        string='Días', compute='_compute_days', store=True
    )

    # Porcentaje de subsidio según CCSS:
    # Primeros 3 días: 0% (a cargo del patrono 100%)
    # Del 4to en adelante: 60% CCSS, 40% patrono
    subsidy_percentage = fields.Float(
        string='% Subsidio CCSS',
        default=60.0,
        help='Porcentaje que paga la CCSS del salario durante la incapacidad'
    )
    employer_percentage = fields.Float(
        string='% Cargo Patrono',
        default=40.0
    )

    daily_salary = fields.Monetary(
        string='Salario Diario', currency_field='currency_id',
        compute='_compute_daily_salary', store=True
    )
    currency_id = fields.Many2one(related='employee_id.currency_id', store=True)
    employer_cost = fields.Monetary(
        string='Costo Patrono', currency_field='currency_id',
        compute='_compute_costs', store=True
    )
    ccss_subsidy = fields.Monetary(
        string='Subsidio CCSS', currency_field='currency_id',
        compute='_compute_costs', store=True
    )

    certificate_number = fields.Char(string='Número de Certificado CCSS')
    diagnosis = fields.Char(string='Diagnóstico')
    note = fields.Text(string='Observaciones')

    state = fields.Selection([
        ('draft', 'Borrador'),
        ('confirmed', 'Confirmado'),
        ('paid', 'Procesado en Planilla'),
        ('cancelled', 'Cancelado'),
    ], string='Estado', default='draft', tracking=True)

    payslip_id = fields.Many2one('planilla.payslip.cr', string='Boleta de Pago')

    @api.depends('employee_id', 'date_start')
    def _compute_name(self):
        for rec in self:
            emp = rec.employee_id.name or ''
            date_str = str(rec.date_start) if rec.date_start else ''
            rec.name = f'INC - {emp} - {date_str}'

    @api.depends('date_start', 'date_end')
    def _compute_days(self):
        for rec in self:
            if rec.date_start and rec.date_end:
                delta = rec.date_end - rec.date_start
                rec.days = delta.days + 1
            else:
                rec.days = 0

    @api.depends('employee_id')
    def _compute_daily_salary(self):
        for rec in self:
            if rec.employee_id and rec.employee_id.base_salary:
                rec.daily_salary = rec.employee_id.base_salary / 30
            else:
                rec.daily_salary = 0.0

    @api.depends('days', 'daily_salary', 'subsidy_percentage', 'employer_percentage')
    def _compute_costs(self):
        for rec in self:
            total = rec.days * rec.daily_salary
            # Los primeros 3 días son 100% patrono
            first_days = min(rec.days, 3)
            remaining_days = max(rec.days - 3, 0)
            rec.employer_cost = (first_days * rec.daily_salary) + \
                                 (remaining_days * rec.daily_salary * rec.employer_percentage / 100)
            rec.ccss_subsidy = remaining_days * rec.daily_salary * rec.subsidy_percentage / 100

    def action_confirm(self):
        self.write({'state': 'confirmed'})

    def action_cancel(self):
        self.write({'state': 'cancelled'})
