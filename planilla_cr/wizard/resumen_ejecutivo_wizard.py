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

        # ── Colores de sección ─────────────────────────────────────────────────
        C_ID    = '#1F4E79'   # Identificación
        C_ING   = '#375623'   # Ingresos del empleado
        C_DED   = '#C00000'   # Deducciones
        C_NET   = '#1F4E79'   # Neto / depósito
        C_PAT   = '#4A4A4A'   # Cargas patronales

        # Colores de fondo por sección
        BG_ID   = '#DEEAF1'
        BG_ING  = '#E2EFDA'
        BG_DED  = '#FCE4D6'
        BG_NET  = '#BDD7EE'
        BG_PAT  = '#EDEDED'
        BG_TOT  = '#FFF2CC'

        def F(bold=False, bg=None, fg='#000000', border=1,
              align='center', sz=9, num=None, wrap=False, italic=False):
            d = dict(bold=bold, font_size=sz, font_name='Arial',
                     align=align, valign='vcenter', border=border,
                     font_color=fg, text_wrap=wrap, italic=italic)
            if bg:  d['bg_color'] = bg
            if num: d['num_format'] = num
            return wb.add_format(d)

        # Formatos de encabezado de grupo (fila con nombre de sección)
        fh_id  = F(bold=True, bg=C_ID,  fg='#FFFFFF', sz=9, wrap=True)
        fh_ing = F(bold=True, bg=C_ING, fg='#FFFFFF', sz=9, wrap=True)
        fh_ded = F(bold=True, bg=C_DED, fg='#FFFFFF', sz=9, wrap=True)
        fh_net = F(bold=True, bg=C_NET, fg='#FFFFFF', sz=9, wrap=True)
        fh_pat = F(bold=True, bg=C_PAT, fg='#FFFFFF', sz=9, wrap=True)

        # Formatos de datos
        fd_id  = F(bg=BG_ID,  align='left')
        fd_ing = F(bg=BG_ING, num='#,##0')
        fd_ded = F(bg=BG_DED, num='#,##0', fg='#C00000')
        fd_net = F(bg=BG_NET, num='#,##0', bold=True)
        fd_pat = F(bg=BG_PAT, num='#,##0')
        fd_tot = F(bg=BG_TOT, num='#,##0', bold=True)
        fd_0   = F(num='#,##0')

        # Formatos de totales por sección
        ft_ing = F(bg=BG_ING, num='#,##0', bold=True, border=2)
        ft_ded = F(bg=BG_DED, num='#,##0', bold=True, border=2, fg='#C00000')
        ft_net = F(bg=BG_NET, num='#,##0', bold=True, border=2)
        ft_pat = F(bg=BG_PAT, num='#,##0', bold=True, border=2)

        f_lbl  = F(align='left')
        f_sep  = F(bg='#FFFFFF', border=0)  # columna separadora

        # ── Definición de columnas ─────────────────────────────────────────────
        # Cada columna: (encabezado, ancho, sección, formato_dato)
        # Las columnas se agrupan en secciones con filas de color
        cols = [
            # ── Identificación ────────────────────────────────────────────────
            ('Empleado',               28, 'id',  fd_id),
            ('Departamento',           18, 'id',  fd_id),
            ('Puesto',                 18, 'id',  fd_id),
            ('Sucursal',               12, 'id',  fd_id),
            ('Días\nLaborados',         8, 'id',  F(bg=BG_ID, num='0')),
            # ── Ingresos del empleado ─────────────────────────────────────────
            ('Salario\nBase',          12, 'ing', fd_ing),
            ('Horas\nExtras',          10, 'ing', fd_ing),
            ('Bonos\nSalariales',      10, 'ing', fd_ing),
            ('Vacaciones\nPagadas',    10, 'ing', fd_ing),
            ('Subsidio\nIncap. Días 1-3', 12, 'ing', fd_ing),
            ('Otros\nIngresos',        10, 'ing', fd_ing),
            # ── Deducciones (rebajan el bruto cotizable) ──────────────────────
            ('Días\nIncapacidad',       8, 'ded', F(bg=BG_DED, num='0', fg='#C00000')),
            ('Monto\nIncapacidad',     11, 'ded', fd_ded),
            ('Licencia\nSin Goce',     11, 'ded', fd_ded),
            # ── Salario Bruto Cotizable ───────────────────────────────────────
            ('Sal. Bruto\nCotizable',  13, 'net', ft_net),
            # ── Deducciones legales ───────────────────────────────────────────
            ('CCSS\nObrero 10.83%',    11, 'ded', fd_ded),
            ('Impuesto\nRenta',        10, 'ded', fd_ded),
            ('Pensión\nAlimenticia',   10, 'ded', fd_ded),
            ('Embargo\nJudicial',      10, 'ded', fd_ded),
            ('Cobros\nEmpleado',       10, 'ded', fd_ded),
            ('Préstamos\nInternos',    10, 'ded', fd_ded),
            ('Ahorro\nNavideño',       10, 'ded', fd_ded),
            ('Sindicato /\nCooper.',   10, 'ded', fd_ded),
            ('Otras\nDeducciones',     10, 'ded', fd_ded),
            ('Total\nDeducciones',     12, 'ded', ft_ded),
            # ── Subsidios post-deducción (no afectan CCSS ni renta) ───────────
            ('Subsidio\nCCSS/Mat.',    12, 'ing', fd_ing),
            ('INS\nPago Directo',      10, 'ing', fd_ing),
            # ── Neto depósito patrono ─────────────────────────────────────────
            ('NETO\nDEPÓSITO',        13, 'net', ft_net),
            # ── Separador ─────────────────────────────────────────────────────
            ('',                        2, 'sep', f_sep),
            # ── Cargas patronales ─────────────────────────────────────────────
            ('CCSS\nPatronal 26.83%', 12, 'pat', fd_pat),
            ('INS\nRiesgos Trabajo',   12, 'pat', fd_pat),
            ('Prov.\nAguinaldo',       11, 'pat', fd_pat),
            ('Prov.\nCesantía',        11, 'pat', fd_pat),
            ('Prov.\nVacaciones',      11, 'pat', fd_pat),
            ('Costo Total\nPatronal',  13, 'pat', ft_pat),
        ]

        N = len(cols)
        secciones = {
            'id':  fh_id,
            'ing': fh_ing,
            'ded': fh_ded,
            'net': fh_net,
            'pat': fh_pat,
            'sep': f_sep,
        }

        # ── Anchos de columna ──────────────────────────────────────────────────
        for ci, (_, w, _, _) in enumerate(cols):
            ws.set_column(ci, ci, w)

        # ── Título ────────────────────────────────────────────────────────────
        empresa = run.company_id.name or ''
        periodo = f"{run.date_start.strftime('%d/%m/%Y')} al {run.date_end.strftime('%d/%m/%Y')}"
        freq_map = {'biweekly': 'Quincenal', 'monthly': 'Mensual',
                    'weekly': 'Semanal', 'bimonthly': 'Bimensual'}
        freq = freq_map.get((run.payroll_calendar_id.frequency or ''), run.payroll_calendar_id.frequency or '')

        titulo_fmt = F(bold=True, sz=13, bg='#1F4E79', fg='#FFFFFF', border=2)
        sub_fmt    = F(sz=9, bg='#D6E4F0', align='left')

        ws.merge_range(0, 0, 0, N-1,
            f'RESUMEN EJECUTIVO DE PLANILLA — {run.name}', titulo_fmt)
        ws.merge_range(1, 0, 1, N-1,
            f'{empresa}  |  Período: {periodo}  |  Frecuencia: {freq}  |  '
            f'Elaborado por: {self.elaborado_por or ""}', sub_fmt)
        ws.set_row(0, 22); ws.set_row(1, 14)

        # ── Fila de sección (color por grupo) ────────────────────────────────
        row_sec = 2
        prev_sec = None
        sec_start = {}
        for ci, (hdr, _, sec, _) in enumerate(cols):
            if sec != prev_sec:
                sec_start[sec] = ci
                prev_sec = sec
            ws.write(row_sec, ci, '', secciones[sec])
        # Labels de sección
        sec_labels = {
            'id':  'IDENTIFICACIÓN',
            'ing': 'INGRESOS',
            'ded': 'DEDUCCIONES',
            'net': 'NETO',
            'pat': 'CARGAS PATRONALES',
            'sep': '',
        }
        prev_sec = None; sec_start_ci = 0
        for ci, (_, _, sec, _) in enumerate(cols):
            if sec != prev_sec:
                if prev_sec and prev_sec != 'sep':
                    end = ci - 1
                    ws.merge_range(row_sec, sec_start_ci, row_sec, end,
                                   sec_labels[prev_sec], secciones[prev_sec])
                sec_start_ci = ci; prev_sec = sec
        if prev_sec and prev_sec != 'sep':
            ws.merge_range(row_sec, sec_start_ci, row_sec, N-1,
                           sec_labels[prev_sec], secciones[prev_sec])
        ws.set_row(row_sec, 16)

        # ── Encabezados de columna ────────────────────────────────────────────
        row_hdr = 3
        for ci, (hdr, _, sec, _) in enumerate(cols):
            ws.write(row_hdr, ci, hdr, secciones[sec])
        ws.set_row(row_hdr, 36)

        # ── Datos por empleado ────────────────────────────────────────────────
        row = 4
        totales = [0.0] * N

        # Subtotales por departamento
        prev_dept = None
        dept_start_row = 4
        dept_totals = [0.0] * N

        for slip in slips:
            emp = slip.employee_id
            dept = emp.department_id.name or 'Sin Departamento'

            # Separador y subtotal de departamento
            if dept != prev_dept:
                if prev_dept is not None:
                    # Fila subtotal del departamento anterior
                    sub_lbl_fmt = F(bold=True, bg='#F2F2F2', align='left', border=1)
                    ws.write(row, 0, f'  Subtotal {prev_dept}', sub_lbl_fmt)
                    for ci in range(1, N):
                        _, _, sec, _ = cols[ci]
                        sf = F(bold=True, bg='#F2F2F2',
                               num='#,##0', border=1,
                               fg='#C00000' if sec == 'ded' else '#000000')
                        v = dept_totals[ci]
                        ws.write(row, ci, v if v else None, sf)
                    ws.set_row(row, 14)
                    row += 1
                    dept_totals = [0.0] * N

                # Encabezado de departamento
                dept_hdr_fmt = F(bold=True, bg='#2E4057', fg='#FFFFFF',
                                 align='left', border=1, sz=9)
                ws.merge_range(row, 0, row, N-1, f'  {dept}', dept_hdr_fmt)
                ws.set_row(row, 14)
                row += 1
                prev_dept = dept

            # ── Leer datos del slip (misma fuente que la boleta) ──────────────
            dias_lab = slip.dias_laborados_periodo or slip.days_worked or 0

            # INGRESOS
            sal_base     = slip.base_salary or 0
            horas_extras = slip.overtime_amount or 0
            bonos        = slip.bono_salarial_amount or 0
            vacaciones   = slip.vacation_amount or 0
            # Subsidio incapacidad días 1-3: lo que el patrono paga por esos días
            subsid_incap_13 = max((slip.base_salary or 0) - (slip.gross_salary or 0), 0) \
                if (slip.gross_salary or 0) < (slip.base_salary or 0) else 0
            # Otros ingresos = residual (bonos no salariales, incentivos, etc.)
            otros_ing    = slip.other_income or 0

            # DEDUCCIONES QUE REDUCEN COTIZABLE
            dias_incap = slip.disability_days_in_period or 0
            monto_incap = max((slip.base_salary or 0) - (slip.gross_salary or 0), 0)
            licencia_sg = round(sum(
                l.amount for l in slip.deduction_line_ids
                if l.line_type == 'deduction'
                and l.deduction_category in ('licencia_sin_goce', 'ausencia')
            ), 2)

            # SALARIO BRUTO COTIZABLE
            bruto_cotiz = slip.base_cotizable_final or 0

            # DEDUCCIONES LEGALES Y ADICIONALES
            ccss_emp   = slip.ccss_employee or 0
            renta      = slip.income_tax or 0

            # Leer cada categoría desde deduction_line_ids
            def _sum_cat(*cats):
                return round(sum(
                    l.amount for l in slip.deduction_line_ids
                    if l.line_type == 'deduction'
                    and l.deduction_category in cats
                ), 2)

            pension_al = _sum_cat('pension_alimentaria')
            embargo    = _sum_cat('embargo', 'embargo_judicial')
            cobros_emp = _sum_cat('cobro', 'cobro_empleado', 'employee_charge')
            prestamos  = _sum_cat('loan', 'prestamo', 'prestamo_interno')
            ahorro_nav = _sum_cat('ahorro', 'ahorro_navidad', 'ahorro_navideno')
            sindicato  = _sum_cat('sindicato', 'cooperativa', 'sindical')
            otras_ded  = max(round(
                (slip.total_employee_deductions or 0)
                - ccss_emp - renta - pension_al - embargo
                - cobros_emp - prestamos - ahorro_nav - sindicato, 2), 0)

            total_ded  = slip.total_employee_deductions or 0

            # SUBSIDIOS POST-DEDUCCIÓN (informativo, no afectan CCSS/renta)
            subsid_ccss = round(
                (slip.ccss_subsidy_total or 0) + (slip.paternity_amount or 0), 2)
            subsid_ins  = slip.ins_subsidy_total or 0

            # NETO DEPÓSITO
            neto_dep = slip.deposito_patrono or 0

            # CARGAS PATRONALES
            ccss_pat   = slip.ccss_employer or 0
            ins_pat    = slip.ins_employer or 0
            prov_agu   = slip.aguinaldo_provision or 0
            prov_ces   = slip.cesantia_provision or 0
            prov_vac   = slip.vacation_provision or 0
            costo_tot  = slip.total_employer_cost or 0

            # ── Escribir fila ──────────────────────────────────────────────────
            vals = [
                emp.name or '',
                dept,
                emp.job_id.name or '',
                slip.branch_id.name or '',
                float(dias_lab),
                sal_base, horas_extras, bonos, vacaciones,
                subsid_incap_13, otros_ing,
                float(dias_incap), monto_incap, licencia_sg,
                bruto_cotiz,
                ccss_emp, renta, pension_al, embargo,
                cobros_emp, prestamos, ahorro_nav, sindicato, otras_ded,
                total_ded,
                subsid_ccss, subsid_ins,
                neto_dep,
                '',  # separador
                ccss_pat, ins_pat, prov_agu, prov_ces, prov_vac, costo_tot,
            ]

            for ci, (val, (_, _, sec, dfmt)) in enumerate(zip(vals, cols)):
                is_num = isinstance(val, float) and sec != 'sep'
                is_int = sec == 'id' and ci == 4
                ws.write(row, ci, val if val != 0.0 or not is_num else None, dfmt)
                if is_num and val:
                    totales[ci] += val
                    dept_totals[ci] += val
                elif is_int:
                    totales[ci] += val
                    dept_totals[ci] += val

            ws.set_row(row, 14)
            row += 1

        # Último subtotal de departamento
        if prev_dept:
            sub_lbl_fmt = F(bold=True, bg='#F2F2F2', align='left', border=1)
            ws.write(row, 0, f'  Subtotal {prev_dept}', sub_lbl_fmt)
            for ci in range(1, N):
                _, _, sec, _ = cols[ci]
                sf = F(bold=True, bg='#F2F2F2', num='#,##0', border=1,
                       fg='#C00000' if sec == 'ded' else '#000000')
                v = dept_totals[ci]
                ws.write(row, ci, v if v else None, sf)
            ws.set_row(row, 14)
            row += 1

        # ── Fila de TOTALES GENERALES ─────────────────────────────────────────
        row += 1
        tot_lbl_fmt = F(bold=True, bg='#FFF2CC', align='left', border=2, sz=10)
        ws.write(row, 0, 'TOTAL GENERAL', tot_lbl_fmt)
        for ci in range(1, N):
            _, _, sec, _ = cols[ci]
            tf = F(bold=True, bg='#FFF2CC', num='#,##0', border=2,
                   fg='#C00000' if sec == 'ded' else '#000000')
            v = totales[ci]
            ws.write(row, ci, v if v else None, tf)
        ws.set_row(row, 18)

        # ── Nota al pie ───────────────────────────────────────────────────────
        row += 2
        note_fmt = F(sz=8, align='left', italic=True, fg='#666666', border=0)
        ws.merge_range(row, 0, row, N-1,
            'Datos leídos directamente de las boletas confirmadas. '
            'Subsidio Incap. días 1-3 = cargo del patrono (no genera CCSS/Renta). '
            'INS Pago Directo = informativo, el INS deposita directamente al empleado. '
            'Subsidio CCSS/Mat. incluye paternidad (Ley 8107).',
            note_fmt)

        ws.freeze_panes(4, 5)  # Congelar ID + primera col de ingresos

        wb.close()
        xlsx_data = base64.b64encode(output.getvalue()).decode()

        slug = run.name.replace(' ', '_')[:40]
        filename = f'ResumenEjecutivo_{slug}.xlsx'

        attach = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': xlsx_data,
            'res_model': self._name,
            'res_id': self.id,
        })
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attach.id}?download=true',
            'target': 'self',
        }
