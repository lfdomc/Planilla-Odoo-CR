from odoo import models, fields, api
from odoo.exceptions import UserError
from datetime import date as _date
from dateutil.relativedelta import relativedelta


class VacationAuditWizard(models.TransientModel):
    """Auditoria completa de saldos de vacaciones de todos los empleados."""
    _name = 'planilla.vacation.audit.wizard'
    _description = 'Auditoria de Saldos de Vacaciones'

    base_days_anniversary = fields.Float(
        string='Dias extra por ano laborado',
        required=True,
        default=2.0,
    )
    reference_date = fields.Date(
        string='Fecha de Referencia',
        required=True,
        default=lambda self: _date.today(),
        help='Fecha hasta la cual calcular el saldo correcto. Normalmente hoy.'
    )
    show_only_discrepancies = fields.Boolean(
        string='Mostrar solo discrepancias',
        default=True,
    )
    line_ids = fields.One2many(
        'planilla.vacation.audit.line', 'wizard_id',
        string='Resultado de Auditoria'
    )
    computed = fields.Boolean(default=False)
    total_employees = fields.Integer(string='Total empleados', readonly=True)
    ok_count        = fields.Integer(string='Correctos', readonly=True)
    discrepancy_count = fields.Integer(string='Con discrepancia', readonly=True)

    def action_audit(self):
        self.ensure_one()
        self.line_ids.unlink()
        lines = []
        ref = self.reference_date
        base = self.base_days_anniversary
        employees = self.env['hr.employee'].search(
            [('active', '=', True)], order='name'
        )
        ok = disc = 0

        for emp in employees:
            if not emp.entry_date:
                continue

            cutoff = emp.vacation_initial_balance_date
            saldo_inicial = emp.vacation_initial_balance or 0.0
            last_ann_year = emp.vacation_last_anniversary_year or 0

            # --- 1. Acumulacion proporcional desde el punto de partida ---
            if cutoff and emp.entry_date < cutoff:
                # Tiene saldo inicial al corte: acumular desde corte a ref
                dias_desde = (ref - cutoff).days
                semanas = max(dias_desde, 0) / 7.0
                acum_post = round((semanas / 50.0) * 12.0, 2)
                base_calc = saldo_inicial
            else:
                # Sin corte o entro despues del corte: acumular desde ingreso
                start = cutoff if (cutoff and cutoff > emp.entry_date) else emp.entry_date
                dias_desde = max((ref - start).days, 0)
                semanas = dias_desde / 7.0
                acum_post = round((semanas / 50.0) * 12.0, 2)
                base_calc = 0.0

            # --- 2. Aniversarios ganados hasta ref ---
            annis_pendientes = []
            annis_ya_en_saldo = []
            yr = emp.entry_date.year + 1
            while True:
                try:
                    ann = emp.entry_date.replace(year=yr)
                except ValueError:
                    ann = _date(yr, 3, 1)
                if ann > ref:
                    break
                anos = yr - emp.entry_date.year
                dias_extra = base * anos
                # Si tiene corte y el aniversario cayo ANTES del corte:
                # ya esta incluido en saldo_inicial
                if cutoff and ann <= cutoff:
                    annis_ya_en_saldo.append((ann, anos, dias_extra))
                else:
                    # Aniversario despues del corte: deberia estar aplicado
                    # si vacation_last_anniversary_year >= ann.year
                    if last_ann_year >= ann.year:
                        annis_ya_en_saldo.append((ann, anos, dias_extra))
                    else:
                        annis_pendientes.append((ann, anos, dias_extra))
                yr += 1

            dias_anni_pendientes = sum(a[2] for a in annis_pendientes)

            # --- 3. Dias tomados en el sistema ---
            taken_recs = self.env['planilla.vacation.payment'].search([
                ('employee_id', '=', emp.id),
                ('state', 'in', ['approved', 'paid']),
                ('vacation_type', 'in', ['disfrutadas', 'adelanto']),
            ])
            taken = round(sum(taken_recs.mapped('days')), 2)

            # --- 4. Saldo correcto esperado ---
            # Calcular el saldo 'real ahora' SIN aniversarios pendientes
            # (esto es lo que el sistema calcularia si el cron corriera ahora)
            saldo_real_ahora = int(base_calc + acum_post - taken)

            # El saldo correcto INCLUYE los aniversarios que faltan aplicar
            saldo_correcto = int(base_calc + acum_post + dias_anni_pendientes - taken)

            # La discrepancia real = solo los dias de aniversario pendientes
            discrepancia = dias_anni_pendientes
            tiene_disc = discrepancia > 0

            if tiene_disc:
                disc += 1
                if discrepancia > 0:
                    estado = 'bajo'   # sistema tiene menos de lo correcto
                else:
                    estado = 'alto'   # sistema tiene mas
            else:
                ok += 1
                estado = 'ok'

            if self.show_only_discrepancies and not tiene_disc:
                continue

            anni_pend_desc = ', '.join([
                f"{a[0].strftime('%d/%m/%y')}+{a[2]:.0f}d"
                for a in annis_pendientes
            ]) or 'ninguno'

            lines.append((0, 0, {
                'employee_id':        emp.id,
                'entry_date':         emp.entry_date,
                'cutoff_date':        cutoff,
                'saldo_inicial':      saldo_inicial,
                'acum_proporcional':  acum_post,
                'aniversarios_pend':  anni_pend_desc,
                'dias_anni_pend':     dias_anni_pendientes,
                'dias_tomados':       taken,
                'saldo_sistema':      saldo_real_ahora,
                'saldo_correcto':     saldo_correcto,
                'discrepancia':       discrepancia,
                'estado':             estado,
                'corregir':           tiene_disc,
            }))

        self.write({
            'line_ids':           lines,
            'computed':           True,
            'total_employees':    ok + disc,
            'ok_count':           ok,
            'discrepancy_count':  disc,
        })
        return {
            'type':      'ir.actions.act_window',
            'res_model': 'planilla.vacation.audit.wizard',
            'res_id':    self.id,
            'view_mode': 'form',
            'target':    'new',
        }

    def action_apply_corrections(self):
        """Aplica las correcciones marcadas."""
        self.ensure_one()
        applied = 0
        for line in self.line_ids.filtered(lambda l: l.corregir):
            emp = line.employee_id
            # Ajustar el saldo_inicial para que el sistema calcule correctamente
            # El nuevo saldo_inicial = saldo_correcto - acum_proporcional + taken
            # (de modo que: saldo_inicial + acum - taken = saldo_correcto)
            # Para que el sistema calcule el saldo correcto, ajustar saldo_inicial
            # saldo_correcto = int(nuevo_inicial + acum_proporcional - tomados)
            # nuevo_inicial = saldo_correcto + tomados - acum_proporcional
            nuevo_inicial = round(
                line.saldo_correcto + line.dias_tomados - line.acum_proporcional, 2
            )
            emp.write({
                'vacation_initial_balance':       nuevo_inicial,
                'vacation_initial_balance_date':  line.cutoff_date or self.reference_date,
                'vacation_last_anniversary_year': self.reference_date.year,
            })
            emp.message_post(
                body=(
                    f"<b>Auditoria de vacaciones {self.reference_date}:</b> "
                    f"Saldo sistema: {line.saldo_sistema}d | "
                    f"Saldo correcto: {line.saldo_correcto}d | "
                    f"Discrepancia: {line.discrepancia:+.0f}d | "
                    f"Nuevo saldo inicial: {nuevo_inicial:.2f}d"
                ),
                message_type='notification',
            )
            applied += 1

        return {
            'type': 'ir.actions.client',
            'tag':  'display_notification',
            'params': {
                'title':   f'Auditoria aplicada',
                'message': f'{applied} empleado(s) corregidos.',
                'type':    'success',
                'sticky':  True,
            }
        }


class VacationAuditLine(models.TransientModel):
    _name = 'planilla.vacation.audit.line'
    _description = 'Linea de Auditoria de Vacaciones'

    wizard_id          = fields.Many2one('planilla.vacation.audit.wizard', ondelete='cascade')
    employee_id        = fields.Many2one('hr.employee', string='Empleado', readonly=True)
    entry_date         = fields.Date(string='Ingreso', readonly=True)
    cutoff_date        = fields.Date(string='Corte', readonly=True)
    saldo_inicial      = fields.Float(string='Saldo Inicial', readonly=True)
    acum_proporcional  = fields.Float(string='Acum. desde Corte', readonly=True)
    aniversarios_pend  = fields.Char(string='Aniversarios Pendientes', readonly=True)
    dias_anni_pend     = fields.Float(string='Dias Aniv.', readonly=True)
    dias_tomados       = fields.Float(string='Dias Tomados', readonly=True)
    saldo_sistema      = fields.Integer(string='Saldo Sistema', readonly=True)
    saldo_correcto     = fields.Integer(string='Saldo Correcto', readonly=True)
    discrepancia       = fields.Integer(string='Diferencia', readonly=True)
    estado             = fields.Selection([
        ('ok',   'OK'),
        ('bajo', 'Sistema bajo'),
        ('alto', 'Sistema alto'),
    ], string='Estado', readonly=True)
    corregir           = fields.Boolean(string='Corregir')
