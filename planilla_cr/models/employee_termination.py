from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError
from datetime import date
from dateutil.relativedelta import relativedelta
from .closed_period import PlanillaClosedPeriod


class EmployeeTermination(models.Model):
    _name = 'planilla.termination'
    _description = 'Liquidación / Finiquito de Empleado'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'termination_date desc'

    name = fields.Char(
        string='Referencia', compute='_compute_name', store=True
    )
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company
    )
    employee_id = fields.Many2one(
        'hr.employee', string='Empleado', required=True,
        domain=[('active', 'in', [True, False])]
    )
    entry_date = fields.Date(
        string='Fecha de Ingreso', required=True
    )
    termination_date = fields.Date(
        string='Fecha de Salida', required=True, default=fields.Date.today
    )
    termination_reason = fields.Selection([
        ('renuncia',       'Renuncia voluntaria'),
        ('despido_justif', 'Despido con causa justificada'),
        ('despido_injust', 'Despido sin causa justificada'),
        ('mutuo',          'Mutuo acuerdo'),
        ('contrato_vence', 'Vencimiento de contrato'),
        ('fallecimiento',  'Fallecimiento'),
    ], string='Motivo de Salida', required=True)

    # ── Salario base ────────────────────────────────────────────
    last_salary = fields.Monetary(
        string='Salario Bruto Mensual', currency_field='currency_id',
        required=True
    )
    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.ref('base.CRC')
    )

    # ── Años y días de servicio (computed) ──────────────────────
    years_service = fields.Float(
        string='Años de Servicio', compute='_compute_service_time', store=True
    )
    months_service = fields.Integer(
        string='Meses de Servicio', compute='_compute_service_time', store=True
    )
    days_service = fields.Integer(
        string='Días de Servicio', compute='_compute_service_time', store=True
    )

    # ── Componentes liquidación ──────────────────────────────────
    preaviso_days = fields.Integer(
        string='Días de Preaviso', compute='_compute_preaviso', store=True
    )
    preaviso_amount = fields.Monetary(
        string='Monto Preaviso (₡)', currency_field='currency_id',
        compute='_compute_amounts', store=True
    )
    preaviso_applies = fields.Boolean(
        string='Aplica Preaviso', default=True,
        help='Desmarcar si el empleado ya trabajó el período de preaviso.'
    )

    cesantia_amount = fields.Monetary(
        string='Cesantía (₡)', currency_field='currency_id',
        compute='_compute_amounts', store=True
    )
    cesantia_applies = fields.Boolean(
        string='Aplica Cesantía', compute='_compute_cesantia_applies', store=True
    )

    # Vacaciones
    vacation_days_accrued = fields.Float(
        string='Días Vacaciones Acumulados', compute='_compute_amounts', store=True
    )
    vacation_amount = fields.Monetary(
        string='Vacaciones Proporcionales (₡)', currency_field='currency_id',
        compute='_compute_amounts', store=True
    )

    # Aguinaldo proporcional
    aguinaldo_amount = fields.Monetary(
        string='Aguinaldo Proporcional (₡)', currency_field='currency_id',
        compute='_compute_amounts', store=True
    )
    aguinaldo_months = fields.Integer(
        string='Meses para Aguinaldo', compute='_compute_amounts', store=True
    )

    # Otros
    other_payments = fields.Monetary(
        string='Otros Pagos (₡)', currency_field='currency_id',
        help='Bonos, comisiones pendientes u otros conceptos.'
    )
    other_payments_note = fields.Char(string='Descripción otros pagos')

    deductions = fields.Monetary(
        string='Deducciones (₡)', currency_field='currency_id',
        help='Adelantos, préstamos u otras deducciones pendientes.'
    )
    deductions_note = fields.Char(string='Descripción deducciones')

    # ── Totales ──────────────────────────────────────────────────
    # FIX A-03 v53: CCSS obrero sobre base liquidable (preaviso + vacaciones proporcionales)
    ccss_employee_on_termination = fields.Monetary(
        string='CCSS Obrero Retenido (₡)', currency_field='currency_id',
        compute='_compute_total', store=True,
        help='10.83% sobre preaviso + vacaciones proporcionales (Art. 26 Reglamento CCSS). '
             'Se retiene del empleado y se deposita a la CCSS.'
    )
    # FIX NEW-02 v54: Impuesto de Renta sobre la liquidación.
    # Art. 35 Ley ISR: preaviso y vacaciones proporcionales son ingreso gravable.
    # Se calcula sobre el total_gross aplicando los tramos vigentes.
    income_tax_on_termination = fields.Monetary(
        string='Impuesto Renta Retenido (₡)', currency_field='currency_id',
        compute='_compute_total', store=True,
        help='Retención de impuesto sobre la renta calculada sobre el total bruto de la liquidación '
             '(Art. 35 Ley ISR). Se retiene del empleado y se deposita a Hacienda.'
    )
    total_gross = fields.Monetary(
        string='Total Bruto Liquidación (₡)', currency_field='currency_id',
        compute='_compute_total', store=True
    )
    total_net = fields.Monetary(
        string='Total Neto a Pagar (₡)', currency_field='currency_id',
        compute='_compute_total', store=True
    )

    move_id = fields.Many2one('account.move', string='Asiento Contable', readonly=True)
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('confirmed','Confirmado'),
        ('paid',     'Pagado'),
        ('cancelled','Cancelado'),
    ], default='draft')

    note = fields.Text(string='Observaciones')

    # ── Computes ─────────────────────────────────────────────────

    @api.depends('employee_id', 'termination_date')
    def _compute_name(self):
        for rec in self:
            if rec.employee_id and rec.termination_date:
                rec.name = f'Liquidación - {rec.employee_id.name} - {rec.termination_date}'
            else:
                rec.name = 'Nueva Liquidación'

    @api.depends('entry_date', 'termination_date')
    def _compute_service_time(self):
        for rec in self:
            if rec.entry_date and rec.termination_date:
                delta = relativedelta(rec.termination_date, rec.entry_date)
                rec.years_service = delta.years + delta.months / 12
                rec.months_service = delta.years * 12 + delta.months
                rec.days_service = (rec.termination_date - rec.entry_date).days
            else:
                rec.years_service = 0
                rec.months_service = 0
                rec.days_service = 0

    @api.depends('months_service', 'termination_reason')
    def _compute_preaviso(self):
        """
        Código de Trabajo CR Art. 28:
        < 3 meses: 1 semana
        3-6 meses: 2 semanas
        6-12 meses: 1 mes
        > 12 meses: 1 mes
        FIX C-10 v53: fallecimiento no genera preaviso (Art. 85 CT — extinción por muerte).
        """
        for rec in self:
            # Fallecimiento: sin preaviso (Art. 85 CT)
            if rec.termination_reason == 'fallecimiento':
                rec.preaviso_days = 0
                continue
            m = rec.months_service
            if m < 3:
                rec.preaviso_days = 7
            elif m < 6:
                rec.preaviso_days = 14
            else:
                rec.preaviso_days = 30

    @api.onchange('termination_reason')
    def _onchange_termination_reason_preaviso(self):
        """FIX C-10 v53: Al seleccionar fallecimiento, desmarcar preaviso automáticamente."""
        if self.termination_reason == 'fallecimiento':
            self.preaviso_applies = False

    @api.depends('termination_reason')
    def _compute_cesantia_applies(self):
        """
        Cesantía aplica en despido injustificado o mutuo acuerdo.
        No aplica en renuncia voluntaria ni despido con justa causa.
        """
        for rec in self:
            rec.cesantia_applies = rec.termination_reason in (
                'despido_injust', 'mutuo', 'contrato_vence', 'fallecimiento'
            )

    @api.depends('last_salary', 'years_service', 'months_service', 'days_service',
                 'termination_date', 'entry_date', 'termination_reason', 'preaviso_days',
                 'employee_id')
    def _compute_amounts(self):
        for rec in self:
            if not rec.last_salary or not rec.entry_date or not rec.termination_date:
                rec.preaviso_amount = 0
                rec.cesantia_amount = 0
                rec.vacation_days_accrued = 0
                rec.vacation_amount = 0
                rec.aguinaldo_amount = 0
                rec.aguinaldo_months = 0
                continue

            daily_salary = rec.last_salary / 30
            monthly_salary = rec.last_salary

            # ── Preaviso ──────────────────────────────────────────
            rec.preaviso_amount = daily_salary * rec.preaviso_days if rec.preaviso_applies else 0

            # ── Cesantía (Art. 29 Código de Trabajo) ─────────────
            # Tabla de días por año trabajado:
            # Año 1: 19.5 días, Año 2: 20 días, Año 3: 20.5 días...
            # Máximo 8 años = 22 días/año
            if rec.cesantia_applies:
                cesantia_days_table = {
                    1: 19.5, 2: 20.0, 3: 20.5, 4: 21.0,
                    5: 21.24, 6: 21.5, 7: 22.0, 8: 22.0,
                }
                years = min(int(rec.years_service), 8)
                fraction = rec.years_service - int(rec.years_service)
                cesantia_days = 0
                for y in range(1, years + 1):
                    cesantia_days += cesantia_days_table.get(y, 22.0)
                # Fracción del año en curso
                if years < 8:
                    days_this_year = cesantia_days_table.get(years + 1, 22.0)
                    cesantia_days += days_this_year * fraction
                rec.cesantia_amount = round(daily_salary * cesantia_days, 2)
            else:
                rec.cesantia_amount = 0

            # ── Vacaciones proporcionales (Art. 153 CT) ───────────────
            # FIX B-01 v51: Calcular días brutos acumulados por tiempo de servicio
            # y descontar los días ya tomados (disfrutadas) y pagados (dinero/proporcionales)
            # para obtener el saldo real pendiente de pagar en la liquidación.
            # Sin este fix el empleado podía cobrar vacaciones que ya había disfrutado.
            weeks_worked = rec.days_service / 7
            vacation_days_gross = weeks_worked * (12 / 50)

            # Días ya consumidos: vacaciones disfrutadas, pagadas en dinero o proporcionales
            # que se hayan aprobado o pagado durante la relación laboral
            vacation_days_taken = 0.0
            if rec.employee_id:
                taken_payments = self.env['planilla.vacation.payment'].search([
                    ('employee_id', '=', rec.employee_id.id),
                    ('state', 'in', ('approved', 'paid')),
                ])
                vacation_days_taken = sum(taken_payments.mapped('days'))

            vacation_days_net = max(vacation_days_gross - vacation_days_taken, 0.0)
            rec.vacation_days_accrued = round(vacation_days_net, 2)
            rec.vacation_amount = round(daily_salary * vacation_days_net, 2)

            # ── Aguinaldo proporcional (Art. 228 - periodo jun-nov) ─
            # El aguinaldo es el salario del mes de diciembre
            # = promedio de salarios jun-nov / 12 * meses trabajados en el periodo
            exit_month = rec.termination_date.month
            exit_year = rec.termination_date.year
            # Determinar cuántos meses del periodo jun-nov están incluidos
            if exit_month >= 12:
                # Ya cobró aguinaldo de diciembre
                rec.aguinaldo_months = 0
                rec.aguinaldo_amount = 0
            elif exit_month >= 6:
                # Meses desde junio hasta mes de salida
                months_in_period = exit_month - 5  # jun=1, jul=2... nov=6
                rec.aguinaldo_months = months_in_period
                rec.aguinaldo_amount = round(monthly_salary / 12 * months_in_period, 2)
            else:
                # Enero-Mayo: periodo diciembre-mayo del año anterior
                months_in_period = exit_month  # ene=1... may=5
                rec.aguinaldo_months = months_in_period
                rec.aguinaldo_amount = round(monthly_salary / 12 * months_in_period, 2)

    def _calc_income_tax(self, gross):
        """FIX NEW-02 v54: calcula renta sobre el total bruto de la liquidacion.
        Reutiliza la misma logica progresiva por tramos que payslip_cr._calc_income_tax.
        La liquidacion se trata como pago unico mensual (freq = monthly).
        """
        brackets = self.env['planilla.income.tax.bracket'].search(
            [('active', '=', True)], order='sequence asc'
        )
        g = gross
        if not brackets:
            # Fallback tramos 2026 (DGT-R-016-2026)
            if g <= 941000:
                return 0.0
            elif g <= 1381000:
                return (g - 941000) * 0.10
            elif g <= 2423000:
                return (440000 * 0.10) + ((g - 1381000) * 0.15)
            elif g <= 4845000:
                return (440000 * 0.10) + (1042000 * 0.15) + ((g - 2423000) * 0.20)
            else:
                return (440000 * 0.10) + (1042000 * 0.15) + (2422000 * 0.20) + ((g - 4845000) * 0.25)
        tax = 0.0
        for bracket in brackets:
            if g <= bracket.limit_from:
                break
            limit_to = bracket.limit_to if bracket.limit_to else float('inf')
            taxable = min(g, limit_to) - bracket.limit_from
            if taxable > 0:
                tax += taxable * (bracket.rate / 100)
        return tax

    @api.depends('preaviso_amount', 'preaviso_applies', 'cesantia_amount',
                 'vacation_amount', 'aguinaldo_amount', 'other_payments', 'deductions')
    def _compute_total(self):
        # FIX A-03 v53: calcular CCSS obrero sobre base liquidable (preaviso + vacaciones prop.)
        # FIX NEW-02 v54: agregar retencion de renta sobre total_gross (Art. 35 Ley ISR)
        rh = self.env['planilla.rate.helper']
        ccss_employee_rate = rh.get_ccss_employee_rate()
        for rec in self:
            gross = (
                (rec.preaviso_amount if rec.preaviso_applies else 0) +
                rec.cesantia_amount +
                rec.vacation_amount +
                rec.aguinaldo_amount +
                rec.other_payments
            )
            # Base cotizable CCSS obrero: preaviso + vacaciones proporcionales
            liquidable_base = (
                (rec.preaviso_amount if rec.preaviso_applies else 0) +
                rec.vacation_amount
            )
            ccss_emp = round(liquidable_base * ccss_employee_rate, 2)
            # FIX NEW-02 v54: renta sobre el total bruto de la liquidacion
            income_tax = round(rec._calc_income_tax(gross), 2)
            rec.total_gross = round(gross, 2)
            rec.ccss_employee_on_termination = ccss_emp
            rec.income_tax_on_termination = income_tax
            # total_net = bruto − CCSS obrero − renta − otras deducciones
            rec.total_net = round(gross - ccss_emp - income_tax - rec.deductions, 2)

    # ── Onchange para autocompletar desde empleado ────────────────

    @api.onchange('employee_id')
    def _onchange_employee(self):
        if self.employee_id:
            emp = self.employee_id
            self.last_salary = emp.base_salary or 0
            self.entry_date = emp.entry_date or False

    # ── Actions ──────────────────────────────────────────────────

    def action_confirm(self):
        self.ensure_one()
        if not self.employee_id or not self.last_salary:
            raise UserError('Complete los datos del empleado y salario antes de confirmar.')
        # Verificar boletas pendientes del empleado
        pending_slips = self.env['planilla.payslip.cr'].search([
            ('employee_id', '=', self.employee_id.id),
            ('state', 'in', ('draft', 'confirmed')),
        ])
        if pending_slips:
            names = ', '.join(pending_slips.mapped('name'))
            raise UserError(
                f'El empleado {self.employee_id.name} tiene boletas pendientes sin pagar:\n'
                f'{names}\n\n'
                f'Pague o cancele esas boletas antes de confirmar la liquidación.'
            )
        # Verificar período cerrado
        termination_date = self.termination_date or fields.Date.today()
        closed = PlanillaClosedPeriod.is_period_closed(
            self.env, self.company_id.id,
            termination_date, termination_date,
            self.employee_id.branch_id.id if self.employee_id.branch_id else False
        )
        if closed:
            raise UserError(
                f'No se puede confirmar la liquidación: el período que incluye la fecha '
                f'{termination_date.strftime("%d/%m/%Y")} está cerrado '
                f'("{closed.name}", cerrado el {closed.closed_date.strftime("%d/%m/%Y")} '
                f'por {closed.closed_by.name}).'
            )

        # BUG #3 FIX v50: Integración automática de préstamos activos
        # Busca préstamos/adelantos activos o aprobados con saldo pendiente
        active_loans = self.env['planilla.employee.loan'].search([
            ('employee_id', '=', self.employee_id.id),
            ('state', 'in', ('approved', 'active')),
            ('amount_pending', '>', 0),
        ])
        if active_loans:
            total_loan_balance = sum(active_loans.mapped('amount_pending'))
            loan_details = ', '.join(
                f'{l.name} (₡{l.amount_pending:,.2f})' for l in active_loans
            )
            # Pre-llenar campo deductions si está vacío
            if not self.deductions:
                self.deductions = round(total_loan_balance, 2)
                self.deductions_note = f'Saldo préstamos activos: {loan_details}'
            else:
                # Ya tiene deducciones manuales: mostrar advertencia
                self.message_post(
                    body=f'<b>⚠️ Aviso:</b> El empleado tiene préstamos activos con saldo '
                         f'pendiente de ₡{total_loan_balance:,.2f} ({loan_details}). '
                         f'Verifique que el campo <b>Deducciones</b> ya lo contempla.',
                    message_type='notification',
                )

        self.state = 'confirmed'

    def action_pay(self):
        self.ensure_one()
        if self.state != 'confirmed':
            raise UserError('Solo se pueden pagar liquidaciones confirmadas.')

        # BUG #7 FIX v50: Savepoint para atomicidad — si el asiento falla,
        # el empleado NO queda inactivo sin reversión contable
        with self.env.cr.savepoint():
            move = self._create_termination_accounting_entry()
            # Inactivar empleado SOLO si el asiento se creó correctamente
            if move:
                self.employee_id.write({'active': False})
            self.write({
                'state': 'paid',
                'move_id': move.id if move else False,
            })

    def _create_termination_accounting_entry(self):
        # FIX BUG-N01 v52: pasar company_id del registro de liquidación, no la compañía
        # activa en sesión. En multi-empresa esto asegura usar la config correcta.
        config = self.env['planilla.accounting.config'].get_config(self.company_id.id)
        if not config:
            return False

        emp = self.employee_id.name
        journal = config.journal_id
        if not journal:
            return False

        lines = []

        def add_line(account, debit=0.0, credit=0.0, name=''):
            if not account or (debit == 0 and credit == 0):
                return
            lines.append((0, 0, {
                'account_id': account.id,
                'name': name,
                'debit': round(debit, 2),
                'credit': round(credit, 2),
            }))

        # ── DÉBITOS (gastos) ─────────────────────────────────────────
        if self.preaviso_applies and self.preaviso_amount:
            add_line(
                config.account_preaviso_expense or config.account_salary_expense,
                debit=self.preaviso_amount,
                name=f'Preaviso — {emp}'
            )
        if self.cesantia_applies and self.cesantia_amount:
            add_line(
                config.account_cesantia_expense,
                debit=self.cesantia_amount,
                name=f'Cesantía — {emp}'
            )
        if self.vacation_amount:
            add_line(
                config.account_vacation_expense,
                debit=self.vacation_amount,
                name=f'Vacaciones proporcionales — {emp}'
            )
        if self.aguinaldo_amount:
            add_line(
                config.account_aguinaldo_expense,
                debit=self.aguinaldo_amount,
                name=f'Aguinaldo proporcional — {emp}'
            )
        if self.other_payments:
            add_line(
                config.account_salary_expense,
                debit=self.other_payments,
                name=f'Otros pagos — {emp}: {self.other_payments_note or ""}'
            )

        # BUG #4 FIX v50: CCSS patronal sobre preaviso + vacaciones proporcionales
        # Art. 26 Reglamento CCSS: cargas sociales aplican sobre estos componentes
        # de la liquidación (preaviso y vacaciones proporcionales son salario ordinario
        # para efectos de cotización CCSS según Reglamento del Seguro Social).
        rh = self.env['planilla.rate.helper']
        ccss_employer_rate = rh.get_ccss_employer_rate()  # 26.83%
        # FIX A-03 v53: CCSS obrero e Impuesto de Renta sobre la liquidación.
        # Art. 26 RCCSS y Art. 29 CT: preaviso y vacaciones proporcionales
        # son base de cotización. La cesantía y el aguinaldo proporcional
        # NO son base imponible (CCSS Resolución Nro. 5 del 24/5/1994).
        # La renta aplica sobre el total bruto según criterio del Ministerio de Hacienda.
        ccss_employee_rate = rh.get_ccss_employee_rate()  # 10.83%
        liquidable_base = (
            (self.preaviso_amount if self.preaviso_applies else 0) +
            self.vacation_amount
        )
        ccss_on_termination = round(liquidable_base * ccss_employer_rate, 2)
        ccss_emp_on_termination = round(liquidable_base * ccss_employee_rate, 2)
        if ccss_on_termination > 0:
            add_line(
                config.account_social_charges_expense,
                debit=ccss_on_termination,
                name=f'CCSS Patronal sobre liquidación — {emp} ({ccss_employer_rate*100:.2f}%)'
            )
            add_line(
                config.account_ccss_payable,
                credit=ccss_on_termination,
                name=f'CCSS Patronal liquidación por pagar — {emp}'
            )
        # CCSS obrero: se retiene del empleado (reduce el neto a pagar)
        if ccss_emp_on_termination > 0:
            add_line(
                config.account_ccss_payable,
                credit=ccss_emp_on_termination,
                name=f'CCSS Obrero retenido en liquidacion — {emp} ({ccss_employee_rate*100:.2f}%)'
            )

        # FIX NEW-02 v54: Impuesto de Renta retenido sobre la liquidacion (Art. 35 Ley ISR)
        income_tax_liq = round(self.income_tax_on_termination or 0.0, 2)
        if income_tax_liq > 0:
            add_line(
                config.account_income_tax_payable,
                credit=income_tax_liq,
                name=f'Retencion Renta en liquidacion — {emp}'
            )

        # ── CREDITO (pasivo por pagar) ───────────────────────────────
        # FIX A-03 v53: neto = total_gross - CCSS obrero.
        # FIX NEW-02 v54: neto = total_gross - CCSS obrero - renta.
        payable_account = config.account_termination_payable or config.account_salary_payable
        net_to_pay = round(self.total_gross - ccss_emp_on_termination - income_tax_liq, 2)
        add_line(
            payable_account,
            credit=net_to_pay if net_to_pay > 0 else self.total_gross,
            name=f'Liquidación por pagar (neto) — {emp}'
        )

        # ── Deducción (si aplica) ────────────────────────────────────
        # DÉBITO en la misma cuenta de Liquidaciones por Pagar → reduce el pasivo.
        # El neto a depositar al empleado = total_gross - deductions (gestionado en banco).
        if self.deductions:
            add_line(
                payable_account,
                debit=self.deductions,
                name=f'Deducción liquidación — {emp}: {self.deductions_note or ""}'
            )

        if not lines:
            return False

        # H1 FIX — Verificar cuadre antes de postear
        total_debit  = round(sum(l[2]['debit']  for l in lines), 2)
        total_credit = round(sum(l[2]['credit'] for l in lines), 2)
        if abs(total_debit - total_credit) > 0.02:  # FIX BUG-N02 v52: tolerancia reducida a ₡0.02
            raise UserError(
                f'El asiento de liquidación no cuadra para {emp}:\n'
                f'  Débitos:  ₡{total_debit:,.2f}\n'
                f'  Créditos: ₡{total_credit:,.2f}\n\n'
                f'Verifique que todas las cuentas contables estén configuradas '
                f'(Planilla → Configuración → Contabilidad → ⚡ Autocompletar).'
            )

        move = self.env['account.move'].create({
            'journal_id': journal.id,
            'date': self.termination_date or fields.Date.today(),
            'ref': f'Liquidación — {emp} — {self.termination_date}',
            'move_type': 'entry',
            'line_ids': lines,
        })
        move.action_post()
        return move



    def action_cancel(self):
        self.ensure_one()
        if self.state == 'paid':
            raise UserError('No se puede cancelar una liquidación ya pagada.')
        self.state = 'cancelled'
        # Reactivar empleado si fue inactivado por esta liquidación
        if self.employee_id and not self.employee_id.active:
            self.employee_id.write({'active': True})

    def action_reset_to_draft(self):
        self.ensure_one()
        if self.state not in ('cancelled', 'confirmed'):
            raise UserError('Solo se puede resetear desde Cancelado o Confirmado.')
        self.state = 'draft'

    def action_view_accounting_entry(self):
        self.ensure_one()
        if not self.move_id:
            raise UserError('No hay asiento contable asociado.')
        return {
            'type': 'ir.actions.act_window',
            'name': 'Asiento Contable',
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': self.move_id.id,
        }

    def action_print_termination(self):
        return self.env.ref('planilla_cr.action_report_termination').report_action(self)
