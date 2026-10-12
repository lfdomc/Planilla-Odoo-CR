from odoo import models, fields, api
from . import planilla_const as K


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
        ('monthly',  'Mensual (se prorratea según frecuencia de pago: quincenal ÷2, semanal ÷4)'),
    ], string='Frecuencia', default='biweekly', required=True)
    date_from   = fields.Date(string='Vigente Desde', required=True, default=fields.Date.today)
    date_to     = fields.Date(string='Vigente Hasta', help='Vacío = sin vencimiento')
    reason      = fields.Selection([
        ('multiempleo',       'Multiempleo (segundo patrono)'),
        ('ajuste_voluntario', 'Ajuste voluntario de renta'),
        ('otro',              'Otro'),
    ], string='Motivo', default='multiempleo', required=True)
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
        if self.frequency == 'biweekly':
            # Configurado como "por quincena": se aplica tal cual en cada
            # quincena. Si la boleta es de otra frecuencia (semanal, mensual)
            # esto es una discrepancia de configuracion -- se aplica el monto
            # completo por falta de una base mas clara para prorratear "por
            # quincena" a otras frecuencias.
            return self.amount
        # Configurado como "Mensual": prorratear segun cuantos periodos de
        # boleta hay en un mes -- generaliza el caso que antes solo cubria
        # quincenal (/2). Sin esto, un empleado pagado semanal con un rebajo
        # mensual se llevaba el monto COMPLETO cada semana (4x de mas).
        periods = K.PERIODOS_POR_MES.get(payslip_frequency, 1)
        return round(self.amount / periods, 2) if periods else self.amount

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
