from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError
import datetime


class PayslipCR(models.Model):
    _name = 'planilla.payslip.cr'
    _description = 'Boleta de Pago'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_to desc, employee_id'

    name = fields.Char(string='Referencia', compute='_compute_name', store=True)
    employee_id = fields.Many2one(
        'hr.employee', string='Empleado', required=True, tracking=True,
        ondelete='restrict',
    )
    branch_id = fields.Many2one(related='employee_id.branch_id', string='Sucursal', store=True)
    company_id = fields.Many2one(
        'res.company', string='Compañía',
        required=True, default=lambda self: self.env.company
    )
    payroll_run_id = fields.Many2one('planilla.run.cr', string='Planilla', ondelete='cascade')
    notes = fields.Text(string='Observaciones', help='Notas internas que aparecen en el PDF de la boleta.')
    payroll_calendar_id = fields.Many2one(
        related='employee_id.payroll_calendar_id', string='Calendarización', store=True
    )
    date_from = fields.Date(string='Desde', required=True)
    date_to = fields.Date(string='Hasta', required=True)
    currency_id = fields.Many2one(related='employee_id.currency_id', store=True)

    # Ingresos
    base_salary = fields.Monetary(string='Salario Base', currency_field='currency_id', compute='_compute_base_salary', store=True)
    overtime_amount = fields.Monetary(string='Monto Horas Extras', currency_field='currency_id', compute='_compute_extras', store=True)
    vacation_amount = fields.Monetary(string='Monto Vacaciones', currency_field='currency_id', compute='_compute_extras', store=True)
    other_income = fields.Monetary(string='Otros Ingresos', currency_field='currency_id')
    gross_salary = fields.Monetary(string='Salario Bruto', currency_field='currency_id', compute='_compute_gross', store=True)

    # Deducciones Obrero
    ccss_employee = fields.Monetary(string='CCSS Obrero (10.83%)', currency_field='currency_id', compute='_compute_deductions', store=True,
        help='Cuota obrera CCSS: 10.83% del salario bruto. Fuente: Decreto Ejecutivo vigente de tasas CCSS. '
             'Detalle: SEM 5.50%, IVM 3.84%, BANCO POPULAR 1%, LPT 0.50%, ASFA 0.25%, FODESAF 0.50%, INA 0.08%.')
    income_tax = fields.Monetary(string='Impuesto Renta', currency_field='currency_id', compute='_compute_deductions', store=True)
    other_deductions = fields.Monetary(string='Otras Deducciones', currency_field='currency_id')
    total_employee_deductions = fields.Monetary(string='Total Deducciones Obrero', currency_field='currency_id', compute='_compute_totals', store=True)

    # Cargas Patronales
    ccss_employer = fields.Monetary(string='CCSS Patronal (26.83%)', currency_field='currency_id', compute='_compute_deductions', store=True,
        help='Cuota patronal CCSS: 26.83% del salario bruto. Fuente: Decreto Ejecutivo vigente de tasas CCSS. '
             'Detalle: SEM 9.25%, IVM 5.75%, BANCO POPULAR 0.25%, LPT 1.50%, ASFA 0.25%, FODESAF 5%, INA 1.50%, IMAS 0.50%, FUNDACIONES 0.50%, FCE 1.50%.')
    ins_employer = fields.Monetary(string='INS Patronal (Riesgos del Trabajo)', currency_field='currency_id', compute='_compute_deductions', store=True,
        help='Prima del Seguro de Riesgos del Trabajo: tasa referencial del 1% sobre el salario bruto. '
             'La tasa exacta depende de la clase de riesgo asignada por el INS segun la actividad economica. '
             'Fuente: INS Costa Rica - Ley N.° 6727 Riesgos del Trabajo.')
    aguinaldo_provision = fields.Monetary(string='Provisión Aguinaldo (8.33%)', currency_field='currency_id', compute='_compute_deductions', store=True,
        help='Provision mensual de aguinaldo: 1/12 del salario anual (8.33%). '
             'Fuente: Codigo de Trabajo de Costa Rica, Ley N.° 2 - Capitulo VIII, Articulo 228 y ss.')
    cesantia_provision = fields.Monetary(string='Provisión Cesantía (5.33%)', currency_field='currency_id', compute='_compute_deductions', store=True,
        help='Provision mensual de cesantia: equivale a 8 dias por ano trabajado (aprox 5.33% mensual para contratos indefinidos). '
             'Fuente: Codigo de Trabajo de Costa Rica, Articulo 29 y ss.')
    vacation_provision = fields.Monetary(string='Provisión Vacaciones (4.16%)', currency_field='currency_id', compute='_compute_deductions', store=True,
        help='Provision mensual de vacaciones: 2 semanas por ano laborado (1/24 del salario anual = 4.16%). '
             'Fuente: Codigo de Trabajo de Costa Rica, Articulo 153 y ss.')
    total_employer_cost = fields.Monetary(string='Costo Total Patronal', currency_field='currency_id', compute='_compute_totals', store=True)
    net_salary = fields.Monetary(string='Salario Neto', currency_field='currency_id', compute='_compute_totals', store=True)

    # Asistencias
    attendance_hours = fields.Float(string='Horas Trabajadas', compute='_compute_attendance_hours', store=True)
    attendance_details = fields.Text(string='Detalle de Asistencias', compute='_compute_attendance_hours', store=True)
    calculation_method = fields.Selection(related='employee_id.payroll_calculation_method', string='Método de Cálculo', store=True)

    # Novedades
    disability_ids = fields.One2many('planilla.disability', 'payslip_id', string='Incapacidades')
    disability_days = fields.Integer(string='Días Incapacidad', compute='_compute_extras', store=True)
    ccss_subsidy_total = fields.Monetary(
        string='Subsidio CCSS (Incapacidades)', currency_field='currency_id',
        compute='_compute_extras', store=True,
        help='Monto que cubre la CCSS por incapacidades > 3 días. No afecta el costo patronal.'
    )
    employer_disability_cost = fields.Monetary(
        string='Costo Patrono por Incapacidades', currency_field='currency_id',
        compute='_compute_extras', store=True,
        help='Primeros 3 días a cargo del patrono + porcentaje de días restantes.'
    )
    overtime_ids = fields.One2many('planilla.overtime', 'payslip_id', string='Horas Extras')
    vacation_ids = fields.One2many('planilla.vacation.payment', 'payslip_id', string='Vacaciones')
    deduction_line_ids = fields.One2many('planilla.payslip.deduction.line', 'payslip_id', string='Deducciones Adicionales')

    state = fields.Selection([
        ('draft', 'Borrador'),
        ('confirmed', 'Confirmado'),
        ('done', 'Pagado'),
        ('cancelled', 'Cancelado'),
    ], string='Estado', default='draft', tracking=True)

    move_id = fields.Many2one('account.move', string='Asiento Contable')

    # ── Constraints ───────────────────────────────────────────────────
    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for rec in self:
            if rec.date_from and rec.date_to and rec.date_from > rec.date_to:
                raise ValidationError('La fecha inicio no puede ser mayor a la fecha fin.')

    @api.constrains('employee_id', 'payroll_run_id')
    def _check_unique_payslip_per_run(self):
        for rec in self:
            if not rec.payroll_run_id:
                continue
            duplicates = self.search([
                ('employee_id', '=', rec.employee_id.id),
                ('payroll_run_id', '=', rec.payroll_run_id.id),
                ('id', '!=', rec.id),
                ('state', '!=', 'cancelled'),
            ])
            if duplicates:
                raise ValidationError(
                    f'El empleado {rec.employee_id.name} ya tiene una boleta '
                    f'en la planilla {rec.payroll_run_id.name}.'
                )

    # ── Computes ──────────────────────────────────────────────────────
    @api.depends('employee_id', 'date_from', 'date_to')
    def _compute_name(self):
        for rec in self:
            emp = rec.employee_id.name or ''
            date_str = str(rec.date_to)[:7] if rec.date_to else ''
            rec.name = f'BOL - {emp} - {date_str}'

    @api.depends('employee_id', 'date_from', 'date_to', 'attendance_hours')
    def _compute_base_salary(self):
        for rec in self:
            emp = rec.employee_id
            if not emp:
                rec.base_salary = 0.0
                continue
            if (emp.payroll_calculation_method or 'fixed') == 'attendance':
                if not rec.date_from or not rec.date_to or not emp.base_salary:
                    rec.base_salary = 0.0
                    continue
                hours_per_day = emp.schedule_type_id.hours_per_day if emp.schedule_type_id else 8.0
                monthly_hours = hours_per_day * 30.0
                hourly_rate = emp.base_salary / monthly_hours if monthly_hours else 0.0
                rec.base_salary = round(hourly_rate * (rec.attendance_hours or 0.0), 2)
            else:
                rec.base_salary = emp.base_salary or 0.0

    @api.depends('employee_id', 'date_from', 'date_to')
    def _compute_attendance_hours(self):
        for rec in self:
            if (not rec.employee_id or not rec.date_from or not rec.date_to
                    or rec.employee_id.payroll_calculation_method != 'attendance'):
                rec.attendance_hours = 0.0
                rec.attendance_details = ''
                continue
            dt_from = fields.Datetime.to_datetime(rec.date_from)
            dt_to = fields.Datetime.to_datetime(rec.date_to) + datetime.timedelta(days=1)
            attendances = self.env['hr.attendance'].search([
                ('employee_id', '=', rec.employee_id.id),
                ('check_in', '>=', dt_from),
                ('check_in', '<', dt_to),
            ])
            rec.attendance_hours = round(sum(a.worked_hours for a in attendances), 2)
            if attendances:
                rec.attendance_details = '\n'.join(
                    f"{str(a.check_in)[:10]}: {round(a.worked_hours, 2)}h"
                    for a in attendances.sorted('check_in')
                )
            else:
                rec.attendance_details = 'Sin registros de asistencia en el período.'

    @api.depends('overtime_ids.amount', 'overtime_ids.state',
                 'vacation_ids.total_amount', 'vacation_ids.state',
                 'disability_ids.days', 'disability_ids.ccss_subsidy',
                 'disability_ids.employer_cost', 'disability_ids.state')
    def _compute_extras(self):
        for rec in self:
            rec.overtime_amount  = sum(o.amount for o in rec.overtime_ids if o.state == 'approved')
            rec.vacation_amount  = sum(v.total_amount for v in rec.vacation_ids if v.state == 'approved')
            active_dis = rec.disability_ids.filtered(lambda d: d.state in ('confirmed', 'paid'))
            rec.disability_days        = sum(d.days for d in active_dis)
            rec.ccss_subsidy_total     = round(sum(d.ccss_subsidy for d in active_dis), 2)
            rec.employer_disability_cost = round(sum(d.employer_cost for d in active_dis), 2)

    @api.depends('base_salary', 'overtime_amount', 'vacation_amount', 'other_income')
    def _compute_gross(self):
        for rec in self:
            rec.gross_salary = (
                (rec.base_salary or 0.0) + (rec.overtime_amount or 0.0) +
                (rec.vacation_amount or 0.0) + (rec.other_income or 0.0)
            )

    @api.depends('gross_salary')
    def _compute_deductions(self):
        rh = self.env['planilla.rate.helper']
        ccss_emp  = rh.get_ccss_employee_rate()
        ccss_pat  = rh.get_ccss_employer_rate()
        agu_rate  = rh.get_aguinaldo_rate()
        ces_rate  = rh.get_cesantia_rate()
        vac_rate  = rh.get_vacation_rate()
        for rec in self:
            g = rec.gross_salary or 0.0
            rec.ccss_employee      = round(g * ccss_emp, 2)
            rec.income_tax         = round(rec._calc_income_tax(g), 2)
            rec.ccss_employer      = round(g * ccss_pat, 2)
            risk                   = rec.employee_id.ins_risk_class or 'II'
            rec.ins_employer       = round(g * rh.get_ins_rate(risk), 2)
            rec.aguinaldo_provision = round(g * agu_rate, 2)
            rec.cesantia_provision  = round(g * ces_rate, 2)
            rec.vacation_provision  = round(g * vac_rate, 2)
            # Auto-agregar cuotas de préstamos pendientes en el periodo
            if rec.state == 'draft' and rec.date_from and rec.date_to:
                rec._sync_loan_deductions()

    def _sync_loan_deductions(self):
        """Sincroniza cuotas de préstamos activos del empleado con las líneas de deducción."""
        self.ensure_one()
        # Código de deducción para préstamos
        loan_code = self.env['planilla.deduction.code'].search(
            [('code', '=', 'PRESTAMO')], limit=1
        )
        if not loan_code:
            return
        # Buscar préstamos activos o aprobados del empleado
        loans = self.env['planilla.employee.loan'].search([
            ('employee_id', '=', self.employee_id.id),
            ('state', 'in', ['approved', 'active']),
        ])
        for loan in loans:
            installment = loan.get_pending_installment(self.date_from, self.date_to)
            if not installment:
                continue
            # Verificar si ya está en las líneas
            existing = self.deduction_line_ids.filtered(
                lambda l: l.loan_installment_id == installment
            )
            if not existing:
                self.env['planilla.payslip.deduction.line'].create({
                    'payslip_id':          self.id,
                    'deduction_code_id':   loan_code.id,
                    'description':         loan.name,
                    'amount':              installment.amount,
                    'loan_installment_id': installment.id,
                })

    def _calc_income_tax(self, gross):
        """Calculo progresivo de renta usando tramos configurados en la UI."""
        brackets = self.env['planilla.income.tax.bracket'].search(
            [('active', '=', True)], order='sequence asc'
        )
        if not brackets:
            # Fallback si no hay tramos configurados
            if gross <= 929000:
                return 0.0
            elif gross <= 1362000:
                return (gross - 929000) * 0.10
            elif gross <= 2414000:
                return (433000 * 0.10) + ((gross - 1362000) * 0.15)
            elif gross <= 4830000:
                return (433000 * 0.10) + (1052000 * 0.15) + ((gross - 2414000) * 0.20)
            else:
                return (433000 * 0.10) + (1052000 * 0.15) + (2416000 * 0.20) + ((gross - 4830000) * 0.25)
        tax = 0.0
        for bracket in brackets:
            if gross <= bracket.limit_from:
                break
            limit_to = bracket.limit_to if bracket.limit_to else float('inf')
            taxable = min(gross, limit_to) - bracket.limit_from
            if taxable > 0:
                tax += taxable * (bracket.rate / 100)
        return tax

    @api.depends(
        'gross_salary', 'ccss_employee', 'income_tax', 'other_deductions',
        'ccss_employer', 'ins_employer', 'aguinaldo_provision',
        'cesantia_provision', 'vacation_provision', 'deduction_line_ids.amount'
    )
    def _compute_totals(self):
        for rec in self:
            extra = sum(rec.deduction_line_ids.mapped('amount'))
            rec.total_employee_deductions = round(
                (rec.ccss_employee or 0.0) + (rec.income_tax or 0.0) +
                (rec.other_deductions or 0.0) + extra, 2
            )
            rec.total_employer_cost = round(
                (rec.gross_salary or 0.0) + (rec.ccss_employer or 0.0) +
                (rec.ins_employer or 0.0) + (rec.aguinaldo_provision or 0.0) +
                (rec.cesantia_provision or 0.0) + (rec.vacation_provision or 0.0), 2
            )
            # El subsidio CCSS se suma al neto: la CCSS paga parte del salario durante incapacidad
            rec.net_salary = round(
                (rec.gross_salary or 0.0) - rec.total_employee_deductions +
                (rec.ccss_subsidy_total or 0.0), 2
            )

    # ── Validacion pre-confirmacion ───────────────────────────────────
    def _validate_before_confirm(self):
        """Valida que la boleta tenga datos completos y correctos antes de confirmar."""
        errors = []
        warnings = []

        for rec in self:
            emp = rec.employee_id
            prefix = f'[{emp.name}]'

            # ── Datos obligatorios del empleado ──────────────────────
            if not emp.identification_id:
                errors.append(f'{prefix} No tiene numero de cedula/identificacion registrado.')

            if not emp.base_salary or emp.base_salary <= 0:
                errors.append(f'{prefix} El salario base es 0 o no esta configurado.')

            if not rec.payroll_calendar_id:
                errors.append(f'{prefix} No tiene calendarizacion de planilla asignada.')

            # ── Salario minimo legal (referencia 2025: ~360,000 CRC mensual) ──
            if emp.base_salary and 0 < emp.base_salary < 100000:
                warnings.append(
                    f'{prefix} El salario base ({emp.base_salary:,.0f}) parece muy bajo. '
                    f'Verifique que no sea un error.'
                )

            # ── Montos calculados coherentes ─────────────────────────
            if rec.gross_salary <= 0:
                errors.append(f'{prefix} El salario bruto calculado es 0 o negativo ({rec.gross_salary:,.2f}).')

            # ── Validación salario mínimo MTSS ───────────────────────
            min_salary = self.env['planilla.minimum.salary'].get_current_minimum(
                category=rec.employee_id.employee_type_id.name if rec.employee_id.employee_type_id else None
            )
            if min_salary > 0 and rec.base_salary < min_salary:
                errors.append(
                    f'{prefix} El salario base ({rec.base_salary:,.2f}) '
                    f'está por debajo del mínimo MTSS vigente ({min_salary:,.2f}). '
                    f'Corrija el salario o verifique la categoría ocupacional del empleado.'
                )

            if rec.net_salary < 0:
                errors.append(
                    f'{prefix} El salario neto es negativo ({rec.net_salary:,.2f}). '
                    f'Las deducciones superan el salario bruto.'
                )

            if rec.net_salary > rec.gross_salary:
                errors.append(
                    f'{prefix} El salario neto ({rec.net_salary:,.2f}) es mayor al bruto '
                    f'({rec.gross_salary:,.2f}). Verifique las deducciones.'
                )

            # ── CCSS coherente ───────────────────────────────────────
            expected_ccss_emp = round(rec.gross_salary * 0.1083, 2)
            if rec.ccss_employee and abs(rec.ccss_employee - expected_ccss_emp) > 1.0:
                warnings.append(
                    f'{prefix} La cuota CCSS obrero ({rec.ccss_employee:,.2f}) '
                    f'difiere del calculo esperado ({expected_ccss_emp:,.2f}). '
                    f'Verifique si hay deducciones manuales.'
                )

            # ── Periodo cerrado ──────────────────────────────────────
            from .closed_period import PlanillaClosedPeriod
            closed = PlanillaClosedPeriod.is_period_closed(
                self.env, rec.company_id.id,
                rec.date_from, rec.date_to,
                rec.branch_id.id if rec.branch_id else False
            )
            if closed:
                errors.append(
                    f'{prefix} El periodo {rec.date_from} - {rec.date_to} esta cerrado '
                    f'("{closed.name}", cerrado el {closed.closed_date.strftime("%d/%m/%Y")} '
                    f'por {closed.closed_by.name}). No se puede confirmar una boleta en un periodo cerrado.'
                )

            # ── Duplicados en el mismo periodo ───────────────────────
            duplicate = self.env['planilla.payslip.cr'].search([
                ('employee_id', '=', emp.id),
                ('date_from', '=', rec.date_from),
                ('date_to', '=', rec.date_to),
                ('state', 'in', ['confirmed', 'paid', 'done']),
                ('id', '!=', rec.id),
            ], limit=1)
            if duplicate:
                errors.append(
                    f'{prefix} Ya existe una boleta confirmada o pagada para el periodo '
                    f'{rec.date_from} - {rec.date_to} (Ref: {duplicate.name}).'
                )

        if errors:
            raise UserError(
                'No se puede confirmar. Se encontraron los siguientes errores:\n\n' +
                '\n'.join(f'• {e}' for e in errors)
            )

        if warnings:
            # Los warnings se muestran pero no bloquean
            return '\n'.join(warnings)
        return None

    # ── Acciones ──────────────────────────────────────────────────────
    def action_confirm(self):
        # Verificar permisos: solo Aprobador o Admin pueden confirmar
        if not self.env.user.has_group('planilla_cr.group_planilla_aprobador'):
            raise UserError(
                'No tiene permisos para confirmar boletas. '
                'Se requiere el rol de Aprobador de Planilla o superior.'
            )
        for rec in self:
            if rec.state != 'draft':
                raise UserError(f'La boleta {rec.name} no esta en borrador.')
        self._validate_before_confirm()
        for rec in self:
            rec.state = 'confirmed'

    def action_pay(self, skip_accounting=False):
        if not self.env.user.has_group('planilla_cr.group_planilla_aprobador'):
            raise UserError(
                'No tiene permisos para pagar boletas. '
                'Se requiere el rol de Aprobador de Planilla o superior.'
            )
        for rec in self:
            if rec.state != 'confirmed':
                raise UserError(f'La boleta {rec.name} debe estar confirmada para pagar.')
            rec.state = 'done'
            if not skip_accounting:
                rec._create_accounting_entry()
            rec.overtime_ids.filtered(lambda o: o.state == 'approved').write({'state': 'paid'})
            rec.vacation_ids.filtered(lambda v: v.state == 'approved').write({'state': 'paid'})
            rec.disability_ids.filtered(lambda d: d.state == 'confirmed').write({'state': 'paid'})
            # Enviar email al empleado si tiene correo configurado
            if rec.employee_id.work_email:
                try:
                    template = self.env.ref('planilla_cr.email_template_payslip_paid', raise_if_not_found=False)
                    if template:
                        template.send_mail(rec.id, force_send=False)
                except Exception:
                    pass  # No bloquear el flujo si falla el email
            # Marcar cuotas de préstamos como descontadas y verificar si el préstamo quedó saldado
            loan_lines = rec.deduction_line_ids.filtered(lambda l: l.loan_installment_id)
            for line in loan_lines:
                line.loan_installment_id.write({'state': 'deducted', 'payslip_id': rec.id})
                line.loan_installment_id.loan_id.action_activate()
                line.loan_installment_id.loan_id.action_check_paid()
            self.env['planilla.salary.history'].create({
                'employee_id': rec.employee_id.id,
                'salary': rec.net_salary,
                'gross_salary': rec.gross_salary,
                'effective_date': rec.date_to,
                'payslip_id': rec.id,
                'reason': f'Planilla {rec.name}',
            })

    def action_cancel(self):
        for rec in self:
            if rec.state == 'done':
                raise UserError(
                    'No se puede cancelar una boleta ya pagada. '
                    'Revierta el asiento contable primero.'
                )
            if rec.move_id and rec.move_id.state == 'posted':
                rec.move_id.button_cancel()
            # Revertir cuotas de préstamos descontadas en esta boleta
            loan_lines = rec.deduction_line_ids.filtered(lambda l: l.loan_installment_id)
            for line in loan_lines:
                inst = line.loan_installment_id
                if inst.state == 'deducted' and inst.payslip_id == rec:
                    inst.write({'state': 'pending', 'payslip_id': False})
                    if inst.loan_id.state == 'paid':
                        inst.loan_id.write({'state': 'active'})
            # Revertir vacaciones pagadas en esta boleta → volver a 'approved'
            rec.vacation_ids.filtered(
                lambda v: v.state == 'paid'
            ).write({'state': 'approved'})
            # Revertir incapacidades procesadas en esta boleta → volver a 'confirmed'
            rec.disability_ids.filtered(
                lambda d: d.state == 'paid'
            ).write({'state': 'confirmed'})
            # Revertir horas extras pagadas en esta boleta → volver a 'approved'
            rec.overtime_ids.filtered(
                lambda o: o.state == 'paid'
            ).write({'state': 'approved'})
            rec.state = 'cancelled'

    def action_reset_to_draft(self):
        for rec in self:
            if rec.state not in ('cancelled', 'confirmed'):
                raise UserError('Solo se pueden reactivar boletas canceladas o confirmadas.')
            rec.state = 'draft'

    def action_send_payslip(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Enviar Boleta',
            'res_model': 'planilla.send.payslip.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_payslip_ids': [(6, 0, self.ids)]},
        }

    def action_view_accounting_entry(self):
        self.ensure_one()
        if not self.move_id:
            raise UserError('Esta boleta no tiene asiento contable generado.')
        return {
            'type': 'ir.actions.act_window',
            'name': 'Asiento Contable',
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': self.move_id.id,
        }

    def action_print_payslip(self):
        return self.env.ref('planilla_cr.action_report_payslip_cr').report_action(self)

    def _create_accounting_entry(self):
        self.ensure_one()
        config = self.env['planilla.accounting.config'].get_config(self.company_id.id)
        if not config or not config.journal_id:
            return

        lines = []

        def add_line(account, debit=0.0, credit=0.0, name=''):
            if not account:
                return
            debit, credit = round(debit, 2), round(credit, 2)
            if debit == 0.0 and credit == 0.0:
                return
            lines.append((0, 0, {'account_id': account.id, 'name': name, 'debit': debit, 'credit': credit}))

        emp = self.employee_id.name
        add_line(config.account_salary_expense, debit=self.gross_salary, name=f'Salarios — {emp}')
        add_line(config.account_social_charges_expense, debit=self.ccss_employer + self.ins_employer, name=f'Cargas Sociales — {emp}')
        add_line(config.account_vacation_expense, debit=self.vacation_provision, name=f'Vacaciones — {emp}')
        add_line(config.account_aguinaldo_expense, debit=self.aguinaldo_provision, name=f'Aguinaldo — {emp}')
        add_line(config.account_cesantia_expense, debit=self.cesantia_provision, name=f'Cesantía — {emp}')
        add_line(config.account_ccss_payable, credit=self.ccss_employee + self.ccss_employer, name=f'CCSS por Pagar — {emp}')
        add_line(config.account_ins_payable, credit=self.ins_employer, name=f'INS por Pagar — {emp}')
        add_line(config.account_income_tax_payable, credit=self.income_tax, name=f'Retención Renta — {emp}')
        add_line(config.account_aguinaldo_provision, credit=self.aguinaldo_provision, name=f'Provisión Aguinaldo — {emp}')
        add_line(config.account_cesantia_provision, credit=self.cesantia_provision, name=f'Provisión Cesantía — {emp}')
        add_line(config.account_vacation_provision, credit=self.vacation_provision, name=f'Provisión Vacaciones — {emp}')
        add_line(config.account_salary_payable, credit=self.net_salary, name=f'Salarios por Pagar — {emp}')

        if not lines:
            return

        move = self.env['account.move'].create({
            'journal_id': config.journal_id.id,
            'date': self.date_to,
            'ref': f'Planilla: {self.name}',
            'move_type': 'entry',
            'line_ids': lines,
        })
        move.action_post()
        self.move_id = move.id


class PayslipDeductionLine(models.Model):
    _name = 'planilla.payslip.deduction.line'
    _description = 'Línea de Deducción en Boleta'

    payslip_id = fields.Many2one('planilla.payslip.cr', string='Boleta', required=True, ondelete='cascade')
    deduction_code_id = fields.Many2one('planilla.deduction.code', string='Código de Deducción', required=True)
    description = fields.Char(string='Descripción')
    amount = fields.Monetary(string='Monto', currency_field='currency_id')
    currency_id = fields.Many2one(related='payslip_id.currency_id', store=True)
    deduction_type = fields.Selection(related='deduction_code_id.deduction_type', string='Tipo')
    loan_installment_id = fields.Many2one(
        'planilla.loan.installment', string='Cuota de Préstamo', readonly=True
    )
