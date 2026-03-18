import logging
from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class PayrollRunCR(models.Model):
    _name = 'planilla.run.cr'
    _description = 'Planilla'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_end desc'

    # FIX v58: El constraint de BD UNIQUE y el ORM simple han sido reemplazados
    # por _check_no_duplicate_employees_across_runs() que implementa la regla
    # de negocio correcta: se permiten múltiples planillas en el mismo período
    # siempre que no dupliquen empleados.

    @api.constrains('company_id', 'date_start', 'date_end')
    def _check_no_duplicate_employees_across_runs(self):
        """
        Regla de negocio central v58 para planillas:

        Se PERMITEN múltiples planillas (runs) en el mismo período calendario
        siempre que cumplan: ningún empleado aparece en dos planillas solapadas.

        Criterios de agrupación VÁLIDOS para planillas del mismo período:
          ✓ Por calendarización (quincenal, mensual, semanal)
          ✓ Por sucursal (Alajuela, San José, Heredia)
          ✓ Por departamento (Ventas, Producción, Admin)
          ✓ Planilla especial de corrección con empleados distintos
          ✓ Cualquier combinación, si no hay empleados duplicados

        Lo que se BLOQUEA:
          ✗ Mismo empleado en dos planillas con períodos que se solapan

        NOTA: La validación efectiva del empleado ocurre al crear la boleta
        (PayslipCR._check_no_duplicate_employee_period). Este constraint en el
        Run es un guardia preventivo que verifica si las boletas YA EXISTENTES
        de esta planilla cruzarían con los de otra planilla activa en el mismo
        período de la misma empresa.
        """
        for rec in self:
            if not rec.date_start or not rec.date_end:
                continue
            # Buscar otras planillas activas de la misma empresa que solapan
            otras_runs = self.search([
                ('company_id', '=', rec.company_id.id),
                ('date_start', '<=', rec.date_end),
                ('date_end',   '>=', rec.date_start),
                ('state',      '!=', 'cancelled'),
                ('id',         '!=', rec.id),
            ])
            if not otras_runs:
                continue
            # Verificar si algún empleado de esta planilla ya está en otra planilla solapada
            mis_empleados = rec.payslip_ids.filtered(
                lambda p: p.state != 'cancelled'
            ).mapped('employee_id')
            if not mis_empleados:
                continue  # Sin boletas aún — no hay conflicto posible
            for otra in otras_runs:
                empleados_otra = otra.payslip_ids.filtered(
                    lambda p: p.state != 'cancelled'
                ).mapped('employee_id')
                duplicados = mis_empleados & empleados_otra
                if duplicados:
                    nombres = ', '.join(duplicados.mapped('name'))
                    raise ValidationError(
                        f'La planilla "{rec.name}" ({rec.date_start} — {rec.date_end}) '
                        f'tiene empleado(s) que ya aparecen en la planilla '
                        f'"{otra.name}" ({otra.date_start} — {otra.date_end}):\n\n'
                        f'  {nombres}\n\n'
                        f'Un mismo empleado no puede tener boletas en dos planillas '
                        f'con períodos que se solapan en el calendario. '
                        f'Verifique que las planillas tengan empleados distintos o '
                        f'que sus períodos no se crucen.'
                    )

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
    total_rop_employer = fields.Monetary(
        string='ROP Patronal Total', currency_field='currency_id',
        compute='_compute_totals', store=True,
        help='Suma del ROP patronal (3.25%) de todas las boletas activas con rop_applies=True.'
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
                 'payslip_ids.income_tax', 'payslip_ids.rop_employer',
                 'payslip_ids.state')  # FIX v512 BUG-05: rop_employer agregado al depends
    def _compute_totals(self):
        for rec in self:
            # FIX NEW-06 v54: excluir boletas canceladas de los totales.
            # Antes se sumaban TODAS las boletas incluidas las canceladas, inflando
            # los totales cuando se cancelaba una boleta dentro de una planilla.
            active_slips = rec.payslip_ids.filtered(lambda p: p.state != 'cancelled')
            rec.total_gross         = sum(active_slips.mapped('gross_salary'))
            rec.total_ccss_employee = sum(active_slips.mapped('ccss_employee'))
            rec.total_income_tax    = sum(active_slips.mapped('income_tax'))
            rec.total_ccss_employer = sum(active_slips.mapped('ccss_employer'))
            rec.total_employer_cost = sum(active_slips.mapped('total_employer_cost'))

            # Total Deducciones = CCSS Obrero + Renta
            rec.total_deductions = round(
                rec.total_ccss_employee + rec.total_income_tax, 2
            )
            # Total Neto = suma del net_salary de cada boleta activa (bruto - CCSS obrero - renta)
            rec.total_net = round(
                sum(active_slips.mapped('net_salary')), 2
            )

            # Salario a Pagar = lo que realmente se deposita (neto - pensiones, prestamos y deducciones adicionales)
            rec.total_salary_payable = round(
                sum(active_slips.mapped('salary_payable')), 2
            )

            # FIX v511: Agregar ROP al costo patronal total de la planilla
            # (rop_employer = 3.25% del gross por empleado, si rop_applies=True)
            rec.total_rop_employer = round(
                sum(active_slips.mapped('rop_employer')), 2
            )

            # Costo real por cada colon que el empleado recibe en mano
            if rec.total_salary_payable and rec.total_salary_payable > 0:
                rec.cost_per_net_colon = round(rec.total_employer_cost / rec.total_salary_payable, 4)
            else:
                rec.cost_per_net_colon = 0.0

    def action_generate_payslips(self):
        """
        Genera boletas para todos los empleados activos según los filtros de la planilla.

        v58: Antes de crear boletas, verifica si algún empleado ya tiene boleta activa
        en OTRA planilla con período solapado. Reporta los conflictos claramente
        para que RRHH pueda decidir qué hacer antes de continuar.

        Filtros aplicados (acumulativos):
          - Sucursal:         si branch_id está definido
          - Departamento:     si department_id está definido
          - Calendarización:  si payroll_calendar_id está definido
        """
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

        # Advertir sobre empleados sin employee_status_id
        without_status = employees.filtered(lambda e: not e.employee_status_id)
        employees_active = employees.filtered(
            lambda e: e.employee_status_id and e.employee_status_id.is_active_payroll
        )
        if without_status:
            names = ', '.join(without_status.mapped('name'))
            self.message_post(
                body=(
                    f'⚠️ <b>Empleados sin Estado de Nómina:</b> Los siguientes empleados '
                    f'no tienen un "Estado de Empleado" configurado y fueron excluidos: '
                    f'<b>{names}</b>. '
                    f'Configure el estado en Planilla → Empleados → Estado de Empleado.'
                ),
                message_type='notification',
            )

        # Excluir empleados sin seguro CCSS de la planilla estándar
        employees_active = employees_active.filtered(
            lambda e: getattr(e, 'ccss_insured', True)
        )

        # ── Verificación cruzada: detectar empleados ya en otra planilla solapada ──
        # Esto previene el error de constraint ANTES de intentar crear las boletas,
        # dando un reporte completo de todos los conflictos en lugar de fallar uno a uno.
        conflictos = []
        otras_runs_activas = self.search([
            ('company_id', '=', self.company_id.id),
            ('date_start', '<=', self.date_end),
            ('date_end',   '>=', self.date_start),
            ('state',      '!=', 'cancelled'),
            ('id',         '!=', self.id),
        ])
        if otras_runs_activas:
            # Indexar empleados de otras planillas: {employee_id: [run_names]}
            emp_en_otras = {}
            for otra in otras_runs_activas:
                for slip in otra.payslip_ids.filtered(lambda p: p.state != 'cancelled'):
                    emp_en_otras.setdefault(slip.employee_id.id, []).append(
                        f'{otra.name} ({otra.date_start} — {otra.date_end})'
                    )
            for emp in employees_active:
                if emp.id in emp_en_otras:
                    runs_texto = ', '.join(emp_en_otras[emp.id])
                    conflictos.append(f'  • {emp.name} → ya en: {runs_texto}')

        if conflictos:
            raise UserError(
                f'No se pueden generar boletas porque los siguientes empleados ya tienen '
                f'boletas activas en otra(s) planilla(s) con período solapado '
                f'({self.date_start} — {self.date_end}):\n\n'
                + '\n'.join(conflictos) + '\n\n'
                f'Opciones:\n'
                f'  1. Ajuste los filtros de esta planilla (sucursal/departamento/calendarización) '
                f'para excluir esos empleados.\n'
                f'  2. Cancele las boletas en conflicto en las otras planillas.\n'
                f'  3. Ajuste los períodos para que no se solapeen.'
            )

        # ── Generación por lotes (FIX B-10 v58) ──────────────────────────────────
        BATCH_SIZE = int(self.env['ir.config_parameter'].sudo().get_param(
            'planilla_cr.batch_size_generate', default=50
        ))
        employees_list = list(employees_active)
        created_count  = 0
        skipped_count  = 0

        for i in range(0, len(employees_list), BATCH_SIZE):
            batch = employees_list[i:i + BATCH_SIZE]
            for employee in batch:
                # Verificar si ya existe boleta en ESTA planilla (re-ejecución del botón)
                existing = self.env['planilla.payslip.cr'].search([
                    ('employee_id',    '=', employee.id),
                    ('payroll_run_id', '=', self.id),
                ])
                if not existing:
                    self.env['planilla.payslip.cr'].create({
                        'employee_id':    employee.id,
                        'payroll_run_id': self.id,
                        'date_from':      self.date_start,
                        'date_to':        self.date_end,
                    })
                    created_count += 1
                else:
                    skipped_count += 1

        _logger.info(
            'planilla_cr.action_generate_payslips: planilla "%s" — %d creadas, %d omitidas (lote=%d)',
            self.name, created_count, skipped_count, BATCH_SIZE
        )

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

        # FIX v512 BUG-03: estado real es 'done', no 'paid'.
        # El filtro anterior usaba 'paid' silenciosamente y dejaba pasar boletas en 'done'.
        not_confirmed = self.payslip_ids.filtered(
            lambda p: p.state not in ('confirmed', 'done', 'cancelled')
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

        # ── Sumar todos los montos de todas las boletas ──────────────────────
        total_gross         = round(sum(payslips.mapped('gross_salary')), 2)
        total_ccss_employer = round(sum(payslips.mapped('ccss_employer')), 2)
        total_ins_employer  = round(sum(payslips.mapped('ins_employer')), 2)
        total_vacation_prov = round(sum(payslips.mapped('vacation_provision')), 2)
        total_aguinaldo_prov= round(sum(payslips.mapped('aguinaldo_provision')), 2)
        total_cesantia_prov = round(sum(payslips.mapped('cesantia_provision')), 2)
        total_ccss_employee = round(sum(payslips.mapped('ccss_employee')), 2)
        total_income_tax    = round(sum(payslips.mapped('income_tax')), 2)

        # FIX v48 — Componentes que faltaban y causaban descuadre en modo per_run
        total_subsidy    = round(sum(payslips.mapped('ccss_subsidy_total')), 2)
        total_dis_cost   = round(sum(payslips.mapped('employer_disability_cost')), 2)
        total_paternity  = round(sum(payslips.mapped('paternity_amount')), 2)

        # Deducciones adicionales de todas las boletas
        all_deduction_lines = payslips.mapped('deduction_line_ids')

        # FIX v54: Separar bonos salariales, subsidios exentos y embargos
        # para asientos correctos en modo per_run (mismo criterio que per_employee)
        total_bonos_salariales = round(sum(p.bono_salarial_amount or 0.0 for p in payslips), 2)
        total_rop_emp    = round(sum(p.rop_employer or 0.0 for p in payslips), 2)
        total_rop_obrero = round(sum(
            l.amount for p in payslips
            for l in p.deduction_line_ids
            if l.deduction_category == 'rop' and l.line_type == 'deduction'
        ), 2)
        total_bonos_total = round(sum(
            l.amount for l in all_deduction_lines
            if l.line_type == 'income' and l.deduction_category == 'bonus'
        ), 2)
        total_subsidios_exentos = max(round(total_bonos_total - total_bonos_salariales, 2), 0.0)
        total_otros_ingresos = round(sum(
            l.amount for l in all_deduction_lines
            if l.line_type == 'income' and l.deduction_category != 'bonus'
        ), 2)
        total_extra_income = round(total_bonos_total + total_otros_ingresos, 2)

        total_pensiones = round(sum(
            l.amount for l in all_deduction_lines
            if l.deduction_category == 'pension_alimentaria'
        ), 2)
        total_prestamos = round(sum(
            l.amount for l in all_deduction_lines
            if l.deduction_category == 'loan'
        ), 2)
        total_embargos = round(sum(
            l.amount for l in all_deduction_lines
            if l.deduction_category == 'embargo'
        ), 2)
        total_ausencias = round(sum(
            l.amount for l in all_deduction_lines
            if l.deduction_category == 'ausencia'
        ), 2)
        # FIX v512 BUG-CRÍTICO-01: 'rop' excluido de total_otras_ded.
        # Mismo fix que en payslip_accounting_mixin: el ROP obrero va a account_rop_payable,
        # no a account_salary_payable. Sin exclusión se descontaba dos veces del neto.
        total_otras_ded = round(sum(
            l.amount for l in all_deduction_lines
            if l.line_type == 'deduction'
            and l.deduction_category not in ('pension_alimentaria', 'loan', 'ausencia', 'embargo', 'rop')
        ), 2)

        # Neto total a depositar
        total_net_for_accounting = round(
            total_gross - total_ccss_employee - total_income_tax
            + total_subsidy + total_paternity + total_extra_income
            - total_pensiones - total_embargos - total_prestamos
            - total_ausencias - total_rop_obrero - total_otras_ded,
            2
        )

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

        # ── DÉBITOS (Gastos del patrono) ────────────────────────────────────
        run_name = self.name
        add_line(config.account_salary_expense, debit=total_gross,
                 name=f'Salarios — Planilla {run_name}')
        add_line(config.account_social_charges_expense,
                 debit=round(total_ccss_employer + total_ins_employer, 2),
                 name=f'Cargas Sociales — Planilla {run_name}')
        add_line(config.account_vacation_expense, debit=total_vacation_prov,
                 name=f'Provisión Vacaciones — Planilla {run_name}')
        add_line(config.account_aguinaldo_expense, debit=total_aguinaldo_prov,
                 name=f'Provisión Aguinaldo — Planilla {run_name}')
        add_line(config.account_cesantia_expense, debit=total_cesantia_prov,
                 name=f'Provisión Cesantía — Planilla {run_name}')
        # FIX v48 — Paternidad, días 1-3 incapacidad y subsidio (mismo lógica que asiento individual)
        if total_paternity > 0:
            add_line(config.account_salary_expense, debit=total_paternity,
                     name=f'Paternidad (Art. 95 CT) — Planilla {run_name}')
        if total_dis_cost > 0:
            add_line(config.account_salary_expense, debit=total_dis_cost,
                     name=f'Incapacidad días 1-3 (cargo patrono) — Planilla {run_name}')
        if total_subsidy > 0:
            # FIX v49 Bug 5: misma jerarquía que el asiento individual
            ccss_subsidy_acct = config.account_ccss_subsidy_receivable
            if not ccss_subsidy_acct:
                ccss_subsidy_acct = self.env['account.account'].search([
                    ('code', '=', '120500'),
                    ('company_ids', 'in', self.env.company.id),
                ], limit=1)
            if not ccss_subsidy_acct:
                ccss_subsidy_acct = config.account_ccss_payable
            add_line(ccss_subsidy_acct, debit=total_subsidy,
                     name=f'Subsidio CCSS por Cobrar (incapacidades) — Planilla {run_name}')
        # FIX v56: ROP patronal — costo del patrono
        if total_rop_emp > 0:
            add_line(config.account_social_charges_expense,
                     debit=total_rop_emp,
                     name=f'ROP Patronal 3.25% Ley 7983 — Planilla {run_name}')

        if total_bonos_salariales > 0:
            bono_acct = config.account_bono_expense or config.account_salary_expense
            add_line(bono_acct, debit=total_bonos_salariales,
                     name=f'Bonos e Incentivos Salariales — Planilla {run_name}')
        if total_subsidios_exentos > 0:
            subs_acct = config.account_subsidio_expense or config.account_salary_expense
            add_line(subs_acct, debit=total_subsidios_exentos,
                     name=f'Subsidios al Personal (exentos) — Planilla {run_name}')
        if total_otros_ingresos > 0:
            add_line(config.account_salary_expense, debit=total_otros_ingresos,
                     name=f'Otros Ingresos en Boletas — Planilla {run_name}')

        # CRÉDITOS
        add_line(config.account_ccss_payable, credit=total_ccss_employee + total_ccss_employer, name='CCSS por Pagar — Planilla ' + self.name)
        add_line(config.account_ins_payable, credit=total_ins_employer, name='INS por Pagar — Planilla ' + self.name)
        add_line(config.account_income_tax_payable, credit=total_income_tax, name='Retención Renta — Planilla ' + self.name)
        add_line(config.account_aguinaldo_provision, credit=total_aguinaldo_prov, name='Provisión Aguinaldo — Planilla ' + self.name)
        add_line(config.account_cesantia_provision, credit=total_cesantia_prov, name='Provisión Cesantía — Planilla ' + self.name)
        add_line(config.account_vacation_provision, credit=total_vacation_prov, name='Provisión Vacaciones — Planilla ' + self.name)

        # FIX C-01 v58: ROP obrero + patronal — consolidar en account_rop_payable.
        # El bug anterior ponía el crédito del ROP obrero en account_ccss_payable,
        # separado del HABER del ROP patronal, causando descuadre cuando rop_emp >0.
        # Ambos tramos van al HABER en la misma cuenta 230350 para depósito al operador.
        total_rop_pagar = round(total_rop_obrero + total_rop_emp, 2)
        if total_rop_pagar > 0:
            rop_acct = (getattr(config, 'account_rop_payable', None)
                        or config.account_ccss_payable)
            add_line(rop_acct, credit=total_rop_pagar,
                     name=f'ROP por Pagar (obrero 1% + patronal 3.25%) — Planilla {run_name}')

        # Embargos judiciales — cuenta separada 230960 para control judicial
        if total_embargos > 0:
            embargo_account = (config.account_embargo_payable or config.account_salary_payable)
            add_line(embargo_account, credit=total_embargos,
                     name=f'Embargos Judiciales Retenidos — Planilla {run_name}')

        # FIX NEW-01 v54: usar account_pension_alimentaria_payable (campo correcto).
        # La version anterior usaba hasattr('account_pension_payable') que no existe en
        # accounting_config.py — las pensiones siempre iban a account_salary_payable.
        if total_pensiones > 0:
            pension_account = (config.account_pension_alimentaria_payable
                               or config.account_salary_payable)
            add_line(pension_account, credit=total_pensiones, name='Pensiones Alimentarias Retenidas — Planilla ' + self.name)

        # B7 FIX: cuotas de préstamos retenidas
        if total_prestamos > 0:
            loan_account = config.account_loans_payable if config.account_loans_payable else config.account_salary_payable
            add_line(loan_account, credit=total_prestamos, name='Cuotas Préstamos Retenidos — Planilla ' + self.name)

        # B7 FIX: otras deducciones adicionales
        if total_ausencias > 0:
            add_line(config.account_salary_payable, credit=total_ausencias,
                     name=f'Descuento Ausencias Sin Goce — Planilla {run_name}')
        if total_otras_ded > 0:
            add_line(config.account_salary_payable, credit=total_otras_ded,
                     name=f'Otras Deducciones Retenidas — Planilla {run_name}')
        if total_dis_cost > 0:
            add_line(config.account_salary_payable, credit=total_dis_cost,
                     name=f'Incapacidad días 1-3 (por pagar al empleado) — Planilla {run_name}')
        if total_net_for_accounting > 0:
            add_line(config.account_salary_payable, credit=total_net_for_accounting,
                     name=f'Salarios por Pagar (neto a depositar) — Planilla {run_name}')

        if not lines:
            return

        # Verificar cuadre antes de postear
        total_debit  = round(sum(l[2]['debit']  for l in lines), 2)
        total_credit = round(sum(l[2]['credit'] for l in lines), 2)
        if abs(total_debit - total_credit) > 0.02:
            detail = '\n'.join(
                f"  {'DEBE' if l[2]['debit'] else 'HABER'} ₡{max(l[2]['debit'], l[2]['credit']):>12,.2f}  {l[2]['name']}"
                for l in lines
            )
            raise UserError(
                f'El asiento consolidado no cuadra para la planilla {run_name}:\n'
                f'  Débitos:  ₡{total_debit:,.2f}\n'
                f'  Créditos: ₡{total_credit:,.2f}\n'
                f'  Diferencia: ₡{abs(total_debit - total_credit):,.2f}\n\n'
                f'Detalle de líneas:\n{detail}'
            )

        move = self.env['account.move'].create({
            'journal_id': config.journal_id.id,
            'date': self.date_end,
            'ref': ref,
            'move_type': 'entry',
            'line_ids': lines,
        })
        move.action_post()
        self.move_id = move.id
        # FIX M-05 v58: Logging de trazabilidad del asiento consolidado
        _logger.info(
            'planilla_cr.per_run._create_consolidated: asiento %s (id=%d) — '
            '%d boleta(s), DEBE=₡%.2f HABER=₡%.2f planilla="%s"',
            move.name, move.id, len(payslips),
            total_debit, total_credit, self.name
        )
        # FIX v512 BUG-CRÍTICO-02: eliminado bloque de código muerto (ensure_one +
        # return act_window) que apareció aquí por error en refactoring.
        # El caller action_pay() no usa el valor de retorno de este método privado.
        # La acción de ver el asiento ya existe en action_view_accounting_entry().

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
            ('state', '=', 'done'),  # FIX v512 BUG-03: estado real es 'done'
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
        if self.state != 'draft':
            raise UserError('Solo se pueden confirmar planillas en borrador.')
        # Verificar doble pago antes de confirmar
        self._check_no_duplicate_payment()
        payslips_draft = self.payslip_ids.filtered(lambda p: p.state == 'draft')
        if not payslips_draft:
            raise UserError(
                'No hay boletas en borrador para confirmar. '
                'Todas las boletas ya están confirmadas, pagadas o canceladas.'
            )
        # FIX A-01 v58: Delegar al action_confirm del mixin que usa write() batch
        # con atomicidad completa. El savepoint garantiza que si una boleta falla,
        # ninguna queda confirmada (antes era loop individual — posible estado inconsistente).
        with self.env.cr.savepoint():
            try:
                payslips_draft.action_confirm()
            except Exception as e:
                raise UserError(
                    f'No se pudo confirmar la planilla "{self.name}". '
                    f'Ninguna boleta fue confirmada (rollback automático).\n\n'
                    f'Error: {str(e)}'
                )
            self.write({'state': 'confirmed'})
        _logger.info(
            'planilla_cr.run.action_confirm: planilla "%s" confirmada — %d boleta(s)',
            self.name, len(payslips_draft)
        )

    def action_send_all_payslips(self):
        """Envía todas las boletas por correo."""
        self.ensure_one()
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
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Boletas de Pago',
            'res_model': 'planilla.payslip.cr',
            'view_mode': 'list,form',
            'domain': [('payroll_run_id', '=', self.id)],
        }

    def action_cancel(self):
        # FIX v512 BP-02: ensure_one() consistente con otros métodos del modelo
        self.ensure_one()
        self.payslip_ids.action_cancel()
        self.state = 'cancelled'

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

    def action_view_accounting_entry(self):
        self.ensure_one()
        if not self.move_id:
            raise UserError(
                'Esta planilla no tiene asiento contable generado. '
                'Pague la planilla primero para generar el asiento contable.'
            )
        return {
            'type': 'ir.actions.act_window',
            'name': 'Asiento Contable',
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': self.move_id.id,
        }

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
                    # FIX v512 SEC-02: registrar trazabilidad ANTES de cancelar masivo.
                    # Sin este log, era imposible auditar quién revirtió el asiento y cuándo.
                    _logger.warning(
                        'planilla_cr.AccountMovePayrollSync: asiento %s revertido/cancelado '
                        'por usuario %s (id=%d) — cancelando planilla "%s" y %d boleta(s).',
                        run.move_id.name, run.env.user.name, run.env.user.id,
                        run.name, len(run.payslip_ids.filtered(lambda p: p.state != 'cancelled'))
                    )
                    slips_to_cancel = run.payslip_ids.filtered(
                        lambda p: p.state not in ('cancelled',)
                    )
                    slips_to_cancel.write({'state': 'cancelled'})
                    run.write({'state': 'cancelled'})
                    # Notificar al grupo aprobador para revisión inmediata
                    try:
                        run.message_post(
                            body=(
                                f'⚠️ <b>Planilla cancelada automáticamente</b> porque el asiento '
                                f'contable <b>{run.move_id.name}</b> fue revertido o cancelado '
                                f'por <b>{run.env.user.name}</b>. '
                                f'Se cancelaron {len(slips_to_cancel)} boleta(s). '
                                f'Revise si esto fue intencional y tome acción si corresponde.'
                            ),
                            message_type='notification',
                        )
                    except Exception as e:
                        _logger.error(
                            'planilla_cr.AccountMovePayrollSync: error enviando notificación: %s', e
                        )
        return res

    def unlink(self):
        # Si se elimina el asiento, cancelar planilla asociada
        runs = self.env['planilla.run.cr'].search([
            ('move_id', 'in', self.ids),
            ('state', '=', 'done'),
        ])
        res = super().unlink()
        for run in runs:
            slips_to_cancel = run.payslip_ids.filtered(
                lambda p: p.state not in ('cancelled',)
            )
            # FIX v512 SEC-02: trazabilidad en eliminación de asiento
            _logger.warning(
                'planilla_cr.AccountMovePayrollSync.unlink: asiento eliminado — '
                'cancelando planilla "%s" y %d boleta(s). Usuario: %s.',
                run.name, len(slips_to_cancel), run.env.user.name
            )
            slips_to_cancel.write({'state': 'cancelled'})
            run.write({'state': 'cancelled'})
            try:
                run.message_post(
                    body=(
                        f'⚠️ <b>Planilla cancelada automáticamente</b> porque el asiento '
                        f'contable fue <b>eliminado</b> por <b>{run.env.user.name}</b>. '
                        f'Se cancelaron {len(slips_to_cancel)} boleta(s). '
                        f'Revise si esto fue intencional.'
                    ),
                    message_type='notification',
                )
            except Exception as e:
                _logger.error(
                    'planilla_cr.AccountMovePayrollSync.unlink: error notificación: %s', e
                )
        return res
