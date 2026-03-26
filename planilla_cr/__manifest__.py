{
    'name': 'Sistema Planilla v5.28.5-PROD',
    'version': '19.0.5.28.5',
    # ── Changelog v5.28.5 (Resumen Completo — ingresos desglosados como texto) ─
    # MEJORA: Resumen Completo vuelve al estilo de filas de texto (sin tablas
    #   widget de Odoo) pero ahora con ingresos adicionales desglosados por tipo:
    #   - Bonos Salariales (afecto CCSS) → bono_salarial_amount (ya existía)
    #   - Bonos / Incentivos (exentos CCSS) → amount_bonos_exentos
    #   - Licencias con Goce Pagadas → amount_licencias_con_goce (NUEVO)
    #   - Otros Ingresos Adicionales → amount_otros_ingresos_adic (NUEVO)
    #   Cada fila es invisible cuando el monto es 0, igual que el resto.
    # NEW-01: payslip_cr — dos campos nuevos computed+stored:
    #   amount_licencias_con_goce: sum de líneas licencia_con_goce tipo income.
    #   amount_otros_ingresos_adic: sum de líneas income que no son bonus ni
    #   licencia_con_goce (recurring benefits, ingresos manuales, etc.).
    # MOD-01: payslip_validation_mixin._compute_deduction_summaries() — ahora
    #   calcula los 3 campos de ingreso por sub-categoría separados.
    # MOD-02: payslip_cr_views — eliminados los widgets de lista (income_line_ids
    #   y deduction_only_line_ids) del Resumen Completo. Reemplazados por filas
    #   <tr> de texto limpio, consistentes con el resto del resumen.
    # ── Changelog v5.28.4 (Fix ParseError editable=false) ────────────────────
    # BUG: Las listas inline del Resumen Completo (income_line_ids y
    #   deduction_only_line_ids) tenían editable="false", que no es un valor
    #   válido en Odoo 19 (solo acepta "top" o "bottom"). Esto causaba un
    #   ParseError al cargar payslip_cr_views.xml.
    # FIX: Se eliminó el atributo editable de ambas listas. Sin ese atributo
    #   las listas heredan el comportamiento readonly del form padre (boleta
    #   en modo borrador permite edición en las pestañas correspondientes,
    #   y el Resumen Completo siempre es solo lectura por tener readonly="1").
    # ── Changelog v5.28.3 (Resumen desglosado) ───────────────────────────────
    # MEJORA: El Resumen Completo ya no agrupa ingresos y deducciones adicionales
    #   en un solo total — ahora muestra cada línea individualmente para poder
    #   identificar exactamente a qué corresponde cada monto.
    # MOD-01: Sección INGRESOS — después de las filas estáticas (base, HE,
    #   vacaciones, bonos salariales) se agrega <field name="income_line_ids">
    #   con lista de solo lectura que muestra Tipo, Concepto y Monto (₡) con
    #   suma al final. Cada ingreso adicional (bono exento, licencia con goce,
    #   subsidio, etc.) aparece en su propia fila con su descripción completa.
    # MOD-02: Sección DEDUCCIONES ADICIONALES — mantiene los sub-totales por
    #   categoría legal (①②③...) como referencia rápida, y agrega debajo un
    #   <field name="deduction_only_line_ids"> con lista de solo lectura
    #   mostrando Tipo, Concepto, Categoría, N° Resolución y Monto con suma.
    #   Esto permite ver cada embargo, cuota de préstamo, cobro o pensión
    #   por separado con su descripción y resolución judicial si aplica.
    # ── Changelog v5.28.2 (BugFix bono duplicado) ────────────────────────────
    # BUG: En el Resumen Completo, un bono salarial (afecto_ccss=True) aparecía
    #   dos veces: una en "Bonos Salariales (afecto CCSS)" y otra en
    #   "Ingresos Adicionales / Licencias con Goce". El cálculo del neto era
    #   correcto (no había doble conteo en el total), pero la presentación
    #   visual era confusa y engañosa.
    # CAUSA: _compute_deduction_summaries calculaba amount_bonos_exentos
    #   sumando TODAS las líneas income sin excluir los bonos salariales que
    #   ya estaban contados en bono_salarial_amount.
    # FIX-01: payslip_validation_mixin._compute_deduction_summaries():
    #   amount_bonos_exentos ahora excluye bonos con afecto_ccss=True,
    #   usando el mismo patrón de _get_bono_salarial_names() que ya usa
    #   _compute_totals. Solo incluye ingresos NO salariales: licencias con
    #   goce, subsidios exentos, recurring benefits, etc.
    # FIX-02: payslip_cr.py — string del campo actualizado a
    #   "Licencias con Goce / Otros ingresos" para reflejar con precisión
    #   qué contiene (ya no incluye bonos salariales).
    # FIX-03: payslip_cr_views.xml — etiqueta en Resumen Completo actualizada
    #   a "Licencias con Goce / Subsidios / Otros ingresos".
    # ── Changelog v5.28.1 (BugFix etiqueta CCSS pensionado) ──────────────────
    # BUG: La pestaña Resumen Completo mostraba "CCSS Obrero 10.83%" para todos
    #   los empleados, incluyendo pensionados sector público cuya tasa correcta
    #   es 6.50%. El CÁLCULO era correcto (₡113,750 para ₡1,750,000 = 6.5%),
    #   pero la ETIQUETA estaba hardcodeada en la vista y causaba confusión.
    # FIX-01: payslip_cr_views — bloque CCSS en Resumen Completo reescrito con
    #   4 variantes de etiqueta controladas por invisible:
    #     - pensioner_type != 'estado' + freq != 'monthly' → "10.83% (frecuencia)"
    #     - pensioner_type != 'estado' + freq == 'monthly' → "10.83% mensual"
    #     - pensioner_type == 'estado' + freq != 'monthly' → "6.50% pensionado (frecuencia)"
    #     - pensioner_type == 'estado' + freq == 'monthly' → "6.50% pensionado mensual"
    #   La etiqueta del pensionado se muestra en color warning (naranja) para
    #   mayor visibilidad de que aplica una tasa especial.
    # FIX-02: payslip_cr_views — pestaña Abonos y Deducciones también muestra
    #   etiqueta dinámica: "CCSS Obrero (10.83%)" o "CCSS Obrero (6.50% —
    #   pensionado, exonerado IVM)" según pensioner_type del empleado.
    # ── Changelog v5.28.0 (Auditoría — 3 correcciones pre-producción) ────────
    #
    # AUDIT-01: hooks.py._setup_accounting_config() — ahora CREA las cuentas
    #   contables si no existen en el plan contable de la empresa, en lugar de
    #   simplemente buscarlas y dejar los campos vacíos.
    #   Función get_or_create_account() reemplaza get_account() para garantizar
    #   que la instalación del módulo genere una configuración contable 100%
    #   completa desde el primer momento, sin intervención manual.
    #   Cuentas creadas automáticamente: 24 cuentas (gastos + pasivos + activos)
    #   incluyendo las nuevas 630600-630800, 230950-230970, 120500, 115000.
    #   También crea el diario "Planilla de Salarios" si no existe ningún diario
    #   general configurado.
    #
    # AUDIT-02: income_tax_bracket.py — campo year (Integer, required) nuevo.
    #   Identifica a qué resolución DGT pertenece cada tramo de renta.
    #   Nuevo @api.constrains(_check_single_active_year): impide que existan
    #   tramos activos de dos años fiscales distintos al mismo tiempo.
    #   Lanza ValidationError con instrucciones claras si se intenta activar
    #   un tramo de año diferente al ya activo.
    #   income_tax_data.xml: todos los registros actualizados con el campo year
    #   (2025 para tramos desactivados, 2026 para los vigentes).
    #   income_tax_bracket_views.xml: columna year en lista, campo year en form,
    #   context active_test=false para ver todos los tramos incluyendo inactivos,
    #   alerta visible en form cuando el tramo está inactivo.
    #
    # AUDIT-03: payslip_validation_mixin._validate_before_confirm() — nueva
    #   validación de embargo máximo legal (Art. 172 CT CR: máx. 25% del neto).
    #   Si los embargos judiciales de una boleta superan el 25% del salario neto,
    #   se lanza error bloqueante con el monto máximo legal calculado.
    #   Tolerancia de ₡0.50 para evitar falsos positivos por redondeo.
    #   Usa K.MAX_PCT_EMBARGO (25.0) de planilla_const.py.
    #   NOTA: pensión alimentaria NO está limitada (Ley 8590) — solo los embargos.
    #
    # ── Changelog v5.27.0 (Resumen Completo — etiquetas dinámicas) ───────────
    # NEW-01: payslip_cr — campo effective_frequency (Selection, computed+stored).
    #   Expone el resultado de _get_effective_freq() como campo almacenado para
    #   poder usarlo en condiciones invisible de la vista sin dot notation.
    #   Depende de payroll_calendar_id y payroll_run_id.payroll_calendar_id.
    # NEW-02: payslip_compute_mixin._compute_effective_frequency() — método que
    #   calcula y almacena effective_frequency en cada boleta.
    # MOD-01: payslip_cr_views — Resumen Completo completamente reescrito con
    #   etiquetas dinámicas que cambian según effective_frequency:
    #   - Encabezado: período, frecuencia y factor proporcional visibles.
    #   - Salario Base → "Mensual" / "Quincenal (50%)" / "Semanal (25%)" / "Bimensual (200%)"
    #   - Fila de proporcionalidad: días trabajados / días período + factor (visible
    #     solo si is_proportional=True).
    #   - Salario Bruto → etiqueta según frecuencia.
    #   - CCSS Obrero → label contextual por frecuencia.
    #   - Salario Neto → "Mensual/Quincenal/Semanal/Bimensual a Recibir".
    #   - Cargas patronales: base de cargas y provisiones con período explícito.
    #   - Deducciones adicionales numeradas ①②③④⑤⑥ con base legal citada.
    #   - Filas con valor 0 ocultas automáticamente (invisible="campo == 0").
    # ── Changelog v5.26.0 (Bloqueo confirmación) ─────────────────────────────
    # MOD-01: payroll_run_cr.action_confirm() — validación nueva ANTES de
    #   confirmar la planilla. Si alguna boleta activa tiene empleados sin
    #   calendarización o sin tipo de horario, lanza UserError con la lista
    #   exacta de empleados afectados y las instrucciones para corregirlo.
    #   Dos bloques independientes: uno para sin calendarización (error grave —
    #   salario calculado con frecuencia incorrecta) y otro para sin horario
    #   (advertencia de cálculo impreciso de HE). Ambos bloquean la confirmación.
    #   El mensaje incluye nombre de planilla, lista de empleados con bullet •
    #   y la ruta exacta dentro de Odoo para corregir cada caso.
    # ── Changelog v5.25.0 (Pestaña Resumen Completo) ─────────────────────────
    # NEW-01: payslip_cr_views — nueva pestaña "Resumen Completo" como PRIMERA
    #   pestaña del notebook. Muestra el flujo completo de cálculo en orden:
    #     1. Ingresos (base, HE, vacaciones, bonos, otros) → Salario Bruto
    #     2. Incapacidades (solo visible si hay en el período) → Base Cotizable
    #     3. Deducciones Legales (CCSS, Renta, créditos fiscales, paternidad)
    #     4. Deducciones Adicionales (pensión, embargos, préstamos, cobros,
    #        sindical, cooperativa, licencias sin goce, otras) — filas ocultas
    #        automáticamente cuando el valor es 0.
    #     5. Resultado Obrero → Salario Neto → Salario a Depositar
    #     6. Cargas Patronales (CCSS, INS, ROP, provisiones) → Costo Total
    #   Diseño: secciones con fondo secundario, valores en rojo (−) o verde (+),
    #   sin campos con valor 0 innecesarios. Todo readonly.
    # MOD-01: payslip_cr_views — pestaña "Deducciones Obrero" renombrada a
    #   "Abonos y Deducciones" para reflejar que contiene tanto ingresos
    #   adicionales como deducciones editables del período.
    # ── Changelog v5.24.0 (FIX F5 — Calendarización faltante) ───────────────
    # BUGFIX: cuando un empleado no tiene calendarización configurada, el sistema
    #   usaba 'monthly' como fallback → en una planilla quincenal el salario base
    #   se calculaba al 100% (mensual) en lugar del 50% (quincenal).
    # NEW-01: payslip_compute_mixin._get_effective_freq() — helper que determina
    #   la frecuencia con orden de prioridad:
    #     1. Calendarización del EMPLEADO (correcto y preferido)
    #     2. Calendarización de la PLANILLA (fallback inteligente)
    #     3. 'monthly' (último recurso)
    # MOD-01: payslip_compute_mixin — reemplazados los 4 usos del fallback manual
    #   `payroll_calendar_id.frequency if ... else 'monthly'` por
    #   `self._get_effective_freq()` en: _compute_base_salary (×2),
    #   _compute_deductions, _calc_income_tax.
    # NEW-02: payroll_run_cr — campos count_missing_calendar y
    #   count_missing_schedule (Integer, computed) que cuentan boletas con
    #   empleados sin esos datos. Calculados en _compute_totals.
    # NEW-03: payslip_cr_views — dos nuevas alertas al inicio del form:
    #   - ROJA: empleado sin calendarización (usa frecuencia de planilla)
    #   - AMARILLA: empleado sin tipo de horario
    #   Ambas invisibles cuando el dato está configurado.
    # NEW-04: payroll_run_cr_views — dos alertas al inicio del form de planilla
    #   mostrando cuántos empleados tienen datos faltantes, con instrucciones
    #   para ir a corregirlos en la ficha del empleado.
    #   Columnas count_missing_calendar (optional=show) y count_missing_schedule
    #   (optional=hide) agregadas a la vista de lista de planillas.
    # ── Changelog v5.23.0 (Vistas de lista ampliadas) ────────────────────────
    # NEW-01: payslip_cr — 8 campos computed+stored de resumen por categoría:
    #   amount_pension_alimentaria, amount_embargo, amount_loans,
    #   amount_cobros_empleado, amount_sindical, amount_cooperativa,
    #   amount_licencias_sin_goce, amount_bonos_exentos.
    #   Calculados desde deduction_line_ids agrupando por deduction_category.
    # NEW-02: payslip_validation_mixin — _compute_deduction_summaries():
    #   método que calcula los 8 campos anteriores. Se ejecuta cuando
    #   cambian las líneas de deducción de la boleta.
    # NEW-03: payroll_run_cr — 16 campos total_* nuevos que agregan los
    #   campos de boleta al nivel de planilla: total_salario_cotizable,
    #   total_bonos_salariales, total_overtime, total_vacaciones_pagadas,
    #   total_disability_days, total_ccss_subsidy, total_income_tax_credits,
    #   total_pension_alimentaria, total_embargo, total_loans,
    #   total_cobros_empleado, total_licencias_sin_goce, total_ins_employer,
    #   total_aguinaldo_provision, total_cesantia_provision,
    #   total_vacation_provision.
    # MOD-01: payroll_run_cr._compute_totals() — expandido con @api.depends
    #   sobre todos los campos nuevos de boleta y cálculo de los 16 totales.
    # MOD-02: payslip_cr_views — vista de lista de boletas completamente
    #   reescrita con 30+ columnas organizadas en secciones:
    #   Identificación → Ingresos → Incapacidades → Deducciones obrero
    #   (en orden de prioridad legal: CCSS, Renta, pensión alimentaria,
    #   embargos, préstamos, cobros, sindical, cooperativa, lic. sin goce)
    #   → Resultado obrero → Cargas patronales.
    #   Todos los campos nuevos con optional="hide" — visibles bajo demanda.
    # MOD-03: payroll_run_cr_views — vista de lista de planillas con las
    #   mismas secciones y todos los totales, igualmente optional="hide".
    # ── Changelog v5.22.0 (BugFix F4 — Salario Cotizable en Incapacidades) ──
    # BUGFIX B-01: CCSS obrero se calculaba sobre gross_salary completo aunque
    #   el empleado estuviera incapacitado. Los días subsidiados (día 4+) NO son
    #   salario → no deben generar CCSS ni Renta.
    #   Base legal: Art. 79 CT / MTSS DAJ-AE-201-12 / Art. 8 Ley ISR /
    #               Sala Segunda Voto 622-2010 / Arts. 3 y 22 Ley Const. CCSS.
    # BUGFIX B-02: CCSS patronal, provisiones (aguinaldo, cesantía, vacaciones)
    #   y ROP se calculaban sobre el salario completo cuando el patrono no tiene
    #   obligación salarial sobre los días subsidiados.
    # NEW-01: payslip_cr — campo disability_days_in_period (Integer, computed):
    #   días de incapacidad que caen DENTRO del período de esta boleta.
    #   Maneja incapacidades que cruzan períodos (overlap start/end).
    # NEW-02: payslip_cr — campo salario_cotizable (Monetary, computed+stored):
    #   base real sobre la que aplican CCSS, Renta, ROP y provisiones.
    #   Fórmula: (días_trabajados × sal_diario) + (días_1-3 × sal_diario × 50%).
    #   Si no hay incapacidades en el período: salario_cotizable == gross_salary.
    # MOD-01: payslip_compute_mixin._compute_extras() — calcula
    #   disability_days_in_period con intersección de fechas boleta/incapacidad,
    #   y salario_cotizable aplicando la fórmula legal correcta.
    #   Nuevos @api.depends: date_from, date_to, disability_ids.date_start/end,
    #   employee_id.base_salary.
    # MOD-02: payslip_compute_mixin._compute_deductions() — usa salario_cotizable
    #   como base g para todos los cálculos. Agrega salario_cotizable al
    #   @api.depends.
    # MOD-03: payslip_cr_views — campo disability_days_in_period en resumen
    #   incapacidades; nuevo grupo "Base Cotizable" con gross vs cotizable y nota
    #   legal; campo salario_cotizable en Deducciones Legales (invisible si = 0).
    # ── Changelog v5.21.0 (Clasificación de Pensionado) ─────────────────────
    # NEW-01: deduction_code_data.xml — nuevo código CCSS_OBR_PENSIONADO (6.50%)
    #   para pensionado sector público (Art. 4 Ley Const. CCSS). Exonerado IVM
    #   4.33%. Configurable desde Planilla CR → Configuración → Códigos de Deducción.
    # NEW-02: planilla_const — constante CCSS_EMP_PENSIONADO_ESTADO = 0.065
    #   como fallback si el código no existe en BD.
    # NEW-03: rate_helper — método get_ccss_pensionado_rate() que lee el código
    #   CCSS_OBR_PENSIONADO con fallback a la constante.
    # NEW-04: hr_employee_extension — campo pensioner_type (Selection 3 opciones):
    #   'none' (default), 'estado' (sector público), 'ivm' (CCSS).
    # NEW-05: hr_employee_extension — campo pension_resolution_number (Char):
    #   N° de resolución o carné. Requerido para tipo 'estado'.
    # NEW-06: hr_employee_extension — @api.onchange: al seleccionar tipo
    #   'estado' o 'ivm', fuerza rop_applies = False automáticamente.
    # NEW-07: hr_employee_extension — @api.constrains: bloquea guardado si
    #   pensioner_type == 'estado' y pension_resolution_number está vacío.
    # MOD-01: payslip_compute_mixin._compute_deductions() — lee pensioner_type
    #   del empleado y aplica tasa CCSS obrero correspondiente:
    #   'estado' → rh.get_ccss_pensionado_rate() (6.50%)
    #   'none' / 'ivm' → rh.get_ccss_employee_rate() (10.83%)
    #   Agrega employee_id.pensioner_type al @api.depends.
    # MOD-02: hr_employee_extension_views — nueva sección "Clasificación de
    #   pensionado" con radio button, campo resolución condicional (required +
    #   invisible según tipo) y avisos contextuales por tipo.
    # MOD-03: payslip_cr_views — campo employee_id.pensioner_type readonly en
    #   la boleta, visible solo cuando es distinto de 'none'.
    # ── Changelog v5.20.0 (Créditos fiscales por cargas familiares) ─────────
    # NEW-01: planilla_const — constantes CREDITO_FISCAL_HIJO (₡1,710/mes) y
    #   CREDITO_FISCAL_CONYUGE (₡2,590/mes). Vigentes 2026 según Decreto 45333-H
    #   (Art. 34 Ley 7092). Actualizar cada enero con el decreto del MH.
    # NEW-02: hr_employee_extension — campo income_tax_children (Integer):
    #   cantidad de hijos menores con derecho a crédito fiscal.
    # NEW-03: hr_employee_extension — campo income_tax_spouse_credit (Boolean):
    #   activa el crédito por cónyuge. Solo uno de los dos puede aplicarlo.
    # NEW-04: payslip_cr — campo income_tax_credits (Monetary, computed+stored):
    #   monto total de créditos aplicados. Informativo en la boleta y PDF.
    # MOD-01: payslip_compute_mixin._calc_income_tax() — ahora retorna tupla
    #   (tax_neto, creditos_aplicados). Los créditos se restan DESPUÉS del
    #   cálculo progresivo. Resultado nunca negativo (max(..., 0.0)).
    #   Ajusta créditos por frecuencia de pago usando K.FREQ_FACTORS.
    # MOD-02: payslip_compute_mixin._compute_deductions() — desempaqueta la
    #   tupla y asigna income_tax y income_tax_credits por separado.
    #   Agrega employee_id.income_tax_children y .income_tax_spouse_credit
    #   al @api.depends para recomputar cuando cambien los créditos del empleado.
    # MOD-03: hr_employee_extension_views — nueva sección "Créditos Fiscales
    #   (Art. 34 LIR)" en pestaña Planilla CR junto al grupo CCSS.
    # MOD-04: payslip_cr_views — campo income_tax_credits visible en pestaña
    #   Deducciones Obrero, oculto automáticamente cuando vale ₡0.
    # ── Changelog v5.19.0 (Toggle base de cálculo de Renta) ─────────────────
    # NEW-01: accounting_config — nuevo campo income_tax_base (Selection):
    #   'gross'    → base imponible = salario bruto (Art. 33 LIR — default)
    #   'net_ccss' → base imponible = bruto − CCSS obrero
    #   El default 'gross' preserva el comportamiento anterior exactamente.
    #   Empresas existentes no sienten cambio hasta que un admin lo modifique.
    # NEW-02: planilla_const — constante RENTA_BASE_DEFAULT = 'gross' como
    #   fallback si la empresa no tiene configuración contable creada.
    # MOD-01: payslip_compute_mixin._calc_income_tax(gross, ccss_emp=0.0) —
    #   nueva firma con parámetro ccss_emp opcional (retrocompatible).
    #   Lee income_tax_base de la config de empresa y ajusta la base antes
    #   de entrar al cálculo progresivo por tramos.
    # MOD-02: payslip_compute_mixin._compute_deductions() — pasa ccss_employee
    #   calculado como segundo argumento a _calc_income_tax().
    # MOD-03: accounting_config_views — nuevo grupo visual "Cálculo de Impuesto
    #   de Renta" con radio button y aviso de responsabilidad fiscal cuando
    #   se selecciona la opción net_ccss.
    # ── Changelog v5.17.0 (Fix dropdowns dinámicos — columnas desplazadas) ─
    # FIX-WIZ-04: _build_dynamic_lists._write_list() — el early return cuando
    #   values=[] causaba que next_col NO se incrementara, desplazando todas
    #   las columnas dinámicas subsecuentes. Efecto: departamento quedaba en
    #   la columna de sucursal, sucursal sin columna, etc. Ahora next_col
    #   siempre incrementa; si la lista está vacía escribe aviso en gris.
    # FIX-WIZ-05: Búsquedas usan .with_context(active_test=False) para incluir
    #   registros independientemente del campo active. Antes con active=True
    #   explícito algunos catálogos retornaban vacío si active no estaba seteado.
    # FIX-WIZ-06: planilla.employee.type y planilla.employee.status no tienen
    #   company_id — se eliminó el filtro de compañía de sus búsquedas.
    # ── Changelog v5.16.9 (Fix company_id int vs recordset) ─────────────────
    # FIX-WIZ-03: _build_dynamic_lists recibía company_id.id (int) pero
    #   usaba co.id internamente → AttributeError: int has no attribute id.
    #   Se pasa self.company_id (recordset) en vez de self.company_id.id.
    # ── Changelog v5.16.8 (Fix AttributeError atributos de instancia) ────────
    # FIX-WIZ-02: import_template_wizard — en Odoo los TransientModel no
    #   permiten asignar atributos de instancia con self.algo = valor fuera
    #   de campos fields.*. Se eliminaron dos atributos de instancia ilegales:
    #     self._listas_static_cols → ahora retornado por _build_listas_sheet
    #     self._dyn_lists          → ahora retornado por _build_dynamic_lists
    #   Flujo correcto:
    #     _, static_cols = self._build_listas_sheet(wb)
    #     dyn_lists = self._build_dynamic_lists(wb, company_id, static_cols)
    #     self._build_employees(wb, sample=s, dyn_lists=dyn_lists)
    #   _dv_dynamic ahora recibe dyn_lists como parámetro explícito.
    # ── Changelog v5.16.7 (Fix vista — campos privados Odoo 19) ─────────────
    # FIX-VIEW-01: hr_employee_extension_views — en Odoo 19 los campos
    #   gender, private_email, private_phone, private_country_id NO existen
    #   directamente en hr.employee (están bajo hr.employee.private o con
    #   control de acceso diferente). Se eliminaron de la vista Planilla CR
    #   para evitar el ParseError al cargar el módulo.
    #   Se mantienen work_email y work_phone (sí existen en hr.employee).
    #   Los campos privados siguen siendo importados desde el Excel via
    #   import_data_wizard con el chequeo dinámico emp_fields.
    # ── Changelog v5.16.6 (Dropdowns dinámicos + campos personales) ─────────
    # ARCH-01: import_template_wizard._build_dynamic_lists(wb, company_id) —
    #   nuevo método que consulta la BD al generar el machote y escribe en la
    #   hoja ⚙ LISTAS las opciones actuales de:
    #     calendar       → planilla.calendar (por compañía)
    #     employee_type  → planilla.employee.type
    #     employee_status→ planilla.employee.status
    #     branch         → planilla.branch (por compañía)
    #     department     → hr.department nivel raíz (por compañía)
    #     subdepartment  → hr.department con parent (por compañía)
    #     job            → hr.job (por compañía)
    #     country        → res.country (todos)
    # ARCH-02: _dv_dynamic(ws, col, key, row) — nuevo helper que aplica
    #   DataValidation apuntando a las columnas dinámicas de ⚙ LISTAS.
    # NEW-01: _build_employees — 44 columnas (era 41):
    #   col 14: Calendarización de Planilla (dropdown dinámico)
    #   col 38: País (dropdown dinámico res.country)
    #   col 42: Correo Personal (privado)
    # NEW-02: Dropdowns dinámicos activos en hoja EMPLEADOS:
    #   col 7 Departamento, col 8 Sub Departamento, col 9 Sucursal,
    #   col 10 Puesto/Cargo, col 11 Tipo de Empleado,
    #   col 12 Estado del Empleado, col 14 Calendarización, col 38 País
    # NEW-03: hr_employee_extension_views — nueva sección Datos Personales
    #   en la pestaña Planilla CR: work_email, work_phone, gender,
    #   private_email, private_phone, private_country_id.
    # FIX-IMP-04: import_data_wizard — lee 'Calendarización de Planilla'
    #   además de 'Frecuencia'/'Calendario'. Lee 'Correo Personal' y 'País'.
    # ── Changelog v5.16.5 (Fix segundo bloque secciones duplicado) ──────────
    # FIX-XLS-06: _build_employees() tenía DOS bloques 'secciones' consecutivos.
    #   El primero (cols 1-41, nuevo) hacía los merges correctamente.
    #   El segundo (cols 1-38, antiguo residual) intentaba mergear las mismas
    #   celdas ya fusionadas → AttributeError: MergedCell read-only en línea 848.
    #   Se eliminó el bloque duplicado. Ahora hay exactamente 1 bloque secciones.
    # ── Changelog v5.16.4 (Fix MergedCell error en machote) ─────────────────
    # FIX-XLS-05: import_template_wizard._build_employees() — AttributeError:
    #   'MergedCell' object attribute 'value' is read-only.
    #   Causa: el loop 'for ci in range(cs+1, ce+1): ws.cell(2,ci).fill=...'
    #   intentaba asignar fill a celdas ya fusionadas, que son read-only en
    #   openpyxl. Las celdas fusionadas heredan el estilo de la celda ancla
    #   automáticamente — el loop era innecesario y se eliminó en ambos sitios
    #   donde aparecía (secciones de EMPLEADOS y re-generación interna).
    # ── Changelog v5.16.3 (Tipos de horario CR + dropdown en machote) ────────
    # NEW-01: default_data.xml — 12 tipos de horario según Código de Trabajo CR:
    #   COMP  Jornada Completa 8h Lun-Vie    | COMP6 Jornada Completa 8h Lun-Sáb
    #   MIXT  Jornada Mixta 7h               | NOCT  Jornada Nocturna 6h
    #   ACU4  Acumulada 4x10h                | ACU3  Acumulada 3x12h (Art.136 bis)
    #   MEDI  Medio Tiempo 4h                | TRCR  Tres Cuartos 6h
    #   CONF  Horario de Confianza (Art.143) | GRD24 Guardias/Turnos 24h
    #   ROTA  Turno Rotativo                 | FDSM  Fines de Semana (HE 2.0x)
    #   Cada registro incluye base legal, horas/día, horas/semana, factor HE.
    # NEW-02: import_template_wizard._DV_LISTS agrega 'schedule' con los 12
    #   nombres para que el machote tenga dropdown en la columna Tipo de Horario.
    # FIX-XLS-04: _build_employees — dropdown activo en col 13 (Tipo de Horario).
    # ── Changelog v5.16.2 (Fix dropdowns Excel — hoja LISTAS visible) ────────
    # FIX-XLS-01: import_template_wizard._build_listas_sheet() — la hoja
    #   _LISTAS se renombra a '⚙ LISTAS' y se deja VISIBLE (no oculta).
    #   Causa: Excel 2016/2019 y algunas versiones de Excel Online no muestran
    #   el dropdown cuando la hoja fuente tiene sheet_state='hidden'.
    #   La hoja se protege con contraseña (planilla_cr_sys) y tab gris para
    #   que el usuario no la edite accidentalmente.
    # FIX-XLS-02: _dv() — el rango de la fórmula ahora empieza en fila 3
    #   (fila 1=advertencia, fila 2=headers, fila 3+=valores) para no incluir
    #   los encabezados en el dropdown.
    # FIX-XLS-03: Tipo de Horario — actualizado el tooltip de la columna para
    #   indicar que es texto libre (catálogo de Odoo, no tiene dropdown).
    # ── Changelog v5.16.1 (Machote generado desde la app + campos médicos) ─────
    # FIX-ARCH: import_template_wizard.py — el machote ahora incluye los
    #   campos médicos y dropdowns directamente en el wizard generador.
    #   El machote se descarga desde Planilla CR → Acción de Personal →
    #   Generar Machote de Importación, con todos los dropdowns activos.
    # NEW-01: _DV_LISTS agrega 'ins_occupation' (304 ocupaciones COCR-2023)
    #   y 'blood_type' (8 tipos de sangre) con dropdowns en hoja EMPLEADOS.
    # NEW-02: _build_employees — 3 columnas nuevas:
    #   col 21: Ocupación INS  — dropdown [XXXX] descripción (COCR-2023)
    #   col 22: Tipo de Sangre — dropdown A+/A-/B+/B-/AB+/AB-/O+/O-
    #   col 40: Diagnóstico / Notas Médicas — texto libre
    # NEW-03: Sección DATOS INS ampliada a cols 14-22 (era 14-20)
    # NEW-04: Sección DATOS PERSONALES Y MÉDICOS (antes solo PERSONALES)
    # ── Changelog v5.16.0 (Campos médicos INS + Dropdowns Excel) ───────────
    # NEW-01: hr_employee_extension — blood_type (tipo de sangre, 8 opciones)
    # NEW-02: hr_employee_extension — medical_notes (Text: diagnóstico/notas)
    # NEW-03: hr_employee_extension_views — sección Datos Médicos en tab INS
    # NEW-04: import_data_wizard — lee Tipo de Sangre, Diagnóstico del Excel
    # NEW-05: import_data_wizard — ocupación ahora acepta texto completo del
    #   dropdown '[1120] Directores y gerentes generales' extrayendo solo el código
    # NEW-06: Machote Excel v3 — 3 nuevos dropdowns en hoja EMPLEADOS:
    #   Clase de Riesgo INS (col S), Ocupación INS (col AE), Tipo de Sangre (col AN)
    #   Las listas usan _LISTAS como fuente (307 ocupaciones COCR-2023)
    #   Machote: Machote_Planilla_Mundo_Pet_v54_v3.xlsx
    # ── Changelog v5.15.9 (Fix INS clase de riesgo en importación) ───────────
    # FIX-IMP-03: INS_RISK en import_data_wizard — el diccionario solo tenía
    #   claves cortas ('i','ii'...) pero el machote envía el valor completo del
    #   dropdown: 'I - Oficinas', 'II - Comercio General', etc.
    #   _normalize('I - Oficinas') → 'i - oficinas' → no encontraba nada → False.
    #   Se agregaron todas las variantes largas con descripción para las 5 clases.
    # NOTA Ocupación INS: columna existe en el machote ('Ocupación INS' col 31)
    #   pero el empleado Walter la tenía vacía — no es un bug del wizard.
    #   El código de ocupación INS (4 dígitos) debe llenarse en el Excel.
    # ── Changelog v5.15.8 (Fix importación tipo de identificación) ───────────
    # FIX-IMP-01: import_data_wizard.py — INS_ID_TYPE usaba códigos del INS
    #   ('01','02'...) para buscar en planilla.identification.type, pero la BD
    #   usa códigos propios ('CI','DIMEX','PAS','CJ','NITE'). El campo
    #   identification_type_id quedaba vacío en todos los empleados importados.
    #   Solución:
    #     - INS_ID_TYPE ahora mapea texto → código BD (CI, DIMEX, PAS, CJ, NITE)
    #     - INS_ID_TYPE_CODE nuevo diccionario mapea texto → código INS numérico
    #       ('01','02'...) solo para el campo ins_id_type del empleado
    #   Mapeos agregados: 'cédula de identidad'→CI, 'nite'→NITE, 'cj'→CJ
    # FIX-IMP-02: Los 4 campos Many2one restantes (Sucursal, Tipo Empleado,
    #   Tipo Horario, Calendarización) requieren que los catálogos existan en BD.
    #   No es un bug del wizard — esos registros deben crearse en Odoo antes
    #   de la importación (ver instrucciones en documentación del módulo).
    # ── Changelog v5.15.7 (Fix wizard instrucciones en columna A) ─────────────
    # FIX-WIZ-01: import_data_wizard._sheet_rows() — nuevo filtro de filas de
    #   instrucciones al pie de la hoja. Algunos machotes tienen un bloque de
    #   instrucciones (fila 85+) en la misma hoja de datos. El wizard los leía
    #   como registros válidos y generaba errores falsos "Empleado no encontrado".
    #   La detección usa 3 criterios:
    #     1. Fila empieza con '📋' (marcador estándar del machote)
    #     2. Primera celda tiene >80 caracteres (ninguna cédula es tan larga)
    #     3. Primera celda empieza con patrón '1. ', '2. ' (lista numerada)
    #   Al detectar la primera fila de instrucciones, se trunca el listado.
    #   Caso verificado: hoja VACACIONES fila 85 → 10 errores falsos eliminados.
    # ── Changelog v5.15.6 (Tests cobros + context_today) ─────────────────────
    # FIX-TZ-01: 13 ocurrencias de fields.Date.today() reemplazadas por
    #   fields.Date.context_today(self) en:
    #   payroll_report.py (3), hr_employee_extension.py (2), disability.py (2),
    #   employee_loan.py (1), payroll_dashboard.py (1), employee_termination.py (2),
    #   vacation_payment.py (1), aguinaldo_wizard.py (1), import_overtime_wizard.py (1)
    #   Impacto: fechas por defecto ahora respetan el timezone del usuario CR (UTC-6).
    # TEST-01: tests/test_employee_charges.py — 51 tests nuevos en 11 clases:
    #   TestChargeModel        (9 tests) — campos computados, constraints
    #   TestChargeStateMachine (7 tests) — flujo draft→approved→applied→cancelled
    #   TestSyncUniqueCharge   (6 tests) — cobro único, deduplicación, subsidio 100%
    #   TestSyncRecurringCharge(6 tests) — recurrencia, períodos, recurrence_end
    #   TestSyncBatch          (3 tests) — batch multi-empleado, aislamiento
    #   TestChargeAccounting   (5 tests) — DEBE=HABER, cuenta 230970, subsidio parcial
    #   TestCancelRestoresCharges(2 tests) — restauración al cancelar boleta
    #   TestChargeTypeCatalog  (6 tests) — unicidad código, datos iniciales, helpers
    #   TestEmployeeChargeInteg(4 tests) — One2many en empleado, auto-sync
    #   TestChargeReport       (3 tests) — action_print, reportes en BD
    #   Total v5.15.6: 51 tests nuevos → 194 tests totales en el módulo
    # ── Changelog v5.15.5 (Integración Contable Cobros + Auditoría) ──────────
    # NEW-01: accounting_config.py — campo account_cobro_empleado_payable
    #   Cuenta 230970 para cobros al empleado (almuerzos, productos, uniformes…)
    #   separada de 230000 Salarios por Pagar para mejor control contable.
    #   Incluida en action_autocompletar_cuentas() → se crea automáticamente.
    # NEW-02: payslip_accounting_mixin._create_accounting_entry() actualizado:
    #   - cobros_empleado: suma deducciones categoría 'other' CON employee_charge_id
    #   - otras_ded_manual: deducciones 'other' SIN employee_charge_id (manuales)
    #   - cobros_empleado se RESTA de net_for_accounting (cuadre DEBE=HABER)
    #   - cobros_empleado se ACREDITA en account_cobro_empleado_payable (230970)
    #   - Fallback: si 230970 no configurada, usa account_salary_payable (230000)
    #   Sin este fix, los cobros iban a 230000 mezclados con salarios netos,
    #   imposibilitando el control y conciliación con el proveedor del comedor.
    # NEW-03: accounting_config_views.xml — campo cobro en UI Configuración Contable
    # AUDIT: 9 categorías auditadas — 0 errores críticos, lógica DEBE=HABER verificada
    # ── Changelog v5.15.4 (Reporte PDF + Pestaña Empleado) ───────────────────
    # NEW-01: report/employee_charge_report.xml — 2 reportes PDF:
    #   - Cobro al Empleado: detalle individual con totales y estado recurrencia
    #   - Resumen de Cobros: multi-registro con totales consolidados
    #   Botón "Imprimir" en form de cobro (visible cuando state != 'draft')
    # NEW-02: hr_employee_extension.py — campo employee_charge_ids One2many
    #   para acceder a cobros desde el formulario del empleado.
    # NEW-03: hr_employee_extension_views.xml — pestaña "🛒 Cobros al Empleado"
    #   muestra lista de cobros del empleado con estado, recurrencia y cargo.
    # ── Changelog v5.15.3 (Cobros Recurrentes Automáticos) ───────────────────
    # NEW-01: PlanillaEmployeeCharge.is_recurring — toggle en el cobro para
    #   activar aplicación automática en cada período de planilla.
    # NEW-02: PlanillaEmployeeCharge.recurrence_end — fecha límite opcional.
    #   Sin fecha = recurrencia indefinida.
    # NEW-03: PlanillaEmployeeCharge.applied_periods — campo Char que registra
    #   los períodos YYYY-MM ya procesados. Evita doble cobro en re-sync.
    # NEW-04: Métodos helper: _get_applied_periods_set, _mark_period_applied,
    #   _is_period_already_applied — lógica de deduplicación por período.
    # NEW-05: _sync_employee_charges() actualizado — diferencia cobros únicos
    #   (se consumen → 'applied') de recurrentes (permanecen en 'approved',
    #   solo se registra el período aplicado en applied_periods).
    # NEW-06: _sync_employee_charges_batch() actualizado — misma lógica en
    #   modo batch para creación masiva de boletas.
    # NEW-07: action_cancel() actualizado — cobros recurrentes no bloquean
    #   la cancelación aunque tengan payslip_id vinculado.
    # NEW-08: Vistas actualizadas — campo is_recurring en list/form,
    #   recurrence_end y applied_periods en form, alert diferenciado para
    #   cobros recurrentes vs únicos.
    # ── Changelog v5.15.2 (Fix tabla planilla_employee_charge) ───────────────
    # FIX-DB-01: PayslipDeductionLine.employee_charge_id cambiado de Many2one
    #   a Integer para eliminar dependencia circular en BD:
    #   planilla_payslip_deduction_line.employee_charge_id → planilla_employee_charge
    #   → planilla_employee_charge.payslip_id → planilla_payslip_cr
    #   → (a través de deduction_line_ids) → planilla_payslip_deduction_line
    #   Con FK circular, Odoo fallaba silenciosamente al crear planilla_employee_charge.
    #   La relación lógica se mantiene (employee_charge_id guarda el ID), solo
    #   se elimina la constraint FK de BD.
    # FIX-DB-02: payslip_sync_mixin._sync_employee_charges — filtered actualizado
    #   para comparar Integer en vez de Many2one.id.
    # FIX-DB-03: payslip_action_mixin.action_cancel — restauración de cobros
    #   actualizada para usar browse() en vez de mapped() sobre Many2one.
    # ── Changelog v5.15.1 (Hotfix iconos accesibilidad) ──────────────────────
    # FIX-ACC-01: vacation_payment_views.xml — 3 iconos <i class="fa ..."> sin
    #   atributo title (fa-exclamation-triangle x2, fa-info-circle x1).
    #   Odoo 19 requiere title en todos los iconos fa para accesibilidad (a11y).
    # FIX-ACC-02: termination_views.xml — fa-info-circle sin title.
    # FIX-ACC-03: payslip_cr_views.xml — fa-exclamation-triangle sin title.
    #   Todos eran WARNING en el log pero no bloqueaban la instalación.
    # ── Changelog v5.15 (Cobros al Empleado) ─────────────────────────────────
    # NEW-01: planilla.charge.type — catálogo configurable de tipos de cobro
    #   (almuerzo, productos, uniformes, parqueo, seguros, etc.)
    #   Soporta modos: fijo por período y por unidades/días.
    #   Subsidio patronal configurable por tipo (0%-100%).
    #   Flag affects_ccss para salario en especie (Art. 166 CT).
    # NEW-02: planilla.employee.charge — cobro por empleado/período
    #   Flujo: draft → approved → applied → cancelled
    #   Campos computados: total_amount, employer_amount, employee_amount
    #   Vinculación a boleta vía payslip_id para trazabilidad completa
    # NEW-03: PayslipDeductionLine.employee_charge_id — nuevo campo FK
    #   para referencia del cobro origen. Evita duplicados en re-sync.
    # NEW-04: _sync_employee_charges() en payslip_sync_mixin
    #   Aplica automáticamente cobros aprobados del período en cada boleta.
    #   Solo crea línea de deducción si employee_amount > 0 (subsidio < 100%).
    # NEW-05: _sync_employee_charges_batch() — versión batch para planilla grupal
    #   Para 200 empleados: 200 queries → 1 query. Consistente con PERF-05.
    # NEW-06: action_sync_novedades() actualizado con _sync_employee_charges()
    # NEW-07: Datos iniciales: 8 tipos de cobro predefinidos (charge_type_data.xml)
    #   ALMUERZO_FIJO, ALMUERZO_DIAS, ALMUERZO_SUBS, PRODUCTOS,
    #   UNIFORME, PARQUEO, SEGURO_COLECT, OTRO_COBRO
    # NEW-08: Permisos en ir.model.access.csv para admin/aprobador/operador
    # ─────────────────────────────────────────────────────────────────────────
    # ── Changelog v5.13 (Licencias Especiales CR + Auditoría Producción) ────
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
    'summary': 'Sistema de Planilla Costa Rica v5.14 — Legislación CR 2026',
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
        'data/leave_cr_data.xml',
        'data/default_data.xml',
        'data/charge_type_data.xml',
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
        'views/leave_cr_views.xml',
        'views/bono_antiguedad_config_views.xml',
        'views/employee_charge_views.xml',
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
        'report/employee_charge_report.xml',
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