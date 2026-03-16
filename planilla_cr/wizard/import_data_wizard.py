"""
planilla.import.data.wizard
Importación masiva desde el machote Excel generado por import_template_wizard.
Estrategia: si el empleado ya existe (por identification_id) → se salta.
Al finalizar: resumen en pantalla + Excel de errores descargable.
"""
from odoo import models, fields, api
from odoo.exceptions import UserError
import base64, io, logging, traceback
from datetime import date, datetime

_logger = logging.getLogger(__name__)

try:
    from openpyxl import load_workbook, Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    OPENPYXL_OK = True
except ImportError:
    OPENPYXL_OK = False

# ══════════════════════════════════════════════════════════════════════════════
# TABLAS DE TRADUCCIÓN  (valor amigable del Excel → valor técnico del modelo)
# ══════════════════════════════════════════════════════════════════════════════

INS_RISK = {
    'i': 'I', 'ii': 'II', 'iii': 'III', 'iv': 'IV', 'v': 'V',
    'I': 'I', 'II': 'II', 'III': 'III', 'IV': 'IV', 'V': 'V',
    '1': 'I', '2': 'II', '3': 'III', '4': 'IV', '5': 'V',
}

INS_WORKDAY = {
    # Valores del dropdown (español legible)
    'ordinaria': '01', 'diurna': '01', '01': '01',
    'extraordinaria': '02', '02': '02',
    'mixta': '03', '03': '03',
    'tiempo parcial': '04', 'medio tiempo': '04', '04': '04',
    'por horas': '05', '05': '05',
    'ocasional': '06', '06': '06',
}

INS_NATIONALITY = {
    'cr': 'CR', 'costarricense': 'CR', 'costa rica': 'CR',
    'ni': 'NI', 'nicaraguense': 'NI', 'nicaragüense': 'NI',
    'co': 'CO', 'colombiano': 'CO', 'colombiana': 'CO', 'colombiano/a': 'CO',
    'us': 'US', 'estadounidense': 'US', 'americano': 'US',
    'hn': 'HN', 'hondureno': 'HN', 'hondureño': 'HN', 'hondureño/a': 'HN',
    'sv': 'SV', 'salvadoreno': 'SV', 'salvadoreño': 'SV', 'salvadoreño/a': 'SV',
    'gt': 'GT', 'guatemalteco': 'GT', 'guatemalteca': 'GT', 'guatemalteco/a': 'GT',
    'pa': 'PA', 'panameno': 'PA', 'panameño': 'PA', 'panameño/a': 'PA',
    'mx': 'MX', 'mexicano': 'MX', 'mexicana': 'MX', 'mexicano/a': 'MX',
    've': 'VE', 'venezolano': 'VE', 'venezolana': 'VE', 'venezolano/a': 'VE',
    'pe': 'PE', 'peruano': 'PE', 'peruana': 'PE', 'peruano/a': 'PE',
    'ec': 'EC', 'ecuatoriano': 'EC', 'ecuatoriana': 'EC', 'ecuatoriano/a': 'EC',
    'ot': 'OT', 'otro': 'OT', 'otra': 'OT', 'other': 'OT', 'otra': 'OT',
}

ACCOUNT_TYPE = {
    'cuenta corriente': 'corriente', 'corriente': 'corriente', 'iban': 'corriente',
    'cuenta de ahorros': 'ahorros', 'ahorros': 'ahorros',
    'sinpe movil': 'sinpe', 'sinpe móvil': 'sinpe', 'sinpe': 'sinpe',
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
    'union libre': '05', 'unión libre': '05', '05': '05',
    'separado/a': '06', 'separado': '06', 'separada': '06', '06': '06',
}

INS_ID_TYPE = {
    'cedula nacional': '01', 'cédula nacional': '01',
    'cedula': '01', 'cédula': '01', '01': '01',
    'residencia / dimex': '02', 'residencia': '02', 'dimex': '02', '02': '02',
    'permiso de trabajo': '03', 'permiso': '03', '03': '03',
    'pasaporte': '04', '04': '04',
    'indocumentado': '05', '05': '05',
}

DISABILITY_TYPE = {
    'enfermedad común (ccss)': 'ccss', 'enfermedad comun (ccss)': 'ccss',
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
    'deducción / descuento': 'deduction', 'deduccion / descuento': 'deduction',
    'deduccion': 'deduction', 'deducción': 'deduction',
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
    'día feriado': 'holiday', 'dia feriado': 'holiday', 'feriado': 'holiday',
    'holiday': 'holiday',
}

BANK = {
    'bncr': 'BNCR', 'banco nacional': 'BNCR', 'nacional': 'BNCR',
    'bcr': 'BCR', 'banco de costa rica': 'BCR',
    'bp': 'BP', 'bpop': 'BP', 'banco popular': 'BP', 'popular': 'BP',
    'bac': 'BAC', 'bac san jose': 'BAC', 'bac san josé': 'BAC',
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
    'préstamo de empresa': 'loan', 'prestamo de empresa': 'loan',
    'loan': 'loan', 'prestamo': 'loan', 'préstamo': 'loan',
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
    'cónyuge': 'conyuge', 'conyuge': 'conyuge', 'compañero': 'conyuge',
    'compañera': 'conyuge', 'conviviente': 'conyuge',
    'padre': 'padre', 'madre': 'madre',
    'otro': 'otro', 'otra': 'otro',
}

BOOL_MAP = {'si': True, 'sí': True, 'yes': True, '1': True, 'true': True, 'x': True}

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


# ══════════════════════════════════════════════════════════════════════════════
# MODELO WIZARD
# ══════════════════════════════════════════════════════════════════════════════

class ImportDataWizard(models.TransientModel):
    _name        = 'planilla.import.data.wizard'
    _description = 'Importación Masiva de Empleados desde Excel'

    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company)

    excel_file = fields.Binary(
        string='Archivo Excel (Machote)', required=True,
        help='Cargue el machote Excel completado por el cliente.')
    excel_filename = fields.Char(string='Nombre de archivo')

    # Hojas a procesar
    import_employees    = fields.Boolean('👤  Empleados',                      default=True)
    import_loans        = fields.Boolean('💰  Préstamos',                      default=True)
    import_pension      = fields.Boolean('👨‍👧  Pensiones Alimentarias',         default=True)
    import_benefits     = fields.Boolean('➕  Beneficios / Deducciones',       default=True)
    import_disabilities = fields.Boolean('🏥  Incapacidades',                  default=True)
    import_vacations    = fields.Boolean('🏖️  Vacaciones',                     default=True)
    import_overtime     = fields.Boolean('⏱️  Horas Extras',                   default=True)
    import_sample_data  = fields.Boolean(
        '🧪  Importar fila de prueba (cédula 1-0000-0001)',
        default=False,
        help='Active solo cuando quiera verificar que la importación funciona. '
             'Desactivado por defecto para que la fila naranja se ignore automáticamente.'
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

    # Cédula reservada para la fila de prueba (debe coincidir con import_template_wizard)
    _SAMPLE_CEDULA = '1-0000-0001'

    # Resultados (readonly, visibles después de procesar)
    state = fields.Selection([
        ('draft',  'Pendiente'),
        ('done',   'Procesado'),
    ], default='draft')

    # Contadores resumen
    emp_created  = fields.Integer('Empleados creados',     readonly=True)
    emp_skipped  = fields.Integer('Empleados existentes (omitidos)', readonly=True)
    emp_errors   = fields.Integer('Empleados con errores', readonly=True)
    loan_created = fields.Integer('Préstamos creados',     readonly=True)
    pen_created  = fields.Integer('Pensiones creadas',     readonly=True)
    ben_created  = fields.Integer('Beneficios creados',    readonly=True)
    dis_created  = fields.Integer('Incapacidades creadas', readonly=True)
    vac_created  = fields.Integer('Saldos de vacaciones procesados', readonly=True)
    ot_created   = fields.Integer('Horas extras creadas',  readonly=True)
    total_errors = fields.Integer('Total errores',         readonly=True)

    result_summary = fields.Text('Resumen',   readonly=True)
    report_file    = fields.Binary('Reporte de Errores (Excel)', readonly=True)
    report_name    = fields.Char(default='Reporte_Importacion.xlsx')

    # ── helpers internos ──────────────────────────────────────────────────────

    def _get_wb(self):
        if not OPENPYXL_OK:
            raise UserError('La librería openpyxl no está instalada en el servidor.')
        raw = base64.b64decode(self.excel_file)
        return load_workbook(io.BytesIO(raw), data_only=True)

    def _sheet_rows(self, wb, sheet_names):
        """Retorna (headers_dict, filas) de la primera hoja encontrada."""
        _EXAMPLE_CEDULA = '1-2345-6789'   # fila verde de ejemplo — siempre se salta
        for name in sheet_names:
            for sn in wb.sheetnames:
                if name.lower() in sn.lower():
                    ws = wb[sn]
                    rows = list(ws.iter_rows(values_only=True))
                    if len(rows) < 2:
                        return {}, []

                    # ── Detectar fila de encabezados ──────────────────────
                    # Buscamos la fila donde alguna celda sea EXACTAMENTE una
                    # palabra clave de encabezado (no un título largo que las
                    # contenga como substring). Esto distingue la fila de
                    # encabezados de columna de la fila de título o secciones.
                    EXACT_KEYS = ('nombre completo', 'cédula / identificación',
                                  'cédula empleado', 'número de expediente',
                                  'concepto', 'tipo de incapacidad',
                                  'días acumulados', 'tipo de hora extra',
                                  'tipo de préstamo', 'cédula empleado')
                    PARTIAL_KEYS = ('cédula', 'cedula')
                    header_row = None
                    for i, r in enumerate(rows):
                        cells = [str(c).strip().lower() for c in r
                                 if c is not None and str(c).strip()]
                        # Coincidencia exacta con alguna clave conocida
                        if any(cell in EXACT_KEYS for cell in cells):
                            header_row = i
                            break
                        # O celda que sea exactamente 'cédula' o 'cedula'
                        if any(cell in PARTIAL_KEYS for cell in cells):
                            header_row = i
                            break

                    if header_row is None:
                        return {}, []

                    hdrs = {str(c).strip(): ci
                            for ci, c in enumerate(rows[header_row]) if c}

                    # ── Datos: todo lo que viene después del encabezado ───
                    data_rows = rows[header_row + 1:]

                    # Saltar la fila de ejemplo verde (cédula 1-2345-6789)
                    # buscando ese valor en cualquier columna de cada fila
                    data_rows = [
                        r for r in data_rows
                        if not any(
                            str(c).strip() == _EXAMPLE_CEDULA
                            for c in r if c is not None
                        )
                    ]

                    # Filtrar filas completamente vacías
                    data_rows = [r for r in data_rows
                                 if any(c for c in r
                                        if c is not None and str(c).strip())]
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
        """Busca un registro por nombre (case-insensitive)."""
        if not name_val:
            return None
        domain = [(field, 'ilike', str(name_val).strip())]
        if extra_domain:
            domain += extra_domain
        return self.env[model].search(domain, limit=1) or None

    # ══════════════════════════════════════════════════════════════════════════
    # PROCESADORES POR HOJA
    # ══════════════════════════════════════════════════════════════════════════

    def _is_sample(self, cedula):
        """Retorna True si la cédula corresponde a la fila de prueba."""
        return str(cedula).strip() == self._SAMPLE_CEDULA

    def _process_employees(self, wb, errors):
        created = skipped = err_count = 0
        hdrs, rows = self._sheet_rows(wb, ['EMPLEADO', 'EMPLOYEE'])
        if not rows:
            return 0, 0, 0

        for row_num, row in enumerate(rows, start=1):
            v = lambda *cols: self._v(row, hdrs, *cols)
            cedula = str(v('Cédula', 'Cedula', 'Identificación', 'Identificacion') or '').strip()
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
                    cal_name     = v('Frecuencia', 'Calendario')
                    etype_name   = v('Tipo de Empleado')
                    estatus_name = v('Estado del Empleado', 'Estado')

                    dept    = self._find_m2o('hr.department', dept_name,
                                extra_domain=[('company_id', '=', company.id)])
                    branch  = self._find_m2o('planilla.branch', branch_name,
                                extra_domain=[('company_id', '=', company.id)])
                    job     = self._find_m2o('hr.job', job_name,
                                extra_domain=[('company_id', '=', company.id)])
                    sched   = self._find_m2o('planilla.schedule.type', sched_name)
                    cal     = self._find_m2o('planilla.calendar', cal_name,
                                extra_domain=[('company_id', '=', company.id)])
                    etype   = self._find_m2o('planilla.employee.type', etype_name)
                    estatus = self._find_m2o('planilla.employee.status', estatus_name)

                    # Sub departamento — buscar dentro del dpto padre si se encontró
                    subdept = None
                    if subdept_name:
                        subdept_domain = [('company_id', '=', company.id)]
                        if dept:
                            subdept_domain.append(('parent_id', '=', dept.id))
                        subdept = self._find_m2o('hr.department', subdept_name,
                                    extra_domain=subdept_domain)

                    # Si no se encontró calendario por nombre, buscar por frecuencia
                    if not cal:
                        freq_raw = _normalize(v('Frecuencia', 'Calendario', 'Frecuencia de Pago') or '')
                        freq_val = FREQUENCY.get(freq_raw)
                        if freq_val:
                            cal = self.env['planilla.calendar'].search([
                                ('frequency', '=', freq_val),
                                ('company_id', '=', company.id),
                            ], limit=1) or None

                    # Identificación type
                    id_type_raw  = _normalize(v('Tipo de Identificación', 'Tipo Identificacion') or '')
                    id_type_code = INS_ID_TYPE.get(id_type_raw, '01')
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
                        'payroll_calculation_method': _map(CALC_METHOD, v('Método', 'Metodo', 'Método de Cálculo')) or 'fixed',
                        'ccss_number':                str(v('CCSS', 'Número CCSS', 'Numero CCSS') or '').strip() or False,
                        'ccss_insured':               _parse_bool(v('Asegurado CCSS', 'CCSS Asegurado')),
                        'bank_account_number':        str(v('Cuenta Bancaria', 'Cuenta') or '').strip() or False,
                        'bank_iban':                  str(v('IBAN') or '').strip() or False,
                        'sinpe_phone':                str(v('SINPE', 'Sinpe Móvil', 'Sinpe Movil') or '').strip() or False,
                        'bank_name':                  _map(BANK, v('Banco')) or False,
                        'bank_account_type':          _map(ACCOUNT_TYPE, v('Tipo de Cuenta Banco', 'Tipo de Cuenta')) or False,
                        # INS
                        'ins_include':               _parse_bool(v('Incluir INS', 'Incluir en INS')),
                        'ins_policy_number':         str(v('Póliza INS', 'Poliza INS', 'Número de Póliza') or '').strip() or False,
                        'ins_first_name':            str(v('Nombre INS') or '').strip() or False,
                        'ins_first_lastname':        str(v('Primer Apellido INS') or '').strip() or False,
                        'ins_second_lastname':       str(v('Segundo Apellido INS') or '').strip() or False,
                        'ins_risk_class':            _map(INS_RISK, v('Clase de Riesgo', 'Riesgo INS')) or False,
                        'ins_workday_type':          _map(INS_WORKDAY, v('Jornada INS', 'Tipo de Jornada INS', 'Tipo de Jornada')) or '01',
                        'ins_civil_status':          _map(INS_CIVIL, v('Estado Civil INS', 'Estado Civil')) or '01',
                        'ins_id_type':               id_type_code,
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

                    # INS occupation (código numérico de 4 dígitos)
                    ins_occ_raw = str(v('Ocupación INS', 'Ocupacion INS') or '').strip()
                    if ins_occ_raw:
                        vals['ins_occupation'] = ins_occ_raw

                    # Campos personales estándar de hr.employee — pueden no existir
                    # según la versión de Odoo o si están en hr.employee.private.
                    # Los agregamos solo si el campo existe en el modelo.
                    emp_fields = self.env['hr.employee']._fields
                    _personal = {
                        'gender':        _map(GENDER, v('Género', 'Genero')) or False,
                        'children':      _parse_int(v('Número de Dependientes', 'Dependientes')) or 0,
                        'private_street': str(v('Dirección', 'Direccion') or '').strip() or False,
                        'private_phone': str(v('Teléfono Personal', 'Telefono Personal') or '').strip() or False,
                        'notes':         str(v('Observaciones') or '').strip() or False,
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

                    self.env['hr.employee'].create(vals)
                    created += 1

            except Exception as e:
                err_count += 1
                errors.append({
                    'hoja': 'EMPLEADOS', 'fila': row_num,
                    'cedula': cedula, 'nombre': nombre,
                    'error': str(e),
                    'traceback': traceback.format_exc(),
                    'vals': {k: str(val)[:120] for k, val in (vals.items() if isinstance(locals().get('vals'), dict) else {}.items())},
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
            cedula = str(v('Cédula', 'Cedula') or '').strip()
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
                    installments = _parse_int(v('Número de Cuotas', 'Cuotas', 'Installments'))
                    date_granted = _parse_date(v('Fecha de Otorgamiento', 'Fecha Otorgamiento'))
                    date_first   = _parse_date(v('Fecha Primera Deducción', 'Primera Deduccion'))
                    state_raw    = _map(LOAN_STATE, v('Estado')) or 'approved'
                    loan_type    = _map(LOAN_TYPE, v('Tipo de Préstamo', 'Tipo')) or 'loan'
                    amount_paid  = _parse_float(v('Monto ya Pagado', 'Monto Pagado'))

                    if amount_total <= 0 or installments <= 0:
                        raise ValueError('Monto total e instalamentos deben ser > 0')

                    loan = self.env['planilla.employee.loan'].create({
                        'employee_id':         emp.id,
                        'loan_type':           loan_type,
                        'description':         str(v('Descripción', 'Descripcion', 'Motivo') or '').strip() or False,
                        'amount_total':        amount_total,
                        'installments':        installments,
                        'date_granted':        date_granted or date.today(),
                        'date_first_deduction': date_first or date.today(),
                        'state':               'approved',
                        'note':                str(v('Observaciones') or '').strip() or False,
                    })

                    # Generar cuotas automáticamente
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
                errors.append({
                    'hoja': 'PRESTAMOS', 'fila': row_num, 'cedula': cedula,
                    'nombre': emp.name, 'error': str(e),
                    'traceback': traceback.format_exc(),
                    'vals': {k: str(val)[:120] for k, val in (vals.items() if isinstance(locals().get('vals'), dict) else {}.items())},
                })
                _logger.warning('ImportDataWizard PRESTAMOS fila %s: %s', row_num, e)

        return created, err_count

    def _process_pension(self, wb, errors):
        created = err_count = 0
        hdrs, rows = self._sheet_rows(wb, ['PENSION', 'PENSIÓN'])
        if not rows:
            return 0, 0

        for row_num, row in enumerate(rows, start=1):
            v = lambda *cols: self._v(row, hdrs, *cols)
            cedula = str(v('Cédula', 'Cedula') or '').strip()
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
                    calc_type = _map(PENSION_CALC, v('Tipo de Cálculo', 'Tipo Calculo')) or 'fixed'
                    pct_raw   = _parse_float(v('Porcentaje'))
                    monto_raw = _parse_float(v('Monto Fijo', 'Monto'))

                    branch = self._find_m2o('planilla.branch', v('Sucursal'),
                                extra_domain=[('company_id', '=', self.company_id.id)])

                    vals = {
                        'employee_id':          emp.id,
                        'numero_expediente':    str(v('Expediente', 'Número de Expediente') or '').strip() or False,
                        'juzgado':              str(v('Juzgado') or '').strip() or False,
                        'fecha_resolucion':     _parse_date(v('Fecha de Resolución', 'Fecha Resolucion')),
                        'beneficiario_nombre':  str(v('Beneficiario', 'Nombre Beneficiario') or '').strip() or False,
                        'beneficiario_relacion': _map(PENSION_RELATION, v('Relación', 'Relacion')) or 'hijo',
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
                    'vals': {k: str(val)[:120] for k, val in (vals.items() if isinstance(locals().get('vals'), dict) else {}.items())},
                })
                _logger.warning('ImportDataWizard PENSION fila %s: %s', row_num, e)

        return created, err_count

    def _process_benefits(self, wb, errors):
        created = err_count = 0
        hdrs, rows = self._sheet_rows(wb, ['BENEFICIO', 'BENEFIT'])
        if not rows:
            return 0, 0

        for row_num, row in enumerate(rows, start=1):
            v = lambda *cols: self._v(row, hdrs, *cols)
            cedula = str(v('Cédula', 'Cedula') or '').strip()
            if not cedula:
                continue
            if self._is_sample(cedula) and not self.import_sample_data:
                continue

            emp = self._find_employee(cedula)
            if not emp:
                err_count += 1
                errors.append({
                    'hoja': 'BENEFICIOS', 'fila': row_num, 'cedula': cedula,
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

                    # Buscar código de deducción por código o por nombre
                    ded_code_raw = str(v('Código Deducción', 'Codigo', 'Código') or '').strip()
                    ded_code = None
                    if ded_code_raw:
                        ded_code = (
                            self.env['planilla.deduction.code'].search(
                                [('code', '=ilike', ded_code_raw)], limit=1) or
                            self.env['planilla.deduction.code'].search(
                                [('name', 'ilike', ded_code_raw)], limit=1)
                        ) or None

                    # deduction_code_id es required=True en el modelo.
                    # Si no se especificó, usar el primer código disponible del tipo correcto,
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
                        raise ValueError('No existe ningún Código de Deducción en Odoo. '
                                         'Cree al menos uno en Configuración → Códigos de Deducción')

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
                    'hoja': 'BENEFICIOS', 'fila': row_num, 'cedula': cedula,
                    'nombre': emp.name, 'error': str(e),
                    'traceback': traceback.format_exc(),
                    'vals': {k: str(val)[:120] for k, val in (vals.items() if isinstance(locals().get('vals'), dict) else {}.items())},
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
            cedula = str(v('Cédula', 'Cedula') or '').strip()
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
                        'employer_percentage':  _parse_float(v('% Patrono', 'Cargo Patrono')) or 40.0,
                        'certificate_number':   str(v('Número Certificado', 'Certificado') or '').strip() or False,
                        'diagnosis':            str(v('Diagnóstico', 'Diagnostico') or '').strip() or False,
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
                    'vals': {k: str(val)[:120] for k, val in (vals.items() if isinstance(locals().get('vals'), dict) else {}.items())},
                })
                _logger.warning('ImportDataWizard INCAPACIDADES fila %s: %s', row_num, e)

        return created, err_count

    def _process_vacations(self, wb, errors):
        """
        Registra los días tomados como registros de vacation.payment tipo 'disfrutadas'.
        Los días acumulados son computados automáticamente por entry_date.
        """
        created = err_count = 0
        hdrs, rows = self._sheet_rows(wb, ['VACACION', 'VACATION'])
        if not rows:
            return 0, 0

        for row_num, row in enumerate(rows, start=1):
            v = lambda *cols: self._v(row, hdrs, *cols)
            cedula = str(v('Cédula', 'Cedula') or '').strip()
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
                    days_taken = _parse_float(v('Días Tomados', 'Dias Tomados'))
                    cutoff     = _parse_date(v('Última Fecha', 'Fecha de Corte')) or date.today()
                    obs        = str(v('Observaciones', 'Período') or '').strip()

                    branch = self._find_m2o('planilla.branch', v('Sucursal'),
                                extra_domain=[('company_id', '=', self.company_id.id)])

                    # Solo crear registro si hay días tomados que registrar
                    if days_taken > 0:
                        days_int = int(days_taken)
                        vals = {
                            'employee_id':    emp.id,
                            'vacation_type':  'disfrutadas',
                            'date_start':     emp.entry_date or cutoff,
                            'date_end':       cutoff,
                            'state':          'paid',
                            'note':           obs or f'Saldo inicial importación — {days_int} días tomados',
                        }
                        if branch:
                            vals['branch_id'] = branch.id

                        self.env['planilla.vacation.payment'].create(vals)
                        created += 1

            except Exception as e:
                err_count += 1
                errors.append({
                    'hoja': 'VACACIONES', 'fila': row_num, 'cedula': cedula,
                    'nombre': emp.name, 'error': str(e),
                    'traceback': traceback.format_exc(),
                    'vals': {k: str(val)[:120] for k, val in (vals.items() if isinstance(locals().get('vals'), dict) else {}.items())},
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
            cedula = str(v('Cédula', 'Cedula') or '').strip()
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
                    'vals': {k: str(val)[:120] for k, val in (vals.items() if isinstance(locals().get('vals'), dict) else {}.items())},
                })
                _logger.warning('ImportDataWizard OVERTIME fila %s: %s', row_num, e)

        return created, err_count

    # ══════════════════════════════════════════════════════════════════════════
    # REPORTE DE ERRORES EN EXCEL
    # ══════════════════════════════════════════════════════════════════════════

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
            counters['ot_errors']] if e)
        total_ok = sum(e for e in [
            counters['emp_created'], counters['loan_created'], counters['pen_created'],
            counters['ben_created'], counters['dis_created'], counters['vac_created'],
            counters['ot_created']] if e)

        # ══════════════════════════════════════════════════════════════════════
        # HOJA 1 — RESUMEN
        # ══════════════════════════════════════════════════════════════════════
        ws1 = wb.active
        ws1.title = '📊 Resumen'
        ws1.sheet_view.showGridLines = False

        # Cabecera
        ws1.merge_cells('A1:G1')
        c = ws1['A1']
        c.value = f'REPORTE DE IMPORTACIÓN — {self.company_id.name}'
        c.font = font(bold=True, color=WHITE, size=14)
        c.fill = fill(DARK)
        c.alignment = center()
        ws1.row_dimensions[1].height = 34

        ws1.merge_cells('A2:G2')
        c = ws1['A2']
        c.value = (f'Archivo: {self.excel_filename or "—"}   |   '
                   f'Procesado: {datetime.now().strftime("%d/%m/%Y %H:%M")}   |   '
                   f'Total creados: {total_ok}   |   Total errores: {total_errors}')
        c.font = font(italic=True, color=WHITE, size=9)
        c.fill = fill(BLUE)
        c.alignment = center()
        ws1.row_dimensions[2].height = 16

        # Resultado global
        ws1.row_dimensions[3].height = 8
        ws1.merge_cells('A4:G4')
        estado_txt = '✅ IMPORTACIÓN COMPLETADA SIN ERRORES' if not total_errors else f'⚠️  IMPORTACIÓN CON {total_errors} ERROR(ES) — Ver hoja "Detalle Errores"'
        c = ws1['A4']
        c.value = estado_txt
        c.font = font(bold=True, color=WHITE, size=11)
        c.fill = fill('375623' if not total_errors else RED)
        c.alignment = center()
        ws1.row_dimensions[4].height = 26

        # Tabla resumen por hoja
        ws1.row_dimensions[5].height = 8
        hdrs_res = ['Hoja', 'Registros Creados ✅', 'Omitidos ⏭️', 'Errores ❌', 'Total Procesados']
        for ci, h in enumerate(hdrs_res, 1):
            c = ws1.cell(6, ci, value=h)
            c.font = font(bold=True, color=WHITE, size=10)
            c.fill = fill(BLUE)
            c.border = bdr()
            c.alignment = center()
        ws1.row_dimensions[6].height = 22

        summary_data = [
            ('👤 Empleados',      counters['emp_created'],  counters['emp_skipped'],  counters['emp_errors']),
            ('💰 Préstamos',      counters['loan_created'], 0,                        counters['loan_errors']),
            ('👨‍👧 Pensiones',       counters['pen_created'],  0,                        counters['pen_errors']),
            ('➕ Beneficios',      counters['ben_created'],  0,                        counters['ben_errors']),
            ('🏥 Incapacidades',   counters['dis_created'],  0,                        counters['dis_errors']),
            ('🏖️ Vacaciones',      counters['vac_created'],  0,                        counters['vac_errors']),
            ('⏱️ Horas Extras',    counters['ot_created'],   0,                        counters['ot_errors']),
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

        # ══════════════════════════════════════════════════════════════════════
        # HOJA 2 — DETALLE DE ERRORES
        # ══════════════════════════════════════════════════════════════════════
        ws2 = wb.create_sheet('❌ Detalle Errores')
        ws2.sheet_view.showGridLines = False

        ws2.merge_cells('A1:H1')
        c = ws2['A1']
        c.value = f'DETALLE DE ERRORES — {self.company_id.name}  |  {datetime.now().strftime("%d/%m/%Y %H:%M")}'
        c.font = font(bold=True, color=WHITE, size=12)
        c.fill = fill(RED)
        c.alignment = center()
        ws2.row_dimensions[1].height = 28

        if not errors:
            ws2.merge_cells('A2:H2')
            c = ws2['A2']
            c.value = '✅ No hubo errores en esta importación'
            c.font = font(bold=True, color=GREEN, size=12)
            c.fill = fill('E2EFDA')
            c.alignment = center()
            ws2.row_dimensions[2].height = 28
        else:
            hdrs2 = ['#', 'Hoja', 'Fila Excel', 'Cédula', 'Nombre', 'Tipo de Error', 'Mensaje de Error', 'Acción Sugerida']
            for ci, h in enumerate(hdrs2, 1):
                c = ws2.cell(2, ci, value=h)
                c.font = font(bold=True, color=WHITE, size=9)
                c.fill = fill(DARK)
                c.border = bdr()
                c.alignment = center()
            ws2.row_dimensions[2].height = 20

            def _classify_error(err_msg):
                """Clasifica el error y sugiere acción correctiva."""
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
                        return f'Valor inválido en {campo}', f'El valor "{valor}" no es válido para el campo {campo}. Consulte la hoja CATALOGOS'
                    return 'Valor inválido', 'Verifique que el valor corresponda exactamente a los listados en la hoja CATALOGOS'
                if 'invalid field' in msg:
                    import re
                    m = re.search(r"'([^']+)'", err_msg)
                    campo = m.group(1) if m else '?'
                    return f'Campo inexistente: {campo}', f'El campo {campo} no existe en esta versión de Odoo. Contacte al administrador'
                if 'required' in msg or 'obligatorio' in msg or 'cannot be empty' in msg:
                    return 'Campo obligatorio vacío', 'Complete todos los campos marcados en amarillo en el machote'
                if 'unique' in msg or 'duplicate' in msg or 'ya existe' in msg:
                    return 'Registro duplicado', 'Este registro ya existe en Odoo. Verifique la cédula y elimine el duplicado'
                if 'constraint' in msg:
                    return 'Restricción de BD', 'Violación de regla de negocio. Revise los datos ingresados'
                if 'date' in msg or 'fecha' in msg:
                    return 'Error de fecha', 'Use el formato DD/MM/AAAA. Ejemplo: 01/03/2024'
                if 'float' in msg or 'int' in msg or 'numeric' in msg:
                    return 'Error numérico', 'Use solo números sin comas ni símbolos. Ejemplo: 750000'
                return 'Error inesperado', 'Revise el log técnico en la hoja "Log Técnico" para más detalles'

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
                    elif ci == 8:  # acción
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

        # ══════════════════════════════════════════════════════════════════════
        # HOJA 3 — LOG TÉCNICO
        # ══════════════════════════════════════════════════════════════════════
        ws3 = wb.create_sheet('🔧 Log Técnico')
        ws3.sheet_view.showGridLines = False

        ws3.merge_cells('A1:C1')
        c = ws3['A1']
        c.value = f'LOG TÉCNICO — {self.company_id.name}  |  {datetime.now().strftime("%d/%m/%Y %H:%M")}'
        c.font = font(bold=True, color=WHITE, size=11)
        c.fill = fill('4A4A4A')
        c.alignment = center()
        ws3.row_dimensions[1].height = 26

        ws3.merge_cells('A2:C2')
        c = ws3['A2']
        c.value = 'Este log contiene el traceback completo y los valores enviados en cada error. Comparta esta hoja con soporte técnico.'
        c.font = font(italic=True, size=9, color='555555')
        c.fill = fill('F5F5F5')
        c.alignment = left()
        ws3.row_dimensions[2].height = 16

        log_row = 4
        if not errors:
            ws3.merge_cells('A3:C3')
            c = ws3['A3']
            c.value = '✅ Sin errores — no hay log técnico que mostrar'
            c.font = font(bold=True, color=GREEN, size=10)
            c.fill = fill('E2EFDA')
            c.alignment = center()
        else:
            for num, err in enumerate(errors, start=1):
                # Bloque por error
                ws3.merge_cells(f'A{log_row}:C{log_row}')
                c = ws3.cell(log_row, 1,
                    value=f'ERROR #{num} — Hoja: {err.get("hoja","")}  |  Fila: {err.get("fila","")}  |  Cédula: {err.get("cedula","")}  |  Nombre: {err.get("nombre","")}')
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

        # ── Serializar ────────────────────────────────────────────────────────
        buf = io.BytesIO()
        wb.save(buf)
        return base64.b64encode(buf.getvalue())

    # ══════════════════════════════════════════════════════════════════════════
    # ACCIÓN PRINCIPAL
    # ══════════════════════════════════════════════════════════════════════════

    def action_import(self):
        self.ensure_one()
        if not self.excel_file:
            raise UserError('Debe cargar el archivo Excel.')

        wb     = self._get_wb()
        errors = []

        # ── Procesar hojas ────────────────────────────────────────────────────
        emp_c = emp_s = emp_e = 0
        loan_c = loan_e = 0
        pen_c = pen_e = 0
        ben_c = ben_e = 0
        dis_c = dis_e = 0
        vac_c = vac_e = 0
        ot_c  = ot_e  = 0

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

        total_err = emp_e + loan_e + pen_e + ben_e + dis_e + vac_e + ot_e

        counters = {
            'emp_created': emp_c, 'emp_skipped': emp_s, 'emp_errors': emp_e,
            'loan_created': loan_c, 'loan_errors': loan_e,
            'pen_created':  pen_c,  'pen_errors':  pen_e,
            'ben_created':  ben_c,  'ben_errors':  ben_e,
            'dis_created':  dis_c,  'dis_errors':  dis_e,
            'vac_created':  vac_c,  'vac_errors':  vac_e,
            'ot_created':   ot_c,   'ot_errors':   ot_e,
        }

        # ── Resumen texto ─────────────────────────────────────────────────────
        summary = (
            f"══ IMPORTACIÓN COMPLETADA ══\n\n"
            f"👤 Empleados:        {emp_c} creados  |  {emp_s} omitidos (ya existían)  |  {emp_e} errores\n"
            f"💰 Préstamos:        {loan_c} creados  |  {loan_e} errores\n"
            f"👨‍👧 Pensiones:         {pen_c} creadas  |  {pen_e} errores\n"
            f"➕ Beneficios:        {ben_c} creados  |  {ben_e} errores\n"
            f"🏥 Incapacidades:    {dis_c} creadas  |  {dis_e} errores\n"
            f"🏖️ Vacaciones:       {vac_c} procesadas  |  {vac_e} errores\n"
            f"⏱️ Horas Extras:     {ot_c} creadas  |  {ot_e} errores\n"
            f"\n{'⚠️  Hay errores — descargue el reporte para ver el detalle.' if total_err else '✅ Sin errores.'}"
        )

        # ── Generar reporte Excel ─────────────────────────────────────────────
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
            'total_errors':   total_err,
            'result_summary': summary,
            'report_file':    report_data,
            'report_name':    fname,
        })

        # ── Reabrir wizard para mostrar resultados ────────────────────────────
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
            raise UserError('No se encontró ningún empleado de prueba (cédula 1-0000-0001) '
                            'en esta empresa. Es posible que ya haya sido eliminado.')

        deleted = []

        # Eliminar registros relacionados antes del empleado
        related = [
            ('planilla.employee.loan',      [('employee_id', '=', emp.id)], 'préstamos'),
            ('planilla.pension.alimentaria',[('employee_id', '=', emp.id)], 'pensiones'),
            ('planilla.recurring.benefit',  [('employee_id', '=', emp.id)], 'beneficios'),
            ('planilla.disability',         [('employee_id', '=', emp.id)], 'incapacidades'),
            ('planilla.vacation.payment',   [('employee_id', '=', emp.id)], 'vacaciones'),
            ('planilla.overtime',           [('employee_id', '=', emp.id)], 'horas extras'),
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
                pass  # modelo puede no existir en esta instalación

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
                'default_delete_msg': f'✅ Empleado "{emp_name}" eliminado correctamente. '
                                      f'Registros eliminados: {deleted_txt}.'
            },
        }
