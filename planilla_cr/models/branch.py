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
