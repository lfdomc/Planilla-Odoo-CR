"""
Helper centralizado para leer tasas de planilla desde los codigos de deduccion.
Un solo lugar para obtener CCSS, INS, aguinaldo, cesantia y vacaciones.

Decision de diseno: NO se usa cache en este helper.
Las tasas son datos contables criticos. Cada consulta va directamente a la BD
para garantizar que siempre se usan los valores vigentes. La exactitud contable
es mas importante que el rendimiento en este caso.

Las optimizaciones de rendimiento se aplican en las capas de batch sync
(PERF-04, PERF-05) que reducen queries de otro tipo sin tocar datos contables.
"""
from odoo import models
from . import planilla_const as K


class RateHelper(models.AbstractModel):
    _name = 'planilla.rate.helper'
    _description = 'Helper de Tasas de Planilla'

    def _get_deduction_code(self, code):
        """Busca el codigo de deduccion filtrando por empresa actual.
        Primero intenta encontrar uno especifico de la empresa, luego uno global."""
        company = self.env.company
        # Buscar especifico de la empresa
        dc = self.env['planilla.deduction.code'].search(
            [('code', '=', code), ('active', '=', True),
             ('company_id', '=', company.id)], limit=1
        )
        if not dc:
            # Fallback: codigo sin empresa asignada (global)
            dc = self.env['planilla.deduction.code'].search(
                [('code', '=', code), ('active', '=', True),
                 ('company_id', '=', False)], limit=1
            )
        if not dc:
            # Ultimo fallback: cualquiera
            dc = self.env['planilla.deduction.code'].search(
                [('code', '=', code), ('active', '=', True)], limit=1
            )
        return dc

    def get_ccss_employee_rate(self):
        """Tasa CCSS obrero (decimal). Default 10.83% si no configurado."""
        dc = self._get_deduction_code('CCSS_OBR')
        return (dc.employee_percentage / 100) if dc else K.CCSS_EMP

    def get_ccss_pensionado_rate(self):
        """Tasa CCSS obrero para pensionado sector publico (decimal).
        Default 6.50% -- exonerado IVM (Art. 4 Ley Const. CCSS).
        Configurable desde planilla.deduction.code codigo CCSS_OBR_PENSIONADO."""
        dc = self._get_deduction_code('CCSS_OBR_PENSIONADO')
        return (dc.employee_percentage / 100) if dc else K.CCSS_EMP_PENSIONADO_ESTADO

    def get_ccss_employer_rate(self):
        """Tasa CCSS patronal (decimal). Default 26.83% si no configurado."""
        dc = self._get_deduction_code('CCSS_PAT')
        return (dc.employer_percentage / 100) if dc else K.CCSS_PAT

    def get_ins_rate(self, risk_class='II'):
        """Tasa INS decimal segun clase de riesgo. Default clase II si no configurado."""
        dc = self._get_deduction_code('INS_PAT')
        if dc:
            return dc.get_ins_rate(risk_class)
        return K.INS_TASAS.get(risk_class, K.INS_TASA_DEFAULT)

    def get_aguinaldo_rate(self):
        """Tasa provision aguinaldo (decimal). Default 8.33%."""
        dc = self._get_deduction_code('AGUINALDO')
        return (dc.employer_percentage / 100) if dc else K.PROV_AGUINALDO

    def get_cesantia_rate(self, entry_date=None, period_date=None):
        """
        Tasa provision cesantia.

        Orden de prioridad:
          1. Si config contable usa 'custom': retorna la tasa fija configurada
             (default 4.80% según criterio contable de Mundopet).
          2. Si config usa 'legal' y hay código CESANTIA en BD: usa ese valor.
          3. Si config usa 'legal' y hay entry_date: calcula por Art. 29 CT.
          4. Fallback: K.PROV_CESANTIA (5.33%).

        Args:
            entry_date: Fecha de ingreso del empleado (date object).
            period_date: Fecha del periodo (date object). Default: hoy.
        """
        # Prioridad 1: configuración contable de la empresa
        try:
            config = self.env['planilla.accounting.config'].search(
                [('company_id', '=', self.env.company.id)], limit=1
            )
            if config:
                mode = config.cesantia_prov_mode or 'legal'
                if mode == 'custom':
                    rate = config.cesantia_prov_rate or 4.8
                    return round(rate / 100, 6)
                # mode == 'legal': usa tabla Art. 29 CT por años de servicio
        except Exception:
            pass  # Si falla la búsqueda, usar tabla Art. 29 CT

        dc = self._get_deduction_code('CESANTIA')
        if dc:
            return dc.employer_percentage / 100

        if not entry_date:
            return K.PROV_CESANTIA  # fallback

        from datetime import date as _date
        ref = period_date or _date.today()
        # Calcular anos completos de servicio
        anos = (ref - entry_date).days // 365
        # Limitar al maximo legal de 8 anos
        anos_capped = max(1, min(anos + 1, K.CESANTIA_MAX_ANOS))
        # dias del ano actual de servicio segun tabla Art. 29 CT
        dias = K.CESANTIA_TABLA.get(anos_capped, K.CESANTIA_TABLA[K.CESANTIA_MAX_ANOS])
        # Tasa = dias / 360 (30 dias x 12 meses)
        return round(dias / 360, 6)

    def get_vacation_rate(self):
        """
        Tasa provision vacaciones exacta (decimal).
        Art. 153 CT: 12 dias por cada 50 semanas laboradas.
        Calculo: 12 dias / 288 dias laborables/ano = 4.1667%.
        (288 = 24 quincenas x 12 dias utiles por quincena)
        """
        dc = self._get_deduction_code('VACACIONES')
        if dc:
            return dc.employer_percentage / 100
        return K.PROV_VACACIONES  # 0.041667 exacto

    def calc_vacation_accrual(self, employee, as_of_date, disability_days_excluded=0, _cfg=None):
        """
        UNICA fuente de verdad para el calculo de dias de vacaciones
        acumulados (bruto, ANTES de restar los dias ya tomados).

        Usado por:
          - hr.employee._compute_vacation_balance() -- saldo en vivo (as_of_date=hoy)
          - termination_simulator.py -- simulacion de liquidacion (as_of_date=fecha simulada)
          - employee_termination.py -- liquidacion real (as_of_date=fecha de salida)

        Antes cada uno de estos 3 lugares tenia su PROPIA copia de esta
        logica, reescrita independientemente -- lo cual permitio que el
        bug de "extra_vacation_days_enabled sin filtrar" (aplicaba el bono
        de aniversario aunque el toggle estuviera apagado, porque el monto
        por defecto es 2.0) sobreviviera en 4 lugares distintos a la vez,
        cada uno con pequenas variaciones. Centralizar aqui significa que
        una correccion futura se aplica automaticamente en los 3 lugares.

        Formula (Art. 153 CT):
          BASE  : 1 dia por cada 29 dias calendario trabajados (o 1 dia por
                  mes calendario, segun accrual_method configurado).
          BONUS : bono de aniversario configurable (Config Contable),
                  completando hasta el monto configurado -- el mes del
                  aniversario ya recibe 1 dia por el ciclo normal, asi que
                  el bono aporta solo la diferencia (top-up), no se suma
                  encima. Ej: config=2 -> 1 normal + 1 bono = 2 total ese
                  mes, no 3. Requiere extra_vacation_days_enabled=True en
                  la empresa; si no esta activado, el bono es 0 (no aplica
                  el default de 2.0 del campo).

        Con fecha de corte (vacation_initial_balance_date, migracion desde
        Excel anterior): parte de vacation_initial_balance en esa fecha.
        Sin fecha de corte: calcula todo desde entry_date.

        :param employee: registro hr.employee (uno solo)
        :param as_of_date: fecha de referencia ("hoy" para el calculo)
        :param disability_days_excluded: dias de incapacidad >90 dias a
               excluir del metodo 'days29' (Art. 153 CT par. 2)
        :param _cfg: PERF -- registro de planilla.accounting.config ya
               cargado por el llamador (evita buscarlo de nuevo). Si se
               llama para muchos empleados de la misma empresa (ej. al
               recalcular la lista completa), el llamador deberia cachear
               este registro UNA vez por empresa y pasarlo aqui, en vez de
               dejar que cada empleado dispare su propia busqueda. Si no
               se provee, se busca aqui como antes (sigue funcionando para
               llamadas puntuales de un solo empleado).
        :return: tuple (accrued_total, nuevos_base, bonus_aniversarios)
        """
        import calendar as _cal
        from datetime import date as _date
        from dateutil.relativedelta import relativedelta as _rdelta

        if not employee.entry_date:
            return 0.0, 0, 0

        hoy = as_of_date
        entry_date = employee.entry_date

        # PERF: una sola busqueda de config sirve para accrual_method Y para
        # el toggle/monto del bono de aniversario -- antes eran 2 busquedas
        # separadas (una de ellas con un filtro de dominio distinto). El
        # toggle se revisa en Python en vez de en el dominio SQL.
        _config = _cfg if _cfg is not None else self.env['planilla.accounting.config'].search(
            [('company_id', '=', employee.company_id.id)], limit=1)
        accrual_method = _config.vacation_accrual_method if _config else 'monthly'

        def _meses_desde(desde, hasta):
            entry_day = entry_date.day
            count = 0
            try:    cand = _date(desde.year, desde.month, entry_day)
            except ValueError: cand = _date(desde.year, desde.month, _cal.monthrange(desde.year, desde.month)[1])
            if cand <= desde:
                m = cand + _rdelta(months=1)
                try:    cand = _date(m.year, m.month, entry_day)
                except ValueError: cand = _date(m.year, m.month, _cal.monthrange(m.year, m.month)[1])
            while cand <= hasta:
                count += 1
                m = cand + _rdelta(months=1)
                try:    cand = _date(m.year, m.month, entry_day)
                except ValueError: cand = _date(m.year, m.month, _cal.monthrange(m.year, m.month)[1])
            return count

        # Mismo _config de arriba -- ya no se busca de nuevo. El toggle se
        # revisa en Python (antes era un segundo dominio de busqueda SQL).
        av_base = (_config.extra_vacation_days_amount
                   if _config and _config.extra_vacation_days_enabled else 0)

        has_cutoff = bool(employee.vacation_initial_balance_date)

        if has_cutoff:
            corte    = employee.vacation_initial_balance_date
            init_bal = employee.vacation_initial_balance or 0.0
            if corte >= hoy:
                return int(init_bal), 0, 0
            if accrual_method == 'monthly':
                nuevos_base = _meses_desde(corte, hoy)
            else:
                dias_ingreso_corte = max((corte - entry_date).days, 0)
                parcial_inicial    = dias_ingreso_corte % 29
                dias_corte_hoy = max((hoy - corte).days - disability_days_excluded, 0)
                total_ciclo = dias_corte_hoy + parcial_inicial
                nuevos_base = total_ciclo // 29
            base_ref = corte
        else:
            if accrual_method == 'monthly':
                nuevos_base = _meses_desde(entry_date, hoy)
            else:
                dias_totales = max((hoy - entry_date).days - disability_days_excluded, 0)
                nuevos_base  = dias_totales // 29
            init_bal = 0.0
            base_ref = entry_date

        bonus_aniversarios = 0
        bonus_anniversary_count = 0
        aniv = entry_date + _rdelta(years=1)
        while aniv <= hoy:
            if aniv > base_ref:
                # Top-up: el ciclo normal ya aporta 1 dia el mes del
                # aniversario, el bono completa el resto.
                bonus_aniversarios += max(av_base - 1, 0)
                bonus_anniversary_count += 1
            aniv += _rdelta(years=1)

        accrued = int(init_bal) + nuevos_base + bonus_aniversarios
        return accrued, nuevos_base, bonus_aniversarios, bonus_anniversary_count

    def next_sequential_code(self, table, prefix):
        """
        UNICA fuente de verdad para generar codigos secuenciales tipo
        'PREFIJO-0001'. Antes esta logica (identica salvo el nombre de
        tabla) estaba copiada en 5 modelos distintos: employee_charge,
        leave_cr, embargo, bono, overtime.

        Busca el ultimo codigo con ese prefijo en la tabla dada y devuelve
        el siguiente numero, formateado a 4 digitos.

        :param table: nombre de tabla SQL (whitelist fija, no viene de
               input de usuario -- se valida para uso seguro de SQL
               dinamico, mismo patron que migrate_codes_wizard.py)
        :param prefix: prefijo del codigo (ej: 'BON', 'EMB', 'HE')
        """
        from psycopg2 import sql as _sql
        _ALLOWED_TABLES = frozenset({
            'planilla_employee_charge', 'planilla_leave_cr', 'planilla_embargo',
            'planilla_bono', 'planilla_overtime',
        })
        if table not in _ALLOWED_TABLES:
            raise ValueError(f'Tabla no permitida para codigo secuencial: {table}')

        self.env.cr.execute(
            _sql.SQL('SELECT code FROM {} WHERE code LIKE %s ORDER BY code DESC LIMIT 1')
                .format(_sql.Identifier(table)),
            (prefix + '-%',)
        )
        row = self.env.cr.fetchone()
        if row and row[0]:
            try:
                num = int(row[0].split('-')[-1]) + 1
            except (ValueError, IndexError):
                num = 1
        else:
            num = 1
        return f'{prefix}-{num:04d}'

    def get_all_ins_rates(self):
        """Dict con todas las tasas INS por clase {clase: decimal}."""
        dc = self._get_deduction_code('INS_PAT')
        if dc:
            return {
                'I':   dc.ins_rate_i   / 100,
                'II':  dc.ins_rate_ii  / 100,
                'III': dc.ins_rate_iii / 100,
                'IV':  dc.ins_rate_iv  / 100,
                'V':   dc.ins_rate_v   / 100,
            }
        return {'I': 0.0087, 'II': 0.0149, 'III': 0.0247, 'IV': 0.0413, 'V': 0.0688}
