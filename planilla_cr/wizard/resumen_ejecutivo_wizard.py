import io
import base64
from odoo import models, fields, api
from odoo.exceptions import UserError


class ResumenEjecutivoWizard(models.TransientModel):
    _name = 'planilla.resumen.ejecutivo.wizard'
    _description = 'Resumen Ejecutivo de Planilla'

    company_id = fields.Many2one(
        'res.company', required=True,
        default=lambda self: self.env.company
    )
    payroll_run_id = fields.Many2one(
        'planilla.run.cr', string='Planilla', required=True,
        domain="[('company_id', '=', company_id)]"
    )
    elaborado_por = fields.Char(
        string='Elaborado por',
        default=lambda self: self.env.user.name
    )

    def action_generate(self):
        self.ensure_one()
        run = self.payroll_run_id
        if not run:
            raise UserError('Seleccione una planilla.')

        slips = self.env['planilla.payslip.cr'].search([
            ('payroll_run_id', '=', run.id),
            ('state', 'in', ['confirmed', 'done']),
        ])
        slips = slips.sorted(key=lambda s: (
            s.employee_id.department_id.name or '',
            s.employee_id.name or ''
        ))

        if not slips:
            raise UserError('No hay boletas confirmadas en esta planilla.')

        try:
            import xlsxwriter
        except ImportError:
            raise UserError('xlsxwriter no instalado.')

        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True})
        ws = wb.add_worksheet('Resumen Ejecutivo')

        # ── Formats ────────────────────────────────────────────────
        def fmt(**kw):
            return wb.add_format(kw)

        title_fmt = fmt(bold=True, font_size=12, align='center',
                        valign='vcenter', font_name='Arial')
        hdr_base = dict(bold=True, font_size=9, font_name='Arial',
                        align='center', valign='vcenter',
                        text_wrap=True, border=1)
        hdr_grp_ing = fmt(**{**hdr_base, 'bg_color': '#92D050',
                             'font_color': '#000000'})
        hdr_grp_reb = fmt(**{**hdr_base, 'bg_color': '#FF0000',
                             'font_color': '#FFFFFF'})
        hdr_col = fmt(**{**hdr_base, 'bg_color': '#D9D9D9'})
        hdr_col_ing = fmt(**{**hdr_base, 'bg_color': '#E2EFDA'})
        hdr_col_reb = fmt(**{**hdr_base, 'bg_color': '#FCE4D6'})
        hdr_col_tot = fmt(**{**hdr_base, 'bg_color': '#BDD7EE'})

        num_fmt = fmt(num_format='#,##0', border=1, font_size=9,
                      font_name='Arial')
        num_neg = fmt(num_format='#,##0', border=1, font_size=9,
                      font_name='Arial', font_color='#C00000')
        txt_fmt = fmt(border=1, font_size=9, font_name='Arial',
                      valign='vcenter')
        sub_fmt = fmt(bold=True, border=1, font_size=9, font_name='Arial',
                      bg_color='#F2F2F2', num_format='#,##0')
        sub_lbl = fmt(bold=True, border=1, font_size=9, font_name='Arial',
                      bg_color='#F2F2F2')
        tot_fmt = fmt(bold=True, border=2, font_size=9, font_name='Arial',
                      bg_color='#BDD7EE', num_format='#,##0')
        tot_lbl = fmt(bold=True, border=2, font_size=9, font_name='Arial',
                      bg_color='#BDD7EE')

        # ── Column widths ──────────────────────────────────────────
        # A=Nombre, B=Dept, C=SalQuinc, D=Otros, E=Extras, F=Subtotal
        # G=CCSS, H=IncapCCSS, I=AhorroNav, J=PermisoSinGoce,
        # K=ImpRenta, L=Otros, M=PrestInternos, N=Facturas, O=Maternidad
        # P=TotalGeneral
        widths = [32, 5, 12, 10, 10, 12, 10, 12, 10, 14, 10, 10, 12, 10, 10, 12]
        for i, w in enumerate(widths):
            ws.set_column(i, i, w)

        # ── Header rows ────────────────────────────────────────────
        ws.set_row(0, 18)
        ws.set_row(1, 14)
        ws.set_row(2, 14)
        ws.set_row(3, 14)

        empresa = run.company_id.name or ''
        periodo = f'{run.date_start.strftime("%d/%m/%Y")} AL {run.date_end.strftime("%d/%m/%Y")}' \
            if run.date_start and run.date_end else ''

        # Determine quincena label
        q_label = run.name or ''

        ws.merge_range('A1:P1', empresa, title_fmt)
        ws.merge_range('A2:P2', f'PLANILLA DEL {periodo}', title_fmt)
        ws.merge_range('A3:P3', q_label, title_fmt)
        ws.merge_range('A4:P4', f'Elaborado por {self.elaborado_por}', title_fmt)

        # ── Group headers row 5 ────────────────────────────────────
        ws.set_row(5, 22)
        ws.merge_range('A5:B5', '', hdr_col)
        ws.merge_range('C5:F5', 'INGRESOS', hdr_grp_ing)
        ws.merge_range('G5:O5', 'REBAJOS', hdr_grp_reb)
        ws.write('P5', '', hdr_col)

        # ── Column headers row 6 ──────────────────────────────────
        ws.set_row(6, 40)
        cols_labels = [
            ('A6', 'Nombre',                    hdr_col),
            ('B6', 'Dpto',                      hdr_col),
            ('C6', 'Salario\nQuincenal',         hdr_col_ing),
            ('D6', 'Otros',                     hdr_col_ing),
            ('E6', 'Extras',                    hdr_col_ing),
            ('F6', 'Sub total\nquincenal',       hdr_col_ing),
            ('G6', 'C.C.S.S.',                  hdr_col_reb),
            ('H6', 'Incapacidad\nC.C.S.S. INS', hdr_col_reb),
            ('I6', 'Ahorro\nNavideno',           hdr_col_reb),
            ('J6', 'Permiso sin\nGoce de Salario', hdr_col_reb),
            ('K6', 'Impuesto\nde Renta',         hdr_col_reb),
            ('L6', 'Otros',                     hdr_col_reb),
            ('M6', 'Prestamos\nInternos',        hdr_col_reb),
            ('N6', 'Facturas',                  hdr_col_reb),
            ('O6', 'Maternidad',                hdr_col_reb),
            ('P6', 'Total\nGeneral',             hdr_col_tot),
        ]
        for cell, label, f in cols_labels:
            ws.write(cell, label, f)

        # ── Data rows ─────────────────────────────────────────────
        row = 7  # 1-indexed row 7 = index 6
        dept_rows = {}  # dept_code -> list of row indices

        def get_deduction_amount(slip, category):
            lines = slip.deduction_line_ids.filtered(
                lambda l: l.deduction_category == category
                and l.line_type == 'deduction'
            )
            return sum(lines.mapped('amount'))

        def get_otros_ingresos(slip):
            lines = slip.deduction_line_ids.filtered(
                lambda l: l.line_type == 'income'
                and l.deduction_category not in ('overtime',)
            )
            return sum(lines.mapped('amount'))

        def get_otros_rebajos(slip):
            cat_mapped = {'ccss', 'loan', 'ahorro', 'maternity',
                          'ausencia', 'income_tax', 'facturas'}
            lines = slip.deduction_line_ids.filtered(
                lambda l: l.line_type == 'deduction'
                and l.deduction_category not in cat_mapped
            )
            return sum(lines.mapped('amount'))

        for slip in slips:
            emp = slip.employee_id
            dept = emp.department_id.name[:1] if emp.department_id else 'O'

            sal_quincenal = slip.base_salary or 0
            otros_ing     = get_otros_ingresos(slip)
            extras        = slip.overtime_amount or 0
            subtotal      = sal_quincenal + otros_ing + extras

            ccss          = slip.ccss_employee or 0
            # Incapacidad CCSS/INS = subsidio CCSS descontado del patrono
            incap_ccss    = slip.ccss_subsidy_total or 0
            ahorro_nav    = get_deduction_amount(slip, 'ahorro')
            # Permiso sin goce: licencias sin goce + ausencias
            permiso_sin   = ((slip.amount_licencias_sin_goce or 0) +
                             get_deduction_amount(slip, 'ausencia') +
                             get_deduction_amount(slip, 'licencia_sin_goce'))
            imp_renta     = slip.income_tax or 0
            # Otros: sindicatos, ROP, pension voluntaria, pension alimentaria, embargo
            otros_reb     = (get_deduction_amount(slip, 'sindical') +
                             get_deduction_amount(slip, 'rop') +
                             get_deduction_amount(slip, 'pension_vol') +
                             get_deduction_amount(slip, 'pension_alimentaria') +
                             get_deduction_amount(slip, 'embargo') +
                             get_deduction_amount(slip, 'other'))
            prestamos     = get_deduction_amount(slip, 'loan')
            facturas      = get_deduction_amount(slip, 'cooperativa')
            maternidad    = get_deduction_amount(slip, 'maternity')
            # Total General = Deposito Patrono (neto real que paga la empresa)
            # Es la fuente de verdad del sistema, equivale a ingresos - rebajos
            total_general = slip.deposito_patrono or slip.salary_payable or 0

            ws.write(row - 1, 0,  emp.name or '',              txt_fmt)
            ws.write(row - 1, 1,  dept,                         txt_fmt)
            ws.write(row - 1, 2,  sal_quincenal,                num_fmt)
            ws.write(row - 1, 3,  otros_ing if otros_ing else None,   num_fmt)
            ws.write(row - 1, 4,  extras    if extras    else None,   num_fmt)
            ws.write(row - 1, 5,  subtotal,                     num_fmt)
            ws.write(row - 1, 6,  ccss      if ccss      else None,   num_fmt)
            ws.write(row - 1, 7,  incap_ccss if incap_ccss else None, num_fmt)
            ws.write(row - 1, 8,  ahorro_nav if ahorro_nav else None, num_fmt)
            ws.write(row - 1, 9,  permiso_sin if permiso_sin else None, num_fmt)
            ws.write(row - 1, 10, imp_renta  if imp_renta  else None, num_fmt)
            ws.write(row - 1, 11, otros_reb  if otros_reb  else None, num_fmt)
            ws.write(row - 1, 12, prestamos  if prestamos  else None, num_fmt)
            ws.write(row - 1, 13, facturas   if facturas   else None, num_fmt)
            ws.write(row - 1, 14, maternidad if maternidad else None, num_fmt)
            ws.write(row - 1, 15, total_general,                num_fmt)

            dept_rows.setdefault(dept, []).append(row - 1)
            row += 1

        # ── Subtotals ─────────────────────────────────────────────
        def write_subtotal(label, rows_list):
            nonlocal row
            if not rows_list:
                return
            ws.write(row - 1, 0, label, sub_lbl)
            ws.write(row - 1, 1, '',    sub_lbl)
            for col in range(2, 16):
                refs = '+'.join(
                    f'{xlsxwriter.utility.xl_rowcol_to_cell(r, col)}'
                    for r in rows_list
                )
                ws.write(row - 1, col,
                         f'={refs}' if refs else 0, sub_fmt)
            # Total General subtotal
            r1 = xlsxwriter.utility.xl_rowcol_to_cell(row - 1, 2)
            r2 = xlsxwriter.utility.xl_rowcol_to_cell(row - 1, 4)
            r3 = xlsxwriter.utility.xl_rowcol_to_cell(row - 1, 6)
            r4 = xlsxwriter.utility.xl_rowcol_to_cell(row - 1, 14)
            refs_total = '+'.join(
                f'{xlsxwriter.utility.xl_rowcol_to_cell(r, 15)}'
                for r in rows_list
            )
            ws.write(row - 1, 15, f'={refs_total}' if refs_total else 0, sub_fmt)
            row += 1

        adm_rows = dept_rows.get('A', [])
        op_rows  = [r for k, v in dept_rows.items() if k != 'A' for r in v]
        all_rows = adm_rows + op_rows

        if adm_rows:
            write_subtotal('SUBTOTAL ADMINISTRATIVO', adm_rows)
        if op_rows:
            write_subtotal('SUBTOTAL OPERATIVO', op_rows)

        # ── Grand total ───────────────────────────────────────────
        ws.write(row - 1, 0, 'TOTALES', tot_lbl)
        ws.write(row - 1, 1, '', tot_lbl)
        for col in range(2, 16):
            refs = '+'.join(
                f'{xlsxwriter.utility.xl_rowcol_to_cell(r, col)}'
                for r in all_rows
            )
            ws.write(row - 1, col, f'={refs}' if refs else 0, tot_fmt)
        refs_t = '+'.join(
            f'{xlsxwriter.utility.xl_rowcol_to_cell(r, 15)}'
            for r in all_rows
        )
        ws.write(row - 1, 15, f'={refs_t}' if refs_t else 0, tot_fmt)

        # ── Freeze panes & print settings ────────────────────────
        ws.freeze_panes(6, 2)
        ws.set_landscape()
        ws.set_paper(9)  # A4
        ws.fit_to_pages(1, 0)
        ws.set_print_scale(85)
        ws.repeat_rows(4, 5)

        wb.close()

        filename = f'Resumen_Ejecutivo_{run.name or "planilla"}.xlsx'.replace(' ', '_')
        att = self.env['ir.attachment'].create({
            'name': filename, 'type': 'binary',
            'datas': base64.b64encode(output.getvalue()),
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{att.id}?download=true',
            'target': 'self',
        }
