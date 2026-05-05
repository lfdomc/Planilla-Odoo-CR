import io
import base64
from odoo import models, fields, api
from odoo.exceptions import UserError
import xlsxwriter

INS_RISK_LABELS = {
    'I': 'Clase I', 'II': 'Clase II', 'III': 'Clase III',
    'IV': 'Clase IV', 'V': 'Clase V',
}


class InsReport(models.TransientModel):
    _name = 'planilla.ins.report'
    _description = 'Reporte Planilla INS - Riesgos del Trabajo'

    company_id = fields.Many2one('res.company', required=True,
                                  default=lambda self: self.env.company)
    payroll_run_ids = fields.Many2many(
        'planilla.run.cr',
        'planilla_ins_report_run_rel',
        'wizard_id', 'run_id',
        string='Planillas',
        domain=[('state', '=', 'done')],
        required=True,
    )
    date_from = fields.Date(compute='_compute_dates', store=False)
    date_to   = fields.Date(compute='_compute_dates', store=False)
    frequency = fields.Selection([
        ('monthly', 'Mensual'),
        ('biweekly', 'Quincenal'),
        ('weekly', 'Semanal'),
    ], string='Frecuencia de Planilla', required=True, default='monthly')
    branch_id = fields.Many2one('planilla.branch', string='Sucursal')

    @api.depends('payroll_run_ids')
    def _compute_dates(self):
        for rec in self:
            if rec.payroll_run_ids:
                rec.date_from = min(rec.payroll_run_ids.mapped('date_start'))
                rec.date_to   = max(rec.payroll_run_ids.mapped('date_end'))
            else:
                rec.date_from = rec.date_to = fields.Date.context_today(rec)

    def _get_payslips_and_employees(self):
        if not self.payroll_run_ids:
            empty = self.env['planilla.payslip.cr']
            return empty, self.env['hr.employee']
        slips = self.payroll_run_ids.mapped('payslip_ids').filtered(
            lambda s: s.state == 'done'
        )
        if self.branch_id:
            slips = slips.filtered(lambda s: s.branch_id == self.branch_id)
        employees = slips.mapped('employee_id').filtered(lambda e: e.ins_include)
        return slips, employees

    def action_generate_report(self):
        self.ensure_one()
        return self.env.ref('planilla_cr.action_report_ins').report_action(self)

    def action_generate_excel(self):
        self.ensure_one()

        payslips, employees = self._get_payslips_and_employees()

        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        ws = workbook.add_worksheet('Planilla INS')

        # -- Formatos ----------------------------------------------
        fmt_title = workbook.add_format({
            'bold': True, 'font_size': 14, 'font_color': '#FFFFFF',
            'bg_color': '#1F4E79', 'align': 'center', 'valign': 'vcenter', 'border': 1
        })
        fmt_subtitle = workbook.add_format({
            'bold': True, 'font_size': 11, 'font_color': '#1F4E79', 'align': 'center'
        })
        fmt_header = workbook.add_format({
            'bold': True, 'font_color': '#FFFFFF', 'bg_color': '#2E75B6',
            'border': 1, 'align': 'center', 'valign': 'vcenter', 'text_wrap': True
        })
        fmt_cell = workbook.add_format({'border': 1, 'valign': 'vcenter', 'font_size': 10})
        fmt_cell_center = workbook.add_format({
            'border': 1, 'valign': 'vcenter', 'align': 'center', 'font_size': 10
        })
        fmt_money = workbook.add_format({
            'border': 1, 'num_format': '#,##0.00', 'align': 'right',
            'valign': 'vcenter', 'font_size': 10
        })
        fmt_pct = workbook.add_format({
            'border': 1, 'num_format': '0.00"%"', 'align': 'center',
            'valign': 'vcenter', 'font_size': 10
        })
        fmt_total_label = workbook.add_format({
            'bold': True, 'bg_color': '#1F4E79', 'font_color': '#FFFFFF',
            'border': 1, 'align': 'right'
        })
        fmt_total_money = workbook.add_format({
            'bold': True, 'bg_color': '#1F4E79', 'font_color': '#FFFFFF',
            'border': 1, 'num_format': '#,##0.00', 'align': 'right'
        })
        fmt_info = workbook.add_format({'font_size': 10, 'font_color': '#555555'})
        fmt_note = workbook.add_format({'font_size': 8, 'font_color': '#888888', 'italic': True})

        # -- Encabezado --------------------------------------------
        freq_label = {'monthly': 'Mensual', 'biweekly': 'Quincenal', 'weekly': 'Semanal'}
        ws.merge_range('A1:N1', 'PLANILLA INS - RIESGOS DEL TRABAJO', fmt_title)
        ws.merge_range('A2:N2', self.company_id.name, fmt_subtitle)
        ws.write('A3', 'Periodo:', fmt_info)
        ws.merge_range('B3:D3', f'{self.date_from} al {self.date_to}', fmt_info)
        ws.write('E3', 'Frecuencia:', fmt_info)
        ws.write('F3', freq_label.get(self.frequency, ''), fmt_info)

        # -- Cabecera de columnas ----------------------------------
        headers = [
            '#', 'Nombre', 'Primer Apellido', 'Segundo Apellido',
            'Identificacion', 'Tipo ID', 'Nacionalidad', 'Estado Civil',
            'Tipo Jornada', 'Ocupacion', 'Clase Riesgo', 'Tasa INS (%)',
            'Salario Periodo', 'Prima INS'
        ]
        col_widths = [4, 18, 18, 18, 14, 12, 14, 12, 14, 35, 12, 12, 15, 14]
        for col, (h, w) in enumerate(zip(headers, col_widths)):
            ws.write(4, col, h, fmt_header)
            ws.set_column(col, col, w)
        ws.set_row(4, 30)

        # -- Catalogos para labels ---------------------------------
        id_types = {
            '01': 'Cedula CR', '02': 'Residencia', '03': 'Permiso',
            '04': 'Pasaporte', '05': 'Indocumentado'
        }
        civil = {
            '01': 'Soltero/a', '02': 'Casado/a', '03': 'Divorciado/a',
            '04': 'Viudo/a', '05': 'Union Libre', '06': 'Separado/a'
        }
        jornada = {
            '01': 'Ordinaria', '02': 'Extraordinaria', '03': 'Mixta',
            '04': 'Tiempo Parcial', '05': 'Por Horas', '06': 'Ocasional'
        }
        nationality_selection = dict(
            self.env['hr.employee']._fields['ins_nationality'].selection
        ) if employees else {}
        occ_selection = dict(
            self.env['hr.employee']._fields['ins_occupation'].selection
        ) if employees else {}

        # -- Filas de empleados ------------------------------------
        row = 5
        total_salary = 0.0
        total_prima = 0.0

        for i, emp in enumerate(employees):
            # Leer directo del slip — ins_employer ya tiene la prima correcta
            # calculada en el modelo (base_cotizable_final × tasa_riesgo).
            emp_payslips = payslips.filtered(lambda p: p.employee_id.id == emp.id)
            emp_salary   = sum(emp_payslips.mapped('gross_salary'))
            emp_prima    = sum(emp_payslips.mapped('ins_employer'))

            risk     = emp.ins_risk_class or 'II'
            ins_rate = self.env['planilla.rate.helper'].get_ins_rate(risk)

            total_salary += emp_salary
            total_prima  += emp_prima

            occ_label = occ_selection.get(emp.ins_occupation, emp.ins_occupation or '')

            bg = '#F2F7FC' if i % 2 == 0 else '#FFFFFF'
            fmt_c  = workbook.add_format({'border': 1, 'bg_color': bg, 'font_size': 10})
            fmt_cc = workbook.add_format({'border': 1, 'bg_color': bg, 'align': 'center', 'font_size': 10})
            fmt_m  = workbook.add_format({'border': 1, 'bg_color': bg, 'num_format': '#,##0.00', 'align': 'right', 'font_size': 10})
            fmt_p  = workbook.add_format({'border': 1, 'bg_color': bg, 'num_format': '0.00"%"', 'align': 'center', 'font_size': 10})

            ws.write(row, 0,  i + 1,                                              fmt_cc)
            ws.write(row, 1,  emp.ins_first_name or emp.name or '',                fmt_c)
            ws.write(row, 2,  emp.ins_first_lastname or '',                        fmt_c)
            ws.write(row, 3,  emp.ins_second_lastname or '',                       fmt_c)
            ws.write(row, 4,  emp.identification_id or '',                         fmt_cc)
            ws.write(row, 5,  id_types.get(emp.ins_id_type, ''),                   fmt_cc)
            ws.write(row, 6,  nationality_selection.get(emp.ins_nationality, ''),  fmt_c)
            ws.write(row, 7,  civil.get(emp.ins_civil_status, ''),                 fmt_c)
            ws.write(row, 8,  jornada.get(emp.ins_workday_type, ''),               fmt_c)
            ws.write(row, 9,  occ_label,                                           fmt_c)
            ws.write(row, 10, INS_RISK_LABELS.get(risk, risk),                    fmt_cc)
            ws.write(row, 11, ins_rate * 100,                                      fmt_p)
            ws.write(row, 12, emp_salary,                                          fmt_m)
            ws.write(row, 13, emp_prima,                                           fmt_m)
            row += 1

        # -- Fila de totales ---------------------------------------
        ws.merge_range(row, 0, row, 11, 'TOTALES', fmt_total_label)
        ws.write(row, 12, total_salary, fmt_total_money)
        ws.write(row, 13, total_prima,  fmt_total_money)

        # -- Nota de tasas -----------------------------------------
        row += 2
        ws.merge_range(row, 0, row, 13,
            'Tasas INS (Ley N.deg 6727):  Clase I: 0.87%  |  Clase II: 1.49%  |  Clase III: 2.47%  |  Clase IV: 4.13%  |  Clase V: 6.88%',
            fmt_note)
        row += 1
        ws.merge_range(row, 0, row, 13,
            f'{self.company_id.name} | Planilla INS Riesgos del Trabajo | '
            f'Periodo: {self.date_from} al {self.date_to} | '
            'Presentar antes del dia 10 de cada mes via RT-Virtual (rtvirtual.grupoins.com)',
            fmt_note)

        workbook.close()
        output.seek(0)
        xlsx_data = base64.b64encode(output.read())

        filename = f'Planilla_INS_{self.date_from}_{self.date_to}.xlsx'
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
