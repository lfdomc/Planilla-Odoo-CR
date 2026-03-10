from odoo import models, fields, api
from dateutil.relativedelta import relativedelta
from datetime import date


class ScheduleType(models.Model):
    _name = 'planilla.schedule.type'
    _description = 'Tipo de Horario'

    name = fields.Char(string='Nombre', required=True)
    code = fields.Char(string='Código')
    active = fields.Boolean(default=True)
    hours_per_day = fields.Float(string='Horas por Día', default=8.0)
    hours_per_week = fields.Float(string='Horas por Semana', default=40.0)
    days_per_week = fields.Integer(string='Días por Semana', default=5)
    overtime_factor = fields.Float(
        string='Factor Horas Extras',
        default=1.5,
        help='Multiplicador para el cálculo de horas extras (ej: 1.5 = 150%)'
    )
    description = fields.Text(string='Descripción')


class PayrollCalendar(models.Model):
    _name = 'planilla.calendar'
    _description = 'Calendarización de Planilla'
    _inherit = ['mail.thread']

    name = fields.Char(string='Nombre', required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', string='Compañía',
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
        string='Día de Pago',
        help='Día del mes en que se realiza el pago (para pagos mensuales/quincenales)'
    )
    second_payment_day = fields.Integer(
        string='Segundo Día de Pago',
        help='Segundo día de pago (solo para pagos quincenales)'
    )

    deduction_code_ids = fields.Many2many(
        'planilla.deduction.code',
        'calendar_deduction_rel',
        'calendar_id', 'deduction_id',
        string='Deducciones Aplicables'
    )

    note = fields.Text(string='Notas')

    def get_period_dates(self, ref_date=None):
        """Devuelve fecha de inicio y fin del período actual según la frecuencia."""
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
