from odoo import models, fields


class IdentificationType(models.Model):
    _name = 'planilla.identification.type'
    _description = 'Tipo de Identificacion'

    name = fields.Char(string='Nombre', required=True)
    code = fields.Char(string='Codigo', required=True)
    active = fields.Boolean(default=True)
    description = fields.Text(string='Descripcion')
