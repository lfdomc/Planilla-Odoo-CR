import logging
import datetime
from odoo import models, fields, api
from .. import planilla_const as K
from odoo.exceptions import UserError, ValidationError
from ..closed_period import PlanillaClosedPeriod

_logger = logging.getLogger(__name__)

class PayslipSyncMixin(models.AbstractModel):
    """
    Mixin: sincronización de novedades con la boleta.
    _sync_recurring_benefits, _sync_loan_deductions, _sync_pension_alimentaria,
    _sync_novedades, _sync_ausencias, _sync_rop, _sync_embargos, _sync_bonos.
    """
    _name = 'planilla.payslip.sync.mixin'
    _description = 'Mixin Sync Novedades Boleta'

    def _sync_recurring_benefits(self) -> None:
        """Auto-apply active recurring benefits/deductions for the period.
        FIX C-08 v53: Si la línea ya existe y es de tipo porcentaje, recalcular
        el monto en base al gross_salary actual (puede haber cambiado por novedades).
        """
        for rec in self:
            if rec.state != 'draft':
                continue
            emp = rec.employee_id
            today = rec.date_from
            if not today:
                continue
            benefits = self.env['planilla.recurring.benefit'].search([
                ('employee_id', '=', emp.id),
                ('active', '=', True),
                '|', ('date_start', '=', False), ('date_start', '<=', today),
                '|', ('date_end', '=', False),   ('date_end', '>=', today),
            ])
            for ben in benefits:
                existing = rec.deduction_line_ids.filtered(
                    lambda l, b=ben: l.recurring_benefit_id.id == b.id
                )
                amt = ben.get_amount_for_salary(rec.gross_salary or 0.0)
                if existing:
                    # FIX C-08 v53: Actualizar monto si el beneficio es de porcentaje
                    # y el salario bruto cambió desde la última sincronización.
                    if ben.amount_type == 'percentage':
                        for line in existing:
                            if line.amount != amt:
                                line.amount = amt
                    continue
                rec.deduction_line_ids = [(0, 0, {
                    'deduction_code_id':    ben.deduction_code_id.id,
                    'description':          ben.name,
                    'line_type':            'income' if ben.benefit_type == 'income' else 'deduction',
                    'amount_type':          ben.amount_type,
                    'amount':               amt,
                    'percentage':           ben.percentage,
                    'recurring_benefit_id': ben.id,
                })]

    def _sync_loan_deductions(self) -> None:
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
                    'line_type':           'deduction',        # FIX-E9: faltaba
                    'deduction_category':  'loan',             # FIX-E9: faltaba → salary_payable correcto
                    'amount':              installment.amount,
                    'loan_installment_id': installment.id,
                })


    def _sync_pension_alimentaria(self) -> None:
        """Sincroniza pensiones alimentarias activas del empleado como deducciones."""
        self.ensure_one()
        if self.state != 'draft':
            return

        # Código de deducción para pensiones alimentarias
        pension_code = self.env['planilla.deduction.code'].search(
            [('code', '=', 'PENSION_ALIM')], limit=1
        )
        if not pension_code:
            # FIX v512 SEC-01: patrón anti race-condition.
            # Con múltiples workers de Odoo en paralelo, dos workers podrían ejecutar
            # el search anterior simultáneamente (ambos vacío) y crear registros duplicados.
            try:
                pension_code = self.env['planilla.deduction.code'].sudo().create({
                    'name': 'Pensión Alimentaria',
                    'code': 'PENSION_ALIM',
                    'deduction_type': 'employee',
                })
            except Exception:
                pension_code = self.env['planilla.deduction.code'].search(
                    [('code', '=', 'PENSION_ALIM')], limit=1
                )

        # Buscar pensiones activas del empleado vigentes en el período
        pensiones = self.env['planilla.pension.alimentaria'].search([
            ('employee_id', '=', self.employee_id.id),
            ('state', '=', 'active'),
            ('active', '=', True),
            ('date_start', '<=', self.date_to),
            '|',
            ('date_end', '=', False),
            ('date_end', '>=', self.date_from),
        ])

        for pension in pensiones:
            # Verificar si ya está aplicada (por numero_expediente)
            existing = self.deduction_line_ids.filtered(
                lambda l: l.deduction_category == 'pension_alimentaria'
                and l.numero_resolucion == pension.numero_expediente
            )
            if existing:
                continue

            monto = pension.compute_amount(self.gross_salary or 0.0)

            self.env['planilla.payslip.deduction.line'].create({
                'payslip_id':         self.id,
                'deduction_code_id':  pension_code.id,
                'description':        f'Pensión Alimentaria — {pension.beneficiario_nombre} ({pension.numero_expediente})',
                'line_type':          'deduction',
                'deduction_category': 'pension_alimentaria',
                'amount_type':        pension.calculation_type,
                'amount':             monto,
                'percentage':         pension.percentage if pension.calculation_type == 'percentage' else 0.0,
                'numero_resolucion':  pension.numero_expediente,
            })

    def _sync_novedades(self) -> None:
        """
        Vincula automáticamente a la boleta las horas extras, incapacidades
        y vacaciones del empleado que corresponden al período de la boleta
        y que aún no tienen boleta asignada.
        Reglas:
          - Horas extras:    state == 'approved',  fecha dentro del período
          - Incapacidades:   state in ('confirmed','paid'), solapa con el período
          - Vacaciones:      state in ('approved','paid'), solapa con el período
        """
        self.ensure_one()
        if not self.employee_id or not self.date_from or not self.date_to:
            return

        emp_id    = self.employee_id.id
        date_from = self.date_from
        date_to   = self.date_to

        # ── Horas Extras ────────────────────────────────────────────────────
        overtimes = self.env['planilla.overtime'].search([
            ('employee_id', '=', emp_id),
            ('state', '=', 'approved'),
            ('date', '>=', date_from),
            ('date', '<=', date_to),
            '|', ('payslip_id', '=', False), ('payslip_id', '=', self.id),
        ])
        overtimes.write({'payslip_id': self.id})

        # ── Incapacidades ────────────────────────────────────────────────────
        # Solapan si date_start <= date_to AND date_end >= date_from
        disabilities = self.env['planilla.disability'].search([
            ('employee_id', '=', emp_id),
            ('state', 'in', ('confirmed', 'paid')),
            ('date_start', '<=', date_to),
            ('date_end',   '>=', date_from),
            '|', ('payslip_id', '=', False), ('payslip_id', '=', self.id),
        ])
        disabilities.write({'payslip_id': self.id})

        # ── Vacaciones ────────────────────────────────────────────────────────
        vacations = self.env['planilla.vacation.payment'].search([
            ('employee_id', '=', emp_id),
            ('state', 'in', ('approved', 'paid')),
            ('date_start', '<=', date_to),
            ('date_end',   '>=', date_from),
            '|', ('payslip_id', '=', False), ('payslip_id', '=', self.id),
        ])
        vacations.write({'payslip_id': self.id})

        # ── Pensiones Alimentarias ─────────────────────────────────────────
        self._sync_pension_alimentaria()

        # ── Licencias Especiales CR (duelo, paternidad, matrimonio, etc.) ─
        self._sync_licencias()

        # ── Ausencias aprobadas (hr_holidays) ─────────────────────────────
        self._sync_ausencias()

    def _sync_licencias(self) -> None:
        """
        Sincroniza licencias especiales CR (planilla.leave.cr) con la boleta.

        Licencias CON goce (duelo 1er grado, paternidad, matrimonio, adopción, etc.):
          → Se registran como INGRESO adicional (line_type='income') en la boleta.
          → No reducen el salario base; son gasto patronal adicional.
          → La boleta refleja el monto pagado y el asiento contable lo debita en 630800.

        Licencias SIN goce (permiso sin goce, duelo 2do grado sin override, etc.):
          → Se registran como DEDUCCIÓN (deduction_category='licencia_sin_goce').
          → Reducen el neto a pagar al empleado.
          → Siguen la misma lógica que ausencias: salario_diario × días ausentes.

        Se vincula la licencia a la boleta (payslip_id) para trazabilidad.
        Evita duplicados verificando si ya existe una línea con leave_cr_id.
        """
        self.ensure_one()
        if self.state != 'draft':
            return
        if not self.employee_id or not self.date_from or not self.date_to:
            return

        # ── Códigos de deducción ──────────────────────────────────────────────
        def _get_or_create_code(code, name, ded_type):
            dc = self.env['planilla.deduction.code'].search([('code', '=', code)], limit=1)
            if not dc:
                try:
                    dc = self.env['planilla.deduction.code'].sudo().create({
                        'code': code, 'name': name, 'deduction_type': ded_type,
                    })
                except Exception:
                    dc = self.env['planilla.deduction.code'].search([('code', '=', code)], limit=1)
            return dc

        code_con_goce  = _get_or_create_code('LIC-GOCE',  'Licencia con Goce de Sueldo', 'employer')
        code_sin_goce  = _get_or_create_code('LIC-SGOCE', 'Licencia Sin Goce de Sueldo', 'employee')

        # ── Buscar licencias aprobadas del período ────────────────────────────
        # FIX-AUD-10: filtrar por company_id para seguridad multi-empresa.
        licencias = self.env['planilla.leave.cr'].search([
            ('employee_id', '=', self.employee_id.id),
            ('company_id',  '=', self.company_id.id),
            ('state', '=', 'approved'),
            ('date_start', '<=', self.date_to),
            ('date_end',   '>=', self.date_from),
            '|', ('payslip_id', '=', False), ('payslip_id', '=', self.id),
        ])

        for lic in licencias:
            # Verificar si ya existe línea para esta licencia
            existing = self.deduction_line_ids.filtered(
                lambda l, lid=lic.id: l.leave_cr_id.id == lid
            )
            if existing:
                continue

            pays = lic.has_salary or lic.has_salary_override
            monto = lic.leave_amount or 0.0
            if monto <= 0:
                continue

            tipo_label = dict(lic._fields['leave_type'].selection).get(lic.leave_type, lic.leave_type)
            dias_efectivos = lic.working_days if lic.working_days > 0 else lic.days

            if pays:
                # Licencia CON goce → ingreso adicional (gasto patronal)
                self.env['planilla.payslip.deduction.line'].create({
                    'payslip_id':          self.id,
                    'deduction_code_id':   code_con_goce.id,
                    'description':         f'Licencia: {tipo_label} ({lic.date_start} al {lic.date_end}, {dias_efectivos} día(s))',
                    'line_type':           'income',
                    'deduction_category':  'licencia_con_goce',
                    'amount':              monto,
                    'leave_cr_id':         lic.id,
                })
            else:
                # Licencia SIN goce → deducción al empleado
                self.env['planilla.payslip.deduction.line'].create({
                    'payslip_id':          self.id,
                    'deduction_code_id':   code_sin_goce.id,
                    'description':         f'Licencia sin goce: {tipo_label} ({lic.date_start} al {lic.date_end}, {dias_efectivos} día(s))',
                    'line_type':           'deduction',
                    'deduction_category':  'licencia_sin_goce',
                    'amount':              monto,
                    'leave_cr_id':         lic.id,
                })

            # Vincular licencia a la boleta para trazabilidad
            # FIX-AUD-07: NO marcar 'paid' aquí — la licencia pasa a 'paid' solo cuando
            # la boleta se paga (action_pay), igual que vacation_ids/overtime_ids/disability_ids.
            # Marcar 'paid' en sync (boleta en draft) causaba que la licencia quedara
            # bloqueada si la boleta se cancelaba antes de pagarse.
            lic.write({'payslip_id': self.id})

    def _sync_ausencias(self) -> None:
        """
        H2 FIX — Integración hr_holidays con planilla.
        Busca ausencias aprobadas (hr.leave en estado validate) del empleado
        en el período de la boleta y crea deducciones automáticas por los
        días sin goce de sueldo.

        Lógica:
          - Solo aplica a ausencias SIN pago (unpaid leave) o cuyo tipo
            tenga work_time_rate = 0 (ausencia injustificada / sin goce).
          - Las ausencias CON pago (vacaciones anuales, maternidad, etc.)
            NO se descuentan aquí: ya están gestionadas por sus propios modelos.
          - El monto diario = salario bruto / días del período.
          - Se crea UNA línea de deducción por leave_id para evitar duplicados.
        """
        self.ensure_one()
        if self.state != 'draft':
            return
        if not self.employee_id or not self.date_from or not self.date_to:
            return

        # Código de deducción para ausencias
        absence_code = self.env['planilla.deduction.code'].search(
            [('code', '=', 'AUSENCIA')], limit=1
        )
        if not absence_code:
            # FIX v512 SEC-01: patrón anti race-condition
            try:
                absence_code = self.env['planilla.deduction.code'].sudo().create({
                    'name': 'Ausencia Sin Goce de Sueldo',
                    'code': 'AUSENCIA',
                    'deduction_type': 'employee',
                })
            except Exception:
                absence_code = self.env['planilla.deduction.code'].search(
                    [('code', '=', 'AUSENCIA')], limit=1
                )

        # Buscar ausencias aprobadas del empleado que solapan con el período
        leaves = self.env['hr.leave'].search([
            ('employee_id', '=', self.employee_id.id),
            ('state', '=', 'validate'),
            ('date_from', '<=', fields.Datetime.to_datetime(self.date_to)),
            ('date_to',   '>=', fields.Datetime.to_datetime(self.date_from)),
        ])

        for leave in leaves:
            # FIX v512 BP-03: eliminado hasattr() anti-patrón.
            # Este módulo es Odoo 19 exclusivo. En Odoo 19 hr.holiday.status
            # expone 'unpaid' (boolean) de forma estable desde la versión 17+.
            # Se usa directamente sin detección dinámica de versión.
            holiday_type = leave.holiday_status_id
            is_unpaid = bool(getattr(holiday_type, 'unpaid', False))

            # Fallback semántico si unpaid no existe (instalación no estándar)
            if not is_unpaid and hasattr(holiday_type, 'work_time_rate'):
                is_unpaid = (holiday_type.work_time_rate == 0)
            elif not is_unpaid:
                name_lower = (holiday_type.name or '').lower()
                is_unpaid = any(k in name_lower for k in (
                    'sin goce', 'injustificad', 'unpaid', 'sin remuner', 'no remuner'
                ))

            # Si la ausencia ES pagada (maternidad, vacaciones anuales, etc.) → omitir.
            # Esas ausencias ya están gestionadas por sus propios modelos (disability, vacation).
            if not is_unpaid:
                continue

            # FIX v56: Validacion cruzada hr_holidays vs planilla.vacation.payment
            # Si ya existe un registro de planilla.vacation.payment para el mismo
            # empleado y período que solapa con esta ausencia, NO crear deducción
            # para evitar doble descuento en el saldo de vacaciones.
            if is_unpaid:
                vac_overlap = self.env['planilla.vacation.payment'].search_count([
                    ('employee_id', '=', self.employee_id.id),
                    ('state', 'in', ('approved', 'paid')),
                    ('date_start', '<=', (leave.date_to.date() if leave.date_to else self.date_to)),
                    ('date_end',   '>=', (leave.date_from.date() if leave.date_from else self.date_from)),
                ])
                if vac_overlap:
                    _logger.info(
                        'planilla_cr._sync_ausencias: ausencia %s omitida — ya existe '
                        'planilla.vacation.payment solapante para %s',
                        leave.id, self.employee_id.name
                    )
                    continue

            # Evitar duplicados: verificar si ya existe línea para este leave
            existing = self.deduction_line_ids.filtered(
                lambda l: l.hr_leave_id == leave
            )
            if existing:
                continue

            # FIX C-05 v53: Usar number_of_days de hr.leave cuando está disponible,
            # ya que Odoo lo calcula correctamente incluyendo medias jornadas (0.5).
            # El cálculo manual por fechas siempre redondea hacia arriba y no maneja
            # ausencias de medio día (request_date_from_period = 'am'/'pm').
            leave_start = leave.date_from.date() if leave.date_from else self.date_from
            leave_end   = leave.date_to.date()   if leave.date_to   else self.date_to
            effective_start = max(leave_start, self.date_from)
            effective_end   = min(leave_end,   self.date_to)

            if effective_end < effective_start:
                continue

            # Si la ausencia está completamente dentro del período, usar number_of_days
            if leave_start >= self.date_from and leave_end <= self.date_to:
                days_absent = getattr(leave, 'number_of_days', None)
                if not days_absent or days_absent <= 0:
                    days_absent = (effective_end - effective_start).days + 1
            else:
                # Ausencia parcialmente fuera del período → calcular intersección en días
                days_absent = (effective_end - effective_start).days + 1

            if days_absent <= 0:
                continue

            # Monto: salario_diario × días ausentes
            salary_daily = round(
                (self.base_salary or 0.0) / max(self.days_in_period or 30, 1), 2
            )
            amount = round(salary_daily * days_absent, 2)
            if amount <= 0:
                continue

            self.env['planilla.payslip.deduction.line'].create({
                'payslip_id':          self.id,
                'deduction_code_id':   absence_code.id,
                'description':         (
                    f'Ausencia sin goce — {leave.holiday_status_id.name} '
                    f'({effective_start} al {effective_end}, {days_absent} día(s))'
                ),
                'amount':              amount,
                'deduction_category':  'ausencia',
                'hr_leave_id':         leave.id,
            })


    def _sync_rop(self) -> None:
        """
        Sincroniza la deducción de ROP (Régimen Obligatorio de Pensiones, Ley 7983)
        en la boleta del empleado.

        - ROP Obrero:   K.ROP_EMP (1.0%) del salario bruto — deducción al empleado
        - ROP Patronal: K.ROP_PAT (3.25%) del salario bruto — costo adicional del patrono

        OPT-IN: Solo aplica si el empleado tiene rop_applies=True.
        El campo está DESACTIVADO por defecto porque muchos contadores en CR
        manejan el ROP con su propio proceso externo (planilla complementaria,
        plataforma del operador, etc.). Activarlo por empleado según confirme
        el contador.
        Si no hay código ROP configurado en BD, usa K.ROP_EMP/K.ROP_PAT.

        Evita duplicados: si ya existe una línea de deducción con deduction_category='rop',
        actualiza el monto en lugar de crear una nueva.
        """
        self.ensure_one()
        if self.state != 'draft':
            return

        emp = self.employee_id
        if not emp or not getattr(emp, 'rop_applies', False):
            return

        g = self.gross_salary or 0.0
        if g <= 0:
            return

        # Tasas desde BD (configurable) con fallback a constantes
        rh = self.env['planilla.rate.helper'].with_company(self.company_id)
        rop_emp_dc = rh._get_deduction_code('ROP_EMP')
        rop_pat_dc = rh._get_deduction_code('ROP_PAT')
        rop_emp_rate = (rop_emp_dc.employee_percentage / 100) if rop_emp_dc else K.ROP_EMP
        rop_pat_rate = (rop_pat_dc.employer_percentage / 100) if rop_pat_dc else K.ROP_PAT

        monto_emp = round(g * rop_emp_rate, 2)
        monto_pat = round(g * rop_pat_rate, 2)

        # Código de deducción ROP
        rop_code = self.env['planilla.deduction.code'].search(
            [('code', '=', 'ROP')], limit=1
        )
        if not rop_code:
            # FIX v512 SEC-01: patrón anti race-condition
            try:
                rop_code = self.env['planilla.deduction.code'].sudo().create({
                    'code': 'ROP',
                    'name': 'ROP — Régimen Obligatorio de Pensiones (Ley 7983)',
                    'deduction_type': 'employee',
                    'calculation_type': 'percentage',
                    'description': 'ROP obrero 1% + patronal 3.25% (Ley 7983 Art. 6)',
                })
            except Exception:
                rop_code = self.env['planilla.deduction.code'].search(
                    [('code', '=', 'ROP')], limit=1
                )

        # Deducción obrera: actualiza si existe, crea si no
        existing_emp = self.deduction_line_ids.filtered(
            lambda l: l.deduction_category == 'rop' and l.line_type == 'deduction'
        )
        if existing_emp:
            if existing_emp[0].amount != monto_emp:
                existing_emp[0].amount = monto_emp
        else:
            self.env['planilla.payslip.deduction.line'].create({
                'payslip_id':         self.id,
                'deduction_code_id':  rop_code.id,
                'description':        f'ROP Obrero {rop_emp_rate*100:.1f}% — Ley 7983',
                'line_type':          'deduction',
                'deduction_category': 'rop',
                'amount_type':        'percentage',
                'percentage':         rop_emp_rate * 100,
                'amount':             monto_emp,
            })

        # Registrar costo patronal ROP en el campo rop_employer
        # para que aparezca en total_employer_cost y en el asiento contable
        self.rop_employer = monto_pat

        _logger.info(
            'planilla_cr._sync_rop: ROP obrero ₡%.2f + patronal ₡%.2f para %s (boleta %s)',
            monto_emp, monto_pat, emp.name, self.name
        )

    def _sync_embargos(self) -> None:
        """
        Sincroniza embargos judiciales activos del empleado con las líneas de deducción.
        Art. 172 CT: máximo 25 % del neto disponible (después de CCSS, renta y pensiones).
        Prioridad: pensión alimentaria → embargo → préstamos.
        """
        self.ensure_one()
        if self.state != 'draft':
            return

        embargo_code = self.env['planilla.deduction.code'].search(
            [('code', '=', 'EMB')], limit=1
        )
        if not embargo_code:
            # FIX v512 SEC-01: patrón anti race-condition
            try:
                embargo_code = self.env['planilla.deduction.code'].sudo().create({
                    'code': 'EMB',
                    'name': 'Embargo Judicial',
                    'deduction_type': 'employee',
                    'calculation_type': 'fixed',
                    'description': 'Embargo judicial — máximo 25% salario neto Art. 172 CT',
                })
            except Exception:
                embargo_code = self.env['planilla.deduction.code'].search(
                    [('code', '=', 'EMB')], limit=1
                )

        embargos = self.env['planilla.embargo'].search([
            ('employee_id', '=', self.employee_id.id),
            ('state', '=', 'active'),
            ('date_start', '<=', self.date_to),
            '|',
            ('date_end', '=', False),
            ('date_end', '>=', self.date_from),
        ])

        # FIX B-08 v58: Incluir ausencias_sin_goce en el neto disponible para embargo.
        # Art. 172 CT establece el tope sobre el salario neto real del empleado.
        # Si hay descuentos por ausencias, estos reducen la base antes del 25%.
        gross     = self.gross_salary or 0.0
        ccss_emp  = self.ccss_employee or 0.0
        renta     = self.income_tax or 0.0
        pensiones = sum(
            l.amount for l in self.deduction_line_ids
            if l.deduction_category == 'pension_alimentaria'
        )
        ausencias_sg = sum(
            l.amount for l in self.deduction_line_ids
            if l.deduction_category == 'ausencia'
        )
        licencias_sg = sum(
            l.amount for l in self.deduction_line_ids
            if l.deduction_category == 'licencia_sin_goce'
        )
        # Art. 172 CT: el neto disponible se calcula descontando CCSS, renta,
        # pensiones alimentarias, ausencias sin goce Y licencias sin goce,
        # antes de aplicar el tope del 25% de embargo.
        neto_disponible = max(0.0, gross - ccss_emp - renta - pensiones - ausencias_sg - licencias_sg)
        limite_total    = round(neto_disponible * K.MAX_PCT_EMBARGO / 100, 2)
        ya_embargado    = sum(
            l.amount for l in self.deduction_line_ids
            if l.deduction_category == 'embargo'
        )

        for embargo in embargos:
            existing = self.deduction_line_ids.filtered(
                lambda l, e=embargo: l.deduction_category == 'embargo'
                and l.numero_resolucion == e.numero_expediente
            )
            if existing:
                continue

            monto = embargo.compute_amount(neto_disponible)
            # Respetar el límite global del 25 %
            espacio = max(0.0, limite_total - ya_embargado)
            monto   = min(monto, espacio)
            if monto <= 0:
                continue

            self.env['planilla.payslip.deduction.line'].create({
                'payslip_id':         self.id,
                'deduction_code_id':  embargo_code.id,
                'description':        (f'Embargo Judicial — {embargo.beneficiario_nombre} '
                                       f'({embargo.numero_expediente})'),
                'line_type':          'deduction',
                'deduction_category': 'embargo',
                'amount_type':        embargo.calculation_type,
                'amount':             monto,
                'percentage':         embargo.percentage if embargo.calculation_type == 'percentage' else 0.0,
                'numero_resolucion':  embargo.numero_expediente,
            })
            ya_embargado += monto
            _logger.info(
                'planilla_cr._sync_embargos: aplicado embargo %s (₡%.2f) a boleta %s',
                embargo.numero_expediente, monto, self.name
            )

    def _sync_bonos(self) -> None:
        """
        Sincroniza bonos activos del empleado con las líneas de ingreso de la boleta.
        Respeta las reglas fiscales CR: flags afecto_ccss / afecto_renta por tipo.
        """
        self.ensure_one()
        if self.state != 'draft':
            return

        bono_code = self.env['planilla.deduction.code'].search(
            [('code', '=', 'BONO')], limit=1
        )
        if not bono_code:
            bono_code = self.env['planilla.deduction.code'].create({
                'code': 'BONO',
                'name': 'Bono / Incentivo',
                'deduction_type': 'employee',
                'calculation_type': 'fixed',
                'description': 'Bonos e incentivos por empleado',
            })

        bonos = self.env['planilla.bono'].search([  # FIX v512 BP-01: eliminada var 'today' sin usar
            ('employee_id', '=', self.employee_id.id),
            ('state', '=', 'active'),
            ('date_start', '<=', self.date_to),
            '|',
            ('date_end', '=', False),
            ('date_end', '>=', self.date_from),
        ])

        for bono in bonos:
            # Evitar duplicados (por concepto)
            existing = self.deduction_line_ids.filtered(
                lambda l, b=bono: l.line_type == 'income'
                and l.description == f'Bono: {b.name}'
            )
            if existing:
                # FIX I-02 v54: Para bonos porcentuales usar employee.base_salary
                # (salario mensual configurado en el empleado) en vez de self.gross_salary.
                # Razón: gross_salary ahora incluye bono_salarial_amount, que a su vez
                # depende de las deduction_line_ids — generaría una dependencia circular
                # y los porcentajes se calcularían sobre una base que ya los incluye.
                # En práctica CR, los bonos % siempre se calculan sobre el salario base,
                # no sobre el bruto total que incluye otros pluses.
                if bono.amount_type == 'percentage':
                    base_ref = self.employee_id.base_salary or 0.0
                    monto = round(base_ref * bono.percentage / 100.0, 2)
                    for line in existing:
                        if line.amount != monto:
                            line.amount = monto
                continue

            # Para el cálculo inicial también usamos base_salary del empleado
            if bono.amount_type == 'fixed':
                monto = bono.amount
            else:
                base_ref = self.employee_id.base_salary or 0.0
                monto = round(base_ref * bono.percentage / 100.0, 2)
            if monto <= 0:
                continue

            _logger.info(
                'planilla_cr._sync_bonos: aplicando bono "%s" (₡%.2f) a boleta %s',
                bono.name, monto, self.name
            )
            self.env['planilla.payslip.deduction.line'].create({
                'payslip_id':         self.id,
                'deduction_code_id':  bono_code.id,
                'description':        f'Bono: {bono.name}',
                'line_type':          'income',
                'deduction_category': 'bonus',
                'amount_type':        bono.amount_type,
                'amount':             monto,
                'percentage':         bono.percentage if bono.amount_type == 'percentage' else 0.0,
            })

    # ══════════════════════════════════════════════════════════════════════
    # MÉTODOS DE SYNC POR LOTE (BATCH)
    # FIX PERF-05: Para planillas grupales, pre-cargar TODOS los datos de
    # TODOS los empleados en UNA query y distribuir. Elimina el patrón N+1
    # donde cada boleta hace sus propias búsquedas independientes.
    #
    # Reducción para 200 empleados:
    #   sync individual: ~200 × 8 = 1.600 queries
    #   sync batch:      ~8 queries (una por tipo de novedad)
    # ══════════════════════════════════════════════════════════════════════

    def _sync_novedades_batch(self) -> None:
        """Versión batch de _sync_novedades — carga todas las novedades en queries mínimas."""
        if not self:
            return
        # Todos los recordsets en self comparten el mismo date_from/date_to (planilla grupal)
        date_from = self[0].date_from
        date_to   = self[0].date_to
        emp_ids   = self.mapped('employee_id.id')
        # Índice: employee_id → boleta
        slip_by_emp = {s.employee_id.id: s for s in self}

        # ── Horas extras — UNA query para todos ──────────────────────────
        overtimes = self.env['planilla.overtime'].search([
            ('employee_id', 'in', emp_ids),
            ('state', '=', 'approved'),
            ('date', '>=', date_from),
            ('date', '<=', date_to),
            '|', ('payslip_id', '=', False), ('payslip_id', 'in', self.ids),
        ])
        by_emp = {}
        for o in overtimes:
            by_emp.setdefault(o.employee_id.id, []).append(o)
        for emp_id, recs in by_emp.items():
            slip = slip_by_emp.get(emp_id)
            if slip:
                self.env['planilla.overtime'].browse([r.id for r in recs]).write(
                    {'payslip_id': slip.id}
                )

        # ── Incapacidades — UNA query para todos ─────────────────────────
        disabilities = self.env['planilla.disability'].search([
            ('employee_id', 'in', emp_ids),
            ('state', 'in', ('confirmed', 'paid')),
            ('date_start', '<=', date_to),
            ('date_end',   '>=', date_from),
            '|', ('payslip_id', '=', False), ('payslip_id', 'in', self.ids),
        ])
        by_emp = {}
        for d in disabilities:
            by_emp.setdefault(d.employee_id.id, []).append(d)
        for emp_id, recs in by_emp.items():
            slip = slip_by_emp.get(emp_id)
            if slip:
                self.env['planilla.disability'].browse([r.id for r in recs]).write(
                    {'payslip_id': slip.id}
                )

        # ── Vacaciones — UNA query para todos ────────────────────────────
        vacations = self.env['planilla.vacation.payment'].search([
            ('employee_id', 'in', emp_ids),
            ('state', 'in', ('approved', 'paid')),
            ('date_start', '<=', date_to),
            ('date_end',   '>=', date_from),
            '|', ('payslip_id', '=', False), ('payslip_id', 'in', self.ids),
        ])
        by_emp = {}
        for v in vacations:
            by_emp.setdefault(v.employee_id.id, []).append(v)
        for emp_id, recs in by_emp.items():
            slip = slip_by_emp.get(emp_id)
            if slip:
                self.env['planilla.vacation.payment'].browse([r.id for r in recs]).write(
                    {'payslip_id': slip.id}
                )

        # ── Pensiones alimentarias — sync individual (complejo, bajo volumen) ──
        pension_code = self.env['planilla.deduction.code'].search(
            [('code', '=', 'PENSION_ALIM')], limit=1
        )
        if not pension_code:
            try:
                pension_code = self.env['planilla.deduction.code'].sudo().create({
                    'name': 'Pensión Alimentaria', 'code': 'PENSION_ALIM',
                    'deduction_type': 'employee',
                })
            except Exception:
                pension_code = self.env['planilla.deduction.code'].search(
                    [('code', '=', 'PENSION_ALIM')], limit=1
                )

        # Pensiones: UNA query para todos
        pensiones = self.env['planilla.pension.alimentaria'].search([
            ('employee_id', 'in', emp_ids),
            ('state', '=', 'active'),
            ('active', '=', True),
            ('date_start', '<=', date_to),
            '|', ('date_end', '=', False), ('date_end', '>=', date_from),
        ])
        by_emp = {}
        for pen in pensiones:
            by_emp.setdefault(pen.employee_id.id, []).append(pen)

        for emp_id, pens in by_emp.items():
            slip = slip_by_emp.get(emp_id)
            if not slip or not pension_code:
                continue
            for pension in pens:
                existing = slip.deduction_line_ids.filtered(
                    lambda l: l.deduction_category == 'pension_alimentaria'
                    and l.numero_resolucion == pension.numero_expediente
                )
                if existing:
                    continue
                monto = pension.compute_amount(slip.gross_salary or 0.0)
                self.env['planilla.payslip.deduction.line'].create({
                    'payslip_id':         slip.id,
                    'deduction_code_id':  pension_code.id,
                    'description':        f'Pensión Alimentaria — {pension.beneficiario_nombre} ({pension.numero_expediente})',
                    'line_type':          'deduction',
                    'deduction_category': 'pension_alimentaria',
                    'amount_type':        pension.calculation_type,
                    'amount':             monto,
                    'percentage':         pension.percentage if pension.calculation_type == 'percentage' else 0.0,
                    'numero_resolucion':  pension.numero_expediente,
                })

        # ── Ausencias sin goce — UNA query para todos ─────────────────
        absence_code = self.env['planilla.deduction.code'].search(
            [('code', '=', 'AUSENCIA')], limit=1
        )
        if not absence_code:
            try:
                absence_code = self.env['planilla.deduction.code'].sudo().create({
                    'name': 'Ausencia Sin Goce de Sueldo', 'code': 'AUSENCIA',
                    'deduction_type': 'employee',
                })
            except Exception:
                absence_code = self.env['planilla.deduction.code'].search(
                    [('code', '=', 'AUSENCIA')], limit=1
                )
        if absence_code:
            from odoo import fields as _fields
            leaves = self.env['hr.leave'].search([
                ('employee_id', 'in', emp_ids),
                ('state', '=', 'validate'),
                ('date_from', '<=', _fields.Datetime.to_datetime(date_to)),
                ('date_to',   '>=', _fields.Datetime.to_datetime(date_from)),
            ])
            by_emp_leaves = {}
            for lv in leaves:
                by_emp_leaves.setdefault(lv.employee_id.id, []).append(lv)
            for emp_id, lv_list in by_emp_leaves.items():
                slip = slip_by_emp.get(emp_id)
                if slip:
                    for leave in lv_list:
                        slip._sync_ausencias_single(leave, absence_code)

        # ── Licencias Especiales CR — UNA query para todos ──────────────────
        self._sync_licencias_batch()

    def _sync_ausencias_single(self, leave, absence_code):
        """Sincroniza una ausencia individual en esta boleta (usado en batch mode)."""
        holiday_type = leave.holiday_status_id
        is_unpaid = bool(getattr(holiday_type, 'unpaid', False))
        if not is_unpaid and hasattr(holiday_type, 'work_time_rate'):
            is_unpaid = (holiday_type.work_time_rate == 0)
        elif not is_unpaid:
            name_lower = (holiday_type.name or '').lower()
            is_unpaid = any(k in name_lower for k in (
                'sin goce', 'injustificad', 'unpaid', 'sin remuner', 'no remuner'))
        if not is_unpaid:
            return
        existing = self.deduction_line_ids.filtered(lambda l: l.hr_leave_id == leave)
        if existing:
            return
        leave_start = leave.date_from.date() if leave.date_from else self.date_from
        leave_end   = leave.date_to.date()   if leave.date_to   else self.date_to
        effective_start = max(leave_start, self.date_from)
        effective_end   = min(leave_end,   self.date_to)
        if effective_end < effective_start:
            return
        if leave_start >= self.date_from and leave_end <= self.date_to:
            days_absent = getattr(leave, 'number_of_days', None) or (effective_end - effective_start).days + 1
        else:
            days_absent = (effective_end - effective_start).days + 1
        if days_absent <= 0:
            return
        salary_daily = round((self.base_salary or 0.0) / max(self.days_in_period or 30, 1), 2)
        amount = round(salary_daily * days_absent, 2)
        if amount <= 0:
            return
        self.env['planilla.payslip.deduction.line'].create({
            'payslip_id':         self.id,
            'deduction_code_id':  absence_code.id,
            'description':        f'Ausencia sin goce — {leave.holiday_status_id.name} ({effective_start} al {effective_end}, {days_absent} día(s))',
            'amount':             amount,
            'deduction_category': 'ausencia',
            'hr_leave_id':        leave.id,
        })

    def _sync_licencias_batch(self) -> None:
        """
        PERF: versión batch de _sync_licencias.
        Carga TODAS las licencias del período en 1 query y las distribuye
        a cada boleta, en lugar de 1 query por empleado.
        Para 200 empleados: 200 queries → 1 query.
        """
        if not self:
            return
        # Guardia de seguridad: si los períodos difieren, volver al modo individual
        dates = {(s.date_from, s.date_to) for s in self}
        if len(dates) > 1:
            for slip in self:
                slip._sync_licencias()
            return

        date_from, date_to = next(iter(dates))
        emp_ids = self.mapped('employee_id.id')
        slip_by_emp = {s.employee_id.id: s for s in self}
        company_id = self[0].company_id.id  # FIX-AUD-10: filtro empresa batch

        # ── Códigos de deducción (1 query cada uno) ──────────────────────────
        def _get_or_create_code(code, name, ded_type):
            dc = self.env['planilla.deduction.code'].search([('code', '=', code)], limit=1)
            if not dc:
                try:
                    dc = self.env['planilla.deduction.code'].sudo().create({
                        'code': code, 'name': name, 'deduction_type': ded_type,
                    })
                except Exception:
                    dc = self.env['planilla.deduction.code'].search([('code', '=', code)], limit=1)
            return dc

        code_con_goce = _get_or_create_code('LIC-GOCE',  'Licencia con Goce de Sueldo', 'employer')
        code_sin_goce = _get_or_create_code('LIC-SGOCE', 'Licencia Sin Goce de Sueldo', 'employee')

        # ── 1 QUERY: todas las licencias aprobadas del período ───────────────
        # FIX-AUD-10: filtro company_id para seguridad multi-empresa en modo batch
        licencias = self.env['planilla.leave.cr'].search([
            ('employee_id', 'in', emp_ids),
            ('company_id',  '=', company_id),
            ('state', '=', 'approved'),
            ('date_start', '<=', date_to),
            ('date_end',   '>=', date_from),
        ])

        by_emp = {}
        for lic in licencias:
            by_emp.setdefault(lic.employee_id.id, []).append(lic)

        processed_ids = []
        for emp_id, lics in by_emp.items():
            slip = slip_by_emp.get(emp_id)
            if not slip:
                continue
            for lic in lics:
                slip._sync_licencias_single(lic, code_con_goce, code_sin_goce)
                processed_ids.append(lic.id)

        # FIX-AUD-07: NO marcar 'paid' en sincronización — solo vincular payslip_id.
        # El estado 'paid' se asigna en action_pay igual que vacation_ids/overtime_ids.

    def _sync_licencias_single(self, lic, code_con_goce, code_sin_goce) -> None:
        """Sincroniza una licencia especial individual en esta boleta (batch mode)."""
        existing = self.deduction_line_ids.filtered(
            lambda l, lid=lic.id: l.leave_cr_id.id == lid
        )
        if existing:
            return
        pays = lic.has_salary or lic.has_salary_override
        monto = lic.leave_amount or 0.0
        if monto <= 0:
            return
        tipo_label = dict(lic._fields['leave_type'].selection).get(lic.leave_type, lic.leave_type)
        dias_efectivos = lic.working_days if lic.working_days > 0 else lic.days
        vals = {
            'payslip_id':        self.id,
            'description':       f'{"Licencia" if pays else "Licencia sin goce"}: {tipo_label} ({lic.date_start} al {lic.date_end}, {dias_efectivos} día(s))',
            'amount':            monto,
            'leave_cr_id':       lic.id,
        }
        if pays:
            vals.update({
                'deduction_code_id':  code_con_goce.id,
                'line_type':          'income',
                'deduction_category': 'licencia_con_goce',
            })
        else:
            vals.update({
                'deduction_code_id':  code_sin_goce.id,
                'line_type':          'deduction',
                'deduction_category': 'licencia_sin_goce',
            })
        self.env['planilla.payslip.deduction.line'].create(vals)
        lic.write({'payslip_id': self.id})

    def _sync_recurring_benefits_batch(self) -> None:
        """Batch: carga beneficios recurrentes de TODOS los empleados en una query."""
        if not self:
            return
        date_from = self[0].date_from
        emp_ids = self.mapped('employee_id.id')
        slip_by_emp = {s.employee_id.id: s for s in self}

        benefits = self.env['planilla.recurring.benefit'].search([
            ('employee_id', 'in', emp_ids),
            ('active', '=', True),
            '|', ('date_start', '=', False), ('date_start', '<=', date_from),
            '|', ('date_end', '=', False),   ('date_end', '>=', date_from),
        ])
        by_emp = {}
        for b in benefits:
            by_emp.setdefault(b.employee_id.id, []).append(b)

        for emp_id, bens in by_emp.items():
            slip = slip_by_emp.get(emp_id)
            if not slip:
                continue
            # FIX-E1: pre-indexar las líneas existentes del slip para evitar duplicados.
            # El modo single verifica existentes; el batch no lo hacía → podía duplicar
            # beneficios si el botón "Sincronizar" se presionaba más de una vez.
            existing_ben_ids = set(
                slip.deduction_line_ids.filtered(
                    lambda l: l.recurring_benefit_id
                ).mapped('recurring_benefit_id.id')
            )
            for ben in bens:
                if ben.id in existing_ben_ids:
                    continue  # ya sincronizado → omitir
                amt = ben.get_amount_for_salary(slip.gross_salary or 0.0)
                self.env['planilla.payslip.deduction.line'].create({
                    'payslip_id':           slip.id,
                    'deduction_code_id':    ben.deduction_code_id.id,
                    'description':          ben.name,
                    'line_type':            'income' if ben.benefit_type == 'income' else 'deduction',
                    'amount_type':          ben.amount_type,
                    'amount':               amt,
                    'percentage':           ben.percentage,
                    'recurring_benefit_id': ben.id,
                })

    def _sync_rop_batch(self) -> None:
        """Batch: sincroniza ROP para todos los empleados con rop_applies=True."""
        if not self:
            return
        rop_slips = self.filtered(
            lambda s: s.state == 'draft' and getattr(s.employee_id, 'rop_applies', False)
                      and (s.gross_salary or 0) > 0
        )
        if not rop_slips:
            return
        rh = self.env['planilla.rate.helper'].with_company(self[0].company_id)
        rop_emp_dc = rh._get_deduction_code('ROP_EMP')
        rop_pat_dc = rh._get_deduction_code('ROP_PAT')
        from . import planilla_const as _K
        rop_emp_rate = (rop_emp_dc.employee_percentage / 100) if rop_emp_dc else _K.ROP_EMP
        rop_pat_rate = (rop_pat_dc.employer_percentage / 100) if rop_pat_dc else _K.ROP_PAT
        rop_code = self.env['planilla.deduction.code'].search([('code', '=', 'ROP')], limit=1)
        if not rop_code:
            try:
                rop_code = self.env['planilla.deduction.code'].sudo().create({
                    'code': 'ROP', 'name': 'ROP — Régimen Obligatorio de Pensiones (Ley 7983)',
                    'deduction_type': 'employee',
                })
            except Exception:
                rop_code = self.env['planilla.deduction.code'].search([('code', '=', 'ROP')], limit=1)
        lines_to_create = []
        for slip in rop_slips:
            g = slip.gross_salary
            monto_emp = round(g * rop_emp_rate, 2)
            monto_pat = round(g * rop_pat_rate, 2)
            lines_to_create.append({
                'payslip_id':         slip.id,
                'deduction_code_id':  rop_code.id,
                'description':        f'ROP Obrero {rop_emp_rate*100:.1f}% — Ley 7983',
                'line_type':          'deduction',
                'deduction_category': 'rop',
                'amount_type':        'percentage',
                'percentage':         rop_emp_rate * 100,
                'amount':             monto_emp,
            })
            slip.rop_employer = monto_pat
        if lines_to_create:
            self.env['planilla.payslip.deduction.line'].create(lines_to_create)

    def _sync_bonos_batch(self) -> None:
        """Batch: carga bonos activos de TODOS los empleados en una query."""
        if not self:
            return
        date_from = self[0].date_from
        date_to   = self[0].date_to
        emp_ids   = self.mapped('employee_id.id')
        slip_by_emp = {s.employee_id.id: s for s in self}

        bono_code = self.env['planilla.deduction.code'].search([('code', '=', 'BONO')], limit=1)
        if not bono_code:
            bono_code = self.env['planilla.deduction.code'].create({
                'code': 'BONO', 'name': 'Bono / Incentivo', 'deduction_type': 'employee',
                'calculation_type': 'fixed',
            })

        bonos = self.env['planilla.bono'].search([
            ('employee_id', 'in', emp_ids),
            ('state', '=', 'active'),
            ('date_start', '<=', date_to),
            '|', ('date_end', '=', False), ('date_end', '>=', date_from),
        ])
        by_emp = {}
        for b in bonos:
            by_emp.setdefault(b.employee_id.id, []).append(b)

        lines_to_create = []
        for emp_id, bono_list in by_emp.items():
            slip = slip_by_emp.get(emp_id)
            if not slip:
                continue
            # FIX-E2: verificar bonos ya sincronizados antes de agregar al batch.
            # El modo single verifica por description; aquí hacemos lo mismo.
            existing_desc = set(
                slip.deduction_line_ids.filtered(
                    lambda l: l.line_type == 'income' and l.deduction_category == 'bonus'
                ).mapped('description')
            )
            for bono in bono_list:
                desc = f'Bono: {bono.name}'
                if desc in existing_desc:
                    continue  # ya sincronizado → omitir
                if bono.amount_type == 'fixed':
                    monto = bono.amount
                else:
                    monto = round((slip.employee_id.base_salary or 0.0) * bono.percentage / 100.0, 2)
                if monto <= 0:
                    continue
                lines_to_create.append({
                    'payslip_id':         slip.id,
                    'deduction_code_id':  bono_code.id,
                    'description':        desc,
                    'line_type':          'income',
                    'deduction_category': 'bonus',
                    'amount_type':        bono.amount_type,
                    'amount':             monto,
                    'percentage':         bono.percentage if bono.amount_type == 'percentage' else 0.0,
                })
        if lines_to_create:
            self.env['planilla.payslip.deduction.line'].create(lines_to_create)

    def _sync_embargos_batch(self) -> None:
        """Batch: carga embargos activos de TODOS los empleados en una query."""
        if not self:
            return
        date_from = self[0].date_from
        date_to   = self[0].date_to
        emp_ids   = self.mapped('employee_id.id')
        slip_by_emp = {s.employee_id.id: s for s in self}

        embargo_code = self.env['planilla.deduction.code'].search([('code', '=', 'EMB')], limit=1)
        if not embargo_code:
            try:
                embargo_code = self.env['planilla.deduction.code'].sudo().create({
                    'code': 'EMB', 'name': 'Embargo Judicial',
                    'deduction_type': 'employee',
                })
            except Exception:
                embargo_code = self.env['planilla.deduction.code'].search([('code', '=', 'EMB')], limit=1)

        embargos = self.env['planilla.embargo'].search([
            ('employee_id', 'in', emp_ids),
            ('state', '=', 'active'),
            ('date_start', '<=', date_to),
            '|', ('date_end', '=', False), ('date_end', '>=', date_from),
        ])
        by_emp = {}
        for e in embargos:
            by_emp.setdefault(e.employee_id.id, []).append(e)

        lines_to_create = []
        for emp_id, emb_list in by_emp.items():
            slip = slip_by_emp.get(emp_id)
            if not slip or not embargo_code:
                continue
            gross     = slip.gross_salary or 0.0
            ccss_emp  = slip.ccss_employee or 0.0
            renta     = slip.income_tax or 0.0
            pensiones = sum(l.amount for l in slip.deduction_line_ids
                           if l.deduction_category == 'pension_alimentaria')
            ausencias = sum(l.amount for l in slip.deduction_line_ids
                           if l.deduction_category == 'ausencia')
            # FIX-M2: incluir licencias_sin_goce en la base para el tope Art. 172 CT.
            # El modo individual sí las incluía; el batch las omitía → tope inflado
            # en planillas grupales con empleados que tienen permisos sin goce.
            licencias_sg = sum(l.amount for l in slip.deduction_line_ids
                               if l.deduction_category == 'licencia_sin_goce')
            neto_disp = max(0.0, gross - ccss_emp - renta - pensiones - ausencias - licencias_sg)
            from . import planilla_const as _K
            limite_total = round(neto_disp * _K.MAX_PCT_EMBARGO / 100, 2)
            ya_embargado = 0.0
            for embargo in emb_list:
                existing = slip.deduction_line_ids.filtered(
                    lambda l, e=embargo: l.deduction_category == 'embargo'
                    and l.numero_resolucion == e.numero_expediente
                )
                if existing:
                    continue
                monto = embargo.compute_amount(neto_disp)
                espacio = max(0.0, limite_total - ya_embargado)
                monto = min(monto, espacio)
                if monto <= 0:
                    continue
                lines_to_create.append({
                    'payslip_id':         slip.id,
                    'deduction_code_id':  embargo_code.id,
                    'description':        f'Embargo Judicial — {embargo.beneficiario_nombre} ({embargo.numero_expediente})',
                    'line_type':          'deduction',
                    'deduction_category': 'embargo',
                    'amount_type':        embargo.calculation_type,
                    'amount':             monto,
                    'percentage':         embargo.percentage if embargo.calculation_type == 'percentage' else 0.0,
                    'numero_resolucion':  embargo.numero_expediente,
                })
                ya_embargado += monto
        if lines_to_create:
            self.env['planilla.payslip.deduction.line'].create(lines_to_create)

    def _sync_loan_deductions_batch(self) -> None:
        """Batch: carga cuotas de préstamos de TODOS los empleados en una query."""
        if not self:
            return
        date_from = self[0].date_from
        date_to   = self[0].date_to
        emp_ids   = self.mapped('employee_id.id')
        slip_by_emp = {s.employee_id.id: s for s in self}

        loan_code = self.env['planilla.deduction.code'].search([('code', '=', 'PRESTAMO')], limit=1)
        if not loan_code:
            return

        loans = self.env['planilla.employee.loan'].search([
            ('employee_id', 'in', emp_ids),
            ('state', 'in', ['approved', 'active']),
        ])
        # Cargar todas las cuotas pendientes en una query
        all_installments = self.env['planilla.loan.installment'].search([
            ('loan_id', 'in', loans.ids),
            ('state', '=', 'pending'),
        ])
        # Indexar cuotas por loan_id
        inst_by_loan = {}
        for inst in all_installments:
            inst_by_loan.setdefault(inst.loan_id.id, []).append(inst)

        lines_to_create = []
        for loan in loans:
            slip = slip_by_emp.get(loan.employee_id.id)
            if not slip:
                continue
            insts = inst_by_loan.get(loan.id, [])
            # Buscar cuota del período
            months_in_period = set()
            y, m = date_from.year, date_from.month
            ey, em = date_to.year, date_to.month
            while (y, m) <= (ey, em):
                months_in_period.add((y, m))
                m += 1
                if m > 12:
                    m = 1
                    y += 1
            matching = [i for i in insts
                        if i.due_date and (i.due_date.year, i.due_date.month) in months_in_period]
            if not matching:
                continue
            installment = matching[0]
            existing = slip.deduction_line_ids.filtered(
                lambda l: l.loan_installment_id == installment
            )
            if not existing:
                lines_to_create.append({
                    'payslip_id':          slip.id,
                    'deduction_code_id':   loan_code.id,
                    'description':         loan.name,
                    'line_type':           'deduction',       # FIX-E9: faltaba
                    'deduction_category':  'loan',            # FIX-E9: faltaba → salary_payable correcto
                    'amount':              installment.amount,
                    'loan_installment_id': installment.id,
                })
        if lines_to_create:
            self.env['planilla.payslip.deduction.line'].create(lines_to_create)
