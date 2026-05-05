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
            ('A6', 'Nombre',                      hdr_col),
            ('B6', 'Dpto',                        hdr_col),
            ('C6', 'Salario\nQuincenal',           hdr_col_ing),
            ('D6', 'Otros',                       hdr_col_ing),
            ('E6', 'Extras',                      hdr_col_ing),
            ('F6', 'Sub total\nquincenal',         hdr_col_ing),
            ('G6', 'Permiso sin\nGoce de Salario', hdr_col_reb),
            ('H6', 'C.C.S.S.',                    hdr_col_reb),
            ('I6', 'Reducción por\nIncapacidad',    hdr_col_reb),
            ('J6', 'Ahorro\nNavideno',             hdr_col_reb),
            ('K6', 'Impuesto\nde Renta',           hdr_col_reb),
            ('L6', 'Otros',                       hdr_col_reb),
            ('M6', 'Prestamos\nInternos',          hdr_col_reb),
            ('N6', 'Facturas',                    hdr_col_reb),
            ('O6', 'Maternidad',                  hdr_col_reb),
            ('P6', 'Total\nGeneral',               hdr_col_tot),
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
            # FIX: excluir licencias_con_goce y vacation (no van a extra_income en net_salary).
            lines = slip.deduction_line_ids.filtered(
                lambda l: l.line_type == 'income'
                and l.deduction_category not in ('overtime', 'licencia_con_goce', 'vacation')
            )
            return sum(lines.mapped('amount'))

        def get_ccss_subsidy_via_patrono(slip):
            """
            Retorna el subsidio CCSS/maternidad que fluye a traves del patrono al empleado.

            Casos donde el subsidio SI aparece en deposito_patrono (ingreso extra del patrono):
              - Maternidad con maternity_ccss_on_employer=True y maternity_split_50=False:
                la CCSS transfiere el subsidio completo al patrono, quien lo deposita al empleado.
              - Incapacidad normal CCSS (no maternidad, no INS):
                el patrono cubre dias 1-3 y la CCSS paga dias 4+ directamente al empleado.
                En este caso ccss_subsidy NO pasa por el patrono -> NO se incluye.

            Casos donde el subsidio NO pasa por el patrono (CCSS/INS deposita directo):
              - split_50: CCSS deposita el 50%% directamente al empleado.
              - INS riesgo laboral: INS deposita directamente.
              - Incapacidad normal: CCSS deposita dias 4+ directamente.
            """
            if not (slip.date_from and slip.date_to):
                return 0.0
            ccss_sub = slip.ccss_subsidy_total or 0.0
            if ccss_sub <= 0:
                return 0.0
            active_dis = slip.disability_ids.filtered(
                lambda d: d.state in ('confirmed', 'paid') and d.date_start and d.date_end
            )
            mat_in_per = [
                d for d in active_dis
                if d.disability_type == 'maternity'
                and max(slip.date_from, d.date_start) <= min(slip.date_to, d.date_end)
            ]
            if not mat_in_per:
                return 0.0  # No maternidad -> CCSS no pasa por patrono
            has_ccss_on_emp = any(getattr(d, 'maternity_ccss_on_employer', False) for d in mat_in_per)
            has_split_50    = any(getattr(d, 'maternity_split_50', False) for d in mat_in_per)
            if has_ccss_on_emp and not has_split_50:
                # Subsidio completo pasa por patrono -> es ingreso del empleado en esta planilla
                return ccss_sub
            return 0.0

        def get_otros_rebajos(slip):
            # cat_excluidas = categorias que ya tienen su propia columna en el reporte.
            # IMPORTANTE — regla de exclusion exacta para evitar doble conteo:
            #   'licencia_sin_goce' -> ya en permiso_col (amount_licencias_sin_goce)
            #   'ausencia'          -> ya en permiso_col
            #   'ccss'              -> columna CCSS (campo computado)
            #   'income_tax'        -> columna Renta (campo computado)
            #   'loan'              -> columna Prestamos
            #   'ahorro'            -> columna Ahorro
            #   'maternity'         -> columna Maternidad
            #   'cooperativa'       -> columna Facturas (get_deduction_amount cooperativa)
            #   'facturas'          -> alias de cooperativa usado por algunos códigos
            # BUG ANTERIOR: 'licencia_sin_goce' y 'cooperativa' NO estaban en cat_excluidas
            # -> se sumaban dos veces (permiso_col + otros_reb o facturas + otros_reb)
            # -> provocaba diff positivo falso en empleados con esas deducciones.
            cat_excluidas = {
                'ccss', 'income_tax', 'loan', 'ahorro', 'maternity',
                'ausencia', 'licencia_sin_goce',  # FIX BUG1: agregar licencia_sin_goce
                'cooperativa', 'facturas',         # FIX BUG2+3: cooperativa y su alias
            }
            lines = slip.deduction_line_ids.filtered(
                lambda l: l.line_type == 'deduction'
                and l.deduction_category not in cat_excluidas
            )
            return sum(lines.mapped('amount'))

        for slip in slips:
            emp = slip.employee_id
            dept = emp.department_id.name[:1] if emp.department_id else 'O'

            # LOGICA FINANCIERA:
            # Sin incapacidad: Subtotal = gross_salary (bruto completo)
            # Con incapacidad/permiso: Subtotal = base_cotizable_final
            #   (lo que el patrono paga realmente, sin el subsidio CCSS/INS)
            has_disability = bool((slip.ccss_subsidy_total or 0) + (slip.ins_subsidy_total or 0))
            # Usar solo el campo oficial — las lineas de deduccion son el mismo dato
            permiso_sin_goce_amt = slip.amount_licencias_sin_goce or 0

            # SIEMPRE mostrar el bruto completo como subtotal.
            # El Permiso sin Goce se muestra como columna de rebajo separada.
            # La ecuacion del reporte es:
            # Subtotal - PermisoSinGoce - CCSS - Incap - Ahorro - Renta - ... = Total
            sal_quincenal = slip.base_salary or 0
            otros_ing     = (get_otros_ingresos(slip)
                             + (slip.vacation_amount or 0)       # FIX BUG4a: vacaciones pagadas
                             + (slip.other_income  or 0)         # FIX BUG4b: otros ingresos (campo directo)
                             + get_ccss_subsidy_via_patrono(slip)) # FIX: subsidio mat que pasa por patrono
            extras        = slip.overtime_amount or 0
            subtotal      = sal_quincenal + otros_ing + extras
            permiso_col   = permiso_sin_goce_amt

            if has_disability:
                # Con incapacidad: el subtotal es el bruto completo
                # y la incapacidad reduce via ccss_subsidy (ya en incap_ccss)
                pass  # subtotal ya correcto

            ccss       = slip.ccss_employee or 0
            # FIX INCAP: usar (base_salary - gross_salary) en lugar de ccss_subsidy.
            # Razon: la formula del reporte exige que
            #   Total = Subtotal - Permiso - CCSS - Incap - Renta - Otros
            # Para que cierre matematicamente, Incap debe representar la REDUCCION
            # TOTAL del salario por incapacidad (lo que el patrono deja de pagar),
            # NO el subsidio CCSS/INS (que es solo el 60% de los dias 4+).
            #
            # Con base_salary - gross_salary:
            #   - Sin incapacidad: base == gross -> incap = 0   OK
            #   - CCSS parcial:    incap = base - salario_cotizable  OK
            #   - INS total:       incap = base - 0 = base completo  OK
            #   - Maternidad total:incap = base - 0 = base completo  OK
            #
            # Formula resultante:
            #   base - (base - gross) - CCSS - Otros = gross - CCSS - Otros
            #   = gross_salary - total_deductions ≈ neto_por_patrono  CORRECTO
            incap_ccss = max((slip.base_salary or 0) - (slip.gross_salary or 0), 0)
            ahorro_nav = get_deduction_amount(slip, 'ahorro')
            permiso_sin = permiso_col
            imp_renta  = slip.income_tax or 0
            # FIX BUG-COBRO-DOBLE: usar get_otros_rebajos() en lugar de la suma manual.
            # PROBLEMA ANTERIOR: slip.amount_cobros_empleado filtra por employee_charge_id,
            # y get_deduction_amount('other') filtra por deduction_category='other'.
            # Todos los cobros tienen AMBOS: employee_charge_id SET y category='other'.
            # Resultado: cada cobro se sumaba DOS VECES → otros_reb = 2 × cobros_reales.
            # SOLUCIÓN: get_otros_rebajos() suma una sola vez todas las líneas de deducción
            # cuya categoría no está ya cubierta por otra columna del reporte.
            otros_reb  = get_otros_rebajos(slip)
            prestamos  = get_deduction_amount(slip, 'loan')
            facturas   = get_deduction_amount(slip, 'cooperativa')
            maternidad = get_deduction_amount(slip, 'maternity')
            # Total General = deposito_patrono: lo que el patrono deposita al empleado
            # (excluye subsidios CCSS/INS que paga la CCSS directamente)
            total_general = slip.deposito_patrono or slip.salary_payable or 0

            ws.write(row - 1, 0,  emp.name or '',                        txt_fmt)
            ws.write(row - 1, 1,  dept,                                   txt_fmt)
            ws.write(row - 1, 2,  sal_quincenal,                          num_fmt)
            ws.write(row - 1, 3,  otros_ing   if otros_ing   else None,   num_fmt)
            ws.write(row - 1, 4,  extras      if extras      else None,   num_fmt)
            ws.write(row - 1, 5,  subtotal,                               num_fmt)
            # Col 6: Permiso sin Goce (va antes de CCSS — reduce base cotizable)
            ws.write(row - 1, 6,  permiso_sin if permiso_sin else None,   num_fmt)
            ws.write(row - 1, 7,  ccss        if ccss        else None,   num_fmt)
            ws.write(row - 1, 8,  incap_ccss  if incap_ccss  else None,   num_fmt)
            ws.write(row - 1, 9,  ahorro_nav  if ahorro_nav  else None,   num_fmt)
            ws.write(row - 1, 10, imp_renta   if imp_renta   else None,   num_fmt)
            ws.write(row - 1, 11, otros_reb   if otros_reb   else None,   num_fmt)
            ws.write(row - 1, 12, prestamos   if prestamos   else None,   num_fmt)
            ws.write(row - 1, 13, facturas    if facturas    else None,   num_fmt)
            ws.write(row - 1, 14, maternidad  if maternidad  else None,   num_fmt)
            ws.write(row - 1, 15, total_general,                          num_fmt)

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
