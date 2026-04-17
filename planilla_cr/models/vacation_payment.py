from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError
from datetime import date
import logging
from dateutil.relativedelta import relativedelta as rdelta
from . import planilla_const as K

_logger = logging.getLogger(__name__)


class VacationPayment(models.Model):
    _name = 'planilla.vacation.payment'
    _description = 'Pago de Vacaciones'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Referencia', compute='_compute_name', store=True)
    employee_id = fields.Many2one(
        'hr.employee', string='Empleado', required=True, tracking=True, index=True
    )
    branch_id = fields.Many2one(
        related='employee_id.branch_id', string='Sucursal', store=True
    )

    vacation_type = fields.Selection([
        ('disfrutadas', 'Vacaciones Disfrutadas'),
        ('proporcionales', 'Vacaciones Proporcionales'),
        ('adelanto', 'Adelanto de Vacaciones'),
    ], string='Tipo', default='disfrutadas', required=True, tracking=True)

    date_start = fields.Date(string='Fecha Inicio', required=True,
                             default=fields.Date.today)
    date_end = fields.Date(string='Fecha Fin', required=True,
                           default=fields.Date.today)
    days = fields.Integer(string='Dias', compute='_compute_days', store=True)

    # Segun Codigo de Trabajo CR: 2 semanas (12 dias habiles) por ano trabajado
    days_accrued = fields.Float(
        string='Dias Disponibles',
        compute='_compute_days_accrued',
        help='Dias de vacaciones disponibles segun hr_employee (descuenta vacaciones tomadas)'
    )

    daily_salary = fields.Monetary(
        string='Salario Diario', currency_field='currency_id',
        compute='_compute_daily_salary', store=True
    )
    currency_id = fields.Many2one(related='employee_id.currency_id', store=True)
    total_amount = fields.Monetary(
        string='Monto Total', currency_field='currency_id',
        compute='_compute_total', store=True
    )

    state = fields.Selection([
        ('draft', 'Borrador'),
        ('approved', 'Aprobado'),
        ('paid', 'Pagado'),
        ('cancelled', 'Cancelado'),
    ], string='Estado', default='draft', tracking=True)

    # -- Metodo de pago Art. 153-156 CT -----------------------------
    payment_method = fields.Selection([
        ('disfrutadas', 'Dias disfrutados (descuento de salario)'),
        ('dinero',      'Pago en dinero (Art. 156 CT -- acuerdo mutuo)'),
        ('mixto',       'Mixto: parte disfrutada + parte en dinero'),
    ], string='Metodo de Pago', default='disfrutadas', tracking=True,
        help='Art. 156 CT: solo se pueden pagar en dinero con acuerdo de ambas partes')

    # Calculo promedio ultimas 4 semanas (Art. 153 CT)
    # FIX NEW-05 v54: avg_last_4_weeks ahora es compute (antes era manual).
    # Se calcula automaticamente como el promedio de los ultimos 4 registros
    # de salary_history del empleado. Si no hay historial, cae al salario diario normal.
    avg_last_4_weeks = fields.Monetary(
        string='Salario Diario Promedio Hist. (Art. 153 CT)', currency_field='currency_id',  # FIX-I1: daily rate
        compute='_compute_avg_last_4_weeks', store=True, readonly=False,
        help='Promedio del salario bruto de las ultimas 4 semanas antes del inicio de vacaciones.\n'
             'Se calcula automaticamente desde el historial salarial (boletas pagadas).\n'
             'Incluye salario base + horas extras + comisiones de cada boleta.\n'
             'Puede editarse manualmente si necesita ajustar el calculo.\n'
             'Obligatorio para empleados con salario variable (Art. 153 CT).'
    )
    use_average = fields.Boolean(
        string='Usar Promedio 4 Semanas (Art. 153 CT)',
        default=False,
        help='OBLIGATORIO para empleados con comisiones, horas extras recurrentes u otros ingresos variables.\n'
             'Se activa automaticamente si el empleado tiene el flag "Salario Variable" activo.\n'
             'Art. 153 CT: durante vacaciones el trabajador recibe el promedio de lo devengado\n'
             'en las ultimas 4 semanas antes del inicio de las vacaciones.'
    )
    # Campo informativo: indica si el promedio difiere significativamente del salario base
    avg_vs_base_diff_pct = fields.Float(
        string='Diferencia Promedio vs Base (%)',
        compute='_compute_avg_vs_base',
        help='Diferencia porcentual entre el promedio 4 semanas y el salario base diario.\n'
             'Si es mayor a 5%, el sistema advierte que se debe usar el promedio.'
    )

    @api.depends('avg_last_4_weeks', 'daily_salary')
    def _compute_avg_vs_base(self):
        for rec in self:
            if rec.daily_salary and rec.daily_salary > 0 and rec.avg_last_4_weeks:
                rec.avg_vs_base_diff_pct = round(
                    abs(rec.avg_last_4_weeks - rec.daily_salary) / rec.daily_salary * 100, 1
                )
            else:
                rec.avg_vs_base_diff_pct = 0.0

    @api.depends('employee_id', 'date_start')
    def _compute_avg_last_4_weeks(self):
        """
        Art. 153 CT CR: calcula el salario diario promedio de las ultimas 4 boletas pagadas.

        FIX-I1: El campo almacena la TARIFA DIARIA promedio (avg_monthly / 30),
        NO la tarifa semanal. _compute_total multiplica este valor por los dias
        de vacaciones -- igual que hace con daily_salary -- asi ambas rutas son
        intercambiables y consistentes.

        Error anterior: se guardaba avg_monthly * 12/52 (tarifa semanal  115k
        para salario de 500k) y se multiplicaba por dias -> 12 dias * 115k = 1.38M
        en lugar de 12 dias * 16,667 = 200k. Overpayment de 7x cuando use_average=True.

        El historial salarial guarda el gross_salary MENSUAL real de cada boleta --
        que incluye salario base + HE + comisiones + bonos. El promedio de esos 4
        registros da el salario mensual promedio representativo, que dividido entre
        30 da la tarifa diaria correcta para el calculo de vacaciones.
        """
        for rec in self:
            if not rec.employee_id or not rec.date_start:
                rec.avg_last_4_weeks = 0.0
                continue
            # Usar salario mensual del empleado directamente
            avg_monthly = rec.employee_id.base_salary or 0.0
