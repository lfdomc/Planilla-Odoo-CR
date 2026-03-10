from odoo import api, SUPERUSER_ID


def post_init_hook(env):
    """
    Crea la configuración contable por defecto usando las cuentas estándar
    que vienen con Odoo (o las que se crearon al importar el plan de cuentas CR).
    Se ejecuta una sola vez al instalar el módulo.
    """
    # Buscar diario de planilla — preferir uno que diga "salario" o "nómina"
    journal = env['account.journal'].search([
        ('type', 'in', ['general', 'purchase']),
        ('name', 'ilike', 'salario'),
        ('company_id', '=', env.company.id),
    ], limit=1)
    if not journal:
        journal = env['account.journal'].search([
            ('type', '=', 'general'),
            ('company_id', '=', env.company.id),
        ], limit=1)

    def get_account(code):
        return env['account.account'].search([
            ('code', '=', code),
            ('company_ids', 'in', env.company.id),
        ], limit=1)

    # Verificar si ya existe config
    existing = env['planilla.accounting.config'].search([
        ('company_id', '=', env.company.id)
    ], limit=1)
    if existing:
        return

    # Cuentas de gasto (Débitos)
    salary_expense        = get_account('630000')  # Gastos salariales
    social_charges_expense = get_account('630100')  # Cargas Sociales Patronales
    vacation_expense      = get_account('630200')  # Provisión Vacaciones
    aguinaldo_expense     = get_account('630300')  # Provisión Aguinaldo
    cesantia_expense      = get_account('630400')  # Provisión Cesantía

    # Cuentas por pagar (Créditos)
    ccss_payable          = get_account('230300')  # CCSS por Pagar
    ins_payable           = get_account('230400')  # INS por Pagar
    income_tax_payable    = get_account('230100')  # Retención Renta
    aguinaldo_provision   = get_account('230500')  # Provisión Aguinaldo
    cesantia_provision    = get_account('230600')  # Provisión Cesantía
    vacation_provision    = get_account('230700')  # Provisión Vacaciones
    salary_payable        = get_account('230000')  # Salarios por Pagar

    vals = {
        'company_id': env.company.id,
        'accounting_entry_mode': 'per_employee',
    }
    if journal:
        vals['journal_id'] = journal.id
    if salary_expense:
        vals['account_salary_expense'] = salary_expense.id
    if social_charges_expense:
        vals['account_social_charges_expense'] = social_charges_expense.id
    if vacation_expense:
        vals['account_vacation_expense'] = vacation_expense.id
    if aguinaldo_expense:
        vals['account_aguinaldo_expense'] = aguinaldo_expense.id
    if cesantia_expense:
        vals['account_cesantia_expense'] = cesantia_expense.id
    if ccss_payable:
        vals['account_ccss_payable'] = ccss_payable.id
    if ins_payable:
        vals['account_ins_payable'] = ins_payable.id
    if income_tax_payable:
        vals['account_income_tax_payable'] = income_tax_payable.id
    if aguinaldo_provision:
        vals['account_aguinaldo_provision'] = aguinaldo_provision.id
    if cesantia_provision:
        vals['account_cesantia_provision'] = cesantia_provision.id
    if vacation_provision:
        vals['account_vacation_provision'] = vacation_provision.id
    if salary_payable:
        vals['account_salary_payable'] = salary_payable.id

    env['planilla.accounting.config'].create(vals)
