"""
planilla.embargo — Embargos Judiciales
Legislación CR: Art. 172 Código de Trabajo.
Límite: 25 % del salario neto disponible después de CCSS obrera, renta
y pensiones alimentarias (prioridad superior).
El patrono tiene responsabilidad solidaria si no aplica el embargo.
"""
from odoo import models, fields, api
from odoo.exceptions import ValidationError


class Embargo(models.Model):
    _name        = 'planilla.embargo'
    _description = 'Embargo Judicial (Art. 172 CT)'
    _inherit     = ['mail.thread', 'mail.activity.mixin']
    _order       = 'employee_id, date_start'
    _rec_name    = 'name'

    name = fields.Char(
        string='Referencia', compute='_compute_name', store=True
    )

    # ── Relaciones ─────────────────────────────────────────────────────────
    employee_id = fields.Many2one(
        'hr.employee', string='Empleado', required=True,
        ondelete='cascade', tracking=True,
        index=True,
    )
    company_id = fields.Many2one(
        related='employee_id.company_id', store=True, string='Empresa'
    )
    branch_id = fields.Many2one(
        related='employee_id.branch_id', store=True, string='Sucursal'
    )
    currency_id = fields.Many2one(
        related='employee_id.currency_id', store=True
    )

    # ── Datos judiciales ───────────────────────────────────────────────────
    numero_expediente = fields.Char(
        string='N° Expediente Judicial', required=True, tracking=True,
        help='Número de expediente del juzgado que ordenó el embargo.'
    )
    juzgado = fields.Char(
        string='Juzgado / Tribunal', required=True, tracking=True
    )
    fecha_resolucion = fields.Date(
        string='Fecha de Resolución', tracking=True
    )

    # ── Beneficiario ───────────────────────────────────────────────────────
    beneficiario_nombre = fields.Char(
        string='Nombre del Beneficiario / Acreedor', required=True
    )
    beneficiario_cuenta = fields.Char(
        string='IBAN / Cuenta del Beneficiario',
        help='IBAN costarricense o número de cuenta del acreedor.'
    )

    # ── Monto ──────────────────────────────────────────────────────────────
    calculation_type = fields.Selection([
        ('fixed',      'Monto Fijo (₡)'),
        ('percentage', 'Porcentaje del Neto Disponible'),
    ], string='Tipo de Cálculo', required=True, default='fixed', tracking=True)

    fixed_amount = fields.Monetary(
        string='Monto Fijo (₡)', currency_field='currency_id',
        help='Monto fijo mensual a descontar.'
    )
    percentage = fields.Float(
        string='Porcentaje (%)', digits=(5, 2),
        help='Porcentaje del neto disponible. Máximo legal: 25 % (Art. 172 CT).'
    )

    # ── Vigencia ───────────────────────────────────────────────────────────
    date_start = fields.Date(
        string='Vigente Desde', required=True, tracking=True
    )
    date_end = fields.Date(
        string='Vigente Hasta', tracking=True,
        help='Dejar vacío si no tiene fecha de vencimiento.'
    )

    # ── Estado ─────────────────────────────────────────────────────────────
    state = fields.Selection([
        ('active',     'Activo'),
        ('suspended',  'Suspendido'),
        ('ended',      'Finalizado'),
    ], string='Estado', default='active', required=True, tracking=True)

    active = fields.Boolean(default=True)

    note = fields.Text(string='Observaciones')

    # ── Computed ───────────────────────────────────────────────────────────
    @api.depends('employee_id', 'numero_expediente', 'beneficiario_nombre')
    def _compute_name(self):
        for rec in self:
            emp  = rec.employee_id.name or ''
            exp  = rec.numero_expediente or ''
            ben  = rec.beneficiario_nombre or ''
            rec.name = f'{emp} — {exp} ({ben})'

    # ── Validaciones ───────────────────────────────────────────────────────
    @api.constrains('percentage', 'calculation_type')
    def _check_percentage(self):
        for rec in self:
            if rec.calculation_type == 'percentage':
                if rec.percentage <= 0:
                    raise ValidationError(
                        'El porcentaje del embargo debe ser mayor a 0.'
                    )
                if rec.percentage > 25:
                    raise ValidationError(
                        f'El porcentaje del embargo ({rec.percentage:.2f} %) '
                        f'no puede superar el 25 % del salario neto disponible '
                        f'(Art. 172 Código de Trabajo CR).'
                    )

    @api.constrains('fixed_amount', 'calculation_type')
    def _check_fixed_amount(self):
        for rec in self:
            if rec.calculation_type == 'fixed' and rec.fixed_amount <= 0:
                raise ValidationError(
                    'El monto fijo del embargo debe ser mayor a ₡0.'
                )

    @api.constrains('date_start', 'date_end')
    def _check_dates(self):
        for rec in self:
            if rec.date_end and rec.date_end < rec.date_start:
                raise ValidationError(
                    '"Vigente Hasta" debe ser posterior a "Vigente Desde".'
                )

    # ── Helpers ────────────────────────────────────────────────────────────
    def compute_amount(self, neto_disponible):
        """
        Calcula el monto a descontar dado el neto disponible.
        neto_disponible = salario bruto - CCSS obrera - renta - pensiones alim.
        FIX P-02 v59: Aplicar tope 25% también para monto fijo.
        Si fixed_amount > 25% del neto, se cobra solo el máximo legal.
        """
        self.ensure_one()
        from . import planilla_const as K
        tope = round(neto_disponible * K.MAX_PCT_EMBARGO / 100.0, 2)
        if self.calculation_type == 'fixed':
            # FIX P-02 v59: respetar el tope legal incluso en monto fijo
            return min(self.fixed_amount, tope)
        monto = round(neto_disponible * self.percentage / 100.0, 2)
        return min(monto, tope)

    # ── Acciones de estado ─────────────────────────────────────────────────
    def action_suspend(self):
        self.write({'state': 'suspended'})

    def action_reactivate(self):
        self.write({'state': 'active'})

    def action_end(self):
        self.write({'state': 'ended', 'active': False})
