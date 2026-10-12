"""
Detalle Mensual de Aguinaldo -- Odoo, quincena por quincena.

Genera un Excel con el MISMO layout que la hoja "Agui." del Excel
oficial de RRHH (una fila por empleado, una columna por quincena desde
diciembre del año anterior hasta noviembre), pero mostrando en detalle
cada componente de la formula que usa rate_helper.calc_aguinaldo_periodo()
para esa quincena -- Sub Total Quincenal, Costo Patrono Dias 1-3,
Incapacidad CCSS, Incapacidad INS, Permiso Sin Goce, y el valor final
ya dividido entre 12 -- para poder comparar celda por celda contra el
Excel real y diagnosticar en que mes exacto (si en alguno) aparece una
diferencia, o si el problema esta en el Acumulado Inicial en vez de en
una quincena especifica.

Dos hojas de salida:
  1. "Resumen (como Excel)": una fila por empleado, una columna por
     quincena, MISMO formato que la hoja "Agui." -- para pegar al lado
     del Excel real y comparar visualmente.
  2. "Detalle Componentes": una fila por CADA quincena de CADA
     empleado, con todos los componentes de la formula desglosados --
     para diagnosticar con precision en que componente especifico
     aparece una diferencia (¿es el Sub Total? ¿el subsidio CCSS? ¿el
     costo patronal? ¿el permiso sin goce?).
"""
import io
import base64
import logging
from datetime import date
from dateutil.relativedelta import relativedelta
from odoo import models, fields
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AguinaldoDetalleMensualWizard(models.TransientModel):
    _name = 'planilla.aguinaldo.detalle.mensual.wizard'
    _description = 'Detalle Mensual de Aguinaldo (quincena por quincena, vs Excel)'

    company_id = fields.Many2one(
        'res.company', required=True,
        default=lambda self: self.env.company,
    )
    year = fields.Integer(
        string='Año del Aguinaldo', required=True,
        default=lambda self: date.today().year,
        help='Periodo legal: 1 de diciembre del año anterior al 30 de '
             'noviembre de este año (Art. 228 CT). Se generan las 24 '
             'quincenas de ese rango, igual que el Excel oficial.',
    )
    branch_id = fields.Many2one('planilla.branch', string='Sucursal (opcional)')
    employee_id = fields.Many2one(
        'hr.employee', string='Empleado (opcional)',
        help='Deje vacio para incluir a todos los empleados. Util '
             'para diagnosticar un caso puntual sin generar el reporte '
             'completo de toda la planilla.',
    )
    incluir_confirmadas = fields.Boolean(
        string='Incluir boletas Confirmadas (no solo Pagadas)',
        default=False,
        help='Si esta marcado, incluye boletas en estado "confirmed" '
             'ademas de "done" -- util si la ultima quincena aun no '
             'esta formalmente pagada pero ya tiene datos reales.',
    )

    def action_generate(self):
        self.ensure_one()
        try:
            import xlsxwriter
        except ImportError:
            raise UserError('xlsxwriter no esta instalado.')

        rh = self.env['planilla.rate.helper']

        # -- 24 quincenas del periodo legal, dic(year-1) - nov(year) ---
        periodo_inicio = date(self.year - 1, 12, 1)
        quincenas = []  # lista de (label_mes, num_quincena, fecha_desde, fecha_hasta)
        cursor = periodo_inicio
        meses_labels = ['Dic-Ant', 'Ene', 'Feb', 'Mar', 'Abr', 'May',
                         'Jun', 'Jul', 'Ago', 'Set', 'Oct', 'Nov']
        for idx, mes_label in enumerate(meses_labels):
            mes_inicio = cursor
            mes_fin = cursor + relativedelta(months=1) - relativedelta(days=1)
            mitad = date(mes_inicio.year, mes_inicio.month, 15)
            quincenas.append((mes_label, 'I', mes_inicio, mitad))
            quincenas.append((mes_label, 'II', mitad + relativedelta(days=1), mes_fin))
            cursor = cursor + relativedelta(months=1)

        # -- Empleados a incluir ----------------------------------------
        emp_domain = [('company_id', '=', self.company_id.id)]
        if self.branch_id:
            emp_domain.append(('branch_id', '=', self.branch_id.id))
        if self.employee_id:
            emp_domain.append(('id', '=', self.employee_id.id))
        empleados = self.env['hr.employee'].search(emp_domain, order='name')

        if not empleados:
            raise UserError('No se encontraron empleados con los filtros indicados.')

        estados = ('done', 'confirmed') if self.incluir_confirmadas else ('done',)

        # -- Recolectar datos por empleado x quincena --------------------
        # data[emp.id][(mes_label, num_q)] = dict con componentes
        #
        # cubierto_inicial: misma decision de negocio centralizada que
        # calc_aguinaldo_completo y la Auditoria de Aguinaldo usan
        # (rate_helper.get_aguinaldo_fecha_corte) -- toda quincena cuya
        # fecha_hasta caiga en o antes del corte real del Acumulado
        # Inicial del empleado ya esta cubierta ahi, y NO debe sumarse
        # como devengado nuevo en este detalle (evita duplicarla).
        data = {}
        cubierto_inicial = {}  # emp.id -> fecha limite cubierta por el Acumulado Inicial
        for emp in empleados:
            data[emp.id] = {}
            corte = rh.get_aguinaldo_fecha_corte(emp, self.year)
            cubierto_inicial[emp.id] = (
                corte['fecha_desde'] - relativedelta(days=1)
                if corte['tiene_inicial'] else None
            )
            slips = self.env['planilla.payslip.cr'].search([
                ('employee_id', '=', emp.id),
                ('state', 'in', estados),
                ('date_from', '>=', periodo_inicio),
                ('date_to', '<=', date(self.year, 11, 30)),
            ], order='date_from')
            slips_by_range = {}
            for s in slips:
                slips_by_range[(s.date_from, s.date_to)] = s

            for mes_label, num_q, f_desde, f_hasta in quincenas:
                limite = cubierto_inicial[emp.id]
                if limite and f_hasta <= limite:
                    # Esta quincena ya esta cubierta por el Acumulado
                    # Inicial (Art. 228 CT, pre-implementacion) del
                    # empleado -- NO se debe sumar como devengado nuevo
                    # aunque exista boleta cargada para ese periodo,
                    # porque duplicaria el monto (una vez en el
                    # acumulado inicial, otra vez aqui).
                    data[emp.id][(mes_label, num_q)] = {
                        'tiene_boleta': False,
                        'cubierto_inicial': True,
                        'slip_name': '',
                        'estado': '',
                        'sub_total': 0.0,
                        'costo_patrono': 0.0,
                        'incap_ccss': 0.0,
                        'incap_ins': 0.0,
                        'psgs': 0.0,
                        'devengado': 0.0,
                        'valor_quincena': 0.0,
                    }
                    continue

                slip = slips_by_range.get((f_desde, f_hasta))
                if not slip:
                    # Buscar por solapamiento si las fechas no calzan exacto
                    candidatos = [
                        s for s in slips
                        if s.date_from and s.date_to
                        and s.date_from <= f_hasta and s.date_to >= f_desde
                    ]
                    slip = candidatos[0] if candidatos else None

                if slip:
                    sub_total = slip.gross_salary or 0.0
                    costo_patrono = round(slip.costo_patrono_periodo or 0.0, 2)
                    incap_ccss = round(slip.ccss_subsidy_total or 0.0, 2)
                    incap_ins = round(slip.ins_subsidy_total or 0.0, 2)
                    psgs = round(sum(
                        l.amount for l in slip.deduction_line_ids
                        if l.deduction_category == 'licencia_sin_goce'
                        and l.line_type == 'deduction'
                    ), 2)
                    devengado = max(round(
                        sub_total + costo_patrono - incap_ccss - incap_ins - psgs, 2
                    ), 0.0)
                    valor_quincena = round(devengado / 12.0, 2)
                    data[emp.id][(mes_label, num_q)] = {
                        'tiene_boleta': True,
                        'cubierto_inicial': False,
                        'slip_name': slip.name or '',
                        'estado': slip.state,
                        'sub_total': sub_total,
                        'costo_patrono': costo_patrono,
                        'incap_ccss': incap_ccss,
                        'incap_ins': incap_ins,
                        'psgs': psgs,
                        'devengado': devengado,
                        'valor_quincena': valor_quincena,
                    }
                else:
                    data[emp.id][(mes_label, num_q)] = {
                        'tiene_boleta': False,
                        'cubierto_inicial': False,
                        'slip_name': '',
                        'estado': '',
                        'sub_total': 0.0,
                        'costo_patrono': 0.0,
                        'incap_ccss': 0.0,
                        'incap_ins': 0.0,
                        'psgs': 0.0,
                        'devengado': 0.0,
                        'valor_quincena': 0.0,
                    }

        # -- Construir el Excel de salida --------------------------------
        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True})

        title_fmt = wb.add_format({'bold': True, 'font_size': 13,
                                    'bg_color': '#1F4E79', 'font_color': 'white'})
        hdr_fmt = wb.add_format({'bold': True, 'bg_color': '#2E4057',
                                  'font_color': 'white', 'border': 1,
                                  'text_wrap': True, 'align': 'center',
                                  'valign': 'vcenter'})
        lbl_fmt = wb.add_format({'align': 'left', 'border': 1})
        num_fmt = wb.add_format({'num_format': '#,##0.00', 'border': 1})
        num_bold_fmt = wb.add_format({'num_format': '#,##0.00', 'border': 1, 'bold': True})
        acum_fmt = wb.add_format({'num_format': '#,##0.00', 'border': 1, 'bold': True,
                                   'bg_color': '#D9E1F2'})
        no_boleta_fmt = wb.add_format({'align': 'center', 'border': 1,
                                        'bg_color': '#FFF2CC', 'italic': True})
        cubierto_fmt = wb.add_format({'align': 'center', 'border': 1,
                                       'bg_color': '#D6DCE5', 'italic': True,
                                       'font_color': '#666666'})

        # ---- HOJA 1: Resumen, mismo layout que la hoja "Agui." del Excel ----
        ws1 = wb.add_worksheet('Resumen (como Excel)')
        ws1.merge_range(
            0, 0, 0, len(quincenas) + 1,
            f'AGUINALDO {self.year} -- Detalle Quincenal (formato equivalente a la '
            f'hoja "Agui." del Excel de RRHH) -- Metodo: rate_helper.calc_aguinaldo_periodo()',
            title_fmt)
        ws1.set_row(0, 20)

        ws1.write(2, 0, 'Nombre', hdr_fmt)
        for i, (mes_label, num_q, _, _) in enumerate(quincenas):
            ws1.write(2, i + 1, f'{num_q} {mes_label}', hdr_fmt)
        ws1.write(2, len(quincenas) + 1, 'Acumulado', hdr_fmt)
        ws1.set_column(0, 0, 30)
        ws1.set_column(1, len(quincenas), 11)
        ws1.set_column(len(quincenas) + 1, len(quincenas) + 1, 14)
        ws1.set_row(2, 28)
        ws1.freeze_panes(3, 1)

        row = 3
        for emp in empleados:
            ws1.write(row, 0, emp.name, lbl_fmt)
            acumulado = 0.0
            for i, (mes_label, num_q, _, _) in enumerate(quincenas):
                d = data[emp.id][(mes_label, num_q)]
                if d['tiene_boleta']:
                    ws1.write(row, i + 1, d['valor_quincena'], num_fmt)
                    acumulado += d['valor_quincena']
                elif d.get('cubierto_inicial'):
                    ws1.write(row, i + 1, 'Acum.Inicial', cubierto_fmt)
                else:
                    ws1.write(row, i + 1, 'S/D', no_boleta_fmt)
            ws1.write(row, len(quincenas) + 1, round(acumulado, 2), acum_fmt)
            row += 1

        # ---- HOJA 2: Detalle de componentes, una fila por quincena -----
        ws2 = wb.add_worksheet('Detalle Componentes')
        headers2 = [
            'Empleado', 'Mes', 'Quincena', 'Boleta', 'Estado',
            'Sub Total Quincenal\n(gross_salary)',
            'Costo Patrono\nDias 1-3 (Art.79)',
            'Incapacidad\nCCSS', 'Incapacidad\nINS',
            'Permiso\nSin Goce',
            'Devengado\n(antes de /12)',
            'Valor Quincena\n(/12)',
            'Acumulado\nCorrido',
        ]
        for ci, h in enumerate(headers2):
            ws2.write(0, ci, h, hdr_fmt)
        anchos2 = [30, 10, 10, 22, 12, 18, 16, 14, 14, 14, 16, 14, 16]
        for ci, w in enumerate(anchos2):
            ws2.set_column(ci, ci, w)
        ws2.set_row(0, 32)
        ws2.freeze_panes(1, 1)

        row2 = 1
        for emp in empleados:
            acumulado = 0.0
            for mes_label, num_q, _, _ in quincenas:
                d = data[emp.id][(mes_label, num_q)]
                acumulado += d['valor_quincena']
                ws2.write(row2, 0, emp.name, lbl_fmt)
                ws2.write(row2, 1, mes_label, lbl_fmt)
                ws2.write(row2, 2, num_q, lbl_fmt)
                if d['tiene_boleta']:
                    ws2.write(row2, 3, d['slip_name'], lbl_fmt)
                    ws2.write(row2, 4, dict(
                        self.env['planilla.payslip.cr']._fields['state'].selection
                    ).get(d['estado'], d['estado']), lbl_fmt)
                    ws2.write(row2, 5, d['sub_total'], num_fmt)
                    ws2.write(row2, 6, d['costo_patrono'], num_fmt)
                    ws2.write(row2, 7, d['incap_ccss'], num_fmt)
                    ws2.write(row2, 8, d['incap_ins'], num_fmt)
                    ws2.write(row2, 9, d['psgs'], num_fmt)
                    ws2.write(row2, 10, d['devengado'], num_fmt)
                    ws2.write(row2, 11, d['valor_quincena'], num_bold_fmt)
                elif d.get('cubierto_inicial'):
                    ws2.write(row2, 3, '', cubierto_fmt)
                    ws2.write(row2, 4, 'Acumulado Inicial (Art.228 CT)', cubierto_fmt)
                    for c in range(5, 12):
                        ws2.write(row2, c, '', cubierto_fmt)
                else:
                    ws2.write(row2, 3, '', no_boleta_fmt)
                    ws2.write(row2, 4, 'Sin boleta', no_boleta_fmt)
                    for c in range(5, 12):
                        ws2.write(row2, c, '', no_boleta_fmt)
                ws2.write(row2, 12, round(acumulado, 2), acum_fmt)
                row2 += 1

        note_fmt = wb.add_format({'italic': True, 'font_color': '#666666', 'font_size': 9})
        ws2.merge_range(
            row2 + 1, 0, row2 + 1, 12,
            'Formula por quincena: Devengado = Sub Total Quincenal (gross_salary) '
            '+ Costo Patrono Dias 1-3 (Art.79 CT) - Incapacidad CCSS - Incapacidad INS '
            '- Permiso Sin Goce. Valor Quincena = Devengado / 12. El Acumulado Corrido '
            'es la suma progresiva de "Valor Quincena" -- compare esta columna contra '
            'el Acumulado del Excel oficial, quincena por quincena, para ubicar '
            'exactamente en que mes (si en alguno) aparece una diferencia. '
            '"S/D" / "Sin boleta" = no hay boleta en estado '
            + ('Pagada o Confirmada' if self.incluir_confirmadas else 'Pagada')
            + ' para esa quincena en el sistema. '
            '"Acum.Inicial" / "Acumulado Inicial (Art.228 CT)" = esta quincena ya '
            'esta cubierta por el Aguinaldo Acumulado Inicial (pre-implementacion) '
            'del empleado, segun su Fecha de Corte -- NO se cuenta como devengado '
            'nuevo aqui para evitar duplicarla (aunque exista boleta cargada en el '
            'sistema para ese periodo, ya esta incluida en el monto del Acumulado '
            'Inicial de su ficha).',
            note_fmt)

        wb.close()
        xlsx_data = base64.b64encode(output.getvalue()).decode()
        filename = f'Aguinaldo_{self.year}_Detalle_Mensual.xlsx'

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
