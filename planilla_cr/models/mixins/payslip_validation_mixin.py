import logging
import datetime
from odoo import models, fields, api
from .. import planilla_const as K
from odoo.exceptions import UserError, ValidationError
from ..closed_period import PlanillaClosedPeriod

_logger = logging.getLogger(__name__)


class PayslipValidationMixin(models.AbstractModel):
    """
    Mixin: totales, helpers y validacion pre-confirmacion.
    _compute_totals, _get_bono_salarial_names, _is_bono_salarial,
    _validate_before_confirm.

    v58: P-02 -- freq_factor centralizado con K.FREQ_FACTORS.
         @api.depends agregado a _compute_totals.
    """
    _name = 'planilla.payslip.validation.mixin'
    _description = 'Mixin Validacion Boleta'

    @api.depends(
        'deduction_line_ids.amount',
        'deduction_line_ids.line_type',
        'deduction_line_ids.deduction_category',
        'deduction_line_ids.employee_charge_id',
        'deduction_line_ids.description',
        'bono_salarial_amount',          # FIX BUG-DOBLE-BONO: re-evaluar cuando cambia afecto_ccss
    )
    def _compute_deduction_summaries(self) -> None:
        """
        Calcula resumenes por categoria de deduccion/ingreso para la vista de lista.
        Permite al usuario ver de un vistazo cuanto pesa cada rubro en la boleta
        sin necesidad de abrir el formulario.
        Orden de aplicacion segun prioridad legal (BLP Legal / Art. 172 CT):
          1. Pension alimentaria (prioridad absoluta -- Ley 8590)
          2. Embargos judiciales (max. 25% neto -- Art. 172 CT)
          3. Prestamos y adelantos
          4. Cobros al empleado (charges)
          5. Cuotas sindicales / cooperativas
          6. Licencias sin goce / ausencias

        FIX BUG-DOBLE-BONO: amount_bonos_exentos excluye los bonos salariales
        (afecto_ccss=True) que ya estan contados en bono_salarial_amount.
        De lo contrario el Resumen Completo mostraria el mismo bono dos veces:
        una en "Bonos Salariales (afecto CCSS)" y otra en "Ingresos Adicionales".
        """
        for rec in self:
            lines = rec.deduction_line_ids
            rec.amount_pension_alimentaria = round(sum(
                l.amount for l in lines
                if l.deduction_category == 'pension_alimentaria' and l.line_type == 'deduction'
            ), 2)
            rec.amount_embargo = round(sum(
                l.amount for l in lines
                if l.deduction_category == 'embargo' and l.line_type == 'deduction'
            ), 2)
            rec.amount_loans = round(sum(
                l.amount for l in lines
                if l.deduction_category == 'loan' and l.line_type == 'deduction'
            ), 2)
            rec.amount_cobros_empleado = round(sum(
                l.amount for l in lines
                if l.employee_charge_id and l.line_type == 'deduction'
            ), 2)
            rec.amount_sindical = round(sum(
                l.amount for l in lines
                if l.deduction_category == 'sindical' and l.line_type == 'deduction'
            ), 2)
            rec.amount_cooperativa = round(sum(
                l.amount for l in lines
                if l.deduction_category == 'cooperativa' and l.line_type == 'deduction'
            ), 2)
            rec.amount_licencias_sin_goce = round(sum(
                l.amount for l in lines
                if l.deduction_category in ('licencia_sin_goce', 'ausencia') and l.line_type == 'deduction'
            ), 2)
            # -- Ingresos adicionales desglosados por sub-categoria ------------
            # Excluimos bonos salariales (afecto_ccss=True) que ya estan en
            # bono_salarial_amount para evitar doble conteo en el Resumen.
            nombres_salariales = rec._get_bono_salarial_names()
            # 1. Bonos exentos de CCSS/Renta (afecto_ccss=False):
            #    transporte, representacion, incentivos no salariales
            rec.amount_bonos_exentos = round(sum(
                l.amount for l in lines
                if l.line_type == 'income'
                and l.deduction_category == 'bonus'
                and (l.description or '').replace('Bono: ', '').strip() not in nombres_salariales
            ), 2)
            # 2. Licencias especiales con goce de sueldo:
            #    duelo, paternidad, matrimonio, adopcion, donacion de sangre
            rec.amount_licencias_con_goce = round(sum(
                l.amount for l in lines
                if l.line_type == 'income'
                and l.deduction_category == 'licencia_con_goce'
            ), 2)
            # 3. Otros ingresos adicionales (recurring benefits, manuales, etc.)
            rec.amount_otros_ingresos_adic = round(sum(
                l.amount for l in lines
                if l.line_type == 'income'
                and l.deduction_category not in ('bonus', 'licencia_con_goce')
            ), 2)

    @api.depends(
        'gross_salary', 'ccss_employee', 'income_tax', 'other_deductions',
        'paternity_amount',
        'ccss_employer', 'ins_employer', 'rop_employer', 'aguinaldo_provision',
        'cesantia_provision', 'vacation_provision', 'deduction_line_ids.amount',
        'deduction_line_ids.line_type', 'deduction_line_ids.deduction_category',
        'bono_salarial_amount',
    )
    def _compute_totals(self) -> None:
        for rec in self:
            # FIX v54b N+1: cargamos el set de nombres salariales UNA vez para el loop.
            nombres_salariales = rec._get_bono_salarial_names()

            # Licencias con goce: son ingresos adicionales que el patrono paga
            licencias_con_goce = sum(
                l.amount for l in rec.deduction_line_ids
                if l.deduction_category == 'licencia_con_goce' and l.line_type == 'income'
            )
            # Licencias sin goce: deducciones al empleado
            licencias_sin_goce = sum(
                l.amount for l in rec.deduction_line_ids
                if l.deduction_category == 'licencia_sin_goce' and l.line_type == 'deduction'
            )

            extra_income = sum(
                l.amount for l in rec.deduction_line_ids
                if l.line_type == 'income'
                and not (
                    l.deduction_category == 'bonus'
                    and (l.description or '').replace('Bono: ', '').strip() in nombres_salariales
                )
            )
            # Deducciones adicionales: sindicato, cooperativa, embargo, prestamos, licencias sin goce
            extra_deductions = sum(
                l.amount for l in rec.deduction_line_ids
                if l.line_type == 'deduction'
            )

            # Total Deducciones Obrero = CCSS + Renta + otras legales + deducciones adicionales
            rec.total_employee_deductions = round(
                (rec.ccss_employee or 0.0) +
                (rec.income_tax or 0.0) +
                (rec.other_deductions or 0.0) +
                extra_deductions, 2
            )
            # FIX-AUD-03: licencias con goce son costo patronal (igual que paternidad)
            rec.total_employer_cost = round(
                (rec.gross_salary or 0.0) +
                (rec.ccss_employer or 0.0) +
                (rec.ins_employer or 0.0) +
                (rec.rop_employer or 0.0) +
                (rec.aguinaldo_provision or 0.0) +
                (rec.cesantia_provision or 0.0) +
                (rec.vacation_provision or 0.0) +
                (rec.paternity_amount or 0.0) +
                (rec.employer_disability_cost or 0.0) +
                licencias_con_goce, 2  # FIX-AUD-03: duelo, matrimonio, paternidad, etc.
            )
            # Salario Neto = Bruto  TODAS las deducciones del obrero
            # + subsidio CCSS (pasa por planilla) + paternidad + ingresos adicionales
            # NOTA: ins_subsidy_total NO suma al neto -- el INS paga directamente al empleado
            rec.net_salary = round(
                (rec.gross_salary or 0.0) - rec.total_employee_deductions +
                (rec.ccss_subsidy_total or 0.0) +
                (rec.paternity_amount or 0.0) +
                extra_income, 2
            )
            rec.salary_payable = rec.net_salary

            # -- Desglose patrono vs CCSS/INS (todos los tipos de incapacidad) --
            ccss_sub = rec.ccss_subsidy_total or 0.0
            ins_sub  = rec.ins_subsidy_total  or 0.0
            if rec.disability_days_in_period:
                # Detectar maternidad con CCSS obrera sobre subsidio patronal
                active_dis_all = rec.disability_ids.filtered(
                    lambda d: d.state in ('confirmed', 'paid')
                    and d.date_start and d.date_end
                )
                mat_dis_now = []
                if rec.date_from and rec.date_to:
                    mat_dis_now = [
                        d for d in active_dis_all
                        if d.disability_type == 'maternity'
                        and max(rec.date_from, d.date_start) <= min(rec.date_to, d.date_end)
                    ]
                has_ccss_on_emp = any(getattr(d, 'maternity_ccss_on_employer', False) for d in mat_dis_now)
                has_split_50    = any(getattr(d, 'maternity_split_50', False) for d in mat_dis_now)

                if mat_dis_now and has_ccss_on_emp and has_split_50:
                    # Modalidad 50/50 + CCSS obrera sobre subsidio completo:
                    # - Base cotizable = subsidio total (ya en salario_cotizable)
                    # - CCSS obrera = 10.83%% sobre el total
                    # - Neto real = total - CCSS obrera
                    # - Patrono y CCSS pagan cada uno el 50%% del neto real
                    total_sub = ccss_sub  # = total del subsidio (toda la maternidad)
                    ccss_obrera = rec.ccss_employee or 0.0
                    neto_real   = round(total_sub - ccss_obrera, 2)
                    rec.neto_por_patrono = round(neto_real / 2.0, 2)
                    rec.neto_por_ccss    = round(neto_real / 2.0, 2)
                elif mat_dis_now and has_split_50 and not has_ccss_on_emp:
                    # Modalidad 50/50 sin CCSS obrera:
                    # Patrono paga 50%%, CCSS paga 50%%, empleado no pierde nada
                    mitad = round(ccss_sub / 2.0, 2) if ccss_sub else 0.0
                    rec.neto_por_patrono = mitad
                    rec.neto_por_ccss    = mitad
                else:
                    # Incapacidad normal: patrono = dias 1-3, CCSS = dias 4+
                    rec.neto_por_patrono = round(
                        (rec.gross_salary or 0.0) - rec.total_employee_deductions +
                        (rec.paternity_amount or 0.0) +
                        extra_income, 2
                    )
                    rec.neto_por_ccss = ccss_sub + ins_sub
            else:
                rec.neto_por_patrono = 0.0
                rec.neto_por_ccss    = 0.0

            if rec.salary_payable and rec.salary_payable > 0:
                rec.cost_per_net_colon = round(rec.total_employer_cost / rec.salary_payable, 2)
            else:
                rec.cost_per_net_colon = 0.0

    # -- Validacion pre-confirmacion -----------------------------------
    def _get_bono_salarial_names(self) -> set:
        """
        FIX v54b (N+1): Retorna el set de nombres de bonos salariales (afecto_ccss=True)
        del empleado. Se llama UNA vez desde _compute_totals y se usa para filtrar
        las lineas de ingreso, evitando un search() por cada linea.
        """
        self.ensure_one()
        bonos = self.env['planilla.bono'].search([
            ('employee_id', '=', self.employee_id.id),
            ('state', '=', 'active'),
        ])
        # Retorna set de nombres de bonos que afectan CCSS (o todos si no hay bono)
        return {b.name for b in bonos if b.afecto_ccss}

    def _is_bono_salarial(self, line):
        """
        Helper de compatibilidad -- usa cache interno para evitar N+1.
        Prefer _get_bono_salarial_names() cuando se llama en loop.
        """
        self.ensure_one()
        concepto = (line.description or '').replace('Bono: ', '').strip()
        bono_rec = self.env['planilla.bono'].search([
            ('employee_id', '=', self.employee_id.id),
            ('name', '=', concepto),
            ('state', '=', 'active'),
        ], limit=1)
        return not bono_rec or bono_rec.afecto_ccss

    def _validate_before_confirm(self) -> None:
        """Valida que la boleta tenga datos completos y correctos antes de confirmar.
        FIX PERF-06: pre-cargar rate_helper y min_salary una vez para todos los registros.
        Para 200 boletas: 400 queries -> 1.
        """
        errors = []
        warnings = []

        # FIX-D2: pre-cargar el salario minimo UNA vez fuera del loop.
        # El comentario original decia "FIX PERF-06: pre-cargar..." pero el codigo
        # lo llamaba dentro del loop (1 query por empleado -> N queries).
        # get_current_minimum sin categoria devuelve el minimo global (trabajador no calificado)
        # -- suficiente para detectar salarios claramente bajo el minimo legal.
        min_salary_global = self.env['planilla.minimum.salary'].get_current_minimum()

        for rec in self:
            emp = rec.employee_id
            prefix = f'[{emp.name}]'

            # -- Datos obligatorios del empleado ----------------------
            if not emp.identification_id:
                errors.append(f'{prefix} No tiene numero de cedula/identificacion registrado.')

            if not emp.base_salary or emp.base_salary <= 0:
                errors.append(f'{prefix} El salario base es 0 o no esta configurado.')

            if not rec.payroll_calendar_id:
                errors.append(f'{prefix} No tiene calendarizacion de planilla asignada.')

            # -- Determinar si tiene incapacidad/maternidad activa en el periodo --
            has_disability = bool(rec.disability_ids.filtered(
                lambda d: d.state in ('confirmed', 'paid')
            ))
            has_maternity = bool(rec.disability_ids.filtered(
                lambda d: d.disability_type == 'maternity' and d.state in ('confirmed', 'paid')
            ))
            is_part_time = (
                rec.employee_id.schedule_type_id and
                rec.employee_id.schedule_type_id.is_part_time
            )

            # -- Montos calculados coherentes -------------------------
            if rec.gross_salary <= 0:
                if has_disability or has_maternity:
                    # En incapacidad/maternidad el bruto puede ser 0 (CCSS paga directamente)
                    warnings.append(
                        f'{prefix} Salario bruto es 0 porque tiene '
                        f'{"maternidad" if has_maternity else "incapacidad"} activa en el periodo. '
                        f'Normal: la CCSS cubre el subsidio directamente.'
                    )
                else:
                    errors.append(
                        f'{prefix} El salario bruto calculado es 0 o negativo ({rec.gross_salary:,.2f}).'
                    )

            # -- Validacion salario minimo MTSS ---------------------------------
            min_salary = min_salary_global
            if rec.employee_id.employee_type_id and rec.employee_id.employee_type_id.name:
                specific = self.env['planilla.minimum.salary'].get_current_minimum(
                    category=rec.employee_id.employee_type_id.name
                )
                if specific > 0:
                    min_salary = specific
            if min_salary > 0:
                freq = rec.payroll_calendar_id.frequency if rec.payroll_calendar_id else 'monthly'
                freq_factor = K.FREQ_FACTORS.get(freq, 1.0)
                min_periodo = round(min_salary * freq_factor, 2)
                if rec.base_salary < min_periodo:
                    if is_part_time:
                        # Medio tiempo: advertencia, no error
                        warnings.append(
                            f'{prefix} Salario del periodo (CRC{rec.base_salary:,.2f}) '
                            f'menor al minimo MTSS (CRC{min_periodo:,.2f}). '
                            f'Exento: horario de medio tiempo / jornada parcial.'
                        )
                    elif has_maternity:
                        # Maternidad: advertencia, no error (CCSS subsidia el resto)
                        warnings.append(
                            f'{prefix} Salario del periodo (CRC{rec.base_salary:,.2f}) '
                            f'menor al minimo MTSS (CRC{min_periodo:,.2f}). '
                            f'Exento: licencia de maternidad activa (Art. 94 CT).'
                        )
                    elif has_disability:
                        # Incapacidad: advertencia, no error
                        warnings.append(
                            f'{prefix} Salario del periodo (CRC{rec.base_salary:,.2f}) '
                            f'menor al minimo MTSS (CRC{min_periodo:,.2f}). '
                            f'Exento: incapacidad activa en el periodo.'
                        )
                    else:
                        errors.append(
                            f'{prefix} El salario base del periodo (CRC{rec.base_salary:,.2f}) '
                            f'esta por debajo del minimo MTSS vigente para el periodo '
                            f'(CRC{min_periodo:,.2f} = CRC{min_salary:,.2f}/mes x {freq_factor}). '
                            f'Corrija el salario o verifique la categoria ocupacional.'
                        )
            elif emp.base_salary and 0 < emp.base_salary < 100_000:
                warnings.append(
                    f'{prefix} El salario base (CRC{emp.base_salary:,.0f}) parece muy bajo. '
                    f'Configure los Salarios Minimos MTSS en Configuracion para validacion precisa.'
                )

            if rec.net_salary < 0:
                errors.append(
                    f'{prefix} El salario neto es negativo ({rec.net_salary:,.2f}). '
                    f'Las deducciones superan el salario bruto.'
                )

            # AUDIT-03: Validar que embargos judiciales no superen el 25% del neto
            # Base legal: Art. 172 Codigo de Trabajo CR.
            # El limite aplica sobre el salario NETO (despues de CCSS y Renta).
            # Excepcion: pension alimentaria NO tiene limite de porcentaje (Ley 8590).
            total_embargos = sum(
                l.amount for l in rec.deduction_line_ids
                if l.deduction_category == 'embargo' and l.line_type == 'deduction'
            )
            if total_embargos > 0 and rec.net_salary > 0:
                max_embargo_legal = round(rec.net_salary * K.MAX_PCT_EMBARGO / 100, 2)
                if total_embargos > max_embargo_legal + 0.5:  # tolerancia CRC0.50 por redondeo
                    errors.append(
                        f'{prefix} Los embargos judiciales (CRC{total_embargos:,.2f}) superan '
                        f'el limite legal del {K.MAX_PCT_EMBARGO:.0f}% del salario neto '
                        f'(CRC{max_embargo_legal:,.2f}). '
                        f'Base legal: Art. 172 Codigo de Trabajo CR. '
                        f'Reduzca el monto del embargo a un maximo de CRC{max_embargo_legal:,.2f}.'
                    )


            # (duelo, paternidad, matrimonio), subsidio CCSS por incapacidad, o paternidad --
            # todos son ingresos adicionales legitimos que el patrono agrega al neto.
            # La validacion correcta es: neto no debe superar bruto + todos los ingresos adicionales legitimos.
            # FIX-M3: NO separar licencias_con_goce del sum(income_lines) -- ya estan incluidas
            # en ese total. Separarlas y sumarlas por separado causaba doble conteo, haciendo
            # que max_net_expected fuera mayor de lo correcto y la validacion nunca detectara errores.
            max_net_expected = round(
                rec.gross_salary
                + (rec.ccss_subsidy_total or 0.0)
                + (rec.paternity_amount or 0.0)
                + sum(l.amount for l in rec.deduction_line_ids if l.line_type == 'income'), 2
            )
            if rec.net_salary > max_net_expected + 1.0:  # tolerancia CRC1 por redondeo
                errors.append(
                    f'{prefix} El salario neto ({rec.net_salary:,.2f}) supera '
                    f'el maximo esperado ({max_net_expected:,.2f}). Verifique las deducciones e ingresos adicionales.'
                )

            # -- CCSS coherente ---------------------------------------
            # FIX D-04 v53: usar get_ccss_employee_rate() en lugar de 0.1083 hardcoded
            # para que la validacion respete la tasa configurada en la empresa.
            rh = rec.env['planilla.rate.helper'].with_company(rec.company_id)
            expected_ccss_emp = round(rec.gross_salary * rh.get_ccss_employee_rate(), 2)
            if rec.ccss_employee and abs(rec.ccss_employee - expected_ccss_emp) > 1.0:
                warnings.append(
                    f'{prefix} La cuota CCSS obrero ({rec.ccss_employee:,.2f}) '
                    f'difiere del calculo esperado ({expected_ccss_emp:,.2f}). '
                    f'Verifique si hay deducciones manuales.'
                )

            # -- FIX v49 Bug 3: validar attendance_hours y existencia de registros --
            if (rec.employee_id.payroll_calculation_method or 'fixed') == 'attendance':
                # FIX TZ v55: mismo ajuste UTC-6 que en _compute_attendance_hours
                dt_from = fields.Datetime.to_datetime(rec.date_from)
                dt_to   = fields.Datetime.to_datetime(rec.date_to) + datetime.timedelta(days=1, hours=6)

                # Contar registros totales en el periodo (abiertos y cerrados)
                total_att = self.env['hr.attendance'].search_count([
                    ('employee_id', '=', emp.id),
                    ('check_in',    '>=', dt_from),
                    ('check_in',    '<',  dt_to),
                ])

                # Error si no hay NINGUN registro de asistencia en el periodo
                if total_att == 0:
                    errors.append(
                        f'{prefix} Modo de calculo por asistencia pero no hay '
                        f'registros de asistencia (check_in) en el periodo '
                        f'{rec.date_from} -- {rec.date_to}. '
                        f'El salario bruto seria CRC0. '
                        f'Registre las asistencias antes de confirmar.'
                    )

                # Error si attendance_hours es 0 aunque haya registros (todos abiertos o con 0h)
                elif (rec.attendance_hours or 0.0) <= 0:
                    errors.append(
                        f'{prefix} Las horas trabajadas calculadas son 0 '
                        f'en modo de calculo por asistencia. '
                        f'Verifique que los registros tengan check_out y horas validas.'
                    )

                # Advertencia si el gross_salary resultante es 0 (captura otros casos)
                elif rec.gross_salary <= 0:
                    errors.append(
                        f'{prefix} El salario bruto es CRC0 en modo attendance '
                        f'({rec.attendance_hours:.1f}h trabajadas). '
                        f'Verifique la tasa horaria y las asistencias del periodo.'
                    )

                # -- Asistencias abiertas (C5) ------------------------------------
                open_att = self.env['hr.attendance'].search_count([
                    ('employee_id', '=', emp.id),
                    ('check_in',    '>=', dt_from),
                    ('check_in',    '<',  dt_to),
                    ('check_out',   '=',  False),
                ])
                if open_att:
                    errors.append(
                        f'{prefix} Hay {open_att} registro(s) de asistencia sin check_out '
                        f'en el periodo. Corrija las marcas antes de confirmar.'
                    )

            # -- Periodo cerrado --------------------------------------
            closed = PlanillaClosedPeriod.is_period_closed(
                self.env, rec.company_id.id,
                rec.date_from, rec.date_to,
                rec.branch_id.id if rec.branch_id else False
            )
            if closed:
                errors.append(
                    f'{prefix} El periodo {rec.date_from} - {rec.date_to} esta cerrado '
                    f'("{closed.name}", cerrado el {closed.closed_date.strftime("%d/%m/%Y")} '
                    f'por {closed.closed_by.name}). No se puede confirmar una boleta en un periodo cerrado.'
                )

            # -- Duplicados con periodos solapados ----------------
            # FIX v512 BUG-06: usar solapamiento (<=,>=) igual que _check_no_duplicate_employee_period.
            # La validacion anterior usaba fechas exactas (=) y dejaba pasar boletas solapadas
            # que fallaban luego en el constraint de BD con un error menos descriptivo.
            duplicate = self.env['planilla.payslip.cr'].search([
                ('employee_id', '=', emp.id),
                ('date_from',   '<=', rec.date_to),
                ('date_to',     '>=', rec.date_from),
                ('state', 'in', ['confirmed', 'done']),
                ('id', '!=', rec.id),
            ], limit=1)
            if duplicate:
                errors.append(
                    f'{prefix} Ya existe una boleta confirmada o pagada que se solapa con el '
                    f'periodo {rec.date_from} -- {rec.date_to} '
                    f'(Ref: {duplicate.name}, periodo: {duplicate.date_from} -- {duplicate.date_to}).'
                )

        if errors:
            raise UserError(
                'No se puede confirmar. Se encontraron los siguientes errores:\n\n' +
                '\n'.join(f' {e}' for e in errors)
            )

        if warnings:
            # Los warnings se muestran pero no bloquean
            return '\n'.join(warnings)
        return None

    # -- Acciones ------------------------------------------------------
