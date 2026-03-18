"""
planilla.bono — Bonos e Incentivos por Empleado
Legislación CR aplicable:
  - Bonos salariales (productividad, asistencia, antigüedad): Art. 162 CT
    → afectan CCSS y renta; se integran al salario para aguinaldo/cesantía.
  - Subsidio de transporte: exento CCSS/renta hasta ₡74 000/mes (Reglamento 2023).
  - Subsidio alimentación en dinero: afecto CCSS y renta.
  - Subsidio alimentación en especie (comedor): exento Art. 5 Ley 7983.
  - Gastos de representación debidamente documentados: exento CCSS (Art. 5 Ley 7983).
"""
from odoo import models, fields, api
from odoo.exceptions import ValidationError
from . import planilla_const as K
# FIX P-01 v59: usar K.TOPE_TRANSPORTE en lugar de constante local
# Eliminada: TOPE_TRANSPORTE = 74_000.0 (duplicaba planilla_const.K.TOPE_TRANSPORTE)


class Bono(models.Model):
    _name        = 'planilla.bono'
    _description = 'Bono / Incentivo por Empleado'
    _inherit     = ['mail.thread', 'mail.activity.mixin']
    _order       = 'employee_id, bono_type, date_start'
    _rec_name    = 'name'

    name = fields.Char(
        string='Concepto', required=True, tracking=True
    )

    # ── Relaciones ─────────────────────────────────────────────────────────
    employee_id = fields.Many2one(
        'hr.employee', string='Empleado', required=True,
        ondelete='cascade', tracking=True, index=True,
    )
    company_id = fields.Many2one(
        related='employee_id.company_id', store=True
    )
    branch_id = fields.Many2one(
        related='employee_id.branch_id', store=True
    )
    currency_id = fields.Many2one(
        related='employee_id.currency_id', store=True
    )

    # ── Tipo de bono ───────────────────────────────────────────────────────
    bono_type = fields.Selection([
        ('productividad',  'Productividad / Rendimiento'),
        ('asistencia',     'Asistencia Perfecta'),
        ('antiguedad',     'Antigüedad por Años de Servicio'),
        ('transporte',     'Subsidio de Transporte / Kilometraje'),
        ('alimentacion',   'Subsidio de Alimentación (en dinero)'),
        ('educacion',      'Subsidio Educativo'),
        ('salud',          'Subsidio de Salud / Médico'),
        ('representacion', 'Gastos de Representación'),
        ('comision',       'Comisión por Ventas'),
        ('incentivo',      'Incentivo / Premio Especial'),
        ('otro',           'Otro'),
    ], string='Tipo de Bono', required=True, default='productividad', tracking=True)

    # ── Cálculo ────────────────────────────────────────────────────────────
    amount_type = fields.Selection([
        ('fixed',      'Monto Fijo (₡)'),
        ('percentage', 'Porcentaje del Salario Base'),
    ], string='Cálculo', required=True, default='fixed')

    amount = fields.Monetary(
        string='Monto (₡)', currency_field='currency_id'
    )
    percentage = fields.Float(
        string='Porcentaje (%)', digits=(5, 2)
    )

    # ── Reglas fiscales/CCSS — se completan automáticamente por tipo ────────
    afecto_ccss = fields.Boolean(
        string='Afecto CCSS', default=True, tracking=True,
        help='Si es True, el monto se suma al salario bruto para calcular CCSS. '
             'Bonos salariales (productividad, asistencia, antigüedad) = True. '
             'Subsidio transporte (hasta tope), gastos representación = False.'
    )
    afecto_renta = fields.Boolean(
        string='Afecto Renta', default=True, tracking=True,
        help='Si es True, el monto se suma al salario gravable para calcular renta. '
             'Subsidio transporte hasta tope legal = False.'
    )
    tope_exento = fields.Monetary(
        string='Tope Exento (₡/mes)', currency_field='currency_id',
        help='Solo aplica para tipos con exención parcial (transporte). '
             'El excedente sobre este tope sí es gravable.'
    )

    # ── Vigencia ───────────────────────────────────────────────────────────
    is_recurring = fields.Boolean(
        string='Es Recurrente', default=True,
        help='Si es True, se aplica en cada boleta dentro del período de vigencia. '
             'Si es False, es un bono puntual (una sola vez).'
    )
    date_start = fields.Date(string='Vigente Desde', required=True, tracking=True)
    date_end   = fields.Date(
        string='Vigente Hasta', tracking=True,
        help='Dejar vacío para aplicar indefinidamente.'
    )

    # ── Estado ─────────────────────────────────────────────────────────────
    state = fields.Selection([
        ('active',    'Activo'),
        ('suspended', 'Suspendido'),
        ('ended',     'Finalizado'),
    ], string='Estado', default='active', required=True, tracking=True)

    active = fields.Boolean(default=True)

    note = fields.Text(string='Observaciones / Referencia')

    # ── Computed: base gravable real ────────────────────────────────────────
    monto_gravable_ccss = fields.Monetary(
        string='Monto Gravable CCSS (₡)',
        compute='_compute_montos_gravables', store=False,
        currency_field='currency_id',
        help='Porción del bono que cuenta para la base de CCSS.'
    )
    monto_gravable_renta = fields.Monetary(
        string='Monto Gravable Renta (₡)',
        compute='_compute_montos_gravables', store=False,
        currency_field='currency_id',
    )

    @api.depends('amount', 'amount_type', 'afecto_ccss', 'afecto_renta',
                 'tope_exento', 'employee_id.base_salary')
    def _compute_montos_gravables(self):
        for rec in self:
            monto = rec._get_monto_base()
            exento = rec.tope_exento or 0.0
            excedente = max(0.0, monto - exento)
            rec.monto_gravable_ccss  = excedente if not rec.afecto_ccss  else monto
            rec.monto_gravable_renta = excedente if not rec.afecto_renta else monto

    # ── Defaults automáticos según tipo ────────────────────────────────────
    @api.onchange('bono_type')
    def _onchange_bono_type(self):
        """Aplica defaults legales según el tipo de bono CR."""
        presets = {
            # tipo: (afecto_ccss, afecto_renta, tope_exento, nombre_sugerido)
            'productividad':  (True,  True,  0.0,          'Bono de Productividad'),
            'asistencia':     (True,  True,  0.0,          'Bono de Asistencia Perfecta'),
            'antiguedad':     (True,  True,  0.0,          'Bono de Antigüedad'),
            'transporte':     (False, False, K.TOPE_TRANSPORTE, 'Subsidio de Transporte'),
            'alimentacion':   (True,  True,  0.0,          'Subsidio de Alimentación'),
            'educacion':      (False, False, 0.0,          'Subsidio Educativo'),
            'salud':          (False, False, 0.0,          'Subsidio de Salud'),
            'representacion': (False, False, 0.0,          'Gastos de Representación'),
            'comision':       (True,  True,  0.0,          'Comisión por Ventas'),
            'incentivo':      (True,  True,  0.0,          'Incentivo Especial'),
            'otro':           (True,  True,  0.0,          'Bono'),
        }
        if self.bono_type in presets:
            ccss, renta, tope, nombre = presets[self.bono_type]
            self.afecto_ccss  = ccss
            self.afecto_renta = renta
            self.tope_exento  = tope
            if not self.name or self.name == 'Bono':
                self.name = nombre

    # ── Validaciones ───────────────────────────────────────────────────────
    @api.constrains('amount', 'percentage', 'amount_type')
    def _check_amounts(self):
        for rec in self:
            if rec.amount_type == 'fixed' and rec.amount <= 0:
                raise ValidationError(
                    f'El monto del bono "{rec.name}" debe ser mayor a ₡0.'
                )
            if rec.amount_type == 'percentage' and rec.percentage <= 0:
                raise ValidationError(
                    f'El porcentaje del bono "{rec.name}" debe ser mayor a 0 %.'
                )

    @api.constrains('date_start', 'date_end')
    def _check_dates(self):
        for rec in self:
            if rec.date_end and rec.date_end < rec.date_start:
                raise ValidationError(
                    '"Vigente Hasta" debe ser posterior a "Vigente Desde".'
                )

    # ── Helpers ────────────────────────────────────────────────────────────
    def _get_monto_base(self):
        """Calcula el monto bruto del bono dado el salario base del empleado."""
        self.ensure_one()
        if self.amount_type == 'fixed':
            return self.amount
        base_sal = self.employee_id.base_salary or 0.0
        return round(base_sal * self.percentage / 100.0, 2)

    def get_amount_for_payslip(self, gross_salary=0.0):
        """
        Retorna (monto_total, monto_gravable_ccss, monto_gravable_renta).
        El payslip lo usa para sumar el bono al bruto según las reglas fiscales.
        """
        self.ensure_one()
        monto = self._get_monto_base()
        exento = self.tope_exento or 0.0

        if self.afecto_ccss:
            grav_ccss = monto
        else:
            grav_ccss = max(0.0, monto - exento)

        if self.afecto_renta:
            grav_renta = monto
        else:
            grav_renta = max(0.0, monto - exento)

        return monto, grav_ccss, grav_renta

    # ── Acciones de estado ─────────────────────────────────────────────────
    def action_suspend(self):
        self.write({'state': 'suspended'})

    def action_reactivate(self):
        self.write({'state': 'active'})

    def action_end(self):
        self.write({'state': 'ended', 'active': False})
