# -*- coding: utf-8 -*-
"""
Funciones administrativas de Reconocimiento Facial: registro de rostro
de un empleado, consulta de estado de asistencia, y verificacion de
disponibilidad de la libreria face_recognition.

Separado del flujo de marcacion (facial_recognition.py) porque estas
son acciones de backend usadas por administradores/RRHH, no parte del
kiosco de marcacion en si.
"""
import logging

from odoo import http
from odoo.http import request

try:
    FACE_RECOGNITION_AVAILABLE = True
    import face_recognition  # noqa: F401
except ImportError:
    FACE_RECOGNITION_AVAILABLE = False

_logger = logging.getLogger(__name__)


class FacialAdminController(http.Controller):

    @http.route('/facial_attendance/register', type='json', auth='user', methods=['POST'])
    def register_face(self, employee_id, image_data, **kwargs):
        """Registra el rostro de un empleado.

        El modelo se escribe con sudo() por simplicidad interna, por lo que el
        control de acceso debe validarse explicitamente aqui antes de delegar.
        """
        if not request.env.user.has_group('hr.group_hr_user'):
            return {
                'success': False,
                'error': 'No tiene permisos para registrar rostros de empleados.',
            }
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

    @http.route('/facial_attendance/employee_status/<int:employee_id>',
                type='json', auth='user', methods=['GET', 'POST'])
    def employee_status(self, employee_id, **kwargs):
        """Retorna el estado de asistencia actual de un empleado."""
        open_att = request.env['hr.attendance'].sudo().search([
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
