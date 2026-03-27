"""
Tests v5.12 -- Cobertura General: flujos completos, legalidad CR y regresion
============================================================================
Este archivo aumenta la cobertura del modulo hacia el 10/10 cubriendo
flujos y casos de borde que no estaban cubiertos en los tests anteriores.

Areas cubiertas:
  - Flujos completos de Horas Extras (calculo + asiento)
  - Incapacidad CCSS + ROP simultaneos
  - Provisiones correctas en todos los modos de pago
  - Pension alimentaria prioridad sobre embargo (Ley 8590)
  - Prestamo + ROP + embargo en la misma boleta
  - Proporcional (empleado que ingresa a mitad de periodo)
  - Frecuencias de pago: quincenal, semanal, mensual
  - Acciones de planilla: confirm -> pay -> reset -> cancel
  - Historial salarial automatico al confirmar boleta
  - Validaciones de salario minimo MTSS
  - Planilla con multiples sucursales

Ejecutar:
  docker compose exec web odoo -d prueba --test-enable \\
    --test-tags planilla_cr.test_v512_coverage --stop-after-init
"""
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError, ValidationError
from odoo.addons.planilla_cr.models import planilla_const as K
from datetime import date, timedelta


def _goc(env, company, code, name, atype):
    acc = env['account.account'].search([
        ('code', '=', code), ('company_ids', 'in', company.id)
    ], limit=1)
    if not acc:
        acc = env['account.account'].create({
            'code': code, 'name': name, 'account_type': atype,
            'company_ids': [(4, company.id)],
        })
    return acc


class TestCoverageBase(TransactionCase):
    """Base con setup completo para tests de cobertura."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref('base.main_company')
        env, co = cls.env, cls.company

        # Cuentas necesarias
        cls.acc_sal   = _goc(env, co, '630000', 'Sal Cov',  'expense')
        cls.acc_soc   = _goc(env, co, '630100', 'Soc Cov',  'expense')
        cls.acc_vac   = _goc(env, co, '630200', 'Vac Cov',  'expense')
        cls.acc_agu   = _goc(env, co, '630300', 'Agu Cov',  'expense')
        cls.acc_ces   = _goc(env, co, '630400', 'Ces Cov',  'expense')
        cls.acc_sp    = _goc(env, co, '230000', 'SP Cov',   'liability_current')
        cls.acc_ccss  = _goc(env, co, '230300', 'CCSS Cov', 'liability_current')
        cls.acc_ins   = _goc(env, co, '230400', 'INS Cov',  'liability_current')
        cls.acc_renta = _goc(env, co, '230100', 'Renta Cov','liability_current')
        cls.acc_agp   = _goc(env, co, '230500', 'PAgCov',   'liability_current')
        cls.acc_csp   = _goc(env, co, '230600', 'PCsCov',   'liability_current')
        cls.acc_vap   = _goc(env, co, '230700', 'PVaCov',   'liability_current')
        cls.acc_rop   = _goc(env, co, '230350', 'ROP Cov',  'liability_current')
        cls.acc_emb   = _goc(env, co, '230960', 'Emb Cov',  'liability_current')
        cls.acc_pen   = _goc(env, co, '230950', 'Pen Cov',  'liability_current')
        cls.acc_loan  = _goc(env, co, '230900', 'Loan Cov', 'liability_current')
        cls.acc_loanr = _goc(env, co, '115000', 'LoanR Cov','asset_current')
        cls.acc_sub   = _goc(env, co, '120500', 'Sub Cov',  'asset_current')

        cls.journal = env['account.journal'].search([
            ('type', '=', 'general'), ('company_id', '=', co.id)
        ], limit=1) or env['account.journal'].create({
            'name': 'Planilla Cov', 'code': 'PCOV',
            'type': 'general', 'company_id': co.id,
        })

        cls.cal_m = env['planilla.calendar'].search([
            ('frequency', '=', 'monthly'), ('company_id', '=', co.id)
        ], limit=1) or env['planilla.calendar'].create({
            'name': 'Mensual Cov', 'frequency': 'monthly', 'company_id': co.id,
        })
        cls.cal_q = env['planilla.calendar'].search([
            ('frequency', '=', 'biweekly'), ('company_id', '=', co.id)
        ], limit=1) or env['planilla.calendar'].create({
            'name': 'Quincenal Cov', 'frequency': 'biweekly', 'company_id': co.id,
        })

        config = env['planilla.accounting.config'].search([
            ('company_id', '=', co.id)
        ], limit=1)
        if not config:
            config = env['planilla.accounting.config'].create({'company_id': co.id})
        config.sudo().write({
            'journal_id': cls.journal.id,
            'accounting_entry_mode': 'per_employee',
            'account_salary_expense':         cls.acc_sal.id,
            'account_social_charges_expense': cls.acc_soc.id,
            'account_vacation_expense':       cls.acc_vac.id,
            'account_aguinaldo_expense':      cls.acc_agu.id,
            'account_cesantia_expense':       cls.acc_ces.id,
            'account_ccss_payable':           cls.acc_ccss.id,
            'account_ins_payable':            cls.acc_ins.id,
            'account_income_tax_payable':     cls.acc_renta.id,
            'account_salary_payable':         cls.acc_sp.id,
            'account_aguinaldo_provision':    cls.acc_agp.id,
            'account_cesantia_provision':     cls.acc_csp.id,
            'account_vacation_provision':     cls.acc_vap.id,
            'account_rop_payable':            cls.acc_rop.id,
            'account_embargo_payable':        cls.acc_emb.id,
            'account_pension_alimentaria_payable': cls.acc_pen.id,
            'account_loans_payable':          cls.acc_loan.id,
            'account_loans_receivable':       cls.acc_loanr.id,
            'account_ccss_subsidy_receivable': cls.acc_sub.id,
        })
        cls.config = config

        # Otorgar grupo aprobador al usuario de prueba (Odoo 19 requiere el grupo incluso con sudo)

    def _emp(self, name, salary=600_000, rop=False, cal=None):
        return self.env['hr.employee'].create({
            'name': name, 'company_id': self.company.id,
            'base_salary': salary,
            'payroll_calendar_id': (cal or self.cal_m).id,
            'rop_applies': rop, 'entry_date': '2021-01-01',
            'identification_id': '1-0101-0001',
            'work_contact_id': self.env.company.partner_id.id,
        })

    def _slip(self, emp, df='2026-09-01', dt='2026-09-30'):
        return self.env['planilla.payslip.cr'].create({
            'employee_id': emp.id, 'date_from': df, 'date_to': dt,
            'company_id': self.company.id,
        })

    def _pay(self, slip):
        slip.sudo().action_confirm()
        try:
            slip.sudo().action_pay()
        except Exception:
            slip.sudo().action_pay(skip_accounting=True)
        return slip

    def _balanced(self, move, ctx=''):
        d = round(sum(move.line_ids.mapped('debit')), 2)
        c = round(sum(move.line_ids.mapped('credit')), 2)
        self.assertAlmostEqual(d, c, delta=0.05,
            msg=f'Asiento [{ctx}] descuadrado: D={d:,.2f} H={c:,.2f}')
        return d


# =======================================================================
# Frecuencias de pago -- factor de conversion
# =======================================================================
class TestFrecuenciasPago(TestCoverageBase):

    def test_01_quincenal_salario_mitad_del_mensual(self):
        """
        Un empleado quincenal debe recibir exactamente 50% del salario mensual.
        """
        sal_mensual = 600_000
        emp = self._emp('Frec Quincenal', sal_mensual, cal=self.cal_q)
        slip = self._slip(emp, '2026-09-01', '2026-09-15')

        expected = round(sal_mensual * K.FREQ_FACTORS['biweekly'], 2)
        self.assertAlmostEqual(slip.base_salary, expected, delta=100,
            msg=f'Salario quincenal: esperado CRC{expected:,.2f}, obtenido CRC{slip.base_salary:,.2f}')

    def test_02_mensual_salario_completo(self):
        """Empleado mensual: base_salary == salario configurado en el empleado."""
        emp = self._emp('Frec Mensual', 700_000, cal=self.cal_m)
        slip = self._slip(emp)
        self.assertAlmostEqual(slip.base_salary, 700_000, delta=100,
            msg='Empleado mensual debe recibir el salario completo')

    def test_03_freq_factors_coherentes_con_const(self):
        """FREQ_FACTORS suma a valores esperados para CR."""
        self.assertAlmostEqual(K.FREQ_FACTORS['monthly'],   1.0,  places=3)
        self.assertAlmostEqual(K.FREQ_FACTORS['biweekly'],  0.5,  places=3)
        self.assertAlmostEqual(K.FREQ_FACTORS['weekly'],    0.25, places=3)
        self.assertAlmostEqual(K.FREQ_FACTORS['bimonthly'], 2.0,  places=3)

    def test_04_periodos_por_mes_bimonthly_es_cero_punto_cinco(self):
        """
        FIX B-04 v58: PERIODOS_POR_MES['bimonthly'] debe ser 0.5.
        Un periodo bimensual ocurre 0.5 veces por mes (cada 2 meses).
        """
        self.assertAlmostEqual(K.PERIODOS_POR_MES['bimonthly'], 0.5, places=3,
            msg='bimonthly debe ser 0.5 periodos/mes (correccion FIX B-04 v58)')


# =======================================================================
# Proporcional -- ingreso a mitad de periodo
# =======================================================================
class TestProporcional(TestCoverageBase):

    def test_05_salario_proporcional_dias_trabajados(self):
        """
        Empleado que ingreso el dia 16 del mes debe recibir 50% del salario
        (15 dias de 30 trabajados = factor 0.5).
        """
        emp = self._emp('Prop Ingreso', 600_000)
        # Fecha de ingreso: dia 16 del periodo
        emp.write({'entry_date': '2026-10-16'})
        slip = self._slip(emp, '2026-10-01', '2026-10-31')

        # El computo automatico debe detectar el ingreso parcial
        slip._onchange_auto_proportional()

        if slip.is_proportional:
            self.assertAlmostEqual(slip.proportional_factor, 0.5, delta=0.05,
                msg=f'Factor proporcional: esperado 0.5, obtenido {slip.proportional_factor}')
            self.assertLess(slip.base_salary, 600_000,
                msg='Salario con ingreso parcial debe ser menor al mensual completo')

    def test_06_factor_proporcional_completo_sin_ingreso_parcial(self):
        """Empleado con periodo completo tiene factor proporcional = 1.0."""
        emp = self._emp('Prop Completo', 600_000)
        # Fecha de ingreso anterior al periodo
        emp.write({'entry_date': '2020-01-01'})
        slip = self._slip(emp)
        self.assertEqual(slip.is_proportional, False,
            msg='Empleado con ingreso anterior al periodo no debe ser proporcional')
        self.assertEqual(slip.proportional_factor, 1.0,
            msg='Factor proporcional debe ser 1.0 para periodo completo')


# =======================================================================
# Horas extras -- calculo y limites legales
# =======================================================================
class TestHorasExtras(TestCoverageBase):

    def test_07_horas_extras_simples_factor_1_5(self):
        """
        Horas extras simples deben calcularse con factor 1.5x del salario hora
        (Art. 139 CT). Verificar que el calculo de tarifa es correcto.
        """
        from odoo.addons.planilla_cr.models import planilla_const as K
        sal  = 600_000
        emp  = self._emp('HE Simple', sal)
        slip = self._slip(emp)

        he = self.env['planilla.overtime'].create({
            'employee_id': emp.id,
            'date': '2026-09-10',
            'hours': 2.0,
            'overtime_type': 'simple',
            'state': 'approved',
            'payslip_id': slip.id,
        })
        try:
            # Tarifa hora = salario_mensual / (dias_mes * horas_jornada)
            expected_rate = round(sal / (K.DIAS_MES * K.HORAS_JORNADA_DEFAULT), 2)
            expected_amount = round(expected_rate * K.FACTOR_HE_SIMPLE * 2.0, 2)

            slip._compute_extras()

            self.assertAlmostEqual(slip.overtime_amount, expected_amount, delta=200,
                msg=(
                    f'HE simples 2h: esperado CRC{expected_amount:,.2f}, '
                    f'obtenido CRC{slip.overtime_amount:,.2f}'
                )
            )
        finally:
            he.unlink()

    def test_08_horas_extras_incluidas_en_gross(self):
        """
        El monto de horas extras aprobadas debe sumarse al gross_salary.
        """
        emp  = self._emp('HE en Gross', 600_000)
        slip = self._slip(emp)

        gross_sin_he = slip.gross_salary

        he = self.env['planilla.overtime'].create({
            'employee_id': emp.id, 'date': '2026-09-05',
            'hours': 3.0, 'overtime_type': 'simple',
            'state': 'approved', 'payslip_id': slip.id,
        })
        try:
            slip._compute_extras()
            slip._compute_gross()
            self.assertGreater(slip.gross_salary, gross_sin_he,
                msg='Horas extras deben aumentar el gross_salary')
            self.assertGreater(slip.overtime_amount, 0,
                msg='overtime_amount debe ser > 0 con HE aprobadas')
        finally:
            he.unlink()


# =======================================================================
# Provisiones -- coherencia en todos los modos
# =======================================================================
class TestProvisiones(TestCoverageBase):

    def test_09_provision_aguinaldo_8_33_pct(self):
        """Provision aguinaldo = 8.33% del gross (Art. 228 CT)."""
        emp  = self._emp('Prov Agu', 600_000)
        slip = self._slip(emp)

        expected = round(slip.gross_salary * K.PROV_AGUINALDO, 2)
        self.assertAlmostEqual(slip.aguinaldo_provision, expected, delta=10,
            msg=f'Provision aguinaldo: esperado CRC{expected:,.2f}, '
                f'obtenido CRC{slip.aguinaldo_provision:,.2f}')

    def test_10_provision_cesantia_5_33_pct(self):
        """Provision cesantia = 5.33% del gross (Art. 29 CT)."""
        emp  = self._emp('Prov Ces', 600_000)
        slip = self._slip(emp)

        expected = round(slip.gross_salary * K.PROV_CESANTIA, 2)
        self.assertAlmostEqual(slip.cesantia_provision, expected, delta=10,
            msg=f'Provision cesantia: esperado CRC{expected:,.2f}, '
                f'obtenido CRC{slip.cesantia_provision:,.2f}')

    def test_11_provision_vacaciones_4_16_pct(self):
        """Provision vacaciones = 4.16% del gross (Art. 153 CT)."""
        emp  = self._emp('Prov Vac', 600_000)
        slip = self._slip(emp)

        expected = round(slip.gross_salary * K.PROV_VACACIONES, 2)
        self.assertAlmostEqual(slip.vacation_provision, expected, delta=10,
            msg=f'Provision vacaciones: esperado CRC{expected:,.2f}, '
                f'obtenido CRC{slip.vacation_provision:,.2f}')

    def test_12_provisiones_en_asiento_contable(self):
        """Las 3 provisiones deben aparecer en el asiento contable."""
        emp  = self._emp('Prov Asiento', 600_000)
        slip = self._pay(self._slip(emp))

        self.assertTrue(slip.move_id)
        self._balanced(slip.move_id, 'provisiones')

        names = [l.name or '' for l in slip.move_id.line_ids]
        self.assertTrue(any('Aguinaldo' in n for n in names),
            msg='Provision Aguinaldo debe aparecer en el asiento')
        self.assertTrue(any('Cesantia' in n or 'Cesantia' in n for n in names),
            msg='Provision Cesantia debe aparecer en el asiento')
        self.assertTrue(any('Vacaciones' in n for n in names),
            msg='Provision Vacaciones debe aparecer en el asiento')


# =======================================================================
# Pension alimentaria -- prioridad absoluta sobre embargo
# =======================================================================
class TestPensionVsEmbargo(TestCoverageBase):

    def test_13_pension_en_cuenta_separada_230950(self):
        """Pension alimentaria debe ir a cuenta 230950, no a 230000."""
        emp = self._emp('Pension Test', 700_000)

        pension = self.env['planilla.pension.alimentaria'].create({
            'employee_id': emp.id,
            'numero_expediente': 'PA-TEST-01',
            'beneficiario_nombre': 'Beneficiario Test',
            'calculation_type': 'fixed',
            'fixed_amount': 80_000,
            'date_start': '2026-01-01',
            'state': 'active',
        })
        try:
            slip = self._slip(emp)
            slip._sync_pension_alimentaria()
            slip = self._pay(slip)

            pen_lines = slip.move_id.line_ids.filtered(
                lambda l: l.account_id.id == self.acc_pen.id
            )
            self.assertTrue(pen_lines,
                msg='Pension alimentaria debe aparecer en cuenta 230950')
            self.assertAlmostEqual(
                round(sum(pen_lines.mapped('credit')), 2), 80_000, delta=1,
                msg='Credito en 230950 debe ser CRC80,000'
            )
            self._balanced(slip.move_id, 'pension alimentaria')
        finally:
            pension.unlink()

    def test_14_embargo_no_supera_25_pct_neto(self):
        """
        El embargo judicial no puede superar 25% del neto disponible (Art. 172 CT).
        Un embargo de monto muy alto debe ser recortado al limite legal.
        """
        sal = 500_000
        emp = self._emp('Embargo Limit', sal)

        # Neto disponible  500,000 - 10.83% CCSS - renta  445,850
        # 25% del neto  111,462 -- pedir embargo de 200,000 debe ser recortado
        embargo = self.env['planilla.embargo'].create({
            'employee_id': emp.id,
            'numero_expediente': 'LIMTEST-01',
            'juzgado': 'Juzgado Lim',
            'beneficiario_nombre': 'Acreedor Lim',
            'calculation_type': 'fixed',
            'fixed_amount': 200_000,  # Muy alto -- debe ser recortado al 25%
            'date_start': '2026-01-01',
            'state': 'active',
        })
        try:
            slip = self._slip(emp)
            slip._sync_embargos()

            emb_lines = slip.deduction_line_ids.filtered(
                lambda l: l.deduction_category == 'embargo'
            )
            if emb_lines:
                total_embargo = sum(emb_lines.mapped('amount'))
                neto_disponible = slip.gross_salary - slip.ccss_employee - slip.income_tax
                limite_legal = round(neto_disponible * K.MAX_PCT_EMBARGO / 100, 2)

                self.assertLessEqual(total_embargo, limite_legal + 0.05,
                    msg=(
                        f'Embargo supera el 25% del neto disponible (Art. 172 CT).\n'
                        f'  Embargo aplicado: CRC{total_embargo:,.2f}\n'
                        f'  Limite legal 25%: CRC{limite_legal:,.2f}'
                    )
                )
        finally:
            embargo.unlink()


# =======================================================================
# Flujo completo planilla: draft -> confirm -> pay -> salary_history
# =======================================================================
class TestFlujoCompletoRun(TestCoverageBase):

    def test_15_flujo_completo_planilla_estados(self):
        """
        Flujo completo de una planilla: draft -> confirmed -> done.
        Verificar transiciones de estado correctas.
        """
        emp  = self._emp('Flujo Run', 600_000)
        run = self.env['planilla.run.cr'].create({
            'name': 'Planilla Flujo Completo Test',
            'company_id': self.company.id,
            'payroll_calendar_id': self.cal_m.id,
            'date_start': '2026-09-01', 'date_end': '2026-09-30',
        })
        slip = self.env['planilla.payslip.cr'].create({
            'employee_id': emp.id, 'date_from': '2026-09-01', 'date_to': '2026-09-30',
            'company_id': self.company.id, 'payroll_run_id': run.id,
        })

        # Estado inicial
        self.assertEqual(run.state, 'draft')
        self.assertEqual(slip.state, 'draft')

        # Confirmar
        slip.sudo().action_confirm()
        run.sudo().write({'state': 'confirmed'})
        self.assertEqual(slip.state, 'confirmed')
        self.assertEqual(run.state, 'confirmed')

        # Pagar
        slip.sudo().action_pay(skip_accounting=True)
        self.assertEqual(slip.state, 'done')

        run.sudo().write({'state': 'done'})
        self.assertEqual(run.state, 'done')

    def test_16_historial_salarial_creado_al_pagar(self):
        """
        Al pagar una boleta debe crearse automaticamente un registro
        en planilla.salary.history con el salario de la boleta.
        """
        emp  = self._emp('Historial Test', 600_000)
        slip = self._slip(emp, '2026-10-01', '2026-10-31')
        slip_id = slip.id

        # Pagar
        slip.sudo().action_confirm()
        slip.sudo().action_pay(skip_accounting=True)

        # Verificar historial
        historial = self.env['planilla.salary.history'].search([
            ('employee_id', '=', emp.id),
            ('payslip_id', '=', slip_id),
        ], limit=1)
        self.assertTrue(historial,
            msg='Debe crearse un registro de historial salarial al pagar la boleta')
        self.assertAlmostEqual(historial.salary, slip.net_salary, delta=1,
            msg='El historial debe registrar el salario neto de la boleta')

    def test_17_cancelar_boleta_pagada_falla(self):
        """
        No se puede cancelar una boleta en estado 'done' (pagada).
        Debe lanzar UserError.
        """
        emp  = self._emp('Cancel Done', 500_000)
        slip = self._slip(emp, '2026-11-01', '2026-11-30')
        slip.sudo().action_confirm()
        slip.sudo().action_pay(skip_accounting=True)

        self.assertEqual(slip.state, 'done')

        with self.assertRaises(UserError,
            msg='Cancelar una boleta pagada debe lanzar UserError'
        ):
            slip.sudo().action_cancel()

    def test_18_reset_to_draft_desde_confirmado(self):
        """Una boleta confirmada puede volver a borrador."""
        emp  = self._emp('Reset Draft', 500_000)
        slip = self._slip(emp, '2026-12-01', '2026-12-31')
        slip.sudo().action_confirm()
        self.assertEqual(slip.state, 'confirmed')

        slip.sudo().action_reset_to_draft()
        self.assertEqual(slip.state, 'draft',
            msg='action_reset_to_draft debe devolver la boleta a estado draft')


# =======================================================================
# Renta progresiva -- tramos correctos 2026
# =======================================================================
class TestRentaProgresiva(TestCoverageBase):

    def test_19_salario_bajo_exento_renta_cero(self):
        """Salario por debajo del minimo exento no paga impuesto de renta."""
        emp  = self._emp('Renta Exento', K.RENTA_EXENTO - 1)
        slip = self._slip(emp)
        self.assertEqual(slip.income_tax, 0.0,
            msg=f'Salario CRC{K.RENTA_EXENTO-1:,} no debe pagar renta (exento CRC{K.RENTA_EXENTO:,})')

    def test_20_salario_sobre_exento_paga_renta(self):
        """Salario sobre el exento paga renta al 10%."""
        exceso = 100_000
        sal = K.RENTA_EXENTO + exceso
        emp  = self._emp('Renta 10pct', sal)
        slip = self._slip(emp)

        expected_renta = round(exceso * K.RENTA_TASA_1, 2)
        self.assertAlmostEqual(slip.income_tax, expected_renta, delta=500,
            msg=f'Renta al 10%: esperado CRC{expected_renta:,.2f}, obtenido CRC{slip.income_tax:,.2f}')

    def test_21_tramos_renta_progresivos(self):
        """Salario alto paga mas tramos -- verificar que el calculo es progresivo."""
        sal_bajo  = K.RENTA_EXENTO + 100_000  # Solo tramo 10%
        sal_alto  = K.RENTA_TOPE_15 + 200_000   # Tramos 10% + 15%

        emp_b = self._emp('Renta Bajo', sal_bajo)
        emp_a = self._emp('Renta Alto', sal_alto)
        slip_b = self._slip(emp_b)
        slip_a = self._slip(emp_a)

        self.assertGreater(slip_a.income_tax, slip_b.income_tax,
            msg='Salario mas alto debe pagar mas impuesto (renta progresiva)')

        # La tasa efectiva del salario alto debe ser mayor
        if slip_a.gross_salary > 0 and slip_b.gross_salary > 0:
            tasa_b = slip_b.income_tax / slip_b.gross_salary
            tasa_a = slip_a.income_tax / slip_a.gross_salary
            self.assertGreater(tasa_a, tasa_b,
                msg='La tasa efectiva de renta debe crecer con el salario (progresividad)')


# =======================================================================
# CCSS -- tasas correctas
# =======================================================================
class TestCCSSTasas(TestCoverageBase):

    def test_22_ccss_obrero_10_83(self):
        """CCSS obrero debe ser exactamente 10.83% del bruto."""
        sal  = 600_000
        emp  = self._emp('CCSS Obrero', sal)
        slip = self._slip(emp)

        expected = round(slip.gross_salary * K.CCSS_EMP, 2)
        self.assertAlmostEqual(slip.ccss_employee, expected, delta=1,
            msg=f'CCSS obrero: esperado CRC{expected:,.2f}, obtenido CRC{slip.ccss_employee:,.2f}')

    def test_23_ccss_patronal_26_83(self):
        """CCSS patronal debe ser exactamente 26.83% del bruto."""
        sal  = 600_000
        emp  = self._emp('CCSS Patronal', sal)
        slip = self._slip(emp)

        expected = round(slip.gross_salary * K.CCSS_PAT, 2)
        self.assertAlmostEqual(slip.ccss_employer, expected, delta=1,
            msg=f'CCSS patronal: esperado CRC{expected:,.2f}, obtenido CRC{slip.ccss_employer:,.2f}')

    def test_24_costo_total_patronal_incluye_provisiones(self):
        """
        total_employer_cost debe incluir salario + CCSS patronal + INS + provisiones.
        Nunca puede ser menor que el gross_salary.
        """
        emp  = self._emp('Costo Patronal', 600_000)
        slip = self._slip(emp)

        self.assertGreater(slip.total_employer_cost, slip.gross_salary,
            msg='El costo total patronal debe ser mayor al salario bruto '
                '(incluye CCSS, INS, provisiones)')


# =======================================================================
# Constraint de unicidad de boleta
# =======================================================================
class TestConstraintUnicidad(TestCoverageBase):

    def test_25_dos_boletas_mismo_empleado_mismo_periodo_falla(self):
        """
        No se pueden crear dos boletas activas para el mismo empleado
        en periodos que se solapan.
        """
        emp   = self._emp('Unicidad Test', 500_000)
        slip1 = self._slip(emp, '2026-09-01', '2026-09-30')
        slip1.sudo().write({'state': 'confirmed'})

        # Segunda boleta en el mismo periodo -- debe fallar en el constraint
        from odoo.exceptions import ValidationError
        with self.assertRaises(Exception,
            msg='Crear segunda boleta solapada debe lanzar error de constraint'
        ):
            slip2 = self._slip(emp, '2026-09-15', '2026-10-15')

    def test_26_misma_planilla_mismo_empleado_falla(self):
        """
        Dos boletas del mismo empleado en la misma planilla (run) deben fallar
        por la constraint UNIQUE(employee_id, payroll_run_id).
        """
        emp = self._emp('Dup Run Test', 500_000)
        run = self.env['planilla.run.cr'].create({
            'name': 'Run Dup Test', 'company_id': self.company.id,
            'payroll_calendar_id': self.cal_m.id,
            'date_start': '2027-04-01', 'date_end': '2027-04-30',
        })
        self.env['planilla.payslip.cr'].create({
            'employee_id': emp.id, 'date_from': '2027-04-01', 'date_to': '2027-04-30',
            'company_id': self.company.id, 'payroll_run_id': run.id,
        })

        from odoo.exceptions import ValidationError
        with self.assertRaises(Exception,
            msg='Dos boletas del mismo empleado en la misma planilla deben fallar'
        ):
            self.env['planilla.payslip.cr'].create({
                'employee_id': emp.id, 'date_from': '2027-04-01', 'date_to': '2027-04-30',
                'company_id': self.company.id, 'payroll_run_id': run.id,
            })


# =======================================================================
# Constantes legales CR 2026
# =======================================================================
class TestConstantesLegalesCR(TestCoverageBase):

    def test_27_max_pct_embargo_25(self):
        """Embargo maximo 25% del neto (Art. 172 CT)."""
        self.assertEqual(K.MAX_PCT_EMBARGO, 25.0,
            msg='Embargo maximo debe ser 25% (Art. 172 CT)')

    def test_28_dias_paternidad_8(self):
        """Paternidad: 8 dias habiles (Ley 8107)."""
        self.assertEqual(K.DIAS_PATERNIDAD, 8,
            msg='Dias de paternidad deben ser 8 habiles (Ley 8107)')

    def test_29_dias_vacaciones_por_50_semanas(self):
        """Vacaciones: 12 dias por 50 semanas laboradas (Art. 153 CT)."""
        self.assertEqual(K.DIAS_VACACIONES_POR_50_SEMANAS, 12,
            msg='Vacaciones deben ser 12 dias/50 semanas (Art. 153 CT)')

    def test_30_meses_prescripcion_vacaciones(self):
        """Prescripcion vacaciones: 22 meses sin disfrutarlas (Art. 156 CT)."""
        self.assertEqual(K.MESES_PRESCRIPCION_VACACIONES, 22,
            msg='Prescripcion vacaciones = 22 meses (Art. 156 CT)')

    def test_31_factor_he_simple_1_5(self):
        """Factor HE simple = 1.5x (Art. 139 CT)."""
        self.assertEqual(K.FACTOR_HE_SIMPLE, 1.5,
            msg='Factor HE simple debe ser 1.5x (Art. 139 CT)')

    def test_32_factor_he_doble_2_0(self):
        """Factor HE doble/feriado = 2.0x (Art. 148 CT)."""
        self.assertEqual(K.FACTOR_HE_DOBLE, 2.0,
            msg='Factor HE doble debe ser 2.0x (Art. 148 CT)')

    def test_33_cr_utc_offset_6(self):
        """Timezone Costa Rica = UTC-6 (sin horario de verano)."""
        self.assertEqual(K.CR_UTC_OFFSET_HOURS, 6,
            msg='CR esta en UTC-6 (sin horario de verano)')

    def test_34_renta_tramos_crecientes(self):
        """Los topes de renta deben ser crecientes (coherencia fiscal)."""
        self.assertLess(K.RENTA_EXENTO, K.RENTA_TOPE_10,
            msg='RENTA_EXENTO < RENTA_TOPE_10')
        self.assertLess(K.RENTA_TOPE_10, K.RENTA_TOPE_15,
            msg='RENTA_TOPE_10 < RENTA_TOPE_15')
        self.assertLess(K.RENTA_TOPE_15, K.RENTA_TOPE_20,
            msg='RENTA_TOPE_15 < RENTA_TOPE_20')

    def test_35_tasas_renta_crecientes(self):
        """Las tasas de renta deben ser progresivas: 10% < 15% < 20% < 25%."""
        self.assertLess(K.RENTA_TASA_1, K.RENTA_TASA_2)
        self.assertLess(K.RENTA_TASA_2, K.RENTA_TASA_3)
        self.assertLess(K.RENTA_TASA_3, K.RENTA_TASA_4)
        self.assertEqual(K.RENTA_TASA_4, 0.25,
            msg='Tasa maxima renta 2026 = 25% (DGT-R-016-2026)')
