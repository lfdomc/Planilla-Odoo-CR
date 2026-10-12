from odoo import models, fields, api
from odoo.exceptions import UserError


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
        """El usuario ya vio las advertencias (soft-checks) y decide continuar.
        Eso NO exime los chequeos obligatorios: permisos de Aprobador y
        _validate_before_confirm(). Por eso se delega en el action_confirm()
        real del mixin en vez de escribir el estado directamente -- antes
        este wizard saltaba ambos chequeos por completo.
        """
        self.ensure_one()
        run = self.run_id
        payslips_draft = run.payslip_ids.filtered(lambda p: p.state == 'draft')
        with self.env.cr.savepoint():
            try:
                payslips_draft.action_confirm()
            except Exception as e:
                raise UserError(
                    f'No se pudo confirmar la planilla "{run.name}". '
                    f'Ninguna boleta fue confirmada (rollback automatico).\n\n'
                    f'Error: {str(e)}'
                )
            run.write({'state': 'confirmed'})
        return {'type': 'ir.actions.act_window_close'}
