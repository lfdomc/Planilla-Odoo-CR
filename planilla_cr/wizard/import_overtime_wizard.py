import logging
from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ImportOvertimeWizard(models.TransientModel):
    _name = 'planilla.import.overtime.wizard'
    _description = 'Importar Horas Extras desde Asistencias'

    company_id = fields.Many2one('res.company', required=True,
                                  default=lambda self: self.env.company)
    date_from = fields.Date(string='Desde', required=True,
                             default=lambda self: fields.Date.context_today(self).replace(day=1))
    date_to   = fields.Date(string='Hasta', required=True,
                             default=lambda self: fields.Date.context_today(self))
    branch_id = fields.Many2one('planilla.branch', string='Sucursal (Opcional)')

    overtime_type = fields.Selection([
        ('simple',  'Simple (1.5x) -- dias habiles fuera de horario'),
        ('double',  'Doble (2x) -- domingos y feriados'),
        ('holiday', 'Dia Feriado'),
    ], string='Tipo de Hora Extra', default='simple', required=True)

    hours_per_day = fields.Float(
        string='Horas laborales por dia',
        default=8.0,
        help='Jornada ordinaria diaria. Las horas adicionales sobre este umbral se consideran extras.'
    )
    min_extra_minutes = fields.Integer(
        string='Minimo de minutos extra para registrar',
        default=15,
        help='Diferencias menores a este valor se ignoran (evita ruido por fichadas imprecisas).'
    )

    preview_ids = fields.One2many(
        'planilla.import.overtime.line', 'wizard_id', string='Vista Previa'
    )
    state = fields.Selection([
        ('draft', 'Configurar'),
        ('preview', 'Vista Previa'),
    ], default='draft')

    def action_preview(self):
        """Calcula las horas extras desde hr.attendance y muestra preview."""
        self.ensure_one()
        # Verificar que el modulo hr.attendance este instalado
        if 'hr.attendance' not in self.env:
            raise UserError(
                'El modulo de Asistencias de Odoo no esta instalado. '
                'Instale "Asistencias" (hr_attendance) para usar esta funcion.'
            )

        # Limpiar preview anterior
        self.preview_ids.unlink()

        domain_emps = [
            ('active', '=', True),
            ('company_id', '=', self.company_id.id),
        ]
        if self.branch_id:
            domain_emps.append(('branch_id', '=', self.branch_id.id))
        employees = self.env['hr.employee'].search(domain_emps)

        # Umbral en horas
        threshold   = self.hours_per_day
        min_hours   = self.min_extra_minutes / 60.0

        # Obtener feriados del periodo UNA sola vez (antes eran identicos
        # en cada iteracion del loop de empleados de mas abajo).
        holidays = self.env['planilla.public.holiday'].get_holidays_in_range(
            self.date_from, self.date_to, self.company_id.id
        )

        # Traer TODAS las asistencias del periodo para todos los empleados
        # en una sola query, en vez de una query por empleado.
        emp_ids = employees.ids
        dt_from = fields.Datetime.from_string(f'{self.date_from} 00:00:00')
        dt_to   = fields.Datetime.from_string(f'{self.date_to} 23:59:59')
        all_attendances = self.env['hr.attendance'].search([
            ('employee_id', 'in', emp_ids),
            ('check_in',  '>=', dt_from),
            ('check_out', '<=', dt_to),
        ]) if emp_ids else self.env['hr.attendance']

        # Agrupar asistencias por empleado en memoria
        att_by_emp = {}
        for att in all_attendances:
            att_by_emp.setdefault(att.employee_id.id, []).append(att)

        # Precargar en una sola query todos los pares empleado+fecha que
        # ya tienen HE registrada en el periodo, para no hacer un
        # search() por cada linea candidata dentro del loop de abajo.
        existing_ot = self.env['planilla.overtime'].search([
            ('employee_id', 'in', emp_ids),
            ('date', '>=', self.date_from),
            ('date', '<=', self.date_to),
            ('state', 'not in', ('cancelled',)),
        ]) if emp_ids else self.env['planilla.overtime']
        existing_set = {(o.employee_id.id, o.date) for o in existing_ot}

        lines = []
        for emp in employees:
            attendances = att_by_emp.get(emp.id)
            if not attendances:
                continue

            # Agrupar por dia
            by_day = {}
            for att in attendances:
                day = att.check_in.date()
                by_day.setdefault(day, 0.0)
                if att.check_out:
                    worked = (att.check_out - att.check_in).total_seconds() / 3600.0
                    by_day[day] += worked

            for day, worked in by_day.items():
                extra = worked - threshold
                if extra >= min_hours:
                    # Auto-detectar si es feriado o domingo
                    auto_type = self.overtime_type
                    if day in holidays or day.weekday() == 6:  # 6 = domingo
                        auto_type = 'holiday'
                    elif day.weekday() == 5:  # 5 = sabado
                        auto_type = 'double'
                    lines.append({
                        'wizard_id': self.id,
                        'employee_id': emp.id,
                        'date': day,
                        'hours_worked': round(worked, 2),
                        'hours_extra': round(extra, 2),
                        'overtime_type': auto_type,
                        'already_imported': (emp.id, day) in existing_set,
                    })

        if not lines:
            raise UserError(
                f'No se encontraron horas extras en el periodo {self.date_from} al {self.date_to} '
                f'para los empleados seleccionados (umbral: {threshold}h/dia).'
            )

        self.env['planilla.import.overtime.line'].create(lines)
        self.state = 'preview'
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _already_has_overtime(self, employee_id, date):
        """Verifica si ya existe un registro de horas extras para este empleado y fecha."""
        return bool(self.env['planilla.overtime'].search([
            ('employee_id', '=', employee_id),
            ('date', '=', date),
            ('state', 'not in', ('cancelled',)),
        ], limit=1))

    def action_import(self):
        """Crea los registros de horas extras para las lineas seleccionadas."""
        self.ensure_one()
        to_import = self.preview_ids.filtered(lambda l: l.selected and not l.already_imported)
        if not to_import:
            raise UserError('No hay lineas seleccionadas para importar (o todas ya fueron importadas).')

        created = self.env['planilla.overtime']
        for line in to_import:
            ot = self.env['planilla.overtime'].create({
                'employee_id':  line.employee_id.id,
                'date':         line.date,
                'hours':        line.hours_extra,
                'overtime_type': line.overtime_type,
                'source':       'attendance',
            })
            ot.action_approve()
            created |= ot

        return {
            'type': 'ir.actions.act_window',
            'name': f'Horas Extras Importadas ({len(created)})',
            'res_model': 'planilla.overtime',
            'view_mode': 'list,form',
            'domain': [('id', 'in', created.ids)],
        }


class ImportOvertimeLine(models.TransientModel):
    _name  = 'planilla.import.overtime.line'
    _description = 'Linea de preview de importacion de horas extras'

    wizard_id      = fields.Many2one('planilla.import.overtime.wizard', ondelete='cascade')
    selected       = fields.Boolean(string='Importar', default=True)
    employee_id    = fields.Many2one('hr.employee', string='Empleado', readonly=True)
    date           = fields.Date(string='Fecha', readonly=True)
    hours_worked   = fields.Float(string='Horas Trabajadas', readonly=True, digits=(5, 2))
    hours_extra    = fields.Float(string='Horas Extra', readonly=True, digits=(5, 2))
    overtime_type  = fields.Selection([
        ('simple',  'Simple (1.5x)'),
        ('double',  'Doble (2x)'),
        ('holiday', 'Dia Feriado'),
    ], string='Tipo', readonly=True)
    already_imported = fields.Boolean(string='Ya importado', readonly=True)
