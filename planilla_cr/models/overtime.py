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



    code = fields.Char(
        string='Codigo',
        readonly=True, copy=False, index=True,
        help='Codigo autogenerado. Formato: HE-XXXX'
    )
    name = fields.Char(
        string='Referencia', compute='_compute_name', store=True
    )
    employee_id = fields.Many2one(
        'hr.employee', string='Empleado', required=True, tracking=True, index=True
    )
    company_id = fields.Many2one(
        'res.company', string='Empresa',
        related='employee_id.company_id', store=True, readonly=True,
        index=True,
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
        ('holiday', 'Dia Feriado'),
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
        help='Alerta cuando se superan los limites del Art. 139 CT.'
    )

    @api.depends('employee_id', 'date', 'hours', 'state')
    def _compute_legal_warning(self):
        """
        Calcula advertencias legales sin bloquear.
        Art. 139 CT: max 4h extras/dia, max 12h extras/semana.
        Se muestra como banner en el formulario pero NO impide guardar ni aprobar.
        """
        from datetime import timedelta
        MAX_HE_DIARIA  = 4.0
        MAX_HE_SEMANAL = 12.0
        for rec in self:
            warnings = []
            if rec.hours and rec.hours > MAX_HE_DIARIA:
                warnings.append(
                    f'WARN Horas del dia ({rec.hours:.1f}h) superan el maximo de '
                    f'{MAX_HE_DIARIA:.0f}h diarias (Art. 139 Codigo de Trabajo).'
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
                        f'WARN Total semanal ({total_semanal:.1f}h, semana {week_start} - {week_end}) '
                        f'supera el maximo de {MAX_HE_SEMANAL:.0f}h semanales '
                        f'(Art. 139 CT). Ya registradas esta semana: {ya_registradas:.1f}h. '
                        f'Se recomienda gestionar autorizacion especial con el empleado.'
                    )
            rec.legal_warning = '  |  '.join(warnings) if warnings else False

    @api.depends('employee_id', 'date')
    def _compute_name(self):
        for rec in self:
            emp = rec.employee_id.name or ''
            date_str = str(rec.date) if rec.date else ''
            rec.name = f'HE - {emp} - {date_str}'

    @api.depends('employee_id', 'date', 'employee_id.base_salary')
    def _compute_hourly_rate(self):
        """
        BUG #6 FIX v50: Usa el salario historico vigente en la fecha de las HE.
        FIX M-04 v51: Usa hours_per_day del schedule_type del empleado en vez
        de 8 horas fijo. Para empleados con jornada de 6h, 10h o 12h la tarifa
        horaria era incorrecta. Fallback a 8h si no hay schedule_type configurado.
        """
        for rec in self:
            if not rec.employee_id:
                rec.hourly_rate = 0.0
                continue
            # Salario mensual del empleado directamente -- sin historial
            base_salary = rec.employee_id.base_salary or 0.0
            # Verificar si la empresa tiene configurado formula fija 8h
            _cfg = rec.env['planilla.accounting.config'].search(
                [('company_id', '=', rec.employee_id.company_id.id)], limit=1)
            _fixed_8h = _cfg.overtime_fixed_8h if _cfg else False
            # Horas por dia: fijo 8h si esta activado, o segun jornada del empleado
            if _fixed_8h:
                hours_per_day = 8.0
            else:
                hours_per_day = 8.0
                if rec.employee_id.schedule_type_id and rec.employee_id.schedule_type_id.hours_per_day:
                    hours_per_day = rec.employee_id.schedule_type_id.hours_per_day
            # Tarifa por hora = Salario mensual / 30 dias / horas_jornada
            rec.hourly_rate = round(base_salary / 30 / hours_per_day, 2) if base_salary else 0.0

    @api.depends('hours', 'hourly_rate', 'overtime_type')
    def _compute_amount(self):
        # Art. 148 CT: feriado trabajado = pago DOBLE.
        # Como el salario mensual (30 días fijos) ya incluye el feriado (1x),
        # el recargo es solo 1 día adicional (factor 1.0), dando total 2x.
        # Factor 2.0 generaría pago TRIPLE (1 del mensual + 2 del recargo).
        # Art. 152 CT: día de descanso trabajado aplica misma lógica.
        factors = {'simple': 1.5, 'double': 2.0, 'holiday': 1.0}
        for rec in self:
            factor = factors.get(rec.overtime_type, 1.5)
            rec.amount = rec.hours * rec.hourly_rate * factor

    def action_recalculate_all_rates(self):
        """Recalcula la tarifa horaria y monto de TODAS las HE no pagadas.
        Se llama desde el boton en la vista lista para corregir tarifas historicas incorrectas."""
        domain = [('state', 'in', ('draft', 'approved'))]
        all_he = self.env['planilla.overtime'].search(domain)
        count = 0
        for rec in all_he:
            old_rate = rec.hourly_rate
            # Forzar recomputacion leyendo base_salary directamente
            base_salary = rec.employee_id.base_salary or 0.0
            _cfg2 = rec.env['planilla.accounting.config'].search(
                [('company_id', '=', rec.employee_id.company_id.id)], limit=1)
            if _cfg2 and _cfg2.overtime_fixed_8h:
                hours_per_day = 8.0
            else:
                hours_per_day = 8.0
                if rec.employee_id.schedule_type_id and rec.employee_id.schedule_type_id.hours_per_day:
                    hours_per_day = rec.employee_id.schedule_type_id.hours_per_day
            new_rate = round(base_salary / 30 / hours_per_day, 2) if base_salary else 0.0
            if new_rate != old_rate:
                factors = {'simple': 1.5, 'double': 2.0, 'holiday': 1.0}  # Art. 148 CT
                factor = factors.get(rec.overtime_type, 1.5)
                rec.write({
                    'hourly_rate': new_rate,
                    'amount': round(rec.hours * new_rate * factor, 2),
                })
                count += 1
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Recalculo completado',
                'message': f'Se corrigieron {count} registros de horas extra con tarifa incorrecta.',
                'type': 'success',
                'sticky': True,
            },
        }

    def action_approve(self):
        self.ensure_one()
        from datetime import timedelta
        import logging
        _logger = logging.getLogger(__name__)
        MAX_HE_DIARIA  = 4.0
        MAX_HE_SEMANAL = 12.0

        # Art. 139 CT -- advertencia diaria (ya NO bloquea, registra en chatter)
        if self.hours > MAX_HE_DIARIA:
            msg = (
                f'WARN ADVERTENCIA LEGAL -- Art. 139 Codigo de Trabajo: '
                f'Las horas extras aprobadas hoy ({self.hours:.1f}h) superan el maximo '
                f'de {MAX_HE_DIARIA:.0f}h diarias. Se aprueba con advertencia. '
                f'Se recomienda gestionar autorizacion especial con el empleado.'
            )
            self.message_post(body=msg)
            _logger.warning('planilla.overtime %s: %s', self.name, msg)

        # Art. 139 CT -- advertencia semanal (ya NO bloquea, registra en chatter)
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
                    f'WARN ADVERTENCIA LEGAL -- Art. 139 Codigo de Trabajo: '
                    f'El total de horas extras de la semana {week_start} - {week_end} '
                    f'({total_semanal:.1f}h) supera el limite de {MAX_HE_SEMANAL:.0f}h semanales. '
                    f'Ya aprobadas esta semana: {ya_aprobadas:.1f}h. '
                    f'Se aprueba con advertencia. Gestione autorizacion especial si aplica.'
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
                    f'El tipo "Dia Feriado" requiere que la fecha ({self.date}) '
                    f'este registrada como feriado de pago obligatorio (Art. 148 CT). '
                    f'Verifique en Planilla -> Feriados Nacionales o use tipo Simple/Doble.'
                )
        self.write({'state': 'approved'})

    @staticmethod
    def _next_code(env, prefix):
        env.cr.execute(
            'SELECT code FROM planilla_overtime '
            'WHERE code LIKE %s ORDER BY code DESC LIMIT 1',
            (prefix + '-%',)
        )
        row = env.cr.fetchone()
        if row and row[0]:
            try:
                num = int(row[0].split('-')[-1]) + 1
            except (ValueError, IndexError):
                num = 1
        else:
            num = 1
        return f'{prefix}-{num:04d}'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('code'):
                vals['code'] = self._next_code(self.env, 'HE')
        return super().create(vals_list)

    def action_cancel(self):
        self.write({'state': 'cancelled'})
