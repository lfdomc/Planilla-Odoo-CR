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
        ('confirm', 'Confirmar'),
        ('done', 'Completado'),
    ], default='capture', string='Estado')

    face_already_registered = fields.Boolean(
        related='employee_id.face_registered',
        readonly=True,
    )

    def action_capture(self):
        """Accion del wizard: abrir captura de camara (manejado en JS)."""
        return {'type': 'ir.actions.do_nothing'}

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

    def action_retake(self):
        """Volver a capturar imagen."""
        self.write({
            'state': 'capture',
            'captured_image': False,
        })
        return {'type': 'ir.actions.do_nothing'}
