from odoo import models, fields, api
from odoo.exceptions import UserError
from dateutil.relativedelta import relativedelta
import datetime


class TerminationSimulatorLoan(models.TransientModel):
    """Línea de préstamo/adelanto pendiente en el simulador."""
    _name = 'planilla.termination.simulator.loan'
    _description = 'Línea de Préstamo en Simulador de Liquidación'

    simulator_id  = fields.Many2one('planilla.termination.simulator', ondelete='cascade')
    loan_id       = fields.Many2one('planilla.employee.loan', string='Referencia', readonly=True)
    loan_type     = fields.Char(string='Tipo', readonly=True)
    amount_total  = fields.Monetary(string='Monto Original', currency_field='currency_id', readonly=True)
    amount_paid   = fields.Monetary(string='Pagado', currency_field='currency_id', readonly=True)
    amount_pending = fields.Monetary(string='Saldo Pendiente', currency_field='currency_id', readonly=True)
    currency_id   = fields.Many2one('res.currency', readonly=True)


class TerminationSimulator(models.TransientModel):
    """Simulador de liquidación — NO guarda datos, solo estima el costo."""
    _name = 'planilla.termination.simulator'
    _description = 'Simulador de Liquidación Laboral'

    employee_id        = fields.Many2one('hr.employee', string='Empleado', required=True)
    simulated_date     = fields.Date(string='Fecha Simulada de Salida', required=True,
                                      default=fields.Date.today)
    termination_reason = fields.Selection([
        ('voluntary',    'Renuncia Voluntaria'),
        ('dismissal',    'Despido sin Causa Justa'),
        ('just_cause',   'Despido con Causa Justa'),
        ('mutual',       'Acuerdo Mutuo'),
        ('contract_end', 'Vencimiento de Contrato'),
    ], string='Motivo', required=True, default='voluntary')

    # ── Datos informativos del empleado (related — más estables en TransientModel) ──
    entry_date     = fields.Date(related='employee_id.entry_date',       string='Fecha de Ingreso', readonly=True)
    department     = fields.Char(related='employee_id.department_id.name', string='Departamento',   readonly=True)
    job_position   = fields.Char(related='employee_id.job_id.name',       string='Puesto',          readonly=True)
    branch_name    = fields.Char(related='employee_id.branch_id.name',    string='Sucursal',        readonly=True)

    # ── Préstamos y adelantos pendientes ────────────────────────────────────
    has_loans          = fields.Boolean(readonly=True)
    loan_line_ids      = fields.One2many(
        'planilla.termination.simulator.loan', 'simulator_id',
        string='Préstamos / Adelantos Pendientes', readonly=True)
    total_loans_pending = fields.Monetary(string='Total Deudas a Rebajar (₡)',
                                           currency_field='currency_id', readonly=True)

    # ── Resultados del cálculo ───────────────────────────────────────────────
    currency_id        = fields.Many2one('res.currency', readonly=True)
    years_service      = fields.Float(string='Años de Servicio', readonly=True)
    last_salary        = fields.Monetary(string='Último Salario Bruto', currency_field='currency_id', readonly=True)
    preaviso_days      = fields.Integer(string='Días de Preaviso', readonly=True)
    preaviso_amount    = fields.Monetary(string='Preaviso (₡)', currency_field='currency_id', readonly=True)
    preaviso_applies   = fields.Boolean(string='Preaviso Aplica', readonly=True)
    cesantia_amount    = fields.Monetary(string='Cesantía (₡)', currency_field='currency_id', readonly=True)
    cesantia_applies   = fields.Boolean(string='Cesantía Aplica', readonly=True)
    vacation_days      = fields.Float(string='Días Vacaciones Pendientes', readonly=True)
    vacation_amount    = fields.Monetary(string='Vacaciones (₡)', currency_field='currency_id', readonly=True)
    aguinaldo_months   = fields.Integer(string='Meses Aguinaldo', readonly=True)
    aguinaldo_amount   = fields.Monetary(string='Aguinaldo Proporcional (₡)', currency_field='currency_id', readonly=True)
    total_gross        = fields.Monetary(string='Subtotal Bruto (₡)', currency_field='currency_id', readonly=True)
    ccss_on_total      = fields.Monetary(string='CCSS Obrero (₡)', currency_field='currency_id', readonly=True)
    total_net          = fields.Monetary(string='Neto antes de deducciones (₡)', currency_field='currency_id', readonly=True)
    total_final        = fields.Monetary(string='TOTAL A PAGAR AL EMPLEADO (₡)', currency_field='currency_id', readonly=True)
    computed           = fields.Boolean(default=False)
    notes              = fields.Text(string='Notas del Cálculo', readonly=True)

    @api.onchange('employee_id')
    def _onchange_employee(self):
        if self.employee_id:
            emp = self.employee_id
            self.currency_id = emp.currency_id
            self.last_salary = emp.base_salary or 0.0

    def action_simulate(self):
        self.ensure_one()
        emp = self.employee_id
        if not emp.entry_date:
            raise UserError('El empleado no tiene fecha de ingreso registrada.')

        exit_date  = self.simulated_date
        entry_date = emp.entry_date
        diff   = relativedelta(exit_date, entry_date)
        years  = diff.years + diff.months / 12.0 + diff.days / 365.0
        salary = emp.base_salary or 0.0
        daily  = salary / 30.0
        notes_lines = []

        # ── Preaviso (Art. 28 CT) ────────────────────────────────────────────
        preaviso_applies = self.termination_reason in ('dismissal', 'mutual')
        if years < 0.25:
            preaviso_days = 7
        elif years < 0.5:
            preaviso_days = 14
        elif years < 1.0:
            preaviso_days = 21
        else:
            preaviso_days = 30
        preaviso_amount = (daily * preaviso_days) if preaviso_applies else 0.0
        notes_lines.append(
            f"Preaviso Art.28 CT: {preaviso_days} días "
            f"{'— APLICA' if preaviso_applies else '— no aplica (renuncia voluntaria)'}"
        )

        # ── Cesantía (Art. 29 CT — máx 8 años) ──────────────────────────────
        cesantia_applies = self.termination_reason == 'dismissal'
        if cesantia_applies:
            years_capped = min(years, 8.0)
            if years_capped < 1:
                cesantia_amount = 0.0
                notes_lines.append('Cesantía Art.29 CT: menos de 1 año — no aplica')
            elif years_capped <= 3:
                cesantia_amount = round(salary * years_capped * 0.195, 2)
                notes_lines.append(f'Cesantía Art.29 CT: {years_capped:.2f} años × 19.5 días/año')
            elif years_capped <= 6:
                cesantia_amount = round(salary * years_capped * 0.20, 2)
                notes_lines.append(f'Cesantía Art.29 CT: {years_capped:.2f} años × 20 días/año')
            else:
                cesantia_amount = round(salary * years_capped * 0.21, 2)
                notes_lines.append(f'Cesantía Art.29 CT: {years_capped:.2f} años × 21 días/año')
        else:
            cesantia_amount = 0.0
            notes_lines.append('Cesantía Art.29 CT: no aplica para este tipo de salida')

        # ── Vacaciones pendientes ────────────────────────────────────────────
        vac_days   = emp.vacation_days_available or 0.0
        vac_amount = round(daily * vac_days, 2)
        notes_lines.append(f'Vacaciones Art.153 CT: {vac_days:.1f} días disponibles × ₡{daily:,.2f}/día')

        # ── Aguinaldo proporcional (Art. 42 CT — dic 1 a nov 30) ────────────
        if exit_date.month >= 12:
            ref_dec = datetime.date(exit_date.year, 12, 1)
        else:
            ref_dec = datetime.date(exit_date.year - 1, 12, 1)
        months_worked = min(round((exit_date - ref_dec).days / 30), 12)
        aguinaldo = round(salary * months_worked / 12.0, 2)
        notes_lines.append(f'Aguinaldo Art.42 CT: {months_worked} meses desde 1-dic')

        # ── Totales ──────────────────────────────────────────────────────────
        total_gross = preaviso_amount + cesantia_amount + vac_amount + aguinaldo
        ccss        = round(total_gross * 0.1083, 2)
        total_net   = round(total_gross - ccss, 2)

        # ── Préstamos y adelantos pendientes ─────────────────────────────────
        loans = self.env['planilla.employee.loan'].search([
            ('employee_id', '=', emp.id),
            ('state', 'in', ('active', 'approved')),
            ('amount_pending', '>', 0),
        ])
        loan_lines = []
        total_pending = 0.0
        type_labels = {
            'loan':    'Préstamo',
            'advance': 'Adelanto de Salario',
        }
        for loan in loans:
            total_pending += loan.amount_pending
            loan_lines.append((0, 0, {
                'loan_id':       loan.id,
                'loan_type':     type_labels.get(loan.loan_type, loan.loan_type),
                'amount_total':  loan.amount_total,
                'amount_paid':   loan.amount_paid,
                'amount_pending': loan.amount_pending,
                'currency_id':   loan.currency_id.id,
            }))

        total_pending = round(total_pending, 2)
        total_final   = round(total_net - total_pending, 2)

        if loan_lines:
            notes_lines.append(
                f'\nDeducciones por préstamos/adelantos: ₡{total_pending:,.2f}'
                f' ({len(loan_lines)} obligación(es) pendiente(s))'
            )
        notes_lines.append(f'\nTOTAL FINAL a pagar: ₡{total_final:,.2f}')

        # Limpiar líneas anteriores antes de escribir
        self.loan_line_ids.unlink()

        self.write({
            'years_service':       round(years, 2),
            'last_salary':         salary,
            'preaviso_days':       preaviso_days,
            'preaviso_amount':     preaviso_amount,
            'preaviso_applies':    preaviso_applies,
            'cesantia_amount':     cesantia_amount,
            'cesantia_applies':    cesantia_applies,
            'vacation_days':       vac_days,
            'vacation_amount':     vac_amount,
            'aguinaldo_months':    months_worked,
            'aguinaldo_amount':    aguinaldo,
            'total_gross':         total_gross,
            'ccss_on_total':       ccss,
            'total_net':           total_net,
            'has_loans':           bool(loan_lines),
            'loan_line_ids':       loan_lines,
            'total_loans_pending': total_pending,
            'total_final':         total_final,
            'computed':            True,
            'notes':               '\n'.join(notes_lines),
        })

        return {
            'type':      'ir.actions.act_window',
            'res_model': 'planilla.termination.simulator',
            'res_id':    self.id,
            'view_mode': 'form',
            'target':    'new',
        }
