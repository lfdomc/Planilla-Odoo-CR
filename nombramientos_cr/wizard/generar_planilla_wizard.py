import logging
import datetime
from collections import defaultdict

from odoo import models, fields, api
from odoo.exceptions import UserError
from ..models import constants as C

_logger = logging.getLogger(__name__)


def _get_monday(d):
    """Retorna el lunes de la semana de la fecha dada."""
    if not d:
        return d
    days = d.weekday()
    return d - datetime.timedelta(days=days)


def _get_worked_hours_for_turno(env, employee_id, turno):
    """Calcula las horas realmente trabajadas por el empleado durante la
    ventana horaria de un turno especifico, sumando los bloques de
    hr.attendance (reloj facial u otro metodo) que se traslapan con ese
    turno.

    hr.attendance es un modelo nativo de Odoo -- no requiere ningun
    modulo adicional instalado. Si el empleado no marco nada ese dia,
    retorna 0.0 (ausencia total).
    """
    cr_offset = datetime.timedelta(hours=6)
    dt_from = datetime.datetime.combine(turno.date, datetime.time.min)
    dt_to   = datetime.datetime.combine(turno.date, datetime.time.max)

    attendances = env['hr.attendance'].search([
        ('employee_id', '=', employee_id),
        ('check_in',    '>=', dt_from - cr_offset),
        ('check_in',    '<=', dt_to   - cr_offset),
        ('check_out',   '!=', False),
    ])
    if not attendances:
        return 0.0

    total = 0.0
    for att in attendances:
        ci_cr = att.check_in + cr_offset
        co_cr = att.check_out + cr_offset
        ci_hr = ci_cr.hour + ci_cr.minute / 60.0
        co_hr = co_cr.hour + co_cr.minute / 60.0
        overlap_start = max(ci_hr, turno.hour_start)
        overlap_end   = min(co_hr, turno.hour_end)
        total += max(0.0, overlap_end - overlap_start)
    return total


class GenerarPlanillaWizard(models.TransientModel):
    """
    Genera una planilla semanal desde los nombramientos confirmados.
    Cada empleado recibe una boleta donde:
      Salario = Horas trabajadas × Tarifa por hora
    Las deducciones (CCSS, Renta) se calculan normalmente sobre ese monto.
    """
    _name = 'nombramientos.generar.planilla.wizard'
    _description = 'Generar Planilla desde Nombramientos'

    date_start = fields.Date(
        string='Semana Desde (Lunes)', required=True,
        default=lambda self: _get_monday(fields.Date.context_today(self)),
    )
    date_end = fields.Date(
        string='Semana Hasta', required=True,
    )
    payroll_run_id = fields.Many2one(
        'planilla.run.cr', string='Agregar a Planilla Existente',
        help='Opcional. Si se deja vacío se crea una planilla nueva.',
    )
    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company,
    )
    nombramiento_ids = fields.Many2many(
        'nombramientos.nombramiento',
        'nom_wizard_nom_rel',
        'wizard_id', 'nom_id',
        string='Nombramientos a incluir',
        domain="[('state','in',['draft','confirmed'])]",
    )
    auto_confirm = fields.Boolean(
        string='Confirmar nombramientos automáticamente',
        default=True,
    )

    def _force_base_salary_zero(self, slip):
        # base_salary es compute+store. El ORM lo recalcula desde emp.base_salary
        # cada vez que se accede. La única forma confiable es SQL + invalidar cache.
        # Se ejecuta dentro de un savepoint propio para no contaminar la transacción.
        sp = f'sp_nom_bsal_{slip.id}'
        try:
            self.env.cr.execute(f'SAVEPOINT {sp}')
            self.env.cr.execute(
                'UPDATE planilla_payslip_cr SET base_salary = 0 WHERE id = %s',
                (slip.id,)
            )
            self.env.cr.execute(f'RELEASE SAVEPOINT {sp}')
            # Invalidar TODOS los campos computados dependientes del salario base
            slip.invalidate_recordset([
                'base_salary', 'gross_salary', 'salario_cotizable',
                'net_salary', 'total_employer_cost', 'ccss_employee',
                'ccss_employer', 'deposito_patrono',
            ])
            _logger.info('base_salary forzado a 0 en boleta id=%s', slip.id)
        except Exception as e:
            try:
                self.env.cr.execute(f'ROLLBACK TO SAVEPOINT {sp}')
            except Exception:
                pass
            _logger.warning('No se pudo forzar base_salary=0 en boleta %s: %s',
                            slip.id, e)

    @api.onchange('date_start')
    def _onchange_date_start(self):
        if self.date_start:
            # Snap to Monday of the selected week
            d = self.date_start
            days_since_monday = d.weekday()  # 0=Mon, 6=Sun
            if days_since_monday > 0:
                d = d - datetime.timedelta(days=days_since_monday)
                self.date_start = d
            self.date_end = d + datetime.timedelta(days=6)

    @api.onchange('date_start', 'date_end')
    def _onchange_dates(self):
        if self.date_start and self.date_end:
            self.nombramiento_ids = self.env['nombramientos.nombramiento'].search([
                ('state', 'in', ['draft', 'confirmed']),
                ('date_start', '>=', self.date_start),
                ('date_end', '<=', self.date_end),
                ('company_id', '=', self.company_id.id),
            ])

    def action_generate(self):
        self.ensure_one()
        if not self.nombramiento_ids:
            raise UserError('No hay nombramientos seleccionados.')

        # Auto-confirmar borradores
        if self.auto_confirm:
            for nom in self.nombramiento_ids.filtered(lambda n: n.state == 'draft'):
                if nom.turno_ids:
                    nom.write({'state': 'confirmed'})

        confirmed = self.nombramiento_ids.filtered(lambda n: n.state == 'confirmed')
        if not confirmed:
            raise UserError('No hay nombramientos confirmados para procesar.')

        # Obtener o crear planilla
        run = self.payroll_run_id
        if not run:
            # Buscar calendarización semanal
            cal = self.env['planilla.calendar'].search(
                [('frequency', '=', 'weekly')], limit=1)
            if not cal:
                cal = self.env['planilla.calendar'].search([], limit=1)
            if not cal:
                raise UserError(
                    'Configure una calendarización en Configuración Contable.')

            run = self.env['planilla.run.cr'].create({
                'name': (f'Nombramientos '
                         f'{self.date_start.strftime("%d/%m/%Y")} – '
                         f'{self.date_end.strftime("%d/%m/%Y")}'),
                'date_start':           self.date_start,
                'date_end':             self.date_end,
                'payroll_calendar_id':  cal.id,
                'company_id':           self.company_id.id,
            })

        # Agrupar nombramientos por empleado
        # Un empleado puede tener varios nombramientos en la semana (distintas sedes)
        from collections import defaultdict
        emp_noms = defaultdict(list)
        for nom in confirmed:
            emp_noms[nom.employee_id.id].append(nom)

        created = 0
        errors = []

        # L1: tipo de jornada para calcular factor HE correcto (Art. 139 CT)
        nom_config = self.env['nombramientos.config'].search([
            ('company_id', '=', self.company_id.id)], limit=1)
        shift_type = nom_config.default_shift_type if nom_config else 'day'

        # L3: flags de pago doble
        pay_double_holiday  = nom_config.pay_double_holiday if nom_config else True
        pay_double_rest_day = nom_config.pay_double_rest_day if nom_config else True

        for emp_id, noms in emp_noms.items():
            emp = self.env['hr.employee'].browse(emp_id)
            try:
                # Sumar todas las horas y monto de todos sus nombramientos
                # (informativo, para la nota de la boleta).
                total_hours  = sum(n.total_hours for n in noms)
                total_amount = sum(n.total_amount for n in noms)

                if total_hours <= 0:
                    continue

                # -- Determinar el modo de pago efectivo -----------------
                # Fuente de verdad: hr.employee.payroll_calculation_method
                # (campo por-empleado de planilla_cr), NO
                # nombramientos.config.payment_mode (config global que
                # nunca se conecto a un calculo real -- se mantiene solo
                # como dato informativo en la vista de calendario).
                #   'attendance' -> modo HOURLY: el empleado no tiene
                #     salario base: cobra horas trabajadas x tarifa.
                #     Ya cubierto por el bloque de ausentismo parcial de
                #     abajo (se paga exactamente lo real, nunca mas de
                #     lo planeado).
                #   'fixed' -> modo FIXED: el empleado SI tiene salario
                #     mensual normal (igual que cualquier empleado fijo
                #     de planilla_cr). El turno de Nombramientos solo
                #     sirve para detectar EXCESO sobre lo planeado --
                #     ese exceso se paga aparte como HE. El salario base
                #     de la boleta se deja que planilla_cr lo calcule de
                #     forma normal (NO se fuerza a 0).
                calc_method = getattr(emp, 'payroll_calculation_method', 'fixed')
                modo_fixed = (calc_method != 'attendance')

                horas_faltantes_total = 0.0
                detalle_faltantes = []
                # Cache de horas reales trabajadas por turno (desde
                # hr.attendance), calculado una sola vez y reutilizado
                # tanto para el ajuste de ausentismo como para el calculo
                # de exceso en modo fixed.
                horas_reales_por_turno = {}
                for nom in noms:
                    for turno in nom.turno_ids.filtered(
                            lambda t: t.hours > 0 and t.state != 'absent'):
                        real = _get_worked_hours_for_turno(self.env, emp_id, turno)
                        faltante = round(turno.hours - real, 2)
                        if faltante > 0.01:
                            horas_faltantes_total += faltante
                            detalle_faltantes.append(
                                f'{turno.date.strftime("%d/%m")}: '
                                f'planeado {turno.hours:.2f}h, '
                                f'real {real:.2f}h (falta {faltante:.2f}h)'
                            )
                        horas_reales_por_turno[turno.id] = real

                # Calcular el salario mensual equivalente (solo relevante
                # para el modo hourly -- informativo en modo fixed).
                freq = run.payroll_calendar_id.frequency or 'weekly'
                factor = C.FREQ_FACTORS.get(freq, 0.25)
                base_salary_equiv = round(total_amount / factor, 2)

                if modo_fixed:
                    nota = (f'Nombramientos (salario fijo): turnos planeados '
                             f'{total_hours:.1f}h ({freq}). El salario base '
                             f'se calcula normalmente; solo el exceso sobre '
                             f'lo planeado se paga como hora extra.')
                else:
                    nota = (f'Nombramientos: {total_hours:.1f}h x '
                            f'CRC{total_amount/total_hours:,.2f}/h = '
                            f'CRC{total_amount:,.2f} ({freq})')
                    if horas_faltantes_total > 0:
                        nota += (f' -- AJUSTADO por asistencia real: se '
                                 f'descontaron {horas_faltantes_total:.2f}h '
                                 f'no trabajadas.')

                # Verificar si ya existe una boleta para este empleado en esta planilla
                existing = self.env['planilla.payslip.cr'].search([
                    ('employee_id', '=', emp_id),
                    ('payroll_run_id', '=', run.id),
                ], limit=1)

                # Crear o reutilizar boleta
                if existing:
                    slip = existing
                    # Eliminar HE de nombramientos anteriores para esta boleta
                    self.env['planilla.overtime'].search([
                        ('payslip_id', '=', slip.id),
                        ('note', 'like', 'NOM-'),
                    ]).unlink()
                else:
                    branch = noms[0].branch_id
                    slip = self.env['planilla.payslip.cr'].create({
                        'employee_id':      emp_id,
                        'payroll_run_id':   run.id,
                        'date_from':        self.date_start,
                        'date_to':          self.date_end,
                        'branch_id':        branch.id if branch else False,
                        'notes':            nota,
                    })

                if modo_fixed:
                    # NO forzar base_salary=0: se deja que
                    # planilla.payslip.cr._compute_base_salary() calcule
                    # el salario normal del empleado (misma logica que
                    # cualquier boleta creada fuera de Nombramientos).
                    # Invalidar cache por si el compute ya corrio con un
                    # valor viejo antes de llegar aqui.
                    slip.invalidate_recordset(['base_salary'])
                else:
                    self._force_base_salary_zero(slip)

                # Alerta al supervisor si el modo es fijo y falta mas de
                # 1h en total respecto a lo planeado (nunca se descuenta
                # solo el salario base en este caso -- solo se notifica).
                if modo_fixed and horas_faltantes_total > 1.0:
                    slip.message_post(
                        body=(
                            f'ATENCION: el empleado tiene salario fijo, pero '
                            f'el reloj de asistencia registra '
                            f'{horas_faltantes_total:.2f}h menos de lo '
                            f'planeado en sus turnos de Nombramientos '
                            f'durante este periodo. No se descuenta '
                            f'automaticamente -- requiere revision manual '
                            f'del supervisor.<br/>' +
                            '<br/>'.join(detalle_faltantes)
                        ),
                        message_type='notification',
                    )

                # Agrupar turnos por fecha -- constraint unico impide
                # dos HE del mismo empleado+fecha+tipo en planilla.overtime
                # Si hay varios turnos en un dia (distintas sedes), se suman las horas.
                #
                # Modo hourly: horas_pagar = horas reales trabajadas
                #   (nunca mas que lo planeado -- el 100% del turno se
                #   paga via esta HE, ya que base_salary quedo en 0).
                # Modo fixed: horas_pagar = SOLO el exceso sobre lo
                #   planeado (el resto ya esta cubierto por el salario
                #   base normal calculado por planilla_cr).
                turnos_por_fecha = defaultdict(lambda: {'hours': 0.0, 'amount': 0.0,
                                                         'rate': 0.0, 'notes': []})
                for nom in noms:
                    for turno in nom.turno_ids.filtered(
                            lambda t: t.hours > 0 and t.state != 'absent'):
                        raw_rate = turno.hourly_rate or nom.hourly_rate or 0.0
                        fecha = turno.date
                        real = horas_reales_por_turno.get(turno.id, turno.hours)

                        if modo_fixed:
                            # Solo el exceso sobre el turno planeado.
                            horas_pagar = max(0.0, round(real - turno.hours, 2))
                        else:
                            # Nunca pagar mas de lo planeado por este
                            # ajuste -- si trabajo de mas, eso lo cubre
                            # el mixin de auto-deteccion de HE aparte
                            # (planilla_cr), no este wizard.
                            horas_pagar = min(real, turno.hours)

                        if horas_pagar <= 0:
                            continue

                        turnos_por_fecha[fecha]['hours']  += horas_pagar
                        turnos_por_fecha[fecha]['amount'] += horas_pagar * raw_rate
                        turnos_por_fecha[fecha]['rate']    = raw_rate
                        turnos_por_fecha[fecha]['notes'].append(f'{nom.name}')
                        # L4: marcar si es feriado o dia de descanso
                        if turno.state == 'holiday':
                            turnos_por_fecha[fecha]['has_holiday_turno'] = True
                        if fecha.weekday() == 6:  # domingo = dia descanso comun
                            turnos_por_fecha[fecha]['has_rest_turno'] = True


                # Crear o actualizar una HE por fecha
                he_created = 0
                for fecha, data in turnos_por_fecha.items():
                    raw_rate = data['rate']
                    is_holiday = data.get('has_holiday_turno', False)
                    is_rest_day = data.get('has_rest_turno', False)

                    if modo_fixed:
                        # Modo fixed: esto SI es una hora extra real en el
                        # sentido legal (Art. 139 CT) -- el salario base
                        # ya cubrio la jornada ordinaria, y este exceso
                        # debe llevar el recargo legal completo.
                        # planilla.overtime aplica su propio factor segun
                        # overtime_type (definido mas abajo): simple=1.5x,
                        # double=2.0x, holiday=1.0x. Para 'holiday' se
                        # necesita hourly_rate=raw_rate*2 para que el
                        # resultado final sea el doble (Art. 148 CT),
                        # ya que ese tipo aplica factor 1.0 sobre lo que
                        # se le pase.
                        if is_holiday and pay_double_holiday:
                            he_rate = raw_rate * 2
                        else:
                            he_rate = raw_rate
                    else:
                        # Modo hourly (comportamiento historico): el HE es
                        # el vehiculo de pago del 100% de las horas, asi
                        # que se neutraliza el factor de recargo para que
                        # el resultado neto sea la tarifa original -- salvo
                        # feriado/descanso, donde SI debe pagar doble.
                        base_he_rate = C.calcular_tarifa_he(raw_rate, shift_type)
                        if is_holiday and pay_double_holiday:
                            # Feriado: 200% -> pasar tarifa completa (x1.5 ya aplicado)
                            # Para obtener 2x el monto: tarifa / factor x 2
                            factor = C.FACTOR_HE_JORNADA.get(shift_type, C.FACTOR_HE_DIURNA)
                            he_rate = round(raw_rate * 2 / factor, 4)
                        elif is_rest_day and pay_double_rest_day:
                            factor = C.FACTOR_HE_JORNADA.get(shift_type, C.FACTOR_HE_DIURNA)
                            he_rate = round(raw_rate * 2 / factor, 4)
                        else:
                            he_rate = base_he_rate

                    # Determinar el tipo de HE. Modo hourly siempre usa
                    # 'simple' (el recargo real ya se neutralizo arriba
                    # con calcular_tarifa_he, salvo feriado/descanso donde
                    # se multiplico manualmente x2). Modo fixed usa el
                    # tipo real para que planilla.overtime aplique su
                    # propio factor de recargo (holiday=1.0x sobre el
                    # extra ya que el mensual cubre 1x, double=2.0x,
                    # simple=1.5x).
                    if modo_fixed:
                        if is_holiday:
                            ot_type = 'holiday'
                        elif is_rest_day:
                            ot_type = 'double'
                        else:
                            ot_type = 'simple'
                    else:
                        ot_type = 'simple'

                    # Buscar HE existente para este empleado+fecha+tipo
                    existing_he = self.env['planilla.overtime'].search([
                        ('employee_id',   '=', emp_id),
                        ('date',          '=', fecha),
                        ('overtime_type', '=', ot_type),
                    ], limit=1)

                    if existing_he:
                        existing_he.write({
                            'hours':      data['hours'],
                            'hourly_rate': he_rate,
                            'note':       ', '.join(data['notes']),
                            'payslip_id': slip.id,
                            'state':      'approved',
                        })
                    else:
                        self.env['planilla.overtime'].create({
                            'employee_id':   emp_id,
                            'date':          fecha,
                            'hours':         data['hours'],
                            'overtime_type': ot_type,
                            'hourly_rate':   he_rate,
                            'note':          ', '.join(data['notes']),
                            'state':         'approved',
                            'payslip_id':    slip.id,
                        })
                    he_created += 1

                # Vincular nombramientos a esta boleta y marcarlos
                for nom in noms:
                    nom.write({
                        'payslip_id':      slip.id,
                        'payroll_run_id':  run.id,
                        'state':           'in_payroll',
                    })

                created += 1

            except Exception as e:
                _logger.exception("Error generando boleta para %s", emp.name)
                errors.append(f'{emp.name}: {str(e)}')

        if errors:
            raise UserError(
                f'Planilla creada con {created} boletas, pero hubo errores:\n'
                + '\n'.join(errors))

        # Abrir la planilla generada
        return {
            'type':      'ir.actions.act_window',
            'name':      'Planilla Generada',
            'res_model': 'planilla.run.cr',
            'res_id':    run.id,
            'view_mode': 'form',
        }
