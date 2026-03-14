from odoo import models, fields, api
import base64, io

# ── openpyxl ─────────────────────────────────────────────────────────────────
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.comments import Comment


class ImportTemplateWizard(models.TransientModel):
    """Genera y descarga el machote Excel para carga masiva de empleados."""
    _name        = 'planilla.import.template.wizard'
    _description = 'Machote de Importación de Empleados'

    company_id = fields.Many2one(
        'res.company', string='Empresa', required=True,
        default=lambda self: self.env.company,
    )
    include_employees    = fields.Boolean('Empleados (datos principales)', default=True)
    include_loans        = fields.Boolean('Préstamos y Adelantos',         default=True)
    include_pension      = fields.Boolean('Pensiones Alimentarias',        default=True)
    include_benefits     = fields.Boolean('Beneficios / Deducciones Recurrentes', default=True)
    include_disabilities = fields.Boolean('Incapacidades',                 default=True)
    include_vacations    = fields.Boolean('Saldo de Vacaciones',           default=True)
    include_overtime     = fields.Boolean('⏱️  Horas Extras (histórico)',   default=True)
    include_sample_data  = fields.Boolean(
        '🧪  Incluir fila de prueba (EMPLEADO PRUEBA)',
        default=False,
        help='Agrega una fila naranja de prueba en todas las hojas con cédula 1-0000-0001. '
             'Active solo cuando quiera verificar que la importación funciona correctamente. '
             'Luego use el botón "Eliminar Empleado de Prueba" para limpiar.'
    )

    # Cédula reservada para la fila de prueba — misma en template y en import wizard
    _SAMPLE_CEDULA = '1-0000-0001'

    # ── paleta ────────────────────────────────────────────────────────────────
    _C = {
        'dark':     '1F3864',
        'med':      '2E75B6',
        'light':    'D6E4F0',
        'req':      'FFF2CC',
        'opt':      'FFFFFF',
        'example':  'E2EFDA',
        'border':   'BDD7EE',
        'white':    'FFFFFF',
        'red_hdr':  'C00000',
    }

    # ── helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _fill(hex_color):
        return PatternFill('solid', fgColor=hex_color)

    @staticmethod
    def _font(bold=False, color='000000', size=10, italic=False):
        return Font(bold=bold, color=color, size=size, italic=italic, name='Arial')

    @staticmethod
    def _border():
        s = Side(style='thin', color='BDD7EE')
        return Border(left=s, right=s, top=s, bottom=s)

    @staticmethod
    def _center():
        return Alignment(horizontal='center', vertical='center', wrap_text=True)

    @staticmethod
    def _left():
        return Alignment(horizontal='left', vertical='center', wrap_text=True)

    def _hdr(self, cell, text, bg=None, txt='FFFFFF', bold=True, size=10):
        C = self._C
        cell.value     = text
        cell.font      = self._font(bold=bold, color=txt, size=size)
        cell.fill      = self._fill(bg or C['med'])
        cell.border    = self._border()
        cell.alignment = self._center()

    def _col_hdr(self, cell, text, required, desc=''):
        C = self._C
        bg  = C['req'] if required else C['light']
        txt = '7B3F00' if required else C['dark']
        cell.value     = text
        cell.font      = self._font(bold=True, color=txt, size=9)
        cell.fill      = self._fill(bg)
        cell.border    = self._border()
        cell.alignment = self._center()
        if desc:
            c = Comment(desc, 'Planilla CR')
            c.width, c.height = 220, 55
            cell.comment = c

    def _example(self, cell, value):
        cell.value     = value
        cell.fill      = self._fill(self._C['example'])
        cell.border    = self._border()
        cell.alignment = self._left()
        cell.font      = self._font(italic=True, size=9, color='375623')

    def _data(self, cell, required=True):
        bg = self._C['req'] if required else self._C['opt']
        cell.fill      = self._fill(bg)
        cell.border    = self._border()
        cell.alignment = self._left()
        cell.font      = self._font(size=10)

    @staticmethod
    def _w(ws, col_idx, width):
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    # ── título de hoja ────────────────────────────────────────────────────────
    def _sheet_title(self, ws, text, ncols, bg=None):
        C = self._C
        col_letter = get_column_letter(ncols)
        ws.merge_cells(f'A1:{col_letter}1')
        c = ws['A1']
        c.value     = text
        c.font      = self._font(bold=True, color=C['white'], size=12)
        c.fill      = self._fill(bg or C['dark'])
        c.alignment = self._center()
        ws.row_dimensions[1].height = 28

    # ── filas de datos (vacías + ejemplo) ─────────────────────────────────────
    def _sample(self, cell, value):
        """Estilo para la fila de prueba: fondo naranja, texto oscuro, itálica."""
        cell.value     = value
        cell.fill      = self._fill('F4B942')   # naranja
        cell.border    = self._border()
        cell.alignment = self._left()
        cell.font      = self._font(italic=True, bold=True, size=9, color='7B2D00')

    def _build_rows(self, ws, cols, data_rows=80, header_row=2, example_row=3,
                    sample_values=None):
        """
        cols = [(nombre, required, width, ejemplo, desc), ...]
        sample_values: lista de valores para la fila de prueba (naranja).
                       Si es None, no se dibuja fila de prueba.
        """
        # Encabezados
        for ci, (nombre, req, w, _, desc) in enumerate(cols, 1):
            self._col_hdr(ws.cell(header_row, ci), nombre, req, desc)
            self._w(ws, ci, w)
        ws.row_dimensions[header_row].height = 45

        # Fila de ejemplo (verde)
        for ci, (_, _, _, ej, _) in enumerate(cols, 1):
            self._example(ws.cell(example_row, ci), ej)
        ws.row_dimensions[example_row].height = 16

        # Fila de prueba (naranja) — opcional, justo debajo del ejemplo
        data_start = example_row + 1
        if sample_values:
            sample_row = example_row + 1
            data_start = sample_row + 1
            for ci, val in enumerate(sample_values, 1):
                self._sample(ws.cell(sample_row, ci), val)
            ws.row_dimensions[sample_row].height = 16

        # Filas vacías
        for r in range(data_start, data_start + data_rows):
            for ci, (_, req, _, _, _) in enumerate(cols, 1):
                self._data(ws.cell(r, ci), req)
            ws.row_dimensions[r].height = 16

        ws.freeze_panes = ws.cell(example_row, 1)
        ws.sheet_view.showGridLines = False

    # ══════════════════════════════════════════════════════════════════════════
    # HOJA INSTRUCCIONES
    # ══════════════════════════════════════════════════════════════════════════
    def _build_instructions(self, wb):
        C = self._C
        ws = wb.active
        ws.title = '📋 INSTRUCCIONES'
        ws.sheet_view.showGridLines = False

        ws.merge_cells('A1:H1')
        c = ws['A1']
        c.value     = (f'MACHOTE DE IMPORTACIÓN — SISTEMA PLANILLA v5.4  '
                       f'|  {self.company_id.name}  |  Legislación CR 2026')
        c.font      = self._font(bold=True, color=C['white'], size=13)
        c.fill      = self._fill(C['dark'])
        c.alignment = self._center()
        ws.row_dimensions[1].height = 34

        ws.merge_cells('A2:H2')
        c = ws['A2']
        c.value     = 'Carga masiva de empleados — complete las hojas y entregue al implementador'
        c.font      = self._font(italic=True, color=C['white'], size=10)
        c.fill      = self._fill(C['med'])
        c.alignment = self._center()
        ws.row_dimensions[2].height = 18

        lines = [
            ('', ''),
            ('¿QUÉ ES ESTE ARCHIVO?', ''),
            ('', 'Permite cargar todos los empleados y sus datos al módulo Planilla CR de Odoo '
                 'de una sola vez, evitando la digitación manual uno a uno.'),
            ('', ''),
            ('HOJAS INCLUIDAS', ''),
            ('', '👤  EMPLEADOS            → Datos principales (obligatorio completar)'),
            ('', '💰  PRESTAMOS            → Préstamos y adelantos activos del empleado'),
            ('', '👨‍👧  PENSION_ALIMENTARIA   → Órdenes judiciales de pensión alimentaria'),
            ('', '➕  BENEFICIOS           → Pluses, subsidios, embargos recurrentes'),
            ('', '🏥  INCAPACIDADES        → Incapacidades activas al momento de la carga'),
            ('', '🏖️  VACACIONES           → Saldo de vacaciones acumulado'),
            ('', '📚  CATALOGOS            → Valores válidos para campos de lista (NO editar)'),
            ('', ''),
            ('INSTRUCCIONES', ''),
            ('', '1. Complete la hoja EMPLEADOS — un empleado por fila.'),
            ('', '2. Use la cédula como llave: debe coincidir exactamente en todas las hojas.'),
            ('', '3. Para préstamos, pensiones o beneficios múltiples: agregue una fila por cada uno.'),
            ('', '4. Respete los valores exactos de la hoja CATALOGOS en los campos de selección.'),
            ('', '5. Fechas en formato DD/MM/AAAA  (ejemplo: 15/03/2020).'),
            ('', '6. Montos en colones (₡), sin símbolo ni comas  (ejemplo: 750000).'),
            ('', '7. La fila de PRUEBA (fondo naranja, cédula 1-0000-0001) sirve para verificar que la importación funciona. Elimine ese empleado luego.'),
            ('', '8. NO modifique los encabezados ni el nombre de las hojas.'),
            ('', ''),
            ('CÓDIGO DE COLORES', ''),
        ]

        for i, (label, text) in enumerate(lines, start=3):
            ws.row_dimensions[i].height = 18
            if label:
                ws.merge_cells(f'A{i}:H{i}')
                c = ws.cell(i, 1, value=label)
                c.font      = self._font(bold=True, color=C['dark'], size=10)
                c.fill      = self._fill(C['light'])
                c.alignment = self._left()
            else:
                ws.merge_cells(f'B{i}:H{i}')
                ws.cell(i, 2, value=text).font = self._font(size=10)

        # leyenda colores
        last = ws.max_row + 1
        leyenda = [
            (C['req'],     '🟡 Fondo AMARILLO → Campo OBLIGATORIO'),
            (C['opt'],     '⬜ Fondo BLANCO   → Campo OPCIONAL'),
            (C['example'], '🟢 Fondo VERDE    → Fila de EJEMPLO (solo referencia, no se importa)'),
            ('F4B942',     '🟠 Fondo NARANJA  → Fila de PRUEBA (cédula 1-0000-0001) — importar para verificar, luego eliminar'),
        ]
        for offset, (color, texto) in enumerate(leyenda):
            r = last + offset
            ws.cell(r, 1).fill   = self._fill(color)
            ws.cell(r, 1).border = self._border()
            ws.merge_cells(f'B{r}:H{r}')
            c = ws.cell(r, 2, value=texto)
            c.font = self._font(size=10)
            ws.row_dimensions[r].height = 18

        ws.column_dimensions['A'].width = 30
        ws.column_dimensions['B'].width = 90

    # ══════════════════════════════════════════════════════════════════════════
    # HOJA EMPLEADOS
    # ══════════════════════════════════════════════════════════════════════════
    def _build_employees(self, wb, sample=False):
        ws = wb.create_sheet('👤 EMPLEADOS')
        self._sheet_title(ws, 'DATOS DE EMPLEADOS — Un empleado por fila', 38)

        # Secciones (fila 2)
        secciones = [
            (1,  6,  'IDENTIFICACIÓN'),
            (7,  13, 'DATOS LABORALES'),
            (14, 20, 'DATOS INS'),
            (21, 26, 'CCSS Y BANCO'),
            (27, 32, 'CONFIGURACIÓN NÓMINA'),
            (33, 38, 'DATOS PERSONALES'),
        ]
        for cs, ce, titulo in secciones:
            ws.merge_cells(start_row=2, start_column=cs,
                           end_row=2,   end_column=ce)
            self._hdr(ws.cell(2, cs), titulo)
            for ci in range(cs + 1, ce + 1):
                ws.cell(2, ci).fill = self._fill(self._C['med'])
        ws.row_dimensions[2].height = 18

        cols = [
            # Identificación (cols 1-6)
            ('Nombre Completo',           True,  28, 'Juan Pérez Rodríguez',     'Nombre completo del empleado'),
            ('Cédula / Identificación',   True,  18, '1-2345-6789',              'Cédula, DIMEX o pasaporte — llave entre hojas'),
            ('Tipo de Identificación',    True,  18, '01',                       'Ver CATALOGOS → id_type  (01 Cédula / 02 DIMEX / 03 Permiso / 04 Pasaporte)'),
            ('Fecha de Ingreso',          True,  14, '01/03/2020',               'Formato DD/MM/AAAA'),
            ('Fecha de Salida',           False, 14, '',                         'Solo si ya no trabaja en la empresa'),
            ('Correo Corporativo',        False, 28, 'juan.perez@empresa.com',   'Email de trabajo en Odoo'),
            # Datos laborales (cols 7-13)
            ('Departamento',              False, 22, 'Administración',           'Nombre del departamento (debe existir en Odoo)'),
            ('Sub Departamento',          False, 22, 'Contabilidad',             'Sub departamento dentro del departamento principal'),
            ('Sucursal',                  False, 20, 'Casa Matriz',              'Nombre de la sucursal (debe existir en Odoo)'),
            ('Puesto / Cargo',            False, 22, 'Asistente Administrativo', 'Título del puesto'),
            ('Tipo de Empleado',          True,  18, 'planilla',                 'Ver CATALOGOS → employee_type (nombre exacto en Odoo)'),
            ('Estado del Empleado',       True,  18, 'activo',                   'Ver CATALOGOS → employee_status (nombre exacto en Odoo)'),
            ('Tipo de Horario',           True,  22, 'jornada_ordinaria',        'Ver CATALOGOS → schedule_type (nombre exacto en Odoo)'),
            # Datos INS (cols 14-20)
            ('Incluir en INS',            True,  12, 'si',                       'si / no'),
            ('Número de Póliza INS',      False, 18, 'POL-12345',                'Número de póliza del INS'),
            ('Nombre INS',                False, 18, 'Juan',                     'Nombre como aparece en el sistema INS'),
            ('Primer Apellido INS',       False, 16, 'Pérez',                    ''),
            ('Segundo Apellido INS',      False, 16, 'Rodríguez',                ''),
            ('Clase de Riesgo INS',       True,  16, 'I',                        'Ver CATALOGOS → ins_risk_class  (I / II / III / IV / V)'),
            ('Jornada INS',               True,  18, '01',                       'Ver CATALOGOS → ins_workday_type  (01 Ordinaria / 02 Extraordinaria / 03 Mixta / 04 Tiempo Parcial / 05 Por Horas / 06 Ocasional)'),
            # CCSS y banco (cols 21-26)
            ('Número CCSS',               False, 16, '123456789',                'Número de asegurado CCSS'),
            ('Asegurado CCSS',            True,  14, 'si',                       'si / no'),
            ('Cuenta Bancaria / IBAN',    False, 30, 'CR21015108010018023571',   'IBAN de 22 caracteres'),
            ('SINPE Móvil',               False, 14, '88887777',                 'Teléfono registrado en SINPE Móvil'),
            ('Banco',                     False, 20, 'BNCR',                     'Ver CATALOGOS → bank'),
            ('Tipo de Cuenta Banco',      False, 16, 'corriente',                'corriente / ahorros / sinpe  — Ver CATALOGOS → account_type'),
            # Configuración nómina (cols 27-32)
            ('Salario Base (₡)',          True,  18, '750000',                   'Salario mensual en colones, sin comas ni símbolo'),
            ('Fecha Vigencia Salarial',   False, 18, '01/01/2026',               'Desde cuándo aplica el salario'),
            ('Frecuencia de Pago',        True,  18, 'monthly',                  'Ver CATALOGOS → frequency'),
            ('Método de Cálculo',         True,  18, 'fixed',                    'Ver CATALOGOS → calc_method  (fixed / attendance)'),
            ('Ocupación INS',             True,  20, '4110',                     'Código numérico INS — ver CATALOGOS → ins_occupation'),
            ('Nacionalidad INS',          False, 14, 'CR',                       'Ver CATALOGOS → ins_nationality  (CR / NI / CO / US / OT…)'),
            # Datos personales (cols 33-38)
            ('Estado Civil INS',          False, 16, '01',                       'Ver CATALOGOS → ins_civil_status  (01 Soltero/a / 02 Casado/a / 03 Divorciado/a / 04 Viudo/a / 05 Unión Libre / 06 Separado/a)'),
            ('Género',                    False, 12, 'masculino',                'masculino / femenino / otro'),
            ('Número de Dependientes',    False, 12, '0',                        'Hijos u otros dependientes'),
            ('Dirección',                 False, 30, 'San José, Escazú',         'Dirección de habitación'),
            ('Teléfono Personal',         False, 14, '88887777',                 ''),
            ('Observaciones',             False, 30, '',                         'Notas internas del empleado'),
        ]

        sv = None
        if sample:
            sv = [
                'EMPLEADO PRUEBA', self._SAMPLE_CEDULA, '01', '01/01/2024', '', 'prueba@empresa.com',
                '', '', '', 'Puesto Prueba', 'planilla', 'activo', 'jornada_ordinaria',
                'si', '', 'Prueba', 'Prueba', 'Prueba', 'I', '01',
                '', 'si', '', '', 'BNCR', 'corriente',
                '500000', '01/01/2024', 'monthly', 'fixed', '4110', 'CR',
                '01', 'masculino', '0', 'San José', '88880000',
                '⚠️ FILA DE PRUEBA — eliminar después de verificar importación',
            ]
        self._build_rows(ws, cols, data_rows=100, header_row=3, example_row=4,
                         sample_values=sv)

    # ══════════════════════════════════════════════════════════════════════════
    # HOJA PRÉSTAMOS
    # ══════════════════════════════════════════════════════════════════════════
    def _build_loans(self, wb, sample=False):
        cols = [
            ('Cédula Empleado',          True,  18, '1-2345-6789',     'Debe coincidir con hoja EMPLEADOS'),
            ('Tipo de Préstamo',         True,  16, 'loan',            'loan = Préstamo   /   advance = Adelanto'),
            ('Descripción / Motivo',     False, 30, 'Préstamo personal',''),
            ('Monto Total (₡)',          True,  16, '500000',          'Total del préstamo, sin comas'),
            ('Número de Cuotas',         True,  14, '10',              'Cantidad de cuotas a descontar'),
            ('Fecha de Otorgamiento',    True,  18, '15/01/2026',      'DD/MM/AAAA'),
            ('Fecha Primera Deducción',  True,  18, '01/02/2026',      'DD/MM/AAAA — primer boleta que descuenta'),
            ('Estado',                   True,  14, 'approved',        'approved / active  (ver CATALOGOS)'),
            ('Monto ya Pagado (₡)',      False, 16, '100000',          'Si ya se ha descontado algo'),
            ('Observaciones',            False, 28, '',                ''),
        ]
        sv = [self._SAMPLE_CEDULA, 'loan', 'Préstamo de prueba', '100000', '5',
              '01/01/2024', '01/02/2024', 'approved', '0', '⚠️ PRUEBA'] if sample else None
        ws = wb.create_sheet('💰 PRESTAMOS')
        self._sheet_title(ws, 'PRÉSTAMOS Y ADELANTOS — Un préstamo por fila (puede haber varios por empleado)', len(cols))
        self._build_rows(ws, cols, data_rows=100, sample_values=sv)

    # ══════════════════════════════════════════════════════════════════════════
    # HOJA PENSIÓN ALIMENTARIA
    # ══════════════════════════════════════════════════════════════════════════
    def _build_pension(self, wb, sample=False):
        cols = [
            ('Cédula Empleado',        True,  18, '1-2345-6789',          'Cédula del empleado afectado'),
            ('Número de Expediente',   True,  22, '15-000123-0638-FA',    'Número del expediente judicial'),
            ('Juzgado',                True,  30, 'Juzgado de Familia SJ', ''),
            ('Fecha de Resolución',    True,  18, '10/06/2023',            'DD/MM/AAAA'),
            ('Nombre Beneficiario',    True,  26, 'María Rodríguez Solano','Nombre completo'),
            ('Relación Beneficiario',  True,  16, 'hijo',                  'hijo / conyuge / padre / madre / otro'),
            ('Cuenta Beneficiario',    False, 28, 'CR21015108010018023571','IBAN del beneficiario (opcional)'),
            ('Tipo de Cálculo',        True,  16, 'porcentaje',            'porcentaje / monto_fijo'),
            ('Porcentaje (%)',          False, 12, '25',                   'Si tipo=porcentaje, solo el número (ej: 25)'),
            ('Monto Fijo (₡)',         False, 14, '',                     'Si tipo=monto_fijo, monto en colones'),
            ('Fecha de Inicio',        True,  14, '01/07/2023',            'DD/MM/AAAA'),
            ('Fecha de Fin',           False, 14, '',                     'Dejar vacío si no tiene vencimiento'),
        ]
        sv = [self._SAMPLE_CEDULA, 'TEST-0000-PRUEBA', 'Juzgado Prueba', '01/01/2024',
              'Beneficiario Prueba', 'hijo', '', 'porcentaje', '10', '',
              '01/01/2024', ''] if sample else None
        ws = wb.create_sheet('👨‍👧 PENSION_ALIMENTARIA')
        self._sheet_title(ws, 'PENSIONES ALIMENTARIAS — Una resolución por fila', len(cols))
        self._build_rows(ws, cols, sample_values=sv)

    # ══════════════════════════════════════════════════════════════════════════
    # HOJA BENEFICIOS RECURRENTES
    # ══════════════════════════════════════════════════════════════════════════
    def _build_benefits(self, wb, sample=False):
        cols = [
            ('Cédula Empleado',   True,  18, '1-2345-6789',        'Cédula del empleado'),
            ('Concepto',          True,  28, 'Plus de transporte',  'Nombre descriptivo del concepto'),
            ('Tipo',              True,  14, 'beneficio',           'beneficio / deduccion'),
            ('Tipo de Monto',     True,  16, 'fijo',                'fijo / porcentaje'),
            ('Monto (₡)',         False, 14, '15000',               'Si tipo_monto=fijo'),
            ('Porcentaje (%)',    False, 12, '',                    'Si tipo_monto=porcentaje, solo el número'),
            ('Código Deducción',  False, 16, '',                    'Código del concepto si el módulo lo requiere'),
            ('Vigente Desde',     True,  14, '01/01/2026',          'DD/MM/AAAA'),
            ('Vigente Hasta',     False, 14, '',                    'Dejar vacío si es indefinido'),
            ('Nota',              False, 28, 'Acuerdo colectivo 2026','Descripción o referencia'),
        ]
        sv = [self._SAMPLE_CEDULA, 'Plus Prueba', 'beneficio', 'fijo',
              '5000', '', '', '01/01/2024', '', '⚠️ PRUEBA'] if sample else None
        ws = wb.create_sheet('➕ BENEFICIOS')
        self._sheet_title(ws, 'BENEFICIOS Y DEDUCCIONES RECURRENTES — Pluses, subsidios, embargos, etc.', len(cols))
        self._build_rows(ws, cols, data_rows=100, sample_values=sv)

    def _build_disabilities(self, wb, sample=False):
        cols = [
            ('Cédula Empleado',      True,  18, '1-2345-6789',    ''),
            ('Tipo de Incapacidad',  True,  22, 'enfermedad',     'enfermedad / accidente_trabajo / maternidad / paternidad'),
            ('Fecha Inicio',         True,  14, '01/02/2026',     'DD/MM/AAAA'),
            ('Fecha Fin',            True,  14, '10/02/2026',     'DD/MM/AAAA'),
            ('% Subsidiado CCSS',    False, 14, '60',             'Porcentaje que paga la CCSS'),
            ('% a Cargo Patrono',    False, 14, '40',             'Porcentaje que asume el patrono'),
            ('Número Certificado',   False, 20, 'CCSS-2026-123',  'Número del certificado CCSS'),
            ('Diagnóstico',          False, 28, 'Gripa severa',   'Descripción del diagnóstico'),
            ('Salario Diario (₡)',   False, 16, '25000',          'Salario mensual ÷ 30'),
            ('Observaciones',        False, 28, '',               ''),
        ]
        sv = [self._SAMPLE_CEDULA, 'ccss', '01/01/2024', '05/01/2024',
              '60', '40', 'PRUEBA-0000', 'Diagnóstico prueba', '16667',
              '⚠️ PRUEBA'] if sample else None
        ws = wb.create_sheet('🏥 INCAPACIDADES')
        self._sheet_title(ws, 'INCAPACIDADES — Solo las activas o dentro del período de carga', len(cols))
        self._build_rows(ws, cols, sample_values=sv)

    def _build_vacations(self, wb, sample=False):
        cols = [
            ('Cédula Empleado',              True,  18, '1-2345-6789',      ''),
            ('Días Acumulados',              True,  16, '8.5',              'Total de días ganados hasta la fecha de corte'),
            ('Días Tomados',                 False, 14, '3',                'Días ya disfrutados en el período'),
            ('Días Disponibles',             False, 14, '5.5',              'Solo referencia: Acumulados − Tomados'),
            ('Última Fecha de Corte',        False, 18, '31/12/2025',       'Hasta cuándo se calcularon los días'),
            ('Salario Diario Referencia (₡)',False, 20, '25000',            'Para calcular el pago en colones'),
            ('Período de Referencia',        False, 22, 'Ene–Dic 2025',     'Período al que corresponden los días'),
            ('Observaciones',                False, 28, '',                  ''),
        ]
        sv = [self._SAMPLE_CEDULA, '5', '2', '3', '31/12/2023',
              '16667', 'Ene–Dic 2023', '⚠️ PRUEBA'] if sample else None
        ws = wb.create_sheet('🏖️ VACACIONES')
        self._sheet_title(ws, 'SALDO DE VACACIONES — Días acumulados al momento de la carga', len(cols))
        self._build_rows(ws, cols, sample_values=sv)

    def _build_overtime(self, wb, sample=False):
        cols = [
            ('Cédula Empleado',      True,  18, '1-2345-6789',    ''),
            ('Fecha',                True,  14, '01/02/2026',      'DD/MM/AAAA'),
            ('Tipo de Hora Extra',   True,  20, 'simple',          'simple / double / holiday'),
            ('Cantidad de Horas',    True,  16, '2.5',             'Número de horas extras trabajadas'),
            ('Salario por Hora (₡)', False, 18, '3500',            'Salario mensual ÷ 240 (o según contrato)'),
            ('Monto Total (₡)',      False, 16, '8750',            'Horas × Salario × Factor (1.5 / 2.0)'),
            ('Período de Planilla',  False, 22, 'Febrero 2026',    'Período al que se carga esta hora extra'),
            ('Observaciones',        False, 28, '',                 ''),
        ]
        sv = [self._SAMPLE_CEDULA, '15/01/2024', 'simple', '2',
              '2083', '6250', 'Enero 2024', '⚠️ PRUEBA'] if sample else None
        ws = wb.create_sheet('⏱️ HORAS EXTRAS')
        self._sheet_title(ws, 'HORAS EXTRAS — Registros históricos a importar', len(cols))
        self._build_rows(ws, cols, sample_values=sv)

    # ══════════════════════════════════════════════════════════════════════════
    # HOJA CATÁLOGOS
    # ══════════════════════════════════════════════════════════════════════════
    def _build_catalogs(self, wb):
        C = self._C
        ws = wb.create_sheet('📚 CATALOGOS')
        ws.sheet_view.showGridLines = False

        ws.merge_cells('A1:C1')
        c = ws['A1']
        c.value     = 'CATÁLOGOS DE VALORES VÁLIDOS — ⚠️ No editar esta hoja'
        c.font      = self._font(bold=True, color=C['white'], size=12)
        c.fill      = self._fill(C['red_hdr'])
        c.alignment = self._center()
        ws.row_dimensions[1].height = 28

        CATALOGS = [
            ('id_type — Tipo de Identificación', [
                ('01',  'Cédula Nacional'),
                ('02',  'Residencia / DIMEX'),
                ('03',  'Permiso de Trabajo'),
                ('04',  'Pasaporte'),
                ('05',  'Indocumentado'),
            ]),
            ('employee_type — Tipo de Empleado (buscar por nombre exacto en Odoo)', [
                ('planilla',    'Ejemplo: nombre del tipo tal como aparece en Configuración → Tipos de Empleado'),
                ('contratado',  'Use el nombre exacto del registro en Odoo'),
            ]),
            ('employee_status — Estado del Empleado (buscar por nombre en Odoo)', [
                ('activo',      'Use el nombre exacto del estado en Configuración → Estados'),
            ]),
            ('schedule_type — Tipo de Horario (nombre exacto en Odoo)', [
                ('',  'Use el nombre del horario tal como aparece en Configuración → Tipos de Horario'),
            ]),
            ('frequency — Frecuencia de Pago', [
                ('monthly',   'Mensual — 1 pago al mes'),
                ('biweekly',  'Quincenal — 2 pagos al mes'),
                ('weekly',    'Semanal — 4 pagos al mes'),
                ('bimonthly', 'Bimensual — cada 2 meses'),
            ]),
            ('ins_risk_class — Clase de Riesgo INS', [
                ('I',   'Clase I   — Oficinas y administrativo (~0.87%)'),
                ('II',  'Clase II  — Comercio (~1.49%)'),
                ('III', 'Clase III — Industria liviana (~2.47%)'),
                ('IV',  'Clase IV  — Construcción / riesgo alto (~4.13%)'),
                ('V',   'Clase V   — Actividades de alto riesgo (~6.88%)'),
            ]),
            ('ins_workday_type — Tipo de Jornada INS', [
                ('01', 'Ordinaria — jornada diurna regular'),
                ('02', 'Extraordinaria — horas extra autorizadas'),
                ('03', 'Mixta — parte diurna y parte nocturna'),
                ('04', 'Tiempo Parcial — menos de jornada completa'),
                ('05', 'Por Horas — según horas efectivamente trabajadas'),
                ('06', 'Ocasional — trabajo esporádico o temporal'),
            ]),
            ('ins_id_type — Tipo de ID INS', [
                ('01', 'Cédula de Costa Rica'),
                ('02', 'Residencia de Costa Rica / DIMEX'),
                ('03', 'Permiso de Trabajo'),
                ('04', 'Pasaporte'),
                ('05', 'Indocumentado'),
            ]),
            ('ins_civil_status — Estado Civil INS', [
                ('01', 'Soltero/a'),
                ('02', 'Casado/a'),
                ('03', 'Divorciado/a'),
                ('04', 'Viudo/a'),
                ('05', 'Unión Libre'),
                ('06', 'Separado/a'),
            ]),
            ('ins_nationality — Nacionalidad INS', [
                ('CR', 'Costarricense'),
                ('NI', 'Nicaragüense'),
                ('CO', 'Colombiana'),
                ('US', 'Estadounidense'),
                ('HN', 'Hondureña'),
                ('SV', 'Salvadoreña'),
                ('GT', 'Guatemalteca'),
                ('PA', 'Panameña'),
                ('MX', 'Mexicana'),
                ('VE', 'Venezolana'),
                ('PE', 'Peruana'),
                ('EC', 'Ecuatoriana'),
                ('OT', 'Otra nacionalidad'),
            ]),
            ('account_type — Tipo de Cuenta Banco', [
                ('corriente', 'Cuenta Corriente / IBAN'),
                ('ahorros',   'Cuenta de Ahorros'),
                ('sinpe',     'SINPE Móvil'),
            ]),
            ('bank — Banco', [
                ('BNCR',       'Banco Nacional de Costa Rica'),
                ('BCR',        'Banco de Costa Rica'),
                ('BP',         'Banco Popular y de Desarrollo Comunal'),
                ('BAC',        'BAC San José'),
                ('BCT',        'Banco BCT'),
                ('CATHAY',     'Banco Cathay de Costa Rica'),
                ('CMB',        'Banco CMB'),
                ('DAVIVIENDA', 'Banco Davivienda'),
                ('GENERAL',    'Banco General'),
                ('IMPROSA',    'Banco Improsa'),
                ('LAFISE',     'Banco La Fise'),
                ('PROMERICA',  'Banco Promerica'),
                ('PRIVAL',     'Prival Bank'),
                ('SCOTIA',     'Scotiabank'),
                ('COOCIQUE',   'Coocique R.L.'),
                ('COOPENAE',   'Coopenae R.L.'),
                ('MUTUAL_ALJ', 'Mutual Alajuela'),
                ('OTRO',       'Otro banco / cooperativa'),
            ]),
            ('loan_type — Tipo de Préstamo', [
                ('loan',    'Préstamo de empresa'),
                ('advance', 'Adelanto de salario'),
            ]),
            ('loan_state — Estado del Préstamo', [
                ('draft',     'Borrador — pendiente de aprobación'),
                ('approved',  'Aprobado — se activará en la próxima boleta'),
                ('active',    'En curso — descuento activo'),
                ('paid',      'Cancelado — totalmente pagado'),
                ('cancelled', 'Anulado'),
            ]),
            ('pension_relacion — Relación Beneficiario', [
                ('hijo',    'Hijo/a'),
                ('conyuge', 'Cónyuge / Conviviente'),
                ('padre',   'Padre'),
                ('madre',   'Madre'),
                ('otro',    'Otro'),
            ]),
            ('pension_calc — Tipo de Cálculo Pensión', [
                ('percentage', 'Porcentaje del salario bruto'),
                ('fixed',      'Monto fijo mensual en colones'),
            ]),
            ('benefit_type — Tipo de Beneficio/Deducción', [
                ('income',    'Ingreso / Beneficio (suma al bruto)'),
                ('deduction', 'Deducción / Descuento (resta al neto)'),
            ]),
            ('amount_type — Tipo de Monto', [
                ('fixed',      'Monto fijo en colones'),
                ('percentage', 'Porcentaje del salario base'),
            ]),
            ('disability_type — Tipo de Incapacidad', [
                ('ccss',          'CCSS — Enfermedad común'),
                ('ccss_accident', 'CCSS — Accidente laboral'),
                ('ins',           'INS — Riesgo laboral'),
                ('maternity',     'Maternidad / Paternidad'),
                ('other',         'Otro tipo de incapacidad'),
            ]),
            ('overtime_type — Tipo de Hora Extra', [
                ('simple',  'Simple (1.5x) — hora extra ordinaria'),
                ('double',  'Doble (2.0x) — hora extra nocturna o dominical'),
                ('holiday', 'Día Feriado — trabajo en día feriado'),
            ]),
            ('calc_method — Método de Cálculo de Planilla', [
                ('fixed',      'Salario Fijo — sin consultar asistencias'),
                ('attendance', 'Por Horas Trabajadas — según módulo de asistencias'),
            ]),
        ]

        row = 3
        for titulo, valores in CATALOGS:
            # Encabezado de sección
            ws.merge_cells(start_row=row, start_column=1,
                           end_row=row,   end_column=3)
            c = ws.cell(row, 1, value=titulo)
            c.font      = self._font(bold=True, color=C['white'], size=10)
            c.fill      = self._fill(C['med'])
            c.border    = self._border()
            c.alignment = self._left()
            for ci in (2, 3):
                ws.cell(row, ci).fill   = self._fill(C['med'])
                ws.cell(row, ci).border = self._border()
            ws.row_dimensions[row].height = 20
            row += 1

            # Sub-encabezado
            for ci, hdr in enumerate(['Valor a usar (exacto)', 'Descripción'], 1):
                c = ws.cell(row, ci, value=hdr)
                c.font      = self._font(bold=True, color=C['dark'], size=9)
                c.fill      = self._fill(C['light'])
                c.border    = self._border()
                c.alignment = self._center()
            ws.row_dimensions[row].height = 16
            row += 1

            # Valores
            for val, desc in valores:
                cv = ws.cell(row, 1, value=val)
                cv.font      = self._font(bold=True, color='00008B', size=10)
                cv.fill      = self._fill(C['opt'])
                cv.border    = self._border()
                cv.alignment = self._left()

                cd = ws.cell(row, 2, value=desc)
                cd.font      = self._font(size=10)
                cd.fill      = self._fill(C['opt'])
                cd.border    = self._border()
                cd.alignment = self._left()
                ws.row_dimensions[row].height = 15
                row += 1

            row += 1  # separador

        ws.column_dimensions['A'].width = 24
        ws.column_dimensions['B'].width = 52
        ws.protection.sheet    = True
        ws.protection.password = 'planillacr2026'

    # ══════════════════════════════════════════════════════════════════════════
    # ACCIÓN PRINCIPAL
    # ══════════════════════════════════════════════════════════════════════════
    def action_generate(self):
        self.ensure_one()

        wb = Workbook()
        s = self.include_sample_data

        # Instrucciones siempre presentes
        self._build_instructions(wb)

        if self.include_employees:
            self._build_employees(wb, sample=s)
        if self.include_loans:
            self._build_loans(wb, sample=s)
        if self.include_pension:
            self._build_pension(wb, sample=s)
        if self.include_benefits:
            self._build_benefits(wb, sample=s)
        if self.include_disabilities:
            self._build_disabilities(wb, sample=s)
        if self.include_vacations:
            self._build_vacations(wb, sample=s)
        if self.include_overtime:
            self._build_overtime(wb, sample=s)

        self._build_catalogs(wb)

        # Serializar a bytes
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        file_data = base64.b64encode(buf.read())

        # Guardar como attachment y devolver descarga
        company_slug = self.company_id.name.replace(' ', '_')[:20]
        filename     = f'Machote_Planilla_{company_slug}_v54.xlsx'

        att = self.env['ir.attachment'].create({
            'name':     filename,
            'type':     'binary',
            'datas':    file_data,
            'res_model': self._name,
            'res_id':    self.id,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })

        return {
            'type':   'ir.actions.act_url',
            'url':    f'/web/content/{att.id}?download=true',
            'target': 'self',
        }
