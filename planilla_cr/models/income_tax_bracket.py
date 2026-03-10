from odoo import models, fields, api
from odoo.exceptions import ValidationError


class IncomeTaxBracket(models.Model):
    _name = 'planilla.income.tax.bracket'
    _description = 'Tramo de Impuesto sobre la Renta (Asalariados CR)'
    _order = 'sequence asc'

    company_id = fields.Many2one(
        'res.company', string='Compañía',
        required=True, default=lambda self: self.env.company
    )
    sequence = fields.Integer(string='Orden', default=10)
    name = fields.Char(string='Descripción', required=True)
    limit_from = fields.Monetary(
        string='Desde (₡)', currency_field='currency_id',
        help='Monto mínimo de salario bruto mensual para aplicar este tramo. 0 = desde cero.'
    )
    limit_to = fields.Monetary(
        string='Hasta (₡)', currency_field='currency_id',
        help='Monto máximo. Dejar en 0 para indicar "sin límite superior".'
    )
    rate = fields.Float(
        string='Tasa (%)', digits=(5, 2),
        help='Porcentaje aplicable sobre el exceso del límite inferior.'
    )
    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.ref('base.CRC')
    )
    active = fields.Boolean(default=True)
    valid_from = fields.Date(string='Vigente desde')
    note = fields.Char(string='Referencia legal')

    @api.constrains('rate')
    def _check_rate(self):
        for rec in self:
            if rec.rate < 0 or rec.rate > 100:
                raise ValidationError('La tasa debe estar entre 0 y 100%.')

    @api.constrains('limit_from', 'limit_to')
    def _check_limits(self):
        for rec in self:
            if rec.limit_to and rec.limit_to <= rec.limit_from:
                raise ValidationError('El límite superior debe ser mayor al inferior.')
