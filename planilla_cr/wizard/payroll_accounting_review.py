from odoo import models, fields, api
from odoo.exceptions import UserError


class PayrollAccountingReview(models.TransientModel):
    """Comparacion de asientos contables vs datos de planilla. Solo lectura."""
    _name = 'planilla.accounting.review'
    _description = 'Revision Asientos vs Planilla'

    date_from = fields.Date(
        string='Desde',
        required=True,
        default=lambda self: fields.Date.today().replace(day=1)
    )
    date_to = fields.Date(
        string='Hasta',
        required=True,
        default=fields.Date.today
    )
    line_ids = fields.One2many(
        'planilla.accounting.review.line', 'review_id',
        string='Planillas'
    )
    computed = fields.Boolean(default=False)
    total_runs = fields.Integer(string='Total planillas', readonly=True)
    ok_count = fields.Integer(string='Cuadran', readonly=True)
    disc_count = fields.Integer(string='Con diferencias', readonly=True)
    no_entry_count = fields.Integer(string='Sin asiento', readonly=True)

    def action_review(self):
        self.ensure_one()
        self.line_ids.unlink()
        lines = []

        runs = self.env['planilla.run.cr'].search([
            ('date_end', '>=', self.date_from),
            ('date_end', '<=', self.date_to),
            ('state', 'in', ('done', 'confirmed')),
        ], order='date_end asc')

        ok = disc = no_entry = 0

        for run in runs:
            move = run.move_id
            if not move:
                no_entry += 1
                lines.append((0, 0, {
                    'run_id':          run.id,
                    'run_name':        run.name,
                    'run_state':       run.state,
                    'date_end':        run.date_end,
                    'move_name':       False,
                    'move_state':      False,
                    'planilla_bruto':  run.total_gross or 0,
                    'planilla_neto':   run.total_net or 0,
                    'planilla_ccss':   (run.total_ccss_employee or 0) + (run.total_ccss_employer or 0),
                    'asiento_salarios': 0,
                    'asiento_ccss':    0,
                    'asiento_neto':    0,
                    'dif_salarios':    0,
                    'dif_ccss':        0,
                    'dif_neto':        run.total_net or 0,
                    'estado':          'sin_asiento',
                    'tiene_diferencia': True,
                }))
                continue

            # Leer lineas del asiento
            move_lines = move.line_ids
            sal_debe   = round(sum(l.debit for l in move_lines if l.account_id.code and l.account_id.code.startswith('630000')), 2)
            ccss_haber = round(sum(l.credit for l in move_lines if l.account_id.code and l.account_id.code.startswith('230300')), 2)
            ins_haber  = round(sum(l.credit for l in move_lines if l.account_id.code and l.account_id.code.startswith('230400')), 2)
            neto_haber = round(sum(
                l.credit for l in move_lines
                if l.account_id.code and l.account_id.code.startswith('230000')
                and 'neto' in (l.name or '').lower()
            ), 2)

            planilla_bruto  = round(run.total_gross or 0, 2)
            planilla_ccss   = round((run.total_ccss_employee or 0) + (run.total_ccss_employer or 0), 2)
            planilla_neto   = round(run.total_net or 0, 2)

            dif_sal  = round(sal_debe  - planilla_bruto, 2)
            dif_ccss = round((ccss_haber + ins_haber) - planilla_ccss, 2)
            dif_neto = round(neto_haber - planilla_neto, 2)

            tiene_dif = abs(dif_sal) > 1 or abs(dif_ccss) > 1 or abs(dif_neto) > 1
            if tiene_dif:
                disc += 1
                estado = 'diferencia'
            else:
                ok += 1
                estado = 'ok'

            lines.append((0, 0, {
                'run_id':           run.id,
                'run_name':         run.name,
                'run_state':        run.state,
                'date_end':         run.date_end,
                'move_name':        move.name,
                'move_state':       move.state,
                'planilla_bruto':   planilla_bruto,
                'planilla_neto':    planilla_neto,
                'planilla_ccss':    planilla_ccss,
                'asiento_salarios': sal_debe,
                'asiento_ccss':     round(ccss_haber + ins_haber, 2),
                'asiento_neto':     neto_haber,
                'dif_salarios':     dif_sal,
                'dif_ccss':         dif_ccss,
                'dif_neto':         dif_neto,
                'estado':           estado,
                'tiene_diferencia': tiene_dif,
            }))

        self.write({
            'line_ids':        lines,
            'computed':        True,
            'total_runs':      ok + disc + no_entry,
            'ok_count':        ok,
            'disc_count':      disc,
            'no_entry_count':  no_entry,
        })
        return {
            'type':      'ir.actions.act_window',
            'res_model': 'planilla.accounting.review',
            'res_id':    self.id,
            'view_mode': 'form',
            'target':    'current',
        }


class PayrollAccountingReviewLine(models.TransientModel):
    _name = 'planilla.accounting.review.line'
    _description = 'Linea de Revision Contable'

    review_id       = fields.Many2one('planilla.accounting.review', ondelete='cascade')
    run_id          = fields.Many2one('planilla.run.cr', string='Planilla', readonly=True)
    run_name        = fields.Char(string='Planilla', readonly=True)
    run_state       = fields.Char(string='Estado Planilla', readonly=True)
    date_end        = fields.Date(string='Fecha', readonly=True)
    move_name       = fields.Char(string='Asiento', readonly=True)
    move_state      = fields.Char(string='Estado Asiento', readonly=True)
    # Planilla
    planilla_bruto  = fields.Monetary(string='Bruto Planilla', currency_field='currency_id', readonly=True)
    planilla_ccss   = fields.Monetary(string='CCSS Planilla', currency_field='currency_id', readonly=True)
    planilla_neto   = fields.Monetary(string='Neto Planilla', currency_field='currency_id', readonly=True)
    # Asiento
    asiento_salarios = fields.Monetary(string='630000 Salarios', currency_field='currency_id', readonly=True)
    asiento_ccss    = fields.Monetary(string='CCSS Asiento', currency_field='currency_id', readonly=True)
    asiento_neto    = fields.Monetary(string='Neto Asiento', currency_field='currency_id', readonly=True)
    # Diferencias
    dif_salarios    = fields.Monetary(string='Dif. Salarios', currency_field='currency_id', readonly=True)
    dif_ccss        = fields.Monetary(string='Dif. CCSS', currency_field='currency_id', readonly=True)
    dif_neto        = fields.Monetary(string='Dif. Neto', currency_field='currency_id', readonly=True)
    estado          = fields.Selection([
        ('ok',          'Cuadra'),
        ('diferencia',  'Diferencia'),
        ('sin_asiento', 'Sin Asiento'),
    ], string='Estado', readonly=True)
    tiene_diferencia = fields.Boolean(readonly=True)
    currency_id     = fields.Many2one(
        'res.currency',
        default=lambda self: self.env.company.currency_id
    )

    def action_open_run(self):
        self.ensure_one()
        if not self.run_id:
            return
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'planilla.run.cr',
            'res_id': self.run_id.id,
            'view_mode': 'form',
        }
