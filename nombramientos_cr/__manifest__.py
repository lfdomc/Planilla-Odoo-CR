{
    'name': 'Nombramientos CR',
    'version': '19.0.1.0.64',
    'summary': 'Módulo de Nombramientos Semanales y Movilidad entre Sucursales',
    'description': """
        Gestión de nombramientos semanales de empleados con soporte para:
        - Asignación de empleados a sucursales/locales por semana
        - Registro de horas trabajadas por nombramiento
        - Pago por hora con tarifas configurables
        - Integración con módulo planilla_cr para generar planilla
        - Ideal para: seguridad, salud, construcción, servicios
    """,
    'author': 'Mundopet / planilla_cr',
    'depends': ['hr', 'planilla_cr'],
    'assets': {
        'web.assets_backend': [
            'nombramientos_cr/static/src/js/calendario_template.xml',
            'nombramientos_cr/static/src/js/calendario_action.js',
        ],
    },
    'data': [
        'data/sequences.xml',
        'data/shift_templates_data.xml',
        # Views primero (sin referencias a actions wizard)
        'views/config_views.xml',
        'views/sede_turno_views.xml',
        'views/nombramiento_views.xml',
        'views/nombramiento_turno_views.xml',
        # Wizards ANTES del menú (el menú referencia sus actions)
        'wizard/generar_planilla_wizard_views.xml',
        'wizard/calendario_wizard_views.xml',
        # Menú al final (después de que todos los actions estén definidos)
        'views/calendario_html_views.xml',
        'views/menu_views.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
    'post_init_hook': 'post_init_hook',
}
