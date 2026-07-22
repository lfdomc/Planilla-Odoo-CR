from datetime import date
from odoo import models, fields, api


class PlanillaEmployeeAsset(models.Model):
    """
    Activos Asignados al Empleado -- planilla.employee.asset
    ====================================================================
    Control de herramientas, uniformes, tecnologia, llaves/accesos y otros
    activos de la empresa que estan en poder de un empleado -- NO es dato
    de planilla (calculo de salario), es control patrimonial/RRHH, por eso
    vive en su propia pestaña en la ficha del empleado, separada de
    "Planilla CR".

    Se conecta naturalmente con las Liquidaciones: al dar de baja a un
    empleado, revisar aqui que activos tiene pendientes de devolver.
    """
    _name = 'planilla.employee.asset'
    _description = 'Activo Asignado a Empleado'
    _order = 'date_assigned desc'
    _inherit = ['mail.thread']

    employee_id = fields.Many2one(
        'hr.employee', string='Empleado', required=True,
        ondelete='cascade', index=True,
    )
    company_id = fields.Many2one(
        related='employee_id.company_id', string='Compania',
        store=True, readonly=True,
    )
    name = fields.Char(
        string='Descripcion', required=True, tracking=True,
        help='Ej: "Laptop Dell Latitude 5420", "Juego de llaves bodega", '
             '"Uniforme talla M (2 camisas + 1 pantalon)".'
    )
    category = fields.Selection([
        ('herramienta',  'Herramienta'),
        ('uniforme',     'Uniforme'),
        ('tecnologia',   'Tecnologia (laptop, celular, etc.)'),
        ('llave_acceso', 'Llave / Acceso'),
        ('vehiculo',     'Vehiculo'),
        ('otro',         'Otro'),
    ], string='Categoria', required=True, default='otro', tracking=True)
    serial_number = fields.Char(string='Numero de Serie / Placa')
    estimated_value = fields.Monetary(
        string='Valor Estimado', currency_field='currency_id',
        help='Opcional -- util para calcular responsabilidad en caso de '
             'perdida o dano.'
    )
    currency_id = fields.Many2one(
        related='company_id.currency_id', string='Moneda',
    )

    date_assigned = fields.Date(
        string='Fecha de Entrega', required=True, default=fields.Date.context_today,
        tracking=True,
    )
    date_returned = fields.Date(string='Fecha de Devolucion', tracking=True)

    state = fields.Selection([
        ('asignado', 'Asignado'),
        ('devuelto', 'Devuelto'),
        ('perdido',  'Perdido'),
        ('danado',   'Dañado'),
    ], string='Estado', default='asignado', required=True, tracking=True)

    notes = fields.Text(string='Notas')
    active = fields.Boolean(default=True)

    def action_mark_returned(self):
        """Marca el activo como devuelto hoy -- accion rapida desde la lista."""
        for asset in self:
            if asset.state != 'asignado':
                continue
            asset.write({
                'state': 'devuelto',
                'date_returned': fields.Date.context_today(self),
            })

    def name_get(self):
        result = []
        for asset in self:
            label = f'{asset.name} ({asset.employee_id.name})'
            result.append((asset.id, label))
        return result
