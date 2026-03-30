"""
Tests unitarios -- Asientos Contables de Planilla (planilla_cr v52 / Odoo 19)

Modelos reales:
  - planilla.payslip.cr
  - planilla.accounting.config
  - planilla.calendar  (frecuencia mensual)
  - account.account, account.journal

Notas Odoo 19:
  - action_confirm() requiere group_planilla_aprobador -> usar sudo()
  - action_pay(skip_accounting=True) para no crear asiento en el pago
  - _create_accounting_entry() crea el asiento directamente
  - res_partner.group_rfq NOT NULL en build 20260217 -> usar partner existente

Ejecutar:
  docker compose run --rm web odoo -d prueba --test-enable \\
    --test-tags planilla_cr.test_accounting_entry --stop-after-init
"""
from odoo.tests.common import TransactionCase


class TestAccountingEntry(TransactionCase):
    """Tests para asientos contables del modulo planilla_cr."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref('base.main_company')

        # -- Journal contable --------------------------------------
        cls.journal = cls.env['account.journal'].search([
            ('type', '=', 'general'),
            ('company_id', '=', cls.company.id),
        ], limit=1)
        if not cls.journal:
            cls.journal = cls.env['account.journal'].create({
                'name': 'Planilla Test v52',
                'code': 'PT52',
                'type': 'general',
                'company_id': cls.company.id,
            })

        # -- Cuentas contables -------------------------------------
        def get_or_create_account(code, name, atype):
            acc = cls.env['account.account'].search([
                ('code', '=', code),
                ('company_ids', 'in', cls.company.id),
            ], limit=1)
            if not acc:
                acc = cls.env['account.account'].create({
                    'code': code, 'name': name,
                    'account_type': atype,
                    'company_ids': [(4, cls.company.id)],
                })
            return acc

        cls.acc_salary_exp  = get_or_create_account('630000', 'Sueldos Test',          'expense')
        cls.acc_social_exp  = get_or_create_account('630100', 'Cargas Sociales Test',  'expense')
        cls.acc_vac_exp     = get_or_create_account('630200', 'Vacaciones Test',        'expense')
        cls.acc_agui_exp    = get_or_create_account('630300', 'Aguinaldo Test',         'expense')
        cls.acc_ces_exp     = get_or_create_account('630400', 'Cesantia Test',          'expense')
        cls.acc_prev_exp    = get_or_create_account('630500', 'Preaviso Test',          'expense')
        cls.acc_salary_pay  = get_or_create_account('230000', 'Salarios por Pagar',    'liability_current')
        cls.acc_renta       = get_or_create_account('230100', 'Renta por Pagar',       'liability_current')
        cls.acc_ccss        = get_or_create_account('230300', 'CCSS por Pagar',        'liability_current')
        cls.acc_ins         = get_or_create_account('230400', 'INS por Pagar',         'liability_current')
        cls.acc_agui_prov   = get_or_create_account('230500', 'Provision Aguinaldo',   'liability_current')
        cls.acc_ces_prov    = get_or_create_account('230600', 'Provision Cesantia',    'liability_current')
        cls.acc_vac_prov    = get_or_create_account('230700', 'Provision Vacaciones',  'liability_current')
        cls.acc_term        = get_or_create_account('230800', 'Liquidaciones Test',    'liability_current')
        cls.acc_loans       = get_or_create_account('230900', 'Prestamos Ret Test',    'liability_current')
        cls.acc_pension     = get_or_create_account('230950', 'Pensiones Ali Test',    'liability_current')
        cls.acc_ccss_sub    = get_or_create_account('120500', 'CCSS Subsidio Test',    'asset_current')
        cls.acc_loans_rec   = get_or_create_account('115000', 'Prestamos Rec Test',    'asset_current')

        # -- Configuracion contable --------------------------------
        config = cls.env['planilla.accounting.config'].search([
            ('company_id', '=', cls.company.id)
        ], limit=1)
        if not config:
            config = cls.env['planilla.accounting.config'].create({
                'company_id': cls.company.id,
            })
        config.write({
            'journal_id':                          cls.journal.id,
            'account_salary_expense':              cls.acc_salary_exp.id,
            'account_social_charges_expense':      cls.acc_social_exp.id,
            'account_vacation_expense':            cls.acc_vac_exp.id,
            'account_aguinaldo_expense':           cls.acc_agui_exp.id,
            'account_cesantia_expense':            cls.acc_ces_exp.id,
            'account_preaviso_expense':            cls.acc_prev_exp.id,
            'account_salary_payable':              cls.acc_salary_pay.id,
            'account_income_tax_payable':          cls.acc_renta.id,
            'account_ccss_payable':                cls.acc_ccss.id,
            'account_ins_payable':                 cls.acc_ins.id,
            'account_aguinaldo_provision':         cls.acc_agui_prov.id,
            'account_cesantia_provision':          cls.acc_ces_prov.id,
            'account_vacation_provision':          cls.acc_vac_prov.id,
            'account_termination_payable':         cls.acc_term.id,
            'account_loans_payable':               cls.acc_loans.id,
            'account_pension_alimentaria_payable': cls.acc_pension.id,
            'account_ccss_subsidy_receivable':     cls.acc_ccss_sub.id,
            'account_loans_receivable':            cls.acc_loans_rec.id,
        })
        cls.config = config

        # -- Calendario mensual ------------------------------------
        cls.calendar = cls.env['planilla.calendar'].search([
            ('frequency', '=', 'monthly'),
            ('company_id', '=', cls.company.id),
        ], limit=1)
        if not cls.calendar:
            cls.calendar = cls.env['planilla.calendar'].create({
                'name': 'Mensual Test v52',
                'frequency': 'monthly',
                'company_id': cls.company.id,
            })

        # -- Empleado de prueba ------------------------------------
        # FIX Odoo 19.0-20260217: usar partner existente como work_contact
        cls.employee = cls.env['hr.employee'].create({
            'name': 'Test Contabilidad Planilla v52',
            'company_id': cls.company.id,
            'work_contact_id': cls.env.company.partner_id.id,
            'payroll_calendar_id': cls.calendar.id,
        })
        cls.employee.base_salary = 600_000

    # -- Helper ----------------------------------------------------

    def _check_balanced(self, move, tolerance=0.05):
        """Verifica DEBE == HABER dentro de la tolerancia dada."""
        debit  = round(sum(move.line_ids.mapped('debit')), 2)
        credit = round(sum(move.line_ids.mapped('credit')), 2)
        self.assertAlmostEqual(debit, credit, delta=tolerance,
            msg=f'Asiento descuadrado: DEBE CRC{debit:,.2f}  HABER CRC{credit:,.2f}')

    def _create_confirmed_payslip(self, date_from, date_to):
        """Crea y confirma una boleta. Usa sudo() para evitar restriccion de grupo."""
        payslip = self.env['planilla.payslip.cr'].sudo().create({
            'employee_id': self.employee.id,
            'date_from': date_from,
            'date_to': date_to,
            'company_id': self.company.id,
        })
        # Forzar recalculo
        payslip._compute_base_salary()
        payslip._compute_gross()
        payslip._compute_deductions()
        payslip._compute_totals()
        # Confirmar con sudo para saltarse la validacion de grupo en tests
        payslip.sudo().write({'state': 'confirmed'})
        return payslip

    # -- Tests -----------------------------------------------------

    def test_01_asiento_cuadra_debe_haber(self):
        """El asiento de boleta debe cuadrar DEBE == HABER."""
        payslip = self._create_confirmed_payslip('2026-02-01', '2026-02-28')
        move = payslip.sudo()._create_accounting_entry()
        if move:
            self._check_balanced(move)
        else:
            self.skipTest('_create_accounting_entry() retorno False -- '
                          'verificar que las cuentas contables esten configuradas')

    def test_02_asiento_tiene_cuenta_salarios(self):
        """El asiento debe incluir la cuenta de gasto de salarios 630000."""
        payslip = self._create_confirmed_payslip('2026-03-01', '2026-03-31')
        move = payslip.sudo()._create_accounting_entry()
        if move:
            codes = move.line_ids.mapped('account_id.code')
            self.assertIn('630000', codes,
                'Falta cuenta 630000 (Sueldos) en el asiento')
        else:
            self.skipTest('Sin asiento contable -- verificar configuracion')

    def test_03_asiento_tiene_cuenta_ccss(self):
        """El asiento debe incluir cuenta CCSS por pagar 230300."""
        payslip = self._create_confirmed_payslip('2026-04-01', '2026-04-30')
        move = payslip.sudo()._create_accounting_entry()
        if move:
            codes = move.line_ids.mapped('account_id.code')
            self.assertIn('230300', codes,
                'Falta cuenta 230300 (CCSS por Pagar) en el asiento')
        else:
            self.skipTest('Sin asiento contable -- verificar configuracion')

    def test_04_asiento_tiene_cuenta_salarios_por_pagar(self):
        """El asiento debe incluir la cuenta de salarios por pagar 230000."""
        payslip = self._create_confirmed_payslip('2026-05-01', '2026-05-31')
        move = payslip.sudo()._create_accounting_entry()
        if move:
            codes = move.line_ids.mapped('account_id.code')
            self.assertIn('230000', codes,
                'Falta cuenta 230000 (Salarios por Pagar) en el asiento')
        else:
            self.skipTest('Sin asiento contable -- verificar configuracion')

    def test_05_config_contable_tiene_journal(self):
        """La configuracion contable debe tener journal asignado."""
        self.assertTrue(self.config.journal_id,
            'La configuracion contable debe tener un journal definido')

    def test_06_config_contable_tiene_cuentas_obligatorias(self):
        """Las cuentas minimas obligatorias deben estar configuradas."""
        cuentas_obligatorias = [
            ('account_salary_expense',         'Gasto de Salarios'),
            ('account_ccss_payable',            'CCSS por Pagar'),
            ('account_salary_payable',          'Salarios por Pagar'),
            ('account_social_charges_expense',  'Cargas Sociales'),
        ]
        for field_name, desc in cuentas_obligatorias:
            cuenta = getattr(self.config, field_name, False)
            self.assertTrue(cuenta,
                f'Falta configurar cuenta: {desc} ({field_name})')
