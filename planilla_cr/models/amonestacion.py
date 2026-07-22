from odoo import models, fields, api
from odoo.exceptions import ValidationError


class Amonestacion(models.Model):
    _name = 'planilla.amonestacion'
    _description = 'Amonestacion Escrita'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'
    _rec_name = 'name'

    name = fields.Char(
        string='Referencia',
        compute='_compute_name', store=True
    )
    employee_id = fields.Many2one(
        'hr.employee', string='Empleado', required=True,
        tracking=True, index=True, ondelete='cascade'
    )
    company_id = fields.Many2one(
        'res.company', string='Empresa',
        related='employee_id.company_id', store=True
    )
    branch_id = fields.Many2one(
        related='employee_id.branch_id', string='Sucursal', store=True
    )
    date = fields.Date(
        string='Fecha', required=True, default=fields.Date.today,
        tracking=True
    )
    amonestacion_type = fields.Selection([
        ('tardanza',   'Tardanzas Reiteradas'),
        ('ausencia',   'Ausencia Injustificada'),
        ('conducta',   'Conducta Inapropiada'),
        ('incumplimiento', 'Incumplimiento de Funciones'),
        ('seguridad',  'Violacion de Normas de Seguridad'),
        ('otro',       'Otro'),
    ], string='Tipo de Falta', required=True, default='tardanza', tracking=True)

    subject = fields.Char(
        string='Asunto',
        default='Amonestacion Escrita',
        required=True,
        tracking=True
    )
    legal_basis = fields.Char(
        string='Base Legal',
        default='Articulo 71 inciso b) del Codigo de Trabajo',
        help='Articulo del CT en que se fundamenta la amonestacion.'
    )
    specific_incidents = fields.Text(
        string='Hechos Especificos',
        help='Descripcion detallada de los incidentes o faltas cometidas.',
        tracking=True
    )
    body = fields.Text(
        string='Texto de la Amonestacion',
        tracking=True
    )
    consequences = fields.Text(
        string='Consecuencias de Reincidencia',
        default='De persistir la situacion, se tomaran medidas disciplinarias '
                'mas severas, incluyendo la posibilidad de suspension o despido '
                'conforme a derecho.'
    )

    # -- Firmante ------------------------------------------------------------
    signed_by_name = fields.Char(
        string='Nombre del Firmante',
        tracking=True
    )
    signed_by_title = fields.Char(
        string='Cargo del Firmante',
        tracking=True
    )

    # -- Testigos --------------------------------------------------------
    # Opcionales -- si se dejan vacios, la carta igual imprime el espacio
    # en blanco para que firmen a mano. Si se llenan, el nombre aparece
    # impreso arriba de la linea de firma.
    witness1_name = fields.Char(string='Testigo 1 - Nombre')
    witness1_id = fields.Char(string='Testigo 1 - Cedula')
    witness2_name = fields.Char(string='Testigo 2 - Nombre')
    witness2_id = fields.Char(string='Testigo 2 - Cedula')

    # -- Lugar ---------------------------------------------------------------
    location = fields.Char(
        string='Lugar de emision',
        help='Ciudad donde se emite la carta. Ej: Alajuela, INVU Las Canas'
    )

    # -- Estado --------------------------------------------------------------
    state = fields.Selection([
        ('draft',        'Borrador'),
        ('issued',       'Emitida'),
        ('acknowledged', 'Recibida por el Empleado'),
        ('cancelled',    'Cancelada'),
    ], string='Estado', default='draft', tracking=True)

    # -- Contador de amonestaciones previas ----------------------------------
    previous_count = fields.Integer(
        string='Amonestaciones Previas',
        compute='_compute_previous_count', store=False
    )

    @api.depends('employee_id', 'date')
    def _compute_name(self):
        TYPES = {
            'tardanza': 'Tardanzas',
            'ausencia': 'Ausencia',
            'conducta': 'Conducta',
            'incumplimiento': 'Incumplimiento',
            'seguridad': 'Seguridad',
            'otro': 'Amonestacion',
        }
        for rec in self:
            if rec.employee_id and rec.date:
                tipo = TYPES.get(rec.amonestacion_type or 'otro', 'Amonestacion')
                rec.name = f'AMON-{rec.employee_id.name[:20]}-{rec.date}'
            else:
                rec.name = 'Nueva Amonestacion'

    @api.depends('employee_id', 'date', 'state')
    def _compute_previous_count(self):
        for rec in self:
            if not rec.employee_id or not rec.date:
                rec.previous_count = 0
                continue
            rec.previous_count = self.search_count([
                ('employee_id', '=', rec.employee_id.id),
                ('date', '<', rec.date),
                ('state', 'in', ('issued', 'acknowledged')),
            ])

    def action_issue(self):
        self.write({'state': 'issued'})

    def action_acknowledge(self):
        self.write({'state': 'acknowledged'})

    def action_cancel(self):
        self.write({'state': 'cancelled'})

    def action_draft(self):
        self.write({'state': 'draft'})

    def action_print_amonestacion(self):
        return self.env.ref(
            'planilla_cr.action_report_amonestacion'
        ).report_action(self)
