from odoo import models, fields, api


class Overtime(models.Model):
    _name = 'planilla.overtime'
    _description = 'Horas Extras'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(
        string='Referencia', compute='_compute_name', store=True
    )
    employee_id = fields.Many2one(
        'hr.employee', string='Empleado', required=True, tracking=True
    )
    branch_id = fields.Many2one(
        related='employee_id.branch_id', string='Sucursal', store=True
    )
    date = fields.Date(string='Fecha', required=True, tracking=True,
                       default=fields.Date.today)
    hours = fields.Float(string='Horas Extras', required=True, tracking=True,
                         default=1.0)
    overtime_type = fields.Selection([
        ('simple', 'Simple (1.5x)'),
        ('double', 'Doble (2x)'),
        ('holiday', 'Día Feriado'),
    ], string='Tipo', default='simple', required=True, tracking=True)

    hourly_rate = fields.Monetary(
        string='Tarifa por Hora', currency_field='currency_id',
        compute='_compute_hourly_rate', store=True
    )
    currency_id = fields.Many2one(
        related='employee_id.currency_id', store=True
    )
    amount = fields.Monetary(
        string='Monto Total', currency_field='currency_id',
        compute='_compute_amount', store=True
    )

    state = fields.Selection([
        ('draft', 'Borrador'),
        ('approved', 'Aprobado'),
        ('paid', 'Pagado'),
        ('cancelled', 'Cancelado'),
    ], string='Estado', default='draft', tracking=True)

    payslip_id = fields.Many2one('planilla.payslip.cr', string='Boleta de Pago')
    note = fields.Text(string='Observaciones')
    source = fields.Selection([
        ('manual', 'Ingreso Manual'),
        ('attendance', 'Importado de Asistencias'),
    ], string='Origen', default='manual', readonly=True)

    @api.depends('employee_id', 'date')
    def _compute_name(self):
        for rec in self:
            emp = rec.employee_id.name or ''
            date_str = str(rec.date) if rec.date else ''
            rec.name = f'HE - {emp} - {date_str}'

    @api.depends('employee_id')
    def _compute_hourly_rate(self):
        for rec in self:
            if rec.employee_id and rec.employee_id.base_salary:
                # Salario mensual / 30 días / 8 horas = tarifa por hora
                rec.hourly_rate = rec.employee_id.base_salary / 30 / 8
            else:
                rec.hourly_rate = 0.0

    @api.depends('hours', 'hourly_rate', 'overtime_type')
    def _compute_amount(self):
        factors = {'simple': 1.5, 'double': 2.0, 'holiday': 2.0}
        for rec in self:
            factor = factors.get(rec.overtime_type, 1.5)
            rec.amount = rec.hours * rec.hourly_rate * factor

    def action_approve(self):
        self.write({'state': 'approved'})

    def action_cancel(self):
        self.write({'state': 'cancelled'})
