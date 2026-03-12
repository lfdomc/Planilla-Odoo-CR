from odoo import models, fields, api
from datetime import date
from dateutil.relativedelta import relativedelta


class PayrollDashboard(models.TransientModel):
    _name = 'planilla.dashboard'
    _description = 'Dashboard de Planilla CR'

    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        'res.currency',
        default=lambda self: self.env.company.currency_id,
        string='Moneda'
    )

    # Rango de fechas configurable
    date_from = fields.Date(string='Desde', required=True,
                            default=lambda self: date.today().replace(day=1))
    date_to = fields.Date(string='Hasta', required=True,
                          default=lambda self: date.today())

    # Métricas
    active_employees = fields.Integer(compute='_compute_metrics', string='Empleados Activos')
    payslips_count = fields.Integer(compute='_compute_metrics', string='Boletas Pagadas')
    total_gross = fields.Monetary(compute='_compute_metrics', string='Salario Bruto Total', currency_field='currency_id')
    total_net = fields.Monetary(compute='_compute_metrics', string='Salario Neto Total', currency_field='currency_id')
    total_employer_cost = fields.Monetary(compute='_compute_metrics', string='Costo Total Empresa', currency_field='currency_id')
    total_ccss = fields.Monetary(compute='_compute_metrics', string='CCSS Total (Obrero + Patronal)', currency_field='currency_id')
    pending_payrolls = fields.Integer(compute='_compute_metrics', string='Planillas Pendientes')
    paid_payrolls = fields.Integer(compute='_compute_metrics', string='Planillas Pagadas')

    # ── KPIs de RRHH ─────────────────────────────────────────────────
    employees_anniversary = fields.Integer(
        compute='_compute_hr_kpis', string='Aniversarios este mes'
    )
    employees_negative_vacation = fields.Integer(
        compute='_compute_hr_kpis', string='Empleados con vacaciones negativas'
    )
    active_loans_count = fields.Integer(
        compute='_compute_hr_kpis', string='Préstamos Activos'
    )
    active_loans_amount = fields.Monetary(
        compute='_compute_hr_kpis', string='Saldo Préstamos Pendiente (₡)',
        currency_field='currency_id'
    )
    # Comparativo mes anterior
    # ── Alertas urgentes ──────────────────────────────────────────────
    urgent_overdue_loans = fields.Integer(
        compute='_compute_urgent_alerts', string='Cuotas Vencidas'
    )
    urgent_expiring_vacations = fields.Integer(
        compute='_compute_urgent_alerts', string='Vacaciones por Prescribir (2 meses)'
    )
    urgent_active_disabilities = fields.Integer(
        compute='_compute_urgent_alerts', string='Incapacidades Activas'
    )

    # ── Comparación períodos ─────────────────────────────────────────
    compare_date_from = fields.Date(string='Período Anterior Desde')
    compare_date_to   = fields.Date(string='Período Anterior Hasta')
    show_comparison   = fields.Boolean(string='Comparar Períodos', default=False)

    # Deltas computed
    delta_gross   = fields.Monetary(compute='_compute_comparison', currency_field='currency_id', string='Δ Bruto')
    delta_net     = fields.Monetary(compute='_compute_comparison', currency_field='currency_id', string='Δ Neto')
    delta_cost    = fields.Monetary(compute='_compute_comparison', currency_field='currency_id', string='Δ Costo Empresa')
    delta_emp     = fields.Integer(compute='_compute_comparison', string='Δ Empleados')
    compare_gross = fields.Monetary(compute='_compute_comparison', currency_field='currency_id', string='Bruto Período Anterior')
    compare_net   = fields.Monetary(compute='_compute_comparison', currency_field='currency_id', string='Neto Período Anterior')
    compare_cost  = fields.Monetary(compute='_compute_comparison', currency_field='currency_id', string='Costo Período Anterior')
    new_employees = fields.Char(compute='_compute_comparison', string='Nuevos Ingresos')
    left_employees = fields.Char(compute='_compute_comparison', string='Salidas')

    prev_total_gross = fields.Monetary(
        compute='_compute_hr_kpis', string='Nómina Mes Anterior (₡)',
        currency_field='currency_id'
    )
    variation_pct = fields.Float(
        compute='_compute_hr_kpis', string='Variación vs mes anterior (%)',
        digits=(5, 2)
    )

    @api.depends('company_id', 'date_from', 'date_to')
    @api.depends('company_id', 'date_from', 'date_to',
             'compare_date_from', 'compare_date_to', 'show_comparison')
    def _compute_comparison(self):
        for rec in self:
            if not rec.show_comparison or not rec.compare_date_from or not rec.compare_date_to:
                rec.delta_gross = rec.delta_net = rec.delta_cost = 0.0
                rec.delta_emp = 0
                rec.compare_gross = rec.compare_net = rec.compare_cost = 0.0
                rec.new_employees = rec.left_employees = ''
                continue

            def get_period_data(df, dt):
                ps = self.env['planilla.payslip.cr'].search([
                    ('company_id', '=', rec.company_id.id),
                    ('state', '=', 'paid'),
                    ('date_from', '>=', df), ('date_to', '<=', dt),
                ])
                return {
                    'gross': sum(ps.mapped('gross_salary')),
                    'net':   sum(ps.mapped('net_salary')),
                    'cost':  sum(ps.mapped('total_employer_cost')),
                    'emps':  set(ps.mapped('employee_id.id')),
                }

            curr = get_period_data(rec.date_from, rec.date_to)
            prev = get_period_data(rec.compare_date_from, rec.compare_date_to)

            rec.compare_gross = prev['gross']
            rec.compare_net   = prev['net']
            rec.compare_cost  = prev['cost']
            rec.delta_gross   = curr['gross'] - prev['gross']
            rec.delta_net     = curr['net']   - prev['net']
            rec.delta_cost    = curr['cost']  - prev['cost']
            rec.delta_emp     = len(curr['emps']) - len(prev['emps'])

            new_ids  = curr['emps'] - prev['emps']
            left_ids = prev['emps'] - curr['emps']
            if new_ids:
                new_names = self.env['hr.employee'].browse(list(new_ids)).mapped('name')
                rec.new_employees = ', '.join(new_names[:5]) + (f' (+{len(new_names)-5} más)' if len(new_names) > 5 else '')
            else:
                rec.new_employees = 'Ninguno'
            if left_ids:
                left_names = self.env['hr.employee'].browse(list(left_ids)).mapped('name')
                rec.left_employees = ', '.join(left_names[:5]) + (f' (+{len(left_names)-5} más)' if len(left_names) > 5 else '')
            else:
                rec.left_employees = 'Ninguno'

    @api.depends('company_id', 'date_from', 'date_to')
    def _compute_urgent_alerts(self):
        today = date.today()
        threshold_vac = today + relativedelta(months=2)

        for rec in self:
            company = rec.company_id

            # 1. Cuotas de préstamos vencidas (due_date < hoy, state=pending)
            overdue = self.env['planilla.loan.installment'].search_count([
                ('loan_id.employee_id.company_id', '=', company.id),
                ('state', '=', 'pending'),
                ('due_date', '<', today),
            ])
            rec.urgent_overdue_loans = overdue

            # 2. Empleados con vacaciones próximas a prescribir (Art. 156 CT)
            # Acumuladas hace >22 meses y sin tomar vacaciones recientes
            employees_at_risk = 0
            # L2 FIX: batch query en vez de N+1
            employees = self.env['hr.employee'].search([
                ('company_id', '=', company.id),
                ('active', '=', True),
                ('entry_date', '!=', False),
                ('vacation_days_available', '>', 0),
            ])
            if employees:
                emp_ids = employees.ids
                # Traer la última vacación de TODOS los empleados en una sola query
                last_vacs = self.env['planilla.vacation.payment'].read_group(
                    domain=[
                        ('employee_id', 'in', emp_ids),
                        ('state', 'in', ('approved', 'paid')),
                        ('vacation_type', '=', 'disfrutadas'),
                    ],
                    fields=['employee_id', 'date_from:max'],
                    groupby=['employee_id'],
                )
                last_vac_by_emp = {
                    lv['employee_id'][0]: lv['date_from']
                    for lv in last_vacs if lv.get('date_from')
                }
                for emp in employees:
                    ref_date = last_vac_by_emp.get(emp.id) or emp.entry_date
                    if ref_date and (today - ref_date).days / 30 >= 22:
                        employees_at_risk += 1
            rec.urgent_expiring_vacations = employees_at_risk

            # 3. Incapacidades activas (confirmed, no vencidas)
            rec.urgent_active_disabilities = self.env['planilla.disability'].search_count([
                ('employee_id.company_id', '=', company.id),
                ('state', '=', 'confirmed'),
            ])

    def action_open_overdue_loans(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Cuotas Vencidas',
            'res_model': 'planilla.loan.installment',
            'view_mode': 'list',
            'domain': [
                ('loan_id.employee_id.company_id', '=', self.company_id.id),
                ('state', '=', 'pending'),
                ('due_date', '<', date.today()),
            ],
        }

    def action_open_expiring_vacations(self):
        employees = self.env['hr.employee'].search([
            ('company_id', '=', self.company_id.id),
            ('active', '=', True),
            ('vacation_balance_alert', '=', False),
            ('vacation_days_available', '>', 0),
        ])
        # L2 FIX: batch query en vez de N+1
        emp_ids = employees.ids
        last_vacs = self.env['planilla.vacation.payment'].read_group(
            domain=[
                ('employee_id', 'in', emp_ids),
                ('state', 'in', ('approved', 'paid')),
                ('vacation_type', '=', 'disfrutadas'),
            ],
            fields=['employee_id', 'date_from:max'],
            groupby=['employee_id'],
        )
        last_vac_by_emp = {
            lv['employee_id'][0]: lv['date_from']
            for lv in last_vacs if lv.get('date_from')
        }
        at_risk_ids = []
        for emp in employees:
            ref_date = last_vac_by_emp.get(emp.id) or emp.entry_date
            if ref_date and (date.today() - ref_date).days / 30 >= 22:
                at_risk_ids.append(emp.id)
        return {
            'type': 'ir.actions.act_window',
            'name': 'Vacaciones por Prescribir',
            'res_model': 'hr.employee',
            'view_mode': 'list,form',
            'domain': [('id', 'in', at_risk_ids)],
        }

    def action_open_active_disabilities(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Incapacidades Activas',
            'res_model': 'planilla.disability',
            'view_mode': 'list,form',
            'domain': [
                ('employee_id.company_id', '=', self.company_id.id),
                ('state', '=', 'confirmed'),
            ],
        }

    def _compute_metrics(self):
        for rec in self:
            rec.active_employees = self.env['hr.employee'].search_count([
                ('active', '=', True),
                ('employee_status_id.is_active_payroll', '=', True),
            ])

            if not rec.date_from or not rec.date_to:
                rec.payslips_count = 0
                rec.total_gross = 0
                rec.total_net = 0
                rec.total_employer_cost = 0
                rec.total_ccss = 0
                rec.pending_payrolls = 0
                rec.paid_payrolls = 0
                continue

            payslips = self.env['planilla.payslip.cr'].search([
                ('date_from', '<=', rec.date_to),
                ('date_to', '>=', rec.date_from),
                ('state', '=', 'done'),
                ('company_id', '=', rec.company_id.id),
            ])
            rec.payslips_count = len(payslips)

            # Convertir todos los montos a la moneda de la compañía para sumar correctamente
            company_currency = rec.company_id.currency_id or self.env.ref('base.USD')
            rec.currency_id = company_currency.id

            total_gross = 0.0
            total_net = 0.0
            total_employer_cost = 0.0
            total_ccss = 0.0

            for slip in payslips:
                slip_currency = slip.currency_id or company_currency
                # Convertir a moneda de la compañía
                total_gross += slip_currency._convert(
                    slip.gross_salary, company_currency,
                    rec.company_id, slip.date_to or fields.Date.today()
                )
                total_net += slip_currency._convert(
                    slip.net_salary, company_currency,
                    rec.company_id, slip.date_to or fields.Date.today()
                )
                total_employer_cost += slip_currency._convert(
                    slip.total_employer_cost, company_currency,
                    rec.company_id, slip.date_to or fields.Date.today()
                )
                total_ccss += slip_currency._convert(
                    slip.ccss_employee + slip.ccss_employer, company_currency,
                    rec.company_id, slip.date_to or fields.Date.today()
                )

            rec.total_gross = round(total_gross, 2)
            rec.total_net = round(total_net, 2)
            rec.total_employer_cost = round(total_employer_cost, 2)
            rec.total_ccss = round(total_ccss, 2)

            rec.pending_payrolls = self.env['planilla.run.cr'].search_count([
                ('date_start', '<=', rec.date_to),
                ('date_end', '>=', rec.date_from),
                ('state', 'in', ('draft', 'confirmed')),
                ('company_id', '=', rec.company_id.id),
            ])
            rec.paid_payrolls = self.env['planilla.run.cr'].search_count([
                ('date_start', '<=', rec.date_to),
                ('date_end', '>=', rec.date_from),
                ('state', '=', 'done'),
                ('company_id', '=', rec.company_id.id),
            ])

    @api.depends('company_id', 'date_from', 'date_to')
    def _compute_hr_kpis(self):
        for rec in self:
            today = date.today()
            company = rec.company_id

            # ── Aniversarios este mes ──────────────────────────────────
            employees = self.env['hr.employee'].search([
                ('active', '=', True),
                ('entry_date', '!=', False),
                ('company_id', '=', company.id),
            ])
            anniversaries = 0
            for emp in employees:
                if emp.entry_date.month == today.month and emp.entry_date.day <= today.day:
                    years = today.year - emp.entry_date.year
                    if years > 0:
                        anniversaries += 1
            rec.employees_anniversary = anniversaries

            # ── Vacaciones negativas ───────────────────────────────────
            rec.employees_negative_vacation = self.env['hr.employee'].search_count([
                ('active', '=', True),
                ('vacation_balance_alert', '=', True),
                ('company_id', '=', company.id),
            ])

            # ── Préstamos activos ──────────────────────────────────────
            loans = self.env['planilla.employee.loan'].search([
                ('state', 'in', ('approved', 'active')),
                ('employee_id.company_id', '=', company.id),
            ])
            rec.active_loans_count  = len(loans)
            rec.active_loans_amount = sum(loans.mapped('amount_pending'))

            # ── Comparativo mes anterior ───────────────────────────────
            if rec.date_from and rec.date_to:
                prev_from = rec.date_from - relativedelta(months=1)
                prev_to   = rec.date_to   - relativedelta(months=1)
                prev_slips = self.env['planilla.payslip.cr'].search([
                    ('date_from', '>=', prev_from),
                    ('date_to',   '<=', prev_to),
                    ('state', '=', 'done'),
                    ('company_id', '=', company.id),
                ])
                prev_gross = sum(prev_slips.mapped('gross_salary'))
                rec.prev_total_gross = round(prev_gross, 2)
                if prev_gross > 0 and rec.total_gross:
                    rec.variation_pct = round(
                        ((rec.total_gross - prev_gross) / prev_gross) * 100, 2
                    )
                else:
                    rec.variation_pct = 0.0
            else:
                rec.prev_total_gross = 0.0
                rec.variation_pct = 0.0

    def action_open_anniversary_employees(self):
        """Abre lista de empleados con aniversario este mes."""
        today = date.today()
        all_emps = self.env['hr.employee'].search([
            ('active', '=', True), ('entry_date', '!=', False),
            ('company_id', '=', self.company_id.id),
        ])
        ids = [e.id for e in all_emps
               if e.entry_date.month == today.month
               and e.entry_date.day <= today.day
               and (today.year - e.entry_date.year) > 0]
        return {
            'type': 'ir.actions.act_window',
            'name': f'Aniversarios — {today.strftime("%B %Y")}',
            'res_model': 'hr.employee',
            'view_mode': 'list,form',
            'domain': [('id', 'in', ids)],
        }

    def action_open_negative_vacation_employees(self):
        """Abre lista de empleados con saldo negativo de vacaciones."""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Empleados con Vacaciones Negativas',
            'res_model': 'hr.employee',
            'view_mode': 'list,form',
            'domain': [
                ('active', '=', True),
                ('vacation_balance_alert', '=', True),
                ('company_id', '=', self.company_id.id),
            ],
        }

    def action_open_active_loans(self):
        """Abre lista de préstamos activos."""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Préstamos Activos',
            'res_model': 'planilla.employee.loan',
            'view_mode': 'list,form',
            'domain': [
                ('state', 'in', ('approved', 'active')),
                ('employee_id.company_id', '=', self.company_id.id),
            ],
        }

    def action_open_payrolls(self):
        domain = []
        if self.date_from and self.date_to:
            domain = [('date_start', '<=', self.date_to), ('date_end', '>=', self.date_from)]
        return {
            'type': 'ir.actions.act_window',
            'name': 'Planillas',
            'res_model': 'planilla.run.cr',
            'view_mode': 'list,form',
            'domain': domain,
        }

    def action_open_payslips(self):
        domain = [('state', '=', 'done')]
        if self.date_from and self.date_to:
            domain += [('date_from', '<=', self.date_to), ('date_to', '>=', self.date_from)]
        return {
            'type': 'ir.actions.act_window',
            'name': 'Boletas de Pago',
            'res_model': 'planilla.payslip.cr',
            'view_mode': 'list,form',
            'domain': domain,
        }

    def action_open_reports(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Reportes de Planilla',
            'res_model': 'planilla.report.wizard',
            'view_mode': 'form',
            'target': 'new',
        }

    def action_refresh(self):
        """Recalcula las métricas con el rango actual."""
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'planilla.dashboard',
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_date_from': self.date_from,
                'default_date_to': self.date_to,
            }
        }
