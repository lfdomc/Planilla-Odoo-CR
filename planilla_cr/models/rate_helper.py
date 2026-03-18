"""
Helper centralizado para leer tasas de planilla desde los códigos de deducción.
Un solo lugar para obtener CCSS, INS, aguinaldo, cesantía y vacaciones.
"""
from odoo import models
from . import planilla_const as K


class RateHelper(models.AbstractModel):
    _name = 'planilla.rate.helper'
    _description = 'Helper de Tasas de Planilla'

    def _get_deduction_code(self, code):
        """Busca el código de deducción filtrando por empresa actual.
        Primero intenta encontrar uno específico de la empresa, luego uno global."""
        company = self.env.company
        # Buscar específico de la empresa
        dc = self.env['planilla.deduction.code'].search(
            [('code', '=', code), ('active', '=', True),
             ('company_id', '=', company.id)], limit=1
        )
        if not dc:
            # Fallback: código sin empresa asignada (global)
            dc = self.env['planilla.deduction.code'].search(
                [('code', '=', code), ('active', '=', True),
                 ('company_id', '=', False)], limit=1
            )
        if not dc:
            # Último fallback: cualquiera
            dc = self.env['planilla.deduction.code'].search(
                [('code', '=', code), ('active', '=', True)], limit=1
            )
        return dc

    def get_ccss_employee_rate(self):
        """Tasa CCSS obrero (decimal). Default 10.83% si no configurado."""
        dc = self._get_deduction_code('CCSS_OBR')
        return (dc.employee_percentage / 100) if dc else K.CCSS_EMP

    def get_ccss_employer_rate(self):
        """Tasa CCSS patronal (decimal). Default 26.83% si no configurado."""
        dc = self._get_deduction_code('CCSS_PAT')
        return (dc.employer_percentage / 100) if dc else K.CCSS_PAT

    def get_ins_rate(self, risk_class='II'):
        """Tasa INS decimal según clase de riesgo. Default clase II si no configurado."""
        dc = self._get_deduction_code('INS_PAT')
        if dc:
            return dc.get_ins_rate(risk_class)
        # Fallback
        fallback = K.INS_TASAS
        return fallback.get(risk_class, K.INS_TASA_DEFAULT)

    def get_aguinaldo_rate(self):
        """Tasa provisión aguinaldo (decimal). Default 8.33%."""
        dc = self._get_deduction_code('AGUINALDO')
        return (dc.employer_percentage / 100) if dc else K.PROV_AGUINALDO

    def get_cesantia_rate(self):
        """Tasa provisión cesantía (decimal). Default 5.33%."""
        dc = self._get_deduction_code('CESANTIA')
        return (dc.employer_percentage / 100) if dc else K.PROV_CESANTIA

    def get_vacation_rate(self):
        """Tasa provisión vacaciones (decimal). Default 4.16%."""
        dc = self._get_deduction_code('VACACIONES')
        return (dc.employer_percentage / 100) if dc else K.PROV_VACACIONES

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
