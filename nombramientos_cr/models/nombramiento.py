import logging
import datetime

from odoo import models, fields, api
from odoo.exceptions import ValidationError, UserError
from . import constants as C

_logger = logging.getLogger(__name__)


class Nombramiento(models.Model):
    """
    Nombramiento semanal de un empleado a una sucursal/sede.
    Puede contener múltiples turnos (días/horas específicos).
    Al confirmar, los turnos quedan listos para pasar a planilla.
    """
    _name = 'nombramientos.nombramiento'
    _description = 'Nombramiento de Empleado'
    _order = 'date_start desc, employee_id'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'display_name_full'

    name = fields.Char(
        string='Referencia', readonly=True,
        default='Nuevo', copy=False, tracking=True,
    )
    display_name_full = fields.Char(
        string='Nombre', compute='_compute_display_name_full', store=True,
    )
    employee_id = fields.Many2one(
        'hr.employee', string='Empleado', required=True,
        tracking=True, index=True,
    )
    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company,
    )
    branch_id = fields.Many2one(
        'planilla.branch', string='Sucursal / Sede', required=True,
        tracking=True,
    )
    date_start = fields.Date(
        string='Inicio de Semana', required=True, tracking=True,
        default=lambda self: fields.Date.context_today(self),
    )
    date_end = fields.Date(
        string='Fin de Semana', required=True, tracking=True,
    )
    job_id = fields.Many2one(
        'hr.job', string='Puesto para este Nombramiento',
        help='Puede diferir del puesto base del empleado.',
    )
    hourly_rate = fields.Monetary(
        string='Tarifa por Hora (₡)',
        currency_field='currency_id',
        tracking=True,
        help='Si se deja en 0, se calcula automáticamente del salario base del empleado.',
    )
    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.company.currency_id,
    )
    state = fields.Selection([
        ('draft',     'Borrador'),
        ('confirmed', 'Confirmado'),
        ('in_payroll','En Planilla'),
        ('paid',      'Pagado'),
        ('cancelled', 'Cancelado'),
    ], default='draft', tracking=True, string='Estado')

    turno_ids = fields.One2many(
        'nombramientos.turno', 'nombramiento_id',
        string='Turnos / Días Trabajados',
    )

    # Computed totals
    total_hours = fields.Float(
        string='Total Horas', compute='_compute_totals', store=True,
    )
    total_amount = fields.Monetary(
        string='Monto Total (₡)', currency_field='currency_id',
        compute='_compute_totals', store=True,
    )
    turno_count = fields.Integer(
        string='Turnos', compute='_compute_totals', store=True,
    )

    notes = fields.Text(string='Observaciones')

    # Link to payroll run when sent to planilla
    payroll_run_id = fields.Many2one(
        'planilla.run.cr', string='Planilla Generada',
        readonly=True, tracking=True,
    )
    payslip_id = fields.Many2one(
        'planilla.payslip.cr', string='Boleta Generada',
        readonly=True,
    )

    @api.depends('employee_id', 'branch_id', 'date_start')
    def _compute_display_name_full(self):
        for rec in self:
            emp = rec.employee_id.name or 'Sin empleado'
            branch = rec.branch_id.name or ''
            date = rec.date_start.strftime('%d/%m') if rec.date_start else ''
            rec.display_name_full = f'{emp} — {branch} ({date})' if branch else f'{emp} ({date})'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'Nuevo') == 'Nuevo':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'nombramientos.nombramiento') or 'Nuevo'
        return super().create(vals_list)

    @api.depends('turno_ids.hours', 'turno_ids.amount')
    def _compute_totals(self):
        for rec in self:
            rec.total_hours  = sum(rec.turno_ids.mapped('hours'))
            rec.total_amount = sum(rec.turno_ids.mapped('amount'))
            rec.turno_count  = len(rec.turno_ids)

    @api.onchange('employee_id')
    def _onchange_employee(self):
        if self.employee_id:
            # Q-RATE: usar función centralizada de constants.py
            base = C.leer_base_salary(self.env.cr, self.employee_id.id)
            if not base:
                base = getattr(self.employee_id, 'wage', 0) or 0
            if base:
                self.hourly_rate = C.calcular_tarifa_hora(base)

    @api.onchange('hourly_rate')
    def _onchange_hourly_rate(self):
        # Propagar la tarifa actualizada a todos los turnos existentes
        for turno in self.turno_ids:
            turno.hourly_rate = self.hourly_rate

    @api.onchange('date_start')
    def _onchange_date_start(self):
        if self.date_start and not self.date_end:
            # Default: semana completa (7 días)
            self.date_end = self.date_start + datetime.timedelta(days=6)

    def action_confirm(self):
        for rec in self:
            # Asignar código si todavía es 'Nuevo'
            if rec.name == 'Nuevo':
                seq = self.env['ir.sequence'].next_by_code('nombramientos.nombramiento')
                if seq:
                    rec.write({'name': seq})
            if not rec.turno_ids:
                raise ValidationError(
                    f'El nombramiento {rec.name} no tiene turnos registrados.')
            if rec.total_hours <= 0:
                raise ValidationError(
                    f'El nombramiento {rec.name} tiene 0 horas totales.')
            # L3: Verificar jornada máxima semanal (Art. 136 CT)
            cfg = self.env['nombramientos.config'].search(
                [('company_id', '=', rec.company_id.id)], limit=1)
            shift_type = cfg.default_shift_type if cfg else 'day'
            max_weekly = C.MAX_HORAS_JORNADA.get(shift_type, 8) * 6
            if rec.total_hours > max_weekly:
                raise ValidationError(
                    f'{rec.name}: {rec.total_hours:.1f}h supera el máximo semanal '
                    f'de {max_weekly}h para jornada '
                    f'{dict(C.MAX_HORAS_JORNADA).get(shift_type, "diurna")}. '
                    f'(Art. 136 CT)')
            # L5: Verificar día de descanso obligatorio (Art. 59 CT)
            dias_trabajados = len(rec.turno_ids.filtered(lambda t: t.state != 'absent'))
            if dias_trabajados >= 7:
                raise ValidationError(
                    f'{rec.name}: el empleado trabajaría los 7 días de la semana '
                    f'sin día de descanso. Art. 59 CT exige al menos 1 día libre '
                    f'por cada 6 trabajados.')
            rec.write({'state': 'confirmed'})
            # L7: Notificar al empleado si está configurado
            if cfg and cfg.notify_employee and rec.employee_id.work_email:
                try:
                    template = self.env.ref(
                        'nombramientos_cr.mail_template_turno_confirmado',
                        raise_if_not_found=False)
                    if template:
                        template.send_mail(rec.id, force_send=False)
                except Exception:
                    _logger.warning(
                        'No se pudo notificar al empleado %s', rec.employee_id.name)

    def action_reset_to_draft(self):
        for rec in self:
            if rec.state not in ('confirmed', 'in_payroll', 'cancelled'):
                continue
            # Si tiene boleta generada, cancelarla también
            if rec.payslip_id and rec.payslip_id.exists():
                slip = rec.payslip_id
                if slip.state == 'draft':
                    slip.unlink()
                elif slip.state == 'confirmed':
                    try:
                        slip.action_cancel()
                    except Exception:
                        pass
            rec.write({
                'state':         'draft',
                'payslip_id':    False,
                'payroll_run_id': False,
            })

    def action_confirm_week(self):
        """Confirmar todos los nombramientos de la semana de este nombramiento."""
        self.ensure_one()
        week_noms = self.search([
            ('date_start', '=', self.date_start),
            ('company_id', '=', self.company_id.id),
            ('state', '=', 'draft'),
        ])
        confirmed = 0
        for rec in week_noms:
            if rec.turno_ids and rec.total_hours > 0:
                rec.write({'state': 'confirmed'})
                confirmed += 1
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Semana confirmada',
                'message': f'{confirmed} nombramiento(s) confirmado(s).',
                'type': 'success',
                'sticky': False,
            },
        }

    def action_generate_planilla_week(self):
        # Mismo comportamiento que action_open_generar_planilla
        return self.action_open_generar_planilla()

    def action_cancel(self):
        for rec in self:
            if rec.state == 'in_payroll':
                raise UserError(
                    'No se puede cancelar un nombramiento que ya está en planilla.')
            rec.write({'state': 'cancelled'})

    def action_reset_draft(self):
        for rec in self:
            if rec.state not in ('confirmed', 'cancelled'):
                raise UserError('Solo se pueden revertir nombramientos confirmados o cancelados.')
            rec.write({'state': 'draft'})

    def action_add_week_turnos(self):
        """Generar automáticamente turnos lunes-viernes para la semana."""
        self.ensure_one()
        if not self.date_start or not self.date_end:
            raise UserError('Defina las fechas de inicio y fin primero.')
        d = self.date_start
        while d <= self.date_end:
            if d.weekday() < 5:  # lunes=0 ... viernes=4
                # Check if already exists
                existing = self.turno_ids.filtered(lambda t: t.date == d)
                if not existing:
                    eff_rate = self.hourly_rate or self._get_effective_rate()
                    self.env['nombramientos.turno'].create({
                        'nombramiento_id': self.id,
                        'date': d,
                        'hour_start': 8.0,
                        'hour_end': 17.0,
                        'hours': 8.0,
                        'hourly_rate': eff_rate,
                    })
            d += datetime.timedelta(days=1)

    def _get_effective_rate(self):
        if self.hourly_rate:
            return self.hourly_rate
        base = getattr(self.employee_id, 'base_salary', 0) or 0
        return C.calcular_tarifa_hora(base)

    def action_open_generar_planilla(self):
        # Abrir wizard pre-cargado con TODOS los nombramientos de la semana
        self.ensure_one()
        # Buscar todos los nombramientos de la misma semana y empresa
        week_noms = self.search([
            ('date_start', '=', self.date_start),
            ('company_id', '=', self.company_id.id),
            ('state', 'in', ['draft', 'confirmed']),
        ])
        return {
            'type': 'ir.actions.act_window',
            'name': 'Generar Planilla Semanal',
            'res_model': 'nombramientos.generar.planilla.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_date_start': str(self.date_start),
                'default_date_end':   str(self.date_end),
                'default_company_id': self.company_id.id,
                'default_nombramiento_ids': [(6, 0, week_noms.ids)],
            },
        }
