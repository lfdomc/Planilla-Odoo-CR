"""
planilla_const.py -- Constantes Legales y Tasas de Planilla CR 2026
===================================================================
Centraliza todas las tasas, topes y valores legales del modulo.

IMPORTANTE: Estos valores son los fallbacks cuando NO hay configuracion
en planilla.deduction.code. En produccion las tasas vienen de BD
(configurables) a traves de planilla.rate.helper.

Actualizar este archivo al inicio de cada ano fiscal cuando el MTSS y
la CCSS publiquen los nuevos decretos.

Fuentes:
  - CCSS: Decreto CCSS 2026 / Reglamento del Seguro de Salud
  - INS: Ley N.deg 6727 Riesgos del Trabajo / Decretos INS 2026
  - MTSS/DGT: Resolucion DGT-R-016-2026 (Renta)
  - CT: Codigo de Trabajo CR (Ley N.deg 2)
  - Ley 7983: Regimen Obligatorio de Pensiones
"""

# -----------------------------------------------------------------------------
# CCSS -- Caja Costarricense del Seguro Social (Decreto 2026)
# -----------------------------------------------------------------------------

#: Cuota obrera CCSS (decimal). Incluye: SEM, IVM, BPOP, LPT, ASFA, FODESAF, INA
CCSS_EMP: float = 0.1083

#: Cuota obrera CCSS para pensionado sector publico (decimal).
#: Exonerado del IVM (4.33%) segun Art. 4 Ley Constitutiva CCSS.
#: Composicion: SEM 5.50% + BPOP + LPT + ASFA + FODESAF + INA 1.00% = 6.50%.
#: Fallback -- tasa configurable desde planilla.deduction.code (CCSS_OBR_PENSIONADO).
CCSS_EMP_PENSIONADO_ESTADO: float = 0.065

#: Cuota patronal CCSS (decimal). Incluye: SEM, IVM, BPOP, LPT, ASFA, FODESAF, INA
CCSS_PAT: float = 0.2683

# -----------------------------------------------------------------------------
# BASE MINIMA CONTRIBUTIVA (BMC) -- CCSS, vigente 2026
# -----------------------------------------------------------------------------
# Si el salario de un trabajador (comunmente tiempo parcial) es MENOR a estos
# montos mensuales, la CCSS exige cotizar sobre el piso, no sobre el salario
# real. Fuente: CCSS Circular / Reglamentos SEM e IVM, vigente enero 2026.
#   SEM (Salud):            CRC 333,328 /mes
#   IVM (Pensiones):        CRC 311,990 /mes
#
# SIMPLIFICACION DE DISENO: el modulo trata CCSS como UNA tasa combinada
# (10.83%% obrero / 26.83%% patronal), no separa el componente SEM del IVM.
# Por eso se usa un solo piso practico = el MAYOR de los dos (SEM), para
# nunca subcotizar. Esto es ligeramente conservador en el componente IVM
# (que legalmente tiene un piso mas bajo) -- si se requiere precision exacta
# por componente, habria que desglosar la tasa CCSS en SEM/IVM por separado.
#
# EXCEPCIONES LEGALES a la BMC (no cubiertas por este piso simple, requieren
# revision caso por caso con el contador):
#   - Trabajadores de tiempo parcial menores de 35 anos: BMC reducida especial.
#   - Ingreso reciente al empleo (primer mes).
#   - Incapacidades activas (ya tienen su propia base legal, Art. 79/94 CT).
#   - Permisos sin goce de salario mayores a 15 dias.
#
# Actualizar este valor cada vez que la CCSS publique un nuevo decreto (revisar
# https://www.ccss.sa.cr/patronos al inicio de cada ano fiscal).
CCSS_BMC_MENSUAL: int = 333_328

# -----------------------------------------------------------------------------
# INS -- Instituto Nacional de Seguros (Riesgos del Trabajo)
# Tasas por clase de riesgo segun actividad economica
# -----------------------------------------------------------------------------

INS_TASAS: dict = {
    'I':   0.0087,   # Riesgo minimo: oficinas, comercio sedentario
    'II':  0.0149,   # Riesgo bajo: comercio general, servicios
    'III': 0.0247,   # Riesgo medio: manufactura ligera, transporte
    'IV':  0.0413,   # Riesgo alto: construccion, industria
    'V':   0.0688,   # Riesgo maximo: mineria, explosivos, pesca
}
INS_TASA_DEFAULT: float = INS_TASAS['II']  # Clase II como fallback

# -----------------------------------------------------------------------------
# PROVISIONES -- Obligaciones patronales (Art. 162-228 CT)
# -----------------------------------------------------------------------------

#: Provision aguinaldo (Art. 228 CT): 1/12 del salario anual = 8.33%
PROV_AGUINALDO: float = 0.0833

#: Provision cesantia fallback promedio (Art. 29 CT)
#: NOTA: Usar _get_cesantia_rate_by_years() del rate_helper para el valor
#: exacto segun anos de servicio del empleado.
PROV_CESANTIA: float = 0.0533  # fallback ano 1 aprox.

#: Tabla Art. 29 CT -- dias de cesantia por ano de servicio
#: (ano_completo: dias). Maximo 8 anos (Art. 29 CT).
#: Formula provision: dias / 360 -> se aplica sobre el salario bruto del periodo.
# Tabla oficial Art. 29 Codigo de Trabajo CR (dias por ano completo trabajado)
# Fuente: texto literal del Art. 29 CT y jurisprudencia Sala Segunda
#
# NOTA DE MIGRACION v5.28.69 (2026-04-06):
#   Tabla corregida. Valores anteriores incorrectos: ano3=20.5, ano5=21.5,
#   ano6=22.0, ano7=22.5, ano8=23.0.
#   DECISION CONTABLE: se aplica la tabla correcta desde esta version en adelante.
#   Las boletas ya pagadas (state=paid) NO se recalculan -- son inmutables por diseno.
#   Las provisiones acumuladas anteriores quedan como estan.
#   Impacto maximo por empleado senior (8+ anos, salario CRC800k):
#     Provision anterior: CRC51,111/mes  |  Provision correcta: CRC48,889/mes
#   El ajuste contable, si se requiere, debe realizarlo el contador del cliente.
# Cesantia sub-año (Art. 29 CT + Tabla oficial Ministerio de Trabajo CR):
# 3 a < 6 meses: 7 días total (pago único)
# 6 meses a < 1 año: 14 días total (pago único) -- NO proporcional
CESANTIA_SUB_ANIO: dict = {
    'tres_seis':   7,   # 3 a < 6 meses
    'seis_doce':  14,   # 6 meses a < 1 año (pago único fijo)
}

CESANTIA_TABLA: dict = {
    1: 19.5,
    2: 20.0,
    3: 20.0,   # Art. 29 CT: ano 3 = 20 dias
    4: 21.0,
    5: 21.24,  # Art. 29 CT: ano 5 = 21.24 dias
    6: 21.5,   # Art. 29 CT: ano 6 = 21.5 dias
    7: 22.0,   # Art. 29 CT: ano 7 = 22 dias
    8: 22.0,   # maximo legal
}
#: Ano maximo de cesantia (Art. 29 CT)
CESANTIA_MAX_ANOS: int = 8

#: Provision vacaciones (Art. 153 CT): 12 dias / 360 dias = 3.3333% + factor
#: Calculo exacto: 12 dias / (30 dias/mes x 12 meses) = 3.3333%
#: Expresado sobre salario: (12/360) = 3.3333%. Sobre dias laborables (288 dias/ano):
#: 12/288 = 4.1667%. El sistema usa 4.1667% (exacto a 4 decimales).
PROV_VACACIONES: float = round(12 / 288, 6)  # = 0.041667 exacto

# -----------------------------------------------------------------------------
# ROP -- Regimen Obligatorio de Pensiones (Ley 7983)
# -----------------------------------------------------------------------------

#: ROP obrero 1% del salario bruto
ROP_EMP: float = 0.01

#: ROP patronal 3.25% del salario bruto
ROP_PAT: float = 0.0325

# -----------------------------------------------------------------------------
# IMPUESTO DE RENTA -- Referencia (DGT-R-016-2026)
# -----------------------------------------------------------------------------
# IMPORTANTE: estos valores son SOLO REFERENCIA DOCUMENTAL.
# El sistema NUNCA los usa para calcular. Los tramos reales provienen
# SIEMPRE de planilla.income.tax.bracket (BD, configurables en la UI).
# Si no hay tramos en BD, el sistema bloquea la boleta con un error claro.
# Fuente: Ministerio de Hacienda CR, vigente marzo 2026.
# -----------------------------------------------------------------------------

#: Monto exento mensual (referencia documental -- usar tramos de BD)
RENTA_EXENTO: int = 918_000

#: Limite superior del tramo 10% (referencia documental)
RENTA_TOPE_10: int = 1_381_000

#: Limite superior del tramo 15% (referencia documental)
RENTA_TOPE_15: int = 2_423_000

#: Limite superior del tramo 20% (referencia documental)
RENTA_TOPE_20: int = 4_845_000

#: Tasas por tramo (referencia documental)
RENTA_TASA_1: float = 0.10
RENTA_TASA_2: float = 0.15
RENTA_TASA_3: float = 0.20
RENTA_TASA_4: float = 0.25

# -----------------------------------------------------------------------------
# BENEFICIOS EXENTOS -- Topes legales CR 2026
# -----------------------------------------------------------------------------

#: Tope exento subsidio de transporte mensual (MTSS 2026, acuerdo Hacienda)
TOPE_TRANSPORTE: int = 74_000

#: Tope exento viaticos/representacion (exento CCSS si cumple requisitos)
TOPE_VIATICOS: int = 0  # No hay tope fijo -- depende de decreto patronal

# -----------------------------------------------------------------------------
# TOPE SALARIAL CCSS -- Nota legal importante
# -----------------------------------------------------------------------------
#: Costa Rica elimino el tope salarial de cotizacion CCSS en 2006
#: (Ley N.deg 8410 -- Art. 19 Reglamento del Seguro de Salud).
#: NO existe un salario maximo cotizable -- el 10.83% obrero y 26.83% patronal
#: se aplican sobre la TOTALIDAD del salario bruto sin limite superior.
#: Por eso este archivo NO define ninguna constante TOPE_CCSS.
#: Referencia: CCSS, Circular DSA-1183, 2006.
CCSS_SIN_TOPE_SALARIAL: bool = True  # Documentacion -- no usar en codigo

# -----------------------------------------------------------------------------
# LABORALES -- Tiempo y calculos (Codigo de Trabajo)
# -----------------------------------------------------------------------------

#: Dias laborales en un mes para calculo de salario diario (Art. 163 CT)
DIAS_MES: int = 30

#: Horas jornada ordinaria por defecto (Art. 136 CT: 8h diurnas)
HORAS_JORNADA_DEFAULT: float = 8.0

#: Maximo horas extras diarias (Art. 139 CT)
MAX_HE_DIARIAS: float = 4.0

#: Factor horas extras simples (1.5x del salario ordinario)
FACTOR_HE_SIMPLE: float = 1.5

#: Factor horas extras dobles / feriado (2x del salario ordinario)
FACTOR_HE_DOBLE: float = 2.0

#: Dias de vacaciones por cada 50 semanas laboradas (Art. 153 CT)
DIAS_VACACIONES_POR_50_SEMANAS: int = 12

#: Dias maximos de incapacidad a cargo del patrono (Art. 79 Regl. CCSS)
DIAS_INCAPACIDAD_PATRONO: int = 3

#: Dias de licencia por paternidad (Ley 8107)
DIAS_PATERNIDAD: int = 8

#: Meses maximos de antiguedad para cesantia (Art. 29 CT)
MAX_ANOS_CESANTIA: int = 8

#: Meses sin vacaciones antes de prescripcion (Art. 156 CT)
MESES_PRESCRIPCION_VACACIONES: int = 22

#: Maximo porcentaje de embargo del salario neto (Art. 172 CT)
MAX_PCT_EMBARGO: float = 25.0

# -----------------------------------------------------------------------------
# FRECUENCIAS DE PAGO -- Factores de conversion
# -----------------------------------------------------------------------------

#: Mapeo de texto (espanol/ingles, como viene del Excel de importacion o de
#: formularios) al codigo interno de frecuencia usado en todo el modulo.
#: FIX BUG: antes vivia solo en wizard/import_data_wizard.py como constante
#: de modulo, sin importarse en wizard/processors/proc_employees.py -- eso
#: causaba NameError: name 'FREQUENCY' is not defined en TODA importacion
#: masiva de empleados por Excel, sin excepcion (nunca se llegaba a crear
#: ni un solo empleado). Centralizada aqui junto a sus constantes hermanas
#: (FREQ_FACTORS, PERIODOS_POR_MES) para que cualquier archivo que la
#: necesite la importe desde la misma fuente unica.
FREQUENCY: dict = {
    'mensual': 'monthly', 'monthly': 'monthly',
    'quincenal': 'biweekly', 'biweekly': 'biweekly',
    'semanal': 'weekly', 'weekly': 'weekly',
    'bimensual': 'bimonthly', 'bimonthly': 'bimonthly',
}

#: Factor de conversion salario mensual -> salario del periodo
FREQ_FACTORS: dict = {
    'monthly':   1.0,    # mensual
    'biweekly':  0.5,    # quincenal (2 periodos/mes)
    'weekly':    0.25,   # semanal (4 periodos/mes)
    'bimonthly': 2.0,    # bimensual (0.5 periodos/mes)
}

#: Periodos por mes por frecuencia
PERIODOS_POR_MES: dict = {
    'monthly':   1,
    'biweekly':  2,
    'weekly':    4,
    'bimonthly': 0.5,  # FIX B-04 v58: bimensual = 1 periodo cada 2 meses = 0.5 per/mes
                       # El valor anterior (1) era incorrecto y podia causar error fiscal
                       # si algun codigo usaba K.PERIODOS_POR_MES directamente.
}

# -----------------------------------------------------------------------------
# TIMEZONE
# -----------------------------------------------------------------------------

#: Offset UTC de Costa Rica (sin horario de verano)
CR_UTC_OFFSET_HOURS: int = 6  # CR = UTC-6

# -----------------------------------------------------------------------------
# CEDULAS DE PRUEBA (sample data para importacion/testing)
# -----------------------------------------------------------------------------

#: Cedula de la fila de ejemplo en el machote Excel (siempre se salta)
SAMPLE_CEDULA: str = '1-2345-6789'

# -----------------------------------------------------------------------------
# BASE DE CALCULO DEL IMPUESTO DE RENTA -- Toggle por empresa
# -----------------------------------------------------------------------------

#: Valor por defecto de la base de calculo de renta (Art. 33 LIR).
#: 'gross'    -> base = salario bruto (correcto segun DGT / Art. 33 LIR)
#: 'net_ccss' -> base = bruto - CCSS obrero (practica de algunas empresas,
#:              no reconocida oficialmente por la DGT)
RENTA_BASE_DEFAULT: str = 'gross'

# -----------------------------------------------------------------------------
# CREDITOS FISCALES -- Art. 34 Ley 7092 / Decreto Ejecutivo 45333-H
# Vigentes a partir del 1 de enero de 2026
# Fuente: https://www.hacienda.go.cr/docs/TramosRenta2026.pdf
# -----------------------------------------------------------------------------

#: Credito fiscal mensual por cada hijo menor o dependiente (Art. 34 LIR).
#: Se resta directamente del impuesto calculado. No genera reembolso.
#: Regla: si ambos conyuges trabajan, solo uno puede aplicarlo por hijo.
CREDITO_FISCAL_HIJO: int = 1_710

#: Credito fiscal mensual por conyuge (Art. 34 LIR).
#: Se resta directamente del impuesto calculado. No genera reembolso.
#: Regla: solo uno de los dos conyuges puede aplicarlo.
CREDITO_FISCAL_CONYUGE: int = 2_590

#: Cedula alternativa para tests unitarios
TEST_CEDULA: str = '1-0000-0001'

# -----------------------------------------------------------------------------
# MAPEOS DE IMPORTACION EXCEL (texto amigable -> valor tecnico del modelo)
# -----------------------------------------------------------------------------
# FIX BUG: estas 18 constantes vivian como variables de modulo sueltas en
# wizard/import_data_wizard.py, sin importarse en los archivos que
# realmente las usan (wizard/processors/proc_employees.py,
# proc_loans.py, proc_novedades.py). Causaba NameError: name '...' is not
# defined en CADA importacion masiva de empleados/prestamos/pensiones/
# incapacidades/horas-extra por Excel -- sin excepcion, nunca se llegaba
# a crear ni un solo registro en esas hojas. Centralizadas aqui junto al
# resto de constantes del modulo para que cualquier archivo que las
# necesite las importe desde la misma fuente unica (K.NOMBRE_CONSTANTE).

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
    'ni': 'NI', 'nicaraguense': 'NI',
    'co': 'CO', 'colombiano': 'CO', 'colombiana': 'CO', 'colombiano/a': 'CO',
    'us': 'US', 'estadounidense': 'US', 'americano': 'US',
    'hn': 'HN', 'hondureno': 'HN', 'hondureno/a': 'HN',
    'sv': 'SV', 'salvadoreno': 'SV', 'salvadoreno/a': 'SV',
    'gt': 'GT', 'guatemalteco': 'GT', 'guatemalteca': 'GT', 'guatemalteco/a': 'GT',
    'pa': 'PA', 'panameno': 'PA', 'panameno/a': 'PA',
    'mx': 'MX', 'mexicano': 'MX', 'mexicana': 'MX', 'mexicano/a': 'MX',
    've': 'VE', 'venezolano': 'VE', 'venezolana': 'VE', 'venezolano/a': 'VE',
    'pe': 'PE', 'peruano': 'PE', 'peruana': 'PE', 'peruano/a': 'PE',
    'ec': 'EC', 'ecuatoriano': 'EC', 'ecuatoriana': 'EC', 'ecuatoriano/a': 'EC',
    'ot': 'OT', 'otro': 'OT', 'otra': 'OT', 'other': 'OT',
}

ACCOUNT_TYPE = {
    'cuenta corriente': 'corriente', 'corriente': 'corriente', 'iban': 'corriente',
    'cuenta de ahorros': 'ahorros', 'ahorros': 'ahorros',
    'sinpe movil': 'sinpe', 'sinpe': 'sinpe',
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
    'union libre': '05', '05': '05',
    'separado/a': '06', 'separado': '06', 'separada': '06', '06': '06',
}

INS_ID_TYPE = {
    # Mapeo texto del Excel -> code de planilla.identification.type en BD
    # Codigos segun data inicial: CI, DIMEX, PAS, CJ, NITE
    'cedula nacional': 'CI',
    'cedula de identidad': 'CI',
    'cedula': 'CI', '01': 'CI', 'ci': 'CI',
    'residencia / dimex': 'DIMEX', 'residencia': 'DIMEX',
    'dimex': 'DIMEX', '02': 'DIMEX',
    'permiso de trabajo': 'NITE', 'permiso': 'NITE',
    'nite': 'NITE', '03': 'NITE',
    'pasaporte': 'PAS', 'pas': 'PAS', '04': 'PAS',
    'cedula juridica': 'CJ',
    'juridica': 'CJ', 'cj': 'CJ',
    'indocumentado': 'NITE', '05': 'NITE',
}

# Mapeo separado texto -> codigo INS (campo ins_id_type, numerico 2 digitos)
INS_ID_TYPE_CODE = {
    'cedula nacional': '01',
    'cedula de identidad': '01',
    'cedula': '01', '01': '01', 'ci': '01',
    'residencia / dimex': '02', 'residencia': '02', 'dimex': '02', '02': '02',
    'permiso de trabajo': '03', 'permiso': '03', 'nite': '03', '03': '03',
    'pasaporte': '04', 'pas': '04', '04': '04',
    'indocumentado': '05', '05': '05',
    'cedula juridica': '06', 'cj': '06',
}

DISABILITY_TYPE = {
    'enfermedad comun (ccss)': 'ccss',
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
    'deduccion / descuento': 'deduction',
    'deduccion': 'deduction',
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
    'dia feriado': 'holiday', 'feriado': 'holiday',
    'holiday': 'holiday',
}

BANK = {
    'bncr': 'BNCR', 'banco nacional': 'BNCR', 'nacional': 'BNCR',
    'bcr': 'BCR', 'banco de costa rica': 'BCR',
    'bp': 'BP', 'bpop': 'BP', 'banco popular': 'BP', 'popular': 'BP',
    'bac': 'BAC', 'bac san jose': 'BAC',
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
    'prestamo de empresa': 'loan',
    'loan': 'loan', 'prestamo': 'loan',
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
    'conyuge': 'conyuge', 'companero': 'conyuge',
    'companera': 'conyuge', 'conviviente': 'conyuge',
    'padre': 'padre', 'madre': 'madre',
    'otro': 'otro', 'otra': 'otro',
}
