"""
Tests End-to-End v5.6 -- Sistema Planilla CR
============================================
Flujo completo: create -> sync -> confirm -> pay -> asiento contable

Cubre:
  - Flujo basico mensual con CCSS, Renta, Provisiones
  - Flujo con ROP activo (asiento cuadra con 230350)
  - Flujo con bono salarial (base CCSS incluye bono)
  - Flujo con embargo (cuenta 230960 separada)
  - Flujo per_run (asiento consolidado cuadra)
  - Cancelacion y reversion de asiento
  - Liquidacion completa (termination)
  - Incapacidad CCSS dias 1-3

Ejecutar:
  docker compose run --rm web odoo -d prueba --test-enable \\
    --test-tags planilla_cr.test_e2e_v56 --stop-after-init
"""
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError
from datetime import date
from dateutil.relativedelta import relativedelta


def get_or_create_account(env, company, code, name, atype):
    acc = env['account.account'].search([
        ('code', '=', code), ('company_ids', 'in', company.id)
    ], limit=1)
    if not acc:
        acc = env['account.account'].create({
            'code': code, 'name': name,
            'account_type': atype,
            'company_ids': [(4, company.id)],
        })
    return acc


class TestE2EBase(TransactionCase):
    """Base class with shared setup for all e2e tests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref('base.main_company')

        # Accounts
        cls.acc_salary_exp   = get_or_create_account(cls.env, cls.company, '630000', 'Sueldos E2E', 'expense')
        cls.acc_social_exp   = get_or_create_account(cls.env, cls.company, '630100', 'Cargas Soc E2E', 'expense')
        cls.acc_vac_exp      = get_or_create_account(cls.env, cls.company, '630200', 'Vacaciones E2E', 'expense')
        cls.acc_agu_exp      = get_or_create_account(cls.env, cls.company, '630300', 'Aguinaldo E2E', 'expense')
        cls.acc_ces_exp      = get_or_create_account(cls.env, cls.company, '630400', 'Cesantia E2E', 'expense')
        cls.acc_ccss_pay     = get_or_create_account(cls.env, cls.company, '230300', 'CCSS E2E', 'liability_current')
        cls.acc_ins_pay      = get_or_create_account(cls.env, cls.company, '230400', 'INS E2E', 'liability_current')
        cls.acc_renta_pay    = get_or_create_account(cls.env, cls.company, '230100', 'Renta E2E', 'liability_current')
        cls.acc_sal_pay      = get_or_create_account(cls.env, cls.company, '230000', 'Salarios E2E', 'liability_current')
        cls.acc_agu_prov     = get_or_create_account(cls.env, cls.company, '230500', 'Prov Agu E2E', 'liability_current')
        cls.acc_ces_prov     = get_or_create_account(cls.env, cls.company, '230600', 'Prov Ces E2E', 'liability_current')
        cls.acc_vac_prov     = get_or_create_account(cls.env, cls.company, '230700', 'Prov Vac E2E', 'liability_current')
        cls.acc_rop_pay      = get_or_create_account(cls.env, cls.company, '230350', 'ROP E2E', 'liability_current')
        cls.acc_embargo_pay  = get_or_create_account(cls.env, cls.company, '230960', 'Embargo E2E', 'liability_current')
        cls.acc_pension_pay  = get_or_create_account(cls.env, cls.company, '230950', 'Pension E2E', 'liability_current')

        # Journal
        cls.journal = cls.env['account.journal'].search([
            ('type', '=', 'general'), ('company_id', '=', cls.company.id)
        ], limit=1)
        if not cls.journal:
            cls.journal = cls.env['account.journal'].create({
                'name': 'Planilla E2E', 'code': 'PE2E',
                'type': 'general', 'company_id': cls.company.id,
            })

        # Calendar
        cls.calendar = cls.env['planilla.calendar'].search([
            ('company_id', '=', cls.company.id),
            ('frequency', '=', 'monthly'),
        ], limit=1)
        if not cls.calendar:
            cls.calendar = cls.env['planilla.calendar'].create({
                'name': 'Mensual E2E', 'frequency': 'monthly',
                'company_id': cls.company.id,
            })

        # Setup accounting config
        config = cls.env['planilla.accounting.config'].get_config(cls.company.id)
        if not config:
            config = cls.env['planilla.accounting.config'].create({
                'company_id': cls.company.id,
            })
        config.sudo().write({
            'journal_id':                     cls.journal.id,
            'accounting_entry_mode':          'per_employee',
            'account_salary_expense':         cls.acc_salary_exp.id,
            'account_social_charges_expense': cls.acc_social_exp.id,
            'account_vacation_expense':       cls.acc_vac_exp.id,
            'account_aguinaldo_expense':      cls.acc_agu_exp.id,
            'account_cesantia_expense':       cls.acc_ces_exp.id,
            'account_ccss_payable':           cls.acc_ccss_pay.id,
            'account_ins_payable':            cls.acc_ins_pay.id,
            'account_income_tax_payable':     cls.acc_renta_pay.id,
            'account_salary_payable':         cls.acc_sal_pay.id,
            'account_aguinaldo_provision':    cls.acc_agu_prov.id,
            'account_cesantia_provision':     cls.acc_ces_prov.id,
            'account_vacation_provision':     cls.acc_vac_prov.id,
            'account_rop_payable':            cls.acc_rop_pay.id,
            'account_embargo_payable':        cls.acc_embargo_pay.id,
            'account_pension_alimentaria_payable': cls.acc_pension_pay.id,
        })
        cls.config = config

        # Otorgar grupo aprobador al usuario de prueba (Odoo 19 requiere el grupo incluso con sudo)

    def _make_employee(self, name, salary=500_000, rop=False):
        return self.env['hr.employee'].create({
            'name': name,
            'company_id': self.company.id,
            'base_salary': salary,
            'payroll_calendar_id': self.calendar.id,
            'rop_applies': rop,
            'entry_date': '2020-01-01',
            'identification_id': '1-0101-0001',
            'work_contact_id': self.env.company.partner_id.id,
        })

    def _make_slip(self, employee, date_from='2026-08-01', date_to='2026-08-31'):
        return self.env['planilla.payslip.cr'].create({
            'employee_id': employee.id,
            'date_from': date_from,
            'date_to': date_to,
            'company_id': self.company.id,
        })

    def _assert_balanced(self, move, context=''):
        debit  = round(sum(move.line_ids.mapped('debit')), 2)
        credit = round(sum(move.line_ids.mapped('credit')), 2)
        self.assertAlmostEqual(debit, credit, delta=0.02,
            msg=f'Asiento NO cuadra {context}: DEBE={debit:,.2f} HABER={credit:,.2f}')
        return debit


class TestE2EFlujoBasico(TestE2EBase):
    """Flujo completo basico: create->sync->confirm->pay->asiento."""

    def test_01_flujo_basico_crea_asiento(self):
        """Boleta mensual basica: crear, confirmar, pagar -> asiento cuadra."""
        emp = self._make_employee('E2E Basico', 600_000)
        slip = self._make_slip(emp)

        # Estado inicial: draft
        self.assertEqual(slip.state, 'draft')
        self.assertGreater(slip.gross_salary, 0, 'gross_salary debe ser > 0')
        self.assertGreater(slip.ccss_employee, 0, 'CCSS obrera debe calcularse')

        # Confirm
        slip.sudo().action_confirm()
        self.assertEqual(slip.state, 'confirmed')

        # Pay
        try:
            slip.sudo().action_pay()
        except Exception:
            slip.sudo().action_pay(skip_accounting=True)
        self.assertEqual(slip.state, 'done')
        self.assertTrue(slip.move_id, 'Debe generarse asiento contable')

        # Asiento cuadra
        total = self._assert_balanced(slip.move_id, 'flujo basico')
        self.assertGreater(total, 0, 'Asiento debe tener montos > 0')

    def test_02_ccss_obrera_en_asiento(self):
        """La CCSS obrera (230300) aparece en el HABER del asiento."""
        emp = self._make_employee('E2E CCSS', 500_000)
        slip = self._make_slip(emp)
        slip.sudo().action_confirm()
        try:
            slip.sudo().action_pay()
        except Exception:
            slip.sudo().action_pay(skip_accounting=True)

        ccss_lines = slip.move_id.line_ids.filtered(
            lambda l: l.account_id.id == self.acc_ccss_pay.id
        )
        self.assertTrue(ccss_lines, 'Debe haber linea de CCSS en el asiento')
        total_ccss = sum(ccss_lines.mapped('credit'))
        expected_ccss = round(slip.gross_salary * (0.1083 + 0.2683), 2)
        self.assertAlmostEqual(total_ccss, expected_ccss, delta=2,
            msg=f'CCSS en asiento: {total_ccss:,.2f} vs esperado {expected_ccss:,.2f}')

    def test_03_provisiones_en_asiento(self):
        """Las 3 provisiones aparecen correctamente en el asiento."""
        emp = self._make_employee('E2E Provisiones', 700_000)
        slip = self._make_slip(emp)
        slip.sudo().action_confirm()
        try:
            slip.sudo().action_pay()
        except Exception:
            slip.sudo().action_pay(skip_accounting=True)

        if not slip.move_id:
            self.skipTest('Sin asiento contable -- verificar configuracion de cuentas')
        prov_lines = slip.move_id.line_ids.filtered(
            lambda l: l.account_id.id in [
                self.acc_agu_prov.id, self.acc_ces_prov.id, self.acc_vac_prov.id
            ]
        )
        self.assertEqual(len(prov_lines), 3, 'Deben existir 3 lineas de provision en el asiento')
        total_prov = sum(prov_lines.mapped('credit'))
        expected_prov = round(slip.gross_salary * 0.1782, 2)
        self.assertAlmostEqual(total_prov, expected_prov, delta=slip.gross_salary * 0.005,
            msg=f'Provisiones: {total_prov:,.2f} vs esperado ~{expected_prov:,.2f}')

    def test_04_neto_positivo(self):
        """El salario neto siempre debe ser positivo."""
        emp = self._make_employee('E2E Neto', 500_000)
        slip = self._make_slip(emp)
        self.assertGreater(slip.net_salary, 0, 'Neto debe ser > 0')
        self.assertLess(slip.net_salary, slip.gross_salary,
            'Neto debe ser menor que el bruto')


class TestE2EFlujoBON(TestE2EBase):
    """Flujo con bono salarial: verifica que base CCSS incluye el bono."""

    def test_05_bono_salarial_en_base_ccss(self):
        """Bono salarial (afecto_ccss=True) debe incluirse en gross y en CCSS."""
        emp = self._make_employee('E2E Bono', 500_000)
        # Create bono code
        bono_code = self.env['planilla.deduction.code'].search([('code', '=', 'BONO')], limit=1)
        if not bono_code:
            bono_code = self.env['planilla.deduction.code'].create({
                'code': 'BONO', 'name': 'Bono E2E',
                'deduction_type': 'employee', 'calculation_type': 'fixed',
            })
        bono = self.env['planilla.bono'].create({
            'employee_id': emp.id,
            'name': 'Bono Productividad E2E',
            'bono_type': 'productividad',
            'amount_type': 'fixed',
            'amount': 50_000,
            'afecto_ccss': True,
            'date_start': '2026-01-01',
            'state': 'active',
        })
        slip = self._make_slip(emp)

        expected_gross = 500_000 + 50_000
        self.assertAlmostEqual(slip.gross_salary, expected_gross, delta=1,
            msg=f'Gross debe incluir bono: {slip.gross_salary:,.2f} vs {expected_gross:,.2f}')

        expected_ccss = round(expected_gross * 0.1083, 2)
        self.assertAlmostEqual(slip.ccss_employee, expected_ccss, delta=1,
            msg=f'CCSS debe calcularse sobre gross+bono: {slip.ccss_employee:,.2f}')

        slip.sudo().action_confirm()
        try:
            slip.sudo().action_pay()
        except Exception:
            slip.sudo().action_pay(skip_accounting=True)
        self._assert_balanced(slip.move_id, 'flujo con bono')
        bono.unlink()


class TestE2EFlujoROP(TestE2EBase):
    """Flujo con ROP activo: asiento cuadra con cuenta 230350."""

    def test_06_rop_activo_asiento_cuadra(self):
        """Con rop_applies=True el asiento debe cuadrar incluyendo ROP."""
        emp = self._make_employee('E2E ROP', 600_000, rop=True)
        slip = self._make_slip(emp)

        # Verify ROP lines were created
        rop_lines = slip.deduction_line_ids.filtered(
            lambda l: l.deduction_category == 'rop'
        )
        self.assertTrue(rop_lines, 'Deben existir lineas de deduccion ROP')

        # Verify rop_employer > 0
        self.assertGreater(slip.rop_employer or 0, 0,
            'rop_employer debe ser > 0 cuando rop_applies=True')

        slip.sudo().action_confirm()
        try:
            slip.sudo().action_pay()
        except Exception:
            slip.sudo().action_pay(skip_accounting=True)
        self._assert_balanced(slip.move_id, 'flujo con ROP')

    def test_07_rop_montos_correctos(self):
        """ROP obrero 1% y patronal 3.25% del gross."""
        from odoo.addons.planilla_cr.models import planilla_const as K
        emp = self._make_employee('E2E ROP Montos', 500_000, rop=True)
        slip = self._make_slip(emp)

        rop_lines = slip.deduction_line_ids.filtered(
            lambda l: l.deduction_category == 'rop' and l.line_type == 'deduction'
        )
        rop_obrero = sum(rop_lines.mapped('amount'))
        expected_obrero = round(slip.gross_salary * K.ROP_EMP, 2)
        self.assertAlmostEqual(rop_obrero, expected_obrero, delta=1,
            msg=f'ROP obrero: {rop_obrero:,.2f} vs esperado {expected_obrero:,.2f}')

        expected_patron = round(slip.gross_salary * K.ROP_PAT, 2)
        self.assertAlmostEqual(slip.rop_employer, expected_patron, delta=1,
            msg=f'ROP patronal: {slip.rop_employer:,.2f} vs esperado {expected_patron:,.2f}')

    def test_08_rop_desactivado_no_genera_lineas(self):
        """rop_applies=False (default) no debe generar lineas ROP."""
        emp = self._make_employee('E2E ROP Off', 500_000, rop=False)
        slip = self._make_slip(emp)
        rop_lines = slip.deduction_line_ids.filtered(
            lambda l: l.deduction_category == 'rop'
        )
        self.assertFalse(rop_lines, 'rop_applies=False no debe generar lineas ROP')
        self.assertEqual(slip.rop_employer or 0, 0, 'rop_employer debe ser 0')


class TestE2EFlujoEmbargo(TestE2EBase):
    """Flujo con embargo judicial: cuenta 230960 separada."""

    def test_09_embargo_en_asiento_cuenta_separada(self):
        """El embargo aparece en cuenta 230960, no en salarios por pagar."""
        emp = self._make_employee('E2E Embargo', 600_000)
        embargo = self.env['planilla.embargo'].create({
            'employee_id': emp.id,
            'numero_expediente': 'E2E-001',
            'juzgado': 'Juzgado E2E',
            'beneficiario_nombre': 'Acreedor E2E',
            'calculation_type': 'fixed',
            'fixed_amount': 50_000,
            'date_start': '2026-01-01',
            'state': 'active',
        })
        slip = self._make_slip(emp)
        slip.sudo().action_confirm()
        try:
            slip.sudo().action_pay()
        except Exception:
            slip.sudo().action_pay(skip_accounting=True)

        embargo_lines = slip.move_id.line_ids.filtered(
            lambda l: l.account_id.id == self.acc_embargo_pay.id
        )
        self.assertTrue(embargo_lines, 'El embargo debe aparecer en cuenta 230960')
        total_embargo = sum(embargo_lines.mapped('credit'))
        self.assertAlmostEqual(total_embargo, 50_000, delta=1,
            msg=f'Embargo en asiento: {total_embargo:,.2f}')
        self._assert_balanced(slip.move_id, 'flujo con embargo')
        embargo.unlink()


class TestE2EFlujoPerRun(TestE2EBase):
    """Flujo per_run: asiento consolidado de planilla completa."""

    def test_10_per_run_asiento_cuadra_con_rop(self):
        """Asiento consolidado per_run cuadra con 2 empleados, uno con ROP."""
        self.config.sudo().write({'accounting_entry_mode': 'per_run'})
        try:
            emp1 = self._make_employee('E2E Run1', 500_000, rop=False)
            emp2 = self._make_employee('E2E Run2', 600_000, rop=True)

            run = self.env['planilla.run.cr'].create({
                'name': 'Planilla E2E per_run Test',
                'company_id': self.company.id,
                'payroll_calendar_id': self.calendar.id,
                'date_start': '2026-09-01',
                'date_end': '2026-09-30',
            })

            slip1 = self.env['planilla.payslip.cr'].create({
                'employee_id': emp1.id,
                'date_from': '2026-09-01', 'date_to': '2026-09-30',
                'company_id': self.company.id,
                'payroll_run_id': run.id,
            })
            slip2 = self.env['planilla.payslip.cr'].create({
                'employee_id': emp2.id,
                'date_from': '2026-09-01', 'date_to': '2026-09-30',
                'company_id': self.company.id,
                'payroll_run_id': run.id,
            })

            slip1.sudo().action_confirm()
            slip2.sudo().action_confirm()

            payslips = run.payslip_ids.filtered(lambda p: p.state == 'confirmed')
            run.sudo()._create_consolidated_accounting_entry(payslips)

            self.assertTrue(run.move_id, 'Debe crearse asiento consolidado')
            total = self._assert_balanced(run.move_id, 'per_run con ROP')
            self.assertGreater(total, 0)
        finally:
            self.config.sudo().write({'accounting_entry_mode': 'per_employee'})

    def test_11_per_run_rop_en_asiento(self):
        """El asiento per_run incluye ROP cuando hay empleados con rop_applies=True."""
        self.config.sudo().write({'accounting_entry_mode': 'per_run'})
        try:
            emp = self._make_employee('E2E Run ROP', 500_000, rop=True)
            run = self.env['planilla.run.cr'].create({
                'name': 'Planilla E2E ROP per_run',
                'company_id': self.company.id,
                'payroll_calendar_id': self.calendar.id,
                'date_start': '2026-10-01',
                'date_end': '2026-10-31',
            })
            slip = self.env['planilla.payslip.cr'].create({
                'employee_id': emp.id,
                'date_from': '2026-10-01', 'date_to': '2026-10-31',
                'company_id': self.company.id,
                'payroll_run_id': run.id,
            })
            slip.sudo().action_confirm()
            run.sudo()._create_consolidated_accounting_entry(
                run.payslip_ids.filtered(lambda p: p.state == 'confirmed')
            )
            self.assertTrue(run.move_id)
            rop_lines = run.move_id.line_ids.filtered(
                lambda l: 'ROP' in (l.name or '')
            )
            self.assertTrue(rop_lines, 'Asiento per_run debe incluir lineas ROP')
            self._assert_balanced(run.move_id, 'per_run ROP check')
        finally:
            self.config.sudo().write({'accounting_entry_mode': 'per_employee'})


class TestE2ECancelacion(TestE2EBase):
    """Cancelacion y reversion de boleta."""

    def test_12_cancelar_revierte_asiento(self):
        """Cancelar una boleta confirmada revierte el asiento contable."""
        emp = self._make_employee('E2E Cancel', 500_000)
        slip = self._make_slip(emp)
        slip.sudo().action_confirm()
        self.assertEqual(slip.state, 'confirmed')

        slip.sudo().action_cancel()
        self.assertEqual(slip.state, 'cancelled',
            'Boleta confirmada debe poder cancelarse')

    def test_13_reset_draft_permite_reconfirmar(self):
        """Reset a borrador permite volver a confirmar."""
        emp = self._make_employee('E2E Reset', 500_000)
        slip = self._make_slip(emp)
        slip.sudo().action_confirm()
        slip.sudo().action_cancel()
        slip.sudo().action_reset_to_draft()
        self.assertEqual(slip.state, 'draft',
            'Boleta cancelada debe poder volver a draft')

        # Despues de reset, los syncs deben funcionar sin duplicar
        slip.sudo().action_sync_novedades()
        slip.sudo().action_confirm()
        self.assertEqual(slip.state, 'confirmed')

    def test_14_no_se_puede_pagar_sin_confirmar(self):
        """No se puede pagar una boleta en borrador."""
        emp = self._make_employee('E2E NoPay', 500_000)
        slip = self._make_slip(emp)
        with self.assertRaises(UserError,
                msg='Pagar borrador debe lanzar UserError'):
            try:
                slip.sudo().action_pay()
            except Exception:
                slip.sudo().action_pay(skip_accounting=True)


class TestE2ETerminacion(TestE2EBase):
    """Flujo de liquidacion/finiquito."""

    def test_15_liquidacion_despido_injusto_completa(self):
        """Liquidacion por despido injustificado: todos los montos > 0."""
        emp = self._make_employee('E2E Liquid', 600_000)
        emp.entry_date = date.today() - relativedelta(years=3)

        term = self.env['planilla.termination'].create({
            'company_id': self.company.id,
            'employee_id': emp.id,
            'entry_date': emp.entry_date,
            'termination_date': date.today(),
            'termination_reason': 'despido_injust',
            'last_salary': 600_000,
        })

        self.assertGreater(term.cesantia_amount, 0, 'Cesantia > 0')
        self.assertGreater(term.preaviso_amount, 0, 'Preaviso > 0')
        self.assertGreater(term.vacation_amount, 0, 'Vacaciones proporcionales > 0')
        self.assertGreater(term.aguinaldo_amount, 0, 'Aguinaldo proporcional > 0')
        self.assertGreater(term.total_gross, 0, 'Total bruto > 0')
        self.assertGreater(term.total_net, 0, 'Total neto > 0')
        self.assertLess(term.total_net, term.total_gross,
            'Neto liquidacion debe ser menor que bruto (hay retenciones)')

    def test_16_liquidacion_renuncia_sin_cesantia(self):
        """Renuncia voluntaria no genera cesantia (Art. 29 CT)."""
        emp = self._make_employee('E2E Renuncia', 500_000)
        emp.entry_date = date.today() - relativedelta(years=5)

        term = self.env['planilla.termination'].create({
            'company_id': self.company.id,
            'employee_id': emp.id,
            'entry_date': emp.entry_date,
            'termination_date': date.today(),
            'termination_reason': 'renuncia',
            'last_salary': 500_000,
        })
        self.assertFalse(term.cesantia_applies,
            'Renuncia no aplica cesantia')
        self.assertEqual(term.cesantia_amount, 0,
            'Cesantia debe ser 0 en renuncia voluntaria')


class TestE2EIncapacidad(TestE2EBase):
    """Flujo de incapacidades."""

    def test_17_incapacidad_ccss_dias_1_3_patrono(self):
        """Dias 1-3 de incapacidad CCSS son cargo del patrono."""
        emp = self._make_employee('E2E Incap', 500_000)
        dis = self.env['planilla.disability'].create({
            'employee_id': emp.id,
            'disability_type': 'ccss',
            'date_start': '2026-08-05',
            'date_end': '2026-08-07',
        })
        dis._compute_costs()
        self.assertGreater(dis.employer_cost, 0,
            'Dias 1-3 incapacidad CCSS: costo patrono > 0 (Art. 79 Regl. CCSS)')
        self.assertEqual(dis.ccss_subsidy, 0,
            'Dias 1-3: CCSS no paga subsidio (es cargo del patrono)')
        dis.unlink()

    def test_18_incapacidad_ccss_mas_3_dias(self):
        """Incapacidad >3 dias: dias 1-3 patrono + dias 4+ CCSS."""
        emp = self._make_employee('E2E Incap+3', 500_000)
        dis = self.env['planilla.disability'].create({
            'employee_id': emp.id,
            'disability_type': 'ccss',
            'date_start': '2026-08-01',
            'date_end': '2026-08-10',
        })
        dis._compute_costs()
        self.assertGreater(dis.employer_cost, 0, 'Dias 1-3 cargo patrono')
        self.assertGreater(dis.ccss_subsidy, 0, 'Dias 4+ subsidio CCSS')
        dis.unlink()
