# -*- coding: utf-8 -*-
from odoo import models, fields


class FacialLogCleanupWizard(models.TransientModel):
    _name = 'facial.log.cleanup.wizard'
    _description = 'Limpiar Imagenes Antiguas de Reconocimiento Facial'

    days = fields.Integer(
        string='Conservar imagenes de los ultimos (dias)',
        default=30, required=True,
        help='Se eliminaran las imagenes capturadas (no los registros '
             'en si) de reconocimientos con mas antiguedad que este '
             'numero de dias. El historial de marcaciones (fecha, '
             'empleado, resultado) se conserva completo.',
    )

    def action_confirm(self):
        self.ensure_one()
        count = self.env['facial.attendance.log'].action_cleanup_old_images(
            days=self.days)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Limpieza completada',
                'message': (
                    f'{count} imagen(es) eliminada(s). Los registros de '
                    f'marcacion se conservan intactos.'
                    if count else
                    'No se encontraron imagenes que cumplan el criterio.'
                ),
                'type': 'success',
            },
        }
