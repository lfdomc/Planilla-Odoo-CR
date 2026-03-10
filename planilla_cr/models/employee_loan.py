from odoo import models, fields, api
from odoo.exceptions import ValidationError, UserError


class EmployeeLoan(models.Model):
    _name = 'planilla.employee.loan'
    _description = 'Préstamos y Adelantos de Salario'
    _order = 'date_granted desc'

    name = fields.Char(string='Referencia', compute='_compute_name', store=True)
    employee_id = fields.Many2one(
        'hr.employee', string='Empleado', required=True, ondelete='restrict'
    )
    branch_id = fields.Many2one(related='employee_id.branch_id', store=True)
    currency_id = fields.Many2one(related='employee_id.currency_id', store=True)

    loan_type = fields.Selection([
        ('loan', 'Préstamo'),
        ('advance', 'Adelanto de Salario'),
    ], string='Tipo', required=True, default='loan')

    amount_total = fields.Monetary(
        string='Monto Total (₡)', currency_field='currency_id', required=True
    )
    installments = fields.Integer(
        string='Número de Cuotas', required=True, default=1,
        help='Número de cuotas mensuales para descontar en boleta'
    )
    installment_amount = fields.Monetary(
        string='Monto por Cuota (₡)', currency_field='currency_id',
        compute='_compute_installment', store=True
    )
    date_granted = fields.Date(
        string='Fecha de Otorgamiento', required=True, default=fields.Date.today
    )
    date_first_deduction = fields.Date(
        string='Primera Deducción en', required=True,
        help='Boleta a partir de la cual se empieza a descontar'
    )
    note = fields.Text(string='Observaciones')

    state = fields.Selection([
        ('draft',    'Borrador'),
        ('approved', 'Aprobado'),
        ('active',   'En curso'),
        ('paid',     'Cancelado'),
        ('cancelled','Anulado'),
    ], string='Estado', default='draft')

    installment_ids = fields.One2many(
        'planilla.loan.installment', 'loan_id', string='Cuotas'
    )
    amount_paid = fields.Monetary(
        string='Pagado (₡)', currency_field='currency_id',
        compute='_compute_amounts', store=True
    )
    max_installment_allowed = fields.Monetary(
        string='Cuota Máxima Permitida (50% neto)',
        currency_field='currency_id',
        compute='_compute_max_installment', store=False,
        help='Límite legal Art. 172 CT: 50% del salario neto estimado del empleado.'
    )

    amount_pending = fields.Monetary(
        string='Saldo Pendiente (₡)', currency_field='currency_id',
        compute='_compute_amounts', store=True
    )

    @api.depends('employee_id', 'loan_type', 'date_granted')
    def _compute_name(self):
        types = {'loan': 'Préstamo', 'advance': 'Adelanto'}
        for rec in self:
            t = types.get(rec.loan_type, '')
            e = rec.employee_id.name or ''
            d = str(rec.date_granted) if rec.date_granted else ''
            rec.name = f'{t} — {e} — {d}'

    @api.depends('amount_total', 'installments')
    def _compute_installment(self):
        for rec in self:
            if rec.installments and rec.installments > 0:
                rec.installment_amount = round(rec.amount_total / rec.installments, 2)
            else:
                rec.installment_amount = rec.amount_total

    @api.depends('installment_ids.amount', 'installment_ids.state')
    def _compute_amounts(self):
        for rec in self:
            paid = sum(
                i.amount for i in rec.installment_ids if i.state == 'deducted'
            )
            rec.amount_paid    = round(paid, 2)
            rec.amount_pending = round(rec.amount_total - paid, 2)

    @api.constrains('amount_total', 'installments')
    def _check_amounts(self):
        for rec in self:
            if rec.amount_total <= 0:
                raise ValidationError('El monto total debe ser mayor a cero.')
            if rec.installments <= 0:
                raise ValidationError('Las cuotas deben ser al menos 1.')

    @api.depends('employee_id', 'employee_id.base_salary')
    def _compute_max_installment(self):
        rh = self.env['planilla.rate.helper']
        ccss_rate = rh.get_ccss_employee_rate()
        for rec in self:
            base = rec.employee_id.base_salary or 0.0
            estimated_net = base * (1 - ccss_rate)
            rec.max_installment_allowed = round(estimated_net * 0.50, 2)

    def action_print_amortization(self):
        return self.env.ref('planilla_cr.action_report_loan_amortization').report_action(self)

    def action_approve(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError('Solo se pueden aprobar préstamos en borrador.')
            # Verificar límite del 50% del salario neto (Art. 172 CT)
            rec._check_installment_salary_limit()
            rec._generate_installments()
            rec.state = 'approved'

    def _generate_installments(self):
        """Genera las líneas de cuota con fechas a partir de date_first_deduction."""
        self.ensure_one()
        self.installment_ids.unlink()
        from dateutil.relativedelta import relativedelta
        base_date = self.date_first_deduction
        for i in range(self.installments):
            due_date = base_date + relativedelta(months=i)
            self.env['planilla.loan.installment'].create({
                'loan_id':    self.id,
                'sequence':   i + 1,
                'due_date':   due_date,
                'amount':     self.installment_amount,
            })

    def action_activate(self):
        self.write({'state': 'active'})

    def action_cancel(self):
        for rec in self:
            pending = rec.installment_ids.filtered(lambda i: i.state == 'pending')
            pending.write({'state': 'cancelled'})
            rec.state = 'cancelled'

    def action_check_paid(self):
        """Marca el préstamo como cancelado si todas las cuotas están deducidas."""
        for rec in self:
            if all(i.state in ('deducted', 'cancelled') for i in rec.installment_ids):
                rec.state = 'paid'
                # Notificar al empleado por email
                if rec.employee_id.work_email:
                    try:
                        template = self.env.ref('planilla_cr.email_template_loan_paid', raise_if_not_found=False)
                        if template:
                            template.send_mail(rec.id, force_send=False)
                    except Exception:
                        pass

    def get_pending_installment(self, date_from, date_to):
        """
        Retorna la cuota pendiente a descontar en el periodo dado.
        Llamado desde la boleta al computar deducciones.
        """
        self.ensure_one()
        installment = self.installment_ids.filtered(
            lambda i: i.state == 'pending' and
            date_from <= i.due_date <= date_to
        )
        return installment[:1]  # solo una cuota por periodo


class LoanInstallment(models.Model):
    _name = 'planilla.loan.installment'
    _description = 'Cuota de Préstamo'
    _order = 'sequence asc'

    loan_id = fields.Many2one(
        'planilla.employee.loan', string='Préstamo', required=True, ondelete='cascade'
    )
    sequence   = fields.Integer(string='N°')
    due_date   = fields.Date(string='Fecha de Descuento')
    amount     = fields.Monetary(string='Monto (₡)', currency_field='currency_id')
    currency_id = fields.Many2one(related='loan_id.currency_id', store=True)
    state = fields.Selection([
        ('pending',   'Pendiente'),
        ('deducted',  'Descontada'),
        ('cancelled', 'Cancelada'),
    ], string='Estado', default='pending')
    payslip_id = fields.Many2one(
        'planilla.payslip.cr', string='Boleta', readonly=True
    )
