from odoo import models, fields, api
import datetime


class CalendarioSemanalWizard(models.TransientModel):
    _name = 'nombramientos.calendario.wizard'
    _description = 'Calendario Semanal de Nombramientos'

    date_start = fields.Date(
        string='Inicio de Semana (Lunes)',
        required=True,
        default=lambda self: _monday_of_week(fields.Date.context_today(self)),
    )
    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company,
    )
    branch_ids = fields.Many2many(
        'planilla.branch', string='Sucursales',
        help='Dejar vacío para mostrar todas.',
    )

    @api.onchange('date_start')
    def _onchange_date_start(self):
        # Snap to Monday
        if self.date_start:
            d = self.date_start
            if d.weekday() != 0:
                self.date_start = d - datetime.timedelta(days=d.weekday())

    def action_print(self):
        self.ensure_one()
        return self.env.ref(
            'nombramientos_cr.action_report_calendario_semanal'
        ).report_action(self)

    def _get_calendar_data(self):
        """
        Retorna la estructura de datos para el calendario:
        {
          branch: {
            'name': 'Universal',
            'days': {
              0: [{'employee': 'María José', 'hour_start': 9, 'hour_end': 17, 'color': '#...'}],
              1: [...],  # martes
              ...
            }
          }
        }
        """
        self.ensure_one()
        date_start = self.date_start
        date_end   = date_start + datetime.timedelta(days=6)

        domain = [
            ('nombramiento_id.state', 'in', ['confirmed', 'in_payroll', 'paid']),
            ('date', '>=', date_start),
            ('date', '<=', date_end),
            ('nombramiento_id.company_id', '=', self.company_id.id),
        ]
        if self.branch_ids:
            domain.append(('effective_branch_id', 'in', self.branch_ids.ids))

        turnos = self.env['nombramientos.turno'].search(domain, order='effective_branch_id, date, hour_start')

        # Color palette for employees (cycling)
        COLORS = [
            '#4472C4', '#ED7D31', '#A9D18E', '#FF0000',
            '#FFC000', '#70AD47', '#9DC3E6', '#F4B183',
            '#C9C9C9', '#FF7C80', '#57A644', '#8EA9C1',
        ]
        emp_colors = {}
        color_idx  = 0

        def get_color(emp_id):
            nonlocal color_idx
            if emp_id not in emp_colors:
                emp_colors[emp_id] = COLORS[color_idx % len(COLORS)]
                color_idx += 1
            return emp_colors[emp_id]

        # Build data structure grouped by branch
        from collections import defaultdict
        calendar = {}

        for turno in turnos:
            branch = turno.effective_branch_id
            branch_key = branch.id if branch else 0
            branch_name = branch.name if branch else 'Sin Sucursal'

            if branch_key not in calendar:
                calendar[branch_key] = {
                    'name':  branch_name,
                    'days':  {i: [] for i in range(7)},  # 0=lunes...6=domingo
                }

            day_idx = turno.date.weekday()  # 0=lunes
            emp = turno.nombramiento_id.employee_id

            def fmt_time(h):
                hh = int(h); mm = int(round((h - hh) * 60))
                return f'{hh}:{mm:02d}'

            entry = {
                'employee':   emp.name,
                'hour_start': turno.hour_start,
                'hour_end':   turno.hour_end,
                'time_range': f'{fmt_time(turno.hour_start)} – {fmt_time(turno.hour_end)}',
                'hours':      turno.hours,
                'state':      turno.state,
                'color':      get_color(emp.id),
                'text_color': _contrast_color(get_color(emp.id)),
            }
            calendar[branch_key]['days'][day_idx].append(entry)

        # Date labels for header
        day_names = ['Lunes', 'Martes', 'Miércoles', 'Jueves',
                     'Viernes', 'Sábado', 'Domingo']
        headers = []
        for i in range(7):
            d = date_start + datetime.timedelta(days=i)
            headers.append({
                'name': day_names[i],
                'date': d.strftime('%d/%m'),
            })

        return {
            'calendar':   list(calendar.values()),
            'headers':    headers,
            'date_start': date_start.strftime('%d de %B de %Y'),
            'date_end':   date_end.strftime('%d de %B de %Y'),
            'company':    self.company_id.name,
        }


def _monday_of_week(d):
    return d - datetime.timedelta(days=d.weekday())


def _contrast_color(hex_color):
    """Return black or white text based on background luminance."""
    hex_color = hex_color.lstrip('#')
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return '#000000' if luminance > 0.5 else '#FFFFFF'
