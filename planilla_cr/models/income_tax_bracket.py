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

    # AUDIT-02: año fiscal explícito — identifica a qué resolución DGT pertenece el tramo
    year = fields.Integer(
        string='Año Fiscal',
        required=True,
        default=lambda self: fields.Date.today().year,
        help='Año al que corresponde esta tabla de renta (ej: 2026). '
             'Todos los tramos activos deben pertenecer al MISMO año fiscal. '
             'El sistema valida que no haya tramos activos de años diferentes '
             'para evitar mezclas de tablas DGT.'
    )

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

    @api.constrains('active', 'year', 'company_id')
    def _check_single_active_year(self):
        """
        AUDIT-02: Valida que todos los tramos ACTIVOS de una empresa pertenezcan
        al mismo año fiscal. Esto previene mezclas de tablas DGT (ej: tramos 2025
        activos junto con tramos 2026 activos), que causarían cálculos incorrectos.

        Regla: si existen tramos activos de año X, no se puede activar un tramo de año Y.
        Para cambiar de año: desactivar TODOS los tramos del año anterior primero,
        o usar el botón "Activar tabla [año]" que lo hace automáticamente.
        """
        for rec in self:
            if not rec.active:
                continue  # tramos inactivos no generan conflicto
            # Buscar otros años distintos con tramos activos en la misma empresa
            otros_anos = self.search([
                ('company_id', '=', rec.company_id.id),
                ('active', '=', True),
                ('year', '!=', rec.year),
                ('id', '!=', rec.id),
            ], limit=1)
            if otros_anos:
                raise ValidationError(
                    f'No se puede activar un tramo del año {rec.year} porque ya existen '
                    f'tramos activos del año {otros_anos.year} en esta empresa.\n\n'
                    f'Para actualizar la tabla de renta:\n'
                    f'  1. Desactive TODOS los tramos del año {otros_anos.year}, o\n'
                    f'  2. Use el botón "Activar tabla [año]" en la vista de lista, '
                    f'     que desactiva el año anterior automáticamente.\n\n'
                    f'Mezclar tablas de años distintos causa cálculos de renta incorrectos.'
                )

