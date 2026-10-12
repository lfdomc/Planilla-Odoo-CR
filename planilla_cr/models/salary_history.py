import logging
from odoo import models, fields, api
from odoo.exceptions import UserError


_logger = logging.getLogger(__name__)
class SalaryHistory(models.Model):
    _name = 'planilla.salary.history'
    _description = 'Historial de Salarios'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'effective_date desc'

    employee_id    = fields.Many2one(
        'hr.employee', string='Empleado', required=True, ondelete='cascade', index=True
    )
    branch_id      = fields.Many2one(related='employee_id.branch_id', string='Sucursal', store=True)
    effective_date = fields.Date(string='Fecha Efectiva', required=True)
    salary         = fields.Monetary(string='Salario Neto',   currency_field='currency_id', required=True)
    gross_salary   = fields.Monetary(string='Salario Bruto',  currency_field='currency_id')
    currency_id    = fields.Many2one(related='employee_id.currency_id', store=True)
    reason         = fields.Char(string='Motivo', required=True)
    payslip_id     = fields.Many2one('planilla.payslip.cr', string='Boleta de Pago')
    note           = fields.Text(string='Notas')

    # -- Flujo de autorizacion ------------------------------------------
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('authorized', 'Autorizado'),
        ('rejected',   'Rechazado'),
    ], default='draft', string='Estado', tracking=True)

    authorized_by   = fields.Many2one('res.users', string='Autorizado por', readonly=True)
    authorized_date = fields.Datetime(string='Fecha de Autorizacion', readonly=True)
    rejection_note  = fields.Text(string='Motivo de Rechazo', readonly=True)

    # -- Salario anterior (para mostrar variacion) ---------------------
    previous_salary = fields.Monetary(
        string='Salario Anterior (CRC)', currency_field='currency_id',
        compute='_compute_previous_salary', store=True
    )
    variation_amount = fields.Monetary(
        string='Variacion (CRC)', currency_field='currency_id',
        compute='_compute_previous_salary', store=True
    )
    variation_pct = fields.Float(
        string='Variacion (%)', compute='_compute_previous_salary', store=True
    )

    @api.depends('employee_id', 'effective_date', 'gross_salary')
    def _compute_previous_salary(self):
        # FIX PERF-07: cuando se computa sobre un recordset (ej. al crear varias historias
        # juntas), pre-cargar todos los historicos de los empleados involucrados en una sola
        # query y resolver en Python, en lugar de 1 search() por registro.
        if not self:
            return
        emp_ids = self.mapped('employee_id').ids
        all_hist = self.search([
            ('employee_id', 'in', emp_ids),
            ('state', '=', 'authorized'),
        ], order='employee_id, effective_date desc')
        # Indice: employee_id -> lista ordenada desc por fecha
        by_emp = {}
        for h in all_hist:
            by_emp.setdefault(h.employee_id.id, []).append(h)

        for rec in self:
            hist_list = by_emp.get(rec.employee_id.id, [])
            # Buscar el mas reciente ANTERIOR a rec.effective_date, excluyendo rec mismo
            prev_salary = 0.0
            for h in hist_list:
                if h.id != rec.id and h.effective_date and rec.effective_date:
                    if h.effective_date < rec.effective_date:
                        prev_salary = h.gross_salary or 0.0
                        break
            rec.previous_salary  = prev_salary
            rec.variation_amount = rec.gross_salary - prev_salary
            rec.variation_pct    = (
                ((rec.gross_salary - prev_salary) / prev_salary * 100)
                if prev_salary else 0.0
            )

    def action_authorize(self):
        for rec in self:
            if not self.env.user.has_group('planilla_cr.group_planilla_aprobador'):
                raise UserError('Solo un aprobador de planilla puede autorizar cambios salariales.')
            if rec.state != 'draft':
                raise UserError('El registro ya fue procesado (estado: %s).' % rec.state)
            rec.write({
                'state':           'authorized',
                'authorized_by':   self.env.user.id,
                'authorized_date': fields.Datetime.now(),
                'rejection_note':  False,
            })
            # FIX C-01 v59: Actualizar el salario base del empleado en hr.employee.
            # Sin este paso, la autorizacion es solo administrativa -- las proximas
            # boletas calcularian CCSS, Renta y provisiones sobre el salario viejo.
            # FIX-Q15: usar skip_salary_history=True en el contexto para que
            # hr_employee_extension.write() NO cree un segundo registro de historial
            # salarial. El registro ya existe (este mismo rec) y acaba de ser autorizado.
            # Sin este contexto, action_authorize creaba un duplicado en planilla.salary.history.
            if rec.gross_salary and rec.gross_salary > 0:
                rec.employee_id.with_context(skip_salary_history=True).write({
                    'base_salary':           rec.gross_salary,
                    'salary_effective_date': rec.effective_date,
                })
                _logger.info(
                    'planilla_cr.salary_history.authorize: empleado %s -- '
                    'base_salary CRC%.2f -> CRC%.2f (efectivo: %s)',
                    rec.employee_id.name,
                    rec.previous_salary or 0.0,
                    rec.gross_salary,
                    rec.effective_date,
                )
            # Registrar movimiento de aumento salarial
            self.env['planilla.employee.movement'].create({
                'employee_id':       rec.employee_id.id,
                'movement_date':     rec.effective_date or fields.Date.today(),
                'movement_type':     'aumento',
                'reason':            rec.reason or 'Cambio salarial',
                'salary_before':     rec.previous_salary or 0.0,
                'salary_after':      rec.gross_salary or 0.0,
                'company_id':        rec.employee_id.company_id.id,
                'salary_history_id': rec.id,
                'note':              rec.note or False,
            })
            if rec.employee_id.work_email:
                try:
                    template = self.env.ref(
                        'planilla_cr.email_template_salary_authorized', raise_if_not_found=False
                    )
                    if template:
                        template.send_mail(rec.id, force_send=False)
                except Exception as e:
                    _logger.warning(
                        'planilla_cr: email historial salarial fallido (%s): %s', rec.id, e
                    )

    def action_reject(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Motivo de Rechazo',
            'res_model': 'planilla.salary.reject.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_history_id': self.id},
        }

    def action_reset_draft(self):
        for rec in self:
            rec.write({'state': 'draft', 'authorized_by': False,
                       'authorized_date': False, 'rejection_note': False})

    def action_print_history(self):
        employees = self.mapped('employee_id')
        return self.env.ref('planilla_cr.action_report_salary_history').report_action(employees)


class SalaryRejectWizard(models.TransientModel):
    _name = 'planilla.salary.reject.wizard'
    _description = 'Wizard Rechazo de Cambio Salarial'
    # FIX P-04 v59: TransientModel no necesita mail.thread ni mail.activity.mixin.
    # Heredarlos genera registros en mail.message en un modelo efimero, consumiendo
    # espacio en BD innecesariamente.

    history_id = fields.Many2one('planilla.salary.history', required=True)
    reason     = fields.Text(string='Motivo de Rechazo', required=True)

    def action_confirm(self):
        self.history_id.write({
            'state':          'rejected',
            'rejection_note': self.reason,
        })
        return {'type': 'ir.actions.act_window_close'}
