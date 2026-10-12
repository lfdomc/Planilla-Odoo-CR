from odoo import models, fields, api
from odoo.exceptions import ValidationError


class MinimumSalary(models.Model):
    _name = 'planilla.minimum.salary'
    _description = 'Salarios Minimos MTSS'
    _order = 'valid_from desc, category asc'

    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company
    )
    category = fields.Char(
        string='Categoria Ocupacional', required=True,
        help='Ej: Trabajador no calificado, Tecnico de nivel medio, etc.'
    )
    amount = fields.Monetary(
        string='Salario Minimo Mensual (CRC)', currency_field='currency_id', required=True
    )
    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.ref('base.CRC')
    )
    valid_from = fields.Date(
        string='Vigente desde', required=True,
        help='Fecha del decreto MTSS que establece este salario minimo'
    )
    decree_ref = fields.Char(
        string='Decreto / Referencia',
        help='Ej: Decreto N.deg 44567-MTSS vigente enero 2026'
    )
    active = fields.Boolean(default=True)

    @api.constrains('amount')
    def _check_amount(self):
        for rec in self:
            if rec.amount <= 0:
                raise ValidationError('El salario minimo debe ser mayor a cero.')

    @api.model
    def get_current_minimum(self, category=None, company_id=None):
        """
        Retorna el salario minimo mas reciente vigente para la compania
        indicada (o global si el registro no tiene compania asignada).
        Si se pasa category, filtra por esa categoria.
        No usa cache -- los salarios minimos son datos contables criticos
        que deben consultarse directamente a la BD en cada operacion.

        FIX BUG: antes no filtraba por compania en absoluto -- en un
        entorno con varias companias, podia devolver el registro de
        OTRA compania distinta a la que esta validando la boleta (el
        primero que search() encontrara segun el orden por defecto,
        sin ninguna relacion con la compania real del empleado). Ahora
        se filtra igual que el resto de modelos hibridos del modulo:
        registros de la compania solicitada, o registros globales
        (company_id vacio) como respaldo.
        """
        domain = [('active', '=', True)]
        if category:
            domain.append(('category', 'ilike', category))
        cid = company_id or self.env.company.id
        domain += ['|', ('company_id', '=', cid), ('company_id', '=', False)]
        rec = self.search(domain, order='valid_from desc', limit=1)
        return rec.amount if rec else 0.0
