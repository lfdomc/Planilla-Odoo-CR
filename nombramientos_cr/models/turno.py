from odoo import models, fields, api
from odoo.exceptions import ValidationError
from . import constants as C


class NombramientoTurno(models.Model):
    """
    Turno individual dentro de un nombramiento.
    Representa un día de trabajo con horas específicas.
    """
    _name = 'nombramientos.turno'
    _description = 'Turno de Nombramiento'
    _order = 'date, hour_start'
    _sql_constraints = [
        ('unique_turno_date',
         'unique(nombramiento_id, date)',
         'Ya existe un turno para este nombramiento en esa fecha.'),
    ]

    @api.constrains('nombramiento_id', 'date', 'hour_start', 'hour_end', 'state')
    def _check_no_overlap(self):
        for rec in self:
            if rec.state == 'absent' or not rec.date:
                continue
            emp_id = rec.nombramiento_id.employee_id.id
            # Buscar todos los turnos del mismo empleado en la misma fecha
            otros = self.search([
                ('id', '!=', rec.id),
                ('date', '=', rec.date),
                ('state', '!=', 'absent'),
                ('nombramiento_id.employee_id', '=', emp_id),
            ])
            for otro in otros:
                # Verificar traslape de horarios
                if rec.hour_start < otro.hour_end and rec.hour_end > otro.hour_start:
                    emp_name = rec.nombramiento_id.employee_id.name
                    sede1 = rec.nombramiento_id.branch_id.name or 'sin sede'
                    sede2 = otro.nombramiento_id.branch_id.name or 'sin sede'
                    raise ValidationError(
                        f'{emp_name} ya tiene un turno el {rec.date} '
                        f'de {self._fmt_hour(otro.hour_start)} a {self._fmt_hour(otro.hour_end)} '
                        f'en "{sede2}". No puede estar también en "{sede1}" '
                        f'de {self._fmt_hour(rec.hour_start)} a {self._fmt_hour(rec.hour_end)} '
                        f'al mismo tiempo.'
                    )

    @staticmethod
    def _fmt_hour(h):
        hh = int(h % 24)
        mm = int(round((h % 1) * 60))
        ampm = 'am' if hh < 12 else 'pm'
        hh12 = hh % 12 or 12
        return f'{hh12}:{mm:02d}{ampm}'

    nombramiento_id = fields.Many2one(
        'nombramientos.nombramiento', string='Nombramiento',
        required=True, ondelete='cascade', index=True,
    )
    employee_id = fields.Many2one(
        'hr.employee', string='Empleado',
        related='nombramiento_id.employee_id', store=True, readonly=True,
    )
    date = fields.Date(
        string='Fecha', required=True,
    )
    day_name = fields.Char(
        string='Día', compute='_compute_day_name', store=True,
    )
    branch_id = fields.Many2one(
        'planilla.branch', string='Sucursal',
        related='nombramiento_id.branch_id', store=True,
        help='Se hereda del nombramiento. Puede sobreescribirse para movilidad.',
    )
    branch_override_id = fields.Many2one(
        'planilla.branch', string='Sucursal para este turno',
        help='Si el empleado va a una sede diferente solo este día.',
    )
    effective_branch_id = fields.Many2one(
        'planilla.branch', string='Sede Efectiva',
        compute='_compute_effective_branch', store=True,
    )
    hour_start = fields.Float(
        string='Hora Entrada', default=8.0,
        help='Formato decimal: 8.5 = 8:30 AM',
    )
    hour_end = fields.Float(
        string='Hora Salida', default=17.0,
    )
    hours = fields.Float(
        string='Horas Trabajadas',
        compute='_compute_hours', store=True, tracking=True,
        help='Se calcula automáticamente de hora entrada/salida. Se puede editar manualmente.',
    )
    hourly_rate = fields.Monetary(
        string='Tarifa/Hora (₡)',
        currency_field='currency_id',
        store=True, tracking=True,
        help='Tarifa por hora para este turno. Se hereda del nombramiento pero puede modificarse.',
    )
    currency_id = fields.Many2one(
        'res.currency',
        related='nombramiento_id.currency_id',
    )
    amount = fields.Monetary(
        string='Monto (₡)', currency_field='currency_id',
        compute='_compute_amount', store=True,
    )
    sede_turno_id = fields.Many2one(
        'nombramientos.shift.template',
        string='Turno / Plantilla',
        ondelete='set null',
    )
    turno_name = fields.Char(
        related='sede_turno_id.name', store=True, string='Nombre del Turno',
    )
    notes = fields.Char(string='Nota del turno')

    # Ausentismo
    state = fields.Selection([
        ('present',  'Presente'),
        ('absent',   'Ausente'),
        ('partial',  'Parcial'),
        ('holiday',  'Feriado'),
    ], default='present', string='Asistencia', required=True, tracking=True)

    @api.depends('date')
    def _compute_day_name(self):
        days = ['Lunes', 'Martes', 'Miércoles', 'Jueves',
                'Viernes', 'Sábado', 'Domingo']
        for rec in self:
            if rec.date:
                rec.day_name = days[rec.date.weekday()]
            else:
                rec.day_name = ''

    @api.depends('branch_override_id', 'nombramiento_id.branch_id')
    def _compute_effective_branch(self):
        for rec in self:
            rec.effective_branch_id = rec.branch_override_id or rec.nombramiento_id.branch_id

    @api.depends('hour_start', 'hour_end', 'state')
    def _compute_hours(self):
        # L2: descuento de almuerzo según configuración (Art. 136 CT)
        cfg = self.env['nombramientos.config'].search(
            [('company_id', '=', self.env.company.id)], limit=1)
        lunch_hours = 0.0
        if cfg and cfg.apply_lunch_break:
            lunch_hours = cfg.lunch_break_minutes / 60.0

        for rec in self:
            if rec.state == 'absent':
                rec.hours = 0.0
            elif rec.hour_end > rec.hour_start:
                raw = rec.hour_end - rec.hour_start
                # Descontar almuerzo solo en jornadas >= mínimo definido (Art. 136 CT)
                if lunch_hours > 0 and raw >= C.MIN_HORAS_CON_DESCANSO:
                    raw = max(0.0, raw - lunch_hours)
                rec.hours = round(raw, 2)
            else:
                rec.hours = 0.0

    @api.depends('hours', 'hourly_rate')
    def _compute_amount(self):
        for rec in self:
            rec.amount = round(rec.hours * rec.hourly_rate, 2)

    @api.constrains('date', 'nombramiento_id')
    def _check_date_in_range(self):
        for rec in self:
            nom = rec.nombramiento_id
            if nom.date_start and nom.date_end:
                if not (nom.date_start <= rec.date <= nom.date_end):
                    raise ValidationError(
                        f'La fecha {rec.date} está fuera del rango del nombramiento '
                        f'({nom.date_start} – {nom.date_end}).')
