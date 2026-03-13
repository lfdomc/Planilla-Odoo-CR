from odoo import models, fields, api
from odoo.models import Constraint


class Overtime(models.Model):
    _name = 'planilla.overtime'
    _description = 'Horas Extras'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _unique_overtime_employee_date_type = Constraint(
        'UNIQUE(employee_id, date, overtime_type)',
        'Ya existe un registro de horas extras del mismo tipo para este empleado en esa fecha. Verifique los registros antes de crear uno nuevo.'
    )



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

    @api.depends('employee_id', 'date')
    def _compute_hourly_rate(self):
        """
        BUG #6 FIX v50: Usa el salario histórico vigente en la fecha de las HE.
        FIX M-04 v51: Usa hours_per_day del schedule_type del empleado en vez
        de 8 horas fijo. Para empleados con jornada de 6h, 10h o 12h la tarifa
        horaria era incorrecta. Fallback a 8h si no hay schedule_type configurado.
        """
        for rec in self:
            if not rec.employee_id:
                rec.hourly_rate = 0.0
                continue
            base_salary = 0.0
            if rec.date:
                # Buscar salario histórico vigente en la fecha de las HE
                history = self.env['planilla.salary.history'].search([
                    ('employee_id', '=', rec.employee_id.id),
                    ('effective_date', '<=', rec.date),
                ], order='effective_date desc', limit=1)
                if history:
                    base_salary = history.gross_salary
            # Fallback: salario base actual
            if not base_salary:
                base_salary = rec.employee_id.base_salary or 0.0
            # Horas por día según jornada del empleado (fallback 8h jornada ordinaria)
            hours_per_day = 8.0
            if rec.employee_id.schedule_type_id and rec.employee_id.schedule_type_id.hours_per_day:
                hours_per_day = rec.employee_id.schedule_type_id.hours_per_day
            # Tarifa por hora = Salario mensual / 30 días / horas_jornada
            rec.hourly_rate = round(base_salary / 30 / hours_per_day, 2) if base_salary else 0.0

    @api.depends('hours', 'hourly_rate', 'overtime_type')
    def _compute_amount(self):
        factors = {'simple': 1.5, 'double': 2.0, 'holiday': 2.0}
        for rec in self:
            factor = factors.get(rec.overtime_type, 1.5)
            rec.amount = rec.hours * rec.hourly_rate * factor

    def action_approve(self):
        self.ensure_one()
        self.write({'state': 'approved'})

    def action_cancel(self):
        self.write({'state': 'cancelled'})
