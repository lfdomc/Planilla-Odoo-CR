from odoo import models, fields, api


class PublicHoliday(models.Model):
    _name = 'planilla.public.holiday'
    _description = 'Feriados Nacionales CR'
    _order = 'date asc'

    name = fields.Char(string='Feriado', required=True)
    date = fields.Date(string='Fecha', required=True)
    type = fields.Selection([
        ('national', 'Nacional Obligatorio (Art. 148 CT)'),
        ('civic',    'Cívico No Laborable'),
        ('custom',   'Personalizado'),
    ], string='Tipo', default='national', required=True)
    company_id = fields.Many2one(
        'res.company', string='Empresa',
        default=lambda self: self.env.company,
        help='Dejar vacío para aplicar a todas las empresas.'
    )
    active = fields.Boolean(default=True)
    notes = fields.Text(string='Notas')

    @api.model
    def is_holiday(self, date_to_check, company_id=None):
        """Retorna True si la fecha es feriado nacional."""
        domain = [('date', '=', date_to_check), ('active', '=', True)]
        if company_id:
            domain += ['|', ('company_id', '=', company_id), ('company_id', '=', False)]
        else:
            domain.append(('company_id', '=', False))
        return bool(self.search(domain, limit=1))

    @api.model
    def get_holidays_in_range(self, date_from, date_to, company_id=None):
        """Retorna el conjunto de fechas feriadas en el rango dado."""
        domain = [
            ('date', '>=', date_from),
            ('date', '<=', date_to),
            ('active', '=', True),
        ]
        if company_id:
            domain += ['|', ('company_id', '=', company_id), ('company_id', '=', False)]
        return set(self.search(domain).mapped('date'))
