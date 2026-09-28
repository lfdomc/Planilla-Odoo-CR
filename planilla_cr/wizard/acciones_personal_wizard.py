"""
Acciones de Personal Consolidadas -- Auditoria General.

Exporta a Excel TODAS las novedades (acciones de personal) que afectan
las boletas de un empleado en un rango de fechas: incapacidades (CCSS,
INS, maternidad), permisos/licencias, bonos, vacaciones pagadas,
pensiones alimentarias y embargos -- en una sola hoja ordenada
cronologicamente por empleado, para poder ver de un vistazo TODO lo
que le paso a un empleado en el periodo sin tener que abrir cada
submenu por separado.

Caso de uso real que origino este reporte: distinguir si una
diferencia de aguinaldo contra el Excel de RRHH se debe a una
incapacidad real (dato correcto en ambos lados, solo criterio legal
distinto) o a una novedad faltante (bono, permiso) que nunca se
registro en el sistema.
"""
import io
import base64
import logging
from datetime import date

from odoo import models, fields
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AccionesPersonalWizard(models.TransientModel):
    _name = 'planilla.acciones.personal.wizard'
    _description = 'Acciones de Personal Consolidadas (Auditoria)'

    company_id = fields.Many2one(
        'res.company', required=True,
        default=lambda self: self.env.company,
    )
    employee_id = fields.Many2one(
        'hr.employee', string='Empleado (opcional)',
        help='Deje vacio para incluir a todos los empleados de la compania.',
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

    def action_generate(self):
        self.ensure_one()
        try:
            import xlsxwriter
        except ImportError:
            raise UserError('xlsxwriter no esta instalado.')

        if self.date_from > self.date_to:
            raise UserError('La fecha "Desde" no puede ser posterior a "Hasta".')

        emp_domain = [('company_id', '=', self.company_id.id)]
        if self.employee_id:
            emp_domain.append(('id', '=', self.employee_id.id))
        if self.branch_id:
            emp_domain.append(('branch_id', '=', self.branch_id.id))
        empleados = self.env['hr.employee'].search(emp_domain)
        emp_ids = empleados.ids
        if not emp_ids:
            raise UserError('No se encontraron empleados con los filtros indicados.')

        rango_domain_base = [
            ('employee_id', 'in', emp_ids),
            ('date_start', '<=', self.date_to),
            ('date_end', '>=', self.date_from),
        ]

        eventos = []  # (empleado, fecha_desde, fecha_hasta, tipo, detalle, monto, estado)

        # -- Incapacidades ---------------------------------------------
        disability_type_labels = dict(
            self.env['planilla.disability']._fields['disability_type'].selection)
        for d in self.env['planilla.disability'].search(rango_domain_base):
            eventos.append((
                d.employee_id.name, d.date_start, d.date_end,
                'Incapacidad', disability_type_labels.get(d.disability_type, d.disability_type),
                d.employer_cost or 0.0,
                dict(d._fields['state'].selection).get(d.state, d.state),
                d.diagnosis or d.certificate_number or '',
            ))

        # -- Permisos / Licencias ----------------------------------------
        leave_type_labels = dict(
            self.env['planilla.leave.cr']._fields['leave_type'].selection)
        for l in self.env['planilla.leave.cr'].search(rango_domain_base):
            eventos.append((
                l.employee_id.name, l.date_start, l.date_end,
                'Permiso/Licencia', leave_type_labels.get(l.leave_type, l.leave_type),
                l.leave_amount or 0.0,
                dict(l._fields['state'].selection).get(l.state, l.state),
                f'{l.days} dias ({l.working_days} laborables)',
            ))

        # -- Bonos (vigencia dentro del rango) ----------------------------
        bono_domain = [
            ('employee_id', 'in', emp_ids),
            ('date_start', '<=', self.date_to),
            '|',
            ('date_end', '=', False),
            ('date_end', '>=', self.date_from),
        ]
        for b in self.env['planilla.bono'].search(bono_domain):
            eventos.append((
                b.employee_id.name, b.date_start, b.date_end or self.date_to,
                'Bono', b.name or b.code or '',
                b.amount or 0.0,
                dict(b._fields['state'].selection).get(b.state, b.state),
                'Recurrente' if b.is_recurring else 'Puntual',
            ))

        # -- Vacaciones pagadas -------------------------------------------
        for v in self.env['planilla.vacation.payment'].search(rango_domain_base):
            eventos.append((
                v.employee_id.name, v.date_start, v.date_end,
                'Vacaciones', f'{v.days} dias',
                v.total_amount or 0.0,
                dict(v._fields['state'].selection).get(v.state, v.state),
                '',
            ))

        # -- Pensiones alimentarias (vigencia dentro del rango) -----------
        pension_domain = [
            ('employee_id', 'in', emp_ids),
            ('date_start', '<=', self.date_to),
            '|',
            ('date_end', '=', False),
            ('date_end', '>=', self.date_from),
        ]
        for p in self.env['planilla.pension.alimentaria'].search(pension_domain):
            eventos.append((
                p.employee_id.name, p.date_start, p.date_end or self.date_to,
                'Pension Alimentaria', '',
                p.fixed_amount or 0.0,
                dict(p._fields['state'].selection).get(p.state, p.state),
                '',
            ))

        # -- Embargos (vigencia dentro del rango) -------------------------
        embargo_domain = [
            ('employee_id', 'in', emp_ids),
            ('date_start', '<=', self.date_to),
            '|',
            ('date_end', '=', False),
            ('date_end', '>=', self.date_from),
        ]
        for e in self.env['planilla.embargo'].search(embargo_domain):
            eventos.append((
                e.employee_id.name, e.date_start, e.date_end or self.date_to,
                'Embargo', '',
                e.fixed_amount or 0.0,
                dict(e._fields['state'].selection).get(e.state, e.state),
                '',
            ))

        if not eventos:
            raise UserError(
                'No se encontraron acciones de personal (incapacidades, permisos, '
                'bonos, vacaciones, pensiones, embargos) con los filtros indicados.')

        eventos.sort(key=lambda ev: (ev[0], ev[1] or date.min))

        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True})
        ws = wb.add_worksheet('Acciones de Personal')

        title_fmt = wb.add_format({'bold': True, 'font_size': 13,
                                    'bg_color': '#1F4E79', 'font_color': 'white'})
        hdr_fmt = wb.add_format({'bold': True, 'bg_color': '#2E4057',
                                  'font_color': 'white', 'border': 1,
                                  'text_wrap': True, 'align': 'center'})
        normal_fmt = wb.add_format({'border': 1})
        money_fmt = wb.add_format({'num_format': '#,##0.00', 'border': 1})
        date_fmt = wb.add_format({'num_format': 'dd/mm/yyyy', 'border': 1})

        tipo_colors = {
            'Incapacidad': '#FCE4D6',
            'Permiso/Licencia': '#FFF2CC',
            'Bono': '#E2EFDA',
            'Vacaciones': '#DDEBF7',
            'Pension Alimentaria': '#EDEDED',
            'Embargo': '#F2DCDB',
        }
        tipo_fmts = {
            tipo: wb.add_format({'border': 1, 'bg_color': color, 'bold': True})
            for tipo, color in tipo_colors.items()
        }

        headers = ['Empleado', 'Tipo', 'Detalle / Subtipo', 'Desde', 'Hasta',
                    'Monto (CRC)', 'Estado', 'Nota Adicional']
        ws.merge_range(0, 0, 0, len(headers) - 1,
                        f'Acciones de Personal Consolidadas -- {self.date_from} a '
                        f'{self.date_to} -- Generado {fields.Date.context_today(self)}',
                        title_fmt)
        for i, h in enumerate(headers):
            ws.write(1, i, h, hdr_fmt)
        ws.set_row(1, 22)
        ws.freeze_panes(2, 1)
        ws.set_column(0, 0, 30)
        ws.set_column(1, 1, 16)
        ws.set_column(2, 2, 22)
        ws.set_column(3, 4, 12)
        ws.set_column(5, 5, 14)
        ws.set_column(6, 6, 12)
        ws.set_column(7, 7, 28)

        row = 2
        for (emp, f_desde, f_hasta, tipo, detalle, monto, estado, nota) in eventos:
            ws.write(row, 0, emp, normal_fmt)
            ws.write(row, 1, tipo, tipo_fmts.get(tipo, normal_fmt))
            ws.write(row, 2, detalle, normal_fmt)
            ws.write(row, 3, f_desde, date_fmt)
            ws.write(row, 4, f_hasta, date_fmt)
            ws.write(row, 5, monto, money_fmt)
            ws.write(row, 6, estado, normal_fmt)
            ws.write(row, 7, nota, normal_fmt)
            row += 1

        note_fmt = wb.add_format({'italic': True, 'font_color': '#666666', 'font_size': 9})
        ws.merge_range(
            row + 1, 0, row + 1, len(headers) - 1,
            'Incluye: Incapacidades (CCSS/INS/Maternidad), Permisos/Licencias, '
            'Bonos (recurrentes y puntuales), Vacaciones pagadas, Pensiones '
            'Alimentarias y Embargos, cuya vigencia se solapa con el rango '
            'consultado. Use junto con "Detalle Completo de Boletas por '
            'Empleado" para confirmar que cada novedad se reflejo '
            'correctamente en la boleta correspondiente.',
            note_fmt)

        wb.close()
        xlsx_data = base64.b64encode(output.getvalue()).decode()
        filename = f'Acciones_Personal_{self.date_from}_{self.date_to}.xlsx'

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
