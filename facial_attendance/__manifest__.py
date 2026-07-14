# -*- coding: ascii -*-
{
    'name': 'Reconocimiento Facial - Asistencias',
    'version': '19.0.1.0.1',
    'category': 'Human Resources/Attendances',
    'summary': 'Registro de asistencias mediante reconocimiento facial',
    'description': """
        Modulo de reconocimiento facial integrado con hr.attendance de Odoo 19.
        Permite registrar entradas y salidas de empleados mediante camara web.

        Caracteristicas:
        - Captura y registro de rostros de empleados
        - Reconocimiento facial en tiempo real
        - Integracion automatica con hr.attendance
        - Panel de quiosco con camara web
        - Historial de reconocimientos
        - Soporte multi-empleado simultaneo
    """,
    'author': 'Modulo Odoo 19',
    'depends': [
        'hr',
        'hr_attendance',
        'web',
        'mail',
    ],
    'data': [
        'security/facial_attendance_security.xml',
        'security/ir.model.access.csv',
        'data/facial_attendance_data.xml',
        'views/hr_employee_views.xml',
        'views/facial_attendance_log_views.xml',
        'views/facial_attendance_kiosk_views.xml',
        'views/menu_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'facial_attendance/static/src/css/facial_attendance.css',
            'facial_attendance/static/src/js/face_api_loader.js',
            'facial_attendance/static/src/js/facial_attendance.js',
            'facial_attendance/static/src/xml/facial_attendance.xml',
        ],
    },
    'images': ['static/description/icon.png'],
    'license': 'LGPL-3',
    'installable': True,
    'application': False,
    'auto_install': False,
}
