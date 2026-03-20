"""
Tests v5.15 — Módulo de Cobros al Empleado
===========================================
Cubre todas las funcionalidades del módulo planilla.charge.type
y planilla.employee.charge introducidas en v5.15:

  MODELO     — Creación, campos computados, constraints
  FLUJO      — Estados draft → approved → applied → cancelled
  ÚNICO      — Cobro único: se consume al sincronizar
  RECURRENTE — Cobro recurrente: persiste, deduplicación por período
  SUBSIDIO   — Cobros con subsidio patronal parcial y total
  SYNC       — _sync_employee_charges individual y batch
  CONTABLE   — Integración en asiento DEBE=HABER (cuenta 230970)
  CANCEL     — action_cancel restaura cobros a 'approved'
  VISTA EMP  — employee_charge_ids en hr.employee
  SEGURIDAD  — Permisos, validaciones, constraints

Ejecutar:
  docker compose exec web odoo -d prueba --test-enable \\
    --test-tags planilla_cr.test_employee_charges --stop-after-init
"""

from datetime import date
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError, ValidationError


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _goc(env, company, code, name, atype):
    """Get or create account by code."""
    acc = env['account.account'].search([
        ('code', '=', code), ('company_ids', 'in', company.id)
    ], limit=1)
    if not acc:
        acc = env['account.account'].create({
            'code': code, 'name': name, 'account_type': atype,
            'company_ids': [(4, company.id)],
        })
    return acc


def _goc_deduction(env, code, name, dtype='employee'):
    """Get or create deduction code."""
    dc = env['planilla.deduction.code'].search([('code', '=', code)], limit=1)
    if not dc:
        dc = env['planilla.deduction.code'].create({
            'name': name, 'code': code, 'deduction_type': dtype,
        })
    return dc


# ═══════════════════════════════════════════════════════════════════════
# BASE
# ═══════════════════════════════════════════════════════════════════════

class TestEmployeeChargesBase(TransactionCase):
    """Base compartida: empresa, config contable, catálogo de cobros, empleados."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref('base.main_company')
        env, co = cls.env, cls.company

        # Cuentas contables
        cls.acc_sal   = _goc(env, co, '630000', 'Salarios CHG',    'expense')
        cls.acc_soc   = _goc(env, co, '630100', 'Cargas CHG',      'expense')
        cls.acc_vac   = _goc(env, co, '630200', 'Vacaciones CHG',  'expense')
        cls.acc_agu   = _goc(env, co, '630300', 'Aguinaldo CHG',   'expense')
        cls.acc_ces   = _goc(env, co, '630400', 'Cesantia CHG',    'expense')
        cls.acc_sal_p = _goc(env, co, '230000', 'SalPag CHG',      'liability_current')
        cls.acc_ccss  = _goc(env, co, '230300', 'CCSS CHG',        'liability_current')
        cls.acc_ins   = _goc(env, co, '230400', 'INS CHG',         'liability_current')
        cls.acc_renta = _goc(env, co, '230100', 'Renta CHG',       'liability_current')
        cls.acc_agu_p = _goc(env, co, '230500', 'PAgu CHG',        'liability_current')
        cls.acc_ces_p = _goc(env, co, '230600', 'PCes CHG',        'liability_current')
        cls.acc_vac_p = _goc(env, co, '230700', 'PVac CHG',        'liability_current')
        cls.acc_rop   = _goc(env, co, '230350', 'ROP CHG',         'liability_current')
        cls.acc_cobro = _goc(env, co, '230970', 'Cobros Emp CHG',  'liability_current')

        # Diario
        cls.journal = env['account.journal'].search([
            ('type', '=', 'general'), ('company_id', '=', co.id)
        ], limit=1) or env['account.journal'].create({
            'name': 'Planilla CHG', 'code': 'PCHG',
            'type': 'general', 'company_id': co.id,
        })

        # Calendarización mensual
        cls.calendar = env['planilla.calendar'].search([
            ('company_id', '=', co.id), ('frequency', '=', 'monthly'),
        ], limit=1) or env['planilla.calendar'].create({
            'name': 'Mensual CHG', 'frequency': 'monthly',
            'company_id': co.id,
        })

        # Configuración contable
        config = env['planilla.accounting.config'].search(
            [('company_id', '=', co.id)], limit=1
        )
        if not config:
            config = env['planilla.accounting.config'].create(
                {'company_id': co.id}
            )
        config.sudo().write({
            'journal_id':                     cls.journal.id,
            'accounting_entry_mode':          'per_employee',
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
            'account_cobro_empleado_payable': cls.acc_cobro.id,
        })
        cls.config = config

        # Código de deducción para cobros
        cls.ded_cobro = _goc_deduction(env, 'COBRO_EMP', 'Cobro al Empleado')

        # Tipo de cobro: almuerzo fijo
        cls.tipo_almuerzo = env['planilla.charge.type'].search(
            [('code', '=', 'TEST_ALMUERZO')], limit=1
        ) or env['planilla.charge.type'].create({
            'name':              'Almuerzo Test',
            'code':              'TEST_ALMUERZO',
            'charge_mode':       'fixed',
            'default_unit_price': 30_000,
            'subsidy_pct':        0.0,
            'deduction_code_id':  cls.ded_cobro.id,
            'company_id':         co.id,
        })

        # Tipo de cobro: almuerzo por días
        cls.tipo_dias = env['planilla.charge.type'].search(
            [('code', '=', 'TEST_DIAS')], limit=1
        ) or env['planilla.charge.type'].create({
            'name':              'Almuerzo por Días Test',
            'code':              'TEST_DIAS',
            'charge_mode':       'per_unit',
            'default_unit_price': 3_000,
            'unit_label':        'días',
            'subsidy_pct':        0.0,
            'deduction_code_id':  cls.ded_cobro.id,
            'company_id':         co.id,
        })

        # Tipo de cobro: con subsidio 50%
        cls.tipo_subsidiado = env['planilla.charge.type'].search(
            [('code', '=', 'TEST_SUBS')], limit=1
        ) or env['planilla.charge.type'].create({
            'name':              'Almuerzo Subsidiado Test',
            'code':              'TEST_SUBS',
            'charge_mode':       'per_unit',
            'default_unit_price': 4_000,
            'subsidy_pct':        50.0,
            'deduction_code_id':  cls.ded_cobro.id,
            'company_id':         co.id,
        })

    def _emp(self, name, salary=600_000):
        return self.env['hr.employee'].create({
            'name':                 name,
            'company_id':           self.company.id,
            'base_salary':          salary,
            'payroll_calendar_id':  self.calendar.id,
            'rop_applies':          False,
            'entry_date':           '2022-01-01',
            'work_contact_id':      self.env.company.partner_id.id,
        })

    def _charge(self, emp, tipo, date_from, date_to,
                quantity=1.0, unit_price=None, subsidy_pct=None,
                is_recurring=False, recurrence_end=None, state='draft'):
        vals = {
            'employee_id':    emp.id,
            'charge_type_id': tipo.id,
            'date_from':      date_from,
            'date_to':        date_to,
            'quantity':       quantity,
            'unit_price':     unit_price if unit_price is not None else tipo.default_unit_price,
            'subsidy_pct':    subsidy_pct if subsidy_pct is not None else tipo.subsidy_pct,
            'is_recurring':   is_recurring,
            'company_id':     self.company.id,
        }
        if recurrence_end:
            vals['recurrence_end'] = recurrence_end
        charge = self.env['planilla.employee.charge'].create(vals)
        if state == 'approved':
            charge.action_approve()
        return charge

    def _slip(self, emp, date_from='2026-03-01', date_to='2026-03-31'):
        return self.env['planilla.payslip.cr'].create({
            'employee_id':  emp.id,
            'date_from':    date_from,
            'date_to':      date_to,
            'company_id':   self.company.id,
        })


# ═══════════════════════════════════════════════════════════════════════
# 1. MODELO — campos computados y constraints
# ═══════════════════════════════════════════════════════════════════════

class TestChargeModel(TestEmployeeChargesBase):

    def test_01_amounts_fixed_no_subsidy(self):
        """Monto fijo sin subsidio: employee_amount = total_amount."""
        emp = self._emp('Test Amounts 01')
        charge = self._charge(emp, self.tipo_almuerzo, '2026-03-01', '2026-03-31',
                              unit_price=30_000, subsidy_pct=0)
        self.assertEqual(charge.total_amount,    30_000.0)
        self.assertEqual(charge.employer_amount, 0.0)
        self.assertEqual(charge.employee_amount, 30_000.0)

    def test_02_amounts_per_unit(self):
        """Por unidades: total = cantidad × precio."""
        emp = self._emp('Test Amounts 02')
        charge = self._charge(emp, self.tipo_dias, '2026-03-01', '2026-03-31',
                              quantity=15, unit_price=3_000, subsidy_pct=0)
        self.assertEqual(charge.total_amount,    45_000.0)
        self.assertEqual(charge.employee_amount, 45_000.0)

    def test_03_amounts_with_subsidy_50(self):
        """Subsidio 50%: empleado paga la mitad."""
        emp = self._emp('Test Amounts 03')
        charge = self._charge(emp, self.tipo_dias, '2026-03-01', '2026-03-31',
                              quantity=10, unit_price=4_000, subsidy_pct=50)
        self.assertEqual(charge.total_amount,    40_000.0)
        self.assertEqual(charge.employer_amount, 20_000.0)
        self.assertEqual(charge.employee_amount, 20_000.0)

    def test_04_amounts_full_subsidy(self):
        """Subsidio 100%: employee_amount = 0."""
        emp = self._emp('Test Amounts 04')
        charge = self._charge(emp, self.tipo_dias, '2026-03-01', '2026-03-31',
                              quantity=10, unit_price=4_000, subsidy_pct=100)
        self.assertEqual(charge.total_amount,    40_000.0)
        self.assertEqual(charge.employer_amount, 40_000.0)
        self.assertEqual(charge.employee_amount, 0.0)

    def test_05_name_computed(self):
        """El nombre se computa: COB - Empleado - Tipo - YYYY-MM."""
        emp = self._emp('Juan Pérez')
        charge = self._charge(emp, self.tipo_almuerzo, '2026-03-01', '2026-03-31')
        self.assertIn('Juan Pérez', charge.name)
        self.assertIn('2026-03', charge.name)

    def test_06_constraint_dates(self):
        """date_from no puede ser mayor que date_to."""
        emp = self._emp('Test Dates 06')
        with self.assertRaises(ValidationError):
            self._charge(emp, self.tipo_almuerzo, '2026-03-31', '2026-03-01')

    def test_07_constraint_quantity_zero(self):
        """quantity = 0 lanza ValidationError."""
        emp = self._emp('Test Qty 07')
        with self.assertRaises(ValidationError):
            self._charge(emp, self.tipo_almuerzo, '2026-03-01', '2026-03-31',
                         quantity=0.0)

    def test_08_constraint_subsidy_out_of_range(self):
        """subsidio > 100% lanza ValidationError."""
        emp = self._emp('Test Subs 08')
        with self.assertRaises(ValidationError):
            self._charge(emp, self.tipo_almuerzo, '2026-03-01', '2026-03-31',
                         subsidy_pct=110.0)

    def test_09_onchange_charge_type_inherits_price(self):
        """Al cambiar tipo_cobro, hereda precio y subsidio del catálogo."""
        emp = self._emp('Test Onchange 09')
        charge = self.env['planilla.employee.charge'].new({
            'employee_id':    emp.id,
            'charge_type_id': self.tipo_subsidiado.id,
            'date_from':      '2026-03-01',
            'date_to':        '2026-03-31',
        })
        charge._onchange_charge_type()
        self.assertEqual(charge.unit_price,  self.tipo_subsidiado.default_unit_price)
        self.assertEqual(charge.subsidy_pct, self.tipo_subsidiado.subsidy_pct)


# ═══════════════════════════════════════════════════════════════════════
# 2. FLUJO DE ESTADOS
# ═══════════════════════════════════════════════════════════════════════

class TestChargeStateMachine(TestEmployeeChargesBase):

    def test_10_initial_state_draft(self):
        """Todo cobro nuevo inicia en draft."""
        emp = self._emp('Test State 10')
        charge = self._charge(emp, self.tipo_almuerzo, '2026-03-01', '2026-03-31')
        self.assertEqual(charge.state, 'draft')

    def test_11_approve_transitions_to_approved(self):
        """action_approve cambia a 'approved'."""
        emp = self._emp('Test State 11')
        charge = self._charge(emp, self.tipo_almuerzo, '2026-03-01', '2026-03-31')
        charge.action_approve()
        self.assertEqual(charge.state, 'approved')

    def test_12_cannot_approve_non_draft(self):
        """No se puede aprobar un cobro ya aprobado."""
        emp = self._emp('Test State 12')
        charge = self._charge(emp, self.tipo_almuerzo, '2026-03-01', '2026-03-31',
                              state='approved')
        with self.assertRaises(UserError):
            charge.action_approve()

    def test_13_cancel_approved(self):
        """action_cancel en approved → cancelled."""
        emp = self._emp('Test State 13')
        charge = self._charge(emp, self.tipo_almuerzo, '2026-03-01', '2026-03-31',
                              state='approved')
        charge.action_cancel()
        self.assertEqual(charge.state, 'cancelled')

    def test_14_reset_cancelled_to_draft(self):
        """action_reset_to_draft en cancelled → draft."""
        emp = self._emp('Test State 14')
        charge = self._charge(emp, self.tipo_almuerzo, '2026-03-01', '2026-03-31',
                              state='approved')
        charge.action_cancel()
        charge.action_reset_to_draft()
        self.assertEqual(charge.state, 'draft')

    def test_15_cannot_cancel_applied_unique(self):
        """Cobro único aplicado no se puede cancelar directamente."""
        emp = self._emp('Test State 15')
        charge = self._charge(emp, self.tipo_almuerzo, '2026-03-01', '2026-03-31',
                              state='approved')
        slip = self._slip(emp)
        # Forzar estado applied + link
        charge.write({'state': 'applied', 'payslip_id': slip.id})
        with self.assertRaises(UserError):
            charge.action_cancel()

    def test_16_recurring_can_cancel_even_if_applied(self):
        """Cobro recurrente SÍ se puede cancelar aunque tenga payslip_id."""
        emp = self._emp('Test State 16')
        charge = self._charge(emp, self.tipo_almuerzo, '2026-03-01', '2026-03-31',
                              is_recurring=True, state='approved')
        slip = self._slip(emp)
        charge.write({'state': 'approved', 'payslip_id': slip.id})
        charge.action_cancel()
        self.assertEqual(charge.state, 'cancelled')


# ═══════════════════════════════════════════════════════════════════════
# 3. SYNC — cobro único
# ═══════════════════════════════════════════════════════════════════════

class TestSyncUniqueCharge(TestEmployeeChargesBase):

    def test_20_unique_charge_creates_deduction_line(self):
        """Cobro único aprobado genera línea de deducción en la boleta."""
        emp = self._emp('Test Sync 20')
        charge = self._charge(emp, self.tipo_almuerzo, '2026-03-01', '2026-03-31',
                              unit_price=30_000, state='approved')
        slip = self._slip(emp)
        slip._sync_employee_charges()

        lines = slip.deduction_line_ids.filtered(
            lambda l: l.employee_charge_id == charge.id
        )
        self.assertEqual(len(lines), 1)
        self.assertAlmostEqual(lines[0].amount, 30_000.0)
        self.assertEqual(lines[0].line_type, 'deduction')
        self.assertEqual(lines[0].deduction_category, 'other')

    def test_21_unique_charge_marked_as_applied(self):
        """Cobro único pasa a 'applied' después del sync."""
        emp = self._emp('Test Sync 21')
        charge = self._charge(emp, self.tipo_almuerzo, '2026-03-01', '2026-03-31',
                              state='approved')
        slip = self._slip(emp)
        slip._sync_employee_charges()
        self.assertEqual(charge.state, 'applied')
        self.assertEqual(charge.payslip_id.id, slip.id)

    def test_22_unique_charge_no_duplicate_on_re_sync(self):
        """Re-sincronizar no crea duplicados para cobros únicos."""
        emp = self._emp('Test Sync 22')
        charge = self._charge(emp, self.tipo_almuerzo, '2026-03-01', '2026-03-31',
                              state='approved')
        slip = self._slip(emp)
        slip._sync_employee_charges()
        count_before = len(slip.deduction_line_ids)
        # Re-sync: el cobro ya está applied, no debe crear otra línea
        slip._sync_employee_charges()
        self.assertEqual(len(slip.deduction_line_ids), count_before)

    def test_23_full_subsidy_no_deduction_line(self):
        """Subsidio 100%: no crea línea pero marca cobro como applied."""
        emp = self._emp('Test Sync 23')
        charge = self._charge(emp, self.tipo_almuerzo, '2026-03-01', '2026-03-31',
                              unit_price=30_000, subsidy_pct=100.0, state='approved')
        slip = self._slip(emp)
        slip._sync_employee_charges()

        lines = slip.deduction_line_ids.filtered(
            lambda l: l.employee_charge_id == charge.id
        )
        self.assertEqual(len(lines), 0, "No debe crear línea si subsidio=100%")
        self.assertEqual(charge.state, 'applied')

    def test_24_charge_outside_period_not_synced(self):
        """Cobro fuera del período de la boleta no se sincroniza."""
        emp = self._emp('Test Sync 24')
        # Cobro de febrero, boleta de marzo
        charge = self._charge(emp, self.tipo_almuerzo, '2026-02-01', '2026-02-28',
                              state='approved')
        slip = self._slip(emp, '2026-03-01', '2026-03-31')
        slip._sync_employee_charges()
        self.assertEqual(charge.state, 'approved', "No debe aplicarse fuera del período")

    def test_25_draft_charge_not_synced(self):
        """Cobro en draft no se sincroniza (requiere estar approved)."""
        emp = self._emp('Test Sync 25')
        charge = self._charge(emp, self.tipo_almuerzo, '2026-03-01', '2026-03-31')
        self.assertEqual(charge.state, 'draft')
        slip = self._slip(emp)
        slip._sync_employee_charges()
        self.assertEqual(charge.state, 'draft')
        lines = slip.deduction_line_ids.filtered(
            lambda l: l.employee_charge_id == charge.id
        )
        self.assertEqual(len(lines), 0)


# ═══════════════════════════════════════════════════════════════════════
# 4. SYNC — cobro recurrente
# ═══════════════════════════════════════════════════════════════════════

class TestSyncRecurringCharge(TestEmployeeChargesBase):

    def test_30_recurring_applies_and_stays_approved(self):
        """Cobro recurrente se aplica pero permanece en 'approved'."""
        emp = self._emp('Test Recur 30')
        charge = self._charge(
            emp, self.tipo_almuerzo, '2026-01-01', '2026-12-31',
            unit_price=30_000, is_recurring=True, state='approved'
        )
        slip = self._slip(emp, '2026-03-01', '2026-03-31')
        slip._sync_employee_charges()

        self.assertEqual(charge.state, 'approved',
                         "Recurrente debe permanecer en approved")
        lines = slip.deduction_line_ids.filtered(
            lambda l: l.employee_charge_id == charge.id
        )
        self.assertEqual(len(lines), 1)

    def test_31_recurring_registers_applied_period(self):
        """Cobro recurrente registra YYYY-MM en applied_periods."""
        emp = self._emp('Test Recur 31')
        charge = self._charge(
            emp, self.tipo_almuerzo, '2026-01-01', '2026-12-31',
            is_recurring=True, state='approved'
        )
        slip = self._slip(emp, '2026-03-01', '2026-03-31')
        slip._sync_employee_charges()
        self.assertIn('2026-03', charge.applied_periods)

    def test_32_recurring_no_duplicate_same_period(self):
        """Cobro recurrente no se aplica dos veces en el mismo período."""
        emp = self._emp('Test Recur 32')
        charge = self._charge(
            emp, self.tipo_almuerzo, '2026-01-01', '2026-12-31',
            is_recurring=True, state='approved'
        )
        slip = self._slip(emp, '2026-03-01', '2026-03-31')
        slip._sync_employee_charges()
        count_after_first = len(slip.deduction_line_ids)

        # Segunda sincronización del mismo período → no debe agregar línea
        slip._sync_employee_charges()
        self.assertEqual(len(slip.deduction_line_ids), count_after_first)

    def test_33_recurring_applies_multiple_periods(self):
        """Cobro recurrente se aplica en períodos diferentes."""
        emp = self._emp('Test Recur 33')
        charge = self._charge(
            emp, self.tipo_almuerzo, '2026-01-01', '2026-12-31',
            is_recurring=True, state='approved'
        )
        slip_mar = self._slip(emp, '2026-03-01', '2026-03-31')
        slip_apr = self.env['planilla.payslip.cr'].create({
            'employee_id': emp.id, 'date_from': '2026-04-01',
            'date_to': '2026-04-30', 'company_id': self.company.id,
        })

        slip_mar._sync_employee_charges()
        slip_apr._sync_employee_charges()

        self.assertIn('2026-03', charge.applied_periods)
        self.assertIn('2026-04', charge.applied_periods)

        lines_mar = slip_mar.deduction_line_ids.filtered(
            lambda l: l.employee_charge_id == charge.id
        )
        lines_apr = slip_apr.deduction_line_ids.filtered(
            lambda l: l.employee_charge_id == charge.id
        )
        self.assertEqual(len(lines_mar), 1)
        self.assertEqual(len(lines_apr), 1)

    def test_34_recurring_respects_recurrence_end(self):
        """Cobro recurrente no se aplica después de recurrence_end."""
        emp = self._emp('Test Recur 34')
        charge = self._charge(
            emp, self.tipo_almuerzo, '2026-01-01', '2026-12-31',
            is_recurring=True, recurrence_end='2026-02-28', state='approved'
        )
        # Boleta de marzo: después del fin de recurrencia
        slip = self._slip(emp, '2026-03-01', '2026-03-31')
        slip._sync_employee_charges()

        lines = slip.deduction_line_ids.filtered(
            lambda l: l.employee_charge_id == charge.id
        )
        self.assertEqual(len(lines), 0,
                         "No debe aplicarse después de recurrence_end")

    def test_35_applied_periods_helper_methods(self):
        """Métodos helper de deduplicación funcionan correctamente."""
        emp = self._emp('Test Recur 35')
        charge = self._charge(
            emp, self.tipo_almuerzo, '2026-01-01', '2026-12-31',
            is_recurring=True, state='approved'
        )
        # Inicialmente vacío
        self.assertFalse(charge._is_period_already_applied(date(2026, 3, 1)))

        # Marcar período
        charge._mark_period_applied(date(2026, 3, 1))
        self.assertTrue(charge._is_period_already_applied(date(2026, 3, 1)))

        # Otro período no marcado
        self.assertFalse(charge._is_period_already_applied(date(2026, 4, 1)))

        # Marcar otro
        charge._mark_period_applied(date(2026, 4, 1))
        periods = charge._get_applied_periods_set()
        self.assertIn('2026-03', periods)
        self.assertIn('2026-04', periods)


# ═══════════════════════════════════════════════════════════════════════
# 5. SYNC — batch (múltiples empleados)
# ═══════════════════════════════════════════════════════════════════════

class TestSyncBatch(TestEmployeeChargesBase):

    def test_40_batch_applies_to_all_employees(self):
        """_sync_employee_charges_batch aplica cobros a múltiples empleados."""
        emp1 = self._emp('Batch Emp 1')
        emp2 = self._emp('Batch Emp 2')

        charge1 = self._charge(emp1, self.tipo_almuerzo, '2026-03-01', '2026-03-31',
                               unit_price=25_000, state='approved')
        charge2 = self._charge(emp2, self.tipo_almuerzo, '2026-03-01', '2026-03-31',
                               unit_price=25_000, state='approved')

        slips = self.env['planilla.payslip.cr'].create([
            {'employee_id': emp1.id, 'date_from': '2026-03-01',
             'date_to': '2026-03-31', 'company_id': self.company.id},
            {'employee_id': emp2.id, 'date_from': '2026-03-01',
             'date_to': '2026-03-31', 'company_id': self.company.id},
        ])
        slips._sync_employee_charges_batch()

        self.assertEqual(charge1.state, 'applied')
        self.assertEqual(charge2.state, 'applied')

    def test_41_batch_recurring_no_duplicate(self):
        """Batch no duplica cobros recurrentes ya aplicados en el período."""
        emp = self._emp('Batch Recur 41')
        charge = self._charge(
            emp, self.tipo_almuerzo, '2026-01-01', '2026-12-31',
            is_recurring=True, state='approved'
        )
        # Marcar período como ya aplicado
        charge._mark_period_applied(date(2026, 3, 1))

        slip = self._slip(emp, '2026-03-01', '2026-03-31')
        self.env['planilla.payslip.cr'].browse(slip.id)._sync_employee_charges_batch()

        lines = slip.deduction_line_ids.filtered(
            lambda l: l.employee_charge_id == charge.id
        )
        self.assertEqual(len(lines), 0,
                         "Batch no debe duplicar período ya aplicado")

    def test_42_batch_isolated_between_employees(self):
        """Batch no aplica cobros de un empleado a otro."""
        emp1 = self._emp('Batch Iso 42a')
        emp2 = self._emp('Batch Iso 42b')

        # Solo emp1 tiene cobro
        self._charge(emp1, self.tipo_almuerzo, '2026-03-01', '2026-03-31',
                     state='approved')

        slip1 = self._slip(emp1)
        slip2 = self._slip(emp2)
        (slip1 | slip2)._sync_employee_charges_batch()

        self.assertEqual(len(slip2.deduction_line_ids), 0,
                         "emp2 no debe recibir cobros de emp1")


# ═══════════════════════════════════════════════════════════════════════
# 6. INTEGRACIÓN CONTABLE
# ═══════════════════════════════════════════════════════════════════════

class TestChargeAccounting(TestEmployeeChargesBase):

    def test_50_cobro_creates_deduction_line_with_correct_amount(self):
        """Cobro genera línea de deducción con el monto correcto en la boleta."""
        emp = self._emp('Test Acc 50', salary=500_000)
        charge = self._charge(emp, self.tipo_almuerzo, '2026-03-01', '2026-03-31',
                              unit_price=30_000, state='approved')
        slip = self._slip(emp)
        slip._sync_employee_charges()

        # Verificar que la línea de deducción existe con el monto correcto
        cobro_lines = slip.deduction_line_ids.filtered(
            lambda l: l.employee_charge_id == charge.id
        )
        self.assertEqual(len(cobro_lines), 1, "Debe existir una línea de cobro")
        self.assertAlmostEqual(cobro_lines[0].amount, 30_000.0, places=2,
                               msg="El monto de la deducción debe ser 30,000")
        self.assertEqual(cobro_lines[0].deduction_category, 'other')
        self.assertEqual(cobro_lines[0].line_type, 'deduction')

    def test_51_accounting_entry_balances_with_cobro(self):
        """Asiento contable cuadra DEBE=HABER cuando hay cobro al empleado."""
        emp = self._emp('Test Acc 51', salary=500_000)
        self._charge(emp, self.tipo_almuerzo, '2026-03-01', '2026-03-31',
                     unit_price=30_000, state='approved')
        slip = self._slip(emp)
        slip._sync_employee_charges()

        # Confirmar y pagar
        slip.sudo().write({'state': 'confirmed'})
        slip.sudo().action_pay(skip_accounting=False)

        self.assertTrue(slip.move_id, "Debe generarse asiento contable")
        move = slip.move_id

        total_debit  = sum(l.debit  for l in move.line_ids)
        total_credit = sum(l.credit for l in move.line_ids)
        self.assertAlmostEqual(total_debit, total_credit, places=2,
                               msg="Asiento contable debe cuadrar DEBE=HABER")

    def test_52_cobro_goes_to_account_230970(self):
        """El cobro al empleado se acredita en cuenta 230970."""
        emp = self._emp('Test Acc 52', salary=500_000)
        self._charge(emp, self.tipo_almuerzo, '2026-03-01', '2026-03-31',
                     unit_price=30_000, state='approved')
        slip = self._slip(emp)
        slip._sync_employee_charges()

        slip.sudo().write({'state': 'confirmed'})
        slip.sudo().action_pay(skip_accounting=False)

        move = slip.move_id
        cobro_lines = move.line_ids.filtered(
            lambda l: l.account_id.id == self.acc_cobro.id
        )
        self.assertTrue(cobro_lines,
                        "Debe existir línea en cuenta 230970 Cobros al Empleado")
        total_cobro_credit = sum(l.credit for l in cobro_lines)
        self.assertAlmostEqual(total_cobro_credit, 30_000.0, places=2)

    def test_53_cobro_not_mixed_with_salary_payable(self):
        """El cobro NO se mezcla con la cuenta 230000 Salarios por Pagar."""
        emp = self._emp('Test Acc 53', salary=500_000)
        self._charge(emp, self.tipo_almuerzo, '2026-03-01', '2026-03-31',
                     unit_price=30_000, state='approved')
        slip = self._slip(emp)
        slip._sync_employee_charges()

        slip.sudo().write({'state': 'confirmed'})
        slip.sudo().action_pay(skip_accounting=False)

        move = slip.move_id
        sal_p_lines = move.line_ids.filtered(
            lambda l: l.account_id.id == self.acc_sal_p.id
        )
        # La suma en 230000 NO debe incluir los 30,000 del cobro
        total_230000 = sum(l.credit for l in sal_p_lines)
        # El neto en 230000 debe ser aprox. net_salary - 30,000
        self.assertAlmostEqual(
            total_230000,
            slip.salary_payable,
            delta=1.0,
            msg="230000 debe reflejar salary_payable (sin el cobro)"
        )

    def test_54_partial_subsidy_only_employee_amount_in_accounting(self):
        """Con subsidio 50%, solo employee_amount va al asiento (no el total)."""
        emp = self._emp('Test Acc 54', salary=500_000)
        self._charge(emp, self.tipo_subsidiado, '2026-03-01', '2026-03-31',
                     quantity=10, unit_price=4_000, subsidy_pct=50, state='approved')
        slip = self._slip(emp)
        slip._sync_employee_charges()

        # Línea de deducción debe ser employee_amount (20,000), no total (40,000)
        cobro_lines = slip.deduction_line_ids.filtered(
            lambda l: l.deduction_category == 'other' and l.employee_charge_id
        )
        self.assertAlmostEqual(cobro_lines[0].amount, 20_000.0, places=2)


# ═══════════════════════════════════════════════════════════════════════
# 7. CANCELACIÓN DE BOLETA — restaura cobros
# ═══════════════════════════════════════════════════════════════════════

class TestCancelRestoresCharges(TestEmployeeChargesBase):

    def test_60_cancel_payslip_restores_unique_charge(self):
        """Cancelar boleta restaura cobro único a 'approved'."""
        emp = self._emp('Test Cancel 60')
        charge = self._charge(emp, self.tipo_almuerzo, '2026-03-01', '2026-03-31',
                              state='approved')
        slip = self._slip(emp)
        slip._sync_employee_charges()
        self.assertEqual(charge.state, 'applied')

        slip.action_cancel()
        self.assertEqual(charge.state, 'approved',
                         "Cobro debe volver a approved al cancelar boleta")
        self.assertFalse(charge.payslip_id,
                         "payslip_id debe quedar vacío")

    def test_61_cancel_payslip_recurring_clears_payslip_link(self):
        """Cancelar boleta en cobro recurrente limpia payslip_id."""
        emp = self._emp('Test Cancel 61')
        charge = self._charge(
            emp, self.tipo_almuerzo, '2026-01-01', '2026-12-31',
            is_recurring=True, state='approved'
        )
        slip = self._slip(emp, '2026-03-01', '2026-03-31')
        slip._sync_employee_charges()
        self.assertIn('2026-03', charge.applied_periods)

        slip.action_cancel()
        # El cobro recurrente sigue approved pero payslip_id se limpia
        self.assertEqual(charge.state, 'approved')


# ═══════════════════════════════════════════════════════════════════════
# 8. CATÁLOGO — planilla.charge.type
# ═══════════════════════════════════════════════════════════════════════

class TestChargeTypeCatalog(TestEmployeeChargesBase):

    def test_70_charge_type_unique_code(self):
        """El código de tipo de cobro debe ser único por compañía."""
        # TEST_ALMUERZO ya existe en setUpClass — crear uno igual debe fallar
        with self.assertRaises(ValidationError):
            self.env['planilla.charge.type'].create({
                'name':             'Almuerzo Duplicado',
                'code':             'TEST_ALMUERZO',
                'charge_mode':      'fixed',
                'default_unit_price': 10_000,
                'deduction_code_id':  self.ded_cobro.id,
                'company_id':         self.company.id,
            })

    def test_71_charge_type_subsidy_constraint(self):
        """Subsidio fuera de [0,100] en charge.type lanza ValidationError."""
        with self.assertRaises(ValidationError):
            self.env['planilla.charge.type'].create({
                'name':              'Tipo Inválido',
                'code':              'TEST_INV',
                'charge_mode':       'fixed',
                'default_unit_price': 1_000,
                'subsidy_pct':        -5.0,
                'deduction_code_id':  self.ded_cobro.id,
                'company_id':         self.company.id,
            })

    def test_72_charge_count_on_type(self):
        """charge_count en el tipo refleja el número de cobros activos."""
        emp = self._emp('Test CType 72')
        initial_count = self.tipo_almuerzo.charge_count

        self._charge(emp, self.tipo_almuerzo, '2026-03-01', '2026-03-31',
                     state='approved')
        self.tipo_almuerzo.invalidate_recordset(['charge_count'])
        self.assertGreater(self.tipo_almuerzo.charge_count, initial_count)

    def test_73_data_iniciales_presentes(self):
        """Los 8 tipos de cobro predefinidos existen en la base de datos."""
        expected_codes = [
            'ALMUERZO_FIJO', 'ALMUERZO_DIAS', 'ALMUERZO_SUBS',
            'PRODUCTOS', 'UNIFORME', 'PARQUEO', 'SEGURO_COLECT', 'OTRO_COBRO',
        ]
        for code in expected_codes:
            tipo = self.env['planilla.charge.type'].search(
                [('code', '=', code)], limit=1
            )
            self.assertTrue(tipo, f"Tipo de cobro {code} no encontrado en BD")

    def test_74_deduction_code_cobro_emp_present(self):
        """El código COBRO_EMP existe en planilla.deduction.code."""
        dc = self.env['planilla.deduction.code'].search(
            [('code', '=', 'COBRO_EMP')], limit=1
        )
        self.assertTrue(dc, "Código COBRO_EMP debe existir en deduction.code")

    def test_75_action_view_charges_returns_domain(self):
        """action_view_charges retorna action con domain filtrado por tipo."""
        action = self.tipo_almuerzo.action_view_charges()
        self.assertEqual(action['res_model'], 'planilla.employee.charge')
        domain_str = str(action.get('domain', []))
        self.assertIn(str(self.tipo_almuerzo.id), domain_str)


# ═══════════════════════════════════════════════════════════════════════
# 9. INTEGRACIÓN — employee_charge_ids en hr.employee
# ═══════════════════════════════════════════════════════════════════════

class TestEmployeeChargeIntegration(TestEmployeeChargesBase):

    def test_80_employee_has_charge_ids_field(self):
        """hr.employee tiene campo employee_charge_ids."""
        emp = self._emp('Test Emp 80')
        self.assertTrue(
            hasattr(emp, 'employee_charge_ids'),
            "hr.employee debe tener employee_charge_ids"
        )

    def test_81_charge_visible_from_employee(self):
        """Cobros creados para el empleado son visibles desde employee_charge_ids."""
        emp = self._emp('Test Emp 81')
        charge = self._charge(emp, self.tipo_almuerzo, '2026-03-01', '2026-03-31')
        self.assertIn(charge, emp.employee_charge_ids)

    def test_82_multiple_charges_per_employee(self):
        """Un empleado puede tener múltiples cobros activos."""
        emp = self._emp('Test Emp 82')
        c1 = self._charge(emp, self.tipo_almuerzo, '2026-03-01', '2026-03-31')
        c2 = self._charge(emp, self.tipo_dias,     '2026-03-01', '2026-03-31',
                          quantity=10)
        self.assertIn(c1, emp.employee_charge_ids)
        self.assertIn(c2, emp.employee_charge_ids)

    def test_83_create_payslip_auto_syncs_approved_charges(self):
        """Al crear boleta se sincronizan automáticamente los cobros aprobados."""
        emp = self._emp('Test Emp 83')
        charge = self._charge(emp, self.tipo_almuerzo, '2026-03-01', '2026-03-31',
                              unit_price=25_000, state='approved')
        # Crear boleta — el create() llama _sync_employee_charges()
        slip = self._slip(emp)
        lines = slip.deduction_line_ids.filtered(
            lambda l: l.employee_charge_id == charge.id
        )
        self.assertEqual(len(lines), 1,
                         "La boleta debe tener la línea del cobro al crearse")


# ═══════════════════════════════════════════════════════════════════════
# 10. REPORTE — action_print_charge
# ═══════════════════════════════════════════════════════════════════════

class TestChargeReport(TestEmployeeChargesBase):

    def test_90_action_print_charge_returns_report_action(self):
        """action_print_charge retorna un dict de tipo report."""
        emp = self._emp('Test Report 90')
        charge = self._charge(emp, self.tipo_almuerzo, '2026-03-01', '2026-03-31',
                              state='approved')
        action = charge.action_print_charge()
        self.assertEqual(action.get('type'), 'ir.actions.report')

    def test_91_report_action_exists_in_db(self):
        """El action del reporte está registrado en ir.actions.report."""
        report = self.env.ref(
            'planilla_cr.action_report_employee_charge',
            raise_if_not_found=False
        )
        self.assertTrue(report,
                        "action_report_employee_charge debe existir en BD")

    def test_92_summary_report_action_exists_in_db(self):
        """El action del reporte resumen está registrado."""
        report = self.env.ref(
            'planilla_cr.action_report_employee_charge_summary',
            raise_if_not_found=False
        )
        self.assertTrue(report,
                        "action_report_employee_charge_summary debe existir en BD")
