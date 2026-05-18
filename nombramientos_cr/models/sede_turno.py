import logging
from odoo import models, fields, api
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class NombraSedeTurno(models.Model):
    """
    Define los turnos disponibles por sede: primer, segundo, tercer turno.
    Cada turno tiene un horario de referencia configurable.
    Los turnos se usan como plantillas al asignar horarios en el calendario.
    """
    _name = 'nombramientos.sede.turno'
    _description = 'Turno de Sede'
    _order = 'branch_id, sequence'

    branch_id = fields.Many2one(
        'planilla.branch', string='Sede / Sucursal',
        required=True, ondelete='cascade', index=True,
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(string='Nombre del Turno',
                   compute='_compute_name', store=True, readonly=False,
                   help='Se auto-llena desde la plantilla. Editable si querés personalizar.')
    shift_template_id = fields.Many2one(
        'nombramientos.shift.template',
        string='Plantilla de Turno',
        help='Al seleccionar una plantilla se auto-llenan entrada, salida y jornada.',
        ondelete='set null',
    )
    hour_start = fields.Float(
        string='Hora Entrada', required=True, default=8.0,
        help='Formato decimal: 8.5 = 8:30am')
    hour_end = fields.Float(
        string='Hora Salida', required=True, default=17.0)
    shift_type = fields.Selection([
        ('day',   'Diurna'),
        ('mixed', 'Mixta'),
        ('night', 'Nocturna'),
    ], string='Tipo de Jornada', default='day', required=True)

    @api.depends('shift_template_id')
    def _compute_name(self):
        for rec in self:
            if rec.shift_template_id and not rec.name:
                rec.name = rec.shift_template_id.name
            elif not rec.name:
                rec.name = ''

    @api.onchange('shift_template_id')
    def _onchange_shift_template(self):
        if self.shift_template_id:
            tpl = self.shift_template_id
            self.hour_start = tpl.hour_start
            self.hour_end   = tpl.hour_end
            self.shift_type = tpl.shift_type
            self.name       = tpl.name
    color = fields.Integer(string='Color Distintivo', default=1)
    active = fields.Boolean(default=True)

    display_name = fields.Char(
        compute='_compute_display_name', store=True)

    @api.depends('name', 'hour_start', 'hour_end', 'branch_id', 'shift_template_id')
    def _compute_display_name(self):
        def fmt(h):
            hh = int(h % 24)
            mm = int(round((h % 1) * 60))
            ampm = 'am' if hh < 12 else 'pm'
            hh12 = hh % 12 or 12
            return f'{hh12}:{mm:02d}{ampm}'
        for rec in self:
            rec.display_name = (
                f'{rec.name}  ·  {fmt(rec.hour_start)} – {fmt(rec.hour_end)}'
            )

    @api.constrains('hour_start', 'hour_end')
    def _check_hours(self):
        for rec in self:
            if rec.hour_end <= rec.hour_start and not (
                    rec.hour_end + 24 > rec.hour_start):
                raise ValidationError(
                    f'Turno {rec.name}: la hora de salida debe ser '
                    f'posterior a la de entrada.')
