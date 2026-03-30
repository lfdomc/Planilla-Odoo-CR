import logging
from odoo import models, fields, api
from odoo.exceptions import ValidationError, UserError
from . import planilla_const as K

_logger = logging.getLogger(__name__)


class PlanillaChargeType(models.Model):
    """
    Catalogo de Tipos de Cobro al Empleado -- planilla.charge.type
    ==============================================================
    Define los conceptos que pueden cobrarse al empleado via boleta:
    almuerzos, productos, uniformes, servicios, etc.

    Cada tipo tiene:
      - Precio unitario de referencia (editable por cobro individual)
      - Porcentaje de subsidio patronal (0% = empleado paga todo,
        100% = empresa paga todo, sin cargo al empleado)
      - Modo de calculo: fijo por periodo o por unidades/dias
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
        string='Codigo', required=True,
        help='Codigo interno unico. Ej: ALMUERZO, PRODUCTO, UNIFORME'
    )
    description = fields.Text(string='Descripcion / Politica')
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    deduction_code_id = fields.Many2one(
        'planilla.deduction.code',
        string='Codigo de Deduccion',
        required=True,
        help='Codigo contable que se usara al crear la linea de deduccion en la boleta.'
    )

    charge_mode = fields.Selection([
        ('fixed',    'Monto Fijo por Periodo'),
        ('per_unit', 'Por Unidades / Dias'),
    ], string='Modo de Cobro', required=True, default='fixed',
        help='Fijo: se cobra un monto fijo por periodo (ej. plan de almuerzo mensual).\n'
             'Por unidades: monto = cantidad x precio unitario (ej. dias asistidos al comedor).')

    default_unit_price = fields.Monetary(
        string='Precio Unitario (CRC)',
        currency_field='currency_id',
        help='Precio por unidad o monto fijo del periodo. Editable en cada cobro individual.'
    )
    currency_id = fields.Many2one(
        'res.currency',
        default=lambda self: self.env.company.currency_id,
        required=True
    )
    unit_label = fields.Char(
        string='Unidad', default='dias',
        help='Etiqueta de la unidad de medida. Ej: dias, unidades, litros, kits'
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
        help='Si esta activo, el valor subsidiado por la empresa se considera\n'
             'salario en especie (Art. 166 CT) y se incluye en la base CCSS.\n'
             'Consulte con su asesor legal antes de activar.'
    )

    company_id = fields.Many2one(
        'res.company', string='Compania',
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
                    f'Ya existe un tipo de cobro con el codigo "{rec.code}". '
                    f'El codigo debe ser unico por compania.'
                )

    def action_view_charges(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Cobros -- {self.name}',
            'res_model': 'planilla.employee.charge',
            'view_mode': 'list,form',
            'domain': [('charge_type_id', '=', self.id)],
            'context': {'default_charge_type_id': self.id},
        }


class PlanillaEmployeeCharge(models.Model):
    """
    Cobro al Empleado por Periodo -- planilla.employee.charge
    =========================================================
    Registra un cobro especifico a un empleado en un periodo determinado.
    El sync _sync_employee_charges() lo aplica automaticamente a la boleta.

    Flujo de estados:
      draft -> approved -> applied (boleta pagada)
                       -> cancelled

    Cubre los tres esquemas de cobro:
      1. Monto fijo por periodo  (charge_mode='fixed',    quantity=1)
      2. Variable por unidades   (charge_mode='per_unit', quantity=N)
      3. Subsidio parcial/total  (subsidy_pct define que % paga la empresa)
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
        'res.company', string='Compania',
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

    # -- Periodo -------------------------------------------------------
    date_from = fields.Date(string='Desde', required=True)
    date_to   = fields.Date(string='Hasta', required=True)

    # -- Calculo del monto ---------------------------------------------
    quantity = fields.Float(
        string='Cantidad', default=1.0, digits=(10, 2),
        help='Numero de unidades/dias. Para monto fijo dejar en 1.'
    )
    unit_price = fields.Monetary(
        string='Precio Unitario (CRC)', currency_field='currency_id',
        help='Precio por unidad. Se hereda del tipo de cobro pero es editable.'
    )
    subsidy_pct = fields.Float(
        string='Subsidio Patronal (%)', digits=(5, 2),
        help='Porcentaje que asume la empresa. 0% = empleado paga todo.'
    )

    total_amount = fields.Monetary(
        string='Costo Total (CRC)', currency_field='currency_id',
        compute='_compute_amounts', store=True,
        help='Costo bruto = cantidad x precio unitario'
    )
    employer_amount = fields.Monetary(
        string='Subsidio Empresa (CRC)', currency_field='currency_id',
        compute='_compute_amounts', store=True,
        help='Monto que asume la empresa = total x (subsidio% / 100)'
    )
    employee_amount = fields.Monetary(
        string='Cargo al Empleado (CRC)', currency_field='currency_id',
        compute='_compute_amounts', store=True,
        help='Monto que se descuenta en la boleta = total  subsidio empresa'
    )

    affects_ccss = fields.Boolean(
        related='charge_type_id.affects_ccss', store=True,
        help='Si esta activo, el subsidio patronal se considera salario en especie.'
    )

    # -- Recurrencia ---------------------------------------------------
    is_recurring = fields.Boolean(
        string='Cobro Recurrente',
        default=False,
        tracking=True,
        help='Si esta activo, este cobro se aplica automaticamente en cada periodo '
             'de planilla mientras este vigente (date_start-date_end).\n'
             'El cobro permanece en estado Aprobado y se reutiliza cada periodo.\n\n'
             'Si esta inactivo (cobro unico), se consume al aplicarse en la primera '
             'boleta y pasa a estado Aplicado.'
    )
    recurrence_end = fields.Date(
        string='Vigente hasta',
        help='Fecha limite de la recurrencia. Dejar vacio para aplicar indefinidamente.\n'
             'Solo aplica cuando "Cobro Recurrente" esta activo.'
    )
    applied_periods = fields.Char(
        string='Periodos Aplicados',
        readonly=True,
        help='Lista de periodos (YYYY-MM) en los que ya se aplico este cobro recurrente. '
             'Evita aplicar dos veces el mismo periodo. Formato: "2026-03,2026-04,..."'
    )

    # -- Estado y trazabilidad -----------------------------------------
    state = fields.Selection([
        ('draft',     'Borrador'),
        ('approved',  'Aprobado'),
        ('applied',   'Aplicado en Boleta'),
        ('cancelled', 'Cancelado'),
    ], string='Estado', default='draft', tracking=True, index=True)

    payslip_id = fields.Many2one(
        'planilla.payslip.cr', string='Ultima Boleta Aplicada',
        readonly=True, ondelete='set null',
        help='Ultima boleta en la que se aplico este cobro. '
             'Para cobros recurrentes muestra la ultima aplicacion.'
    )
    notes = fields.Text(string='Observaciones')

    # -- Helpers de recurrencia ----------------------------------------
    def _get_applied_periods_set(self) -> set:
        """Retorna el set de periodos YYYY-MM ya aplicados."""
        self.ensure_one()
        if not self.applied_periods:
            return set()
        return set(p.strip() for p in self.applied_periods.split(',') if p.strip())

    def _mark_period_applied(self, date_from) -> None:
        """Registra el periodo YYYY-MM como aplicado en el campo applied_periods."""
        self.ensure_one()
        period_key = str(date_from)[:7]  # YYYY-MM
        periods = self._get_applied_periods_set()
        periods.add(period_key)
        self.applied_periods = ','.join(sorted(periods))

    def _remove_period_applied(self, date_from) -> None:
        """
        Elimina el periodo YYYY-MM de applied_periods.
        Llamado cuando la boleta que lo aplico es cancelada o borrada.
        """
        self.ensure_one()
        period_key = str(date_from)[:7]
        periods = self._get_applied_periods_set()
        if period_key in periods:
            periods.discard(period_key)
            self.applied_periods = ','.join(sorted(periods)) if periods else False
            _logger.info(
                'planilla_cr.employee_charge: periodo huerfano "%s" eliminado de cobro "%s".',
                period_key, self.name
            )

    def _is_period_already_applied(self, date_from) -> bool:
        """
        Verifica si el periodo YYYY-MM ya fue aplicado en este cobro recurrente.

        FIX BUG-COBRO-01: Verificacion activa de huerfanos.
        Si el periodo esta en applied_periods pero NO existe una linea de deduccion
        activa (en boleta no cancelada) que lo referencie, el periodo se considera
        huerfano (la boleta original fue borrada sin cancelar). Se limpia
        automaticamente y se permite re-aplicar el cobro.
        """
        self.ensure_one()
        period_key = str(date_from)[:7]
        if period_key not in self._get_applied_periods_set():
            return False   # nunca aplicado en este periodo

        # El periodo esta marcado -> verificar que exista una boleta ACTIVA
        # (estado draft, confirmed o paid) con una linea de deduccion de este cobro.
        active_line = self.env['planilla.payslip.deduction.line'].search([
            ('employee_charge_id', '=', self.id),
            ('payslip_id.state', 'in', ('draft', 'confirmed', 'paid')),
        ], limit=1)

        if active_line:
            return True   # hay boleta activa -> periodo realmente aplicado

        # No hay boleta activa -> periodo HUERFANO (boleta borrada/cancelada sin limpiar)
        _logger.warning(
            'planilla_cr.employee_charge: periodo "%s" huerfano en cobro "%s" (ID %d). '
            'No existe boleta activa con este cobro -- limpiando y permitiendo re-aplicar.',
            period_key, self.name, self.id
        )
        self._remove_period_applied(date_from)
        return False   # permitir re-aplicar

    def action_clean_orphan_periods(self):
        """
        Boton manual: verifica y limpia periodos huerfanos en applied_periods.
        Un periodo es huerfano si no hay ninguna boleta activa (draft/confirmed/paid)
        que tenga una linea de deduccion referenciando este cobro.
        Util cuando el usuario borra boletas sin cancelarlas.
        """
        for rec in self:
            if not rec.applied_periods:
                continue
            periods = rec._get_applied_periods_set()
            # Obtener los periodos que SI tienen boleta activa
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
                    'planilla_cr.employee_charge: %d periodo(s) huerfano(s) limpiados '
                    'de cobro "%s": %s',
                    len(orphans), rec.name, orphans
                )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Periodos Huerfanos Limpiados',
                'message': 'Los periodos sin boleta activa han sido eliminados. '
                           'El cobro puede aplicarse nuevamente.',
                'type': 'success',
                'sticky': False,
            },
        }

    # -- Computed fields -----------------------------------------------
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

    # -- Onchange para heredar valores del tipo ------------------------
    @api.onchange('charge_type_id')
    def _onchange_charge_type(self):
        if self.charge_type_id:
            self.unit_price  = self.charge_type_id.default_unit_price
            self.subsidy_pct = self.charge_type_id.subsidy_pct
            if self.charge_type_id.charge_mode == 'fixed':
                self.quantity = 1.0

    # -- Constraints ---------------------------------------------------
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

    # -- Acciones de estado --------------------------------------------
    def action_approve(self):
        """Aprobar cobro para que sea sincronizado en la proxima boleta."""
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
        """Cancelar cobro. Si esta aplicado en boleta, se debe revisar manualmente."""
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

    def action_sanear_huerfanos(self):
        """
        Saneamiento masivo de cobros huerfanos -- BUG-COBRO-02.

        Un cobro es 'huerfano' cuando:
          - Cobro unico (is_recurring=False): state='applied' pero su
            payslip_id ya no existe o esta cancelada.
          - Cobro recurrente (is_recurring=True): applied_periods contiene
            periodos sin ninguna linea de deduccion activa que los respalde.

        Esta accion puede ejecutarse:
          - Desde la vista lista (seleccionando varios cobros -> Accion -> Sanear)
          - Desde el menu Configuracion -> Saneamiento de Cobros
          - Manualmente en cada cobro con el boton " Limpiar Periodos Huerfanos"

        Retorna un resumen de lo que se corrigio.
        """
        cobros_a_sanar = self if self else self.search([
            '|',
            '&', ('is_recurring', '=', False), ('state', '=', 'applied'),
            '&', ('is_recurring', '=', True),  ('applied_periods', '!=', False),
        ])

        revertidos = 0
        periodos_limpiados = 0

        for charge in cobros_a_sanar:
            if not charge.is_recurring:
                # -- Cobro unico: verificar que la boleta existe y esta activa --
                if charge.state != 'applied':
                    continue
                boleta_activa = False
                if charge.payslip_id:
                    boleta_activa = charge.payslip_id.state in ('draft', 'confirmed', 'paid')
                if not boleta_activa:
                    # No hay boleta activa -> huerfano -> revertir a approved
                    charge.write({'state': 'approved', 'payslip_id': False})
                    revertidos += 1
                    _logger.info(
                        'planilla_cr.sanear_huerfanos: cobro unico "%s" (ID %d) '
                        'revertido a "approved" -- boleta borrada o cancelada.',
                        charge.name, charge.id
                    )
            else:
                # -- Cobro recurrente: verificar cada periodo en applied_periods --
                if not charge.applied_periods:
                    continue
                periods = charge._get_applied_periods_set()
                # Obtener todos los periodos que SI tienen linea en boleta activa
                active_lines = self.env['planilla.payslip.deduction.line'].search([
                    ('employee_charge_id', '=', charge.id),
                    ('payslip_id.state', 'in', ('draft', 'confirmed', 'paid')),
                ])
                active_periods = set()
                for line in active_lines:
                    if line.payslip_id.date_from:
                        active_periods.add(str(line.payslip_id.date_from)[:7])

                orphan_periods = periods - active_periods
                if orphan_periods:
                    periods -= orphan_periods
                    charge.applied_periods = ','.join(sorted(periods)) if periods else False
                    if not charge.applied_periods:
                        charge.payslip_id = False
                    periodos_limpiados += len(orphan_periods)
                    _logger.info(
                        'planilla_cr.sanear_huerfanos: cobro recurrente "%s" (ID %d) -- '
                        '%d periodo(s) huerfano(s) eliminados: %s',
                        charge.name, charge.id, len(orphan_periods), orphan_periods
                    )

        total = revertidos + periodos_limpiados
        if total == 0:
            mensaje = 'No se encontraron cobros huerfanos. Todos los cobros aplicados tienen boleta activa.'
            msg_type = 'info'
        else:
            partes = []
            if revertidos:
                partes.append(f'{revertidos} cobro(s) unico(s) revertido(s) a "Aprobado"')
            if periodos_limpiados:
                partes.append(f'{periodos_limpiados} periodo(s) huerfano(s) limpiados en cobros recurrentes')
            mensaje = 'Saneamiento completado: ' + ' . '.join(partes) + '.'
            msg_type = 'success'

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': ' Saneamiento de Cobros Huerfanos',
                'message': mensaje,
                'type': msg_type,
                'sticky': True,
            },
        }

    def action_print_charge(self):
        """Imprimir reporte PDF del cobro."""
        return self.env.ref('planilla_cr.action_report_employee_charge').report_action(self)
