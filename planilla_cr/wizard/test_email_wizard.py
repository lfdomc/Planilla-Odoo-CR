from odoo import models, fields
from odoo.exceptions import UserError


class TestEmailWizard(models.TransientModel):
    _name = 'planilla.test.email.wizard'
    _description = 'Prueba de Envio de Correo'

    config_id = fields.Many2one(
        'planilla.accounting.config',
        string='Configuracion',
        required=True,
    )
    recipient = fields.Char(
        string='Destinatario',
        required=True,
        help='Correo electronico al que se enviara la prueba.'
    )

    def action_send(self):
        self.ensure_one()
        if not self.config_id:
            raise UserError('No hay configuracion de correo disponible.')
        return self.config_id._do_send_test_email(self.recipient)
