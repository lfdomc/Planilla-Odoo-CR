from odoo import models, fields, api
import base64, io

# ── openpyxl ─────────────────────────────────────────────────────────────────
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.comments import Comment
from openpyxl.worksheet.datavalidation import DataValidation


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
    include_embargos     = fields.Boolean('⚖️  Embargos Judiciales',        default=True)
    include_bonos        = fields.Boolean('🎯  Bonos e Incentivos',         default=True)
    include_sample_data  = fields.Boolean(
        '🧪  Incluir fila de prueba (EMPLEADO PRUEBA)',
        default=False,
        help='Agrega una fila naranja de prueba en todas las hojas con cédula 1-0000-0001. '
             'Active solo cuando quiera verificar que la importación funciona correctamente. '
             'Luego use el botón "Eliminar Empleado de Prueba" para limpiar.'
    )

    # Cédula reservada para la fila de prueba — misma en template y en import wizard
    _SAMPLE_CEDULA = '1-0000-0001'

    # ── Listas de valores para dropdowns Excel ────────────────────────────────
    # Orden importa: cada key ocupa una columna en la hoja oculta _LISTAS
    _DV_LISTS = {
        # Identificación
        'id_type':      ['Cédula Nacional', 'Residencia / DIMEX',
                         'Permiso de Trabajo', 'Pasaporte', 'Indocumentado'],
        # INS
        'ins_risk':     ['I - Oficinas', 'II - Comercio', 'III - Industria',
                         'IV - Construcción', 'V - Alto Riesgo'],
        'ins_workday':  ['Ordinaria', 'Extraordinaria', 'Mixta',
                         'Tiempo Parcial', 'Por Horas', 'Ocasional'],
        'ins_civil':    ['Soltero/a', 'Casado/a', 'Divorciado/a',
                         'Viudo/a', 'Unión Libre', 'Separado/a'],
        'ins_nat':      ['Costarricense', 'Nicaragüense', 'Colombiano/a',
                         'Estadounidense', 'Hondureño/a', 'Salvadoreño/a',
                         'Guatemalteco/a', 'Panameño/a', 'Mexicano/a',
                         'Venezolano/a', 'Peruano/a', 'Ecuatoriano/a', 'Otra'],
        # Banco y cuenta
        'banco':        ['BNCR', 'BCR', 'BP', 'BAC', 'BCT', 'CATHAY', 'CMB',
                         'DAVIVIENDA', 'GENERAL', 'IMPROSA', 'LAFISE',
                         'PROMERICA', 'PRIVAL', 'SCOTIA', 'COOCIQUE',
                         'COOPENAE', 'MUTUAL_ALJ', 'Otro'],
        'account_type': ['Cuenta Corriente', 'Cuenta de Ahorros', 'SINPE Móvil'],
        # Nómina
        'frequency':    ['Mensual', 'Quincenal', 'Semanal', 'Bimensual'],
        'calc_method':  ['Salario Fijo', 'Por Horas Trabajadas'],
        # Género y si/no
        'gender':       ['Masculino', 'Femenino', 'Otro'],
        'si_no':        ['Si', 'No'],
        # Préstamos
        'loan_type':    ['Préstamo de Empresa', 'Adelanto de Salario'],
        'loan_state':   ['Aprobado', 'En Curso', 'Borrador', 'Pagado', 'Anulado'],
        # Pensión
        'pension_rel':  ['Hijo/a', 'Cónyuge', 'Padre', 'Madre', 'Otro'],
        'pension_calc': ['Porcentaje del Salario', 'Monto Fijo'],
        # Beneficios
        'benefit_type': ['Beneficio / Ingreso', 'Deducción / Descuento'],
        'amount_type':  ['Monto Fijo', 'Porcentaje'],
        # Incapacidades
        'disability':   ['Enfermedad Común (CCSS)', 'Accidente de Trabajo (CCSS)',
                         'Riesgo Laboral (INS)', 'Maternidad / Paternidad', 'Otro'],
        # Horas extras
        'overtime_type':['Simple (1.5x)', 'Doble (2.0x)', 'Día Feriado'],
        # Embargos
        'embargo_calc': ['Monto Fijo', 'Porcentaje del Neto Disponible'],
        # Bonos
        'bono_type':    ['Productividad / Rendimiento', 'Asistencia Perfecta',
                         'Antigüedad por Años de Servicio', 'Subsidio de Transporte / Kilometraje',
                         'Subsidio de Alimentación (en dinero)', 'Subsidio Educativo',
                         'Subsidio de Salud / Médico', 'Gastos de Representación',
                         'Comisión por Ventas', 'Incentivo / Premio Especial', 'Otro'],
        'bono_calc':    ['Monto Fijo', 'Porcentaje del Salario Base'],
        'si_no_recurrente': ['Si', 'No'],
    }

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

    # ── catálogos para dropdowns ──────────────────────────────────────────────
    # Orden de columnas en la hoja oculta _LISTAS (A, B, C, ...)
    # Clave → (col_idx_0based, [valores])
    _LISTAS = {
        'id_type':          (0,  ['01','02','03','04','05']),
        'si_no':            (1,  ['si','no']),
        'ins_risk':         (2,  ['I','II','III','IV','V']),
        'ins_workday':      (3,  ['01','02','03','04','05','06']),
        'banco':            (4,  ['BNCR','BCR','BP','BAC','BCT','CATHAY','CMB',
                                  'DAVIVIENDA','GENERAL','IMPROSA','LAFISE',
                                  'PROMERICA','PRIVAL','SCOTIA','COOCIQUE',
                                  'COOPENAE','MUTUAL_ALJ','OTRO']),
        'account_type':     (5,  ['corriente','ahorros','sinpe']),
        'frequency':        (6,  ['monthly','biweekly','weekly','bimonthly']),
        'calc_method':      (7,  ['fixed','attendance']),
        'ins_nationality':  (8,  ['CR','NI','CO','US','HN','SV','GT','PA',
                                  'MX','VE','PE','EC','OT']),
        'ins_civil':        (9,  ['01','02','03','04','05','06']),
        'gender':           (10, ['masculino','femenino','otro']),
        'loan_type':        (11, ['loan','advance']),
        'loan_state':       (12, ['approved','active','draft','paid','cancelled']),
        'pension_relacion': (13, ['hijo','conyuge','padre','madre','otro']),
        'pension_calc':     (14, ['porcentaje','monto_fijo']),
        'benefit_type':     (15, ['beneficio','deduccion']),
        'amount_type':      (16, ['fijo','porcentaje']),
        'disability_type':  (17, ['ccss','ccss_accident','ins','maternity','other']),
        'overtime_type':    (18, ['simple','double','holiday']),
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

    # ── filas de datos (vacías + ejemplo + prueba) ─────────────────────────────
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

    def _sample(self, cell, value):
        """Estilo para la fila de prueba: fondo naranja, texto oscuro, itálica."""
        cell.value     = value
        cell.fill      = self._fill('F4B942')
        cell.border    = self._border()
        cell.alignment = self._left()
        cell.font      = self._font(italic=True, bold=True, size=9, color='7B2D00')

    def _build_listas_sheet(self, wb):
        """Crea hoja oculta con todas las listas para DataValidation."""
        ws = wb.create_sheet('_LISTAS')
        for col_idx, (key, vals) in enumerate(self._DV_LISTS.items(), 1):
            for row_idx, val in enumerate(vals, 1):
                ws.cell(row_idx, col_idx, value=val)
        ws.sheet_state = 'hidden'
        return ws

    def _dv(self, ws, col_idx, list_key, first_data_row, last_data_row=500,
            title='Opciones'):
        """Helper rápido que busca la lista en _DV_LISTS y aplica el dropdown."""
        vals = self._DV_LISTS.get(list_key, [])
        if not vals:
            return
        keys = list(self._DV_LISTS.keys())
        listas_col = get_column_letter(keys.index(list_key) + 1)
        last_r     = len(vals)
        formula    = f"'_LISTAS'!${listas_col}$1:${listas_col}${last_r}"
        col_letter = get_column_letter(col_idx)
        sqref      = f'{col_letter}{first_data_row}:{col_letter}{last_data_row}'

        dv = DataValidation(
            type='list',
            formula1=formula,
            allow_blank=True,
            showDropDown=False,
            showErrorMessage=True,
            errorStyle='warning',
            errorTitle='Valor no reconocido',
            error='El valor ingresado no está en el catálogo. Revise la hoja CATALOGOS.',
            showInputMessage=True,
            promptTitle=title,
            prompt=f'Seleccione: {", ".join(vals[:6])}{"…" if len(vals)>6 else ""}',
        )
        ws.add_data_validation(dv)
        dv.sqref = sqref

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
            ('', '➕  OTROS DESCUENTOS      → Cuota sindical, cooperativa, ahorro voluntario, seguro médico (no embargos ni bonos formales)'),
            ('', '🏥  INCAPACIDADES        → Incapacidades activas al momento de la carga'),
            ('', '🏖️  VACACIONES           → Saldo de vacaciones acumulado'),
            ('', '⏱️  HORAS EXTRAS         → Horas extras históricas'),
            ('', '⚖️  EMBARGOS             → Embargos judiciales (Art. 172 CT — máx. 25% neto)'),
            ('', '🎯  BONOS                → Bonos e incentivos (productividad, transporte, etc.)'),
            ('', '📚  CATALOGOS            → Valores válidos para campos de lista (NO editar)'),
            ('', ''),
            ('INSTRUCCIONES', ''),
            ('', '1. Complete la hoja EMPLEADOS — un empleado por fila.'),
            ('', '2. Use la cédula como llave: debe coincidir exactamente en todas las hojas.'),
            ('', '3. Para préstamos, pensiones o beneficios múltiples: agregue una fila por cada uno.'),
            ('', '4. Los campos de selección tienen menú desplegable — haga clic en la celda y elija de la lista.'),
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
            ('Tipo de Identificación',    True,  18, 'Cédula Nacional',             'Ver CATALOGOS → id_type  (01 Cédula / 02 DIMEX / 03 Permiso / 04 Pasaporte)'),
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
            ('Clase de Riesgo INS',       True,  16, 'I - Oficinas',                'Ver CATALOGOS → ins_risk_class  (I / II / III / IV / V)'),
            ('Jornada INS',               True,  18, 'Ordinaria',                   'Ver CATALOGOS → ins_workday_type  (01 Ordinaria / 02 Extraordinaria / 03 Mixta / 04 Tiempo Parcial / 05 Por Horas / 06 Ocasional)'),
            # CCSS y banco (cols 21-26)
            ('Número CCSS',               False, 16, '123456789',                'Número de asegurado CCSS'),
            ('Asegurado CCSS',            True,  14, 'si',                       'si / no'),
            ('Cuenta Bancaria / IBAN',    False, 30, 'CR21015108010018023571',   'IBAN de 22 caracteres'),
            ('SINPE Móvil',               False, 14, '88887777',                 'Teléfono registrado en SINPE Móvil'),
            ('Banco',                     False, 20, 'BNCR',                        'Ver CATALOGOS → bank'),
            ('Tipo de Cuenta Banco',      False, 16, 'Cuenta Corriente',            'Ver CATALOGOS → account_type'),
            # Configuración nómina (cols 27-32)
            ('Salario Base (₡)',          True,  18, '750000',                   'Salario mensual en colones, sin comas ni símbolo'),
            ('Fecha Vigencia Salarial',   False, 18, '01/01/2026',               'Desde cuándo aplica el salario'),
            ('Frecuencia de Pago',        True,  18, 'Mensual',                     'Ver CATALOGOS → frequency'),
            ('Método de Cálculo',         True,  18, 'Salario Fijo',                'Ver CATALOGOS → calc_method'),
            ('Ocupación INS',             True,  20, '4110',                     'Código numérico INS — ver CATALOGOS → ins_occupation'),
            ('Nacionalidad INS',          False, 14, 'Costarricense',               'Ver CATALOGOS → ins_nationality  (CR / NI / CO / US / OT…)'),
            # Datos personales (cols 33-38)
            ('Estado Civil INS',          False, 16, 'Soltero/a',                   'Ver CATALOGOS → ins_civil_status  (01 Soltero/a / 02 Casado/a / 03 Divorciado/a / 04 Viudo/a / 05 Unión Libre / 06 Separado/a)'),
            ('Género',                    False, 12, 'Masculino',                   'Masculino / Femenino / Otro'),
            ('Número de Dependientes',    False, 12, '0',                        'Hijos u otros dependientes'),
            ('Dirección',                 False, 30, 'San José, Escazú',         'Dirección de habitación'),
            ('Teléfono Personal',         False, 14, '88887777',                 ''),
            ('Observaciones',             False, 30, '',                         'Notas internas del empleado'),
        ]

        sv = None
        if sample:
            sv = [
                'EMPLEADO PRUEBA', self._SAMPLE_CEDULA, 'Cédula Nacional', '01/01/2024', '', 'prueba@empresa.com',
                '', '', '', 'Puesto Prueba', 'planilla', 'activo', 'jornada_ordinaria',
                'Si', '', 'Prueba', 'Prueba', 'Prueba', 'I - Oficinas', 'Ordinaria',
                '', 'Si', '', '', 'BNCR', 'Cuenta Corriente',
                '500000', '01/01/2024', 'Mensual', 'Salario Fijo', '4110', 'Costarricense',
                'Soltero/a', 'Masculino', '0', 'San José', '88880000',
                '⚠️ FILA DE PRUEBA — eliminar después de verificar importación',
            ]
        self._build_rows(ws, cols, data_rows=100, header_row=3, example_row=4,
                         sample_values=sv)
        # ── Dropdowns en filas de datos (5 en adelante) ──────────────────
        # col  3: Tipo de Identificación
        self._dv(ws,  3, 'id_type',      5, title='Tipo de Identificación')
        # col 14: Incluir en INS
        self._dv(ws, 14, 'si_no',        5, title='Incluir en INS (si/no)')
        # col 19: Clase de Riesgo INS
        self._dv(ws, 19, 'ins_risk',     5, title='Clase de Riesgo INS')
        # col 20: Jornada INS
        self._dv(ws, 20, 'ins_workday',  5, title='Tipo de Jornada INS')
        # col 22: Asegurado CCSS
        self._dv(ws, 22, 'si_no',        5, title='Asegurado CCSS (si/no)')
        # col 25: Banco
        self._dv(ws, 25, 'banco',        5, title='Banco')
        # col 26: Tipo de Cuenta
        self._dv(ws, 26, 'account_type', 5, title='Tipo de Cuenta Banco')
        # col 29: Frecuencia de Pago
        self._dv(ws, 29, 'frequency',    5, title='Frecuencia de Pago')
        # col 30: Método de Cálculo
        self._dv(ws, 30, 'calc_method',  5, title='Método de Cálculo')
        # col 32: Nacionalidad INS
        self._dv(ws, 32, 'ins_nat',      5, title='Nacionalidad INS')
        # col 33: Estado Civil INS
        self._dv(ws, 33, 'ins_civil',    5, title='Estado Civil INS')
        # col 34: Género
        self._dv(ws, 34, 'gender',       5, title='Género')

    # ══════════════════════════════════════════════════════════════════════════
    # HOJA PRÉSTAMOS
    # ══════════════════════════════════════════════════════════════════════════
    def _build_loans(self, wb, sample=False):
        cols = [
            ('Cédula Empleado',          True,  18, '1-2345-6789',     'Debe coincidir con hoja EMPLEADOS'),
            ('Tipo de Préstamo',         True,  16, 'Préstamo de Empresa', 'Préstamo de Empresa / Adelanto de Salario'),
            ('Descripción / Motivo',     False, 30, 'Préstamo personal',''),
            ('Monto Total (₡)',          True,  16, '500000',          'Total del préstamo, sin comas'),
            ('Número de Cuotas',         True,  14, '10',              'Cantidad de cuotas a descontar'),
            ('Fecha de Otorgamiento',    True,  18, '15/01/2026',      'DD/MM/AAAA'),
            ('Fecha Primera Deducción',  True,  18, '01/02/2026',      'DD/MM/AAAA — primer boleta que descuenta'),
            ('Estado',                   True,  14, 'Aprobado',            'Ver CATALOGOS → loan_state'),
            ('Monto ya Pagado (₡)',      False, 16, '100000',          'Si ya se ha descontado algo'),
            ('Observaciones',            False, 28, '',                ''),
        ]
        sv = [self._SAMPLE_CEDULA, 'Préstamo de Empresa', 'Préstamo de prueba', '100000', '5',
              '01/01/2024', '01/02/2024', 'Aprobado', '0', '⚠️ PRUEBA'] if sample else None
        ws = wb.create_sheet('💰 PRESTAMOS')
        self._sheet_title(ws, 'PRÉSTAMOS Y ADELANTOS — Un préstamo por fila (puede haber varios por empleado)', len(cols))
        self._build_rows(ws, cols, data_rows=100, sample_values=sv)
        # col 2: Tipo de Préstamo, col 8: Estado
        self._dv(ws, 2, 'loan_type',   4, title='Tipo de Préstamo')
        self._dv(ws, 8, 'loan_state',  4, title='Estado del Préstamo')

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
            ('Relación Beneficiario',  True,  16, 'Hijo/a',                  'Ver CATALOGOS → pension_relacion'),
            ('Cuenta Beneficiario',    False, 28, 'CR21015108010018023571','IBAN del beneficiario (opcional)'),
            ('Tipo de Cálculo',        True,  16, 'Porcentaje del Salario', 'Porcentaje del Salario / Monto Fijo'),
            ('Porcentaje (%)',          False, 12, '25',                   'Si tipo=porcentaje, solo el número (ej: 25)'),
            ('Monto Fijo (₡)',         False, 14, '',                     'Si tipo=monto_fijo, monto en colones'),
            ('Fecha de Inicio',        True,  14, '01/07/2023',            'DD/MM/AAAA'),
            ('Fecha de Fin',           False, 14, '',                     'Dejar vacío si no tiene vencimiento'),
        ]
        sv = [self._SAMPLE_CEDULA, 'TEST-0000-PRUEBA', 'Juzgado Prueba', '01/01/2024',
              'Beneficiario Prueba', 'Hijo/a', '', 'Porcentaje del Salario', '10', '',
              '01/01/2024', ''] if sample else None
        ws = wb.create_sheet('👨‍👧 PENSION_ALIMENTARIA')
        self._sheet_title(ws, 'PENSIONES ALIMENTARIAS — Una resolución por fila', len(cols))
        self._build_rows(ws, cols, sample_values=sv)
        # col 6: Relación Beneficiario, col 8: Tipo de Cálculo
        self._dv(ws, 6, 'pension_rel',  4, title='Relación Beneficiario')
        self._dv(ws, 8, 'pension_calc', 4, title='Tipo de Cálculo')

    # ══════════════════════════════════════════════════════════════════════════
    # HOJA BENEFICIOS RECURRENTES
    # ══════════════════════════════════════════════════════════════════════════
    def _build_benefits(self, wb, sample=False):
        cols = [
            ('Cédula Empleado',   True,  18, '1-2345-6789',        'Cédula del empleado'),
            ('Concepto',          True,  28, 'Cuota Sindical',      'Nombre descriptivo del descuento o deducción'),
            ('Tipo',              True,  14, 'Deducción / Descuento','Deducción / Descuento   o   Beneficio / Ingreso'),
            ('Tipo de Monto',     True,  16, 'Monto Fijo',           'Monto Fijo / Porcentaje'),
            ('Monto (₡)',         False, 14, '15000',               'Si tipo_monto=fijo'),
            ('Porcentaje (%)',    False, 12, '',                    'Si tipo_monto=porcentaje, solo el número'),
            ('Código Deducción',  False, 16, '',                    'Código del concepto si el módulo lo requiere'),
            ('Vigente Desde',     True,  14, '01/01/2026',          'DD/MM/AAAA'),
            ('Vigente Hasta',     False, 14, '',                    'Dejar vacío si es indefinido'),
            ('Nota',              False, 28, 'Acuerdo colectivo 2026','Descripción o referencia'),
        ]
        sv = [self._SAMPLE_CEDULA, 'Cuota Sindical Prueba', 'Deducción / Descuento', 'Monto Fijo',
              '2000', '', '', '01/01/2024', '', '⚠️ PRUEBA'] if sample else None
        ws = wb.create_sheet('➕ OTROS DESCUENTOS')
        self._sheet_title(ws, 'OTROS DESCUENTOS / DEDUCCIONES RECURRENTES — Cuota sindical, cooperativa, ahorro voluntario, seguro médico, etc.', len(cols))
        self._build_rows(ws, cols, data_rows=100, sample_values=sv)
        # col 3: Tipo, col 4: Tipo de Monto
        self._dv(ws, 3, 'benefit_type', 4, title='Tipo (beneficio/deduccion)')
        self._dv(ws, 4, 'amount_type',  4, title='Tipo de Monto')

    def _build_disabilities(self, wb, sample=False):
        cols = [
            ('Cédula Empleado',      True,  18, '1-2345-6789',    ''),
            ('Tipo de Incapacidad',  True,  22, 'Enfermedad Común (CCSS)', 'Ver CATALOGOS → disability_type'),
            ('Fecha Inicio',         True,  14, '01/02/2026',     'DD/MM/AAAA'),
            ('Fecha Fin',            True,  14, '10/02/2026',     'DD/MM/AAAA'),
            ('% Subsidiado CCSS',    False, 14, '60',             'Porcentaje que paga la CCSS'),
            ('% a Cargo Patrono',    False, 14, '40',             'Porcentaje que asume el patrono'),
            ('Número Certificado',   False, 20, 'CCSS-2026-123',  'Número del certificado CCSS'),
            ('Diagnóstico',          False, 28, 'Gripa severa',   'Descripción del diagnóstico'),
            ('Salario Diario (₡)',   False, 16, '25000',          'Salario mensual ÷ 30'),
            ('Observaciones',        False, 28, '',               ''),
        ]
        sv = [self._SAMPLE_CEDULA, 'Enfermedad Común (CCSS)', '01/01/2024', '05/01/2024',
              '60', '40', 'PRUEBA-0000', 'Diagnóstico prueba', '16667',
              '⚠️ PRUEBA'] if sample else None
        ws = wb.create_sheet('🏥 INCAPACIDADES')
        self._sheet_title(ws, 'INCAPACIDADES — Solo las activas o dentro del período de carga', len(cols))
        self._build_rows(ws, cols, sample_values=sv)
        # col 2: Tipo de Incapacidad
        self._dv(ws, 2, 'disability', 4, title='Tipo de Incapacidad')

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
            ('Tipo de Hora Extra',   True,  20, 'Simple (1.5x)',        'Ver CATALOGOS → overtime_type'),
            ('Cantidad de Horas',    True,  16, '2.5',             'Número de horas extras trabajadas'),
            ('Salario por Hora (₡)', False, 18, '3500',            'Salario mensual ÷ 240 (o según contrato)'),
            ('Monto Total (₡)',      False, 16, '8750',            'Horas × Salario × Factor (1.5 / 2.0)'),
            ('Período de Planilla',  False, 22, 'Febrero 2026',    'Período al que se carga esta hora extra'),
            ('Observaciones',        False, 28, '',                 ''),
        ]
        sv = [self._SAMPLE_CEDULA, '15/01/2024', 'Simple (1.5x)', '2',
              '2083', '6250', 'Enero 2024', '⚠️ PRUEBA'] if sample else None
        ws = wb.create_sheet('⏱️ HORAS EXTRAS')
        self._sheet_title(ws, 'HORAS EXTRAS — Registros históricos a importar', len(cols))
        self._build_rows(ws, cols, sample_values=sv)
        # col 3: Tipo de Hora Extra
        self._dv(ws, 3, 'overtime_type', 4, title='Tipo de Hora Extra')

    # ══════════════════════════════════════════════════════════════════════════
    # HOJA OCULTA DE LISTAS (fuente de los dropdowns)
    # ══════════════════════════════════════════════════════════════════════════
    # ══════════════════════════════════════════════════════════════════════════
    # HOJA EMBARGOS JUDICIALES
    # ══════════════════════════════════════════════════════════════════════════
    def _build_embargos(self, wb, sample=False):
        cols = [
            ('Cédula Empleado',        True,  18, '1-2345-6789',          'Cédula del empleado afectado'),
            ('N° Expediente Judicial', True,  24, '15-000456-0638-CI',    'Número del expediente del juzgado'),
            ('Juzgado / Tribunal',     True,  30, 'Juzgado Civil SJ',     'Nombre completo del juzgado'),
            ('Fecha de Resolución',    False, 16, '15/01/2024',           'DD/MM/AAAA'),
            ('Nombre del Acreedor',    True,  28, 'Empresa XYZ S.A.',     'Nombre del beneficiario del embargo'),
            ('IBAN del Acreedor',      False, 30, 'CR21015108010018023571','IBAN para girar el embargo (opcional)'),
            ('Tipo de Cálculo',        True,  22, 'Monto Fijo',           'Ver CATALOGOS → embargo_calc'),
            ('Monto Fijo (₡)',         False, 16, '50000',                'Si tipo = Monto Fijo'),
            ('Porcentaje (%)',          False, 12, '',                     'Si tipo = Porcentaje. Máx 25% (Art. 172 CT)'),
            ('Vigente Desde',          True,  14, '01/02/2024',           'DD/MM/AAAA'),
            ('Vigente Hasta',          False, 14, '',                     'Dejar vacío si no tiene vencimiento'),
            ('Observaciones',          False, 28, '',                     ''),
        ]
        sv = [self._SAMPLE_CEDULA, 'TEST-EMB-0000', 'Juzgado Prueba', '01/01/2024',
              'Acreedor Prueba', '', 'Monto Fijo', '10000', '',
              '01/01/2024', '', '⚠️ PRUEBA'] if sample else None
        ws = wb.create_sheet('⚖️ EMBARGOS')
        self._sheet_title(ws, 'EMBARGOS JUDICIALES — Art. 172 CT (máx. 25% del neto disponible)', len(cols))
        self._build_rows(ws, cols, data_rows=80, sample_values=sv)
        self._dv(ws, 7, 'embargo_calc', 4, title='Tipo de Cálculo')

    # ══════════════════════════════════════════════════════════════════════════
    # HOJA BONOS E INCENTIVOS
    # ══════════════════════════════════════════════════════════════════════════
    def _build_bonos(self, wb, sample=False):
        cols = [
            ('Cédula Empleado',        True,  18, '1-2345-6789',                    'Cédula del empleado'),
            ('Concepto / Nombre',      True,  28, 'Bono de Productividad Q1 2024',  'Nombre descriptivo del bono'),
            ('Tipo de Bono',           True,  28, 'Productividad / Rendimiento',    'Ver CATALOGOS → bono_type'),
            ('Tipo de Cálculo',        True,  18, 'Monto Fijo',                     'Monto Fijo / Porcentaje del Salario Base'),
            ('Monto Fijo (₡)',         False, 16, '25000',                          'Si tipo cálculo = Monto Fijo'),
            ('Porcentaje (%)',          False, 12, '',                               'Si tipo cálculo = Porcentaje'),
            ('Es Recurrente',          True,  14, 'Si',                             'Si = se aplica cada boleta / No = solo una vez'),
            ('Afecto CCSS',            True,  12, 'Si',                             'Si = suma a base CCSS (bonos salariales)'),
            ('Afecto Renta',           True,  12, 'Si',                             'Si = suma a base de renta'),
            ('Tope Exento (₡/mes)',    False, 16, '',                               'Solo para transporte (₡74 000/mes) o similar'),
            ('Vigente Desde',          True,  14, '01/01/2024',                     'DD/MM/AAAA'),
            ('Vigente Hasta',          False, 14, '',                               'Dejar vacío para aplicar indefinidamente'),
            ('Observaciones',          False, 30, 'Acuerdo de junta 2024',          ''),
        ]
        sv = [self._SAMPLE_CEDULA, 'Bono Prueba', 'Productividad / Rendimiento',
              'Monto Fijo', '5000', '', 'Si', 'Si', 'Si', '',
              '01/01/2024', '', '⚠️ PRUEBA'] if sample else None
        ws = wb.create_sheet('🎯 BONOS')
        self._sheet_title(ws, 'BONOS E INCENTIVOS — Aplican automáticamente en cada boleta', len(cols))
        self._build_rows(ws, cols, data_rows=100, sample_values=sv)
        self._dv(ws, 3, 'bono_type',        4, title='Tipo de Bono')
        self._dv(ws, 4, 'bono_calc',        4, title='Tipo de Cálculo')
        self._dv(ws, 7, 'si_no_recurrente', 4, title='¿Es Recurrente?')
        self._dv(ws, 8, 'si_no',            4, title='¿Afecto CCSS?')
        self._dv(ws, 9, 'si_no',            4, title='¿Afecto Renta?')

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
                ('Mensual',    'Mensual — 1 pago al mes'),
                ('Quincenal',  'Quincenal — 2 pagos al mes'),
                ('Semanal',    'Semanal — 4 pagos al mes'),
                ('Bimensual',  'Bimensual — cada 2 meses'),
            ]),
            ('ins_risk_class — Clase de Riesgo INS', [
                ('I',   'Clase I   — Oficinas y administrativo (~0.87%)'),
                ('II',  'Clase II  — Comercio (~1.49%)'),
                ('III', 'Clase III — Industria liviana (~2.47%)'),
                ('IV',  'Clase IV  — Construcción / riesgo alto (~4.13%)'),
                ('V',   'Clase V   — Actividades de alto riesgo (~6.88%)'),
            ]),
            ('ins_workday_type — Tipo de Jornada INS', [
                ('Ordinaria',      'Jornada diurna regular'),
                ('Extraordinaria', 'Horas extra autorizadas'),
                ('Mixta',          'Parte diurna y parte nocturna'),
                ('Tiempo Parcial', 'Menos de jornada completa'),
                ('Por Horas',      'Según horas efectivamente trabajadas'),
                ('Ocasional',      'Trabajo esporádico o temporal'),
            ]),
            ('ins_id_type — Tipo de ID INS', [
                ('01', 'Cédula de Costa Rica'),
                ('02', 'Residencia de Costa Rica / DIMEX'),
                ('03', 'Permiso de Trabajo'),
                ('04', 'Pasaporte'),
                ('05', 'Indocumentado'),
            ]),
            ('ins_civil_status — Estado Civil INS', [
                ('Soltero/a',    ''),
                ('Casado/a',     ''),
                ('Divorciado/a', ''),
                ('Viudo/a',      ''),
                ('Unión Libre',  ''),
                ('Separado/a',   ''),
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
                ('Cuenta Corriente', 'Cuenta corriente o IBAN'),
                ('Cuenta de Ahorros','Cuenta de ahorros'),
                ('SINPE Móvil',      'SINPE Móvil'),
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
                ('Préstamo de Empresa', 'Préstamo otorgado por la empresa'),
                ('Adelanto de Salario', 'Adelanto sobre el salario del período'),
            ]),
            ('loan_state — Estado del Préstamo', [
                ('Aprobado', 'Se activará en la próxima boleta'),
                ('En Curso', 'Descuento activo'),
                ('Borrador', 'Pendiente de aprobación'),
                ('Pagado',   'Totalmente cancelado'),
                ('Anulado',  'Préstamo anulado'),
            ]),
            ('pension_relacion — Relación Beneficiario', [
                ('Hijo/a',   ''),
                ('Cónyuge',  'Cónyuge / Conviviente'),
                ('Padre',    ''),
                ('Madre',    ''),
                ('Otro',     ''),
            ]),
            ('pension_calc — Tipo de Cálculo Pensión', [
                ('Porcentaje del Salario', 'Porcentaje del salario bruto'),
                ('Monto Fijo',             'Monto fijo mensual en colones'),
            ]),
            ('benefit_type — Tipo de Descuento/Deducción Recurrente', [
                ('Beneficio / Ingreso',    'Suma al salario bruto (ej: plus informal no cubierto por BONOS)'),
                ('Deducción / Descuento',  'Resta del salario neto (ej: cuota sindical, cooperativa, ahorro)'),
            ]),
            ('amount_type — Tipo de Monto', [
                ('Monto Fijo',  'Monto fijo en colones'),
                ('Porcentaje',  'Porcentaje del salario base'),
            ]),
            ('disability_type — Tipo de Incapacidad', [
                ('Enfermedad Común (CCSS)',     'Enfermedad o accidente no laboral'),
                ('Accidente de Trabajo (CCSS)', 'Accidente en el lugar de trabajo'),
                ('Riesgo Laboral (INS)',         'Cubierto por póliza INS'),
                ('Maternidad / Paternidad',      'Licencia pre/post natal'),
                ('Otro',                         'Otro tipo de incapacidad'),
            ]),
            ('overtime_type — Tipo de Hora Extra', [
                ('Simple (1.5x)', 'Hora extra ordinaria — factor 1.5'),
                ('Doble (2.0x)',  'Hora extra nocturna o dominical — factor 2.0'),
                ('Día Feriado',   'Trabajo en día feriado nacional'),
            ]),
            ('embargo_calc — Tipo de Cálculo Embargo', [
                ('Monto Fijo',                  'Monto fijo en colones (₡) cada período'),
                ('Porcentaje del Neto Disponible', 'Porcentaje del neto (bruto − CCSS − renta − pensiones). Máx 25% Art. 172 CT'),
            ]),
            ('bono_type — Tipo de Bono', [
                ('Productividad / Rendimiento',          'Afecto CCSS y Renta — integra salario para aguinaldo/cesantía'),
                ('Asistencia Perfecta',                   'Afecto CCSS y Renta — integra salario para aguinaldo/cesantía'),
                ('Antigüedad por Años de Servicio',       'Afecto CCSS y Renta — integra salario para aguinaldo/cesantía'),
                ('Subsidio de Transporte / Kilometraje',  'Exento CCSS/Renta hasta ₡74 000/mes (Reglamento 2023)'),
                ('Subsidio de Alimentación (en dinero)',  'Afecto CCSS y Renta si se paga en dinero'),
                ('Subsidio Educativo',                    'Generalmente exento según convenio colectivo'),
                ('Subsidio de Salud / Médico',            'Exento CCSS (Art. 5 Ley 7983) si es póliza médica'),
                ('Gastos de Representación',              'Exento CCSS si están debidamente documentados'),
                ('Comisión por Ventas',                   'Afecto CCSS y Renta — integra salario'),
                ('Incentivo / Premio Especial',           'Afecto CCSS y Renta'),
                ('Otro',                                  'Consulte con su contador el tratamiento fiscal'),
            ]),
            ('calc_method — Método de Cálculo de Planilla', [
                ('Salario Fijo',         'Sin consultar asistencias'),
                ('Por Horas Trabajadas', 'Según módulo de asistencias'),
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

        # Hoja oculta de listas — debe crearse ANTES de las demás hojas
        # para que las referencias de DataValidation sean válidas
        self._build_listas_sheet(wb)

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
        if self.include_embargos:
            self._build_embargos(wb, sample=s)
        if self.include_bonos:
            self._build_bonos(wb, sample=s)

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
