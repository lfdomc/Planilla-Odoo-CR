import io
import base64
from odoo import models, fields
from odoo.exceptions import UserError


class ResumenEjecutivoReducidoWizard(models.TransientModel):
    _name = 'planilla.resumen.ejecutivo.reducido.wizard'
    _description = 'Resumen Ejecutivo Reducido de Planilla'

    company_id = fields.Many2one(
        'res.company', required=True,
        default=lambda self: self.env.company
    )
    period_mode = fields.Selection([
        ('quincena', 'Por Quincena'),
        ('mes',      'Por Mes (dos quincenas)'),
    ], string='Período', default='quincena', required=True)

    payroll_run_id = fields.Many2one(
        'planilla.run.cr', string='Planilla (quincena)',
        domain="[('company_id', '=', company_id)]"
    )
    payroll_run_id_1 = fields.Many2one(
        'planilla.run.cr', string='Primera Quincena',
        domain="[('company_id', '=', company_id)]"
    )
    payroll_run_id_2 = fields.Many2one(
        'planilla.run.cr', string='Segunda Quincena',
        domain="[('company_id', '=', company_id)]"
    )
    elaborado_por = fields.Char(
        string='Elaborado por',
        default=lambda self: self.env.user.name
    )
    include_draft = fields.Boolean(
        string='Incluir boletas en borrador',
        default=True,
        help='Activar para revisar cálculos antes de confirmar la planilla.',
    )

    def _dias_incapacidad_por_tipo(self, slip):
        """
        Suma los dias de incapacidad de esta boleta, separados en dos
        grupos: CCSS (enfermedad + accidente laboral + maternidad) e INS
        (riesgo laboral). Replica el mismo calculo de traslape de fechas
        que usa payslip_compute_mixin.py para disability_days_in_period,
        pero acumulando por separado segun disability_type -- ese campo
        ya existe en planilla.disability (ccss/ccss_accident/ins/
        maternity/other) y cada boleta tiene acceso directo a sus
        incapacidades vinculadas via slip.disability_ids, sin necesitar
        reabrir el calculo completo de subsidios/dias patronales (eso
        afecta montos que ya vienen correctos en la boleta, no la
        simple cuenta de dias por tipo que se pide aqui).

        Retorna (dias_ccss, dias_ins) -- ambos como float, ya que un
        traslape puede incluir medio dia extra del primer dia
        (extra_half_day), igual que el calculo original.
        """
        dias_ccss = 0.0
        dias_ins = 0.0
        active_dis = getattr(slip, 'disability_ids', None)
        if not active_dis:
            return dias_ccss, dias_ins
        date_from = getattr(slip, 'date_from', None)
        date_to = getattr(slip, 'date_to', None)
        if not date_from or not date_to:
            return dias_ccss, dias_ins

        for dis in active_dis:
            if not dis.date_start or not dis.date_end:
                continue
            overlap_start = max(date_from, dis.date_start)
            overlap_end = min(date_to, dis.date_end)
            if overlap_end < overlap_start:
                continue
            dias_overlap = (overlap_end - overlap_start).days + 1
            if getattr(dis, 'extra_half_day', False):
                if date_from <= dis.date_start <= date_to:
                    dias_overlap += 0.5
            if dis.disability_type == 'ins':
                dias_ins += dias_overlap
            else:
                # ccss, ccss_accident, maternity, other -> se agrupan
                # bajo "Incapacidad CCSS" para este reporte reducido
                dias_ccss += dias_overlap
        return dias_ccss, dias_ins

    def action_generate(self):
        self.ensure_one()

        if self.period_mode == 'mes':
            if not self.payroll_run_id_1 and not self.payroll_run_id_2:
                raise UserError('Seleccione al menos una quincena para el mes.')
            runs = (self.payroll_run_id_1 | self.payroll_run_id_2).filtered(lambda r: r)
            run = self.payroll_run_id_1 or self.payroll_run_id_2
        else:
            if not self.payroll_run_id:
                raise UserError('Seleccione una planilla.')
            runs = self.payroll_run_id
            run = self.payroll_run_id

        slips = self.env['planilla.payslip.cr'].search([
            ('payroll_run_id', 'in', runs.ids),
            ('state', 'in', (['draft', 'confirmed', 'done']
                             if self.include_draft
                             else ['confirmed', 'done'])),
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

        # En modo mensual: consolidar las dos quincenas por empleado,
        # mismo patron usado en el Resumen Ejecutivo completo.
        if self.period_mode == 'mes':
            from collections import defaultdict
            emp_order = {}
            emp_slips = defaultdict(list)
            for _s in slips:
                eid = _s.employee_id.id
                if eid not in emp_order:
                    emp_order[eid] = len(emp_order)
                emp_slips[eid].append(_s)

            class _MergedSlip:
                def __init__(self, slips_list):
                    s0 = slips_list[0]
                    self.employee_id = s0.employee_id
                    self.date_from = min(s.date_from for s in slips_list if s.date_from)
                    self.date_to = max(s.date_to for s in slips_list if s.date_to)

                    def _sum(attr):
                        return sum(getattr(s, attr) or 0 for s in slips_list)
                    self.base_salary          = s0.base_salary
                    self.overtime_amount      = _sum('overtime_amount')
                    self.other_income         = _sum('other_income')
                    self.ccss_employee        = _sum('ccss_employee')
                    self.income_tax           = _sum('income_tax')
                    self.rebajo_renta_amount  = _sum('rebajo_renta_amount')
                    # Union de recordsets de Odoo (soporta el operador |
                    # directamente) -- evita duplicados si la misma
                    # incapacidad aparece vinculada a ambas quincenas.
                    dis_union = s0.disability_ids
                    for s in slips_list[1:]:
                        dis_union = dis_union | s.disability_ids
                    self.disability_ids = dis_union
                    # Sumar deduction_line_ids de todos los slips
                    self.deduction_line_ids = sum(
                        (list(s.deduction_line_ids) for s in slips_list), [])

            merged = []
            for eid in sorted(emp_order, key=lambda e: emp_order[e]):
                merged.append(_MergedSlip(emp_slips[eid]))
            slips = merged
            slips = merged

        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True})
        ws = wb.add_worksheet('Resumen Ejecutivo Reducido')

        def F(bold=False, bg=None, fg='#000000', border=1,
              align='center', sz=9, num=None, wrap=False, italic=False):
            d = dict(bold=bold, font_size=sz, font_name='Arial',
                     align=align, valign='vcenter', border=border,
                     font_color=fg, text_wrap=wrap, italic=italic)
            if bg:
                d['bg_color'] = bg
            if num:
                d['num_format'] = num
            return wb.add_format(d)

        C_ING = '#375623'
        C_DED = '#C00000'
        BG_ING = '#E2EFDA'
        BG_DED = '#FCE4D6'
        BG_TOT = '#FFF2CC'

        fh_lbl = F(bold=True, bg='#1F4E79', fg='#FFFFFF', sz=9, wrap=True)
        fh_ing = F(bold=True, bg=C_ING, fg='#FFFFFF', sz=9, wrap=True)
        fh_ded = F(bold=True, bg=C_DED, fg='#FFFFFF', sz=9, wrap=True)

        fd_lbl = F(align='left')
        fd_tipo = F(align='center')
        fd_ing = F(bg=BG_ING, num='#,##0')
        fd_ded = F(bg=BG_DED, num='#,##0', fg='#C00000')
        ft_ing = F(bg=BG_ING, num='#,##0', bold=True, border=2)

        # -- Columnas, igual al Excel de referencia de Mundopet -------------
        # (encabezado, ancho, tipo: 'lbl'/'tipo'/'ing'/'ded', formato)
        cols = [
            ('Nombre',                          26, 'lbl',  fd_lbl),
            ('T',                                4, 'tipo', fd_tipo),
            ('Salario\nQuincenal',              12, 'ing',  fd_ing),
            ('Otros',                           10, 'ing',  fd_ing),
            ('Extras',                          10, 'ing',  fd_ing),
            ('Sub total\nquincenal',            13, 'ing',  ft_ing),
            ('C.C.S.S.',                        11, 'ded',  fd_ded),
            ('Incapacidad\nC.C.S.S.',           11, 'ded',  fd_ded),
            ('Incapacidad\nI.N.S.',             11, 'ded',  fd_ded),
            ('Ahorro\nNavideño',                11, 'ded',  fd_ded),
            ('Permiso sin\nGoce de Salario',    12, 'ded',  fd_ded),
            ('Impuesto\nde Renta',              11, 'ded',  fd_ded),
            ('Otros',                           11, 'ded',  fd_ded),
            ('Embargos',                        11, 'ded',  fd_ded),
            ('Préstamos\nInternos',             11, 'ded',  fd_ded),
        ]
        N = len(cols)
        tipo_hdr = {'lbl': fh_lbl, 'tipo': fh_lbl, 'ing': fh_ing, 'ded': fh_ded}

        for ci, (_, w, _, _) in enumerate(cols):
            ws.set_column(ci, ci, w)

        empresa = run.company_id.name or ''
        d_start = min(r.date_start for r in runs)
        d_end = max(r.date_end for r in runs)
        periodo = f"{d_start.strftime('%d/%m/%Y')} al {d_end.strftime('%d/%m/%Y')}"

        draft_warn = ' -- INCLUYE BORRADORES -- Solo para revision interna' if self.include_draft else ''
        titulo_fmt = F(bold=True, sz=12, bg='#1F4E79', fg='#FFFFFF', border=2)
        sub_fmt = F(sz=9, bg='#D6E4F0', align='left')

        titulo_periodo = ('Mes ' + d_start.strftime('%B %Y').title()
                           if self.period_mode == 'mes' else run.name)
        ws.merge_range(0, 0, 0, N - 1,
            f'RESUMEN EJECUTIVO REDUCIDO -- {titulo_periodo}{draft_warn}',
            titulo_fmt)
        ws.merge_range(1, 0, 1, N - 1,
            f'{empresa}  |  Periodo: {periodo}  |  '
            f'Elaborado por: {self.elaborado_por or ""}', sub_fmt)
        ws.set_row(0, 20)
        ws.set_row(1, 14)

        # -- Fila de sección + encabezados ----------------------------------
        row_sec = 2
        sec_labels = [('lbl', 'IDENTIFICACION', 0, 1),
                      ('ing', 'INGRESOS', 2, 5),
                      ('ded', 'REBAJOS', 6, N - 1)]
        for tipo, label, ci_start, ci_end in sec_labels:
            ws.merge_range(row_sec, ci_start, row_sec, ci_end, label, tipo_hdr[tipo])
        ws.set_row(row_sec, 16)

        row_hdr = 3
        for ci, (hdr, _, tipo, _) in enumerate(cols):
            ws.write(row_hdr, ci, hdr, tipo_hdr[tipo])
        ws.set_row(row_hdr, 34)

        # -- Datos por empleado, agrupados por departamento -----------------
        row = 4
        totales = [0.0] * N
        prev_dept = None
        dept_totals = [0.0] * N

        def _sum_cat(slip, *cats):
            return round(sum(
                l.amount for l in slip.deduction_line_ids
                if getattr(l, 'line_type', '') == 'deduction'
                and getattr(l, 'deduction_category', '') in cats
            ), 2)

        for slip in slips:
            emp = slip.employee_id
            dept = emp.department_id.name or 'Sin Departamento'

            if dept != prev_dept:
                if prev_dept is not None:
                    sub_lbl_fmt = F(bold=True, bg='#F2F2F2', align='left', border=1)
                    ws.write(row, 0, f'  Subtotal {prev_dept}', sub_lbl_fmt)
                    for ci in range(1, N):
                        _, _, tipo, _ = cols[ci]
                        if tipo == 'tipo':
                            ws.write(row, ci, '', sub_lbl_fmt)
                            continue
                        sf = F(bold=True, bg='#F2F2F2', num='#,##0', border=1,
                               fg='#C00000' if tipo == 'ded' else '#000000')
                        v = dept_totals[ci]
                        ws.write(row, ci, v if v else None, sf)
                    ws.set_row(row, 14)
                    row += 1
                    dept_totals = [0.0] * N

                dept_hdr_fmt = F(bold=True, bg='#2E4057', fg='#FFFFFF',
                                 align='left', border=1, sz=9)
                ws.merge_range(row, 0, row, N - 1, f'  {dept}', dept_hdr_fmt)
                ws.set_row(row, 14)
                row += 1
                prev_dept = dept

            # T = tipo de empleado (A=administrativo, O=operativo), toma
            # la primera letra del codigo de employee_type_id si existe.
            tipo_letra = ''
            if emp.employee_type_id and emp.employee_type_id.code:
                tipo_letra = emp.employee_type_id.code[0].upper()

            sal_base = slip.base_salary or 0
            extras = slip.overtime_amount or 0
            otros_ing = slip.other_income or 0
            sub_total = sal_base + extras + otros_ing

            ccss_emp = slip.ccss_employee or 0
            dias_ccss, dias_ins = self._dias_incapacidad_por_tipo(slip)
            _daily_rate = round((slip.base_salary or 0) / 30, 4)
            monto_incap_ccss = round(dias_ccss * _daily_rate, 2)
            monto_incap_ins = round(dias_ins * _daily_rate, 2)

            ahorro = _sum_cat(slip, 'ahorro')
            permiso_sg = _sum_cat(slip, 'licencia_sin_goce', 'ausencia')
            renta = slip.income_tax or 0
            embargo = _sum_cat(slip, 'embargo', 'embargo_judicial')
            prestamos = _sum_cat(slip, 'loan', 'prestamo', 'prestamo_interno')
            # "Otros" = todo lo demas que no tiene columna propia en este
            # reporte reducido (seguro, pension voluntaria, cobros al
            # empleado, pension alimentaria, rebajo consolidado, etc.)
            otros_ded = _sum_cat(
                slip, 'sindical', 'cooperativa', 'rop', 'seguro',
                'pension_vol', 'pension_alimentaria', 'other')
            otros_ded += round(getattr(slip, 'rebajo_renta_amount', 0) or 0, 2)
            otros_ded += round(sum(
                l.amount for l in slip.deduction_line_ids
                if getattr(l, 'line_type', '') == 'deduction'
                and getattr(l, 'deduction_category', '') in ('cobro', 'cobro_empleado', 'employee_charge')
            ), 2)

            vals = [
                emp.name or '', tipo_letra,
                sal_base, otros_ing, extras, sub_total,
                ccss_emp, monto_incap_ccss, monto_incap_ins, ahorro,
                permiso_sg, renta, otros_ded, embargo, prestamos,
            ]

            for ci, (val, (_, _, tipo, dfmt)) in enumerate(zip(vals, cols)):
                is_num = isinstance(val, (int, float)) and tipo in ('ing', 'ded')
                ws.write(row, ci, val if val != 0 or not is_num else None, dfmt)
                if is_num and val:
                    totales[ci] += val
                    dept_totals[ci] += val

            ws.set_row(row, 14)
            row += 1

        if prev_dept:
            sub_lbl_fmt = F(bold=True, bg='#F2F2F2', align='left', border=1)
            ws.write(row, 0, f'  Subtotal {prev_dept}', sub_lbl_fmt)
            for ci in range(1, N):
                _, _, tipo, _ = cols[ci]
                if tipo == 'tipo':
                    ws.write(row, ci, '', sub_lbl_fmt)
                    continue
                sf = F(bold=True, bg='#F2F2F2', num='#,##0', border=1,
                       fg='#C00000' if tipo == 'ded' else '#000000')
                v = dept_totals[ci]
                ws.write(row, ci, v if v else None, sf)
            ws.set_row(row, 14)
            row += 1

        row += 1
        tot_lbl_fmt = F(bold=True, bg=BG_TOT, align='left', border=2, sz=10)
        ws.write(row, 0, 'TOTAL GENERAL', tot_lbl_fmt)
        for ci in range(1, N):
            _, _, tipo, _ = cols[ci]
            if tipo == 'tipo':
                ws.write(row, ci, '', tot_lbl_fmt)
                continue
            tf = F(bold=True, bg=BG_TOT, num='#,##0', border=2,
                   fg='#C00000' if tipo == 'ded' else '#000000')
            v = totales[ci]
            ws.write(row, ci, v if v else None, tf)
        ws.set_row(row, 18)

        row += 2
        note_fmt = F(sz=8, align='left', italic=True, fg='#666666', border=0)
        ws.merge_range(row, 0, row, N - 1,
            'Datos leidos directamente de las boletas confirmadas. '
            'T = Tipo de Empleado (primera letra del codigo configurado). '
            '"Otros" en Rebajos agrupa: cuota sindical, cooperativa, ROP, '
            'seguro/poliza, pension voluntaria, pension alimentaria, cobros '
            'al empleado, y rebajo consolidado de renta.',
            note_fmt)

        ws.freeze_panes(4, 2)

        wb.close()
        xlsx_data = base64.b64encode(output.getvalue()).decode()

        if self.period_mode == 'mes':
            slug = f"Mes_{d_start.strftime('%B_%Y').title()}"
        else:
            slug = run.name.replace(' ', '_')[:40]
        filename = f'ResumenEjecutivoReducido_{slug}.xlsx'

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
