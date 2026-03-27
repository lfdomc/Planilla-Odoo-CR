"""
planilla.import.data.wizard
Importacion masiva desde el machote Excel generado por import_template_wizard.
Estrategia: si el empleado ya existe (por identification_id) -> se salta.
Al finalizar: resumen en pantalla + Excel de errores descargable.
"""
from odoo import models, fields, api
from ..models import planilla_const as K
from odoo.exceptions import UserError
import base64, io, logging, re, traceback
from datetime import date, datetime

_logger = logging.getLogger(__name__)

try:
    from openpyxl import load_workbook, Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    OPENPYXL_OK = True
except ImportError:
    OPENPYXL_OK = False

# ==============================================================================
# TABLAS DE TRADUCCION  (valor amigable del Excel -> valor tecnico del modelo)
# ==============================================================================

INS_RISK = {
    # Valor corto (solo el numero romano)
    'i': 'I', 'ii': 'II', 'iii': 'III', 'iv': 'IV', 'v': 'V',
    'I': 'I', 'II': 'II', 'III': 'III', 'IV': 'IV', 'V': 'V',
    '1': 'I', '2': 'II', '3': 'III', '4': 'IV', '5': 'V',
    # Valor largo con descripcion (formato del machote: "I - Oficinas")
    'i - oficinas': 'I', 'i - riesgo minimo': 'I', 'i - minimo': 'I',
    'ii - comercio': 'II', 'ii - riesgo bajo': 'II', 'ii - bajo': 'II',
    'ii - comercio general': 'II', 'ii - servicios': 'II',
    'iii - manufactura': 'III', 'iii - riesgo medio': 'III', 'iii - medio': 'III',
    'iii - manufactura ligera': 'III', 'iii - transporte': 'III',
    'iv - construccion': 'IV', 'iv - construccion': 'IV',
    'iv - riesgo alto': 'IV', 'iv - alto': 'IV', 'iv - industria': 'IV',
    'v - mineria': 'V', 'v - mineria': 'V',
    'v - riesgo maximo': 'V', 'v - riesgo maximo': 'V', 'v - maximo': 'V',
    'v - explosivos': 'V', 'v - pesca': 'V',
}

INS_WORKDAY = {
    # Valores del dropdown (espanol legible)
    'ordinaria': '01', 'diurna': '01', '01': '01',
    'extraordinaria': '02', '02': '02',
    'mixta': '03', '03': '03',
    'tiempo parcial': '04', 'medio tiempo': '04', '04': '04',
    'por horas': '05', '05': '05',
    'ocasional': '06', '06': '06',
}

INS_NATIONALITY = {
    'cr': 'CR', 'costarricense': 'CR', 'costa rica': 'CR',
    'ni': 'NI', 'nicaraguense': 'NI', 'nicaraguense': 'NI',
    'co': 'CO', 'colombiano': 'CO', 'colombiana': 'CO', 'colombiano/a': 'CO',
    'us': 'US', 'estadounidense': 'US', 'americano': 'US',
    'hn': 'HN', 'hondureno': 'HN', 'hondureno': 'HN', 'hondureno/a': 'HN',
    'sv': 'SV', 'salvadoreno': 'SV', 'salvadoreno': 'SV', 'salvadoreno/a': 'SV',
    'gt': 'GT', 'guatemalteco': 'GT', 'guatemalteca': 'GT', 'guatemalteco/a': 'GT',
    'pa': 'PA', 'panameno': 'PA', 'panameno': 'PA', 'panameno/a': 'PA',
    'mx': 'MX', 'mexicano': 'MX', 'mexicana': 'MX', 'mexicano/a': 'MX',
    've': 'VE', 'venezolano': 'VE', 'venezolana': 'VE', 'venezolano/a': 'VE',
    'pe': 'PE', 'peruano': 'PE', 'peruana': 'PE', 'peruano/a': 'PE',
    'ec': 'EC', 'ecuatoriano': 'EC', 'ecuatoriana': 'EC', 'ecuatoriano/a': 'EC',
    'ot': 'OT', 'otro': 'OT', 'otra': 'OT', 'other': 'OT', 'otra': 'OT',
}

ACCOUNT_TYPE = {
    'cuenta corriente': 'corriente', 'corriente': 'corriente', 'iban': 'corriente',
    'cuenta de ahorros': 'ahorros', 'ahorros': 'ahorros',
    'sinpe movil': 'sinpe', 'sinpe movil': 'sinpe', 'sinpe': 'sinpe',
}

GENDER = {
    'masculino': 'male', 'hombre': 'male', 'male': 'male', 'm': 'male',
    'femenino': 'female', 'mujer': 'female', 'female': 'female', 'f': 'female',
    'otro': 'other', 'other': 'other',
}

INS_CIVIL = {
    'soltero/a': '01', 'soltero': '01', 'soltera': '01', '01': '01',
    'casado/a': '02', 'casado': '02', 'casada': '02', '02': '02',
    'divorciado/a': '03', 'divorciado': '03', 'divorciada': '03', '03': '03',
    'viudo/a': '04', 'viudo': '04', 'viuda': '04', '04': '04',
    'union libre': '05', 'union libre': '05', '05': '05',
    'separado/a': '06', 'separado': '06', 'separada': '06', '06': '06',
}

INS_ID_TYPE = {
    # Mapeo texto del Excel -> code de planilla.identification.type en BD
    # Codigos segun data inicial: CI, DIMEX, PAS, CJ, NITE
    'cedula nacional': 'CI', 'cedula nacional': 'CI',
    'cedula de identidad': 'CI', 'cedula de identidad': 'CI',
    'cedula': 'CI', 'cedula': 'CI', '01': 'CI', 'ci': 'CI',
    'residencia / dimex': 'DIMEX', 'residencia': 'DIMEX',
    'dimex': 'DIMEX', '02': 'DIMEX',
    'permiso de trabajo': 'NITE', 'permiso': 'NITE',
    'nite': 'NITE', '03': 'NITE',
    'pasaporte': 'PAS', 'pas': 'PAS', '04': 'PAS',
    'cedula juridica': 'CJ', 'cedula juridica': 'CJ',
    'juridica': 'CJ', 'cj': 'CJ',
    'indocumentado': 'NITE', '05': 'NITE',
}

# Mapeo separado texto -> codigo INS (campo ins_id_type, numerico 2 digitos)
INS_ID_TYPE_CODE = {
    'cedula nacional': '01', 'cedula nacional': '01',
    'cedula de identidad': '01', 'cedula de identidad': '01',
    'cedula': '01', 'cedula': '01', '01': '01', 'ci': '01',
    'residencia / dimex': '02', 'residencia': '02', 'dimex': '02', '02': '02',
    'permiso de trabajo': '03', 'permiso': '03', 'nite': '03', '03': '03',
    'pasaporte': '04', 'pas': '04', '04': '04',
    'indocumentado': '05', '05': '05',
    'cedula juridica': '06', 'cedula juridica': '06', 'cj': '06',
}

DISABILITY_TYPE = {
    'enfermedad comun (ccss)': 'ccss', 'enfermedad comun (ccss)': 'ccss',
    'enfermedad': 'ccss', 'ccss': 'ccss',
    'accidente de trabajo (ccss)': 'ccss_accident',
    'accidente trabajo': 'ccss_accident', 'accidente_trabajo': 'ccss_accident',
    'riesgo laboral (ins)': 'ins', 'ins': 'ins', 'riesgo laboral': 'ins',
    'maternidad / paternidad': 'maternity',
    'maternidad': 'maternity', 'paternidad': 'maternity',
    'otro': 'other', 'otra': 'other', 'other': 'other',
}

BENEFIT_TYPE = {
    'beneficio / ingreso': 'income', 'beneficio': 'income', 'ingreso': 'income',
    'income': 'income', 'plus': 'income',
    'deduccion / descuento': 'deduction', 'deduccion / descuento': 'deduction',
    'deduccion': 'deduction', 'deduccion': 'deduction',
    'descuento': 'deduction', 'deduction': 'deduction', 'embargo': 'deduction',
}

AMOUNT_TYPE = {
    'monto fijo': 'fixed', 'fijo': 'fixed', 'fixed': 'fixed',
    'porcentaje': 'percentage', 'percentage': 'percentage', '%': 'percentage',
}

PENSION_CALC = {
    'porcentaje del salario': 'percentage', 'porcentaje': 'percentage',
    'percentage': 'percentage', '%': 'percentage',
    'monto fijo': 'fixed', 'monto_fijo': 'fixed', 'fixed': 'fixed', 'fijo': 'fixed',
}

OVERTIME_TYPE = {
    'simple (1.5x)': 'simple', 'simple': 'simple', '1.5x': 'simple', 'ordinaria': 'simple',
    'doble (2.0x)': 'double', 'doble': 'double', '2x': 'double', 'double': 'double',
    'dia feriado': 'holiday', 'dia feriado': 'holiday', 'feriado': 'holiday',
    'holiday': 'holiday',
}

BANK = {
    'bncr': 'BNCR', 'banco nacional': 'BNCR', 'nacional': 'BNCR',
    'bcr': 'BCR', 'banco de costa rica': 'BCR',
    'bp': 'BP', 'bpop': 'BP', 'banco popular': 'BP', 'popular': 'BP',
    'bac': 'BAC', 'bac san jose': 'BAC', 'bac san jose': 'BAC',
    'bct': 'BCT', 'banco bct': 'BCT',
    'cathay': 'CATHAY',
    'cmb': 'CMB',
    'davivienda': 'DAVIVIENDA',
    'general': 'GENERAL', 'banco general': 'GENERAL',
    'improsa': 'IMPROSA',
    'lafise': 'LAFISE', 'lafise banistmo': 'LAFISE',
    'promerica': 'PROMERICA', 'banco promerica': 'PROMERICA',
    'prival': 'PRIVAL',
    'scotiabank': 'SCOTIA', 'scotia': 'SCOTIA',
    'coocique': 'COOCIQUE',
    'coopenae': 'COOPENAE',
    'mucap': 'MUTUAL_ALJ', 'mutual alajuela': 'MUTUAL_ALJ',
    'otro': 'OTRO', 'other': 'OTRO',
}

CALC_METHOD = {
    'salario fijo': 'fixed', 'fijo': 'fixed', 'fixed': 'fixed',
    'por horas trabajadas': 'attendance', 'asistencia': 'attendance',
    'attendance': 'attendance', 'horas': 'attendance',
}

LOAN_TYPE = {
    'prestamo de empresa': 'loan', 'prestamo de empresa': 'loan',
    'loan': 'loan', 'prestamo': 'loan', 'prestamo': 'loan',
    'adelanto de salario': 'advance', 'advance': 'advance', 'adelanto': 'advance',
}

LOAN_STATE = {
    'aprobado': 'approved', 'approved': 'approved',
    'en curso': 'active', 'active': 'active', 'activo': 'active',
    'borrador': 'draft', 'draft': 'draft',
    'pagado': 'paid', 'paid': 'paid', 'cancelado': 'paid',
    'anulado': 'cancelled', 'cancelled': 'cancelled',
}

PENSION_RELATION = {
    'hijo/a': 'hijo', 'hijo': 'hijo', 'hija': 'hijo',
    'conyuge': 'conyuge', 'conyuge': 'conyuge', 'companero': 'conyuge',
    'companera': 'conyuge', 'conviviente': 'conyuge',
    'padre': 'padre', 'madre': 'madre',
    'otro': 'otro', 'otra': 'otro',
}

BOOL_MAP = {'si': True, 'si': True, 'yes': True, '1': True, 'true': True, 'x': True}

FREQUENCY = {
    'mensual': 'monthly', 'monthly': 'monthly',
    'quincenal': 'biweekly', 'biweekly': 'biweekly',
    'semanal': 'weekly', 'weekly': 'weekly',
    'bimensual': 'bimonthly', 'bimonthly': 'bimonthly',
}


def _normalize(val):
    """Convierte a string lowercase sin espacios extra."""
    if val is None:
        return ''
    return str(val).strip().lower()


def _parse_date(val):
    """Acepta DD/MM/AAAA, YYYY-MM-DD, datetime, date."""
    if not val:
        return None
    if isinstance(val, (date, datetime)):
        return val.date() if isinstance(val, datetime) else val
    s = str(val).strip()
    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y', '%Y/%m/%d'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_float(val):
    if val is None or str(val).strip() == '':
        return 0.0
    try:
        return float(str(val).replace(',', '').strip())
    except (ValueError, TypeError):
        return 0.0


def _parse_int(val):
    f = _parse_float(val)
    return int(f)


def _parse_bool(val):
    return BOOL_MAP.get(_normalize(val), False)


def _map(table, val, default=None):
    return table.get(_normalize(val), default)


# ==============================================================================
# MODELO WIZARD
# ==============================================================================

class ImportDataWizard(models.TransientModel):
    _name        = 'planilla.import.data.wizard'
    _description = 'Importacion Masiva de Empleados desde Excel'

    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company)

    excel_file = fields.Binary(
        string='Archivo Excel (Machote)', required=True,
        help='Cargue el machote Excel completado por el cliente.')
    excel_filename = fields.Char(string='Nombre de archivo')

    # Hojas a procesar
    import_employees    = fields.Boolean('  Empleados',                      default=True)
    import_loans        = fields.Boolean('  Prestamos',                      default=True)
    import_pension      = fields.Boolean('  Pensiones Alimentarias',         default=True)
    import_benefits     = fields.Boolean('  Beneficios / Deducciones',       default=True)
    import_disabilities = fields.Boolean('  Incapacidades',                  default=True)
    import_vacations    = fields.Boolean('  Vacaciones',                     default=True)
    import_overtime     = fields.Boolean('  Horas Extras',                   default=True)
    import_embargos     = fields.Boolean('  Embargos Judiciales',            default=True)
    import_bonos        = fields.Boolean('  Bonos y Beneficios',             default=True)
    import_sample_data  = fields.Boolean(
        '  Importar fila de prueba (cedula 1-0000-0001)',
        default=False,
        help='Active solo cuando quiera verificar que la importacion funciona. '
             'Desactivado por defecto para que la fila naranja se ignore automaticamente.'
    )
    sample_exists = fields.Boolean(
        string='Empleado de prueba existe',
        compute='_compute_sample_exists',
    )

    @api.depends('company_id')
    def _compute_sample_exists(self):
        for rec in self:
            rec.sample_exists = bool(
                self.env['hr.employee'].search([
                    ('identification_id', '=', rec._SAMPLE_CEDULA),
                    ('company_id', '=', rec.company_id.id),
                ], limit=1)
            )

    # FIX v512 BUG-07: unificada con K.TEST_CEDULA (era '1-0000-0001' hardcoded en la clase,
    # desincronizado de planilla_const.py). Ahora un solo punto de verdad.
    _SAMPLE_CEDULA = K.TEST_CEDULA

    # Resultados (readonly, visibles despues de procesar)
    state = fields.Selection([
        ('draft',  'Pendiente'),
        ('done',   'Procesado'),
    ], default='draft')

    # Contadores resumen
    emp_created  = fields.Integer('Empleados creados',     readonly=True)
    emp_skipped  = fields.Integer('Empleados existentes (omitidos)', readonly=True)
    emp_errors   = fields.Integer('Empleados con errores', readonly=True)
    loan_created = fields.Integer('Prestamos creados',     readonly=True)
    pen_created  = fields.Integer('Pensiones creadas',     readonly=True)
    ben_created  = fields.Integer('Beneficios creados',    readonly=True)
    dis_created  = fields.Integer('Incapacidades creadas', readonly=True)
    vac_created  = fields.Integer('Saldos de vacaciones procesados', readonly=True)
    ot_created   = fields.Integer('Horas extras creadas',  readonly=True)
    emb_created  = fields.Integer('Embargos creados',      readonly=True)
    bon_created  = fields.Integer('Bonos creados',         readonly=True)
    total_errors = fields.Integer('Total errores',         readonly=True)

    result_summary = fields.Text('Resumen',   readonly=True)
    report_file    = fields.Binary('Reporte de Errores (Excel)', readonly=True)
    report_name    = fields.Char(default='Reporte_Importacion.xlsx')

    # -- helpers internos ------------------------------------------------------

    def _get_wb(self):
        if not OPENPYXL_OK:
            raise UserError('La libreria openpyxl no esta instalada en el servidor.')
        raw = base64.b64decode(self.excel_file)
        return load_workbook(io.BytesIO(raw), data_only=True, read_only=True)

    def _sheet_rows(self, wb, sheet_names):
        """Retorna (headers_dict, filas) de la primera hoja encontrada."""
        _EXAMPLE_CEDULA = K.SAMPLE_CEDULA   # fila verde de ejemplo -- siempre se salta
        for name in sheet_names:
            for sn in wb.sheetnames:
                if name.lower() in sn.lower():
                    ws = wb[sn]
                    rows = list(ws.iter_rows(values_only=True))
                    if len(rows) < 2:
                        return {}, []

                    # -- Detectar fila de encabezados ----------------------
                    # Buscamos la fila donde alguna celda sea EXACTAMENTE una
                    # palabra clave de encabezado (no un titulo largo que las
                    # contenga como substring). Esto distingue la fila de
                    # encabezados de columna de la fila de titulo o secciones.
                    EXACT_KEYS = ('nombre completo', 'cedula / identificacion',
                                  'cedula empleado', 'numero de expediente',
                                  'concepto', 'tipo de incapacidad',
                                  'dias acumulados', 'tipo de hora extra',
                                  'tipo de prestamo', 'cedula empleado')
                    PARTIAL_KEYS = ('cedula', 'cedula')
                    header_row = None
                    for i, r in enumerate(rows):
                        cells = [str(c).strip().lower() for c in r
                                 if c is not None and str(c).strip()]
                        # Coincidencia exacta con alguna clave conocida
                        if any(cell in EXACT_KEYS for cell in cells):
                            header_row = i
                            break
                        # O celda que sea exactamente 'cedula' o 'cedula'
                        if any(cell in PARTIAL_KEYS for cell in cells):
                            header_row = i
                            break

                    if header_row is None:
                        return {}, []

                    hdrs = {str(c).strip(): ci
                            for ci, c in enumerate(rows[header_row]) if c}

                    # -- Datos: todo lo que viene despues del encabezado ---
                    data_rows = rows[header_row + 1:]

                    # Saltar la fila de ejemplo verde (cedula 1-2345-6789)
                    # buscando ese valor en cualquier columna de cada fila
                    data_rows = [
                        r for r in data_rows
                        if not any(
                            str(c).strip() == _EXAMPLE_CEDULA
                            for c in r if c is not None
                        )
                    ]

                    # Filtrar filas completamente vacias
                    data_rows = [r for r in data_rows
                                 if any(c for c in r
                                        if c is not None and str(c).strip())]

                    # FIX v5.15.6: Filtrar filas de instrucciones al pie de la hoja.
                    # Algunos machotes tienen un bloque de instrucciones al final
                    # (ej. fila 85+ en VACACIONES) que el wizard leia como datos.
                    # Una fila es instruccion si su primera celda no-nula:
                    #   - empieza con '' (marcador de instrucciones), o
                    #   - tiene mas de 80 chars (ninguna cedula/nombre es tan largo), o
                    #   - empieza con un numero seguido de punto y espacio ('1. ', '2. ')
                    def _is_instruction_row(row):
                        for c in row:
                            if c is not None and str(c).strip():
                                txt = str(c).strip()
                                if txt.startswith(''):
                                    return True
                                if len(txt) > 80:
                                    return True
                                import re
                                if re.match(r'^\d+\.\s+', txt):
                                    return True
                                break  # solo checar la primera celda no-nula
                        return False

                    # Truncar en el primer bloque de instrucciones encontrado
                    clean_rows = []
                    for r in data_rows:
                        if _is_instruction_row(r):
                            break  # todo lo que sigue es instrucciones
                        clean_rows.append(r)
                    data_rows = clean_rows

                    return hdrs, data_rows
        return {}, []

    def _v(self, row, hdrs, *col_names):
        """Obtiene valor de la primera columna que coincida."""
        for name in col_names:
            for hdr, idx in hdrs.items():
                if name.lower() in hdr.lower():
                    val = row[idx] if idx < len(row) else None
                    if val is not None and str(val).strip():
                        return val
        return None

    def _find_employee(self, cedula):
        """Busca empleado por identification_id."""
        if not cedula:
            return None
        cedula = str(cedula).strip()
        return self.env['hr.employee'].search(
            [('identification_id', '=', cedula),
             ('company_id', '=', self.company_id.id)], limit=1)

    def _find_m2o(self, model, name_val, field='name', extra_domain=None):
        """Busca un registro por nombre (case-insensitive).
        Usa sudo() + active_test=False para bypassear ir.rules y filtros
        de registros archivados. Los registros pueden tener company_id=NULL
        o pertenecer a otra empresa en la misma BD.
        """
        if not name_val:
            return None
        name_str = str(name_val).strip()
        domain = [(field, 'ilike', name_str)]
        if extra_domain:
            domain += extra_domain
        result = self.env[model].sudo().with_context(active_test=False).search(
            domain, limit=1)
        return result or None

    # ==========================================================================
    # PROCESADORES POR HOJA
    # ==========================================================================

    def _is_sample(self, cedula):
        """Retorna True si la cedula corresponde a la fila de prueba."""
        return str(cedula).strip() == self._SAMPLE_CEDULA

    def _process_employees(self, wb, errors):
        created = skipped = err_count = 0
        hdrs, rows = self._sheet_rows(wb, ['EMPLEADO', 'EMPLOYEE'])
        if not rows:
            return 0, 0, 0

        for row_num, row in enumerate(rows, start=1):
            v = lambda *cols: self._v(row, hdrs, *cols)
            cedula = str(v('Cedula', 'Cedula', 'Identificacion', 'Identificacion') or '').strip()
            nombre = str(v('Nombre') or '').strip()

            if not cedula or not nombre:
                continue
            if self._is_sample(cedula) and not self.import_sample_data:
                continue
            if self._find_employee(cedula):
                skipped += 1
                continue

            try:
                with self.env.cr.savepoint():
                    # Many2one lookups
                    company = self.company_id
                    dept_name    = v('Departamento')
                    subdept_name = v('Sub Departamento', 'Sub-Departamento', 'Subdepartamento')
                    branch_name  = v('Sucursal')
                    job_name     = v('Puesto', 'Cargo')
                    sched_name   = v('Tipo de Horario', 'Horario')
                    cal_name     = v('Calendarizacion de Planilla', 'Frecuencia', 'Calendario', 'Frecuencia de Pago')
                    etype_name   = v('Tipo de Empleado')
                    estatus_name = v('Estado del Empleado', 'Estado')

                    dept    = self._find_m2o('hr.department', dept_name,
                                extra_domain=['|', ('company_id', '=', company.id),
                                                   ('company_id', '=', False)])
                    branch  = self._find_m2o('planilla.branch', branch_name,
                                extra_domain=['|', ('company_id', '=', company.id),
                                                   ('company_id', '=', False)])
                    job     = self._find_m2o('hr.job', job_name,
                                extra_domain=['|', ('company_id', '=', company.id),
                                                   ('company_id', '=', False)])
                    sched   = self._find_m2o('planilla.schedule.type', sched_name)
                    # Si no encontro por nombre completo, intentar con las
                    # primeras palabras (ej: "Jornada Completa" de
                    # "Jornada Completa (8 horas - Lun a Vie)")
                    if not sched and sched_name:
                        short_name = sched_name.split('(')[0].strip()
                        if short_name != sched_name:
                            sched = self._find_m2o('planilla.schedule.type', short_name)
                    cal     = self._find_m2o('planilla.calendar', cal_name,
                                extra_domain=['|', ('company_id', '=', company.id),
                                                   ('company_id', '=', False)])
                    etype   = self._find_m2o('planilla.employee.type', etype_name)
                    estatus = self._find_m2o('planilla.employee.status', estatus_name)

                    # Sub departamento -- buscar dentro del dpto padre si se encontro
                    subdept = None
                    if subdept_name:
                        subdept_domain = ['|', ('company_id', '=', company.id),
                                               ('company_id', '=', False)]
                        if dept:
                            subdept_domain.append(('parent_id', '=', dept.id))
                        subdept = self._find_m2o('hr.department', subdept_name,
                                    extra_domain=subdept_domain)

                    # Si no se encontro calendario por nombre, buscar por frecuencia
                    if not cal:
                        freq_raw = _normalize(v('Calendarizacion de Planilla', 'Frecuencia', 'Calendario', 'Frecuencia de Pago') or '')
                        freq_val = FREQUENCY.get(freq_raw)
                        if freq_val:
                            cal = self.env['planilla.calendar'].sudo().search([
                                '|',
                                ('company_id', '=', company.id),
                                ('company_id', '=', False),
                                ('frequency', '=', freq_val),
                            ], limit=1) or None

                    # Identificacion type
                    id_type_raw  = _normalize(v('Tipo de Identificacion', 'Tipo Identificacion') or '')
                    id_type_code = INS_ID_TYPE.get(id_type_raw, 'CI')      # code en planilla.identification.type
                    ins_id_code  = INS_ID_TYPE_CODE.get(id_type_raw, '01') # codigo numerico para INS
                    id_type_rec  = self.env['planilla.identification.type'].search(
                        [('code', '=', id_type_code)], limit=1)

                    vals = {
                        'name':                       nombre,
                        'identification_id':          cedula,
                        'company_id':                 company.id,
                        'entry_date':                 _parse_date(v('Fecha de Ingreso', 'Fecha Ingreso')),
                        'exit_date':                  _parse_date(v('Fecha de Salida', 'Fecha Salida')),
                        'work_email':                 v('Correo', 'Email') or False,
                        'base_salary':                _parse_float(v('Salario Base', 'Salario')),
                        'salary_effective_date':      _parse_date(v('Fecha Vigencia', 'Vigencia Salarial')),
                        'payroll_calculation_method': _map(CALC_METHOD, v('Metodo', 'Metodo', 'Metodo de Calculo')) or 'fixed',
                        'ccss_number':                str(v('CCSS', 'Numero CCSS', 'Numero CCSS') or '').strip() or False,
                        'ccss_insured':               _parse_bool(v('Asegurado CCSS', 'CCSS Asegurado')),
                        'has_variable_income':        _parse_bool(v('Salario Variable', 'Comisiones', 'Ingreso Variable')),
                        'bank_account_number':        str(v('Cuenta Bancaria', 'Cuenta') or '').strip() or False,
                        'bank_iban':                  str(v('IBAN') or '').strip() or False,
                        'sinpe_phone': re.sub(r'\D', '', str(v('SINPE', 'Sinpe Movil', 'Sinpe Movil') or ''))[:8] or False,
                        'bank_name':                  _map(BANK, v('Banco')) or False,
                        'bank_account_type':          _map(ACCOUNT_TYPE, v('Tipo de Cuenta Banco', 'Tipo de Cuenta')) or False,
                        # INS
                        'ins_include':               _parse_bool(v('Incluir INS', 'Incluir en INS')),
                        'ins_policy_number':         str(v('Poliza INS', 'Poliza INS', 'Numero de Poliza') or '').strip() or False,
                        'ins_first_name':            str(v('Nombre INS') or '').strip() or False,
                        'ins_first_lastname':        str(v('Primer Apellido INS') or '').strip() or False,
                        'ins_second_lastname':       str(v('Segundo Apellido INS') or '').strip() or False,
                        'ins_risk_class':            _map(INS_RISK, v('Clase de Riesgo', 'Riesgo INS')) or False,
                        'ins_workday_type':          _map(INS_WORKDAY, v('Jornada INS', 'Tipo de Jornada INS', 'Tipo de Jornada')) or '01',
                        'ins_civil_status':          _map(INS_CIVIL, v('Estado Civil INS', 'Estado Civil')) or '01',
                        'ins_id_type':               ins_id_code,
                        'ins_nationality':           _map(INS_NATIONALITY, v('Nacionalidad INS', 'Nacionalidad')) or 'CR',
                    }

                    # Relacionales opcionales
                    if dept:    vals['department_id']       = dept.id
                    if subdept: vals['sub_department_id']   = subdept.id
                    if branch:  vals['branch_id']           = branch.id
                    if job:     vals['job_id']              = job.id
                    if sched:   vals['schedule_type_id']    = sched.id
                    if cal:     vals['payroll_calendar_id'] = cal.id
                    if etype:   vals['employee_type_id']    = etype.id
                    if estatus: vals['employee_status_id']  = estatus.id
                    if id_type_rec: vals['identification_type_id'] = id_type_rec.id

                    # INS occupation -- acepta codigo numerico (4 digitos) o
                    # texto completo del dropdown "[1120] Directores y gerentes generales"
                    ins_occ_raw = str(v('Ocupacion INS', 'Ocupacion INS') or '').strip()
                    if ins_occ_raw:
                        # Extraer solo el codigo si viene como "[1120] Descripcion..."
                        import re as _re
                        _occ_match = _re.match(r'\[(\d{4})\]', ins_occ_raw)
                        vals['ins_occupation'] = _occ_match.group(1) if _occ_match else ins_occ_raw

                    # Tipo de sangre y notas medicas
                    blood_raw = str(v('Tipo de Sangre', 'Sangre') or '').strip().upper()
                    if blood_raw in ('A+','A-','B+','B-','AB+','AB-','O+','O-'):
                        vals['blood_type'] = blood_raw
                    medical = str(v('Diagnostico', 'Diagnostico', 'Notas Medicas', 'Notas Medicas') or '').strip()
                    if medical:
                        vals['medical_notes'] = medical

                    # Campos personales estandar de hr.employee -- pueden no existir
                    # segun la version de Odoo o si estan en hr.employee.private.
                    # Los agregamos solo si el campo existe en el modelo.
                    emp_fields = self.env['hr.employee']._fields

                    # Pais: buscar por nombre en res.country
                    country_raw = str(v('Pais', 'Pais') or '').strip()
                    country_id  = False
                    if country_raw:
                        country = self.env['res.country'].search(
                            [('name', 'ilike', country_raw)], limit=1)
                        country_id = country.id if country else False

                    _personal = {
                        'gender':            _map(GENDER, v('Genero', 'Genero')) or False,
                        'children':          _parse_int(v('Numero de Dependientes', 'Dependientes')) or 0,
                        'private_street':    str(v('Direccion', 'Direccion') or '').strip() or False,
                        'private_phone':     str(v('Telefono Personal', 'Telefono Personal') or '').strip() or False,
                        'private_email':     str(v('Correo Personal', 'Email Personal') or '').strip() or False,
                        'private_country_id': country_id or False,
                        'notes':             str(v('Observaciones') or '').strip() or False,
                    }
                    for fname, fval in _personal.items():
                        if fname in emp_fields and fval:
                            vals[fname] = fval

                    # Work contact (requerido por hr.employee en Odoo 17+)
                    contact = self.env['res.partner'].create({
                        'name':  nombre,
                        'email': vals.get('work_email') or False,
                        'company_type': 'person',
                    })
                    vals['work_contact_id'] = contact.id

                    # Crear empleado. Si el IBAN falla la validacion del
                    # digito verificador, reintentar sin el IBAN para no
                    # bloquear toda la importacion -- el IBAN se puede
                    # corregir manualmente despues.
                    try:
                        self.env['hr.employee'].create(vals)
                    except Exception as e_create:
                        if 'iban' in str(e_create).lower() or 'digito verificador' in str(e_create).lower():
                            iban_original = vals.pop('bank_iban', None)
                            vals.pop('bank_account_number', None)
                            self.env['hr.employee'].create(vals)
                            created += 1
                            errors.append({
                                'hoja': 'EMPLEADOS', 'fila': row_num,
                                'cedula': cedula, 'nombre': nombre,
                                'error': f'ADVERTENCIA: Empleado creado SIN IBAN -- digito verificador invalido: {iban_original}. Corrija el IBAN manualmente en el empleado.',
                                'traceback': '',
                                'vals': {},
                            })
                            _logger.warning('ImportDataWizard EMPLEADOS fila %s cedula %s: IBAN invalido %s -- empleado creado sin IBAN',
                                            row_num, cedula, iban_original)
                            continue
                        else:
                            raise
                    created += 1

            except Exception as e:
                err_count += 1
                errors.append({
                    'hoja': 'EMPLEADOS', 'fila': row_num,
                    'cedula': cedula, 'nombre': nombre,
                    'error': str(e),
                    'traceback': traceback.format_exc(),
                    'vals': {k: str(val)[:120] for k, val in vals.items()},
                })
                _logger.warning('ImportDataWizard EMPLEADOS fila %s cedula %s: %s',
                                row_num, cedula, e)

        return created, skipped, err_count

    def _process_loans(self, wb, errors):
        created = err_count = 0
        hdrs, rows = self._sheet_rows(wb, ['PRESTAMO', 'LOAN'])
        if not rows:
            return 0, 0

        for row_num, row in enumerate(rows, start=1):
            v = lambda *cols: self._v(row, hdrs, *cols)
            cedula = str(v('Cedula', 'Cedula') or '').strip()
            if not cedula:
                continue
            if self._is_sample(cedula) and not self.import_sample_data:
                continue

            emp = self._find_employee(cedula)
            if not emp:
                err_count += 1
                errors.append({
                    'hoja': 'PRESTAMOS', 'fila': row_num, 'cedula': cedula,
                    'nombre': '', 'error': 'Empleado no encontrado en Odoo',
                })
                continue

            try:
                with self.env.cr.savepoint():
                    amount_total = _parse_float(v('Monto Total', 'Monto'))
                    installments = _parse_int(v('Numero de Cuotas', 'Cuotas', 'Installments'))
                    date_granted = _parse_date(v('Fecha de Otorgamiento', 'Fecha Otorgamiento'))
                    date_first   = _parse_date(v('Fecha Primera Deduccion', 'Primera Deduccion'))
                    state_raw    = _map(LOAN_STATE, v('Estado')) or 'approved'
                    loan_type    = _map(LOAN_TYPE, v('Tipo de Prestamo', 'Tipo')) or 'loan'
                    amount_paid  = _parse_float(v('Monto ya Pagado', 'Monto Pagado'))

                    if amount_total <= 0 or installments <= 0:
                        raise ValueError('Monto total e instalamentos deben ser > 0')

                    loan = self.env['planilla.employee.loan'].create({
                        'employee_id':         emp.id,
                        'loan_type':           loan_type,
                        'description':         str(v('Descripcion', 'Descripcion', 'Motivo') or '').strip() or False,
                        'amount_total':        amount_total,
                        'installments':        installments,
                        'date_granted':        date_granted or date.today(),
                        'date_first_deduction': date_first or date.today(),
                        'state':               'approved',
                        'note':                str(v('Observaciones') or '').strip() or False,
                    })

                    # Generar cuotas automaticamente
                    loan._generate_installments()

                    # Si ya tiene pagos parciales, marcar cuotas como descontadas
                    if amount_paid > 0 and loan.installment_ids:
                        remaining = amount_paid
                        for inst in loan.installment_ids.sorted('due_date'):
                            if remaining <= 0:
                                break
                            if remaining >= inst.amount:
                                inst.state = 'deducted'
                                remaining -= inst.amount
                            else:
                                break
                        loan._compute_amounts()

                    # Activar si el estado final solicitado es active
                    if state_raw == 'active' and loan.state == 'approved':
                        loan.state = 'active'

                    created += 1

            except Exception as e:
                err_count += 1
                # FIX-A3: _process_loans no usa dict 'vals' (crea el objeto loan directamente).
                # El bloque except referenciaba vals.items() que causa NameError secundario
                # cuando el error ocurre antes de que loan se cree. Usar locals() como fallback.
                _safe_vals = locals().get('vals', {}) or {}
                errors.append({
                    'hoja': 'PRESTAMOS', 'fila': row_num, 'cedula': cedula,
                    'nombre': emp.name, 'error': str(e),
                    'traceback': traceback.format_exc(),
                    'vals': {k: str(v)[:120] for k, v in _safe_vals.items()},
                })
                _logger.warning('ImportDataWizard PRESTAMOS fila %s: %s', row_num, e)

        return created, err_count

    def _process_pension(self, wb, errors):
        created = err_count = 0
        hdrs, rows = self._sheet_rows(wb, ['PENSION', 'PENSION'])
        if not rows:
            return 0, 0

        for row_num, row in enumerate(rows, start=1):
            v = lambda *cols: self._v(row, hdrs, *cols)
            cedula = str(v('Cedula', 'Cedula') or '').strip()
            if not cedula:
                continue
            if self._is_sample(cedula) and not self.import_sample_data:
                continue

            emp = self._find_employee(cedula)
            if not emp:
                err_count += 1
                errors.append({
                    'hoja': 'PENSION_ALIMENTARIA', 'fila': row_num, 'cedula': cedula,
                    'nombre': '', 'error': 'Empleado no encontrado en Odoo',
                })
                continue

            try:
                with self.env.cr.savepoint():
                    calc_type = _map(PENSION_CALC, v('Tipo de Calculo', 'Tipo Calculo')) or 'fixed'
                    pct_raw   = _parse_float(v('Porcentaje'))
                    monto_raw = _parse_float(v('Monto Fijo', 'Monto'))

                    branch = self._find_m2o('planilla.branch', v('Sucursal'),
                                extra_domain=[('company_id', '=', self.company_id.id)])

                    vals = {
                        'employee_id':          emp.id,
                        'numero_expediente':    str(v('Expediente', 'Numero de Expediente') or '').strip() or False,
                        'juzgado':              str(v('Juzgado') or '').strip() or False,
                        'fecha_resolucion':     _parse_date(v('Fecha de Resolucion', 'Fecha Resolucion')),
                        'beneficiario_nombre':  str(v('Beneficiario', 'Nombre Beneficiario') or '').strip() or False,
                        'beneficiario_relacion': _map(PENSION_RELATION, v('Relacion', 'Relacion')) or 'hijo',
                        'beneficiario_cuenta':  str(v('Cuenta Beneficiario') or '').strip() or False,
                        'calculation_type':     calc_type,
                        'percentage':           pct_raw if calc_type == 'percentage' else 0.0,
                        'fixed_amount':         monto_raw if calc_type == 'fixed' else 0.0,
                        'date_start':           _parse_date(v('Fecha de Inicio', 'Fecha Inicio')) or date.today(),
                        'date_end':             _parse_date(v('Fecha de Fin', 'Fecha Fin')),
                        'state':                'active',
                        'active':               True,
                    }
                    if branch:
                        vals['branch_id'] = branch.id

                    self.env['planilla.pension.alimentaria'].create(vals)
                    created += 1

            except Exception as e:
                err_count += 1
                errors.append({
                    'hoja': 'PENSION_ALIMENTARIA', 'fila': row_num, 'cedula': cedula,
                    'nombre': emp.name, 'error': str(e),
                    'traceback': traceback.format_exc(),
                    'vals': {k: str(val)[:120] for k, val in vals.items()},
                })
                _logger.warning('ImportDataWizard PENSION fila %s: %s', row_num, e)

        return created, err_count

    def _process_benefits(self, wb, errors):
        created = err_count = 0
        hdrs, rows = self._sheet_rows(wb, ['OTROS DESCUENTOS', 'DESCUENTO', 'BENEFICIO', 'BENEFIT'])
        if not rows:
            return 0, 0

        for row_num, row in enumerate(rows, start=1):
            v = lambda *cols: self._v(row, hdrs, *cols)
            cedula = str(v('Cedula', 'Cedula') or '').strip()
            if not cedula:
                continue
            if self._is_sample(cedula) and not self.import_sample_data:
                continue

            emp = self._find_employee(cedula)
            if not emp:
                err_count += 1
                errors.append({
                    'hoja': 'OTROS DESCUENTOS', 'fila': row_num, 'cedula': cedula,
                    'nombre': '', 'error': 'Empleado no encontrado en Odoo',
                })
                continue

            try:
                with self.env.cr.savepoint():
                    benefit_type = _map(BENEFIT_TYPE, v('Tipo')) or 'deduction'
                    amount_type  = _map(AMOUNT_TYPE, v('Tipo de Monto', 'Tipo Monto')) or 'fixed'
                    amount       = _parse_float(v('Monto'))
                    pct          = _parse_float(v('Porcentaje'))
                    concepto     = str(v('Concepto') or '').strip()

                    # Buscar codigo de deduccion por codigo o por nombre
                    ded_code_raw = str(v('Codigo Deduccion', 'Codigo', 'Codigo') or '').strip()
                    ded_code = None
                    if ded_code_raw:
                        ded_code = (
                            self.env['planilla.deduction.code'].search(
                                [('code', '=ilike', ded_code_raw)], limit=1) or
                            self.env['planilla.deduction.code'].search(
                                [('name', 'ilike', ded_code_raw)], limit=1)
                        ) or None

                    # deduction_code_id es required=True en el modelo.
                    # Si no se especifico, usar el primer codigo disponible del tipo correcto,
                    # o el primero que exista.
                    if not ded_code:
                        ded_type = 'employee' if benefit_type == 'deduction' else 'employer'
                        ded_code = (
                            self.env['planilla.deduction.code'].search(
                                [('deduction_type', '=', ded_type)], limit=1) or
                            self.env['planilla.deduction.code'].search([], limit=1)
                        ) or None

                    if not concepto:
                        raise ValueError('El campo Concepto es obligatorio')
                    if not ded_code:
                        raise ValueError('No existe ningun Codigo de Deduccion en Odoo. '
                                         'Cree al menos uno en Configuracion -> Codigos de Deduccion')

                    vals = {
                        'employee_id':      emp.id,
                        'name':             concepto,
                        'benefit_type':     benefit_type,
                        'amount_type':      amount_type,
                        'amount':           amount if amount_type == 'fixed' else 0.0,
                        'percentage':       pct    if amount_type == 'percentage' else 0.0,
                        'date_start':       _parse_date(v('Vigente Desde', 'Fecha Inicio')),
                        'date_end':         _parse_date(v('Vigente Hasta', 'Fecha Fin')),
                        'note':             str(v('Nota', 'Observaciones') or '').strip() or False,
                        'active':           True,
                        'deduction_code_id': ded_code.id,
                    }

                    self.env['planilla.recurring.benefit'].create(vals)
                    created += 1

            except Exception as e:
                err_count += 1
                errors.append({
                    'hoja': 'OTROS DESCUENTOS', 'fila': row_num, 'cedula': cedula,
                    'nombre': emp.name, 'error': str(e),
                    'traceback': traceback.format_exc(),
                    'vals': {k: str(val)[:120] for k, val in vals.items()},
                })
                _logger.warning('ImportDataWizard BENEFICIOS fila %s: %s', row_num, e)

        return created, err_count

    def _process_disabilities(self, wb, errors):
        created = err_count = 0
        hdrs, rows = self._sheet_rows(wb, ['INCAPACIDAD', 'DISABILITY'])
        if not rows:
            return 0, 0

        for row_num, row in enumerate(rows, start=1):
            v = lambda *cols: self._v(row, hdrs, *cols)
            cedula = str(v('Cedula', 'Cedula') or '').strip()
            if not cedula:
                continue
            if self._is_sample(cedula) and not self.import_sample_data:
                continue

            emp = self._find_employee(cedula)
            if not emp:
                err_count += 1
                errors.append({
                    'hoja': 'INCAPACIDADES', 'fila': row_num, 'cedula': cedula,
                    'nombre': '', 'error': 'Empleado no encontrado en Odoo',
                })
                continue

            try:
                with self.env.cr.savepoint():
                    branch = self._find_m2o('planilla.branch', v('Sucursal'),
                                extra_domain=[('company_id', '=', self.company_id.id)])

                    vals = {
                        'employee_id':          emp.id,
                        'disability_type':      _map(DISABILITY_TYPE, v('Tipo de Incapacidad', 'Tipo')) or 'ccss',
                        'date_start':           _parse_date(v('Fecha Inicio')) or date.today(),
                        'date_end':             _parse_date(v('Fecha Fin')) or date.today(),
                        'subsidy_percentage':   _parse_float(v('% Subsidiado', 'Subsidiado CCSS')),
                        'employer_percentage':  _parse_float(v('% Patrono', 'Cargo Patrono')) or 0.0,
                        # FIX-A2: default 0.0 -- el complemento patronal NO es obligatorio
                        # (Art. 79 Regl. CCSS). El valor anterior 40.0 era fiscalmente incorrecto.
                        'certificate_number':   str(v('Numero Certificado', 'Certificado') or '').strip() or False,
                        'diagnosis':            str(v('Diagnostico', 'Diagnostico') or '').strip() or False,
                        'note':                 str(v('Observaciones') or '').strip() or False,
                        'state':                'confirmed',
                    }
                    daily = _parse_float(v('Salario Diario', 'Daily Salary'))
                    if daily:
                        vals['daily_salary'] = daily
                    if branch:
                        vals['branch_id'] = branch.id

                    self.env['planilla.disability'].create(vals)
                    created += 1

            except Exception as e:
                err_count += 1
                errors.append({
                    'hoja': 'INCAPACIDADES', 'fila': row_num, 'cedula': cedula,
                    'nombre': emp.name, 'error': str(e),
                    'traceback': traceback.format_exc(),
                    'vals': {k: str(val)[:120] for k, val in vals.items()},
                })
                _logger.warning('ImportDataWizard INCAPACIDADES fila %s: %s', row_num, e)

        return created, err_count

    def _process_vacations(self, wb, errors):
        """
        Carga el saldo inicial de vacaciones directamente en los campos
        vacation_initial_balance y vacation_initial_balance_date del empleado.

        Estrategia:
          - Lee 'Saldo Inicial (dias)' y 'Fecha de Corte del Saldo' del Excel.
          - Actualiza el empleado con esos valores.
          - El calculo automatico (_compute_vacation_balance) usara estos campos
            como punto de partida y acumulara dias solo a partir de esa fecha.
          - Si el empleado ya tiene saldo inicial configurado, lo SOBREESCRIBE
            (idempotente: se puede reimportar sin duplicar datos).
        """
        created = err_count = 0
        hdrs, rows = self._sheet_rows(wb, ['VACACION', 'VACATION'])
        if not rows:
            return 0, 0

        for row_num, row in enumerate(rows, start=1):
            v = lambda *cols: self._v(row, hdrs, *cols)
            cedula = str(v('Cedula', 'Cedula') or '').strip()
            if not cedula:
                continue
            if self._is_sample(cedula) and not self.import_sample_data:
                continue

            emp = self._find_employee(cedula)
            if not emp:
                err_count += 1
                errors.append({
                    'hoja': 'VACACIONES', 'fila': row_num, 'cedula': cedula,
                    'nombre': '', 'error': 'Empleado no encontrado en Odoo',
                })
                continue

            try:
                with self.env.cr.savepoint():
                    # Columnas principales (obligatorias para saldo inicial)
                    saldo_raw = v('Saldo Inicial', 'Saldo Inicial (dias)', 'Dias Disponibles', 'Dias Disponibles')
                    # Distinguir celda vacia (None/'') de cero real
                    saldo_vacio = (saldo_raw is None or str(saldo_raw).strip() == '')
                    saldo_inicial = _parse_float(saldo_raw)  # 0.0 si vacio
                    fecha_corte = _parse_date(
                        v('Fecha de Corte del Saldo', 'Fecha de Corte', 'Ultima Fecha de Corte', 'Ultima Fecha')
                    )
                    obs = str(v('Observaciones', 'Periodo', 'Periodo') or '').strip()

                    vals_emp = {}

                    # Solo actualizar si la celda tiene un valor (incluso 0 explicito es valido)
                    if not saldo_vacio:
                        vals_emp['vacation_initial_balance'] = round(saldo_inicial, 2)

                    if fecha_corte:
                        vals_emp['vacation_initial_balance_date'] = fecha_corte
                    elif not saldo_vacio and saldo_inicial > 0 and not fecha_corte:
                        # Hay saldo pero no fecha -> advertir y usar hoy
                        vals_emp['vacation_initial_balance_date'] = date.today()
                        errors.append({
                            'hoja': 'VACACIONES', 'fila': row_num, 'cedula': cedula,
                            'nombre': emp.name,
                            'error': 'Advertencia: no se encontro Fecha de Corte del Saldo. '
                                     'Se uso la fecha de hoy como corte. '
                                     'Corrija manualmente en el perfil del empleado.',
                        })

                    if vals_emp:
                        emp.write(vals_emp)
                        # Forzar recalculo del saldo
                        emp._compute_vacation_balance()
                        created += 1

                        if obs:
                            emp.message_post(
                                body=f'<b>Saldo inicial de vacaciones importado:</b> '
                                     f'{saldo_inicial} dias al {fecha_corte}. '
                                     f'Obs: {obs}',
                                message_type='notification',
                            )

            except Exception as e:
                err_count += 1
                errors.append({
                    'hoja': 'VACACIONES', 'fila': row_num, 'cedula': cedula,
                    'nombre': emp.name if emp else '', 'error': str(e),
                    'traceback': traceback.format_exc(),
                })
                _logger.warning('ImportDataWizard VACACIONES fila %s: %s', row_num, e)

        return created, err_count


    def _process_overtime(self, wb, errors):
        created = err_count = 0
        hdrs, rows = self._sheet_rows(wb, ['HORA', 'OVERTIME', 'HE'])
        if not rows:
            return 0, 0

        for row_num, row in enumerate(rows, start=1):
            v = lambda *cols: self._v(row, hdrs, *cols)
            cedula = str(v('Cedula', 'Cedula') or '').strip()
            if not cedula:
                continue
            if self._is_sample(cedula) and not self.import_sample_data:
                continue

            emp = self._find_employee(cedula)
            if not emp:
                err_count += 1
                errors.append({
                    'hoja': 'HORAS_EXTRAS', 'fila': row_num, 'cedula': cedula,
                    'nombre': '', 'error': 'Empleado no encontrado en Odoo',
                })
                continue

            try:
                with self.env.cr.savepoint():
                    ot_date  = _parse_date(v('Fecha'))
                    hours    = _parse_float(v('Horas', 'Horas Extras'))
                    ot_type  = _map(OVERTIME_TYPE, v('Tipo', 'Tipo de HE')) or 'simple'
                    branch   = self._find_m2o('planilla.branch', v('Sucursal'),
                                  extra_domain=[('company_id', '=', self.company_id.id)])

                    if not ot_date or hours <= 0:
                        raise ValueError('Fecha y horas son obligatorias y horas > 0')

                    vals = {
                        'employee_id':   emp.id,
                        'date':          ot_date,
                        'hours':         hours,
                        'overtime_type': ot_type,
                        'note':          str(v('Observaciones') or '').strip() or False,
                        'state':         'approved',
                        'source':        'manual',
                    }
                    if branch:
                        vals['branch_id'] = branch.id

                    self.env['planilla.overtime'].create(vals)
                    created += 1

            except Exception as e:
                err_count += 1
                errors.append({
                    'hoja': 'HORAS_EXTRAS', 'fila': row_num, 'cedula': cedula,
                    'nombre': emp.name, 'error': str(e),
                    'traceback': traceback.format_exc(),
                    'vals': {k: str(val)[:120] for k, val in vals.items()},
                })
                _logger.warning('ImportDataWizard OVERTIME fila %s: %s', row_num, e)

        return created, err_count

    # ==========================================================================
    # PROCESADOR EMBARGOS JUDICIALES
    # ==========================================================================

    def _process_embargos(self, wb, errors):
        """Importa embargos judiciales desde la hoja EMBARGOS del machote."""
        created = err_count = 0
        hdrs, rows = self._sheet_rows(wb, ['EMBARGO', 'EMB'])
        if not rows:
            return 0, 0

        EMBARGO_CALC = {
            'monto fijo': 'fixed', 'fixed': 'fixed',
            'porcentaje del neto disponible': 'percentage', 'porcentaje': 'percentage',
            'percentage': 'percentage',
        }

        for row_num, row in enumerate(rows, start=1):
            v = lambda *cols: self._v(row, hdrs, *cols)
            cedula = str(v('Cedula', 'Cedula') or '').strip()
            if not cedula:
                continue
            if self._is_sample(cedula) and not self.import_sample_data:
                continue

            vals = {}
            emp = self._find_employee(cedula)
            if not emp:
                err_count += 1
                errors.append({
                    'hoja': 'EMBARGOS', 'fila': row_num, 'cedula': cedula,
                    'nombre': '', 'error': 'Empleado no encontrado en Odoo',
                    'traceback': '', 'vals': {},
                })
                continue

            try:
                with self.env.cr.savepoint():
                    calc_raw  = _normalize(v('Tipo de Calculo', 'Tipo Calculo') or '')
                    calc_type = EMBARGO_CALC.get(calc_raw, 'fixed')
                    pct       = _parse_float(v('Porcentaje', 'Porcentaje (%)'))
                    monto     = _parse_float(v('Monto Fijo', 'Monto Fijo (CRC)'))
                    expediente= str(v('Ndeg Expediente', 'Expediente') or '').strip()
                    juzgado   = str(v('Juzgado', 'Juzgado / Tribunal') or '').strip()

                    if not expediente:
                        raise ValueError('El Ndeg Expediente Judicial es obligatorio')
                    if calc_type == 'fixed' and monto <= 0:
                        raise ValueError('El Monto Fijo debe ser mayor a CRC0')
                    if calc_type == 'percentage' and not (0 < pct <= 25):
                        raise ValueError(f'El porcentaje ({pct}%) debe estar entre 0 y 25% (Art. 172 CT)')

                    vals = {
                        'employee_id':        emp.id,
                        'numero_expediente':  expediente,
                        'juzgado':            juzgado or 'Sin especificar',
                        'fecha_resolucion':   _parse_date(v('Fecha de Resolucion', 'Fecha Resolucion')),
                        'beneficiario_nombre': str(v('Nombre del Acreedor', 'Acreedor') or '').strip() or 'Sin especificar',
                        'beneficiario_cuenta': str(v('IBAN del Acreedor', 'IBAN Acreedor') or '').strip() or False,
                        'calculation_type':   calc_type,
                        'fixed_amount':       monto if calc_type == 'fixed' else 0.0,
                        'percentage':         pct   if calc_type == 'percentage' else 0.0,
                        'date_start':         _parse_date(v('Vigente Desde', 'Desde')) or date.today(),
                        'date_end':           _parse_date(v('Vigente Hasta', 'Hasta')),
                        'state':              'active',
                        'note':               str(v('Observaciones') or '').strip() or False,
                    }
                    self.env['planilla.embargo'].create(vals)
                    created += 1
                    _logger.info('ImportDataWizard EMBARGOS: creado embargo %s para %s', vals.get('numero_expediente'), cedula)

            except Exception as e:
                err_count += 1
                errors.append({
                    'hoja': 'EMBARGOS', 'fila': row_num, 'cedula': cedula,
                    'nombre': emp.name, 'error': str(e),
                    'traceback': traceback.format_exc(),
                    'vals': {k: str(val)[:120] for k, val in vals.items()},
                })
                _logger.warning('ImportDataWizard EMBARGOS fila %s: %s', row_num, e)

        return created, err_count

    # ==========================================================================
    # PROCESADOR BONOS E INCENTIVOS
    # ==========================================================================

    def _process_bonos(self, wb, errors):
        """Importa bonos e incentivos desde la hoja BONOS del machote."""
        created = err_count = 0
        hdrs, rows = self._sheet_rows(wb, ['BONO', 'INCENTIVO'])
        if not rows:
            return 0, 0

        BONO_TYPE_MAP = {
            'productividad / rendimiento':         'productividad',
            'productividad':                       'productividad',
            'asistencia perfecta':                 'asistencia',
            'asistencia':                          'asistencia',
            'antiguedad por anos de servicio':     'antiguedad',
            'antiguedad':                          'antiguedad',
            'subsidio de transporte / kilometraje':'transporte',
            'transporte':                          'transporte',
            'subsidio de alimentacion (en dinero)':'alimentacion',
            'alimentacion':                        'alimentacion',
            'alimentacion':                        'alimentacion',
            'subsidio educativo':                  'educacion',
            'educacion':                           'educacion',
            'subsidio de salud / medico':          'salud',
            'salud':                               'salud',
            'gastos de representacion':            'representacion',
            'representacion':                      'representacion',
            'comision por ventas':                 'comision',
            'comision':                            'comision',
            'incentivo / premio especial':         'incentivo',
            'incentivo':                           'incentivo',
            'otro':                                'otro',
        }
        BONO_CALC_MAP = {
            'monto fijo': 'fixed', 'fixed': 'fixed',
            'porcentaje del salario base': 'percentage',
            'porcentaje': 'percentage', 'percentage': 'percentage',
        }

        for row_num, row in enumerate(rows, start=1):
            v = lambda *cols: self._v(row, hdrs, *cols)
            cedula = str(v('Cedula', 'Cedula') or '').strip()
            if not cedula:
                continue
            if self._is_sample(cedula) and not self.import_sample_data:
                continue

            vals = {}
            emp = self._find_employee(cedula)
            if not emp:
                err_count += 1
                errors.append({
                    'hoja': 'BONOS', 'fila': row_num, 'cedula': cedula,
                    'nombre': '', 'error': 'Empleado no encontrado en Odoo',
                    'traceback': '', 'vals': {},
                })
                continue

            try:
                with self.env.cr.savepoint():
                    concepto  = str(v('Concepto', 'Nombre', 'Concepto / Nombre') or '').strip()
                    tipo_raw  = _normalize(v('Tipo de Bono', 'Tipo') or '')
                    calc_raw  = _normalize(v('Tipo de Calculo', 'Tipo Calculo') or '')
                    bono_type = BONO_TYPE_MAP.get(tipo_raw, 'otro')
                    calc_type = BONO_CALC_MAP.get(calc_raw, 'fixed')
                    monto     = _parse_float(v('Monto Fijo', 'Monto Fijo (CRC)'))
                    pct       = _parse_float(v('Porcentaje', 'Porcentaje (%)'))
                    recurrente= _parse_bool(v('Es Recurrente', 'Recurrente'))
                    afecto_ccss  = _parse_bool(v('Afecto CCSS', 'CCSS'))
                    afecto_renta = _parse_bool(v('Afecto Renta', 'Renta'))
                    tope      = _parse_float(v('Tope Exento', 'Tope Exento (CRC/mes)'))

                    if not concepto:
                        raise ValueError('El Concepto del bono es obligatorio')
                    if calc_type == 'fixed' and monto <= 0:
                        raise ValueError('El Monto Fijo debe ser mayor a CRC0')
                    if calc_type == 'percentage' and pct <= 0:
                        raise ValueError('El Porcentaje debe ser mayor a 0%')

                    # Si no se especifico afecto_ccss/renta, aplicar defaults del tipo
                    DEFAULTS_CCSS  = {'transporte': False, 'educacion': False,
                                      'salud': False, 'representacion': False}
                    DEFAULTS_RENTA = {'transporte': False, 'educacion': False,
                                      'salud': False, 'representacion': False}
                    if not v('Afecto CCSS', 'CCSS'):
                        afecto_ccss  = DEFAULTS_CCSS.get(bono_type, True)
                    if not v('Afecto Renta', 'Renta'):
                        afecto_renta = DEFAULTS_RENTA.get(bono_type, True)
                    if not tope and bono_type == 'transporte':
                        tope = K.TOPE_TRANSPORTE

                    vals = {
                        'employee_id':  emp.id,
                        'name':         concepto,
                        'bono_type':    bono_type,
                        'amount_type':  calc_type,
                        'amount':       monto if calc_type == 'fixed' else 0.0,
                        'percentage':   pct   if calc_type == 'percentage' else 0.0,
                        'is_recurring': recurrente if v('Es Recurrente', 'Recurrente') else True,
                        'afecto_ccss':  afecto_ccss,
                        'afecto_renta': afecto_renta,
                        'tope_exento':  tope,
                        'date_start':   _parse_date(v('Vigente Desde', 'Desde')) or date.today(),
                        'date_end':     _parse_date(v('Vigente Hasta', 'Hasta')),
                        'state':        'active',
                        'note':         str(v('Observaciones') or '').strip() or False,
                    }
                    self.env['planilla.bono'].create(vals)
                    created += 1
                    _logger.info('ImportDataWizard BONOS: creado bono "%s" para %s', vals.get('name'), cedula)

            except Exception as e:
                err_count += 1
                errors.append({
                    'hoja': 'BONOS', 'fila': row_num, 'cedula': cedula,
                    'nombre': emp.name, 'error': str(e),
                    'traceback': traceback.format_exc(),
                    'vals': {k: str(val)[:120] for k, val in vals.items()},
                })
                _logger.warning('ImportDataWizard BONOS fila %s: %s', row_num, e)

        return created, err_count

    # ==========================================================================
    # REPORTE DE ERRORES EN EXCEL
    # ==========================================================================

    def _build_error_report(self, errors, counters):
        wb = Workbook()

        DARK  = '1F3864'
        GREEN = '375623'
        RED   = 'C00000'
        AMBER = 'BF8F00'
        LGRAY = 'F2F2F2'
        ORANGE= 'F4B942'
        WHITE = 'FFFFFF'
        BLUE  = '2E75B6'

        def fill(c):  return PatternFill('solid', fgColor=c)
        def font(bold=False, color='000000', size=10, italic=False):
            return Font(bold=bold, color=color, size=size, italic=italic, name='Arial')
        def bdr():
            s = Side(style='thin', color='BDD7EE')
            return Border(left=s, right=s, top=s, bottom=s)
        def center(): return Alignment(horizontal='center', vertical='center', wrap_text=True)
        def left(wrap=True): return Alignment(horizontal='left', vertical='top', wrap_text=wrap)

        total_errors = sum(e for e in [
            counters['emp_errors'], counters['loan_errors'], counters['pen_errors'],
            counters['ben_errors'], counters['dis_errors'], counters['vac_errors'],
            counters['ot_errors'],
            # FIX-O4: faltaban embargos y bonos -- el resumen subestimaba errores
            counters.get('emb_errors', 0), counters.get('bon_errors', 0),
        ] if e)
        total_ok = sum(e for e in [
            counters['emp_created'], counters['loan_created'], counters['pen_created'],
            counters['ben_created'], counters['dis_created'], counters['vac_created'],
            counters['ot_created'],
            # FIX-O4: faltaban embargos y bonos -- el resumen subestimaba importaciones
            counters.get('emb_created', 0), counters.get('bon_created', 0),
        ] if e)

        # ======================================================================
        # HOJA 1 -- RESUMEN
        # ======================================================================
        ws1 = wb.active
        ws1.title = ' Resumen'
        ws1.sheet_view.showGridLines = False

        # Cabecera
        ws1.merge_cells('A1:G1')
        c = ws1['A1']
        c.value = f'REPORTE DE IMPORTACION -- {self.company_id.name}'
        c.font = font(bold=True, color=WHITE, size=14)
        c.fill = fill(DARK)
        c.alignment = center()
        ws1.row_dimensions[1].height = 34

        ws1.merge_cells('A2:G2')
        c = ws1['A2']
        c.value = (f'Archivo: {self.excel_filename or "--"}   |   '
                   f'Procesado: {datetime.now().strftime("%d/%m/%Y %H:%M")}   |   '
                   f'Total creados: {total_ok}   |   Total errores: {total_errors}')
        c.font = font(italic=True, color=WHITE, size=9)
        c.fill = fill(BLUE)
        c.alignment = center()
        ws1.row_dimensions[2].height = 16

        # Resultado global
        ws1.row_dimensions[3].height = 8
        ws1.merge_cells('A4:G4')
        estado_txt = 'OK IMPORTACION COMPLETADA SIN ERRORES' if not total_errors else f'WARN  IMPORTACION CON {total_errors} ERROR(ES) -- Ver hoja "Detalle Errores"'
        c = ws1['A4']
        c.value = estado_txt
        c.font = font(bold=True, color=WHITE, size=11)
        c.fill = fill('375623' if not total_errors else RED)
        c.alignment = center()
        ws1.row_dimensions[4].height = 26

        # Tabla resumen por hoja
        ws1.row_dimensions[5].height = 8
        hdrs_res = ['Hoja', 'Registros Creados OK', 'Omitidos ', 'Errores ERR', 'Total Procesados']
        for ci, h in enumerate(hdrs_res, 1):
            c = ws1.cell(6, ci, value=h)
            c.font = font(bold=True, color=WHITE, size=10)
            c.fill = fill(BLUE)
            c.border = bdr()
            c.alignment = center()
        ws1.row_dimensions[6].height = 22

        summary_data = [
            (' Empleados',      counters['emp_created'],  counters['emp_skipped'],  counters['emp_errors']),
            (' Prestamos',      counters['loan_created'], 0,                        counters['loan_errors']),
            (' Pensiones',       counters['pen_created'],  0,                        counters['pen_errors']),
            (' Otros Descuentos',counters['ben_created'],  0,                        counters['ben_errors']),
            (' Incapacidades',   counters['dis_created'],  0,                        counters['dis_errors']),
            (' Vacaciones',      counters['vac_created'],  0,                        counters['vac_errors']),
            (' Horas Extras',    counters['ot_created'],   0,                        counters['ot_errors']),
            (' Embargos',        counters.get('emb_created', 0), 0,                 counters.get('emb_errors', 0)),
            (' Bonos',           counters.get('bon_created', 0), 0,                 counters.get('bon_errors', 0)),
        ]

        for i, (hoja, created, skipped, errs) in enumerate(summary_data, start=7):
            total = created + skipped + errs
            row_data = [hoja, created, skipped, errs, total]
            for ci, val in enumerate(row_data, 1):
                c = ws1.cell(i, ci, value=val)
                c.border = bdr()
                c.alignment = center() if ci > 1 else left(wrap=False)
                if ci == 1:
                    c.fill = fill('EBF3FB')
                    c.font = font(bold=True, size=10)
                elif ci == 2:
                    c.fill = fill('E2EFDA' if created else WHITE)
                    c.font = font(bold=bool(created), color=GREEN if created else '888888', size=10)
                elif ci == 3:
                    c.fill = fill('FFF9E6' if skipped else WHITE)
                    c.font = font(size=10, color=AMBER if skipped else '888888')
                elif ci == 4:
                    c.fill = fill('FCE4D6' if errs else WHITE)
                    c.font = font(bold=bool(errs), color=RED if errs else '888888', size=10)
                else:
                    c.fill = fill(LGRAY)
                    c.font = font(size=10)
            ws1.row_dimensions[i].height = 18

        # Totales
        tot_row = 7 + len(summary_data)
        tot_data = ['TOTAL', total_ok,
                    counters['emp_skipped'],
                    total_errors,
                    total_ok + counters['emp_skipped'] + total_errors]
        for ci, val in enumerate(tot_data, 1):
            c = ws1.cell(tot_row, ci, value=val)
            c.font = font(bold=True, size=10, color=WHITE)
            c.fill = fill(DARK)
            c.border = bdr()
            c.alignment = center() if ci > 1 else left(wrap=False)
        ws1.row_dimensions[tot_row].height = 20

        # Anchos hoja 1
        for col, w in [(1,26),(2,22),(3,16),(4,14),(5,18)]:
            ws1.column_dimensions[get_column_letter(col)].width = w

        # ======================================================================
        # HOJA 2 -- DETALLE DE ERRORES
        # ======================================================================
        ws2 = wb.create_sheet('ERR Detalle Errores')
        ws2.sheet_view.showGridLines = False

        ws2.merge_cells('A1:H1')
        c = ws2['A1']
        c.value = f'DETALLE DE ERRORES -- {self.company_id.name}  |  {datetime.now().strftime("%d/%m/%Y %H:%M")}'
        c.font = font(bold=True, color=WHITE, size=12)
        c.fill = fill(RED)
        c.alignment = center()
        ws2.row_dimensions[1].height = 28

        if not errors:
            ws2.merge_cells('A2:H2')
            c = ws2['A2']
            c.value = 'OK No hubo errores en esta importacion'
            c.font = font(bold=True, color=GREEN, size=12)
            c.fill = fill('E2EFDA')
            c.alignment = center()
            ws2.row_dimensions[2].height = 28
        else:
            hdrs2 = ['#', 'Hoja', 'Fila Excel', 'Cedula', 'Nombre', 'Tipo de Error', 'Mensaje de Error', 'Accion Sugerida']
            for ci, h in enumerate(hdrs2, 1):
                c = ws2.cell(2, ci, value=h)
                c.font = font(bold=True, color=WHITE, size=9)
                c.fill = fill(DARK)
                c.border = bdr()
                c.alignment = center()
            ws2.row_dimensions[2].height = 20

            def _classify_error(err_msg):
                """Clasifica el error y sugiere accion correctiva."""
                msg = str(err_msg).lower()
                if 'not found' in msg or 'no encontrado' in msg:
                    return 'Empleado no existe', 'Importe primero la hoja EMPLEADOS antes de importar otras hojas'
                if 'wrong value' in msg or 'invalid value' in msg:
                    campo = ''
                    import re
                    m = re.search(r"'([^']+)':\s*'([^']+)'", err_msg)
                    if m:
                        campo = m.group(1).split('.')[-1]
                        valor = m.group(2)
                        return f'Valor invalido en {campo}', f'El valor "{valor}" no es valido para el campo {campo}. Consulte la hoja CATALOGOS'
                    return 'Valor invalido', 'Verifique que el valor corresponda exactamente a los listados en la hoja CATALOGOS'
                if 'invalid field' in msg:
                    import re
                    m = re.search(r"'([^']+)'", err_msg)
                    campo = m.group(1) if m else ''
                    return f'Campo inexistente: {campo}', f'El campo {campo} no existe en esta version de Odoo. Contacte al administrador'
                if 'required' in msg or 'obligatorio' in msg or 'cannot be empty' in msg:
                    return 'Campo obligatorio vacio', 'Complete todos los campos marcados en amarillo en el machote'
                if 'unique' in msg or 'duplicate' in msg or 'ya existe' in msg:
                    return 'Registro duplicado', 'Este registro ya existe en Odoo. Verifique la cedula y elimine el duplicado'
                if 'constraint' in msg:
                    return 'Restriccion de BD', 'Violacion de regla de negocio. Revise los datos ingresados'
                if 'date' in msg or 'fecha' in msg:
                    return 'Error de fecha', 'Use el formato DD/MM/AAAA. Ejemplo: 01/03/2024'
                if 'float' in msg or 'int' in msg or 'numeric' in msg:
                    return 'Error numerico', 'Use solo numeros sin comas ni simbolos. Ejemplo: 750000'
                return 'Error inesperado', 'Revise el log tecnico en la hoja "Log Tecnico" para mas detalles'

            for num, err in enumerate(errors, start=1):
                r = 2 + num
                err_msg = err.get('error', '')
                tipo, accion = _classify_error(err_msg)
                row_data = [
                    num,
                    err.get('hoja', ''),
                    err.get('fila', ''),
                    err.get('cedula', ''),
                    err.get('nombre', ''),
                    tipo,
                    err_msg,
                    accion,
                ]
                is_odd = num % 2 == 0
                row_bg = 'FFF9F9' if is_odd else WHITE
                for ci, val in enumerate(row_data, 1):
                    c = ws2.cell(r, ci, value=val)
                    c.border = bdr()
                    c.alignment = left() if ci >= 6 else center()
                    if ci == 7:  # mensaje error
                        c.font = font(size=9, color=RED, bold=True)
                        c.fill = fill('FCE4D6')
                    elif ci == 8:  # accion
                        c.font = font(size=9, color='7B3F00', italic=True)
                        c.fill = fill('FFF2CC')
                    elif ci == 6:  # tipo
                        c.font = font(size=9, color=DARK, bold=True)
                        c.fill = fill('EBF3FB')
                    else:
                        c.font = font(size=9)
                        c.fill = fill(row_bg)
                ws2.row_dimensions[r].height = 40

        for col, w in [(1,5),(2,18),(3,10),(4,16),(5,22),(6,24),(7,48),(8,46)]:
            ws2.column_dimensions[get_column_letter(col)].width = w

        # ======================================================================
        # HOJA 3 -- LOG TECNICO
        # ======================================================================
        ws3 = wb.create_sheet(' Log Tecnico')
        ws3.sheet_view.showGridLines = False

        ws3.merge_cells('A1:C1')
        c = ws3['A1']
        c.value = f'LOG TECNICO -- {self.company_id.name}  |  {datetime.now().strftime("%d/%m/%Y %H:%M")}'
        c.font = font(bold=True, color=WHITE, size=11)
        c.fill = fill('4A4A4A')
        c.alignment = center()
        ws3.row_dimensions[1].height = 26

        ws3.merge_cells('A2:C2')
        c = ws3['A2']
        c.value = 'Este log contiene el traceback completo y los valores enviados en cada error. Comparta esta hoja con soporte tecnico.'
        c.font = font(italic=True, size=9, color='555555')
        c.fill = fill('F5F5F5')
        c.alignment = left()
        ws3.row_dimensions[2].height = 16

        log_row = 4
        if not errors:
            ws3.merge_cells('A3:C3')
            c = ws3['A3']
            c.value = 'OK Sin errores -- no hay log tecnico que mostrar'
            c.font = font(bold=True, color=GREEN, size=10)
            c.fill = fill('E2EFDA')
            c.alignment = center()
        else:
            for num, err in enumerate(errors, start=1):
                # Bloque por error
                ws3.merge_cells(f'A{log_row}:C{log_row}')
                c = ws3.cell(log_row, 1,
                    value=f'ERROR #{num} -- Hoja: {err.get("hoja","")}  |  Fila: {err.get("fila","")}  |  Cedula: {err.get("cedula","")}  |  Nombre: {err.get("nombre","")}')
                c.font = font(bold=True, color=WHITE, size=10)
                c.fill = fill(RED if num % 2 else '8B0000')
                c.alignment = left(wrap=False)
                ws3.row_dimensions[log_row].height = 20
                log_row += 1

                # Mensaje de error
                ws3.cell(log_row, 1, value='MENSAJE:').font = font(bold=True, size=9, color=DARK)
                ws3.cell(log_row, 1).fill = fill('EBF3FB')
                ws3.merge_cells(f'B{log_row}:C{log_row}')
                c = ws3.cell(log_row, 2, value=err.get('error', ''))
                c.font = font(size=9, color=RED, bold=True)
                c.fill = fill('FCE4D6')
                c.alignment = left()
                ws3.row_dimensions[log_row].height = 30
                log_row += 1

                # Valores enviados
                vals = err.get('vals', {})
                if vals:
                    ws3.cell(log_row, 1, value='VALORES\nENVIADOS:').font = font(bold=True, size=9, color=DARK)
                    ws3.cell(log_row, 1).fill = fill('EBF3FB')
                    ws3.cell(log_row, 1).alignment = center()
                    ws3.merge_cells(f'B{log_row}:C{log_row}')
                    vals_txt = '\n'.join(f'  {k}: {v}' for k, v in vals.items() if v and v != 'False')
                    c = ws3.cell(log_row, 2, value=vals_txt or '(sin valores)')
                    c.font = font(size=8, color='333333')
                    c.fill = fill('F8F8F8')
                    c.alignment = left()
                    ws3.row_dimensions[log_row].height = max(14 * max(1, len(vals_txt.split('\n'))), 30)
                    log_row += 1

                # Traceback
                tb = err.get('traceback', '')
                if tb and tb.strip() and 'NoneType' not in tb:
                    ws3.cell(log_row, 1, value='TRACEBACK:').font = font(bold=True, size=9, color=DARK)
                    ws3.cell(log_row, 1).fill = fill('EBF3FB')
                    ws3.cell(log_row, 1).alignment = center()
                    ws3.merge_cells(f'B{log_row}:C{log_row}')
                    c = ws3.cell(log_row, 2, value=tb.strip())
                    c.font = Font(name='Courier New', size=8, color='444444')
                    c.fill = fill('1E1E1E' if False else 'F0F0F0')
                    c.alignment = left()
                    lines = tb.count('\n')
                    ws3.row_dimensions[log_row].height = max(14 * min(lines, 20), 40)
                    log_row += 1

                # Separador
                log_row += 1

        ws3.column_dimensions['A'].width = 16
        ws3.column_dimensions['B'].width = 60
        ws3.column_dimensions['C'].width = 60

        # -- Serializar --------------------------------------------------------
        buf = io.BytesIO()
        wb.save(buf)
        return base64.b64encode(buf.getvalue())

    # ==========================================================================
    # ACCION PRINCIPAL
    # ==========================================================================

    def action_import(self):
        self.ensure_one()
        if not self.excel_file:
            raise UserError('Debe cargar el archivo Excel.')

        wb     = self._get_wb()
        errors = []

        # -- Procesar hojas ----------------------------------------------------
        emp_c = emp_s = emp_e = 0
        loan_c = loan_e = 0
        pen_c = pen_e = 0
        ben_c = ben_e = 0
        dis_c = dis_e = 0
        vac_c = vac_e = 0
        ot_c  = ot_e  = 0
        emb_c = emb_e = 0
        bon_c = bon_e = 0

        if self.import_employees:
            emp_c, emp_s, emp_e = self._process_employees(wb, errors)
        if self.import_loans:
            loan_c, loan_e = self._process_loans(wb, errors)
        if self.import_pension:
            pen_c, pen_e = self._process_pension(wb, errors)
        if self.import_benefits:
            ben_c, ben_e = self._process_benefits(wb, errors)
        if self.import_disabilities:
            dis_c, dis_e = self._process_disabilities(wb, errors)
        if self.import_vacations:
            vac_c, vac_e = self._process_vacations(wb, errors)
        if self.import_overtime:
            ot_c, ot_e = self._process_overtime(wb, errors)
        if self.import_embargos:
            emb_c, emb_e = self._process_embargos(wb, errors)
        if self.import_bonos:
            bon_c, bon_e = self._process_bonos(wb, errors)

        total_err = emp_e + loan_e + pen_e + ben_e + dis_e + vac_e + ot_e + emb_e + bon_e

        counters = {
            'emp_created': emp_c, 'emp_skipped': emp_s, 'emp_errors': emp_e,
            'loan_created': loan_c, 'loan_errors': loan_e,
            'pen_created':  pen_c,  'pen_errors':  pen_e,
            'ben_created':  ben_c,  'ben_errors':  ben_e,
            'dis_created':  dis_c,  'dis_errors':  dis_e,
            'vac_created':  vac_c,  'vac_errors':  vac_e,
            'ot_created':   ot_c,   'ot_errors':   ot_e,
            'emb_created':  emb_c,  'emb_errors':  emb_e,
            'bon_created':  bon_c,  'bon_errors':  bon_e,
        }

        # -- Resumen texto -----------------------------------------------------
        summary = (
            f"== IMPORTACION COMPLETADA ==\n\n"
            f" Empleados:        {emp_c} creados  |  {emp_s} omitidos (ya existian)  |  {emp_e} errores\n"
            f" Prestamos:        {loan_c} creados  |  {loan_e} errores\n"
            f" Pensiones:         {pen_c} creadas  |  {pen_e} errores\n"
            f" Otros Descuentos:  {ben_c} creados  |  {ben_e} errores\n"
            f" Incapacidades:    {dis_c} creadas  |  {dis_e} errores\n"
            f" Vacaciones:       {vac_c} procesadas  |  {vac_e} errores\n"
            f" Horas Extras:     {ot_c} creadas  |  {ot_e} errores\n"
            f" Embargos:         {emb_c} creados  |  {emb_e} errores\n"
            f" Bonos:            {bon_c} creados  |  {bon_e} errores\n"
            f"\n{'WARN  Hay errores -- descargue el reporte para ver el detalle.' if total_err else 'OK Sin errores.'}"
        )

        # -- Generar reporte Excel ---------------------------------------------
        report_data = self._build_error_report(errors, counters)
        fname = f'Reporte_Importacion_{self.company_id.name}_{date.today()}.xlsx'

        self.write({
            'state':          'done',
            'emp_created':    emp_c, 'emp_skipped':  emp_s, 'emp_errors':  emp_e,
            'loan_created':   loan_c,
            'pen_created':    pen_c,
            'ben_created':    ben_c,
            'dis_created':    dis_c,
            'vac_created':    vac_c,
            'ot_created':     ot_c,
            'emb_created':    emb_c,
            'bon_created':    bon_c,
            'total_errors':   total_err,
            'result_summary': summary,
            'report_file':    report_data,
            'report_name':    fname,
        })

        # -- Reabrir wizard para mostrar resultados ----------------------------
        return {
            'type':    'ir.actions.act_window',
            'res_model': self._name,
            'res_id':  self.id,
            'view_mode': 'form',
            'view_id': self.env.ref('planilla_cr.view_import_data_wizard_form').id,
            'target': 'new',
        }

    def action_delete_sample(self):
        """Elimina el EMPLEADO PRUEBA y todos sus registros relacionados."""
        self.ensure_one()
        emp = self.env['hr.employee'].search([
            ('identification_id', '=', self._SAMPLE_CEDULA),
            ('company_id', '=', self.company_id.id),
        ], limit=1)

        if not emp:
            raise UserError('No se encontro ningun empleado de prueba (cedula 1-0000-0001) '
                            'en esta empresa. Es posible que ya haya sido eliminado.')

        deleted = []

        # Eliminar registros relacionados antes del empleado
        related = [
            ('planilla.employee.loan',      [('employee_id', '=', emp.id)], 'prestamos'),
            ('planilla.pension.alimentaria',[('employee_id', '=', emp.id)], 'pensiones'),
            ('planilla.recurring.benefit',  [('employee_id', '=', emp.id)], 'beneficios'),
            ('planilla.disability',         [('employee_id', '=', emp.id)], 'incapacidades'),
            ('planilla.vacation.payment',   [('employee_id', '=', emp.id)], 'vacaciones'),
            ('planilla.overtime',           [('employee_id', '=', emp.id)], 'horas extras'),
            ('planilla.embargo',            [('employee_id', '=', emp.id)], 'embargos'),
            ('planilla.bono',               [('employee_id', '=', emp.id)], 'bonos'),
            ('planilla.payslip.cr',         [('employee_id', '=', emp.id)], 'boletas'),
            ('planilla.salary.history',     [('employee_id', '=', emp.id)], 'historial salarial'),
            ('planilla.termination',        [('employee_id', '=', emp.id)], 'liquidaciones'),
        ]
        for model, domain, label in related:
            try:
                recs = self.env[model].search(domain)
                if recs:
                    recs.unlink()
                    deleted.append(f'{len(recs)} {label}')
            except Exception:
                pass  # modelo puede no existir en esta instalacion

        # Eliminar el empleado
        emp_name = emp.name
        emp.unlink()
        deleted_txt = ', '.join(deleted) if deleted else 'sin registros adicionales'

        # Reabrir el wizard con el resultado
        self.write({'sample_exists': False})
        return {
            'type':    'ir.actions.act_window',
            'res_model': self._name,
            'res_id':  self.id,
            'view_mode': 'form',
            'view_id': self.env.ref('planilla_cr.view_import_data_wizard_form').id,
            'target': 'new',
            'context': {
                'default_delete_msg': f'OK Empleado "{emp_name}" eliminado correctamente. '
                                      f'Registros eliminados: {deleted_txt}.'
            },
        }
