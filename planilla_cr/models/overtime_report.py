from odoo import models, fields, api
from odoo.exceptions import UserError
import io
import base64
import xlsxwriter


class OvertimeConsolidatedReport(models.TransientModel):
    _name = 'planilla.overtime.consolidated.report'
    _description = 'Reporte Consolidado de Horas Extras'

    company_id  = fields.Many2one('res.company', required=True,
                                   default=lambda self: self.env.company)
    date_from   = fields.Date(string='Desde', required=True)
    date_to     = fields.Date(string='Hasta',  required=True)
    branch_id   = fields.Many2one('planilla.branch', string='Sucursal (Opcional)')
    group_by    = fields.Selection([
        ('employee', 'Por Empleado'),
        ('branch',   'Por Sucursal'),
        ('type',     'Por Tipo de Hora Extra'),
    ], string='Agrupar por', default='employee', required=True)

    def _get_data(self):
        domain = [
            ('state', '=', 'approved'),
            ('employee_id.company_id', '=', self.company_id.id),
            ('date', '>=', self.date_from),
            ('date', '<=', self.date_to),
        ]
        if self.branch_id:
            domain.append(('employee_id.branch_id', '=', self.branch_id.id))
        records = self.env['planilla.overtime'].search(domain, order='employee_id, date')
        if not records:
            raise UserError('No hay horas extras aprobadas en el periodo seleccionado.')

        type_labels = dict(records._fields['overtime_type'].selection)
        rows, totals = [], {'hours': 0.0, 'amount': 0.0, 'simple': 0.0, 'double': 0.0, 'holiday': 0.0}
        for r in records:
            row = {
                'employee': r.employee_id.name,
                'branch':   r.employee_id.branch_id.name or 'Sin Sucursal',
                'type':     type_labels.get(r.overtime_type, r.overtime_type),
                'type_key': r.overtime_type,
                'date':     str(r.date),
                'hours':    r.hours,
                'amount':   r.amount,
            }
            rows.append(row)
            totals['hours']  += r.hours
            totals['amount'] += r.amount
            totals[r.overtime_type if r.overtime_type in ('simple','double','holiday') else 'simple'] += r.hours

        grouped = {}
        key_field = self.group_by
        for row in rows:
            key = row.get(key_field if key_field != 'type' else 'type_key', row['employee'])
            if key not in grouped:
                grouped[key] = {'label': row['type'] if key_field == 'type' else row.get(key_field, key),
                                'hours': 0.0, 'amount': 0.0, 'rows': []}
            grouped[key]['hours']  += row['hours']
            grouped[key]['amount'] += row['amount']
            grouped[key]['rows'].append(row)

        return {'wizard': self, 'groups': list(grouped.values()), 'totals': totals}

    def action_generate_pdf(self):
        self.ensure_one()
        return self.env.ref('planilla_cr.action_report_overtime_consolidated').report_action(self)

    def action_export_excel(self):
        self.ensure_one()
        data = self._get_data()
        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True})
        ws = wb.add_worksheet('Horas Extras')
        hdr   = wb.add_format({'bold': True, 'bg_color': '#1F4E79', 'font_color': 'white', 'border': 1})
        grp   = wb.add_format({'bold': True, 'bg_color': '#BDD7EE', 'border': 1})
        money = wb.add_format({'border': 1, 'num_format': '#,##0.00'})
        norm  = wb.add_format({'border': 1})
        total = wb.add_format({'bold': True, 'bg_color': '#D9E1F2', 'border': 2, 'num_format': '#,##0.00'})
        title = wb.add_format({'bold': True, 'font_size': 13, 'color': '#1F4E79'})

        ws.write(0, 0, 'REPORTE CONSOLIDADO DE HORAS EXTRAS', title)
        ws.write(1, 0, f"Periodo: {self.date_from} al {self.date_to}")
        ws.write(2, 0, f"Empresa: {self.company_id.name} | Sucursal: {self.branch_id.name if self.branch_id else 'Todas'}")
        COLS = [('Empleado', 28), ('Sucursal', 18), ('Tipo', 15), ('Fecha', 12), ('Horas', 10), ('Monto (CRC)', 16)]
        row = 4
        for col, (nm, w) in enumerate(COLS):
            ws.write(row, col, nm, hdr); ws.set_column(col, col, w)
        for grp_data in data['groups']:
            row += 1
            ws.write(row, 0, grp_data['label'], grp)
            ws.write(row, 4, grp_data['hours'], grp)
            ws.write(row, 5, grp_data['amount'], grp)
            for d in grp_data['rows']:
                row += 1
                for col, val in enumerate([d['employee'], d['branch'], d['type'], d['date']]):
                    ws.write(row, col, val, norm)
                ws.write(row, 4, d['hours'], money)
                ws.write(row, 5, d['amount'], money)
        row += 2
        ws.write(row, 0, 'TOTAL', total)
        ws.write(row, 4, data['totals']['hours'], total)
        ws.write(row, 5, data['totals']['amount'], total)
        wb.close()
        fname = f"HorasExtras_{self.date_from}_{self.date_to}.xlsx"
        att = self.env['ir.attachment'].create({
            'name': fname, 'type': 'binary',
            'datas': base64.b64encode(output.getvalue()),
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })
        return {'type': 'ir.actions.act_url', 'url': f'/web/content/{att.id}download=true', 'target': 'self'}


class MtssExportWizard(models.TransientModel):
    _name = 'planilla.mtss.export'
    _description = 'Exportacion MTSS para Inspecciones'

    company_id  = fields.Many2one('res.company', required=True,
                                   default=lambda self: self.env.company)
    date_from   = fields.Date(string='Desde', required=True)
    date_to     = fields.Date(string='Hasta',  required=True)
    branch_id   = fields.Many2one('planilla.branch', string='Sucursal (Opcional)')
    include_inactive = fields.Boolean(string='Incluir empleados inactivos', default=False)

    def _get_employees(self):
        domain = [('company_id', '=', self.company_id.id)]
        if not self.include_inactive:
            domain.append(('active', '=', True))
        if self.branch_id:
            domain.append(('branch_id', '=', self.branch_id.id))
        return self.env['hr.employee'].search(domain, order='name')

    def action_export_excel(self):
        self.ensure_one()

        employees = self._get_employees()
        if not employees:
            raise UserError('No hay empleados con los filtros seleccionados.')

        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True})
        ws = wb.add_worksheet('Planilla MTSS')

        hdr   = wb.add_format({'bold': True, 'bg_color': '#1F4E79', 'font_color': 'white',
                                'border': 1, 'align': 'center', 'text_wrap': True})
        norm  = wb.add_format({'border': 1})
        money = wb.add_format({'border': 1, 'num_format': '#,##0.00'})
        title = wb.add_format({'bold': True, 'font_size': 12, 'color': '#1F4E79'})
        date_fmt = wb.add_format({'border': 1, 'num_format': 'yyyy-mm-dd'})

        ws.write(0, 0, 'PLANILLA PARA INSPECCION MINISTERIO DE TRABAJO (MTSS)', title)
        ws.write(1, 0, f"Empresa: {self.company_id.name} | Periodo: {self.date_from} al {self.date_to}")
        ws.write(2, 0, f"Sucursal: {self.branch_id.name if self.branch_id else 'Todas'}")

        COLS = [
            ('Ndeg', 5), ('Nombre Completo', 30), ('Cedula', 15),
            ('Tipo ID', 12), ('Jornada', 12), ('Fecha Ingreso', 14),
            ('Puesto', 20), ('Sucursal', 18), ('Salario Bruto (CRC)', 18),
            ('CCSS Ndeg', 15), ('Asegurado CCSS', 14), ('Estado', 10),
        ]
        row = 4
        ws.set_row(row, 30)
        for col, (nm, w) in enumerate(COLS):
            ws.write(row, col, nm, hdr)
            ws.set_column(col, col, w)

        for i, emp in enumerate(employees, 1):
            row += 1
            # Get latest paid payslip salary in period
            payslip = self.env['planilla.payslip.cr'].search([
                ('employee_id', '=', emp.id),
                ('state', '=', 'paid'),
                ('date_from', '>=', self.date_from),
                ('date_to', '<=', self.date_to),
            ], order='date_to desc', limit=1)
            salary = payslip.gross_salary if payslip else (emp.base_salary or 0.0)

            ws.write(row, 0,  i, norm)
            ws.write(row, 1,  emp.name or '', norm)
            ws.write(row, 2,  emp.identification_id or '', norm)
            ws.write(row, 3,  emp.identification_type_id.name if emp.identification_type_id else 'Cedula', norm)
            ws.write(row, 4,  emp.schedule_type_id.name if emp.schedule_type_id else '', norm)
            ws.write(row, 5,  str(emp.entry_date) if emp.entry_date else '', norm)
            ws.write(row, 6,  emp.job_title or emp.job_id.name if emp.job_id else '', norm)
            ws.write(row, 7,  emp.branch_id.name if emp.branch_id else '', norm)
            ws.write(row, 8,  salary, money)
            ws.write(row, 9,  emp.ccss_number or '', norm)
            ws.write(row, 10, 'Si' if emp.ccss_insured else 'No', norm)
            ws.write(row, 11, 'Activo' if emp.active else 'Inactivo', norm)

        wb.close()
        fname = f"PlanillaMTSS_{self.company_id.name}_{self.date_from}_{self.date_to}.xlsx"
        att = self.env['ir.attachment'].create({
            'name': fname, 'type': 'binary',
            'datas': base64.b64encode(output.getvalue()),
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })
        return {'type': 'ir.actions.act_url', 'url': f'/web/content/{att.id}download=true', 'target': 'self'}
