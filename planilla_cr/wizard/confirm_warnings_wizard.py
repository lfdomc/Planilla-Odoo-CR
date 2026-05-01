from odoo import models, fields, api


class ConfirmWithWarningsWizard(models.TransientModel):
    _name = 'planilla.confirm.warnings.wizard'
    _description = 'Confirmar Planilla con Advertencias'

    run_id = fields.Many2one('planilla.run.cr', required=True)
    warnings_text = fields.Text(string='Advertencias', readonly=True)
    message = fields.Char(
        default='Las siguientes advertencias se encontraron. '
                'Puede confirmar la planilla de todas formas.',
        readonly=True
    )

    def action_confirm_anyway(self):
        self.ensure_one()
        run = self.run_id
        payslips_draft = run.payslip_ids.filtered(lambda p: p.state == 'draft')
        with self.env.cr.savepoint():
            payslips_draft.write({'state': 'confirmed'})
            run.write({'state': 'confirmed'})
        return {'type': 'ir.actions.act_window_close'}
