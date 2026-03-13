{
    'name': 'Sistema Planilla v5.4',
    'version': '19.0.5.4',
    'category': 'Human Resources/Payroll',
    'summary': 'Sistema de Planilla Costa Rica v5.4 — Legislación CR 2026',
    # ── Changelog v54 ──────────────────────────────────────────────────────────
    # NEW-01: account_pension_alimentaria_payable (campo correcto en per_run).
    # NEW-02: income_tax_on_termination en liquidaciones (Art. 35 Ley ISR).
    # NEW-03: help text employer_cost en incapacidad INS (valor 0 es correcto).
    # NEW-04: 5 nuevos tests — renta quincenal, pension+embargo, HE feriado,
    #         liquidacion con prestamo, provisiones 17.82%.
    # NEW-05: avg_last_4_weeks computed desde salary_history (Art. 153 CT).
    # NEW-06: _compute_totals excluye boletas canceladas (state != cancelled).
    # NEW-07: EDDI-7 v4.x — validacion longitud/ASCII, _sanitize_ascii para SICERE.
    # NEW-08: salary_increase_wizard crea salary_history con gross_salary+authorized.
    # ──────────────────────────────────────────────────────────────────────────
    'description': """
        Módulo completo de gestión de planillas para Costa Rica.
        Incluye:
        - Gestión de empleados con tipos, estados y puestos
        - Códigos de deducción (CCSS, INS, renta, etc.)
        - Calendarizaciones de pago (semanal, quincenal, mensual)
        - Horas extras, incapacidades y vacaciones
        - Boletas de pago con envío automático por correo
        - Historial de salarios por colaborador
        - Soporte para múltiples sucursales
        - Integración contable completa (por empleado o por planilla)
        - Dashboard con métricas del mes
        - Reportes PDF: Resumen Mensual, CCSS, Costo por Sucursal, Detalle Empleado
    """,
    'author': 'Planilla CR',
    'website': '',
    'license': 'LGPL-3',
    'depends': [
        'hr',
        'hr_attendance',
        'hr_holidays',
        'hr_recruitment',
        'hr_expense',
        'account',
        'mail',
    ],
    'data': [
        # Security - grupos primero
        'security/security.xml',
        'security/ir.model.access.csv',
        'security/record_rules.xml',
        # Data
        'data/identification_type_data.xml',
        'data/income_tax_data.xml',
        'data/minimum_salary_data.xml',
        'data/deduction_code_data.xml',
        'data/default_data.xml',
        # Views - Configuración
        'views/income_tax_bracket_views.xml',
        'views/minimum_salary_views.xml',
        'views/employee_loan_views.xml',
        'views/branch_views.xml',
        'views/identification_type_views.xml',
        'views/employee_status_views.xml',
        'views/employee_type_views.xml',
        'views/deduction_code_views.xml',
        'views/schedule_type_views.xml',
        'views/payroll_calendar_views.xml',
        'views/accounting_config_views.xml',
        'views/closed_period_views.xml',
        # Views - Empleados
        'views/hr_employee_extension_views.xml',
        # Views - Planilla
        'views/overtime_views.xml',
        'views/disability_views.xml',
        'views/vacation_payment_views.xml',
        'views/pension_alimentaria_views.xml',
        'views/termination_views.xml',
        'views/payslip_cr_views.xml',
        'views/payroll_run_cr_views.xml',
        # Views - Historial y Reportes
        'views/salary_history_views.xml',
        'views/dashboard_report_views.xml',
        # Reports
        'report/termination_report.xml',
        'report/loan_report.xml',
        'report/vacation_balance_report.xml',
        'report/employer_cost_report.xml',
        'report/overtime_consolidated_report.xml',
        'report/payslip_report.xml',
        'report/salary_history_report.xml',
        'report/payroll_reports.xml',
        'report/ins_report.xml',
        'report/ccss_report.xml',
        'views/ins_report_views.xml',
        'views/bank_payment_views.xml',
        'views/ccss_report_views.xml',
        # Wizards
        'wizard/send_payslip_wizard_views.xml',
        'wizard/salary_increase_wizard_views.xml',
        'views/import_overtime_wizard_views.xml',
        'views/public_holiday_views.xml',
        'wizard/vacation_balance_wizard_views.xml',
        'wizard/employer_cost_wizard_views.xml',
        'wizard/wizard_views_v24.xml',
        'wizard/aguinaldo_wizard_views.xml',
        # Data con referencias a modelos externos (cargar al final, modelos ya cargados)
        'data/cron_jobs.xml',
        'data/email_templates.xml',
        'data/public_holidays_cr.xml',
        # EDDI-7 CCSS
        'views/eddi7_export_views.xml',
        # Menus (siempre al final)
        'views/menu_views.xml',
    ],
    # FIX BUG-N03 v52: campo 'test' eliminado — Odoo 19 lo ignora completamente.
    # Para ejecutar tests usar:
    #   docker compose exec web odoo -d prueba --test-enable \
    #     --test-tags planilla_cr --stop-after-init
    'installable': True,
    'application': True,
    'auto_install': False,
    'images': ['static/description/icon.png'],
    'post_init_hook': 'post_init_hook',
    'post_migrate_hook': 'post_migrate_hook',
}
