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

    legal_warning = fields.Char(
        string='Advertencia Legal',
        compute='_compute_legal_warning',
        store=False,
        help='Alerta cuando se superan los límites del Art. 139 CT.'
    )

    @api.depends('employee_id', 'date', 'hours', 'state')
    def _compute_legal_warning(self):
        """
        Calcula advertencias legales sin bloquear.
        Art. 139 CT: máx 4h extras/día, máx 12h extras/semana.
        Se muestra como banner en el formulario pero NO impide guardar ni aprobar.
        """
        from datetime import timedelta
        MAX_HE_DIARIA  = 4.0
        MAX_HE_SEMANAL = 12.0
        for rec in self:
            warnings = []
            if rec.hours and rec.hours > MAX_HE_DIARIA:
                warnings.append(
                    f'⚠ Horas del día ({rec.hours:.1f}h) superan el máximo de '
                    f'{MAX_HE_DIARIA:.0f}h diarias (Art. 139 Código de Trabajo).'
                )
            if rec.employee_id and rec.date:
                day_of_week = rec.date.weekday()
                week_start  = rec.date - timedelta(days=day_of_week)
                week_end    = week_start + timedelta(days=6)
                other_he = rec.env['planilla.overtime'].search([
                    ('employee_id', '=', rec.employee_id.id),
                    ('date', '>=', week_start),
                    ('date', '<=', week_end),
                    ('state', 'in', ('draft', 'approved')),
                    ('id', '!=', rec.id if rec.id else 0),
                ])
                total_semanal = sum(other_he.mapped('hours')) + (rec.hours or 0.0)
                if total_semanal > MAX_HE_SEMANAL:
                    ya_registradas = total_semanal - (rec.hours or 0.0)
                    warnings.append(
                        f'⚠ Total semanal ({total_semanal:.1f}h, semana {week_start} – {week_end}) '
                        f'supera el máximo de {MAX_HE_SEMANAL:.0f}h semanales '
                        f'(Art. 139 CT). Ya registradas esta semana: {ya_registradas:.1f}h. '
                        f'Se recomienda gestionar autorización especial con el empleado.'
                    )
            rec.legal_warning = '  |  '.join(warnings) if warnings else False

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
        import logging
        _logger = logging.getLogger(__name__)
        MAX_HE_DIARIA  = 4.0
        MAX_HE_SEMANAL = 12.0

        # Art. 139 CT — advertencia diaria (ya NO bloquea, registra en chatter)
        if self.hours > MAX_HE_DIARIA:
            msg = (
                f'⚠ ADVERTENCIA LEGAL — Art. 139 Código de Trabajo: '
                f'Las horas extras aprobadas hoy ({self.hours:.1f}h) superan el máximo '
                f'de {MAX_HE_DIARIA:.0f}h diarias. Se aprueba con advertencia. '
                f'Se recomienda gestionar autorización especial con el empleado.'
            )
            self.message_post(body=msg)
            _logger.warning('planilla.overtime %s: %s', self.name, msg)

        # Art. 139 CT — advertencia semanal (ya NO bloquea, registra en chatter)
        if self.date:
            day_of_week = self.date.weekday()
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
            if total_semanal > MAX_HE_SEMANAL:
                ya_aprobadas = total_semanal - self.hours
                msg = (
                    f'⚠ ADVERTENCIA LEGAL — Art. 139 Código de Trabajo: '
                    f'El total de horas extras de la semana {week_start} – {week_end} '
                    f'({total_semanal:.1f}h) supera el límite de {MAX_HE_SEMANAL:.0f}h semanales. '
                    f'Ya aprobadas esta semana: {ya_aprobadas:.1f}h. '
                    f'Se aprueba con advertencia. Gestione autorización especial si aplica.'
                )
                self.message_post(body=msg)
                _logger.warning('planilla.overtime %s: %s', self.name, msg)

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
