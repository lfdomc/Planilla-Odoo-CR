import io
import base64
from odoo import models, fields, api
from odoo.exceptions import UserError
import xlsxwriter


class PayrollReportWizard(models.TransientModel):
    _name = 'planilla.report.wizard'
    _description = 'Asistente de Reportes de Planilla'

    report_type = fields.Selection([
        ('monthly_summary', 'Resumen Mensual de Planilla'),
        ('ccss_report', 'Reporte CCSS (Planilla de Patrono)'),
        ('cost_by_branch', 'Costo por Sucursal'),
        ('employee_detail', 'Detalle por Empleado'),
    ], string='Tipo de Reporte', required=True, default='monthly_summary')

    date_from = fields.Date(string='Desde', required=True,
                            default=lambda self: fields.Date.context_today(self).replace(day=1))
    date_to = fields.Date(string='Hasta', required=True,
                          default=lambda self: fields.Date.context_today(self))
    branch_id = fields.Many2one('planilla.branch', string='Sucursal (Opcional)')
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        'res.currency',
        string='Moneda del Reporte',
        default=lambda self: self.env.company.currency_id,
        required=True,
        help='Moneda en que se mostrarán los montos del reporte. Los salarios en otras monedas serán convertidos.'
    )

    def action_generate_report(self):
        self.ensure_one()
        if self.report_type == 'monthly_summary':
            return self.env.ref('planilla_cr.action_report_monthly_summary').report_action(self)
        elif self.report_type == 'ccss_report':
            return self.env.ref('planilla_cr.action_report_ccss').report_action(self)
        elif self.report_type == 'cost_by_branch':
            return self.env.ref('planilla_cr.action_report_cost_branch').report_action(self)
        elif self.report_type == 'employee_detail':
            return self.env.ref('planilla_cr.action_report_employee_detail').report_action(self)

    def _get_payslips(self):
        # Overlap logic: finds payslips that overlap the selected period
        domain = [
            ('date_from', '<=', self.date_to),
            ('date_to', '>=', self.date_from),
            ('state', '=', 'done'),
            ('company_id', '=', self.company_id.id),
        ]
        if self.branch_id:
            domain.append(('branch_id', '=', self.branch_id.id))
        return self.env['planilla.payslip.cr'].search(domain)

    def _convert_amount(self, amount, slip):
        """Convert amount from slip currency to report currency."""
        report_currency = self.currency_id
        slip_currency = slip.currency_id or self.env.company.currency_id
        if slip_currency == report_currency:
            return amount
        return slip_currency._convert(
            amount, report_currency,
            self.company_id,
            self.date_to or fields.Date.context_today(self)
        )

    def action_export_excel(self):
        """Exporta el detalle completo de planilla a Excel con todas las columnas."""
        self.ensure_one()

        payslips = self._get_payslips()
        if not payslips:
            raise UserError('No hay boletas pagadas en el periodo seleccionado.')

        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True})
        ws = wb.add_worksheet('Planilla Detalle')

        # ── Formatos ────────────────────────────────────────────────
        hdr = wb.add_format({
            'bold': True, 'bg_color': '#1F4E79', 'font_color': 'white',
            'border': 1, 'align': 'center', 'valign': 'vcenter', 'text_wrap': True
        })
        money = wb.add_format({'num_format': '#,##0.00', 'border': 1})
        normal = wb.add_format({'border': 1})
        total_lbl = wb.add_format({
            'bold': True, 'bg_color': '#D9E1F2', 'border': 1
        })
        total_num = wb.add_format({
            'bold': True, 'bg_color': '#D9E1F2', 'num_format': '#,##0.00', 'border': 1
        })

        # ── Encabezado del reporte ───────────────────────────────────
        title_fmt = wb.add_format({'bold': True, 'font_size': 14, 'color': '#1F4E79'})
        ws.write(0, 0, 'PLANILLA DETALLADA — PLANILLA CR', title_fmt)
        ws.write(1, 0, f'Periodo: {self.date_from} al {self.date_to}')
        ws.write(2, 0, f'Empresa: {self.company_id.name}')
        ws.write(3, 0, f'Sucursal: {self.branch_id.name if self.branch_id else "Todas"}')

        # ── Columnas ────────────────────────────────────────────────
        columns = [
            ('Empleado', 28), ('Sucursal', 16), ('Cédula', 14),
            ('Salario Base (₡)', 16), ('H. Extras (₡)', 14), ('Vacaciones (₡)', 14),
            ('Otros Ingresos (₡)', 16), ('Salario Bruto (₡)', 16),
            ('CCSS Obrero (₡)', 14), ('Impuesto Renta (₡)', 15),
            ('Otras Ded. (₡)', 14), ('Total Ded. Obrero (₡)', 16),
            ('Salario Neto (₡)', 16),
            ('CCSS Patronal (₡)', 15), ('INS (₡)', 12),
            ('Prov. Aguinaldo (₡)', 16), ('Prov. Cesantía (₡)', 15),
            ('Prov. Vacaciones (₡)', 16), ('Costo Total Empresa (₡)', 18),
        ]
        row = 5
        for col, (name, width) in enumerate(columns):
            ws.write(row, col, name, hdr)
            ws.set_column(col, col, width)
        ws.set_row(row, 30)

        # ── Datos por boleta ─────────────────────────────────────────
        row = 6
        totals = [0.0] * len(columns)
        for slip in payslips.sorted(key=lambda s: s.employee_id.name):
            c = self._convert_amount
            data = [
                slip.employee_id.name,
                slip.branch_id.name or '',
                slip.employee_id.identification_id or '',
                c(slip.base_salary, slip),
                c(slip.overtime_amount, slip),
                c(slip.vacation_amount, slip),
                c(slip.other_income, slip),
                c(slip.gross_salary, slip),
                c(slip.ccss_employee, slip),
                c(slip.income_tax, slip),
                c(slip.other_deductions, slip),
                c(slip.total_employee_deductions, slip),
                c(slip.net_salary, slip),
                c(slip.ccss_employer, slip),
                c(slip.ins_employer, slip),
                c(slip.aguinaldo_provision, slip),
                c(slip.cesantia_provision, slip),
                c(slip.vacation_provision, slip),
                c(slip.total_employer_cost, slip),
            ]
            for col, val in enumerate(data):
                if isinstance(val, float):
                    ws.write(row, col, val, money)
                    totals[col] = totals[col] + val
                else:
                    ws.write(row, col, val, normal)
            row += 1

        # ── Fila de totales ─────────────────────────────────────────
        ws.write(row, 0, 'TOTALES', total_lbl)
        ws.write(row, 1, '', total_lbl)
        ws.write(row, 2, '', total_lbl)
        for col in range(3, len(columns)):
            ws.write(row, col, totals[col], total_num)

        wb.close()
        xlsx_data = base64.b64encode(output.getvalue()).decode()
        filename = f'Planilla_{self.date_from}_{self.date_to}.xlsx'

        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': xlsx_data,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }

    def _get_report_data(self):
        payslips = self._get_payslips()

        def total_field(field):
            return sum(self._convert_amount(getattr(s, field) or 0.0, s) for s in payslips)

        return {
            'wizard': self,
            'payslips': payslips,
            'currency': self.currency_id,
            'total_gross': total_field('gross_salary'),
            'total_net': total_field('net_salary'),
            'total_ccss_employee': total_field('ccss_employee'),
            'total_ccss_employer': total_field('ccss_employer'),
            'total_ins': total_field('ins_employer'),
            'total_income_tax': total_field('income_tax'),
            'total_aguinaldo': total_field('aguinaldo_provision'),
            'total_cesantia': total_field('cesantia_provision'),
            'total_vacation': total_field('vacation_provision'),
            'total_employer_cost': total_field('total_employer_cost'),
            'employee_count': len(payslips.mapped('employee_id')),
        }
