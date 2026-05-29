import logging
import re
import datetime
from odoo import models, fields, api
from .. import planilla_const as K
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class PayslipComputeMixin(models.AbstractModel):
    """
    Mixin: campos computados de la boleta de pago.
    Metodos: _compute_proportional_days, _compute_name, _compute_base_salary,
    _compute_attendance_hours, _compute_extras, _compute_bono_salarial,
    _compute_gross, _compute_deductions, _onchange_auto_proportional,
    _calc_income_tax, _compute_totals.

    v58: _calc_income_tax movido aqui desde payslip_cr.py.
         P-02: dicts de frecuencia centralizados con K.FREQ_FACTORS.
         B-04: K.PERIODOS_POR_MES['bimonthly'] corregido a 0.5.
    """
    _name = 'planilla.payslip.compute.mixin'
    _description = 'Mixin Computos Boleta'

    def _get_effective_freq(self) -> str:
        """
        Retorna la frecuencia de pago efectiva para esta boleta.

        Orden de prioridad (FIX F5 -- Bug calendarizacion faltante):
          1. Calendarizacion del EMPLEADO (employee_id.payroll_calendar_id)
          2. Calendarizacion de la PLANILLA (payroll_run_id.payroll_calendar_id)
          3. Fallback: 'monthly'

        Esto evita que un empleado sin calendarizacion configurada
        reciba un salario mensual completo en una planilla quincenal.
        El patrono debe configurar la calendarizacion en la ficha del empleado,
        pero mientras tanto el sistema usa la frecuencia de la planilla como
        referencia en lugar de asumir mensual por defecto.
        """
        self.ensure_one()
        if self.payroll_calendar_id:
            return self.payroll_calendar_id.frequency
        if self.payroll_run_id and self.payroll_run_id.payroll_calendar_id:
            return self.payroll_run_id.payroll_calendar_id.frequency
        return 'monthly'

    @api.depends('payroll_calendar_id', 'payroll_run_id',
                 'payroll_run_id.payroll_calendar_id')
    def _compute_effective_frequency(self):
        """Almacena la frecuencia efectiva como campo Selection para usarla
        en condiciones invisible de la vista sin dot notation."""
        for rec in self:
            rec.effective_frequency = rec._get_effective_freq()


    @api.depends('date_from', 'date_to', 'is_proportional', 'days_worked')
    def _compute_proportional_days(self):
        for rec in self:
            if rec.date_from and rec.date_to:
                rec.days_in_period = (rec.date_to - rec.date_from).days + 1
            else:
                rec.days_in_period = 30
            if rec.is_proportional and rec.days_in_period > 0:
                worked = rec.days_worked or rec.days_in_period
                rec.proportional_factor = round(worked / rec.days_in_period, 4)
            else:
                rec.proportional_factor = 1.0

    @api.depends('employee_id', 'date_from', 'date_to')
    def _compute_name(self):
        for rec in self:
            emp = rec.employee_id.name or ''
            date_str = str(rec.date_to)[:7] if rec.date_to else ''
            rec.name = f'BOL - {emp} - {date_str}'

    def _get_salary_for_date(self, emp, ref_date):
        """
        Retorna el salario mensual del empleado para una boleta.

        LOGICA SIMPLIFICADA (ficha del empleado es la fuente de verdad):
        1. Siempre parte de base_salary de la ficha como valor por defecto.
        2. Si hay una entrada de historial cuya effective_date cae DENTRO del
           periodo de la boleta (date_from..date_to) -> usa ese salario.
           Esto soporta cambios salariales que aplican en medio de un periodo.
        3. En cualquier otro caso (historial antes o despues del periodo) ->
           usa base_salary de la ficha.

        Esta logica es mas robusta porque:
        - Evita usar historiales obsoletos cuando la ficha fue actualizada
          manualmente sin un registro formal de historial.
        - Solo sobreescribe la ficha cuando hay un cambio salarial ESPECIFICO
          que aplica exactamente en el periodo que se esta calculando.
        """
        if not emp:
            return 0.0
        base = emp.base_salary or 0.0
        if not ref_date:
            return base
        # Buscar si hay un cambio salarial que aplica EXACTAMENTE en este periodo
        # (solo sobreescribir la ficha si hay un cambio formal dentro del periodo)
        if hasattr(self, 'date_from') and self.date_from and hasattr(self, 'date_to') and self.date_to:
            history_in_period = self.env['planilla.salary.history'].search([
                ('employee_id', '=', emp.id),
                ('state', '=', 'authorized'),
                ('effective_date', '>=', self.date_from),
                ('effective_date', '<=', self.date_to),
            ], order='effective_date desc', limit=1)
            if history_in_period and history_in_period.gross_salary:
                return history_in_period.gross_salary
        # Sin cambio formal en el periodo -> usar ficha del empleado
        return base

    @api.depends('employee_id', 'date_from', 'date_to', 'attendance_hours',
                 'is_proportional', 'proportional_factor', 'payroll_calendar_id',
                 'days_in_period',
                 'employee_id.schedule_type_id',
                 'employee_id.base_salary')
    def _compute_base_salary(self):
        for rec in self:
            if rec.state == 'paid':
                continue  # Boleta pagada: valores congelados
            emp = rec.employee_id
            if not emp:
                rec.base_salary = 0.0
                continue
            if (emp.payroll_calculation_method or 'fixed') == 'attendance':
                if not rec.date_from or not rec.date_to or not emp.base_salary:
                    rec.base_salary = 0.0
                    continue
                hours_per_day     = emp.schedule_type_id.hours_per_day if emp.schedule_type_id else K.HORAS_JORNADA_DEFAULT
                period_days       = max(rec.days_in_period or 30, 1)
                freq              = rec._get_effective_freq()
                # FIX B-04 v58: usar K.PERIODOS_POR_MES -- bimonthly ahora es 0.5 (corregido)
                periods_per_month = K.PERIODOS_POR_MES.get(freq, 1)
                monthly_hours     = hours_per_day * period_days * periods_per_month
                hourly_rate       = emp.base_salary / monthly_hours if monthly_hours else 0.0
                rec.base_salary   = round(hourly_rate * (rec.attendance_hours or 0.0), 2)
            else:
                # Usar salario vigente en la fecha de inicio de la boleta
                # Si hay historial autorizado, se usa ese; si no, el salario actual
                ref_date = rec.date_from or fields.Date.today()
                raw  = rec._get_salary_for_date(emp, ref_date)
                freq = rec._get_effective_freq()
                freq_factor = K.FREQ_FACTORS.get(freq, 1.0)
                prop_factor = rec.proportional_factor if rec.is_proportional else 1.0
                rec.base_salary = round(raw * freq_factor * prop_factor, 2)

    @api.onchange('date_from', 'date_to', 'employee_id')
    def _onchange_auto_proportional(self):
        """Auto-detecta si el empleado ingreso o salio durante el periodo."""
        for rec in self:
            emp = rec.employee_id
            if emp and emp.entry_date and rec.date_from and rec.date_to:
                if rec.date_from <= emp.entry_date <= rec.date_to:
                    rec.is_proportional = True
                    rec.days_worked = (rec.date_to - emp.entry_date).days + 1
                elif emp.exit_date and rec.date_from <= emp.exit_date <= rec.date_to:
                    rec.is_proportional = True
                    rec.days_worked = (emp.exit_date - rec.date_from).days + 1

    @api.depends('employee_id', 'date_from', 'date_to', 'attendance_hours', 'is_proportional',
                 'proportional_factor',
                 'employee_id.payroll_calculation_method')
    def _compute_attendance_hours(self):
        for rec in self:
            if (not rec.employee_id or not rec.date_from or not rec.date_to
                    or rec.employee_id.payroll_calculation_method != 'attendance'):
                rec.attendance_hours   = 0.0
                rec.attendance_details = ''
                continue
            # FIX TZ v55: CR = UTC-6. Extender 6h extra para capturar turnos nocturnos.
            dt_from = fields.Datetime.to_datetime(rec.date_from)
            dt_to   = fields.Datetime.to_datetime(rec.date_to) + datetime.timedelta(
                days=1, hours=K.CR_UTC_OFFSET_HOURS  # FIX B-11 v58: usar constante
            )
            attendances = self.env['hr.attendance'].search([
                ('employee_id', '=', rec.employee_id.id),
                ('check_in',    '>=', dt_from),
                ('check_in',    '<',  dt_to),
            ])
            open_att = attendances.filtered(lambda a: not a.check_out)
            if open_att:
                fechas = ', '.join(str(a.check_in)[:10] for a in open_att)
                rec.attendance_hours   = 0.0
                rec.attendance_details = (
                    f'WARN ADVERTENCIA: Existen {len(open_att)} registro(s) de asistencia '
                    f'sin check_out en las fechas: {fechas}. '
                    f'Corrija las marcas antes de confirmar la boleta.'
                )
                continue
            rec.attendance_hours = round(sum(a.worked_hours for a in attendances), 2)
            if attendances:
                rec.attendance_details = '\n'.join(
                    f"{str(a.check_in)[:10]}: {round(a.worked_hours, 2)}h"
                    for a in attendances.sorted('check_in')
                )
            else:
                rec.attendance_details = 'Sin registros de asistencia en el periodo.'

    @api.depends('overtime_ids.amount', 'overtime_ids.state',
                 'vacation_ids.total_amount', 'vacation_ids.state',
                 'disability_ids.days', 'disability_ids.ccss_subsidy',
                 'disability_ids.employer_cost', 'disability_ids.state',
                 'disability_ids.date_start', 'disability_ids.date_end',
                 'disability_ids.disability_type',
                 'disability_ids.maternity_avg_salary',
                 'disability_ids.daily_salary',
                 'date_from', 'date_to',
                 'employee_id.base_salary')
    def _compute_extras(self):
        for rec in self:
            if rec.state == 'paid':
                continue  # Boleta pagada: valores congelados
            approved_ot = [o for o in rec.overtime_ids if o.state == 'approved']
            # Incluir HE aprobadas Y pagadas (las pagadas ya pertenecen a esta boleta)
            billable_ot = [o for o in rec.overtime_ids if o.state in ('approved', 'paid')]
            rec.overtime_amount         = sum(o.amount for o in billable_ot)
            rec.overtime_hours_total    = round(sum(o.hours  for o in billable_ot), 2)
            rec.overtime_holiday_hours  = round(sum(
                o.hours for o in billable_ot if o.overtime_type == 'holiday'
            ), 2)
            # Solo sumar al bruto las vacaciones pagadas en DINERO (Art. 156 CT).
            # Las vacaciones DISFRUTADAS no se suman: el salario base del periodo
            # ya cubre esos dias -- sumarlas generaria pago doble.
            rec.vacation_amount = sum(
                v.total_amount for v in rec.vacation_ids
                if v.state == 'approved' and v.payment_method in ('dinero', 'mixto')
            )
            active_dis = rec.disability_ids.filtered(lambda d: d.state in ('confirmed', 'paid'))
            rec.disability_days          = 0  # se actualiza abajo tras calcular disability_days_in_period
            # FIX COST-PERIODO: employer_cost en el modelo disability es el costo TOTAL
            # de toda la incapacidad (ej: maternidad 121 dias = 756,250).
            # Para el Costo Total Patronal del periodo, solo debe contarse
            # la porcion proporcional que cae dentro de este periodo.
            # Se calcula como: overlap_days / total_days x employer_cost de cada incapacidad.
            # Esto evita que el Costo Total Patronal sea absurdamente alto (el costo de
            # 121 dias sumado en CADA una de las 8 boletas que cubre la maternidad).
            if rec.date_from and rec.date_to:
                cost_periodo = 0.0
                for d in active_dis:
                    if not d.date_start or not d.date_end:
                        continue
                    overlap_start = max(rec.date_from, d.date_start)
                    overlap_end   = min(rec.date_to, d.date_end)
                    if overlap_end < overlap_start:
                        continue
                    overlap_days = (overlap_end - overlap_start).days + 1
                    total_days   = max(d.days or 1, 1)
                    cost_periodo += (d.employer_cost or 0.0) * overlap_days / total_days
                rec.employer_disability_cost = round(cost_periodo, 2)
            else:
                rec.employer_disability_cost = round(sum(d.employer_cost for d in active_dis), 2)

            # -- BUG FIX MATERNIDAD: calcular subsidio y cotizable por periodo --
            dias_incap_periodo    = 0
            ccss_subsidy_periodo  = 0.0
            ins_subsidy_periodo   = 0.0  # INS paga fuera de planilla
            costo_patrono_periodo = 0.0  # dias 1-3 a cargo del patrono en este periodo

            if rec.date_from and rec.date_to:
                # -- Agrupacion de incapacidades consecutivas (prorrogas) --------
                # Regla legal CR (CCSS): si una incapacidad inicia el dia siguiente
                # a que termina otra del mismo empleado, es una PRORROGA del mismo
                # evento. Los 3 dias del tramo patronal (Art. 79 CT) NO se reinician
                # en cada incapacidad individual -- se comparten en todo el grupo.
                #
                # Ejemplo: Incap1=26-feb->9-mar, Incap2=10-mar->13-mar
                #   -> Grupo continuo. Dias 1-3 patronal se agotaron en febrero.
                #   -> En marzo: 13 dias todos subsidiados CCSS al 60%.
                from datetime import timedelta as _td

                dis_validas = sorted(
                    [d for d in active_dis if d.date_start and d.date_end],
                    key=lambda d: d.date_start
                )
                # Construir grupos: agregar a grupo si inicia el dia siguiente del ultimo
                groups = []
                for dis in dis_validas:
                    if groups and dis.date_start <= groups[-1][-1].date_end + _td(days=1):
                        groups[-1].append(dis)   # prorroga del mismo evento
                    else:
                        groups.append([dis])     # nuevo evento independiente

                for group in groups:
                    group_start = group[0].date_start  # inicio real del evento completo

                    for dis in group:
                        overlap_start = max(rec.date_from, dis.date_start)
                        overlap_end   = min(rec.date_to,   dis.date_end)
                        if overlap_end < overlap_start:
                            continue
                        dias_overlap = (overlap_end - overlap_start).days + 1
                        # Aplicar medio dia extra del primer dia si esta activo
                        if getattr(dis, 'extra_half_day', False):
                            # El medio dia extra aplica solo si el inicio de la incapacidad
                            # cae dentro del periodo de esta boleta
                            if rec.date_from <= dis.date_start <= rec.date_to:
                                dias_overlap += 0.5
                        dias_incap_periodo += dias_overlap

                        if dis.disability_type == 'maternity':
                            daily = dis.maternity_avg_salary or dis.daily_salary or 0.0
                        else:
                            daily = dis.daily_salary or 0.0

                        if dis.disability_type == 'ins':
                            # INS - Riesgo Laboral (Art. 218 CT):
                            #  Cubre desde el DIA 1 sin carencia patronal.
                            #  60% del salario asegurado (subsidy_percentage=60).
                            #  Paga FUERA de planilla -> va a ins_subsidy_periodo.
                            #  No afecta salario_cotizable CCSS (base=CRC0 en planilla).
                            #  Patrono: CRC0, no hay dias carencia.
                            ins_rate = (dis.subsidy_percentage or 60.0) / 100.0
                            ins_subsidy_periodo += round(dias_overlap * daily * ins_rate, 2)
                            # dias_patrono y subsidiados = 0 para CCSS (no aplica)
                        elif dis.disability_type == 'maternity':
                            dias_patrono_overlap     = 0
                            dias_subsidiados_overlap = dias_overlap
                            subsidy_rate = 1.0
                            # FIX QUINCENAL-MAT: usar freq_factor cuando la maternidad
                            # cubre TODO el periodo (dias_overlap == dias_periodo_total).
                            # Razon: el salario quincenal se calcula siempre como
                            # mensual x 0.5, NO como diario x dias_reales. Sin este fix,
                            # quincenas de meses con 31 dias (16 dias) generan un subsidio
                            # diferente a las quincenas de 15 dias, lo que es inconsistente
                            # con el modelo quincenal y con la practica contable CR estandar.
                            # Cuando la maternidad es PARCIAL (algunos dias laborados),
                            # se mantiene el calculo por dias reales porque el salario
                            # de los dias trabajados tambien usa dias_trabajados x diario.
                            dias_periodo_local = (rec.date_to - rec.date_from).days + 1 if (rec.date_from and rec.date_to) else 15
                            es_mat_periodo_completo = (dias_overlap >= dias_periodo_local)
                            if es_mat_periodo_completo:
                                freq = rec._get_effective_freq()
                                freq_factor_local = K.FREQ_FACTORS.get(freq, 1.0)
                                ccss_subsidy_periodo += round(daily * K.DIAS_MES * freq_factor_local, 2)
                            else:
                                # Periodo parcial: algunos dias laborados + maternidad
                                # -> seguir con dias reales para consistencia con salario laborado
                                ccss_subsidy_periodo += round(dias_subsidiados_overlap * daily * subsidy_rate, 2)
                        else:
                            # CCSS Enfermedad/Accidente (Art. 79 CT)
                            days_since_group_start = (overlap_start - group_start).days
                            employer_remaining = max(3 - days_since_group_start, 0)
                            dias_patrono_overlap     = min(dias_overlap, employer_remaining)
                            dias_subsidiados_overlap = dias_overlap - dias_patrono_overlap
                            subsidy_rate = (dis.subsidy_percentage or 60.0) / 100.0
                            costo_patrono_periodo += round(dias_patrono_overlap * daily * 0.50, 2)
                            ccss_subsidy_periodo   += round(dias_subsidiados_overlap * daily * subsidy_rate, 2)

            rec.disability_days_in_period = dias_incap_periodo
            rec.disability_days          = dias_incap_periodo  # FIX DISPLAY: dias del PERIODO, no total incapacidad
            dias_periodo_total = (rec.date_to - rec.date_from).days + 1 if (rec.date_from and rec.date_to) else 15
            rec.dias_laborados_periodo = max(dias_periodo_total - dias_incap_periodo, 0)
            rec.ccss_subsidy_total  = round(ccss_subsidy_periodo, 2)
            rec.ins_subsidy_total   = round(ins_subsidy_periodo, 2)
            rec.costo_patrono_periodo = round(costo_patrono_periodo, 2)

            # Detectar si alguna incapacidad viene de un periodo anterior
            viene_de_anterior = False
            fechas_anteriores = []
            if rec.date_from:
                for dis in active_dis:
                    if not dis.date_start or not dis.date_end:
                        continue
                    ov_s = max(rec.date_from, dis.date_start)
                    ov_e = min(rec.date_to or dis.date_end, dis.date_end)
                    if ov_e >= ov_s and dis.date_start < rec.date_from:
                        viene_de_anterior = True
                        fechas_anteriores.append(
                            f"{dis.date_start.strftime('%d/%m/%Y')} -> {dis.date_end.strftime('%d/%m/%Y')}"
                        )
            rec.incap_viene_de_anterior = viene_de_anterior
            if viene_de_anterior and costo_patrono_periodo == 0.0:
                rec.nota_incap_anterior = (
                    f"Prorroga de incapacidad iniciada el {fechas_anteriores[0].split(' -> ')[0]}. "
                    f"Los 3 dias del tramo patronal (Art. 79 CT) ya se aplicaron en el periodo anterior -- "
                    f"no generan costo patronal en esta quincena."
                )
            elif viene_de_anterior:
                rec.nota_incap_anterior = (
                    f"Incapacidad iniciada el {fechas_anteriores[0].split(' -> ')[0]}, "
                    f"continua en este periodo."
                )
            else:
                rec.nota_incap_anterior = ""


            # -- Salario cotizable por periodo --------------------------------
            emp = rec.employee_id
            if emp and emp.base_salary and dias_incap_periodo > 0:
                # Usar salario historico vigente en la fecha de la boleta
                _sal_hist = rec._get_salary_for_date(emp, rec.date_from)
                salario_diario  = round(_sal_hist / K.DIAS_MES, 4)
                dias_periodo    = (rec.date_to - rec.date_from).days + 1 if (rec.date_from and rec.date_to) else K.DIAS_MES
                dias_trabajados = max(dias_periodo - dias_incap_periodo, 0)

                # Detectar tipo de incapacidad dominante en el periodo
                dis_in_per = [
                    d for d in active_dis
                    if d.date_start and d.date_end
                    and max(rec.date_from, d.date_start) <= min(rec.date_to, d.date_end)
                ]
                es_maternidad_total = (
                    all(d.disability_type == 'maternity' for d in dis_in_per)
                    and dias_trabajados == 0
                )
                es_ins_total = (
                    all(d.disability_type == 'ins' for d in dis_in_per)
                    and dias_trabajados == 0
                )

                if es_maternidad_total:
                    # Maternidad Art. 94 CT
                    # Si maternity_ccss_on_employer=True: el subsidio COMPLETO es
                    # la base cotizable porque el empleado lo recibe como salario-
                    # equivalente y debe pagar CCSS obrera sobre el (10.83%%).
                    # Si es False: base = 0 (patrono no paga, CCSS paga directo).
                    mat_dis_in_per = [d for d in dis_in_per if d.disability_type == 'maternity']
                    has_ccss_on_emp = any(getattr(d, 'maternity_ccss_on_employer', False) for d in mat_dis_in_per)
                    if has_ccss_on_emp:
                        # Base cotizable = subsidio total (CCSS pasa por planilla)
                        rec.salario_cotizable = rec.ccss_subsidy_total or 0.0
                    else:
                        rec.salario_cotizable = 0.0
                elif es_ins_total:
                    # INS total: el INS paga fuera de planilla.
                    # Base cotizable CCSS = CRC0 (no hay salario que reportar).
                    rec.salario_cotizable = 0.0
                else:
                    # Incapacidad normal CCSS o mixta: calcular salario_cotizable
                    # usando el mismo modelo que el salario base (freq_factor),
                    # NO como dias_trabajados x diario.
                    #
                    # RAZON: el sistema calcula el salario normal como:
                    #   base = mensual x freq_factor (siempre igual, sin importar
                    #   cuantos dias calendario tiene el periodo -- 15 o 16).
                    # Con incapacidad, la logica debe ser:
                    #   base_quincenal (fijo) - dias_incap x diario + costo_patrono
                    # En lugar de:
                    #   dias_trabajados x diario + costo_patrono  <- BUG en 16-day periods
                    #
                    # Bug especifico: en meses de 31 dias (2a quincena = 16 dias),
                    # con 1 dia incapacidad y 15 trabajados:
                    #   15 x (monthly/30) = monthly x 0.5 = QUINCENAL COMPLETO
                    # y al sumarle costo_patrono el empleado recibe MAS que su
                    # quincenal completo -- incorrecto.
                    # Con el fix: monthlyx0.5 - 1xdiario + costo = correcto.
                    #
                    # Para 15-day periods: ambas formulas dan practicamente el mismo
                    # resultado (diferencia de <1 colon por redondeo), pero la nueva
                    # es conceptualmente correcta en todos los casos.
                    #
                    # NO usar min(dias_incap, 3) que ignora las prorrogas.
                    freq = rec._get_effective_freq()
                    ff = K.FREQ_FACTORS.get(freq, 1.0)
                    prop = rec.proportional_factor if rec.is_proportional else 1.0
                    base_quincenal = round(_sal_hist * ff * prop, 2)
                    rec.salario_cotizable = round(
                        base_quincenal
                        - (dias_incap_periodo * salario_diario)
                        + costo_patrono_periodo,
                        2
                    )
            else:
                # Sin incapacidades: salario cotizable = salario base del periodo
                # (gross_salary no esta disponible aqui sin crear dependencia circular)
                emp = rec.employee_id
                if emp and emp.base_salary:
                    freq = rec._get_effective_freq()
                    freq_factor = K.FREQ_FACTORS.get(freq, 1.0)
                    prop_factor = rec.proportional_factor if rec.is_proportional else 1.0
                    _sal_base = rec._get_salary_for_date(emp, rec.date_from)
                    rec.salario_cotizable = round(_sal_base * freq_factor * prop_factor, 2)
                else:
                    rec.salario_cotizable = 0.0


    @api.depends('deduction_line_ids.amount', 'deduction_line_ids.line_type',
                 'deduction_line_ids.deduction_category',
                 'deduction_line_ids.bono_id',
                 'deduction_line_ids.bono_id.afecto_ccss')
    def _compute_bono_salarial(self) -> None:
        """
        FIX C-01 v54: Suma de bonos con afecto_ccss=True para integrar al salario bruto.

        FIX v512 BUG-04 + BP-05:
          - BUG-04: eliminado N+1 -- se precarga un unico dict de bonos para TODOS los
            registros del recordset antes del loop, en lugar de un search por cada boleta.
          - BP-05: corregida logica de afecto_ccss: solo se suma si el bono SE ENCUENTRA
            y tiene afecto_ccss=True. Antes la logica era inversa: si no encontraba el bono
            lo asumia afecto_ccss=True (inclusivo por defecto -- incorrecto fiscalmente).
        """
        if not self:
            return

        # FIX BP-06: usar bono_id (ID unico) en lugar de parsear la descripcion.
        # La descripcion ahora tiene formato '[BON-XXXX] Nombre' que no coincide
        # con el indice por nombre. bono_id es el enlace directo y es inmune
        # a cambios de formato en la descripcion.
        for rec in self:
            if rec.state == 'paid':
                continue  # Boleta pagada: valores congelados
            bonus_lines = rec.deduction_line_ids.filtered(
                lambda l: l.line_type == 'income' and l.deduction_category == 'bonus'
            )
            if not bonus_lines:
                rec.bono_salarial_amount = 0.0
                continue

            total = 0.0
            for line in bonus_lines:
                # Preferir bono_id (enlace directo) sobre busqueda por nombre
                bono_rec = line.bono_id
                if not bono_rec:
                    # Fallback para lineas sin bono_id (creadas antes del campo)
                    emp_ids_fb = [rec.employee_id.id]
                    nombre = (line.description or '').replace('Bono: ', '').strip()
                    # Quitar prefijo [BON-XXXX] si existe
                    nombre = re.sub(r'^\[\w+-\d+\]\s*', '', nombre).strip()
                    bono_rec = self.env['planilla.bono'].search([
                        ('employee_id', '=', rec.employee_id.id),
                        ('name', '=', nombre),
                        ('state', '=', 'active'),
                    ], limit=1)
                if bono_rec and bono_rec.afecto_ccss:
                    total += line.amount
            rec.bono_salarial_amount = round(total, 2)

    @api.depends('base_salary', 'salario_cotizable', 'costo_patrono_periodo',
                 'disability_days_in_period',
                 'overtime_amount', 'vacation_amount', 'other_income',
                 'bono_salarial_amount',
                 'disability_ids.state', 'disability_ids.date_start',
                 'disability_ids.date_end', 'disability_ids.disability_type',
                 'date_from', 'date_to')
    def _compute_gross(self) -> None:
        for rec in self:
            if rec.state == 'paid':
                continue  # Boleta pagada: valores congelados
            # -- Base salarial segun incapacidad ------------------------------
            # REGLA LEGAL (Art. 79 y 94 CT):
            # - Sin incapacidad: gross = base_salary completo del periodo
            # - Incapacidad normal (1-3 dias patrono): gross = salario_cotizable
            #     = dias_trabajadosxdiario + dias_1-3xdiariox50%
            #     El neto correcto: cotizable - CCSS (sin "salario fantasma")
            # - Maternidad completa: gross = 0 (patrono no paga nada)
            dis_in_period = []
            if rec.date_from and rec.date_to:
                active_dis = rec.disability_ids.filtered(
                    lambda d: d.state in ('confirmed', 'paid')
                    and d.date_start and d.date_end
                )
                dis_in_period = [
                    d for d in active_dis
                    if max(rec.date_from, d.date_start) <= min(rec.date_to, d.date_end)
                ]

            if dis_in_period:
                dias_periodo = (rec.date_to - rec.date_from).days + 1
                dias_incap = sum(
                    (min(rec.date_to, d.date_end) - max(rec.date_from, d.date_start)).days + 1
                    for d in dis_in_period
                )
                es_maternidad_total = all(d.disability_type == 'maternity' for d in dis_in_period)
                if es_maternidad_total and dias_incap >= dias_periodo:
                    sal_base = 0.0   # Maternidad total
                else:
                    sal_base = rec.salario_cotizable or 0.0  # Incapacidad parcial
            else:
                sal_base = rec.base_salary or 0.0  # Sin incapacidad

            bruto = round(
                sal_base +
                (rec.overtime_amount or 0.0) +
                (rec.vacation_amount or 0.0) +
                (rec.other_income or 0.0) +
                (rec.bono_salarial_amount or 0.0),
                2
            )
            rec.gross_salary = bruto

    @api.depends('gross_salary', 'salario_cotizable', 'bono_salarial_amount', 'overtime_amount', 'company_id', 'paternity_days',
                 'payroll_calendar_id',
                 'deduction_line_ids.amount',
                 'deduction_line_ids.line_type',
                 'deduction_line_ids.deduction_category',
                 'disability_ids.state',
                 'disability_ids.date_start',
                 'disability_ids.date_end',
                 'disability_ids.disability_type',
                 'employee_id.ins_risk_class',
                 'employee_id.income_tax_children',
                 'employee_id.income_tax_spouse_credit',
                 'employee_id.pensioner_type')
    def _compute_deductions(self) -> None:
        for rec in self:
            if rec.state == 'paid':
                continue  # Boleta pagada: valores congelados
            rh       = rec.env['planilla.rate.helper'].with_company(rec.company_id)
            # F3: tasa CCSS obrero depende del tipo de pensionado
            pensioner = rec.employee_id.pensioner_type or 'none'
            if pensioner == 'estado':
                ccss_emp = rh.get_ccss_pensionado_rate()  # 6.50% -- exonerado IVM
            else:
                ccss_emp = rh.get_ccss_employee_rate()    # 10.83% -- normal e IVM
            ccss_pat = rh.get_ccss_employer_rate()
            agu_rate = rh.get_aguinaldo_rate()
            # L1+L3 FIX: Cesantia con tabla Art. 29 CT segun anos de servicio.
            # Pasa entry_date del empleado para calcular la tasa exacta por ano.
            # Maximo legal: ano 8 -> 23 dias -> 6.3889%.
            emp_entry = rec.employee_id.entry_date if rec.employee_id else None
            ces_rate = rh.get_cesantia_rate(
                entry_date=emp_entry,
                period_date=rec.date_from
            )
            vac_rate = rh.get_vacation_rate()

            # -- Base cotizable para CCSS, Renta, ROP y provisiones -----------
            # REGLA: si hay incapacidades activas en el periodo, usar
            # salario_cotizable directamente (puede ser 0 para maternidad --
            # es un cero LEGITIMO, no un fallback).
            # Si NO hay incapacidades, usar gross_salary como base.
            # El fallback anterior (0 > 0 -> gross_salary) rompia maternidad
            # porque convertia el cero correcto en el salario bruto completo.
            #
            # Base legal: Art. 94 CT (maternidad, base=0),
            #             Art. 79 CT (incapacidad normal, base=dias_patronox50%),
            #             Art. 8 Ley ISR / Sala Segunda Voto 622-2010.

            # Detectar si hay alguna incapacidad activa que solape este periodo
            active_dis_period = rec.disability_ids.filtered(
                lambda d: d.state in ('confirmed', 'paid')
                and d.date_start and d.date_end
            )
            has_disability_in_period = False
            if rec.date_from and rec.date_to:
                for dis in active_dis_period:
                    if max(rec.date_from, dis.date_start) <= min(rec.date_to, dis.date_end):
                        has_disability_in_period = True
                        break

            if has_disability_in_period:
                # Hay incapacidad: respetar salario_cotizable aunque sea 0
                # (0 es el valor correcto para maternidad completa).
                # FIX BONO-INCAP: sumar bono salarial afecto CCSS.
                # FIX OVERTIME-INCAP: sumar horas extras a la base cotizable.
                # Las horas extras son ingreso salarial gravado con CCSS, Renta y
                # provisiones independientemente de que haya incapacidad en el periodo.
                # Sin este fix: CCSS, provisiones y Renta se calculan solo sobre
                # salario_cotizable (dias_laborados x diario), ignorando las HE.
                g = (
                    (rec.salario_cotizable  or 0.0) +
                    (rec.bono_salarial_amount or 0.0) +
                    (rec.overtime_amount    or 0.0)
                )
            else:
                # Sin incapacidad: base = salario bruto del periodo
                # (gross_salary ya incluye overtime + bono)
                g = rec.gross_salary or 0.0

            # FIX LICENCIAS: restar licencias sin goce y ausencias de la base cotizable.
            # Un dia no laborado no genera salario -> no debe generar CCSS obrero,
            # patronal, Renta, ROP ni provisiones (aguinaldo, cesantia, vacaciones).
            # Base legal: Arts. 31 y 79 CT / Circular CCSS DSA-1183 /
            #             Sala Segunda Voto 2018-000622.
            # Esto es identico al tratamiento de los dias subsidiados en incapacidades.
            licencias_sg = round(sum(
                l.amount for l in rec.deduction_line_ids
                if l.deduction_category in ('licencia_sin_goce', 'ausencia')
                and l.line_type == 'deduction'
            ), 2)
            if licencias_sg > 0:
                g = max(round(g - licencias_sg, 2), 0.0)

            # Almacenar la base cotizable final (para Resumen Completo y auditoria)
            rec.base_cotizable_final = g

            # SP: sin CCSS obrero
            if getattr(rec, 'is_sp', False):
                rec.ccss_employee = 0.0
            else:
                rec.ccss_employee = round(g * ccss_emp, 2)
            if rec.paternity_days > 0:
                daily = round(g / K.DIAS_MES, 2)
                rec.paternity_amount = round(daily * rec.paternity_days, 2)
            else:
                rec.paternity_amount = 0.0
            # FIX RENTA-BONO: calcular bonos NO recurrentes para excluirlos
            # de la anualizacion en _calc_income_tax.
            one_time_bonus = sum(
                l.amount for l in rec.deduction_line_ids
                if l.line_type == 'income'
                and l.deduction_category == 'bonus'
                and not l.is_recurring_bono
            )
            # F1 + F2: toggle base renta + creditos fiscales
            tax_neto, creditos = rec._calc_income_tax(g, rec.ccss_employee, one_time_bonus)
            rec.income_tax        = round(tax_neto, 2)
            rec.income_tax_credits = round(creditos, 2)
            # Desglose de creditos para mostrar en Resumen
            _freq   = rec._get_effective_freq() if hasattr(rec, '_get_effective_freq') else 'biweekly'
            _ff     = K.FREQ_FACTORS.get(_freq, 0.5)
            _emp    = rec.employee_id
            rec.credit_hijos   = round((_emp.income_tax_children or 0) * K.CREDITO_FISCAL_HIJO * _ff, 2)
            rec.credit_conyuge = round(K.CREDITO_FISCAL_CONYUGE * _ff if _emp.income_tax_spouse_credit else 0.0, 2)
            rec.income_tax_children_count = _emp.income_tax_children or 0
            # Texto de detalle para mostrar en la vista (evita campos monetarios nuevos en OWL)
            parts = []
            if rec.credit_conyuge:
                parts.append(f"Conyuge: CRC{rec.credit_conyuge:,.2f}")
            if rec.credit_hijos and rec.income_tax_children_count:
                parts.append(f"{rec.income_tax_children_count} hijo(s): CRC{rec.credit_hijos:,.2f}")
            rec.tax_credits_detail = '  .  '.join(parts) if parts else ''
            # SP: sin CCSS patronal ni INS
            if getattr(rec, 'is_sp', False):
                rec.ccss_employer = 0.0
                rec.ins_employer  = 0.0
            else:
                rec.ccss_employer = round(g * ccss_pat, 2)
                risk              = rec.employee_id.ins_risk_class or 'II'
                rec.ins_employer  = round(g * rh.get_ins_rate(risk), 2)
            freq        = rec._get_effective_freq()
            # FIX P-02 v58: usar K.FREQ_FACTORS centralizado
            prov_factor = K.FREQ_FACTORS.get(freq, 1.0)
            # FIX PROV-01: provisiones NO usan prov_factor porque g ya es
            # el salario del periodo (quincenal). prov_factor=0.5 para biweekly
            # causaba que la provision fuera la MITAD de lo correcto.
            # Correcto: provision_quincenal = g_quincenal × rate
            # Aguinaldo anual = g_quincenal × 8.33% × 24 = 1 salario mensual ✓
            # Art. 228 CT: aguinaldo se calcula sobre "salarios ordinarios devengados".
            # Los días subsidiados por CCSS (incapacidad) no son salario devengado
            # del patrono -> se excluyen de la base de aguinaldo.
            # El Excel lo implementa restando: Col20(incap CCSS) + Col22(incap INS)
            # + Col25+Col27 (permisos sin goce) del sub-total quincenal.
            # g ya tiene licencias_sg restadas; solo falta restar el subsidio CCSS.
            subsidio_incap = 0.0
            if has_disability_in_period and rec.date_from and rec.date_to:
                for dis in active_dis_period:
                    if not dis.date_start or not dis.date_end:
                        continue
                    ovlp_start = max(rec.date_from, dis.date_start)
                    ovlp_end   = min(rec.date_to, dis.date_end)
                    if ovlp_end < ovlp_start:
                        continue
                    # El subsidio pagado por CCSS en este periodo no es salario patronal
                    ovlp_days = (ovlp_end - ovlp_start).days + 1
                    total_days = max((dis.date_end - dis.date_start).days + 1, 1)
                    subsidio_incap += (dis.ccss_subsidy or 0.0) * ovlp_days / total_days
            subsidio_incap = round(subsidio_incap, 2)
            base_aguinaldo = max(round(g - subsidio_incap, 2), 0.0)

            rec.aguinaldo_provision = round(base_aguinaldo * agu_rate, 2)
            rec.cesantia_provision  = round(g * ces_rate, 2)
            rec.vacation_provision  = round(g * vac_rate, 2)

    def _calc_income_tax(self, gross: float, ccss_emp: float = 0.0,
                         one_time_bonus: float = 0.0) -> tuple:
        """
        Calculo progresivo de renta usando tramos configurados en la UI.

        NUEVO PARAMETRO: one_time_bonus -- monto de bonos NO recurrentes.
        Los bonos puntuales (is_recurring=False) NO se anualizan porque no
        se repetiran en el proximo periodo. Anualizarlos generaria un impuesto
        incorrecto proyectando ingresos que no existiran.

        Logica correcta (DGT-R-016-2026, Art. 33 LIR):
          - base recurrente = gross - one_time_bonus -> se anualiza x periodos/mes
          - bono puntual = one_time_bonus -> se agrega SIN anualizar
          monthly_equiv = (gross - one_time_bonus) x periods_per_month + one_time_bonus

        Retorna tupla (tax_neto, creditos_aplicados) para que _compute_deductions
        pueda almacenar ambos valores por separado en la boleta.

        Los tramos de renta del MTSS estan definidos en base mensual.
        Para periodos quincenales/semanales se anualiza el salario,
        se aplican los tramos equivalentes y se divide entre los periodos.
        Esto evita que un quincenal pague menos renta de la que corresponde.
        """
        # -- Toggle base de calculo (Feature 1) -------------------------------
        config = self.env['planilla.accounting.config'].search(
            [('company_id', '=', self.company_id.id)], limit=1
        )
        tax_base_mode = (config.income_tax_base or K.RENTA_BASE_DEFAULT) if config else K.RENTA_BASE_DEFAULT
        if tax_base_mode == 'net_ccss':
            gross = max(gross - ccss_emp, 0.0)
        # ---------------------------------------------------------------------

        freq = self._get_effective_freq()
        # FIX B-04 v58: usar K.PERIODOS_POR_MES corregido (bimonthly = 0.5)
        periods_per_month = K.PERIODOS_POR_MES.get(freq, 1)
        # FIX RENTA-BONO: solo anualizar la parte recurrente del salario.
        # Los bonos puntuales (is_recurring=False) no se proyectan al mes
        # porque no se repetiran en el siguiente periodo.
        base_recurrente = max(gross - one_time_bonus, 0.0)
        monthly_equiv = base_recurrente * periods_per_month + one_time_bonus

        brackets = self.env['planilla.income.tax.bracket'].search(
            # Buscar tramos especificos de la empresa actual O globales.
            ['|',
             ('company_id', '=', self.company_id.id),
             ('company_id', '=', False),
             ('active', '=', True)],
            order='sequence asc'
        )
        if not brackets:
            # Fallback: usar cualquier tramo activo en el sistema.
            # Cubre el caso donde los tramos fueron cargados con una company_id
            # distinta (ej: DEMO S.A.) pero se calculan boletas de otra empresa.
            brackets = self.env['planilla.income.tax.bracket'].search(
                [('active', '=', True)],
                order='sequence asc'
            )
        if not brackets:
            raise UserError(
                'No hay tramos de impuesto de renta activos configurados.\n\n'
                'Para crear boletas debe configurar los tramos primero:\n'
                'Planilla -> Configuracion -> Tramos de Renta\n\n'
                'Los tramos vigentes para 2026 son:\n'
                '   Exento: hasta 918,000\n'
                '   10%%: exceso de 918,000 a 1,381,000\n'
                '   15%%: exceso de 1,381,000 a 2,423,000\n'
                '   20%%: exceso de 2,423,000 a 4,845,000\n'
                '   25%%: exceso de 4,845,000 en adelante'
            )

        g           = monthly_equiv
        tax_monthly = 0.0
        for bracket in brackets:
            if g <= bracket.limit_from:
                break
            limit_to = bracket.limit_to if bracket.limit_to else float('inf')
            taxable  = min(g, limit_to) - bracket.limit_from
            if taxable > 0:
                tax_monthly += taxable * (bracket.rate / 100)

        tax_raw = tax_monthly / periods_per_month if periods_per_month else 0.0

        # -- Creditos fiscales (Feature 2) -- Art. 34 LIR ----------------------
        # Solo aplican si hay impuesto calculado. Se descuentan del impuesto
        # ya calculado. El resultado nunca puede ser negativo.
        emp             = self.employee_id
        freq_factor     = K.FREQ_FACTORS.get(freq, 1.0)
        credito_hijos   = (emp.income_tax_children or 0) * K.CREDITO_FISCAL_HIJO * freq_factor
        credito_conyuge = K.CREDITO_FISCAL_CONYUGE * freq_factor if emp.income_tax_spouse_credit else 0.0
        total_creditos  = credito_hijos + credito_conyuge

        # Los creditos solo reducen hasta CRC0 -- nunca generan reembolso
        creditos_aplicados = min(total_creditos, tax_raw)
        tax_neto           = max(tax_raw - total_creditos, 0.0)
        # ---------------------------------------------------------------------

        return tax_neto, creditos_aplicados

