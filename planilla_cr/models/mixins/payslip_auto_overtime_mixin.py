import datetime
import logging
from collections import defaultdict

from odoo import models, api

_logger = logging.getLogger(__name__)

# Hora a partir de la cual empieza la jornada nocturna (Art. 135 CT: 10pm = 22:00)
HORA_NOCTURNA = 22.0
# Zona mixta: 7pm-10pm (19:00-22:00) -- se trata como simple en este modulo
HORA_MIXTA = 19.0


class PayslipAutoOvertimeMixin(models.AbstractModel):
    _name = 'planilla.payslip.auto.overtime.mixin'
    _description = 'Deteccion automatica de HE y tardias desde asistencias'

    # ==================================================================
    # Integracion OPCIONAL con nombramientos_cr.
    #
    # planilla_cr NO declara dependencia del modulo nombramientos_cr. Se
    # detecta en tiempo de ejecucion si el modelo 'nombramientos.turno'
    # existe en el registro de Odoo. Si no existe, todo el bloque de
    # Nombramientos se salta y el sistema usa unicamente
    # planilla.schedule.type (comportamiento historico).
    #
    # facial_attendance no necesita deteccion especial: escribe
    # directamente en el modelo estandar hr.attendance.
    # ==================================================================

    def _get_nombramientos_turnos_for_day(self, emp, day):
        """Retorna TODOS los turnos confirmados del empleado para ese dia
        (puede ser mas de uno si trabaja en varias sedes el mismo dia).
        Recordset vacio si nombramientos_cr no esta instalado o no hay
        turnos ese dia.
        """
        Turno = self.env.get('nombramientos.turno')
        if Turno is None:
            return None
        return Turno.sudo().search([
            ('employee_id', '=', emp.id),
            ('date', '=', day),
            ('state', '!=', 'absent'),
            ('nombramiento_id.state', 'in', ['confirmed', 'in_payroll', 'paid']),
        ], order='hour_start')

    def _get_general_schedule_for_day(self, emp, day):
        """Retorna planilla.schedule.type activo para un dia (principal o
        secundario)."""
        schedule = emp.schedule_type_id
        if emp.schedule_secondary_id:
            sec = emp.schedule_secondary_id
            if hasattr(sec, 'is_working_day') and sec.is_working_day(day):
                return sec
        return schedule

    def _build_day_plan(self, emp, day, config):
        """Construye el plan esperado del dia: una lista de 'bloques'
        esperados, cada uno con hora_entrada, hora_salida, sede, horas
        netas (ya con almuerzo descontado si aplica) y origen.

        Casos:
          - Con Nombramientos activo y turno(s) ese dia: un bloque por
            turno confirmado (soporta multiples turnos/sedes el mismo
            dia -- Caso 6).
          - Sin turno ese dia pero con horario general: un bloque unico
            con el almuerzo del horario general descontado si aplica.
          - Sin turno y sin horario general: sin bloques (lista vacia),
            senal de que se debe usar la jornada por defecto / HE total.

        Retorna (bloques, origen) donde origen es 'nombramiento',
        'horario_general' o 'sin_configuracion'.
        """
        use_nom = bool(config and config.use_nombramientos_schedule)
        turnos = self._get_nombramientos_turnos_for_day(emp, day) if use_nom else None
        nombramientos_disponible = turnos is not None

        if nombramientos_disponible and turnos:
            bloques = []
            for turno in turnos:
                hora_entrada = turno.hour_start or 8.0
                hora_salida = turno.hour_end or 17.0
                # turno.hours ya viene neto (con almuerzo descontado si
                # nombramientos.config.apply_lunch_break esta activo) --
                # se respeta ese calculo tal cual, sin volver a restar.
                horas_netas = turno.hours or max(hora_salida - hora_entrada, 0.0)
                bloques.append({
                    'hora_entrada': hora_entrada,
                    'hora_salida': hora_salida,
                    'horas_netas': horas_netas,
                    'branch_id': turno.effective_branch_id.id if turno.effective_branch_id else False,
                    'branch_name': turno.effective_branch_id.name if turno.effective_branch_id else '',
                    'turno': turno,
                })
            return bloques, 'nombramiento', nombramientos_disponible

        schedule = self._get_general_schedule_for_day(emp, day)
        if not schedule:
            return [], 'sin_configuracion', nombramientos_disponible

        is_workday = schedule.is_working_day(day) if hasattr(schedule, 'is_working_day') else True
        if not is_workday:
            # Dia de descanso segun horario general: sin bloque de jornada
            # ordinaria (el flujo de HE lo trata aparte, tipo 'double').
            return [], 'horario_general_descanso', nombramientos_disponible

        hora_entrada = schedule.hora_entrada or 8.0
        hora_salida = schedule.hora_salida or 17.0
        horas_netas = schedule.hours_per_day or 8.0
        return [{
            'hora_entrada': hora_entrada,
            'hora_salida': hora_salida,
            'horas_netas': horas_netas,
            'branch_id': emp.branch_id.id if emp.branch_id else False,
            'branch_name': emp.branch_id.name if emp.branch_id else '',
            'turno': None,
        }], 'horario_general', nombramientos_disponible

    # ------------------------------------------------------------------
    # Almuerzo: ajusta horas brutas segun cuantas marcaciones hubo
    # ------------------------------------------------------------------

    def _apply_lunch_adjustment(self, emp, day, attendance_blocks, plan_bloques, origen, config):
        """Ajusta las horas brutas del dia por almuerzo, segun la regla
        acordada:
          - 2 marcaciones (1 bloque de asistencia): se asume que el
            empleado almorzo el tiempo COMPLETO configurado (del turno
            de Nombramientos si apply_lunch_break esta activo, o del
            horario general segun apply_general_schedule_lunch) y se
            resta ese tiempo fijo del bruto.
          - 4+ marcaciones (2+ bloques de asistencia con un hueco entre
            ellos): se usa el hueco REAL medido entre el check_out del
            primer bloque y el check_in del segundo -- no se asume nada,
            se descuenta lo que realmente paso. Ademas se compara ese
            hueco real contra el almuerzo permitido + tolerancia para
            detectar tardia de regreso.

        Retorna (horas_netas_dia, tardia_regreso_info o None).
        """
        total_bruto = sum(b['hours'] for b in attendance_blocks)

        # Determinar minutos de almuerzo configurados segun origen
        if origen == 'nombramiento':
            NomConfig = self.env.get('nombramientos.config')
            lunch_minutes = 0
            if NomConfig is not None:
                nom_cfg = NomConfig.sudo().get_config()
                if nom_cfg and nom_cfg.apply_lunch_break:
                    lunch_minutes = nom_cfg.lunch_break_minutes or 0
        else:
            lunch_minutes = 0
            if config and config.apply_general_schedule_lunch:
                lunch_minutes = config.general_schedule_lunch_minutes or 0

        if len(attendance_blocks) <= 1:
            # 2 marcaciones (o menos): asumir el almuerzo completo
            # configurado. Si origen=='nombramiento', turno.hours ya
            # viene neto -- no restar de nuevo aqui, el bruto se deja
            # tal cual y la comparacion contra horas_netas del plan
            # se hace mas arriba en el flujo principal. Para
            # horario_general si se resta aqui porque schedule.
            # hours_per_day es la jornada teorica, no calculada desde
            # horas brutas reales.
            if origen == 'horario_general' and lunch_minutes > 0:
                horas_netas = max(0.0, total_bruto - (lunch_minutes / 60.0))
            else:
                horas_netas = total_bruto
            return horas_netas, None

        # 4+ marcaciones: usar el hueco real entre bloques consecutivos.
        # Se toma el hueco mas largo del dia como "el almuerzo" (asume
        # que el break de almuerzo es la pausa mas larga entre marcas).
        gaps = []
        sorted_blocks = sorted(attendance_blocks, key=lambda b: b['check_in'])
        for i in range(len(sorted_blocks) - 1):
            gap_start = sorted_blocks[i]['check_out']
            gap_end = sorted_blocks[i + 1]['check_in']
            gap_minutes = (gap_end - gap_start).total_seconds() / 60.0
            gaps.append(gap_minutes)

        # El bruto ya excluye los huecos (cada bloque es su propio
        # worked_hours), asi que horas_netas = total_bruto directamente.
        horas_netas = total_bruto

        tardia_info = None
        if gaps:
            almuerzo_real_minutos = max(gaps)
            permitido = lunch_minutes if lunch_minutes > 0 else 60
            tolerancia = config.lunch_return_tolerance_minutes if config else 10
            exceso = almuerzo_real_minutos - permitido - tolerancia
            if exceso > 0:
                tardia_info = {
                    'minutos_tarde': round(exceso),
                    'almuerzo_real': round(almuerzo_real_minutos),
                    'almuerzo_permitido': permitido,
                }

        return horas_netas, tardia_info

    # ------------------------------------------------------------------
    # Deteccion de tardias de entrada (informativa, no afecta el pago)
    # ------------------------------------------------------------------

    def _detect_tardiness_entrada(self, checkin_hr, hora_entrada_esperada, config):
        tolerance_min = 15
        if config and config.tardiness_tolerance_minutes is not None:
            tolerance_min = config.tardiness_tolerance_minutes
        diff_minutos = round((checkin_hr - hora_entrada_esperada) * 60)
        if diff_minutos > tolerance_min:
            return True, diff_minutos
        return False, 0

    # ------------------------------------------------------------------
    # Deteccion principal
    # ------------------------------------------------------------------

    def _auto_detect_overtime(self) -> int:
        """Analiza asistencias del periodo, detecta HE y tardias por dia,
        y crea registros en estado Borrador.

        Reglas aplicadas (en orden de prioridad por dia):
          1. Si hay turno(s) de Nombramientos ese dia (modulo instalado y
             activo): cada bloque de asistencia se compara contra el
             turno de su rango horario/sede (Caso 6: multiples turnos).
          2. Si no hay turno pero si horario general: se usa ese horario,
             con descuento de almuerzo configurable.
          3. Si no hay ni turno ni horario general: TODAS las horas
             marcadas se registran como HE en Borrador pendiente de
             aprobacion del supervisor (mismo tratamiento que asistencia
             sin nombramiento).

        Returns: numero de registros HE creados.
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

        cr_offset = datetime.timedelta(hours=6)
        dt_from = datetime.datetime.combine(self.date_from, datetime.time.min)
        dt_to   = datetime.datetime.combine(self.date_to,   datetime.time.max)

        attendances = self.env['hr.attendance'].search([
            ('employee_id', '=', emp.id),
            ('check_in',    '>=', dt_from - cr_offset),
            ('check_in',    '<=', dt_to   - cr_offset),
            ('check_out',   '!=', False),
        ], order='check_in')

        holidays_recs = self.env['planilla.public.holiday'].search([
            ('date', '>=', self.date_from),
            ('date', '<=', self.date_to),
        ])
        holidays = {h.date: h for h in holidays_recs}

        existing_ot = self.env['planilla.overtime'].search([
            ('employee_id', '=', emp.id),
            ('date',        '>=', self.date_from),
            ('date',        '<=', self.date_to),
        ])
        existing_set = {(o.date, o.overtime_type) for o in existing_ot}

        # Agrupar bloques de asistencia (cada check_in/check_out) por dia
        # calendario CR, preservando cada bloque individual -- necesario
        # para: (a) detectar multiples marcaciones (almuerzo), y (b)
        # emparejar cada bloque con el turno de su sede/horario (Caso 6).
        blocks_by_day = defaultdict(list)
        for att in attendances:
            ci_cr = att.check_in + cr_offset
            co_cr = att.check_out + cr_offset
            day_cr = ci_cr.date()
            blocks_by_day[day_cr].append({
                'check_in': ci_cr,
                'check_out': co_cr,
                'hours': att.worked_hours,
                'ci_hr': ci_cr.hour + ci_cr.minute / 60.0,
                'co_hr': co_cr.hour + co_cr.minute / 60.0,
            })

        created = 0
        tardanzas = []
        OT = self.env['planilla.overtime']

        for day, blocks in sorted(blocks_by_day.items()):

            plan_bloques, origen, nombramientos_disponible = self._build_day_plan(emp, day, config)

            # -- Caso: sin nombramiento y sin horario general ----------
            # No hay ninguna jornada esperada contra la cual comparar.
            # Regla acordada: todas las horas se registran como HE en
            # borrador pendiente de aprobacion del supervisor.
            if origen == 'sin_configuracion':
                total_hours = round(sum(b['hours'] for b in blocks), 2)
                ot_type = 'simple'
                if total_hours > 0 and (day, ot_type) not in existing_set:
                    OT.create({
                        'employee_id': emp.id,
                        'date':         day,
                        'overtime_type': ot_type,
                        'hours':        total_hours,
                        'state':        'draft',
                        'source':       'auto',
                        'note': (
                            f'ATENCION: el empleado marco {total_hours}h el '
                            f'{day.strftime("%d/%m/%Y")} SIN tener nombramiento '
                            f'asignado NI Tipo de Horario configurado. No hay '
                            f'jornada esperada valida para calcular HE. '
                            f'Referencia de jornada estandar: '
                            f'{config.default_workday_hours_no_schedule:.1f}h. '
                            f'Requiere revision y aprobacion explicita del '
                            f'supervisor, y configurar horario/nombramiento '
                            f'para este empleado.'
                        ),
                    })
                    created += 1
                continue

            # -- Dia de descanso segun horario general (sin turno, y sin
            # nombramientos activos o el dia cae fuera de jornada) -----
            # Se evalua ANTES que "asistencia sin nombramiento" porque
            # un dia de descanso real no es lo mismo que un dia laboral
            # sin turno asignado: aqui el empleado no debia trabajar,
            # asi que todo lo marcado es HE doble por Art. 152 CT, no
            # una alerta de "falta configurar turno".
            if origen == 'horario_general_descanso':
                total_hours = round(sum(b['hours'] for b in blocks), 2)
                ot_type = 'double'
                if total_hours > 0 and (day, ot_type) not in existing_set:
                    OT.create({
                        'employee_id': emp.id,
                        'date':         day,
                        'overtime_type': ot_type,
                        'hours':        total_hours,
                        'state':        'draft',
                        'source':       'auto',
                        'note': (
                            f'Auto-detectado: Dia de descanso '
                            f'({day.strftime("%A")}) -- {total_hours}h trabajadas'
                        ),
                    })
                    created += 1
                continue

            # -- Caso: asistencia SIN nombramiento (pero con horario) --
            # El empleado SI debia trabajar segun su horario general
            # (dia laboral), pero no tiene turno de Nombramientos
            # asignado para hoy -- requiere aprobacion del supervisor.
            if (config.use_nombramientos_schedule and nombramientos_disponible
                    and origen == 'horario_general'):
                if not config.unassigned_attendance_as_overtime:
                    continue
                total_hours = round(sum(b['hours'] for b in blocks), 2)
                ot_type = 'simple'
                if total_hours > 0 and (day, ot_type) not in existing_set:
                    OT.create({
                        'employee_id': emp.id,
                        'date':         day,
                        'overtime_type': ot_type,
                        'hours':        total_hours,
                        'state':        'draft',
                        'source':       'auto',
                        'note': (
                            f'ATENCION: asistencia SIN nombramiento asignado. '
                            f'El empleado marco {total_hours}h el '
                            f'{day.strftime("%d/%m/%Y")} sin tener un turno '
                            f'confirmado en Nombramientos ese dia. Requiere '
                            f'revision y aprobacion explicita del supervisor.'
                        ),
                    })
                    created += 1
                continue

            # -- Es feriado? (aplica con nombramiento u horario general) -
            if day in holidays:
                holiday = holidays[day]
                total_hours = round(sum(b['hours'] for b in blocks), 2)
                ot_type = 'holiday'
                if total_hours > 0 and (day, ot_type) not in existing_set:
                    OT.create({
                        'employee_id': emp.id,
                        'date':         day,
                        'overtime_type': ot_type,
                        'hours':        total_hours,
                        'state':        'draft',
                        'source':       'auto',
                        'note': (
                            f'Auto-detectado: Feriado '
                            f'{"Obligatorio" if holiday.is_paid else "No Obligatorio"} '
                            f'-- {holiday.name} -- {total_hours}h trabajadas'
                        ),
                    })
                    created += 1
                continue

            # -- Emparejar bloques de asistencia con bloques del plan ---
            # Caso 6 (multiples turnos/sedes el mismo dia): cada bloque
            # de asistencia se asigna al bloque del plan cuyo rango
            # horario mas se traslapa. Con horario general solo hay un
            # bloque de plan, asi que todos los bloques de asistencia
            # se comparan contra el.
            if len(plan_bloques) > 1:
                asignaciones = self._match_blocks_to_plan(blocks, plan_bloques)
            else:
                asignaciones = {0: blocks}

            for plan_idx, asig_blocks in asignaciones.items():
                if not asig_blocks:
                    continue
                plan = plan_bloques[plan_idx]

                # -- Tardia de entrada (primer bloque del grupo) -------
                first_block = min(asig_blocks, key=lambda b: b['check_in'])
                es_tardia, minutos_tarde = self._detect_tardiness_entrada(
                    first_block['ci_hr'], plan['hora_entrada'], config)
                if es_tardia:
                    sede_txt = f' en {plan["branch_name"]}' if plan['branch_name'] else ''
                    tardanzas.append(
                        f'{day.strftime("%d/%m/%Y")}{sede_txt}: llego '
                        f'{minutos_tarde} min tarde (esperado '
                        f'{plan["hora_entrada"]:.2f}h, {origen}).'
                    )

                # -- Ajuste de almuerzo + tardia de regreso ------------
                horas_netas, tardia_almuerzo = self._apply_lunch_adjustment(
                    emp, day, asig_blocks, plan_bloques, origen, config)
                if tardia_almuerzo:
                    sede_txt = f' en {plan["branch_name"]}' if plan['branch_name'] else ''
                    tardanzas.append(
                        f'{day.strftime("%d/%m/%Y")}{sede_txt}: regreso de '
                        f'almuerzo {tardia_almuerzo["minutos_tarde"]} min tarde '
                        f'(almuerzo real {tardia_almuerzo["almuerzo_real"]} min, '
                        f'permitido {tardia_almuerzo["almuerzo_permitido"]} min).'
                    )

                # -- Exceso sobre la jornada esperada de este bloque ---
                extra_hours = max(round(horas_netas - plan['horas_netas'], 2), 0.0)
                if extra_hours <= 0:
                    continue

                last_block = max(asig_blocks, key=lambda b: b['check_out'])
                checkout_hr = last_block['co_hr']
                ot_type = 'nocturna' if checkout_hr >= HORA_NOCTURNA else 'simple'

                if (day, ot_type) not in existing_set:
                    sede_txt = f' -- {plan["branch_name"]}' if plan['branch_name'] else ''
                    OT.create({
                        'employee_id': emp.id,
                        'date':         day,
                        'overtime_type': ot_type,
                        'hours':        extra_hours,
                        'state':        'draft',
                        'source':       'auto',
                        'note': (
                            f'Auto-detectado ({origen}{sede_txt}): '
                            f'{horas_netas:.2f}h netas, jornada esperada '
                            f'{plan["horas_netas"]:.2f}h, exceso {extra_hours}h '
                            f'(salida {checkout_hr:.2f}h -> {ot_type})'
                        ),
                    })
                    created += 1

        if tardanzas:
            self.message_post(
                body=(
                    'Novedades de asistencia detectadas en el periodo '
                    '(informativo, no afecta el calculo del pago):<br/>' +
                    '<br/>'.join(tardanzas)
                ),
                message_type='notification',
            )

        if created:
            _logger.info(
                'planilla_cr auto-overtime: %d HE en borrador creadas para %s (%s -> %s)',
                created, emp.name, self.date_from, self.date_to
            )
        return created

    def _match_blocks_to_plan(self, attendance_blocks, plan_bloques):
        """Caso 6: asigna cada bloque de asistencia al bloque del plan
        (turno) cuyo horario esperado mas se traslapa con la marcacion
        real. Retorna dict {plan_index: [attendance_blocks]}.
        """
        asignaciones = {i: [] for i in range(len(plan_bloques))}
        for block in attendance_blocks:
            mejor_idx = None
            mejor_overlap = -1.0
            for i, plan in enumerate(plan_bloques):
                overlap_start = max(block['ci_hr'], plan['hora_entrada'])
                overlap_end = min(block['co_hr'], plan['hora_salida'])
                overlap = max(0.0, overlap_end - overlap_start)
                if overlap > mejor_overlap:
                    mejor_overlap = overlap
                    mejor_idx = i
            if mejor_idx is None:
                mejor_idx = 0
            asignaciones[mejor_idx].append(block)
        return asignaciones
