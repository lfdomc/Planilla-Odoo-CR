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

    # Metricas
    active_employees = fields.Integer(compute='_compute_metrics', string='Empleados Activos')
    payslips_count = fields.Integer(compute='_compute_metrics', string='Boletas Pagadas')
    total_gross = fields.Monetary(compute='_compute_metrics', string='Salario Bruto Total', currency_field='currency_id')
    total_net = fields.Monetary(compute='_compute_metrics', string='Salario Neto Total', currency_field='currency_id')
    total_employer_cost = fields.Monetary(compute='_compute_metrics', string='Costo Total Empresa', currency_field='currency_id')
    total_ccss = fields.Monetary(compute='_compute_metrics', string='CCSS Total (Obrero + Patronal)', currency_field='currency_id')
    pending_payrolls = fields.Integer(compute='_compute_metrics', string='Planillas Pendientes')
    paid_payrolls = fields.Integer(compute='_compute_metrics', string='Planillas Pagadas')

    # -- KPIs de RRHH -------------------------------------------------
    employees_anniversary = fields.Integer(
        compute='_compute_hr_kpis', string='Aniversarios este mes'
    )
    employees_negative_vacation = fields.Integer(
        compute='_compute_hr_kpis', string='Empleados con vacaciones negativas'
    )
    active_loans_count = fields.Integer(
        compute='_compute_hr_kpis', string='Prestamos Activos'
    )
    active_loans_amount = fields.Monetary(
        compute='_compute_hr_kpis', string='Saldo Prestamos Pendiente (CRC)',
        currency_field='currency_id'
    )
    # Comparativo mes anterior
    # -- Alertas urgentes ----------------------------------------------
    urgent_overdue_loans = fields.Integer(
        compute='_compute_urgent_alerts', string='Cuotas Vencidas'
    )
    urgent_expiring_vacations = fields.Integer(
        compute='_compute_urgent_alerts', string='Vacaciones por Prescribir (2 meses)'
    )
    urgent_active_disabilities = fields.Integer(
        compute='_compute_urgent_alerts', string='Incapacidades Activas'
    )

    # -- Comparacion periodos -----------------------------------------
    compare_date_from = fields.Date(string='Periodo Anterior Desde')
    compare_date_to   = fields.Date(string='Periodo Anterior Hasta')
    show_comparison   = fields.Boolean(string='Comparar Periodos', default=False)

    # Deltas computed
    delta_gross   = fields.Monetary(compute='_compute_comparison', currency_field='currency_id', string=' Bruto')
    delta_net     = fields.Monetary(compute='_compute_comparison', currency_field='currency_id', string=' Neto')
    delta_cost    = fields.Monetary(compute='_compute_comparison', currency_field='currency_id', string=' Costo Empresa')
    delta_emp     = fields.Integer(compute='_compute_comparison', string=' Empleados')
    compare_gross = fields.Monetary(compute='_compute_comparison', currency_field='currency_id', string='Bruto Periodo Anterior')
    compare_net   = fields.Monetary(compute='_compute_comparison', currency_field='currency_id', string='Neto Periodo Anterior')
    compare_cost  = fields.Monetary(compute='_compute_comparison', currency_field='currency_id', string='Costo Periodo Anterior')
    new_employees = fields.Char(compute='_compute_comparison', string='Nuevos Ingresos')
    left_employees = fields.Char(compute='_compute_comparison', string='Salidas')

    prev_total_gross = fields.Monetary(
        compute='_compute_hr_kpis', string='Nomina Mes Anterior (CRC)',
        currency_field='currency_id'
    )
    variation_pct = fields.Float(
        compute='_compute_hr_kpis', string='Variacion vs mes anterior (%)',
        digits=(5, 2)
    )

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
                    ('state', '=', 'done'),  # FIX-C19: estado pagado es 'done', no 'paid'
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
                rec.new_employees = ', '.join(new_names[:5]) + (f' (+{len(new_names)-5} mas)' if len(new_names) > 5 else '')
            else:
                rec.new_employees = 'Ninguno'
            if left_ids:
                left_names = self.env['hr.employee'].browse(list(left_ids)).mapped('name')
                rec.left_employees = ', '.join(left_names[:5]) + (f' (+{len(left_names)-5} mas)' if len(left_names) > 5 else '')
            else:
                rec.left_employees = 'Ninguno'

    @api.depends('company_id', 'date_from', 'date_to')
    def _compute_urgent_alerts(self):
        today = date.today()
        threshold_vac = today + relativedelta(months=2)

        for rec in self:
            company = rec.company_id

            # 1. Cuotas de prestamos vencidas (due_date < hoy, state=pending)
            overdue = self.env['planilla.loan.installment'].search_count([
                ('loan_id.employee_id.company_id', '=', company.id),
                ('state', '=', 'pending'),
                ('due_date', '<', today),
            ])
            rec.urgent_overdue_loans = overdue

            # 2. Empleados con vacaciones proximas a prescribir (Art. 156 CT)
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
                # Traer la ultima vacacion de TODOS los empleados en una sola query
                # FIX v53: campo correcto es date_start, no date_from (no existe en vacation.payment)
                last_vacs = self.env['planilla.vacation.payment'].read_group(
                    domain=[
                        ('employee_id', 'in', emp_ids),
                        ('state', 'in', ('approved', 'paid')),
                        ('vacation_type', '=', 'disfrutadas'),
                    ],
                    fields=['employee_id', 'date_start:max'],
                    groupby=['employee_id'],
                )
                last_vac_by_emp = {
                    lv['employee_id'][0]: lv['date_start']
                    for lv in last_vacs if lv.get('date_start')
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
        # FIX v53: campo correcto es date_start, no date_from
        last_vacs = self.env['planilla.vacation.payment'].read_group(
            domain=[
                ('employee_id', 'in', emp_ids),
                ('state', 'in', ('approved', 'paid')),
                ('vacation_type', '=', 'disfrutadas'),
            ],
            fields=['employee_id', 'date_start:max'],
            groupby=['employee_id'],
        )
        last_vac_by_emp = {
            lv['employee_id'][0]: lv['date_start']
            for lv in last_vacs if lv.get('date_start')
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

    @api.depends('company_id', 'date_from', 'date_to')
    def _compute_hr_kpis(self):
        """
        KPIs de RRHH: aniversarios, vacaciones negativas, prestamos activos,
        nomina anterior y variacion porcentual.
        """
        today = fields.Date.context_today(self)
        for rec in self:
            company = rec.company_id

            # -- Aniversarios laborales este mes ----------------------
            emp_all = self.env['hr.employee'].search([
                ('company_id', '=', company.id),
                ('active', '=', True),
                ('entry_date', '!=', False),
            ])
            rec.employees_anniversary = sum(
                1 for emp in emp_all
                if emp.entry_date.month == today.month
                and emp.entry_date.year < today.year
            )

            # -- Vacaciones negativas ----------------------------------
            rec.employees_negative_vacation = self.env['hr.employee'].search_count([
                ('company_id', '=', company.id),
                ('active', '=', True),
                ('vacation_days_available', '<', 0),
            ])

            # -- Prestamos activos -------------------------------------
            loans = self.env['planilla.employee.loan'].search([
                ('employee_id.company_id', '=', company.id),
                ('state', 'in', ('approved', 'active')),
            ])
            rec.active_loans_count  = len(loans)
            rec.active_loans_amount = round(sum(loans.mapped('amount_pending')), 2)

            # -- Nomina mes anterior y variacion -----------------------
            rec.prev_total_gross = 0.0
            rec.variation_pct    = 0.0
            if rec.date_from and rec.date_to:
                from dateutil.relativedelta import relativedelta
                prev_from = rec.date_from - relativedelta(months=1)
                prev_to   = rec.date_to   - relativedelta(months=1)
                prev_groups = self.env['planilla.payslip.cr'].read_group(
                    domain=[
                        ('date_from', '<=', prev_to),
                        ('date_to',   '>=', prev_from),
                        ('state', '=', 'done'),
                        ('company_id', '=', company.id),
                    ],
                    fields=['gross_salary:sum'],
                    groupby=[],
                )
                prev_gross = (prev_groups[0].get('gross_salary') or 0.0) if prev_groups else 0.0
                rec.prev_total_gross = round(prev_gross, 2)
                curr_groups = self.env['planilla.payslip.cr'].read_group(
                    domain=[
                        ('date_from', '<=', rec.date_to),
                        ('date_to',   '>=', rec.date_from),
                        ('state', '=', 'done'),
                        ('company_id', '=', company.id),
                    ],
                    fields=['gross_salary:sum'],
                    groupby=[],
                )
                curr_gross = (curr_groups[0].get('gross_salary') or 0.0) if curr_groups else 0.0
                if prev_gross:
                    rec.variation_pct = round((curr_gross - prev_gross) / prev_gross * 100, 2)


    def _compute_metrics(self):
        """FIX N-01 v54: Reemplaza loop N+1 por read_group() -- una sola query SQL."""
        for rec in self:
            rec.active_employees = self.env['hr.employee'].search_count([
                ('active', '=', True),
                ('employee_status_id.is_active_payroll', '=', True),
            ])

            if not rec.date_from or not rec.date_to:
                rec.payslips_count = rec.total_gross = rec.total_net = 0
                rec.total_employer_cost = rec.total_ccss = 0
                rec.pending_payrolls = rec.paid_payrolls = 0
                continue

            slip_domain = [
                ('date_from', '<=', rec.date_to),
                ('date_to', '>=', rec.date_from),
                ('state', '=', 'done'),
                ('company_id', '=', rec.company_id.id),
            ]

            # Una sola query SQL en lugar de N llamadas _convert()
            groups = self.env['planilla.payslip.cr'].read_group(
                domain=slip_domain,
                fields=[
                    'gross_salary:sum', 'net_salary:sum',
                    'total_employer_cost:sum',
                    'ccss_employee:sum', 'ccss_employer:sum',
                ],
                groupby=[],
            )
            g = groups[0] if groups else {}
            rec.payslips_count      = g.get('__count', 0)
            rec.total_gross         = round(g.get('gross_salary', 0.0) or 0.0, 2)
            rec.total_net           = round(g.get('net_salary', 0.0) or 0.0, 2)
            rec.total_employer_cost = round(g.get('total_employer_cost', 0.0) or 0.0, 2)
            rec.total_ccss          = round(
                (g.get('ccss_employee', 0.0) or 0.0) +
                (g.get('ccss_employer', 0.0) or 0.0), 2
            )

            run_groups = self.env['planilla.run.cr'].read_group(
                domain=[
                    ('date_start', '<=', rec.date_to),
                    ('date_end', '>=', rec.date_from),
                    ('company_id', '=', rec.company_id.id),
                ],
                fields=['state'],
                groupby=['state'],
            )
            rec.pending_payrolls = sum(
                g.get('__count', 0) for g in run_groups if g['state'] in ('draft', 'confirmed')
            )
            rec.paid_payrolls = sum(
                g.get('__count', 0) for g in run_groups if g['state'] == 'done'
            )

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
            'name': f'Aniversarios -- {today.strftime("%B %Y")}',
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
        """Abre lista de prestamos activos."""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Prestamos Activos',
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
        """Recalcula las metricas con el rango actual."""
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
