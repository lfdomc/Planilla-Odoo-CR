from odoo import models, fields, api


class RebajoRenta(models.Model):
    _name        = 'planilla.rebajo.renta'
    _description = 'Rebajo Consolidado de Renta'
    _order       = 'date_from desc'
    _rec_name    = 'name'

    name = fields.Char(compute='_compute_name', store=True)
    employee_id = fields.Many2one('hr.employee', required=True, ondelete='cascade', index=True)
    company_id  = fields.Many2one('res.company', related='employee_id.company_id', store=True, readonly=True)
    currency_id = fields.Many2one('res.currency', related='company_id.currency_id', readonly=True)
    amount      = fields.Monetary(string='Monto por Período', currency_field='currency_id', required=True)
    frequency   = fields.Selection([
        ('biweekly', 'Por Quincena'),
        ('monthly',  'Mensual (÷2 en nómina quincenal)'),
    ], default='biweekly', required=True)
    date_from   = fields.Date(required=True, default=fields.Date.today)
    date_to     = fields.Date(help='Vacío = sin vencimiento')
    reason      = fields.Selection([
        ('multiempleo',       'Multiempleo (segundo patrono)'),
        ('ajuste_voluntario', 'Ajuste voluntario de renta'),
        ('otro',              'Otro'),
    ], default='multiempleo', required=True)
    reference_doc = fields.Char(string='N° Declaración del Empleado')
    notes         = fields.Text()

    @api.depends('employee_id', 'reason', 'date_from')
    def _compute_name(self):
        labels = {'multiempleo': 'Multiempleo', 'ajuste_voluntario': 'Ajuste', 'otro': 'Otro'}
        for rec in self:
            emp  = rec.employee_id.name or '?'
            mot  = labels.get(rec.reason, '')
            date = rec.date_from.strftime('%m/%Y') if rec.date_from else ''
            rec.name = f'RBJ-{emp}-{mot} ({date})'

    def get_amount_for_period(self, payslip_frequency):
        self.ensure_one()
        if self.frequency == 'monthly' and payslip_frequency == 'biweekly':
            return round(self.amount / 2, 2)
        return self.amount

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if rec.reason == 'multiempleo' and rec.employee_id:
                rec.employee_id.sudo().write({'es_multiempleado': True})
        return records

    def write(self, vals):
        res = super().write(vals)
        if vals.get('reason') == 'multiempleo':
            self.mapped('employee_id').sudo().write({'es_multiempleado': True})
        return res

    def unlink(self):
        employees = self.mapped('employee_id')
        res = super().unlink()
        for emp in employees:
            still_has = self.env['planilla.rebajo.renta'].search([
                ('employee_id', '=', emp.id), ('reason', '=', 'multiempleo')], limit=1)
            if not still_has:
                emp.sudo().write({'es_multiempleado': False})
        return res
