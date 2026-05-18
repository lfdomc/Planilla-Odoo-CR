import logging
import datetime

from odoo import models, fields, api
from . import constants as C

_logger = logging.getLogger(__name__)


class CalendarioNombramientos(models.Model):
    """
    Controlador para la vista de calendario semanal de nombramientos.
    Provee los datos al HTML embebido vía JSON.
    """
    _name = 'nombramientos.calendario'
    _description = 'Calendario Semanal de Nombramientos'

    @api.model
    def get_week_data(self, week_start_str, week_end_str=None):
        # Retorna turnos del rango solicitado (semana, quincena o mes)
        try:
            week_start = datetime.date.fromisoformat(week_start_str)
        except Exception:
            today = datetime.date.today()
            week_start = today - datetime.timedelta(days=today.weekday())

        if week_end_str:
            try:
                week_end = datetime.date.fromisoformat(week_end_str)
            except Exception:
                week_end = week_start + datetime.timedelta(days=6)
        else:
            week_end = week_start + datetime.timedelta(days=6)

        turnos = self.env['nombramientos.turno'].search([
            ('date', '>=', week_start),
            ('date', '<=', week_end),
            ('nombramiento_id.state', 'in', ['draft', 'confirmed', 'in_payroll', 'paid']),
        ])

        result = []
        for t in turnos:
            emp = t.nombramiento_id.employee_id
            sede_name = (
                (t.branch_override_id.name if t.branch_override_id else None) or
                (t.nombramiento_id.branch_id.name if t.nombramiento_id.branch_id else None) or
                'Sin sede asignada'
            )
            result.append({
                'id':         t.id,
                'nom_id':     t.nombramiento_id.id,
                'emp_id':     emp.id,
                'emp_name':   emp.name or '',
                'turno_name': t.turno_name or '',
                'sede_turno_id': t.sede_turno_id.id if t.sede_turno_id else False,
                'sede':       sede_name,
                'date':       t.date.isoformat(),
                'hour_start': t.hour_start,
                'hour_end':   t.hour_end,
                'hours':      t.hours,
                'state':      t.state,
                'rate':       t.hourly_rate or 0,
                'amount':     t.amount or 0,
                'notes':      t.notes or '',
            })

        # Empleados activos de la empresa
        employees = self.env['hr.employee'].search([
            ('company_id', '=', self.env.company.id),
            ('active', '=', True),
        ], order='name')
        emp_list = [{'id': e.id, 'name': e.name} for e in employees]

        # Sucursales con sus turnos configurados
        branch_list = []
        if 'planilla.branch' in self.env:
            branches = self.env['planilla.branch'].search([], order='name')
            branch_list = []
            for b in branches:
                turnos_sede = []
                if hasattr(b, 'nom_turno_ids'):
                    for t in b.nom_turno_ids.filtered(lambda x: x.active):
                        turnos_sede.append({
                            'id':      t.id,
                            'name':    t.display_name or t.name,
                            'h_start': t.hour_start,
                            'h_end':   t.hour_end,
                        })
                branch_list.append({
                    'id':     b.id,
                    'name':   b.name,
                    'turnos': turnos_sede,
                })

        if not branch_list:
            sedes_en_turnos = list({t['sede'] for t in result if t.get('sede')})
            branch_list = [{'id': i, 'name': s, 'turnos': []}
                           for i, s in enumerate(sorted(sedes_en_turnos), 1)]

        if not branch_list:
            branch_list = [{'id': 0, 'name': 'Sin sede asignada', 'turnos': []}]

        # Plantillas de turno — nombre descriptivo con horario
        def fmt_hour(h):
            hh = int(h % 24)
            mm = int(round((h % 1) * 60))
            ampm = 'am' if hh < 12 else 'pm'
            hh12 = hh % 12 or 12
            return f'{hh12}:{mm:02d}{ampm}'

        type_labels = {'day': 'Diurno', 'mixed': 'Mixto', 'night': 'Nocturno'}
        templates = self.env['nombramientos.shift.template'].search(
            [('active', '=', True)], order='sequence')
        template_list = [{
            'id':      t.id,
            'name':    (f'{t.name}  ·  {fmt_hour(t.hour_start)}'
                        f' – {fmt_hour(t.hour_end)}'
                        f'  ({type_labels.get(t.shift_type, "")})'),
            'h_start': t.hour_start,
            'h_end':   t.hour_end,
        } for t in templates]

        # Configuración de la empresa
        cfg = self.env['nombramientos.config'].get_config()

        return {
            'turnos':     result,
            'employees':  emp_list,
            'branches':   branch_list,
            'templates':  template_list,
            'config': {
                'payment_mode':      cfg.payment_mode,
                'frequency':         cfg.payment_frequency,
                'shift_type':        cfg.default_shift_type,
                'max_daily_hours':   cfg.max_daily_hours(),
                'apply_lunch':       cfg.apply_lunch_break,
                'lunch_minutes':     cfg.lunch_break_minutes,
                'auto_overtime':     cfg.auto_overtime,
            },
            'week_start': week_start.isoformat(),
            'week_end':   week_end.isoformat(),
        }

    @api.model
    def save_turnos_batch(self, vals_list):
        # Crea todos los turnos del multi-día en UN solo nombramiento
        if not vals_list:
            return {'ok': False, 'error': 'Sin turnos'}
        first     = vals_list[0]
        emp_id    = first.get('emp_id')
        branch_id = first.get('branch_id')
        if not emp_id:
            return {'ok': False, 'error': 'Empleado requerido'}

        date = datetime.date.fromisoformat(first['date'])
        week_start = date - datetime.timedelta(days=date.weekday())
        week_end   = week_start + datetime.timedelta(days=6)

        # Buscar nombramiento existente para esta semana
        nom = self.env['nombramientos.nombramiento'].search([
            ('employee_id', '=', emp_id),
            ('branch_id',   '=', branch_id),
            ('date_start',  '=', week_start),
            ('state', 'not in', ['cancelled']),
        ], limit=1)

        if not nom:
            base = C.leer_base_salary(self.env.cr, emp_id)
            rate = C.calcular_tarifa_hora(base) or (first.get('rate') or 0)
            nom_vals = {
                'employee_id': emp_id,
                'date_start':  week_start,
                'date_end':    week_end,
                'hourly_rate': rate,
                'company_id':  self.env.company.id,
            }
            if branch_id and int(branch_id) > 0:
                nom_vals['branch_id'] = int(branch_id)
            nom = self.env['nombramientos.nombramiento'].create(nom_vals)

        created_ids = []
        for v in vals_list:
            fecha = datetime.date.fromisoformat(v['date'])
            h_start = v.get('hour_start', 8.0)
            h_end   = v.get('hour_end', 17.0)

            # Verificar traslape ANTES de crear
            emp_id = int(v.get('emp_id', 0))
            if emp_id and v.get('state', 'present') != 'absent':
                solapados = self.env['nombramientos.turno'].search([
                    ('date', '=', fecha),
                    ('state', '!=', 'absent'),
                    ('nombramiento_id.employee_id', '=', emp_id),
                ])
                for otro in solapados:
                    if h_start < otro.hour_end and h_end > otro.hour_start:
                        emp = self.env['hr.employee'].browse(emp_id)
                        sede_nueva = self.env['planilla.branch'].browse(
                            int(v.get('branch_id', 0))).name if v.get('branch_id') else 'nueva sede'
                        sede_otro = otro.nombramiento_id.branch_id.name or 'otra sede'

                        def fmt(h):
                            hh=int(h%24); mm=int(round((h%1)*60))
                            ampm='am' if hh<12 else 'pm'; hh12=hh%12 or 12
                            return f'{hh12}:{mm:02d}{ampm}'

                        from odoo.exceptions import ValidationError
                        raise ValidationError(
                            f'{emp.name} ya tiene turno el {fecha.strftime("%d/%m/%Y")} '
                            f'de {fmt(otro.hour_start)} a {fmt(otro.hour_end)} '
                            f'en "{sede_otro}". No puede estar también en "{sede_nueva}" '
                            f'de {fmt(h_start)} a {fmt(h_end)} al mismo tiempo.'
                        )

            turno_vals = {
                'nombramiento_id': nom.id,
                'date':            fecha,
                'hour_start':      h_start,
                'hour_end':        h_end,
                'state':           v.get('state', 'present'),
                'hourly_rate':     nom.hourly_rate,
                'notes':           v.get('notes', ''),
            }
            if v.get('sede_turno_id'):
                try:
                    turno_vals['sede_turno_id'] = int(v['sede_turno_id'])
                except (ValueError, TypeError):
                    pass
            turno = self.env['nombramientos.turno'].create(turno_vals)
            created_ids.append(turno.id)
        return {'ok': True, 'nom_id': nom.id, 'turno_ids': created_ids}

    @api.model
    def get_employee_rate(self, emp_id):
        if not emp_id or not isinstance(emp_id, int):
            return {'rate': 0}
        base = C.leer_base_salary(self.env.cr, emp_id)
        if not base:
            try:
                emp = self.env['hr.employee'].browse(emp_id).exists()
                if emp and hasattr(emp, 'contract_id') and emp.contract_id:
                    base = float(emp.contract_id.wage or 0)
            except Exception:
                pass
        return {'rate': C.calcular_tarifa_hora(base), 'base': base}

    @api.model
    def save_turno(self, vals):
        # Q1: Delegado a save_turnos_batch para evitar duplicación de lógica.
        turno_id = vals.get('id')
        if turno_id:
            turno = self.env['nombramientos.turno'].browse(turno_id).exists()
            if turno:
                turno.write({
                    'hour_start':  vals.get('hour_start', turno.hour_start),
                    'hour_end':    vals.get('hour_end', turno.hour_end),
                    'state':       vals.get('state', turno.state),
                    'hourly_rate': vals.get('rate', turno.hourly_rate),
                    'notes':       vals.get('notes', turno.notes or ''),
                })
                return {'ok': True, 'id': turno.id}
            return {'ok': False, 'error': 'Turno no encontrado'}
        return self.save_turnos_batch([vals])

    @api.model
    def save_turnos_batch(self, vals_list):
        # Crea todos los turnos del multi-día en UN solo nombramiento
        if not vals_list:
            return {'ok': False, 'error': 'Sin turnos'}
        first     = vals_list[0]
        emp_id    = first.get('emp_id')
        branch_id = first.get('branch_id')
        if not emp_id:
            return {'ok': False, 'error': 'Empleado requerido'}

        date = datetime.date.fromisoformat(first['date'])
        week_start = date - datetime.timedelta(days=date.weekday())
        week_end   = week_start + datetime.timedelta(days=6)

        # Buscar nombramiento existente para esta semana
        nom = self.env['nombramientos.nombramiento'].search([
            ('employee_id', '=', emp_id),
            ('branch_id',   '=', branch_id),
            ('date_start',  '=', week_start),
            ('state', 'not in', ['cancelled']),
        ], limit=1)

        if not nom:
            base = C.leer_base_salary(self.env.cr, emp_id)
            rate = C.calcular_tarifa_hora(base) or (first.get('rate') or 0)
            nom_vals = {
                'employee_id': emp_id,
                'date_start':  week_start,
                'date_end':    week_end,
                'hourly_rate': rate,
                'company_id':  self.env.company.id,
            }
            if branch_id and int(branch_id) > 0:
                nom_vals['branch_id'] = int(branch_id)
            nom = self.env['nombramientos.nombramiento'].create(nom_vals)

        created_ids = []
        for v in vals_list:
            fecha = datetime.date.fromisoformat(v['date'])
            h_start = v.get('hour_start', 8.0)
            h_end   = v.get('hour_end', 17.0)

            # Verificar traslape ANTES de crear
            emp_id = int(v.get('emp_id', 0))
            if emp_id and v.get('state', 'present') != 'absent':
                solapados = self.env['nombramientos.turno'].search([
                    ('date', '=', fecha),
                    ('state', '!=', 'absent'),
                    ('nombramiento_id.employee_id', '=', emp_id),
                ])
                for otro in solapados:
                    if h_start < otro.hour_end and h_end > otro.hour_start:
                        emp = self.env['hr.employee'].browse(emp_id)
                        sede_nueva = self.env['planilla.branch'].browse(
                            int(v.get('branch_id', 0))).name if v.get('branch_id') else 'nueva sede'
                        sede_otro = otro.nombramiento_id.branch_id.name or 'otra sede'

                        def fmt(h):
                            hh=int(h%24); mm=int(round((h%1)*60))
                            ampm='am' if hh<12 else 'pm'; hh12=hh%12 or 12
                            return f'{hh12}:{mm:02d}{ampm}'

                        from odoo.exceptions import ValidationError
                        raise ValidationError(
                            f'{emp.name} ya tiene turno el {fecha.strftime("%d/%m/%Y")} '
                            f'de {fmt(otro.hour_start)} a {fmt(otro.hour_end)} '
                            f'en "{sede_otro}". No puede estar también en "{sede_nueva}" '
                            f'de {fmt(h_start)} a {fmt(h_end)} al mismo tiempo.'
                        )

            turno_vals = {
                'nombramiento_id': nom.id,
                'date':            fecha,
                'hour_start':      h_start,
                'hour_end':        h_end,
                'state':           v.get('state', 'present'),
                'hourly_rate':     nom.hourly_rate,
                'notes':           v.get('notes', ''),
            }
            if v.get('sede_turno_id'):
                try:
                    turno_vals['sede_turno_id'] = int(v['sede_turno_id'])
                except (ValueError, TypeError):
                    pass
            turno = self.env['nombramientos.turno'].create(turno_vals)
            created_ids.append(turno.id)
        return {'ok': True, 'nom_id': nom.id, 'turno_ids': created_ids}

    @api.model
    def get_employee_rate(self, emp_id):
        if not emp_id or not isinstance(emp_id, int):
            return {'rate': 0}
        base = C.leer_base_salary(self.env.cr, emp_id)
        if not base:
            try:
                emp = self.env['hr.employee'].browse(emp_id).exists()
                if emp and hasattr(emp, 'contract_id') and emp.contract_id:
                    base = float(emp.contract_id.wage or 0)
            except Exception:
                pass
        return {'rate': C.calcular_tarifa_hora(base), 'base': base}

    @api.model
    def save_turno(self, vals):
        """Crea o actualiza un turno desde el calendario."""
        turno_id = vals.get('id')

        if turno_id:
            # Actualizar existente
            turno = self.env['nombramientos.turno'].browse(turno_id).exists()
            if turno:
                turno.write({
                    'hour_start': vals.get('hour_start', 8.0),
                    'hour_end':   vals.get('hour_end', 17.0),
                    'state':      vals.get('state', 'present'),
                    'hourly_rate': vals.get('rate', 0),
                    'notes':      vals.get('notes', ''),
                })
                return {'ok': True, 'id': turno.id}
        else:
            # Crear nuevo turno — buscar o crear nombramiento
            emp_id    = vals.get('emp_id')
            branch_id = vals.get('branch_id')
            date_str  = vals.get('date')

            if not emp_id or not date_str:
                return {'ok': False, 'error': 'Empleado y fecha requeridos'}

            date = datetime.date.fromisoformat(date_str)
            week_start = date - datetime.timedelta(days=date.weekday())
            week_end   = week_start + datetime.timedelta(days=6)

            # Buscar nombramiento existente para este empleado/sede en esta semana
            nom = self.env['nombramientos.nombramiento'].search([
                ('employee_id', '=', emp_id),
                ('branch_id', '=', branch_id),
                ('date_start', '=', week_start),
                ('state', 'not in', ['cancelled']),
            ], limit=1)

            if not nom:
                emp = self.env['hr.employee'].browse(emp_id)
                # Leer base_salary del módulo planilla_cr directamente de BD
                # para evitar cache stale
                self.env.cr.execute(
                    "SELECT base_salary FROM hr_employee WHERE id = %s", (emp_id,))
                row = self.env.cr.fetchone()
                base = (row[0] if row and row[0] else 0)
                # Tarifa por hora = salario mensual / 30 días / 8 horas
                rate = C.calcular_tarifa_hora(base) or (vals.get('rate') or 0)

                nom_vals = {
                    'employee_id':  emp_id,
                    'date_start':   week_start,
                    'date_end':     week_end,
                    'hourly_rate':  rate,
                    'company_id':   self.env.company.id,
                }
                if branch_id and branch_id > 0:
                    nom_vals['branch_id'] = branch_id
                nom = self.env['nombramientos.nombramiento'].create(nom_vals)

            # Usar la tarifa calculada o la ingresada manualmente
            final_rate = vals.get('rate') or nom.hourly_rate or rate
            # Crear turno
            turno = self.env['nombramientos.turno'].create({
                'nombramiento_id': nom.id,
                'date':            date,
                'hour_start':      vals.get('hour_start', 8.0),
                'hour_end':        vals.get('hour_end', 17.0),
                'state':           vals.get('state', 'present'),
                'hourly_rate':     final_rate,
                'notes':           vals.get('notes', ''),
            })
            return {'ok': True, 'id': turno.id, 'nom_id': nom.id}

        return {'ok': False, 'error': 'No se pudo guardar'}

    @api.model
    def delete_turno(self, turno_id):
        """Elimina un turno."""
        turno = self.env['nombramientos.turno'].browse(turno_id).exists()
        if turno:
            nom = turno.nombramiento_id
            turno.unlink()
            # Si el nombramiento quedó sin turnos, eliminarlo también
            if nom.exists() and not nom.turno_ids:
                nom.unlink()
            return {'ok': True}
        return {'ok': False, 'error': 'Turno no encontrado'}
