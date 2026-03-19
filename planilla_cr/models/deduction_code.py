from odoo import models, fields, api
from odoo.models import Constraint
from odoo.exceptions import ValidationError


class DeductionCode(models.Model):
    _name = 'planilla.deduction.code'
    _description = 'Código de Deducción'
    _inherit = ['mail.thread']
    # FIX-G5: constraint company-aware para soporte multi-empresa.
    # La versión anterior (UNIQUE(code)) impedía que dos empresas distintas
    # tuvieran el mismo código (ej: ambas con 'CCSS_OBR'), lo cual es correcto
    # cuando company_id es NULL (código global), pero bloqueaba codes por empresa.
    # La lógica correcta: code es único POR empresa (o único globalmente si company_id es NULL).
    # Odoo maneja esto con @api.constrains a nivel ORM para mayor flexibilidad.
    _unique_deduction_code = Constraint(
        'UNIQUE(code, company_id)',
        'El código de deducción ya existe para esta empresa. Cada código debe ser único por empresa.'
    )



    name = fields.Char(string='Nombre', required=True, tracking=True)
    code = fields.Char(string='Código', required=True, tracking=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', string='Empresa',
        default=lambda self: self.env.company,
        help='Deje vacío para que aplique a todas las empresas (código global).'
    )
    description = fields.Text(string='Descripción')

    deduction_type = fields.Selection([
        ('employee', 'Deducción Obrero'),
        ('employer', 'Cargo Patronal'),
        ('both', 'Obrero y Patronal'),
        ('other', 'Otro'),
    ], string='Tipo de Deducción', required=True, default='employee', tracking=True)

    calculation_type = fields.Selection([
        ('percentage', 'Porcentaje'),
        ('fixed', 'Monto Fijo'),
        ('table', 'Tabla Progresiva'),
    ], string='Tipo de Cálculo', required=True, default='percentage', tracking=True)

    # Porcentajes
    employee_percentage = fields.Float(
        string='% Obrero', digits=(5, 4), tracking=True,
        help='Porcentaje deducido al empleado'
    )
    employer_percentage = fields.Float(
        string='% Patronal', digits=(5, 4), tracking=True,
        help='Porcentaje a cargo del patrono'
    )
    fixed_amount = fields.Float(string='Monto Fijo', digits=(12, 2))

    # Configuración contable
    account_debit_id = fields.Many2one(
        'account.account', string='Cuenta Débito',
        help='Cuenta contable de gasto patronal'
    )
    account_credit_id = fields.Many2one(
        'account.account', string='Cuenta Crédito',
        help='Cuenta contable de pasivo (por pagar)'
    )
    account_employee_id = fields.Many2one(
        'account.account', string='Cuenta Deducción Obrero',
        help='Cuenta donde se registra la deducción al empleado'
    )

    # Categorías predefinidas
    is_ccss = fields.Boolean(string='Es CCSS', default=False)
    is_ins = fields.Boolean(string='Es INS', default=False)
    is_income_tax = fields.Boolean(string='Es Renta', default=False)

    # ── Tasas INS por Clase de Riesgo (solo aplica si is_ins=True) ───
    ins_rate_i   = fields.Float(string='Clase I — Oficinas (%)',          digits=(5, 4), default=0.87)
    ins_rate_ii  = fields.Float(string='Clase II — Comercio (%)',         digits=(5, 4), default=1.49)
    ins_rate_iii = fields.Float(string='Clase III — Industria liviana (%)',digits=(5, 4), default=2.47)
    ins_rate_iv  = fields.Float(string='Clase IV — Construcción (%)',     digits=(5, 4), default=4.13)
    ins_rate_v   = fields.Float(string='Clase V — Alto riesgo (%)',       digits=(5, 4), default=6.88)
    ins_valid_from = fields.Date(string='Vigente desde', help='Fecha desde la cual aplican estas tasas INS')

    def get_ins_rate(self, risk_class):
        """Retorna la tasa INS decimal para la clase de riesgo dada."""
        self.ensure_one()
        mapping = {
            'I':   self.ins_rate_i   / 100,
            'II':  self.ins_rate_ii  / 100,
            'III': self.ins_rate_iii / 100,
            'IV':  self.ins_rate_iv  / 100,
            'V':   self.ins_rate_v   / 100,
        }
        return mapping.get(risk_class, self.ins_rate_ii / 100)

    # Topes
    has_ceiling = fields.Boolean(string='Tiene Tope Salarial', default=False)
    salary_ceiling = fields.Float(string='Tope Salarial', digits=(12, 2))

    @api.constrains('employee_percentage', 'employer_percentage')
    def _check_percentages(self):
        for rec in self:
            if rec.employee_percentage < 0 or rec.employer_percentage < 0:
                raise ValidationError('Los porcentajes no pueden ser negativos.')
            if rec.employee_percentage > 100 or rec.employer_percentage > 100:
                raise ValidationError('Los porcentajes no pueden superar el 100%.')

    def compute_deduction(self, base_salary):
        """Calcula el monto de deducción dado un salario base."""
        self.ensure_one()
        if self.calculation_type == 'percentage':
            salary = min(base_salary, self.salary_ceiling) if self.has_ceiling else base_salary
            return {
                'employee': salary * (self.employee_percentage / 100),
                'employer': salary * (self.employer_percentage / 100),
            }
        elif self.calculation_type == 'fixed':
            return {'employee': self.fixed_amount, 'employer': 0.0}
        return {'employee': 0.0, 'employer': 0.0}

