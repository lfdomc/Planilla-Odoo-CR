from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError


class PlanillaClosedPeriod(models.Model):
    _name = 'planilla.closed.period'
    _description = 'Periodo Cerrado de Planilla'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_from desc'

    company_id  = fields.Many2one('res.company', required=True,
                                   default=lambda self: self.env.company)
    branch_id   = fields.Many2one('planilla.branch', string='Sucursal',
                                   help='Dejar vacio para aplicar a toda la empresa')
    date_from   = fields.Date(string='Desde', required=True)
    date_to     = fields.Date(string='Hasta',  required=True)
    name        = fields.Char(string='Descripcion', required=True)
    state       = fields.Selection([
        ('closed',   'Cerrado'),
        ('reopened', 'Reabierto'),
    ], default='closed', string='Estado', readonly=True)

    # Quien cerro
    closed_by   = fields.Many2one('res.users', string='Cerrado por',
                                   default=lambda self: self.env.user, readonly=True)
    closed_date = fields.Datetime(string='Fecha de Cierre',
                                   default=lambda self: fields.Datetime.now(), readonly=True)
    notes       = fields.Text(string='Notas')

    # Planillas incluidas al momento del cierre
    run_ids     = fields.Many2many(
        'planilla.run.cr', string='Planillas en este Periodo',
        help='Planillas que estaban pagadas cuando se cerro el periodo.',
        readonly=True
    )
    payslip_count = fields.Integer(
        string='Boletas', compute='_compute_payslip_count'
    )

    # Reapertura
    reopened_by    = fields.Many2one('res.users', string='Reabierto por', readonly=True)
    reopened_date  = fields.Datetime(string='Fecha de Reapertura', readonly=True)
    reopen_reason  = fields.Text(string='Motivo de Reapertura', readonly=True)

    @api.depends('run_ids')
    def _compute_payslip_count(self):
        for rec in self:
            rec.payslip_count = sum(len(r.payslip_ids) for r in rec.run_ids)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            # Auto-capturar planillas pagadas en el periodo al momento del cierre.
            # FIX-AUD-14: planilla.run.cr usa date_start/date_end (no date_from/date_to)
            # y el estado pagado es 'done' (no 'paid').
            runs = self.env['planilla.run.cr'].search([
                ('company_id', '=', rec.company_id.id),
                ('state', '=', 'done'),
                ('date_start', '>=', rec.date_from),
                ('date_end',   '<=', rec.date_to),
            ])
            if runs:
                rec.run_ids = [(6, 0, runs.ids)]
        return records

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for rec in self:
            if rec.date_from > rec.date_to:
                raise ValidationError('La fecha inicio no puede ser mayor a la fecha fin.')

    def action_reopen(self):
        """Reabre el periodo con autorizacion de admin y registro de motivo."""
        self.ensure_one()
        if not self.env.user.has_group('planilla_cr.group_planilla_admin'):
            raise UserError('Solo un administrador de planilla puede reabrir periodos cerrados.')
        return {
            'type': 'ir.actions.act_window',
            'name': 'Motivo de Reapertura',
            'res_model': 'planilla.reopen.period.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_period_id': self.id},
        }

    def action_view_payslips(self):
        self.ensure_one()
        payslip_ids = self.run_ids.mapped('payslip_ids').ids
        return {
            'type': 'ir.actions.act_window',
            'name': f'Boletas -- {self.name}',
            'res_model': 'planilla.payslip.cr',
            'view_mode': 'list,form',
            'domain': [('id', 'in', payslip_ids)],
        }

    @classmethod
    def is_period_closed(cls, env, company_id, date_from, date_to, branch_id=False):
        """Verifica si un periodo esta cerrado. Retorna el registro si esta cerrado."""
        domain = [
            ('company_id', '=', company_id),
            ('date_from', '<=', date_to),
            ('date_to', '>=', date_from),
            ('state', '=', 'closed'),
        ]
        if branch_id:
            domain += ['|', ('branch_id', '=', branch_id), ('branch_id', '=', False)]
        else:
            domain.append(('branch_id', '=', False))
        return env['planilla.closed.period'].search(domain, limit=1)


class ReopenPeriodWizard(models.TransientModel):
    _name = 'planilla.reopen.period.wizard'
    _description = 'Wizard Reapertura de Periodo'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    period_id = fields.Many2one('planilla.closed.period', required=True)
    reason    = fields.Text(string='Motivo de Reapertura', required=True,
                             help='Explique por que es necesario reabrir este periodo.')

    def action_confirm_reopen(self):
        self.ensure_one()
        self.period_id.write({
            'state':         'reopened',
            'reopened_by':   self.env.user.id,
            'reopened_date': fields.Datetime.now(),
            'reopen_reason': self.reason,
        })
        return {'type': 'ir.actions.act_window_close'}
