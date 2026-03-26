import logging
import datetime
from odoo import models, fields, api
from .. import planilla_const as K
from odoo.exceptions import UserError, ValidationError
from ..closed_period import PlanillaClosedPeriod

_logger = logging.getLogger(__name__)


class PayslipValidationMixin(models.AbstractModel):
    """
    Mixin: totales, helpers y validación pre-confirmación.
    _compute_totals, _get_bono_salarial_names, _is_bono_salarial,
    _validate_before_confirm.

    v58: P-02 — freq_factor centralizado con K.FREQ_FACTORS.
         @api.depends agregado a _compute_totals.
    """
    _name = 'planilla.payslip.validation.mixin'
    _description = 'Mixin Validacion Boleta'

    @api.depends(
        'deduction_line_ids.amount',
        'deduction_line_ids.line_type',
        'deduction_line_ids.deduction_category',
        'deduction_line_ids.employee_charge_id',
    )
    def _compute_deduction_summaries(self) -> None:
        """
        Calcula resúmenes por categoría de deducción/ingreso para la vista de lista.
        Permite al usuario ver de un vistazo cuánto pesa cada rubro en la boleta
        sin necesidad de abrir el formulario.
        Orden de aplicación según prioridad legal (BLP Legal / Art. 172 CT):
          1. Pensión alimentaria (prioridad absoluta — Ley 8590)
          2. Embargos judiciales (máx. 25% neto — Art. 172 CT)
          3. Préstamos y adelantos
          4. Cobros al empleado (charges)
          5. Cuotas sindicales / cooperativas
          6. Licencias sin goce / ausencias

        FIX BUG-DOBLE-BONO: amount_bonos_exentos excluye los bonos salariales
        (afecto_ccss=True) que ya están contados en bono_salarial_amount.
        De lo contrario el Resumen Completo mostraría el mismo bono dos veces:
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
            # FIX: excluir bonos salariales (afecto_ccss=True) que ya están en
            # bono_salarial_amount. Solo contar ingresos NO salariales:
            # licencias con goce, subsidios exentos, recurring benefits, etc.
            nombres_salariales = rec._get_bono_salarial_names()
            rec.amount_bonos_exentos = round(sum(
                l.amount for l in lines
                if l.line_type == 'income'
                and not (
                    l.deduction_category == 'bonus'
                    and (l.description or '').replace('Bono: ', '').strip() in nombres_salariales
                )
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
            # Deducciones adicionales: sindicato, cooperativa, embargo, préstamos, licencias sin goce
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
            # Salario Neto = Bruto − TODAS las deducciones del obrero
            # + subsidio CCSS + paternidad + ingresos adicionales (incl. licencias con goce)
            # − licencias sin goce (ya están en extra_deductions → total_employee_deductions)
            rec.net_salary = round(
                (rec.gross_salary or 0.0) - rec.total_employee_deductions +
                (rec.ccss_subsidy_total or 0.0) +
                (rec.paternity_amount or 0.0) +
                extra_income, 2
            )
            rec.salary_payable = rec.net_salary

            if rec.salary_payable and rec.salary_payable > 0:
                rec.cost_per_net_colon = round(rec.total_employer_cost / rec.salary_payable, 2)
            else:
                rec.cost_per_net_colon = 0.0

    # ── Validacion pre-confirmacion ───────────────────────────────────
    def _get_bono_salarial_names(self) -> set:
        """
        FIX v54b (N+1): Retorna el set de nombres de bonos salariales (afecto_ccss=True)
        del empleado. Se llama UNA vez desde _compute_totals y se usa para filtrar
        las líneas de ingreso, evitando un search() por cada línea.
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
        Helper de compatibilidad — usa caché interno para evitar N+1.
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
        Para 200 boletas: 400 queries → 1.
        """
        errors = []
        warnings = []

        # FIX-D2: pre-cargar el salario mínimo UNA vez fuera del loop.
        # El comentario original decía "FIX PERF-06: pre-cargar..." pero el código
        # lo llamaba dentro del loop (1 query por empleado → N queries).
        # get_current_minimum sin categoría devuelve el mínimo global (trabajador no calificado)
        # — suficiente para detectar salarios claramente bajo el mínimo legal.
        min_salary_global = self.env['planilla.minimum.salary'].get_current_minimum()

        for rec in self:
            emp = rec.employee_id
            prefix = f'[{emp.name}]'

            # ── Datos obligatorios del empleado ──────────────────────
            if not emp.identification_id:
                errors.append(f'{prefix} No tiene numero de cedula/identificacion registrado.')

            if not emp.base_salary or emp.base_salary <= 0:
                errors.append(f'{prefix} El salario base es 0 o no esta configurado.')

            if not rec.payroll_calendar_id:
                errors.append(f'{prefix} No tiene calendarizacion de planilla asignada.')

            # ── Montos calculados coherentes ─────────────────────────
            if rec.gross_salary <= 0:
                errors.append(f'{prefix} El salario bruto calculado es 0 o negativo ({rec.gross_salary:,.2f}).')

            # ── Validación salario mínimo MTSS — FIX M-03 v54 / FIX-D2 ──────
            # FIX-D2: usar el valor pre-cargado fuera del loop (1 query total).
            # Se usa el mínimo global; si el empleado tiene tipo configurado se intenta
            # una consulta más específica solo cuando el global no es suficiente.
            min_salary = min_salary_global
            if rec.employee_id.employee_type_id and rec.employee_id.employee_type_id.name:
                specific = self.env['planilla.minimum.salary'].get_current_minimum(
                    category=rec.employee_id.employee_type_id.name
                )
                if specific > 0:
                    min_salary = specific
            if min_salary > 0:
                freq = rec.payroll_calendar_id.frequency if rec.payroll_calendar_id else 'monthly'
                # FIX P-02 v58: usar K.FREQ_FACTORS centralizado
                freq_factor = K.FREQ_FACTORS.get(freq, 1.0)
                min_periodo = round(min_salary * freq_factor, 2)
                if rec.base_salary < min_periodo:
                    errors.append(
                        f'{prefix} El salario base del período (₡{rec.base_salary:,.2f}) '
                        f'está por debajo del mínimo MTSS vigente para el período '
                        f'(₡{min_periodo:,.2f} = ₡{min_salary:,.2f}/mes × {freq_factor}). '
                        f'Corrija el salario o verifique la categoría ocupacional.'
                    )
            elif emp.base_salary and 0 < emp.base_salary < 100_000:
                warnings.append(
                    f'{prefix} El salario base (₡{emp.base_salary:,.0f}) parece muy bajo. '
                    f'Configure los Salarios Mínimos MTSS en Configuración para validación precisa.'
                )

            if rec.net_salary < 0:
                errors.append(
                    f'{prefix} El salario neto es negativo ({rec.net_salary:,.2f}). '
                    f'Las deducciones superan el salario bruto.'
                )

            # AUDIT-03: Validar que embargos judiciales no superen el 25% del neto
            # Base legal: Art. 172 Código de Trabajo CR.
            # El límite aplica sobre el salario NETO (después de CCSS y Renta).
            # Excepción: pensión alimentaria NO tiene límite de porcentaje (Ley 8590).
            total_embargos = sum(
                l.amount for l in rec.deduction_line_ids
                if l.deduction_category == 'embargo' and l.line_type == 'deduction'
            )
            if total_embargos > 0 and rec.net_salary > 0:
                max_embargo_legal = round(rec.net_salary * K.MAX_PCT_EMBARGO / 100, 2)
                if total_embargos > max_embargo_legal + 0.5:  # tolerancia ₡0.50 por redondeo
                    errors.append(
                        f'{prefix} Los embargos judiciales (₡{total_embargos:,.2f}) superan '
                        f'el límite legal del {K.MAX_PCT_EMBARGO:.0f}% del salario neto '
                        f'(₡{max_embargo_legal:,.2f}). '
                        f'Base legal: Art. 172 Código de Trabajo CR. '
                        f'Reduzca el monto del embargo a un máximo de ₡{max_embargo_legal:,.2f}.'
                    )


            # (duelo, paternidad, matrimonio), subsidio CCSS por incapacidad, o paternidad —
            # todos son ingresos adicionales legítimos que el patrono agrega al neto.
            # La validación correcta es: neto no debe superar bruto + todos los ingresos adicionales legítimos.
            # FIX-M3: NO separar licencias_con_goce del sum(income_lines) — ya están incluidas
            # en ese total. Separarlas y sumarlas por separado causaba doble conteo, haciendo
            # que max_net_expected fuera mayor de lo correcto y la validación nunca detectara errores.
            max_net_expected = round(
                rec.gross_salary
                + (rec.ccss_subsidy_total or 0.0)
                + (rec.paternity_amount or 0.0)
                + sum(l.amount for l in rec.deduction_line_ids if l.line_type == 'income'), 2
            )
            if rec.net_salary > max_net_expected + 1.0:  # tolerancia ₡1 por redondeo
                errors.append(
                    f'{prefix} El salario neto ({rec.net_salary:,.2f}) supera '
                    f'el máximo esperado ({max_net_expected:,.2f}). Verifique las deducciones e ingresos adicionales.'
                )

            # ── CCSS coherente ───────────────────────────────────────
            # FIX D-04 v53: usar get_ccss_employee_rate() en lugar de 0.1083 hardcoded
            # para que la validación respete la tasa configurada en la empresa.
            rh = rec.env['planilla.rate.helper'].with_company(rec.company_id)
            expected_ccss_emp = round(rec.gross_salary * rh.get_ccss_employee_rate(), 2)
            if rec.ccss_employee and abs(rec.ccss_employee - expected_ccss_emp) > 1.0:
                warnings.append(
                    f'{prefix} La cuota CCSS obrero ({rec.ccss_employee:,.2f}) '
                    f'difiere del calculo esperado ({expected_ccss_emp:,.2f}). '
                    f'Verifique si hay deducciones manuales.'
                )

            # ── FIX v49 Bug 3: validar attendance_hours y existencia de registros ──
            if (rec.employee_id.payroll_calculation_method or 'fixed') == 'attendance':
                # FIX TZ v55: mismo ajuste UTC-6 que en _compute_attendance_hours
                dt_from = fields.Datetime.to_datetime(rec.date_from)
                dt_to   = fields.Datetime.to_datetime(rec.date_to) + datetime.timedelta(days=1, hours=6)

                # Contar registros totales en el período (abiertos y cerrados)
                total_att = self.env['hr.attendance'].search_count([
                    ('employee_id', '=', emp.id),
                    ('check_in',    '>=', dt_from),
                    ('check_in',    '<',  dt_to),
                ])

                # Error si no hay NINGÚN registro de asistencia en el período
                if total_att == 0:
                    errors.append(
                        f'{prefix} Modo de cálculo por asistencia pero no hay '
                        f'registros de asistencia (check_in) en el período '
                        f'{rec.date_from} — {rec.date_to}. '
                        f'El salario bruto sería ₡0. '
                        f'Registre las asistencias antes de confirmar.'
                    )

                # Error si attendance_hours es 0 aunque haya registros (todos abiertos o con 0h)
                elif (rec.attendance_hours or 0.0) <= 0:
                    errors.append(
                        f'{prefix} Las horas trabajadas calculadas son 0 '
                        f'en modo de cálculo por asistencia. '
                        f'Verifique que los registros tengan check_out y horas válidas.'
                    )

                # Advertencia si el gross_salary resultante es 0 (captura otros casos)
                elif rec.gross_salary <= 0:
                    errors.append(
                        f'{prefix} El salario bruto es ₡0 en modo attendance '
                        f'({rec.attendance_hours:.1f}h trabajadas). '
                        f'Verifique la tasa horaria y las asistencias del período.'
                    )

                # ── Asistencias abiertas (C5) ────────────────────────────────────
                open_att = self.env['hr.attendance'].search_count([
                    ('employee_id', '=', emp.id),
                    ('check_in',    '>=', dt_from),
                    ('check_in',    '<',  dt_to),
                    ('check_out',   '=',  False),
                ])
                if open_att:
                    errors.append(
                        f'{prefix} Hay {open_att} registro(s) de asistencia sin check_out '
                        f'en el período. Corrija las marcas antes de confirmar.'
                    )

            # ── Periodo cerrado ──────────────────────────────────────
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

            # ── Duplicados con períodos solapados ────────────────
            # FIX v512 BUG-06: usar solapamiento (<=,>=) igual que _check_no_duplicate_employee_period.
            # La validación anterior usaba fechas exactas (=) y dejaba pasar boletas solapadas
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
                    f'período {rec.date_from} — {rec.date_to} '
                    f'(Ref: {duplicate.name}, período: {duplicate.date_from} — {duplicate.date_to}).'
                )

        if errors:
            raise UserError(
                'No se puede confirmar. Se encontraron los siguientes errores:\n\n' +
                '\n'.join(f'• {e}' for e in errors)
            )

        if warnings:
            # Los warnings se muestran pero no bloquean
            return '\n'.join(warnings)
        return None

    # ── Acciones ──────────────────────────────────────────────────────
