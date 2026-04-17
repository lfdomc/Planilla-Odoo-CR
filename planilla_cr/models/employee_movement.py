import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class EmployeeMovement(models.Model):
    _name = 'planilla.employee.movement'
    _description = 'Historial de Movimientos de Personal'
    _order = 'movement_date desc, id desc'
    _rec_name = 'display_name'

    display_name = fields.Char(
        string='Referencia', compute='_compute_display_name', store=True
    )
    employee_id = fields.Many2one(
        'hr.employee', string='Empleado', required=True, index=True,
        ondelete='cascade', domain=[('active', 'in', [True, False])]
    )
    branch_id = fields.Many2one(
        related='employee_id.branch_id', string='Sucursal', store=True
    )
    company_id = fields.Many2one(
        'res.company', string='Empresa',
        default=lambda self: self.env.company
    )
    movement_date = fields.Date(
        string='Fecha del Movimiento', required=True, default=fields.Date.today
    )
    movement_type = fields.Selection([
        ('ingreso',    'Ingreso'),
        ('salida',     'Salida'),
        ('reingreso',  'Reingreso'),
        ('aumento',    'Aumento Salarial'),
        ('suspension', 'Suspension Temporal'),
        ('otro',       'Otro'),
    ], string='Tipo de Movimiento', required=True)
    reason = fields.Char(string='Motivo / Detalle', required=True)
    salary_before = fields.Monetary(
        string='Salario Anterior (CRC)', currency_field='currency_id'
    )
    salary_after = fields.Monetary(
        string='Salario Nuevo (CRC)', currency_field='currency_id'
    )
    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.ref('base.CRC')
    )
    registered_by = fields.Many2one(
        'res.users', string='Registrado por',
        default=lambda self: self.env.user, readonly=True
    )
    termination_id = fields.Many2one(
        'planilla.termination', string='Liquidacion Relacionada',
        readonly=True, ondelete='set null'
    )
    salary_history_id = fields.Many2one(
        'planilla.salary.history', string='Historial Salarial Relacionado',
        readonly=True, ondelete='set null'
    )
    note = fields.Text(string='Observaciones')

    @api.depends('employee_id', 'movement_type', 'movement_date')
    def _compute_display_name(self):
        tipos = dict(self._fields['movement_type'].selection)
        for rec in self:
            emp = rec.employee_id.name or '?'
            tipo = tipos.get(rec.movement_type, rec.movement_type)
            fecha = rec.movement_date.strftime('%d/%m/%Y') if rec.movement_date else '?'
            rec.display_name = f'{tipo} - {emp} - {fecha}'
