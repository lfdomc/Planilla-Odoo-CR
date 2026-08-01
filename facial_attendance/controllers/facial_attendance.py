# -*- coding: utf-8 -*-
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

    # -- Quiosco --------------------

    @http.route('/facial_attendance/kiosk', type='http', auth='user', website=False)
    def kiosk(self, **kwargs):
        """Pagina principal del quiosco (usuario logueado en el backend)."""
        return request.render('facial_attendance.kiosk_template', {
            'company': request.env.company,
            'public_mode': False,
            'public_kiosk_enabled': True,
            'recognize_url': '/facial_attendance/recognize',
        })

    @http.route('/facial_attendance/kiosk/public', type='http', auth='public', website=False)
    def kiosk_public(self, **kwargs):
        """Quiosco en modo publico (sin login), pensado para tablets dedicadas.
        Debe habilitarse explicitamente desde Ajustes por motivos de seguridad."""
        ICP = request.env['ir.config_parameter'].sudo()
        enabled = ICP.get_param('facial_attendance.enable_public_kiosk', 'False') == 'True'
        return request.render('facial_attendance.kiosk_template', {
            'company': request.env.company,
            'public_mode': True,
            'public_kiosk_enabled': enabled,
            'recognize_url': '/facial_attendance/kiosk/public/recognize',
        })

    @http.route('/facial_attendance/k/<string:token>', type='http', auth='public', website=False)
    def kiosk_by_token(self, token, **kwargs):
        """Enlace directo y unico de un kiosco especifico, identificado por
        su access_token. Publico (sin login) y sin ningun menu de Odoo
        alrededor -- equivalente al enlace con token que genera el
        Modo Quiosco nativo de Asistencias, pero propio de Reconocimiento
        Facial y por kiosco individual (cada dispositivo puede tener su
        propio enlace, en vez de un unico enlace compartido por toda la
        compania).

        No requiere que el toggle 'quiosco publico' este activo -- el
        propio token ya es el mecanismo de control de acceso, igual que
        el token del kiosco nativo de Odoo.
        """
        Kiosk = request.env['facial.attendance.kiosk'].sudo()
        kiosk = Kiosk.search([('access_token', '=', token)], limit=1)
        if not kiosk:
            return request.render('facial_attendance.kiosk_invalid_token_template', {})
        return request.render('facial_attendance.kiosk_template', {
            'company': kiosk.company_id or request.env.company,
            'public_mode': True,
            'public_kiosk_enabled': True,
            'recognize_url': f'/facial_attendance/k/{token}/recognize',
        })

    @http.route('/facial_attendance/k/<string:token>/recognize',
                type='json', auth='public', methods=['POST'])
    def recognize_face_by_token(self, token, image_data, action_type=None,
                                 device_ip=None, device_token=None,
                                 gps_lat=None, gps_lng=None, **kwargs):
        """Variante de recognize_face para el enlace con token por kiosco.
        Valida el access_token antes de procesar -- si no coincide con
        ningun kiosco, rechaza sin intentar el reconocimiento facial.
        """
        Kiosk = request.env['facial.attendance.kiosk'].sudo()
        if not Kiosk.search_count([('access_token', '=', token)]):
            return {
                'success': False,
                'error': 'invalid_kiosk_link',
                'error_detail': 'Este enlace de quiosco ya no es valido.',
            }
        return self.recognize_face(
            image_data, action_type=action_type, device_ip=device_ip,
            device_token=device_token, gps_lat=gps_lat, gps_lng=gps_lng,
        )

    @http.route('/facial_attendance/k/<string:token>/kiosk_status',
                type='json', auth='public', methods=['POST'])
    def kiosk_status_by_token(self, token, device_token=None,
                               gps_lat=None, gps_lng=None, **kwargs):
        """Variante de kiosk_status para el enlace con token por kiosco."""
        Kiosk = request.env['facial.attendance.kiosk'].sudo()
        if not Kiosk.search_count([('access_token', '=', token)]):
            return {'status': 'not_required', 'distance_meters': None}
        return self._do_kiosk_status(device_token, gps_lat, gps_lng)

    # -- API de reconocimiento (usuario autenticado) --------------------

    @http.route('/facial_attendance/recognize', type='json', auth='user', methods=['POST'])
    def recognize_face(self, image_data, action_type=None, device_ip=None,
                        device_token=None, gps_lat=None, gps_lng=None, **kwargs):
        """
        Recibe una imagen en base64, busca coincidencia facial
        y registra la asistencia. Requiere sesion de usuario interno.
        """
        if not FACE_RECOGNITION_AVAILABLE:
            return {
                'success': False,
                'error': 'face_recognition_not_installed',
                'error_detail': (
                    "La libreria 'face_recognition' no esta instalada. "
                    "Ejecute: pip install face_recognition numpy Pillow"
                ),
            }

        try:
            return self._do_recognize(
                image_data, action_type, device_ip,
                device_token=device_token, gps_lat=gps_lat, gps_lng=gps_lng,
            )
        except Exception as e:
            _logger.error('Error en reconocimiento facial: %s', str(e), exc_info=True)
            request.env['facial.attendance.log'].sudo().create_failed_log(
                error_code='error_interno',
                error_detail=str(e),
                device_ip=device_ip,
            )
            return {
                'success': False,
                'error': 'error_interno',
                'error_detail': str(e),
            }

    @http.route('/facial_attendance/kiosk/public/recognize',
                type='json', auth='public', methods=['POST'])
    def recognize_face_public(self, image_data, action_type=None, device_ip=None,
                               device_token=None, gps_lat=None, gps_lng=None, **kwargs):
        """Variante publica (sin login) de recognize_face, para tablets dedicadas.
        Deshabilitada por defecto; debe activarse desde Ajustes > Reconocimiento Facial."""
        ICP = request.env['ir.config_parameter'].sudo()
        if ICP.get_param('facial_attendance.enable_public_kiosk', 'False') != 'True':
            return {
                'success': False,
                'error': 'public_kiosk_disabled',
                'error_detail': (
                    'El quiosco publico esta deshabilitado. '
                    'Un administrador debe activarlo desde Ajustes.'
                ),
            }
        return self.recognize_face(
            image_data, action_type=action_type, device_ip=device_ip,
            device_token=device_token, gps_lat=gps_lat, gps_lng=gps_lng,
        )

    def _do_recognize(self, image_data, action_type, device_ip,
                       device_token=None, gps_lat=None, gps_lng=None):
        """Logica principal de reconocimiento facial."""
        env = request.env
        FacialLog = env['facial.attendance.log'].sudo()
        Kiosk = env['facial.attendance.kiosk'].sudo()

        # -- Resolver/registrar el kiosco desde el token del dispositivo --
        # Si no se envia token (navegador viejo en cache, o llamada
        # directa a la API), se continua sin kiosco -- no bloquea la
        # marcacion, solo queda sin device asociado ni validacion GPS.
        kiosk = Kiosk.browse()
        if device_token:
            user_agent = request.httprequest.headers.get('User-Agent', '')
            kiosk = Kiosk.get_or_create_pending(device_token, user_agent=user_agent)
            if kiosk and kiosk.state == 'pending':
                return {
                    'success': False,
                    'error': 'kiosk_pending_activation',
                    'error_detail': (
                        'Este dispositivo aun no ha sido activado. '
                        'Contacte a un administrador para autorizarlo '
                        'desde Reconocimiento Facial > Kioscos.'
                    ),
                }
            if kiosk and kiosk.state == 'revoked':
                return {
                    'success': False,
                    'error': 'kiosk_revoked',
                    'error_detail': (
                        'Este dispositivo fue revocado y ya no puede '
                        'registrar asistencia. Contacte a un administrador.'
                    ),
                }

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
            detail = 'No se detecto ningun rostro. Acerquese a la camara.'
            # FIX: ya no se registra en la base de datos -- "no habia
            # nadie enfrente" no aporta valor de auditoria, y generaba
            # decenas de registros identicos por sesion mientras el
            # kiosco esperaba a que alguien se acercara. Los demas
            # errores reales (no_match, encoding_failed,
            # no_employees_registered) si se siguen registrando.
            return {'success': False, 'error': 'no_face_detected', 'error_detail': detail}

        captured_encodings = face_recognition.face_encodings(image_np, face_locations)
        if not captured_encodings:
            detail = 'No se pudo procesar el rostro detectado.'
            FacialLog.create_failed_log(error_code='encoding_failed',
                                        error_detail=detail, device_ip=device_ip,
                                        kiosk_id=kiosk.id if kiosk else None)
            return {'success': False, 'error': 'encoding_failed', 'error_detail': detail}
        captured_encoding = captured_encodings[0]

        all_encodings = env['hr.employee'].sudo().get_all_face_encodings()
        if not all_encodings:
            detail = 'No hay empleados con rostro registrado en el sistema.'
            FacialLog.create_failed_log(error_code='no_employees_registered',
                                        error_detail=detail, device_ip=device_ip,
                                        kiosk_id=kiosk.id if kiosk else None)
            return {'success': False, 'error': 'no_employees_registered', 'error_detail': detail}

        known_encodings = [np.array(e['encoding']) for e in all_encodings]
        distances = face_recognition.face_distance(known_encodings, captured_encoding)

        best_idx = int(np.argmin(distances))
        best_distance = float(distances[best_idx])
        confidence = max(0.0, (1.0 - best_distance) * 100.0)

        if best_distance > tolerance or confidence < confidence_threshold:
            detail = (
                'No se encontro coincidencia. '
                'Asegurese de tener buena iluminacion y mirar directamente a la camara.'
            )
            FacialLog.create_failed_log(error_code='no_match', error_detail=detail,
                                        confidence=confidence, device_ip=device_ip,
                                        kiosk_id=kiosk.id if kiosk else None)
            return {
                'success': False,
                'error': 'no_match',
                'error_detail': detail,
                'confidence': round(confidence, 1),
            }

        matched = all_encodings[best_idx]
        employee_id = matched['employee_id']
        employee_name = matched['employee_name']

        if auto_action and not action_type:
            action_type = self._determine_action_type(employee_id)
        action_type = action_type or 'check_in'

        # -- Validacion GPS complementaria (nunca bloquea la marcacion) --
        # Si el kiosco tiene require_gps activo, se compara la ubicacion
        # reportada por el navegador contra las coordenadas de referencia.
        # Fuera de rango = se acepta la asistencia igual, pero queda
        # marcada out_of_range=True para revision del supervisor.
        out_of_range = False
        gps_distance = None
        lat_f = lng_f = None
        if kiosk and kiosk.require_gps:
            try:
                lat_f = float(gps_lat) if gps_lat is not None else None
                lng_f = float(gps_lng) if gps_lng is not None else None
            except (TypeError, ValueError):
                lat_f = lng_f = None
            in_range, gps_distance, _origen = kiosk.check_gps_in_range(lat_f, lng_f)
            out_of_range = not in_range

        captured_img_b64 = image_data if save_images else None
        log = FacialLog.create_from_recognition(
            employee_id=employee_id,
            action_type=action_type,
            confidence=confidence,
            captured_image_b64=captured_img_b64,
            device_ip=device_ip,
            kiosk_id=kiosk.id if kiosk else None,
            gps_latitude=lat_f,
            gps_longitude=lng_f,
            gps_distance_meters=gps_distance,
            out_of_range=out_of_range,
        )

        action_label = 'Entrada' if action_type == 'check_in' else 'Salida'
        now_str = fields.Datetime.now().strftime('%H:%M')

        message = '%s registrada para %s (%s)' % (action_label, employee_name, now_str)
        if out_of_range:
            message += ' -- ATENCION: fuera del area permitida, marcada para revision.'

        return {
            'success': True,
            'employee_id': employee_id,
            'employee_name': employee_name,
            'action_type': action_type,
            'action_label': action_label,
            'confidence': round(confidence, 1),
            'time': now_str,
            'log_id': log.id,
            'out_of_range': out_of_range,
            'gps_distance_meters': gps_distance,
            'message': message,
        }

    def _determine_action_type(self, employee_id):
        """Determina si el empleado debe marcar entrada o salida."""
        open_att = request.env['hr.attendance'].sudo().search([
            ('employee_id', '=', employee_id),
            ('check_out', '=', False),
        ], limit=1)
        return 'check_out' if open_att else 'check_in'

    # -- Registro facial --------------------

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

    # -- Estado del empleado --------------------

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

    # -- Verificacion de libreria --------------------

    @http.route('/facial_attendance/kiosk_status', type='json', auth='user', methods=['POST'])
    def kiosk_status(self, device_token=None, gps_lat=None, gps_lng=None, **kwargs):
        """Chequeo de posicion EN VIVO del kiosco, sin foto. El frontend
        lo llama periodicamente mientras la pantalla esta en espera, para
        mostrar el borde verde ("en el lugar correcto") o naranja/rojo
        ("fuera de zona") antes de que nadie intente marcar.

        Tambien es el punto donde se captura la ubicacion de referencia
        del kiosco la primera vez (ver
        FacialAttendanceKiosk.set_kiosk_location_from_device): si el
        kiosco requiere GPS y todavia no tiene una ubicacion guardada,
        la primera posicion valida que llega aqui se convierte en el
        punto de referencia contra el que se miden todas las
        marcaciones futuras.
        """
        return self._do_kiosk_status(device_token, gps_lat, gps_lng)

    @http.route('/facial_attendance/kiosk/public/kiosk_status',
                type='json', auth='public', methods=['POST'])
    def kiosk_status_public(self, device_token=None, gps_lat=None, gps_lng=None, **kwargs):
        """Variante publica de kiosk_status, para tablets dedicadas sin login."""
        ICP = request.env['ir.config_parameter'].sudo()
        if ICP.get_param('facial_attendance.enable_public_kiosk', 'False') != 'True':
            return {'status': 'not_required', 'distance_meters': None}
        return self._do_kiosk_status(device_token, gps_lat, gps_lng)

    def _do_kiosk_status(self, device_token, gps_lat, gps_lng):
        env = request.env
        Kiosk = env['facial.attendance.kiosk'].sudo()

        if not device_token:
            return {'status': 'not_required', 'distance_meters': None}

        kiosk = Kiosk.search([('device_token', '=', device_token)], limit=1)
        if not kiosk or kiosk.state != 'active':
            # Dispositivo pendiente/revocado/desconocido: el chequeo de
            # posicion no aplica todavia -- eso lo maneja el flujo normal
            # de reconocimiento (recognize_face), que si informa
            # claramente "dispositivo no activado".
            return {'status': 'not_required', 'distance_meters': None}

        try:
            lat_f = float(gps_lat) if gps_lat is not None else None
            lng_f = float(gps_lng) if gps_lng is not None else None
        except (TypeError, ValueError):
            lat_f = lng_f = None

        return kiosk.check_live_position(lat_f, lng_f)

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
