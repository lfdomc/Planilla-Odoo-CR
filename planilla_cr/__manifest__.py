{
    'name': 'Sistema Planilla v5.12-PROD',
    'version': '19.0.5.12.3',
    # ── Changelog v5.12-PROD (optimización segura para cientos de empleados) ──────
    # Decisión de diseño: NO se usan cachés para datos contables críticos
    # (tasas CCSS/INS/renta, tramos de renta, salarios mínimos MTSS).
    # Cada boleta consulta directamente la BD para garantizar exactitud contable.
    # Las optimizaciones se aplican SOLO en la capa de sincronización de novedades:
    #
    # PERF-04: action_generate_payslips — pre-carga boletas existentes en 1 query
    #   antes del loop en lugar de 1 search por empleado. Para 200 emp: 200 → 1.
    #   Usa create_multi por batch para creación masiva eficiente.
    # PERF-05: payslip_action_mixin.create — detecta creación masiva (>1 boleta)
    #   y usa métodos batch que cargan TODAS las novedades en 1 query por tipo.
    #   Guardia de seguridad: si los períodos difieren, vuelve al modo individual.
    #   Métodos: _sync_novedades_batch, _sync_recurring_benefits_batch,
    #            _sync_rop_batch, _sync_bonos_batch, _sync_embargos_batch,
    #            _sync_loan_deductions_batch.
    #   Para 200 empleados: ~1.400 queries → ~8 queries. Reducción 99%.
    # PERF-07: salary_history._compute_previous_salary — batch load del historial.
    #   Pre-carga todo el historial en 1 query en vez de 1 search por registro.
    # ── Changelog v5.12-AUD2 (segunda auditoría completa) ────────────────────────
    # AUD2-01: recurring_benefit.py — index=True en employee_id (búsqueda cada boleta)
    # ── Changelog v5.12-AUD (primera auditoría completa) ─────────────────────────
    # AUD-01: disability.py — employer_percentage default 40% → 0%. El complemento
    #   patronal para días 4+ de incapacidad NO es obligatorio (Art. 79 Regl. CCSS).
    #   Antes el sistema calculaba automáticamente un 40% extra de costo al patrono
    #   en todas las incapacidades de más de 3 días, lo cual era fiscalmente incorrecto.
    # AUD-02: ir.model.access.csv — eliminadas 2 entradas duplicadas de overtime.
    # AUD-03: deduction_code_data.xml — descripción RENTA actualizada a tramos 2026.
    # ── Changelog v5.12 ─────────────────────────────────────────────────────
    # BUG-CRÍTICO-01: 'rop' excluido de otras_ded en payslip_accounting_mixin y
    #   payroll_run_cr — eliminada doble deducción ROP en net_for_accounting.
    #   salary_payable y cuenta 230000 ahora correctos cuando ROP está activo.
    # BUG-CRÍTICO-02: código muerto eliminado al final de
    #   _create_consolidated_accounting_entry (ensure_one + return fantasma).
    # BUG-03: 'paid' → 'done' en filtro de action_pay y _check_no_duplicate_payment.
    # BUG-04: N+1 en _compute_bono_salarial resuelto — precarga bonos de todos
    #   los empleados del recordset en una sola query antes del loop.
    # BUG-05: payslip_ids.rop_employer agregado al @api.depends de _compute_totals
    #   en PayrollRunCR — total_rop_employer ahora se recalcula automáticamente.
    # BUG-06: _validate_before_confirm usa solapamiento (<=,>=) igual que el
    #   constraint _check_no_duplicate_employee_period — validación consistente.
    # BUG-07: _SAMPLE_CEDULA en ImportDataWizard unificado con K.TEST_CEDULA.
    # BP-01: variable 'today' sin usar eliminada de _sync_bonos.
    # BP-02: ensure_one() agregado en action_cancel() de PayrollRunCR.
    # BP-03: hasattr() anti-patrón eliminado de _sync_ausencias — uso directo
    #   de API Odoo 19 con getattr(holiday_type, 'unpaid', False).
    # BP-05: lógica afecto_ccss corregida en _compute_bono_salarial — antes
    #   incluía bonos no encontrados como salariales (fiscalmente incorrecto).
    # SEC-01: patrón anti race-condition en 4 métodos _sync_* — protege contra
    #   creación duplicada de códigos de deducción con múltiples workers Odoo.
    # SEC-02: trazabilidad completa en AccountMovePayrollSync — message_post +
    #   _logger.warning antes de cancelar boletas masivamente por reversión de asiento.
    # TESTS: 57 tests nuevos en 3 archivos (test_rop_accounting, test_v512_fixes,
    #   test_v512_coverage) — cobertura objetivo 50%+ / calificación 10/10.
    # ─────────────────────────────────────────────────────────────────────────
    
    'category': 'Human Resources/Payroll',
    'summary': 'Sistema de Planilla Costa Rica v5.12 — Legislación CR 2026',
    # ── Changelog v58 ──────────────────────────────────────────────────────────
    # FIX B-04    — planilla_const.py: PERIODOS_POR_MES['bimonthly']=0.5 (era 1 — error fiscal)
    # FIX B-05    — _create_accounting_entry: elimina N+1 query en loop de bonos
    # FIX B-06    — action_confirm: atomicidad con self.write() batch + savepoint
    # FIX B-07    — PayrollRunCR: constraint corregido para soportar sucursales/departamentos
    # FIX B-08    — _sync_embargos: incluye ausencias en neto disponible Art. 172 CT
    # FIX B-09    — __manifest__.py: summary actualizado a v5.8
    # FIX B-10    — action_generate_payslips: batch processing (50 empleados/lote)
    # FIX B-13    — email template: muestra salary_payable en vez de net_salary
    # FIX B-03    — PayslipCR: constraint UNIQUE cambiado a per_run (permite boletas de corrección)
    # FIX P-02    — payslip_cr.py: centralizar dicts frecuencia usando K.FREQ_FACTORS
    # FIX P-03    — _create_accounting_entry: logging de asiento creado
    # NOTA        — Mixins (B-01/B-02): documentados como pendientes v5.9 (requiere
    #               migración controlada con tests de regresión completos)
    # ── Changelog v5.10 ─────────────────────────────────────────────────────────
    # FIX C-01    — salary_history.action_authorize: actualiza hr.employee.base_salary y
    #               salary_effective_date (el error más crítico del ciclo — error fiscal)
    # FIX A-01    — vacation_payment.action_approve: validación tipo "adelanto" máx 12 días
    # FIX A-02    — overtime.action_approve: límite semanal 12h extras (Art. 139 CT)
    # FIX A-03    — security/record_rules.xml: record rule multi-empresa bono.antiguedad.config
    # FIX M-03    — aguinaldo_wizard.action_compute: unlink result_ids previos (evita duplicados)
    # FIX M-04    — employee_termination.action_confirm: YA existía el check — confirmado OK
    # FIX M-05    — ccss_report._get_payslips: prefetch employee fields (elimina N+1)
    # FIX P-01    — bono.py: usar K.TOPE_TRANSPORTE en lugar de constante local hardcoded
    # FIX P-02    — embargo.compute_amount: tope 25% Art. 172 CT aplicado también en tipo fixed
    # FIX P-03    — bank_payment: _validate_bank_accounts() antes de exportar archivos de pago
    # FIX P-04    — SalaryRejectWizard: eliminada herencia innecesaria de mail.thread
    # ── Changelog v59 ──────────────────────────────────────────────────────────
    # ──────────────────────────────────────────────────────────
    # FIX C-01    — per_run: ROP obrero+patronal consolidados en account_rop_payable (DEBE=HABER)
    # FIX C-02a   — payslip_action_mixin: eliminado import duplicado de logging
    # FIX C-02b   — payslip_accounting_mixin: N+1 en bonos eliminado (fix B-05 aplicado al mixin)
    # FIX A-01    — payroll_run.action_confirm: batch atómico via mixin.write() + savepoint
    # FIX A-03    — payslip_accounting_mixin: logging de asiento creado (move.name, DEBE/HABER)
    # FIX A-04    — payslip_accounting_mixin: imports limpios (eliminados fields,api,K,datetime)
    # FIX M-02    — PayslipDeductionLine: usar K.MAX_PCT_EMBARGO en vez de 0.25 hardcoded
    # FIX M-04    — payroll_run.action_view_accounting_entry: UserError si no hay asiento
    # FIX M-05    — per_run: logging del asiento consolidado creado
    # ── Changelog v58 ──────────────────────────────────────────────────────────
    # ── Changelog v57 ──────────────────────────────────────────────────────────
    # planilla_const.py  — constantes centralizadas CR 2026 (tasas, topes, factores)
    # _sync_rop()        — ROP automatico Ley 7983 (1% obrero + 3.25% patron)
    # rop_applies field  — flag en empleado para activar/desactivar ROP
    # FIX VAC            — _sync_ausencias verifica solapamiento con vacation.payment
    # Type hints         — 17 metodos criticos de payslip_cr.py tipados
    # planilla_const     — integrada en rate_helper, pension_alimentaria, terminacion
    # Tests v56          — 26 nuevos tests (total 47): const, ROP, termination,
    #                      disability, per_run, crons, bono_antiguedad_config
    # RECORD RULES v55   — 5 nuevas (overtime, salary_hist, recurring, installment, config)
    # FIX DEPENDS v55    — payroll_calendar_id, ins_risk_class, schedule_type_id
    # ── Changelog v55 ──────────────────────────────────────────────────────────
    # FIX-TZ: Timezone CR UTC-6 — asistencias nocturnas corregidas (+6h en rango)
    # FIX-N1: Dashboard read_group() — elimina N+1 en _compute_metrics
    # FIX-N2: index=True en employee_id de 9 modelos — elimina full table scan
    # FIX-N3: load_workbook read_only=True — reduce memoria en importacion Excel
    # FIX-N5: salary_history store=True — elimina N+1 en lista historial
    # NEW-01: planilla.bono.antiguedad.config — tabla configurable por empresa
    # NEW-02: cron_bono_antiguedad — bono automatico en aniversario laboral
    # NEW-03: cron_alert_embargo_expiry — alerta embargo por vencer (Art. 172 CT)
    # v54 incluia: C-01 bonos en CCSS, C-02 record rules, C-03 hooks,
    #   per_run embargos/bonos separados, I-01 paternidad, I-02 bono%,
    #   I-04 constraint prestamo, M-02 email, M-03 MTSS, M-05 logging
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
        'views/embargo_views.xml',
        'views/bono_views.xml',
        'views/bono_antiguedad_config_views.xml',
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
        'wizard/import_template_wizard_views.xml',
        'wizard/import_data_wizard_views.xml',
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
    'external_dependencies': {'python': ['openpyxl']},
}