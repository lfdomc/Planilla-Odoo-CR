# -*- coding: utf-8 -*-
"""
Paginas HTML del kiosco de reconocimiento facial.

Separado de la logica de reconocimiento (facial_recognition.py) y del
estado GPS (facial_kiosk_status.py) para que cada archivo tenga una
responsabilidad clara: este solo sirve las 3 variantes de la pagina
del kiosco (backend logueado, publico sin login, y por token
individual), sin ninguna logica de procesamiento de imagenes.
"""
from odoo import http
from odoo.http import request


class FacialKioskPageController(http.Controller):

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
