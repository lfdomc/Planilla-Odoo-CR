# -*- coding: ascii -*-
import base64
import json
import logging
import io

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

try:
    import face_recognition
    import numpy as np
    from PIL import Image
    FACE_RECOGNITION_AVAILABLE = True
except ImportError:
    FACE_RECOGNITION_AVAILABLE = False
    _logger.warning(
        "La libreria 'face_recognition' no esta instalada. "
        "Ejecute: pip install face_recognition numpy Pillow"
    )


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    face_encoding = fields.Text(
        string='Codificacion Facial',
        help='Datos internos de reconocimiento facial (no editar manualmente)',
        groups='hr.group_hr_user',
    )
    face_image = fields.Binary(
        string='Imagen Facial de Referencia',
        attachment=True,
        help='Foto de referencia usada para el reconocimiento facial',
    )
    face_image_filename = fields.Char(string='Nombre de archivo facial')
    face_registered = fields.Boolean(
        string='Rostro Registrado',
        compute='_compute_face_registered',
        store=True,
        help='Indica si el empleado tiene un rostro registrado para reconocimiento',
    )
    face_registration_date = fields.Datetime(
        string='Fecha de Registro Facial',
        readonly=True,
    )
    facial_attendance_ids = fields.One2many(
        'facial.attendance.log',
        'employee_id',
        string='Registros de Reconocimiento Facial',
    )
    facial_attendance_count = fields.Integer(
        string='Total Reconocimientos',
        compute='_compute_facial_attendance_count',
    )

    @api.depends('face_encoding')
    def _compute_face_registered(self):
        for rec in self:
            rec.face_registered = bool(rec.face_encoding)

    def _compute_facial_attendance_count(self):
        FacialLog = self.env['facial.attendance.log']
        for rec in self:
            rec.facial_attendance_count = FacialLog.search_count([
                ('employee_id', '=', rec.id),
            ])

    def action_register_face(self):
        """Abre el wizard para registrar el rostro del empleado."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Registrar Rostro - %s') % self.name,
            'res_model': 'facial.register.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_employee_id': self.id,
            },
        }

    def action_clear_face(self):
        """Elimina el registro facial del empleado."""
        self.ensure_one()
        self.write({
            'face_encoding': False,
            'face_image': False,
            'face_image_filename': False,
            'face_registration_date': False,
        })
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Registro facial eliminado'),
                'message': _('Se elimino el registro facial de %s.') % self.name,
                'type': 'warning',
                'sticky': False,
            },
        }

    def action_view_facial_logs(self):
        """Muestra los registros de reconocimiento facial del empleado."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Registros Faciales - %s') % self.name,
            'res_model': 'facial.attendance.log',
            'view_mode': 'list,form',
            'domain': [('employee_id', '=', self.id)],
            'context': {'default_employee_id': self.id},
        }

    def save_face_encoding(self, image_base64):
        """
        Procesa una imagen en base64, extrae la codificacion facial
        y la guarda en el empleado.
        """
        self.ensure_one()
        if not FACE_RECOGNITION_AVAILABLE:
            raise UserError(_(
                "La libreria 'face_recognition' no esta instalada en el servidor. "
                "Contacte al administrador del sistema."
            ))

        try:
            if ',' in image_base64:
                image_base64 = image_base64.split(',')[1]

            image_data = base64.b64decode(image_base64)
            image = Image.open(io.BytesIO(image_data)).convert('RGB')
            image_np = np.array(image)

            face_locations = face_recognition.face_locations(image_np, model='hog')
            if not face_locations:
                raise ValidationError(_(
                    "No se detecto ningun rostro en la imagen. "
                    "Asegurese de que la cara sea visible y bien iluminada."
                ))
            if len(face_locations) > 1:
                raise ValidationError(_(
                    "Se detectaron multiples rostros en la imagen (%d). "
                    "Por favor capture solo un rostro a la vez."
                ) % len(face_locations))

            face_encodings = face_recognition.face_encodings(image_np, face_locations)
            if not face_encodings:
                raise ValidationError(_(
                    "No se pudo calcular la codificacion facial. "
                    "Por favor intente con una imagen de mejor calidad."
                ))

            encoding_list = face_encodings[0].tolist()
            encoding_json = json.dumps(encoding_list)

            self.write({
                'face_encoding': encoding_json,
                'face_image': image_base64,
                'face_image_filename': 'face_%s.jpg' % self.id,
                'face_registration_date': fields.Datetime.now(),
            })
            _logger.info(
                'Rostro registrado exitosamente para el empleado: %s (ID: %s)',
                self.name, self.id,
            )
            return True

        except (ValidationError, UserError):
            raise
        except Exception as e:
            _logger.error(
                'Error al procesar imagen facial para empleado %s: %s',
                self.name, str(e),
            )
            raise UserError(_(
                "Error al procesar la imagen: %s"
            ) % str(e))

    @api.model
    def get_all_face_encodings(self):
        """
        Retorna todas las codificaciones faciales registradas.
        Usado por el controlador de reconocimiento.
        """
        employees = self.search([('face_encoding', '!=', False)])
        result = []
        for emp in employees:
            try:
                encoding = json.loads(emp.face_encoding)
                result.append({
                    'employee_id': emp.id,
                    'employee_name': emp.name,
                    'encoding': encoding,
                })
            except (json.JSONDecodeError, Exception) as e:
                _logger.warning(
                    'Codificacion facial invalida para empleado %s: %s',
                    emp.name, str(e),
                )
        return result
