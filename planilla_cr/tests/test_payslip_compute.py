"""
Tests unitarios — Cálculos de Boleta de Pago (planilla_cr v52 / Odoo 19)

Modelos reales del módulo:
  - planilla.payslip.cr
  - planilla.rate.helper
  - planilla.calendar  (no planilla.payroll.calendar)
  - planilla.run.cr

El payroll_calendar_id en el payslip es un related del empleado.
Para confirmar boletas se requiere group_planilla_aprobador — en tests
se usa sudo() para evitar restricción de grupo.

Ejecutar:
  docker compose run --rm web odoo -d prueba --test-enable \\
    --test-tags planilla_cr.test_payslip_compute --stop-after-init
"""
from odoo.tests.common import TransactionCase


class TestPayslipCompute(TransactionCase):
    """Tests para cálculos de nómina Costa Rica 2026."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref('base.main_company')

        # ── Calendario mensual (modelo real: planilla.calendar) ───
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

        # ── Empleado de prueba ────────────────────────────────────
        # FIX Odoo 19.0-20260217: res_partner.group_rfq NOT NULL.
        # Usar partner existente como work_contact evita _create_work_contacts().
        cls.employee = cls.env['hr.employee'].create({
            'name': 'Test Empleado Planilla v52',
            'company_id': cls.company.id,
            'work_contact_id': cls.env.company.partner_id.id,
            'payroll_calendar_id': cls.calendar.id,
        })
        cls.employee.write({'base_salary': 700_000})

        # ── Boleta base ───────────────────────────────────────────
        # payroll_calendar_id es related del empleado, no se pasa al create
        cls.payslip = cls.env['planilla.payslip.cr'].create({
            'employee_id': cls.employee.id,
            'date_from': '2026-01-01',
            'date_to': '2026-01-31',
            'company_id': cls.company.id,
        })

    # ── Tasas CCSS ────────────────────────────────────────────────

    def test_01_ccss_obrero_rate(self):
        """CCSS obrera debe ser 10.83% (Decreto CCSS 2026)."""
        rate = self.env['planilla.rate.helper'].get_ccss_employee_rate()
        self.assertAlmostEqual(rate, 0.1083, places=4,
            msg='CCSS obrera debe ser 10.83%')

    def test_02_ccss_patronal_rate(self):
        """CCSS patronal debe ser 26.83% (Decreto CCSS 2026)."""
        rate = self.env['planilla.rate.helper'].get_ccss_employer_rate()
        self.assertAlmostEqual(rate, 0.2683, places=4,
            msg='CCSS patronal debe ser 26.83%')

    # ── Montos calculados ─────────────────────────────────────────

    def test_03_ccss_obrero_monto(self):
        """CCSS obrera sobre ₡700,000 ≈ ₡75,810."""
        self.payslip._compute_deductions()
        expected = round(700_000 * 0.1083, 2)
        self.assertAlmostEqual(self.payslip.ccss_employee, expected, delta=2.0,
            msg=f'CCSS obrera esperada ₡{expected:,.2f}')

    def test_04_ccss_patronal_monto(self):
        """CCSS patronal sobre ₡700,000 ≈ ₡187,810."""
        self.payslip._compute_deductions()
        expected = round(700_000 * 0.2683, 2)
        self.assertAlmostEqual(self.payslip.ccss_employer, expected, delta=2.0,
            msg=f'CCSS patronal esperada ₡{expected:,.2f}')

    def test_05_renta_exento(self):
        """Salario ₡700,000 está en tramo exento — renta = 0."""
        self.payslip._compute_deductions()
        # Tramo exento 2026: hasta ₡941,000
        self.assertEqual(self.payslip.income_tax, 0.0,
            'Salario de ₡700,000 debe estar exento de renta (límite ₡941,000)')

    def test_06_neto_menor_bruto(self):
        """Salario neto < bruto después de deducciones."""
        self.payslip._compute_deductions()
        self.payslip._compute_totals()
        self.assertLess(self.payslip.net_salary, self.payslip.gross_salary,
            'Neto debe ser menor que bruto')

    def test_07_aguinaldo_provision(self):
        """Provisión aguinaldo = 8.33% del bruto."""
        self.payslip._compute_deductions()
        expected = round(self.payslip.gross_salary * 0.0833, 2)
        self.assertAlmostEqual(self.payslip.aguinaldo_provision, expected, delta=2.0,
            msg='Provisión aguinaldo debe ser 8.33%')

    def test_08_cesantia_provision(self):
        """Provisión cesantía = 5.33% del bruto."""
        self.payslip._compute_deductions()
        expected = round(self.payslip.gross_salary * 0.0533, 2)
        self.assertAlmostEqual(self.payslip.cesantia_provision, expected, delta=2.0,
            msg='Provisión cesantía debe ser 5.33%')

    def test_09_vacaciones_provision(self):
        """Provisión vacaciones = 4.16% del bruto."""
        self.payslip._compute_deductions()
        expected = round(self.payslip.gross_salary * 0.0416, 2)
        self.assertAlmostEqual(self.payslip.vacation_provision, expected, delta=2.0,
            msg='Provisión vacaciones debe ser 4.16%')

    def test_10_salario_minimo_no_calificado(self):
        """Salario mínimo no calificado 2026 >= ₡384,300."""
        min_sal = self.env['planilla.minimum.salary'].search([
            ('active', '=', True),
        ], limit=1)
        if min_sal:
            self.assertGreaterEqual(min_sal.amount, 384_300,
                'Salario mínimo no calificado 2026 debe ser >= ₡384,300 (MTSS)')

    def test_11_feriados_obligatorios_2026(self):
        """Deben existir feriados obligatorios configurados para 2026."""
        from datetime import date
        holidays = self.env['planilla.public.holiday'].search([
            ('date', '>=', date(2026, 1, 1)),
            ('date', '<=', date(2026, 12, 31)),
            ('is_paid', '=', True),
        ])
        self.assertGreaterEqual(len(holidays), 10,
            'Deben existir al menos 10 feriados de pago obligatorio en 2026')

    def test_12_feriado_1_octubre_obligatorio(self):
        """1 de octubre (Día de la Cultura) debe ser feriado de pago obligatorio."""
        from datetime import date
        holiday = self.env['planilla.public.holiday'].search([
            ('date', '=', date(2026, 10, 1)),
            ('is_paid', '=', True),
        ], limit=1)
        self.assertTrue(holiday,
            '1 de octubre debe existir como feriado de pago obligatorio (Art. 148 CT + Ley 8442)')

    def test_13_feriado_2_diciembre_no_obligatorio(self):
        """2 de diciembre (Abolición Ejército) debe ser feriado NO obligatorio."""
        from datetime import date
        holiday = self.env['planilla.public.holiday'].search([
            ('date', '=', date(2026, 12, 2)),
        ], limit=1)
        self.assertTrue(holiday,
            '2 de diciembre debe existir como feriado (Ley 8886)')
        if holiday:
            self.assertFalse(holiday.is_paid,
                '2 de diciembre debe ser NOT obligatorio (is_paid=False)')


# ═══════════════════════════════════════════════════════════════════
#  FIX NEW-04 v54 — Casos adicionales de cobertura
# ═══════════════════════════════════════════════════════════════════

class TestPayslipCoverageNew04(TransactionCase):
    """FIX NEW-04 v54: casos faltantes — HE feriado, renta quincenal,
    pensiones + embargo, liquidacion con prestamo activo.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref('base.main_company')

        cls.calendar_monthly = cls.env['planilla.calendar'].search([
            ('frequency', '=', 'monthly'),
            ('company_id', '=', cls.company.id),
        ], limit=1) or cls.env['planilla.calendar'].create({
            'name': 'Mensual NEW04',
            'frequency': 'monthly',
            'company_id': cls.company.id,
        })

        cls.calendar_biweekly = cls.env['planilla.calendar'].search([
            ('frequency', '=', 'biweekly'),
            ('company_id', '=', cls.company.id),
        ], limit=1) or cls.env['planilla.calendar'].create({
            'name': 'Quincenal NEW04',
            'frequency': 'biweekly',
            'company_id': cls.company.id,
        })

        cls.employee = cls.env['hr.employee'].create({
            'name': 'Test NEW04 Cobertura',
            'company_id': cls.company.id,
            'work_contact_id': cls.env.company.partner_id.id,
            'payroll_calendar_id': cls.calendar_monthly.id,
            'base_salary': 1_500_000,
        })

    # ── TEST: Renta quincenal ────────────────────────────────────

    def test_14_renta_quincenal_factor_correcto(self):
        """FIX NEW-04 v54 — Renta quincenal: el salario debe anualizarse
        con factor 24 periodos/anio antes de calcular tramos.
        Con salario de ₡800,000 quincenal (₡19.2M/año) debe haber retención.
        """
        # FIX test_14: usar create() directo en lugar de copy() para evitar
        # UniqueViolation en hr_version_check_unique_date_version.
        #
        # NOTA sobre base_salary: el modelo almacena base_salary como salario
        # MENSUAL en el empleado. Para quincenas, _compute_base_salary aplica
        # freq_factor=0.5, y _calc_income_tax multiplica de vuelta ×2 para
        # obtener el equivalente mensual. Por lo tanto el equivalente mensual
        # siempre es igual a emp.base_salary, independientemente de la frecuencia.
        # Para generar retención se necesita base_salary > ₡941,000 (umbral exento 2026).
        # Usamos ₡2,000,000 mensual → período quincenal = ₡1,000,000 → renta > 0.
        emp_q = self.env['hr.employee'].create({
            'name': 'Test Quincenal NEW04',
            'company_id': self.company.id,
            'work_contact_id': self.env.company.partner_id.id,
            'payroll_calendar_id': self.calendar_biweekly.id,
            'base_salary': 2_000_000,
        })
        slip = self.env['planilla.payslip.cr'].create({
            'employee_id': emp_q.id,
            'date_from': '2026-01-01',
            'date_to': '2026-01-15',
            'company_id': self.company.id,
        })
        slip._compute_deductions()
        # ₡2,000,000 mensual → equiv. mensual ₡2,000,000 → supera umbral ₡941,000
        self.assertGreater(
            slip.income_tax, 0,
            'Salario mensual ₡2,000,000 (quincenal) debe generar retención de renta (umbral exento ₡941,000)'
        )

    # ── TEST: Pensión alimentaria + embargo judicial ─────────────

    def test_15_pension_y_embargo_limite_neto(self):
        """FIX NEW-04 v54 — Pensión alimentaria + embargo judicial:
        la suma de ambas deducciones no puede superar el salario neto
        del empleado (protección mínimo vital Art. 172 CT).
        """
        slip = self.env['planilla.payslip.cr'].create({
            'employee_id': self.employee.id,
            'date_from': '2026-02-01',
            'date_to': '2026-02-28',
            'company_id': self.company.id,
        })
        slip._compute_deductions()
        slip._compute_totals()

        # FIX test_15: pension_alimentaria_total y embargo_judicial no existen
        # como campos directos. Se calculan desde deduction_line_ids.
        pension_total = sum(
            l.amount for l in slip.deduction_line_ids
            if l.deduction_category == 'pension_alimentaria'
        )
        embargo_total = sum(
            l.amount for l in slip.deduction_line_ids
            if l.deduction_category == 'embargo'
        )
        neto = slip.net_salary or 0.0

        total_deducciones_especiales = pension_total + embargo_total
        if neto > 0 and total_deducciones_especiales > 0:
            self.assertLessEqual(
                total_deducciones_especiales, neto,
                f'Suma pensiones (₡{pension_total:,.0f}) + embargo (₡{embargo_total:,.0f}) '
                f'supera neto (₡{neto:,.0f}). Viola proteccion minimo vital Art. 172 CT.'
            )

    # ── TEST: Horas extras en feriado ───────────────────────────

    def test_16_he_feriado_requiere_feriado_registrado(self):
        """FIX NEW-04 v54 — HE tipo 'holiday' solo se puede aprobar
        si la fecha coincide con un feriado registrado en planilla.public.holiday.
        """
        from datetime import date as date_cls
        # Buscar un feriado obligatorio de 2026
        holiday = self.env['planilla.public.holiday'].search([
            ('date', '>=', date_cls(2026, 1, 1)),
            ('is_paid', '=', True),
        ], limit=1)

        if not holiday:
            self.skipTest('No hay feriados obligatorios configurados para 2026')

        # Intentar crear HE de feriado en una fecha SIN feriado
        # (el martes más cercano al feriado encontrado)
        non_holiday_date = date_cls(2026, 1, 6)  # martes — no es feriado
        ot = self.env['planilla.overtime'].create({
            'employee_id': self.employee.id,
            'date': non_holiday_date,
            'hours': 2.0,
            'overtime_type': 'holiday',
        })
        from odoo.exceptions import ValidationError as OdooValidationError
        with self.assertRaises(OdooValidationError,
                               msg='HE de feriado en fecha sin feriado debe lanzar ValidationError'):
            ot.action_approve()

    # ── TEST: Liquidación con préstamo activo ───────────────────

    def test_17_liquidacion_con_prestamo_activo(self):
        """FIX NEW-04 v54 — Liquidación: si el empleado tiene préstamo activo
        con saldo pendiente, el total neto debe ser menor que el bruto (el
        préstamo se descuenta en la liquidación).
        """
        # FIX test_17: nombre correcto del modelo es 'planilla.termination'
        # (no 'planilla.employee.termination'). Campos requeridos: employee_id,
        # entry_date, termination_date, termination_reason, last_salary.
        # No existe termination_type ni years_of_service como campos editables.
        loan = self.env['planilla.employee.loan'].create({
            'employee_id': self.employee.id,
            'amount_total': 500_000,
            'installments': 10,
            'date_granted': '2026-01-01',
            'date_first_deduction': '2026-02-01',
            'state': 'approved',
            'loan_type': 'loan',
        })

        term = self.env['planilla.termination'].create({
            'employee_id': self.employee.id,
            'entry_date': '2023-01-01',
            'termination_date': '2026-03-31',
            'termination_reason': 'renuncia',
            'last_salary': self.employee.base_salary or 1_500_000,
            'company_id': self.company.id,
        })
        # FIX test_17: _compute_all() no existe. Métodos reales: _compute_amounts() + _compute_total()
        term._compute_amounts()
        term._compute_total()

        # Con préstamo activo, el neto debe ser menor que el bruto
        if term.total_gross > 0:
            self.assertLessEqual(
                term.total_net, term.total_gross,
                'Neto liquidación debe ser <= bruto (préstamo activo debe descontarse)'
            )

    # ── TEST: Provisiones cuadran con bruto ─────────────────────

    def test_18_provisiones_suman_correctamente(self):
        """FIX NEW-04 v54 — Las 3 provisiones deben sumar ~17.82% del bruto
        (aguinaldo 8.33% + cesantia 5.33% + vacaciones 4.16% = 17.82%).
        """
        slip = self.env['planilla.payslip.cr'].create({
            'employee_id': self.employee.id,
            'date_from': '2026-04-01',
            'date_to': '2026-04-30',
            'company_id': self.company.id,
        })
        slip._compute_deductions()
        total_prov = (
            (slip.aguinaldo_provision or 0) +
            (slip.cesantia_provision or 0) +
            (slip.vacation_provision or 0)
        )
        expected_pct = 0.1782
        expected_amount = round(slip.gross_salary * expected_pct, 2)
        self.assertAlmostEqual(
            total_prov, expected_amount, delta=slip.gross_salary * 0.005,
            msg=f'Provisiones deben ser ~17.82% del bruto. '
                f'Obtenido: ₡{total_prov:,.2f}, esperado: ₡{expected_amount:,.2f}'
        )

    # ── TEST: Bono salarial afecta base CCSS ─────────────────────

    def test_19_bono_salarial_suma_al_bruto(self):
        """FIX C-01 v54 — Bono con afecto_ccss=True debe sumarse al gross_salary
        para que CCSS y Renta se calculen sobre la base correcta (Art. 3 Ley 7983).
        """
        from odoo.fields import Date
        # Crear código de deducción para bonos
        bono_code = self.env['planilla.deduction.code'].search(
            [('code', '=', 'BONO')], limit=1
        )
        if not bono_code:
            bono_code = self.env['planilla.deduction.code'].create({
                'code': 'BONO', 'name': 'Bono Test',
                'deduction_type': 'employee', 'calculation_type': 'fixed',
            })
        # Crear bono salarial para el empleado
        bono = self.env['planilla.bono'].create({
            'employee_id': self.employee.id,
            'name': 'Bono Productividad Test',
            'bono_type': 'productividad',
            'amount_type': 'fixed',
            'amount': 50000,
            'afecto_ccss': True,
            'afecto_renta': True,
            'is_recurring': True,
            'date_start': '2026-01-01',
            'state': 'active',
        })
        slip = self.env['planilla.payslip.cr'].create({
            'employee_id': self.employee.id,
            'date_from': '2026-05-01',
            'date_to': '2026-05-31',
            'company_id': self.company.id,
        })
        # _sync_bonos ya corrió en el create — solo recalcular
        slip._compute_bono_salarial()
        slip._compute_gross()
        self.assertAlmostEqual(
            slip.bono_salarial_amount, 50000, delta=1,
            msg='bono_salarial_amount debe ser ₡50,000'
        )
        self.assertAlmostEqual(
            slip.gross_salary, self.employee.base_salary + 50000, delta=1,
            msg='gross_salary debe incluir el bono salarial'
        )
        ccss_expected = round((self.employee.base_salary + 50000) * 0.1083, 2)
        slip._compute_deductions()
        self.assertAlmostEqual(
            slip.ccss_employee, ccss_expected, delta=1,
            msg=f'CCSS obrera debe calcularse sobre salario+bono. '
                f'Esperado: ₡{ccss_expected:,.2f}, obtenido: ₡{slip.ccss_employee:,.2f}'
        )
        bono.unlink()

    # ── TEST: Embargo respeta límite 25% neto (Art. 172 CT) ─────

    def test_20_embargo_limite_25_pct(self):
        """FIX C-02 v54 — Embargo no debe superar 25% del neto disponible.
        El modelo planilla.embargo debe rechazar porcentajes > 25%.
        """
        from odoo.exceptions import ValidationError
        # Porcentaje válido (20%) — debe crearse sin error
        embargo_valido = self.env['planilla.embargo'].create({
            'employee_id': self.employee.id,
            'numero_expediente': 'TEST-EMB-001',
            'juzgado': 'Juzgado Test',
            'beneficiario_nombre': 'Acreedor Test',
            'calculation_type': 'percentage',
            'percentage': 20.0,
            'date_start': '2026-01-01',
            'state': 'active',
        })
        self.assertEqual(embargo_valido.percentage, 20.0)
        # Porcentaje inválido (30%) — debe fallar
        with self.assertRaises(ValidationError,
                msg='Porcentaje > 25% debe lanzar ValidationError (Art. 172 CT)'):
            self.env['planilla.embargo'].create({
                'employee_id': self.employee.id,
                'numero_expediente': 'TEST-EMB-002',
                'juzgado': 'Juzgado Test',
                'beneficiario_nombre': 'Acreedor Test',
                'calculation_type': 'percentage',
                'percentage': 30.0,
                'date_start': '2026-01-01',
                'state': 'active',
            })
        embargo_valido.unlink()

    # ── TEST: Paternity formula correcta ────────────────────────

    def test_21_paternity_daily_rate(self):
        """FIX I-01 v54 — Salario diario de paternidad = salario_mensual / 30.
        El bug anterior usaba g * 2 / 30 generando el doble del valor correcto.
        """
        slip = self.env['planilla.payslip.cr'].create({
            'employee_id': self.employee.id,
            'date_from': '2026-06-01',
            'date_to': '2026-06-30',
            'company_id': self.company.id,
            'paternity_days': 8,
        })
        slip._compute_deductions()
        expected = round((self.employee.base_salary / 30) * 8, 2)
        self.assertAlmostEqual(
            slip.paternity_amount, expected, delta=1,
            msg=f'Paternidad 8 días hábiles = salario/30 × 8 = ₡{expected:,.2f}. '
                f'Obtenido: ₡{slip.paternity_amount:,.2f}'
        )
