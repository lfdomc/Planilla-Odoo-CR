import logging
from odoo import models, fields, api
from odoo.models import Constraint
from odoo.exceptions import ValidationError
from . import planilla_const as K

_logger = logging.getLogger(__name__)


class PayslipCR(models.Model):
    """
    Boleta de Pago — planilla.payslip.cr
    =====================================
    Contiene únicamente: campos, Constraint de BD y constraints ORM.
    Toda la lógica de negocio está distribuida en los mixins:

      payslip_compute_mixin    → _compute_*, _calc_income_tax
      payslip_sync_mixin       → _sync_* (novedades, embargos, ROP, bonos)
      payslip_accounting_mixin → _create_accounting_entry
      payslip_validation_mixin → _compute_totals, _validate_before_confirm
      payslip_action_mixin     → create, action_confirm, action_pay, action_cancel...

    v58: Activación real de los mixins — payslip_cr.py reducido de 1,921 a ~220 líneas.
    """
    _name = 'planilla.payslip.cr'
    _description = 'Boleta de Pago'
    _inherit = [
        'mail.thread',
        'mail.activity.mixin',
        'planilla.payslip.compute.mixin',
        'planilla.payslip.sync.mixin',
        'planilla.payslip.accounting.mixin',
        'planilla.payslip.validation.mixin',
        'planilla.payslip.action.mixin',
    ]
    _order = 'date_to desc, employee_id'

    # ── Constraint de BD ──────────────────────────────────────────────
    # FIX B-03 v58: Solo se previene duplicar un empleado dentro de la MISMA planilla.
    # La validación de "un empleado no puede estar en dos planillas del mismo período"
    # se hace en _check_no_duplicate_employee_period() a nivel ORM — más flexible
    # y con mejor mensaje de error.
    # Odoo 19: usar Constraint (odoo.models) en lugar de _sql_constraints (eliminado).
    _unique_payslip_per_run = Constraint(
        'UNIQUE(employee_id, payroll_run_id)',
        'Un empleado no puede tener dos boletas en la misma planilla.'
    )

    # ══════════════════════════════════════════════════════════════════
    # CAMPOS
    # ══════════════════════════════════════════════════════════════════

    name = fields.Char(
        string='Referencia', compute='_compute_name', store=True
    )
    employee_id = fields.Many2one(
        'hr.employee', string='Empleado', required=True, tracking=True,
        ondelete='restrict', index=True,
    )
    branch_id = fields.Many2one(
        related='employee_id.branch_id', string='Sucursal', store=True
    )
    company_id = fields.Many2one(
        'res.company', string='Compañía',
        required=True, default=lambda self: self.env.company
    )
    payroll_run_id = fields.Many2one(
        'planilla.run.cr', string='Planilla', ondelete='cascade'
    )
    notes = fields.Text(
        string='Observaciones',
        help='Notas internas que aparecen en el PDF de la boleta.'
    )
    payroll_calendar_id = fields.Many2one(
        related='employee_id.payroll_calendar_id',
        string='Calendarización', store=True
    )
    # Frecuencia efectiva del período — usada en el Resumen Completo para
    # mostrar etiquetas dinámicas ("Salario Base Quincenal", etc.)
    # Usa _get_effective_freq(): calendarización del empleado → de la planilla → 'monthly'
    effective_frequency = fields.Selection([
        ('weekly',    'Semanal'),
        ('biweekly',  'Quincenal'),
        ('monthly',   'Mensual'),
        ('bimonthly', 'Bimensual'),
    ], string='Frecuencia Efectiva',
        compute='_compute_effective_frequency', store=True,
        help='Frecuencia de pago efectiva para esta boleta. '
             'Si el empleado tiene calendarización, usa esa. '
             'Si no, usa la frecuencia de la planilla.'
    )
    date_from = fields.Date(string='Desde', required=True)
    date_to   = fields.Date(string='Hasta',  required=True)
    currency_id = fields.Many2one(
        related='employee_id.currency_id', store=True
    )

    # ── Ingresos ──────────────────────────────────────────────────────
    base_salary = fields.Monetary(
        string='Salario Base', currency_field='currency_id',
        compute='_compute_base_salary', store=True
    )
    is_proportional = fields.Boolean(
        string='Calcular Proporcional',
        help='Activar si el empleado ingresó o salió durante el período (Art. 163 CT).'
    )
    days_in_period = fields.Integer(
        string='Días del Período',
        compute='_compute_proportional_days', store=True
    )
    days_worked = fields.Integer(
        string='Días Trabajados',
        help='Días reales trabajados en el período. '
             'Se calcula automáticamente o edite manualmente.'
    )
    proportional_factor = fields.Float(
        string='Factor Proporcional',
        compute='_compute_proportional_days', store=True,
        digits=(4, 4)
    )
    overtime_amount = fields.Monetary(
        string='Monto Horas Extras', currency_field='currency_id',
        compute='_compute_extras', store=True
    )
    vacation_amount = fields.Monetary(
        string='Monto Vacaciones', currency_field='currency_id',
        compute='_compute_extras', store=True
    )
    other_income = fields.Monetary(
        string='Otros Ingresos', currency_field='currency_id'
    )
    # FIX C-01 v54: Bonos afecto_ccss=True integrados al salario bruto
    # para CCSS, Renta y provisiones (Art. 3 Ley 7983 / Art. 1 Ley ISR).
    bono_salarial_amount = fields.Monetary(
        string='Bonos Salariales (afecto CCSS)',
        currency_field='currency_id',
        compute='_compute_bono_salarial', store=True,
        help='Bonos con afecto_ccss=True (productividad, asistencia, antigüedad, '
             'comisiones). Se integran al salario bruto para CCSS y Renta.'
    )
    gross_salary = fields.Monetary(
        string='Salario Bruto', currency_field='currency_id',
        compute='_compute_gross', store=True
    )

    # ── Deducciones Obrero ────────────────────────────────────────────
    ccss_employee = fields.Monetary(
        string='CCSS Obrero (10.83%)', currency_field='currency_id',
        compute='_compute_deductions', store=True,
        help='Cuota obrera CCSS 10.83%. '
             'Detalle: SEM 5.50%, IVM 3.84%, BPOP 1%, LPT 0.50%, ASFA 0.25%, '
             'FODESAF 0.50%, INA 0.08%.'
    )
    income_tax = fields.Monetary(
        string='Impuesto Renta', currency_field='currency_id',
        compute='_compute_deductions', store=True
    )
    income_tax_credits = fields.Monetary(
        string='Créditos Fiscales (Art. 34 LIR)', currency_field='currency_id',
        compute='_compute_deductions', store=True,
        help='Total de créditos fiscales por hijos y cónyuge aplicados (Art. 34 LIR). '
             'Este monto ya está descontado del Impuesto de Renta mostrado arriba. '
             'Créditos 2026: ₡1,710/hijo/mes · ₡2,590/cónyuge/mes.'
    )
    pensioner_type = fields.Selection(
        related='employee_id.pensioner_type',
        string='Tipo de pensionado', store=True,
        help='Clasificación del pensionado según el empleado. '
             'Afecta la tasa de CCSS obrero aplicada en esta boleta.'
    )
    other_deductions = fields.Monetary(
        string='Otras Deducciones', currency_field='currency_id'
    )
    paternity_days = fields.Integer(
        string='Días Paternidad', default=0,
        help='Días hábiles de permiso de paternidad (Ley 8107 — 8 días hábiles, cargo patrono).'
    )
    paternity_amount = fields.Monetary(
        string='Pago Paternidad', currency_field='currency_id',
        compute='_compute_deductions', store=True,
        help='8 días hábiles remunerados al 100% a cargo del patrono (Ley 8107).'
    )
    total_employee_deductions = fields.Monetary(
        string='Total Deducciones Obrero', currency_field='currency_id',
        compute='_compute_totals', store=True
    )

    # ── Cargas Patronales ─────────────────────────────────────────────
    ccss_employer = fields.Monetary(
        string='CCSS Patronal (26.83%)', currency_field='currency_id',
        compute='_compute_deductions', store=True,
        help='Cuota patronal CCSS 26.83%. '
             'Detalle: SEM 9.25%, IVM 5.75%, BPOP 0.25%, LPT 1.50%, ASFA 0.25%, '
             'FODESAF 5%, INA 1.50%, IMAS 0.50%, FUND 0.50%, FCE 1.50%.'
    )
    ins_employer = fields.Monetary(
        string='INS Patronal (Riesgos del Trabajo)', currency_field='currency_id',
        compute='_compute_deductions', store=True,
        help='Prima INS según clase de riesgo (Ley N.° 6727). '
             'Tasa referencial ~1.49% clase II.'
    )
    rop_employer = fields.Monetary(
        string='ROP Patronal (3.25%)', currency_field='currency_id',
        help='Costo patronal ROP (Ley 7983). '
             'Asignado por _sync_rop() si rop_applies=True en el empleado.'
    )
    aguinaldo_provision = fields.Monetary(
        string='Provisión Aguinaldo (8.33%)', currency_field='currency_id',
        compute='_compute_deductions', store=True,
        help='1/12 del salario anual (Art. 228 CT).'
    )
    cesantia_provision = fields.Monetary(
        string='Provisión Cesantía (5.33%)', currency_field='currency_id',
        compute='_compute_deductions', store=True,
        help='Aprox. 8 días/año trabajado (Art. 29 CT).'
    )
    vacation_provision = fields.Monetary(
        string='Provisión Vacaciones (4.16%)', currency_field='currency_id',
        compute='_compute_deductions', store=True,
        help='2 semanas por año laborado — 1/24 del salario anual (Art. 153 CT).'
    )
    total_employer_cost = fields.Monetary(
        string='Costo Total Patronal', currency_field='currency_id',
        compute='_compute_totals', store=True
    )
    net_salary = fields.Monetary(
        string='Salario Neto', currency_field='currency_id',
        compute='_compute_totals', store=True
    )
    salary_payable = fields.Monetary(
        string='Salario a Pagar', currency_field='currency_id',
        compute='_compute_totals', store=True,
        help='Monto neto a depositar al empleado '
             '(neto menos préstamos, embargos y deducciones adicionales).'
    )
    neto_por_patrono = fields.Monetary(
        string='Neto por Patrono', currency_field='currency_id',
        compute='_compute_totals', store=True,
        help='Monto que el PATRONO deposita directamente al empleado.\n'
             'Fórmula: Salario Bruto − Deducciones Obrero + ingresos adicionales\n'
             '(excluye subsidio CCSS — ese lo deposita la CCSS directamente).\n'
             'Solo visible cuando hay incapacidades con subsidio CCSS.'
    )
    neto_por_ccss = fields.Monetary(
        string='Neto por CCSS', currency_field='currency_id',
        compute='_compute_totals', store=True,
        help='Monto que la CCSS deposita al empleado por subsidio de incapacidad.\n'
             'Art. 79 CT (incapacidad normal días 4+) o Art. 94 CT (maternidad).\n'
             'Solo visible cuando hay incapacidades con subsidio CCSS.'
    )
    cost_per_net_colon = fields.Float(
        string='₡ Costo/₡ Neto', digits=(6, 2),
        compute='_compute_totals', store=True,
        help='Por cada ₡1 neto que recibe el empleado, '
             'cuánto gasta la empresa en total (salario + cargas patronales).'
    )

    # ── Asistencias ───────────────────────────────────────────────────
    attendance_hours = fields.Float(
        string='Horas Trabajadas',
        compute='_compute_attendance_hours', store=True
    )
    attendance_details = fields.Text(
        string='Detalle de Asistencias',
        compute='_compute_attendance_hours', store=True
    )
    calculation_method = fields.Selection(
        related='employee_id.payroll_calculation_method',
        string='Método de Cálculo', store=True
    )

    # ── Novedades ─────────────────────────────────────────────────────
    disability_ids = fields.One2many(
        'planilla.disability', 'payslip_id', string='Incapacidades'
    )
    disability_days = fields.Integer(
        string='Días Incapacidad',
        compute='_compute_extras', store=True
    )
    disability_days_in_period = fields.Integer(
        string='Días incapacidad en este período',
        compute='_compute_extras', store=True,
        help='Días de incapacidad que caen dentro del período de esta boleta. '
             'Puede ser menor al total de días de la incapacidad si ésta cruza períodos. '
             'Este valor es la base para calcular el salario cotizable.'
    )
    dias_laborados_periodo = fields.Integer(
        string='Días laborados en el período',
        compute='_compute_extras', store=True,
        help='Días efectivamente trabajados = días del período − días de incapacidad.'
    )
    ccss_subsidy_total = fields.Monetary(
        string='Subsidio CCSS (Incapacidades)', currency_field='currency_id',
        compute='_compute_extras', store=True,
        help='Monto que cubre la CCSS por incapacidades (días 4+, maternidad).\n'
             'Solo aplica a tipos CCSS — NO incluye INS.\n'
             'Este monto sí pasa por planilla (el patrono puede adelantarlo).'
    )
    ins_subsidy_total = fields.Monetary(
        string='Subsidio INS (Riesgo Laboral)', currency_field='currency_id',
        compute='_compute_extras', store=True,
        help='Monto que cubre el INS por incapacidad de riesgo laboral.\n'
             'El INS paga DIRECTAMENTE al empleado — NO pasa por planilla.\n'
             'Se registra aquí como referencia informativa.\n'
             'Base legal: Art. 218 CT / Regl. Seguro Riesgos del Trabajo.\n'
             'Tasa: 60% del salario asegurado desde el día 1 (sin carencia).'
    )
    employer_disability_cost = fields.Monetary(
        string='Costo Patrono por Incapacidades', currency_field='currency_id',
        compute='_compute_extras', store=True,
        help='Días 1-3 a cargo del patrono + % días restantes.'
    )
    costo_patrono_periodo = fields.Monetary(
        string='Costo Patrono Incap. (período)', currency_field='currency_id',
        compute='_compute_extras', store=True,
        help='Monto a cargo del patrono por los días de incapacidad que caen\n'
             'DENTRO de este período de boleta (días 1-3 al 50%).\n'
             'Este monto reduce la base cotizable: no es salario → no genera cargas.'
    )
    incap_viene_de_anterior = fields.Boolean(
        string='Incapacidad de período anterior',
        compute='_compute_extras', store=True,
        help='True si alguna incapacidad activa en este período inició antes de '
             'la fecha de inicio de la boleta. Indica que es continuación de un '
             'evento de un período/mes anterior.'
    )
    nota_incap_anterior = fields.Char(
        string='Nota de período anterior',
        compute='_compute_extras', store=True,
        help='Nota informativa cuando la incapacidad viene de un período anterior.'
    )
    salario_cotizable = fields.Monetary(
        string='Salario Cotizable (incapacidad)', currency_field='currency_id',
        compute='_compute_extras', store=True,
        help='Base ajustada por incapacidades (Art. 79 CT). '
             'Ver base_cotizable_final para la base final incluyendo licencias sin goce.'
    )
    base_cotizable_final = fields.Monetary(
        string='Base Cotizable Final', currency_field='currency_id',
        compute='_compute_deductions', store=True,
        help='Base real final: salario cotizable menos licencias sin goce y ausencias.\n'
             'Base legal: Arts. 31 y 79 CT / Circular CCSS DSA-1183.'
    )
    overtime_ids   = fields.One2many('planilla.overtime', 'payslip_id', string='Horas Extras')
    vacation_ids   = fields.One2many('planilla.vacation.payment', 'payslip_id', string='Vacaciones')
    leave_cr_ids   = fields.One2many('planilla.leave.cr', 'payslip_id', string='Licencias Especiales CR')
    deduction_line_ids = fields.One2many(
        'planilla.payslip.deduction.line', 'payslip_id', string='Deducciones Adicionales'
    )
    income_line_ids = fields.One2many(
        'planilla.payslip.deduction.line', 'payslip_id',
        string='Ingresos Adicionales',
        domain=[('line_type', '=', 'income')],
    )
    deduction_only_line_ids = fields.One2many(
        'planilla.payslip.deduction.line', 'payslip_id',
        string='Deducciones Adicionales',
        domain=[('line_type', '=', 'deduction')],
    )

    # ── Estado y Contabilidad ─────────────────────────────────────────
    state = fields.Selection([
        ('draft',     'Borrador'),
        ('confirmed', 'Confirmado'),
        ('done',      'Pagado'),
        ('cancelled', 'Cancelado'),
    ], string='Estado', default='draft', tracking=True)

    move_id = fields.Many2one('account.move', string='Asiento Contable')

    # ── Resúmenes por categoría (para vista de lista y seguimiento) ───────────
    # Permiten ver en la lista de boletas cuánto pesa cada rubro sin abrir el form.
    # Se calculan desde deduction_line_ids agrupando por deduction_category.
    amount_pension_alimentaria = fields.Monetary(
        string='Pensión Alimentaria', currency_field='currency_id',
        compute='_compute_deduction_summaries', store=True,
        help='Total pensiones alimentarias (Ley 8590 — prioridad absoluta).'
    )
    amount_embargo = fields.Monetary(
        string='Embargos Judiciales', currency_field='currency_id',
        compute='_compute_deduction_summaries', store=True,
        help='Total embargos judiciales (máx. 25% neto, Art. 172 CT).'
    )
    amount_loans = fields.Monetary(
        string='Préstamos / Adelantos', currency_field='currency_id',
        compute='_compute_deduction_summaries', store=True,
        help='Total cuotas de préstamos y adelantos de salario en este período.'
    )
    amount_cobros_empleado = fields.Monetary(
        string='Cobros al Empleado', currency_field='currency_id',
        compute='_compute_deduction_summaries', store=True,
        help='Total cobros al empleado (almuerzo, uniforme, productos, etc.).'
    )
    amount_sindical = fields.Monetary(
        string='Cuota Sindical', currency_field='currency_id',
        compute='_compute_deduction_summaries', store=True,
        help='Total cuotas sindicales del período.'
    )
    amount_cooperativa = fields.Monetary(
        string='Cuota Cooperativa', currency_field='currency_id',
        compute='_compute_deduction_summaries', store=True,
        help='Total cuotas cooperativas del período.'
    )
    amount_licencias_sin_goce = fields.Monetary(
        string='Licencias / Ausencias sin goce', currency_field='currency_id',
        compute='_compute_deduction_summaries', store=True,
        help='Total deducciones por licencias sin goce y ausencias injustificadas.'
    )
    amount_bonos_exentos = fields.Monetary(
        string='Bonos / Incentivos (exentos CCSS)', currency_field='currency_id',
        compute='_compute_deduction_summaries', store=True,
        help='Bonos con afecto_ccss=False: transporte, representación, incentivos exentos.'
    )
    amount_licencias_con_goce = fields.Monetary(
        string='Licencias con Goce Pagadas', currency_field='currency_id',
        compute='_compute_deduction_summaries', store=True,
        help='Licencias especiales con goce de sueldo: duelo, paternidad, matrimonio, etc.'
    )
    amount_otros_ingresos_adic = fields.Monetary(
        string='Otros Ingresos Adicionales', currency_field='currency_id',
        compute='_compute_deduction_summaries', store=True,
        help='Ingresos adicionales que no son bonos ni licencias: recurring benefits, etc.'
    )

    # ══════════════════════════════════════════════════════════════════
    # CONSTRAINTS ORM
    # ══════════════════════════════════════════════════════════════════

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for rec in self:
            if rec.date_from and rec.date_to and rec.date_from > rec.date_to:
                raise ValidationError(
                    'La fecha de inicio no puede ser mayor a la fecha de fin.'
                )

    @api.constrains('employee_id', 'date_from', 'date_to', 'payroll_run_id')
    def _check_no_duplicate_employee_period(self):
        """
        Regla de negocio central v58:
        Un empleado NO puede tener dos boletas activas (no canceladas) en
        períodos que se solapen en el tiempo calendario, sin importar en qué
        planilla (run) estén ni su sucursal/departamento/calendarización.

        Esto garantiza:
          ✓ Se PUEDEN crear múltiples planillas en el mismo período.
          ✓ Distintas planillas pueden tener empleados distintos sin problema.
          ✓ Se BLOQUEA si el mismo empleado aparece en dos planillas solapadas.

        Ejemplos PERMITIDOS:
          - Planilla Sucursal A (período 1-15 marzo) +
            Planilla Sucursal B (período 1-15 marzo) → OK si no comparten empleados
          - Planilla Quincenal (1-15 marzo) +
            Planilla Especial Aguinaldo (1-15 marzo, empleado distinto) → OK
          - Planilla Depto Ventas + Planilla Depto Producción (mismo período) → OK

        Ejemplos BLOQUEADOS:
          - Empleado Juan en Planilla A (1-28 feb) y también en Planilla B (15 feb - 15 mar) → ERROR
          - Mismo empleado en dos planillas del mismo período → ERROR
        """
        for rec in self:
            if not rec.employee_id or not rec.date_from or not rec.date_to:
                continue
            # Buscar boletas activas del mismo empleado que solapan con este período
            # Solape: date_from_otra <= date_to_esta AND date_to_otra >= date_from_esta
            overlapping = self.search([
                ('employee_id', '=', rec.employee_id.id),
                ('date_from',   '<=', rec.date_to),
                ('date_to',     '>=', rec.date_from),
                ('state',       '!=', 'cancelled'),
                ('id',          '!=', rec.id),
            ])
            if overlapping:
                # Construir mensaje detallado con las planillas en conflicto
                conflictos = []
                for dup in overlapping:
                    run_name = dup.payroll_run_id.name if dup.payroll_run_id else 'Boleta individual'
                    conflictos.append(
                        f'  • {run_name}: {dup.date_from} — {dup.date_to} (Ref: {dup.name})'
                    )
                raise ValidationError(
                    f'El empleado {rec.employee_id.name} ya tiene boleta(s) activa(s) '
                    f'que se solapan con el período {rec.date_from} — {rec.date_to}:\n'
                    + '\n'.join(conflictos) + '\n\n'
                    f'Para crear esta boleta primero cancele las boletas en conflicto, '
                    f'o ajuste los períodos para que no se solapeen en el calendario.'
                )


class PayslipDeductionLine(models.Model):
    _name = 'planilla.payslip.deduction.line'
    _description = 'Línea de Deducción / Ingreso en Boleta'

    payslip_id        = fields.Many2one('planilla.payslip.cr', required=True, ondelete='cascade')
    deduction_code_id = fields.Many2one('planilla.deduction.code', string='Código', required=True)
    description       = fields.Char(string='Descripción')
    line_type         = fields.Selection([
        ('deduction', 'Deducción'),
        ('income',    'Ingreso Adicional'),
    ], string='Tipo de Línea', default='deduction', required=True)
    deduction_category = fields.Selection([
        ('loan',               'Préstamo'),
        ('sindical',           'Cuota Sindical'),
        ('cooperativa',        'Cuota Cooperativa'),
        ('embargo',            'Embargo Judicial'),
        ('rop',                'ROP — Régimen Obligatorio Pensiones (Ley 7983)'),
        ('seguro',             'Póliza / Seguro'),
        ('ahorro',             'Ahorro Voluntario'),
        ('pension_vol',        'Pensión Voluntaria'),
        ('maternity',          'Permiso Maternidad'),
        ('paternity',          'Permiso Paternidad'),
        ('vacation',           'Pago de Vacaciones'),
        ('bonus',              'Bono / Incentivo'),
        ('pension_alimentaria','Pensión Alimentaria'),
        ('ausencia',           'Ausencia Injustificada / Sin Goce'),
        ('licencia_con_goce',  'Licencia con Goce (Duelo, Paternidad, Matrimonio...)'),
        ('licencia_sin_goce',  'Licencia Sin Goce de Sueldo'),
        ('other',              'Otro'),
    ], string='Categoría', default='other')
    amount_type = fields.Selection([
        ('fixed',      'Monto Fijo'),
        ('percentage', 'Porcentaje del Bruto'),
    ], string='Cálculo', default='fixed')
    amount            = fields.Monetary(string='Monto (₡)', currency_field='currency_id')
    percentage        = fields.Float(string='% del Bruto', digits=(5, 2))
    currency_id       = fields.Many2one(related='payslip_id.currency_id', store=True)
    deduction_type    = fields.Selection(related='deduction_code_id.deduction_type', string='Tipo')
    numero_resolucion = fields.Char(
        string='N° Resolución / Referencia',
        help='Número de resolución judicial (embargos) o referencia del documento.'
    )
    recurring_benefit_id = fields.Many2one(
        'planilla.recurring.benefit', string='Beneficio Recurrente', readonly=True
    )
    loan_installment_id = fields.Many2one(
        'planilla.loan.installment', string='Cuota de Préstamo', readonly=True
    )
    hr_leave_id = fields.Many2one(
        'hr.leave', string='Ausencia (hr.leave)',
        readonly=True, ondelete='set null',
        help='Referencia a la ausencia aprobada que originó esta deducción. '
             'Evita duplicados al re-sincronizar.'
    )
    leave_cr_id = fields.Many2one(
        'planilla.leave.cr', string='Licencia Especial CR',
        readonly=True, ondelete='set null',
        help='Referencia a la licencia especial CR (duelo, paternidad, matrimonio, etc.) '
             'que originó esta línea. Evita duplicados al re-sincronizar.'
    )
    employee_charge_id = fields.Integer(
        string='ID Cobro al Empleado',
        readonly=True,
        help='ID del cobro al empleado (almuerzo, producto, uniforme, etc.) '
             'que originó esta deducción. Evita duplicados al re-sincronizar. '
             'Se usa como Integer para evitar dependencia circular en BD.'
    )

    @api.constrains('amount', 'deduction_category', 'payslip_id')
    def _check_deduction_limits(self):
        """
        Valida límites legales en tiempo real:
        - Embargo judicial: máximo 25% del neto disponible (Art. 172 CT).
        - Pensión alimentaria: sin límite, prioridad absoluta (Ley 8590).

        FIX B-08 v58: incluye ausencias en el neto disponible (igual que _sync_embargos).
        """
        for line in self:
            if line.deduction_category != 'embargo':
                continue
            slip = line.payslip_id
            if not slip:
                continue
            gross    = slip.gross_salary or 0.0
            ccss_emp = slip.ccss_employee or 0.0
            renta    = slip.income_tax or 0.0
            pensiones = sum(
                l.amount for l in slip.deduction_line_ids
                if l.deduction_category == 'pension_alimentaria'
            )
            ausencias = sum(
                l.amount for l in slip.deduction_line_ids
                if l.deduction_category == 'ausencia'
            )
            licencias_sg = sum(
                l.amount for l in slip.deduction_line_ids
                if l.deduction_category == 'licencia_sin_goce'
            )
            neto_disponible = gross - ccss_emp - renta - pensiones - ausencias - licencias_sg
            # FIX M-02 v58: usar K.MAX_PCT_EMBARGO para consistencia con _sync_embargos
            limit = round(neto_disponible * K.MAX_PCT_EMBARGO / 100, 2)
            if line.amount > limit and limit > 0:
                raise ValidationError(
                    f'El embargo judicial (₡{line.amount:,.2f}) supera el 25% del '
                    f'salario neto disponible después de pensiones y ausencias '
                    f'(máximo ₡{limit:,.2f}) — Art. 172 CT.'
                )
