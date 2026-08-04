# -*- coding: utf-8 -*-
"""
Flujo principal de reconocimiento facial: recibe una foto, encuentra
el rostro, lo compara contra los empleados registrados, y registra la
asistencia si hay coincidencia.

Dividido en sub-metodos con responsabilidad unica (ver _do_recognize)
para que cada paso del proceso sea facil de leer, probar y modificar
de forma aislada, en vez de una sola funcion larga que hace todo.
"""
import base64
import io
import logging

from odoo import http, fields
from odoo.http import request

from . import _liveness_utils

_logger = logging.getLogger(__name__)

try:
    import face_recognition
    import numpy as np
    from PIL import Image
    FACE_RECOGNITION_AVAILABLE = True
except ImportError:
    FACE_RECOGNITION_AVAILABLE = False

# -- Constantes de deteccion de vida (liveness) por parpadeo -----------------
LIVENESS_WINDOW_SECONDS = 12  # ventana de tiempo para comparar dos intentos
                               # consecutivos del mismo kiosco como parte de
                               # la misma "sesion" de verificacion.
EAR_BLINK_THRESHOLD = 0.05    # diferencia minima de Eye Aspect Ratio entre
                               # intentos para considerarla un parpadeo real,
                               # no ruido normal de la deteccion.


class FacialRecognitionController(http.Controller):

    # -- Rutas HTTP -----------------------------------------------------

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

    @http.route('/facial_attendance/recognize', type='json', auth='user', methods=['POST'])
    def recognize_face(self, image_data, action_type=None, device_ip=None,
                        device_token=None, gps_lat=None, gps_lng=None,
                        log_on_failure=True, **kwargs):
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
                log_on_failure=log_on_failure,
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
                # No se expone str(e) (detalle tecnico crudo de la
                # excepcion) en la respuesta publica -- queda registrado
                # en el log interno (arriba) y en el log del servidor
                # para diagnostico del administrador, pero el kiosco
                # publico solo ve un mensaje generico y seguro.
                'error_detail': (
                    'Ocurrio un error inesperado al procesar el '
                    'reconocimiento. Intente de nuevo o contacte a un '
                    'administrador si el problema persiste.'
                ),
            }

    @http.route('/facial_attendance/kiosk/public/recognize',
                type='json', auth='public', methods=['POST'])
    def recognize_face_public(self, image_data, action_type=None, device_ip=None,
                               device_token=None, gps_lat=None, gps_lng=None,
                               log_on_failure=True, **kwargs):
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
            log_on_failure=log_on_failure,
        )

    # -- Orquestacion principal -------------------------------------------

    def _do_recognize(self, image_data, action_type, device_ip,
                       device_token=None, gps_lat=None, gps_lng=None,
                       log_on_failure=True):
        """
        Logica principal de reconocimiento facial, dividida en pasos
        claros (cada uno en su propio metodo privado abajo):
          1. Resolver/registrar el kiosco desde el token del dispositivo.
          2. Decodificar la imagen recibida.
          3. Verificar deteccion de vida por parpadeo (si esta activa).
          4. Detectar el rostro y compararlo contra los empleados
             registrados.
          5. Registrar la asistencia y armar la respuesta final.

        log_on_failure: si es False, los intentos fallidos (no_match,
        encoding_failed) NO se registran en facial.attendance.log. Se
        usa desde el kiosco para que, dentro de una misma ventana de
        deteccion activa (varios reintentos automaticos tras presionar
        "Marcar Asistencia"), solo el ultimo intento de la ventana
        quede registrado -- evita que una sola sesion de intento del
        usuario genere 3-4 registros identicos en la base de datos.
        Default True por seguridad: si algun llamador no envia este
        parametro, el comportamiento es el mismo de siempre (registrar).
        """
        env = request.env
        FacialLog = env['facial.attendance.log'].sudo()

        kiosk, kiosk_error = self._resolve_kiosk(device_token, gps_lat, gps_lng)
        if kiosk_error:
            return kiosk_error

        image_np = self._decode_image(image_data)

        ICP = env['ir.config_parameter'].sudo()
        # IMPORTANTE: Odoo BORRA la fila de ir.config_parameter (no la
        # deja en 'False') cuando un campo Boolean de Ajustes se
        # desmarca y se guarda. Esto significa que get_param() siempre
        # cae al default especificado aqui cuando el usuario desactiva
        # un toggle -- ese default DEBE coincidir exactamente con el
        # default=... del campo correspondiente en
        # res_config_settings.py, o el toggle parecera no tener efecto
        # (sintoma real que ocurrio con save_images: el campo tenia
        # default=False en Python, pero el get_param() de aqui seguia
        # usando 'True' como default, asi que desmarcar y guardar el
        # toggle nunca cambiaba el comportamiento real).
        tolerance = float(ICP.get_param('facial_attendance.tolerance', 0.55))
        confidence_threshold = float(ICP.get_param('facial_attendance.confidence_threshold', 60.0))
        recognition_model = ICP.get_param('facial_attendance.recognition_model', 'hog')
        save_images = ICP.get_param('facial_attendance.save_images', 'False') == 'True'
        auto_action = ICP.get_param('facial_attendance.auto_action', 'True') == 'True'
        liveness_enabled = ICP.get_param('facial_attendance.liveness_enabled', 'False') == 'True'

        face_locations = face_recognition.face_locations(image_np, model=recognition_model)
        if not face_locations:
            detail = 'No se detecto ningun rostro. Acerquese a la camara.'
            # No se registra en la base de datos -- "no habia nadie
            # enfrente" no aporta valor de auditoria, y generaba
            # decenas de registros identicos por sesion mientras el
            # kiosco esperaba a que alguien se acercara. Los demas
            # errores reales (no_match, encoding_failed,
            # no_employees_registered) si se siguen registrando.
            return {'success': False, 'error': 'no_face_detected', 'error_detail': detail}

        if liveness_enabled and kiosk:
            liveness_error = self._check_liveness(kiosk, image_np)
            if liveness_error:
                return liveness_error

        match_result = self._match_employee(
            image_np, face_locations, tolerance, confidence_threshold,
            FacialLog, device_ip, kiosk, log_on_failure,
        )
        if not match_result['success']:
            return match_result

        return self._register_attendance_and_respond(
            env, FacialLog, match_result, action_type, auto_action,
            gps_lat, gps_lng, kiosk, image_data, save_images, device_ip,
        )

    # -- Paso 1: resolver el kiosco ----------------------------------------

    def _resolve_kiosk(self, device_token, gps_lat, gps_lng):
        """
        Resuelve el kiosco a partir del token del dispositivo, o lo
        registra como pendiente de activacion si es la primera vez que
        se ve ese token.

        Retorna (kiosk, error_response). Si error_response no es None,
        el llamador debe devolverlo de inmediato sin continuar el
        reconocimiento (dispositivo pendiente o revocado).
        """
        env = request.env
        Kiosk = env['facial.attendance.kiosk'].sudo()
        kiosk = Kiosk.browse()

        # Si no se envia token (navegador viejo en cache, o llamada
        # directa a la API), se continua sin kiosco -- no bloquea la
        # marcacion, solo queda sin device asociado ni validacion GPS.
        if not device_token:
            return kiosk, None

        user_agent = request.httprequest.headers.get('User-Agent', '')
        kiosk = Kiosk.get_or_create_pending(
            device_token, user_agent=user_agent,
            gps_lat=gps_lat, gps_lng=gps_lng,
        )
        if kiosk and kiosk.state == 'pending':
            return kiosk, {
                'success': False,
                'error': 'kiosk_pending_activation',
                'error_detail': (
                    'Este dispositivo aun no ha sido activado. '
                    'Contacte a un administrador para autorizarlo '
                    'desde Reconocimiento Facial > Kioscos.'
                ),
            }
        if kiosk and kiosk.state == 'revoked':
            return kiosk, {
                'success': False,
                'error': 'kiosk_revoked',
                'error_detail': (
                    'Este dispositivo fue revocado y ya no puede '
                    'registrar asistencia. Contacte a un administrador.'
                ),
            }
        return kiosk, None

    # -- Paso 2: decodificar la imagen -------------------------------------

    @staticmethod
    def _decode_image(image_data):
        """Convierte la imagen recibida en base64 a un array de NumPy RGB."""
        if ',' in image_data:
            image_data = image_data.split(',')[1]
        image_bytes = base64.b64decode(image_data)
        image = Image.open(io.BytesIO(image_bytes)).convert('RGB')
        return np.array(image)

    # -- Paso 3: deteccion de vida por parpadeo ----------------------------

    def _check_liveness(self, kiosk, image_np):
        """
        Verifica deteccion de vida por parpadeo (anti-suplantacion):
        compara el Eye Aspect Ratio (EAR) del intento actual contra el
        guardado en el kiosco del intento anterior. Si ambos existen y
        estan dentro de LIVENESS_WINDOW_SECONDS, un cambio significativo
        entre ellos (>= EAR_BLINK_THRESHOLD) confirma parpadeo real.

        El EAR se guarda en la BASE DE DATOS (no en memoria del
        proceso) para funcionar correctamente sin importar cuantos
        workers tenga el servidor de produccion.

        Retorna None si el parpadeo se confirma (o si no se pudo
        calcular el EAR, para no bloquear el flujo por un caso
        tecnico), o el diccionario de rechazo si aun no se confirma.
        """
        current_ear = _liveness_utils.get_ear_from_image(image_np)
        if current_ear is None:
            return None

        prev_ear = kiosk.liveness_last_ear
        prev_time = kiosk.liveness_last_ear_time
        blink_confirmed = False
        if prev_ear and prev_time:
            seconds_elapsed = (fields.Datetime.now() - prev_time).total_seconds()
            if seconds_elapsed <= LIVENESS_WINDOW_SECONDS:
                if abs(current_ear - prev_ear) >= EAR_BLINK_THRESHOLD:
                    blink_confirmed = True

        if not blink_confirmed:
            # Guardar el EAR actual para comparar en el proximo
            # intento, y rechazar este intento pidiendo que se
            # mantenga frente a la camara un momento mas.
            kiosk.sudo().write({
                'liveness_last_ear': current_ear,
                'liveness_last_ear_time': fields.Datetime.now(),
            })
            return {
                'success': False,
                'error': 'liveness_check',
                'error_detail': (
                    'Verificando... mantenga la mirada en la '
                    'camara un momento.'
                ),
            }

        # Parpadeo confirmado: limpiar el estado para no reusar el
        # mismo parpadeo en un intento futuro no relacionado.
        kiosk.sudo().write({
            'liveness_last_ear': 0.0,
            'liveness_last_ear_time': False,
        })
        return None

    # -- Paso 4: detectar y comparar el rostro -----------------------------

    def _match_employee(self, image_np, face_locations, tolerance,
                         confidence_threshold, FacialLog, device_ip,
                         kiosk, log_on_failure):
        """
        Calcula la codificacion facial del rostro detectado y la
        compara contra todos los empleados con rostro registrado.

        Retorna un dict. Si 'success' es False, es la respuesta final
        que el llamador debe devolver de inmediato. Si es True, incluye
        'employee_id', 'employee_name' y 'confidence' para que
        _register_attendance_and_respond() continue el flujo.
        """
        captured_encodings = face_recognition.face_encodings(image_np, face_locations)
        if not captured_encodings:
            detail = 'No se pudo procesar el rostro detectado.'
            FacialLog.create_failed_log(error_code='encoding_failed',
                                        error_detail=detail, device_ip=device_ip,
                                        kiosk_id=kiosk.id if kiosk else None)
            return {'success': False, 'error': 'encoding_failed', 'error_detail': detail}
        captured_encoding = captured_encodings[0]

        all_encodings = request.env['hr.employee'].sudo().get_all_face_encodings()
        if not all_encodings:
            detail = 'No hay empleados con rostro registrado en el sistema.'
            FacialLog.create_failed_log(error_code='no_employees_registered',
                                        error_detail=detail, device_ip=device_ip,
                                        kiosk_id=kiosk.id if kiosk else None)
            return {'success': False, 'error': 'no_employees_registered', 'error_detail': detail}

        # get_all_face_encodings() ya devuelve cada 'encoding' como
        # np.array (no como lista de Python), asi que ya no hace falta
        # reconstruirlo aqui -- esa conversion ocurre una sola vez
        # dentro del cache, no en cada llamada.
        known_encodings = [e['encoding'] for e in all_encodings]
        distances = face_recognition.face_distance(known_encodings, captured_encoding)

        best_idx = int(np.argmin(distances))
        best_distance = float(distances[best_idx])
        confidence = max(0.0, (1.0 - best_distance) * 100.0)

        if best_distance > tolerance or confidence < confidence_threshold:
            detail = (
                'No se encontro coincidencia. '
                'Asegurese de tener buena iluminacion y mirar directamente a la camara.'
            )
            if log_on_failure:
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
        return {
            'success': True,
            'employee_id': matched['employee_id'],
            'employee_name': matched['employee_name'],
            'confidence': confidence,
        }

    # -- Paso 5: registrar asistencia y armar la respuesta -----------------

    def _register_attendance_and_respond(self, env, FacialLog, match_result,
                                          action_type, auto_action, gps_lat,
                                          gps_lng, kiosk, image_data,
                                          save_images, device_ip):
        """
        Determina la accion (entrada/salida), valida GPS de forma
        complementaria (nunca bloquea la marcacion), crea el registro
        de asistencia, y arma la respuesta final de exito.
        """
        employee_id = match_result['employee_id']
        employee_name = match_result['employee_name']
        confidence = match_result['confidence']

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
