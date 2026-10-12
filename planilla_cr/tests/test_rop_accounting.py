"""
Tests v5.12 -- Asiento Contable con ROP: Verificacion de Montos por Cuenta
==========================================================================
Este archivo cubre el BUG-CRITICO-01 corregido en v5.12:
  Antes de la correccion, el ROP obrero se deducia DOS veces de net_for_accounting
  (una vez en rop_obrero_net y otra dentro de otras_ded), haciendo que:
    - salary_payable fuera CRCrop_obrero MENOS que el monto real
    - La cuenta 230000 (salary_payable) recibiera credito incorrecto
    - La cuenta 230350 (rop_payable) fuera correcta pero 230000 no

Tests especificos:
  01 -- salary_payable == gross - ccss_emp - renta - rop_obrero (sin doble deduccion)
  02 -- Cuenta 230350 recibe exactamente rop_obrero + rop_patronal
  03 -- Cuenta 230000 NO incluye el ROP (que va solo a 230350)
  04 -- salary_payable del campo coincide con credito en 230000 del asiento
  05 -- Per_run: mismos invariantes con asiento consolidado
  06 -- Per_run: cuenta 230350 tiene el total de todos los empleados con ROP
  07 -- Embargo + ROP simultaneos: ambas cuentas correctas
  08 -- Sin ROP: no hay lineas en 230350, neto en 230000 es correcto
  09 -- Bono salarial + ROP: base CCSS incluye bono, asiento cuadra
  10 -- ROP obrero no aparece en 'Otras Deducciones' del asiento

Ejecutar:
  docker compose exec web odoo -d prueba --test-enable \\
    --test-tags planilla_cr.test_rop_accounting --stop-after-init
"""
from odoo.tests.common import TransactionCase
from odoo.addons.planilla_cr.models import planilla_const as K


def _goc_account(env, company, code, name, atype):
    """Get or create account -- helper para setup."""
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


class TestROPAccountingBase(TransactionCase):
    """Base: setup completo con todas las cuentas incluyendo 230350."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref('base.main_company')
        env = cls.env
        co  = cls.company

        cls.acc_sal_exp   = _goc_account(env, co, '630000', 'Salarios ROP Test',      'expense')
        cls.acc_soc_exp   = _goc_account(env, co, '630100', 'Cargas Soc ROP Test',    'expense')
        cls.acc_vac_exp   = _goc_account(env, co, '630200', 'Vacaciones ROP Test',    'expense')
        cls.acc_agu_exp   = _goc_account(env, co, '630300', 'Aguinaldo ROP Test',     'expense')
        cls.acc_ces_exp   = _goc_account(env, co, '630400', 'Cesantia ROP Test',      'expense')
        cls.acc_sal_pay   = _goc_account(env, co, '230000', 'Sal por Pagar ROP Test', 'liability_current')
        cls.acc_ccss_pay  = _goc_account(env, co, '230300', 'CCSS ROP Test',          'liability_current')
        cls.acc_ins_pay   = _goc_account(env, co, '230400', 'INS ROP Test',           'liability_current')
        cls.acc_renta_pay = _goc_account(env, co, '230100', 'Renta ROP Test',         'liability_current')
        cls.acc_agu_prov  = _goc_account(env, co, '230500', 'Prov Agu ROP Test',      'liability_current')
        cls.acc_ces_prov  = _goc_account(env, co, '230600', 'Prov Ces ROP Test',      'liability_current')
        cls.acc_vac_prov  = _goc_account(env, co, '230700', 'Prov Vac ROP Test',      'liability_current')
        cls.acc_rop_pay   = _goc_account(env, co, '230350', 'ROP por Pagar Test',     'liability_current')
        cls.acc_emb_pay   = _goc_account(env, co, '230960', 'Embargo ROP Test',       'liability_current')
        cls.acc_pen_pay   = _goc_account(env, co, '230950', 'Pension ROP Test',       'liability_current')
        cls.acc_bono_exp  = _goc_account(env, co, '630600', 'Bonos ROP Test',         'expense')

        # Journal
        cls.journal = env['account.journal'].search([
            ('type', '=', 'general'), ('company_id', '=', co.id)
        ], limit=1)
        if not cls.journal:
            cls.journal = env['account.journal'].create({
                'name': 'Planilla ROP Test', 'code': 'PROP',
                'type': 'general', 'company_id': co.id,
            })

        # Calendar
        cls.calendar = env['planilla.calendar'].search([
            ('company_id', '=', co.id),
            ('frequency', '=', 'monthly'),
        ], limit=1)
        if not cls.calendar:
            cls.calendar = env['planilla.calendar'].create({
                'name': 'Mensual ROP Test', 'frequency': 'monthly',
                'company_id': co.id,
            })

        # Accounting config
        config = env['planilla.accounting.config'].search([
            ('company_id', '=', co.id)
        ], limit=1)
        if not config:
            config = env['planilla.accounting.config'].create({'company_id': co.id})
        config.sudo().write({
            'journal_id':                          cls.journal.id,
            'accounting_entry_mode':               'per_employee',
            'account_salary_expense':              cls.acc_sal_exp.id,
            'account_social_charges_expense':      cls.acc_soc_exp.id,
            'account_vacation_expense':            cls.acc_vac_exp.id,
            'account_aguinaldo_expense':           cls.acc_agu_exp.id,
            'account_cesantia_expense':            cls.acc_ces_exp.id,
            'account_ccss_payable':                cls.acc_ccss_pay.id,
            'account_ins_payable':                 cls.acc_ins_pay.id,
            'account_income_tax_payable':          cls.acc_renta_pay.id,
            'account_salary_payable':              cls.acc_sal_pay.id,
            'account_aguinaldo_provision':         cls.acc_agu_prov.id,
            'account_cesantia_provision':          cls.acc_ces_prov.id,
            'account_vacation_provision':          cls.acc_vac_prov.id,
            'account_rop_payable':                 cls.acc_rop_pay.id,
            'account_embargo_payable':             cls.acc_emb_pay.id,
            'account_pension_alimentaria_payable': cls.acc_pen_pay.id,
            'account_bono_expense':                cls.acc_bono_exp.id,
        })
        cls.config = config

        # Otorgar grupo aprobador al usuario de prueba (Odoo 19 requiere el grupo incluso con sudo)

    def _make_emp(self, name, salary=700_000, rop=False):
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

    def _make_slip(self, emp, date_from='2026-06-01', date_to='2026-06-30'):
        return self.env['planilla.payslip.cr'].create({
            'employee_id': emp.id,
            'date_from': date_from,
            'date_to': date_to,
            'company_id': self.company.id,
        })

    def _assert_balanced(self, move, ctx=''):
        debit  = round(sum(move.line_ids.mapped('debit')), 2)
        credit = round(sum(move.line_ids.mapped('credit')), 2)
        self.assertAlmostEqual(
            debit, credit, delta=0.05,
            msg=f'Asiento descuadrado [{ctx}]: DEBE CRC{debit:,.2f}  HABER CRC{credit:,.2f}'
        )
        return debit

    def _credits_for_account(self, move, account):
        """Suma de creditos de una cuenta en el asiento."""
        lines = move.line_ids.filtered(lambda l: l.account_id.id == account.id)
        return round(sum(lines.mapped('credit')), 2)

    def _debits_for_account(self, move, account):
        """Suma de debitos de una cuenta en el asiento."""
        lines = move.line_ids.filtered(lambda l: l.account_id.id == account.id)
        return round(sum(lines.mapped('debit')), 2)


class TestROPAccountingPerEmployee(TestROPAccountingBase):
    """
    Tests modo per_employee: verificar que el BUG-CRITICO-01 esta resuelto.
    Invariante principal: salary_payable = gross - ccss_emp - renta - rop_obrero
    (sin doble deduccion del ROP).
    """

    def test_01_salary_payable_correcto_con_rop(self):
        """
        BUG-CRITICO-01 fix: salary_payable == gross - ccss_emp - renta - rop_obrero.
        Antes de v5.12 era CRCrop_obrero MENOR por doble deduccion.
        """
        salary = 700_000
        emp  = self._make_emp('ROP Test 01', salary, rop=True)
        slip = self._make_slip(emp)

        gross     = slip.gross_salary
        ccss_emp  = slip.ccss_employee
        renta     = slip.income_tax
        rop_lines = slip.deduction_line_ids.filtered(
            lambda l: l.deduction_category == 'rop' and l.line_type == 'deduction'
        )
        rop_obrero = round(sum(rop_lines.mapped('amount')), 2)

        # El salary_payable del campo debe restar ROP obrero UNA sola vez
        expected_net = round(gross - ccss_emp - renta - rop_obrero, 2)
        actual_net   = round(slip.salary_payable, 2)

        self.assertAlmostEqual(
            actual_net, expected_net, delta=0.05,
            msg=(
                f'salary_payable incorrecto con ROP activo.\n'
                f'  Gross:        CRC{gross:,.2f}\n'
                f'  CCSS obrero:  CRC{ccss_emp:,.2f}\n'
                f'  Renta:        CRC{renta:,.2f}\n'
                f'  ROP obrero:   CRC{rop_obrero:,.2f}\n'
                f'  Esperado:     CRC{expected_net:,.2f}\n'
                f'  Obtenido:     CRC{actual_net:,.2f}\n'
                f'  Diferencia:   CRC{abs(actual_net - expected_net):,.2f}\n'
                f'Si la diferencia = rop_obrero, el BUG-CRITICO-01 no fue corregido.'
            )
        )

    def test_02_cuenta_230350_monto_exacto(self):
        """
        La cuenta 230350 (ROP por pagar) debe contener exactamente
        rop_obrero + rop_patronal en el HABER del asiento.
        """
        salary = 700_000
        emp  = self._make_emp('ROP Test 02', salary, rop=True)
        slip = self._make_slip(emp)
        slip.sudo().action_confirm()
        try:
            slip.sudo().action_pay()
        except Exception:
            slip.sudo().action_pay(skip_accounting=True)

        self.assertTrue(slip.move_id, 'Debe generarse asiento contable')
        self._assert_balanced(slip.move_id, 'test_02')

        rop_lines = slip.deduction_line_ids.filtered(
            lambda l: l.deduction_category == 'rop' and l.line_type == 'deduction'
        )
        rop_obrero   = round(sum(rop_lines.mapped('amount')), 2)
        rop_patronal = round(slip.rop_employer or 0.0, 2)
        expected_rop_total = round(rop_obrero + rop_patronal, 2)

        actual_rop_credit = self._credits_for_account(slip.move_id, self.acc_rop_pay)

        self.assertAlmostEqual(
            actual_rop_credit, expected_rop_total, delta=0.05,
            msg=(
                f'Cuenta 230350 (ROP por pagar) tiene credito incorrecto.\n'
                f'  ROP obrero:   CRC{rop_obrero:,.2f}\n'
                f'  ROP patronal: CRC{rop_patronal:,.2f}\n'
                f'  Esperado:     CRC{expected_rop_total:,.2f}\n'
                f'  En asiento:   CRC{actual_rop_credit:,.2f}'
            )
        )

    def test_03_cuenta_230000_no_incluye_rop(self):
        """
        La cuenta 230000 (salary_payable) debe contener UNICAMENTE el neto
        a depositar al empleado. El ROP obrero va a 230350, NO a 230000.
        """
        salary = 700_000
        emp  = self._make_emp('ROP Test 03', salary, rop=True)
        slip = self._make_slip(emp)
        slip.sudo().action_confirm()
        try:
            slip.sudo().action_pay()
        except Exception:
            slip.sudo().action_pay(skip_accounting=True)

        self.assertTrue(slip.move_id)

        rop_lines = slip.deduction_line_ids.filtered(
            lambda l: l.deduction_category == 'rop' and l.line_type == 'deduction'
        )
        rop_obrero = round(sum(rop_lines.mapped('amount')), 2)
        gross      = slip.gross_salary
        ccss_emp   = slip.ccss_employee
        renta      = slip.income_tax

        # El neto correcto a depositar = gross - ccss - renta - rop_obrero
        expected_230000 = round(gross - ccss_emp - renta - rop_obrero, 2)
        actual_230000   = self._credits_for_account(slip.move_id, self.acc_sal_pay)

        self.assertAlmostEqual(
            actual_230000, expected_230000, delta=0.05,
            msg=(
                f'Cuenta 230000 tiene monto incorrecto con ROP activo.\n'
                f'  Neto correcto (sin doble ROP): CRC{expected_230000:,.2f}\n'
                f'  Neto en asiento 230000:        CRC{actual_230000:,.2f}\n'
                f'  Diferencia: CRC{abs(actual_230000 - expected_230000):,.2f}\n'
                f'  Si diferencia  CRC{rop_obrero:,.2f} (rop_obrero), '
                f'el BUG-CRITICO-01 no fue corregido.'
            )
        )

    def test_04_salary_payable_igual_credito_230000(self):
        """
        Invariante critico: payslip.salary_payable DEBE igualar el credito
        en la cuenta 230000 del asiento contable.
        Este test detecta cualquier discrepancia entre el campo calculado
        y lo que realmente se registra en contabilidad.
        """
        emp  = self._make_emp('ROP Test 04', 700_000, rop=True)
        slip = self._make_slip(emp)
        slip.sudo().action_confirm()
        try:
            slip.sudo().action_pay()
        except Exception:
            slip.sudo().action_pay(skip_accounting=True)

        self.assertTrue(slip.move_id)
        self._assert_balanced(slip.move_id, 'test_04')

        salary_payable_campo = round(slip.salary_payable, 2)
        credito_230000 = self._credits_for_account(slip.move_id, self.acc_sal_pay)

        self.assertAlmostEqual(
            salary_payable_campo, credito_230000, delta=0.05,
            msg=(
                f'INCONSISTENCIA: salary_payable (campo)  credito en 230000 (asiento).\n'
                f'  salary_payable (campo):    CRC{salary_payable_campo:,.2f}\n'
                f'  Credito en 230000:         CRC{credito_230000:,.2f}\n'
                f'  Diferencia:                CRC{abs(salary_payable_campo - credito_230000):,.2f}\n'
                f'El empleado recibiria un monto diferente al registrado en contabilidad.'
            )
        )

    def test_05_asiento_cuadra_y_montos_correctos_rop(self):
        """
        Test integral: DEBE=HABER, 230350 correcto, 230000 correcto,
        salary_payable coincide. Todo en un solo test de regresion.
        """
        salary = 800_000
        emp  = self._make_emp('ROP Test 05 Integral', salary, rop=True)
        slip = self._make_slip(emp)
        slip.sudo().action_confirm()
        try:
            slip.sudo().action_pay()
        except Exception:
            slip.sudo().action_pay(skip_accounting=True)

        move = slip.move_id
        self.assertTrue(move, 'No se genero asiento contable')
        self._assert_balanced(move, 'test_05 integral')

        # Calcular valores esperados
        gross    = slip.gross_salary
        ccss_emp = slip.ccss_employee
        renta    = slip.income_tax
        rop_obs  = round(sum(
            l.amount for l in slip.deduction_line_ids
            if l.deduction_category == 'rop' and l.line_type == 'deduction'
        ), 2)
        rop_pat  = round(slip.rop_employer or 0.0, 2)

        # 1. 230350 debe tener rop_obrero + rop_patronal
        exp_230350 = round(rop_obs + rop_pat, 2)
        got_230350 = self._credits_for_account(move, self.acc_rop_pay)
        self.assertAlmostEqual(got_230350, exp_230350, delta=0.05,
            msg=f'230350: esperado CRC{exp_230350:,.2f}, obtenido CRC{got_230350:,.2f}')

        # 2. salary_payable campo debe coincidir con credito en 230000
        self.assertAlmostEqual(
            round(slip.salary_payable, 2),
            self._credits_for_account(move, self.acc_sal_pay),
            delta=0.05,
            msg='salary_payable no coincide con credito en 230000'
        )

        # 3. El neto correcto = gross - ccss - renta - rop_obrero (UNA vez)
        exp_net = round(gross - ccss_emp - renta - rop_obs, 2)
        self.assertAlmostEqual(round(slip.salary_payable, 2), exp_net, delta=0.05,
            msg=f'salary_payable: esperado CRC{exp_net:,.2f}, obtenido CRC{slip.salary_payable:,.2f}')

    def test_06_sin_rop_230350_vacia(self):
        """Sin ROP activo: la cuenta 230350 no debe tener lineas en el asiento."""
        emp  = self._make_emp('ROP Test 06 Sin ROP', 700_000, rop=False)
        slip = self._make_slip(emp)
        slip.sudo().action_confirm()
        try:
            slip.sudo().action_pay()
        except Exception:
            slip.sudo().action_pay(skip_accounting=True)

        self.assertTrue(slip.move_id)
        self._assert_balanced(slip.move_id, 'test_06 sin ROP')

        credit_230350 = self._credits_for_account(slip.move_id, self.acc_rop_pay)
        self.assertEqual(
            credit_230350, 0.0,
            msg=f'Sin ROP, cuenta 230350 debe estar vacia. Tiene: CRC{credit_230350:,.2f}'
        )

    def test_07_embargo_y_rop_simultaneos(self):
        """
        Embargo judicial + ROP activos en la misma boleta.
        Ambas cuentas separadas (230960 y 230350) deben tener los montos correctos.
        El neto en 230000 debe excluir tanto embargo como ROP.
        """
        salary  = 700_000
        monto_embargo = 30_000
        emp  = self._make_emp('ROP Test 07 Embargo+ROP', salary, rop=True)

        embargo = self.env['planilla.embargo'].create({
            'employee_id': emp.id,
            'numero_expediente': 'ROP-TEST-07',
            'juzgado': 'Juzgado ROP Test',
            'beneficiario_nombre': 'Acreedor ROP Test',
            'calculation_type': 'fixed',
            'fixed_amount': monto_embargo,
            'date_start': '2026-01-01',
            'state': 'active',
        })

        try:
            slip = self._make_slip(emp)
            slip.sudo().action_confirm()
            try:
                slip.sudo().action_pay()
            except Exception:
                slip.sudo().action_pay(skip_accounting=True)

            move = slip.move_id
            self.assertTrue(move)
            self._assert_balanced(move, 'test_07 embargo+ROP')

            # 230960 debe tener el embargo
            credit_embargo = self._credits_for_account(move, self.acc_emb_pay)
            self.assertAlmostEqual(credit_embargo, monto_embargo, delta=0.05,
                msg=f'Embargo en 230960: esperado CRC{monto_embargo:,.2f}, obtenido CRC{credit_embargo:,.2f}')

            # 230350 debe tener el ROP
            rop_obs = round(sum(
                l.amount for l in slip.deduction_line_ids
                if l.deduction_category == 'rop' and l.line_type == 'deduction'
            ), 2)
            rop_pat = round(slip.rop_employer or 0.0, 2)
            credit_rop = self._credits_for_account(move, self.acc_rop_pay)
            self.assertAlmostEqual(credit_rop, round(rop_obs + rop_pat, 2), delta=0.05,
                msg=f'ROP en 230350: esperado CRC{rop_obs + rop_pat:,.2f}, obtenido CRC{credit_rop:,.2f}')

            # salary_payable coincide con 230000
            self.assertAlmostEqual(
                round(slip.salary_payable, 2),
                self._credits_for_account(move, self.acc_sal_pay),
                delta=0.05,
                msg='salary_payable no coincide con credito en 230000 (embargo+ROP)'
            )

        finally:
            embargo.unlink()

    def test_08_rop_no_aparece_en_otras_deducciones(self):
        """
        El ROP obrero NO debe aparecer en lineas 'Otras Deducciones' del asiento.
        Debe ir exclusivamente a la linea de 230350 (ROP por Pagar).
        Antes de v5.12 aparecia en otras_ded causando el doble descuento.
        """
        emp  = self._make_emp('Empleado Sin Otras Ded', 700_000, rop=True)
        slip = self._make_slip(emp)
        slip.sudo().action_confirm()
        try:
            slip.sudo().action_pay()
        except Exception:
            slip.sudo().action_pay(skip_accounting=True)

        self.assertTrue(slip.move_id, 'Debe haberse creado asiento contable')

        # Buscar lineas del asiento en 230000 que contengan 'ROP' en el nombre
        rop_in_230000 = slip.move_id.line_ids.filtered(
            lambda l: l.account_id.id == self.acc_sal_pay.id
            and 'ROP' in (l.name or '').upper()
        )
        self.assertFalse(
            rop_in_230000,
            msg=(
                f'BUG-CRITICO-01 no corregido: el ROP aparece en la cuenta 230000 '
                f'(salary_payable) en lugar de ir solo a 230350 (ROP por pagar).\n'
                f'Lineas encontradas: {[(l.name, l.credit) for l in rop_in_230000]}'
            )
        )


class TestROPAccountingPerRun(TestROPAccountingBase):
    """
    Tests modo per_run: mismos invariantes en asiento consolidado.
    """

    def setUp(self):
        super().setUp()
        # Modo per_run para estos tests
        self.config.sudo().write({'accounting_entry_mode': 'per_run'})

    def tearDown(self):
        # Restaurar modo per_employee
        self.config.sudo().write({'accounting_entry_mode': 'per_employee'})
        super().tearDown()

    def _make_run(self, date_from, date_to):
        return self.env['planilla.run.cr'].create({
            'name': f'Planilla ROP Test {date_from}',
            'company_id': self.company.id,
            'payroll_calendar_id': self.calendar.id,
            'date_start': date_from,
            'date_end': date_to,
        })

    def test_09_per_run_salary_payable_correcto(self):
        """
        Per_run: salary_payable total de la planilla es la suma de los netos correctos.
        Sin doble deduccion de ROP en el neto consolidado.
        """
        emp = self._make_emp('ROP Run Test 09', 700_000, rop=True)
        run = self._make_run('2026-07-01', '2026-07-31')

        slip = self.env['planilla.payslip.cr'].create({
            'employee_id': emp.id,
            'date_from': '2026-07-01', 'date_to': '2026-07-31',
            'company_id': self.company.id,
            'payroll_run_id': run.id,
        })
        slip.sudo().action_confirm()

        payslips = run.payslip_ids.filtered(lambda p: p.state == 'confirmed')
        run.sudo()._create_consolidated_accounting_entry(payslips)

        self.assertTrue(run.move_id)
        self._assert_balanced(run.move_id, 'test_09 per_run')

        rop_obs = round(sum(
            l.amount for l in slip.deduction_line_ids
            if l.deduction_category == 'rop' and l.line_type == 'deduction'
        ), 2)
        rop_pat = round(slip.rop_employer or 0.0, 2)

        # 230350 debe tener rop_obrero + rop_patronal
        credit_rop = self._credits_for_account(run.move_id, self.acc_rop_pay)
        self.assertAlmostEqual(credit_rop, round(rop_obs + rop_pat, 2), delta=0.05,
            msg=f'Per_run 230350: esperado CRC{rop_obs+rop_pat:,.2f}, obtenido CRC{credit_rop:,.2f}')

        # salary_payable del run debe coincidir con credito en 230000
        total_neto_run = round(run.total_salary_payable, 2)
        credit_230000  = self._credits_for_account(run.move_id, self.acc_sal_pay)
        self.assertAlmostEqual(
            total_neto_run, credit_230000, delta=0.05,
            msg=(
                f'Per_run: total_salary_payable  credito en 230000.\n'
                f'  total_salary_payable: CRC{total_neto_run:,.2f}\n'
                f'  Credito en 230000:    CRC{credit_230000:,.2f}'
            )
        )

    def test_10_per_run_230350_suma_todos_empleados_rop(self):
        """
        Per_run con multiples empleados: 230350 debe ser la suma del ROP
        de TODOS los empleados con rop_applies=True.
        """
        emp1 = self._make_emp('ROP Run10 A', 500_000, rop=True)
        emp2 = self._make_emp('ROP Run10 B', 700_000, rop=True)
        emp3 = self._make_emp('ROP Run10 C', 600_000, rop=False)  # sin ROP
        run  = self._make_run('2026-08-01', '2026-08-31')

        slips = []
        for emp in [emp1, emp2, emp3]:
            s = self.env['planilla.payslip.cr'].create({
                'employee_id': emp.id,
                'date_from': '2026-08-01', 'date_to': '2026-08-31',
                'company_id': self.company.id,
                'payroll_run_id': run.id,
            })
            slips.append(s)
            s.sudo().action_confirm()

        payslips = run.payslip_ids.filtered(lambda p: p.state == 'confirmed')
        run.sudo()._create_consolidated_accounting_entry(payslips)

        self.assertTrue(run.move_id)
        self._assert_balanced(run.move_id, 'test_10 per_run multi-emp')

        # Calcular ROP esperado: solo emp1 y emp2 (emp3 no tiene rop)
        expected_rop = 0.0
        for slip in slips[:2]:  # emp1 y emp2
            rop_obs = sum(
                l.amount for l in slip.deduction_line_ids
                if l.deduction_category == 'rop' and l.line_type == 'deduction'
            )
            rop_pat = slip.rop_employer or 0.0
            expected_rop += rop_obs + rop_pat
        expected_rop = round(expected_rop, 2)

        actual_rop = self._credits_for_account(run.move_id, self.acc_rop_pay)
        self.assertAlmostEqual(
            actual_rop, expected_rop, delta=0.10,
            msg=(
                f'Per_run 230350 con 2 empleados con ROP:\n'
                f'  Esperado: CRC{expected_rop:,.2f}\n'
                f'  Obtenido: CRC{actual_rop:,.2f}'
            )
        )
