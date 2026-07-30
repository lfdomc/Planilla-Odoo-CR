import logging
import datetime
from odoo import models, fields, api
from .. import planilla_const as K
from odoo.exceptions import UserError, ValidationError
from ..closed_period import PlanillaClosedPeriod

_logger = logging.getLogger(__name__)

class PayslipSyncMixin(models.AbstractModel):
    """
    Mixin: sincronizacion de novedades con la boleta.
    _sync_recurring_benefits, _sync_loan_deductions, _sync_pension_alimentaria,
    _sync_novedades, _sync_ausencias, _sync_rop, _sync_embargos, _sync_bonos.
    """
    _name = 'planilla.payslip.sync.mixin'
    _description = 'Mixin Sync Novedades Boleta'

    def _sync_recurring_benefits(self) -> None:
        """Auto-apply active recurring benefits/deductions for the period.
        FIX C-08 v53: Si la linea ya existe y es de tipo porcentaje, recalcular
        el monto en base al gross_salary actual (puede haber cambiado por novedades).
        """
        for rec in self:
            if rec.state != 'draft':
                continue
            emp = rec.employee_id
            today = rec.date_from
            if not today:
                continue
            benefits = self.env['planilla.recurring.benefit'].search([
                ('employee_id', '=', emp.id),
                ('active', '=', True),
                '|', ('date_start', '=', False), ('date_start', '<=', today),
                '|', ('date_end', '=', False),   ('date_end', '>=', today),
            ])
            for ben in benefits:
                existing = rec.deduction_line_ids.filtered(
                    lambda l, b=ben: l.recurring_benefit_id.id == b.id
                )
                amt = ben.get_amount_for_salary(rec.gross_salary or 0.0)
                if existing:
                    # FIX C-08 v53: Actualizar monto si el beneficio es de porcentaje
                    # y el salario bruto cambio desde la ultima sincronizacion.
                    if ben.amount_type == 'percentage':
                        for line in existing:
                            if line.amount != amt:
                                line.amount = amt
                    continue
                rec.deduction_line_ids = [(0, 0, {
                    'deduction_code_id':    ben.deduction_code_id.id,
                    'description':          ben.name,
                    'line_type':            'income' if ben.benefit_type == 'income' else 'deduction',
                    'amount_type':          ben.amount_type,
                    'amount':               amt,
                    'percentage':           ben.percentage,
                    'recurring_benefit_id': ben.id,
                })]

    def _sync_loan_deductions(self) -> None:
        """Sincroniza cuotas de prestamos activos del empleado con las lineas de deduccion."""
        self.ensure_one()
        # Codigo de deduccion para prestamos
        loan_code = self.env['planilla.deduction.code'].search(
            [('code', '=', 'PRESTAMO')], limit=1
        )
        if not loan_code:
            return
        # Buscar prestamos activos o aprobados del empleado
        loans = self.env['planilla.employee.loan'].search([
            ('employee_id', '=', self.employee_id.id),
            ('state', 'in', ['approved', 'active']),
        ])
        for loan in loans:
            installment = loan.get_pending_installment(self.date_from, self.date_to)
            if not installment:
                continue
            # BUG FIX: verificar GLOBALMENTE (no solo en self.deduction_line_ids)
            # si esta cuota ya fue aplicada en cualquier boleta no cancelada --
            # evita que Q1 y Q2 del mismo mes, ambas en borrador antes de
            # confirmar cualquiera, dupliquen la misma cuota mensual.
            existing = self.env['planilla.payslip.deduction.line'].search([
                ('loan_installment_id', '=', installment.id),
                ('payslip_id.state', '!=', 'cancelled'),
            ], limit=1)
            if not existing:
                self.env['planilla.payslip.deduction.line'].create({
                    'payslip_id':          self.id,
                    'deduction_code_id':   loan_code.id,
                    'description':         loan.name,
                    'line_type':           'deduction',        # FIX-E9: faltaba
                    'deduction_category':  'loan',             # FIX-E9: faltaba -> salary_payable correcto
                    'amount':              installment.amount,
                    'loan_installment_id': installment.id,
                })


    def _sync_pension_alimentaria(self) -> None:
        """Sincroniza pensiones alimentarias activas del empleado como deducciones."""
        self.ensure_one()
        if self.state != 'draft':
            return

        # Codigo de deduccion para pensiones alimentarias
        pension_code = self.env['planilla.deduction.code'].search(
            [('code', '=', 'PENSION_ALIM')], limit=1
        )
        if not pension_code:
            # FIX v512 SEC-01: patron anti race-condition.
            # Con multiples workers de Odoo en paralelo, dos workers podrian ejecutar
            # el search anterior simultaneamente (ambos vacio) y crear registros duplicados.
            try:
                pension_code = self.env['planilla.deduction.code'].sudo().create({
                    'name': 'Pension Alimentaria',
                    'code': 'PENSION_ALIM',
                    'deduction_type': 'employee',
                })
            except Exception:
                pension_code = self.env['planilla.deduction.code'].search(
                    [('code', '=', 'PENSION_ALIM')], limit=1
                )

        # Buscar pensiones activas del empleado vigentes en el periodo
        pensiones = self.env['planilla.pension.alimentaria'].search([
            ('employee_id', '=', self.employee_id.id),
            ('state', '=', 'active'),
            ('active', '=', True),
            ('date_start', '<=', self.date_to),
            '|',
            ('date_end', '=', False),
            ('date_end', '>=', self.date_from),
        ])

        for pension in pensiones:
            # Verificar si ya esta aplicada en la boleta actual
            existing = self.deduction_line_ids.filtered(
                lambda l: l.deduction_category == 'pension_alimentaria'
                and l.numero_resolucion == pension.numero_expediente
            )
            if existing:
                continue
            # FIX DEDUP: verificar también en otras boletas en borrador para
            # evitar duplicación al resetear y re-sincronizar
            applied_elsewhere = self.env['planilla.payslip.deduction.line'].search([
                ('deduction_category', '=', 'pension_alimentaria'),
                ('numero_resolucion', '=', pension.numero_expediente),
                ('payslip_id', '!=', self.id),
                ('payslip_id.state', 'in', ('draft', 'confirmed', 'done')),
                ('payslip_id.employee_id', '=', self.employee_id.id),
                ('payslip_id.date_from', '>=', self.date_from),
            ], limit=1)
            if applied_elsewhere:
                continue

            monto = pension.compute_amount(self.gross_salary or 0.0)

            self.env['planilla.payslip.deduction.line'].create({
                'payslip_id':         self.id,
                'deduction_code_id':  pension_code.id,
                'description':        f'Pension Alimentaria -- {pension.beneficiario_nombre} ({pension.numero_expediente})',
                'line_type':          'deduction',
                'deduction_category': 'pension_alimentaria',
                'amount_type':        pension.calculation_type,
                'amount':             monto,
                'percentage':         pension.percentage if pension.calculation_type == 'percentage' else 0.0,
                'numero_resolucion':  pension.numero_expediente,
            })

    def _sync_novedades(self) -> None:
        """
        Vincula automaticamente a la boleta las horas extras, incapacidades
        y vacaciones del empleado que corresponden al periodo de la boleta
        y que aun no tienen boleta asignada.
        Reglas:
          - Horas extras:    state == 'approved',  fecha dentro del periodo
          - Incapacidades:   state in ('confirmed','paid'), solapa con el periodo
          - Vacaciones:      state in ('approved','paid'), solapa con el periodo
        """
        self.ensure_one()
        if not self.employee_id or not self.date_from or not self.date_to:
            return

        emp_id    = self.employee_id.id
        date_from = self.date_from
        date_to   = self.date_to

        # -- Horas Extras ----------------------------------------------------
        overtimes = self.env['planilla.overtime'].search([
            ('employee_id', '=', emp_id),
            ('state', '=', 'approved'),
            ('date', '>=', date_from),
            ('date', '<=', date_to),
            '|', ('payslip_id', '=', False), ('payslip_id', '=', self.id),
        ])
        overtimes.write({'payslip_id': self.id})

        # -- Incapacidades ----------------------------------------------------
        # FIX MULTI-PERIODO: una incapacidad (ej. maternidad) puede cruzar
        # varios periodos de pago. Se usa Many2many (payslip_ids) para que
        # cada boleta que solape con la incapacidad la incluya correctamente.
        # La restriccion '|payslip_id=False' se elimina: buscamos POR FECHA,
        # y la relacion M2M permite vinculos multiples sin sobrescribir.
        disabilities = self.env['planilla.disability'].search([
            ('employee_id', '=', emp_id),
            ('state', 'in', ('confirmed', 'paid')),
            ('date_start', '<=', date_to),
            ('date_end',   '>=', date_from),
        ])
        # Agregar esta boleta a payslip_ids (no sobrescribe, acumula)
        if disabilities:
            disabilities.write({'payslip_ids': [(4, self.id)]})

        # -- Vacaciones --------------------------------------------------------
        vacations = self.env['planilla.vacation.payment'].search([
            ('employee_id', '=', emp_id),
            ('state', 'in', ('approved', 'paid')),
            ('date_start', '<=', date_to),
            ('date_end',   '>=', date_from),
            '|', ('payslip_id', '=', False), ('payslip_id', '=', self.id),
        ])
        vacations.write({'payslip_id': self.id})

        # -- HE automaticas desde asistencias -------------------------------
        # NOTA (auditoria): la llamada a _sync_overtime_from_attendance()
        # que vivia aqui fue removida. Ese metodo duplicaba la deteccion
        # que ya hace planilla.payslip.auto.overtime.mixin._auto_detect_overtime(),
        # que corre ANTES en el mismo flujo de creacion de boleta
        # (ver payslip_action_mixin.create()). Ambos analizaban la misma
        # ventana de hr.attendance de forma independiente, lo que producia:
        #   - HE duplicada (pago doble) cuando ambos clasificaban el
        #     excedente con overtime_type distinto para el mismo dia.
        #   - Error de integridad (UNIQUE employee_id+date+overtime_type)
        #     cuando ambos coincidian en el tipo, abortando la creacion
        #     de la boleta completa.
        #   - El mecanismo viejo ademas ignoraba el toggle
        #     enable_auto_overtime de Configuracion Contable, generando
        #     HE automatica incluso con esa opcion desactivada -- lo cual
        #     contradice el comportamiento documentado ("OFF = HE siempre
        #     manuales").
        # El metodo _sync_overtime_from_attendance() se conserva en este
        # archivo (mas abajo) por compatibilidad con codigo o tests que
        # lo invoquen directamente, pero ya no se llama desde el flujo
        # normal de sincronizacion.

        # -- Pensiones Alimentarias -----------------------------------------
        self._sync_pension_alimentaria()

        # -- Licencias Especiales CR (duelo, paternidad, matrimonio, etc.) -
        self._sync_licencias()

        # -- Ausencias aprobadas (hr_holidays) -----------------------------
        self._sync_ausencias()


    def _sync_overtime_from_attendance(self) -> None:
        """
        Genera automaticamente registros de planilla.overtime en estado 'draft'
        basados en las marcas de hr.attendance para empleados con metodo
        payroll_calculation_method = 'attendance'.

        Logica (Art. 139 CT):
          1. Por cada dia del periodo, suma horas trabajadas desde hr.attendance.
          2. Compara contra la jornada ordinaria diaria del empleado.
          3. El excedente se clasifica:
               - 'holiday' : feriado de pago obligatorio (Art. 148 CT)
               - 'double'  : domingo o HE nocturnas (> jornada mixta/nocturna)
               - 'simple'  : cualquier otro excedente diurno
          4. Crea planilla.overtime en draft con source='attendance'.
          5. No duplica: si ya existe un registro auto-generado para ese
             dia+tipo, lo actualiza. Si el usuario lo modifico manualmente
             (source='manual'), no lo toca.

        Se llama desde _sync_novedades() solo si metodo == 'attendance'.
        El aprobador debe revisar y aprobar los registros antes de confirmar
        la boleta (igual que las HE manuales).
        """
        self.ensure_one()
        emp = self.employee_id
        if not emp or not self.date_from or not self.date_to:
            return
        if (emp.payroll_calculation_method or 'fixed') != 'attendance':
            return

        import datetime as dt

        # -- Jornada ordinaria del empleado -----------------------------------
        schedule = emp.schedule_type_id
        hours_per_day = schedule.hours_per_day if schedule else 8.0
        # Jornada: diurna=8h, mixta=7h, nocturna=6h. El excedente es HE.
        # Usamos hours_per_day del tipo de horario como umbral de HE simple.
        # Horas sobre el doble de la jornada = HE doble (improbable pero posible).

        # -- Feriados del periodo ---------------------------------------------
        PublicHoliday = self.env['planilla.public.holiday']
        paid_holidays = PublicHoliday.get_paid_holidays_in_range(
            self.date_from, self.date_to,
            company_id=emp.company_id.id if emp.company_id else None,
        )

        # -- Leer asistencias del periodo dia a dia --------------------------
        # Timezone CR = UTC-6. Ampliar ventana para capturar turnos nocturnos.
        tz_offset = dt.timedelta(hours=6)
        dt_from = dt.datetime.combine(self.date_from, dt.time.min) - tz_offset
        dt_to   = dt.datetime.combine(self.date_to,   dt.time.max) + tz_offset

        all_att = self.env['hr.attendance'].search([
            ('employee_id', '=', emp.id),
            ('check_in',    '>=', dt_from),
            ('check_in',    '<=', dt_to),
            ('check_out',   '!=', False),   # solo registros completos
        ])

        # Agrupar horas por fecha local (CR = UTC-6)
        hours_by_day = {}
        for att in all_att:
            local_date = (att.check_in - tz_offset).date()
            if local_date < self.date_from or local_date > self.date_to:
                continue
            hours_by_day.setdefault(local_date, 0.0)
            hours_by_day[local_date] += att.worked_hours

        if not hours_by_day:
            return

        Overtime = self.env['planilla.overtime']

        for work_date, total_hours in hours_by_day.items():
            extra_hours = round(total_hours - hours_per_day, 2)
            if extra_hours <= 0.05:   # tolerancia de 3 minutos
                continue

            # Clasificar tipo de HE
            is_sunday  = work_date.weekday() == 6
            is_holiday = work_date in paid_holidays

            if is_holiday:
                ot_type = 'holiday'
            elif is_sunday:
                ot_type = 'double'
            else:
                ot_type = 'simple'

            # Buscar registro existente AUTO-generado para no duplicar
            existing = Overtime.search([
                ('employee_id', '=', emp.id),
                ('date',        '=', work_date),
                ('overtime_type', '=', ot_type),
                ('source',      '=', 'attendance'),
            ], limit=1)

            note_text = (
                f'Generado automaticamente desde asistencias. '
                f'Jornada ordinaria: {hours_per_day}h. '
                f'Horas registradas: {round(total_hours, 2)}h. '
                f'Excedente: {extra_hours}h.'
            )

            if existing:
                # Actualizar si cambiaron las horas (ej: el empleado corrigio su marca)
                if existing.state == 'draft' and abs(existing.hours - extra_hours) > 0.01:
                    existing.write({'hours': extra_hours, 'note': note_text})
            else:
                Overtime.create({
                    'employee_id':   emp.id,
                    'date':          work_date,
                    'hours':         extra_hours,
                    'overtime_type': ot_type,
                    'source':        'attendance',
                    'state':         'draft',
                    'payslip_id':    self.id,
                    'note':          note_text,
                })

    def _sync_licencias(self) -> None:
        """
        Sincroniza licencias especiales CR (planilla.leave.cr) con la boleta.

        Licencias CON goce (duelo 1er grado, paternidad, matrimonio, adopcion, etc.):
          -> Se registran como INGRESO adicional (line_type='income') en la boleta.
          -> No reducen el salario base; son gasto patronal adicional.
          -> La boleta refleja el monto pagado y el asiento contable lo debita en 630800.

        Licencias SIN goce (permiso sin goce, duelo 2do grado sin override, etc.):
          -> Se registran como DEDUCCION (deduction_category='licencia_sin_goce').
          -> Reducen el neto a pagar al empleado.
          -> Siguen la misma logica que ausencias: salario_diario x dias ausentes.

        Se vincula la licencia a la boleta (payslip_id) para trazabilidad.
        Evita duplicados verificando si ya existe una linea con leave_cr_id.
        """
        self.ensure_one()
        if self.state != 'draft':
            return
        if not self.employee_id or not self.date_from or not self.date_to:
            return

        # GUARD: si ya es proporcional por fecha de salida, no agregar licencias sin goce
        _emp_lic = self.employee_id
        if self.is_proportional and _emp_lic.exit_date:
            from odoo.fields import Date as _Date
            _exit_d = _emp_lic.exit_date if hasattr(_emp_lic.exit_date, 'year') else _Date.from_string(_emp_lic.exit_date)
            _per_end = self.date_to if hasattr(self.date_to, 'year') else _Date.from_string(self.date_to)
            _per_start = self.date_from if hasattr(self.date_from, 'year') else _Date.from_string(self.date_from)
            if _per_start <= _exit_d <= _per_end:
                # La proporcionalidad ya descuenta los días no laborados
                return

        # -- Codigos de deduccion ----------------------------------------------
        def _get_or_create_code(code, name, ded_type):
            dc = self.env['planilla.deduction.code'].search([('code', '=', code)], limit=1)
            if not dc:
                try:
                    dc = self.env['planilla.deduction.code'].sudo().create({
                        'code': code, 'name': name, 'deduction_type': ded_type,
                    })
                except Exception:
                    dc = self.env['planilla.deduction.code'].search([('code', '=', code)], limit=1)
            return dc

        code_con_goce  = _get_or_create_code('LIC-GOCE',  'Licencia con Goce de Sueldo', 'employer')
        code_sin_goce  = _get_or_create_code('LIC-SGOCE', 'Licencia Sin Goce de Sueldo', 'employee')

        # -- Buscar licencias aprobadas del periodo ----------------------------
        # FIX MULTI-PERIODO: se eliminan restricciones de payslip_id para
        # soportar licencias que cruzan varios periodos (adopcion 90d,
        # paternidad ~12 dias cal, sin goce de duracion libre).
        # Busqueda unificada por rango de fechas:
        licencias = self.env['planilla.leave.cr'].search([
            ('employee_id', '=', self.employee_id.id),
            ('company_id',  '=', self.company_id.id),
            ('state', '=', 'approved'),
            ('date_start', '<=', self.date_to),
            ('date_end',   '>=', self.date_from),
        ])

        import datetime as _dt

        for lic in licencias:
            # -- Calcular overlap de fechas entre licencia y periodo -----------
            overlap_start = max(self.date_from, lic.date_start)
            overlap_end   = min(self.date_to,   lic.date_end)
            if overlap_end < overlap_start:
                continue
            overlap_days = (overlap_end - overlap_start).days + 1

            # -- Monto proporcional al periodo ---------------------------------
            # Para licencias por horas: monto unico, no hay distribucion
            # Para licencias por dias: distribuir diariamente
            if lic.leave_unit == 'hour':
                # Horas: un solo dia, no cruza periodos -> monto total
                monto = lic.leave_amount or 0.0
                periodo_desc = f'{lic.hours}h el {lic.date_start}'
            else:
                # Dias: calcular monto proporcional a los dias de este periodo
                total_days = max(lic.days or 1, 1)
                # daily_rate basado en leave_amount total / dias calendario totales
                daily_rate = (lic.leave_amount or 0.0) / total_days
                # FIX DIAS-16: usar freq_factor si la licencia cubre todo el periodo
                dias_periodo_local = (self.date_to - self.date_from).days + 1 if (self.date_from and self.date_to) else 15
                if overlap_days >= dias_periodo_local:
                    from ..planilla_const import FREQ_FACTORS as _FF, DIAS_MES as _DM
                    _ff = _FF.get(self._get_effective_freq() if hasattr(self, '_get_effective_freq') else 'biweekly', 0.5)
                    monto = round(daily_rate * _DM * _ff, 2)
                else:
                    monto = round(daily_rate * overlap_days, 2)
                periodo_desc = (
                    f'{overlap_start} al {overlap_end}, {overlap_days} dia(s)'
                    if overlap_days < total_days
                    else f'{lic.date_start} al {lic.date_end}, {total_days} dia(s)'
                )

            if monto <= 0:
                continue

            pays = lic.has_salary or lic.has_salary_override
            tipo_label = dict(lic._fields['leave_type'].selection).get(lic.leave_type, lic.leave_type)

            # -- Evitar duplicados: verificar si ya existe linea para este
            #    periodo especifico (comparando fecha de overlap)
            existing = self.deduction_line_ids.filtered(
                lambda l, lid=lic.id: l.leave_cr_id and l.leave_cr_id.id == lid
            )
            if existing:
                continue

            if pays:
                # Licencia CON goce -> ingreso adicional (gasto patronal)
                self.env['planilla.payslip.deduction.line'].create({
                    'payslip_id':          self.id,
                    'deduction_code_id':   code_con_goce.id,
                    'description':         (
                        f'[{lic.code}] {tipo_label} ({periodo_desc})'
                        if lic.code else f'Licencia: {tipo_label} ({periodo_desc})'
                    ),
                    'line_type':           'income',
                    'deduction_category':  'licencia_con_goce',
                    'amount':              monto,
                    'leave_cr_id':         lic.id,
                })
            else:
                # Licencia SIN goce -> deduccion al empleado
                self.env['planilla.payslip.deduction.line'].create({
                    'payslip_id':          self.id,
                    'deduction_code_id':   code_sin_goce.id,
                    'description':         (
                        f'[{lic.code}] Sin goce: {tipo_label} ({periodo_desc})'
                        if lic.code else f'Licencia sin goce: {tipo_label} ({periodo_desc})'
                    ),
                    'line_type':           'deduction',
                    'deduction_category':  'licencia_sin_goce',
                    'amount':              monto,
                    'leave_cr_id':         lic.id,
                })

            # Vincular licencia a esta boleta via M2M (sin sobrescribir)
            lic.write({'payslip_ids': [(4, self.id)]})

    def _sync_ausencias(self) -> None:
        """
        H2 FIX -- Integracion hr_holidays con planilla.
        Busca ausencias aprobadas (hr.leave en estado validate) del empleado
        en el periodo de la boleta y crea deducciones automaticas por los
        dias sin goce de sueldo.

        Logica:
          - Solo aplica a ausencias SIN pago (unpaid leave) o cuyo tipo
            tenga work_time_rate = 0 (ausencia injustificada / sin goce).
          - Las ausencias CON pago (vacaciones anuales, maternidad, etc.)
            NO se descuentan aqui: ya estan gestionadas por sus propios modelos.
          - El monto diario = salario bruto / dias del periodo.
          - Se crea UNA linea de deduccion por leave_id para evitar duplicados.
        """
        self.ensure_one()
        if self.state != 'draft':
            return
        if not self.employee_id or not self.date_from or not self.date_to:
            return

        # GUARD: si la boleta ya es proporcional por fecha de salida del empleado,
        # no agregar ausencias para los días no trabajados — sería doble descuento.
        emp = self.employee_id
        if self.is_proportional and emp.exit_date:
            from odoo.fields import Date
            exit_d = emp.exit_date if hasattr(emp.exit_date, 'year') else Date.from_string(emp.exit_date)
            period_end = self.date_to if hasattr(self.date_to, 'year') else Date.from_string(self.date_to)
            period_start = self.date_from if hasattr(self.date_from, 'year') else Date.from_string(self.date_from)
            if period_start <= exit_d <= period_end:
                # La proporcionalidad ya descuenta los días no trabajados → skip
                return

        # Codigo de deduccion para ausencias
        absence_code = self.env['planilla.deduction.code'].search(
            [('code', '=', 'AUSENCIA')], limit=1
        )
        if not absence_code:
            # FIX v512 SEC-01: patron anti race-condition
            try:
                absence_code = self.env['planilla.deduction.code'].sudo().create({
                    'name': 'Ausencia Sin Goce de Sueldo',
                    'code': 'AUSENCIA',
                    'deduction_type': 'employee',
                })
            except Exception:
                absence_code = self.env['planilla.deduction.code'].search(
                    [('code', '=', 'AUSENCIA')], limit=1
                )

        # Buscar ausencias aprobadas del empleado que solapan con el periodo
        leaves = self.env['hr.leave'].search([
            ('employee_id', '=', self.employee_id.id),
            ('state', '=', 'validate'),
            ('date_from', '<=', fields.Datetime.to_datetime(self.date_to)),
            ('date_to',   '>=', fields.Datetime.to_datetime(self.date_from)),
        ])

        for leave in leaves:
            # FIX v512 BP-03: eliminado hasattr() anti-patron.
            # Este modulo es Odoo 19 exclusivo. En Odoo 19 hr.holiday.status
            # expone 'unpaid' (boolean) de forma estable desde la version 17+.
            # Se usa directamente sin deteccion dinamica de version.
            holiday_type = leave.holiday_status_id
            is_unpaid = bool(getattr(holiday_type, 'unpaid', False))

            # Fallback semantico si unpaid no existe (instalacion no estandar)
            if not is_unpaid and hasattr(holiday_type, 'work_time_rate'):
                is_unpaid = (holiday_type.work_time_rate == 0)
            elif not is_unpaid:
                name_lower = (holiday_type.name or '').lower()
                is_unpaid = any(k in name_lower for k in (
                    'sin goce', 'injustificad', 'unpaid', 'sin remuner', 'no remuner'
                ))

            # Si la ausencia ES pagada (maternidad, vacaciones anuales, etc.) -> omitir.
            # Esas ausencias ya estan gestionadas por sus propios modelos (disability, vacation).
            if not is_unpaid:
                continue

            # FIX v56: Validacion cruzada hr_holidays vs planilla.vacation.payment
            # Si ya existe un registro de planilla.vacation.payment para el mismo
            # empleado y periodo que solapa con esta ausencia, NO crear deduccion
            # para evitar doble descuento en el saldo de vacaciones.
            if is_unpaid:
                vac_overlap = self.env['planilla.vacation.payment'].search_count([
                    ('employee_id', '=', self.employee_id.id),
                    ('state', 'in', ('approved', 'paid')),
                    ('date_start', '<=', (leave.date_to.date() if leave.date_to else self.date_to)),
                    ('date_end',   '>=', (leave.date_from.date() if leave.date_from else self.date_from)),
                ])
                if vac_overlap:
                    _logger.info(
                        'planilla_cr._sync_ausencias: ausencia %s omitida -- ya existe '
                        'planilla.vacation.payment solapante para %s',
                        leave.id, self.employee_id.name
                    )
                    continue

            # Evitar duplicados: verificar si ya existe linea para este leave
            existing = self.deduction_line_ids.filtered(
                lambda l: l.hr_leave_id == leave
            )
            if existing:
                continue

            # FIX C-05 v53: Usar number_of_days de hr.leave cuando esta disponible,
            # ya que Odoo lo calcula correctamente incluyendo medias jornadas (0.5).
            # El calculo manual por fechas siempre redondea hacia arriba y no maneja
            # ausencias de medio dia (request_date_from_period = 'am'/'pm').
            leave_start = leave.date_from.date() if leave.date_from else self.date_from
            leave_end   = leave.date_to.date()   if leave.date_to   else self.date_to
            effective_start = max(leave_start, self.date_from)
            effective_end   = min(leave_end,   self.date_to)

            if effective_end < effective_start:
                continue

            # Si la ausencia esta completamente dentro del periodo, usar number_of_days
            if leave_start >= self.date_from and leave_end <= self.date_to:
                days_absent = getattr(leave, 'number_of_days', None)
                if not days_absent or days_absent <= 0:
                    days_absent = (effective_end - effective_start).days + 1
            else:
                # Ausencia parcialmente fuera del periodo -> calcular interseccion en dias
                days_absent = (effective_end - effective_start).days + 1

            if days_absent <= 0:
                continue

            # Monto: salario_diario x dias ausentes
            # FIX DIAS-16: usar monthly/DIAS_MES como diario (no base_salary/days_in_period).
            # base_salary = monthly x freq_factor (siempre igual, 15 o 16 dias).
            # Si days_in_period=16, diario = 205,000/16 = 12,812.50 MENOR que el correcto
            # 205,000/15 = 13,666.67, dando un descuento incorrecto por la misma ausencia
            # dependiendo del mes. La referencia correcta es siempre monthly/30.
            emp_monthly = (
                self.employee_id.base_salary
                if self.employee_id and self.employee_id.base_salary
                else (self.base_salary or 0.0)
            )
            from ..planilla_const import DIAS_MES as _DIAS_MES
            salary_daily = round(emp_monthly / _DIAS_MES, 4)
            amount = round(salary_daily * days_absent, 2)
            if amount <= 0:
                continue

            self.env['planilla.payslip.deduction.line'].create({
                'payslip_id':          self.id,
                'deduction_code_id':   absence_code.id,
                'description':         (
                    f'Ausencia sin goce -- {leave.holiday_status_id.name} '
                    f'({effective_start} al {effective_end}, {days_absent} dia(s))'
                ),
                'amount':              amount,
                'deduction_category':  'ausencia',
                'hr_leave_id':         leave.id,
            })


    def _sync_rop(self) -> None:
        """
        Sincroniza la deduccion de ROP (Regimen Obligatorio de Pensiones, Ley 7983)
        en la boleta del empleado.

        - ROP Obrero:   K.ROP_EMP (1.0%) del salario bruto -- deduccion al empleado
        - ROP Patronal: K.ROP_PAT (3.25%) del salario bruto -- costo adicional del patrono

        OPT-IN: Solo aplica si el empleado tiene rop_applies=True.
        El campo esta DESACTIVADO por defecto porque muchos contadores en CR
        manejan el ROP con su propio proceso externo (planilla complementaria,
        plataforma del operador, etc.). Activarlo por empleado segun confirme
        el contador.
        Si no hay codigo ROP configurado en BD, usa K.ROP_EMP/K.ROP_PAT.

        Evita duplicados: si ya existe una linea de deduccion con deduction_category='rop',
        actualiza el monto en lugar de crear una nueva.
        """
        self.ensure_one()
        if self.state != 'draft':
            return

        emp = self.employee_id
        if not emp or not getattr(emp, 'rop_applies', False):
            return

        g = self.gross_salary or 0.0
        if g <= 0:
            return

        # Tasas desde BD (configurable) con fallback a constantes
        rh = self.env['planilla.rate.helper'].with_company(self.company_id)
        rop_emp_dc = rh._get_deduction_code('ROP_EMP')
        rop_pat_dc = rh._get_deduction_code('ROP_PAT')
        rop_emp_rate = (rop_emp_dc.employee_percentage / 100) if rop_emp_dc else K.ROP_EMP
        rop_pat_rate = (rop_pat_dc.employer_percentage / 100) if rop_pat_dc else K.ROP_PAT

        monto_emp = round(g * rop_emp_rate, 2)
        monto_pat = round(g * rop_pat_rate, 2)

        # Codigo de deduccion ROP
        rop_code = self.env['planilla.deduction.code'].search(
            [('code', '=', 'ROP')], limit=1
        )
        if not rop_code:
            # FIX v512 SEC-01: patron anti race-condition
            try:
                rop_code = self.env['planilla.deduction.code'].sudo().create({
                    'code': 'ROP',
                    'name': 'ROP -- Regimen Obligatorio de Pensiones (Ley 7983)',
                    'deduction_type': 'employee',
                    'calculation_type': 'percentage',
                    'description': 'ROP obrero 1% + patronal 3.25% (Ley 7983 Art. 6)',
                })
            except Exception:
                rop_code = self.env['planilla.deduction.code'].search(
                    [('code', '=', 'ROP')], limit=1
                )

        # Deduccion obrera: actualiza si existe, crea si no
        existing_emp = self.deduction_line_ids.filtered(
            lambda l: l.deduction_category == 'rop' and l.line_type == 'deduction'
        )
        if existing_emp:
            if existing_emp[0].amount != monto_emp:
                existing_emp[0].amount = monto_emp
        else:
            self.env['planilla.payslip.deduction.line'].create({
                'payslip_id':         self.id,
                'deduction_code_id':  rop_code.id,
                'description':        f'ROP Obrero {rop_emp_rate*100:.1f}% -- Ley 7983',
                'line_type':          'deduction',
                'deduction_category': 'rop',
                'amount_type':        'percentage',
                'percentage':         rop_emp_rate * 100,
                'amount':             monto_emp,
            })

        # Registrar costo patronal ROP en el campo rop_employer
        # para que aparezca en total_employer_cost y en el asiento contable
        self.rop_employer = monto_pat

        _logger.info(
            'planilla_cr._sync_rop: ROP obrero CRC%.2f + patronal CRC%.2f para %s (boleta %s)',
            monto_emp, monto_pat, emp.name, self.name
        )

    def _sync_rebajo_renta(self) -> None:
        """Sincroniza el rebajo consolidado de renta activo del empleado."""
        self.ensure_one()
        if self.state != 'draft':
            return
        if not self.employee_id or not self.date_from or not self.date_to:
            return
        rebajos = self.env['planilla.rebajo.renta'].search([
            ('employee_id', '=', self.employee_id.id),
            ('date_from', '<=', self.date_to),
            '|',
            ('date_to', '=', False),
            ('date_to', '>=', self.date_from),
        ])
        freq = self._get_effective_freq()
        total = sum(reb.get_amount_for_period(freq) for reb in rebajos)
        self.rebajo_renta_amount = round(total, 2)

    def _sync_embargos(self) -> None:
        """
        Sincroniza embargos judiciales activos del empleado con las lineas de deduccion.
        Art. 172 CT: maximo 25 % del neto disponible (despues de CCSS, renta y pensiones).
        Prioridad: pension alimentaria -> embargo -> prestamos.
        """
        self.ensure_one()
        if self.state != 'draft':
            return

        embargo_code = self.env['planilla.deduction.code'].search(
            [('code', '=', 'EMB')], limit=1
        )
        if not embargo_code:
            # FIX v512 SEC-01: patron anti race-condition
            try:
                embargo_code = self.env['planilla.deduction.code'].sudo().create({
                    'code': 'EMB',
                    'name': 'Embargo Judicial',
                    'deduction_type': 'employee',
                    'calculation_type': 'fixed',
                    'description': 'Embargo judicial -- maximo 25% salario neto Art. 172 CT',
                })
            except Exception:
                embargo_code = self.env['planilla.deduction.code'].search(
                    [('code', '=', 'EMB')], limit=1
                )

        embargos = self.env['planilla.embargo'].search([
            ('employee_id', '=', self.employee_id.id),
            ('state', '=', 'active'),
            ('date_start', '<=', self.date_to),
            '|',
            ('date_end', '=', False),
            ('date_end', '>=', self.date_from),
        ])

        # FIX B-08 v58: Incluir ausencias_sin_goce en el neto disponible para embargo.
        # Art. 172 CT establece el tope sobre el salario neto real del empleado.
        # Si hay descuentos por ausencias, estos reducen la base antes del 25%.
        gross     = self.gross_salary or 0.0
        ccss_emp  = self.ccss_employee or 0.0
        renta     = self.income_tax or 0.0
        pensiones = sum(
            l.amount for l in self.deduction_line_ids
            if l.deduction_category == 'pension_alimentaria'
        )
        ausencias_sg = sum(
            l.amount for l in self.deduction_line_ids
            if l.deduction_category == 'ausencia'
        )
        licencias_sg = sum(
            l.amount for l in self.deduction_line_ids
            if l.deduction_category == 'licencia_sin_goce'
        )
        # Art. 172 CT: el neto disponible se calcula descontando CCSS, renta,
        # pensiones alimentarias, ausencias sin goce Y licencias sin goce,
        # antes de aplicar el tope del 25% de embargo.
        neto_disponible = max(0.0, gross - ccss_emp - renta
                              - (self.rebajo_renta_amount or 0.0)
                              - pensiones - ausencias_sg - licencias_sg)
        limite_total    = round(neto_disponible * K.MAX_PCT_EMBARGO / 100, 2)
        ya_embargado    = sum(
            l.amount for l in self.deduction_line_ids
            if l.deduction_category == 'embargo'
        )

        for embargo in embargos:
            existing = self.deduction_line_ids.filtered(
                lambda l, e=embargo: l.deduction_category == 'embargo'
                and (l.embargo_id and l.embargo_id.id == e.id
                     or l.numero_resolucion == e.numero_expediente)
            )
            if existing:
                continue

            monto = embargo.compute_amount(neto_disponible)
            # Respetar el limite global del 25 %
            espacio = max(0.0, limite_total - ya_embargado)
            monto   = min(monto, espacio)
            if monto <= 0:
                continue

            desc_emb = (
                f'[{embargo.code}] {embargo.beneficiario_nombre} ({embargo.numero_expediente})'
                if embargo.code
                else f'Embargo Judicial -- {embargo.beneficiario_nombre} ({embargo.numero_expediente})'
            )
            self.env['planilla.payslip.deduction.line'].create({
                'payslip_id':         self.id,
                'deduction_code_id':  embargo_code.id,
                'description':        desc_emb,
                'line_type':          'deduction',
                'deduction_category': 'embargo',
                'amount_type':        embargo.calculation_type,
                'amount':             monto,
                'percentage':         embargo.percentage if embargo.calculation_type == 'percentage' else 0.0,
                'numero_resolucion':  embargo.numero_expediente,
                'embargo_id':         embargo.id,
            })
            ya_embargado += monto
            _logger.info(
                'planilla_cr._sync_embargos: aplicado embargo %s (CRC%.2f) a boleta %s',
                embargo.numero_expediente, monto, self.name
            )

    def _sync_bonos(self) -> None:
        """
        Sincroniza bonos activos del empleado con las lineas de ingreso de la boleta.
        Respeta las reglas fiscales CR: flags afecto_ccss / afecto_renta por tipo.
        """
        self.ensure_one()
        if self.state != 'draft':
            return

        bono_code = self.env['planilla.deduction.code'].search(
            [('code', '=', 'BONO')], limit=1
        )
        if not bono_code:
            bono_code = self.env['planilla.deduction.code'].create({
                'code': 'BONO',
                'name': 'Bono / Incentivo',
                'deduction_type': 'employee',
                'calculation_type': 'fixed',
                'description': 'Bonos e incentivos por empleado',
            })

        bonos = self.env['planilla.bono'].search([  # FIX v512 BP-01: eliminada var 'today' sin usar
            ('employee_id', '=', self.employee_id.id),
            ('state', '=', 'active'),
            ('date_start', '<=', self.date_to),
            '|',
            ('date_end', '=', False),
            ('date_end', '>=', self.date_from),
        ])

        for bono in bonos:
            # Deduplicacion por bono_id (ID unico)
            existing = self.deduction_line_ids.filtered(
                lambda l, b=bono: l.bono_id and l.bono_id.id == b.id
            )
            # Fallback: buscar por nombre del bono en la descripcion (con o sin prefijo)
            if not existing:
                existing = self.deduction_line_ids.filtered(
                    lambda l, b=bono: l.line_type == 'income'
                    and l.deduction_category == 'bonus'
                    and b.name in (l.description or '')
                )
                # Migrar: asignar bono_id y actualizar descripcion
                if existing:
                    new_desc = f'[{bono.code}] {bono.name}' if bono.code else f'Bono: {bono.name}'
                    existing.write({'bono_id': bono.id, 'description': new_desc})
            if existing:
                # Recalcular monto si el bono cambio
                if bono.amount_type == 'percentage':
                    base_ref = self.employee_id.base_salary or 0.0
                    monto_actual = round(base_ref * bono.percentage / 100.0, 2)
                else:
                    monto_actual = bono.amount
                for line in existing:
                    if line.amount != monto_actual:
                        line.amount = monto_actual
                continue

            # Para el calculo inicial tambien usamos base_salary del empleado
            if bono.amount_type == 'fixed':
                monto = bono.amount
            else:
                base_ref = self.employee_id.base_salary or 0.0
                monto = round(base_ref * bono.percentage / 100.0, 2)
            if monto <= 0:
                continue

            _logger.info(
                'planilla_cr._sync_bonos: aplicando bono "%s" (CRC%.2f) a boleta %s',
                bono.name, monto, self.name
            )
            self.env['planilla.payslip.deduction.line'].create({
                'payslip_id':         self.id,
                'deduction_code_id':  bono_code.id,
                'description':        f'[{bono.code}] {bono.name}' if bono.code else f'Bono: {bono.name}',
                'line_type':          'income',
                'deduction_category': 'bonus',
                'amount_type':        bono.amount_type,
                'amount':             monto,
                'percentage':         bono.percentage if bono.amount_type == 'percentage' else 0.0,
                'is_recurring_bono':  bono.is_recurring,
                'bono_id':            bono.id,
            })

    # ======================================================================
    # METODOS DE SYNC POR LOTE (BATCH)
    # FIX PERF-05: Para planillas grupales, pre-cargar TODOS los datos de
    # TODOS los empleados en UNA query y distribuir. Elimina el patron N+1
    # donde cada boleta hace sus propias busquedas independientes.
    #
    # Reduccion para 200 empleados:
    #   sync individual: ~200 x 8 = 1.600 queries
    #   sync batch:      ~8 queries (una por tipo de novedad)
    # ======================================================================

    def _sync_novedades_batch(self) -> None:
        """Version batch de _sync_novedades -- carga todas las novedades en queries minimas."""
        if not self:
            return
        # Todos los recordsets en self comparten el mismo date_from/date_to (planilla grupal)
        date_from = self[0].date_from
        date_to   = self[0].date_to
        emp_ids   = self.mapped('employee_id').ids
        # Indice: employee_id -> boleta
        slip_by_emp = {s.employee_id.id: s for s in self}

        # -- Horas extras -- UNA query para todos --------------------------
        overtimes = self.env['planilla.overtime'].search([
            ('employee_id', 'in', emp_ids),
            ('state', '=', 'approved'),
            ('date', '>=', date_from),
            ('date', '<=', date_to),
            '|', ('payslip_id', '=', False), ('payslip_id', 'in', self.ids),
        ])
        by_emp = {}
        for o in overtimes:
            by_emp.setdefault(o.employee_id.id, []).append(o)
        for emp_id, recs in by_emp.items():
            slip = slip_by_emp.get(emp_id)
            if slip:
                self.env['planilla.overtime'].browse([r.id for r in recs]).write(
                    {'payslip_id': slip.id}
                )

        # -- Incapacidades -- UNA query para todos (FIX MULTI-PERIODO M2M) --
        disabilities = self.env['planilla.disability'].search([
            ('employee_id', 'in', emp_ids),
            ('state', 'in', ('confirmed', 'paid')),
            ('date_start', '<=', date_to),
            ('date_end',   '>=', date_from),
        ])
        by_emp = {}
        for d in disabilities:
            by_emp.setdefault(d.employee_id.id, []).append(d)
        for emp_id, recs in by_emp.items():
            slip = slip_by_emp.get(emp_id)
            if slip:
                self.env['planilla.disability'].browse(
                    [r.id for r in recs]
                ).write({'payslip_ids': [(4, slip.id)]})

        # -- Vacaciones -- UNA query para todos ----------------------------
        vacations = self.env['planilla.vacation.payment'].search([
            ('employee_id', 'in', emp_ids),
            ('state', 'in', ('approved', 'paid')),
            ('date_start', '<=', date_to),
            ('date_end',   '>=', date_from),
            '|', ('payslip_id', '=', False), ('payslip_id', 'in', self.ids),
        ])
        by_emp = {}
        for v in vacations:
            by_emp.setdefault(v.employee_id.id, []).append(v)
        for emp_id, recs in by_emp.items():
            slip = slip_by_emp.get(emp_id)
            if slip:
                self.env['planilla.vacation.payment'].browse([r.id for r in recs]).write(
                    {'payslip_id': slip.id}
                )

        # -- Pensiones alimentarias -- sync individual (complejo, bajo volumen) --
        pension_code = self.env['planilla.deduction.code'].search(
            [('code', '=', 'PENSION_ALIM')], limit=1
        )
        if not pension_code:
            try:
                pension_code = self.env['planilla.deduction.code'].sudo().create({
                    'name': 'Pension Alimentaria', 'code': 'PENSION_ALIM',
                    'deduction_type': 'employee',
                })
            except Exception:
                pension_code = self.env['planilla.deduction.code'].search(
                    [('code', '=', 'PENSION_ALIM')], limit=1
                )

        # Pensiones: UNA query para todos
        pensiones = self.env['planilla.pension.alimentaria'].search([
            ('employee_id', 'in', emp_ids),
            ('state', '=', 'active'),
            ('active', '=', True),
            ('date_start', '<=', date_to),
            '|', ('date_end', '=', False), ('date_end', '>=', date_from),
        ])
        by_emp = {}
        for pen in pensiones:
            by_emp.setdefault(pen.employee_id.id, []).append(pen)

        for emp_id, pens in by_emp.items():
            slip = slip_by_emp.get(emp_id)
            if not slip or not pension_code:
                continue
            for pension in pens:
                existing = slip.deduction_line_ids.filtered(
                    lambda l: l.deduction_category == 'pension_alimentaria'
                    and l.numero_resolucion == pension.numero_expediente
                )
                if existing:
                    continue
                monto = pension.compute_amount(slip.gross_salary or 0.0)
                self.env['planilla.payslip.deduction.line'].create({
                    'payslip_id':         slip.id,
                    'deduction_code_id':  pension_code.id,
                    'description':        f'Pension Alimentaria -- {pension.beneficiario_nombre} ({pension.numero_expediente})',
                    'line_type':          'deduction',
                    'deduction_category': 'pension_alimentaria',
                    'amount_type':        pension.calculation_type,
                    'amount':             monto,
                    'percentage':         pension.percentage if pension.calculation_type == 'percentage' else 0.0,
                    'numero_resolucion':  pension.numero_expediente,
                })

        # -- Ausencias sin goce -- UNA query para todos -----------------
        absence_code = self.env['planilla.deduction.code'].search(
            [('code', '=', 'AUSENCIA')], limit=1
        )
        if not absence_code:
            try:
                absence_code = self.env['planilla.deduction.code'].sudo().create({
                    'name': 'Ausencia Sin Goce de Sueldo', 'code': 'AUSENCIA',
                    'deduction_type': 'employee',
                })
            except Exception:
                absence_code = self.env['planilla.deduction.code'].search(
                    [('code', '=', 'AUSENCIA')], limit=1
                )
        if absence_code:
            from odoo import fields as _fields
            leaves = self.env['hr.leave'].search([
                ('employee_id', 'in', emp_ids),
                ('state', '=', 'validate'),
                ('date_from', '<=', _fields.Datetime.to_datetime(date_to)),
                ('date_to',   '>=', _fields.Datetime.to_datetime(date_from)),
            ])
            by_emp_leaves = {}
            for lv in leaves:
                by_emp_leaves.setdefault(lv.employee_id.id, []).append(lv)
            for emp_id, lv_list in by_emp_leaves.items():
                slip = slip_by_emp.get(emp_id)
                if slip:
                    for leave in lv_list:
                        slip._sync_ausencias_single(leave, absence_code)

        # -- Licencias Especiales CR -- UNA query para todos ------------------
        self._sync_licencias_batch()

    def _sync_ausencias_single(self, leave, absence_code):
        """Sincroniza una ausencia individual en esta boleta (usado en batch mode)."""
        holiday_type = leave.holiday_status_id
        is_unpaid = bool(getattr(holiday_type, 'unpaid', False))
        if not is_unpaid and hasattr(holiday_type, 'work_time_rate'):
            is_unpaid = (holiday_type.work_time_rate == 0)
        elif not is_unpaid:
            name_lower = (holiday_type.name or '').lower()
            is_unpaid = any(k in name_lower for k in (
                'sin goce', 'injustificad', 'unpaid', 'sin remuner', 'no remuner'))
        if not is_unpaid:
            return
        existing = self.deduction_line_ids.filtered(lambda l: l.hr_leave_id == leave)
        if existing:
            return
        leave_start = leave.date_from.date() if leave.date_from else self.date_from
        leave_end   = leave.date_to.date()   if leave.date_to   else self.date_to
        effective_start = max(leave_start, self.date_from)
        effective_end   = min(leave_end,   self.date_to)
        if effective_end < effective_start:
            return
        if leave_start >= self.date_from and leave_end <= self.date_to:
            days_absent = getattr(leave, 'number_of_days', None) or (effective_end - effective_start).days + 1
        else:
            days_absent = (effective_end - effective_start).days + 1
        if days_absent <= 0:
            return
        # FIX DIAS-16: misma correccion que _sync_ausencias individual.
        # Usar monthly/DIAS_MES, no base_salary/days_in_period.
        emp_monthly2 = (
            self.employee_id.base_salary
            if self.employee_id and self.employee_id.base_salary
            else (self.base_salary or 0.0)
        )
        from ..planilla_const import DIAS_MES as _DIAS_MES2
        salary_daily = round(emp_monthly2 / _DIAS_MES2, 4)
        amount = round(salary_daily * days_absent, 2)
        if amount <= 0:
            return
        self.env['planilla.payslip.deduction.line'].create({
            'payslip_id':         self.id,
            'deduction_code_id':  absence_code.id,
            'description':        f'Ausencia sin goce -- {leave.holiday_status_id.name} ({effective_start} al {effective_end}, {days_absent} dia(s))',
            'amount':             amount,
            'deduction_category': 'ausencia',
            'hr_leave_id':        leave.id,
        })

    def _sync_licencias_batch(self) -> None:
        """
        PERF: version batch de _sync_licencias.
        Carga TODAS las licencias del periodo en 1 query y las distribuye
        a cada boleta, en lugar de 1 query por empleado.
        Para 200 empleados: 200 queries -> 1 query.
        """
        if not self:
            return
        # Guardia de seguridad: si los periodos difieren, volver al modo individual
        dates = {(s.date_from, s.date_to) for s in self}
        if len(dates) > 1:
            for slip in self:
                slip._sync_licencias()
            return

        date_from, date_to = next(iter(dates))
        emp_ids = self.mapped('employee_id').ids
        slip_by_emp = {s.employee_id.id: s for s in self}
        company_id = self[0].company_id.id  # FIX-AUD-10: filtro empresa batch

        # -- Codigos de deduccion (1 query cada uno) --------------------------
        def _get_or_create_code(code, name, ded_type):
            dc = self.env['planilla.deduction.code'].search([('code', '=', code)], limit=1)
            if not dc:
                try:
                    dc = self.env['planilla.deduction.code'].sudo().create({
                        'code': code, 'name': name, 'deduction_type': ded_type,
                    })
                except Exception:
                    dc = self.env['planilla.deduction.code'].search([('code', '=', code)], limit=1)
            return dc

        code_con_goce = _get_or_create_code('LIC-GOCE',  'Licencia con Goce de Sueldo', 'employer')
        code_sin_goce = _get_or_create_code('LIC-SGOCE', 'Licencia Sin Goce de Sueldo', 'employee')

        # -- 1 QUERY: todas las licencias aprobadas del periodo ---------------
        # FIX-AUD-10: filtro company_id para seguridad multi-empresa en modo batch
        # Licencias por HORAS: filtrar por date_start dentro del periodo
        licencias_horas = self.env['planilla.leave.cr'].search([
            ('employee_id', 'in', emp_ids),
            ('company_id',  '=', company_id),
            ('state', '=', 'approved'),
            ('leave_unit', '=', 'hour'),
            ('date_start', '<=', date_to),
            ('date_start', '>=', date_from),
        ])
        # Licencias por DIAS: filtrar por rango
        licencias_dias = self.env['planilla.leave.cr'].search([
            ('employee_id', 'in', emp_ids),
            ('company_id',  '=', company_id),
            ('state', '=', 'approved'),
            ('leave_unit', '=', 'day'),
            ('date_start', '<=', date_to),
            ('date_end',   '>=', date_from),
        ])
        licencias = licencias_horas | licencias_dias

        by_emp = {}
        for lic in licencias:
            by_emp.setdefault(lic.employee_id.id, []).append(lic)

        processed_ids = []
        for emp_id, lics in by_emp.items():
            slip = slip_by_emp.get(emp_id)
            if not slip:
                continue
            for lic in lics:
                slip._sync_licencias_single(lic, code_con_goce, code_sin_goce)
                processed_ids.append(lic.id)

        # FIX-AUD-07: NO marcar 'paid' en sincronizacion -- solo vincular payslip_id.
        # El estado 'paid' se asigna en action_pay igual que vacation_ids/overtime_ids.

    def _sync_licencias_single(self, lic, code_con_goce, code_sin_goce) -> None:
        """Sincroniza una licencia especial individual en esta boleta (batch mode).
        FIX MULTI-PERIODO: calcula monto proporcional al overlap de fechas y usa
        M2M payslip_ids en lugar de sobrescribir payslip_id."""
        existing = self.deduction_line_ids.filtered(
            lambda l, lid=lic.id: l.leave_cr_id and l.leave_cr_id.id == lid
        )
        if existing:
            return

        # -- Overlap y monto proporcional ------------------------------------
        if lic.leave_unit == 'hour':
            monto = lic.leave_amount or 0.0
            overlap_days = 1
            total_days   = 1
            overlap_start = lic.date_start
            overlap_end   = lic.date_end
        else:
            overlap_start = max(self.date_from, lic.date_start)
            overlap_end   = min(self.date_to,   lic.date_end)
            if overlap_end < overlap_start:
                return
            overlap_days = (overlap_end - overlap_start).days + 1
            total_days   = max(lic.days or 1, 1)
            daily_rate   = (lic.leave_amount or 0.0) / total_days
            # FIX DIAS-16: usar freq_factor si la licencia cubre todo el periodo
            dias_periodo_s = (self.date_to - self.date_from).days + 1 if (self.date_from and self.date_to) else 15
            if overlap_days >= dias_periodo_s:
                from ..planilla_const import FREQ_FACTORS as _FF2, DIAS_MES as _DM2
                _ff2  = _FF2.get(self._get_effective_freq() if hasattr(self, '_get_effective_freq') else 'biweekly', 0.5)
                monto = round(daily_rate * _DM2 * _ff2, 2)
            else:
                monto = round(daily_rate * overlap_days, 2)

        if monto <= 0:
            return

        pays = lic.has_salary or lic.has_salary_override
        tipo_label = dict(lic._fields['leave_type'].selection).get(lic.leave_type, lic.leave_type)
        periodo_desc = (
            f'{overlap_start} al {overlap_end}, {overlap_days} dia(s)'
            if overlap_days < total_days
            else f'{lic.date_start} al {lic.date_end}, {total_days} dia(s)'
        )

        vals = {
            'payslip_id':  self.id,
            'description': f'{"Licencia" if pays else "Licencia sin goce"}: {tipo_label} ({periodo_desc})',
            'amount':      monto,
            'leave_cr_id': lic.id,
        }
        if pays:
            vals.update({
                'deduction_code_id':  code_con_goce.id,
                'line_type':          'income',
                'deduction_category': 'licencia_con_goce',
            })
        else:
            vals.update({
                'deduction_code_id':  code_sin_goce.id,
                'line_type':          'deduction',
                'deduction_category': 'licencia_sin_goce',
            })
        self.env['planilla.payslip.deduction.line'].create(vals)
        # M2M: agregar esta boleta a payslip_ids sin sobrescribir
        lic.write({'payslip_ids': [(4, self.id)]})

    def _sync_recurring_benefits_batch(self) -> None:
        """Batch: carga beneficios recurrentes de TODOS los empleados en una query."""
        if not self:
            return
        date_from = self[0].date_from
        emp_ids = self.mapped('employee_id').ids
        slip_by_emp = {s.employee_id.id: s for s in self}

        benefits = self.env['planilla.recurring.benefit'].search([
            ('employee_id', 'in', emp_ids),
            ('active', '=', True),
            '|', ('date_start', '=', False), ('date_start', '<=', date_from),
            '|', ('date_end', '=', False),   ('date_end', '>=', date_from),
        ])
        by_emp = {}
        for b in benefits:
            by_emp.setdefault(b.employee_id.id, []).append(b)

        for emp_id, bens in by_emp.items():
            slip = slip_by_emp.get(emp_id)
            if not slip:
                continue
            # FIX-E1: pre-indexar las lineas existentes del slip para evitar duplicados.
            # El modo single verifica existentes; el batch no lo hacia -> podia duplicar
            # beneficios si el boton "Sincronizar" se presionaba mas de una vez.
            existing_ben_ids = set(
                slip.deduction_line_ids.filtered(
                    lambda l: l.recurring_benefit_id
                ).mapped('recurring_benefit_id').ids
            )
            for ben in bens:
                if ben.id in existing_ben_ids:
                    continue  # ya sincronizado -> omitir
                amt = ben.get_amount_for_salary(slip.gross_salary or 0.0)
                self.env['planilla.payslip.deduction.line'].create({
                    'payslip_id':           slip.id,
                    'deduction_code_id':    ben.deduction_code_id.id,
                    'description':          ben.name,
                    'line_type':            'income' if ben.benefit_type == 'income' else 'deduction',
                    'amount_type':          ben.amount_type,
                    'amount':               amt,
                    'percentage':           ben.percentage,
                    'recurring_benefit_id': ben.id,
                })

    def _sync_rop_batch(self) -> None:
        """Batch: sincroniza ROP para todos los empleados con rop_applies=True."""
        if not self:
            return
        rop_slips = self.filtered(
            lambda s: s.state == 'draft' and getattr(s.employee_id, 'rop_applies', False)
                      and (s.gross_salary or 0) > 0
        )
        if not rop_slips:
            return
        rh = self.env['planilla.rate.helper'].with_company(self[0].company_id)
        rop_emp_dc = rh._get_deduction_code('ROP_EMP')
        rop_pat_dc = rh._get_deduction_code('ROP_PAT')
        from .. import planilla_const as _K
        rop_emp_rate = (rop_emp_dc.employee_percentage / 100) if rop_emp_dc else _K.ROP_EMP
        rop_pat_rate = (rop_pat_dc.employer_percentage / 100) if rop_pat_dc else _K.ROP_PAT
        rop_code = self.env['planilla.deduction.code'].search([('code', '=', 'ROP')], limit=1)
        if not rop_code:
            try:
                rop_code = self.env['planilla.deduction.code'].sudo().create({
                    'code': 'ROP', 'name': 'ROP -- Regimen Obligatorio de Pensiones (Ley 7983)',
                    'deduction_type': 'employee',
                })
            except Exception:
                rop_code = self.env['planilla.deduction.code'].search([('code', '=', 'ROP')], limit=1)
        lines_to_create = []
        for slip in rop_slips:
            g = slip.gross_salary
            monto_emp = round(g * rop_emp_rate, 2)
            monto_pat = round(g * rop_pat_rate, 2)
            lines_to_create.append({
                'payslip_id':         slip.id,
                'deduction_code_id':  rop_code.id,
                'description':        f'ROP Obrero {rop_emp_rate*100:.1f}% -- Ley 7983',
                'line_type':          'deduction',
                'deduction_category': 'rop',
                'amount_type':        'percentage',
                'percentage':         rop_emp_rate * 100,
                'amount':             monto_emp,
            })
            slip.rop_employer = monto_pat
        if lines_to_create:
            self.env['planilla.payslip.deduction.line'].create(lines_to_create)

    def _sync_bonos_batch(self) -> None:
        """Batch: carga bonos activos de TODOS los empleados en una query."""
        if not self:
            return
        date_from = self[0].date_from
        date_to   = self[0].date_to
        emp_ids   = self.mapped('employee_id').ids
        slip_by_emp = {s.employee_id.id: s for s in self}

        bono_code = self.env['planilla.deduction.code'].search([('code', '=', 'BONO')], limit=1)
        if not bono_code:
            bono_code = self.env['planilla.deduction.code'].create({
                'code': 'BONO', 'name': 'Bono / Incentivo', 'deduction_type': 'employee',
                'calculation_type': 'fixed',
            })

        bonos = self.env['planilla.bono'].search([
            ('employee_id', 'in', emp_ids),
            ('state', '=', 'active'),
            ('date_start', '<=', date_to),
            '|', ('date_end', '=', False), ('date_end', '>=', date_from),
        ])
        by_emp = {}
        for b in bonos:
            by_emp.setdefault(b.employee_id.id, []).append(b)

        lines_to_create = []
        for emp_id, bono_list in by_emp.items():
            slip = slip_by_emp.get(emp_id)
            if not slip:
                continue
            # FIX-E2: verificar bonos ya sincronizados antes de agregar al batch.
            # El modo single verifica por description; aqui hacemos lo mismo.
            existing_desc = set(
                slip.deduction_line_ids.filtered(
                    lambda l: l.line_type == 'income' and l.deduction_category == 'bonus'
                ).mapped('description')
            )
            # Set de bono_ids ya en esta boleta (dedup por ID unico)
            existing_bono_ids = set(
                slip.deduction_line_ids.filtered(
                    lambda l: l.line_type == 'income' and l.deduction_category == 'bonus' and l.bono_id
                ).mapped('bono_id').ids
            )
            for bono in bono_list:
                desc = f'[{bono.code}] {bono.name}' if bono.code else f'Bono: {bono.name}'
                # Dedup por ID unico del bono
                if bono.id in existing_bono_ids:
                    continue
                # Fallback por descripcion para lineas creadas antes del campo bono_id
                if desc in existing_desc and bono.id not in existing_bono_ids:
                    existing_bono_ids.add(bono.id)
                    continue
                if bono.amount_type == 'fixed':
                    monto = bono.amount
                else:
                    monto = round((slip.employee_id.base_salary or 0.0) * bono.percentage / 100.0, 2)
                if monto <= 0:
                    continue
                lines_to_create.append({
                    'payslip_id':         slip.id,
                    'deduction_code_id':  bono_code.id,
                    'description':        desc,
                    'line_type':          'income',
                    'deduction_category': 'bonus',
                    'amount_type':        bono.amount_type,
                    'amount':             monto,
                    'percentage':         bono.percentage if bono.amount_type == 'percentage' else 0.0,
                    'is_recurring_bono':  bono.is_recurring,
                    'bono_id':            bono.id,
                })
                existing_bono_ids.add(bono.id)
                existing_desc.add(desc)
        if lines_to_create:
            self.env['planilla.payslip.deduction.line'].create(lines_to_create)

    def _sync_embargos_batch(self) -> None:
        """Batch: carga embargos activos de TODOS los empleados en una query."""
        if not self:
            return
        date_from = self[0].date_from
        date_to   = self[0].date_to
        emp_ids   = self.mapped('employee_id').ids
        slip_by_emp = {s.employee_id.id: s for s in self}

        embargo_code = self.env['planilla.deduction.code'].search([('code', '=', 'EMB')], limit=1)
        if not embargo_code:
            try:
                embargo_code = self.env['planilla.deduction.code'].sudo().create({
                    'code': 'EMB', 'name': 'Embargo Judicial',
                    'deduction_type': 'employee',
                })
            except Exception:
                embargo_code = self.env['planilla.deduction.code'].search([('code', '=', 'EMB')], limit=1)

        embargos = self.env['planilla.embargo'].search([
            ('employee_id', 'in', emp_ids),
            ('state', '=', 'active'),
            ('date_start', '<=', date_to),
            '|', ('date_end', '=', False), ('date_end', '>=', date_from),
        ])
        by_emp = {}
        for e in embargos:
            by_emp.setdefault(e.employee_id.id, []).append(e)

        lines_to_create = []
        for emp_id, emb_list in by_emp.items():
            slip = slip_by_emp.get(emp_id)
            if not slip or not embargo_code:
                continue
            gross     = slip.gross_salary or 0.0
            ccss_emp  = slip.ccss_employee or 0.0
            renta     = slip.income_tax or 0.0
            pensiones = sum(l.amount for l in slip.deduction_line_ids
                           if l.deduction_category == 'pension_alimentaria')
            ausencias = sum(l.amount for l in slip.deduction_line_ids
                           if l.deduction_category == 'ausencia')
            # FIX-M2: incluir licencias_sin_goce en la base para el tope Art. 172 CT.
            # El modo individual si las incluia; el batch las omitia -> tope inflado
            # en planillas grupales con empleados que tienen permisos sin goce.
            licencias_sg = sum(l.amount for l in slip.deduction_line_ids
                               if l.deduction_category == 'licencia_sin_goce')
            neto_disp = max(0.0, gross - ccss_emp - renta - pensiones - ausencias - licencias_sg)
            from .. import planilla_const as _K
            limite_total = round(neto_disp * _K.MAX_PCT_EMBARGO / 100, 2)
            ya_embargado = 0.0
            for embargo in emb_list:
                existing = slip.deduction_line_ids.filtered(
                    lambda l, e=embargo: l.deduction_category == 'embargo'
                    and l.numero_resolucion == e.numero_expediente
                )
                if existing:
                    continue
                monto = embargo.compute_amount(neto_disp)
                espacio = max(0.0, limite_total - ya_embargado)
                monto = min(monto, espacio)
                if monto <= 0:
                    continue
                lines_to_create.append({
                    'payslip_id':         slip.id,
                    'deduction_code_id':  embargo_code.id,
                    'description':        f'Embargo Judicial -- {embargo.beneficiario_nombre} ({embargo.numero_expediente})',
                    'line_type':          'deduction',
                    'deduction_category': 'embargo',
                    'amount_type':        embargo.calculation_type,
                    'amount':             monto,
                    'percentage':         embargo.percentage if embargo.calculation_type == 'percentage' else 0.0,
                    'numero_resolucion':  embargo.numero_expediente,
                })
                ya_embargado += monto
        if lines_to_create:
            self.env['planilla.payslip.deduction.line'].create(lines_to_create)

    def _sync_loan_deductions_batch(self) -> None:
        """Batch: carga cuotas de prestamos de TODOS los empleados en una query."""
        if not self:
            return
        date_from = self[0].date_from
        date_to   = self[0].date_to
        emp_ids   = self.mapped('employee_id').ids
        slip_by_emp = {s.employee_id.id: s for s in self}

        loan_code = self.env['planilla.deduction.code'].search([('code', '=', 'PRESTAMO')], limit=1)
        if not loan_code:
            return

        loans = self.env['planilla.employee.loan'].search([
            ('employee_id', 'in', emp_ids),
            ('state', 'in', ['approved', 'active']),
        ])
        # Cargar todas las cuotas pendientes en una query
        all_installments = self.env['planilla.loan.installment'].search([
            ('loan_id', 'in', loans.ids),
            ('state', '=', 'pending'),
        ])
        # Indexar cuotas por loan_id
        inst_by_loan = {}
        for inst in all_installments:
            inst_by_loan.setdefault(inst.loan_id.id, []).append(inst)

        # BUG FIX: precargar en UNA query las cuotas ya reclamadas por
        # cualquier boleta no cancelada -- evita duplicar la cuota mensual
        # cuando Q1 y Q2 del mismo mes se crean ambas en borrador antes de
        # confirmar cualquiera (mismo bug que en _sync_loan_deductions).
        _all_pending_ids = [i.id for lst in inst_by_loan.values() for i in lst]
        _already_claimed_ids = set()
        if _all_pending_ids:
            claimed_lines = self.env['planilla.payslip.deduction.line'].search([
                ('loan_installment_id', 'in', _all_pending_ids),
                ('payslip_id.state', '!=', 'cancelled'),
            ])
            _already_claimed_ids = set(claimed_lines.mapped('loan_installment_id').ids)

        lines_to_create = []
        for loan in loans:
            slip = slip_by_emp.get(loan.employee_id.id)
            if not slip:
                continue
            insts = inst_by_loan.get(loan.id, [])
            # Buscar cuota del periodo
            months_in_period = set()
            y, m = date_from.year, date_from.month
            ey, em = date_to.year, date_to.month
            while (y, m) <= (ey, em):
                months_in_period.add((y, m))
                m += 1
                if m > 12:
                    m = 1
                    y += 1
            matching = [i for i in insts
                        if i.due_date and (i.due_date.year, i.due_date.month) in months_in_period]
            if not matching:
                continue
            installment = matching[0]
            if installment.id in _already_claimed_ids:
                continue
            lines_to_create.append({
                'payslip_id':          slip.id,
                'deduction_code_id':   loan_code.id,
                'description':         loan.name,
                'line_type':           'deduction',       # FIX-E9: faltaba
                'deduction_category':  'loan',            # FIX-E9: faltaba -> salary_payable correcto
                'amount':              installment.amount,
                'loan_installment_id': installment.id,
            })
        if lines_to_create:
            self.env['planilla.payslip.deduction.line'].create(lines_to_create)

    # -- Cobros al Empleado --------------------------------------------

    def _sync_employee_charges(self) -> None:
        """
        Sincroniza cobros aprobados al empleado (planilla.employee.charge)
        como lineas de deduccion en la boleta.

        Maneja dos modalidades:
          - Cobro unico (is_recurring=False): se consume al aplicarse -> 'applied'
          - Cobro recurrente (is_recurring=True): permanece en 'approved', se aplica
            cada periodo nuevo. Deduplicacion por applied_periods (YYYY-MM).

        Si employee_amount=0 (subsidio 100%), no crea linea pero si registra
        el periodo o marca el cobro como aplicado para trazabilidad.
        """
        self.ensure_one()
        if self.state != 'draft':
            return

        charges = self.env['planilla.employee.charge'].search([
            ('employee_id', '=', self.employee_id.id),
            ('state', '=', 'approved'),
            '|',
            # Cobros unicos: periodo solapa con la boleta
            '&', ('is_recurring', '=', False),
                 '&', ('date_from', '<=', self.date_to),
                      ('date_to', '>=', self.date_from),
            # Cobros recurrentes: vigentes en el periodo de la boleta
            '&', ('is_recurring', '=', True),
                 '&', ('date_from', '<=', self.date_to),
                      '|', ('recurrence_end', '=', False),
                           ('recurrence_end', '>=', self.date_from),
        ])
        if not charges:
            return

        default_code = self.env['planilla.deduction.code'].search(
            [('code', '=', 'COBRO_EMP')], limit=1
        )

        lines_to_create   = []
        charges_to_apply  = []   # unicos -> marcar 'applied'
        charges_recurring = []   # recurrentes -> registrar periodo

        for charge in charges:
            # -- Deduplicacion -----------------------------------------
            if charge.is_recurring:
                if charge._is_period_already_applied(self.date_from):
                    continue
            else:
                # Deduplicacion en boleta actual
                existing = self.deduction_line_ids.filtered(
                    lambda l, c=charge: l.employee_charge_id == c.id
                )
                if existing:
                    continue
                # Deduplicacion cross-boleta: si ya fue aplicado en CUALQUIER
                # boleta activa (draft/confirmed/done), no duplicar.
                # FIX: incluir 'draft' para evitar doble carga cuando se
                # resetea a borrador y se vuelve a sincronizar.
                applied_elsewhere = self.env['planilla.payslip.deduction.line'].search([
                    ('employee_charge_id', '=', charge.id),
                    ('payslip_id', '!=', self.id),
                    ('payslip_id.state', 'in', ('draft', 'confirmed', 'done')),
                ], limit=1)
                if applied_elsewhere:
                    continue

            ded_code = charge.charge_type_id.deduction_code_id or default_code
            if not ded_code:
                _logger.warning(
                    'planilla_cr._sync_employee_charges: sin codigo de deduccion '
                    'para cobro "%s" del empleado %s -- omitido.',
                    charge.name, self.employee_id.name
                )
                continue

            if charge.is_recurring:
                charges_recurring.append(charge)
            else:
                charges_to_apply.append(charge)

            if charge.employee_amount <= 0:
                continue

            desc = charge.charge_type_id.name
            if charge.notes:
                desc = f'{desc}: {charge.notes}'

            # Forzar lectura fresca del código para evitar cache stale
            # Leer código directamente de BD para evitar cache stale del ORM
            self.env.cr.execute(
                "SELECT code FROM planilla_employee_charge WHERE id = %s",
                (charge.id,)
            )
            row = self.env.cr.fetchone()
            charge_code = (row[0] if row else None) or charge.code

            lines_to_create.append({
                'payslip_id':          self.id,
                'deduction_code_id':   ded_code.id,
                'description':         (
                    f'[{charge_code}] {desc}'
                    if charge_code else desc
                ),
                'line_type':           'deduction',
                'deduction_category':  'other',
                'amount_type':         'fixed',
                'amount':              charge.employee_amount,
                'employee_charge_id':  charge.id,
            })

        if lines_to_create:
            self.env['planilla.payslip.deduction.line'].create(lines_to_create)

        # Cobros unicos -> consumed, pasan a 'applied'
        if charges_to_apply:
            self.env['planilla.employee.charge'].browse(
                [c.id for c in charges_to_apply]
            ).write({'state': 'applied', 'payslip_id': self.id})

        # Cobros recurrentes -> registrar periodo, mantener 'approved'
        for charge in charges_recurring:
            charge._mark_period_applied(self.date_from)
            charge.payslip_id = self.id

    def _sync_employee_charges_batch(self) -> None:
        """
        Batch: carga cobros aprobados de TODOS los empleados en una query.
        Para 200 empleados: 200 queries -> 1 query. Reduccion 99%.
        Se activa automaticamente en la creacion masiva de boletas.

        Maneja cobros unicos y recurrentes con deduplicacion correcta.
        """
        if not self:
            return

        date_from = self[0].date_from
        date_to   = self[0].date_to
        emp_ids   = self.mapped('employee_id').ids
        slip_by_emp = {s.employee_id.id: s for s in self}

        # Cargar todos los cobros aprobados en una sola query
        all_charges = self.env['planilla.employee.charge'].search([
            ('employee_id', 'in', emp_ids),
            ('state', '=', 'approved'),
            '|',
            '&', ('is_recurring', '=', False),
                 '&', ('date_from', '<=', date_to),
                      ('date_to', '>=', date_from),
            '&', ('is_recurring', '=', True),
                 '&', ('date_from', '<=', date_to),
                      '|', ('recurrence_end', '=', False),
                           ('recurrence_end', '>=', date_from),
        ])
        if not all_charges:
            return

        charges_by_emp: dict = {}
        for charge in all_charges:
            charges_by_emp.setdefault(charge.employee_id.id, []).append(charge)

        default_code = self.env['planilla.deduction.code'].search(
            [('code', '=', 'COBRO_EMP')], limit=1
        )

        lines_to_create   = []
        unique_to_apply   = []        # (charge_id, slip_id) unicos
        recurring_to_mark = []        # (charge, date_from, slip_id) recurrentes

        # PERF FIX: precargar en UNA query los cobros unicos que ya fueron
        # aplicados en otra boleta confirmada/pagada -- antes esto era una
        # query .search() POR CADA cobro dentro del loop de abajo, anulando
        # el proposito del metodo (que segun su propio docstring existe para
        # pasar de 200 queries a 1).
        _unique_charge_ids = [
            c.id for charges in charges_by_emp.values() for c in charges
            if not c.is_recurring
        ]
        _already_applied_ids = set()
        if _unique_charge_ids:
            applied_lines = self.env['planilla.payslip.deduction.line'].search([
                ('employee_charge_id', 'in', _unique_charge_ids),
                ('payslip_id.state', '!=', 'cancelled'),
            ])
            _already_applied_ids = set(applied_lines.mapped('employee_charge_id').ids)

        for slip in self:
            if slip.state != 'draft':
                continue
            emp_charges = charges_by_emp.get(slip.employee_id.id, [])
            for charge in emp_charges:
                # -- Deduplicacion --------------------------------------
                if charge.is_recurring:
                    if charge._is_period_already_applied(slip.date_from):
                        continue
                # cobros unicos: verificar que no hayan sido aplicados en otra boleta confirmada
                if not charge.is_recurring:
                    if charge.id in _already_applied_ids:
                        continue

                ded_code = charge.charge_type_id.deduction_code_id or default_code
                if not ded_code:
                    _logger.warning(
                        'planilla_cr._sync_employee_charges_batch: sin codigo de '
                        'deduccion para cobro "%s" del empleado %s -- omitido.',
                        charge.name, slip.employee_id.name
                    )
                    continue

                if charge.is_recurring:
                    recurring_to_mark.append((charge, slip.date_from, slip.id))
                else:
                    unique_to_apply.append((charge.id, slip.id))

                if charge.employee_amount <= 0:
                    continue

                desc = charge.charge_type_id.name
                if charge.notes:
                    desc = f'{desc}: {charge.notes}'

                # PERF FIX: eliminada la query SQL cruda por linea -- charge.code
                # ya viene fresco del all_charges.search() de mas arriba en este
                # mismo metodo; nada escribio sobre 'code' entre medio, asi que
                # el cache del ORM ya es correcto.
                _charge_code = charge.code

                lines_to_create.append({
                    'payslip_id':          slip.id,
                    'deduction_code_id':   ded_code.id,
                    'description':         (
                        f'[{_charge_code}] {desc}'
                        if _charge_code else desc
                    ),
                    'line_type':           'deduction',
                    'deduction_category':  'other',
                    'amount_type':         'fixed',
                    'amount':              charge.employee_amount,
                    'employee_charge_id':  charge.id,
                })

        if lines_to_create:
            self.env['planilla.payslip.deduction.line'].create(lines_to_create)

        # Cobros unicos -> 'applied'
        if unique_to_apply:
            # Agrupar por slip para batch write
            by_slip: dict = {}
            for cid, sid in unique_to_apply:
                by_slip.setdefault(sid, []).append(cid)
            for slip_id, cids in by_slip.items():
                self.env['planilla.employee.charge'].browse(cids).write({
                    'state': 'applied', 'payslip_id': slip_id
                })

        # Cobros recurrentes -> registrar periodo
        for charge, df, slip_id in recurring_to_mark:
            charge._mark_period_applied(df)
            charge.payslip_id = slip_id
