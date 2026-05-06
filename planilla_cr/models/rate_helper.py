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
                mode = config.cesantia_prov_mode or 'custom'
                if mode == 'custom':
                    rate = config.cesantia_prov_rate or 4.8
                    return round(rate / 100, 6)
                # mode == 'legal': continúa con lógica por años
        except Exception:
            pass  # Si falla la búsqueda, usar lógica estándar

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
