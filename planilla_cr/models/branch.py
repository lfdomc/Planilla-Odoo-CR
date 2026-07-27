from odoo import models, fields, api


class Branch(models.Model):
    _name = 'planilla.branch'
    _description = 'Sucursal'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Nombre', required=True, tracking=True)
    code = fields.Char(string='Codigo', required=True, tracking=True)
    address = fields.Char(string='Direccion')
    phone = fields.Char(string='Telefono')
    email = fields.Char(string='Correo')
    company_id = fields.Many2one(
        'res.company', string='Compania',
        required=True, default=lambda self: self.env.company
    )
    active = fields.Boolean(default=True)
    employee_ids = fields.One2many(
        'hr.employee', 'branch_id', string='Empleados'
    )
    employee_count = fields.Integer(
        compute='_compute_employee_count', string='Total Empleados'
    )

    # -- Coordenadas GPS (para validacion de marcaciones fuera de area) ----
    # Usadas por facial_attendance.kiosk.get_reference_coordinates() si
    # ese modulo esta instalado. planilla_cr NO depende de
    # facial_attendance -- estos campos son utiles por si solos (ej. para
    # mostrar la sede en un mapa) y se leen de forma segura desde
    # facial_attendance via self.env.get('planilla.branch') cuando
    # existe, sin que planilla_cr necesite saber nada de ese modulo.
    latitude = fields.Float(
        string='Latitud', digits=(10, 7),
        help='Coordenada GPS de la sede. Usada para validar que las '
             'marcaciones de asistencia (si el modulo de Reconocimiento '
             'Facial tiene el GPS complementario activado) ocurran '
             'dentro de un radio razonable de este lugar.',
    )
    longitude = fields.Float(
        string='Longitud', digits=(10, 7),
    )

    # -- Sucursales temporales (obras de construccion, proyectos) ---------
    is_temporary = fields.Boolean(
        string='Sucursal Temporal',
        default=False, tracking=True,
        help='Active para sitios de obra o proyectos con fecha de fin '
             'conocida. Las sucursales temporales aparecen resaltadas en '
             'la lista y pueden eliminarse facilmente cuando el proyecto '
             'termina, a diferencia de las sucursales permanentes.',
    )
    project_end_date = fields.Date(
        string='Fecha Estimada de Cierre',
        help='Fecha en que se espera que termine el proyecto/obra. '
             'Puramente informativa -- no elimina la sucursal '
             'automaticamente.',
    )

    @api.depends('employee_ids')
    def _compute_employee_count(self):
        for rec in self:
            rec.employee_count = len(rec.employee_ids)

    def action_view_employees(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Empleados',
            'res_model': 'hr.employee',
            'view_mode': 'list,form',
            'domain': [('branch_id', '=', self.id)],
            'context': {'default_branch_id': self.id},
        }

    def action_archive_temporary(self):
        """Atajo para cerrar una sucursal temporal cuando termina la obra:
        la desactiva (active=False) en vez de borrarla directamente, para
        conservar el historial de nombramientos/boletas que la
        referencian. El borrado fisico solo lo puede hacer un usuario con
        permisos de eliminacion desde la vista de archivados, una vez
        confirmado que ya no hay datos dependientes.
        """
        non_temp = self.filtered(lambda b: not b.is_temporary)
        if non_temp:
            from odoo.exceptions import UserError
            raise UserError(
                'Solo se pueden archivar sucursales marcadas como '
                'Temporales. Las siguientes no lo son: '
                + ', '.join(non_temp.mapped('name'))
            )
        self.write({'active': False})
