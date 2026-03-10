from odoo import models, fields, api


class PayrollAccountingConfig(models.Model):
    """Configuración de cuentas contables para planilla CR.
    Una configuración por compañía.
    """
    _name = 'planilla.accounting.config'
    _description = 'Configuración Contable de Planilla'
    _rec_name = 'company_id'

    company_id = fields.Many2one(
        'res.company', string='Compañía',
        required=True, default=lambda self: self.env.company,
        ondelete='cascade'
    )

    # ── Modo de generación de asientos ─────────────────────────────
    accounting_entry_mode = fields.Selection([
        ('per_employee', 'Por Empleado (un asiento por boleta)'),
        ('per_run', 'Por Planilla (un asiento consolidado por planilla)'),
    ], string='Modo de Asiento Contable',
        default='per_employee', required=True,
        help='Define si se genera un asiento contable por cada boleta de pago '
             'o un único asiento consolidado por planilla.'
    )

    # ── Diario ──────────────────────────────────────────────────────
    journal_id = fields.Many2one(
        'account.journal', string='Diario de Planilla',
        domain=[('type', 'in', ['general', 'purchase'])],
        help='Diario contable donde se registran los asientos de planilla.'
    )

    # ══════════════════════════════════════════════════════════════
    # CUENTAS DE GASTO (DÉBITO)
    # Estos son los que aparecen en "Gastos Administrativos" en el asiento
    # ══════════════════════════════════════════════════════════════
    account_salary_expense = fields.Many2one(
        'account.account', string='Salarios (Gasto)',
        help='DÉBITO — Salario bruto del empleado.\nEj: 5.1.01 Sueldos y Salarios'
    )
    account_social_charges_expense = fields.Many2one(
        'account.account', string='Cargas Sociales Patronales (Gasto)',
        help='DÉBITO — CCSS Patronal (26.83%) + INS (1%).\nEj: 5.1.02 Cargas Sociales'
    )
    account_vacation_expense = fields.Many2one(
        'account.account', string='Vacaciones (Gasto)',
        help='DÉBITO — Provisión de vacaciones (4.16%).\nEj: 5.1.03 Vacaciones'
    )
    account_aguinaldo_expense = fields.Many2one(
        'account.account', string='Aguinaldo (Gasto)',
        help='DÉBITO — Provisión aguinaldo (8.33%).\nEj: 5.1.04 Aguinaldo'
    )
    account_cesantia_expense = fields.Many2one(
        'account.account', string='Cesantía / Multa CCSS (Gasto)',
        help='DÉBITO — Provisión de auxilio de cesantía (5.33%).\nEj: 5.1.05 Auxilio de Cesantía'
    )

    # ══════════════════════════════════════════════════════════════
    # CUENTAS POR PAGAR (CRÉDITO)
    # ══════════════════════════════════════════════════════════════
    account_ccss_payable = fields.Many2one(
        'account.account', string='Deducciones CCSS por Pagar (Cuota Obrera + Patronal)',
        help='CRÉDITO — Total CCSS a pagar: Cuota Obrera (10.83%) + Cuota Patronal (26.83%).\nEj: 2.1.01 CCSS por Pagar'
    )
    account_ins_payable = fields.Many2one(
        'account.account', string='INS por Pagar',
        help='CRÉDITO — INS Riesgos del Trabajo (~1%).\nEj: 2.1.02 INS por Pagar'
    )
    account_income_tax_payable = fields.Many2one(
        'account.account', string='Retención Renta por Pagar',
        help='CRÉDITO — Impuesto de renta retenido al empleado.\nEj: 2.1.03 Renta Retenida por Pagar'
    )
    account_aguinaldo_provision = fields.Many2one(
        'account.account', string='Provisión Aguinaldo por Pagar',
        help='CRÉDITO — Pasivo acumulado de aguinaldo.\nEj: 2.1.04 Provisión para Aguinaldo'
    )
    account_cesantia_provision = fields.Many2one(
        'account.account', string='Provisión Cesantía por Pagar',
        help='CRÉDITO — Pasivo acumulado de cesantía.\nEj: 2.1.05 Provisión para Cesantía'
    )
    account_vacation_provision = fields.Many2one(
        'account.account', string='Provisión Vacaciones por Pagar',
        help='CRÉDITO — Pasivo acumulado de vacaciones.\nEj: 2.1.06 Provisión para Vacaciones'
    )
    account_salary_payable = fields.Many2one(
        'account.account', string='Salarios por Pagar',
        help='CRÉDITO — Salario neto pendiente de pago al empleado.\nEj: 2.1.07 Salarios por Pagar'
    )
    # ── Liquidaciones ────────────────────────────────────────────────
    account_preaviso_expense = fields.Many2one(
        'account.account', string='Gasto Preaviso',
        help='DÉBITO — Gasto por preaviso en liquidación.\nEj: 5.1.08 Gasto Preaviso'
    )
    account_termination_payable = fields.Many2one(
        'account.account', string='Liquidaciones por Pagar',
        help='CRÉDITO — Pasivo por liquidaciones pendientes de pago.\nEj: 2.1.08 Liquidaciones por Pagar'
    )

    @api.model
    def get_config(self, company_id=None):
        company = company_id or self.env.company.id
        config = self.search([('company_id', '=', company)], limit=1)
        return config

    @api.model
    def _ensure_default_config(self):
        """Llamado en post_init y post_update para garantizar config por defecto."""
        from odoo.addons.planilla_cr.hooks import post_init_hook
        post_init_hook(self.env)
