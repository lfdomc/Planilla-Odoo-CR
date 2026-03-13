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
