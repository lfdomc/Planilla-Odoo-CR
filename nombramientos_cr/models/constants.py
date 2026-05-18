"""
Constantes del módulo nombramientos_cr.
Centraliza valores legales y de configuración para evitar números mágicos.
"""

# ── Jornada laboral (Art. 136 CT) ────────────────────────────────────────────
DIAS_MES        = 30        # días por mes para cálculo de tarifa
HORAS_DIA       = 8         # horas máximas jornada diurna
HORAS_DIA_MIXTA = 7         # horas máximas jornada mixta
HORAS_DIA_NOCTURNA = 6      # horas máximas jornada nocturna
HORAS_SEMANA_DIURNA   = 48
HORAS_SEMANA_MIXTA    = 42
HORAS_SEMANA_NOCTURNA = 36

# ── Factores de recargo HE (Art. 139 CT) ─────────────────────────────────────
FACTOR_HE_DIURNA   = 1.50   # +50% jornada diurna y mixta
FACTOR_HE_NOCTURNA = 1.75   # +75% jornada nocturna

# Horas máximas por tipo de jornada
MAX_HORAS_JORNADA = {
    'day':   HORAS_DIA,
    'mixed': HORAS_DIA_MIXTA,
    'night': HORAS_DIA_NOCTURNA,
}

# Factor HE por tipo de jornada
FACTOR_HE_JORNADA = {
    'day':   FACTOR_HE_DIURNA,
    'mixed': FACTOR_HE_DIURNA,
    'night': FACTOR_HE_NOCTURNA,
}

# ── Frecuencias de planilla (deben coincidir con planilla_cr) ─────────────────
FREQ_FACTORS = {
    'monthly':   1.0,
    'biweekly':  0.5,
    'weekly':    0.25,
    'bimonthly': 2.0,
}

# ── Mínimo de días para derecho a vacaciones (Art. 153 CT) ───────────────────
DIAS_MIN_VACACIONES = 50

# ── Porcentaje de aguinaldo (Art. 228 CT) ────────────────────────────────────
AGUINALDO_PCT = 1 / 12      # 1 mes de salario al año = 8.33%

# ── Descanso mínimo en jornada continua (Art. 136 CT) ────────────────────────
MIN_DESCANSO_MINUTOS = 30
MIN_HORAS_CON_DESCANSO = 6  # jornadas ≥ 6h requieren descanso


def calcular_tarifa_hora(base_salary, horas_dia=HORAS_DIA, dias_mes=DIAS_MES):
    """
    Calcula la tarifa por hora a partir del salario mensual.
    base_salary / dias_mes / horas_dia
    """
    if not base_salary or base_salary <= 0:
        return 0.0
    return round(base_salary / dias_mes / horas_dia, 2)


def calcular_tarifa_he(tarifa_hora, shift_type='day'):
    """
    Calcula la tarifa a pasar a planilla.overtime considerando el factor 1.5x.
    planilla.overtime aplica el factor automáticamente, así que dividimos
    para que el resultado final sea la tarifa original.
    """
    factor = FACTOR_HE_JORNADA.get(shift_type, FACTOR_HE_DIURNA)
    return round(tarifa_hora / factor, 4) if tarifa_hora else 0.0


def leer_base_salary(cr, emp_id):
    # Lee base_salary directo de BD — centralizado para evitar duplicación.
    # Verifica si la columna existe (planilla_cr puede no estar instalado).
    if not emp_id:
        return 0.0
    try:
        cr.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='hr_employee' AND column_name='base_salary'"
        )
        if not cr.fetchone():
            return 0.0
        cr.execute(
            "SELECT COALESCE(base_salary, 0) FROM hr_employee WHERE id = %s",
            (emp_id,)
        )
        row = cr.fetchone()
        return float(row[0]) if row else 0.0
    except Exception:
        return 0.0
