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
CESANTIA_TABLA: dict = {
    1: 19.5,
    2: 20.0,
    3: 20.0,   # Art. 29 CT: ano 3 = 20 dias (corregido)
    4: 21.0,
    5: 21.24,  # Art. 29 CT: ano 5 = 21.24 dias (corregido)
    6: 21.5,   # Art. 29 CT: ano 6 = 21.5 dias (corregido)
    7: 22.0,   # Art. 29 CT: ano 7 = 22 dias (corregido)
    8: 22.0,   # maximo legal -- mas de 8 anos usa esta tasa (corregido)
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
