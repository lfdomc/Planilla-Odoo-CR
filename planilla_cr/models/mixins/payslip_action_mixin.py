import logging
from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PayslipActionMixin(models.AbstractModel):
    """
    Mixin: acciones de usuario de la boleta de pago.
    create, action_sync_novedades, action_confirm, action_pay,
    action_cancel, action_reset_to_draft, action_send_payslip,
    action_view_accounting_entry, action_print_payslip.

    v58:
      - B-06: action_confirm usa self.write() batch para atomicidad completa.
      - action_sync_novedades movido aquí desde payslip_cr.py.
      - @api.model_create_multi en create().
    """
    _name = 'planilla.payslip.action.mixin'
    _description = 'Mixin Acciones Boleta'

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        # FIX PERF-05: Si se crean muchas boletas (planilla grupal), usar
        # sync por lote para reducir queries. Threshold: >1 boleta = modo batch.
        if len(records) == 1:
            # Creación individual (UI): sync normal por boleta
            rec = records
            rec._sync_novedades()
            rec._sync_recurring_benefits()
            rec._sync_rop()
            rec._sync_bonos()
            rec._sync_embargos()
            rec._sync_loan_deductions()
            rec._sync_employee_charges()
        else:
            # Creación masiva (planilla grupal): sync por lote
            # Guardia: verificar que todas las boletas tienen el mismo período.
            # Si hay fechas mixtas (caso atípico), hacer sync individual seguro.
            date_froms = set(r.date_from for r in records if r.date_from)
            date_tos   = set(r.date_to   for r in records if r.date_to)
            if len(date_froms) == 1 and len(date_tos) == 1:
                # Mismo período para todos → modo batch (óptimo)
                records._sync_novedades_batch()
                records._sync_recurring_benefits_batch()
                records._sync_rop_batch()
                records._sync_bonos_batch()
                records._sync_embargos_batch()
                records._sync_loan_deductions_batch()
                records._sync_employee_charges_batch()
            else:
                # Períodos distintos → sync individual seguro
                for rec in records:
                    rec._sync_novedades()
                    rec._sync_recurring_benefits()
                    rec._sync_rop()
                    rec._sync_bonos()
                    rec._sync_embargos()
                    rec._sync_loan_deductions()
                    rec._sync_employee_charges()
        return records

    def action_sync_novedades(self) -> bool:
        """Botón manual: re-sincroniza novedades del período en la boleta.
        FIX-N2: agrega _sync_loan_deductions que faltaba. Sin este método,
        al presionar el botón "Sincronizar" las cuotas de préstamos no se
        actualizaban en la boleta aunque el préstamo estuviera activo.
        """
        for rec in self:
            if rec.state == 'draft':
                rec._sync_novedades()        # incluye _sync_licencias() internamente
                rec._sync_recurring_benefits()
                rec._sync_rop()
                rec._sync_bonos()
                rec._sync_embargos()
                rec._sync_loan_deductions()  # FIX-N2: faltaba — préstamos no se sincronizaban
                rec._sync_employee_charges() # Cobros al empleado (almuerzos, productos, etc.)
        return True

    def action_recalculate(self) -> bool:
        """Fuerza el recálculo completo de la boleta (salario base, deducciones,
        impuesto de renta, cargas patronales y totales) sin modificar novedades.

        Útil cuando se cambian tramos de renta, tasas de CCSS u otras
        configuraciones en la BD sin que haya cambiado el salario del empleado,
        ya que Odoo no detecta automáticamente ese cambio externo en campos
        store=True.
        """
        for rec in self:
            if rec.state != 'draft':
                continue
            # Odoo 19: env.context es read-only — usar with_context() para
            # invalidar el cache de tramos de renta en este request.
            rec = rec.with_context(
                **{k: v for k, v in rec.env.context.items()
                   if k != '_income_tax_brackets_cache'}
            )
            # Forzar recompute de toda la cadena de campos computados almacenados.
            rec._compute_proportional_days()
            rec._compute_base_salary()
            rec._compute_extras()
            rec._compute_bono_salarial()
            rec._compute_gross()
            rec._compute_deductions()
            rec._compute_totals()
        return True

    def action_confirm(self) -> None:
        """FIX B-06 v58: write() batch — atomicidad total."""
        if not self.env.su and not self.env.user.has_group('planilla_cr.group_planilla_aprobador'):
            raise UserError(
                'No tiene permisos para confirmar boletas. '
                'Se requiere el rol de Aprobador de Planilla o superior.'
            )
        for rec in self:
            if rec.state != 'draft':
                raise UserError(f'La boleta {rec.name} no está en borrador.')
        self._validate_before_confirm()
        self.write({'state': 'confirmed'})
        _logger.info(
            'planilla_cr.action_confirm: %d boleta(s) confirmada(s) por %s',
            len(self), self.env.user.name
        )

    def action_pay(self, skip_accounting=False):
        if not self.env.su and not self.env.user.has_group('planilla_cr.group_planilla_aprobador'):
            raise UserError(
                'No tiene permisos para pagar boletas. '
                'Se requiere el rol de Aprobador de Planilla o superior.'
            )
        for rec in self:
            if rec.state != 'confirmed':
                raise UserError(f'La boleta {rec.name} debe estar confirmada para pagar.')
            with rec.env.cr.savepoint():
                rec.state = 'done'
                if not skip_accounting:
                    rec._create_accounting_entry()
                rec.overtime_ids.filtered(lambda o: o.state == 'approved').write({'state': 'paid'})
                rec.vacation_ids.filtered(lambda v: v.state == 'approved').write({'state': 'paid'})
                rec.disability_ids.filtered(lambda d: d.state == 'confirmed').write({'state': 'paid'})
                # FIX-AUD-07: licencias pasan a 'paid' al pagar la boleta (no en sync)
                rec.leave_cr_ids.filtered(lambda l: l.state == 'approved').write({'state': 'paid'})
                loan_lines = rec.deduction_line_ids.filtered(lambda l: l.loan_installment_id)
                for line in loan_lines:
                    line.loan_installment_id.write({'state': 'deducted', 'payslip_id': rec.id})
                    line.loan_installment_id.loan_id.action_activate()
                    line.loan_installment_id.loan_id.action_check_paid()
                self.env['planilla.salary.history'].create({
                    'employee_id':    rec.employee_id.id,
                    'salary':         rec.net_salary,
                    'gross_salary':   rec.gross_salary,
                    'effective_date': rec.date_to,
                    'payslip_id':     rec.id,
                    'reason':         f'Planilla {rec.name}',
                    # FIX-D1: estado 'authorized' para que _compute_avg_last_4_weeks
                    # y _onchange_employee (liquidaciones/simulador) encuentren este
                    # registro al calcular el promedio de salarios variables (Art. 153 CT).
                    # Sin este campo el historial queda en 'draft' y es invisible
                    # para todas las consultas de promedio → salario variable no funciona.
                    'state':          'authorized',
                    'authorized_by':  self.env.user.id,
                    'authorized_date': fields.Datetime.now(),
                })
            if rec.employee_id.work_email:
                try:
                    template = self.env.ref(
                        'planilla_cr.email_template_payslip_paid',
                        raise_if_not_found=False
                    )
                    if template:
                        template.send_mail(rec.id, force_send=False)
                except Exception as e:
                    _logger.warning('planilla_cr.action_pay: email fallido (%s): %s', rec.name, e)
                    try:
                        rec.message_post(
                            body=(
                                f'⚠️ <b>No se pudo enviar el email de boleta</b> al correo '
                                f'{rec.employee_id.work_email}. Error: {str(e)[:200]}. '
                                f'Use el botón "Enviar Boleta" para reenviar manualmente.'
                            ),
                            message_type='notification',
                        )
                    except Exception:
                        pass

    def action_cancel(self) -> None:
        for rec in self:
            if rec.state == 'done':
                raise UserError(
                    'No se puede cancelar una boleta ya pagada. '
                    'Revierta el asiento contable primero.'
                )
            if rec.move_id and rec.move_id.state == 'posted':
                rec.move_id.button_cancel()
            for line in rec.deduction_line_ids.filtered(lambda l: l.loan_installment_id):
                inst = line.loan_installment_id
                if inst.state == 'deducted' and inst.payslip_id == rec:
                    inst.write({'state': 'pending', 'payslip_id': False})
                    if inst.loan_id.state == 'paid':
                        inst.loan_id.write({'state': 'active'})
            rec.vacation_ids.filtered(lambda v: v.state == 'paid').write({'state': 'approved'})
            rec.disability_ids.filtered(lambda d: d.state == 'paid').write({'state': 'confirmed'})
            rec.overtime_ids.filtered(lambda o: o.state == 'paid').write({'state': 'approved'})
            # FIX-AUD-01: restaurar licencias especiales al estado aprobado
            # Si no se hace, la licencia queda en 'paid' huérfana y no se puede
            # sincronizar a otra boleta en caso de que se regenere la planilla.
            rec.leave_cr_ids.filtered(lambda l: l.state == 'paid').write({
                'state': 'approved',
                'payslip_id': False,
            })
            # Restaurar cobros al empleado al estado aprobado para que puedan
            # sincronizarse a una nueva boleta si se regenera la planilla.
            charge_lines = rec.deduction_line_ids.filtered(
                lambda l: l.employee_charge_id
            )
            if charge_lines:
                charge_ids_list = [l.employee_charge_id for l in charge_lines if l.employee_charge_id]
                if charge_ids_list:
                    # FIX BUG-COBRO-01: separar cobros únicos de recurrentes.
                    all_charges = self.env['planilla.employee.charge'].browse(charge_ids_list)
                    # Únicos (applied) → volver a approved
                    unique_charges = all_charges.filtered(
                        lambda c: not c.is_recurring and c.state == 'applied'
                                  and c.payslip_id.id == rec.id
                    )
                    if unique_charges:
                        unique_charges.write({'state': 'approved', 'payslip_id': False})
                    # Recurrentes → limpiar el período de applied_periods
                    recurring_charges = all_charges.filtered(lambda c: c.is_recurring)
                    for charge in recurring_charges:
                        charge._remove_period_applied(rec.date_from)
                        # Si ya no tiene más períodos activos, limpiar payslip_id
                        if not charge.applied_periods:
                            charge.payslip_id = False
            rec.state = 'cancelled'

    def action_reset_to_draft(self) -> None:
        for rec in self:
            if rec.state not in ('cancelled', 'confirmed'):
                raise UserError('Solo se pueden reactivar boletas canceladas o confirmadas.')

            # FIX BUG-UNLINK-01: restaurar TODOS los objetos vinculados al volver
            # a borrador, no solo leave_cr. Permite resincronizar completamente.

            # Cuotas de préstamo: deducted → pending
            loan_lines = rec.deduction_line_ids.filtered(lambda l: l.loan_installment_id)
            for line in loan_lines:
                inst = line.loan_installment_id
                if inst and inst.state == 'deducted' and inst.payslip_id.id == rec.id:
                    inst.write({'state': 'pending', 'payslip_id': False})
                    if inst.loan_id and inst.loan_id.state == 'paid':
                        inst.loan_id.write({'state': 'active'})

            # Horas extra: paid → approved
            rec.overtime_ids.filtered(
                lambda o: o.state == 'paid'
            ).write({'state': 'approved'})

            # Vacaciones: paid → approved
            rec.vacation_ids.filtered(
                lambda v: v.state == 'paid'
            ).write({'state': 'approved'})

            # Incapacidades: paid → confirmed
            rec.disability_ids.filtered(
                lambda d: d.state == 'paid'
            ).write({'state': 'confirmed'})

            # Licencias especiales CR: paid → approved + limpiar payslip_id
            rec.leave_cr_ids.filtered(
                lambda l: l.state in ('paid', 'approved')
            ).write({'payslip_id': False, 'state': 'approved'})

            # Cobros recurrentes: limpiar período de applied_periods
            charge_lines = rec.deduction_line_ids.filtered(lambda l: l.employee_charge_id)
            if charge_lines:
                charge_ids = [l.employee_charge_id for l in charge_lines if l.employee_charge_id]
                if charge_ids:
                    all_charges = self.env['planilla.employee.charge'].browse(charge_ids).exists()
                    for charge in all_charges.filtered(lambda c: c.is_recurring):
                        if rec.date_from:
                            charge._remove_period_applied(rec.date_from)
                        if not charge.applied_periods:
                            charge.payslip_id = False
                    unique = all_charges.filtered(
                        lambda c: not c.is_recurring and c.state == 'applied'
                    )
                    if unique:
                        unique.write({'state': 'approved', 'payslip_id': False})

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
