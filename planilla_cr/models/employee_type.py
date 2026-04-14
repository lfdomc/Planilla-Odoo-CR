from odoo import models, fields


class EmployeeType(models.Model):
    _name = 'planilla.employee.type'
    _description = 'Tipo de Empleado'

    name = fields.Char(string='Tipo', required=True)
    code = fields.Char(string='Codigo')
    active = fields.Boolean(default=True)
    description = fields.Text(string='Descripcion')
    contract_type = fields.Selection([
        ('indefinido', 'Contrato Indefinido'),
        ('fijo', 'Contrato a Plazo Fijo'),
        ('obra', 'Contrato por Obra'),
        ('servicios', 'Servicios Profesionales'),
        ('practicante', 'Practicante'),
    ], string='Tipo de Contrato', default='indefinido')
    apply_ccss = fields.Boolean(string='Aplica CCSS', default=True)
    apply_ins = fields.Boolean(string='Aplica INS', default=True)
    apply_income_tax = fields.Boolean(string='Aplica Renta', default=True)
