import logging
import datetime
from odoo import models, fields, api
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

TASA_RETENCION = 0.15  # Art. 23 inciso c) LISR


class PlanillaPayslipSP(models.Model):
    """
    Boleta de pago para contratistas de Servicios Profesionales.
    - Sin CCSS (el contratista es trabajador independiente)
    - Sin INS (el contratista gestiona su propio seguro)
    - Retención de renta 15% opcional (Art. 23 LISR) según flag en el empleado
    - Pago por horas o monto fijo
    """
    _name = 'planilla.payslip.sp'
    _description = 'Boleta Servicios Profesionales'
    _order = 'date_from desc, employee_id'
    _inherit = ['mail.thread']

    name = fields.Char(string='Referencia', compute='_compute_name', store=True)
    employee_id = fields.Many2one(
        'hr.employee', string='Contratista', required=True,
        domain=[('ccss_insured', '=', False)], tracking=True,
    )
    company_id = fields.Many2one(
        'res.company', string='Empresa',
        default=lambda self: self.env.company,
    )
    branch_id = fields.Many2one('planilla.branch', string='Sucursal')
    date_from = fields.Date(string='Desde', required=True)
    date_to   = fields.Date(string='Hasta', required=True)

    # Método de pago
    metodo_pago = fields.Selection([
        ('horas',  'Por Horas'),
        ('fijo',   'Monto Fijo'),
    ], string='Método de Pago', required=True, default='horas')

    horas        = fields.Float(string='Horas Trabajadas')
    tarifa_hora  = fields.Float(string='Tarifa por Hora (₡)')
    monto_fijo   = fields.Float(string='Monto Fijo Acordado (₡)')
    descripcion  = fields.Text(string='Descripción del Servicio')

    # Cálculos
    monto_bruto      = fields.Monetary(string='Honorarios Brutos',
                                        currency_field='currency_id',
                                        compute='_compute_montos', store=True)
    retencion_renta  = fields.Monetary(string='Retención Renta 15% (Art. 23 LISR)',
                                        currency_field='currency_id',
                                        compute='_compute_montos', store=True)
    neto_a_pagar     = fields.Monetary(string='Neto a Pagar',
                                        currency_field='currency_id',
                                        compute='_compute_montos', store=True)
    aplica_retencion = fields.Boolean(
        string='Aplica Retención Renta',
        compute='_compute_montos', store=True,
    )

    state = fields.Selection([
        ('draft',     'Borrador'),
        ('confirmed', 'Confirmado'),
        ('paid',      'Pagado'),
    ], string='Estado', default='draft', tracking=True)

    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.ref('base.CRC'))

    @api.depends('employee_id', 'date_from', 'date_to')
    def _compute_name(self):
        for rec in self:
            emp = rec.employee_id.name or 'SP'
            d = rec.date_from.strftime('%Y-%m') if rec.date_from else ''
            rec.name = f'SP - {emp} - {d}'

    @api.depends('metodo_pago', 'horas', 'tarifa_hora', 'monto_fijo', 'employee_id')
    def _compute_montos(self):
        for rec in self:
            # Bruto
            if rec.metodo_pago == 'horas':
                bruto = round(rec.horas * rec.tarifa_hora, 2)
            else:
                bruto = round(rec.monto_fijo, 2)
            rec.monto_bruto = bruto

            # Retención renta: solo si el empleado tiene el flag
            aplica = not rec.employee_id.ccss_insured and \
                     rec.employee_id.retencion_renta_sp
            rec.aplica_retencion = aplica
            rec.retencion_renta  = round(bruto * TASA_RETENCION, 2) if aplica else 0.0
            rec.neto_a_pagar     = round(bruto - rec.retencion_renta, 2)

    # Onchange para cargar tarifa del empleado
    @api.onchange('employee_id')
    def _onchange_employee(self):
        if self.employee_id and self.metodo_pago == 'horas':
            # Sugerir tarifa desde base_salary / 240 (mes de 30 días × 8h)
            bs = self.employee_id.base_salary or 0
            if bs:
                self.tarifa_hora = round(bs / 240, 2)

    @staticmethod
    def _check_sp_enabled(env):
        config = env['planilla.accounting.config'].search(
            [('company_id', '=', env.company.id)], limit=1)
        if config and not config.enable_servicios_profesionales:
            from odoo.exceptions import UserError
            raise UserError(
                'El módulo de Servicios Profesionales está desactivado. '
                'Actívelo en Planillas → Configuración → Cuentas Contables.'
            )

    @api.model_create_multi
    def create(self, vals_list):
        self._check_sp_enabled(self.env)
        return super().create(vals_list)

    def action_confirm(self):
        self.write({'state': 'confirmed'})

    def action_pay(self):
        self.write({'state': 'paid'})

    def action_reset_draft(self):
        self.write({'state': 'draft'})
