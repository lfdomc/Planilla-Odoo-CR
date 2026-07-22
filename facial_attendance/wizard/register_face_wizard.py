# -*- coding: ascii -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class FacialRegisterWizard(models.TransientModel):
    _name = 'facial.register.wizard'
    _description = 'Asistente de Registro Facial'

    employee_id = fields.Many2one(
        'hr.employee',
        string='Empleado',
        required=True,
        domain=[('active', '=', True)],
    )
    captured_image = fields.Char(
        string='Imagen Capturada (Base64)',
        help='Imagen capturada por la camara web',
    )
    state = fields.Selection([
        ('capture', 'Capturar Imagen'),
        ('done', 'Completado'),
    ], default='capture', string='Estado')

    face_already_registered = fields.Boolean(
        related='employee_id.face_registered',
        readonly=True,
    )

    def action_save_face(self):
        """Guarda la imagen capturada como encoding facial del empleado."""
        self.ensure_one()
        if not self.captured_image:
            raise UserError(_(
                "Por favor capture una imagen primero usando la camara."
            ))
        if not self.employee_id:
            raise UserError(_("Seleccione un empleado."))

        self.employee_id.save_face_encoding(self.captured_image)
        self.write({'state': 'done'})

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Registro exitoso!'),
                'message': _(
                    'El rostro de %s ha sido registrado correctamente.'
                ) % self.employee_id.name,
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    # Nota: el "retake" (volver a capturar) se maneja enteramente en el
    # widget de camara (JS): FacialCameraWidget.retakeImage() vuelve a
    # mostrar el video y limpia el campo captured_image via record.update().
    # El boton de footer "Volver a Capturar" que dependia de un estado
    # 'confirm' nunca asignado se elimino junto con action_retake() y el
    # estado 'confirm' mismo, que nunca se usaron.
