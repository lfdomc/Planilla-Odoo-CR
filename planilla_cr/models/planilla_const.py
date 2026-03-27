"""
planilla_const.py — Constantes Legales y Tasas de Planilla CR 2026
===================================================================
Centraliza todas las tasas, topes y valores legales del módulo.

IMPORTANTE: Estos valores son los fallbacks cuando NO hay configuración
en planilla.deduction.code. En producción las tasas vienen de BD
(configurables) a través de planilla.rate.helper.

Actualizar este archivo al inicio de cada año fiscal cuando el MTSS y
la CCSS publiquen los nuevos decretos.

Fuentes:
  - CCSS: Decreto CCSS 2026 / Reglamento del Seguro de Salud
  - INS: Ley N.° 6727 Riesgos del Trabajo / Decretos INS 2026
  - MTSS/DGT: Resolución DGT-R-016-2026 (Renta)
  - CT: Código de Trabajo CR (Ley N.° 2)
  - Ley 7983: Régimen Obligatorio de Pensiones
"""

# ─────────────────────────────────────────────────────────────────────────────
# CCSS — Caja Costarricense del Seguro Social (Decreto 2026)
# ─────────────────────────────────────────────────────────────────────────────

#: Cuota obrera CCSS (decimal). Incluye: SEM, IVM, BPOP, LPT, ASFA, FODESAF, INA
CCSS_EMP: float = 0.1083

#: Cuota obrera CCSS para pensionado sector público (decimal).
#: Exonerado del IVM (4.33%) según Art. 4 Ley Constitutiva CCSS.
#: Composición: SEM 5.50% + BPOP + LPT + ASFA + FODESAF + INA 1.00% = 6.50%.
#: Fallback — tasa configurable desde planilla.deduction.code (CCSS_OBR_PENSIONADO).
CCSS_EMP_PENSIONADO_ESTADO: float = 0.065

#: Cuota patronal CCSS (decimal). Incluye: SEM, IVM, BPOP, LPT, ASFA, FODESAF, INA
CCSS_PAT: float = 0.2683

# ─────────────────────────────────────────────────────────────────────────────
# INS — Instituto Nacional de Seguros (Riesgos del Trabajo)
# Tasas por clase de riesgo según actividad económica
# ─────────────────────────────────────────────────────────────────────────────

INS_TASAS: dict = {
    'I':   0.0087,   # Riesgo mínimo: oficinas, comercio sedentario
    'II':  0.0149,   # Riesgo bajo: comercio general, servicios
    'III': 0.0247,   # Riesgo medio: manufactura ligera, transporte
    'IV':  0.0413,   # Riesgo alto: construcción, industria
    'V':   0.0688,   # Riesgo máximo: minería, explosivos, pesca
}
INS_TASA_DEFAULT: float = INS_TASAS['II']  # Clase II como fallback

# ─────────────────────────────────────────────────────────────────────────────
# PROVISIONES — Obligaciones patronales (Art. 162-228 CT)
# ─────────────────────────────────────────────────────────────────────────────

#: Provisión aguinaldo (Art. 228 CT): 1/12 del salario anual = 8.33%
PROV_AGUINALDO: float = 0.0833

#: Provisión cesantía fallback promedio (Art. 29 CT)
#: NOTA: Usar _get_cesantia_rate_by_years() del rate_helper para el valor
#: exacto según años de servicio del empleado.
PROV_CESANTIA: float = 0.0533  # fallback año 1 aprox.

#: Tabla Art. 29 CT — días de cesantía por año de servicio
#: (año_completo: días). Máximo 8 años (Art. 29 CT).
#: Fórmula provisión: dias / 360 → se aplica sobre el salario bruto del período.
CESANTIA_TABLA: dict = {
    1: 19.5,
    2: 20.0,
    3: 20.5,
    4: 21.0,
    5: 21.5,
    6: 22.0,
    7: 22.5,
    8: 23.0,   # máximo legal — más de 8 años usa esta tasa
}
#: Año máximo de cesantía (Art. 29 CT)
CESANTIA_MAX_ANOS: int = 8

#: Provisión vacaciones (Art. 153 CT): 12 días / 360 días = 3.3333% + factor
#: Cálculo exacto: 12 días ÷ (30 días/mes × 12 meses) = 3.3333%
#: Expresado sobre salario: (12/360) = 3.3333%. Sobre días laborables (288 días/año):
#: 12/288 = 4.1667%. El sistema usa 4.1667% (exacto a 4 decimales).
PROV_VACACIONES: float = round(12 / 288, 6)  # = 0.041667 exacto

# ─────────────────────────────────────────────────────────────────────────────
# ROP — Régimen Obligatorio de Pensiones (Ley 7983)
# ─────────────────────────────────────────────────────────────────────────────

#: ROP obrero 1% del salario bruto
ROP_EMP: float = 0.01

#: ROP patronal 3.25% del salario bruto
ROP_PAT: float = 0.0325

# ─────────────────────────────────────────────────────────────────────────────
# IMPUESTO DE RENTA — Tabla progresiva (DGT-R-016-2026)
# Tramos en colones mensuales para trabajadores en relación de dependencia
# ─────────────────────────────────────────────────────────────────────────────

#: Monto exento mensual (no paga renta)
RENTA_EXENTO: int = 941_000

#: Límite superior del tramo 10%
RENTA_TOPE_10: int = 1_381_000

#: Límite superior del tramo 15%
RENTA_TOPE_15: int = 2_423_000

#: Límite superior del tramo 20%
RENTA_TOPE_20: int = 4_845_000

#: A partir de 4,845,000 → tasa 25%
RENTA_TASA_1: float = 0.10
RENTA_TASA_2: float = 0.15
RENTA_TASA_3: float = 0.20
RENTA_TASA_4: float = 0.25

# ─────────────────────────────────────────────────────────────────────────────
# BENEFICIOS EXENTOS — Topes legales CR 2026
# ─────────────────────────────────────────────────────────────────────────────

#: Tope exento subsidio de transporte mensual (MTSS 2026, acuerdo Hacienda)
TOPE_TRANSPORTE: int = 74_000

#: Tope exento viáticos/representación (exento CCSS si cumple requisitos)
TOPE_VIATICOS: int = 0  # No hay tope fijo — depende de decreto patronal

# ─────────────────────────────────────────────────────────────────────────────
# TOPE SALARIAL CCSS — Nota legal importante
# ─────────────────────────────────────────────────────────────────────────────
#: Costa Rica eliminó el tope salarial de cotización CCSS en 2006
#: (Ley N.° 8410 — Art. 19 Reglamento del Seguro de Salud).
#: NO existe un salario máximo cotizable — el 10.83% obrero y 26.83% patronal
#: se aplican sobre la TOTALIDAD del salario bruto sin límite superior.
#: Por eso este archivo NO define ninguna constante TOPE_CCSS.
#: Referencia: CCSS, Circular DSA-1183, 2006.
CCSS_SIN_TOPE_SALARIAL: bool = True  # Documentación — no usar en código

# ─────────────────────────────────────────────────────────────────────────────
# LABORALES — Tiempo y cálculos (Código de Trabajo)
# ─────────────────────────────────────────────────────────────────────────────

#: Días laborales en un mes para cálculo de salario diario (Art. 163 CT)
DIAS_MES: int = 30

#: Horas jornada ordinaria por defecto (Art. 136 CT: 8h diurnas)
HORAS_JORNADA_DEFAULT: float = 8.0

#: Máximo horas extras diarias (Art. 139 CT)
MAX_HE_DIARIAS: float = 4.0

#: Factor horas extras simples (1.5x del salario ordinario)
FACTOR_HE_SIMPLE: float = 1.5

#: Factor horas extras dobles / feriado (2x del salario ordinario)
FACTOR_HE_DOBLE: float = 2.0

#: Días de vacaciones por cada 50 semanas laboradas (Art. 153 CT)
DIAS_VACACIONES_POR_50_SEMANAS: int = 12

#: Días máximos de incapacidad a cargo del patrono (Art. 79 Regl. CCSS)
DIAS_INCAPACIDAD_PATRONO: int = 3

#: Días de licencia por paternidad (Ley 8107)
DIAS_PATERNIDAD: int = 8

#: Meses máximos de antigüedad para cesantía (Art. 29 CT)
MAX_ANOS_CESANTIA: int = 8

#: Meses sin vacaciones antes de prescripción (Art. 156 CT)
MESES_PRESCRIPCION_VACACIONES: int = 22

#: Máximo porcentaje de embargo del salario neto (Art. 172 CT)
MAX_PCT_EMBARGO: float = 25.0

# ─────────────────────────────────────────────────────────────────────────────
# FRECUENCIAS DE PAGO — Factores de conversión
# ─────────────────────────────────────────────────────────────────────────────

#: Factor de conversión salario mensual → salario del período
FREQ_FACTORS: dict = {
    'monthly':   1.0,    # mensual
    'biweekly':  0.5,    # quincenal (2 períodos/mes)
    'weekly':    0.25,   # semanal (4 períodos/mes)
    'bimonthly': 2.0,    # bimensual (0.5 períodos/mes)
}

#: Períodos por mes por frecuencia
PERIODOS_POR_MES: dict = {
    'monthly':   1,
    'biweekly':  2,
    'weekly':    4,
    'bimonthly': 0.5,  # FIX B-04 v58: bimensual = 1 período cada 2 meses = 0.5 per/mes
                       # El valor anterior (1) era incorrecto y podía causar error fiscal
                       # si algún código usaba K.PERIODOS_POR_MES directamente.
}

# ─────────────────────────────────────────────────────────────────────────────
# TIMEZONE
# ─────────────────────────────────────────────────────────────────────────────

#: Offset UTC de Costa Rica (sin horario de verano)
CR_UTC_OFFSET_HOURS: int = 6  # CR = UTC-6

# ─────────────────────────────────────────────────────────────────────────────
# CÉDULAS DE PRUEBA (sample data para importación/testing)
# ─────────────────────────────────────────────────────────────────────────────

#: Cédula de la fila de ejemplo en el machote Excel (siempre se salta)
SAMPLE_CEDULA: str = '1-2345-6789'

# ─────────────────────────────────────────────────────────────────────────────
# BASE DE CÁLCULO DEL IMPUESTO DE RENTA — Toggle por empresa
# ─────────────────────────────────────────────────────────────────────────────

#: Valor por defecto de la base de cálculo de renta (Art. 33 LIR).
#: 'gross'    → base = salario bruto (correcto según DGT / Art. 33 LIR)
#: 'net_ccss' → base = bruto - CCSS obrero (práctica de algunas empresas,
#:              no reconocida oficialmente por la DGT)
RENTA_BASE_DEFAULT: str = 'gross'

# ─────────────────────────────────────────────────────────────────────────────
# CRÉDITOS FISCALES — Art. 34 Ley 7092 / Decreto Ejecutivo 45333-H
# Vigentes a partir del 1 de enero de 2026
# Fuente: https://www.hacienda.go.cr/docs/TramosRenta2026.pdf
# ─────────────────────────────────────────────────────────────────────────────

#: Crédito fiscal mensual por cada hijo menor o dependiente (Art. 34 LIR).
#: Se resta directamente del impuesto calculado. No genera reembolso.
#: Regla: si ambos cónyuges trabajan, solo uno puede aplicarlo por hijo.
CREDITO_FISCAL_HIJO: int = 1_710

#: Crédito fiscal mensual por cónyuge (Art. 34 LIR).
#: Se resta directamente del impuesto calculado. No genera reembolso.
#: Regla: solo uno de los dos cónyuges puede aplicarlo.
CREDITO_FISCAL_CONYUGE: int = 2_590

#: Cédula alternativa para tests unitarios
TEST_CEDULA: str = '1-0000-0001'
