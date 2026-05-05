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

    date_from = fields.Date(string='Desde', required=True,
                            default=lambda self: fields.Date.context_today(self).replace(day=1))
    date_to = fields.Date(string='Hasta', required=True,
                          default=lambda self: fields.Date.context_today(self))
    branch_id = fields.Many2one('planilla.branch', string='Sucursal (Opcional)')
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        'res.currency',
        string='Moneda del Reporte',
        default=lambda self: self.env.company.currency_id,
        required=True,
        help='Moneda en que se mostraran los montos del reporte. Los salarios en otras monedas seran convertidos.'
    )

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
        # Overlap logic: finds payslips that overlap the selected period
        domain = [
            ('date_from', '<=', self.date_to),
            ('date_to', '>=', self.date_from),
            ('state', '=', 'done'),
            ('company_id', '=', self.company_id.id),
        ]
        if self.branch_id:
            domain.append(('branch_id', '=', self.branch_id.id))
        return self.env['planilla.payslip.cr'].search(domain)

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

        payslips = self._get_payslips()
        if not payslips:
            raise UserError('No hay boletas pagadas en el periodo seleccionado.')

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

        # -- Encabezado del reporte -----------------------------------
        title_fmt = wb.add_format({'bold': True, 'font_size': 14, 'color': '#1F4E79'})
        ws.write(0, 0, 'PLANILLA DETALLADA -- PLANILLA CR', title_fmt)
        ws.write(1, 0, f'Periodo: {self.date_from} al {self.date_to}')
        ws.write(2, 0, f'Empresa: {self.company_id.name}')
        ws.write(3, 0, f'Sucursal: {self.branch_id.name if self.branch_id else "Todas"}')

        # -- Columnas ------------------------------------------------
        # ── Fórmulas de cierre fila a fila ────────────────────────────────
        # INGRESOS:
        #   SalBruto  = SalBase + HExtras + BonSal + Vacaciones + OtrosIng - PermisoSinGoce
        # COTIZACIÓN:
        #   BaseCotiz = base_cotizable_final  (ajustada por incap + licencias)
        #   CCSSOb    = 10.83% × BaseCotiz
        #   CCSSpat   = 26.83% × BaseCotiz
        # DEDUCCIONES:
        #   TotalDed  = CCSSOb + Renta + OtrasDed
        # NETO:
        #   SalNeto   = SalBruto - TotalDed + SubsidioCCSS + SubsidioINS
        #   (SubsidioCCSS/INS = días 4+ cubiertos por la CCSS/INS, no por patrono)
        columns = [
            # ── Identificación ────────────────────────────────────────────────
            ('Empleado',              28),
            ('Sucursal',              16),
            ('Cédula',                14),
            # ── Contexto de días ──────────────────────────────────────────────
            ('Días Periodo',          10),
            ('Días Trabajados',       12),
            ('Días Incapacidad',      12),
            ('Días Lic. Sin Goce',    14),
            # ── Ingresos ──────────────────────────────────────────────────────
            ('Salario Base (CRC)',    16),
            ('H. Extras (CRC)',       14),
            ('Bonos Sal. (CRC)',      14),
            ('Vacaciones (CRC)',      14),
            ('Otros Ingresos (CRC)', 16),
            ('Permiso sin Goce (CRC)',18),
            ('Salario Bruto (CRC)',   16),
            # ── Cotización CCSS ───────────────────────────────────────────────
            ('Base Cotizable (CRC)',  16),
            ('CCSS Obrero (CRC)',     14),
            ('Imp. Renta (CRC)',      15),
            ('Otras Ded. (CRC)',      14),
            ('Total Ded. Obrero (CRC)',17),
            # ── Subsidios ─────────────────────────────────────────────────────
            ('Subsidio CCSS (CRC)',   15),
            ('Subsidio INS (CRC)',    14),
            ('Salario Neto (CRC)',    16),
            # ── Cargas patronales ─────────────────────────────────────────────
            ('CCSS Patronal (CRC)',   15),
            ('INS Patronal (CRC)',    14),
            ('Prov. Aguinaldo (CRC)', 16),
            ('Prov. Cesantía (CRC)',  16),
            ('Prov. Vacaciones (CRC)',16),
            ('Costo Total Emp. (CRC)',18),
        ]
        # Encabezados con color por sección
        # Mapeo col_index -> (header_format, data_format)
        # Secciones: [0-2]=id, [3-6]=dias, [7-13]=ing, [14-15]=cotiz,
        #            [16-18]=ded, [19-20]=sub, [21]=neto, [22-27]=pat
        col_formats = (
            [hdr_id,   money]      * 3   # 0-2 id (pero 0,1,2 son texto)
        )
        section_map = {
            # col_idx: (hdr_fmt, data_fmt, is_int)
            0:  (hdr_id,    normal,      False),
            1:  (hdr_id,    normal,      False),
            2:  (hdr_id,    normal,      False),
            3:  (hdr_dias,  int_dias,    True),
            4:  (hdr_dias,  int_dias,    True),
            5:  (hdr_dias,  int_dias,    True),
            6:  (hdr_dias,  int_dias,    True),
            7:  (hdr_ing,   money_ing,   False),
            8:  (hdr_ing,   money_ing,   False),
            9:  (hdr_ing,   money_ing,   False),
            10: (hdr_ing,   money_ing,   False),
            11: (hdr_ing,   money_ing,   False),
            12: (hdr_ing,   money_ing,   False),
            13: (hdr_ing,   money_ing,   False),
            14: (hdr_cotiz, money_cotiz, False),
            15: (hdr_cotiz, money_cotiz, False),
            16: (hdr_ded,   money_ded,   False),
            17: (hdr_ded,   money_ded,   False),
            18: (hdr_ded,   money_ded,   False),
            19: (hdr_ded,   money_ded,   False),
            20: (hdr_sub,   money_sub,   False),
            21: (hdr_sub,   money_sub,   False),
            22: (hdr_ing,   money_ing,   False),   # Salario Neto = ingreso color
            23: (hdr_pat,   money_pat,   False),
            24: (hdr_pat,   money_pat,   False),
            25: (hdr_pat,   money_pat,   False),
            26: (hdr_pat,   money_pat,   False),
            27: (hdr_pat,   money_pat,   False),
            28: (hdr_pat,   money_pat,   False),
        }
        row = 5
        for col, (name, width) in enumerate(columns):
            hfmt = section_map.get(col, (hdr_id, money, False))[0]
            ws.write(row, col, name, hfmt)
            ws.set_column(col, col, width)
        ws.set_row(row, 36)

        # -- Datos por boleta -----------------------------------------
        row = 6
        totals = [0.0] * len(columns)
        for slip in payslips.sorted(key=lambda s: s.employee_id.name):
            c = self._convert_amount
            # ── Contexto de días ──────────────────────────────────────────
            dias_periodo   = slip.days_in_period or 0
            dias_trabajados = slip.dias_laborados_periodo or 0
            dias_incap     = slip.disability_days_in_period or 0
            dias_licencia  = round(sum(
                l.amount for l in slip.deduction_line_ids
                if l.line_type == 'deduction'
                and l.deduction_category in ('licencia_sin_goce', 'ausencia')
            ) / (slip.base_salary / (slip.days_in_period or 1) or 1), 1) if slip.base_salary else 0

            # ── Ingresos ──────────────────────────────────────────────────────
            bono_salarial  = c(slip.bono_salarial_amount or 0.0, slip)
            # OtrosIng = todo lo que va al bruto además de base/extras/bonos/vacaciones.
            # Incluye other_income + cualquier ingreso adicional no categorizado.
            otros_ingresos = max(round(
                c(slip.gross_salary, slip)
                - c(slip.base_salary, slip)
                - c(slip.overtime_amount, slip)
                - bono_salarial
                - c(slip.vacation_amount, slip),
                2), 0.0)
            # Permiso sin goce leído desde líneas (evita campo store stale).
            permiso_sin_goce = round(sum(
                l.amount for l in slip.deduction_line_ids
                if l.line_type == 'deduction'
                and l.deduction_category in ('licencia_sin_goce', 'ausencia')
            ), 2)

            # ── Cotización ────────────────────────────────────────────────────
            # base_cotizable_final = campo del modelo que ya incluye todos los
            # ajustes: incapacidades (Art.79 CT) + licencias sin goce.
            # CCSSOb = 10.83% × base_cotizable_final  (verificable por el usuario).
            base_cotiz = c(slip.base_cotizable_final or 0.0, slip)

            # ── Deducciones ───────────────────────────────────────────────────
            # OtrasDed = todo lo que deduce además de CCSS y Renta:
            # pensiones, embargos, cobros, sindicato, cooperativa, préstamos, etc.
            otras_ded = max(round(
                c(slip.total_employee_deductions, slip)
                - c(slip.ccss_employee, slip)
                - c(slip.income_tax, slip),
                2), 0.0)

            # ── Subsidios ─────────────────────────────────────────────────────
            # Subsidio CCSS: días 4+ maternidad/enfermedad, pasa por el patrono.
            # Subsidio INS : riesgo laboral, INS paga directamente al empleado.
            # Paternidad   : 8 días hábiles a cargo del patrono (Ley 8107).
            # Ambos se muestran por separado para transparencia.
            subsidio_ccss = max(round(
                c(slip.ccss_subsidy_total or 0.0, slip)
                + c(slip.paternity_amount  or 0.0, slip), 2), 0.0)
            subsidio_ins  = max(c(slip.ins_subsidy_total or 0.0, slip), 0.0)

            data = [
                # Identificación
                slip.employee_id.name,
                slip.branch_id.name or '',
                slip.employee_id.identification_id or '',
                # Días
                float(dias_periodo),
                float(dias_trabajados),
                float(dias_incap),
                float(dias_licencia),
                # Ingresos
                c(slip.base_salary, slip),
                c(slip.overtime_amount, slip),
                bono_salarial,
                c(slip.vacation_amount, slip),
                otros_ingresos,
                permiso_sin_goce,
                c(slip.gross_salary, slip),
                # Cotización y deducciones
                base_cotiz,
                c(slip.ccss_employee, slip),
                c(slip.income_tax, slip),
                otras_ded,
                c(slip.total_employee_deductions, slip),
                # Subsidios y neto
                subsidio_ccss,
                subsidio_ins,
                c(slip.net_salary, slip),
                # Cargas patronales
                c(slip.ccss_employer, slip),
                c(slip.ins_employer, slip),
                c(slip.aguinaldo_provision, slip),
                c(slip.cesantia_provision, slip),
                c(slip.vacation_provision, slip),
                c(slip.total_employer_cost, slip),
            ]
            for col, val in enumerate(data):
                _, dfmt, is_int = section_map.get(col, (None, money, False))
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

        wb.close()
        xlsx_data = base64.b64encode(output.getvalue()).decode()
        filename = f'Planilla_{self.date_from}_{self.date_to}.xlsx'

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
