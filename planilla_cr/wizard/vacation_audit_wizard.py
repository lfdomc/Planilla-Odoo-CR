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
        default=False,
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

            # Leer directamente los valores calculados de la ficha del empleado.
            # _compute_vacation_balance ya ejecutó arriba — no recalcular.
            cutoff       = emp.vacation_initial_balance_date
            saldo_inicial = emp.vacation_initial_balance or 0.0

            # Acumulado y tomados desde los campos compute del empleado
            accrued_total = emp.vacation_days_accrued   # inicial + nuevos + aniversarios
            taken         = round(emp.vacation_days_taken, 2)
            disponible    = emp.vacation_days_available

            # Para mostrar en el reporte: nuevos días desde el corte
            acum_post = round(accrued_total - saldo_inicial, 1)

            # Aniversarios pendientes de aplicar (los que no han sido marcados)
            from dateutil.relativedelta import relativedelta as _rdelta
            annis_pendientes = []
            last_ann_year = emp.vacation_last_anniversary_year or 0
            _config2 = self.env['planilla.accounting.config'].search(
                [('company_id', '=', emp.company_id.id)], limit=1)
            _av_base = _config2.extra_vacation_days_amount if _config2 else 2
            _av_mode = _config2.extra_vacation_days_mode if _config2 else 'per_year'
            yr = emp.entry_date.year + 1
            while True:
                try:    ann = emp.entry_date.replace(year=yr)
                except: ann = _date(yr, 3, 1)
                if ann > ref:
                    break
                anos = yr - emp.entry_date.year
                dias_extra = (_av_base * anos) if _av_mode == 'per_year' else _av_base
                if last_ann_year < ann.year and (not cutoff or ann > cutoff):
                    annis_pendientes.append((ann, anos, dias_extra))
                yr += 1

            dias_anni_pendientes = sum(a[2] for a in annis_pendientes)

            # Los valores reales vienen del empleado — sin recalcular
            saldo_real_ahora = int(disponible)
            saldo_correcto   = int(disponible)
            discrepancia     = dias_anni_pendientes
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

    def action_export_excel(self):
        """Exporta la auditoria de vacaciones a Excel."""
        self.ensure_one()
        if not self.computed:
            raise UserError('Primero ejecute la auditoria.')
        try:
            import xlsxwriter
        except ImportError:
            raise UserError('xlsxwriter no esta instalado.')
        import io, base64

        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True})
        ws = wb.add_worksheet('Auditoria Vacaciones')

        bold   = wb.add_format({'bold': True, 'bg_color': '#1F4E79',
                                'font_color': 'white', 'border': 1})
        normal = wb.add_format({'border': 1})
        numfmt = wb.add_format({'num_format': '#,##0.00', 'border': 1})
        ok_f   = wb.add_format({'border': 1, 'bg_color': '#E2EFDA'})
        err_f  = wb.add_format({'border': 1, 'bg_color': '#FCE4EC', 'bold': True})

        ws.set_column('A:A', 32); ws.set_column('B:C', 12)
        ws.set_column('D:E', 10); ws.set_column('F:F', 22)
        ws.set_column('G:I', 10); ws.set_column('J:J', 10)

        headers = ['Empleado', 'Fecha Corte', 'Saldo Inicial',
                   'Nuevos Dias', 'Aniversarios Pend.',
                   'Detalle Aniversarios', 'Dias Tomados',
                   'Saldo Sistema', 'Saldo Correcto', 'Estado']
        for col, h in enumerate(headers):
            ws.write(0, col, h, bold)

        for row, line in enumerate(self.line_ids, 1):
            fmt = err_f if line.estado != 'ok' else ok_f
            ws.write(row, 0, line.employee_id.name or '', fmt)
            ws.write(row, 1, line.cutoff_date.strftime('%d/%m/%Y') if line.cutoff_date else '', fmt)
            ws.write(row, 2, line.saldo_inicial,     numfmt)
            ws.write(row, 3, line.acum_proporcional, numfmt)
            ws.write(row, 4, line.dias_anni_pend,    numfmt)
            ws.write(row, 5, line.aniversarios_pend or '', fmt)
            ws.write(row, 6, line.dias_tomados,      numfmt)
            ws.write(row, 7, line.saldo_sistema,     numfmt)
            ws.write(row, 8, line.saldo_correcto,    numfmt)
            ws.write(row, 9, line.estado or 'ok',    fmt)

        wb.close()
        data = base64.b64encode(output.getvalue()).decode()
        fname = f'Auditoria_Vacaciones_{self.ref_date}.xlsx'

        att = self.env['ir.attachment'].create({
            'name': fname, 'type': 'binary', 'datas': data,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })
        return {'type': 'ir.actions.act_url',
                'url': f'/web/content/{att.id}?download=true',
                'target': 'self'}

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
            # Forzar recompute inmediato para que la ficha muestre el valor correcto
            emp._compute_vacation_balance()
            emp.flush_recordset()
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
