from odoo import models, fields, api
from odoo.exceptions import UserError
import io
import base64
import xlsxwriter
from datetime import date


class EmployerCostReport(models.TransientModel):
    _name = 'planilla.employer.cost.report'
    _description = 'Reporte de Costos Patronales Consolidado'

    company_id = fields.Many2one('res.company', required=True,
                                  default=lambda self: self.env.company)
    date_from = fields.Date(string='Desde', required=True)
    date_to   = fields.Date(string='Hasta',  required=True)
    branch_id = fields.Many2one('planilla.branch', string='Sucursal (Opcional)')
    group_by  = fields.Selection([
        ('branch',    'Por Sucursal'),
        ('employee',  'Por Empleado'),
        ('month',     'Por Mes'),
    ], string='Agrupar por', default='branch', required=True)

    def _get_payslips(self):
        domain = [
            ('state', '=', 'paid'),
            ('company_id', '=', self.company_id.id),
            ('date_from', '>=', self.date_from),
            ('date_to',   '<=', self.date_to),
        ]
        if self.branch_id:
            domain.append(('employee_id.branch_id', '=', self.branch_id.id))
        return self.env['planilla.payslip.cr'].search(domain, order='date_from, employee_id')

    def _build_report_data(self):
        payslips = self._get_payslips()
        if not payslips:
            raise UserError('No hay boletas pagadas en el período y filtros seleccionados.')

        rows = []
        totals = {k: 0.0 for k in (
            'gross', 'ccss_patronal', 'ins', 'aguinaldo',
            'cesantia', 'vacaciones', 'total_cost'
        )}

        for ps in payslips:
            branch = ps.employee_id.branch_id.name if ps.employee_id.branch_id else 'Sin Sucursal'
            month  = ps.date_from.strftime('%Y-%m') if ps.date_from else ''
            row = {
                'employee':    ps.employee_id.name,
                'branch':      branch,
                'month':       month,
                'period':      f"{ps.date_from} — {ps.date_to}",
                'gross':       ps.gross_salary or 0.0,
                'ccss_patronal': ps.ccss_employer or 0.0,
                'ins':           ps.ins_employer or 0.0,
                'aguinaldo':     ps.aguinaldo_provision or 0.0,
                'cesantia':      ps.cesantia_provision or 0.0,
                'vacaciones':    ps.vacation_provision or 0.0,
            }
            row['total_cost'] = sum(row[k] for k in (
                'gross', 'ccss_patronal', 'ins', 'aguinaldo', 'cesantia', 'vacaciones'
            ))
            rows.append(row)
            for k in totals:
                totals[k] += row[k]

        # Agrupar según selección
        grouped = {}
        for row in rows:
            key = row[self.group_by] if self.group_by in ('branch', 'month') else row['employee']
            if key not in grouped:
                grouped[key] = {k: 0.0 for k in totals}
                grouped[key]['label'] = key
                grouped[key]['detail_rows'] = []
            for k in totals:
                grouped[key][k] += row[k]
            grouped[key]['detail_rows'].append(row)

        return {
            'wizard':   self,
            'groups':   list(grouped.values()),
            'totals':   totals,
            'payslips': payslips,
        }

    def action_generate_pdf(self):
        self.ensure_one()
        return self.env.ref(
            'planilla_cr.action_report_employer_cost'
        ).report_action(self)

    def action_export_excel(self):
        self.ensure_one()

        data   = self._build_report_data()
        output = io.BytesIO()
        wb     = xlsxwriter.Workbook(output, {'in_memory': True})
        ws     = wb.add_worksheet('Costos Patronales')

        hdr   = wb.add_format({'bold': True, 'bg_color': '#1F4E79', 'font_color': 'white', 'border': 1})
        title = wb.add_format({'bold': True, 'font_size': 13, 'color': '#1F4E79'})
        money = wb.add_format({'border': 1, 'num_format': '#,##0.00'})
        norm  = wb.add_format({'border': 1})
        total = wb.add_format({'bold': True, 'border': 2, 'bg_color': '#D9E1F2', 'num_format': '#,##0.00'})
        grp   = wb.add_format({'bold': True, 'bg_color': '#BDD7EE', 'border': 1})

        ws.write(0, 0, 'REPORTE DE COSTOS PATRONALES CONSOLIDADO', title)
        ws.write(1, 0, f"Empresa: {self.company_id.name}")
        ws.write(2, 0, f"Período: {self.date_from} al {self.date_to}")
        ws.write(3, 0, f"Sucursal: {self.branch_id.name if self.branch_id else 'Todas'}")

        COLS = [
            ('Grupo / Empleado', 28), ('Salario Bruto', 16),
            ('CCSS Patronal', 15), ('INS', 13),
            ('Aguinaldo Prov.', 15), ('Cesantía Prov.', 15),
            ('Vacaciones Prov.', 16), ('COSTO TOTAL (₡)', 17),
        ]
        row = 5
        for col, (name, width) in enumerate(COLS):
            ws.write(row, col, name, hdr)
            ws.set_column(col, col, width)
        ws.set_row(row, 20)

        for group in data['groups']:
            row += 1
            ws.write(row, 0, group['label'], grp)
            for col, key in enumerate(['gross','ccss_patronal','ins','aguinaldo','cesantia','vacaciones','total_cost'], 1):
                ws.write(row, col, group[key], grp)

            if self.group_by == 'branch':
                for detail in group['detail_rows']:
                    row += 1
                    ws.write(row, 0, f"   {detail['employee']}", norm)
                    for col, key in enumerate(['gross','ccss_patronal','ins','aguinaldo','cesantia','vacaciones','total_cost'], 1):
                        ws.write(row, col, detail[key], money)

        row += 2
        ws.write(row, 0, 'TOTAL GENERAL', total)
        for col, key in enumerate(['gross','ccss_patronal','ins','aguinaldo','cesantia','vacaciones','total_cost'], 1):
            ws.write(row, col, data['totals'][key], total)

        wb.close()
        fname = f"CostosPatronales_{self.date_from}_{self.date_to}.xlsx"
        att = self.env['ir.attachment'].create({
            'name': fname, 'type': 'binary',
            'datas': base64.b64encode(output.getvalue()),
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })
        return {'type': 'ir.actions.act_url', 'url': f'/web/content/{att.id}?download=true', 'target': 'self'}
