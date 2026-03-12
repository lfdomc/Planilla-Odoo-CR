import logging
from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError
import datetime
from .closed_period import PlanillaClosedPeriod


_logger = logging.getLogger(__name__)
class PayslipCR(models.Model):
    _name = 'planilla.payslip.cr'
    _description = 'Boleta de Pago'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_to desc, employee_id'

    # M3 FIX — Constraint de BD para prevenir boletas duplicadas en concurrencia
    _sql_constraints = [
        (
            'unique_payslip_employee_period',
            'UNIQUE(employee_id, date_from, date_to)',
            'Ya existe una boleta para este empleado en el mismo período. '
            'No se pueden crear dos boletas para el mismo empleado y fechas.'
        ),
    ]

    name = fields.Char(string='Referencia', compute='_compute_name', store=True)
    employee_id = fields.Many2one(
        'hr.employee', string='Empleado', required=True, tracking=True,
        ondelete='restrict',
    )
    branch_id = fields.Many2one(related='employee_id.branch_id', string='Sucursal', store=True)
    company_id = fields.Many2one(
        'res.company', string='Compañía',
        required=True, default=lambda self: self.env.company
    )
    payroll_run_id = fields.Many2one('planilla.run.cr', string='Planilla', ondelete='cascade')
    notes = fields.Text(string='Observaciones', help='Notas internas que aparecen en el PDF de la boleta.')
    payroll_calendar_id = fields.Many2one(
        related='employee_id.payroll_calendar_id', string='Calendarización', store=True
    )
    date_from = fields.Date(string='Desde', required=True)
    date_to = fields.Date(string='Hasta', required=True)
    currency_id = fields.Many2one(related='employee_id.currency_id', store=True)

    # Ingresos
    base_salary = fields.Monetary(string='Salario Base', currency_field='currency_id', compute='_compute_base_salary', store=True)
    # ── Salario proporcional (Art. 163 CT) ───────────────────────────
    is_proportional  = fields.Boolean(
        string='Calcular Proporcional',
        help='Activar si el empleado ingresó o salió durante el período (Art. 163 CT).'
    )
    days_in_period   = fields.Integer(
        string='Días del Período', compute='_compute_proportional_days', store=True
    )
    days_worked      = fields.Integer(
        string='Días Trabajados',
        help='Días reales trabajados en el período. Se calcula automáticamente o edite manualmente.'
    )
    proportional_factor = fields.Float(
        string='Factor Proporcional', compute='_compute_proportional_days',
        store=True, digits=(4, 4)
    )

    overtime_amount = fields.Monetary(string='Monto Horas Extras', currency_field='currency_id', compute='_compute_extras', store=True)
    vacation_amount = fields.Monetary(string='Monto Vacaciones', currency_field='currency_id', compute='_compute_extras', store=True)
    other_income = fields.Monetary(string='Otros Ingresos', currency_field='currency_id')
    gross_salary = fields.Monetary(string='Salario Bruto', currency_field='currency_id', compute='_compute_gross', store=True)

    # Deducciones Obrero
    ccss_employee = fields.Monetary(string='CCSS Obrero (10.83%)', currency_field='currency_id', compute='_compute_deductions', store=True,
        help='Cuota obrera CCSS: 10.83% del salario bruto. Fuente: Decreto Ejecutivo vigente de tasas CCSS. '
             'Detalle: SEM 5.50%, IVM 3.84%, BANCO POPULAR 1%, LPT 0.50%, ASFA 0.25%, FODESAF 0.50%, INA 0.08%.')
    income_tax = fields.Monetary(string='Impuesto Renta', currency_field='currency_id', compute='_compute_deductions', store=True)
    other_deductions = fields.Monetary(string='Otras Deducciones', currency_field='currency_id')

    # ROP eliminado — confirmado con contador que no aplica en este régimen

    # ── Paternidad (Ley 8107) ────────────────────────────────────────
    # Nota: Maternidad se gestiona desde Novedades → Incapacidades (tipo Maternidad)
    # con subsidio 100% CCSS y validación 120 días Art. 94 CT
    paternity_days = fields.Integer(
        string='Días Paternidad', default=0,
        help='Días hábiles de permiso de paternidad (Ley 8107 — 8 días hábiles, cargo patrono)'
    )
    paternity_amount = fields.Monetary(
        string='Pago Paternidad', currency_field='currency_id',
        compute='_compute_deductions', store=True,
        help='8 días hábiles remunerados al 100% cargo patrono (Ley 8107)'
    )
    total_employee_deductions = fields.Monetary(string='Total Deducciones Obrero', currency_field='currency_id', compute='_compute_totals', store=True)

    # Cargas Patronales
    ccss_employer = fields.Monetary(string='CCSS Patronal (26.83%)', currency_field='currency_id', compute='_compute_deductions', store=True,
        help='Cuota patronal CCSS: 26.83% del salario bruto. Fuente: Decreto Ejecutivo vigente de tasas CCSS. '
             'Detalle: SEM 9.25%, IVM 5.75%, BANCO POPULAR 0.25%, LPT 1.50%, ASFA 0.25%, FODESAF 5%, INA 1.50%, IMAS 0.50%, FUNDACIONES 0.50%, FCE 1.50%.')
    ins_employer = fields.Monetary(string='INS Patronal (Riesgos del Trabajo)', currency_field='currency_id', compute='_compute_deductions', store=True,
        help='Prima del Seguro de Riesgos del Trabajo: tasa referencial del 1% sobre el salario bruto. '
             'La tasa exacta depende de la clase de riesgo asignada por el INS segun la actividad economica. '
             'Fuente: INS Costa Rica - Ley N.° 6727 Riesgos del Trabajo.')
    aguinaldo_provision = fields.Monetary(string='Provisión Aguinaldo (8.33%)', currency_field='currency_id', compute='_compute_deductions', store=True,
        help='Provision mensual de aguinaldo: 1/12 del salario anual (8.33%). '
             'Fuente: Codigo de Trabajo de Costa Rica, Ley N.° 2 - Capitulo VIII, Articulo 228 y ss.')
    cesantia_provision = fields.Monetary(string='Provisión Cesantía (5.33%)', currency_field='currency_id', compute='_compute_deductions', store=True,
        help='Provision mensual de cesantia: equivale a 8 dias por ano trabajado (aprox 5.33% mensual para contratos indefinidos). '
             'Fuente: Codigo de Trabajo de Costa Rica, Articulo 29 y ss.')
    vacation_provision = fields.Monetary(string='Provisión Vacaciones (4.16%)', currency_field='currency_id', compute='_compute_deductions', store=True,
        help='Provision mensual de vacaciones: 2 semanas por ano laborado (1/24 del salario anual = 4.16%). '
             'Fuente: Codigo de Trabajo de Costa Rica, Articulo 153 y ss.')
    total_employer_cost = fields.Monetary(string='Costo Total Patronal', currency_field='currency_id', compute='_compute_totals', store=True)
    net_salary = fields.Monetary(string='Salario Neto', currency_field='currency_id', compute='_compute_totals', store=True)
    salary_payable = fields.Monetary(
        string='Salario a Pagar', currency_field='currency_id',
        compute='_compute_totals', store=True,
        help='Monto neto a depositar al empleado (neto menos préstamos y deducciones adicionales)'
    )
    cost_per_net_colon = fields.Float(
        string='₡ Costo/₡ Neto', digits=(6, 2),
        compute='_compute_totals', store=True,
        help='Por cada ₡1 neto que recibe el empleado, la empresa gasta este monto total (salario + cargas patronales)'
    )

    # Asistencias
    attendance_hours = fields.Float(string='Horas Trabajadas', compute='_compute_attendance_hours', store=True)
    attendance_details = fields.Text(string='Detalle de Asistencias', compute='_compute_attendance_hours', store=True)
    calculation_method = fields.Selection(related='employee_id.payroll_calculation_method', string='Método de Cálculo', store=True)

    # Novedades
    disability_ids = fields.One2many('planilla.disability', 'payslip_id', string='Incapacidades')
    disability_days = fields.Integer(string='Días Incapacidad', compute='_compute_extras', store=True)
    ccss_subsidy_total = fields.Monetary(
        string='Subsidio CCSS (Incapacidades)', currency_field='currency_id',
        compute='_compute_extras', store=True,
        help='Monto que cubre la CCSS por incapacidades > 3 días. No afecta el costo patronal.'
    )
    employer_disability_cost = fields.Monetary(
        string='Costo Patrono por Incapacidades', currency_field='currency_id',
        compute='_compute_extras', store=True,
        help='Primeros 3 días a cargo del patrono + porcentaje de días restantes.'
    )
    overtime_ids = fields.One2many('planilla.overtime', 'payslip_id', string='Horas Extras')
    vacation_ids = fields.One2many('planilla.vacation.payment', 'payslip_id', string='Vacaciones')
    deduction_line_ids = fields.One2many('planilla.payslip.deduction.line', 'payslip_id', string='Deducciones Adicionales')

    state = fields.Selection([
        ('draft', 'Borrador'),
        ('confirmed', 'Confirmado'),
        ('done', 'Pagado'),
        ('cancelled', 'Cancelado'),
    ], string='Estado', default='draft', tracking=True)

    move_id = fields.Many2one('account.move', string='Asiento Contable')

    # ── Constraints ───────────────────────────────────────────────────
    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for rec in self:
            if rec.date_from and rec.date_to and rec.date_from > rec.date_to:
                raise ValidationError('La fecha inicio no puede ser mayor a la fecha fin.')

    @api.constrains('employee_id', 'payroll_run_id')
    def _check_unique_payslip_per_run(self):
        for rec in self:
            if not rec.payroll_run_id:
                continue
            duplicates = self.search([
                ('employee_id', '=', rec.employee_id.id),
                ('payroll_run_id', '=', rec.payroll_run_id.id),
                ('id', '!=', rec.id),
                ('state', '!=', 'cancelled'),
            ])
            if duplicates:
                raise ValidationError(
                    f'El empleado {rec.employee_id.name} ya tiene una boleta '
                    f'en la planilla {rec.payroll_run_id.name}.'
                )

    # ── Computes ──────────────────────────────────────────────────────
    @api.depends('date_from', 'date_to', 'is_proportional', 'days_worked')
    def _compute_proportional_days(self):
        for rec in self:
            if rec.date_from and rec.date_to:
                rec.days_in_period = (rec.date_to - rec.date_from).days + 1
            else:
                rec.days_in_period = 30
            if rec.is_proportional and rec.days_in_period > 0:
                worked = rec.days_worked or rec.days_in_period
                rec.proportional_factor = round(worked / rec.days_in_period, 4)
            else:
                rec.proportional_factor = 1.0


    @api.depends('employee_id', 'date_from', 'date_to')
    def _compute_name(self):
        for rec in self:
            emp = rec.employee_id.name or ''
            date_str = str(rec.date_to)[:7] if rec.date_to else ''
            rec.name = f'BOL - {emp} - {date_str}'

    @api.depends('employee_id', 'date_from', 'date_to', 'attendance_hours', 'is_proportional', 'proportional_factor', 'payroll_calendar_id')
    def _compute_base_salary(self):
        for rec in self:
            emp = rec.employee_id
            if not emp:
                rec.base_salary = 0.0
                continue
            if (emp.payroll_calculation_method or 'fixed') == 'attendance':
                if not rec.date_from or not rec.date_to or not emp.base_salary:
                    rec.base_salary = 0.0
                    continue
                hours_per_day = emp.schedule_type_id.hours_per_day if emp.schedule_type_id else 8.0
                monthly_hours = hours_per_day * 30.0
                hourly_rate = emp.base_salary / monthly_hours if monthly_hours else 0.0
                rec.base_salary = round(hourly_rate * (rec.attendance_hours or 0.0), 2)
            else:
                raw = emp.base_salary or 0.0
                # Aplicar factor de frecuencia según calendarización
                # Mensual=1.0, Quincenal=0.5, Semanal=0.25, Bimensual=2.0
                freq = rec.payroll_calendar_id.frequency if rec.payroll_calendar_id else 'monthly'
                freq_factor = {
                    'monthly':   1.0,
                    'biweekly':  0.5,
                    'weekly':    0.25,
                    'bimonthly': 2.0,
                }.get(freq, 1.0)
                # Si además es proporcional, se multiplica por ambos factores
                prop_factor = rec.proportional_factor if rec.is_proportional else 1.0
                rec.base_salary = round(raw * freq_factor * prop_factor, 2)

    @api.onchange('date_from', 'date_to', 'employee_id')
    def _onchange_auto_proportional(self):
        """Auto-detect if employee entered during the period."""
        for rec in self:
            emp = rec.employee_id
            if emp and emp.entry_date and rec.date_from and rec.date_to:
                if rec.date_from <= emp.entry_date <= rec.date_to:
                    rec.is_proportional = True
                    rec.days_worked = (rec.date_to - emp.entry_date).days + 1
                elif emp.exit_date and rec.date_from <= emp.exit_date <= rec.date_to:
                    rec.is_proportional = True
                    rec.days_worked = (emp.exit_date - rec.date_from).days + 1

    @api.depends('employee_id', 'date_from', 'date_to', 'attendance_hours', 'is_proportional', 'proportional_factor')
    def _compute_attendance_hours(self):
        for rec in self:
            if (not rec.employee_id or not rec.date_from or not rec.date_to
                    or rec.employee_id.payroll_calculation_method != 'attendance'):
                rec.attendance_hours = 0.0
                rec.attendance_details = ''
                continue
            dt_from = fields.Datetime.to_datetime(rec.date_from)
            dt_to = fields.Datetime.to_datetime(rec.date_to) + datetime.timedelta(days=1)
            attendances = self.env['hr.attendance'].search([
                ('employee_id', '=', rec.employee_id.id),
                ('check_in', '>=', dt_from),
                ('check_in', '<', dt_to),
            ])
            # C5 — Detectar registros abiertos (check_in sin check_out)
            open_att = attendances.filtered(lambda a: not a.check_out)
            if open_att:
                fechas = ', '.join(str(a.check_in)[:10] for a in open_att)
                rec.attendance_hours = 0.0
                rec.attendance_details = (
                    f'⚠ ADVERTENCIA: Existen {len(open_att)} registro(s) de asistencia '
                    f'sin check_out en las fechas: {fechas}. '
                    f'Corrija las marcas antes de confirmar la boleta.'
                )
                continue
            rec.attendance_hours = round(sum(a.worked_hours for a in attendances), 2)
            if attendances:
                rec.attendance_details = '\n'.join(
                    f"{str(a.check_in)[:10]}: {round(a.worked_hours, 2)}h"
                    for a in attendances.sorted('check_in')
                )
            else:
                rec.attendance_details = 'Sin registros de asistencia en el período.'

    @api.depends('overtime_ids.amount', 'overtime_ids.state',
                 'vacation_ids.total_amount', 'vacation_ids.state',
                 'disability_ids.days', 'disability_ids.ccss_subsidy',
                 'disability_ids.employer_cost', 'disability_ids.state')
    def _compute_extras(self):
        for rec in self:
            rec.overtime_amount  = sum(o.amount for o in rec.overtime_ids if o.state == 'approved')
            rec.vacation_amount  = sum(v.total_amount for v in rec.vacation_ids if v.state == 'approved')
            active_dis = rec.disability_ids.filtered(lambda d: d.state in ('confirmed', 'paid'))
            rec.disability_days        = sum(d.days for d in active_dis)
            rec.ccss_subsidy_total     = round(sum(d.ccss_subsidy for d in active_dis), 2)
            rec.employer_disability_cost = round(sum(d.employer_cost for d in active_dis), 2)

    @api.depends('base_salary', 'overtime_amount', 'vacation_amount', 'other_income')
    def _compute_gross(self):
        for rec in self:
            rec.gross_salary = (
                (rec.base_salary or 0.0) + (rec.overtime_amount or 0.0) +
                (rec.vacation_amount or 0.0) + (rec.other_income or 0.0)
            )

    @api.depends('gross_salary')
    def _compute_deductions(self):
        rh = self.env['planilla.rate.helper']
        ccss_emp  = rh.get_ccss_employee_rate()
        ccss_pat  = rh.get_ccss_employer_rate()
        agu_rate  = rh.get_aguinaldo_rate()
        ces_rate  = rh.get_cesantia_rate()
        vac_rate  = rh.get_vacation_rate()
        for rec in self:
            g = rec.gross_salary or 0.0
            rec.ccss_employee      = round(g * ccss_emp, 2)



            # ── Paternidad (Ley 8107 — 8 días hábiles 100% patrono) ──
            if rec.paternity_days > 0:
                daily = round(g * 2 / 30, 2)  # salario diario mensual
                rec.paternity_amount = round(daily * rec.paternity_days, 2)
            else:
                rec.paternity_amount = 0.0
            rec.income_tax         = round(rec._calc_income_tax(g), 2)
            rec.ccss_employer      = round(g * ccss_pat, 2)
            risk                   = rec.employee_id.ins_risk_class or 'II'
            rec.ins_employer       = round(g * rh.get_ins_rate(risk), 2)
            rec.aguinaldo_provision = round(g * agu_rate, 2)
            rec.cesantia_provision  = round(g * ces_rate, 2)
            rec.vacation_provision  = round(g * vac_rate, 2)
            # Pensión alimentaria primero (Ley 8590, Art. 59), luego préstamos
            if rec.state == 'draft' and rec.date_from and rec.date_to:
                rec._sync_pension_alimentaria()
                rec._sync_loan_deductions()

    def _sync_recurring_benefits(self):
        """Auto-apply active recurring benefits/deductions for the period."""
        for rec in self:
            if rec.state != 'draft':
                continue
            emp = rec.employee_id
            today = rec.date_from
            if not today:
                continue
            benefits = self.env['planilla.recurring.benefit'].search([
                ('employee_id', '=', emp.id),
                ('active', '=', True),
                '|', ('date_start', '=', False), ('date_start', '<=', today),
                '|', ('date_end', '=', False),   ('date_end', '>=', today),
            ])
            for ben in benefits:
                # Skip if already applied (by recurring_benefit_id)
                existing = rec.deduction_line_ids.filtered(
                    lambda l: l.recurring_benefit_id.id == ben.id
                )
                if existing:
                    continue
                amt = ben.get_amount_for_salary(rec.gross_salary or 0.0)
                rec.deduction_line_ids = [(0, 0, {
                    'deduction_code_id':    ben.deduction_code_id.id,
                    'description':          ben.name,
                    'line_type':            'income' if ben.benefit_type == 'income' else 'deduction',
                    'amount_type':          ben.amount_type,
                    'amount':               amt,
                    'percentage':           ben.percentage,
                    'recurring_benefit_id': ben.id,
                })]

    def _sync_loan_deductions(self):
        """Sincroniza cuotas de préstamos activos del empleado con las líneas de deducción."""
        self.ensure_one()
        # Código de deducción para préstamos
        loan_code = self.env['planilla.deduction.code'].search(
            [('code', '=', 'PRESTAMO')], limit=1
        )
        if not loan_code:
            return
        # Buscar préstamos activos o aprobados del empleado
        loans = self.env['planilla.employee.loan'].search([
            ('employee_id', '=', self.employee_id.id),
            ('state', 'in', ['approved', 'active']),
        ])
        for loan in loans:
            installment = loan.get_pending_installment(self.date_from, self.date_to)
            if not installment:
                continue
            # Verificar si ya está en las líneas
            existing = self.deduction_line_ids.filtered(
                lambda l: l.loan_installment_id == installment
            )
            if not existing:
                self.env['planilla.payslip.deduction.line'].create({
                    'payslip_id':          self.id,
                    'deduction_code_id':   loan_code.id,
                    'description':         loan.name,
                    'amount':              installment.amount,
                    'loan_installment_id': installment.id,
                })


    def _sync_pension_alimentaria(self):
        """Sincroniza pensiones alimentarias activas del empleado como deducciones."""
        self.ensure_one()
        if self.state != 'draft':
            return

        # Código de deducción para pensiones alimentarias
        pension_code = self.env['planilla.deduction.code'].search(
            [('code', '=', 'PENSION_ALIM')], limit=1
        )
        if not pension_code:
            pension_code = self.env['planilla.deduction.code'].create({
                'name': 'Pensión Alimentaria',
                'code': 'PENSION_ALIM',
                'deduction_type': 'employee',
            })

        # Buscar pensiones activas del empleado vigentes en el período
        pensiones = self.env['planilla.pension.alimentaria'].search([
            ('employee_id', '=', self.employee_id.id),
            ('state', '=', 'active'),
            ('active', '=', True),
            ('date_start', '<=', self.date_to),
            '|',
            ('date_end', '=', False),
            ('date_end', '>=', self.date_from),
        ])

        for pension in pensiones:
            # Verificar si ya está aplicada (por numero_expediente)
            existing = self.deduction_line_ids.filtered(
                lambda l: l.deduction_category == 'pension_alimentaria'
                and l.numero_resolucion == pension.numero_expediente
            )
            if existing:
                continue

            monto = pension.compute_amount(self.gross_salary or 0.0)

            self.env['planilla.payslip.deduction.line'].create({
                'payslip_id':         self.id,
                'deduction_code_id':  pension_code.id,
                'description':        f'Pensión Alimentaria — {pension.beneficiario_nombre} ({pension.numero_expediente})',
                'line_type':          'deduction',
                'deduction_category': 'pension_alimentaria',
                'amount_type':        pension.calculation_type,
                'amount':             monto,
                'percentage':         pension.percentage if pension.calculation_type == 'percentage' else 0.0,
                'numero_resolucion':  pension.numero_expediente,
            })

    def _sync_novedades(self):
        """
        Vincula automáticamente a la boleta las horas extras, incapacidades
        y vacaciones del empleado que corresponden al período de la boleta
        y que aún no tienen boleta asignada.
        Reglas:
          - Horas extras:    state == 'approved',  fecha dentro del período
          - Incapacidades:   state in ('confirmed','paid'), solapa con el período
          - Vacaciones:      state in ('approved','paid'), solapa con el período
        """
        self.ensure_one()
        if not self.employee_id or not self.date_from or not self.date_to:
            return

        emp_id    = self.employee_id.id
        date_from = self.date_from
        date_to   = self.date_to

        # ── Horas Extras ────────────────────────────────────────────────────
        overtimes = self.env['planilla.overtime'].search([
            ('employee_id', '=', emp_id),
            ('state', '=', 'approved'),
            ('date', '>=', date_from),
            ('date', '<=', date_to),
            '|', ('payslip_id', '=', False), ('payslip_id', '=', self.id),
        ])
        overtimes.write({'payslip_id': self.id})

        # ── Incapacidades ────────────────────────────────────────────────────
        # Solapan si date_start <= date_to AND date_end >= date_from
        disabilities = self.env['planilla.disability'].search([
            ('employee_id', '=', emp_id),
            ('state', 'in', ('confirmed', 'paid')),
            ('date_start', '<=', date_to),
            ('date_end',   '>=', date_from),
            '|', ('payslip_id', '=', False), ('payslip_id', '=', self.id),
        ])
        disabilities.write({'payslip_id': self.id})

        # ── Vacaciones ────────────────────────────────────────────────────────
        vacations = self.env['planilla.vacation.payment'].search([
            ('employee_id', '=', emp_id),
            ('state', 'in', ('approved', 'paid')),
            ('date_start', '<=', date_to),
            ('date_end',   '>=', date_from),
            '|', ('payslip_id', '=', False), ('payslip_id', '=', self.id),
        ])
        vacations.write({'payslip_id': self.id})

        # ── Pensiones Alimentarias ─────────────────────────────────────────
        self._sync_pension_alimentaria()

        # ── Ausencias aprobadas (hr_holidays) ─────────────────────────────
        self._sync_ausencias()

    def _sync_ausencias(self):
        """
        H2 FIX — Integración hr_holidays con planilla.
        Busca ausencias aprobadas (hr.leave en estado validate) del empleado
        en el período de la boleta y crea deducciones automáticas por los
        días sin goce de sueldo.

        Lógica:
          - Solo aplica a ausencias SIN pago (unpaid leave) o cuyo tipo
            tenga work_time_rate = 0 (ausencia injustificada / sin goce).
          - Las ausencias CON pago (vacaciones anuales, maternidad, etc.)
            NO se descuentan aquí: ya están gestionadas por sus propios modelos.
          - El monto diario = salario bruto / días del período.
          - Se crea UNA línea de deducción por leave_id para evitar duplicados.
        """
        self.ensure_one()
        if self.state != 'draft':
            return
        if not self.employee_id or not self.date_from or not self.date_to:
            return

        # Código de deducción para ausencias
        absence_code = self.env['planilla.deduction.code'].search(
            [('code', '=', 'AUSENCIA')], limit=1
        )
        if not absence_code:
            absence_code = self.env['planilla.deduction.code'].create({
                'name': 'Ausencia Sin Goce de Sueldo',
                'code': 'AUSENCIA',
                'deduction_type': 'employee',
            })

        # Buscar ausencias aprobadas del empleado que solapan con el período
        leaves = self.env['hr.leave'].search([
            ('employee_id', '=', self.employee_id.id),
            ('state', '=', 'validate'),
            ('date_from', '<=', fields.Datetime.to_datetime(self.date_to)),
            ('date_to',   '>=', fields.Datetime.to_datetime(self.date_from)),
        ])

        for leave in leaves:
            # Solo procesar ausencias sin goce (unpaid) o injustificadas
            # Las ausencias CON pago (vacaciones anuales legales, maternidad)
            # tienen work_time_rate > 0 y se manejan por sus propios modelos.
            holiday_type = leave.holiday_status_id
            is_paid = getattr(holiday_type, 'work_time_rate', 0) > 0 or \
                      getattr(holiday_type, 'leave_validation_type', '') == 'no_validation'
            # Si el tipo de ausencia tiene "time_type = leave" y es pagada, omitir
            if getattr(holiday_type, 'time_type', 'leave') == 'leave' and is_paid:
                continue

            # Evitar duplicados: verificar si ya existe línea para este leave
            existing = self.deduction_line_ids.filtered(
                lambda l: l.hr_leave_id == leave
            )
            if existing:
                continue

            # Calcular días efectivos dentro del período de la boleta
            leave_start = leave.date_from.date() if leave.date_from else self.date_from
            leave_end   = leave.date_to.date()   if leave.date_to   else self.date_to
            effective_start = max(leave_start, self.date_from)
            effective_end   = min(leave_end,   self.date_to)
            days_absent = (effective_end - effective_start).days + 1
            if days_absent <= 0:
                continue

            # Monto: salario_diario × días ausentes
            salary_daily = round(
                (self.base_salary or 0.0) / max(self.days_in_period or 30, 1), 2
            )
            amount = round(salary_daily * days_absent, 2)
            if amount <= 0:
                continue

            self.env['planilla.payslip.deduction.line'].create({
                'payslip_id':          self.id,
                'deduction_code_id':   absence_code.id,
                'description':         (
                    f'Ausencia sin goce — {leave.holiday_status_id.name} '
                    f'({effective_start} al {effective_end}, {days_absent} día(s))'
                ),
                'amount':              amount,
                'deduction_category':  'ausencia',
                'hr_leave_id':         leave.id,
            })

    def action_sync_novedades(self):
        """Botón manual: re-sincroniza novedades del período en la boleta."""
        for rec in self:
            if rec.state == 'draft':
                rec._sync_novedades()           # incluye _sync_ausencias() internamente
                rec._sync_pension_alimentaria()
                rec._sync_ausencias()           # B2 FIX: también en sincronización manual
        return True

    def _calc_income_tax(self, gross):
        """Calculo progresivo de renta usando tramos configurados en la UI.

        Los tramos de renta del MTSS están definidos en base mensual.
        Para períodos quincenales/semanales se anualiza el salario,
        se aplican los tramos equivalentes y se divide entre los períodos.
        Esto evita que un quincenal pague menos renta de la que corresponde.
        """
        # Factor de frecuencia para normalizar a mensual
        freq = self.payroll_calendar_id.frequency if self.payroll_calendar_id else 'monthly'
        periods_per_month = {
            'monthly':   1,
            'biweekly':  2,
            'weekly':    4,
            'bimonthly': 1,
        }.get(freq, 1)

        # Salario mensual equivalente para aplicar tramos correctamente
        monthly_equiv = gross * periods_per_month

        brackets = self.env['planilla.income.tax.bracket'].search(
            [('active', '=', True)], order='sequence asc'
        )
        if not brackets:
            # Fallback con tramos 2026 sobre equivalente mensual
            g = monthly_equiv
            if g <= 941000:
                tax_monthly = 0.0
            elif g <= 1381000:
                tax_monthly = (g - 941000) * 0.10
            elif g <= 2423000:
                tax_monthly = (440000 * 0.10) + ((g - 1381000) * 0.15)
            elif g <= 4845000:
                tax_monthly = (440000 * 0.10) + (1042000 * 0.15) + ((g - 2423000) * 0.20)
            else:
                tax_monthly = (440000 * 0.10) + (1042000 * 0.15) + (2422000 * 0.20) + ((g - 4845000) * 0.25)
        else:
            g = monthly_equiv
            tax_monthly = 0.0
            for bracket in brackets:
                if g <= bracket.limit_from:
                    break
                limit_to = bracket.limit_to if bracket.limit_to else float('inf')
                taxable = min(g, limit_to) - bracket.limit_from
                if taxable > 0:
                    tax_monthly += taxable * (bracket.rate / 100)

        # Dividir la renta mensual entre los períodos del mes
        return tax_monthly / periods_per_month

    @api.depends(
        'gross_salary', 'ccss_employee', 'income_tax', 'other_deductions',
        'paternity_amount',
        'ccss_employer', 'ins_employer', 'aguinaldo_provision',
        'cesantia_provision', 'vacation_provision', 'deduction_line_ids.amount'
    )
    def _compute_totals(self):
        for rec in self:
            # Separar líneas adicionales por tipo
            extra_income = sum(
                l.amount for l in rec.deduction_line_ids
                if l.line_type == 'income'
            )
            # Deducciones adicionales: sindicato, cooperativa, embargo, préstamos
            extra_deductions = sum(
                l.amount for l in rec.deduction_line_ids
                if l.line_type == 'deduction'
            )

            # Total Deducciones Legales Obrero = CCSS + ROP + Renta + otras legales
            rec.total_employee_deductions = round(
                (rec.ccss_employee or 0.0) +
                (rec.income_tax or 0.0) +
                (rec.other_deductions or 0.0), 2
            )
            rec.total_employer_cost = round(
                (rec.gross_salary or 0.0) +
                (rec.ccss_employer or 0.0) +
                (rec.ins_employer or 0.0) +
                (rec.aguinaldo_provision or 0.0) +
                (rec.cesantia_provision or 0.0) +
                (rec.vacation_provision or 0.0) +
                (rec.paternity_amount or 0.0) +
                # C4: días 1-3 incapacidad CCSS a cargo del patrono (Art. 79 Reglamento CCSS)
                (rec.employer_disability_cost or 0.0), 2
            )
            # Salario Neto = Bruto − deducciones legales + subsidio CCSS + ingresos adicionales
            # Paternidad: el patrono paga los 8 días hábiles, se suma al neto
            rec.net_salary = round(
                (rec.gross_salary or 0.0) - rec.total_employee_deductions +
                (rec.ccss_subsidy_total or 0.0) +
                (rec.paternity_amount or 0.0) +
                extra_income, 2
            )
            # Salario a Pagar = Neto − préstamos, adelantos y deducciones adicionales
            rec.salary_payable = round(rec.net_salary - extra_deductions, 2)

            # KPI: costo total patronal por cada ₡1 que el empleado recibe en mano
            if rec.salary_payable and rec.salary_payable > 0:
                rec.cost_per_net_colon = round(rec.total_employer_cost / rec.salary_payable, 2)
            else:
                rec.cost_per_net_colon = 0.0

    # ── Validacion pre-confirmacion ───────────────────────────────────
    def _validate_before_confirm(self):
        """Valida que la boleta tenga datos completos y correctos antes de confirmar."""
        errors = []
        warnings = []

        for rec in self:
            emp = rec.employee_id
            prefix = f'[{emp.name}]'

            # ── Datos obligatorios del empleado ──────────────────────
            if not emp.identification_id:
                errors.append(f'{prefix} No tiene numero de cedula/identificacion registrado.')

            if not emp.base_salary or emp.base_salary <= 0:
                errors.append(f'{prefix} El salario base es 0 o no esta configurado.')

            if not rec.payroll_calendar_id:
                errors.append(f'{prefix} No tiene calendarizacion de planilla asignada.')

            # ── Salario minimo legal (referencia 2025: ~360,000 CRC mensual) ──
            if emp.base_salary and 0 < emp.base_salary < 100000:
                warnings.append(
                    f'{prefix} El salario base ({emp.base_salary:,.0f}) parece muy bajo. '
                    f'Verifique que no sea un error.'
                )

            # ── Montos calculados coherentes ─────────────────────────
            if rec.gross_salary <= 0:
                errors.append(f'{prefix} El salario bruto calculado es 0 o negativo ({rec.gross_salary:,.2f}).')

            # ── Validación salario mínimo MTSS ───────────────────────
            min_salary = self.env['planilla.minimum.salary'].get_current_minimum(
                category=rec.employee_id.employee_type_id.name if rec.employee_id.employee_type_id else None
            )
            if min_salary > 0 and rec.base_salary < min_salary:
                errors.append(
                    f'{prefix} El salario base ({rec.base_salary:,.2f}) '
                    f'está por debajo del mínimo MTSS vigente ({min_salary:,.2f}). '
                    f'Corrija el salario o verifique la categoría ocupacional del empleado.'
                )

            if rec.net_salary < 0:
                errors.append(
                    f'{prefix} El salario neto es negativo ({rec.net_salary:,.2f}). '
                    f'Las deducciones superan el salario bruto.'
                )

            if rec.net_salary > rec.gross_salary:
                errors.append(
                    f'{prefix} El salario neto ({rec.net_salary:,.2f}) es mayor al bruto '
                    f'({rec.gross_salary:,.2f}). Verifique las deducciones.'
                )

            # ── CCSS coherente ───────────────────────────────────────
            expected_ccss_emp = round(rec.gross_salary * 0.1083, 2)
            if rec.ccss_employee and abs(rec.ccss_employee - expected_ccss_emp) > 1.0:
                warnings.append(
                    f'{prefix} La cuota CCSS obrero ({rec.ccss_employee:,.2f}) '
                    f'difiere del calculo esperado ({expected_ccss_emp:,.2f}). '
                    f'Verifique si hay deducciones manuales.'
                )

            # ── Asistencias abiertas (C5) ────────────────────────────────
            if (rec.employee_id.payroll_calculation_method or 'fixed') == 'attendance':
                dt_from = fields.Datetime.to_datetime(rec.date_from)
                dt_to = fields.Datetime.to_datetime(rec.date_to) + datetime.timedelta(days=1)
                open_att = self.env['hr.attendance'].search_count([
                    ('employee_id', '=', emp.id),
                    ('check_in', '>=', dt_from),
                    ('check_in', '<', dt_to),
                    ('check_out', '=', False),
                ])
                if open_att:
                    errors.append(
                        f'{prefix} Hay {open_att} registro(s) de asistencia sin check_out '
                        f'en el período. Corrija las marcas antes de confirmar.'
                    )

            # ── Periodo cerrado ──────────────────────────────────────
            closed = PlanillaClosedPeriod.is_period_closed(
                self.env, rec.company_id.id,
                rec.date_from, rec.date_to,
                rec.branch_id.id if rec.branch_id else False
            )
            if closed:
                errors.append(
                    f'{prefix} El periodo {rec.date_from} - {rec.date_to} esta cerrado '
                    f'("{closed.name}", cerrado el {closed.closed_date.strftime("%d/%m/%Y")} '
                    f'por {closed.closed_by.name}). No se puede confirmar una boleta en un periodo cerrado.'
                )

            # ── Duplicados en el mismo periodo ───────────────────────
            duplicate = self.env['planilla.payslip.cr'].search([
                ('employee_id', '=', emp.id),
                ('date_from', '=', rec.date_from),
                ('date_to', '=', rec.date_to),
                ('state', 'in', ['confirmed', 'paid', 'done']),
                ('id', '!=', rec.id),
            ], limit=1)
            if duplicate:
                errors.append(
                    f'{prefix} Ya existe una boleta confirmada o pagada para el periodo '
                    f'{rec.date_from} - {rec.date_to} (Ref: {duplicate.name}).'
                )

        if errors:
            raise UserError(
                'No se puede confirmar. Se encontraron los siguientes errores:\n\n' +
                '\n'.join(f'• {e}' for e in errors)
            )

        if warnings:
            # Los warnings se muestran pero no bloquean
            return '\n'.join(warnings)
        return None

    # ── Acciones ──────────────────────────────────────────────────────
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            rec._sync_novedades()
            rec._sync_recurring_benefits()
            # Pensión alimentaria tiene prioridad absoluta (Ley 8590, Art. 59)
            # Se sincroniza ANTES que préstamos y embargos
            rec._sync_pension_alimentaria()
            rec._sync_loan_deductions()
        return records

    def action_confirm(self):
        # Verificar permisos: solo Aprobador o Admin pueden confirmar
        if not self.env.user.has_group('planilla_cr.group_planilla_aprobador'):
            raise UserError(
                'No tiene permisos para confirmar boletas. '
                'Se requiere el rol de Aprobador de Planilla o superior.'
            )
        for rec in self:
            if rec.state != 'draft':
                raise UserError(f'La boleta {rec.name} no esta en borrador.')
        self._validate_before_confirm()
        for rec in self:
            rec.state = 'confirmed'

    def action_pay(self, skip_accounting=False):
        if not self.env.user.has_group('planilla_cr.group_planilla_aprobador'):
            raise UserError(
                'No tiene permisos para pagar boletas. '
                'Se requiere el rol de Aprobador de Planilla o superior.'
            )
        for rec in self:
            if rec.state != 'confirmed':
                raise UserError(f'La boleta {rec.name} debe estar confirmada para pagar.')
            rec.state = 'done'
            if not skip_accounting:
                rec._create_accounting_entry()
            rec.overtime_ids.filtered(lambda o: o.state == 'approved').write({'state': 'paid'})
            rec.vacation_ids.filtered(lambda v: v.state == 'approved').write({'state': 'paid'})
            rec.disability_ids.filtered(lambda d: d.state == 'confirmed').write({'state': 'paid'})
            # Enviar email al empleado si tiene correo configurado
            if rec.employee_id.work_email:
                try:
                    template = self.env.ref('planilla_cr.email_template_payslip_paid', raise_if_not_found=False)
                    if template:
                        template.send_mail(rec.id, force_send=False)
                except Exception as e:
                    _logger.warning(f"planilla_cr: No se pudo enviar email de boleta ({rec.name}): {e}")
            # Marcar cuotas de préstamos como descontadas y verificar si el préstamo quedó saldado
            loan_lines = rec.deduction_line_ids.filtered(lambda l: l.loan_installment_id)
            for line in loan_lines:
                line.loan_installment_id.write({'state': 'deducted', 'payslip_id': rec.id})
                line.loan_installment_id.loan_id.action_activate()
                line.loan_installment_id.loan_id.action_check_paid()
            self.env['planilla.salary.history'].create({
                'employee_id': rec.employee_id.id,
                'salary': rec.net_salary,
                'gross_salary': rec.gross_salary,
                'effective_date': rec.date_to,
                'payslip_id': rec.id,
                'reason': f'Planilla {rec.name}',
            })

    def action_cancel(self):
        for rec in self:
            if rec.state == 'done':
                raise UserError(
                    'No se puede cancelar una boleta ya pagada. '
                    'Revierta el asiento contable primero.'
                )
            if rec.move_id and rec.move_id.state == 'posted':
                rec.move_id.button_cancel()
            # Revertir cuotas de préstamos descontadas en esta boleta
            loan_lines = rec.deduction_line_ids.filtered(lambda l: l.loan_installment_id)
            for line in loan_lines:
                inst = line.loan_installment_id
                if inst.state == 'deducted' and inst.payslip_id == rec:
                    inst.write({'state': 'pending', 'payslip_id': False})
                    if inst.loan_id.state == 'paid':
                        inst.loan_id.write({'state': 'active'})
            # Revertir vacaciones pagadas en esta boleta → volver a 'approved'
            rec.vacation_ids.filtered(
                lambda v: v.state == 'paid'
            ).write({'state': 'approved'})
            # Revertir incapacidades procesadas en esta boleta → volver a 'confirmed'
            rec.disability_ids.filtered(
                lambda d: d.state == 'paid'
            ).write({'state': 'confirmed'})
            # Revertir horas extras pagadas en esta boleta → volver a 'approved'
            rec.overtime_ids.filtered(
                lambda o: o.state == 'paid'
            ).write({'state': 'approved'})
            rec.state = 'cancelled'

    def action_reset_to_draft(self):
        for rec in self:
            if rec.state not in ('cancelled', 'confirmed'):
                raise UserError('Solo se pueden reactivar boletas canceladas o confirmadas.')
            rec.state = 'draft'

    def action_send_payslip(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Enviar Boleta',
            'res_model': 'planilla.send.payslip.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_payslip_ids': [(6, 0, self.ids)]},
        }

    def action_view_accounting_entry(self):
        self.ensure_one()
        if not self.move_id:
            raise UserError('Esta boleta no tiene asiento contable generado.')
        return {
            'type': 'ir.actions.act_window',
            'name': 'Asiento Contable',
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': self.move_id.id,
        }

    def action_print_payslip(self):
        return self.env.ref('planilla_cr.action_report_payslip_cr').report_action(self)

    def _create_accounting_entry(self):
        self.ensure_one()
        config = self.env['planilla.accounting.config'].get_config(self.company_id.id)

        # C3 — Avisar explícitamente si no hay configuración contable
        if not config:
            raise UserError(
                'No existe configuración contable para esta compañía. '
                'Configure las cuentas en Planilla → Configuración → Contabilidad.'
            )
        if not config.journal_id:
            raise UserError(
                'No hay diario contable configurado para planilla. '
                'Configure el diario en Planilla → Configuración → Contabilidad.'
            )

        lines = []

        def add_line(account, debit=0.0, credit=0.0, name=''):
            if not account:
                return
            debit, credit = round(debit, 2), round(credit, 2)
            if debit == 0.0 and credit == 0.0:
                return
            lines.append((0, 0, {'account_id': account.id, 'name': name, 'debit': debit, 'credit': credit}))

        emp = self.employee_id.name

        # ── DÉBITOS (Gastos) ─────────────────────────────────────────────────
        add_line(config.account_salary_expense,
                 debit=self.gross_salary, name=f'Salarios — {emp}')
        add_line(config.account_social_charges_expense,
                 debit=self.ccss_employer + self.ins_employer, name=f'Cargas Sociales — {emp}')
        add_line(config.account_vacation_expense,
                 debit=self.vacation_provision, name=f'Vacaciones — {emp}')
        add_line(config.account_aguinaldo_expense,
                 debit=self.aguinaldo_provision, name=f'Aguinaldo — {emp}')
        add_line(config.account_cesantia_expense,
                 debit=self.cesantia_provision, name=f'Cesantía — {emp}')

        # ── CRÉDITOS (Pasivos) ───────────────────────────────────────────────
        add_line(config.account_ccss_payable,
                 credit=round(self.ccss_employee + self.ccss_employer, 2),
                 name=f'CCSS por Pagar — {emp}')
        add_line(config.account_ins_payable,
                 credit=self.ins_employer, name=f'INS por Pagar — {emp}')
        add_line(config.account_income_tax_payable,
                 credit=self.income_tax, name=f'Retención Renta — {emp}')
        add_line(config.account_aguinaldo_provision,
                 credit=self.aguinaldo_provision, name=f'Provisión Aguinaldo — {emp}')
        add_line(config.account_cesantia_provision,
                 credit=self.cesantia_provision, name=f'Provisión Cesantía — {emp}')
        add_line(config.account_vacation_provision,
                 credit=self.vacation_provision, name=f'Provisión Vacaciones — {emp}')

        # C2 — Pensiones alimentarias retenidas (cuenta retenciones por pagar)
        pensiones = sum(
            l.amount for l in self.deduction_line_ids
            if l.deduction_category == 'pension_alimentaria'
        )
        if pensiones > 0:
            pension_account = config.account_salary_payable  # fallback si no hay cuenta específica
            if hasattr(config, 'account_pension_payable') and config.account_pension_payable:
                pension_account = config.account_pension_payable
            add_line(pension_account,
                     credit=pensiones, name=f'Pensión Alimentaria Retenida — {emp}')

        # B7 FIX — Cuotas de préstamos retenidas
        # Necesitan su propia cuenta de crédito para cuadrar el asiento.
        # salary_payable ya descuenta estos montos, pero sin su línea de crédito
        # el asiento queda descuadrado en exactamente ese monto.
        prestamos = round(sum(
            l.amount for l in self.deduction_line_ids
            if l.deduction_category == 'loan'
        ), 2)
        if prestamos > 0:
            loan_account = (
                config.account_loans_payable
                if config.account_loans_payable
                else config.account_salary_payable  # fallback: usa salarios por pagar
            )
            add_line(loan_account,
                     credit=prestamos, name=f'Cuotas Préstamos Retenidos — {emp}')

        # B7 FIX — Otras deducciones adicionales (no son pensión ni préstamo)
        # Por ejemplo: sindicato, asociación, embargo judicial, etc.
        otras_ded = round(sum(
            l.amount for l in self.deduction_line_ids
            if l.deduction_category not in ('pension_alimentaria', 'loan')
               and l.deduction_type == 'deduction'
        ), 2)
        if otras_ded > 0:
            # Fallback a salary_payable — la empresa puede separar estas cuentas luego
            add_line(config.account_salary_payable,
                     credit=otras_ded, name=f'Otras Deducciones Retenidas — {emp}')

        # C1 — Usar salary_payable (ya descontados préstamos y pensiones y otras)
        add_line(config.account_salary_payable,
                 credit=self.salary_payable,
                 name=f'Salarios por Pagar (neto a depositar) — {emp}')

        if not lines:
            return

        # Verificar cuadre antes de postear
        total_debit = round(sum(l[2]['debit'] for l in lines), 2)
        total_credit = round(sum(l[2]['credit'] for l in lines), 2)
        if abs(total_debit - total_credit) > 1.0:
            raise UserError(
                f'El asiento contable no cuadra para {emp}: '
                f'Débitos={total_debit:,.2f} / Créditos={total_credit:,.2f}. '
                f'Verifique que todas las cuentas contables estén configuradas.'
            )

        move = self.env['account.move'].create({
            'journal_id': config.journal_id.id,
            'date': self.date_to,
            'ref': f'Planilla: {self.name}',
            'move_type': 'entry',
            'line_ids': lines,
        })
        move.action_post()
        self.move_id = move.id


class PayslipDeductionLine(models.Model):
    _name = 'planilla.payslip.deduction.line'
    _description = 'Línea de Deducción / Ingreso en Boleta'

    payslip_id         = fields.Many2one('planilla.payslip.cr', required=True, ondelete='cascade')
    deduction_code_id  = fields.Many2one('planilla.deduction.code', string='Código', required=True)
    description        = fields.Char(string='Descripción')
    line_type          = fields.Selection([
        ('deduction', 'Deducción'),
        ('income',    'Ingreso Adicional'),
    ], string='Tipo de Línea', default='deduction', required=True)
    deduction_category = fields.Selection([
        ('loan',        'Préstamo'),
        ('sindical',    'Cuota Sindical'),
        ('cooperativa', 'Cuota Cooperativa'),
        ('embargo',     'Embargo Judicial'),
        ('seguro',      'Póliza / Seguro'),
        ('ahorro',      'Ahorro Voluntario'),
        ('pension_vol', 'Pensión Voluntaria'),
        ('maternity',   'Permiso Maternidad'),
        ('paternity',   'Permiso Paternidad'),
        ('vacation',    'Pago de Vacaciones'),
        ('bonus',       'Bono / Incentivo'),
        ('pension_alimentaria', 'Pensión Alimentaria'),
        ('ausencia',    'Ausencia Injustificada / Sin Goce'),
        ('other',       'Otro'),
    ], string='Categoría', default='other')
    amount_type        = fields.Selection([
        ('fixed',      'Monto Fijo'),
        ('percentage', 'Porcentaje del Bruto'),
    ], string='Cálculo', default='fixed')
    amount             = fields.Monetary(string='Monto (₡)', currency_field='currency_id')
    percentage         = fields.Float(string='% del Bruto', digits=(5, 2))
    currency_id        = fields.Many2one(related='payslip_id.currency_id', store=True)
    deduction_type     = fields.Selection(related='deduction_code_id.deduction_type', string='Tipo')
    numero_resolucion  = fields.Char(
        string='N° Resolución / Referencia',
        help='Número de resolución judicial (para embargos) o referencia del documento'
    )
    recurring_benefit_id = fields.Many2one(
        'planilla.recurring.benefit', string='Beneficio Recurrente', readonly=True
    )
    loan_installment_id = fields.Many2one(
        'planilla.loan.installment', string='Cuota de Préstamo', readonly=True
    )
    hr_leave_id = fields.Many2one(
        'hr.leave', string='Ausencia (hr.leave)',
        readonly=True, ondelete='set null',
        help='Referencia a la ausencia aprobada que originó esta deducción. '
             'Evita que se cree la misma deducción dos veces al re-sincronizar.'
    )

    @api.constrains('amount', 'deduction_category', 'payslip_id')
    def _check_deduction_limits(self):
        """
        Valida límites legales:
        - Embargo judicial: máximo 25% salario neto (Art. 172 CT)
        - Pensión alimentaria: sin límite, tiene prioridad absoluta (Ley 8590)
        """
        for line in self:
            if line.deduction_category == 'embargo' and line.payslip_id.salary_payable:
                # H5 FIX — Calcular neto disponible sobre salary_payable
                # (neto real después de CCSS, renta, pensiones y préstamos)
                # en lugar de net_salary que no descuenta préstamos activos.
                # Art. 172 CT: límite 25% sobre el neto disponible DESPUÉS
                # de descontar pensiones alimentarias (Ley 8590 prioridad).
                pensiones = sum(
                    l.amount for l in line.payslip_id.deduction_line_ids
                    if l.deduction_category == 'pension_alimentaria'
                )
                neto_disponible = line.payslip_id.salary_payable - pensiones
                limit = neto_disponible * 0.25
                if line.amount > limit:
                    raise ValidationError(
                        f'El embargo judicial (₡{line.amount:,.2f}) supera el 25% del '
                        f'salario neto disponible después de pensiones alimentarias '
                        f'(máximo ₡{limit:,.2f}) — Art. 172 CT.'
                    )
