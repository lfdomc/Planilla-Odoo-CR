"""
Detalle Completo de Boletas por Empleado -- Auditoria General.

Exporta a Excel TODA la informacion de las boletas de un empleado (o de
todos, con filtros) en un rango de fechas: ingresos, deducciones,
cargas patronales, horas extra, incapacidades, provisiones y estado --
los mismos datos que se ven en las pestanas "Resumen Completo" de la
boleta en pantalla, pero en una sola hoja para comparar boleta contra
boleta, mes contra mes, o contra un Excel externo (RRHH, auditoria).

Caso de uso real que origino este reporte: detectar que una boleta
puntual no tenia un Bono Salarial recurrente cargado (novedad faltante
en Novedades -> Bonos) comparando "Bonos Salariales (afecto CCSS)"
boleta por boleta sin tener que abrir cada una en la UI.
"""
import io
import base64
import logging
from datetime import date

from odoo import models, fields
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class EmployeeBoletaDetalleWizard(models.TransientModel):
    _name = 'planilla.employee.boleta.detalle.wizard'
    _description = 'Detalle Completo de Boletas por Empleado (Auditoria)'

    company_id = fields.Many2one(
        'res.company', required=True,
        default=lambda self: self.env.company,
    )
    employee_id = fields.Many2one(
        'hr.employee', string='Empleado (opcional)',
        help='Deje vacio para incluir a todos los empleados de la '
             'compania (y sucursal, si se indica).',
    )
    branch_id = fields.Many2one('planilla.branch', string='Sucursal (opcional)')
    date_from = fields.Date(
        string='Desde', required=True,
        default=lambda self: date(fields.Date.context_today(self).year, 1, 1),
    )
    date_to = fields.Date(
        string='Hasta', required=True,
        default=lambda self: fields.Date.context_today(self),
    )
    incluir_borradores = fields.Boolean(
        string='Incluir Borradores y Confirmadas (no solo Pagadas)',
        default=True,
        help='Si esta marcado, incluye boletas en cualquier estado '
             '(draft, confirmed, done) -- util para auditar boletas '
             'que aun no se han pagado. Las Canceladas nunca se incluyen.',
    )

    def action_generate(self):
        self.ensure_one()
        try:
            import xlsxwriter
        except ImportError:
            raise UserError('xlsxwriter no esta instalado.')

        if self.date_from > self.date_to:
            raise UserError('La fecha "Desde" no puede ser posterior a "Hasta".')

        estados = (['draft', 'confirmed', 'done'] if self.incluir_borradores
                   else ['done'])
        domain = [
            ('company_id', '=', self.company_id.id),
            ('state', 'in', estados),
            ('date_from', '>=', self.date_from),
            ('date_to', '<=', self.date_to),
        ]
        if self.employee_id:
            domain.append(('employee_id', '=', self.employee_id.id))
        if self.branch_id:
            domain.append(('branch_id', '=', self.branch_id.id))

        slips = self.env['planilla.payslip.cr'].search(
            domain, order='employee_id, date_from')
        if not slips:
            raise UserError('No se encontraron boletas con los filtros indicados.')

        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True})
        ws = wb.add_worksheet('Detalle Boletas')

        title_fmt = wb.add_format({'bold': True, 'font_size': 13,
                                    'bg_color': '#1F4E79', 'font_color': 'white'})
        hdr_fmt = wb.add_format({'bold': True, 'bg_color': '#2E4057',
                                  'font_color': 'white', 'border': 1,
                                  'text_wrap': True, 'align': 'center',
                                  'valign': 'vcenter'})
        section_fmt = wb.add_format({'bold': True, 'bg_color': '#D9E1F2',
                                      'border': 1, 'align': 'center'})
        normal_fmt = wb.add_format({'border': 1})
        money_fmt = wb.add_format({'num_format': '#,##0.00', 'border': 1})
        date_fmt = wb.add_format({'num_format': 'dd/mm/yyyy', 'border': 1})
        alerta_fmt = wb.add_format({'border': 1, 'bg_color': '#FCE4D6',
                                     'font_color': '#9C4221', 'bold': True})

        # -- Definicion de columnas: (header, campo/lambda, formato, seccion) --
        cols = [
            ('Empleado', lambda s: s.employee_id.name, normal_fmt, 'Identificacion'),
            ('Cedula', lambda s: s.employee_id.identification_id or '', normal_fmt, 'Identificacion'),
            ('Sucursal', lambda s: s.branch_id.name or '', normal_fmt, 'Identificacion'),
            ('Boleta', lambda s: s.name or '', normal_fmt, 'Identificacion'),
            ('Desde', lambda s: s.date_from, date_fmt, 'Identificacion'),
            ('Hasta', lambda s: s.date_to, date_fmt, 'Identificacion'),
            ('Estado', lambda s: dict(s._fields['state'].selection).get(s.state, s.state), normal_fmt, 'Identificacion'),
            ('Dias Laborados', lambda s: s.days_worked or 0, normal_fmt, 'Identificacion'),

            ('Salario Base', lambda s: s.base_salary or 0.0, money_fmt, 'Ingresos'),
            ('Horas Extra ($)', lambda s: s.overtime_amount or 0.0, money_fmt, 'Ingresos'),
            ('Horas Extra (total)', lambda s: s.overtime_hours_total or 0.0, normal_fmt, 'Ingresos'),
            ('Vacaciones Pagadas', lambda s: s.vacation_amount or 0.0, money_fmt, 'Ingresos'),
            ('Otros Ingresos', lambda s: s.other_income or 0.0, money_fmt, 'Ingresos'),
            ('Bonos Salariales (afecto CCSS)', lambda s: s.bono_salarial_amount or 0.0, money_fmt, 'Ingresos'),
            ('Bonos que suman a Salario Base', lambda s: s.bono_base_salarial_amount or 0.0, money_fmt, 'Ingresos'),
            ('Salario Bruto (gross_salary)', lambda s: s.gross_salary or 0.0, money_fmt, 'Ingresos'),

            ('CCSS Obrero', lambda s: s.ccss_employee or 0.0, money_fmt, 'Deducciones'),
            ('Impuesto de Renta', lambda s: s.income_tax or 0.0, money_fmt, 'Deducciones'),
            ('Creditos Fiscales (Art.34)', lambda s: s.income_tax_credits or 0.0, money_fmt, 'Deducciones'),
            ('Otras Deducciones', lambda s: s.other_deductions or 0.0, money_fmt, 'Deducciones'),
            ('Pension Alimentaria', lambda s: s.amount_pension_alimentaria or 0.0, money_fmt, 'Deducciones'),
            ('Embargo', lambda s: s.amount_embargo or 0.0, money_fmt, 'Deducciones'),
            ('Total Deducciones Obrero', lambda s: s.total_employee_deductions or 0.0, money_fmt, 'Deducciones'),
            ('Salario Neto', lambda s: s.net_salary or 0.0, money_fmt, 'Deducciones'),

            ('CCSS Patronal', lambda s: s.ccss_employer or 0.0, money_fmt, 'Cargas Patronales'),
            ('INS Patronal', lambda s: s.ins_employer or 0.0, money_fmt, 'Cargas Patronales'),
            ('ROP Patronal', lambda s: s.rop_employer or 0.0, money_fmt, 'Cargas Patronales'),
            ('Provision Aguinaldo', lambda s: s.aguinaldo_provision or 0.0, money_fmt, 'Cargas Patronales'),
            ('Provision Cesantia', lambda s: s.cesantia_provision or 0.0, money_fmt, 'Cargas Patronales'),
            ('Provision Vacaciones', lambda s: s.vacation_provision or 0.0, money_fmt, 'Cargas Patronales'),
            ('Costo Total Patronal', lambda s: s.total_employer_cost or 0.0, money_fmt, 'Cargas Patronales'),

            ('Dias Incapacidad (periodo)', lambda s: s.disability_days_in_period or 0, normal_fmt, 'Incapacidades'),
            ('Costo Patrono Dias 1-3 (Art.79)', lambda s: s.costo_patrono_periodo or 0.0, money_fmt, 'Incapacidades'),
            ('Subsidio CCSS', lambda s: s.ccss_subsidy_total or 0.0, money_fmt, 'Incapacidades'),
            ('Subsidio INS', lambda s: s.ins_subsidy_total or 0.0, money_fmt, 'Incapacidades'),
            ('Salario Cotizable', lambda s: s.salario_cotizable or 0.0, money_fmt, 'Incapacidades'),

            ('Ultima Boleta del Mes', lambda s: 'Si' if s.is_last_payslip_of_month else 'No', normal_fmt, 'Otros'),
            ('Notas', lambda s: s.notes or '', normal_fmt, 'Otros'),
        ]

        # -- Fila de titulo y secciones -----------------------------------
        ws.merge_range(0, 0, 0, len(cols) - 1,
                        f'Detalle Completo de Boletas -- {self.date_from} a '
                        f'{self.date_to} -- Generado {fields.Date.context_today(self)}',
                        title_fmt)
        ws.set_row(0, 20)

        secciones = []
        last_sec = None
        start = 0
        for i, (_, _, _, sec) in enumerate(cols):
            if sec != last_sec:
                if last_sec is not None:
                    secciones.append((last_sec, start, i - 1))
                last_sec = sec
                start = i
        secciones.append((last_sec, start, len(cols) - 1))

        for sec, c0, c1 in secciones:
            if c0 == c1:
                ws.write(1, c0, sec, section_fmt)
            else:
                ws.merge_range(1, c0, 1, c1, sec, section_fmt)

        for i, (header, _, _, _) in enumerate(cols):
            ws.write(2, i, header, hdr_fmt)
        ws.set_row(2, 40)
        ws.freeze_panes(3, 4)
        ws.set_column(0, 0, 30)
        ws.set_column(1, len(cols) - 1, 16)

        # -- Filas de datos: alerta visual cuando Bono Salarial esta en 0 --
        # pero el empleado tiene bonos recurrentes configurados en otras
        # boletas del mismo rango -- ayuda a detectar novedades faltantes
        # como el caso que origino este reporte.
        emp_bono_promedio = {}
        for s in slips:
            if s.bono_salarial_amount:
                emp_bono_promedio.setdefault(s.employee_id.id, []).append(
                    s.bono_salarial_amount)

        row = 3
        for slip in slips:
            bonos_previos = emp_bono_promedio.get(slip.employee_id.id, [])
            sospechoso = bool(
                bonos_previos and not slip.bono_salarial_amount
                and slip.state != 'cancelled'
            )
            for col_idx, (_, getter, fmt, _) in enumerate(cols):
                value = getter(slip)
                use_fmt = alerta_fmt if (sospechoso and col_idx == 13) else fmt
                ws.write(row, col_idx, value, use_fmt)
            row += 1

        note_fmt = wb.add_format({'italic': True, 'font_color': '#666666', 'font_size': 9})
        ws.merge_range(
            row + 1, 0, row + 1, len(cols) - 1,
            'Celdas resaltadas en "Bonos Salariales (afecto CCSS)" = el empleado '
            'tiene bono recurrente en otras boletas del rango consultado pero '
            'esta boleta muestra 0 -- revisar si falta registrar la novedad '
            '(Novedades -> Bonos) para ese periodo.',
            note_fmt)

        wb.close()
        xlsx_data = base64.b64encode(output.getvalue()).decode()
        filename = f'Detalle_Boletas_{self.date_from}_{self.date_to}.xlsx'

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
