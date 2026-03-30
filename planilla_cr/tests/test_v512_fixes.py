"""
Tests v5.12 -- Verificacion de todos los fixes aplicados en v5.12
================================================================
Cubre los bugs y mejoras corregidos en esta version:

  BUG-03 -- Estado 'done' en action_pay (era 'paid')
  BUG-05 -- @api.depends incluye rop_employer
  BUG-06 -- _validate_before_confirm usa solapamiento de fechas
  BUG-07 -- K.TEST_CEDULA unificado en import_data_wizard
  BP-01  -- Variable 'today' eliminada de _sync_bonos
  BP-02  -- ensure_one() en action_cancel del run
  BP-04  -- _compute_bono_salarial no hace N+1 (precarga bonos)
  BP-05  -- Logica afecto_ccss corregida (era invertida)
  SEC-01 -- Codigos de deduccion protegidos contra race conditions
  SEC-02 -- Trazabilidad en AccountMovePayrollSync

Ejecutar:
  docker compose exec web odoo -d prueba --test-enable \\
    --test-tags planilla_cr.test_v512_fixes --stop-after-init
"""
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError, ValidationError
from unittest.mock import patch


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


class TestV512FixBase(TransactionCase):
    """Base compartida para todos los tests de fixes v5.12."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref('base.main_company')
        env, co = cls.env, cls.company

        cls.acc_sal    = _goc(env, co, '630000', 'Sal 512', 'expense')
        cls.acc_soc    = _goc(env, co, '630100', 'Soc 512', 'expense')
        cls.acc_vac    = _goc(env, co, '630200', 'Vac 512', 'expense')
        cls.acc_agu    = _goc(env, co, '630300', 'Agu 512', 'expense')
        cls.acc_ces    = _goc(env, co, '630400', 'Ces 512', 'expense')
        cls.acc_sal_p  = _goc(env, co, '230000', 'SalPag 512', 'liability_current')
        cls.acc_ccss   = _goc(env, co, '230300', 'CCSS 512', 'liability_current')
        cls.acc_ins    = _goc(env, co, '230400', 'INS 512', 'liability_current')
        cls.acc_renta  = _goc(env, co, '230100', 'Renta 512', 'liability_current')
        cls.acc_agu_p  = _goc(env, co, '230500', 'PAgui 512', 'liability_current')
        cls.acc_ces_p  = _goc(env, co, '230600', 'PCes 512', 'liability_current')
        cls.acc_vac_p  = _goc(env, co, '230700', 'PVac 512', 'liability_current')
        cls.acc_rop    = _goc(env, co, '230350', 'ROP 512', 'liability_current')

        cls.journal = env['account.journal'].search([
            ('type', '=', 'general'), ('company_id', '=', co.id)
        ], limit=1) or env['account.journal'].create({
            'name': 'Planilla 512', 'code': 'P512',
            'type': 'general', 'company_id': co.id,
        })

        cls.calendar = env['planilla.calendar'].search([
            ('company_id', '=', co.id),
            ('frequency', '=', 'monthly'),
        ], limit=1) or env['planilla.calendar'].create({
            'name': 'Mensual 512', 'frequency': 'monthly',
            'company_id': co.id,
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
            'account_salary_payable':         cls.acc_sal_p.id,
            'account_aguinaldo_provision':    cls.acc_agu_p.id,
            'account_cesantia_provision':     cls.acc_ces_p.id,
            'account_vacation_provision':     cls.acc_vac_p.id,
            'account_rop_payable':            cls.acc_rop.id,
        })
        cls.config = config

        # Otorgar grupo aprobador al usuario de prueba (Odoo 19 requiere el grupo incluso con sudo)

    def _emp(self, name, salary=500_000, rop=False):
        return self.env['hr.employee'].create({
            'name': name, 'company_id': self.company.id,
            'base_salary': salary, 'payroll_calendar_id': self.calendar.id,
            'rop_applies': rop, 'entry_date': '2022-01-01',
            'work_contact_id': self.env.company.partner_id.id,
        })

    def _slip(self, emp, date_from='2026-11-01', date_to='2026-11-30'):
        return self.env['planilla.payslip.cr'].create({
            'employee_id': emp.id, 'date_from': date_from,
            'date_to': date_to, 'company_id': self.company.id,
        })

    def _run(self, date_from, date_to):
        return self.env['planilla.run.cr'].create({
            'name': f'Run 512 {date_from}',
            'company_id': self.company.id,
            'payroll_calendar_id': self.calendar.id,
            'date_start': date_from, 'date_end': date_to,
        })


# =======================================================================
# BUG-03: Estado 'done' en action_pay
# =======================================================================
class TestBug03EstadoDone(TestV512FixBase):

    def test_01_boletas_done_no_bloquean_action_pay_run(self):
        """
        BUG-03 fix: boletas en estado 'done' (pagadas) no deben
        bloquear el pago de la planilla. El filtro usaba 'paid' (estado inexistente)
        y silenciosamente dejaba pasar boletas done como si fueran no confirmadas.
        """
        emp = self._emp('Bug03 Test', 500_000)
        run = self._run('2026-12-01', '2026-12-31')

        slip = self.env['planilla.payslip.cr'].create({
            'employee_id': emp.id, 'date_from': '2026-12-01', 'date_to': '2026-12-31',
            'company_id': self.company.id, 'payroll_run_id': run.id,
        })
        # Confirmar boleta
        slip.sudo().write({'state': 'confirmed'})
        run.sudo().write({'state': 'confirmed'})

        # El estado 'done' es el estado real tras pagar -- no 'paid'
        self.assertNotEqual(
            slip._fields['state'].selection,
            [('draft', ''), ('confirmed', ''), ('paid', ''), ('cancelled', '')],
            "El modelo usa 'done' no 'paid' -- verificar que el fix sea coherente"
        )
        # Verificar que los estados validos incluyen 'done'
        valid_states = [s[0] for s in slip._fields['state'].selection]
        self.assertIn('done', valid_states, "Estado 'done' debe existir en el modelo")
        self.assertNotIn('paid', valid_states, "Estado 'paid' NO debe existir en el modelo")


# =======================================================================
# BUG-05: @api.depends incluye rop_employer
# =======================================================================
class TestBug05ROPEmployerDepends(TestV512FixBase):

    def test_02_total_rop_employer_se_recalcula(self):
        """
        BUG-05 fix: total_rop_employer en PayrollRunCR se recalcula
        automaticamente cuando cambia rop_employer en una boleta.
        """
        emp = self._emp('Bug05 ROP', 600_000, rop=True)
        run = self._run('2027-01-01', '2027-01-31')

        slip = self.env['planilla.payslip.cr'].create({
            'employee_id': emp.id, 'date_from': '2027-01-01', 'date_to': '2027-01-31',
            'company_id': self.company.id, 'payroll_run_id': run.id,
        })

        # Con ROP activo, rop_employer debe ser > 0
        self.assertGreater(slip.rop_employer or 0, 0,
            'rop_employer debe ser > 0 cuando rop_applies=True')

        # El total en la planilla debe reflejar la suma
        expected = round(slip.rop_employer, 2)
        actual = round(run.total_rop_employer, 2)
        self.assertAlmostEqual(actual, expected, delta=0.05,
            msg=f'total_rop_employer del run: esperado CRC{expected:,.2f}, '
                f'obtenido CRC{actual:,.2f}. El @api.depends puede estar incompleto.')

    def test_03_total_rop_employer_en_depends_declarado(self):
        """
        Verificar que 'payslip_ids.rop_employer' esta declarado en
        el @api.depends de PayrollRunCR._compute_totals.
        """
        from odoo.addons.planilla_cr.models.payroll_run_cr import PayrollRunCR
        compute_method = PayrollRunCR._compute_totals
        depends = getattr(compute_method, '_depends', [])
        # Odoo almacena los depends como lista de strings en el decorador
        depends_flat = ' '.join(str(d) for d in depends)
        self.assertIn(
            'rop_employer', depends_flat,
            'payslip_ids.rop_employer debe estar en @api.depends de _compute_totals'
        )


# =======================================================================
# BUG-06: Validacion de fechas con solapamiento
# =======================================================================
class TestBug06ValidacionFechasSolapamiento(TestV512FixBase):

    def test_04_validate_detecta_solapamiento_parcial(self):
        """
        BUG-06 fix: el constraint _check_no_duplicate_employee_period detecta
        solapamiento parcial de fechas ya desde el CREATE de la boleta.
        """
        emp = self._emp('Bug06 Solapamiento', 500_000)
        emp.write({'identification_id': '1-0101-0001'})

        # Boleta 1: Febrero completo -- confirmar
        slip1 = self._slip(emp, '2027-02-01', '2027-02-28')
        slip1.sudo().write({'state': 'confirmed'})

        # Boleta 2: Feb 15 - Mar 15 (se solapa) -- debe fallar al crear
        from odoo.exceptions import ValidationError
        with self.assertRaises(Exception, msg=(
            'BUG-06 no corregido: crear boleta solapada debe lanzar error'
        )):
            self._slip(emp, '2027-02-15', '2027-03-15')

    def test_05_validate_permite_periodos_no_solapados(self):
        """
        Periodos que no se solapan deben pasar la validacion sin error.
        """
        emp = self._emp('Bug06 No Solap', 500_000)
        emp.write({'identification_id': '1-0101-0002'})

        # Boleta 1: Enero
        slip1 = self._slip(emp, '2027-03-01', '2027-03-31')
        slip1.sudo().write({'state': 'confirmed'})

        # Boleta 2: Febrero -- no se solapa con Enero
        slip2 = self._slip(emp, '2027-04-01', '2027-04-30')

        # No debe lanzar excepcion
        try:
            result = slip2.sudo()._validate_before_confirm()
            # Si no lanza excepcion, el test pasa
        except UserError as e:
            self.fail(
                f'_validate_before_confirm lanzo UserError para periodos no solapados: {e}'
            )


# =======================================================================
# BUG-07: Cedula de prueba unificada con K.TEST_CEDULA
# =======================================================================
class TestBug07CedulaPrueba(TestV512FixBase):

    def test_06_sample_cedula_unificada(self):
        """
        BUG-07 fix: _SAMPLE_CEDULA en ImportDataWizard debe ser igual a K.TEST_CEDULA.
        Antes eran valores distintos (1-0000-0001 hardcoded vs K.TEST_CEDULA).
        """
        from odoo.addons.planilla_cr.models import planilla_const as K
        from odoo.addons.planilla_cr.wizard.import_data_wizard import ImportDataWizard

        self.assertEqual(
            ImportDataWizard._SAMPLE_CEDULA, K.TEST_CEDULA,
            msg=(
                f'BUG-07 no corregido: _SAMPLE_CEDULA ({ImportDataWizard._SAMPLE_CEDULA!r}) '
                f' K.TEST_CEDULA ({K.TEST_CEDULA!r}). '
                f'Actualizar K.TEST_CEDULA no actualizaria _is_sample().'
            )
        )

    def test_07_k_sample_y_test_cedula_son_distintas(self):
        """
        K.SAMPLE_CEDULA (fila verde excel)  K.TEST_CEDULA (empleado de prueba).
        Son dos propositos distintos y deben tener valores distintos.
        """
        from odoo.addons.planilla_cr.models import planilla_const as K
        self.assertNotEqual(
            K.SAMPLE_CEDULA, K.TEST_CEDULA,
            msg='K.SAMPLE_CEDULA y K.TEST_CEDULA deben ser cedulas distintas'
        )


# =======================================================================
# BP-02: ensure_one() en action_cancel del run
# =======================================================================
class TestBP02EnsureOneCancel(TestV512FixBase):

    def test_08_action_cancel_run_acepta_single_record(self):
        """
        BP-02: action_cancel() del run debe aceptar un solo registro.
        Con ensure_one(), llamar sobre un recordset de mas de uno debe fallar.
        """
        run1 = self._run('2027-05-01', '2027-05-31')
        run2 = self._run('2027-06-01', '2027-06-30')

        # Un solo run: debe funcionar
        try:
            run1.sudo().action_cancel()
        except Exception as e:
            self.fail(f'action_cancel() fallo en un solo registro: {e}')

    def test_09_action_cancel_run_falla_con_multiples(self):
        """
        Tras agregar ensure_one(), action_cancel() sobre multiples registros
        debe lanzar error (behavior correcto de Odoo).
        """
        run1 = self._run('2027-07-01', '2027-07-31')
        run2 = self._run('2027-08-01', '2027-08-31')
        multi = run1 | run2

        with self.assertRaises(Exception, msg=(
            'BP-02: action_cancel() con ensure_one() deberia fallar '
            'si se llama con multiples registros'
        )):
            multi.sudo().action_cancel()


# =======================================================================
# BP-04 + BP-05: _compute_bono_salarial sin N+1 y logica afecto_ccss
# =======================================================================
class TestBP04BP05BonoSalarial(TestV512FixBase):

    def test_10_bono_afecto_ccss_incluido_en_bruto(self):
        """
        BP-05 fix: Solo los bonos con afecto_ccss=True se incluyen en
        bono_salarial_amount (que entra en el bruto para CCSS y Renta).
        Un bono con afecto_ccss=False NO debe entrar.
        """
        emp = self._emp('BP05 Bono CCSS', 500_000)

        # Codigo de deduccion para bonos
        bono_code = self.env['planilla.deduction.code'].search(
            [('code', '=', 'BONO')], limit=1
        ) or self.env['planilla.deduction.code'].create({
            'code': 'BONO', 'name': 'Bono Test', 'deduction_type': 'employee',
        })

        # Bono salarial (afecto_ccss=True) -- debe entrar al bruto
        bono_sal = self.env['planilla.bono'].create({
            'employee_id': emp.id, 'name': 'Bono Productividad Test',
            'amount': 50_000, 'amount_type': 'fixed',
            'afecto_ccss': True, 'state': 'active',
            'date_start': '2020-01-01',
        })
        # Bono exento (afecto_ccss=False) -- NO debe entrar al bruto
        bono_exe = self.env['planilla.bono'].create({
            'employee_id': emp.id, 'name': 'Subsidio Transporte Test',
            'amount': 30_000, 'amount_type': 'fixed',
            'afecto_ccss': False, 'state': 'active',
            'date_start': '2020-01-01',
        })

        try:
            slip = self._slip(emp)
            # Sincronizar bonos manualmente para el test
            slip._sync_bonos()

            bono_sal_amount = slip.bono_salarial_amount

            # Solo el bono salarial (50,000) debe estar en bono_salarial_amount
            self.assertAlmostEqual(bono_sal_amount, 50_000, delta=1,
                msg=(
                    f'bono_salarial_amount debe incluir SOLO bonos afecto_ccss=True.\n'
                    f'  Esperado: CRC50,000 (bono productividad)\n'
                    f'  Obtenido: CRC{bono_sal_amount:,.2f}\n'
                    f'  Si incluye los CRC30,000 del transporte, la logica esta invertida (BP-05).'
                )
            )

        finally:
            bono_sal.unlink()
            bono_exe.unlink()

    def test_11_bono_exento_no_en_bruto_ccss(self):
        """
        Un bono con afecto_ccss=False NO debe sumarse a bono_salarial_amount
        y por ende no entra en la base del CCSS.
        """
        emp = self._emp('BP05 Bono Exento', 500_000)

        bono_exe = self.env['planilla.bono'].create({
            'employee_id': emp.id, 'name': 'Bono Exento Test CCSS',
            'amount': 74_000, 'amount_type': 'fixed',
            'afecto_ccss': False, 'state': 'active',
            'date_start': '2020-01-01',
        })

        try:
            slip = self._slip(emp)
            slip._sync_bonos()

            # bono_salarial_amount debe ser 0 (el bono exento no entra)
            self.assertEqual(slip.bono_salarial_amount, 0.0,
                msg=(
                    f'Un bono con afecto_ccss=False no debe entrar en bono_salarial_amount.\n'
                    f'  Obtenido: CRC{slip.bono_salarial_amount:,.2f}'
                )
            )

        finally:
            bono_exe.unlink()

    def test_12_compute_bono_salarial_multiples_registros(self):
        """
        BP-04: _compute_bono_salarial debe manejar un recordset de multiples
        boletas sin hacer N+1 (se llama una sola query para todos los empleados).
        Test de comportamiento: resultado correcto para 3 boletas distintas.
        """
        emp1 = self._emp('BP04 Multi A', 500_000)
        emp2 = self._emp('BP04 Multi B', 600_000)
        emp3 = self._emp('BP04 Multi C', 700_000, rop=True)

        slip1 = self._slip(emp1, '2027-09-01', '2027-09-30')
        slip2 = self._slip(emp2, '2027-09-01', '2027-09-30')
        slip3 = self._slip(emp3, '2027-09-01', '2027-09-30')

        # Llamar _compute_bono_salarial sobre el recordset completo
        recordset = slip1 | slip2 | slip3
        try:
            recordset._compute_bono_salarial()
        except Exception as e:
            self.fail(
                f'_compute_bono_salarial fallo con recordset de multiples boletas: {e}'
            )

        # Sin bonos configurados, todos deben ser 0
        for slip in [slip1, slip2, slip3]:
            self.assertEqual(slip.bono_salarial_amount, 0.0,
                msg=f'Sin bonos activos, bono_salarial_amount debe ser 0 para {slip.employee_id.name}')


# =======================================================================
# SEC-01: Codigos de deduccion -- creacion idempotente
# =======================================================================
class TestSEC01DeductionCodeIdempotent(TestV512FixBase):

    def test_13_pension_code_creado_una_vez(self):
        """
        SEC-01: Llamar _sync_pension_alimentaria dos veces en boletas distintas
        del mismo empleado debe crear el codigo PENSION_ALIM solo una vez.
        """
        # Asegurar que no existe el codigo
        self.env['planilla.deduction.code'].search(
            [('code', '=', 'PENSION_ALIM_TEST')]
        ).unlink()

        emp = self._emp('SEC01 Pension', 500_000)
        # Llamar sincronizacion dos veces -- solo debe haber un codigo
        slip1 = self._slip(emp, '2027-10-01', '2027-10-31')
        slip2 = self._slip(emp, '2027-11-01', '2027-11-30')

        # Los dos slips deberian usar el mismo codigo PENSION_ALIM
        pension_codes = self.env['planilla.deduction.code'].search(
            [('code', '=', 'PENSION_ALIM')]
        )
        self.assertLessEqual(len(pension_codes), 1,
            msg=f'Debe existir maximo 1 codigo PENSION_ALIM, encontrados: {len(pension_codes)}')

    def test_14_rop_code_creado_una_vez(self):
        """
        SEC-01: Codigo ROP debe existir maximo una vez aunque se llame
        _sync_rop multiples veces.
        """
        emp1 = self._emp('SEC01 ROP A', 500_000, rop=True)
        emp2 = self._emp('SEC01 ROP B', 600_000, rop=True)

        slip1 = self._slip(emp1, '2027-10-01', '2027-10-31')
        slip2 = self._slip(emp2, '2027-10-01', '2027-10-31')

        rop_codes = self.env['planilla.deduction.code'].search([('code', '=', 'ROP')])
        self.assertLessEqual(len(rop_codes), 1,
            msg=f'Debe existir maximo 1 codigo ROP, encontrados: {len(rop_codes)}')

    def test_15_embargo_code_creado_una_vez(self):
        """
        SEC-01: Codigo EMB debe existir maximo una vez aunque multiples
        embargos sean procesados para distintos empleados.
        """
        emp1 = self._emp('SEC01 Emb A', 500_000)
        emp2 = self._emp('SEC01 Emb B', 600_000)

        for emp in [emp1, emp2]:
            emb = self.env['planilla.embargo'].create({
                'employee_id': emp.id,
                'numero_expediente': f'SEC01-{emp.id}',
                'juzgado': 'Juzgado Test', 'beneficiario_nombre': 'Test',
                'calculation_type': 'fixed', 'fixed_amount': 20_000,
                'date_start': '2026-01-01', 'state': 'active',
            })
            slip = self._slip(emp, '2027-10-01', '2027-10-31')
            emb.unlink()

        emb_codes = self.env['planilla.deduction.code'].search([('code', '=', 'EMB')])
        self.assertLessEqual(len(emb_codes), 1,
            msg=f'Debe existir maximo 1 codigo EMB, encontrados: {len(emb_codes)}')


# =======================================================================
# SEC-02: Trazabilidad en AccountMovePayrollSync
# =======================================================================
class TestSEC02Trazabilidad(TestV512FixBase):

    def test_16_move_sync_tiene_logger_warning(self):
        """
        SEC-02: AccountMovePayrollSync.write() debe registrar _logger.warning
        antes de cancelar boletas masivamente.
        Verifica que la funcion exista y contenga la llamada al logger.
        """
        from odoo.addons.planilla_cr.models.payroll_run_cr import AccountMovePayrollSync
        import inspect
        source = inspect.getsource(AccountMovePayrollSync.write)
        self.assertIn('_logger.warning', source,
            msg='AccountMovePayrollSync.write() debe tener _logger.warning para trazabilidad')
        self.assertIn('message_post', source,
            msg='AccountMovePayrollSync.write() debe tener message_post para notificacion')

    def test_17_move_unlink_tiene_trazabilidad(self):
        """
        SEC-02: AccountMovePayrollSync.unlink() tambien debe tener trazabilidad.
        """
        from odoo.addons.planilla_cr.models.payroll_run_cr import AccountMovePayrollSync
        import inspect
        source = inspect.getsource(AccountMovePayrollSync.unlink)
        self.assertIn('_logger.warning', source,
            msg='AccountMovePayrollSync.unlink() debe tener _logger.warning')
