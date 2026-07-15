from odoo import models, fields, api
from odoo.exceptions import ValidationError
from dateutil.relativedelta import relativedelta
from datetime import date


class ScheduleType(models.Model):
    _name = 'planilla.schedule.type'
    _description = 'Tipo de Horario'

    name = fields.Char(string='Nombre', required=True)
    code = fields.Char(string='Codigo')
    active = fields.Boolean(default=True)
    hours_per_day = fields.Float(string='Horas por Dia', default=8.0)
    hours_per_week = fields.Float(string='Horas por Semana', default=40.0)
    days_per_week = fields.Integer(string='Dias por Semana', default=5)
    # Hora de entrada/salida para detección automática de HE
    hora_entrada  = fields.Float(
        string='Hora de Entrada', default=8.0,
        help='Hora de inicio de la jornada en formato decimal (8.5 = 8:30am)'
    )
    hora_salida   = fields.Float(
        string='Hora de Salida', default=17.0,
        help='Hora de fin de la jornada en formato decimal (17.0 = 5:00pm)'
    )
    # Días laborales — para detectar HE en días de descanso
    lunes     = fields.Boolean(string='Lunes',     default=True)
    martes    = fields.Boolean(string='Martes',    default=True)
    miercoles = fields.Boolean(string='Miércoles', default=True)
    jueves    = fields.Boolean(string='Jueves',    default=True)
    viernes   = fields.Boolean(string='Viernes',   default=True)
    sabado    = fields.Boolean(string='Sábado',    default=False)
    domingo   = fields.Boolean(string='Domingo',   default=False)

    @api.onchange('lunes','martes','miercoles','jueves','viernes','sabado','domingo')
    def _onchange_working_days(self):
        """Auto-actualiza days_per_week y hours_per_week cuando cambian los días."""
        day_fields = ['lunes','martes','miercoles','jueves','viernes','sabado','domingo']
        count = sum(1 for d in day_fields if getattr(self, d, False))
        if count > 0:
            self.days_per_week = count
            hpd = self.hours_per_day or 8.0
            self.hours_per_week = round(count * hpd, 2)

    @api.onchange('hours_per_day')
    def _onchange_hours_per_day(self):
        """Auto-actualiza hours_per_week cuando cambian las horas por día."""
        dpw = self.days_per_week or 5
        hpd = self.hours_per_day or 8.0
        self.hours_per_week = round(dpw * hpd, 2)

    def action_populate_defaults(self):
        """Botón/acción para poblar días y horas desde la lista."""
        from odoo.addons.planilla_cr import hooks
        hooks._populate_schedule_defaults(self.env)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Listo',
                'message': 'Días laborales y horas actualizados en todos los horarios.',
                'type': 'success',
                'sticky': False,
            }
        }

    def is_working_day(self, date):
        """Retorna True si la fecha es un día laboral según este horario."""
        self.ensure_one()
        day_map = {0:'lunes',1:'martes',2:'miercoles',3:'jueves',
                   4:'viernes',5:'sabado',6:'domingo'}
        field = day_map.get(date.weekday(), 'lunes')
        return getattr(self, field, False)
    is_part_time = fields.Boolean(
        string='Jornada de Medio Tiempo / Parcial',
        default=False,
        help='Active esta opcion si este horario es de jornada parcial o medio tiempo. '
             'Los empleados con este horario quedan exentos de la validacion de salario '
             'minimo MTSS, ya que su salario proporcional puede ser menor al minimo completo.'
    )
    overtime_factor = fields.Float(
        string='Factor Horas Extras',
        default=1.5,
        help='Multiplicador para el calculo de horas extras (ej: 1.5 = 150%)'
    )
    description = fields.Text(string='Descripcion')

    @api.constrains('hours_per_day')
    def _check_hours_per_day(self):
        """FIX C-06 v53: Validar rango legal de horas por dia (Art. 136 CT: max 8h ordinarias)."""
        for rec in self:
            if rec.hours_per_day <= 0:
                raise ValidationError('Las horas por dia deben ser mayor a 0.')
            if rec.hours_per_day > 12:
                raise ValidationError(
                    f'Las horas por dia ({rec.hours_per_day}) superan el maximo permitido (12h). '
                    f'La jornada ordinaria maxima es 8h + 4h extras (Art. 136 y 139 CT).'
                )

    @api.constrains('days_per_week')
    def _check_days_per_week(self):
        """Validar rango de dias por semana."""
        for rec in self:
            if rec.days_per_week < 1 or rec.days_per_week > 7:
                raise ValidationError('Los dias por semana deben estar entre 1 y 7.')


class PayrollCalendar(models.Model):
    _name = 'planilla.calendar'
    _description = 'Calendarizacion de Planilla'
    _inherit = ['mail.thread']

    name = fields.Char(string='Nombre', required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', string='Compania',
        required=True, default=lambda self: self.env.company
    )
    branch_id = fields.Many2one('planilla.branch', string='Sucursal')

    frequency = fields.Selection([
        ('weekly', 'Semanal'),
        ('biweekly', 'Quincenal'),
        ('monthly', 'Mensual'),
        ('bimonthly', 'Bimensual'),
    ], string='Frecuencia de Pago', required=True, default='monthly', tracking=True)

    payment_day = fields.Integer(
        string='Dia de Pago',
        help='Dia del mes en que se realiza el pago (para pagos mensuales/quincenales)'
    )
    second_payment_day = fields.Integer(
        string='Segundo Dia de Pago',
        help='Segundo dia de pago (solo para pagos quincenales)'
    )

    deduction_code_ids = fields.Many2many(
        'planilla.deduction.code',
        'calendar_deduction_rel',
        'calendar_id', 'deduction_id',
        string='Deducciones Aplicables'
    )

    note = fields.Text(string='Notas')

    def get_period_dates(self, ref_date=None):
        """Devuelve fecha de inicio y fin del periodo actual segun la frecuencia."""
        self.ensure_one()
        today = ref_date or date.today()
        if self.frequency == 'monthly':
            start = today.replace(day=1)
            end = (start + relativedelta(months=1)) - relativedelta(days=1)
        elif self.frequency == 'weekly':
            start = today - relativedelta(days=today.weekday())
            end = start + relativedelta(days=6)
        elif self.frequency == 'biweekly':
            if today.day <= 15:
                start = today.replace(day=1)
                end = today.replace(day=15)
            else:
                start = today.replace(day=16)
                end = (today.replace(day=1) + relativedelta(months=1)) - relativedelta(days=1)
        else:
            start = today.replace(day=1)
            end = (start + relativedelta(months=1)) - relativedelta(days=1)
        return start, end
