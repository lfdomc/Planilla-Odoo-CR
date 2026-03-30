from odoo import api, SUPERUSER_ID


def post_init_hook(env):
    _create_email_templates(env)
    _setup_accounting_config(env)


def post_migrate_hook(env):
    """
    L3 FIX -- Hook de migracion entre versiones.
    Se ejecuta automaticamente al hacer -u planilla_cr.
    Garantiza que la configuracion contable este actualizada
    con las cuentas nuevas agregadas en cada version.
    """
    _setup_accounting_config(env)
    _ensure_deduction_codes(env)


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
        vals = {
            'company_id': env.company.id,
            'accounting_entry_mode': 'per_employee',
            'journal_id': journal.id,
        }
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

