from odoo import models, fields


class EmployeeStatus(models.Model):
    _name = 'planilla.employee.status'
    _description = 'Estado de Empleado'

    name = fields.Char(string='Estado', required=True)
    code = fields.Char(string='Código')
    color = fields.Integer(string='Color')
    active = fields.Boolean(default=True)
    description = fields.Text(string='Descripción')
    is_active_payroll = fields.Boolean(
        string='Incluir en Planilla',
        default=True,
        help='Si está activo, los empleados con este estado serán incluidos en la generación de planilla.'
    )
