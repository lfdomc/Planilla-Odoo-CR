import logging
from odoo import models, fields, api
from odoo.exceptions import ValidationError, UserError
from . import planilla_const as K

_logger = logging.getLogger(__name__)


class PlanillaChargeType(models.Model):
    """
    Catálogo de Tipos de Cobro al Empleado — planilla.charge.type
    ==============================================================
    Define los conceptos que pueden cobrarse al empleado vía boleta:
    almuerzos, productos, uniformes, servicios, etc.

    Cada tipo tiene:
      - Precio unitario de referencia (editable por cobro individual)
      - Porcentaje de subsidio patronal (0% = empleado paga todo,
        100% = empresa paga todo, sin cargo al empleado)
      - Modo de cálculo: fijo por período o por unidades/días
      - Indicador si el beneficio en especie afecta base CCSS (Art. 166 CT)
    """
    _name = 'planilla.charge.type'
    _description = 'Tipo de Cobro al Empleado'
    _order = 'sequence, name'

    name = fields.Char(
        string='Nombre', required=True,
        help='Ej: Almuerzo Comedor, Producto de Tienda, Uniforme, Seguro Voluntario'
    )
    code = fields.Char(
        string='Código', required=True,
        help='Código interno único. Ej: ALMUERZO, PRODUCTO, UNIFORME'
    )
    description = fields.Text(string='Descripción / Política')
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    deduction_code_id = fields.Many2one(
        'planilla.deduction.code',
        string='Código de Deducción',
        required=True,
        help='Código contable que se usará al crear la línea de deducción en la boleta.'
    )

    charge_mode = fields.Selection([
        ('fixed',    'Monto Fijo por Período'),
        ('per_unit', 'Por Unidades / Días'),
    ], string='Modo de Cobro', required=True, default='fixed',
        help='Fijo: se cobra un monto fijo por período (ej. plan de almuerzo mensual).\n'
             'Por unidades: monto = cantidad × precio unitario (ej. días asistidos al comedor).')

    default_unit_price = fields.Monetary(
        string='Precio Unitario (₡)',
        currency_field='currency_id',
        help='Precio por unidad o monto fijo del período. Editable en cada cobro individual.'
    )
    currency_id = fields.Many2one(
        'res.currency',
        default=lambda self: self.env.company.currency_id,
        required=True
    )
    unit_label = fields.Char(
        string='Unidad', default='días',
        help='Etiqueta de la unidad de medida. Ej: días, unidades, litros, kits'
    )

    subsidy_pct = fields.Float(
        string='Subsidio Patronal (%)', default=0.0, digits=(5, 2),
        help='Porcentaje del costo que asume la empresa.\n'
             '  0%  = empleado paga el costo total\n'
             ' 50%  = empresa subsidia la mitad\n'
             '100%  = beneficio gratuito para el empleado (sin cobro en boleta)'
    )

    affects_ccss = fields.Boolean(
        string='Afecta Base CCSS',
        default=False,
        help='Si está activo, el valor subsidiado por la empresa se considera\n'
             'salario en especie (Art. 166 CT) y se incluye en la base CCSS.\n'
             'Consulte con su asesor legal antes de activar.'
    )

    company_id = fields.Many2one(
        'res.company', string='Compañía',
        default=lambda self: self.env.company
    )

    # Contador de cobros activos para este tipo
    charge_count = fields.Integer(
        string='Cobros Registrados',
        compute='_compute_charge_count'
    )

    @api.depends()
    def _compute_charge_count(self):
        for rec in self:
            rec.charge_count = self.env['planilla.employee.charge'].search_count([
                ('charge_type_id', '=', rec.id),
                ('state', '!=', 'cancelled'),
            ])

    @api.constrains('subsidy_pct')
    def _check_subsidy_pct(self):
        for rec in self:
            if not (0.0 <= rec.subsidy_pct <= 100.0):
                raise ValidationError(
                    f'El subsidio patronal debe estar entre 0% y 100% '
                    f'(valor ingresado: {rec.subsidy_pct:.2f}%).'
                )

    @api.constrains('code')
    def _check_code_unique(self):
        for rec in self:
            domain = [
                ('code', '=', rec.code),
                ('id', '!=', rec.id),
                ('company_id', 'in', [rec.company_id.id, False]),
            ]
            if self.search(domain, limit=1):
                raise ValidationError(
                    f'Ya existe un tipo de cobro con el código "{rec.code}". '
                    f'El código debe ser único por compañía.'
                )

    def action_view_charges(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Cobros — {self.name}',
            'res_model': 'planilla.employee.charge',
            'view_mode': 'list,form',
            'domain': [('charge_type_id', '=', self.id)],
            'context': {'default_charge_type_id': self.id},
        }


class PlanillaEmployeeCharge(models.Model):
    """
    Cobro al Empleado por Período — planilla.employee.charge
    =========================================================
    Registra un cobro específico a un empleado en un período determinado.
    El sync _sync_employee_charges() lo aplica automáticamente a la boleta.

    Flujo de estados:
      draft → approved → applied (boleta pagada)
                       → cancelled

    Cubre los tres esquemas de cobro:
      1. Monto fijo por período  (charge_mode='fixed',    quantity=1)
      2. Variable por unidades   (charge_mode='per_unit', quantity=N)
      3. Subsidio parcial/total  (subsidy_pct define qué % paga la empresa)
    """
    _name = 'planilla.employee.charge'
    _description = 'Cobro al Empleado'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_from desc, employee_id'

    name = fields.Char(
        string='Referencia', compute='_compute_name', store=True
    )
    employee_id = fields.Many2one(
        'hr.employee', string='Empleado', required=True,
        ondelete='restrict', index=True, tracking=True
    )
    company_id = fields.Many2one(
        'res.company', string='Compañía',
        required=True, default=lambda self: self.env.company
    )
    currency_id = fields.Many2one(
        related='employee_id.currency_id', store=True
    )
    branch_id = fields.Many2one(
        related='employee_id.branch_id', string='Sucursal', store=True
    )

    charge_type_id = fields.Many2one(
        'planilla.charge.type', string='Tipo de Cobro',
        required=True, ondelete='restrict', tracking=True
    )
    charge_mode = fields.Selection(
        related='charge_type_id.charge_mode', store=True
    )
    unit_label = fields.Char(related='charge_type_id.unit_label', store=True)

    # ── Período ───────────────────────────────────────────────────────
    date_from = fields.Date(string='Desde', required=True)
    date_to   = fields.Date(string='Hasta', required=True)

    # ── Cálculo del monto ─────────────────────────────────────────────
    quantity = fields.Float(
        string='Cantidad', default=1.0, digits=(10, 2),
        help='Número de unidades/días. Para monto fijo dejar en 1.'
    )
    unit_price = fields.Monetary(
        string='Precio Unitario (₡)', currency_field='currency_id',
        help='Precio por unidad. Se hereda del tipo de cobro pero es editable.'
    )
    subsidy_pct = fields.Float(
        string='Subsidio Patronal (%)', digits=(5, 2),
        help='Porcentaje que asume la empresa. 0% = empleado paga todo.'
    )

    total_amount = fields.Monetary(
        string='Costo Total (₡)', currency_field='currency_id',
        compute='_compute_amounts', store=True,
        help='Costo bruto = cantidad × precio unitario'
    )
    employer_amount = fields.Monetary(
        string='Subsidio Empresa (₡)', currency_field='currency_id',
        compute='_compute_amounts', store=True,
        help='Monto que asume la empresa = total × (subsidio% / 100)'
    )
    employee_amount = fields.Monetary(
        string='Cargo al Empleado (₡)', currency_field='currency_id',
        compute='_compute_amounts', store=True,
        help='Monto que se descuenta en la boleta = total − subsidio empresa'
    )

    affects_ccss = fields.Boolean(
        related='charge_type_id.affects_ccss', store=True,
        help='Si está activo, el subsidio patronal se considera salario en especie.'
    )

    # ── Recurrencia ───────────────────────────────────────────────────
    is_recurring = fields.Boolean(
        string='Cobro Recurrente',
        default=False,
        tracking=True,
        help='Si está activo, este cobro se aplica automáticamente en cada período '
             'de planilla mientras esté vigente (date_start–date_end).\n'
             'El cobro permanece en estado Aprobado y se reutiliza cada período.\n\n'
             'Si está inactivo (cobro único), se consume al aplicarse en la primera '
             'boleta y pasa a estado Aplicado.'
    )
    recurrence_end = fields.Date(
        string='Vigente hasta',
        help='Fecha límite de la recurrencia. Dejar vacío para aplicar indefinidamente.\n'
             'Solo aplica cuando "Cobro Recurrente" está activo.'
    )
    applied_periods = fields.Char(
        string='Períodos Aplicados',
        readonly=True,
        help='Lista de períodos (YYYY-MM) en los que ya se aplicó este cobro recurrente. '
             'Evita aplicar dos veces el mismo período. Formato: "2026-03,2026-04,..."'
    )

    # ── Estado y trazabilidad ─────────────────────────────────────────
    state = fields.Selection([
        ('draft',     'Borrador'),
        ('approved',  'Aprobado'),
        ('applied',   'Aplicado en Boleta'),
        ('cancelled', 'Cancelado'),
    ], string='Estado', default='draft', tracking=True, index=True)

    payslip_id = fields.Many2one(
        'planilla.payslip.cr', string='Última Boleta Aplicada',
        readonly=True, ondelete='set null',
        help='Última boleta en la que se aplicó este cobro. '
             'Para cobros recurrentes muestra la última aplicación.'
    )
    notes = fields.Text(string='Observaciones')

    # ── Helpers de recurrencia ────────────────────────────────────────
    def _get_applied_periods_set(self) -> set:
        """Retorna el set de períodos YYYY-MM ya aplicados."""
        self.ensure_one()
        if not self.applied_periods:
            return set()
        return set(p.strip() for p in self.applied_periods.split(',') if p.strip())

    def _mark_period_applied(self, date_from) -> None:
        """Registra el período YYYY-MM como aplicado en el campo applied_periods."""
        self.ensure_one()
        period_key = str(date_from)[:7]  # YYYY-MM
        periods = self._get_applied_periods_set()
        periods.add(period_key)
        self.applied_periods = ','.join(sorted(periods))

    def _remove_period_applied(self, date_from) -> None:
        """
        Elimina el período YYYY-MM de applied_periods.
        Llamado cuando la boleta que lo aplicó es cancelada o borrada.
        """
        self.ensure_one()
        period_key = str(date_from)[:7]
        periods = self._get_applied_periods_set()
        if period_key in periods:
            periods.discard(period_key)
            self.applied_periods = ','.join(sorted(periods)) if periods else False
            _logger.info(
                'planilla_cr.employee_charge: período huérfano "%s" eliminado de cobro "%s".',
                period_key, self.name
            )

    def _is_period_already_applied(self, date_from) -> bool:
        """
        Verifica si el período YYYY-MM ya fue aplicado en este cobro recurrente.

        FIX BUG-COBRO-01: Verificación activa de huérfanos.
        Si el período está en applied_periods pero NO existe una línea de deducción
        activa (en boleta no cancelada) que lo referencie, el período se considera
        huérfano (la boleta original fue borrada sin cancelar). Se limpia
        automáticamente y se permite re-aplicar el cobro.
        """
        self.ensure_one()
        period_key = str(date_from)[:7]
        if period_key not in self._get_applied_periods_set():
            return False   # nunca aplicado en este período

        # El período está marcado → verificar que exista una boleta ACTIVA
        # (estado draft, confirmed o paid) con una línea de deducción de este cobro.
        active_line = self.env['planilla.payslip.deduction.line'].search([
            ('employee_charge_id', '=', self.id),
            ('payslip_id.state', 'in', ('draft', 'confirmed', 'paid')),
        ], limit=1)

        if active_line:
            return True   # hay boleta activa → período realmente aplicado

        # No hay boleta activa → período HUÉRFANO (boleta borrada/cancelada sin limpiar)
        _logger.warning(
            'planilla_cr.employee_charge: período "%s" huérfano en cobro "%s" (ID %d). '
            'No existe boleta activa con este cobro — limpiando y permitiendo re-aplicar.',
            period_key, self.name, self.id
        )
        self._remove_period_applied(date_from)
        return False   # permitir re-aplicar

    def action_clean_orphan_periods(self):
        """
        Botón manual: verifica y limpia períodos huérfanos en applied_periods.
        Un período es huérfano si no hay ninguna boleta activa (draft/confirmed/paid)
        que tenga una línea de deducción referenciando este cobro.
        Útil cuando el usuario borra boletas sin cancelarlas.
        """
        for rec in self:
            if not rec.applied_periods:
                continue
            periods = rec._get_applied_periods_set()
            # Obtener los períodos que SÍ tienen boleta activa
            active_lines = self.env['planilla.payslip.deduction.line'].search([
                ('employee_charge_id', '=', rec.id),
                ('payslip_id.state', 'in', ('draft', 'confirmed', 'paid')),
            ])
            active_periods = set()
            for line in active_lines:
                if line.payslip_id.date_from:
                    active_periods.add(str(line.payslip_id.date_from)[:7])

            orphans = periods - active_periods
            if orphans:
                periods -= orphans
                rec.applied_periods = ','.join(sorted(periods)) if periods else False
                _logger.info(
                    'planilla_cr.employee_charge: %d período(s) huérfano(s) limpiados '
                    'de cobro "%s": %s',
                    len(orphans), rec.name, orphans
                )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Períodos Huérfanos Limpiados',
                'message': 'Los períodos sin boleta activa han sido eliminados. '
                           'El cobro puede aplicarse nuevamente.',
                'type': 'success',
                'sticky': False,
            },
        }

    # ── Computed fields ───────────────────────────────────────────────
    @api.depends('employee_id', 'charge_type_id', 'date_from')
    def _compute_name(self):
        for rec in self:
            emp  = rec.employee_id.name or ''
            tipo = rec.charge_type_id.name or ''
            mes  = str(rec.date_from)[:7] if rec.date_from else ''
            rec.name = f'COB - {emp} - {tipo} - {mes}'

    @api.depends('quantity', 'unit_price', 'subsidy_pct')
    def _compute_amounts(self):
        for rec in self:
            total    = round((rec.quantity or 0.0) * (rec.unit_price or 0.0), 2)
            employer = round(total * (rec.subsidy_pct or 0.0) / 100.0, 2)
            rec.total_amount    = total
            rec.employer_amount = employer
            rec.employee_amount = round(total - employer, 2)

    # ── Onchange para heredar valores del tipo ────────────────────────
    @api.onchange('charge_type_id')
    def _onchange_charge_type(self):
        if self.charge_type_id:
            self.unit_price  = self.charge_type_id.default_unit_price
            self.subsidy_pct = self.charge_type_id.subsidy_pct
            if self.charge_type_id.charge_mode == 'fixed':
                self.quantity = 1.0

    # ── Constraints ───────────────────────────────────────────────────
    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for rec in self:
            if rec.date_from and rec.date_to and rec.date_from > rec.date_to:
                raise ValidationError(
                    'La fecha de inicio no puede ser mayor a la fecha de fin.'
                )

    @api.constrains('quantity')
    def _check_quantity(self):
        for rec in self:
            if rec.quantity <= 0:
                raise ValidationError(
                    f'La cantidad debe ser mayor a 0 (valor: {rec.quantity}).'
                )

    @api.constrains('subsidy_pct')
    def _check_subsidy(self):
        for rec in self:
            if not (0.0 <= rec.subsidy_pct <= 100.0):
                raise ValidationError(
                    f'El subsidio patronal debe estar entre 0% y 100% '
                    f'(valor: {rec.subsidy_pct:.2f}%).'
                )

    # ── Acciones de estado ────────────────────────────────────────────
    def action_approve(self):
        """Aprobar cobro para que sea sincronizado en la próxima boleta."""
        for rec in self:
            if rec.state != 'draft':
                raise UserError(
                    f'Solo se pueden aprobar cobros en borrador '
                    f'(estado actual: {rec.state}).'
                )
            if rec.employee_amount <= 0 and rec.employer_amount <= 0:
                raise UserError(
                    f'El cobro "{rec.name}" tiene monto cero. '
                    f'Verifique la cantidad y el precio unitario.'
                )
        self.write({'state': 'approved'})
        _logger.info(
            'planilla_cr.employee_charge.approve: %d cobro(s) aprobado(s) por %s',
            len(self), self.env.user.name
        )

    def action_cancel(self):
        """Cancelar cobro. Si está aplicado en boleta, se debe revisar manualmente."""
        for rec in self:
            if not rec.is_recurring and rec.state == 'applied' and rec.payslip_id:
                raise UserError(
                    f'El cobro "{rec.name}" ya fue aplicado en la boleta '
                    f'"{rec.payslip_id.name}". Para cancelarlo primero cancele '
                    f'o reactive la boleta a borrador.'
                )
        self.write({'state': 'cancelled', 'payslip_id': False})

    def action_reset_to_draft(self):
        """Reactivar cobro cancelado a borrador."""
        for rec in self:
            if rec.state not in ('cancelled',):
                raise UserError(
                    'Solo se pueden reactivar cobros cancelados.'
                )
        self.write({'state': 'draft'})

    def action_print_charge(self):
        """Imprimir reporte PDF del cobro."""
        return self.env.ref('planilla_cr.action_report_employee_charge').report_action(self)
