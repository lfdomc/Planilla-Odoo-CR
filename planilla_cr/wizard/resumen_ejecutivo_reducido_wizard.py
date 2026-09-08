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

    # NOTA: el metodo _dias_incapacidad_por_tipo() que vivia aqui se
    # elimino -- ya no se usa. El reporte ahora absorbe directamente
    # ccss_subsidy_total / ins_subsidy_total (los montos de subsidio
    # ya calculados y probados de cada boleta), en vez de reconstruir
    # el monto sumando dias de incapacidad x tarifa diaria -- esa
    # reconstruccion podia dar montos matematicamente imposibles (ver
    # historial de esta funcion para el caso real que motivo el cambio).

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
        fd_ing = F(bg=BG_ING, num='#,##0')
        fd_ded = F(bg=BG_DED, num='#,##0', fg='#C00000')
        ft_ing = F(bg=BG_ING, num='#,##0', bold=True, border=2)

        # -- Columnas, igual al Excel de referencia de Mundopet, con las
        # siguientes diferencias por pedido explicito:
        #   - Se quito la columna T (tipo de empleado) -- no aportaba
        #     informacion relevante para este reporte.
        #   - Se agrego Maternidad como columna propia, separada de
        #     Incapacidad C.C.S.S. (antes iban agrupadas).
        #   - Facturas (Cobros al Empleado) y Prestamos Internos son
        #     columnas SEPARADAS, para poder diferenciar los valores
        #     de cada concepto -- antes estaban unificadas en una sola
        #     columna, pero se dividieron por pedido explicito.
        #   - Total (leido de salary_payable, no reconstruido) al final de
        #     cada fila: neto del empleado incluyendo subsidios CCSS/INS.
        #   - Deposito Patrono (leido de deposito_patrono) como ultima
        #     columna: el monto real que la empresa deposita, excluyendo
        #     esos subsidios -- valor correcto para libros contables.
        # (encabezado, ancho, tipo: 'lbl'/'ing'/'ded'/'tot', formato)
        cols = [
            ('Nombre',                          26, 'lbl',  fd_lbl),
            ('Salario\nQuincenal',              12, 'ing',  fd_ing),
            ('Otros',                           10, 'ing',  fd_ing),
            ('Extras',                          10, 'ing',  fd_ing),
            ('Sub total\nquincenal',            13, 'ing',  ft_ing),
            ('C.C.S.S.',                        11, 'ded',  fd_ded),
            ('Incapacidad\nC.C.S.S.',           11, 'ded',  fd_ded),
            ('Incapacidad\nI.N.S.',             11, 'ded',  fd_ded),
            ('Maternidad',                      11, 'ded',  fd_ded),
            ('Ahorro\nNavideño',                11, 'ded',  fd_ded),
            ('Permiso sin\nGoce de Salario',    12, 'ded',  fd_ded),
            ('Impuesto\nde Renta',              11, 'ded',  fd_ded),
            ('Facturas',                        11, 'ded',  fd_ded),
            ('Préstamos\nInternos',             12, 'ded',  fd_ded),
            ('Otros',                           11, 'ded',  fd_ded),
            ('Embargos',                        11, 'ded',  fd_ded),
            ('Total',                           13, 'tot',  None),
            ('Depósito\nPatrono',               14, 'tot',  None),
        ]
        N = len(cols)
        tipo_hdr = {'lbl': fh_lbl, 'ing': fh_ing, 'ded': fh_ded,
                    'tot': F(bold=True, bg='#1F4E79', fg='#FFFFFF', sz=9, wrap=True)}
        ft_tot = F(bg='#FFF2CC', num='#,##0', bold=True, border=2)

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
        ws.write(row_sec, 0, 'IDENTIFICACION', tipo_hdr['lbl'])
        ws.merge_range(row_sec, 1, row_sec, 4, 'INGRESOS', tipo_hdr['ing'])
        ws.merge_range(row_sec, 5, row_sec, N - 3, 'REBAJOS', tipo_hdr['ded'])
        ws.merge_range(row_sec, N - 2, row_sec, N - 1, 'TOTAL', tipo_hdr['tot'])
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
                        color = '#C00000' if tipo == 'ded' else (
                            '#1F4E79' if tipo == 'tot' else '#000000')
                        sf = F(bold=True, bg='#F2F2F2', num='#,##0', border=1,
                               fg=color)
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

            sal_base = slip.base_salary or 0
            extras = slip.overtime_amount or 0
            # FIX DE FONDO: usar gross_salary DIRECTAMENTE como fuente
            # del Sub Total, en vez de reconstruirlo sumando piezas
            # (sal_base+extras+otros_ing) -- gross_salary es el campo
            # real y ya calculado por la boleta que suma TODOS los
            # componentes del bruto (base_salary, overtime_amount,
            # vacation_amount, other_income, bono_salarial_amount).
            # La reconstruccion manual anterior no incluia
            # vacation_amount (dias de vacaciones pagados dentro del
            # periodo) -- un componente real del bruto que quedaba
            # fuera del Sub Total sin que nadie lo notara, el mismo
            # patron de bug que ya se corrigio dos veces en este
            # reporte (bonos, luego incapacidad). "Otros" ahora se
            # calcula como la DIFERENCIA real (gross_salary - sal_base
            # - extras), garantizando que las columnas SIEMPRE sumen
            # exactamente el bruto real de la boleta, sin importar que
            # componentes nuevos se agreguen al calculo en el futuro.
            sub_total = round(slip.gross_salary or 0.0, 2)
            otros_ing = round(sub_total - sal_base - extras, 2)

            ccss_emp = slip.ccss_employee or 0
            # FIX DE FONDO: absorber DIRECTAMENTE los montos ya
            # calculados y probados de la boleta (ccss_subsidy_total,
            # ins_subsidy_total), en vez de reconstruirlos con una
            # formula propia -- confirmado que la reconstruccion por
            # dias x tarifa diaria podia dar montos matematicamente
            # imposibles (mayores al salario real de la persona).
            # ccss_subsidy_total YA incluye maternidad combinada con
            # CCSS regular (asi lo calcula el sistema, sin separar el
            # monto en dinero por tipo) -- se usa disability_ids (que
            # SI conoce el tipo real de cada incapacidad vinculada a
            # esta boleta) para decidir a cual columna va el monto: si
            # TODAS son maternidad, va a Maternidad; si no, va a
            # Incapacidad C.C.S.S. Una mezcla de tipos en la misma
            # boleta (caso raro) se deja integra en Incapacidad C.C.S.S.,
            # sin repartir sin base real.
            _ccss_subsidio = round(slip.ccss_subsidy_total or 0.0, 2)
            _tipos_activos = set(
                d.disability_type for d in (slip.disability_ids or [])
                if getattr(d, 'disability_type', False)
            )
            if _tipos_activos and _tipos_activos == {'maternity'}:
                monto_maternidad = _ccss_subsidio
                monto_incap_ccss = 0.0
            else:
                monto_incap_ccss = _ccss_subsidio
                monto_maternidad = 0.0
            monto_incap_ins = round(slip.ins_subsidy_total or 0.0, 2)

            ahorro = _sum_cat(slip, 'ahorro')
            permiso_sg = _sum_cat(slip, 'licencia_sin_goce', 'ausencia')
            renta = slip.income_tax or 0
            # FIX: 'embargo_judicial' nunca fue una categoria real del
            # sistema (confirmado contra la definicion real del campo
            # deduction_category) -- solo 'embargo' existe.
            embargo = _sum_cat(slip, 'embargo')
            # FIX: separar Facturas y Prestamos en DOS columnas propias
            # (antes unificadas en una sola), por pedido explicito, para
            # poder diferenciar los valores de cada concepto. Se
            # identifican por los campos de VINCULO directo, no solo
            # por deduction_category -- 'prestamo', 'prestamo_interno',
            # 'cobro', 'cobro_empleado' y 'employee_charge' nunca
            # fueron categorias reales del sistema, y aunque 'loan' si
            # es real, no todas las lineas de prestamo quedan siempre
            # bien categorizadas con ese texto -- el vinculo directo al
            # registro de origen es la fuente confiable.
            #   - Facturas = Cobros al Empleado (employee_charge_id):
            #     compras/cargos internos que se le cobran al empleado.
            #   - Prestamos = cuotas de prestamos internos
            #     (loan_installment_id, o deduction_category='loan' de
            #     respaldo cuando el vinculo directo no este presente).
            facturas = round(sum(
                l.amount for l in slip.deduction_line_ids
                if getattr(l, 'line_type', '') == 'deduction'
                and getattr(l, 'employee_charge_id', False)
            ), 2)
            prestamos = round(sum(
                l.amount for l in slip.deduction_line_ids
                if getattr(l, 'line_type', '') == 'deduction'
                and not getattr(l, 'employee_charge_id', False)
                and (getattr(l, 'loan_installment_id', False)
                     or getattr(l, 'deduction_category', '') == 'loan')
            ), 2)
            # "Otros" = todo lo demas que no tiene columna propia en este
            # reporte reducido (cuota sindical, cooperativa, ROP,
            # seguro/poliza, pension voluntaria, pension alimentaria,
            # rebajo consolidado de renta).
            otros_ded = _sum_cat(
                slip, 'sindical', 'cooperativa', 'rop', 'seguro',
                'pension_vol', 'pension_alimentaria')
            otros_ded += round(getattr(slip, 'rebajo_renta_amount', 0) or 0, 2)
            # Cualquier linea de deduccion que no encaje en NINGUNA
            # columna especifica de arriba (incluyendo la categoria
            # 'other' o cualquier otra no contemplada) tambien cae aqui
            # -- se excluyen explicitamente las lineas ya contadas en
            # facturas, prestamos y embargo para no duplicarlas.
            categorias_con_columna_propia = {
                'ahorro', 'licencia_sin_goce', 'ausencia', 'embargo',
                'sindical', 'cooperativa', 'rop', 'seguro',
                'pension_vol', 'pension_alimentaria', 'loan',
            }
            otros_ded += round(sum(
                l.amount for l in slip.deduction_line_ids
                if getattr(l, 'line_type', '') == 'deduction'
                and not getattr(l, 'loan_installment_id', False)
                and not getattr(l, 'employee_charge_id', False)
                and getattr(l, 'deduction_category', '') not in categorias_con_columna_propia
            ), 2)

            # FIX: Total y Deposito Patrono ya NO se reconstruyen sumando/
            # restando columnas de este reporte (ej. total_rebajos que se
            # calculaba aqui antes) -- se leen DIRECTAMENTE de los campos
            # reales que ya calculo y confirmo la boleta (salary_payable,
            # deposito_patrono). Reconstruirlos manualmente arrastraba
            # error acumulado cada vez que algun concepto de la boleta no
            # tenia columna propia en este reporte reducido (ej. el
            # subsidio patronal de dias 1-3 de incapacidad, que no es una
            # deduccion sino parte del calculo de ingresos, y por lo tanto
            # nunca se restaba en la reconstruccion manual) -- confirmado
            # con casos reales donde el Total de este reporte no coincidia
            # con el Salario Neto real de la planilla ya pagada.
            #   - Total = salary_payable: neto del empleado incluyendo
            #     subsidios CCSS/INS (lo que efectivamente recibe la persona).
            #   - Deposito Patrono = deposito_patrono: monto real que la
            #     empresa deposita, excluyendo esos subsidios (que paga la
            #     Caja/INS directamente) -- el valor correcto para libros
            #     contables, documentado asi en el propio modelo de boleta.
            total_empleado = round(slip.salary_payable or 0.0, 2)
            deposito_patrono = round(slip.deposito_patrono or 0.0, 2)

            vals = [
                emp.name or '',
                sal_base, otros_ing, extras, sub_total,
                ccss_emp, monto_incap_ccss, monto_incap_ins, monto_maternidad,
                ahorro, permiso_sg, renta, facturas, prestamos, otros_ded, embargo,
                total_empleado, deposito_patrono,
            ]

            for ci, (val, (_, _, tipo, dfmt)) in enumerate(zip(vals, cols)):
                if tipo == 'tot':
                    # El Total de la fila siempre se muestra, incluso
                    # si diera 0 -- a diferencia de ingresos/rebajos
                    # individuales, que se dejan en blanco cuando no
                    # aplican para no saturar visualmente la hoja.
                    ws.write(row, ci, val, ft_tot)
                    # FIX: acumular tambien la columna Total en los
                    # totales de departamento y general -- antes el
                    # 'continue' saltaba esta linea, dejando la
                    # sumatoria de Total vacia en ambas filas de cierre.
                    totales[ci] += val
                    dept_totals[ci] += val
                    continue
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
                color = '#C00000' if tipo == 'ded' else (
                    '#1F4E79' if tipo == 'tot' else '#000000')
                sf = F(bold=True, bg='#F2F2F2', num='#,##0', border=1,
                       fg=color)
                v = dept_totals[ci]
                ws.write(row, ci, v if v else None, sf)
            ws.set_row(row, 14)
            row += 1

        row += 1
        tot_lbl_fmt = F(bold=True, bg=BG_TOT, align='left', border=2, sz=10)
        ws.write(row, 0, 'TOTAL GENERAL', tot_lbl_fmt)
        for ci in range(1, N):
            _, _, tipo, _ = cols[ci]
            color = '#C00000' if tipo == 'ded' else (
                '#1F4E79' if tipo == 'tot' else '#000000')
            tf = F(bold=True, bg=BG_TOT, num='#,##0', border=2,
                   fg=color)
            v = totales[ci]
            ws.write(row, ci, v if v else None, tf)
        ws.set_row(row, 18)

        row += 2
        note_fmt = F(sz=8, align='left', italic=True, fg='#666666', border=0)
        ws.merge_range(row, 0, row, N - 1,
            'Datos leidos directamente de las boletas confirmadas. '
            'Total = Salario Neto real de la boleta (incluye subsidios '
            'CCSS/INS que recibe el empleado). '
            'Deposito Patrono = monto real que la empresa deposita '
            '(excluye subsidios que paga la Caja/INS directamente) -- '
            'valor correcto para libros contables. '
            '"Facturas" = cobros al empleado (ej. compras en la empresa). '
            '"Prestamos Internos" = cuotas de prestamos internos. '
            '"Otros" en Rebajos agrupa: cuota sindical, cooperativa, ROP, '
            'seguro/poliza, pension voluntaria, pension alimentaria, '
            'rebajo consolidado de renta, y cualquier otra deduccion sin '
            'columna propia en este reporte.',
            note_fmt)

        ws.freeze_panes(4, 1)

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
