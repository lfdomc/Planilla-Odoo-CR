from odoo import models, fields, api
from datetime import date as _date


class VacationInitialBalanceWizard(models.TransientModel):
    _name = 'planilla.vacation.initial.balance.wizard'
    _description = 'Corrector de Saldo Inicial de Vacaciones'

    company_id = fields.Many2one(
        'res.company', required=True,
        default=lambda self: self.env.company
    )
    preview_line_ids = fields.One2many(
        'planilla.vacation.initial.balance.line', 'wizard_id',
        string='Empleados a corregir'
    )
    corrected_count = fields.Integer(string='Empleados corregidos', readonly=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        return res

    def action_preview(self):
        """Calcula el saldo inicial correcto para todos los empleados
        que tienen vacation_initial_balance_date pero saldo = 0."""
        self.ensure_one()
        # Limpiar lineas anteriores
        self.preview_line_ids.unlink()

        employees = self.env['hr.employee'].search([
            ('company_id', '=', self.company_id.id),
            ('active', '=', True),
            ('vacation_initial_balance_date', '!=', False),
        ])

        lines = []
        today = _date.today()
        for emp in employees:
            if not emp.entry_date or not emp.vacation_initial_balance_date:
                continue

            cutoff = emp.vacation_initial_balance_date
            current_balance = emp.vacation_initial_balance or 0.0

            # Calcular el saldo teorico correcto en la fecha de corte
            # Art. 153 CT: 12 dias habiles / 50 semanas laboradas
            days_entry_to_cutoff = max((cutoff - emp.entry_date).days, 0)
            teorico_al_corte = (days_entry_to_cutoff / 7.0 / 50.0) * 12.0
            correct_balance = int(teorico_al_corte)

            # Solo incluir si el saldo actual difiere del calculado
            if abs(current_balance - correct_balance) >= 1:
                lines.append({
                    'wizard_id':      self.id,
                    'employee_id':    emp.id,
                    'entry_date':     emp.entry_date,
                    'cutoff_date':    cutoff,
                    'current_balance': current_balance,
                    'correct_balance': correct_balance,
                    'apply':          True,
                })

        if lines:
            self.env['planilla.vacation.initial.balance.line'].create(lines)

        return {
            'type':     'ir.actions.act_window',
            'res_model': 'planilla.vacation.initial.balance.wizard',
            'res_id':   self.id,
            'view_mode': 'form',
            'target':   'new',
        }

    def action_apply(self):
        """Aplica las correcciones marcadas."""
        self.ensure_one()
        count = 0
        for line in self.preview_line_ids.filtered('apply'):
            line.employee_id.write({
                'vacation_initial_balance': line.correct_balance,
            })
            count += 1

        # Forzar recompute de saldos
        employees = self.preview_line_ids.filtered('apply').mapped('employee_id')
        employees._compute_vacation_balance()
        employees.flush_recordset()

        self.corrected_count = count
        return {
            'type':     'ir.actions.act_window',
            'res_model': 'planilla.vacation.initial.balance.wizard',
            'res_id':   self.id,
            'view_mode': 'form',
            'target':   'new',
        }



    def action_export_excel(self):
        """Exporta a Excel los datos de saldo inicial para revision."""
        self.ensure_one()
        import io, base64
        from datetime import date as _date2
        try:
            import xlsxwriter
        except ImportError:
            from odoo.exceptions import UserError as _UE
            raise _UE('xlsxwriter no instalado.')

        employees = self.env['hr.employee'].search([
            ('company_id', '=', self.company_id.id),
            ('active', '=', True),
        ], order='name')

        today = _date2.today()
        output = io.BytesIO()
        wb  = xlsxwriter.Workbook(output, {'in_memory': True})
        ws  = wb.add_worksheet('Saldos Iniciales')

        hdr  = wb.add_format({'bold':True,'bg_color':'#1F4E79','font_color':'white','border':1,'align':'center'})
        norm = wb.add_format({'border':1})
        date_fmt = wb.add_format({'border':1,'num_format':'dd/mm/yyyy'})
        num_fmt  = wb.add_format({'border':1,'num_format':'#,##0.0'})
        warn_fmt = wb.add_format({'border':1,'bg_color':'#FFF2CC'})
        ok_fmt   = wb.add_format({'border':1,'bg_color':'#E2EFDA'})
        miss_fmt = wb.add_format({'border':1,'bg_color':'#FFE0E0'})

        cols = [
            ('Empleado',28),('Fecha Ingreso',14),('Fecha Corte',14),
            ('Saldo Inicial',14),('Dias Acumulados Hoy',20),
            ('Dias Tomados',13),('Saldo Disponible',16),('Estado',20),
        ]
        for c,(name,width) in enumerate(cols):
            ws.write(0, c, name, hdr)
            ws.set_column(c, c, width)
        ws.freeze_panes(1, 0)

        row = 1
        for emp in employees:
            if not emp.entry_date:
                continue
            has_cutoff = bool(emp.vacation_initial_balance_date)
            init_bal   = emp.vacation_initial_balance or 0.0
            accrued    = emp.vacation_days_accrued or 0.0
            taken      = emp.vacation_days_taken   or 0.0
            available  = emp.vacation_days_available or 0.0

            if has_cutoff and init_bal == 0:
                estado = 'REVISAR - saldo inicial = 0'
                sfmt = warn_fmt
            elif has_cutoff and init_bal > 0:
                estado = 'OK con saldo inicial'
                sfmt = ok_fmt
            elif not has_cutoff:
                estado = 'Sin fecha de corte'
                sfmt = miss_fmt
            else:
                estado = 'OK'
                sfmt = norm

            ws.write(row, 0, emp.name or '',            norm)
            ws.write_datetime(row, 1, emp.entry_date, date_fmt) if emp.entry_date else ws.write(row,1,'',norm)
            ws.write_datetime(row, 2, emp.vacation_initial_balance_date, date_fmt) if has_cutoff else ws.write(row,2,'Sin corte',miss_fmt)
            ws.write(row, 3, init_bal,  num_fmt)
            ws.write(row, 4, accrued,   num_fmt)
            ws.write(row, 5, taken,     num_fmt)
            ws.write(row, 6, available, num_fmt)
            ws.write(row, 7, estado,    sfmt)
            row += 1

        wb.close()
        filename = f'Revision_Saldos_Iniciales_{today.strftime("%Y-%m-%d")}.xlsx'
        att = self.env['ir.attachment'].create({
            'name': filename, 'type': 'binary',
            'datas': base64.b64encode(output.getvalue()),
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })
        return {'type':'ir.actions.act_url','url':f'/web/content/{att.id}?download=true','target':'self'}


class VacationInitialBalanceLine(models.TransientModel):
    _name = 'planilla.vacation.initial.balance.line'
    _description = 'Linea de corrector de saldo inicial'

    wizard_id      = fields.Many2one('planilla.vacation.initial.balance.wizard')
    employee_id    = fields.Many2one('hr.employee', string='Empleado', readonly=True)
    entry_date     = fields.Date(string='Fecha Ingreso', readonly=True)
    cutoff_date    = fields.Date(string='Fecha de Corte', readonly=True)
    current_balance = fields.Float(string='Saldo Actual', readonly=True, digits=(6, 1))
    correct_balance = fields.Float(string='Saldo Correcto (Art.153 CT)', readonly=True, digits=(6, 1))
    difference      = fields.Float(
        string='Diferencia', compute='_compute_difference',
        digits=(6, 1), readonly=True
    )
    apply          = fields.Boolean(string='Aplicar', default=True)

    @api.depends('current_balance', 'correct_balance')
    def _compute_difference(self):
        for rec in self:
            rec.difference = rec.correct_balance - rec.current_balance
