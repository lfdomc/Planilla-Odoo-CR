from odoo import models, fields


class EmployeeStatus(models.Model):
    _name = 'planilla.employee.status'
    _description = 'Estado de Empleado'

    name = fields.Char(string='Estado', required=True)
    code = fields.Char(string='Codigo')
    color = fields.Integer(string='Color')
    active = fields.Boolean(default=True)
    description = fields.Text(string='Descripcion')
    is_active_payroll = fields.Boolean(
        string='Incluir en Planilla',
        default=True,
        help='Si esta activo, los empleados con este estado seran incluidos en la generacion de planilla.'
    )
