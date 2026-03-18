import logging
from odoo import models, api
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
            else:
                # Períodos distintos → sync individual seguro
                for rec in records:
                    rec._sync_novedades()
                    rec._sync_recurring_benefits()
                    rec._sync_rop()
                    rec._sync_bonos()
                    rec._sync_embargos()
                    rec._sync_loan_deductions()
        return records

    def action_sync_novedades(self) -> bool:
        """Botón manual: re-sincroniza novedades del período en la boleta."""
        for rec in self:
            if rec.state == 'draft':
                rec._sync_novedades()
                rec._sync_recurring_benefits()
                rec._sync_rop()
                rec._sync_bonos()
                rec._sync_embargos()
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
            rec.state = 'cancelled'

    def action_reset_to_draft(self) -> None:
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
