from odoo import models, fields, api


class RecurringBenefit(models.Model):
    """Beneficios/deducciones fijos por empleado, se aplican automáticamente en cada boleta."""
    _name = 'planilla.recurring.benefit'
    _description = 'Beneficio o Deducción Recurrente por Empleado'
    _order = 'employee_id, sequence'

    employee_id    = fields.Many2one('hr.employee', required=True, ondelete='cascade')
    sequence       = fields.Integer(default=10)
    deduction_code_id = fields.Many2one(
        'planilla.deduction.code', string='Código', required=True
    )
    name           = fields.Char(string='Concepto', required=True)
    benefit_type   = fields.Selection([
        ('income',     'Ingreso / Beneficio'),
        ('deduction',  'Deducción'),
    ], string='Tipo', required=True, default='deduction')
    amount_type    = fields.Selection([
        ('fixed',      'Monto Fijo'),
        ('percentage', 'Porcentaje del Salario Bruto'),
    ], string='Cálculo', required=True, default='fixed')
    amount         = fields.Monetary(string='Monto (₡)', currency_field='currency_id')
    percentage     = fields.Float(string='Porcentaje (%)', digits=(5, 2))
    currency_id    = fields.Many2one(related='employee_id.currency_id', store=True)
    active         = fields.Boolean(default=True)
    date_start     = fields.Date(string='Vigente desde')
    date_end       = fields.Date(string='Vigente hasta',
                                  help='Dejar vacío para aplicar indefinidamente.')
    note           = fields.Char(string='Nota')

    def get_amount_for_salary(self, gross_salary):
        """Retorna el monto a aplicar dado el salario bruto."""
        self.ensure_one()
        if self.amount_type == 'fixed':
            return self.amount
        return round(gross_salary * self.percentage / 100.0, 2)
