import datetime
import logging

from odoo import models, api

_logger = logging.getLogger(__name__)

# Hora a partir de la cual empieza la jornada nocturna (Art. 135 CT: 10pm = 22:00)
HORA_NOCTURNA = 22.0
# Zona mixta: 7pm-10pm (19:00-22:00) — se trata como simple en este módulo
HORA_MIXTA = 19.0


class PayslipAutoOvertimeMixin(models.AbstractModel):
    _name = 'planilla.payslip.auto.overtime.mixin'
    _description = 'Detección automática de HE desde asistencias'

    def _auto_detect_overtime(self) -> int:
        """Analiza asistencias del período, detecta HE por día y crea registros
        en estado Borrador. Solo actúa si:
          - El empleado usa método 'attendance'
          - La config tiene enable_auto_overtime=True
          - No existe ya un registro de HE para ese día y tipo

        Returns: número de registros HE creados.
        """
        self.ensure_one()
        emp = self.employee_id
        if emp.payroll_calculation_method != 'attendance':
            return 0
        if not self.date_from or not self.date_to:
            return 0

        config = self.env['planilla.accounting.config'].sudo().get_config(
            emp.company_id.id if emp.company_id else self.env.company.id
        )
        if not config or not config.enable_auto_overtime:
            return 0

        schedule = emp.schedule_type_id
        if not schedule:
            _logger.warning(
                'planilla_cr auto-overtime: empleado %s sin tipo de horario', emp.name)
            return 0

        hours_per_day = schedule.hours_per_day or 8.0
        hora_entrada  = schedule.hora_entrada  or 8.0
        hora_salida   = schedule.hora_salida   or 17.0

        # Obtener asistencias del período con zona horaria CR (UTC-6)
        cr_offset = datetime.timedelta(hours=6)
        dt_from = datetime.datetime.combine(self.date_from, datetime.time.min)
        dt_to   = datetime.datetime.combine(self.date_to,   datetime.time.max)

        attendances = self.env['hr.attendance'].search([
            ('employee_id', '=', emp.id),
            ('check_in',    '>=', dt_from - cr_offset),
            ('check_in',    '<=', dt_to   - cr_offset),
            ('check_out',   '!=', False),
        ])

        # Feriados del período (con is_paid para obligatorio vs no obligatorio)
        holidays_recs = self.env['planilla.public.holiday'].search([
            ('date', '>=', self.date_from),
            ('date', '<=', self.date_to),
        ])
        holidays = {h.date: h for h in holidays_recs}

        # HE ya existentes: (date, overtime_type) → skip si ya existe
        existing_ot = self.env['planilla.overtime.cr'].search([
            ('employee_id', '=', emp.id),
            ('date',        '>=', self.date_from),
            ('date',        '<=', self.date_to),
        ])
        existing_set = {(o.date, o.overtime_type) for o in existing_ot}

        # Agrupar asistencias por día calendario CR
        from collections import defaultdict
        by_day = defaultdict(float)
        checkout_hour_by_day = defaultdict(float)  # última hora de salida del día
        for att in attendances:
            day_cr = (att.check_in + cr_offset).date()
            by_day[day_cr] += att.worked_hours
            # Hora de salida en CR
            co_cr = att.check_out + cr_offset
            co_hr = co_cr.hour + co_cr.minute / 60.0
            checkout_hour_by_day[day_cr] = max(checkout_hour_by_day[day_cr], co_hr)

        created = 0
        OT = self.env['planilla.overtime.cr']

        for day, total_hours in sorted(by_day.items()):

            # 1. ¿Es feriado?
            if day in holidays:
                holiday = holidays[day]
                ot_type = 'holiday'
                # Horas trabajadas en feriado = todo el tiempo registrado
                ot_hours = round(total_hours, 2)
                if ot_hours > 0 and (day, ot_type) not in existing_set:
                    OT.create({
                        'employee_id': emp.id,
                        'date':         day,
                        'overtime_type': ot_type,
                        'hours':        ot_hours,
                        'state':        'draft',
                        'source':       'auto',
                        'note': (
                            f'Auto-detectado: Feriado '
                            f'{"Obligatorio" if holiday.is_paid else "No Obligatorio"} '
                            f'— {holiday.name} — {ot_hours}h trabajadas'
                        ),
                    })
                    created += 1
                continue  # No procesar más para este día

            # 2. ¿Es día de descanso (fuera de jornada laboral)?
            is_workday = schedule.is_working_day(day) if hasattr(schedule, 'is_working_day') else True
            if not is_workday:
                ot_hours = round(total_hours, 2)
                ot_type  = 'double'
                if ot_hours > 0 and (day, ot_type) not in existing_set:
                    OT.create({
                        'employee_id': emp.id,
                        'date':         day,
                        'overtime_type': ot_type,
                        'hours':        ot_hours,
                        'state':        'draft',
                        'source':       'auto',
                        'note': (
                            f'Auto-detectado: Día de descanso '
                            f'({day.strftime("%A")}) — {ot_hours}h trabajadas'
                        ),
                    })
                    created += 1
                continue

            # 3. Día laboral — detectar exceso sobre jornada
            extra_hours = max(round(total_hours - hours_per_day, 2), 0.0)
            if extra_hours <= 0:
                continue

            # Determinar tipo según hora de salida real
            checkout_hr = checkout_hour_by_day.get(day, hora_salida)
            if checkout_hr >= HORA_NOCTURNA:
                ot_type = 'nocturna'
            else:
                ot_type = 'simple'

            if (day, ot_type) not in existing_set:
                OT.create({
                    'employee_id': emp.id,
                    'date':         day,
                    'overtime_type': ot_type,
                    'hours':        extra_hours,
                    'state':        'draft',
                    'source':       'auto',
                    'note': (
                        f'Auto-detectado: {total_hours}h trabajadas, '
                        f'jornada {hours_per_day}h, exceso {extra_hours}h '
                        f'(salida {checkout_hr:.1f}h → {ot_type})'
                    ),
                })
                created += 1

        if created:
            _logger.info(
                'planilla_cr auto-overtime: %d HE en borrador creadas para %s (%s → %s)',
                created, emp.name, self.date_from, self.date_to
            )
        return created
