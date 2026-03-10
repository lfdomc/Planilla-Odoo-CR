from odoo import models, fields, api
from odoo.exceptions import UserError


class SalaryIncreaseWizard(models.TransientModel):
    _name = 'planilla.salary.increase.wizard'
    _description = 'Incremento Salarial Masivo'

    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company
    )
    increase_type = fields.Selection([
        ('percent', 'Porcentaje (%)'),
        ('fixed',   'Monto Fijo (₡)'),
    ], string='Tipo de Incremento', required=True, default='percent')

    percent = fields.Float(string='Porcentaje (%)', digits=(5, 2))
    fixed_amount = fields.Monetary(
        string='Monto Fijo (₡)', currency_field='currency_id'
    )
    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.ref('base.CRC')
    )

    # ── Filtros ────────────────────────────────────────────────
    filter_type = fields.Selection([
        ('all',        'Todos los empleados activos'),
        ('department', 'Por Departamento'),
        ('branch',     'Por Sucursal'),
        ('employee',   'Empleados específicos'),
    ], string='Aplicar a', required=True, default='all')

    department_id = fields.Many2one('hr.department', string='Departamento')
    branch_id = fields.Many2one('planilla.branch', string='Sucursal')
    employee_ids = fields.Many2many(
        'hr.employee', string='Empleados',
        domain=[('active', '=', True)]
    )

    effective_date = fields.Date(
        string='Fecha Efectiva', required=True, default=fields.Date.today
    )
    note = fields.Char(string='Motivo / Referencia', help='Ej: Aumento salario mínimo 2026')

    # ── Preview ────────────────────────────────────────────────
    preview_line_ids = fields.One2many(
        'planilla.salary.increase.preview', 'wizard_id', string='Vista Previa'
    )

    @api.onchange('filter_type', 'department_id', 'branch_id', 'employee_ids',
                  'increase_type', 'percent', 'fixed_amount')
    def _onchange_compute_preview(self):
        self.preview_line_ids = [(5, 0, 0)]
        employees = self._get_employees()
        lines = []
        for emp in employees:
            current = emp.base_salary or 0
            new_salary = self._calc_new_salary(current)
            lines.append((0, 0, {
                'employee_id': emp.id,
                'department_id': emp.department_id.id if emp.department_id else False,
                'current_salary': current,
                'new_salary': new_salary,
                'difference': new_salary - current,
            }))
        self.preview_line_ids = lines

    def _get_employees(self):
        domain = [('active', '=', True), ('company_id', '=', self.company_id.id)]
        if self.filter_type == 'department' and self.department_id:
            domain.append(('department_id', '=', self.department_id.id))
        elif self.filter_type == 'branch' and self.branch_id:
            domain.append(('branch_id', '=', self.branch_id.id))
        elif self.filter_type == 'employee' and self.employee_ids:
            return self.employee_ids
        return self.env['hr.employee'].search(domain)

    def _calc_new_salary(self, current):
        if self.increase_type == 'percent':
            return round(current * (1 + self.percent / 100), 2)
        else:
            return round(current + self.fixed_amount, 2)

    def action_apply(self):
        self.ensure_one()
        if self.increase_type == 'percent' and self.percent <= 0:
            raise UserError('Ingrese un porcentaje mayor a 0.')
        if self.increase_type == 'fixed' and self.fixed_amount <= 0:
            raise UserError('Ingrese un monto fijo mayor a 0.')

        employees = self._get_employees()
        if not employees:
            raise UserError('No hay empleados que coincidan con los filtros seleccionados.')

        count = 0
        for emp in employees:
            current = emp.base_salary or 0
            new_salary = self._calc_new_salary(current)
            if new_salary != current:
                emp.write({
                    'base_salary': new_salary,
                    'salary_effective_date': self.effective_date,
                })
                # Registrar en historial de salarios
                self.env['planilla.salary.history'].create({
                    'employee_id': emp.id,
                    'salary': new_salary,
                    'effective_date': self.effective_date,
                    'note': self.note or (
                        f'Incremento masivo: {self.percent}%' if self.increase_type == 'percent'
                        else f'Incremento masivo: ₡{self.fixed_amount:,.0f}'
                    ),
                })
                count += 1

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Incremento Aplicado',
                'message': f'Se actualizó el salario de {count} empleado(s) exitosamente.',
                'type': 'success',
                'sticky': False,
            }
        }


class SalaryIncreasePreview(models.TransientModel):
    _name = 'planilla.salary.increase.preview'
    _description = 'Vista Previa Incremento Salarial'

    wizard_id = fields.Many2one('planilla.salary.increase.wizard')
    employee_id = fields.Many2one('hr.employee', string='Empleado')
    department_id = fields.Many2one('hr.department', string='Departamento')
    current_salary = fields.Monetary(string='Salario Actual', currency_field='currency_id')
    new_salary = fields.Monetary(string='Salario Nuevo', currency_field='currency_id')
    difference = fields.Monetary(string='Diferencia', currency_field='currency_id')
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.ref('base.CRC'))
