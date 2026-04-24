from odoo import models, fields, api
from datetime import date as _date


class VacationInitialBalanceWizard(models.TransientModel):
    _name = 'planilla.vacation.initial.balance.wizard'
    _description = 'Corrector de Saldo Inicial de Vacaciones'

    company_id = fields.Many2one(
        'res.company', required=True,
        default=lambda self: self.env.company
    )
    preview_line_ids = fields.One2many(
        'planilla.vacation.initial.balance.line', 'wizard_id',
        string='Empleados a corregir'
    )
    corrected_count = fields.Integer(string='Empleados corregidos', readonly=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        return res

    def action_preview(self):
        """Calcula el saldo inicial correcto para todos los empleados
        que tienen vacation_initial_balance_date pero saldo = 0."""
        self.ensure_one()
        # Limpiar lineas anteriores
        self.preview_line_ids.unlink()

        employees = self.env['hr.employee'].search([
            ('company_id', '=', self.company_id.id),
            ('active', '=', True),
            ('vacation_initial_balance_date', '!=', False),
        ])

        lines = []
        today = _date.today()
        for emp in employees:
            if not emp.entry_date or not emp.vacation_initial_balance_date:
                continue

            cutoff = emp.vacation_initial_balance_date
            current_balance = emp.vacation_initial_balance or 0.0

            # Calcular el saldo teorico correcto en la fecha de corte
            # Art. 153 CT: 12 dias habiles / 50 semanas laboradas
            days_entry_to_cutoff = max((cutoff - emp.entry_date).days, 0)
            teorico_al_corte = (days_entry_to_cutoff / 7.0 / 50.0) * 12.0
            correct_balance = int(teorico_al_corte)

            # Solo incluir si el saldo actual difiere del calculado
            if abs(current_balance - correct_balance) >= 1:
                lines.append({
                    'wizard_id':      self.id,
                    'employee_id':    emp.id,
                    'entry_date':     emp.entry_date,
                    'cutoff_date':    cutoff,
                    'current_balance': current_balance,
                    'correct_balance': correct_balance,
                    'apply':          True,
                })

        if lines:
            self.env['planilla.vacation.initial.balance.line'].create(lines)

        return {
            'type':     'ir.actions.act_window',
            'res_model': 'planilla.vacation.initial.balance.wizard',
            'res_id':   self.id,
            'view_mode': 'form',
            'target':   'new',
        }

    def action_apply(self):
        """Aplica las correcciones marcadas."""
        self.ensure_one()
        count = 0
        for line in self.preview_line_ids.filtered('apply'):
            line.employee_id.write({
                'vacation_initial_balance': line.correct_balance,
            })
            count += 1

        # Forzar recompute de saldos
        employees = self.preview_line_ids.filtered('apply').mapped('employee_id')
        employees._compute_vacation_balance()
        employees.flush_recordset()

        self.corrected_count = count
        return {
            'type':     'ir.actions.act_window',
            'res_model': 'planilla.vacation.initial.balance.wizard',
            'res_id':   self.id,
            'view_mode': 'form',
            'target':   'new',
        }


class VacationInitialBalanceLine(models.TransientModel):
    _name = 'planilla.vacation.initial.balance.line'
    _description = 'Linea de corrector de saldo inicial'

    wizard_id      = fields.Many2one('planilla.vacation.initial.balance.wizard')
    employee_id    = fields.Many2one('hr.employee', string='Empleado', readonly=True)
    entry_date     = fields.Date(string='Fecha Ingreso', readonly=True)
    cutoff_date    = fields.Date(string='Fecha de Corte', readonly=True)
    current_balance = fields.Float(string='Saldo Actual', readonly=True, digits=(6, 1))
    correct_balance = fields.Float(string='Saldo Correcto (Art.153 CT)', readonly=True, digits=(6, 1))
    difference      = fields.Float(
        string='Diferencia', compute='_compute_difference',
        digits=(6, 1), readonly=True
    )
    apply          = fields.Boolean(string='Aplicar', default=True)

    @api.depends('current_balance', 'correct_balance')
    def _compute_difference(self):
        for rec in self:
            rec.difference = rec.correct_balance - rec.current_balance
