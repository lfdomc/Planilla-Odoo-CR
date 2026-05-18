from odoo import models, fields, api
from odoo.exceptions import ValidationError


class NombramientosConfig(models.Model):
    """Configuración global del módulo de Nombramientos por empresa."""
    _name = 'nombramientos.config'
    _description = 'Configuración de Nombramientos'
    _rec_name = 'company_id'
    _sql_constraints = [
        ('unique_company',
         'unique(company_id)',
         'Ya existe una configuración para esta empresa.'),
    ]

    company_id = fields.Many2one(
        'res.company', string='Empresa',
        required=True, default=lambda self: self.env.company,
    )

    # ── Modo de pago ────────────────────────────────────────────────────────
    payment_mode = fields.Selection([
        ('hourly',  'Por horas trabajadas (sin salario base)'),
        ('fixed',   'Salario fijo + horas extras por exceso de turno'),
    ], string='Modo de Pago', default='hourly', required=True,
       help='Hourly: bruto = horas × tarifa. Fixed: salario normal + HE si supera la jornada.')

    # ── Frecuencia ──────────────────────────────────────────────────────────
    payment_frequency = fields.Selection([
        ('weekly',    'Semanal'),
        ('biweekly',  'Quincenal'),
        ('monthly',   'Mensual'),
    ], string='Frecuencia de Pago', default='weekly', required=True)

    payroll_calendar_id = fields.Many2one(
        'planilla.calendar', string='Calendarización de Planilla',
        help='Calendarización que se usará al generar planillas de nombramientos.',
    )

    # ── Jornada por defecto ─────────────────────────────────────────────────
    default_shift_type = fields.Selection([
        ('day',   'Diurna (máx 8h/día · 48h/sem · HE +50%)'),
        ('mixed', 'Mixta (máx 7h/día · 42h/sem · HE +50%)'),
        ('night', 'Nocturna (máx 6h/día · 36h/sem · HE +75%)'),
    ], string='Tipo de Jornada por Defecto', default='day', required=True)

    # ── Horario por defecto ─────────────────────────────────────────────────
    default_shift_template_id = fields.Many2one(
        'nombramientos.shift.template',
        string='Plantilla de Horario por Defecto',
        domain="[('company_id','=',company_id)]",
    )

    # ── Reglas de horas extras ──────────────────────────────────────────────
    auto_overtime = fields.Boolean(
        string='Calcular HE automáticamente',
        default=True,
        help='Si el turno supera el máximo diario, las horas adicionales se crean como HE.',
    )
    pay_double_rest_day = fields.Boolean(
        string='Pago doble en día de descanso (Art. 152 CT)',
        default=True,
    )
    pay_double_holiday = fields.Boolean(
        string='Pago doble en feriados nacionales (Art. 148 CT)',
        default=True,
    )

    # ── Descansos ───────────────────────────────────────────────────────────
    lunch_break_minutes = fields.Integer(
        string='Tiempo de almuerzo (minutos)',
        default=60,
        help='Minutos de descanso no pagado por turno ≥ 6h. Art. 136 CT requiere mín. 30 min.',
    )
    apply_lunch_break = fields.Boolean(
        string='Descontar almuerzo del tiempo pagado',
        default=False,
        help='Si activo, las horas pagadas = horas totales − tiempo de almuerzo.',
    )

    # ── Viáticos ────────────────────────────────────────────────────────────
    viatico_enabled = fields.Boolean(
        string='Aplicar viático por cambio de sede',
        default=False,
    )
    viatico_amount = fields.Monetary(
        string='Monto de viático por día (₡)',
        currency_field='currency_id',
    )
    currency_id = fields.Many2one(
        'res.currency',
        default=lambda self: self.env.company.currency_id,
    )

    # ── Notificaciones ──────────────────────────────────────────────────────
    notify_employee = fields.Boolean(
        string='Notificar al empleado al asignar turno',
        default=False,
        help='Envía correo/notificación Odoo al empleado cuando se confirma su nombramiento.',
    )
    notify_manager = fields.Boolean(
        string='Notificar al encargado en cambios de cuadrante',
        default=False,
    )
    manager_id = fields.Many2one(
        'res.users', string='Encargado de Nombramientos',
        help='Usuario que recibe notificaciones de cambios.',
    )

    @api.constrains('lunch_break_minutes')
    def _check_lunch(self):
        for rec in self:
            if rec.apply_lunch_break and rec.lunch_break_minutes < 30:
                raise ValidationError(
                    'Art. 136 CT: el descanso mínimo es 30 minutos.')

    @api.onchange('payment_frequency')
    def _onchange_payment_frequency(self):
        if self.payment_frequency:
            cal = self.env['planilla.calendar'].search([
                ('frequency', '=', self.payment_frequency),
                ('company_id', '=', self.env.company.id),
            ], limit=1)
            if cal:
                self.payroll_calendar_id = cal

    @api.onchange('payroll_calendar_id')
    def _onchange_payroll_calendar(self):
        if self.payroll_calendar_id and self.payroll_calendar_id.frequency:
            freq_map = {'weekly': 'weekly', 'biweekly': 'biweekly', 'monthly': 'monthly'}
            freq = freq_map.get(self.payroll_calendar_id.frequency)
            if freq:
                self.payment_frequency = freq

    def write(self, vals):
        res = super().write(vals)
        if ('payroll_calendar_id' in vals or 'payment_frequency' in vals)                 and not self.env.context.get('skip_planilla_sync'):
            self._sync_to_planilla_config()
        return res

    def _sync_to_planilla_config(self):
        # Sincronizar calendarizacion con planilla.accounting.config
        for rec in self:
            if not rec.payroll_calendar_id:
                continue
            planilla_config = self.env['planilla.accounting.config'].search([
                ('company_id', '=', rec.company_id.id),
            ], limit=1)
            if planilla_config:
                planilla_config.write({
                    'default_payroll_calendar_id': rec.payroll_calendar_id.id,
                })

    @api.model
    def get_config(self):
        # Retorna la configuración de la empresa actual.
        # Si no existe, retorna un recordset vacío en lugar de crear
        # (crear en contexto de lectura causa problemas de concurrencia).
        # La creación ocurre solo cuando el usuario entra a la vista de configuración.
        config = self.search([('company_id', '=', self.env.company.id)], limit=1)
        if not config:
            # Crear con sudo para evitar problemas de permisos y usar try/except
            # para manejar la race condition si dos usuarios crean a la vez
            try:
                config = self.sudo().create({'company_id': self.env.company.id})
            except Exception:
                config = self.search([('company_id', '=', self.env.company.id)], limit=1)
        return config

    def max_daily_hours(self):
        """Horas máximas diarias según tipo de jornada."""
        return {'day': 8, 'mixed': 7, 'night': 6}.get(
            self.default_shift_type, 8)

    def overtime_factor(self):
        """Factor de recargo para HE según jornada."""
        return 1.75 if self.default_shift_type == 'night' else 1.5


class ShiftTemplate(models.Model):
    """Plantillas de horario reutilizables."""
    _name = 'nombramientos.shift.template'
    _description = 'Plantilla de Turno'
    _order = 'sequence, name'
    _rec_name = 'display_name'

    name = fields.Char(string='Nombre', required=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company,
    )
    hour_start = fields.Float(
        string='Hora Entrada', required=True, default=8.0,
        help='Formato decimal: 8.5 = 8:30am',
    )
    hour_end = fields.Float(
        string='Hora Salida', required=True, default=17.0,
    )
    shift_type = fields.Selection([
        ('day',   'Diurna'),
        ('mixed', 'Mixta'),
        ('night', 'Nocturna'),
    ], string='Tipo de Jornada', default='day', required=True)
    color = fields.Integer(string='Color', default=1)
    active = fields.Boolean(default=True)

    display_name = fields.Char(
        string='Nombre Completo', compute='_compute_display_name',
        store=True,
    )

    @api.depends('name', 'hour_start', 'hour_end', 'shift_type')
    def _compute_display_name(self):
        type_labels = {'day': 'Diurno', 'mixed': 'Mixto', 'night': 'Nocturno'}
        for rec in self:
            def fmt(h):
                hh = int(h)
                mm = int(round((h % 1) * 60))
                ampm = 'am' if hh < 12 else 'pm'
                hh12 = hh % 12 or 12
                return f'{hh12}:{mm:02d}{ampm}'
            label = type_labels.get(rec.shift_type, '')
            rec.display_name = (
                f'{rec.name}  ·  {fmt(rec.hour_start)} – {fmt(rec.hour_end)}'
                f'  ({label})'
            )

    # ── Días de aplicación ──────────────────────────────────────────────────
    apply_monday    = fields.Boolean(default=True)
    apply_tuesday   = fields.Boolean(default=True)
    apply_wednesday = fields.Boolean(default=True)
    apply_thursday  = fields.Boolean(default=True)
    apply_friday    = fields.Boolean(default=True)
    apply_saturday  = fields.Boolean(default=False)
    apply_sunday    = fields.Boolean(default=False)

    @property
    def hours(self):
        return max(0, self.hour_end - self.hour_start)

    @property
    def hours_str(self):
        def fmt(h):
            hh, mm = int(h), int(round((h % 1) * 60))
            return f'{hh:02d}:{mm:02d}'
        return f'{fmt(self.hour_start)} – {fmt(self.hour_end)}'

    @property
    def default_days(self):
        """Lista de nombres de campo de días activos."""
        mapping = {
            0: 'apply_monday', 1: 'apply_tuesday', 2: 'apply_wednesday',
            3: 'apply_thursday', 4: 'apply_friday',
            5: 'apply_saturday', 6: 'apply_sunday',
        }
        return [v for k, v in mapping.items() if getattr(self, v, False)]
