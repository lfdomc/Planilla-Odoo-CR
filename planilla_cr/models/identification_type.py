from odoo import models, fields


class IdentificationType(models.Model):
    _name = 'planilla.identification.type'
    _description = 'Tipo de Identificación'

    name = fields.Char(string='Nombre', required=True)
    code = fields.Char(string='Código', required=True)
    active = fields.Boolean(default=True)
    description = fields.Text(string='Descripción')
