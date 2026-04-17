from odoo import models, fields, api
from ..models import planilla_const as K
from odoo.exceptions import UserError
from dateutil.relativedelta import relativedelta
import datetime


class TerminationSimulatorLoan(models.TransientModel):
    """Linea de prestamo/adelanto pendiente en el simulador."""
    _name = 'planilla.termination.simulator.loan'
    _description = 'Linea de Prestamo en Simulador de Liquidacion'

    simulator_id  = fields.Many2one('planilla.termination.simulator', ondelete='cascade')
    loan_id       = fields.Many2one('planilla.employee.loan', string='Referencia', readonly=True)
    loan_type     = fields.Char(string='Tipo', readonly=True)
    amount_total  = fields.Monetary(string='Monto Original', currency_field='currency_id', readonly=True)
    amount_paid   = fields.Monetary(string='Pagado', currency_field='currency_id', readonly=True)
    amount_pending = fields.Monetary(string='Saldo Pendiente', currency_field='currency_id', readonly=True)
    currency_id   = fields.Many2one('res.currency', readonly=True)


class TerminationSimulator(models.TransientModel):
    """Simulador de liquidacion -- NO guarda datos, solo estima el costo."""
    _name = 'planilla.termination.simulator'
    _description = 'Simulador de Liquidacion Laboral'

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

    # -- Datos informativos del empleado (related -- mas estables en TransientModel) --
    entry_date     = fields.Date(related='employee_id.entry_date',       string='Fecha de Ingreso', readonly=True)
    department     = fields.Char(related='employee_id.department_id.name', string='Departamento',   readonly=True)
    job_position   = fields.Char(related='employee_id.job_id.name',       string='Puesto',          readonly=True)
    branch_name    = fields.Char(related='employee_id.branch_id.name',    string='Sucursal',        readonly=True)

    # -- Prestamos y adelantos pendientes ------------------------------------
    has_loans          = fields.Boolean(readonly=True)
    loan_line_ids      = fields.One2many(
        'planilla.termination.simulator.loan', 'simulator_id',
        string='Prestamos / Adelantos Pendientes', readonly=True)
    total_loans_pending = fields.Monetary(string='Total Deudas a Rebajar (CRC)',
                                           currency_field='currency_id', readonly=True)

    # -- Resultados del calculo -----------------------------------------------
    currency_id        = fields.Many2one('res.currency', readonly=True)
    years_service      = fields.Float(string='Anos de Servicio', readonly=True)
    last_salary        = fields.Monetary(string='Ultimo Salario Bruto', currency_field='currency_id', readonly=True)
    preaviso_days      = fields.Integer(string='Dias de Preaviso', readonly=True)
    preaviso_amount    = fields.Monetary(string='Preaviso (CRC)', currency_field='currency_id', readonly=True)
    preaviso_applies   = fields.Boolean(string='Preaviso Aplica', readonly=True)
    cesantia_amount    = fields.Monetary(string='Cesantia (CRC)', currency_field='currency_id', readonly=True)
    cesantia_applies   = fields.Boolean(string='Cesantia Aplica', readonly=True)
    vacation_days      = fields.Float(string='Dias Vacaciones Pendientes', readonly=True)
    vacation_amount    = fields.Monetary(string='Vacaciones (CRC)', currency_field='currency_id', readonly=True)
    # Desglose vacaciones
    vac_daily_rate     = fields.Monetary(string='Salario Diario (CRC)', currency_field='currency_id', readonly=True)
    vac_initial_days   = fields.Float(string='Dias Saldo Inicial', readonly=True)
    vac_accrued_since  = fields.Float(string='Dias Acumulados desde Corte', readonly=True)
    vac_taken_system   = fields.Float(string='Dias Tomados en Sistema', readonly=True)
    # Desglose aguinaldo
    aguinaldo_months   = fields.Integer(string='Meses Aguinaldo', readonly=True)
    aguinaldo_amount   = fields.Monetary(string='Aguinaldo Proporcional (CRC)', currency_field='currency_id', readonly=True)
    aguinaldo_initial  = fields.Monetary(string='Aguinaldo Acumulado Inicial (CRC)', currency_field='currency_id', readonly=True)
    aguinaldo_system   = fields.Monetary(string='Aguinaldo del Sistema (CRC)', currency_field='currency_id', readonly=True)
    # Desglose cesantia
    cesantia_days      = fields.Float(string='Dias de Cesantia', readonly=True)
    cesantia_daily     = fields.Monetary(string='Salario Diario para Cesantia (CRC)', currency_field='currency_id', readonly=True)
    # Desglose CCSS
    ccss_rate          = fields.Float(string='Tasa CCSS Obrero (%)', readonly=True)
    total_gross        = fields.Monetary(string='Subtotal Bruto (CRC)', currency_field='currency_id', readonly=True)
    ccss_on_total      = fields.Monetary(string='CCSS Obrero (CRC)', currency_field='currency_id', readonly=True)
    total_net          = fields.Monetary(string='Neto antes de deducciones (CRC)', currency_field='currency_id', readonly=True)
    total_final        = fields.Monetary(string='TOTAL A PAGAR AL EMPLEADO (CRC)', currency_field='currency_id', readonly=True)
    computed           = fields.Boolean(default=False)
    notes              = fields.Text(string='Notas del Calculo', readonly=True)

    @api.onchange('employee_id')
    def _onchange_employee(self):
        if self.employee_id:
            emp = self.employee_id
            self.currency_id = emp.currency_id
            # FIX-B2: para empleados con salario variable (comisiones, HE recurrentes),
            # usar el promedio de los ultimos 4 meses del historial salarial.
            # Consistente con employee_termination._onchange_employee (Art. 153 CT).
            if getattr(emp, 'has_variable_income', False):
                history = self.env['planilla.salary.history'].search([
                    ('employee_id', '=', emp.id),
                    ('state', '=', 'authorized'),
                    ('payslip_id', '=', False),
                ], order='effective_date desc', limit=4)
                if history:
                    salaries = [h.gross_salary or h.salary or 0.0 for h in history]
                    self.last_salary = round(sum(salaries) / len(salaries), 2)
                else:
                    self.last_salary = emp.base_salary or 0.0
            else:
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
        # FIX-B2: usar last_salary (ya tiene el promedio si is variable income)
        # en lugar de recalcular desde emp.base_salary
        salary = self.last_salary or emp.base_salary or 0.0
        daily  = salary / 30.0
        notes_lines = []
        if getattr(emp, 'has_variable_income', False) and self.last_salary != emp.base_salary:
            notes_lines.append(
                f'WARN  Empleado con salario variable: se usa promedio historico '
                f'CRC{salary:,.2f} (salario base: CRC{emp.base_salary:,.2f}) -- Art. 153 CT'
            )

        # -- Preaviso (Art. 28 CT) --------------------------------------------
        preaviso_applies = self.termination_reason in ('dismissal', 'mutual')
        if years < 0.25:
            preaviso_days = 7
        elif years < 0.5:
            preaviso_days = 14
        else:
            # FIX-O5: Art. 28 CT establece 1 mes (30 dias) tanto para 6-12 meses
            # como para mas de 1 ano. La version anterior usaba 21 dias para el
            # tramo 0.5-1.0 ano, que no corresponde a ningun tramo legal del Art. 28 CT.
            # employee_termination.py ya tenia los 30 dias correctamente.
            preaviso_days = 30
        preaviso_amount = (daily * preaviso_days) if preaviso_applies else 0.0
        notes_lines.append(
            f"Preaviso Art.28 CT: {preaviso_days} dias "
            f"{'-- APLICA' if preaviso_applies else '-- no aplica (renuncia voluntaria)'}"
        )

        # -- Cesantia (Art. 29 CT -- max 8 anos) ------------------------------
        cesantia_applies = self.termination_reason == 'dismissal'
        if cesantia_applies:
            # FIX-O6: la version anterior usaba salary * years * factor (incorrecto).
            # Art. 29 CT establece una tabla de DIAS POR ANO (no un factor del salario mensual).
            # Formula correcta: daily_salary x suma_de_dias_tabla, igual que employee_termination.py.
            # Error anterior: para 4 anos con CRC1M de salario daba CRC800k en vez de CRC2.7M (3.4x menos).
            # FIX CALC-01: usar tabla centralizada K.CESANTIA_TABLA (Art. 29 CT oficial)
            cesantia_days_table = K.CESANTIA_TABLA
            years_int = min(int(years), 8)
            fraction = years - int(years)
            cesantia_days = 0.0
            for y in range(1, years_int + 1):
                cesantia_days += cesantia_days_table.get(y, 22.0)
            # Fraccion del ano en curso (proporcional)
            if years_int < 8:
                days_this_year = cesantia_days_table.get(years_int + 1, 22.0)
                cesantia_days += days_this_year * fraction
            cesantia_amount = round(daily * cesantia_days, 2)
            if years < 1:
                cesantia_amount = 0.0
                notes_lines.append('Cesantia Art.29 CT: menos de 1 ano -- no aplica')
            else:
                notes_lines.append(f'Cesantia Art.29 CT: {years:.2f} anos x {cesantia_days:.1f} dias (tabla Art. 29)')
        else:
            cesantia_amount = 0.0
            notes_lines.append('Cesantia Art.29 CT: no aplica para este tipo de salida')

        # -- Vacaciones pendientes --------------------------------------------
        vac_days   = emp.vacation_days_available or 0.0
        vac_amount = round(daily * vac_days, 2)
        notes_lines.append(f'Vacaciones Art.153 CT: {vac_days:.1f} dias disponibles x CRC{daily:,.2f}/dia')

        # -- Aguinaldo proporcional (Art. 228 CT) ----------------------------
        # FIX: incluir acumulado pre-implementacion si existe
        if exit_date.month >= 12:
            period_start = datetime.date(exit_date.year, 12, 1)
            total_months = 0
        elif exit_date.month >= 6:
            period_start = datetime.date(exit_date.year - 1, 12, 1)
            total_months = exit_date.month - 5
        else:
            period_start = datetime.date(exit_date.year - 1, 12, 1)
            total_months = exit_date.month

        ag_init_amount = emp.aguinaldo_initial_amount or 0.0
        ag_init_date   = emp.aguinaldo_initial_date
        if ag_init_amount and ag_init_date and ag_init_date >= period_start:
            months_covered = (
                (ag_init_date.year * 12 + ag_init_date.month) -
                (period_start.year * 12 + period_start.month) + 1
            )
            months_from_system = max(0, total_months - months_covered)
            aguinaldo_system   = round(salary * months_from_system / 12.0, 2)
            months_worked      = total_months
            aguinaldo          = round(ag_init_amount + aguinaldo_system, 2)
            notes_lines.append(
                'Aguinaldo Art.228 CT: acumulado inicial CRC%s + %s meses sistema' % (
                    '{:,.2f}'.format(ag_init_amount), months_from_system)
            )
        else:
            months_worked = total_months
            aguinaldo = round(salary * months_worked / 12.0, 2)
            notes_lines.append(
                'Aguinaldo Art.228 CT: %s meses desde 1-dic' % months_worked
            )

        # -- Totales ----------------------------------------------------------
        total_gross = preaviso_amount + cesantia_amount + vac_amount + aguinaldo
        ccss        = round(total_gross * K.CCSS_EMP, 2)
        total_net   = round(total_gross - ccss, 2)

        # -- Prestamos y adelantos pendientes ---------------------------------
        loans = self.env['planilla.employee.loan'].search([
            ('employee_id', '=', emp.id),
            ('state', 'in', ('active', 'approved')),
            ('amount_pending', '>', 0),
        ])
        loan_lines = []
        total_pending = 0.0
        type_labels = {
            'loan':    'Prestamo',
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
                f'\nDeducciones por prestamos/adelantos: CRC{total_pending:,.2f}'
                f' ({len(loan_lines)} obligacion(es) pendiente(s))'
            )
        notes_lines.append(f'\nTOTAL FINAL a pagar: CRC{total_final:,.2f}')

        # Limpiar lineas anteriores antes de escribir
        self.loan_line_ids.unlink()

        # -- Calculate breakdown data for display ---------------------
        vac_initial_days  = emp.vacation_initial_balance or 0.0
        vac_accrued_since = round(vac_days - vac_initial_days, 2) if vac_initial_days else 0.0
        taken_payments_sim = self.env['planilla.vacation.payment'].search([
            ('employee_id', '=', emp.id),
            ('state', 'in', ('approved', 'paid')),
        ])
        vac_taken_system = round(sum(taken_payments_sim.mapped('days')), 2)

        ag_init_disp = emp.aguinaldo_initial_amount or 0.0
        ag_sys_disp  = round(aguinaldo - ag_init_disp, 2) if ag_init_disp else aguinaldo

        cesantia_days_disp = 0.0
        if cesantia_amount and daily > 0:
            cesantia_days_disp = round(cesantia_amount / daily, 2)

        ccss_rate_pct = round(K.CCSS_EMP * 100, 2)

        self.write({
            'years_service':       round(years, 2),
            'last_salary':         salary,
            'preaviso_days':       preaviso_days,
            'preaviso_amount':     preaviso_amount,
            'preaviso_applies':    preaviso_applies,
            'cesantia_amount':     cesantia_amount,
            'cesantia_applies':    cesantia_applies,
            'cesantia_days':       cesantia_days_disp,
            'cesantia_daily':      daily,
            'vacation_days':       vac_days,
            'vacation_amount':     vac_amount,
            'vac_daily_rate':      daily,
            'vac_initial_days':    vac_initial_days,
            'vac_accrued_since':   vac_accrued_since,
            'vac_taken_system':    vac_taken_system,
            'aguinaldo_months':    months_worked,
            'aguinaldo_amount':    aguinaldo,
            'aguinaldo_initial':   ag_init_disp,
            'aguinaldo_system':    ag_sys_disp,
            'ccss_rate':           ccss_rate_pct,
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
