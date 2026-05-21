"""
Reporte de Decimo Mes (Aguinaldo) -- Art. 228 Codigo de Trabajo CR
Calcula el aguinaldo real que le corresponde a cada empleado
basado en los salarios ordinarios del periodo junio-noviembre del ano en curso.
"""
from odoo import models, fields, api
from odoo.exceptions import UserError
import io
import base64


class AguinaldoWizard(models.TransientModel):
    _name = 'planilla.aguinaldo.wizard'
    _description = 'Reporte Decimo Mes (Aguinaldo)'

    year = fields.Integer(
        string='Ano', required=True,
        default=lambda self: fields.Date.context_today(self).year
    )
    branch_id = fields.Many2one('planilla.branch', string='Sucursal (opcional)')
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company
    )

    # Resultado
    salary_basis = fields.Selection([
        ('gross', 'Salario Bruto (base_salary + horas extras + vacaciones)'),
        ('base',  'Salario Base unicamente'),
    ], string='Base de Calculo', required=True, default='gross',
       help='Art. 228 CT: el aguinaldo se calcula sobre salarios ordinarios devengados. '
            'Se recomienda usar Salario Bruto para incluir todos los componentes ordinarios.')

    result_ids = fields.One2many(
        'planilla.aguinaldo.line', 'wizard_id', string='Detalle por Empleado'
    )
    total_aguinaldo = fields.Monetary(
        string='Total Aguinaldo', currency_field='currency_id',
        compute='_compute_total'
    )
    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.ref('base.CRC')
    )
    provision_acumulada = fields.Monetary(
        string='Provisión Acum. (dic→hoy)', currency_field='currency_id',
    )
    initial_amount = fields.Monetary(
        string='Acumulado Inicial', currency_field='currency_id',
    )
    total_acumulado = fields.Monetary(
        string='Total Acumulado', currency_field='currency_id',
    )
    computed = fields.Boolean(default=False)

    @api.depends('result_ids.aguinaldo_amount')
    def _compute_total(self):
        for rec in self:
            rec.total_aguinaldo = sum(rec.result_ids.mapped('aguinaldo_amount'))

    def action_compute(self):
        """
        Art. 228 CT: Aguinaldo = suma de salarios ordinarios recibidos
        en el periodo junio 1 - noviembre 30 del ano, dividido entre 12.
        Para empleados con menos de un ano, proporcional al tiempo trabajado.
        FIX M-03 v59: Limpiar result_ids previos para evitar duplicados
        si el usuario ejecuta el boton mas de una vez.
        """
        self.ensure_one()
        # FIX M-03 / FIX-B1: Eliminar calculos anteriores UNA sola vez al inicio.
        # La version anterior llamaba unlink() dos veces (lineas 61 y 108),
        # la segunda podia fallar silenciosamente o borrar lineas recien creadas
        # si el loop tardaba y el usuario recargaba.
        self.result_ids.unlink()
        from datetime import date

        period_start = date(self.year, 6, 1)
        period_end   = date(self.year, 11, 30)

        # Buscar boletas pagadas en el periodo jun-nov
        domain = [
            ('state', '=', 'done'),
            ('date_from', '>=', period_start),
            ('date_to', '<=', period_end),
            ('company_id', '=', self.company_id.id),
        ]
        if self.branch_id:
            domain.append(('branch_id', '=', self.branch_id.id))

        slips = self.env['planilla.payslip.cr'].search(domain)

        if not slips:
            emp_check = self.env['hr.employee'].search([
                ('company_id', '=', self.company_id.id),
                ('aguinaldo_initial_amount', '>', 0),
            ], limit=1)
            if not emp_check:
                raise UserError(
                    'No se encontraron boletas pagadas en el periodo '
                    'junio-noviembre %s ni empleados con acumulado inicial.' % self.year
                )

        # Agrupar por empleado
        employee_data = {}
        for slip in slips:
            eid = slip.employee_id.id
            if eid not in employee_data:
                employee_data[eid] = {
                    'employee_id': eid,
                    'employee_name': slip.employee_id.name,
                    'branch': slip.employee_id.branch_id.name or '',
                    'entry_date': slip.employee_id.entry_date,
                    'total_ordinary': 0.0,
                    'months_count': 0,
                    'slip_count': 0,
                    'aguinaldo_initial': slip.employee_id.aguinaldo_initial_amount or 0.0,
                    'aguinaldo_initial_date': slip.employee_id.aguinaldo_initial_date,
                }
            # Art. 228 CT: usar salario bruto (incluye horas extras, vacaciones)
            # o solo base segun la seleccion del usuario
            if self.salary_basis == 'gross':
                employee_data[eid]['total_ordinary'] += slip.gross_salary or 0.0
            else:
                employee_data[eid]['total_ordinary'] += slip.base_salary or 0.0
            employee_data[eid]['slip_count'] += 1

        # Agregar empleados con acumulado inicial aunque no tengan boletas en el sistema todavia
        # (empleados que solo tienen datos pre-implementacion)
        period_start = date(self.year, 6, 1)  # ya definido arriba pero repetimos para el nuevo bloque
        emp_domain = [
            ('company_id', '=', self.company_id.id),
            ('aguinaldo_initial_amount', '>', 0),
            ('aguinaldo_initial_date', '!=', False),
        ]
        if self.branch_id:
            emp_domain.append(('branch_id', '=', self.branch_id.id))
        emps_with_initial = self.env['hr.employee'].search(emp_domain)
        for emp in emps_with_initial:
            if emp.id not in employee_data:
                employee_data[emp.id] = {
                    'employee_id': emp.id,
                    'employee_name': emp.name,
                    'branch': emp.branch_id.name or '',
                    'entry_date': emp.entry_date,
                    'total_ordinary': 0.0,
                    'months_count': 0,
                    'slip_count': 0,
                    'aguinaldo_initial': emp.aguinaldo_initial_amount or 0.0,
                    'aguinaldo_initial_date': emp.aguinaldo_initial_date,
                }

        # Calcular aguinaldo por empleado (result_ids ya se limpio al inicio del metodo)
        lines = []
        for eid, data in employee_data.items():
            # Meses trabajados en el periodo (max 6)
            entry = data['entry_date']
            if entry and entry > period_start:
                from dateutil.relativedelta import relativedelta
                months_worked = (
                    (min(period_end, date.today()) - entry).days / 30.0
                )
                months_worked = min(months_worked, 6.0)
            else:
                months_worked = 6.0

            # Aguinaldo = total_ordinario / 12
            aguinaldo = round(data['total_ordinary'] / 12.0, 2)

            # Sumar acumulado pre-implementacion si existe y corresponde al mismo ano
            initial = data.get('aguinaldo_initial', 0.0)
            initial_date = data.get('aguinaldo_initial_date')
            if initial and initial_date:
                # Verificar que el corte sea del mismo ano de aguinaldo
                # (aguinaldo de diciembre YEAR usa periodo dic(YEAR-1) - nov(YEAR))
                aguinaldo_year_start = date(self.year - 1, 12, 1)
                aguinaldo_year_end   = date(self.year, 11, 30)
                if aguinaldo_year_start <= initial_date <= aguinaldo_year_end:
                    aguinaldo = round(aguinaldo + initial, 2)

            # Acumulado provisión dic anterior → hoy
            today = date.today()
            dic_start = date(today.year - 1, 12, 1)
            prov_slips = self.env['planilla.payslip.cr'].search([
                ('employee_id', '=', eid),
                ('state', 'in', ('done', 'confirmed')),
                ('date_from', '>=', dic_start),
                ('date_to', '<=', today),
                ('company_id', '=', self.company_id.id),
            ])
            provision_sum = round(
                sum(s.aguinaldo_provision for s in prov_slips), 2)
            emp_obj = self.env['hr.employee'].browse(eid)
            emp_initial = 0.0
            if (emp_obj.aguinaldo_initial_amount
                    and emp_obj.aguinaldo_initial_date
                    and emp_obj.aguinaldo_initial_date >= dic_start):
                emp_initial = emp_obj.aguinaldo_initial_amount

            lines.append({
                'wizard_id':           self.id,
                'employee_id':         eid,
                'total_ordinary':      data['total_ordinary'],
                'months_worked':       round(months_worked, 1),
                'slip_count':          data['slip_count'],
                'aguinaldo_amount':    aguinaldo,
                'branch':              data['branch'],
                'provision_acumulada': provision_sum,
                'initial_amount':      emp_initial,
                'total_acumulado':     round(provision_sum + emp_initial, 2),
            })

        # Ordenar por nombre
        lines.sort(key=lambda l: l['employee_id'])
        for line in lines:
            self.env['planilla.aguinaldo.line'].create(line)

        self.computed = True
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'planilla.aguinaldo.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_export_excel(self):
        """Exporta el resultado a Excel."""
        self.ensure_one()
        if not self.computed:
            raise UserError('Primero calcule el aguinaldo.')
        try:
            import xlsxwriter
        except ImportError:
            raise UserError('xlsxwriter no esta instalado.')

        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True})
        ws = wb.add_worksheet('Aguinaldo')

        # Formatos
        bold = wb.add_format({'bold': True, 'bg_color': '#1F4E79', 'font_color': 'white', 'border': 1})
        money = wb.add_format({'num_format': '#,##0.00', 'border': 1})
        normal = wb.add_format({'border': 1})
        total_fmt = wb.add_format({'bold': True, 'num_format': '#,##0.00', 'bg_color': '#D9E1F2', 'border': 1})

        ws.set_column('A:A', 30)
        ws.set_column('B:B', 18)
        ws.set_column('C:F', 16)

        headers = ['Empleado', 'Sucursal', 'Boletas Jun-Nov', 'Meses Trabajados',
                   'Total Salarios Ordinarios (CRC)', 'Aguinaldo a Pagar (CRC)']
        for col, h in enumerate(headers):
            ws.write(0, col, h, bold)

        row = 1
        for line in self.result_ids:
            ws.write(row, 0, line.employee_id.name, normal)
            ws.write(row, 1, line.branch or '', normal)
            ws.write(row, 2, line.slip_count, normal)
            ws.write(row, 3, line.months_worked, normal)
            ws.write(row, 4, line.total_ordinary, money)
            ws.write(row, 5, line.aguinaldo_amount, money)
            row += 1

        # Total
        ws.write(row, 4, 'TOTAL', total_fmt)
        ws.write(row, 5, self.total_aguinaldo, total_fmt)

        wb.close()
        xlsx_data = base64.b64encode(output.getvalue()).decode()
        filename = f'Aguinaldo_{self.year}.xlsx'

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


class AguinaldoLine(models.TransientModel):
    _name = 'planilla.aguinaldo.line'
    _description = 'Linea de Aguinaldo por Empleado'
    _order = 'employee_id asc'

    wizard_id   = fields.Many2one('planilla.aguinaldo.wizard', ondelete='cascade')
    employee_id = fields.Many2one('hr.employee', string='Empleado')
    branch      = fields.Char(string='Sucursal')
    slip_count  = fields.Integer(string='Boletas')
    months_worked = fields.Float(string='Meses Jun-Nov', digits=(4, 1))
    total_ordinary = fields.Monetary(
        string='Salarios Ordinarios (CRC)', currency_field='currency_id'
    )
    aguinaldo_amount = fields.Monetary(
        string='Aguinaldo (CRC)', currency_field='currency_id'
    )
    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.ref('base.CRC')
    )
    provision_acumulada = fields.Monetary(
        string='Provisión Acum. (dic→hoy)', currency_field='currency_id',
    )
    initial_amount = fields.Monetary(
        string='Acumulado Inicial', currency_field='currency_id',
    )
    total_acumulado = fields.Monetary(
        string='Total Acumulado', currency_field='currency_id',
    )
