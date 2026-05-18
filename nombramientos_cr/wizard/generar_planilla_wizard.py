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
                total_hours  = sum(n.total_hours for n in noms)
                total_amount = sum(n.total_amount for n in noms)

                if total_hours <= 0:
                    continue

                # Calcular el salario mensual equivalente para que el sistema
                # aplique el factor de frecuencia y llegue al monto correcto.
                # Ej: weekly factor=0.25 → base_salary = total_amount / 0.25 = total_amount * 4
                # Así: base_salary * 0.25 = total_amount ✅
                freq = run.payroll_calendar_id.frequency or 'weekly'
                factor = C.FREQ_FACTORS.get(freq, 0.25)
                # Salario mensual equivalente = monto_período / factor
                base_salary_equiv = round(total_amount / factor, 2)

                nota = (f'Nombramientos: {total_hours:.1f}h × '
                        f'₡{total_amount/total_hours:,.2f}/h = '
                        f'₡{total_amount:,.2f} ({freq})')

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

                self._force_base_salary_zero(slip)

                # Agrupar turnos por fecha — constraint único impide
                # dos HE del mismo empleado+fecha+tipo en planilla.overtime
                # Si hay varios turnos en un día (distintas sedes), se suman las horas
                turnos_por_fecha = defaultdict(lambda: {'hours': 0.0, 'amount': 0.0,
                                                         'rate': 0.0, 'notes': []})
                for nom in noms:
                    for turno in nom.turno_ids.filtered(
                            lambda t: t.hours > 0 and t.state != 'absent'):
                        raw_rate = turno.hourly_rate or nom.hourly_rate or 0.0
                        fecha = turno.date
                        turnos_por_fecha[fecha]['hours']  += turno.hours
                        turnos_por_fecha[fecha]['amount'] += turno.hours * raw_rate
                        turnos_por_fecha[fecha]['rate']    = raw_rate
                        turnos_por_fecha[fecha]['notes'].append(f'{nom.name}')
                        # L4: marcar si es feriado o día de descanso
                        if turno.state == 'holiday':
                            turnos_por_fecha[fecha]['has_holiday_turno'] = True
                        if fecha.weekday() == 6:  # domingo = día descanso común
                            turnos_por_fecha[fecha]['has_rest_turno'] = True

                # Crear o actualizar una HE por fecha
                he_created = 0
                for fecha, data in turnos_por_fecha.items():
                    raw_rate = data['rate']
                    # L1: factor base según tipo de jornada (Art. 139 CT)
                    base_he_rate = C.calcular_tarifa_he(raw_rate, shift_type)
                    # L4: doble pago en feriados y días de descanso
                    is_holiday = data.get('has_holiday_turno', False)
                    is_rest_day = data.get('has_rest_turno', False)
                    if is_holiday and pay_double_holiday:
                        # Feriado: 200% → pasar tarifa completa (×1.5 ya aplicado)
                        # Para obtener 2× el monto: tarifa / factor × 2
                        factor = C.FACTOR_HE_JORNADA.get(shift_type, C.FACTOR_HE_DIURNA)
                        he_rate = round(raw_rate * 2 / factor, 4)
                    elif is_rest_day and pay_double_rest_day:
                        factor = C.FACTOR_HE_JORNADA.get(shift_type, C.FACTOR_HE_DIURNA)
                        he_rate = round(raw_rate * 2 / factor, 4)
                    else:
                        he_rate = base_he_rate

                    # Buscar HE existente para este empleado+fecha+tipo
                    existing_he = self.env['planilla.overtime'].search([
                        ('employee_id',   '=', emp_id),
                        ('date',          '=', fecha),
                        ('overtime_type', '=', 'simple'),
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
                            'overtime_type': 'simple',
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
