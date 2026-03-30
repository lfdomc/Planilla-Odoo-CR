from odoo import models, fields, api


class PublicHoliday(models.Model):
    _name = 'planilla.public.holiday'
    _description = 'Feriados Nacionales CR'
    _order = 'date asc'

    name = fields.Char(string='Feriado', required=True)
    date = fields.Date(string='Fecha', required=True)
    type = fields.Selection([
        ('national', 'Nacional Obligatorio (Art. 148 CT)'),
        ('optional', 'No Obligatorio / Trasladable (Ley 8886)'),
        ('civic',    'Civico No Laborable'),
        ('custom',   'Personalizado'),
    ], string='Tipo', default='national', required=True)

    # FIX BUG-N06 v52: campo is_paid para distinguir pago obligatorio vs. opcional
    # Feriado obligatorio (is_paid=True): trabajar ese dia devuelve doble salario (Art. 148 CT)
    # Feriado no obligatorio (is_paid=False): no genera pago doble obligatorio, es trasladable
    is_paid = fields.Boolean(
        string='Pago Obligatorio',
        default=True,
        help='Art. 148 CT: feriados obligatorios requieren pago doble si se trabaja.\n'
             'Feriados no obligatorios (ej: 2 de diciembre) son trasladables y '
             'no generan doble salario automaticamente.'
    )
    company_id = fields.Many2one(
        'res.company', string='Empresa',
        default=lambda self: self.env.company,
        help='Dejar vacio para aplicar a todas las empresas.'
    )
    active = fields.Boolean(default=True)
    note = fields.Text(string='Notas')

    @api.model
    def is_holiday(self, date_to_check, company_id=None):
        """Retorna True si la fecha es feriado nacional (obligatorio o no)."""
        domain = [('date', '=', date_to_check), ('active', '=', True)]
        if company_id:
            domain += ['|', ('company_id', '=', company_id), ('company_id', '=', False)]
        else:
            domain.append(('company_id', '=', False))
        return bool(self.search(domain, limit=1))

    @api.model
    def is_paid_holiday(self, date_to_check, company_id=None):
        """Retorna True si la fecha es feriado de pago obligatorio (Art. 148 CT).
        Usar para determinar si se debe pagar doble al empleado que trabaja ese dia.
        """
        domain = [
            ('date', '=', date_to_check),
            ('active', '=', True),
            ('is_paid', '=', True),
        ]
        if company_id:
            domain += ['|', ('company_id', '=', company_id), ('company_id', '=', False)]
        else:
            domain.append(('company_id', '=', False))
        return bool(self.search(domain, limit=1))

    @api.model
    def get_holidays_in_range(self, date_from, date_to, company_id=None):
        """Retorna el conjunto de fechas feriadas en el rango dado (todos los tipos)."""
        domain = [
            ('date', '>=', date_from),
            ('date', '<=', date_to),
            ('active', '=', True),
        ]
        if company_id:
            domain += ['|', ('company_id', '=', company_id), ('company_id', '=', False)]
        return set(self.search(domain).mapped('date'))

    @api.model
    def get_paid_holidays_in_range(self, date_from, date_to, company_id=None):
        """Retorna solo feriados de pago obligatorio en el rango dado.
        Util para calculo de horas extras en dias feriados (tipo 'holiday' en overtime).
        """
        domain = [
            ('date', '>=', date_from),
            ('date', '<=', date_to),
            ('active', '=', True),
            ('is_paid', '=', True),
        ]
        if company_id:
            domain += ['|', ('company_id', '=', company_id), ('company_id', '=', False)]
        return set(self.search(domain).mapped('date'))
