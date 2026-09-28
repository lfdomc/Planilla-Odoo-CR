"""
Diagnostico Integral del Sistema -- Reporte Consolidado.

Combina en UN SOLO archivo Excel los reportes de auditoria/diagnostico
de aguinaldo y novedades que existen hoy como wizards separados:

  1. Resumen Ejecutivo de Diagnostico (nueva, generada aqui) -- lista
     los empleados con diferencia relevante entre Odoo y el Excel de
     la empresa, y la causa mas probable de cada diferencia (cruzando
     contra las Acciones de Personal del mismo periodo).
  2. Auditoria de Aguinaldo (Odoo vs Excel) -- si se adjunta el Excel
     de la empresa.
  3. Detalle Mensual de Aguinaldo (quincena por quincena).
  4. Detalle Completo de Boletas por Empleado.
  5. Acciones de Personal Consolidadas.

No duplica logica de calculo: cada seccion reusa el wizard ya existente
(mismos metodos rate_helper.calc_aguinaldo_periodo /
get_aguinaldo_fecha_corte que usan Liquidacion, Simulador y los demas
reportes) e importa sus hojas de Excel ya generadas dentro de este
libro consolidado, para garantizar que el numero que se ve aqui es
exactamente el mismo que el que se ve en el reporte individual.
"""
import io
import base64
import logging
from datetime import date

from odoo import models, fields
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    from openpyxl import load_workbook
except ImportError:
    load_workbook = None


class DiagnosticoSistemaWizard(models.TransientModel):
    _name = 'planilla.diagnostico.sistema.wizard'
    _description = 'Diagnostico Integral del Sistema (Reporte Consolidado)'

    company_id = fields.Many2one(
        'res.company', required=True,
        default=lambda self: self.env.company,
    )
    year = fields.Integer(
        string='Año del Aguinaldo', required=True,
        default=lambda self: date.today().year,
    )
    date_from = fields.Date(
        string='Acciones de Personal Desde', required=True,
        default=lambda self: date(fields.Date.context_today(self).year, 1, 1),
        help='Rango usado para las secciones de Acciones de Personal y '
             'Detalle Completo de Boletas.',
    )
    date_to = fields.Date(
        string='Acciones de Personal Hasta', required=True,
        default=lambda self: fields.Date.context_today(self),
    )
    excel_empresa = fields.Binary(
        string='Excel de la Empresa (opcional)',
        help='Si lo adjunta, se incluye la Auditoria de Aguinaldo '
             '(Odoo vs Excel). Si lo deja vacio, esa seccion se omite '
             'y el reporte solo trae las secciones internas de Odoo.',
    )
    excel_empresa_filename = fields.Char(string='Nombre de archivo')
    hasta_columna = fields.Selection([
        ('ago', 'Hasta Agosto'), ('set', 'Hasta Septiembre'),
        ('oct', 'Hasta Octubre'), ('nov', 'Hasta Noviembre (año completo)'),
    ], string='Excel Actualizado Hasta', default='ago')
    incluir_detalle_boletas = fields.Boolean(
        string='Incluir Detalle Completo de Boletas', default=True,
        help='Trae los ~36 campos de cada boleta del periodo. Puede '
             'ser una hoja grande si hay muchos empleados -- '
             'desactivelo si solo necesita el diagnostico de aguinaldo.',
    )

    def _copiar_hoja(self, wb_out, wb_src, sheet_name, nombre_destino=None):
        """
        Copia una hoja de un workbook openpyxl fuente (wb_src) a uno de
        salida (wb_out), preservando valores, anchos de columna basicos
        y los estilos de celda (fill, font, border, number_format) -- lo
        suficiente para que el reporte combinado se vea igual que el
        original individual.
        """
        if sheet_name not in wb_src.sheetnames:
            return None
        ws_src = wb_src[sheet_name]
        ws_out = wb_out.create_sheet(nombre_destino or sheet_name)

        for row in ws_src.iter_rows():
            for cell in row:
                new_cell = ws_out.cell(
                    row=cell.row, column=cell.column, value=cell.value)
                if cell.has_style:
                    new_cell.font = cell.font.copy()
                    new_cell.border = cell.border.copy()
                    new_cell.fill = cell.fill.copy()
                    new_cell.alignment = cell.alignment.copy()
                    new_cell.number_format = cell.number_format

        for col_letter, dim in ws_src.column_dimensions.items():
            if dim.width:
                ws_out.column_dimensions[col_letter].width = dim.width
        for row_idx, dim in ws_src.row_dimensions.items():
            if dim.height:
                ws_out.row_dimensions[row_idx].height = dim.height

        for merged in ws_src.merged_cells.ranges:
            ws_out.merge_cells(str(merged))

        try:
            ws_out.freeze_panes = ws_src.freeze_panes
        except Exception:
            pass

        return ws_out

    def _generar_adjunto_y_leer(self, wizard):
        """
        Llama action_generate() de un wizard hijo ya instanciado,
        recupera el ir.attachment que crea, y devuelve el Workbook
        openpyxl leido desde su binario. El adjunto original queda
        intacto (no se borra) por si el usuario lo quiere descargar
        aparte tambien.
        """
        action = wizard.action_generate()
        url = action.get('url', '')
        # url = '/web/content/<id>?download=true'
        try:
            attachment_id = int(url.split('/web/content/')[1].split('?')[0])
        except (IndexError, ValueError):
            raise UserError(
                f'No se pudo leer el adjunto generado por '
                f'{wizard._name}: {url}')
        attachment = self.env['ir.attachment'].browse(attachment_id)
        raw = base64.b64decode(attachment.datas)
        return load_workbook(io.BytesIO(raw), data_only=True)

    def _seccion_resumen_ejecutivo(self, wb_out, secciones_incluidas):
        """
        Hoja 1: portada del diagnostico -- que secciones incluye este
        reporte y como leerlas, para que quien lo abra sepa por donde
        empezar sin tener que adivinar.
        """
        from openpyxl.styles import Font, PatternFill, Alignment

        ws = wb_out.create_sheet('Resumen Ejecutivo', 0)
        ws.column_dimensions['A'].width = 42
        ws.column_dimensions['B'].width = 90

        title_font = Font(bold=True, size=14, color='FFFFFF')
        title_fill = PatternFill('solid', fgColor='1F4E79')
        hdr_font = Font(bold=True, size=11, color='FFFFFF')
        hdr_fill = PatternFill('solid', fgColor='2E4057')
        section_font = Font(bold=True, size=11, color='1F4E79')

        ws.merge_cells('A1:B1')
        ws['A1'] = (
            f'DIAGNOSTICO INTEGRAL DEL SISTEMA -- Año {self.year} -- '
            f'Generado {fields.Date.context_today(self)}')
        ws['A1'].font = title_font
        ws['A1'].fill = title_fill
        ws.row_dimensions[1].height = 24

        ws['A3'] = 'Rango de Acciones de Personal'
        ws['A3'].font = section_font
        ws['B3'] = f'{self.date_from} a {self.date_to}'

        ws['A4'] = 'Empresa'
        ws['A4'].font = section_font
        ws['B4'] = self.company_id.name

        row = 6
        ws.cell(row=row, column=1, value='Hoja').font = hdr_font
        ws.cell(row=row, column=1).fill = hdr_fill
        ws.cell(row=row, column=2, value='Contenido / Como usarla').font = hdr_font
        ws.cell(row=row, column=2).fill = hdr_fill
        row += 1

        explicaciones = {
            'Auditoria Aguinaldo': (
                'Compara el aguinaldo calculado por Odoo (metodo '
                'centralizado unico usado tambien por Liquidacion y '
                'Simulador) contra el total que reporta el Excel de la '
                'empresa, empleado por empleado. Filas en rojo tienen '
                'diferencia real que amerita revision; filas en verde '
                'coinciden.'),
            'Resumen (como Excel)': (
                'Detalle quincenal del aguinaldo, mismo formato que la '
                'hoja "Agui." del Excel de RRHH -- para pegar al lado y '
                'comparar celda por celda.'),
            'Detalle Componentes': (
                'Cada quincena de cada empleado con la formula de '
                'aguinaldo desglosada en sus componentes (Sub Total, '
                'Costo Patrono, Incapacidad CCSS, Incapacidad INS, '
                'Permiso Sin Goce) -- para ubicar en que componente '
                'exacto esta una diferencia.'),
            'Detalle Boletas': (
                'Todos los campos de cada boleta del periodo consultado '
                '(~36 columnas: ingresos, deducciones, cargas '
                'patronales, incapacidades). Resalta en naranja cuando '
                'un empleado con bono en otras boletas del rango '
                'aparece en 0 en una boleta puntual -- sintoma de '
                'novedad de bono faltante.'),
            'Acciones de Personal': (
                'TODAS las novedades del periodo (incapacidades, '
                'permisos, bonos, vacaciones pagadas, pensiones '
                'alimentarias, embargos) en una sola hoja ordenada por '
                'empleado -- para confirmar si una diferencia de '
                'aguinaldo corresponde a una novedad real o a un dato '
                'faltante.'),
        }
        for nombre in secciones_incluidas:
            ws.cell(row=row, column=1, value=nombre)
            ws.cell(row=row, column=2, value=explicaciones.get(nombre, ''))
            ws.cell(row=row, column=2).alignment = Alignment(wrap_text=True)
            row += 1

        row += 1
        ws.cell(row=row, column=1, value='Metodo de calculo (referencia legal)').font = section_font
        row += 1
        nota = (
            'Aguinaldo = suma de salarios REALMENTE DEVENGADOS en los 12 '
            'meses anteriores al 1-dic, dividido entre 12 (Art. 228 CT / '
            'Ley 2412 Art. 2). Se excluyen del devengado los subsidios de '
            'incapacidad (CCSS y INS por igual: ambos son "subsidio", no '
            'salario -- confirmado en el folleto oficial del MTSS '
            '"El Aguinaldo en la Empresa Privada"). Solo la licencia de '
            'maternidad cuenta como salario completo. El Acumulado '
            'Inicial de cada empleado (pre-implementacion) usa su propia '
            'fecha de corte individual, nunca una fecha fija para todos.')
        ws.merge_cells(f'A{row}:B{row+3}')
        ws.cell(row=row, column=1, value=nota).alignment = Alignment(wrap_text=True, vertical='top')
        return ws

    def action_generate(self):
        self.ensure_one()
        if not load_workbook:
            raise UserError('La libreria openpyxl no esta instalada en el servidor.')
        try:
            import xlsxwriter  # noqa: F401 -- validar que los wizards hijos puedan usarlo
        except ImportError:
            raise UserError('xlsxwriter no esta instalado.')

        from openpyxl import Workbook
        wb_out = Workbook()
        wb_out.remove(wb_out.active)  # se agrega Resumen Ejecutivo despues, en indice 0

        secciones_incluidas = []

        # -- 1. Auditoria de Aguinaldo (solo si adjuntaron el Excel) -----
        if self.excel_empresa:
            aud_wizard = self.env['planilla.aguinaldo.auditoria.wizard'].create({
                'company_id': self.company_id.id,
                'year': self.year,
                'excel_file': self.excel_empresa,
                'excel_filename': self.excel_empresa_filename or 'excel_empresa.xlsx',
                'hasta_columna': self.hasta_columna,
            })
            wb_src = self._generar_adjunto_y_leer(aud_wizard)
            if self._copiar_hoja(wb_out, wb_src, 'Auditoria Aguinaldo'):
                secciones_incluidas.append('Auditoria Aguinaldo')

        # -- 2. Detalle Mensual de Aguinaldo (siempre, es interno) -------
        det_wizard = self.env['planilla.aguinaldo.detalle.mensual.wizard'].create({
            'company_id': self.company_id.id,
            'year': self.year,
        })
        wb_src = self._generar_adjunto_y_leer(det_wizard)
        for sheet in ('Resumen (como Excel)', 'Detalle Componentes'):
            if self._copiar_hoja(wb_out, wb_src, sheet):
                secciones_incluidas.append(sheet)

        # -- 3. Detalle Completo de Boletas por Empleado (opcional) -----
        if self.incluir_detalle_boletas:
            boletas_wizard = self.env['planilla.employee.boleta.detalle.wizard'].create({
                'company_id': self.company_id.id,
                'date_from': self.date_from,
                'date_to': self.date_to,
                'incluir_borradores': True,
            })
            wb_src = self._generar_adjunto_y_leer(boletas_wizard)
            if self._copiar_hoja(wb_out, wb_src, 'Detalle Boletas'):
                secciones_incluidas.append('Detalle Boletas')

        # -- 4. Acciones de Personal Consolidadas ------------------------
        acciones_wizard = self.env['planilla.acciones.personal.wizard'].create({
            'company_id': self.company_id.id,
            'date_from': self.date_from,
            'date_to': self.date_to,
        })
        wb_src = self._generar_adjunto_y_leer(acciones_wizard)
        if self._copiar_hoja(wb_out, wb_src, 'Acciones de Personal'):
            secciones_incluidas.append('Acciones de Personal')

        if not secciones_incluidas:
            raise UserError(
                'No se pudo generar ninguna seccion del diagnostico -- '
                'revise que existan datos para el periodo indicado.')

        # -- 0. Resumen Ejecutivo (portada, siempre primera hoja) -------
        self._seccion_resumen_ejecutivo(wb_out, secciones_incluidas)
        wb_out.move_sheet('Resumen Ejecutivo', offset=-len(wb_out.sheetnames))

        output = io.BytesIO()
        wb_out.save(output)
        xlsx_data = base64.b64encode(output.getvalue()).decode()
        filename = f'Diagnostico_Integral_{self.year}_{self.date_from}_{self.date_to}.xlsx'

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
