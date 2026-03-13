from odoo import models, fields, api
from odoo.models import Constraint
from odoo.exceptions import ValidationError


class PensionAlimentaria(models.Model):
    _name = 'planilla.pension.alimentaria'
    _description = 'Pensión Alimentaria'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'employee_id, date_start'
    _unique_pension_employee_expediente = Constraint(
        'UNIQUE(employee_id, numero_expediente)',
        'Ya existe una pensión alimentaria con ese número de expediente para este empleado. Verifique el número de expediente antes de continuar.'
    )



    name = fields.Char(string='Referencia', compute='_compute_name', store=True)

    employee_id = fields.Many2one(
        'hr.employee', string='Empleado', required=True, tracking=True
    )
    branch_id = fields.Many2one(
        related='employee_id.branch_id', string='Sucursal', store=True
    )

    # ── Datos judiciales ────────────────────────────────────────────
    numero_expediente = fields.Char(
        string='N° Expediente Judicial', required=True, tracking=True
    )
    juzgado = fields.Char(
        string='Juzgado de Familia', tracking=True
    )
    fecha_resolucion = fields.Date(
        string='Fecha Resolución', tracking=True
    )

    # ── Beneficiario ────────────────────────────────────────────────
    beneficiario_nombre = fields.Char(
        string='Nombre Beneficiario', required=True
    )
    beneficiario_relacion = fields.Selection([
        ('hijo', 'Hijo/a'),
        ('conyuge', 'Cónyuge / Conviviente'),
        ('padre', 'Padre'),
        ('madre', 'Madre'),
        ('otro', 'Otro'),
    ], string='Relación', required=True, default='hijo')
    beneficiario_cuenta = fields.Char(
        string='IBAN / Cuenta Beneficiario',
        help='Cuenta donde se deposita la pensión (IBAN CR o número de cuenta)'
    )

    # ── Monto ───────────────────────────────────────────────────────
    calculation_type = fields.Selection([
        ('fixed',      'Monto Fijo (₡)'),
        ('percentage', 'Porcentaje del Salario Bruto (%)'),
    ], string='Tipo de Cálculo', required=True, default='fixed', tracking=True)

    fixed_amount = fields.Monetary(
        string='Monto Fijo (₡)', currency_field='currency_id',
        tracking=True
    )
    percentage = fields.Float(
        string='Porcentaje (%)', digits=(5, 2),
        tracking=True,
        help='Porcentaje del salario bruto ordenado por el juez'
    )
    currency_id = fields.Many2one(
        related='employee_id.currency_id', store=True
    )

    # ── Vigencia ────────────────────────────────────────────────────
    date_start = fields.Date(
        string='Fecha Inicio', required=True,
        default=fields.Date.today, tracking=True
    )
    date_end = fields.Date(
        string='Fecha Fin',
        help='Dejar vacío si la pensión no tiene fecha de vencimiento definida'
    )
    active = fields.Boolean(default=True, tracking=True)

    state = fields.Selection([
        ('active',    'Activa'),
        ('suspended', 'Suspendida'),
        ('ended',     'Terminada'),
    ], string='Estado', default='active', tracking=True)

    notes = fields.Text(string='Observaciones')

    @api.depends('employee_id', 'numero_expediente', 'beneficiario_nombre')
    def _compute_name(self):
        for rec in self:
            emp = rec.employee_id.name or ''
            exp = rec.numero_expediente or ''
            ben = rec.beneficiario_nombre or ''
            rec.name = f'PA — {emp} → {ben} [{exp}]'

    @api.constrains('percentage')
    def _check_percentage(self):
        for rec in self:
            if rec.calculation_type == 'percentage' and rec.percentage <= 0:
                raise ValidationError('El porcentaje debe ser mayor a 0.')

    @api.constrains('fixed_amount')
    def _check_fixed_amount(self):
        for rec in self:
            if rec.calculation_type == 'fixed' and rec.fixed_amount <= 0:
                raise ValidationError('El monto fijo debe ser mayor a 0.')

    def compute_amount(self, gross_salary):
        """Calcula el monto de pensión para un salario bruto dado."""
        self.ensure_one()
        if self.calculation_type == 'fixed':
            return self.fixed_amount
        else:
            return round(gross_salary * self.percentage / 100, 2)

    def action_suspend(self):
        self.write({'state': 'suspended'})

    def action_reactivate(self):
        self.write({'state': 'active'})

    def action_end(self):
        self.write({'state': 'ended', 'active': False})
