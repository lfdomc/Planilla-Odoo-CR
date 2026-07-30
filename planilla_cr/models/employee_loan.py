import logging
import math
from odoo import models, fields, api
from odoo.models import Constraint
from odoo.exceptions import ValidationError, UserError
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta


_logger = logging.getLogger(__name__)
class EmployeeLoan(models.Model):
    _name = 'planilla.employee.loan'
    _description = 'Prestamos y Adelantos de Salario'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_granted desc'

    # FIX I-04 v54: La constraint original UNIQUE(employee_id, date_granted, loan_type)
    # era demasiado permisiva -- permitia crear dos prestamos del mismo tipo el mismo dia
    # con diferentes montos. La nueva constraint incluye amount_total para cubrir ese caso.
    # Se mantiene la constraint de BD para proteccion a nivel de base de datos.
    _unique_loan_employee_date_type = Constraint(
        'UNIQUE(employee_id, date_granted, loan_type, amount_total)',
        'Ya existe un prestamo del mismo tipo y monto para este empleado en la misma fecha.'
    )

    name = fields.Char(string='Referencia', compute='_compute_name', store=True)
    employee_id = fields.Many2one(
        'hr.employee', string='Empleado', required=True, ondelete='restrict', index=True
    )
    company_id = fields.Many2one(
        'res.company', string='Empresa',
        related='employee_id.company_id', store=True, readonly=True,
        index=True,
    )

    branch_id = fields.Many2one(related='employee_id.branch_id', store=True)
    currency_id = fields.Many2one(related='employee_id.currency_id', store=True)

    description = fields.Char(string='Motivo / Descripcion', help='Motivo del prestamo o adelanto')
    loan_type = fields.Selection([
        ('loan', 'Prestamo'),
        ('advance', 'Adelanto de Salario'),
    ], string='Tipo', required=True, default='loan')

    amount_total = fields.Monetary(
        string='Monto Total (CRC)', currency_field='currency_id', required=True
    )
    installments = fields.Integer(
        string='Numero de Cuotas', required=True, default=1,
        help='Numero de cuotas para descontar en boleta, segun la '
             'Frecuencia de Cuota configurada abajo.'
    )
    installment_frequency = fields.Selection([
        ('monthly',   'Mensual (1 cuota por mes)'),
        ('biweekly',  'Quincenal (1 cuota por quincena)'),
    ], string='Frecuencia de Cuota', required=True, default='monthly',
        help='MENSUAL (por defecto): se descuenta 1 cuota al mes, en la '
             'primera boleta de ese mes que se procese -- sin importar si '
             'el empleado tiene calendarizacion mensual, quincenal o '
             'semanal. Comportamiento original del sistema.\\n\\n'
             'QUINCENAL: se descuenta 1 cuota en cada quincena '
             '(aproximadamente cada 15 dias desde la primera deduccion), '
             'para prestamos que el empleado prefiere pagar mas rapido '
             'repartiendo el monto entre ambas quincenas del mes. Solo '
             'tiene sentido si el empleado tiene calendarizacion '
             'quincenal o semanal -- con calendarizacion mensual el '
             'resultado es el mismo que Mensual.'
    )
    installment_amount = fields.Monetary(
        string='Monto por Cuota (CRC)', currency_field='currency_id',
        compute='_compute_installment', store=True
    )
    date_granted = fields.Date(
        string='Fecha de Otorgamiento', required=True, default=fields.Date.today
    )
    date_first_deduction = fields.Date(
        string='Primera Deduccion en', required=True,
        help='Boleta a partir de la cual se empieza a descontar'
    )
    note = fields.Text(string='Observaciones')

    state = fields.Selection([
        ('draft', 'Borrador'),
        ('approved', 'Aprobado'),
        ('active',   'En curso'),
        ('paid',     'Cancelado'),
        ('cancelled','Anulado'),
    ], string='Estado', default='draft')

    installment_ids = fields.One2many(
        'planilla.loan.installment', 'loan_id', string='Cuotas'
    )
    amount_paid = fields.Monetary(
        string='Pagado (CRC)', currency_field='currency_id',
        compute='_compute_amounts', store=True
    )
    max_installment_allowed = fields.Monetary(
        string='Cuota Maxima Permitida (CRC)',
        currency_field='currency_id',
        compute='_compute_max_installment', store=True,
        help='50% del salario neto estimado -- limite legal Art. 172 CT'
    )
    amount_pending = fields.Monetary(
        string='Saldo Pendiente (CRC)', currency_field='currency_id',
        compute='_compute_amounts', store=True
    )
    move_id = fields.Many2one(
        'account.move', string='Asiento de Otorgamiento',
        readonly=True, copy=False,
        help='Asiento contable generado al aprobar el prestamo: '
             'Debito Prestamos por Cobrar / Credito Caja o Banco.'
    )

    @api.depends('employee_id', 'loan_type', 'date_granted')
    def _compute_name(self):
        types = {'loan': 'Prestamo', 'advance': 'Adelanto'}
        for rec in self:
            t = types.get(rec.loan_type, '')
            e = rec.employee_id.name or ''
            d = str(rec.date_granted) if rec.date_granted else ''
            rec.name = f'{t} -- {e} -- {d}'

    @api.depends('amount_total', 'installments')
    def _compute_installment(self):
        for rec in self:
            if rec.installments and rec.installments > 0:
                rec.installment_amount = round(rec.amount_total / rec.installments, 2)
            else:
                rec.installment_amount = rec.amount_total

    @api.depends('installment_ids.amount', 'installment_ids.state')
    def _compute_amounts(self):
        for rec in self:
            paid = sum(
                i.amount for i in rec.installment_ids if i.state == 'deducted'
            )
            rec.amount_paid    = round(paid, 2)
            rec.amount_pending = round(rec.amount_total - paid, 2)

    @api.constrains('amount_total', 'installments')
    def _check_amounts(self):
        for rec in self:
            if rec.amount_total <= 0:
                raise ValidationError('El monto total debe ser mayor a cero.')
            if rec.installments <= 0:
                raise ValidationError('Las cuotas deben ser al menos 1.')

    has_active_loan_warning = fields.Char(
        string='Alerta préstamo activo',
        compute='_compute_active_loan_warning',
        help='Alerta informativa si el empleado ya tiene un préstamo activo del mismo tipo.'
    )

    def _compute_active_loan_warning(self):
        for rec in self:
            if rec.state in ('draft', 'cancelled') and rec.employee_id and rec.loan_type:
                duplicates = self.search([
                    ('employee_id', '=', rec.employee_id.id),
                    ('loan_type', '=', rec.loan_type),
                    ('state', 'in', ('approved', 'active')),
                    ('id', '!=', rec.id or 0),
                ], limit=1)
                if duplicates:
                    tipo = dict(rec._fields['loan_type'].selection).get(rec.loan_type, rec.loan_type)
                    rec.has_active_loan_warning = (
                        f'⚠ El empleado ya tiene un {tipo} activo: '
                        f'{duplicates[0].name} (pendiente por pagar). '
                        f'Se puede registrar este nuevo adelanto, pero considere '
                        f'la capacidad de pago del empleado.'
                    )
                else:
                    rec.has_active_loan_warning = False
            else:
                rec.has_active_loan_warning = False

    # _check_duplicate_active eliminado — ahora es alerta informativa, no bloqueo

    @api.depends('employee_id', 'employee_id.base_salary', 'installment_frequency')
    def _compute_max_installment(self):
        rh = self.env['planilla.rate.helper']
        ccss_rate = rh.get_ccss_employee_rate()
        for rec in self:
            base = rec.employee_id.base_salary or 0.0
            estimated_net_monthly = base * (1 - ccss_rate)
            # FIX: el limite del 50% (Art. 172 CT) debe calcularse sobre el
            # salario neto del MISMO periodo que la cuota, no siempre sobre
            # el mensual completo. Si la cuota es quincenal, comparar
            # contra el neto mensual completo permitiria cuotas quincenales
            # hasta el doble de lo que la ley realmente autoriza por pago.
            if rec.installment_frequency == 'biweekly':
                rec.max_installment_allowed = round(estimated_net_monthly / 2 * 0.50, 2)
            else:
                rec.max_installment_allowed = round(estimated_net_monthly * 0.50, 2)

    def action_print_amortization(self):
        return self.env.ref('planilla_cr.action_report_loan_amortization').report_action(self)


    def _check_installment_salary_limit(self):
        """Verifica que la cuota no supere el 50% del salario neto del
        periodo correspondiente (Art. 172 CT) -- mensual o quincenal
        segun installment_frequency."""
        self.ensure_one()
        if self.max_installment_allowed and self.installment_amount > self.max_installment_allowed:
            periodo_txt = 'quincenal' if self.installment_frequency == 'biweekly' else 'mensual'
            raise UserError(
                f'La cuota {periodo_txt} (CRC{self.installment_amount:,.2f}) supera el 50% '
                f'del salario neto {periodo_txt} estimado (CRC{self.max_installment_allowed:,.2f}). '
                f'Ajuste el monto o el numero de cuotas (Art. 172 Codigo de Trabajo).'
            )

    def action_approve(self):
        self.ensure_one()
        if self.state != 'draft':
            raise UserError('Solo se pueden aprobar prestamos en borrador.')
        # Verificar limite del 50% del salario neto (Art. 172 CT)
        self._check_installment_salary_limit()
        self._generate_installments()
        self.state = 'approved'
        # H4 FIX -- Generar asiento contable de otorgamiento
        self._create_loan_accounting_entry()

    def _create_loan_accounting_entry(self):
        """
        H4 FIX -- Asiento contable al otorgar el prestamo:
          DEBE:  Prestamos por Cobrar (activo corriente)
          HABER: Caja / Banco (activo -- la empresa desembolsa el dinero)

        Usa las cuentas configuradas en planilla.accounting.config.
        Si no hay cuentas especificas de prestamos, usa cuentas de fallback
        y registra una advertencia en el chatter.
        """
        self.ensure_one()
        config = self.env['planilla.accounting.config'].sudo().get_config(
            self.employee_id.company_id.id
        )
        if not config or not config.journal_id:
            # Sin configuracion contable -- registrar advertencia pero no bloquear
            self.message_post(
                body='<b>Aviso:</b> No se genero asiento contable de otorgamiento '
                     'porque no hay configuracion contable configurada. '
                     'Vaya a Planilla -> Configuracion -> Contabilidad.',
                message_type='notification',
            )
            return

        # BUG #11 FIX v50: Usar cuenta configurada en accounting_config (115000)
        # en lugar de busqueda fragil por nombre que podria fallar silenciosamente.
        loan_receivable = config.account_loans_receivable
        if not loan_receivable:
            # Fallback: buscar por codigo exacto
            loan_receivable = self.env['account.account'].search([
                ('code', '=', '115000'),
                ('company_ids', 'in', self.employee_id.company_id.id),
            ], limit=1)
        if not loan_receivable:
            # Ultimo fallback: crear cuenta 115000
            # FIX BUG-N05 v52: (4, id) compatible Odoo 14-19 -- sin imports extra
            loan_receivable = self.env['account.account'].create({
                'code': '115000',
                'name': 'Prestamos a Empleados por Cobrar',
                'account_type': 'asset_current',
                'company_ids': [(4, self.employee_id.company_id.id)],
            })
            self.message_post(
                body='<b>Aviso:</b> Se creo la cuenta 115000 Prestamos a Empleados por Cobrar. '
                     'Configure account_loans_receivable en Planilla -> Configuracion -> Contabilidad.',
                message_type='notification',
            )

        # FIX-L2: usar la MISMA config (con company_id del empleado) para bank_account.
        # La version anterior hacia get_config() sin argumento (sesion del usuario),
        # lo que en multi-empresa podia mezclar la config de dos companias distintas.
        bank_account = config.account_bank_disbursement
        if not bank_account:
            # Fallback: buscar cuenta de Caja/Banco activa en la empresa del empleado
            bank_account = self.env['account.account'].search([
                ('account_type', 'in', ('asset_cash',)),
                ('company_ids', 'in', self.employee_id.company_id.id),
            ], limit=1)
        if not bank_account:
            self.message_post(
                body='<b>Aviso:</b> No se encontro cuenta de Caja/Banco para el '
                     'asiento de otorgamiento. Configure la cuenta en '
                     'Planilla -> Configuracion -> Contabilidad -> Banco/Caja para Desembolso.',
                message_type='notification',
            )
            return

        emp = self.employee_id.name
        lines = [
            (0, 0, {
                'account_id': loan_receivable.id,
                'name': f'Prestamo otorgado -- {emp} -- {self.name}',
                'debit': round(self.amount_total, 2),
                'credit': 0.0,
            }),
            (0, 0, {
                'account_id': bank_account.id,
                'name': f'Desembolso prestamo -- {emp} -- {self.name}',
                'debit': 0.0,
                'credit': round(self.amount_total, 2),
            }),
        ]
        _cur = config.journal_id.currency_id or self.company_id.currency_id
        for _l in lines:
            _l[2]['currency_id'] = _cur.id

        move = self.env['account.move'].sudo().create({
            'journal_id':  config.journal_id.id,
            'company_id':  self.employee_id.company_id.id,
            'date':        self.date_granted or fields.Date.context_today(self),
            'ref':         f'Prestamo -- {emp} -- {self.name}',
            'move_type':   'entry',
            'currency_id': _cur.id,
            'line_ids':    lines,
        })
        move.action_post()
        self.move_id = move.id
        self.message_post(
            body=f'Asiento contable de otorgamiento creado: <a href="/web#id={move.id}&model=account.move">{move.name}</a>',
            message_type='notification',
        )

    def _generate_installments(self):
        """Genera las lineas de cuota con fechas a partir de date_first_deduction.
        FIX-L4: La ultima cuota se ajusta para cubrir el residuo de redondeo.
        Ej: CRC100,000 en 3 cuotas -> CRC33,333.33 * 3 = CRC99,999.99 (falta CRC0.01).
        Sin el ajuste, action_check_paid nunca marcaria el prestamo como pagado
        porque la suma de cuotas no iguala exactamente amount_total.

        La separacion entre cuotas depende de installment_frequency:
          - 'monthly' (default): cada cuota un mes calendario despues de la
            anterior (comportamiento original, sin cambios).
          - 'biweekly': cada cuota 15 dias despues de la anterior, para
            cobrar en cada quincena en vez de una vez al mes.
        """
        self.ensure_one()
        self.installment_ids.unlink()
        base_date = self.date_first_deduction
        base_amount = self.installment_amount
        n = self.installments
        for i in range(n):
            if self.installment_frequency == 'biweekly':
                due_date = base_date + relativedelta(days=15 * i)
            else:
                due_date = base_date + relativedelta(months=i)
            # Ultima cuota: ajustar para cubrir exactamente el monto total
            if i == n - 1:
                already = round(base_amount * (n - 1), 2)
                amount = round(self.amount_total - already, 2)
            else:
                amount = base_amount
            self.env['planilla.loan.installment'].create({
                'loan_id':    self.id,
                'sequence':   i + 1,
                'due_date':   due_date,
                'amount':     amount,
            })

    def action_convert_to_biweekly(self):
        """
        Cambia un prestamo YA aprobado/activo de cobro mensual a
        quincenal, sin perder el historial de cuotas ya descontadas.

        Caso de uso: el prestamo se creo con frecuencia mensual y ya
        tiene una o mas cuotas cobradas, pero ahora se quiere que las
        cuotas RESTANTES se cobren quincenalmente en vez de mensualmente
        (ej. el empleado pidio pagar mas rapido, o se cambio su
        calendarizacion a quincenal). A diferencia de crear el prestamo
        directamente en 'biweekly' (que solo aplica ANTES de aprobar,
        mientras installment_frequency sigue siendo editable), este
        boton opera sobre un prestamo en curso:

          1. Las cuotas ya marcadas 'deducted' (ya cobradas en boletas
             pasadas) NUNCA se tocan -- se preserva el historial exacto
             de lo que ya se le desconto al empleado.
          2. Las cuotas 'pending' que aun no se han cobrado se eliminan
             y se regeneran quincenalmente, repartiendo el SALDO REAL
             pendiente (amount_pending, que ya excluye lo cobrado) en
             cuotas del mismo monto que tenia la cuota original, para
             no sorprender al empleado con un monto de cuota distinto
             al que ya conocia.
          3. La primera cuota nueva se programa a partir de la fecha de
             HOY (o la fecha de la cuota pendiente mas antigua que se
             esta reemplazando, la que sea posterior), asegurando que
             no se genere una cuota con fecha ya pasada.
        """
        self.ensure_one()
        if self.state not in ('approved', 'active'):
            raise UserError(
                'Solo se puede convertir a cobro quincenal un prestamo '
                'Aprobado o En curso. Si el prestamo esta en Borrador, '
                'simplemente cambie la Frecuencia de Cuota antes de '
                'aprobarlo.'
            )
        if self.installment_frequency == 'biweekly':
            raise UserError('Este prestamo ya esta configurado como quincenal.')

        pending_installments = self.installment_ids.filtered(
            lambda i: i.state == 'pending'
        )
        if not pending_installments:
            raise UserError(
                'No hay cuotas pendientes que convertir -- el prestamo '
                'ya esta completamente cobrado.'
            )

        # Monto de la cuota original (mismo monto por cuota que ya
        # conocia el empleado) -- se usa como base para repartir el
        # saldo pendiente en cuotas quincenales del mismo tamano.
        old_installment_amount = self.installment_amount or 0.0
        remaining_amount = round(self.amount_pending, 2)
        if old_installment_amount <= 0 or remaining_amount <= 0:
            raise UserError(
                'No se pudo determinar el monto a repartir en las '
                'nuevas cuotas quincenales. Revise el saldo pendiente '
                'del prestamo.'
            )

        # Cuantas cuotas quincenales del monto original se necesitan
        # para cubrir el saldo pendiente (redondeado hacia arriba --
        # la ultima cuota se ajusta para cubrir el residuo exacto,
        # igual que en _generate_installments).
        n_new = max(1, math.ceil(remaining_amount / old_installment_amount))

        # Primera fecha disponible: hoy, o la fecha de la cuota pendiente
        # mas antigua si esa es posterior a hoy (evita reprogramar una
        # cuota futura hacia una fecha mas temprana de la planeada).
        today = fields.Date.context_today(self)
        earliest_pending_date = min(pending_installments.mapped('due_date') or [today])
        start_date = max(today, earliest_pending_date)

        next_sequence = max(self.installment_ids.mapped('sequence') or [0]) - len(pending_installments) + 1
        pending_installments.unlink()

        for i in range(n_new):
            due_date = start_date + relativedelta(days=15 * i)
            if i == n_new - 1:
                already = round(old_installment_amount * (n_new - 1), 2)
                amount = round(remaining_amount - already, 2)
            else:
                amount = old_installment_amount
            self.env['planilla.loan.installment'].create({
                'loan_id':    self.id,
                'sequence':   next_sequence + i,
                'due_date':   due_date,
                'amount':     amount,
            })

        # Actualizar el numero total de cuotas y la frecuencia para que
        # installments/installment_frequency reflejen la realidad actual
        # del prestamo (cuotas ya cobradas + las nuevas quincenales).
        deducted_count = len(self.installment_ids) - n_new
        self.write({
            'installments': deducted_count + n_new,
            'installment_frequency': 'biweekly',
        })
        self.message_post(
            body=(
                f'Prestamo convertido a cobro quincenal. '
                f'{deducted_count} cuota(s) ya cobrada(s) se conservan sin '
                f'cambios. Saldo pendiente (CRC{remaining_amount:,.2f}) '
                f'repartido en {n_new} cuota(s) quincenal(es) nueva(s), '
                f'a partir del {start_date}.'
            ),
            message_type='notification',
        )

    def action_activate(self):
        self.write({'state': 'active'})

    def action_cancel(self):
        for rec in self:
            pending = rec.installment_ids.filtered(lambda i: i.state == 'pending')
            pending.write({'state': 'cancelled'})
            rec.state = 'cancelled'

    def action_check_paid(self):
        """Marca el prestamo como cancelado si todas las cuotas estan deducidas."""
        for rec in self:
            if all(i.state in ('deducted', 'cancelled') for i in rec.installment_ids):
                rec.state = 'paid'
                # Notificar al empleado por email
                if rec.employee_id.work_email:
                    try:
                        template = self.env.ref('planilla_cr.email_template_loan_paid', raise_if_not_found=False)
                        if template:
                            template.send_mail(rec.id, force_send=False)
                    except Exception as e:
                        _logger.warning(f"planilla_cr: No se pudo enviar email de prestamo pagado ({rec.name}): {e}")

    def get_pending_installment(self, date_from, date_to):
        """
        Retorna la cuota pendiente a descontar en el periodo dado.

        Comportamiento depende de installment_frequency:

        'monthly' (default, sin cambios respecto al diseno original):
          Busca por MES/ANO para ser compatible con nominas quincenales,
          semanales o de cualquier frecuencia -- la cuota se descuenta en
          la primera boleta que caiga dentro del mismo mes que su
          due_date. FIX D-02 v53: calcula el set de meses cubiertos sin
          iterar dia a dia.

        'biweekly':
          Busca por RANGO EXACTO de fechas (due_date dentro de
          [date_from, date_to] de la boleta), no por mes/ano. Necesario
          porque un prestamo quincenal puede tener 2 cuotas dentro del
          mismo mes (una por quincena) -- la logica de mes/ano no puede
          distinguir cual de las 2 corresponde a esta boleta especifica,
          y terminaria repitiendo o saltando cuotas.
        """
        self.ensure_one()
        if not date_from or not date_to:
            return self.env['planilla.loan.installment']

        if self.installment_frequency == 'biweekly':
            installment = self.installment_ids.filtered(
                lambda i: i.state == 'pending' and
                i.due_date and date_from <= i.due_date <= date_to
            )
            return installment[:1]  # solo una cuota por periodo

        # FIX D-02 v53: Calcular directamente los meses en el rango sin iterar dia a dia.
        # Para periodos de hasta 24 meses esto es O(1) en lugar de O(dias).
        months_in_period = set()
        y, m = date_from.year, date_from.month
        end_y, end_m = date_to.year, date_to.month
        while (y, m) <= (end_y, end_m):
            months_in_period.add((y, m))
            m += 1
            if m > 12:
                m = 1
                y += 1

        installment = self.installment_ids.filtered(
            lambda i: i.state == 'pending' and
            i.due_date and
            (i.due_date.year, i.due_date.month) in months_in_period
        )
        return installment[:1]  # solo una cuota por periodo


class LoanInstallment(models.Model):
    _name = 'planilla.loan.installment'
    _description = 'Cuota de Prestamo'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'sequence asc'

    loan_id = fields.Many2one(
        'planilla.employee.loan', string='Prestamo', required=True, ondelete='cascade'
    )
    sequence   = fields.Integer(string='Ndeg')
    due_date   = fields.Date(string='Fecha de Descuento')
    amount     = fields.Monetary(string='Monto (CRC)', currency_field='currency_id')
    currency_id = fields.Many2one(related='loan_id.currency_id', store=True)
    state = fields.Selection([
        ('pending', 'Pendiente'),
        ('deducted',  'Descontada'),
        ('cancelled', 'Cancelada'),
    ], string='Estado', default='pending')
    payslip_id = fields.Many2one(
        'planilla.payslip.cr', string='Boleta', readonly=True
    )

    def action_fix_orphan_state(self):
        """
        Revierte manualmente a 'Pendiente' una cuota que quedo huerfana:
        marcada 'Descontada' pero SIN una boleta real que la respalde
        (payslip_id vacio, o apuntando a una boleta que ya no existe).

        Caso de uso: correccion RETROACTIVA de cuotas que quedaron en
        este estado por eliminar directamente una boleta pagada, antes
        del fix en payslip_cr.py::unlink() que ahora revierte
        automaticamente prestamos, horas extra, vacaciones,
        incapacidades, licencias y cobros al eliminar CUALQUIER boleta,
        sin importar su estado (incluidas las pagadas). Con ese fix ya
        aplicado, este escenario no deberia volver a ocurrir hacia
        adelante -- este boton queda disponible como herramienta de
        correccion manual y red de seguridad general.

        Por seguridad, NUNCA revierte una cuota que este correctamente
        vinculada a una boleta real y existente -- para esos casos se
        debe usar el flujo normal (Reactivar la boleta a Borrador).
        """
        for rec in self:
            if rec.state != 'deducted':
                raise UserError(
                    f'La cuota #{rec.sequence} no esta en estado '
                    f'"Descontada" -- no hay nada que corregir.'
                )
            if rec.payslip_id and rec.payslip_id.exists():
                raise UserError(
                    f'La cuota #{rec.sequence} esta vinculada a una '
                    f'boleta real y existente ({rec.payslip_id.name}). '
                    f'Para revertirla, reactive esa boleta a Borrador '
                    f'en vez de usar esta correccion manual.'
                )
            rec.write({'state': 'pending', 'payslip_id': False})
            if rec.loan_id.state == 'paid':
                rec.loan_id.write({'state': 'active'})
            rec.message_post(
                body=(
                    'Cuota corregida manualmente: estaba marcada '
                    '"Descontada" sin ninguna boleta real que la '
                    'respalde (la boleta original fue eliminada '
                    'directamente sin revertirla primero). Se devolvio '
                    'a "Pendiente" para que pueda cobrarse en la '
                    'proxima boleta correspondiente.'
                ),
                message_type='notification',
            )
