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
        ('dismissal',    'Despido con responsabilidad patronal (Art. 80 CT)'),
        ('just_cause',   'Despido sin responsabilidad patronal (Art. 81 CT)'),
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
    # Promedio salarial manual (opcional -- Art. 153 CT para salario variable)
    use_salary_average = fields.Boolean(
        string='Usar Promedio Manual de Salarios',
        default=False,
        help='Active si desea ingresar manualmente el promedio de los ultimos 6 salarios '
             'para calcular cesantia, preaviso y vacaciones (Art. 153 CT).'
    )
    salary_average_manual = fields.Monetary(
        string='Promedio Salarios Ultimos 6 Meses (CRC)',
        currency_field='currency_id',
        help='Calculado automaticamente de los 6 salarios ingresados. '
             'Se usa para preaviso, cesantia y vacaciones (Art. 153 CT).'
    )
    # Campos para ingresar los 6 salarios individuales
    sal_m1 = fields.Monetary(string='Salario Mes 1', currency_field='currency_id',
        help='Salario bruto del mes mas reciente antes de la salida')
    sal_m2 = fields.Monetary(string='Salario Mes 2', currency_field='currency_id')
    sal_m3 = fields.Monetary(string='Salario Mes 3', currency_field='currency_id')
    sal_m4 = fields.Monetary(string='Salario Mes 4', currency_field='currency_id')
    sal_m5 = fields.Monetary(string='Salario Mes 5', currency_field='currency_id')
    sal_m6 = fields.Monetary(string='Salario Mes 6 (mas antiguo)', currency_field='currency_id')
    sal_promedio_calc = fields.Monetary(
        string='Promedio Calculado (CRC)',
        currency_field='currency_id',
        compute='_compute_sal_promedio', store=False,
        help='Promedio de los meses con salario > 0 (igual que formula Excel de RRHH)'
    )
    sal_meses_con_valor = fields.Integer(
        string='Meses con salario',
        compute='_compute_sal_promedio', store=False
    )
    preaviso_days      = fields.Integer(string='Dias de Preaviso', readonly=True)
    preaviso_amount    = fields.Monetary(string='Preaviso (CRC)', currency_field='currency_id', readonly=True)
    preaviso_applies   = fields.Boolean(
        string='Aplica Preaviso',
        help='Desmarcar si el empleado decide no ejercer/cobrar el preaviso.',
    )
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
    cesantia_days      = fields.Float(
        string='Dias de Cesantia',
        help='Pre-calculado segun tabla Art. 29 CT. Puede editar este valor '
             'si la empresa usa un criterio diferente (ej: 14 dias en lugar de 18.33).'
    )
    cesantia_days_locked = fields.Boolean(
        string='Usar dias calculados automaticamente', default=True,
        help='Desactive para ingresar manualmente los dias de cesantia.'
    )
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
            # Siempre cargar el salario base del empleado
            self.last_salary = emp.base_salary or 0.0
            # Pre-cargar ultimas 6 quincenas del sistema (agrupadas en meses)
            # Solo si NO hay datos manuales ya ingresados
            if not any([self.sal_m1, self.sal_m2, self.sal_m3,
                        self.sal_m4, self.sal_m5, self.sal_m6]):
                slips = self.env['planilla.payslip.cr'].search([
                    ('employee_id', '=', emp.id),
                    ('state', 'in', ('done', 'confirmed')),
                ], order='date_to desc', limit=12)
                # Agrupar quincenas en meses (suma de las 2 quincenas del mes)
                from collections import defaultdict
                monthly = defaultdict(float)
                for s in slips:
                    key = s.date_to.strftime('%Y-%m') if s.date_to else ''
                    if key:
                        # Usar base_salary mensual / 2 (no gross que incluye incap/bonos)
                        # Esto da el salario puro que el Excel usa en su calculo
                        monthly[key] = (s.employee_id.base_salary or 0)
                months_sorted = sorted(monthly.keys(), reverse=True)[:6]
                sal_fields = ['sal_m1','sal_m2','sal_m3','sal_m4','sal_m5','sal_m6']
                for i, key in enumerate(months_sorted):
                    setattr(self, sal_fields[i], round(monthly[key], 2))
                # Si no hay boletas, usar salario base actual para todos los meses
                if not months_sorted and emp.base_salary:
                    for f in sal_fields:
                        setattr(self, f, round(emp.base_salary, 2))

    @api.depends('sal_m1','sal_m2','sal_m3','sal_m4','sal_m5','sal_m6')
    def _compute_sal_promedio(self):
        for rec in self:
            vals = [rec.sal_m1 or 0, rec.sal_m2 or 0, rec.sal_m3 or 0,
                    rec.sal_m4 or 0, rec.sal_m5 or 0, rec.sal_m6 or 0]
            nonzero = [v for v in vals if v > 0]
            if nonzero:
                rec.sal_promedio_calc  = round(sum(nonzero) / len(nonzero), 2)
                rec.sal_meses_con_valor = len(nonzero)
            else:
                rec.sal_promedio_calc  = 0.0
                rec.sal_meses_con_valor = 0

    @api.onchange('sal_m1','sal_m2','sal_m3','sal_m4','sal_m5','sal_m6')
    def _onchange_sal_entries(self):
        """Actualiza salary_average_manual en tiempo real al digitar los salarios."""
        vals = [self.sal_m1 or 0, self.sal_m2 or 0, self.sal_m3 or 0,
                self.sal_m4 or 0, self.sal_m5 or 0, self.sal_m6 or 0]
        nonzero = [v for v in vals if v > 0]
        if nonzero:
            self.salary_average_manual = round(sum(nonzero) / len(nonzero), 2)
            self.use_salary_average = True
        else:
            self.salary_average_manual = 0.0

    def action_simulate(self):
        self.ensure_one()
        emp = self.employee_id
        if not emp.entry_date:
            raise UserError('El empleado no tiene fecha de ingreso registrada.')

        exit_date  = self.simulated_date
        entry_date = emp.entry_date
        extra_vac_days = 2  # Art. 153 CT — configurable en Cuentas Contables
        diff   = relativedelta(exit_date, entry_date)
        years  = diff.years + diff.months / 12.0 + diff.days / 365.0
        # Determinar salario a usar: promedio manual > salario base actual
        notes_lines = []
        # Calcular promedio desde los 6 campos (fuente unica de verdad)
        sal_vals = [
            self.sal_m1 or 0, self.sal_m2 or 0, self.sal_m3 or 0,
            self.sal_m4 or 0, self.sal_m5 or 0, self.sal_m6 or 0,
        ]
        sal_nonzero = [v for v in sal_vals if v > 0]
        if sal_nonzero:
            salary = round(sum(sal_nonzero) / len(sal_nonzero), 2)
            notes_lines.append(
                f'Salario: promedio {len(sal_nonzero)} meses = CRC{salary:,.2f}/mes (Art. 153 CT)'
            )
            self.salary_average_manual = salary
            self.use_salary_average = True
        elif self.use_salary_average and self.salary_average_manual > 0:
            salary = self.salary_average_manual
            notes_lines.append(
                f'Salario: promedio manual CRC{salary:,.2f} (Art. 153 CT)'
            )
        else:
            salary = self.last_salary or emp.base_salary or 0.0
            notes_lines.append(f'Salario: ultimo bruto CRC{salary:,.2f}')
        daily = salary / 30.0

        # -- Preaviso (Art. 28 CT) --------------------------------------------
        # FIX: igual que employee_termination.py — preaviso solo aplica
        # para despido sin justa causa (dismissal). 'mutual' NO genera
        # preaviso automático; el usuario puede activarlo manualmente si aplica.
        preaviso_applies = self.termination_reason in ('dismissal',)
        # Art. 28 CT -- tabla oficial:
        # < 3 meses (0.25 anos):  7 dias
        # 3-6 meses (0.25-0.5):  14 dias
        # 6-12 meses (0.5-1.0):  15 dias  <-- CORREGIDO (antes usaba 30)
        # > 1 ano:               30 dias
        # Art. 28 CT — usar meses exactos (no float) para evitar errores de redondeo
        _total_months = diff.years * 12 + diff.months
        if _total_months < 3:
            preaviso_days = 7   # < 3 meses (periodo de prueba)
        elif _total_months < 6:
            preaviso_days = 7   # 3 a menos de 6 meses
        elif _total_months < 12:
            preaviso_days = 15  # 6 meses a menos de 1 año
        else:
            preaviso_days = 30  # 1 año o más
        preaviso_amount = (daily * preaviso_days) if preaviso_applies else 0.0
        # Importante: si el usuario ya simuló y luego desmarcó preaviso_applies,
        # recalcular con el valor actualizado del campo
        if not self.preaviso_applies and preaviso_applies:
            # El usuario ya había desactivado manualmente — respetar su elección
            pass  # preaviso_applies se sobreescribirá al final con self.write()
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
            # Tabla oficial Ministerio de Trabajo CR (Art. 29 CT):
            # < 3 meses:        no aplica
            # 3 a < 6 meses:    7 días total (pago único)
            # 6 meses a < 1 año: 14 días total (pago único)
            # 1 año o más:      tabla acumulativa K.CESANTIA_TABLA
            _tm = diff.years * 12 + diff.months
            if _tm < 3:
                cesantia_amount = 0.0
                notes_lines.append('Cesantia Art.29 CT: menos de 3 meses -- no aplica')
            elif _tm < 6:
                cesantia_days = 7.0
                cesantia_amount = round(daily * cesantia_days, 2)
                notes_lines.append(f'Cesantia Art.29 CT: 3-6 meses = 7 dias = CRC{cesantia_amount:,.2f}')
            elif _tm < 12:
                cesantia_days = 14.0
                cesantia_amount = round(daily * cesantia_days, 2)
                notes_lines.append(f'Cesantia Art.29 CT: 6-12 meses = 14 dias = CRC{cesantia_amount:,.2f}')
            else:
                cesantia_days_table = K.CESANTIA_TABLA
                years_int = min(int(years), 8)
                fraction = years - int(years)
                cesantia_days = 0.0
                for y in range(1, years_int + 1):
                    cesantia_days += cesantia_days_table.get(y, 22.0)
                if years_int < 8:
                    days_this_year = cesantia_days_table.get(years_int + 1, 22.0)
                    cesantia_days += days_this_year * fraction
                cesantia_amount = round(daily * cesantia_days, 2)
                notes_lines.append(
                    f'Cesantia Art.29 CT: {years:.2f} anos x {cesantia_days:.1f} dias'
                    f' = CRC{cesantia_amount:,.2f}'
                )
        else:
            cesantia_amount = 0.0
            notes_lines.append('Cesantia Art.29 CT: no aplica para este tipo de salida')

        # -- Vacaciones pendientes (Art. 153 CT) ------------------------------
        # MISMA LÓGICA que employee_termination.py — usar exit_date como referencia.
        import math as _math
        vac_init   = emp.vacation_initial_balance or 0.0
        vac_cutoff = emp.vacation_initial_balance_date

        taken_recs = self.env['planilla.vacation.payment'].search([
            ('employee_id', '=', emp.id),
            ('state', 'in', ['approved', 'paid']),
            ('vacation_type', 'in', ['disfrutadas', 'adelanto']),
        ])
        vacation_days_taken = sum(taken_recs.mapped('days'))

        # Método de acumulación configurado en Configuración Contable
        _sim_config = self.env['planilla.accounting.config'].search(
            [('company_id', '=', self.env.company.id)], limit=1)
        accrual_method = _sim_config.vacation_accrual_method if _sim_config else 'monthly'

        def _sim_meses_desde(desde, hasta):
            import calendar as _cal2
            entry_day = entry_date.day
            count = 0
            try:    cand = datetime.date(desde.year, desde.month, entry_day)
            except: cand = datetime.date(desde.year, desde.month, _cal2.monthrange(desde.year, desde.month)[1])
            if cand <= desde:
                from dateutil.relativedelta import relativedelta as _rd3
                m = cand + _rd3(months=1)
                try:    cand = datetime.date(m.year, m.month, entry_day)
                except: cand = datetime.date(m.year, m.month, _cal2.monthrange(m.year, m.month)[1])
            while cand <= hasta:
                count += 1
                from dateutil.relativedelta import relativedelta as _rd4
                m = cand + _rd4(months=1)
                try:    cand = datetime.date(m.year, m.month, entry_day)
                except: cand = datetime.date(m.year, m.month, _cal2.monthrange(m.year, m.month)[1])
            return count

        # Inicializar para uso posterior en el breakdown
        accrued_since_cutoff = 0

        # Usar directamente los campos calculados de la ficha del empleado
        # _compute_vacation_balance ya tiene la logica correcta (igual a la ficha)
        # Solo recalcular si la fecha de salida difiere de hoy
        import datetime as _dt
        _today = _dt.date.today()
        if exit_date == _today:
            # Fecha de salida = hoy: usar directamente la ficha
            vacation_days_gross = emp.vacation_days_accrued or 0
        else:
            # Fecha de salida diferente: calcular desde la ficha + ajuste
            # Usar saldo_inicial de la ficha como base confiable
            if vac_cutoff:
                if vac_cutoff >= exit_date:
                    accrued_since_cutoff = 0
                else:
                    accrued_since_cutoff = _sim_meses_desde(vac_cutoff, exit_date)
                # Bonus aniversarios post-corte
                _bonus = 0
                _aniv = entry_date + _rd4(years=1)
                while _aniv <= exit_date:
                    if _aniv > vac_cutoff:
                        _bonus += extra_vac_days
                    _aniv += _rd4(years=1)
                vacation_days_gross = _math.floor(vac_init + accrued_since_cutoff) + _bonus
            else:
                vacation_days_gross = _sim_meses_desde(entry_date, exit_date)
                _bonus = 0
                _aniv = entry_date + _rd4(years=1)
                while _aniv <= exit_date:
                    _bonus += extra_vac_days
                    _aniv += _rd4(years=1)
                vacation_days_gross += _bonus

        vac_days   = max(vacation_days_gross - vacation_days_taken, 0)
        daily_vac  = daily
        vac_amount = round(daily_vac * vac_days, 2)
        notes_lines.append(
            f'Vacaciones Art.153 CT: {vac_days} dias '
            f'(acumulado={vacation_days_gross}, tomados={int(vacation_days_taken)}) '
            f'x CRC{daily_vac:,.2f}/dia'
        )

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
        # FIX-LIQ-03: si el usuario ingreso los 6 salarios, calcular aguinaldo
        # directamente como suma/12 (igual que Excel de RRHH).
        # Ignorar acumulado inicial si los salarios ya cubren todo el periodo.
        if sal_nonzero:
            # Aguinaldo Art.228 CT = suma de salarios del período / 12
            # MISMA LÓGICA que employee_termination.py cuando usa boletas directas:
            # ag = sum(gross_salary_boletas_en_periodo) / 12
            # Con los 6 campos manuales: ag = sum(sal_nonzero) / 12
            # Número de meses para mostrar (informativo)
            if exit_date.month == 12:
                ag_period_start = datetime.date(exit_date.year, 12, 1)
            else:
                ag_period_start = datetime.date(exit_date.year - 1, 12, 1)
            total_months = (exit_date.month % 12) + 1

            # Si hay aguinaldo_initial_amount: solo agregar meses NO cubiertos
            # IDÉNTICO a employee_termination.py para evitar doble conteo.
            # ag_init ya cubre meses desde period_start hasta ag_init_date.
            # El sistema solo aporta los meses RESTANTES.
            ag_init_amount = emp.aguinaldo_initial_amount or 0.0
            ag_init_date   = emp.aguinaldo_initial_date
            if ag_init_amount and ag_init_date and ag_init_date >= ag_period_start:
                months_covered = (
                    (ag_init_date.year * 12 + ag_init_date.month) -
                    (ag_period_start.year * 12 + ag_period_start.month) + 1
                )
                months_from_system = max(total_months - months_covered, 0)
                # Solo los meses restantes × salario mensual promedio
                aguinaldo_system   = round(salary / 12.0 * months_from_system, 2)
                months_worked      = total_months
                aguinaldo          = round(ag_init_amount + aguinaldo_system, 2)
            else:
                # Sin acumulado inicial: suma de salarios del período / 12
                months_worked = total_months
                aguinaldo     = round(sum(sal_nonzero) / 12.0, 2)

            notes_lines.append(
                'Aguinaldo Art.228 CT: suma {} salarios / 12 = CRC{:,.2f} ({} meses)'.format(
                    len(sal_nonzero), aguinaldo, months_worked)
            )
        else:
            # Sin campos manuales: usar boletas del sistema
            slips_in_period = self.env['planilla.payslip.cr'].search([
                ('employee_id', '=', emp.id),
                ('state', '=', 'done'),
                ('date_from', '>=', period_start),
                ('date_to', '<=', exit_date),
            ])
            sum_salaries_system = round(sum(slips_in_period.mapped('gross_salary')), 2)
            if sum_salaries_system > 0:
                aguinaldo_from_system = round(sum_salaries_system / 12.0, 2)
                if ag_init_amount and ag_init_date and ag_init_date >= period_start:
                    aguinaldo     = round(ag_init_amount + aguinaldo_from_system, 2)
                    months_worked = total_months
                    notes_lines.append(
                        'Aguinaldo Art.228 CT: inicial CRC%s + sistema CRC%s' % (
                            '{:,.2f}'.format(ag_init_amount),
                            '{:,.2f}'.format(aguinaldo_from_system))
                    )
                else:
                    aguinaldo     = aguinaldo_from_system
                    months_worked = len(slips_in_period)
                    notes_lines.append(
                        'Aguinaldo Art.228 CT: CRC%s / 12 (%s boletas)' % (
                            '{:,.2f}'.format(sum_salaries_system),
                            len(slips_in_period))
                    )
            elif ag_init_amount:
                aguinaldo     = ag_init_amount
                months_worked = total_months
                notes_lines.append(
                    'Aguinaldo Art.228 CT: acumulado inicial CRC%s' % (
                        '{:,.2f}'.format(ag_init_amount),)
                )
            else:
                months_worked = total_months
                aguinaldo = round(salary * months_worked / 12.0, 2)
                notes_lines.append(
                    'Aguinaldo Art.228 CT: %s meses estimado' % months_worked
                )

        # -- Totales ----------------------------------------------------------
        # Si el usuario ya había desmarcado preaviso_applies antes de recalcular,
        # respetar esa decisión. El campo se guarda antes del recálculo.
        if not self.preaviso_applies and self.id:
            preaviso_applies = False
            preaviso_amount  = 0.0
        total_gross = preaviso_amount + cesantia_amount + vac_amount + aguinaldo
        # FIX-LIQ-01: CCSS solo sobre rubros AFECTOS (vacaciones + preaviso)
        # Aguinaldo y cesantia estan EXENTOS de CCSS (Art. 35 Ley CCSS, Art. 173 CT)
        base_ccss   = round((vac_amount or 0.0) + (preaviso_amount or 0.0), 2)
        _cfg = self.env['planilla.accounting.config'].search(
            [('company_id', '=', self.env.company.id)], limit=1)
        _skip = _cfg.skip_ccss_on_termination if _cfg else False
        ccss        = 0.0 if _skip else round(base_ccss * K.CCSS_EMP, 2)
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
        vac_initial_days  = int(vac_init)
        vac_accrued_since = int(accrued_since_cutoff) if vac_cutoff else 0
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
            'cesantia_days_locked': True,
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

    @api.onchange('cesantia_days')
    def _onchange_cesantia_days(self):
        """Recalcula cesantia_amount en tiempo real al editar los dias."""
        if not self.computed or not self.cesantia_days:
            return
        emp = self.employee_id
        if self.use_salary_average and self.salary_average_manual > 0:
            salary = self.salary_average_manual
        else:
            salary = self.last_salary or (emp.base_salary if emp else 0.0) or 0.0
        daily = salary / 30.0
        new_ces = round(daily * self.cesantia_days, 2)
        self.cesantia_amount  = new_ces
        self.cesantia_daily   = daily
        self.cesantia_days_locked = False
        total_gross = self.preaviso_amount + new_ces + self.vacation_amount + self.aguinaldo_amount
        # CCSS solo sobre rubros afectos: vacaciones + preaviso
        # Cesantia (Art.173 CT) y Aguinaldo (Art.35 Ley CCSS) exentos
        base_ccss = round((self.preaviso_amount or 0.0) + (self.vacation_amount or 0.0), 2)
        _cfg2 = self.env['planilla.accounting.config'].search(
            [('company_id', '=', self.env.company.id)], limit=1)
        ccss = 0.0 if (_cfg2.skip_ccss_on_termination if _cfg2 else False) \
            else round(base_ccss * K.CCSS_EMP, 2)
        self.total_gross   = total_gross
        self.ccss_on_total = ccss
        self.total_net     = round(total_gross - ccss, 2)
        self.total_final   = round(self.total_net - self.total_loans_pending, 2)

    def action_recalculate_cesantia(self):
        """Guarda y recalcula cesantia con los dias editados manualmente."""
        self.ensure_one()
        # Forzar recalculo (el onchange ya actualizo los valores en memoria)
        # Llamar explicitamente para asegurar que la BD tiene los valores
        self._onchange_cesantia_days()
        emp = self.employee_id
        if self.use_salary_average and self.salary_average_manual > 0:
            salary = self.salary_average_manual
        else:
            salary = self.last_salary or emp.base_salary or 0.0
        daily = salary / 30.0
        new_cesantia = round(daily * self.cesantia_days, 2)
        total_gross = self.preaviso_amount + new_cesantia + self.vacation_amount + self.aguinaldo_amount
        base_ccss_rec = round((self.preaviso_amount or 0.0) + (self.vacation_amount or 0.0), 2)
        _cfg3 = self.env['planilla.accounting.config'].search(
            [('company_id', '=', self.env.company.id)], limit=1)
        ccss = 0.0 if (_cfg3.skip_ccss_on_termination if _cfg3 else False) \
            else round(base_ccss_rec * K.CCSS_EMP, 2)
        total_net = round(total_gross - ccss, 2)
        total_final = round(total_net - self.total_loans_pending, 2)
        self.write({
            'cesantia_amount':     new_cesantia,
            'cesantia_daily':      daily,
            'total_gross':         total_gross,
            'ccss_on_total':       ccss,
            'total_net':           total_net,
            'total_final':         total_final,
            'cesantia_days_locked': False,
        })
        return {
            'type':      'ir.actions.act_window',
            'res_model': 'planilla.termination.simulator',
            'res_id':    self.id,
            'view_mode': 'form',
            'target':    'new',
        }
