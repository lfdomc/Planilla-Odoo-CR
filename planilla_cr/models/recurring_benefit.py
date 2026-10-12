from odoo import models, fields, api
from odoo.exceptions import ValidationError


class RecurringBenefit(models.Model):
    """Beneficios/deducciones fijos por empleado, se aplican automaticamente en cada boleta."""
    _name = 'planilla.recurring.benefit'
    _description = 'Beneficio o Deduccion Recurrente por Empleado'
    _order = 'employee_id, sequence'

    employee_id    = fields.Many2one('hr.employee', required=True, ondelete='cascade', index=True)
    sequence       = fields.Integer(default=10)
    deduction_code_id = fields.Many2one(
        'planilla.deduction.code', string='Codigo', required=True
    )
    name           = fields.Char(string='Concepto', required=True)
    benefit_type   = fields.Selection([
        ('income',     'Ingreso / Beneficio'),
        ('deduction',  'Deduccion'),
    ], string='Tipo', required=True, default='deduction')
    amount_type    = fields.Selection([
        ('fixed',      'Monto Fijo'),
        ('percentage', 'Porcentaje del Salario Bruto'),
    ], string='Calculo', required=True, default='fixed')
    amount         = fields.Monetary(string='Monto (CRC)', currency_field='currency_id')
    percentage     = fields.Float(string='Porcentaje (%)', digits=(5, 2))
    currency_id    = fields.Many2one(related='employee_id.currency_id', store=True)
    active         = fields.Boolean(default=True)
    date_start     = fields.Date(string='Vigente desde')
    date_end       = fields.Date(string='Vigente hasta',
                                  help='Dejar vacio para aplicar indefinidamente.')
    note           = fields.Char(string='Nota')

    @api.constrains('amount', 'percentage', 'amount_type')
    def _check_amounts(self):
        """FIX BUG-N08 v52: Validar que monto/porcentaje no sean negativos.
        Un monto negativo crearia ingresos o deducciones negativas en la boleta,
        causando que el neto se calcule incorrectamente.
        """
        for rec in self:
            if rec.amount_type == 'fixed' and rec.amount < 0:
                raise ValidationError(
                    f'El monto del beneficio/deduccion "{rec.name}" no puede ser negativo '
                    f'(valor: CRC{rec.amount:,.2f}). '
                    f'Si desea reducir, use el tipo de linea correcto.'
                )
            if rec.amount_type == 'percentage' and rec.percentage <= 0:
                raise ValidationError(
                    f'El porcentaje del beneficio/deduccion "{rec.name}" debe ser mayor a 0 '
                    f'(valor: {rec.percentage:.2f}%). '
                    f'Ingrese un valor positivo.'
                )

    def get_amount_for_salary(self, gross_salary):
        """Retorna el monto a aplicar dado el salario bruto."""
        self.ensure_one()
        if self.amount_type == 'fixed':
            return self.amount
        return round(gross_salary * self.percentage / 100.0, 2)
