"""
leave_cr.py — Licencias Especiales con Base Legal (Costa Rica)
==============================================================
Gestiona todas las licencias laborales distintas a incapacidades médicas
y vacaciones anuales, según el Código de Trabajo CR y leyes especiales.

Licencias implementadas:
  DUELO         — Ley 8698 Art. 37 bis CT: 3 días hábiles con goce (cónyuge/hijo/padre/madre)
                  Otros parientes: a criterio patronal (sin goce o con goce voluntario)
  PATERNIDAD    — Ley 8107 / Art. 95 CT: 8 días hábiles con goce 100% cargo patrono
  MATRIMONIO    — Art. 37 CT: 2 días con goce de sueldo
  DONACION_SANGRE — Art. 37 CT: 1 día con goce por donación de sangre
  CIUDADANA     — Art. 37 CT: tiempo necesario para ejercer el voto (elecciones)
  LACTANCIA     — Art. 95 CT: períodos diarios para lactancia (mínimo 1 año)
  ADOPCION      — Ley 9406: 3 meses con goce, equiparable a maternidad
  ESTUDIO       — Convenciones colectivas / acuerdo patronal: sin base legal obligatoria
  SINDICAL      — Art. 60 Constitución / Convenio OIT 135: dirigentes sindicales
  PATERNIDAD_ADOPCION — Ley 9406: 8 días hábiles con goce (padre adoptivo)
  SIN_GOCE      — Art. 85 CT: permiso sin goce de sueldo, acuerdo entre partes

Integración:
  - Se vincula automáticamente a la boleta de pago del período
  - Las licencias CON goce generan gasto patronal (debit cuenta 630800)
  - Las licencias SIN goce generan deducción en la boleta (category='licencia_sin_goce')
  - El asiento contable se extiende via _sync_licencias() en el sync mixin
"""

from odoo import models, fields, api
from odoo.models import Constraint
from odoo.exceptions import ValidationError
from . import planilla_const as K

# ── Mapa legal: tipo → (días_max, con_goce, base_legal) ──────────────────────
LEAVE_LEGAL_MAP = {
    'duelo_primer_grado':  (3,  True,  'Art. 37 bis CT / Ley 8698 — 3 días hábiles cónyuge/hijo/padre/madre/hermano'),
    'duelo_otro':          (0,  False, 'Art. 37 bis CT — otros parientes: a criterio patronal'),
    'paternidad':          (8,  True,  'Art. 95 CT / Ley 8107 — 8 días hábiles con goce 100%'),
    'paternidad_adopcion': (8,  True,  'Ley 9406 Art. 8 — 8 días hábiles padre adoptivo'),
    'matrimonio':          (2,  True,  'Art. 37 CT — 2 días con goce'),
    'donacion_sangre':     (1,  True,  'Art. 37 CT — 1 día con goce por donación'),
    'ciudadana':           (0,  True,  'Art. 37 CT — tiempo para ejercer voto'),
    'lactancia':           (0,  True,  'Art. 95 CT — períodos diarios, mínimo 1 año post-parto'),
    'adopcion':            (90, True,  'Ley 9406 — 3 meses con goce equiparable a maternidad'),
    'sindical':            (0,  True,  'Art. 60 Constitución / Convenio OIT 135'),
    'estudio':             (0,  False, 'Acuerdo patronal — sin base legal obligatoria'),
    'sin_goce':            (0,  False, 'Art. 85 CT — permiso sin goce de sueldo por acuerdo'),
    'otro_con_goce':       (0,  True,  'Licencia pagada por política interna de la empresa'),
    'otro_sin_goce':       (0,  False, 'Licencia sin goce por acuerdo entre las partes'),
}


class LeaveCR(models.Model):
    """
    planilla.leave.cr — Licencias especiales con base legal CR.
    """
    _name = 'planilla.leave.cr'
    _description = 'Licencia Especial CR'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_start desc, employee_id'

    # Constraint: evitar duplicado del mismo tipo en la misma fecha para el mismo empleado
    _unique_leave = Constraint(
        'UNIQUE(employee_id, leave_type, date_start)',
        'Ya existe una licencia del mismo tipo para este empleado en esa fecha de inicio.'
    )

    # ── Identificación ────────────────────────────────────────────────────────
    name = fields.Char(
        string='Referencia', compute='_compute_name', store=True
    )
    employee_id = fields.Many2one(
        'hr.employee', string='Empleado', required=True,
        tracking=True, index=True, ondelete='restrict'
    )
    branch_id = fields.Many2one(
        related='employee_id.branch_id', string='Sucursal', store=True
    )
    company_id = fields.Many2one(
        'res.company', string='Compañía',
        required=True, default=lambda self: self.env.company
    )
    currency_id = fields.Many2one(
        related='employee_id.currency_id', store=True
    )

    # ── Tipo y fechas ─────────────────────────────────────────────────────────
    leave_type = fields.Selection([
        # ── Con goce legal obligatorio ──────────────────────────────────────
        ('duelo_primer_grado',  'Duelo — 1er grado (cónyuge/hijo/padre/madre/hermano)'),
        ('paternidad',          'Paternidad — 8 días hábiles (Ley 8107)'),
        ('paternidad_adopcion', 'Paternidad Adopción — 8 días hábiles (Ley 9406)'),
        ('matrimonio',          'Matrimonio — 2 días (Art. 37 CT)'),
        ('donacion_sangre',     'Donación de Sangre — 1 día (Art. 37 CT)'),
        ('ciudadana',           'Licencia Ciudadana — Derecho al voto (Art. 37 CT)'),
        ('lactancia',           'Lactancia — Períodos diarios (Art. 95 CT)'),
        ('adopcion',            'Adopción — 3 meses con goce (Ley 9406)'),
        ('sindical',            'Sindical — Dirigentes (Art. 60 Const.)'),
        ('otro_con_goce',       'Otra con Goce — Política interna empresa'),
        # ── Sin goce / a criterio patronal ─────────────────────────────────
        ('duelo_otro',          'Duelo — Otro pariente (a criterio patronal)'),
        ('estudio',             'Estudio — Acuerdo patronal'),
        ('sin_goce',            'Permiso Sin Goce de Sueldo (Art. 85 CT)'),
        ('otro_sin_goce',       'Otra Sin Goce — Acuerdo entre partes'),
    ], string='Tipo de Licencia', required=True, tracking=True)

    date_start = fields.Date(
        string='Fecha Inicio', required=True, tracking=True,
        default=fields.Date.today
    )
    date_end = fields.Date(
        string='Fecha Fin', required=True, tracking=True,
        default=fields.Date.today
    )
    days = fields.Integer(
        string='Días Calendario', compute='_compute_days', store=True
    )
    working_days = fields.Integer(
        string='Días Hábiles',
        help='Complete manualmente si necesita distinguir días hábiles de calendario. '
             'Para duelo/paternidad/matrimonio la ley habla de días HÁBILES. '
             'Si queda en 0, el sistema usa los días calendario para el cálculo.'
    )

    # ── Pariente (para duelo) ─────────────────────────────────────────────────
    relative_name = fields.Char(
        string='Nombre del Fallecido / Familiar',
        help='Para licencias de duelo: nombre del familiar para el expediente.'
    )
    relative_relationship = fields.Selection([
        ('conyuge',   'Cónyuge / Compañero(a)'),
        ('hijo',      'Hijo(a)'),
        ('padre',     'Padre'),
        ('madre',     'Madre'),
        ('hermano',   'Hermano(a)'),
        ('abuelo',    'Abuelo(a)'),
        ('nieto',     'Nieto(a)'),
        ('suegro',    'Suegro(a)'),
        ('cunado',    'Cuñado(a)'),
        ('otro',      'Otro'),
    ], string='Parentesco')

    # ── Goce de sueldo ────────────────────────────────────────────────────────
    has_salary = fields.Boolean(
        string='Con Goce de Sueldo',
        compute='_compute_has_salary', store=True,
        help='Determinado por la ley según el tipo de licencia. '
             'Para tipos "a criterio patronal" puede editarse manualmente.'
    )
    # Permite al patrono pagar voluntariamente un duelo de 2do grado, etc.
    has_salary_override = fields.Boolean(
        string='Goce Voluntario (Patrono)',
        help='Active si la empresa decide pagar voluntariamente esta licencia '
             'aunque la ley no lo obligue (ej: duelo de segundo grado).'
    )

    # ── Montos calculados ─────────────────────────────────────────────────────
    daily_salary = fields.Monetary(
        string='Salario Diario', currency_field='currency_id',
        compute='_compute_amounts', store=True
    )
    leave_amount = fields.Monetary(
        string='Monto a Pagar / Descontar', currency_field='currency_id',
        compute='_compute_amounts', store=True,
        help='Con goce: monto a pagar al empleado (gasto patronal).\n'
             'Sin goce: monto a descontar de la boleta.'
    )

    # ── Documentación ─────────────────────────────────────────────────────────
    certificate_number = fields.Char(
        string='N° Documento / Certificado',
        help='Acta de defunción, partida de nacimiento, certificado médico, etc.'
    )
    note = fields.Text(string='Observaciones')

    # ── Flujo de estados ─────────────────────────────────────────────────────
    state = fields.Selection([
        ('draft',     'Borrador'),
        ('approved',  'Aprobado'),
        ('paid',      'Procesado en Planilla'),
        ('cancelled', 'Cancelado'),
    ], string='Estado', default='draft', tracking=True)

    # ── Vinculación a boleta ──────────────────────────────────────────────────
    payslip_id = fields.Many2one(
        'planilla.payslip.cr', string='Boleta de Pago',
        readonly=True, index=True
    )

    # ── Base legal (informativo) ──────────────────────────────────────────────
    legal_basis = fields.Char(
        string='Base Legal', compute='_compute_legal_basis', store=False
    )
    max_days_info = fields.Integer(
        string='Días Máximos (Ley)', compute='_compute_legal_basis', store=False
    )

    # ═════════════════════════════════════════════════════════════════════════
    # COMPUTOS
    # ═════════════════════════════════════════════════════════════════════════

    @api.depends('employee_id', 'leave_type', 'date_start')
    def _compute_name(self):
        for rec in self:
            emp = rec.employee_id.name or ''
            tipo = dict(rec._fields['leave_type'].selection).get(rec.leave_type, '') if rec.leave_type else ''
            date_str = str(rec.date_start) if rec.date_start else ''
            rec.name = f'LIC - {emp} - {tipo[:20]} - {date_str}'

    @api.depends('date_start', 'date_end')
    def _compute_days(self):
        for rec in self:
            if rec.date_start and rec.date_end:
                rec.days = (rec.date_end - rec.date_start).days + 1
            else:
                rec.days = 0

    @api.depends('leave_type')
    def _compute_has_salary(self):
        for rec in self:
            info = LEAVE_LEGAL_MAP.get(rec.leave_type or '', (0, False, ''))
            rec.has_salary = info[1]

    @api.depends('leave_type')
    def _compute_legal_basis(self):
        for rec in self:
            info = LEAVE_LEGAL_MAP.get(rec.leave_type or '', (0, False, ''))
            rec.max_days_info = info[0]
            rec.legal_basis = info[2]

    @api.depends('employee_id', 'date_start', 'days', 'working_days',
                 'has_salary', 'has_salary_override', 'leave_type')
    def _compute_amounts(self):
        for rec in self:
            if not rec.employee_id or not rec.employee_id.base_salary:
                rec.daily_salary = 0.0
                rec.leave_amount = 0.0
                continue

            daily = round(rec.employee_id.base_salary / K.DIAS_MES, 2)
            rec.daily_salary = daily

            # Días efectivos: preferir working_days si está ingresado, sino días calendario
            effective_days = rec.working_days if rec.working_days > 0 else (rec.days or 0)

            pays = rec.has_salary or rec.has_salary_override
            rec.leave_amount = round(daily * effective_days, 2) if effective_days > 0 else 0.0

    # ═════════════════════════════════════════════════════════════════════════
    # VALIDACIONES
    # ═════════════════════════════════════════════════════════════════════════

    @api.constrains('date_start', 'date_end')
    def _check_dates(self):
        for rec in self:
            if rec.date_start and rec.date_end and rec.date_end < rec.date_start:
                raise ValidationError(
                    f'La Fecha Fin ({rec.date_end}) no puede ser anterior '
                    f'a la Fecha Inicio ({rec.date_start}).'
                )

    @api.constrains('leave_type', 'days', 'working_days')
    def _check_legal_limits(self):
        """Advierte si se exceden los días máximos establecidos por ley."""
        for rec in self:
            info = LEAVE_LEGAL_MAP.get(rec.leave_type or '', (0, False, ''))
            max_days = info[0]
            if max_days <= 0:
                continue  # Sin límite definido o a criterio patronal
            effective = rec.working_days if rec.working_days > 0 else rec.days
            if effective > max_days:
                tipo_label = dict(rec._fields['leave_type'].selection).get(rec.leave_type, rec.leave_type)
                raise ValidationError(
                    f'La licencia "{tipo_label}" no puede exceder {max_days} días hábiles '
                    f'según {info[2]}. Días ingresados: {effective}.\n\n'
                    f'Si necesita extender la licencia más allá del límite legal, '
                    f'use el tipo "Permiso Sin Goce de Sueldo" para los días adicionales.'
                )

    @api.constrains('leave_type', 'relative_name')
    def _check_duelo_fields(self):
        for rec in self:
            if rec.leave_type in ('duelo_primer_grado', 'duelo_otro'):
                if not rec.relative_name:
                    raise ValidationError(
                        'Para licencias de duelo debe indicar el nombre del familiar fallecido '
                        '(campo "Nombre del Fallecido / Familiar").'
                    )

    # ═════════════════════════════════════════════════════════════════════════
    # ACCIONES
    # ═════════════════════════════════════════════════════════════════════════

    def action_approve(self):
        for rec in self:
            if rec.state != 'draft':
                raise ValidationError('Solo se pueden aprobar licencias en estado Borrador.')
        self.write({'state': 'approved'})

    def action_cancel(self):
        for rec in self:
            if rec.state == 'paid':
                raise ValidationError(
                    'No se puede cancelar una licencia que ya fue procesada en planilla. '
                    'Contacte al administrador de planilla.'
                )
        self.write({'state': 'cancelled', 'payslip_id': False})

    def action_reset_draft(self):
        for rec in self:
            if rec.state == 'paid':
                raise ValidationError('No se puede reabrir una licencia ya procesada en planilla.')
        self.write({'state': 'draft'})

    @api.onchange('leave_type')
    def _onchange_leave_type(self):
        """Sugerir fecha fin según días máximos de ley cuando el tipo cambia."""
        if not self.leave_type or not self.date_start:
            return
        info = LEAVE_LEGAL_MAP.get(self.leave_type, (0, False, ''))
        max_days = info[0]
        if max_days > 0 and self.date_start:
            import datetime
            self.date_end = self.date_start + datetime.timedelta(days=max_days - 1)
        # Limpiar campos de pariente si no es duelo
        if self.leave_type not in ('duelo_primer_grado', 'duelo_otro'):
            self.relative_name = False
            self.relative_relationship = False
