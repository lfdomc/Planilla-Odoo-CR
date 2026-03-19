from odoo import models, fields, api
from odoo.models import Constraint
from odoo.exceptions import UserError, ValidationError


class Overtime(models.Model):
    _name = 'planilla.overtime'
    _description = 'Horas Extras'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _unique_overtime_employee_date_type = Constraint(
        'UNIQUE(employee_id, date, overtime_type)',
        'Ya existe un registro de horas extras del mismo tipo para este empleado en esa fecha. Verifique los registros antes de crear uno nuevo.'
    )



    name = fields.Char(
        string='Referencia', compute='_compute_name', store=True
    )
    employee_id = fields.Many2one(
        'hr.employee', string='Empleado', required=True, tracking=True, index=True
    )
    branch_id = fields.Many2one(
        related='employee_id.branch_id', string='Sucursal', store=True
    )
    date = fields.Date(string='Fecha', required=True, tracking=True,
                       default=fields.Date.today)
    hours = fields.Float(string='Horas Extras', required=True, tracking=True,
                         default=1.0)
    overtime_type = fields.Selection([
        ('simple', 'Simple (1.5x)'),
        ('double', 'Doble (2x)'),
        ('holiday', 'Día Feriado'),
    ], string='Tipo', default='simple', required=True, tracking=True)

    hourly_rate = fields.Monetary(
        string='Tarifa por Hora', currency_field='currency_id',
        compute='_compute_hourly_rate', store=True
    )
    currency_id = fields.Many2one(
        related='employee_id.currency_id', store=True
    )
    amount = fields.Monetary(
        string='Monto Total', currency_field='currency_id',
        compute='_compute_amount', store=True
    )

    state = fields.Selection([
        ('draft', 'Borrador'),
        ('approved', 'Aprobado'),
        ('paid', 'Pagado'),
        ('cancelled', 'Cancelado'),
    ], string='Estado', default='draft', tracking=True)

    payslip_id = fields.Many2one('planilla.payslip.cr', string='Boleta de Pago')
    note = fields.Text(string='Observaciones')
    source = fields.Selection([
        ('manual', 'Ingreso Manual'),
        ('attendance', 'Importado de Asistencias'),
    ], string='Origen', default='manual', readonly=True)

    @api.depends('employee_id', 'date')
    def _compute_name(self):
        for rec in self:
            emp = rec.employee_id.name or ''
            date_str = str(rec.date) if rec.date else ''
            rec.name = f'HE - {emp} - {date_str}'

    @api.depends('employee_id', 'date')
    def _compute_hourly_rate(self):
        """
        BUG #6 FIX v50: Usa el salario histórico vigente en la fecha de las HE.
        FIX M-04 v51: Usa hours_per_day del schedule_type del empleado en vez
        de 8 horas fijo. Para empleados con jornada de 6h, 10h o 12h la tarifa
        horaria era incorrecta. Fallback a 8h si no hay schedule_type configurado.
        """
        for rec in self:
            if not rec.employee_id:
                rec.hourly_rate = 0.0
                continue
            base_salary = 0.0
            if rec.date:
                # Buscar salario histórico vigente en la fecha de las HE
                history = self.env['planilla.salary.history'].search([
                    ('employee_id', '=', rec.employee_id.id),
                    ('effective_date', '<=', rec.date),
                    ('state', '=', 'authorized'),  # FIX-G2: solo registros autorizados
                ], order='effective_date desc', limit=1)
                if history:
                    base_salary = history.gross_salary
            # Fallback: salario base actual
            if not base_salary:
                base_salary = rec.employee_id.base_salary or 0.0
            # Horas por día según jornada del empleado (fallback 8h jornada ordinaria)
            hours_per_day = 8.0
            if rec.employee_id.schedule_type_id and rec.employee_id.schedule_type_id.hours_per_day:
                hours_per_day = rec.employee_id.schedule_type_id.hours_per_day
            # Tarifa por hora = Salario mensual / 30 días / horas_jornada
            rec.hourly_rate = round(base_salary / 30 / hours_per_day, 2) if base_salary else 0.0

    @api.depends('hours', 'hourly_rate', 'overtime_type')
    def _compute_amount(self):
        factors = {'simple': 1.5, 'double': 2.0, 'holiday': 2.0}
        for rec in self:
            factor = factors.get(rec.overtime_type, 1.5)
            rec.amount = rec.hours * rec.hourly_rate * factor

    def action_approve(self):
        self.ensure_one()
        from datetime import timedelta
        # Validación diaria: máx 4h extras/día (Art. 139 CT)
        if self.hours > 4.0:
            raise ValidationError(
                f'Las horas extras ({self.hours:.1f}h) superan el máximo legal de 4 horas '
                f'diarias establecido en el Art. 139 del Código de Trabajo. '
                f'Verifique si necesita dividir en varios días o solicitar autorización especial.'
            )
        # FIX A-02 v59: Validación semanal — máx 12h extras/semana (Art. 139 CT)
        if self.date:
            day_of_week = self.date.weekday()  # 0 = lunes
            week_start  = self.date - timedelta(days=day_of_week)
            week_end    = week_start + timedelta(days=6)
            other_he = self.env['planilla.overtime'].search([
                ('employee_id', '=', self.employee_id.id),
                ('date', '>=', week_start),
                ('date', '<=', week_end),
                ('state', '=', 'approved'),
                ('id', '!=', self.id),
            ])
            total_semanal = sum(other_he.mapped('hours')) + self.hours
            MAX_HE_SEMANAL = 12.0  # Art. 139 CT
            if total_semanal > MAX_HE_SEMANAL:
                raise ValidationError(
                    f'Las horas extras de la semana {week_start} — {week_end} '
                    f'({total_semanal:.1f}h) superarían el límite legal de '
                    f'{MAX_HE_SEMANAL}h semanales (Art. 139 CT). '
                    f'Ya aprobadas esta semana: {total_semanal - self.hours:.1f}h.'
                )
        # FIX C-01 v53: Validar que overtime_type=holiday corresponda a un feriado real.
        if self.overtime_type == 'holiday' and self.date:
            is_holiday = self.env['planilla.public.holiday'].is_paid_holiday(
                self.date,
                company_id=self.employee_id.company_id.id if self.employee_id else None
            )
            if not is_holiday:
                raise ValidationError(
                    f'El tipo "Día Feriado" requiere que la fecha ({self.date}) '
                    f'esté registrada como feriado de pago obligatorio (Art. 148 CT). '
                    f'Verifique en Planilla → Feriados Nacionales o use tipo Simple/Doble.'
                )
        self.write({'state': 'approved'})

    def action_cancel(self):
        self.write({'state': 'cancelled'})
