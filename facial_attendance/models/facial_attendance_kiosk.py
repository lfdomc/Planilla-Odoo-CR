# -*- coding: utf-8 -*-
import logging
import math
import secrets

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class FacialAttendanceKiosk(models.Model):
    """Dispositivo (tablet/celular/computadora) autorizado para marcar
    asistencia por reconocimiento facial.

    El vinculo dispositivo <-> kiosco NO se basa en MAC address (no es
    accesible desde el navegador por diseno de seguridad de los browsers)
    ni en IP fija (poco confiable: IP dinamica, redes compartidas, VPN).
    En su lugar, el navegador genera un token aleatorio la primera vez
    que abre el kiosco y lo persiste en localStorage. Un administrador
    aprueba ese token UNA vez y le asigna nombre + sede. De ahi en
    adelante, todas las marcaciones desde ese dispositivo quedan
    automaticamente asociadas a ese kiosco, sin que el usuario final
    tenga que hacer nada.
    """
    _name = 'facial.attendance.kiosk'
    _description = 'Kiosco de Reconocimiento Facial'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name'

    name = fields.Char(
        string='Nombre del Kiosco', required=True, tracking=True,
        help='Ej: "Bodega", "Obra Curridabat Fase 2", "Entrada Principal Escazu".',
    )
    device_token = fields.Char(
        string='Token del Dispositivo', required=True, copy=False, index=True,
        readonly=True,
        help='Identificador unico generado automaticamente por el navegador '
             'la primera vez que se abre el kiosco en ese dispositivo. '
             'No editable manualmente. Distinto del Token de Acceso: este '
             'identifica AL DISPOSITIVO (para saber que marcaciones vienen '
             'de donde); el Token de Acceso identifica AL LINK (para poder '
             'abrir la pantalla del kiosco sin iniciar sesion en Odoo).',
    )
    access_token = fields.Char(
        string='Token de Acceso', required=True, copy=False, index=True,
        readonly=True,
        help='Token del link publico de este kiosco '
             '(/facial_attendance/k/<token>). Funciona igual que la URL '
             'de Kiosco de Asistencia que genera el modulo nativo de '
             'Odoo: abre directo la pantalla de camara, sin pedir inicio '
             'de sesion ni mostrar ningun menu de Odoo alrededor. Puede '
             'regenerarse con el boton "Regenerar Enlace" si se '
             'compromete o se quiere invalidar el anterior.',
    )
    kiosk_url = fields.Char(
        string='Enlace del Kiosco', compute='_compute_kiosk_url',
        help='Enlace directo y unico de este kiosco. Abralo en el '
             'dispositivo dedicado (tablet, celular) y guardelo como '
             'favorito o en la pantalla de inicio -- no requiere iniciar '
             'sesion en Odoo ni muestra ningun menu, solo la camara.',
    )
    state = fields.Selection([
        ('pending', 'Pendiente de Activacion'),
        ('active', 'Activo'),
        ('revoked', 'Revocado'),
    ], string='Estado', default='pending', required=True, tracking=True,
       help='Pendiente: el dispositivo se conecto por primera vez pero '
            'ningun administrador lo ha aprobado -- no puede marcar '
            'asistencia todavia. Activo: autorizado para marcar. '
            'Revocado: el dispositivo perdio autorizacion (ej. se perdio '
            'el celular, cambio de responsable).')

    company_id = fields.Many2one(
        'res.company', string='Compania', required=True,
        default=lambda self: self.env.company,
    )

    # -- Sede asociada (integracion opcional con planilla_cr) --------------
    # NO se declara un Many2one('planilla.branch', ...) clasico: ese modelo
    # solo existe si planilla_cr esta instalado, y facial_attendance no
    # depende de planilla_cr (ni al reves). Declarar un Many2one a un
    # modelo que podria no existir rompe la carga del modulo para
    # cualquier cliente que no tenga planilla_cr instalado.
    #
    # En su lugar: branch_res_id guarda el ID numerico de la sede de forma
    # generica (sin FK real), y branch_display_name se resuelve en
    # tiempo de ejecucion consultando 'planilla.branch' solo si ese
    # modelo existe en el registro (self.env.get(...) -- mismo patron ya
    # usado en planilla_cr/models/mixins/payslip_auto_overtime_mixin.py
    # para leer nombramientos.turno de forma segura).
    branch_res_id = fields.Integer(
        string='ID Sucursal (Planilla)',
        help='ID interno de la sucursal de planilla_cr asociada a este '
             'kiosco. Se gestiona desde el selector "Sucursal (Planilla)" '
             'en el formulario -- no editar este numero directamente.',
    )
    branch_display_name = fields.Char(
        string='Sucursal (Planilla)', compute='_compute_branch_display_name',
        help='Nombre de la sucursal de planilla_cr vinculada. Solo '
             'disponible si el modulo Planilla CR esta instalado.',
    )
    branch_name = fields.Char(
        string='Sede (texto libre)',
        help='Nombre de la sede si el modulo Nombramientos/Planilla no '
             'esta instalado, o si prefiere no vincular una sucursal '
             'formal (ej. obra temporal sin sucursal creada todavia). '
             'Si hay una Sucursal (Planilla) vinculada, ese nombre tiene '
             'prioridad para mostrar en reportes.',
    )

    def _compute_branch_display_name(self):
        Branch = self.env.get('planilla.branch')
        for rec in self:
            if Branch is not None and rec.branch_res_id:
                branch = Branch.sudo().browse(rec.branch_res_id)
                rec.branch_display_name = branch.name if branch.exists() else False
            else:
                rec.branch_display_name = False

    device_info = fields.Char(
        string='Info del Dispositivo',
        readonly=True,
        help='User-agent del navegador capturado en la primera conexion, '
             'util para identificar el dispositivo fisico (ej. modelo de '
             'tablet o telefono).',
    )
    first_seen = fields.Datetime(
        string='Primera Conexion', readonly=True,
    )
    last_seen = fields.Datetime(
        string='Ultima Marcacion', readonly=True,
    )
    activated_by = fields.Many2one(
        'res.users', string='Activado Por', readonly=True,
    )
    activation_date = fields.Datetime(
        string='Fecha de Activacion', readonly=True,
    )

    # -- GPS complementario --------------------------------------------------
    # El GPS es complementario y OPCIONAL por kiosco: no todos los
    # dispositivos lo necesitan (ej. una tablet fija en bodega no
    # requiere validar ubicacion, mientras que el celular del jefe de
    # obra si). require_gps controla esto por kiosco individualmente.
    require_gps = fields.Boolean(
        string='Requerir GPS',
        default=False,
        help='ON = este kiosco valida la ubicacion GPS del dispositivo, '
             'tanto en vivo (mientras esta en pantalla de espera) como al '
             'marcar asistencia. Si la marcacion ocurre fuera del radio '
             'permitido, la asistencia se acepta igual (nunca se bloquea '
             'al empleado) pero queda marcada como "fuera de area" para '
             'revision del supervisor. OFF = este kiosco no usa GPS en '
             'absoluto (comportamiento por defecto).',
    )
    gps_radius_meters = fields.Integer(
        string='Radio Permitido (metros)',
        default=250,
        help='Distancia maxima aceptable entre la ubicacion GPS actual y '
             'el punto de referencia del kiosco, antes de considerarlo '
             'fuera de zona. Por defecto 250 metros.',
    )
    kiosk_latitude = fields.Float(
        string='Latitud del Kiosco', digits=(10, 7), readonly=True,
        help='Ubicacion de referencia del kiosco. Se captura '
             'automaticamente desde el propio dispositivo la primera vez '
             'que se activa con GPS -- no se digita manualmente. Si el '
             'kiosco esta vinculado a una sucursal de Planilla con '
             'coordenadas propias, esas tienen prioridad sobre esta.',
    )
    kiosk_longitude = fields.Float(
        string='Longitud del Kiosco', digits=(10, 7), readonly=True,
    )
    kiosk_location_set = fields.Boolean(
        string='Ubicacion Capturada', default=False, readonly=True,
        help='Indica si el dispositivo ya envio su ubicacion GPS de '
             'referencia. Se marca automaticamente la primera vez que el '
             'kiosco reporta su posicion con require_gps activo.',
    )

    log_ids = fields.One2many(
        'facial.attendance.log', 'kiosk_id', string='Marcaciones',
    )
    log_count = fields.Integer(
        string='Total Marcaciones', compute='_compute_log_count',
    )
    out_of_range_count = fields.Integer(
        string='Fuera de Area (30 dias)', compute='_compute_log_count',
        help='Marcaciones fuera del radio GPS permitido en los ultimos 30 dias.',
    )

    def _compute_log_count(self):
        from datetime import timedelta
        Log = self.env['facial.attendance.log']
        cutoff = fields.Datetime.now() - timedelta(days=30)
        for rec in self:
            rec.log_count = Log.search_count([('kiosk_id', '=', rec.id)])
            rec.out_of_range_count = Log.search_count([
                ('kiosk_id', '=', rec.id),
                ('out_of_range', '=', True),
                ('recognition_date', '>=', cutoff),
            ])

    def _compute_kiosk_url(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        for rec in self:
            rec.kiosk_url = (
                f'{base_url}/facial_attendance/k/{rec.access_token}'
                if rec.access_token else False
            )

    _sql_constraints = [
        ('unique_device_token', 'unique(device_token)',
         'Ya existe un kiosco registrado con este token de dispositivo.'),
        ('unique_access_token', 'unique(access_token)',
         'Colision de token de acceso -- intente de nuevo.'),
    ]

    @api.model
    def _generate_token(self):
        return secrets.token_urlsafe(32)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('access_token'):
                vals['access_token'] = self._generate_token()
        return super().create(vals_list)

    def action_regenerate_access_token(self):
        """Invalida el enlace actual del kiosco y genera uno nuevo -- igual
        que 'Generar nueva URL' en el kiosco nativo de Asistencias. Util
        si el enlace se comparte por error o el dispositivo se pierde.
        No afecta el device_token (identidad del dispositivo aprobado);
        solo cambia el enlace de acceso a la pantalla del kiosco.
        """
        for rec in self:
            rec.write({'access_token': self._generate_token()})

    @api.model
    def get_or_create_pending(self, device_token, user_agent=None):
        """Punto de entrada desde el controlador: dado el token que envia
        el navegador, retorna el kiosco correspondiente. Si el token no
        existe todavia (primera conexion de este dispositivo), lo crea
        en estado 'pending' automaticamente -- sin autorizar nada, solo
        para que aparezca en la lista de dispositivos por aprobar.
        """
        if not device_token:
            return self.browse()
        kiosk = self.sudo().search([('device_token', '=', device_token)], limit=1)
        if kiosk:
            kiosk.write({'last_seen': fields.Datetime.now()})
            return kiosk
        kiosk = self.sudo().create({
            'name': _('Dispositivo sin nombre'),
            'device_token': device_token,
            'device_info': (user_agent or '')[:250],
            'first_seen': fields.Datetime.now(),
            'last_seen': fields.Datetime.now(),
            'state': 'pending',
        })
        _logger.info(
            'facial_attendance: nuevo dispositivo pendiente de activacion, token=%s...',
            device_token[:12],
        )
        return kiosk

    def action_activate(self):
        for rec in self:
            if not rec.name or rec.name == _('Dispositivo sin nombre'):
                raise ValidationError(_(
                    'Asigne un nombre descriptivo al kiosco antes de activarlo '
                    '(ej. "Bodega", "Obra Curridabat").'
                ))
            rec.write({
                'state': 'active',
                'activated_by': self.env.user.id,
                'activation_date': fields.Datetime.now(),
            })

    def action_revoke(self):
        self.write({'state': 'revoked'})

    def action_reset_to_pending(self):
        self.write({'state': 'pending', 'activated_by': False, 'activation_date': False})

    def action_recapture_location(self):
        """Permite al administrador pedir que el kiosco vuelva a enviar
        su ubicacion la proxima vez que se abra (ej. si el dispositivo se
        reubico fisicamente). No borra el historial, solo desmarca
        kiosk_location_set para que la proxima conexion con GPS
        sobreescriba las coordenadas actuales.
        """
        self.write({'kiosk_location_set': False})

    def set_kiosk_location_from_device(self, lat, lng):
        """Guarda la ubicacion GPS reportada por el propio dispositivo
        como el punto de referencia del kiosco. Se llama automaticamente
        UNA vez (la primera conexion con GPS disponible tras activarse o
        tras un recaptura solicitada) -- no se sobreescribe en cada
        marcacion, para que el punto de referencia sea estable y no
        "camine" con pequenas variaciones de precision del GPS.

        Esto es lo que reemplaza la digitacion manual de coordenadas: el
        kiosco, al llegar al lugar de uso (ej. la obra), reporta donde
        esta y esa se convierte en la referencia contra la que se miden
        todas las marcaciones futuras.
        """
        self.ensure_one()
        if self.kiosk_location_set:
            return False
        if lat is None or lng is None:
            return False
        self.write({
            'kiosk_latitude': lat,
            'kiosk_longitude': lng,
            'kiosk_location_set': True,
        })
        _logger.info(
            'facial_attendance: kiosco "%s" (id=%s) capturo su ubicacion '
            'de referencia: %.7f, %.7f', self.name, self.id, lat, lng,
        )
        return True

    def get_reference_coordinates(self):
        """Retorna (lat, lng, origen) a usar como referencia para validar
        GPS: prioriza las coordenadas de la sede vinculada (si planilla_cr
        esta instalado y la sede tiene coordenadas), y usa las
        coordenadas propias del kiosco (capturadas automaticamente en la
        activacion) como respaldo.
        """
        self.ensure_one()
        Branch = self.env.get('planilla.branch')
        if Branch is not None and self.branch_res_id:
            branch = Branch.sudo().browse(self.branch_res_id)
            if branch.exists() and branch.latitude and branch.longitude:
                return branch.latitude, branch.longitude, 'sede'
        if self.kiosk_latitude and self.kiosk_longitude:
            return self.kiosk_latitude, self.kiosk_longitude, 'kiosco'
        return None, None, None

    @staticmethod
    def _haversine_meters(lat1, lon1, lat2, lon2):
        """Distancia en metros entre dos coordenadas GPS (formula haversine)."""
        R = 6371000.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = (math.sin(dphi / 2) ** 2
             + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    def check_gps_in_range(self, lat, lng):
        """Retorna (dentro_de_rango: bool, distancia_metros: float o None,
        motivo: str). Si el kiosco no requiere GPS, o no hay coordenadas
        de referencia configuradas, retorna (True, None, 'sin_validacion')
        -- nunca bloquea por falta de configuracion.
        """
        self.ensure_one()
        if not self.require_gps:
            return True, None, 'gps_no_requerido'
        if lat is None or lng is None:
            return False, None, 'sin_gps_del_dispositivo'
        ref_lat, ref_lng, origen = self.get_reference_coordinates()
        if ref_lat is None:
            return True, None, 'sin_coordenadas_referencia'
        distance = self._haversine_meters(ref_lat, ref_lng, lat, lng)
        in_range = distance <= (self.gps_radius_meters or 250)
        return in_range, round(distance, 1), origen

    def check_live_position(self, lat, lng):
        """Chequeo de posicion EN VIVO del kiosco, independiente de
        cualquier marcacion de asistencia. Se llama periodicamente desde
        el frontend mientras el kiosco esta en pantalla de espera, para
        mostrar el borde verde/naranja de "en el lugar correcto" antes de
        que nadie intente marcar.

        Si el kiosco aun no tiene ubicacion de referencia capturada
        (kiosk_location_set=False) y llega una posicion valida, la
        captura automaticamente -- asi el simple hecho de encender el
        kiosco por primera vez en el sitio de trabajo establece la
        referencia, sin pasos manuales adicionales.

        Retorna un dict listo para el frontend:
          {status: 'ok'|'out_of_range'|'no_gps'|'not_required',
           distance_meters: float|None}
        """
        self.ensure_one()
        if not self.require_gps:
            return {'status': 'not_required', 'distance_meters': None}

        if lat is None or lng is None:
            return {'status': 'no_gps', 'distance_meters': None}

        if not self.kiosk_location_set:
            captured = self.set_kiosk_location_from_device(lat, lng)
            if captured:
                return {'status': 'ok', 'distance_meters': 0.0}

        in_range, distance, _origen = self.check_gps_in_range(lat, lng)
        return {
            'status': 'ok' if in_range else 'out_of_range',
            'distance_meters': distance,
        }
