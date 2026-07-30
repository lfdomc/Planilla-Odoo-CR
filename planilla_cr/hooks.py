from odoo import api, SUPERUSER_ID


def pre_init_hook(env):
    """
    Corre ANTES de que Odoo cargue los modelos del modulo.
    Crea columnas faltantes en hr_employee para evitar error 500
    en BDs que vienen de versiones anteriores del modulo.
    En Odoo 19 recibe 'env' (no 'cr' directamente).
    """
    import logging
    _logger = logging.getLogger(__name__)
    cr = env.cr if hasattr(env, 'cr') else env
    cols = [
        ('vacation_last_anniversary_year', 'INTEGER', '0'),
        ('vacation_balance_alert',         'BOOLEAN', 'FALSE'),
        ('vacation_days_accrued',          'NUMERIC', '0'),
        ('vacation_days_taken',            'NUMERIC', '0'),
        ('vacation_days_available',        'NUMERIC', '0'),
        ('vacation_initial_balance',       'NUMERIC', '0'),
        ('years_of_service',               'INTEGER', '0'),
        ('next_anniversary_date',          'DATE',    'NULL'),
        ('next_anniversary_days',          'NUMERIC', '0'),
    ]
    created = []
    for col, typ, dflt in cols:
        cr.execute("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name='hr_employee' AND column_name=%s
        """, (col,))
        if not cr.fetchone():
            cr.execute(
                'ALTER TABLE hr_employee ADD COLUMN %s %s DEFAULT %s' % (col, typ, dflt)
            )
            created.append(col)
    if created:
        _logger.info('planilla_cr pre_init_hook: columnas creadas: %s', ', '.join(created))

    # FIX: Re-registrar codigos de deduccion huerfanos en ir_model_data.
    # Problema: cuando el modulo se desinstala, Odoo elimina los registros
    # de ir_model_data pero deja las filas reales en planilla_deduction_code.
    # Al reinstalar, el XML intenta INSERT de nuevo y choca con la restriccion
    # unica (code, company_id). La solucion es re-vincular esos registros
    # existentes en ir_model_data ANTES de que el loader XML los intente crear,
    # para que el mecanismo noupdate="1" los omita correctamente.
    _relink_orphan_deduction_codes(cr, _logger)


def _relink_orphan_deduction_codes(cr, logger):
    """
    FIX reinstalacion: evita UniqueViolation / @constrains en TODOS los modelos
    de datos del modulo.

    Cuando el modulo se desinstala, Odoo borra los registros de ir_model_data
    pero deja las filas reales en cada tabla intactas (por diseno, para no
    borrar datos de produccion). Al reinstalar, los archivos XML con
    noupdate="1" intentan INSERT de nuevo y chocan con restricciones unicas
    (a nivel de BD o Python @constrains).

    Solucion: parsear TODOS los XMLs de data/ buscando <record> de cualquier
    modelo planilla.*, verificar si la fila ya existe en la tabla usando el
    campo 'code' (o 'name' o 'date' como fallback), y si existe registrarla
    en ir_model_data. Con eso el mecanismo noupdate="1" la omite completamente
    sin llamar a create(), por lo que ninguna restriccion se dispara.

    El parseo dinamico cubre automaticamente cualquier record nuevo que se
    agregue en el futuro. Es seguro en instalaciones limpias: si la tabla no
    existe o el valor de lookup no existe, simplemente no hace nada.

    Odoo convierte nombres de modelo a tabla reemplazando puntos por
    guiones bajos: planilla.charge.type -> planilla_charge_type.
    """
    import os
    import glob
    from xml.etree import ElementTree as ET

    module_dir = os.path.dirname(os.path.abspath(__file__))
    xml_files = glob.glob(os.path.join(module_dir, 'data', '*.xml'))

    # Recopilar todos los <record model="planilla.*"> de todos los XMLs.
    # Para cada uno extraer: xmlid, model, campo de lookup y su valor.
    records_to_check = []
    for xml_path in xml_files:
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
            for record in root.iter('record'):
                model = record.get('model', '')
                if not model.startswith('planilla.'):
                    continue
                xmlid = record.get('id')
                if not xmlid:
                    continue
                # Prioridad de lookup: code > name > date
                lookup_field = None
                lookup_value = None
                for field_name in ('code', 'name', 'date'):
                    field_el = record.find("field[@name='%s']" % field_name)
                    if field_el is not None and field_el.text and field_el.text.strip():
                        lookup_field = field_name
                        lookup_value = field_el.text.strip()
                        break
                if lookup_field:
                    records_to_check.append((xmlid, model, lookup_field, lookup_value))
        except Exception as parse_err:
            logger.warning(
                'planilla_cr _relink_orphan_deduction_codes: '
                'no se pudo parsear %s: %s', xml_path, parse_err
            )

    if not records_to_check:
        return

    relinked = []
    tables_checked = {}  # cache: table -> bool (existe)
    columns_checked = {}  # cache: (table, col) -> bool

    for xml_name, model, lookup_field, lookup_value in records_to_check:
        # Nombre de tabla: puntos -> guiones bajos
        table = model.replace('.', '_')

        # Verificar que la tabla exista (instalacion limpia)
        if table not in tables_checked:
            cr.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
                (table,)
            )
            tables_checked[table] = bool(cr.fetchone())
        if not tables_checked[table]:
            continue

        # ¿Ya existe entrada en ir_model_data para este modulo+xmlid?
        cr.execute(
            "SELECT 1 FROM ir_model_data WHERE module = 'planilla_cr' AND name = %s",
            (xml_name,)
        )
        if cr.fetchone():
            continue  # Ya registrado, OK

        # Verificar que la columna de lookup exista en la tabla
        col_key = (table, lookup_field)
        if col_key not in columns_checked:
            cr.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = %s",
                (table, lookup_field)
            )
            columns_checked[col_key] = bool(cr.fetchone())
        if not columns_checked[col_key]:
            continue

        # ¿Existe la fila real en la tabla?
        cr.execute(
            'SELECT id FROM %s WHERE %s = %%s ORDER BY id LIMIT 1' % (table, lookup_field),
            (lookup_value,)
        )
        row = cr.fetchone()
        if not row:
            continue  # No existe: el XML la creara normalmente

        res_id = row[0]

        # Re-registrar en ir_model_data para que noupdate="1" la omita.
        # Odoo 19 elimino las columnas date_init/date_update de ir_model_data.
        cr.execute("""
            INSERT INTO ir_model_data (module, name, model, res_id, noupdate)
            VALUES ('planilla_cr', %s, %s, %s, TRUE)
            ON CONFLICT (module, name) DO NOTHING
        """, (xml_name, model, res_id))
        relinked.append('%s:%s' % (model.split('.')[-1], lookup_value))

    if relinked:
        logger.info(
            'planilla_cr pre_init_hook: %d registros re-vinculados en '
            'ir_model_data para evitar UniqueViolation: %s',
            len(relinked), ', '.join(relinked)
        )


def post_init_hook(env):
    _create_email_templates(env)
    _setup_all_companies_config(env)
    _ensure_schedule_types(env)
    _ensure_payroll_calendars(env)
    _fix_income_tax_bracket_company_scope(env)
    _fix_minimum_salary_company_scope(env)
    try:
        from .models.migrate_codes import migrate_codes
        migrate_codes(env)
    except Exception as _e:
        import logging
        logging.getLogger(__name__).warning(
            'planilla_cr.migrate_codes (init): %s (no critico)', _e
        )


def post_migrate_hook(env):
    """
    L3 FIX -- Hook de migracion entre versiones.
    Se ejecuta automaticamente al hacer -u planilla_cr.
    Garantiza que la configuracion contable este actualizada
    con las cuentas nuevas agregadas en cada version.
    """
    # Garantizar que columnas nuevas existan aunque el ORM no las creara automaticamente
    _ensure_missing_columns(env)
    # Preserve show_vacation_on_payslip: ensure column exists without resetting
    env.cr.execute("""
        ALTER TABLE planilla_accounting_config
        ADD COLUMN IF NOT EXISTS show_vacation_on_payslip BOOLEAN;
        UPDATE planilla_accounting_config
        SET show_vacation_on_payslip = TRUE
        WHERE show_vacation_on_payslip IS NULL;
    """)

    # Fix configs without company_id: assign current company
    # This prevents the 'same change affects all companies' bug
    env.cr.execute("""
        UPDATE planilla_accounting_config
        SET company_id = (SELECT id FROM res_company LIMIT 1)
        WHERE company_id IS NULL;
    """)
    try:
        from .models.migrate_codes import migrate_codes
        migrate_codes(env)
    except Exception as _e:
        import logging
        logging.getLogger(__name__).warning(
            'planilla_cr.migrate_codes: %s (no critico)', _e
        )
    _setup_all_companies_config(env)
    _populate_schedule_defaults(env)
    _ensure_deduction_codes(env)
    _ensure_schedule_types(env)
    _ensure_payroll_calendars(env)
    _fix_income_tax_bracket_company_scope(env)
    _fix_minimum_salary_company_scope(env)
    _ensure_default_branch(env)
    _fix_holiday_company_scope(env)
    _fix_employee_document_type_company_scope(env)
    _fix_hour_license_date_end(env)
    _migrate_disability_payslip_m2m(env)
    _migrate_leave_cr_payslip_m2m(env)
    _remove_legacy_menu_items(env)


def _fix_holiday_company_scope(env):
    """
    FIX BUG: los 13 feriados nacionales estandar de Costa Rica
    (data/public_holidays_cr.xml) se crean con noupdate="1" y SIN
    especificar company_id. Como el campo tiene
    default=lambda self: self.env.company, cada uno queda atado a la
    UNICA compania que estuviera activa durante la instalacion inicial
    del modulo (normalmente la primera/demo) -- exactamente el mismo
    patron de bug que las calendarizaciones (ver
    _ensure_payroll_calendars).

    Sintoma real reportado: un usuario en la compania "Condominio
    Horizontal Residencial..." intenta registrar una hora extra tipo
    "Dia Feriado" para el 25 de julio (Anexion del Partido de Nicoya).
    El sistema rechaza la fecha diciendo que no esta registrada como
    feriado obligatorio -- aunque la lista de Feriados Nacionales SI
    muestra el 25 de julio marcado como obligatorio. La causa: ese
    registro de feriado pertenece a OTRA compania (la que estaba activa
    al instalar el modulo), y is_paid_holiday() filtra por
    company_id = compania_actual OR company_id = False (global) -- como
    el feriado no es global ni de "Condominio...", nunca coincide,
    aunque el usuario lo vea en pantalla porque tiene acceso
    multi-compania.

    A diferencia de las calendarizaciones (que si tiene sentido
    duplicar por compania, cada una con su propio dia de pago), los
    feriados nacionales son los MISMOS para todas las companias de
    Costa Rica -- la correccion correcta es liberar el company_id (que
    quede vacio/global) en vez de duplicar 13 registros por cada
    compania del cliente. Solo se tocan los feriados con type=national
    creados por el catalogo estandar (identificados por su nombre
    conocido) -- nunca se toca un feriado personalizado (type=custom)
    que un cliente haya creado a proposito para una compania
    especifica.
    """
    Holiday = env['planilla.public.holiday']
    NOMBRES_CATALOGO_ESTANDAR = [
        'A\u00f1o Nuevo', 'Ano Nuevo',
        'Jueves Santo', 'Viernes Santo',
        'Batalla de Rivas (Juan Santamar\u00eda)', 'Batalla de Rivas (Juan Santamaria)',
        'D\u00eda del Trabajador', 'Dia del Trabajador',
        'Anexi\u00f3n del Partido de Nicoya', 'Anexion del Partido de Nicoya',
        'Virgen de los \u00c1ngeles', 'Virgen de los Angeles',
        'Madre y D\u00eda de la Anexi\u00f3n de Guanacaste',
        'Madre y Dia de la Anexion de Guanacaste',
        'Independencia', 'D\u00eda de la Independencia', 'Dia de la Independencia',
        'D\u00eda de la Cultura', 'Dia de la Cultura',
        'D\u00eda de las Culturas', 'Dia de las Culturas',
        'Abolici\u00f3n del Ej\u00e9rcito', 'Abolicion del Ejercito',
        'Navidad',
    ]
    huerfanos = Holiday.sudo().search([
        ('type', '=', 'national'),
        ('company_id', '!=', False),
        ('name', 'in', NOMBRES_CATALOGO_ESTANDAR),
    ])
    if not huerfanos:
        return
    # Antes de liberar cada uno, verificar que no exista ya un duplicado
    # global (company_id=False) para esa misma fecha -- si ya existe,
    # simplemente se elimina el atado a una sola compania sin crear
    # conflicto; si no existe, se libera el registro actual.
    for h in huerfanos:
        ya_global = Holiday.sudo().search([
            ('date', '=', h.date),
            ('company_id', '=', False),
            ('id', '!=', h.id),
        ], limit=1)
        if ya_global:
            continue  # ya hay una version global de ese feriado, no duplicar
        h.write({'company_id': False})


def _fix_hour_license_date_end(env):
    """
    FIX v5.28.72: Corregir licencias por horas con date_end != date_start.
    Una licencia de horas ocurre en un solo dia, por lo tanto
    date_end siempre debe ser igual a date_start.
    Se ejecuta automaticamente en cada -u planilla_cr para corregir
    registros historicos creados antes de este fix.

    NOTA TECNICA: se usa SQL directo en lugar de ORM write() porque los
    registros aprobados/pagados pueden disparar recomputes secundarios o
    validaciones de estado que bloqueen el write silenciosamente. El SQL
    garantiza la correccion independientemente del estado del registro.
    """
    import logging
    _logger = logging.getLogger(__name__)

    env.cr.execute("""
        UPDATE planilla_leave_cr
        SET date_end = date_start
        WHERE leave_unit = 'hour'
          AND date_start IS NOT NULL
          AND date_end IS NOT NULL
          AND date_end != date_start
        RETURNING id
    """)
    fixed_ids = [r[0] for r in env.cr.fetchall()]

    if fixed_ids:
        _logger.info(
            'planilla_cr._fix_hour_license_date_end: '
            'corregidas %s licencias por horas con date_end incorrecto. IDs: %s',
            len(fixed_ids), fixed_ids
        )
        # Invalidar cache ORM para que la UI refleje el cambio sin recargar
        env['planilla.leave.cr'].browse(fixed_ids).invalidate_recordset()



def _migrate_disability_payslip_m2m(env):
    """
    FIX v5.28.74: Migrar datos de planilla_disability.payslip_id (Many2one)
    a la nueva tabla de relacion Many2many planilla_disability_payslip_rel.

    Contexto: el campo payslip_id paso de Many2one a Many2many (payslip_ids)
    para soportar incapacidades que cruzan multiples periodos de pago
    (ej. maternidad 4-dic-2025 a 26-mar-2026 afecta 3+ boletas).

    La columna `payslip_id` (Many2one) sigue existiendo como campo computado
    de compatibilidad (apunta al primero de payslip_ids), pero ya no se escribe
    directamente. Este hook copia los datos historicos a la tabla M2M.
    """
    import logging
    _logger = logging.getLogger(__name__)

    # Verificar si la columna payslip_id aun existe como columna real en la tabla
    # (puede que ya se haya eliminado en una migracion anterior)
    env.cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'planilla_disability'
          AND column_name = 'payslip_id_legacy'
    """)
    legacy_col_exists = bool(env.cr.fetchone())

    # Verificar si hay datos en la tabla M2M
    env.cr.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_name = 'planilla_disability_payslip_rel'"
    )
    m2m_table_exists = bool(env.cr.fetchone())
    if not m2m_table_exists:
        _logger.warning(
            'planilla_cr._migrate_disability_payslip_m2m: '
            'tabla planilla_disability_payslip_rel no existe aun -- '
            'se creara en el siguiente -u. Saltando migracion.'
        )
        return

    # Leer incapacidades que tienen payslip_id directo en la columna de DB
    # NOTA: como payslip_id ahora es computed/store=True basado en payslip_ids,
    # la columna en DB se llenara automaticamente. Buscamos registros con
    # payslip_id_legacy si existe, o usamos la columna payslip_id si aun esta.
    env.cr.execute("""
        SELECT id,
               COALESCE(payslip_id, NULL) as pid
        FROM planilla_disability
        WHERE payslip_id IS NOT NULL
    """)
    rows = env.cr.fetchall()
    migrated = 0
    for dis_id, payslip_id in rows:
        # Verificar si la relacion ya existe en M2M
        env.cr.execute("""
            SELECT 1 FROM planilla_disability_payslip_rel
            WHERE disability_id = %s AND payslip_id = %s
        """, (dis_id, payslip_id))
        if not env.cr.fetchone():
            env.cr.execute("""
                INSERT INTO planilla_disability_payslip_rel
                    (disability_id, payslip_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
            """, (dis_id, payslip_id))
            migrated += 1

    if migrated:
        _logger.info(
            'planilla_cr._migrate_disability_payslip_m2m: '
            'migradas %s relaciones disability->payslip al nuevo M2M.', migrated
        )
    else:
        _logger.info(
            'planilla_cr._migrate_disability_payslip_m2m: '
            'sin datos nuevos que migrar (ya actualizado o primera instalacion).'
        )


def _migrate_leave_cr_payslip_m2m(env):
    """
    FIX v5.28.75: Migrar planilla_leave_cr.payslip_id (Many2one)
    a la nueva tabla M2M planilla_leave_cr_payslip_rel.
    Mismo patron que _migrate_disability_payslip_m2m.
    """
    import logging
    _logger = logging.getLogger(__name__)

    env.cr.execute("""
        SELECT COUNT(*) FROM information_schema.tables
        WHERE table_name = 'planilla_leave_cr_payslip_rel'
    """)
    if not env.cr.fetchone():
        _logger.warning('planilla_cr: tabla leave_cr M2M no existe aun -- saltando.')
        return

    env.cr.execute("""
        SELECT id, payslip_id FROM planilla_leave_cr
        WHERE payslip_id IS NOT NULL
    """)
    rows = env.cr.fetchall()
    migrated = 0
    for leave_id, payslip_id in rows:
        env.cr.execute("""
            INSERT INTO planilla_leave_cr_payslip_rel (leave_cr_id, payslip_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
        """, (leave_id, payslip_id))
        if env.cr.rowcount:
            migrated += 1

    if migrated:
        _logger.info(
            'planilla_cr._migrate_leave_cr_payslip_m2m: '
            'migradas %s relaciones leave_cr->payslip al M2M.', migrated
        )


def _remove_legacy_menu_items(env):
    """
    Elimina items de menu obsoletos de versiones anteriores del modulo.
    Se ejecuta en cada -u planilla_cr para limpiar menus huerfanos en la BD.

    - "Constancia Laboral" en Accion de Personal: se accede desde la ficha
      del empleado (boton imprimir), no desde el menu de acciones.
    """
    import logging
    _logger = logging.getLogger(__name__)

    # Nombres de menus a desactivar del menu principal de planilla
    menus_to_remove = [
        'Constancia Laboral',
        'Constancia Salarial',
    ]

    removed = 0
    for menu_name in menus_to_remove:
        menus = env['ir.ui.menu'].search([
            ('name', '=', menu_name),
            ('parent_id.name', 'in', ['Accion de Personal', 'Sistema Planilla']),
            ('active', '=', True),
        ])
        if menus:
            menus.write({'active': False})
            removed += len(menus)
            _logger.info(
                'planilla_cr._remove_legacy_menu_items: '
                'desactivado menu "%s" (ids: %s)', menu_name, menus.ids
            )

    if not removed:
        _logger.info(
            'planilla_cr._remove_legacy_menu_items: '
            'no se encontraron menus obsoletos activos.'
        )


def _ensure_schedule_types(env):
    """
    Garantiza que los horarios de medio tiempo existan para cada empresa.
    Se ejecuta en instalacion y en cada migracion (-u planilla_cr).
    Los horarios de tiempo parcial requieren is_part_time=True para que
    la validacion de salario minimo MTSS los exima correctamente.
    """
    companies = env['res.company'].search([])
    ScheduleType = env['planilla.schedule.type']

    # days_per_week y hours_per_week son ahora campos computados; se derivan
    # automaticamente de los booleanos de dias y hours_per_day.
    # Los booleanos se definen aqui explicitamente para que el calculo sea correcto.
    PART_TIME_SCHEDULES = [
        {
            'code':           'MEDI',
            'name':           'Medio Tiempo (4 horas)',
            'hours_per_day':  4.0,
            'overtime_factor': 1.5,
            'is_part_time':   True,
            'lunes': True, 'martes': True, 'miercoles': True,
            'jueves': True, 'viernes': True, 'sabado': False, 'domingo': False,
            'description':    'Jornada a tiempo parcial. Art. 136 CT. '
                              'Proporcional en salario, vacaciones y prestaciones.',
        },
        {
            'code':           'TRCR',
            'name':           'Tres Cuartos (6 horas)',
            'hours_per_day':  6.0,
            'overtime_factor': 1.5,
            'is_part_time':   True,
            'lunes': True, 'martes': True, 'miercoles': True,
            'jueves': True, 'viernes': True, 'sabado': False, 'domingo': False,
            'description':    'Jornada parcial de 6 horas diurnas. '
                              'Proporcional en salario, vacaciones y prestaciones segun CT.',
        },
    ]

    for company in companies:
        for sched_vals in PART_TIME_SCHEDULES:
            try:
                existing = ScheduleType.search([
                    ('code', '=', sched_vals['code']),
                    ('company_id', '=', company.id),
                ], limit=1)
            except Exception:
                existing = ScheduleType.search([
                    ('code', '=', sched_vals['code']),
                ], limit=1)
            if existing:
                if not existing.is_part_time:
                    existing.write({'is_part_time': True})
            else:
                create_vals = {k: v for k, v in sched_vals.items()}
                if 'company_id' in ScheduleType._fields:
                    create_vals['company_id'] = company.id
                ScheduleType.create(create_vals)


def _ensure_payroll_calendars(env):
    """
    Garantiza que las 3 calendarizaciones estandar (Mensual, Quincenal,
    Semanal) existan para CADA compania. Se ejecuta en instalacion y en
    cada migracion (-u planilla_cr).

    FIX BUG: data/default_data.xml crea estos 3 registros con noupdate="1"
    y sin especificar company_id -- quedan atados a la compania que
    estuviera activa (self.env.company) durante la instalacion inicial
    del modulo, normalmente la primera/demo. Como noupdate="1" impide que
    se vuelvan a ejecutar en actualizaciones, cualquier compania creada
    DESPUES de la instalacion (ej. un cliente nuevo) nunca recibe sus
    propias calendarizaciones -- el generador de machote de importacion
    las busca filtrando por company_id de esa compania y encuentra 0,
    aunque el usuario vea registros de OTRA compania en la lista si tiene
    varias companias activas en su sesion (confusion visual real que
    llevo a este bug a pasar desapercibido).
    """
    companies = env['res.company'].search([])
    Calendar = env['planilla.calendar']

    STANDARD_CALENDARS = [
        {'name': 'Mensual',   'frequency': 'monthly',  'payment_day': 30},
        {'name': 'Quincenal', 'frequency': 'biweekly', 'payment_day': 15,
         'second_payment_day': 30},
        {'name': 'Semanal',   'frequency': 'weekly',   'payment_day': 5},
    ]

    for company in companies:
        for cal_vals in STANDARD_CALENDARS:
            existing = Calendar.search([
                ('name', '=', cal_vals['name']),
                ('company_id', '=', company.id),
            ], limit=1)
            if existing:
                continue
            create_vals = dict(cal_vals)
            create_vals['company_id'] = company.id
            create_vals['active'] = True
            Calendar.create(create_vals)


def _fix_income_tax_bracket_company_scope(env):
    """
    Libera el company_id de los tramos de impuesto sobre la renta
    (Resolucion DGT-R-016-2026) para que apliquen a TODAS las
    companias por igual -- son un decreto nacional, identico para
    cualquier empresa de Costa Rica.

    FIX BUG (version anterior de este mismo hook): data/income_tax_data.xml
    crea los 10 tramos (5 de 2025 desactivados + 5 de 2026 activos) con
    IDs XML fijos y SIN especificar company_id. Como el campo tiene
    default=lambda self: self.env.company, los tramos quedaron atados a
    la UNICA compania activa durante la primera instalacion del modulo.

    CORRECCION DE ENFOQUE: una version anterior de este hook intentaba
    solucionar esto DUPLICANDO la tabla completa por compania -- un
    error de diseno, porque los tramos de renta no varian entre
    empresas costarricenses. La correccion correcta es la misma que
    para feriados/deducciones: liberar el registro (company_id=False)
    para que sea global. El propio codigo de calculo ya esperaba esto:
    payslip_compute_mixin.py y employee_termination.py buscan tramos con
    ('company_id', '=', company_actual) OR ('company_id', '=', False),
    y payslip_compute_mixin.py incluso tiene un fallback explicito
    comentado como "Cubre el caso donde los tramos fueron cargados con
    una company_id distinta" -- confirmando que la intencion de diseno
    original siempre fue que estos tramos fueran globales.

    Si una compania anterior a este fix ya tiene su PROPIA copia
    duplicada de los tramos 2026 (creada por la version anterior de
    este hook), esos duplicados se dejan intactos -- no se eliminan
    datos, solo se libera el/los tramo(s) originales que seguian
    atados a una sola empresa.
    """
    Bracket = env['planilla.income.tax.bracket']
    NOMBRES_TRAMOS_2026 = [
        'Exento 2026 -- hasta CRC918,000',
        '10% sobre el exceso de CRC918,000 hasta CRC1,347,000',
        '15% sobre el exceso de CRC1,347,000 hasta CRC2,364,000',
        '20% sobre el exceso de CRC2,364,000 hasta CRC4,727,000',
        '25% sobre el exceso de CRC4,727,000',
        # Nombres originales del XML (con simbolo de colones, por si el
        # registro nunca paso por la version anterior de este hook)
        'Exento 2026 \u2014 hasta \u20a1918,000',
        '10% sobre el exceso de \u20a1918,000 hasta \u20a11,347,000',
        '15% sobre el exceso de \u20a11,347,000 hasta \u20a12,364,000',
        '20% sobre el exceso de \u20a12,364,000 hasta \u20a14,727,000',
        '25% sobre el exceso de \u20a14,727,000',
    ]
    huerfanos = Bracket.sudo().search([
        ('company_id', '!=', False),
        ('year', '=', 2026),
        ('name', 'in', NOMBRES_TRAMOS_2026),
    ])
    for b in huerfanos:
        ya_global = Bracket.search([
            ('sequence', '=', b.sequence),
            ('year', '=', 2026),
            ('company_id', '=', False),
            ('id', '!=', b.id),
        ], limit=1)
        if ya_global:
            continue  # ya hay una version global de este tramo, no duplicar
        b.write({'company_id': False})


def _fix_minimum_salary_company_scope(env):
    """
    Libera el company_id de los salarios minimos MTSS (Decreto vigente
    enero 2026) para que apliquen a TODAS las companias por igual --
    son un decreto nacional del Ministerio de Trabajo, identico para
    cualquier empresa de Costa Rica.

    CORRECCION DE ENFOQUE (mismo caso que tramos de renta, ver
    _fix_income_tax_bracket_company_scope): una version anterior de
    este hook duplicaba las 5 categorias por compania -- error de
    diseno, porque los salarios minimos MTSS no varian entre empresas.
    get_current_minimum() (ver models/minimum_salary.py) ya filtra con
    ('company_id', '=', compania_actual) OR ('company_id', '=', False),
    exactamente el mismo patron que el resto de catalogos globales del
    modulo (feriados, codigos de deduccion, tramos de renta).

    Si alguna compania ya tiene su propia copia duplicada (creada por
    la version anterior de este hook), esos duplicados se dejan
    intactos -- solo se libera el/los registro(s) que seguian atados
    a una sola empresa.
    """
    MinSalary = env['planilla.minimum.salary']
    CATEGORIAS_2026 = [
        'Trabajador no calificado generico',
        'Trabajador semicalificado',
        'Trabajador calificado',
        'Tecnico de nivel medio',
        'Universitario con titulo',
    ]
    huerfanos = MinSalary.sudo().search([
        ('company_id', '!=', False),
        ('category', 'in', CATEGORIAS_2026),
        ('valid_from', '=', '2026-01-01'),
    ])
    for m in huerfanos:
        ya_global = MinSalary.search([
            ('category', '=', m.category),
            ('valid_from', '=', m.valid_from),
            ('company_id', '=', False),
            ('id', '!=', m.id),
        ], limit=1)
        if ya_global:
            continue  # ya hay una version global de esta categoria, no duplicar
        m.write({'company_id': False})


def _ensure_default_branch(env):
    """
    Garantiza que cada compania tenga al menos una sucursal (la
    sucursal "Principal" de data/default_data.xml, que se creo sin
    company_id explicito y quedo atada a una sola compania por el mismo
    patron de bug que las calendarizaciones). De menor severidad que
    los tramos de renta o salarios minimos (no rompe ningun calculo
    legal, solo deja el campo Sucursal vacio en companias nuevas), pero
    se corrige por consistencia.
    """
    companies = env['res.company'].search([])
    Branch = env['planilla.branch']
    for company in companies:
        if Branch.search_count([('company_id', '=', company.id)]):
            continue
        Branch.create({
            'code': 'PRINCIPAL',
            'name': 'Principal',
            'company_id': company.id,
            'active': True,
        })


def _ensure_deduction_codes(env):
    """
    Garantiza que los codigos de deduccion estandar existan, como
    codigos GLOBALES (sin compania asignada), disponibles para todas las
    empresas por igual. Se ejecuta en cada migracion para agregar nuevos
    codigos sin perder los existentes.

    FIX BUG: antes se creaban con DeductionCode.create(vals) sin
    especificar company_id -- el campo tiene
    default=lambda self: self.env.company, asi que cada codigo quedaba
    atado por accidente a la UNICA compania activa durante la primera
    migracion que corrio esto, a pesar de que el propio texto de ayuda
    del campo dice explicitamente "Deje vacio para que aplique a todas
    las empresas (codigo global)". Companias creadas despues de esa
    primera migracion nunca veian estos codigos disponibles. Mismo
    patron de bug que los feriados nacionales (ver
    _fix_holiday_company_scope) y que las calendarizaciones de planilla
    (ver _ensure_payroll_calendars) -- aqui la correccion es la misma
    que en feriados: mantener el registro global (company_id=False) en
    vez de duplicarlo por compania, porque son los mismos codigos para
    todas las empresas del cliente.
    """
    standard_codes = [
        {'code': 'CCSS',     'name': 'CCSS Obrero',                   'deduction_type': 'employee'},
        {'code': 'RENTA',    'name': 'Impuesto sobre la Renta',        'deduction_type': 'employee'},
        {'code': 'PENSION',  'name': 'Pension Alimentaria',            'deduction_type': 'employee'},
        {'code': 'PRESTAMO', 'name': 'Cuota de Prestamo',              'deduction_type': 'employee'},
        {'code': 'EMBARGO',  'name': 'Embargo Judicial',               'deduction_type': 'employee'},
        {'code': 'SINDICAL', 'name': 'Cuota Sindical',                 'deduction_type': 'employee'},
        {'code': 'COOP',     'name': 'Cuota Cooperativa',              'deduction_type': 'employee'},
        {'code': 'AUSENCIA', 'name': 'Ausencia Sin Goce de Sueldo',    'deduction_type': 'employee'},
        {'code': 'SEGURO',   'name': 'Poliza / Seguro Voluntario',     'deduction_type': 'employee'},
        {'code': 'AHORRO',   'name': 'Ahorro Voluntario',              'deduction_type': 'employee'},
    ]
    DeductionCode = env['planilla.deduction.code']
    for vals in standard_codes:
        existing = DeductionCode.search([('code', '=', vals['code'])], limit=1)
        if not existing:
            create_vals = dict(vals)
            create_vals['company_id'] = False  # global explicito, no depender del default
            DeductionCode.create(create_vals)
        elif existing.company_id:
            # Ya existe pero quedo atado a una compania por el bug
            # anterior -- liberarlo para que vuelva a ser global, salvo
            # que ya exista otro codigo global con el mismo code (poco
            # probable dado el unique constraint esperado, pero se
            # verifica por seguridad antes de escribir).
            ya_global = DeductionCode.search([
                ('code', '=', vals['code']),
                ('company_id', '=', False),
                ('id', '!=', existing.id),
            ], limit=1)
            if not ya_global:
                existing.write({'company_id': False})

    # -- Liberar TODOS los codigos huerfanos, no solo los 10 de arriba --
    # data/deduction_code_data.xml tiene 17 codigos, data/leave_cr_data.xml
    # agrega 2 mas, y data/charge_type_data.xml agrega 1 mas
    # (COBRO_EMP) -- 20 en total repartidos en 3 archivos distintos. La
    # lista fija de arriba solo cubria 10. Este paso generico corrige
    # CUALQUIER planilla.deduction.code que haya quedado atado a una
    # compania por el mismo bug, sin depender de mantener una lista
    # actualizada cada vez que se agregue un codigo nuevo en cualquier
    # archivo de datos del modulo.
    for code_rec in DeductionCode.search([('company_id', '!=', False)]):
        ya_global = DeductionCode.search([
            ('code', '=', code_rec.code),
            ('company_id', '=', False),
            ('id', '!=', code_rec.id),
        ], limit=1)
        if not ya_global:
            code_rec.write({'company_id': False})

    # -- Mismo tratamiento generico para Tipos de Cobro al Empleado --
    # (data/charge_type_data.xml, 8 registros, mismo bug de company_id).
    # planilla.charge.type es un modelo propio de este modulo (definido
    # en models/employee_charge.py) -- siempre disponible aqui, sin
    # necesitar el patron defensivo env.get() usado para modelos de
    # OTROS modulos opcionales como nombramientos_cr.
    ChargeType = env['planilla.charge.type']
    for charge_rec in ChargeType.search([('company_id', '!=', False)]):
        ya_global = ChargeType.search([
            ('code', '=', charge_rec.code),
            ('company_id', '=', False),
            ('id', '!=', charge_rec.id),
        ], limit=1)
        if not ya_global:
            charge_rec.write({'company_id': False})


def _fix_employee_document_type_company_scope(env):
    """
    Mismo patron de bug que _ensure_deduction_codes, aplicado a
    planilla.employee.document.type (ver data/employee_document_type_data.xml):
    los 9 tipos de documento estandar se crean sin company_id explicito
    y quedan atados a una sola compania por el default del campo.
    """
    DocType = env['planilla.employee.document.type']
    huerfanos = DocType.sudo().search([('company_id', '!=', False)])
    for d in huerfanos:
        ya_global = DocType.search([
            ('name', '=', d.name),
            ('company_id', '=', False),
            ('id', '!=', d.id),
        ], limit=1)
        if not ya_global:
            d.write({'company_id': False})


def _setup_accounting_config(env):
    """
    Crea o actualiza la configuracion contable por defecto.
    - Si no existe: la crea con todas las cuentas estandar CR.
    - Si existe pero tiene campos vacios: rellena solo los vacios.
    Se ejecuta tanto en instalacion (post_init_hook) como en actualizacion.

    FIX AUDIT-01: ahora CREA las cuentas si no existen en el plan contable,
    en lugar de simplemente buscarlas y dejarlas vacias. Esto garantiza que
    la configuracion quede 100% completa desde la instalacion.
    """
    # Mapa campo -> (codigo, nombre, tipo_odoo19)
    ACCOUNT_MAP = {
        'account_salary_expense':              ('630000', 'Sueldos y Salarios',                          'expense'),
        'account_social_charges_expense':      ('630100', 'Cargas Sociales Patronales (CCSS+INS)',       'expense'),
        'account_vacation_expense':            ('630200', 'Provision para Vacaciones',                   'expense'),
        'account_aguinaldo_expense':           ('630300', 'Provision para Aguinaldo',                    'expense'),
        'account_cesantia_expense':            ('630400', 'Provision para Cesantia / Auxilio',           'expense'),
        'account_preaviso_expense':            ('630500', 'Gasto por Preaviso',                          'expense'),
        'account_bono_expense':                ('630600', 'Bonos e Incentivos al Personal',              'expense'),
        'account_subsidio_expense':            ('630700', 'Subsidios al Personal (Transporte/Alim.)',    'expense'),
        'account_licencia_expense':            ('630800', 'Licencias y Permisos con Goce',               'expense'),
        'account_salary_payable':              ('230000', 'Salarios por Pagar',                          'liability_current'),
        'account_income_tax_payable':          ('230100', 'Retencion de Renta por Pagar',               'liability_current'),
        'account_ccss_payable':                ('230300', 'CCSS por Pagar (Obrero + Patronal)',          'liability_current'),
        'account_ins_payable':                 ('230400', 'INS por Pagar (Riesgos del Trabajo)',         'liability_current'),
        'account_aguinaldo_provision':         ('230500', 'Provision Aguinaldo por Pagar',               'liability_current'),
        'account_cesantia_provision':          ('230600', 'Provision Cesantia por Pagar',                'liability_current'),
        'account_vacation_provision':          ('230700', 'Provision Vacaciones por Pagar',              'liability_current'),
        'account_termination_payable':         ('230800', 'Liquidaciones por Pagar',                     'liability_current'),
        'account_loans_payable':               ('230900', 'Cuotas Prestamos Retenidos por Pagar',        'liability_current'),
        'account_rop_payable':                 ('230350', 'ROP por Pagar (Obrero+Patronal)',             'liability_current'),
        'account_pension_alimentaria_payable': ('230950', 'Pensiones Alimentarias por Pagar',            'liability_current'),
        'account_embargo_payable':             ('230960', 'Embargos Judiciales por Pagar',               'liability_current'),
        'account_cobro_empleado_payable':      ('230970', 'Cobros al Empleado por Liquidar',             'liability_current'),
        'account_loans_receivable':            ('115000', 'Prestamos a Empleados por Cobrar',            'asset_current'),
        'account_ccss_subsidy_receivable':     ('120500', 'Subsidio CCSS por Cobrar',                    'asset_current'),
    }

    def get_or_create_account(code, name, acc_type):
        """Busca la cuenta por codigo. Si no existe, la crea."""
        account = env['account.account'].search([
            ('code', '=', code),
            ('company_ids', 'in', env.company.id),
        ], limit=1)
        if not account:
            account = env['account.account'].create({
                'code': code,
                'name': name,
                'account_type': acc_type,
                'company_ids': [(4, env.company.id)],
            })
        return account

    # Buscar o crear diario de planilla
    journal = env['account.journal'].search([
        ('type', 'in', ['general', 'purchase']),
        '|', '|',
        ('name', 'ilike', 'salario'),
        ('name', 'ilike', 'nomina'),
        ('name', 'ilike', 'planilla'),
        ('company_id', '=', env.company.id),
    ], limit=1)
    if not journal:
        journal = env['account.journal'].search([
            ('type', '=', 'general'),
            ('company_id', '=', env.company.id),
        ], limit=1)
    if not journal:
        journal = env['account.journal'].create({
            'name': 'Planilla de Salarios',
            'code': 'PLAN',
            'type': 'general',
            'company_id': env.company.id,
        })

    existing = env['planilla.accounting.config'].search([
        ('company_id', '=', env.company.id)
    ], limit=1)

    if not existing:
        # -- Crear config nueva con TODAS las cuentas --------------
        # Default calendar: Quincenal (biweekly)
        default_cal = env['planilla.calendar'].search(
            [('frequency', '=', 'biweekly')], limit=1)

        vals = {
            'company_id': env.company.id,
            'accounting_entry_mode': 'per_run',
            'journal_id': journal.id,
        }
        if default_cal:
            vals['default_payroll_calendar_id'] = default_cal.id
        for field_name, (code, name, acc_type) in ACCOUNT_MAP.items():
            account = get_or_create_account(code, name, acc_type)
            vals[field_name] = account.id

        # Copiar preferencias de usuario de otra empresa si existe
        # para que los checks configurables no queden en default inesperado
        other_config = env['planilla.accounting.config'].search([], limit=1)
        user_pref_fields = [
            'show_vacation_on_payslip',
            'overtime_fixed_8h',
            'skip_ccss_on_termination',
            'accrual_method',
        ]
        if other_config:
            for f in user_pref_fields:
                if hasattr(other_config, f):
                    vals[f] = getattr(other_config, f)

        env['planilla.accounting.config'].create(vals)
    else:
        # -- Actualizar config existente: solo rellenar campos vacios --
        vals = {}
        if not existing.journal_id:
            vals['journal_id'] = journal.id
        for field_name, (code, name, acc_type) in ACCOUNT_MAP.items():
            if not getattr(existing, field_name):
                account = get_or_create_account(code, name, acc_type)
                vals[field_name] = account.id
        if vals:
            existing.write(vals)




def _populate_schedule_defaults(env):
    """Pobla hora_entrada, hora_salida y días laborales en tipos de horario
    existentes que aun no tienen configurados los dias booleanos.

    Se ejecuta en post_migrate; no sobreescribe registros que ya tienen
    al menos un dia marcado. Sirve de fallback para horarios creados antes
    de que existiera la funcionalidad de dias booleanos.

    NOTA: days_per_week y hours_per_week son ahora campos computados derivados
    de los booleanos de dias. Esta funcion ya no puede usar sch.days_per_week
    para inferir los dias porque ese valor es 0 cuando todos los booleanos
    estan en False. En su lugar usa el campo 'code' para determinar el patron
    de dias de cada horario.
    """
    import logging
    _logger = logging.getLogger(__name__)

    # Mapeo codigo (fragmento) -> dias a activar.
    # El orden importa: se evalua el primero que coincida con el codigo.
    # Para horarios sin codigo reconocido se aplica el default Lun-Vie.
    CODE_DAYS_MAP = [
        ('FDSM',  {'sabado': True, 'domingo': True}),
        ('COMP6', {'lunes':True,'martes':True,'miercoles':True,
                   'jueves':True,'viernes':True,'sabado':True}),
        ('NOCT',  {'lunes':True,'martes':True,'miercoles':True,
                   'jueves':True,'viernes':True,'sabado':True}),
        ('MIXT',  {'lunes':True,'martes':True,'miercoles':True,
                   'jueves':True,'viernes':True,'sabado':True}),
        ('ACU4',  {'lunes':True,'martes':True,'miercoles':True,'jueves':True}),
        ('ACU3',  {'lunes':True,'martes':True,'miercoles':True}),
        ('GRD24', {'lunes':True,'miercoles':True,'viernes':True}),
        ('PRM',   {'martes':True,'miercoles':True,'jueves':True,
                   'viernes':True,'sabado':True}),
        ('PROM',  {'martes':True,'miercoles':True,'jueves':True,
                   'viernes':True,'sabado':True}),
    ]
    DEFAULT_DAYS = {'lunes':True,'martes':True,'miercoles':True,
                    'jueves':True,'viernes':True}

    schedules = env['planilla.schedule.type'].sudo().search([])
    updated = 0
    for sch in schedules:
        vals = {}
        code = (sch.code or '').upper()
        hpd  = sch.hours_per_day or 8.0

        # Hora entrada/salida: poner defaults si no estan configuradas
        if not sch.hora_entrada and not sch.hora_salida:
            if 'NOCT' in code:
                vals['hora_entrada'] = 22.0
                vals['hora_salida']  = 4.0
            else:
                vals['hora_entrada'] = 8.0
                vals['hora_salida']  = 8.0 + hpd

        # Dias laborales: configurar solo si todos los booleanos estan en False.
        # Usa el codigo del horario para determinar el patron correcto.
        day_fields = ['lunes','martes','miercoles','jueves','viernes','sabado','domingo']
        if not any(getattr(sch, d, False) for d in day_fields):
            day_vals = None
            for fragment, days in CODE_DAYS_MAP:
                if fragment in code:
                    day_vals = days
                    break
            if day_vals is None:
                day_vals = DEFAULT_DAYS
            vals.update(day_vals)

        if vals:
            sch.write(vals)
            updated += 1

    if updated:
        _logger.info('planilla_cr: %d tipos de horario actualizados con dias/horas', updated)


def _setup_all_companies_config(env):
    """Llama _setup_accounting_config para cada empresa del sistema."""
    all_companies = env['res.company'].sudo().search([])
    for company in all_companies:
        _company_env = env['res.company'].with_company(company).env
        _setup_accounting_config(_company_env)
    _repair_cross_company_accounts(env)


def _repair_cross_company_accounts(env):
    """
    Repara configuraciones contables cuyas cuentas (account.account) NO
    pertenecen a la empresa de la configuracion. Esto ocurre con datos
    heredados de antes del soporte multi-empresa, donde una cuenta con
    el mismo codigo se reutilizo entre empresas sin estar correctamente
    asociada a company_ids.

    Para cada empresa, para cada campo de cuenta en la config, si la
    cuenta actual no incluye esa empresa en su company_ids, se busca o
    crea una cuenta CON ESE CODIGO correctamente asociada y se reasigna.
    """
    import logging
    _logger = logging.getLogger(__name__)
    Account = env['account.account'].sudo()
    Config  = env['planilla.accounting.config'].sudo()

    # Mismo mapa que en _setup_accounting_config (duplicado para evitar
    # depender de variables locales de esa funcion).
    ACCOUNT_MAP = {
        'account_salary_expense':              ('630000', 'Sueldos y Salarios',                          'expense'),
        'account_social_charges_expense':      ('630100', 'Cargas Sociales Patronales (CCSS+INS)',       'expense'),
        'account_vacation_expense':            ('630200', 'Provision para Vacaciones',                   'expense'),
        'account_aguinaldo_expense':           ('630300', 'Provision para Aguinaldo',                    'expense'),
        'account_cesantia_expense':            ('630400', 'Provision para Cesantia / Auxilio',           'expense'),
        'account_preaviso_expense':            ('630500', 'Gasto por Preaviso',                          'expense'),
        'account_bono_expense':                ('630600', 'Bonos e Incentivos al Personal',              'expense'),
        'account_subsidio_expense':            ('630700', 'Subsidios al Personal (Transporte/Alim.)',    'expense'),
        'account_licencia_expense':            ('630800', 'Licencias y Permisos con Goce',               'expense'),
        'account_salary_payable':              ('230000', 'Salarios por Pagar',                          'liability_current'),
        'account_income_tax_payable':          ('230100', 'Retencion de Renta por Pagar',               'liability_current'),
        'account_ccss_payable':                ('230300', 'CCSS por Pagar (Obrero + Patronal)',          'liability_current'),
        'account_ins_payable':                 ('230400', 'INS por Pagar (Riesgos del Trabajo)',         'liability_current'),
        'account_aguinaldo_provision':         ('230500', 'Provision Aguinaldo por Pagar',               'liability_current'),
        'account_cesantia_provision':          ('230600', 'Provision Cesantia por Pagar',                'liability_current'),
        'account_vacation_provision':          ('230700', 'Provision Vacaciones por Pagar',              'liability_current'),
        'account_termination_payable':         ('230800', 'Liquidaciones por Pagar',                     'liability_current'),
        'account_loans_payable':               ('230900', 'Cuotas Prestamos Retenidos por Pagar',        'liability_current'),
        'account_rop_payable':                 ('230350', 'ROP por Pagar (Obrero+Patronal)',             'liability_current'),
        'account_pension_alimentaria_payable': ('230950', 'Pensiones Alimentarias por Pagar',            'liability_current'),
        'account_embargo_payable':             ('230960', 'Embargos Judiciales por Pagar',               'liability_current'),
        'account_cobro_empleado_payable':      ('230970', 'Cobros al Empleado por Liquidar',             'liability_current'),
        'account_loans_receivable':            ('115000', 'Prestamos a Empleados por Cobrar',            'asset_current'),
        'account_ccss_subsidy_receivable':     ('120500', 'Subsidio CCSS por Cobrar',                    'asset_current'),
    }

    configs = Config.search([])
    fixed_count = 0
    for cfg in configs:
        company = cfg.company_id
        if not company:
            continue
        for field_name, (code, name, acc_type) in ACCOUNT_MAP.items():
            current = getattr(cfg, field_name, False)
            if current and company.id in current.company_ids.ids:
                continue  # ya esta bien asociada
            # Buscar cuenta correcta para ESTA empresa
            correct = Account.search([
                ('code', '=', code),
                ('company_ids', 'in', company.id),
            ], limit=1)
            if not correct:
                # Si existe una cuenta con ese codigo pero de otra empresa,
                # usar un codigo alternativo para no chocar con la unicidad
                # del codigo dentro del mismo plan contable compartido.
                same_code_other_co = Account.search([('code', '=', code)], limit=1)
                use_code = code
                if same_code_other_co:
                    use_code = f'{code}.{company.id}'
                correct = Account.create({
                    'code': use_code,
                    'name': name,
                    'account_type': acc_type,
                    'company_ids': [(4, company.id)],
                })
            cfg.sudo().write({field_name: correct.id})
            fixed_count += 1

    # --- Reparar journal_id (el diario, igual que las cuentas, pertenece
    #     a UNA sola empresa -- no es Many2many como account.account) ---
    Journal = env['account.journal'].sudo()
    journal_fixed = 0
    for cfg in configs:
        company = cfg.company_id
        if not company:
            continue
        jrn = cfg.journal_id
        if jrn and jrn.company_id.id == company.id:
            continue  # ya esta bien
        # Buscar diario de planilla ya existente para esta empresa
        correct_jrn = Journal.search([
            ('company_id', '=', company.id),
            ('type', 'in', ['general', 'purchase']),
            '|', '|',
            ('name', 'ilike', 'salario'),
            ('name', 'ilike', 'nomina'),
            ('name', 'ilike', 'planilla'),
        ], limit=1)
        if not correct_jrn:
            correct_jrn = Journal.search([
                ('company_id', '=', company.id),
                ('type', '=', 'general'),
            ], limit=1)
        if not correct_jrn:
            # Crear uno nuevo para esta empresa, con codigo unico
            base_code = 'PLAN'
            use_code = base_code
            suffix = 1
            while Journal.search([('code', '=', use_code), ('company_id', '=', company.id)], limit=1):
                use_code = f'{base_code}{suffix}'
                suffix += 1
            correct_jrn = Journal.create({
                'name': 'Planilla de Salarios',
                'code': use_code,
                'type': 'general',
                'company_id': company.id,
            })
        cfg.sudo().write({'journal_id': correct_jrn.id})
        journal_fixed += 1

    if fixed_count or journal_fixed:
        _logger.info(
            'planilla_cr._repair_cross_company_accounts: %d cuenta(s) y %d diario(s) '
            'reasignados a su empresa correcta.', fixed_count, journal_fixed
        )


def _create_email_templates(env):
    """Create mail templates programmatically to avoid XML schema issues in Odoo 19."""
    IrModel = env['ir.model']
    Template = env['mail.template']

    templates = [
        {
            'xml_id': 'planilla_cr.email_template_payslip_paid',
            'model': 'planilla.payslip.cr',
            'name': 'Planilla CR -- Boleta de Pago',
            'subject': 'Su boleta de pago esta disponible -- {{ object.date_from }} al {{ object.date_to }}',
            'email_to': '{{ object.employee_id.work_email }}',
            'body_html': """
<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;">
  <div style="background:#1F4E79;color:white;padding:16px 20px;border-radius:6px 6px 0 0;">
    <h2 style="margin:0;font-size:18px;">Su Boleta de Pago esta disponible</h2>
    <p style="margin:6px 0 0;opacity:.9;font-size:13px;"><t t-out="object.company_id.name"/></p>
  </div>
  <div style="background:#F8FAFC;padding:20px;border:1px solid #E2E8F0;border-top:none;border-radius:0 0 6px 6px;">
    <p>Estimado/a <strong><t t-out="object.employee_id.name"/></strong>,</p>
    <p>Su boleta de pago del periodo <strong><t t-out="str(object.date_from)"/></strong>
       al <strong><t t-out="str(object.date_to)"/></strong> ha sido procesada.</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0;">
      <tr style="background:#1F4E79;color:white;">
        <td style="padding:8px;font-weight:bold;">Concepto</td>
        <td style="padding:8px;font-weight:bold;text-align:right;">Monto (CRC)</td>
      </tr>
      <tr><td style="padding:8px;border-bottom:1px solid #E2E8F0;color:#555;">Salario Base</td>
          <td style="padding:8px;text-align:right;border-bottom:1px solid #E2E8F0;">
            <t t-out="'{:,.2f}'.format(object.base_salary)"/></td></tr>
      <t t-if="object.overtime_amount &gt; 0">
      <tr><td style="padding:8px;border-bottom:1px solid #E2E8F0;color:#555;">Horas Extras</td>
          <td style="padding:8px;text-align:right;border-bottom:1px solid #E2E8F0;">
            <t t-out="'{:,.2f}'.format(object.overtime_amount)"/></td></tr>
      </t>
      <t t-if="object.bono_salarial_amount &gt; 0">
      <tr><td style="padding:8px;border-bottom:1px solid #E2E8F0;color:#1a7f45;">&#43; Bonos e Incentivos</td>
          <td style="padding:8px;text-align:right;border-bottom:1px solid #E2E8F0;color:#1a7f45;">
            <t t-out="'{:,.2f}'.format(object.bono_salarial_amount)"/></td></tr>
      </t>
      <tr style="background:#EBF5FB;">
        <td style="padding:8px;font-weight:bold;">Salario Bruto</td>
        <td style="padding:8px;text-align:right;font-weight:bold;">
          <t t-out="'{:,.2f}'.format(object.gross_salary)"/></td></tr>
      <tr><td style="padding:8px;border-bottom:1px solid #E2E8F0;color:#555;">&#8722; CCSS Obrero (10.83%)</td>
          <td style="padding:8px;text-align:right;border-bottom:1px solid #E2E8F0;color:#C0392B;">
            <t t-out="'{:,.2f}'.format(object.ccss_employee)"/></td></tr>
      <tr><td style="padding:8px;border-bottom:1px solid #E2E8F0;color:#555;">&#8722; Impuesto de Renta</td>
          <td style="padding:8px;text-align:right;border-bottom:1px solid #E2E8F0;color:#C0392B;">
            <t t-out="'{:,.2f}'.format(object.income_tax)"/></td></tr>
      <t t-foreach="object.deduction_line_ids.filtered(lambda l: l.deduction_category == 'pension_alimentaria')" t-as="l">
      <tr><td style="padding:8px;border-bottom:1px solid #E2E8F0;color:#8E44AD;">&#8722; <t t-out="l.description or 'Pension Alimentaria'"/></td>
          <td style="padding:8px;text-align:right;border-bottom:1px solid #E2E8F0;color:#8E44AD;">
            <t t-out="'{:,.2f}'.format(l.amount)"/></td></tr>
      </t>
      <t t-foreach="object.deduction_line_ids.filtered(lambda l: l.deduction_category == 'embargo')" t-as="l">
      <tr><td style="padding:8px;border-bottom:1px solid #E2E8F0;color:#8E44AD;">&#8722; <t t-out="l.description or 'Embargo Judicial'"/></td>
          <td style="padding:8px;text-align:right;border-bottom:1px solid #E2E8F0;color:#8E44AD;">
            <t t-out="'{:,.2f}'.format(l.amount)"/></td></tr>
      </t>
      <t t-foreach="object.deduction_line_ids.filtered(lambda l: l.deduction_category == 'loan')" t-as="l">
      <tr><td style="padding:8px;border-bottom:1px solid #E2E8F0;color:#555;">&#8722; <t t-out="l.description or 'Prestamo'"/></td>
          <td style="padding:8px;text-align:right;border-bottom:1px solid #E2E8F0;color:#C0392B;">
            <t t-out="'{:,.2f}'.format(l.amount)"/></td></tr>
      </t>
      <tr style="background:#F0FBF4;">
        <td style="padding:8px;font-weight:bold;color:#27AE60;">Salario a Depositar (Neto)</td>
        <td style="padding:8px;text-align:right;font-weight:bold;color:#27AE60;">
          &#8353; <t t-out="'{:,.2f}'.format(object.salary_payable)"/></td></tr>
    </table>
    <p style="color:#888;font-size:11px;">Generado automaticamente por Planilla CR -- Odoo 19.
       Este correo es informativo. Para consultas contacte a Recursos Humanos.</p>
  </div>
</div>""",
        },
        {
            'xml_id': 'planilla_cr.email_template_loan_paid',
            'model': 'planilla.employee.loan',
            'name': 'Planilla CR -- Prestamo Cancelado',
            'subject': 'Su prestamo ha sido cancelado -- {{ object.name }}',
            'email_to': '{{ object.employee_id.work_email }}',
            'body_html': """
<div style="font-family:Arial,sans-serif;max-width:520px;margin:auto;padding:20px;">
  <div style="background:#27AE60;color:white;padding:14px 18px;border-radius:6px 6px 0 0;">
    <h3 style="margin:0;">Prestamo Cancelado</h3>
  </div>
  <div style="padding:18px;border:1px solid #ddd;border-top:none;border-radius:0 0 6px 6px;">
    <p>Estimado/a <strong><t t-out="object.employee_id.name"/></strong>,</p>
    <p>Su prestamo <strong><t t-out="object.name"/></strong> por
       CRC <t t-out="'{:,.2f}'.format(object.amount_total)"/> ha sido cancelado exitosamente.</p>
    <p style="color:#666;font-size:11px;">Generado automaticamente por Planilla CR.</p>
  </div>
</div>""",
        },
        {
            'xml_id': 'planilla_cr.email_template_anniversary',
            'model': 'hr.employee',
            'name': 'Planilla CR -- Aniversario Laboral',
            'subject': 'Feliz Aniversario Laboral -- {{ object.name }}',
            'email_to': '{{ object.work_email }}',
            'body_html': """
<div style="font-family:Arial,sans-serif;max-width:520px;margin:auto;padding:20px;">
  <div style="background:#E67E22;color:white;padding:14px 18px;border-radius:6px 6px 0 0;">
    <h3 style="margin:0;"> Aniversario Laboral</h3>
  </div>
  <div style="padding:18px;border:1px solid #ddd;border-top:none;border-radius:0 0 6px 6px;">
    <p>Estimado/a <strong><t t-out="object.name"/></strong>,</p>
    <p>Hoy celebramos su aniversario laboral. Gracias por su dedicacion y compromiso!</p>
    <p style="color:#666;font-size:11px;">Generado automaticamente por Planilla CR.</p>
  </div>
</div>""",
        },
        {
            'xml_id': 'planilla_cr.email_template_salary_authorized',
            'model': 'planilla.salary.history',
            'name': 'Planilla CR -- Cambio Salarial Autorizado',
            'subject': 'Actualizacion Salarial -- {{ object.employee_id.name }}',
            'email_to': '{{ object.employee_id.work_email }}',
            'body_html': """
<div style="font-family:Arial,sans-serif;max-width:520px;margin:auto;border:1px solid #ddd;border-radius:6px;overflow:hidden;">
  <div style="background:#1F4E79;padding:16px 20px;">
    <h3 style="color:white;margin:0;">Actualizacion Salarial</h3>
  </div>
  <div style="padding:20px;">
    <p>Estimado/a <strong><t t-out="object.employee_id.name"/></strong>,</p>
    <p>Su salario ha sido actualizado a partir del <strong><t t-out="str(object.effective_date)"/></strong>.</p>
    <table style="width:100%;border-collapse:collapse;margin:12px 0;">
      <tr style="background:#F0F4F8;">
        <td style="padding:8px;font-weight:bold;">Nuevo salario bruto:</td>
        <td style="padding:8px;color:#1F4E79;font-weight:bold;">
          CRC <t t-out="'{:,.2f}'.format(object.gross_salary)"/></td></tr>
      <tr>
        <td style="padding:8px;font-weight:bold;">Motivo:</td>
        <td style="padding:8px;"><t t-out="object.reason or 'Ajuste salarial'"/></td></tr>
      <tr style="background:#F0F4F8;">
        <td style="padding:8px;font-weight:bold;">Autorizado por:</td>
        <td style="padding:8px;">
          <t t-out="object.authorized_by.name if object.authorized_by else ''"/></td></tr>
    </table>
    <p style="color:#666;font-size:11px;">Generado automaticamente por Planilla CR -- Odoo 19.</p>
  </div>
</div>""",
        },
    ]

    IrModelData = env['ir.model.data']
    for tpl in templates:
        xml_id = tpl.pop('xml_id')
        module, name = xml_id.split('.')
        model_name = tpl.pop('model')
        model = IrModel.search([('model', '=', model_name)], limit=1)
        if not model:
            continue
        tpl['model_id'] = model.id
        tpl['auto_delete'] = True

        # Check if already exists
        existing = IrModelData.search([('module', '=', module), ('name', '=', name)])
        if existing:
            record = Template.browse(existing.res_id)
            if record.exists():
                continue  # already created, skip (noupdate behavior)

        record = Template.create(tpl)
        IrModelData.create({
            'module': module, 'name': name,
            'model': 'mail.template', 'res_id': record.id,
            'noupdate': True,
        })



def _ensure_missing_columns(env):
    """
    Crea columnas que pueden faltar en BDs existentes cuando Odoo no las
    agrega automaticamente en la actualizacion del modulo.
    Usa ADD COLUMN IF NOT EXISTS para ser idempotente.
    IMPORTANTE: los campos configurables por usuario (como show_vacation_on_payslip)
    NO se agregan aqui para evitar resetearlos. Odoo ORM los maneja directamente.
    """
    import logging
    _logger = logging.getLogger(__name__)

    columns = [
        # (tabla, columna, tipo_sql, default_sql)
        ('hr_employee', 'vacation_last_anniversary_year', 'INTEGER', '0'),
        ('hr_employee', 'vacation_balance_alert',         'BOOLEAN', 'FALSE'),
        ('hr_employee', 'vacation_days_accrued',          'NUMERIC', '0'),
        ('hr_employee', 'vacation_days_taken',            'NUMERIC', '0'),
        ('hr_employee', 'vacation_days_available',        'NUMERIC', '0'),
        ('hr_employee', 'vacation_initial_balance',       'NUMERIC', '0'),
    ]

    for table, column, col_type, default in columns:
        env.cr.execute("""
            ALTER TABLE %(table)s
            ADD COLUMN IF NOT EXISTS %(column)s %(type)s DEFAULT %(default)s
        """ % {'table': table, 'column': column,
               'type': col_type, 'default': default})

    env.cr.execute("SELECT 1")  # flush
    _logger.info('planilla_cr._ensure_missing_columns: verificacion completada.')
