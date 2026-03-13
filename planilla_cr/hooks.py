from odoo import api, SUPERUSER_ID


def post_init_hook(env):
    _create_email_templates(env)
    _setup_accounting_config(env)


def post_migrate_hook(env):
    """
    L3 FIX — Hook de migración entre versiones.
    Se ejecuta automáticamente al hacer -u planilla_cr.
    Garantiza que la configuración contable esté actualizada
    con las cuentas nuevas agregadas en cada versión.
    """
    _setup_accounting_config(env)
    _ensure_deduction_codes(env)


def _ensure_deduction_codes(env):
    """
    Garantiza que los códigos de deducción estándar existan.
    Se ejecuta en cada migración para agregar nuevos códigos sin perder los existentes.
    """
    standard_codes = [
        {'code': 'CCSS',     'name': 'CCSS Obrero',                   'deduction_type': 'employee'},
        {'code': 'RENTA',    'name': 'Impuesto sobre la Renta',        'deduction_type': 'employee'},
        {'code': 'PENSION',  'name': 'Pensión Alimentaria',            'deduction_type': 'employee'},
        {'code': 'PRESTAMO', 'name': 'Cuota de Préstamo',              'deduction_type': 'employee'},
        {'code': 'EMBARGO',  'name': 'Embargo Judicial',               'deduction_type': 'employee'},
        {'code': 'SINDICAL', 'name': 'Cuota Sindical',                 'deduction_type': 'employee'},
        {'code': 'COOP',     'name': 'Cuota Cooperativa',              'deduction_type': 'employee'},
        {'code': 'AUSENCIA', 'name': 'Ausencia Sin Goce de Sueldo',    'deduction_type': 'employee'},
        {'code': 'SEGURO',   'name': 'Póliza / Seguro Voluntario',     'deduction_type': 'employee'},
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
    Crea o actualiza la configuración contable por defecto.
    - Si no existe: la crea con todas las cuentas estándar CR.
    - Si existe pero tiene campos vacíos: rellena solo los vacíos.
    Se ejecuta tanto en instalación (post_init_hook) como en actualización.
    """
    def get_account(code):
        return env['account.account'].search([
            ('code', '=', code),
            ('company_ids', 'in', env.company.id),
        ], limit=1)

    # Mapa campo → código de cuenta estándar CR (16 cuentas — v49)
    ACCOUNT_MAP = {
        'account_salary_expense':         '630000',
        'account_social_charges_expense': '630100',
        'account_vacation_expense':       '630200',
        'account_aguinaldo_expense':      '630300',
        'account_cesantia_expense':       '630400',
        'account_preaviso_expense':       '630500',
        'account_salary_payable':         '230000',
        'account_income_tax_payable':     '230100',
        'account_ccss_payable':           '230300',
        'account_ins_payable':            '230400',
        'account_aguinaldo_provision':    '230500',
        'account_cesantia_provision':     '230600',
        'account_vacation_provision':     '230700',
        'account_termination_payable':         '230800',
        'account_loans_payable':               '230900',
        # FIX v49 Bug 5 — Cuenta para subsidio CCSS por cobrar (activo corriente 120500)
        'account_ccss_subsidy_receivable':     '120500',
    }

    # Buscar diario de planilla
    journal = env['account.journal'].search([
        ('type', 'in', ['general', 'purchase']),
        ('name', 'ilike', 'salario'),
        ('company_id', '=', env.company.id),
    ], limit=1)
    if not journal:
        journal = env['account.journal'].search([
            ('type', 'in', ['general', 'purchase']),
            ('name', 'ilike', 'nomina'),
            ('company_id', '=', env.company.id),
        ], limit=1)
    if not journal:
        journal = env['account.journal'].search([
            ('type', '=', 'general'),
            ('company_id', '=', env.company.id),
        ], limit=1)

    existing = env['planilla.accounting.config'].search([
        ('company_id', '=', env.company.id)
    ], limit=1)

    if not existing:
        # ── Crear config nueva ─────────────────────────────────────
        vals = {
            'company_id': env.company.id,
            'accounting_entry_mode': 'per_employee',
        }
        if journal:
            vals['journal_id'] = journal.id
        for field_name, code in ACCOUNT_MAP.items():
            account = get_account(code)
            if account:
                vals[field_name] = account.id
        env['planilla.accounting.config'].create(vals)
    else:
        # ── Actualizar config existente: solo rellenar campos vacíos ──
        vals = {}
        if not existing.journal_id and journal:
            vals['journal_id'] = journal.id
        for field_name, code in ACCOUNT_MAP.items():
            if not getattr(existing, field_name):
                account = get_account(code)
                if account:
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
            'name': 'Planilla CR — Boleta de Pago',
            'subject': 'Su boleta de pago está disponible — {{ object.date_from }} al {{ object.date_to }}',
            'email_to': '{{ object.employee_id.work_email }}',
            'body_html': """
<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;">
  <div style="background:#1F4E79;color:white;padding:16px 20px;border-radius:6px 6px 0 0;">
    <h2 style="margin:0;font-size:18px;">Su Boleta de Pago está disponible</h2>
    <p style="margin:6px 0 0;opacity:.9;font-size:13px;"><t t-out="object.company_id.name"/></p>
  </div>
  <div style="background:#F8FAFC;padding:20px;border:1px solid #E2E8F0;border-top:none;border-radius:0 0 6px 6px;">
    <p>Estimado/a <strong><t t-out="object.employee_id.name"/></strong>,</p>
    <p>Su boleta de pago del período <strong><t t-out="str(object.date_from)"/></strong>
       al <strong><t t-out="str(object.date_to)"/></strong> ha sido procesada.</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0;">
      <tr style="background:#1F4E79;color:white;">
        <td style="padding:8px;font-weight:bold;">Concepto</td>
        <td style="padding:8px;font-weight:bold;text-align:right;">Monto (₡)</td>
      </tr>
      <tr><td style="padding:8px;border-bottom:1px solid #E2E8F0;">Salario Bruto</td>
          <td style="padding:8px;text-align:right;border-bottom:1px solid #E2E8F0;">
            <t t-out="'{:,.2f}'.format(object.gross_salary)"/></td></tr>
      <tr><td style="padding:8px;border-bottom:1px solid #E2E8F0;">Deducciones</td>
          <td style="padding:8px;text-align:right;border-bottom:1px solid #E2E8F0;">
            <t t-out="'{:,.2f}'.format(object.total_employee_deductions)"/></td></tr>
      <tr style="background:#F0FBF4;">
        <td style="padding:8px;font-weight:bold;color:#27AE60;">Salario Neto</td>
        <td style="padding:8px;text-align:right;font-weight:bold;color:#27AE60;">
          ₡ <t t-out="'{:,.2f}'.format(object.net_salary)"/></td></tr>
    </table>
    <p style="color:#666;font-size:11px;">Generado automáticamente por Planilla CR — Odoo 19.</p>
  </div>
</div>""",
        },
        {
            'xml_id': 'planilla_cr.email_template_loan_paid',
            'model': 'planilla.employee.loan',
            'name': 'Planilla CR — Préstamo Cancelado',
            'subject': 'Su préstamo ha sido cancelado — {{ object.name }}',
            'email_to': '{{ object.employee_id.work_email }}',
            'body_html': """
<div style="font-family:Arial,sans-serif;max-width:520px;margin:auto;padding:20px;">
  <div style="background:#27AE60;color:white;padding:14px 18px;border-radius:6px 6px 0 0;">
    <h3 style="margin:0;">Préstamo Cancelado</h3>
  </div>
  <div style="padding:18px;border:1px solid #ddd;border-top:none;border-radius:0 0 6px 6px;">
    <p>Estimado/a <strong><t t-out="object.employee_id.name"/></strong>,</p>
    <p>Su préstamo <strong><t t-out="object.name"/></strong> por
       ₡ <t t-out="'{:,.2f}'.format(object.amount_total)"/> ha sido cancelado exitosamente.</p>
    <p style="color:#666;font-size:11px;">Generado automáticamente por Planilla CR.</p>
  </div>
</div>""",
        },
        {
            'xml_id': 'planilla_cr.email_template_anniversary',
            'model': 'hr.employee',
            'name': 'Planilla CR — Aniversario Laboral',
            'subject': 'Feliz Aniversario Laboral — {{ object.name }}',
            'email_to': '{{ object.work_email }}',
            'body_html': """
<div style="font-family:Arial,sans-serif;max-width:520px;margin:auto;padding:20px;">
  <div style="background:#E67E22;color:white;padding:14px 18px;border-radius:6px 6px 0 0;">
    <h3 style="margin:0;">🎉 Aniversario Laboral</h3>
  </div>
  <div style="padding:18px;border:1px solid #ddd;border-top:none;border-radius:0 0 6px 6px;">
    <p>Estimado/a <strong><t t-out="object.name"/></strong>,</p>
    <p>Hoy celebramos su aniversario laboral. ¡Gracias por su dedicación y compromiso!</p>
    <p style="color:#666;font-size:11px;">Generado automáticamente por Planilla CR.</p>
  </div>
</div>""",
        },
        {
            'xml_id': 'planilla_cr.email_template_salary_authorized',
            'model': 'planilla.salary.history',
            'name': 'Planilla CR — Cambio Salarial Autorizado',
            'subject': 'Actualización Salarial — {{ object.employee_id.name }}',
            'email_to': '{{ object.employee_id.work_email }}',
            'body_html': """
<div style="font-family:Arial,sans-serif;max-width:520px;margin:auto;border:1px solid #ddd;border-radius:6px;overflow:hidden;">
  <div style="background:#1F4E79;padding:16px 20px;">
    <h3 style="color:white;margin:0;">Actualización Salarial</h3>
  </div>
  <div style="padding:20px;">
    <p>Estimado/a <strong><t t-out="object.employee_id.name"/></strong>,</p>
    <p>Su salario ha sido actualizado a partir del <strong><t t-out="str(object.effective_date)"/></strong>.</p>
    <table style="width:100%;border-collapse:collapse;margin:12px 0;">
      <tr style="background:#F0F4F8;">
        <td style="padding:8px;font-weight:bold;">Nuevo salario bruto:</td>
        <td style="padding:8px;color:#1F4E79;font-weight:bold;">
          ₡ <t t-out="'{:,.2f}'.format(object.gross_salary)"/></td></tr>
      <tr>
        <td style="padding:8px;font-weight:bold;">Motivo:</td>
        <td style="padding:8px;"><t t-out="object.reason or 'Ajuste salarial'"/></td></tr>
      <tr style="background:#F0F4F8;">
        <td style="padding:8px;font-weight:bold;">Autorizado por:</td>
        <td style="padding:8px;">
          <t t-out="object.authorized_by.name if object.authorized_by else ''"/></td></tr>
    </table>
    <p style="color:#666;font-size:11px;">Generado automáticamente por Planilla CR — Odoo 19.</p>
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

