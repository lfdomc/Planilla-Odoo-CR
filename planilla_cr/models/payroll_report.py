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

    # ── Selección de planillas ────────────────────────────────────────────────
    # El usuario escoge una o más planillas ya procesadas (state=done).
    # date_from/date_to se calculan automáticamente desde las planillas elegidas.
    payroll_run_ids = fields.Many2many(
        'planilla.run.cr',
        'planilla_report_run_rel',
        'wizard_id', 'run_id',
        string='Planillas',
        domain=[('state', '=', 'done')],
        required=True,
    )
    date_from = fields.Date(
        string='Desde',
        compute='_compute_dates_from_runs',
        store=False,
    )
    date_to = fields.Date(
        string='Hasta',
        compute='_compute_dates_from_runs',
        store=False,
    )

    branch_id = fields.Many2one('planilla.branch', string='Sucursal (Opcional)')
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        'res.currency',
        string='Moneda del Reporte',
        default=lambda self: self.env.company.currency_id,
        required=True,
        help='Moneda en que se mostraran los montos del reporte.',
    )

    @api.depends('payroll_run_ids')
    def _compute_dates_from_runs(self):
        for rec in self:
            if rec.payroll_run_ids:
                rec.date_from = min(rec.payroll_run_ids.mapped('date_start'))
                rec.date_to   = max(rec.payroll_run_ids.mapped('date_end'))
            else:
                rec.date_from = fields.Date.context_today(rec)
                rec.date_to   = fields.Date.context_today(rec)

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
        # Obtener boletas directamente de los runs seleccionados.
        # Esto garantiza que solo aparecen las boletas de las planillas
        # que el usuario eligió — sin duplicados ni periodos adyacentes.
        if not self.payroll_run_ids:
            return self.env['planilla.payslip.cr']
        slips = self.payroll_run_ids.mapped('payslip_ids').filtered(
            lambda s: s.state == 'done'
        )
        if self.branch_id:
            slips = slips.filtered(lambda s: s.branch_id == self.branch_id)
        return slips

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

        if not self.payroll_run_ids:
            raise UserError('Debe seleccionar al menos una planilla.')
        payslips = self._get_payslips()
        if not payslips:
            raise UserError('Las planillas seleccionadas no tienen boletas pagadas.')

        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True})
        ws = wb.add_worksheet('Planilla Detalle')

        # -- Formatos ------------------------------------------------
        # Encabezados por sección (colores distintos para lectura rápida)
        def _hdr(color):
            return wb.add_format({
                'bold': True, 'bg_color': color, 'font_color': 'white',
                'border': 1, 'align': 'center', 'valign': 'vcenter',
                'text_wrap': True, 'font_size': 9,
            })
        hdr_id    = _hdr('#1F4E79')  # azul oscuro — identificación
        hdr_dias  = _hdr('#833C00')  # café       — días
        hdr_ing   = _hdr('#375623')  # verde      — ingresos
        hdr_cotiz = _hdr('#7B3F8C')  # morado     — cotización
        hdr_ded   = _hdr('#C00000')  # rojo       — deducciones
        hdr_sub   = _hdr('#1F618D')  # azul medio — subsidios
        hdr_pat   = _hdr('#4A4A4A')  # gris       — cargas patronales

        # Colores de celda por sección
        bg_dias  = '#FFF2CC'
        bg_ing   = '#E2EFDA'
        bg_cotiz = '#EAD1DC'
        bg_ded   = '#FCE4D6'
        bg_sub   = '#DEEAF1'
        bg_pat   = '#EDEDED'

        def _money(bg=None):
            fmt = {'num_format': '#,##0.00', 'border': 1, 'font_size': 9}
            if bg: fmt['bg_color'] = bg
            return wb.add_format(fmt)
        def _int_fmt(bg=None):
            fmt = {'num_format': '0', 'border': 1, 'font_size': 9, 'align': 'center'}
            if bg: fmt['bg_color'] = bg
            return wb.add_format(fmt)
        def _txt(bg=None):
            fmt = {'border': 1, 'font_size': 9}
            if bg: fmt['bg_color'] = bg
            return wb.add_format(fmt)

        money     = _money()
        money_ing  = _money(bg_ing)
        money_cotiz= _money(bg_cotiz)
        money_ded  = _money(bg_ded)
        money_sub  = _money(bg_sub)
        money_pat  = _money(bg_pat)
        int_dias   = _int_fmt(bg_dias)
        normal     = _txt()

        total_lbl = wb.add_format({'bold': True, 'bg_color': '#D9E1F2', 'border': 1, 'font_size': 9})
        total_num = wb.add_format({'bold': True, 'bg_color': '#D9E1F2', 'num_format': '#,##0.00', 'border': 1, 'font_size': 9})
        # Pensionados: fondo naranja suave para toda la fila CCSS
        money_pen_ccss = wb.add_format({'num_format': '#,##0.00', 'border': 1, 'font_size': 9,
                                        'bg_color': '#FCE4D6', 'bold': True})   # naranja suave
        # Encabezado especial para primera sección de pensionados
        hdr_pen = wb.add_format({'bold': True, 'bg_color': '#ED7D31', 'font_color': 'white',
                                  'border': 1, 'font_size': 9, 'align': 'center'})

        # -- Encabezado del reporte -----------------------------------
        title_fmt = wb.add_format({'bold': True, 'font_size': 14, 'color': '#1F4E79'})
        ws.write(0, 0, 'PLANILLA DETALLADA -- PLANILLA CR', title_fmt)
        run_names = ', '.join(self.payroll_run_ids.mapped('name'))
        ws.write(1, 0, run_names)
        ws.write(2, 0, f'Empresa: {self.company_id.name}')
        ws.write(3, 0, f'Sucursal: {self.branch_id.name if self.branch_id else "Todas"}')

        # -- Columnas ------------------------------------------------
        # Columnas — mapeo 1:1 con campos de planilla.payslip.cr
        # El orden espeja la boleta del empleado: ingresos → deducciones → neto → cargas
        columns = [
            # ── A-C Identificación ───────────────────────────────────────────
            ('Empleado',                    28),
            ('Sucursal',                    16),
            ('Cédula',                      14),
            # ── D-F Período ──────────────────────────────────────────────────
            ('Período',                     22),
            ('Frecuencia',                  14),
            ('Días Laborados',              12),
            # ── G-K Ingresos (= boleta sección Ingresos) ─────────────────────
            ('Salario Base Quincenal',      20),
            ('Horas Extras',                16),
            ('Bonos Salariales (CCSS)',     20),
            ('Vacaciones Pagadas',          18),
            ('Otros Ingresos',              16),
            ('Permiso sin Goce',            18),
            ('Reducc. Incapacidad',         18),   # base - gross - permiso cuando hay incapac
            ('Salario Bruto',               18),
            # ── L-P Deducciones (= boleta sección Deducciones) ───────────────
            ('Base Cotizable CCSS',         18),
            ('CCSS Obrero 10.83%',          18),
            ('Impuesto Renta',              16),
            ('Otras Deducciones',           18),
            ('Total Deducciones Obrero',    20),
            # ── Q-S Subsidios + Neto ──────────────────────────────────────────
            ('Subsidio CCSS/Mat.',         18),
            ('Otros Sub. en Neto',         16),
            ('Salario Neto a Recibir',     20),
            ('INS Pago Directo',           16),
            # ── T Depósito Patrono ────────────────────────────────────────────
            ('Depósito Patrono',           20),
            # ── U (vacía separadora) ──────────────────────────────────────────
            ('',                            2),
            # ── V-AB Cargas Patronales (= boleta sección Cargas) ─────────────
            ('CCSS Patronal 26.83%',       20),
            ('INS Riesgos del Trabajo',    20),
            ('Provisión Aguinaldo',        18),
            ('Provisión Cesantía',         18),
            ('Provisión Vacaciones',       18),
            ('Costo Total Patronal',       20),
        ]
        # Encabezados con color por sección
        # Mapeo col_index -> (header_format, data_format)
        # Secciones: [0-2]=id, [3-6]=dias, [7-13]=ing, [14-15]=cotiz,
        #            [16-18]=ded, [19-20]=sub, [21]=neto, [22-27]=pat
        # section_map: col_idx → (header_fmt, data_fmt, is_int)
        # A-C id, D-F periodo, G-N ingresos, L-P deducciones,
        # Q-S subsidios+neto, T deposito, U vacía, V-AB patronales
        section_map = {}
        for i in range(3):    section_map[i] = (hdr_id,    normal,      False)
        section_map[3] = (hdr_dias, normal,   False)   # D Período (texto)
        section_map[4] = (hdr_dias, normal,   False)   # E Frecuencia (texto)
        section_map[5] = (hdr_dias, int_dias, True)    # F Días Laborados
        for i in range(6,13): section_map[i] = (hdr_ing,   money_ing,   False)  # G-M ingresos
        section_map[13] = (hdr_ded,  money_ded,   False)  # N Reducción Incapacidad (rebajo)
        section_map[14] = (hdr_ing,  money_ing,   False)  # O Salario Bruto
        for i in range(15,20):section_map[i] = (hdr_cotiz, money_cotiz, False)  # P-T cotiz/ded
        for i in range(20,24):section_map[i] = (hdr_sub,   money_sub,   False)  # sub+neto+ins_info
        section_map[24] = (hdr_ing,  money_ing,  False)   # Depósito Patrono
        section_map[25] = (hdr_id,   normal,     False)   # vacía
        for i in range(26,32):section_map[i] = (hdr_pat,  money_pat,  False)   # patronales
        row = 5
        for col, (name, width) in enumerate(columns):
            hfmt = section_map.get(col, (hdr_id, money, False))[0]
            ws.write(row, col, name, hfmt)
            ws.set_column(col, col, width)
        ws.set_row(row, 36)

        # -- Datos por boleta -----------------------------------------
        row = 6
        totals = [0.0] * len(columns)
        # Pensionados primero, luego orden alfabético
        def _sort_key(s):
            is_pen = 0 if (s.employee_id.pensioner_type or 'none') != 'none' else 1
            return (is_pen, s.employee_id.name or '')
        sorted_slips = sorted(payslips, key=_sort_key)
        past_first_regular = False
        # Insertar encabezado de sección pensionados si existen
        has_pensionados = any((s.employee_id.pensioner_type or 'none') != 'none' for s in sorted_slips)
        if has_pensionados:
            ws.merge_range(row, 0, row, len(columns)-1,
                           '⚠ PENSIONADOS SECTOR PÚBLICO / IVM — CCSS Obrero 6.50% (exoneración IVM Art. 4 Ley Const. CCSS)',
                           hdr_pen)
            ws.set_row(row, 16)
            row += 1
        for slip in sorted_slips:
            c = self._convert_amount
            # ── Período — los 3 campos que tienen columna en el reporte
            periodo_str = (
                f"{slip.date_from.strftime('%d/%b/%Y')} – {slip.date_to.strftime('%d/%b/%Y')}"
                if slip.date_from and slip.date_to else ''
            )
            freq_map = {'biweekly': 'Quincenal', 'monthly': 'Mensual',
                        'weekly': 'Semanal', 'bimonthly': 'Bimensual'}
            freq_str = freq_map.get(getattr(slip, 'period_type', None) or
                                    getattr(slip, 'frequency', ''), 'Quincenal')


            # Todos los cálculos se leen directo del slip — sin variables intermedias

            # Permiso sin goce — dos fuentes, la que tenga valor gana:
            # 1. Líneas explícitas de deducción (evita campo stale)
            permiso_lines = round(sum(
                l.amount for l in slip.deduction_line_ids
                if l.line_type == 'deduction'
                and l.deduction_category in ('licencia_sin_goce', 'ausencia')
            ), 2)
            # 2. Reducción implícita: cuando gross < base+HE+bonos+vac
            #    (licencia via proporcional, hr.leave, u otro mecanismo que
            #     reduce gross_salary sin crear línea de deducción explícita)
            bruto_esperado = round(
                c(slip.base_salary or 0, slip)
                + c(slip.overtime_amount or 0, slip)
                + c(slip.bono_salarial_amount or 0, slip)
                + c(slip.vacation_amount or 0, slip), 2)
            permiso_implicito = max(round(bruto_esperado - c(slip.gross_salary or 0, slip), 2), 0.0)
            # Usar el mayor de los dos (líneas explícitas vs reducción implícita)
            permiso_real = max(permiso_lines, permiso_implicito)

            data = [
                slip.employee_id.name or '',
                slip.branch_id.name   or '',
                slip.employee_id.identification_id or '',
                periodo_str,
                freq_str,
                float(slip.dias_laborados_periodo or slip.days_worked or 0),
                c(slip.base_salary            or 0, slip),
                c(slip.overtime_amount         or 0, slip),
                c(slip.bono_salarial_amount    or 0, slip),
                c(slip.vacation_amount         or 0, slip),
                max(round(
                    c(slip.gross_salary or 0, slip)
                    - c(slip.base_salary or 0, slip)
                    - c(slip.overtime_amount or 0, slip)
                    - c(slip.bono_salarial_amount or 0, slip)
                    - c(slip.vacation_amount or 0, slip)
                    + permiso_real,
                    2), 0.0),
                permiso_real,
                max(round(
                    c(slip.base_salary or 0, slip)
                    - c(slip.gross_salary or 0, slip)
                    - permiso_real,
                    2), 0.0),
                c(slip.gross_salary            or 0, slip),
                c(slip.base_cotizable_final    or 0, slip),
                c(slip.ccss_employee           or 0, slip),
                c(slip.income_tax              or 0, slip),
                max(round(
                    c(slip.total_employee_deductions or 0, slip)
                    - c(slip.ccss_employee or 0, slip)
                    - c(slip.income_tax    or 0, slip), 2), 0.0),
                c(slip.total_employee_deductions or 0, slip),
                c((slip.ccss_subsidy_total or 0) + (slip.paternity_amount or 0), slip),
                max(round(
                    c(slip.net_salary or 0, slip)
                    - c(slip.gross_salary or 0, slip)
                    + c(slip.total_employee_deductions or 0, slip)
                    - c((slip.ccss_subsidy_total or 0) + (slip.paternity_amount or 0), slip),
                    2), 0.0),
                c(slip.net_salary          or 0, slip),
                c(slip.ins_subsidy_total   or 0, slip),
                c(slip.deposito_patrono    or 0, slip),
                '',
                c(slip.ccss_employer       or 0, slip),
                c(slip.ins_employer        or 0, slip),
                c(slip.aguinaldo_provision or 0, slip),
                c(slip.cesantia_provision  or 0, slip),
                c(slip.vacation_provision  or 0, slip),
                c(slip.total_employer_cost or 0, slip),
            ]
            is_pensionado = (slip.employee_id.pensioner_type or 'none') != 'none'
            # Separador cuando cambia de pensionados a no-pensionados
            if not is_pensionado and not past_first_regular:
                past_first_regular = True
                # Fila separadora con etiqueta
                ws.merge_range(row, 0, row, len(columns)-1,
                               'EMPLEADOS REGULARES', hdr_pen)
                ws.set_row(row, 14)
                row += 1
            for col, val in enumerate(data):
                _, dfmt, is_int = section_map.get(col, (None, money, False))
                # Col P (15) = CCSS Obrero → usar color especial para pensionados
                if is_pensionado and col == 15:
                    dfmt = money_pen_ccss
                if isinstance(val, str):
                    ws.write(row, col, val, dfmt)
                elif is_int:
                    ws.write(row, col, int(val or 0), dfmt)
                else:
                    ws.write(row, col, val or 0.0, dfmt)
                    totals[col] = totals[col] + (val or 0.0)
            row += 1

        # -- Fila de totales -----------------------------------------
        ws.write(row, 0, 'TOTALES', total_lbl)
        ws.write(row, 1, '', total_lbl)
        ws.write(row, 2, '', total_lbl)
        for col in range(3, len(columns)):
            ws.write(row, col, totals[col], total_num)

        # Nota al pie — tope salarial CCSS y fórmulas de cierre
        row += 2
        fmt_note = wb.add_format({'font_size': 8, 'font_color': '#888888',
                                   'italic': True, 'text_wrap': True})
        ws.merge_range(row, 0, row, 14,
            'FÓRMULAS DE CIERRE: '
            '(1) SalBruto = Base + HExtras + Bonos + Vac + OtrosIng − Permiso − ReducIncap  '
            '(2) TotalDed = CCSS + Renta + OtrasDed  '
            '(3) SalNeto = Bruto − TotalDed + SubCCSS + OtrosSub  '
            '(4) CCSS = 10.83% × BaseCotiz (3 empleados sobre tope salarial CCSS: correcto)  '
            '| INS Pago Directo = informativo, INS deposita directo al empleado, NO reduce el Depósito Patrono.  '
            '| Depósito Patrono = valor del sistema (correcto). '
            'Karla: split 50/50 maternidad → Dep = Neto − SubCCSS/2 (CCSS deposita su mitad directa).',
            fmt_note)

        wb.close()
        xlsx_data = base64.b64encode(output.getvalue()).decode()
        # Nombre: YYYY_Mes_Primera/Segunda_Quincena (ej. 2026_Abril_Primera_Quincena)
        import re as _re
        def _run_slug(run):
            name = run.name or ''
            # Detectar año desde name o date_start
            year = run.date_start.year if run.date_start else ''
            # Detectar mes
            month_es = {1:'Enero',2:'Febrero',3:'Marzo',4:'Abril',5:'Mayo',
                        6:'Junio',7:'Julio',8:'Agosto',9:'Septiembre',
                        10:'Octubre',11:'Noviembre',12:'Diciembre'}
            month = month_es.get(run.date_start.month, '') if run.date_start else ''
            # Detectar primera/segunda quincena
            q = 'Primera_Quincena' if (run.date_start and run.date_start.day <= 15) else 'Segunda_Quincena'
            return f'{year}_{month}_{q}'
        run_slug = '_'.join(_run_slug(r) for r in self.payroll_run_ids[:2])
        filename = f'Planilla_{run_slug}.xlsx'

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
