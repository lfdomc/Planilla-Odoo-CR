from datetime import date
from odoo import models, fields, api


class VacationBalanceWizard(models.TransientModel):
    _name = 'planilla.vacation.balance.wizard'
    _description = 'Reporte de Saldo de Vacaciones'

    company_id = fields.Many2one('res.company', required=True,
                                  default=lambda self: self.env.company)
    branch_id  = fields.Many2one('planilla.branch', string='Sucursal (Opcional)')
    include_inactive = fields.Boolean(
        string='Incluir empleados inactivos',
        default=False,
        help='Marcar para incluir empleados que ya salieron de la empresa.'
    )

    def action_generate_pdf(self):
        self.ensure_one()
        return self.env.ref('planilla_cr.action_report_vacation_balance').report_action(self)

    def action_export_excel(self):
        self.ensure_one()
        import io, base64
        try:
            import xlsxwriter
        except ImportError:
            from odoo.exceptions import UserError
            raise UserError('xlsxwriter no está instalado.')

        rows = self._get_vacation_report_data()
        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True})
        ws = wb.add_worksheet('Saldo Vacaciones')

        hdr = wb.add_format({'bold': True, 'bg_color': '#1F4E79', 'font_color': 'white', 'border': 1})
        normal = wb.add_format({'border': 1})
        neg_fmt = wb.add_format({'border': 1, 'font_color': '#C0392B', 'bold': True, 'bg_color': '#FDE8E8'})
        num_fmt = wb.add_format({'border': 1, 'num_format': '#,##0.0'})
        neg_num = wb.add_format({'border': 1, 'num_format': '#,##0.0', 'font_color': '#C0392B', 'bold': True, 'bg_color': '#FDE8E8'})
        money = wb.add_format({'border': 1, 'num_format': '#,##0'})

        # Title
        title_fmt = wb.add_format({'bold': True, 'font_size': 13, 'color': '#1F4E79'})
        ws.write(0, 0, 'SALDO DE VACACIONES POR EMPLEADO', title_fmt)
        ws.write(1, 0, f'Empresa: {self.company_id.name}')
        ws.write(2, 0, f'Sucursal: {self.branch_id.name if self.branch_id else "Todas"}')
        ws.write(3, 0, f'Generado: {date.today().strftime("%d/%m/%Y")}')

        cols = [
            ('Empleado', 30), ('Sucursal', 16), ('Fecha Ingreso', 14),
            ('Antigüedad (años)', 16), ('Días Acumulados', 16),
            ('Días Tomados', 14), ('Saldo Disponible', 16), ('Provisión (₡)', 16),
        ]
        row = 5
        for col, (name, width) in enumerate(cols):
            ws.write(row, col, name, hdr)
            ws.set_column(col, col, width)
        ws.set_row(row, 22)

        for r in rows:
            row += 1
            is_neg = r['available'] < 0
            fmt = neg_fmt if is_neg else normal
            nfmt = neg_num if is_neg else num_fmt
            ws.write(row, 0, r['name'], fmt)
            ws.write(row, 1, r['branch'], fmt)
            ws.write(row, 2, r['entry_date'], fmt)
            ws.write(row, 3, r['years'], nfmt)
            ws.write(row, 4, r['accrued'], num_fmt)
            ws.write(row, 5, r['taken'], num_fmt)
            ws.write(row, 6, r['available'], nfmt)
            ws.write(row, 7, r['provision'], money)

        wb.close()
        filename = f'Saldo_Vacaciones_{date.today().strftime("%Y-%m")}.xlsx'
        att = self.env['ir.attachment'].create({
            'name': filename, 'type': 'binary',
            'datas': base64.b64encode(output.getvalue()),
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })
        return {'type': 'ir.actions.act_url', 'url': f'/web/content/{att.id}?download=true', 'target': 'self'}

    def _get_vacation_report_data(self):
        today = date.today()
        domain = [('company_id', '=', self.company_id.id)]
        if not self.include_inactive:
            domain.append(('active', '=', True))
        if self.branch_id:
            domain.append(('branch_id', '=', self.branch_id.id))

        employees = self.env['hr.employee'].search(domain, order='name')
        rows = []
        for emp in employees:
            if not emp.entry_date:
                continue
            accrued  = round(emp.vacation_days_accrued or 0.0, 1)
            taken    = round(emp.vacation_days_taken   or 0.0, 1)
            available = round(emp.vacation_days_available or 0.0, 1)
            years = round((today - emp.entry_date).days / 365.25, 1)
            daily = (emp.base_salary or 0.0) / 30.0
            provision = round(available * daily, 0) if available > 0 else 0.0
            rows.append({
                'name':       emp.name,
                'branch':     emp.branch_id.name if emp.branch_id else '',
                'entry_date': emp.entry_date.strftime('%d/%m/%Y'),
                'years':      years,
                'accrued':    accrued,
                'taken':      taken,
                'available':  available,
                'provision':  provision,
            })
        return rows
