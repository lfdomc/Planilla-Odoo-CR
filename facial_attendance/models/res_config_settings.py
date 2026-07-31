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
    facial_save_images = fields.Boolean(
        string='Guardar imagenes capturadas',
        default=True,
        config_parameter='facial_attendance.save_images',
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
