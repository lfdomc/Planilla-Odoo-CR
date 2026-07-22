import io, base64, datetime, calendar as _cal
import xlsxwriter
from odoo import models, fields, api
from odoo.exceptions import UserError

class Reporte208Wizard(models.TransientModel):
    _name = 'planilla.reporte.208.wizard'
    _description = 'Reporte Retenciones Renta 208/138 TRIBU-CR'

    company_id  = fields.Many2one('res.company', required=True, default=lambda s: s.env.company)
    filter_mode = fields.Selection([('month','Por Mes'),('range','Rango de Fechas')], default='month', required=True)
    month       = fields.Selection([(str(i), datetime.date(2000,i,1).strftime('%B').capitalize()) for i in range(1,13)],
                                   default=lambda s: str(datetime.date.today().month))
    year        = fields.Integer(default=lambda s: datetime.date.today().year)
    date_from   = fields.Date()
    date_to     = fields.Date()

    def _get_range(self):
        if self.filter_mode == 'month':
            m, y = int(self.month), self.year
            return datetime.date(y,m,1), datetime.date(y,m,_cal.monthrange(y,m)[1])
        if not self.date_from or not self.date_to:
            raise UserError('Seleccione el rango de fechas.')
        return self.date_from, self.date_to

    def _build_data(self):
        df, dt = self._get_range()
        # SEC-2 fix: sin sudo() -- el ir.rule de aislamiento por compañía
        # (security/record_rules.xml) ya filtra correctamente. Antes esto
        # traia boletas de TODAS las compañías a memoria y filtraba despues
        # en Python -- ademas de ser un leak de datos fiscales, cargaba
        # registros de compañías ajenas innecesariamente.
        slips = self.env['planilla.payslip.cr'].search([
            ('state', '!=', 'cancelled'),
            ('date_from', '<=', dt),
            ('date_to', '>=', df),
            '|',
            ('company_id', '=', self.company_id.id),
            ('employee_id.company_id', '=', self.company_id.id),
        ])
        by_emp = {}
        for s in slips:
            eid = s.employee_id.id
            if eid not in by_emp:
                emp = s.employee_id
                by_emp[eid] = dict(cedula=emp.identification_id or '',
                    nombre=emp.name or '', multi=emp.es_multiempleado,
                    bruto=0.0, creditos=0.0, renta=0.0, rebajo=0.0)
            r = by_emp[eid]
            r['bruto']   += s.gross_salary or 0
            r['creditos'] += getattr(s, "income_tax_credits", 0) or 0
            r['renta']   += s.income_tax or 0
            r['rebajo']  += s.rebajo_renta_amount or 0
        rows = []
        for r in by_emp.values():
            r['base']  = max(r['bruto'] - r['creditos'], 0)
            r['total'] = r['renta'] + r['rebajo']
            r['estado'] = ('con_rebajo' if r['multi'] and r['rebajo'] > 0
                           else 'sin_rebajo' if r['multi'] else 'normal')
            rows.append(r)
        return sorted(rows, key=lambda x: x['nombre']), df, dt

    def action_show_report(self):
        rows, df, dt = self._build_data()
        # Crear registros transitorios y guardar IDs para que no se pierdan
        self.env['planilla.reporte.208.result'].search([('wizard_id','=',self.id)]).unlink()
        ids = []
        for r in rows:
            rec = self.env['planilla.reporte.208.result'].create({
                'wizard_id': self.id, 'cedula': r['cedula'], 'nombre': r['nombre'],
                'es_multiempleado': r['multi'], 'estado_multi': r['estado'],
                'bruto': r['bruto'], 'creditos': r['creditos'], 'base_imp': r['base'],
                'renta_normal': r['renta'], 'rebajo_renta': r['rebajo'], 'total_renta': r['total'],
            })
            ids.append(rec.id)
        titulo = f'Reporte 208/138 — {self.company_id.name} ({df.strftime("%d/%m/%Y")} al {dt.strftime("%d/%m/%Y")})'
        return {'type':'ir.actions.act_window','name': titulo,
                'res_model':'planilla.reporte.208.result','view_mode':'list',
                'domain':[('id','in',ids)],'target':'main',
                'context':{'no_create':True,'no_delete':True}}

    def action_export_excel(self):
        rows, df, dt = self._build_data()
        out = io.BytesIO()
        wb = xlsxwriter.Workbook(out, {'in_memory': True})
        ws = wb.add_worksheet('208-138')
        H = wb.add_format({'bold':True,'bg_color':'#1F4E79','font_color':'#FFFFFF','border':1,'font_size':10})
        N = wb.add_format({'num_format':'#,##0.00','border':1,'font_size':10})
        T = wb.add_format({'border':1,'font_size':10})
        OK= wb.add_format({'border':1,'font_size':10,'font_color':'#1A7A3D','bold':True})
        WA= wb.add_format({'border':1,'font_size':10,'font_color':'#C0392B','bold':True})
        TL= wb.add_format({'bold':True,'font_size':13})
        ws.merge_range('A1:J1',f'RETENCIONES RENTA 208/138 TRIBU-CR — {self.company_id.name}',TL)
        ws.write('A2',f'Período: {df.strftime("%d/%m/%Y")} al {dt.strftime("%d/%m/%Y")}')
        hdrs  = ['Cédula','Nombre','Multiempleo','Estado','Bruto','Créditos','Base Imp.','Renta Normal','Rebajo Renta','Total Retenido']
        widths= [15,35,12,28,18,16,16,16,18,18]
        for c,(h,w) in enumerate(zip(hdrs,widths)):
            ws.write(3,c,h,H); ws.set_column(c,c,w)
        elabels = {'con_rebajo':'✓ Multiempleo — con rebajo','sin_rebajo':'⚠ Multiempleo — SIN rebajo','normal':'— Normal'}
        for i,r in enumerate(rows,4):
            ef = OK if r['estado']=='con_rebajo' else WA if r['estado']=='sin_rebajo' else T
            ws.write(i,0,r['cedula'],T); ws.write(i,1,r['nombre'],T)
            ws.write(i,2,'Sí' if r['multi'] else 'No',T)
            ws.write(i,3,elabels[r['estado']],ef)
            for c,k in enumerate(['bruto','creditos','base','renta','rebajo','total'],4):
                ws.write(i,c,r[k],N)
        last=len(rows)+4
        TF=wb.add_format({'bold':True,'num_format':'#,##0.00','border':1,'bg_color':'#D9E1F2'})
        ws.write(last,1,'TOTAL',wb.add_format({'bold':True,'border':1}))
        for c,k in enumerate(['bruto','creditos','base','renta','rebajo','total'],4):
            ws.write(last,c,sum(r[k] for r in rows),TF)
        wb.close(); out.seek(0)
        fname=f'Reporte_208_{df.strftime("%Y%m")}.xlsx'
        att=self.env['ir.attachment'].create({'name':fname,'type':'binary',
            'datas':base64.b64encode(out.read()).decode(),
            'mimetype':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'})
        return {'type':'ir.actions.act_url','url':f'/web/content/{att.id}?download=true','target':'new'}

class Reporte208Result(models.TransientModel):
    _name = 'planilla.reporte.208.result'
    _description = 'Resultado Reporte 208/138'
    wizard_id      = fields.Many2one('planilla.reporte.208.wizard', ondelete='cascade')
    cedula         = fields.Char('Cédula')
    nombre         = fields.Char('Empleado')
    es_multiempleado = fields.Boolean('Multiempleo')
    estado_multi   = fields.Selection([('con_rebajo','Con rebajo'),('sin_rebajo','Sin rebajo'),('normal','Normal')])
    bruto          = fields.Float('Bruto Mes')
    creditos       = fields.Float('Créditos')
    base_imp       = fields.Float('Base Imponible')
    renta_normal   = fields.Float('Renta Normal')
    rebajo_renta   = fields.Float('Rebajo Consolidado')
    total_renta    = fields.Float('Total Retenido')
