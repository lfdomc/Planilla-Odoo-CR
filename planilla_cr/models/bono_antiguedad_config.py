"""
planilla.bono.antiguedad.config -- Tabla de Bonos por Antiguedad
Permite configurar un porcentaje o monto fijo por tramo de anos de servicio.
El cron cron_bono_antiguedad() crea automaticamente el bono al cumplir
el aniversario laboral del empleado.
"""
from odoo import models, fields, api
from odoo.exceptions import ValidationError


class BonoAntiguedadConfig(models.Model):
    _name        = 'planilla.bono.antiguedad.config'
    _description = 'Configuracion de Bono de Antiguedad por Anos'
    _order       = 'years_from'
    _rec_name    = 'name'

    company_id = fields.Many2one(
        'res.company', string='Empresa', required=True,
        default=lambda self: self.env.company
    )
    name = fields.Char(
        string='Descripcion del Tramo', required=True,
        help='Ej: "1-3 anos -- 2%"'
    )
    years_from = fields.Integer(
        string='Desde (anos cumplidos)', required=True,
        help='Anos de servicio minimos para este tramo. Ej: 1 = aplica al primer aniversario.'
    )
    years_to = fields.Integer(
        string='Hasta (anos cumplidos)',
        help='Anos maximos del tramo. Dejar en 0 para "en adelante".'
    )
    amount_type = fields.Selection([
        ('percentage', 'Porcentaje del Salario Base'),
        ('fixed',      'Monto Fijo (CRC)'),
    ], string='Tipo de Calculo', required=True, default='percentage')
    percentage = fields.Float(
        string='Porcentaje (%)', digits=(5, 2),
        help='Porcentaje del salario base mensual a pagar como bono.'
    )
    fixed_amount = fields.Monetary(
        string='Monto Fijo (CRC)', currency_field='currency_id'
    )
    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.ref('base.CRC')
    )
    active = fields.Boolean(default=True)

    @api.constrains('years_from', 'years_to')
    def _check_years(self):
        for rec in self:
            if rec.years_from < 1:
                raise ValidationError('El tramo debe iniciar desde al menos 1 ano.')
            if rec.years_to and rec.years_to < rec.years_from:
                raise ValidationError('"Hasta" debe ser mayor o igual a "Desde".')

    @api.constrains('percentage', 'fixed_amount', 'amount_type')
    def _check_amounts(self):
        for rec in self:
            if rec.amount_type == 'percentage' and rec.percentage <= 0:
                raise ValidationError('El porcentaje debe ser mayor a 0.')
            if rec.amount_type == 'fixed' and rec.fixed_amount <= 0:
                raise ValidationError('El monto fijo debe ser mayor a CRC0.')

    def compute_bono_amount(self, base_salary, years):
        """
        Retorna el monto del bono para un salario base y anos de servicio dados.
        Busca el tramo correspondiente en esta configuracion.
        """
        self.ensure_one()
        if self.amount_type == 'percentage':
            return round(base_salary * self.percentage / 100.0, 2)
        return self.fixed_amount

    @api.model
    def get_config_for_years(self, company_id, years):
        """
        Retorna el registro de configuracion que aplica para los anos dados.
        """
        configs = self.search([
            ('company_id', '=', company_id),
            ('active', '=', True),
            ('years_from', '<=', years),
        ], order='years_from desc')
        for cfg in configs:
            if not cfg.years_to or cfg.years_to >= years:
                return cfg
        return self.browse()
