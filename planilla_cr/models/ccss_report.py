import io
import base64
from odoo import models, fields, api
from odoo.exceptions import UserError
import xlsxwriter


class CcssReport(models.TransientModel):
    _name = 'planilla.ccss.report'
    _description = 'Reporte Planilla CCSS - Patrono'

    company_id = fields.Many2one('res.company', required=True,
                                  default=lambda self: self.env.company)
    date_from = fields.Date(string='Desde', required=True)
    date_to = fields.Date(string='Hasta', required=True)
    branch_id = fields.Many2one('planilla.branch', string='Sucursal')
    report_format = fields.Selection([
        ('pdf', 'PDF'),
        ('excel', 'Excel'),
        ('sicere', 'Archivo SICERE (.txt)'),
    ], string='Formato', required=True, default='pdf')

    # Numero de patrono CCSS (requerido para SICERE)
    patron_number = fields.Char(
        string='Numero de Patrono CCSS',
        help='Numero de patrono asignado por la CCSS (requerido para generar archivo SICERE)'
    )



    def _get_payslips(self):
        domain = [
            ('date_from', '<=', self.date_to),
            ('date_to', '>=', self.date_from),
            ('state', '=', 'done'),
            ('company_id', '=', self.company_id.id),
        ]
        if self.branch_id:
            domain.append(('branch_id', '=', self.branch_id.id))
        payslips = self.env['planilla.payslip.cr'].search(domain)
        # FIX M-05 v59: Prefetch de campos de empleado para eliminar N+1.
        # Sin esto, cada iteración del loop genera queries individuales.
        if payslips:
            payslips.mapped('employee_id.identification_id')
            payslips.mapped('employee_id.name')
            payslips.mapped('employee_id.ccss_number')
            payslips.mapped('employee_id.branch_id.name')
            payslips.mapped('employee_id.base_salary')
        return payslips

    def action_generate(self):
        self.ensure_one()
        if self.report_format == 'pdf':
            return self.env.ref('planilla_cr.action_report_ccss').report_action(self)
        elif self.report_format == 'sicere':
            return self.action_generate_sicere()
        return self.action_generate_excel()

    def get_report_data(self):
        """Devuelve datos estructurados para el reporte PDF y Excel."""
        payslips = self._get_payslips()
        # Leer tasas desde RateHelper (fuente única — Configuración → Códigos de Deducción)
        rh = self.env['planilla.rate.helper']
        tasa_obrero = rh.get_ccss_employee_rate()
        tasa_patronal = rh.get_ccss_employer_rate()

        rows = []
        total_bruto = 0.0
        total_obrero = 0.0
        total_patronal = 0.0

        for ps in payslips:
            emp = ps.employee_id
            bruto = ps.gross_salary
            # Usar monto real de la boleta si está disponible (más preciso)
            obrero   = ps.ccss_employee if ps.ccss_employee else round(bruto * tasa_obrero, 2)
            patronal = ps.ccss_employer if ps.ccss_employer else round(bruto * tasa_patronal, 2)
            total_bruto += bruto
            total_obrero += obrero
            total_patronal += patronal

            rows.append({
                'cedula': emp.identification_id or '',
                'nombre': emp.name or '',
                'salario_bruto': bruto,
                'cuota_obrero': obrero,
                'cuota_patronal': patronal,
                'total_cuota': obrero + patronal,
                'fecha_ingreso': emp.entry_date.strftime('%d/%m/%Y') if emp.entry_date else '',
            })

        return {
            'rows': rows,
            'total_bruto': total_bruto,
            'total_obrero': total_obrero,
            'total_patronal': total_patronal,
            'total_cuota': total_obrero + total_patronal,
            'company': self.company_id,
            'date_from': self.date_from,
            'date_to': self.date_to,
            'tasa_obrero': tasa_obrero * 100,
            'tasa_patronal': tasa_patronal * 100,
        }

    def action_generate_excel(self):
        self.ensure_one()
        self.ensure_one()

        data = self.get_report_data()
        if not data['rows']:
            raise UserError('No hay boletas aprobadas en el periodo seleccionado.')

        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True})
        ws = wb.add_worksheet('Planilla CCSS')

        # ── Formatos ─────────────────────────────────────────────
        ft = wb.add_format({'bold': True, 'font_size': 14, 'font_color': '#FFFFFF',
                            'bg_color': '#1A5276', 'align': 'center', 'border': 1})
        fs = wb.add_format({'bold': True, 'font_size': 11, 'font_color': '#1A5276', 'align': 'center'})
        fi = wb.add_format({'font_size': 10, 'font_color': '#555555'})
        fh = wb.add_format({'bold': True, 'font_color': '#FFFFFF', 'bg_color': '#2E86C1',
                            'border': 1, 'align': 'center', 'text_wrap': True})
        fc = wb.add_format({'border': 1, 'font_size': 10})
        fcc = wb.add_format({'border': 1, 'font_size': 10, 'align': 'center'})
        fm = wb.add_format({'border': 1, 'font_size': 10, 'num_format': '#,##0.00', 'align': 'right'})
        ftl = wb.add_format({'bold': True, 'bg_color': '#1A5276', 'font_color': '#FFFFFF',
                             'border': 1, 'align': 'right'})
        ftm = wb.add_format({'bold': True, 'bg_color': '#1A5276', 'font_color': '#FFFFFF',
                             'border': 1, 'num_format': '#,##0.00', 'align': 'right'})

        # ── Encabezado ────────────────────────────────────────────
        ws.merge_range('A1:G1', 'PLANILLA CCSS - CUOTAS DE PATRONO', ft)
        ws.merge_range('A2:G2', data['company'].name, fs)
        ws.write('A3', 'Periodo:', fi)
        ws.merge_range('B3:C3', f"{data['date_from']} al {data['date_to']}", fi)
        ws.write('D3', f"Tasa Obrero: {data['tasa_obrero']:.2f}%", fi)
        ws.write('E3', f"Tasa Patronal: {data['tasa_patronal']:.2f}%", fi)

        # ── Cabeceras ─────────────────────────────────────────────
        headers = ['#', 'Cedula', 'Nombre Empleado', 'Salario Bruto',
                   'Cuota Obrero (10.83%)', 'Cuota Patronal (26.83%)', 'Total Cuota']
        widths = [4, 14, 30, 16, 20, 20, 16]
        for c, (h, w) in enumerate(zip(headers, widths)):
            ws.write(4, c, h, fh)
            ws.set_column(c, c, w)
        ws.set_row(4, 35)

        # ── Datos ─────────────────────────────────────────────────
        row = 5
        for i, r in enumerate(data['rows']):
            bg = '#EBF5FB' if i % 2 == 0 else '#FFFFFF'
            fce = wb.add_format({'border': 1, 'bg_color': bg, 'font_size': 10})
            fcce = wb.add_format({'border': 1, 'bg_color': bg, 'align': 'center', 'font_size': 10})
            fme = wb.add_format({'border': 1, 'bg_color': bg, 'num_format': '#,##0.00',
                                 'align': 'right', 'font_size': 10})
            ws.write(row, 0, i + 1, fcce)
            ws.write(row, 1, r['cedula'], fcce)
            ws.write(row, 2, r['nombre'], fce)
            ws.write(row, 3, r['salario_bruto'], fme)
            ws.write(row, 4, r['cuota_obrero'], fme)
            ws.write(row, 5, r['cuota_patronal'], fme)
            ws.write(row, 6, r['total_cuota'], fme)
            row += 1

        # ── Totales ───────────────────────────────────────────────
        ws.merge_range(row, 0, row, 2, 'TOTALES', ftl)
        ws.write(row, 3, data['total_bruto'], ftm)
        ws.write(row, 4, data['total_obrero'], ftm)
        ws.write(row, 5, data['total_patronal'], ftm)
        ws.write(row, 6, data['total_cuota'], ftm)

        # ── Resumen debajo ────────────────────────────────────────
        row += 2
        fr = wb.add_format({'bold': True, 'font_size': 10, 'border': 1,
                            'bg_color': '#D6EAF8', 'align': 'center'})
        frv = wb.add_format({'font_size': 10, 'border': 1, 'num_format': '#,##0.00',
                             'align': 'right', 'bg_color': '#EBF5FB'})
        ws.merge_range(row, 0, row, 6, 'RESUMEN DE CUOTAS', fr)
        row += 1
        ws.write(row, 0, 'Total Salarios Brutos:', fr)
        ws.merge_range(row, 1, row, 2, data['total_bruto'], frv)
        ws.write(row, 3, 'Total Cuota Obrero:', fr)
        ws.merge_range(row, 4, row, 4, data['total_obrero'], frv)
        ws.write(row, 5, 'Total Cuota Patronal:', fr)
        ws.merge_range(row, 6, row, 6, data['total_patronal'], frv)
        row += 1
        ws.merge_range(row, 0, row, 4,
            'TOTAL A PAGAR CCSS (Obrero + Patronal):', fr)
        ws.merge_range(row, 5, row, 6, data['total_cuota'], frv)

        row += 2
        ws.merge_range(row, 0, row, 6,
            f"Presentar planilla CCSS antes del dia 15 de cada mes en la plataforma Sicere (sicere.ccss.sa.cr)",
            wb.add_format({'font_size': 8, 'font_color': '#888888', 'italic': True}))

        wb.close()
        output.seek(0)
        filename = f'Planilla_CCSS_{self.date_from}_{self.date_to}.xlsx'
        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': base64.b64encode(output.read()),
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }

    def action_generate_sicere(self):
        """
        Genera el archivo de texto para SICERE (planilla electronica CCSS).
        Formato: posicional de longitud fija segun especificacion CCSS Costa Rica.

        Registro tipo 1 (Encabezado patrono):
          01-02: Tipo registro "01"
          03-12: Numero patrono (10 digitos)
          13-18: Periodo AAMM (anio-mes)
          19-20: Tipo planilla "01"=ordinaria

        Registro tipo 2 (Detalle trabajador):
          01-02: Tipo registro "02"
          03-12: Cedula trabajador (10 digitos, ceros a la izquierda)
          13-14: Tipo cedula "01"=fisica, "02"=juridica, "03"=extranjero
          15-28: Salario bruto (14 digitos, 2 decimales implicitos, sin punto)
          29-30: Dias laborados (2 digitos)
          31-32: Tipo trabajador "01"=ordinario
        """
        self.ensure_one()
        if not self.patron_number:
            raise UserError(
                'Debe ingresar el Numero de Patrono CCSS para generar el archivo SICERE.'
            )

        payslips = self._get_payslips()
        if not payslips:
            raise UserError('No hay boletas aprobadas en el periodo seleccionado.')

        lines = []

        # ── Registro tipo 01: Encabezado ──────────────────────────────
        patron = str(self.patron_number).replace('-', '').replace(' ', '').rjust(10, '0')[:10]
        periodo = self.date_from.strftime('%Y%m')  # AAAAMM
        r1 = f'01{patron}{periodo}01'
        lines.append(r1)

        # ── Registros tipo 02: Un registro por empleado ───────────────
        errors = []
        total_bruto = 0.0

        for ps in payslips:
            emp = ps.employee_id
            id_num = (emp.identification_id or '').replace('-', '').replace(' ', '')

            if not id_num:
                errors.append(f'{emp.name}: sin cedula/identificacion')
                continue

            # Tipo de cedula segun tipo INS
            ins_id_type = getattr(emp, 'ins_id_type', '01') or '01'
            if ins_id_type == '01':
                tipo_cedula = '01'  # Fisica CR
            elif ins_id_type in ('02', '03'):
                tipo_cedula = '03'  # Extranjero
            else:
                tipo_cedula = '01'

            # Cedula formateada a 10 digitos
            cedula = id_num.rjust(10, '0')[:10]

            # Salario bruto: 14 digitos con 2 decimales implicitos (sin punto decimal)
            # Ej: 500000.00 -> "00000050000000"
            bruto_centimos = int(round(ps.gross_salary * 100))
            salario_fmt = str(bruto_centimos).rjust(14, '0')[:14]
            total_bruto += ps.gross_salary

            # Dias laborados: calcular segun periodo
            delta = (ps.date_to - ps.date_from).days + 1
            dias = min(delta, 30)  # CCSS usa maximo 30 dias
            dias_fmt = str(dias).rjust(2, '0')

            r2 = f'02{cedula}{tipo_cedula}{salario_fmt}{dias_fmt}01'
            lines.append(r2)

        if not lines or len(lines) <= 1:
            raise UserError(
                'No se generaron registros de empleados. Errores:\n' +
                '\n'.join(errors)
            )

        # ── Registro tipo 09: Total/Cierre ────────────────────────────
        total_centimos = int(round(total_bruto * 100))
        total_fmt = str(total_centimos).rjust(14, '0')[:14]
        num_trabajadores = str(len(lines) - 1).rjust(6, '0')  # excluir encabezado
        r9 = f'09{num_trabajadores}{total_fmt}'
        lines.append(r9)

        content = '\r\n'.join(lines) + '\r\n'
        if errors:
            content = '** Empleados omitidos por datos incompletos:\r\n'
            content += '\r\n'.join(f'** {e}' for e in errors) + '\r\n\r\n' + content

        filename = f'SICERE_{self.patron_number}_{self.date_from.strftime("%Y%m")}.txt'
        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': base64.b64encode(content.encode('latin-1', errors='replace')),
            'mimetype': 'text/plain',
        })
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }
