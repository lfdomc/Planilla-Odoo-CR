from odoo import models, fields, api
from odoo.exceptions import ValidationError


class MinimumSalary(models.Model):
    _name = 'planilla.minimum.salary'
    _description = 'Salarios Mínimos MTSS'
    _order = 'valid_from desc, category asc'

    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company
    )
    category = fields.Char(
        string='Categoría Ocupacional', required=True,
        help='Ej: Trabajador no calificado, Técnico de nivel medio, etc.'
    )
    amount = fields.Monetary(
        string='Salario Mínimo Mensual (₡)', currency_field='currency_id', required=True
    )
    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.ref('base.CRC')
    )
    valid_from = fields.Date(
        string='Vigente desde', required=True,
        help='Fecha del decreto MTSS que establece este salario mínimo'
    )
    decree_ref = fields.Char(
        string='Decreto / Referencia',
        help='Ej: Decreto N.° 44567-MTSS vigente enero 2026'
    )
    active = fields.Boolean(default=True)

    @api.constrains('amount')
    def _check_amount(self):
        for rec in self:
            if rec.amount <= 0:
                raise ValidationError('El salario mínimo debe ser mayor a cero.')

    @api.model
    def get_current_minimum(self, category=None):
        """
        Retorna el salario mínimo más reciente vigente.
        Si se pasa category, filtra por esa categoría.
        No usa caché — los salarios mínimos son datos contables críticos
        que deben consultarse directamente a la BD en cada operación.
        """
        domain = [('active', '=', True)]
        if category:
            domain.append(('category', 'ilike', category))
        rec = self.search(domain, order='valid_from desc', limit=1)
        return rec.amount if rec else 0.0
