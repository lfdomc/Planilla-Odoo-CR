# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    facial_recognition_tolerance = fields.Float(
        string='Tolerancia de Reconocimiento Facial',
        default=0.55,
        config_parameter='facial_attendance.tolerance',
        digits=(3, 2),
        help='Distancia maxima para considerar un match valido (0.4=estricto, 0.6=tolerante)',
    )
    facial_confidence_threshold = fields.Float(
        string='Umbral Minimo de Confianza (%)',
        default=60.0,
        config_parameter='facial_attendance.confidence_threshold',
        digits=(5, 2),
    )
    facial_liveness_enabled = fields.Boolean(
        string='Deteccion de vida (anti-suplantacion)',
        default=False,
        config_parameter='facial_attendance.liveness_enabled',
        help='Exige confirmar un parpadeo real antes de aceptar el '
             'reconocimiento, para evitar que alguien marque asistencia '
             'de otra persona mostrando una foto (impresa o en '
             'pantalla) a la camara en vez de su propio rostro -- '
             'tecnica conocida en la industria como "buddy punching". '
             'Añade 1-2 intentos adicionales al proceso normal (la '
             'persona ya esta parada frente a la camara varios '
             'segundos de forma natural; el sistema aprovecha ese '
             'tiempo para confirmar el parpadeo sin pedir nada '
             'especial). Desactivado por defecto -- activelo si sus '
             'kioscos estan en zonas sin supervision donde suplantar '
             'la identidad de otro empleado sea un riesgo real.',
    )
    facial_save_images = fields.Boolean(
        string='Guardar imagenes capturadas',
        default=False,
        config_parameter='facial_attendance.save_images',
        help='Si esta activo, CADA intento de reconocimiento (exitoso o '
             'fallido) guarda la foto capturada como adjunto permanente. '
             'Con el tiempo esto acumula miles de imagenes en el '
             'servidor sin ningun mecanismo de limpieza automatica. '
             'Se recomienda dejarlo desactivado (por defecto) y '
             'activarlo solo temporalmente si necesita revisar por que '
             'un dispositivo no esta reconociendo bien a los empleados.',
    )
    facial_auto_action = fields.Boolean(
        string='Accion automatica (entrada/salida)',
        default=True,
        config_parameter='facial_attendance.auto_action',
        help='Determina automaticamente si es entrada o salida segun estado del empleado',
    )
    facial_recognition_model = fields.Selection([
        ('hog', 'HOG (Rapido, CPU)'),
        ('cnn', 'CNN (Preciso, requiere GPU)'),
    ], string='Modelo de Deteccion',
        default='hog',
        config_parameter='facial_attendance.recognition_model',
        help='Se usa tanto para el reconocimiento en el quiosco como para '
             'el registro inicial del rostro del empleado.',
    )
    facial_enable_public_kiosk = fields.Boolean(
        string='Habilitar quiosco publico (sin login)',
        default=False,
        config_parameter='facial_attendance.enable_public_kiosk',
        help='Permite usar /facial_attendance/kiosk/public sin iniciar sesion, '
             'pensado para una tablet dedicada en la entrada. '
             'Activelo solo si el dispositivo esta en una red controlada, '
             'ya que expone el reconocimiento facial sin autenticacion.',
    )
    facial_public_kiosk_url = fields.Char(
        string='Enlace del Quiosco Publico',
        compute='_compute_facial_public_kiosk_url',
        help='URL completa del quiosco publico, lista para copiar y abrir '
             'directamente en el dispositivo dedicado (tablet, celular).',
    )

    @api.depends('facial_enable_public_kiosk')
    def _compute_facial_public_kiosk_url(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        for rec in self:
            rec.facial_public_kiosk_url = (
                f'{base_url}/facial_attendance/kiosk/public' if base_url else False
            )
    facial_replace_native_kiosk = fields.Boolean(
        string='Usar reconocimiento facial como quiosco principal',
        default=False,
        config_parameter='facial_attendance.replace_native_kiosk',
        help='(Reservado para uso futuro, sin efecto activo actualmente.) '
             'La forma recomendada de usar el reconocimiento facial como '
             'quiosco principal es crear un kiosco propio en '
             'Reconocimiento Facial > Kioscos y usar su enlace directo '
             'en el dispositivo, en vez de compartir el enlace del Modo '
             'Quiosco nativo de Asistencias.',
    )

    def action_sync_branches_from_planilla(self):
        """
        Delega a facial.attendance.branch.action_sync_from_planilla().
        Se expone aqui (en vez de solo en la lista de Sucursales) para
        que el boton de sincronizacion siempre este visible en Ajustes,
        sin importar si la lista de sucursales ya tiene registros --
        cuando esta vacia, Odoo muestra el mensaje de bienvenida
        ("Cree una sucursal...") en vez del header con los botones de
        la lista, dejando ese boton inaccesible la primera vez que se
        usa el modulo.
        """
        return self.env['facial.attendance.branch'].action_sync_from_planilla()
