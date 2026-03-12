from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError


class PayrollRunCR(models.Model):
    _name = 'planilla.run.cr'
    _description = 'Planilla'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_end desc'

    # M3 FIX — Constraint de BD para prevenir planillas duplicadas en concurrencia
    _sql_constraints = [
        (
            'unique_run_company_period',
            'UNIQUE(company_id, date_start, date_end)',
            'Ya existe una planilla para esta compañía en el mismo período. '
            'No se pueden crear dos planillas con las mismas fechas.'
        ),
    ]

    name = fields.Char(string='Nombre', required=True, tracking=True)
    company_id = fields.Many2one(
        'res.company', string='Compañía',
        required=True, default=lambda self: self.env.company
    )
    branch_id = fields.Many2one('planilla.branch', string='Sucursal', tracking=True)
    department_id = fields.Many2one(
        'hr.department', string='Departamento', tracking=True,
        help='Si se selecciona, solo se generan boletas para empleados de este departamento.'
    )
    payroll_calendar_id = fields.Many2one(
        'planilla.calendar', string='Calendarización', tracking=True
    )

    date_start = fields.Date(string='Desde', required=True, tracking=True)
    date_end = fields.Date(string='Hasta', required=True, tracking=True)

    payslip_ids = fields.One2many(
        'planilla.payslip.cr', 'payroll_run_id', string='Boletas de Pago'
    )
    payslip_count = fields.Integer(
        compute='_compute_payslip_count', string='Boletas'
    )

    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.ref('base.CRC')
    )
    total_gross = fields.Monetary(
        string='Total Bruto', currency_field='currency_id',
        compute='_compute_totals', store=True
    )
    total_net = fields.Monetary(
        string='Total Neto', currency_field='currency_id',
        compute='_compute_totals', store=True
    )
    total_employer_cost = fields.Monetary(
        string='Costo Total Patronal', currency_field='currency_id',
        compute='_compute_totals', store=True
    )
    total_ccss_employer = fields.Monetary(
        string='Total CCSS Patronal', currency_field='currency_id',
        compute='_compute_totals', store=True
    )
    total_ccss_employee = fields.Monetary(
        string='Total CCSS Obrero', currency_field='currency_id',
        compute='_compute_totals', store=True
    )
    total_income_tax = fields.Monetary(
        string='Imp. Renta Total', currency_field='currency_id',
        compute='_compute_totals', store=True
    )
    total_deductions = fields.Monetary(
        string='Total Deducciones Obrero', currency_field='currency_id',
        compute='_compute_totals', store=True,
        help='CCSS Obrero + Impuesto de Renta'
    )
    total_salary_payable = fields.Monetary(
        string='Salario a Pagar', currency_field='currency_id',
        compute='_compute_totals', store=True,
        help='Total neto a transferir a los empleados (bruto - CCSS obrero - renta - deducciones)'
    )
    cost_per_net_colon = fields.Float(
        string='Costo por ₡1 neto', digits=(6, 4),
        compute='_compute_totals', store=True,
        help='Por cada ₡1 que recibe el empleado en mano, cuánto gasta la empresa en total (salario + cargas patronales)'
    )

    state = fields.Selection([
        ('draft', 'Borrador'),
        ('confirmed', 'Confirmado'),
        ('done', 'Pagado'),
        ('cancelled', 'Cancelado'),
    ], string='Estado', default='draft', tracking=True)

    note = fields.Text(string='Notas')

    @api.constrains('date_start', 'date_end')
    def _check_dates(self):
        for rec in self:
            if rec.date_start and rec.date_end and rec.date_start > rec.date_end:
                raise ValidationError('La fecha inicio no puede ser mayor a la fecha fin.')

    @api.depends('payslip_ids')
    def _compute_payslip_count(self):
        for rec in self:
            rec.payslip_count = len(rec.payslip_ids)

    @api.depends('payslip_ids.gross_salary', 'payslip_ids.net_salary',
                 'payslip_ids.salary_payable', 'payslip_ids.total_employer_cost',
                 'payslip_ids.ccss_employer', 'payslip_ids.ccss_employee',
                 'payslip_ids.income_tax')
    def _compute_totals(self):
        for rec in self:
            rec.total_gross         = sum(rec.payslip_ids.mapped('gross_salary'))
            rec.total_ccss_employee = sum(rec.payslip_ids.mapped('ccss_employee'))
            rec.total_income_tax    = sum(rec.payslip_ids.mapped('income_tax'))
            rec.total_ccss_employer = sum(rec.payslip_ids.mapped('ccss_employer'))
            rec.total_employer_cost = sum(rec.payslip_ids.mapped('total_employer_cost'))

            # Total Deducciones = CCSS Obrero + Renta
            rec.total_deductions = round(
                rec.total_ccss_employee + rec.total_income_tax, 2
            )
            # Total Neto = suma del net_salary de cada boleta (bruto - CCSS obrero - renta)
            rec.total_net = round(
                sum(rec.payslip_ids.mapped('net_salary')), 2
            )

            # Salario a Pagar = lo que realmente se deposita (neto - pensiones, prestamos y deducciones adicionales)
            rec.total_salary_payable = round(
                sum(rec.payslip_ids.mapped('salary_payable')), 2
            )

            # Costo real por cada colón que el empleado recibe en mano
            if rec.total_salary_payable and rec.total_salary_payable > 0:
                rec.cost_per_net_colon = round(rec.total_employer_cost / rec.total_salary_payable, 4)
            else:
                rec.cost_per_net_colon = 0.0

    def action_generate_payslips(self):
        """Genera boletas para todos los empleados activos de la calendarización."""
        self.ensure_one()
        if self.state != 'draft':
            raise UserError('Solo se pueden generar boletas en planillas en borrador.')

        domain = [('active', '=', True)]
        if self.branch_id:
            domain.append(('branch_id', '=', self.branch_id.id))
        if self.department_id:
            domain.append(('department_id', '=', self.department_id.id))
        if self.payroll_calendar_id:
            domain.append(('payroll_calendar_id', '=', self.payroll_calendar_id.id))

        employees = self.env['hr.employee'].search(domain)

        # Filtrar solo empleados activos en planilla
        employees = employees.filtered(
            lambda e: e.employee_status_id and e.employee_status_id.is_active_payroll
        )

        for employee in employees:
            # Verificar si ya existe boleta para este periodo
            existing = self.env['planilla.payslip.cr'].search([
                ('employee_id', '=', employee.id),
                ('payroll_run_id', '=', self.id),
            ])
            if not existing:
                self.env['planilla.payslip.cr'].create({
                    'employee_id': employee.id,
                    'payroll_run_id': self.id,
                    'date_from': self.date_start,
                    'date_to': self.date_end,
                })

        return {
            'type': 'ir.actions.act_window',
            'name': 'Boletas Generadas',
            'res_model': 'planilla.payslip.cr',
            'view_mode': 'list,form',
            'domain': [('payroll_run_id', '=', self.id)],
        }

    move_id = fields.Many2one('account.move', string='Asiento Contable Planilla')

    def action_pay(self):
        self.ensure_one()
        if self.state != 'confirmed':
            raise UserError('Solo se pueden pagar planillas confirmadas.')

        # M1 FIX — Verificar que todas las boletas están confirmadas antes de pagar
        not_confirmed = self.payslip_ids.filtered(
            lambda p: p.state not in ('confirmed', 'paid', 'cancelled')
        )
        if not_confirmed:
            names = ', '.join(not_confirmed.mapped('employee_id.name'))
            raise UserError(
                f'Las siguientes boletas no están confirmadas:\n{names}\n\n'
                f'Confirme todas las boletas antes de pagar la planilla.'
            )

        payslips_to_pay = self.payslip_ids.filtered(lambda p: p.state == 'confirmed')
        if not payslips_to_pay:
            raise UserError(
                'No hay boletas confirmadas para pagar. '
                'Todas las boletas están canceladas o ya fueron pagadas.'
            )

        config = self.env['planilla.accounting.config'].get_config(self.company_id.id)
        mode = config.accounting_entry_mode if config else 'per_employee'

        if mode == 'per_run':
            # Asiento consolidado por planilla
            payslips_to_pay.action_pay(skip_accounting=True)
            self._create_consolidated_accounting_entry(payslips_to_pay)
        else:
            # Asiento por empleado (comportamiento original)
            payslips_to_pay.action_pay()

        self.state = 'done'

    def _create_consolidated_accounting_entry(self, payslips):
        """Genera un único asiento contable consolidado para toda la planilla."""
        if not payslips:
            return

        config = self.env['planilla.accounting.config'].get_config(self.company_id.id)
        if not config:
            raise UserError(
                'No hay configuración contable para esta compañía. '
                'Configure las cuentas en Planilla → Configuración → Contabilidad.'
            )
        if not config.journal_id:
            raise UserError(
                'El diario de planilla no está configurado. '
                'Vaya a Planilla → Configuración → Contabilidad y asigne un diario, '
                'o use el botón "⚡ Autocompletar Cuentas CR".'
            )

        # Sumar todos los montos de todas las boletas
        total_gross = sum(payslips.mapped('gross_salary'))
        total_ccss_employer = sum(payslips.mapped('ccss_employer'))
        total_ins_employer = sum(payslips.mapped('ins_employer'))
        total_vacation_prov = sum(payslips.mapped('vacation_provision'))
        total_aguinaldo_prov = sum(payslips.mapped('aguinaldo_provision'))
        total_cesantia_prov = sum(payslips.mapped('cesantia_provision'))
        total_ccss_employee = sum(payslips.mapped('ccss_employee'))
        total_income_tax = sum(payslips.mapped('income_tax'))
        # B1 FIX: usar salary_payable (neto real después de pensiones y préstamos)
        total_salary_payable = sum(payslips.mapped('salary_payable'))

        # B7 FIX: sumar pensiones, préstamos y otras deducciones de TODAS las boletas
        all_deduction_lines = payslips.mapped('deduction_line_ids')
        total_pensiones = round(sum(
            l.amount for l in all_deduction_lines
            if l.deduction_category == 'pension_alimentaria'
        ), 2)
        total_prestamos = round(sum(
            l.amount for l in all_deduction_lines
            if l.deduction_category == 'loan'
        ), 2)
        total_otras_ded = round(sum(
            l.amount for l in all_deduction_lines
            if l.deduction_category not in ('pension_alimentaria', 'loan')
               and l.deduction_type == 'deduction'
        ), 2)

        ref = f'Planilla: {self.name} ({len(payslips)} empleados)'
        lines = []

        def add_line(account, debit=0.0, credit=0.0, name=''):
            if not account or (round(debit, 2) == 0.0 and round(credit, 2) == 0.0):
                return
            lines.append((0, 0, {
                'account_id': account.id,
                'name': name,
                'debit': round(debit, 2),
                'credit': round(credit, 2),
            }))

        # DÉBITOS
        add_line(config.account_salary_expense, debit=total_gross, name='Salarios — Planilla ' + self.name)
        add_line(config.account_social_charges_expense, debit=total_ccss_employer + total_ins_employer, name='Cargas Sociales — Planilla ' + self.name)
        add_line(config.account_vacation_expense, debit=total_vacation_prov, name='Vacaciones — Planilla ' + self.name)
        add_line(config.account_aguinaldo_expense, debit=total_aguinaldo_prov, name='Aguinaldo — Planilla ' + self.name)
        add_line(config.account_cesantia_expense, debit=total_cesantia_prov, name='Cesantía — Planilla ' + self.name)

        # CRÉDITOS
        add_line(config.account_ccss_payable, credit=total_ccss_employee + total_ccss_employer, name='CCSS por Pagar — Planilla ' + self.name)
        add_line(config.account_ins_payable, credit=total_ins_employer, name='INS por Pagar — Planilla ' + self.name)
        add_line(config.account_income_tax_payable, credit=total_income_tax, name='Retención Renta — Planilla ' + self.name)
        add_line(config.account_aguinaldo_provision, credit=total_aguinaldo_prov, name='Provisión Aguinaldo — Planilla ' + self.name)
        add_line(config.account_cesantia_provision, credit=total_cesantia_prov, name='Provisión Cesantía — Planilla ' + self.name)
        add_line(config.account_vacation_provision, credit=total_vacation_prov, name='Provisión Vacaciones — Planilla ' + self.name)

        # B7 FIX: pensiones retenidas
        if total_pensiones > 0:
            pension_account = config.account_salary_payable
            if hasattr(config, 'account_pension_payable') and config.account_pension_payable:
                pension_account = config.account_pension_payable
            add_line(pension_account, credit=total_pensiones, name='Pensiones Alimentarias Retenidas — Planilla ' + self.name)

        # B7 FIX: cuotas de préstamos retenidas
        if total_prestamos > 0:
            loan_account = config.account_loans_payable if config.account_loans_payable else config.account_salary_payable
            add_line(loan_account, credit=total_prestamos, name='Cuotas Préstamos Retenidos — Planilla ' + self.name)

        # B7 FIX: otras deducciones adicionales
        if total_otras_ded > 0:
            add_line(config.account_salary_payable, credit=total_otras_ded, name='Otras Deducciones Retenidas — Planilla ' + self.name)

        add_line(config.account_salary_payable, credit=total_salary_payable, name='Salarios por Pagar — Planilla ' + self.name)

        if not lines:
            return

        move = self.env['account.move'].create({
            'journal_id': config.journal_id.id,
            'date': self.date_end,
            'ref': ref,
            'move_type': 'entry',
            'line_ids': lines,
        })
        move.action_post()
        self.move_id = move.id

    def action_view_accounting_entry(self):
        self.ensure_one()
        if not self.move_id:
            return
        return {
            'type': 'ir.actions.act_window',
            'name': 'Asiento Contable',
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': self.move_id.id,
        }

    def _check_no_duplicate_payment(self):
        """
        Verifica que ningún empleado en esta corrida ya tenga una boleta
        PAGADA en el mismo período (mismo date_start/date_end).
        Previene doble pago accidental al recrear una planilla.
        """
        self.ensure_one()
        employee_ids = self.payslip_ids.mapped('employee_id.id')
        if not employee_ids:
            return

        # Buscar boletas ya pagadas de estos empleados en el mismo período
        # Excluir las propias boletas de esta corrida
        duplicates = self.env['planilla.payslip.cr'].search([
            ('employee_id', 'in', employee_ids),
            ('date_from', '=', self.date_start),
            ('date_to', '=', self.date_end),
            ('state', '=', 'paid'),
            ('payroll_run_id', '!=', self.id),
        ])
        if duplicates:
            names = ', '.join(sorted(set(duplicates.mapped('employee_id.name'))))
            raise UserError(
                f'⚠️ Doble pago detectado — los siguientes empleados ya tienen '
                f'una boleta PAGADA en el período {self.date_start} – {self.date_end}:\n\n'
                f'{names}\n\n'
                f'Cancele o archive la planilla anterior antes de continuar. '
                f'Si es un reliquidado, use el campo "Notas" en la boleta para documentarlo.'
            )

    def action_confirm(self):
        self.ensure_one()
        self.payslip_ids.action_confirm()
        self.state = 'confirmed'

    def action_send_all_payslips(self):
        """Envía todas las boletas por correo."""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Enviar Boletas',
            'res_model': 'planilla.send.payslip.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_payslip_ids': [(6, 0, self.payslip_ids.ids)],
                'default_send_all': True,
            },
        }

    def action_view_payslips(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Boletas de Pago',
            'res_model': 'planilla.payslip.cr',
            'view_mode': 'list,form',
            'domain': [('payroll_run_id', '=', self.id)],
        }

    def action_cancel(self):
        for rec in self:
            rec.payslip_ids.action_cancel()
            rec.state = 'cancelled'

    def unlink(self):
        for rec in self:
            if rec.move_id and rec.move_id.state == 'posted':
                raise UserError(
                    f'No se puede eliminar la planilla "{rec.name}" porque tiene un asiento contable '
                    f'publicado (#{rec.move_id.name}). '
                    'Primero revierta o cancele el asiento desde Contabilidad.'
                )
        return super().unlink()

    def action_reset_to_draft(self):
        for rec in self:
            if rec.state not in ('cancelled', 'confirmed'):
                raise UserError('Solo se puede resetear planillas canceladas o confirmadas.')
            rec.payslip_ids.filtered(lambda p: p.state in ('cancelled', 'confirmed')).action_reset_to_draft()
            rec.state = 'draft'


class AccountMovePayrollSync(models.Model):
    _inherit = 'account.move'

    def write(self, vals):
        # Capturar planillas ANTES del cambio de estado
        runs_to_check = self.env['planilla.run.cr']
        if 'state' in vals:
            runs_to_check = self.env['planilla.run.cr'].search([
                ('move_id', 'in', self.ids),
                ('state', '=', 'done'),
            ])
        res = super().write(vals)
        # Ahora verificar si el asiento ya no está publicado
        if runs_to_check:
            for run in runs_to_check:
                if run.move_id and run.move_id.state != 'posted':
                    run.payslip_ids.filtered(
                        lambda p: p.state not in ('cancelled',)
                    ).write({'state': 'cancelled'})
                    run.write({'state': 'cancelled'})
        return res

    def unlink(self):
        # Si se elimina el asiento, cancelar planilla asociada
        runs = self.env['planilla.run.cr'].search([
            ('move_id', 'in', self.ids),
            ('state', '=', 'done'),
        ])
        res = super().unlink()
        for run in runs:
            run.payslip_ids.filtered(
                lambda p: p.state not in ('cancelled',)
            ).write({'state': 'cancelled'})
            run.write({'state': 'cancelled'})
        return res
