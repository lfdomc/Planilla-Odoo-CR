import logging
from odoo import models, fields, api
from odoo.models import Constraint
from odoo.exceptions import ValidationError
from . import planilla_const as K

_logger = logging.getLogger(__name__)


class PayslipCR(models.Model):
    """
    Boleta de Pago -- planilla.payslip.cr
    =====================================
    Contiene unicamente: campos, Constraint de BD y constraints ORM.
    Toda la logica de negocio esta distribuida en los mixins:

      payslip_compute_mixin    -> _compute_*, _calc_income_tax
      payslip_sync_mixin       -> _sync_* (novedades, embargos, ROP, bonos)
      payslip_accounting_mixin -> _create_accounting_entry
      payslip_validation_mixin -> _compute_totals, _validate_before_confirm
      payslip_action_mixin     -> create, action_confirm, action_pay, action_cancel...

    v58: Activacion real de los mixins -- payslip_cr.py reducido de 1,921 a ~220 lineas.
    """
    _name = 'planilla.payslip.cr'
    _description = 'Boleta de Pago'
    _inherit = [
        'mail.thread',
        'mail.activity.mixin',
        'planilla.payslip.compute.mixin',
        'planilla.payslip.sync.mixin',
        'planilla.payslip.accounting.mixin',
        'planilla.payslip.auto.overtime.mixin',
        'planilla.payslip.validation.mixin',
        'planilla.payslip.action.mixin',
    ]
    _order = 'date_to desc, employee_id'

    # -- Constraint de BD ----------------------------------------------
    # FIX B-03 v58: Solo se previene duplicar un empleado dentro de la MISMA planilla.
    # La validacion de "un empleado no puede estar en dos planillas del mismo periodo"
    # se hace en _check_no_duplicate_employee_period() a nivel ORM -- mas flexible
    # y con mejor mensaje de error.
    # Odoo 19: usar Constraint (odoo.models) en lugar de _sql_constraints (eliminado).
    _unique_payslip_per_run = Constraint(
        'UNIQUE(employee_id, payroll_run_id)',
        'Un empleado no puede tener dos boletas en la misma planilla.'
    )

    # ==================================================================
    # CAMPOS
    # ==================================================================

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
        'res.company', string='Compania',
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
        string='Calendarizacion', store=True
    )
    # Frecuencia efectiva del periodo -- usada en el Resumen Completo para
    # mostrar etiquetas dinamicas ("Salario Base Quincenal", etc.)
    # Usa _get_effective_freq(): calendarizacion del empleado -> de la planilla -> 'monthly'
    effective_frequency = fields.Selection([
        ('weekly',    'Semanal'),
        ('biweekly',  'Quincenal'),
        ('monthly',   'Mensual'),
        ('bimonthly', 'Bimensual'),
    ], string='Frecuencia Efectiva',
        compute='_compute_effective_frequency', store=True,
        help='Frecuencia de pago efectiva para esta boleta. '
             'Si el empleado tiene calendarizacion, usa esa. '
             'Si no, usa la frecuencia de la planilla.'
    )
    date_from = fields.Date(string='Desde', required=True)
    date_to   = fields.Date(string='Hasta',  required=True)
    currency_id = fields.Many2one(
        related='employee_id.currency_id', store=True
    )

    # -- Ingresos ------------------------------------------------------
    base_salary = fields.Monetary(
        string='Salario Base', currency_field='currency_id',
        compute='_compute_base_salary', store=True
    )
    is_proportional = fields.Boolean(
        string='Calcular Proporcional',
        help='Activar si el empleado ingreso o salio durante el periodo (Art. 163 CT).'
    )
    days_in_period = fields.Integer(
        string='Dias del Periodo',
        compute='_compute_proportional_days', store=True
    )
    days_worked = fields.Integer(
        string='Dias Trabajados',
        help='Dias reales trabajados en el periodo. '
             'Se calcula automaticamente o edite manualmente.'
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
    overtime_amount_ccss = fields.Monetary(
        string='HE afecto CCSS/Renta', currency_field='currency_id',
        compute='_compute_extras', store=True,
        help='Suma de horas extras con afecto_ccss=True. Se usa para calcular '
             'la base cotizable de CCSS, Renta y provisiones. Las horas extras '
             'con afecto_ccss=False se pagan al empleado pero no afectan estas cargas.'
    )
    overtime_hours_total = fields.Float(
        string='Total Horas Extras',
        compute='_compute_extras', store=True,
        digits=(5, 2),
        help='Suma de horas extras aprobadas en este periodo (simple + doble + feriado).'
    )
    overtime_holiday_hours = fields.Float(
        string='Horas en Dia Feriado',
        compute='_compute_extras', store=True,
        digits=(5, 2),
        help='Horas trabajadas en dias feriados de pago obligatorio (Art. 148 CT).'
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
        help='Bonos con afecto_ccss=True (productividad, asistencia, antiguedad, '
             'comisiones). Se integran al salario bruto para CCSS y Renta.'
    )
    rebajo_renta_amount = fields.Monetary(
        string='Rebajo Consolidado Renta', currency_field='currency_id',
        default=0.0,
        help='Retención adicional de renta por multiempleo u otro ajuste (formulario 208/138).'
    )

    bono_base_salarial_amount = fields.Monetary(
        string='Bonos que suman al Salario Base', currency_field='currency_id',
        compute='_compute_bono_salarial', store=True,
        help='Suma de bonos con afecto_salario_base=True. '
             'Se incluye en la base para aguinaldo, vacaciones y cesantia.'
    )
    gross_salary = fields.Monetary(
        string='Salario Bruto', currency_field='currency_id',
        compute='_compute_gross', store=True
    )

    # -- Deducciones Obrero --------------------------------------------
    is_sp = fields.Boolean(
        string='Servicios Profesionales',
        default=False,
        help='Si True, esta boleta es para un contratista SP: sin CCSS, sin INS, sin ROP.'
    )

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
        string='Creditos Fiscales (Art. 34 LIR)', currency_field='currency_id',
        compute='_compute_deductions', store=True,
        help='Total de creditos fiscales por hijos y conyuge aplicados (Art. 34 LIR). '
             'Este monto ya esta descontado del Impuesto de Renta mostrado arriba. '
             'Creditos 2026: CRC1,710/hijo/mes . CRC2,590/conyuge/mes.'
    )
    credit_conyuge = fields.Monetary(
        string='Credito por Conyuge', currency_field='currency_id',
        compute='_compute_deductions', store=True,
        help='Credito fiscal por conyuge (Art. 34 LIR). CRC2,590/mes proporcional a la frecuencia.'
    )
    credit_hijos = fields.Monetary(
        string='Credito por Hijos', currency_field='currency_id',
        compute='_compute_deductions', store=True,
        help='Credito fiscal por hijos dependientes (Art. 34 LIR). CRC1,710/hijo/mes.'
    )
    income_tax_children_count = fields.Integer(
        string='Hijos con credito fiscal',
        compute='_compute_deductions', store=True,
        help='Copia del numero de hijos con credito fiscal del empleado al momento del calculo.'
    )
    tax_credits_detail = fields.Char(
        string='Detalle creditos fiscales',
        compute='_compute_deductions', store=True,
        help='Texto descriptivo del desglose de creditos fiscales: conyuge e hijos.'
    )
    pensioner_type = fields.Selection(
        related='employee_id.pensioner_type',
        string='Tipo de pensionado', store=True,
        help='Clasificacion del pensionado segun el empleado. '
             'Afecta la tasa de CCSS obrero aplicada en esta boleta.'
    )
    other_deductions = fields.Monetary(
        string='Otras Deducciones', currency_field='currency_id'
    )
    paternity_days = fields.Integer(
        string='Dias Paternidad', default=0,
        help='Dias habiles de permiso de paternidad (Ley 8107 -- 8 dias habiles, cargo patrono).'
    )
    paternity_amount = fields.Monetary(
        string='Pago Paternidad', currency_field='currency_id',
        compute='_compute_deductions', store=True,
        help='8 dias habiles remunerados al 100% a cargo del patrono (Ley 8107).'
    )
    total_employee_deductions = fields.Monetary(
        string='Total Deducciones Obrero', currency_field='currency_id',
        compute='_compute_totals', store=True
    )

    # -- Cargas Patronales ---------------------------------------------
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
        help='Prima INS segun clase de riesgo (Ley N.deg 6727). '
             'Tasa referencial ~1.49% clase II.'
    )
    rop_employer = fields.Monetary(
        string='ROP Patronal (3.25%)', currency_field='currency_id',
        help='Costo patronal ROP (Ley 7983). '
             'Asignado por _sync_rop() si rop_applies=True en el empleado.'
    )
    aguinaldo_provision = fields.Monetary(
        string='Provision Aguinaldo (8.33%)', currency_field='currency_id',
        compute='_compute_deductions', store=True,
        help='1/12 del salario anual (Art. 228 CT).'
    )
    cesantia_provision = fields.Monetary(
        string='Provision Cesantia (5.33%)', currency_field='currency_id',
        compute='_compute_deductions', store=True,
        help='Aprox. 8 dias/ano trabajado (Art. 29 CT).'
    )
    vacation_provision = fields.Monetary(
        string='Provision Vacaciones (4.16%)', currency_field='currency_id',
        compute='_compute_deductions', store=True,
        help='2 semanas por ano laborado -- 1/24 del salario anual (Art. 153 CT).'
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
        string='Neto Empleado (incl. subsidios)', currency_field='currency_id',
        compute='_compute_totals', store=True,
        help='Total neto que recibe el empleado incluyendo subsidios CCSS/INS. '
             'Para el deposito real de la empresa usar deposito_patrono.'
    )
    deposito_patrono = fields.Monetary(
        string='Deposito del Patrono', currency_field='currency_id',
        compute='_compute_totals', store=True,
        help='Monto real que la empresa deposita al empleado. '
             'Excluye subsidios CCSS/INS que paga la Caja/INS directamente. '
             'Este es el valor correcto para los libros contables de la empresa.'
    )
    neto_por_patrono = fields.Monetary(
        string='Neto por Patrono', currency_field='currency_id',
        compute='_compute_totals', store=True,
        help='Monto que el PATRONO deposita directamente al empleado.\n'
             'Formula: Salario Bruto  Deducciones Obrero + ingresos adicionales\n'
             '(excluye subsidio CCSS -- ese lo deposita la CCSS directamente).\n'
             'Solo visible cuando hay incapacidades con subsidio CCSS.'
    )
    neto_por_ccss = fields.Monetary(
        string='Neto por CCSS', currency_field='currency_id',
        compute='_compute_totals', store=True,
        help='Monto que la CCSS deposita al empleado por subsidio de incapacidad.\n'
             'Art. 79 CT (incapacidad normal dias 4+) o Art. 94 CT (maternidad).\n'
             'Solo visible cuando hay incapacidades con subsidio CCSS.'
    )
    cost_per_net_colon = fields.Float(
        string='CRC Costo/CRC Neto', digits=(6, 2),
        compute='_compute_totals', store=True,
        help='Por cada CRC1 neto que recibe el empleado, '
             'cuanto gasta la empresa en total (salario + cargas patronales).'
    )

    # -- Asistencias ---------------------------------------------------
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
        string='Metodo de Calculo', store=True
    )

    # -- Novedades -----------------------------------------------------
    disability_ids = fields.Many2many(
        'planilla.disability',
        'planilla_disability_payslip_rel',
        'payslip_id', 'disability_id',
        string='Incapacidades',
        help='Incapacidades que afectan este periodo de pago (pueden abarcar varios periodos).'
    )
    disability_days = fields.Integer(
        string='Dias Incapacidad en Periodo',
        compute='_compute_extras', store=True,
        help='Dias de incapacidad que caen dentro del periodo de esta boleta. '
             'Puede ser menor al total de dias de la incapacidad si esta cruza varios periodos.'
    )
    disability_days_in_period = fields.Integer(
        string='Dias incapacidad en este periodo',
        compute='_compute_extras', store=True,
        help='Dias de incapacidad que caen dentro del periodo de esta boleta. '
             'Puede ser menor al total de dias de la incapacidad si esta cruza periodos. '
             'Este valor es la base para calcular el salario cotizable.'
    )
    dias_laborados_periodo = fields.Integer(
        string='Dias laborados en el periodo',
        compute='_compute_extras', store=True,
        help='Dias efectivamente trabajados = dias del periodo  dias de incapacidad.'
    )
    ccss_subsidy_total = fields.Monetary(
        string='Subsidio CCSS (Incapacidades)', currency_field='currency_id',
        compute='_compute_extras', store=True,
        help='Monto que cubre la CCSS por incapacidades (dias 4+, maternidad).\n'
             'Solo aplica a tipos CCSS -- NO incluye INS.\n'
             'Este monto si pasa por planilla (el patrono puede adelantarlo).'
    )
    ins_subsidy_total = fields.Monetary(
        string='Subsidio INS (Riesgo Laboral)', currency_field='currency_id',
        compute='_compute_extras', store=True,
        help='Monto que cubre el INS por incapacidad de riesgo laboral.\n'
             'El INS paga DIRECTAMENTE al empleado -- NO pasa por planilla.\n'
             'Se registra aqui como referencia informativa.\n'
             'Base legal: Art. 218 CT / Regl. Seguro Riesgos del Trabajo.\n'
             'Tasa: 60% del salario asegurado desde el dia 1 (sin carencia).'
    )
    employer_disability_cost = fields.Monetary(
        string='Costo Patrono por Incapacidades', currency_field='currency_id',
        compute='_compute_extras', store=True,
        help='Dias 1-3 a cargo del patrono + % dias restantes.'
    )
    costo_patrono_periodo = fields.Monetary(
        string='Costo Patrono Incap. (periodo)', currency_field='currency_id',
        compute='_compute_extras', store=True,
        help='Monto a cargo del patrono por los dias de incapacidad que caen\n'
             'DENTRO de este periodo de boleta (dias 1-3 al 50%).\n'
             'Este monto reduce la base cotizable: no es salario -> no genera cargas.'
    )
    incap_viene_de_anterior = fields.Boolean(
        string='Incapacidad de periodo anterior',
        compute='_compute_extras', store=True,
        help='True si alguna incapacidad activa en este periodo inicio antes de '
             'la fecha de inicio de la boleta. Indica que es continuacion de un '
             'evento de un periodo/mes anterior.'
    )
    nota_incap_anterior = fields.Char(
        string='Nota de periodo anterior',
        compute='_compute_extras', store=True,
        help='Nota informativa cuando la incapacidad viene de un periodo anterior.'
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
    pending_overtime_from_attendance = fields.Boolean(
        string='HE Pendientes de Asistencias',
        compute='_compute_pending_overtime',
        store=False,
        help='True cuando hay HE auto-generadas desde asistencias pendientes de aprobacion.'
    )
    pending_overtime_count = fields.Integer(
        string='HE por Aprobar',
        compute='_compute_pending_overtime',
        store=False,
    )
    vacation_ids   = fields.One2many('planilla.vacation.payment', 'payslip_id', string='Vacaciones')
    leave_cr_ids = fields.Many2many(
        'planilla.leave.cr',
        'planilla_leave_cr_payslip_rel',
        'payslip_id', 'leave_cr_id',
        string='Licencias Especiales CR',
        help='Licencias que afectan este periodo. Una licencia larga (adopcion, sin goce) '
             'puede aparecer en multiples boletas con monto proporcional a cada periodo.'
    )
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
        string='Solo Deducciones',
        domain=[('line_type', '=', 'deduction')],
    )

    # -- Estado y Contabilidad -----------------------------------------
    state = fields.Selection([
        ('draft',     'Borrador'),
        ('confirmed', 'Confirmado'),
        ('done',      'Pagado'),
        ('cancelled', 'Cancelado'),
    ], string='Estado', default='draft', tracking=True)

    move_id = fields.Many2one('account.move', string='Asiento Contable')

    # -- Resumenes por categoria (para vista de lista y seguimiento) -----------
    # Permiten ver en la lista de boletas cuanto pesa cada rubro sin abrir el form.
    # Se calculan desde deduction_line_ids agrupando por deduction_category.
    amount_pension_alimentaria = fields.Monetary(
        string='Pension Alimentaria', currency_field='currency_id',
        compute='_compute_deduction_summaries', store=True,
        help='Total pensiones alimentarias (Ley 8590 -- prioridad absoluta).'
    )
    amount_embargo = fields.Monetary(
        string='Embargos Judiciales', currency_field='currency_id',
        compute='_compute_deduction_summaries', store=True,
        help='Total embargos judiciales (max. 25% neto, Art. 172 CT).'
    )
    amount_loans = fields.Monetary(
        string='Prestamos / Adelantos', currency_field='currency_id',
        compute='_compute_deduction_summaries', store=True,
        help='Total cuotas de prestamos y adelantos de salario en este periodo.'
    )
    amount_cobros_empleado = fields.Monetary(
        string='Cobros al Empleado', currency_field='currency_id',
        compute='_compute_deduction_summaries', store=True,
        help='Total cobros al empleado (almuerzo, uniforme, productos, etc.).'
    )
    amount_sindical = fields.Monetary(
        string='Cuota Sindical', currency_field='currency_id',
        compute='_compute_deduction_summaries', store=True,
        help='Total cuotas sindicales del periodo.'
    )
    amount_cooperativa = fields.Monetary(
        string='Cuota Cooperativa', currency_field='currency_id',
        compute='_compute_deduction_summaries', store=True,
        help='Total cuotas cooperativas del periodo.'
    )
    amount_licencias_sin_goce = fields.Monetary(
        string='Licencias / Ausencias sin goce', currency_field='currency_id',
        compute='_compute_deduction_summaries', store=True,
        help='Total deducciones por licencias sin goce y ausencias injustificadas.'
    )
    amount_bonos_exentos = fields.Monetary(
        string='Bonos / Incentivos (exentos CCSS)', currency_field='currency_id',
        compute='_compute_deduction_summaries', store=True,
        help='Bonos con afecto_ccss=False: transporte, representacion, incentivos exentos.'
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

    # ==================================================================
    # CONSTRAINTS ORM
    # ==================================================================

    @api.model_create_multi
    def create(self, vals_list):
        """Auto-activa is_sp si el empleado tiene exento_deducciones."""
        for vals in vals_list:
            if 'employee_id' in vals and not vals.get('is_sp'):
                emp = self.env['hr.employee'].browse(vals['employee_id'])
                if emp and getattr(emp, 'exento_deducciones', False):
                    vals['is_sp'] = True
        return super().create(vals_list)

    def write(self, vals):
        """Proteger boletas pagadas contra escritura accidental en campos criticos."""
        campos_criticos = {
            'base_salary', 'gross_salary', 'net_salary', 'salary_payable',
            'ccss_employee', 'income_tax', 'total_employee_deductions',
            'total_employer_cost', 'deposito_patrono', 'bono_salarial_amount',
            'overtime_amount', 'ccss_subsidy_total',
        }
        if any(f in vals for f in campos_criticos):
            paid_recs = self.filtered(lambda r: r.state == 'paid')
            if paid_recs:
                # Silenciosamente ignorar escrituras de campos criticos en pagadas
                # (los computes intentan escribir pero no deben alterar el valor)
                vals = {k: v for k, v in vals.items() if k not in campos_criticos}
                if not vals:
                    return True
        return super().write(vals)

    def unlink(self):
        """
        FIX BUG-UNLINK-02: La implementacion anterior leia deduction_line_ids
        dentro del loop, pero PostgreSQL ya las habia borrado en cascada antes
        de que el ORM Python pudiera leerlas.

        SOLUCION: llamar action_cancel() ANTES de super().unlink().
        action_cancel() ya maneja correctamente las 6 entidades vinculadas:
          - planilla.employee.charge  (applied_periods + state)
          - planilla.loan.installment (deducted -> pending)
          - planilla.overtime         (paid -> approved)
          - planilla.vacation.payment (paid -> approved)
          - planilla.leave.cr         (paid -> approved)
          - planilla.disability.cr    (paid -> confirmed)

        Al llamar action_cancel() primero, las lineas todavia existen en BD
        y el cleanup puede leerlas y restaurar los estados correctamente.
        Luego super().unlink() borra la boleta y PostgreSQL hace el cascade.
        """
        # Cancelar solo las boletas que no estan ya canceladas
        # (las canceladas ya tuvieron su cleanup en action_cancel anterior)
        to_cancel = self.filtered(lambda r: r.state != 'cancelled')
        if to_cancel:
            to_cancel.action_cancel()

        return super().unlink()


    @api.depends('overtime_ids.state', 'overtime_ids.source',
                 'employee_id.payroll_calculation_method')
    def _compute_pending_overtime(self):
        for rec in self:
            if rec.employee_id.payroll_calculation_method != 'attendance':
                rec.pending_overtime_from_attendance = False
                rec.pending_overtime_count = 0
                continue
            pending = rec.overtime_ids.filtered(
                lambda o: o.state == 'draft' and o.source == 'attendance'
            )
            rec.pending_overtime_count = len(pending)
            rec.pending_overtime_from_attendance = bool(pending)


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
        periodos que se solapen en el tiempo calendario, sin importar en que
        planilla (run) esten ni su sucursal/departamento/calendarizacion.

        Esto garantiza:
          OK Se PUEDEN crear multiples planillas en el mismo periodo.
          OK Distintas planillas pueden tener empleados distintos sin problema.
          OK Se BLOQUEA si el mismo empleado aparece en dos planillas solapadas.

        Ejemplos PERMITIDOS:
          - Planilla Sucursal A (periodo 1-15 marzo) +
            Planilla Sucursal B (periodo 1-15 marzo) -> OK si no comparten empleados
          - Planilla Quincenal (1-15 marzo) +
            Planilla Especial Aguinaldo (1-15 marzo, empleado distinto) -> OK
          - Planilla Depto Ventas + Planilla Depto Produccion (mismo periodo) -> OK

        Ejemplos BLOQUEADOS:
          - Empleado Juan en Planilla A (1-28 feb) y tambien en Planilla B (15 feb - 15 mar) -> ERROR
          - Mismo empleado en dos planillas del mismo periodo -> ERROR
        """
        for rec in self:
            if not rec.employee_id or not rec.date_from or not rec.date_to:
                continue
            # Buscar boletas activas del mismo empleado que solapan con este periodo
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
                        f'   {run_name}: {dup.date_from} -- {dup.date_to} (Ref: {dup.name})'
                    )
                raise ValidationError(
                    f'El empleado {rec.employee_id.name} ya tiene boleta(s) activa(s) '
                    f'que se solapan con el periodo {rec.date_from} -- {rec.date_to}:\n'
                    + '\n'.join(conflictos) + '\n\n'
                    f'Para crear esta boleta primero cancele las boletas en conflicto, '
                    f'o ajuste los periodos para que no se solapeen en el calendario.'
                )


class PayslipDeductionLine(models.Model):
    _name = 'planilla.payslip.deduction.line'
    _description = 'Linea de Deduccion / Ingreso en Boleta'

    payslip_id        = fields.Many2one('planilla.payslip.cr', required=True, ondelete='cascade')
    deduction_code_id = fields.Many2one('planilla.deduction.code', string='Codigo', required=True)
    description       = fields.Char(string='Descripcion')
    line_type         = fields.Selection([
        ('deduction', 'Deduccion'),
        ('income',    'Ingreso Adicional'),
    ], string='Tipo de Linea', default='deduction', required=True)
    deduction_category = fields.Selection([
        ('loan',               'Prestamo'),
        ('sindical',           'Cuota Sindical'),
        ('cooperativa',        'Cuota Cooperativa'),
        ('embargo',            'Embargo Judicial'),
        ('rop',                'ROP -- Regimen Obligatorio Pensiones (Ley 7983)'),
        ('seguro',             'Poliza / Seguro'),
        ('ahorro',             'Ahorro Voluntario'),
        ('pension_vol',        'Pension Voluntaria'),
        ('maternity',          'Permiso Maternidad'),
        ('paternity',          'Permiso Paternidad'),
        ('vacation',           'Pago de Vacaciones'),
        ('bonus',              'Bono / Incentivo'),
        ('pension_alimentaria','Pension Alimentaria'),
        ('ausencia',           'Ausencia Injustificada / Sin Goce'),
        ('licencia_con_goce',  'Licencia con Goce (Duelo, Paternidad, Matrimonio...)'),
        ('licencia_sin_goce',  'Licencia Sin Goce de Sueldo'),
        ('other',              'Otro'),
    ], string='Categoria', default='other')
    amount_type = fields.Selection([
        ('fixed',      'Monto Fijo'),
        ('percentage', 'Porcentaje del Bruto'),
    ], string='Calculo', default='fixed')
    amount            = fields.Monetary(string='Monto (CRC)', currency_field='currency_id')
    percentage        = fields.Float(string='% del Bruto', digits=(5, 2))
    currency_id       = fields.Many2one(related='payslip_id.currency_id', store=True)
    deduction_type    = fields.Selection(related='deduction_code_id.deduction_type', string='Tipo')
    numero_resolucion = fields.Char(
        string='Ndeg Resolucion / Referencia',
        help='Numero de resolucion judicial (embargos) o referencia del documento.'
    )
    recurring_benefit_id = fields.Many2one(
        'planilla.recurring.benefit', string='Beneficio Recurrente', readonly=True
    )
    loan_installment_id = fields.Many2one(
        'planilla.loan.installment', string='Cuota de Prestamo', readonly=True
    )
    hr_leave_id = fields.Many2one(
        'hr.leave', string='Ausencia (hr.leave)',
        readonly=True, ondelete='set null',
        help='Referencia a la ausencia aprobada que origino esta deduccion. '
             'Evita duplicados al re-sincronizar.'
    )
    leave_cr_id = fields.Many2one(
        'planilla.leave.cr', string='Licencia Especial CR',
        readonly=True, ondelete='set null',
        help='Referencia a la licencia especial CR (duelo, paternidad, matrimonio, etc.) '
             'que origino esta linea. Evita duplicados al re-sincronizar.'
    )
    employee_charge_id = fields.Integer(
        string='ID Cobro al Empleado',
        readonly=True,
        help='ID del cobro al empleado (almuerzo, producto, uniforme, etc.) '
             'que origino esta deduccion. Evita duplicados al re-sincronizar. '
             'Se usa como Integer para evitar dependencia circular en BD.'
    )
    is_recurring_bono = fields.Boolean(
        string='Bono Recurrente',
        default=True,
        help='False si el bono es puntual (una sola vez). '
             'Los bonos puntuales NO se anualizan para calcular renta, '
             'evitando pagar impuesto por ingresos proyectados que no se repetiran.'
    )
    bono_id = fields.Many2one(
        'planilla.bono',
        string='Bono de Origen',
        readonly=True, ondelete='set null',
        help='Bono especifico que origino esta linea. Permite deduplicacion por ID unico.'
    )
    embargo_id = fields.Many2one(
        'planilla.embargo',
        string='Embargo de Origen',
        readonly=True, ondelete='set null',
        help='Embargo judicial que origino esta linea. Trazabilidad directa al registro.'
    )

    def unlink(self):
        """Al eliminar manualmente una línea de deducción, restaurar el
        cobro/recurso vinculado a estado 'approved' para que pueda
        re-sincronizarse en la siguiente llamada a Sincronizar Novedades."""
        for line in self:
            # Restaurar cobro al empleado si fue marcado como 'applied'
            if line.employee_charge_id:
                charge = self.env['planilla.employee.charge'].browse(
                    line.employee_charge_id).exists()
                if charge and charge.state == 'applied':
                    charge.write({'state': 'approved', 'payslip_id': False})
            # Restaurar cuota de préstamo
            if line.loan_installment_id and line.loan_installment_id.state == 'deducted':
                if line.loan_installment_id.payslip_id == line.payslip_id:
                    line.loan_installment_id.write({'state': 'pending', 'payslip_id': False})
        return super().unlink()

    @api.constrains('amount', 'deduction_category', 'payslip_id')
    def _check_deduction_limits(self):
        """
        Valida limites legales en tiempo real:
        - Embargo judicial: maximo 25% del neto disponible (Art. 172 CT).
        - Pension alimentaria: sin limite, prioridad absoluta (Ley 8590).

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
                    f'El embargo judicial (CRC{line.amount:,.2f}) supera el 25% del '
                    f'salario neto disponible despues de pensiones y ausencias '
                    f'(maximo CRC{limit:,.2f}) -- Art. 172 CT.'
                )
