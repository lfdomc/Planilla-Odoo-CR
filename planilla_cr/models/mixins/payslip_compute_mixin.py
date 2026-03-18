import logging
import datetime
from odoo import models, fields, api
from .. import planilla_const as K
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class PayslipComputeMixin(models.AbstractModel):
    """
    Mixin: campos computados de la boleta de pago.
    Métodos: _compute_proportional_days, _compute_name, _compute_base_salary,
    _compute_attendance_hours, _compute_extras, _compute_bono_salarial,
    _compute_gross, _compute_deductions, _onchange_auto_proportional,
    _calc_income_tax, _compute_totals.

    v58: _calc_income_tax movido aquí desde payslip_cr.py.
         P-02: dicts de frecuencia centralizados con K.FREQ_FACTORS.
         B-04: K.PERIODOS_POR_MES['bimonthly'] corregido a 0.5.
    """
    _name = 'planilla.payslip.compute.mixin'
    _description = 'Mixin Computos Boleta'

    # FIX v58: @api.depends faltaba en la versión original del mixin
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

    @api.depends('employee_id', 'date_from', 'date_to', 'attendance_hours',
                 'is_proportional', 'proportional_factor', 'payroll_calendar_id',
                 'days_in_period',
                 'employee_id.schedule_type_id',
                 'employee_id.base_salary')
    def _compute_base_salary(self):
        for rec in self:
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
                freq              = rec.payroll_calendar_id.frequency if rec.payroll_calendar_id else 'monthly'
                # FIX B-04 v58: usar K.PERIODOS_POR_MES — bimonthly ahora es 0.5 (corregido)
                periods_per_month = K.PERIODOS_POR_MES.get(freq, 1)
                monthly_hours     = hours_per_day * period_days * periods_per_month
                hourly_rate       = emp.base_salary / monthly_hours if monthly_hours else 0.0
                rec.base_salary   = round(hourly_rate * (rec.attendance_hours or 0.0), 2)
            else:
                raw  = emp.base_salary or 0.0
                freq = rec.payroll_calendar_id.frequency if rec.payroll_calendar_id else 'monthly'
                # FIX P-02 v58: usar K.FREQ_FACTORS centralizado en planilla_const
                freq_factor = K.FREQ_FACTORS.get(freq, 1.0)
                prop_factor = rec.proportional_factor if rec.is_proportional else 1.0
                rec.base_salary = round(raw * freq_factor * prop_factor, 2)

    @api.onchange('date_from', 'date_to', 'employee_id')
    def _onchange_auto_proportional(self):
        """Auto-detecta si el empleado ingresó o salió durante el período."""
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
                    f'⚠ ADVERTENCIA: Existen {len(open_att)} registro(s) de asistencia '
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
                rec.attendance_details = 'Sin registros de asistencia en el período.'

    @api.depends('overtime_ids.amount', 'overtime_ids.state',
                 'vacation_ids.total_amount', 'vacation_ids.state',
                 'disability_ids.days', 'disability_ids.ccss_subsidy',
                 'disability_ids.employer_cost', 'disability_ids.state')
    def _compute_extras(self):
        for rec in self:
            rec.overtime_amount  = sum(o.amount for o in rec.overtime_ids if o.state == 'approved')
            rec.vacation_amount  = sum(v.total_amount for v in rec.vacation_ids if v.state == 'approved')
            active_dis = rec.disability_ids.filtered(lambda d: d.state in ('confirmed', 'paid'))
            rec.disability_days          = sum(d.days for d in active_dis)
            rec.ccss_subsidy_total       = round(sum(d.ccss_subsidy for d in active_dis), 2)
            rec.employer_disability_cost = round(sum(d.employer_cost for d in active_dis), 2)

    @api.depends('deduction_line_ids.amount', 'deduction_line_ids.line_type',
                 'deduction_line_ids.deduction_category')
    def _compute_bono_salarial(self) -> None:
        """
        FIX C-01 v54: Suma de bonos con afecto_ccss=True para integrar al salario bruto.

        FIX v512 BUG-04 + BP-05:
          - BUG-04: eliminado N+1 — se precarga un único dict de bonos para TODOS los
            registros del recordset antes del loop, en lugar de un search por cada boleta.
          - BP-05: corregida lógica de afecto_ccss: solo se suma si el bono SE ENCUENTRA
            y tiene afecto_ccss=True. Antes la lógica era inversa: si no encontraba el bono
            lo asumía afecto_ccss=True (inclusivo por defecto — incorrecto fiscalmente).
        """
        if not self:
            return

        # Pre-cargar todos los bonos activos de TODOS los empleados del recordset
        # en UNA sola query. Elimina el N+1 anterior (1 query por boleta).
        emp_ids = self.mapped('employee_id.id')
        all_bonos = self.env['planilla.bono'].search([
            ('employee_id', 'in', emp_ids),
            ('state', '=', 'active'),
        ])
        # Índice: emp_id → {bono_name: bono_rec}
        bono_index: dict = {}
        for b in all_bonos:
            bono_index.setdefault(b.employee_id.id, {})[b.name] = b

        for rec in self:
            bonus_lines = rec.deduction_line_ids.filtered(
                lambda l: l.line_type == 'income' and l.deduction_category == 'bonus'
            )
            if not bonus_lines:
                rec.bono_salarial_amount = 0.0
                continue

            emp_bonos = bono_index.get(rec.employee_id.id, {})
            total = 0.0
            for line in bonus_lines:
                concepto = (line.description or '').replace('Bono: ', '').strip()
                bono_rec = emp_bonos.get(concepto)
                # FIX BP-05: solo sumar si el bono existe Y tiene afecto_ccss=True.
                # La lógica anterior (not bono_rec OR afecto_ccss) era incorrecta:
                # sumaba bonos no encontrados como si fueran salariales (fiscalmente erróneo).
                if bono_rec and bono_rec.afecto_ccss:
                    total += line.amount
            rec.bono_salarial_amount = round(total, 2)

    @api.depends('base_salary', 'overtime_amount', 'vacation_amount', 'other_income',
                 'bono_salarial_amount')
    def _compute_gross(self) -> None:
        for rec in self:
            rec.gross_salary = round(
                (rec.base_salary or 0.0) +
                (rec.overtime_amount or 0.0) +
                (rec.vacation_amount or 0.0) +
                (rec.other_income or 0.0) +
                (rec.bono_salarial_amount or 0.0),
                2
            )

    @api.depends('gross_salary', 'company_id', 'paternity_days',
                 'payroll_calendar_id',
                 'employee_id.ins_risk_class')
    def _compute_deductions(self) -> None:
        for rec in self:
            rh       = rec.env['planilla.rate.helper'].with_company(rec.company_id)
            ccss_emp = rh.get_ccss_employee_rate()
            ccss_pat = rh.get_ccss_employer_rate()
            agu_rate = rh.get_aguinaldo_rate()
            ces_rate = rh.get_cesantia_rate()
            vac_rate = rh.get_vacation_rate()
            g = rec.gross_salary or 0.0
            rec.ccss_employee = round(g * ccss_emp, 2)
            if rec.paternity_days > 0:
                daily = round(g / K.DIAS_MES, 2)
                rec.paternity_amount = round(daily * rec.paternity_days, 2)
            else:
                rec.paternity_amount = 0.0
            rec.income_tax    = round(rec._calc_income_tax(g), 2)
            rec.ccss_employer = round(g * ccss_pat, 2)
            risk              = rec.employee_id.ins_risk_class or 'II'
            rec.ins_employer  = round(g * rh.get_ins_rate(risk), 2)
            freq        = rec.payroll_calendar_id.frequency if rec.payroll_calendar_id else 'monthly'
            # FIX P-02 v58: usar K.FREQ_FACTORS centralizado
            prov_factor = K.FREQ_FACTORS.get(freq, 1.0)
            rec.aguinaldo_provision = round(g * agu_rate * prov_factor, 2)
            rec.cesantia_provision  = round(g * ces_rate * prov_factor, 2)
            rec.vacation_provision  = round(g * vac_rate * prov_factor, 2)

    def _calc_income_tax(self, gross: float) -> float:
        """
        Calculo progresivo de renta usando tramos configurados en la UI.
        v58: movido desde payslip_cr.py al mixin correspondiente.

        Los tramos de renta del MTSS están definidos en base mensual.
        Para períodos quincenales/semanales se anualiza el salario,
        se aplican los tramos equivalentes y se divide entre los períodos.
        Esto evita que un quincenal pague menos renta de la que corresponde.
        """
        freq = self.payroll_calendar_id.frequency if self.payroll_calendar_id else 'monthly'
        # FIX B-04 v58: usar K.PERIODOS_POR_MES corregido (bimonthly = 0.5)
        periods_per_month = K.PERIODOS_POR_MES.get(freq, 1)
        monthly_equiv     = gross * periods_per_month

        brackets = self.env['planilla.income.tax.bracket'].search(
            [('active', '=', True)], order='sequence asc'
        )
        if not brackets:
            # Fallback hardcoded — Tramos 2026 (DGT-R-016-2026)
            # ACTUALIZAR cada enero en:
            # https://www.hacienda.go.cr/contenido/15169-impuesto-sobre-la-renta-asalariados
            g = monthly_equiv
            if g <= K.RENTA_EXENTO:
                tax_monthly = 0.0
            elif g <= K.RENTA_TOPE_10:
                tax_monthly = (g - K.RENTA_EXENTO) * K.RENTA_TASA_1
            elif g <= K.RENTA_TOPE_15:
                tax_monthly = ((K.RENTA_TOPE_10 - K.RENTA_EXENTO) * K.RENTA_TASA_1
                               + (g - K.RENTA_TOPE_10) * K.RENTA_TASA_2)
            elif g <= K.RENTA_TOPE_20:
                tax_monthly = ((K.RENTA_TOPE_10 - K.RENTA_EXENTO) * K.RENTA_TASA_1
                               + (K.RENTA_TOPE_15 - K.RENTA_TOPE_10) * K.RENTA_TASA_2
                               + (g - K.RENTA_TOPE_15) * K.RENTA_TASA_3)
            else:
                tax_monthly = ((K.RENTA_TOPE_10 - K.RENTA_EXENTO) * K.RENTA_TASA_1
                               + (K.RENTA_TOPE_15 - K.RENTA_TOPE_10) * K.RENTA_TASA_2
                               + (K.RENTA_TOPE_20 - K.RENTA_TOPE_15) * K.RENTA_TASA_3
                               + (g - K.RENTA_TOPE_20) * K.RENTA_TASA_4)
        else:
            g           = monthly_equiv
            tax_monthly = 0.0
            for bracket in brackets:
                if g <= bracket.limit_from:
                    break
                limit_to = bracket.limit_to if bracket.limit_to else float('inf')
                taxable  = min(g, limit_to) - bracket.limit_from
                if taxable > 0:
                    tax_monthly += taxable * (bracket.rate / 100)

        return tax_monthly / periods_per_month if periods_per_month else 0.0

