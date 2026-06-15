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


def post_init_hook(env):
    _create_email_templates(env)
    _setup_accounting_config(env)
    _ensure_schedule_types(env)
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
    # Use ALTER TABLE with no DEFAULT to avoid overwriting user's False setting
    env.cr.execute("""
        ALTER TABLE planilla_accounting_config
        ADD COLUMN IF NOT EXISTS show_vacation_on_payslip BOOLEAN;
        UPDATE planilla_accounting_config
        SET show_vacation_on_payslip = TRUE
        WHERE show_vacation_on_payslip IS NULL;
    """)
    try:
        from .models.migrate_codes import migrate_codes
        migrate_codes(env)
    except Exception as _e:
        import logging
        logging.getLogger(__name__).warning(
            'planilla_cr.migrate_codes: %s (no critico)', _e
        )
    _setup_accounting_config(env)
    _ensure_deduction_codes(env)
    _ensure_schedule_types(env)
    _fix_hour_license_date_end(env)
    _migrate_disability_payslip_m2m(env)
    _migrate_leave_cr_payslip_m2m(env)
    _remove_legacy_menu_items(env)


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

    PART_TIME_SCHEDULES = [
        {
            'code':           'MEDI',
            'name':           'Medio Tiempo (4 horas)',
            'hours_per_day':  4.0,
            'hours_per_week': 20.0,
            'days_per_week':  5,
            'overtime_factor': 1.5,
            'is_part_time':   True,
            'description':    'Jornada a tiempo parcial. Art. 136 CT. '
                              'Proporcional en salario, vacaciones y prestaciones.',
        },
        {
            'code':           'TRCR',
            'name':           'Tres Cuartos (6 horas)',
            'hours_per_day':  6.0,
            'hours_per_week': 30.0,
            'days_per_week':  5,
            'overtime_factor': 1.5,
            'is_part_time':   True,
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


def _ensure_deduction_codes(env):
    """
    Garantiza que los codigos de deduccion estandar existan.
    Se ejecuta en cada migracion para agregar nuevos codigos sin perder los existentes.
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
        if not DeductionCode.search([('code', '=', vals['code'])], limit=1):
            DeductionCode.create(vals)


    _create_email_templates(env)
    _setup_accounting_config(env)


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
