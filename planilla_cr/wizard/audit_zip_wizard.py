from odoo import models, fields, api
from odoo.exceptions import UserError
import base64, io, zipfile


class AuditZipWizard(models.TransientModel):
    """Genera un ZIP con todos los documentos del período para auditoría externa."""
    _name = 'planilla.audit.zip.wizard'
    _description = 'Exportación ZIP para Auditoría'

    company_id     = fields.Many2one('res.company', required=True,
                                      default=lambda self: self.env.company)
    date_from      = fields.Date(string='Desde', required=True)
    date_to        = fields.Date(string='Hasta',  required=True)
    branch_id      = fields.Many2one('planilla.branch', string='Sucursal (Opcional)')
    include_payslips_pdf = fields.Boolean(string='Boletas individuales PDF', default=True)
    include_ccss         = fields.Boolean(string='Planilla CCSS / SICERE',   default=True)
    include_employer_cost = fields.Boolean(string='Costos Patronales Excel', default=True)
    include_mtss         = fields.Boolean(string='Planilla MTSS',            default=True)
    include_overtime     = fields.Boolean(string='Horas Extras Consolidado', default=False)

    def action_generate_zip(self):
        self.ensure_one()
        zip_buffer = io.BytesIO()
        file_count = 0

        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:

            # 1. Boletas PDF individuales
            if self.include_payslips_pdf:
                payslips = self.env['planilla.payslip.cr'].search([
                    ('company_id', '=', self.company_id.id),
                    ('state', '=', 'paid'),
                    ('date_from', '>=', self.date_from),
                    ('date_to',   '<=', self.date_to),
                ])
                if payslips:
                    report = self.env.ref('planilla_cr.action_report_payslip')
                    pdf_content, _ = self.env['ir.actions.report']._render_qweb_pdf(
                        report, payslips.ids
                    )
                    fname = f'Boletas_{self.date_from}_{self.date_to}.pdf'
                    zf.writestr(f'01_Boletas/{fname}', pdf_content)
                    file_count += 1

            # 2. Costos Patronales Excel
            if self.include_employer_cost:
                try:
                    cost_wiz = self.env['planilla.employer.cost.report'].create({
                        'company_id': self.company_id.id,
                        'date_from':  self.date_from,
                        'date_to':    self.date_to,
                        'branch_id':  self.branch_id.id if self.branch_id else False,
                        'group_by':   'branch',
                    })
                    import xlsxwriter
                    data = cost_wiz._build_report_data()
                    out = io.BytesIO()
                    wb = xlsxwriter.Workbook(out, {'in_memory': True})
                    ws = wb.add_worksheet('Costos')
                    hdr = wb.add_format({'bold': True, 'bg_color': '#1F4E79', 'font_color': 'white'})
                    ws.write(0, 0, f'Costos Patronales {self.date_from} al {self.date_to}', hdr)
                    for i, grp in enumerate(data['groups'], 1):
                        ws.write(i, 0, grp['label'])
                        ws.write(i, 1, grp['gross'])
                        ws.write(i, 2, grp['total_cost'])
                    wb.close()
                    zf.writestr(f'02_CostosPatronales/CostosPatronales_{self.date_from}_{self.date_to}.xlsx',
                                out.getvalue())
                    file_count += 1
                except Exception as e:
                    zf.writestr('02_CostosPatronales/ERROR.txt', str(e))

            # 3. Planilla MTSS Excel
            if self.include_mtss:
                try:
                    mtss_wiz = self.env['planilla.mtss.export'].create({
                        'company_id': self.company_id.id,
                        'date_from':  self.date_from,
                        'date_to':    self.date_to,
                        'branch_id':  self.branch_id.id if self.branch_id else False,
                    })
                    employees = mtss_wiz._get_employees()
                    out = io.BytesIO()
                    import xlsxwriter
                    wb = xlsxwriter.Workbook(out, {'in_memory': True})
                    ws = wb.add_worksheet('MTSS')
                    hdr = wb.add_format({'bold': True, 'bg_color': '#1F4E79', 'font_color': 'white'})
                    for col, nm in enumerate(['Nombre', 'Cédula', 'Jornada', 'Ingreso', 'Salario', 'CCSS']):
                        ws.write(0, col, nm, hdr)
                    for i, emp in enumerate(employees, 1):
                        ws.write(i, 0, emp.name or '')
                        ws.write(i, 1, emp.identification_id or '')
                        ws.write(i, 2, emp.schedule_type_id.name if emp.schedule_type_id else '')
                        ws.write(i, 3, str(emp.entry_date) if emp.entry_date else '')
                        ws.write(i, 4, emp.base_salary or 0)
                        ws.write(i, 5, emp.ccss_number or '')
                    wb.close()
                    zf.writestr(f'03_MTSS/PlanillaMTSS_{self.date_from}_{self.date_to}.xlsx',
                                out.getvalue())
                    file_count += 1
                except Exception as e:
                    zf.writestr('03_MTSS/ERROR.txt', str(e))

            # 4. README del ZIP
            readme = f"""AUDITORÍA PLANILLA CR
Empresa: {self.company_id.name}
Período: {self.date_from} al {self.date_to}
Sucursal: {self.branch_id.name if self.branch_id else 'Todas'}
Generado: {fields.Datetime.now()}
Archivos incluidos: {file_count}

Estructura:
  01_Boletas/    — Boletas de pago individuales en PDF
  02_CostosPatronales/ — CCSS patronal, INS y provisiones en Excel
  03_MTSS/       — Planilla para inspecciones MTSS en Excel
"""
            zf.writestr('README.txt', readme)

        if file_count == 0:
            raise UserError('No se encontraron datos para los filtros seleccionados.')

        zip_buffer.seek(0)
        fname = f'AuditoriaPlanilla_{self.company_id.name}_{self.date_from}_{self.date_to}.zip'
        att = self.env['ir.attachment'].create({
            'name': fname, 'type': 'binary',
            'datas': base64.b64encode(zip_buffer.getvalue()),
            'mimetype': 'application/zip',
        })
        return {'type': 'ir.actions.act_url', 'url': f'/web/content/{att.id}?download=true', 'target': 'self'}
