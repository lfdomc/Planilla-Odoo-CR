# -*- coding: utf-8 -*-
"""
Estado GPS en vivo del kiosco.

Separado del reconocimiento facial (facial_recognition.py) porque es
un flujo independiente: el frontend llama esto periodicamente MIENTRAS
la pantalla esta en espera (sin foto, sin intento de reconocer a
nadie), solo para mostrar el borde verde/naranja de "en el lugar
correcto" antes de que alguien intente marcar.
"""
from odoo import http
from odoo.http import request


class FacialKioskStatusController(http.Controller):

    @http.route('/facial_attendance/k/<string:token>/kiosk_status',
                type='json', auth='public', methods=['POST'])
    def kiosk_status_by_token(self, token, device_token=None,
                               gps_lat=None, gps_lng=None, **kwargs):
        """Variante de kiosk_status para el enlace con token por kiosco."""
        Kiosk = request.env['facial.attendance.kiosk'].sudo()
        if not Kiosk.search_count([('access_token', '=', token)]):
            return {'status': 'not_required', 'distance_meters': None}
        return self._do_kiosk_status(device_token, gps_lat, gps_lng)

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

        try:
            lat_f = float(gps_lat) if gps_lat is not None else None
            lng_f = float(gps_lng) if gps_lng is not None else None
        except (TypeError, ValueError):
            lat_f = lng_f = None

        kiosk = Kiosk.search([('device_token', '=', device_token)], limit=1)
        if not kiosk or kiosk.state != 'active':
            # FIX: capturar el GPS desde el primer contacto, aunque el
            # dispositivo siga pendiente de activacion -- asi el
            # administrador ya ve la ubicacion real al revisar el
            # dispositivo pendiente, en vez de tener que activarlo
            # primero y esperar otra visita para que se reporte el GPS.
            # get_or_create_pending() ya maneja tanto crear el registro
            # nuevo como actualizar uno existente que aun no tenga
            # ubicacion capturada.
            if lat_f and lng_f:
                Kiosk.get_or_create_pending(
                    device_token, gps_lat=lat_f, gps_lng=lng_f,
                )
            # El chequeo de posicion "en vivo" (contra el radio permitido)
            # no aplica todavia -- eso lo maneja el flujo normal de
            # reconocimiento (recognize_face), que si informa claramente
            # "dispositivo no activado".
            return {'status': 'not_required', 'distance_meters': None}

        return kiosk.check_live_position(lat_f, lng_f)
