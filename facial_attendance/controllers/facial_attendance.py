# -*- coding: ascii -*-
import base64
import json
import logging
import io

from odoo import http, fields, _
from odoo.http import request

_logger = logging.getLogger(__name__)

try:
    import face_recognition
    import numpy as np
    from PIL import Image
    FACE_RECOGNITION_AVAILABLE = True
except ImportError:
    FACE_RECOGNITION_AVAILABLE = False


class FacialAttendanceController(http.Controller):

    # Quiosco

    @http.route('/facial_attendance/kiosk', type='http', auth='user', website=False)
    def kiosk(self, **kwargs):
        """Pagina principal del quiosco de reconocimiento facial."""
        return request.render('facial_attendance.kiosk_template', {
            'company': request.env.company,
        })

    @http.route('/facial_attendance/kiosk/public', type='http', auth='public', website=False)
    def kiosk_public(self, **kwargs):
        """Quiosco en modo publico (sin login requerido)."""
        return request.render('facial_attendance.kiosk_template', {
            'company': request.env.company,
            'public_mode': True,
        })

    # API de reconocimiento

    @http.route('/facial_attendance/recognize', type='json', auth='user', methods=['POST'])
    def recognize_face(self, image_data, action_type=None, device_ip=None, **kwargs):
        """
        Recibe una imagen en base64, busca coincidencia facial
        y registra la asistencia.
        """
        if not FACE_RECOGNITION_AVAILABLE:
            return {
                'success': False,
                'error': 'face_recognition no instalado',
                'error_detail': (
                    "La libreria 'face_recognition' no esta instalada. "
                    "Ejecute: pip install face_recognition numpy Pillow"
                ),
            }

        try:
            result = self._do_recognize(image_data, action_type, device_ip)
            return result
        except Exception as e:
            _logger.error('Error en reconocimiento facial: %s', str(e), exc_info=True)
            return {
                'success': False,
                'error': 'error_interno',
                'error_detail': str(e),
            }

    def _do_recognize(self, image_data, action_type, device_ip):
        """Logica principal de reconocimiento."""
        env = request.env

        if ',' in image_data:
            image_data = image_data.split(',')[1]

        image_bytes = base64.b64decode(image_data)
        image = Image.open(io.BytesIO(image_bytes)).convert('RGB')
        image_np = np.array(image)

        ICP = env['ir.config_parameter'].sudo()
        tolerance = float(ICP.get_param('facial_attendance.tolerance', 0.55))
        confidence_threshold = float(ICP.get_param('facial_attendance.confidence_threshold', 60.0))
        recognition_model = ICP.get_param('facial_attendance.recognition_model', 'hog')
        save_images = ICP.get_param('facial_attendance.save_images', 'True') == 'True'
        auto_action = ICP.get_param('facial_attendance.auto_action', 'True') == 'True'

        face_locations = face_recognition.face_locations(image_np, model=recognition_model)
        if not face_locations:
            return {
                'success': False,
                'error': 'no_face_detected',
                'error_detail': 'No se detecto ningun rostro. Acerquese a la camara.',
            }

        captured_encodings = face_recognition.face_encodings(image_np, face_locations)
        if not captured_encodings:
            return {
                'success': False,
                'error': 'encoding_failed',
                'error_detail': 'No se pudo procesar el rostro detectado.',
            }
        captured_encoding = captured_encodings[0]

        all_encodings = env['hr.employee'].sudo().get_all_face_encodings()
        if not all_encodings:
            return {
                'success': False,
                'error': 'no_employees_registered',
                'error_detail': 'No hay empleados con rostro registrado en el sistema.',
            }

        known_encodings = [np.array(e['encoding']) for e in all_encodings]
        distances = face_recognition.face_distance(known_encodings, captured_encoding)

        best_idx = int(np.argmin(distances))
        best_distance = float(distances[best_idx])
        confidence = max(0.0, (1.0 - best_distance) * 100.0)

        if best_distance > tolerance or confidence < confidence_threshold:
            return {
                'success': False,
                'error': 'no_match',
                'error_detail': (
                    'No se encontro coincidencia. '
                    'Asegurese de tener buena iluminacion y mirar directamente a la camara.'
                ),
                'confidence': round(confidence, 1),
            }

        matched_employee_data = all_encodings[best_idx]
        employee_id = matched_employee_data['employee_id']
        employee_name = matched_employee_data['employee_name']

        if auto_action and not action_type:
            action_type = self._determine_action_type(employee_id)

        action_type = action_type or 'check_in'

        captured_img_b64 = image_data if save_images else None
        log = env['facial.attendance.log'].sudo().create_from_recognition(
            employee_id=employee_id,
            action_type=action_type,
            confidence=confidence,
            captured_image_b64=captured_img_b64,
            device_ip=device_ip,
        )

        action_label = 'Entrada' if action_type == 'check_in' else 'Salida'
        now_str = fields.Datetime.now().strftime('%H:%M')

        return {
            'success': True,
            'employee_id': employee_id,
            'employee_name': employee_name,
            'action_type': action_type,
            'action_label': action_label,
            'confidence': round(confidence, 1),
            'time': now_str,
            'log_id': log.id,
            'message': '%s registrada para %s (%s)' % (action_label, employee_name, now_str),
        }

    def _determine_action_type(self, employee_id):
        """Determina si el empleado debe marcar entrada o salida."""
        HrAttendance = request.env['hr.attendance'].sudo()
        open_attendance = HrAttendance.search([
            ('employee_id', '=', employee_id),
            ('check_out', '=', False),
        ], limit=1)
        return 'check_out' if open_attendance else 'check_in'

    # Registro facial

    @http.route('/facial_attendance/register', type='json', auth='user', methods=['POST'])
    def register_face(self, employee_id, image_data, **kwargs):
        """Registra el rostro de un empleado."""
        try:
            employee = request.env['hr.employee'].browse(int(employee_id))
            if not employee.exists():
                return {'success': False, 'error': 'Empleado no encontrado.'}

            employee.sudo().save_face_encoding(image_data)
            return {
                'success': True,
                'message': 'Rostro de %s registrado exitosamente.' % employee.name,
                'employee_name': employee.name,
            }
        except Exception as e:
            _logger.error('Error al registrar rostro: %s', str(e))
            return {'success': False, 'error': str(e)}

    # Estado del empleado

    @http.route('/facial_attendance/employee_status/<int:employee_id>',
                type='json', auth='user', methods=['GET', 'POST'])
    def employee_status(self, employee_id, **kwargs):
        """Retorna el estado de asistencia actual de un empleado."""
        HrAttendance = request.env['hr.attendance'].sudo()
        open_att = HrAttendance.search([
            ('employee_id', '=', employee_id),
            ('check_out', '=', False),
        ], limit=1, order='check_in desc')

        status = 'out'
        check_in_time = None
        if open_att:
            status = 'in'
            check_in_time = open_att.check_in.strftime('%H:%M') if open_att.check_in else None

        return {
            'employee_id': employee_id,
            'status': status,
            'check_in_time': check_in_time,
        }

    # Verificacion de libreria

    @http.route('/facial_attendance/check_library', type='json', auth='user', methods=['POST'])
    def check_library(self, **kwargs):
        """Verifica si la libreria face_recognition esta disponible."""
        return {
            'available': FACE_RECOGNITION_AVAILABLE,
            'message': (
                'face_recognition disponible' if FACE_RECOGNITION_AVAILABLE
                else 'Ejecute: pip install face_recognition numpy Pillow'
            ),
        }
