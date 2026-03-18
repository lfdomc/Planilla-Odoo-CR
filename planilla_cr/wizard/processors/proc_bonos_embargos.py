"""
Procesadores de importación masiva — Planilla CR v5.6
Cada procesador es un método del wizard ImportDataWizard.
Se importan desde import_data_wizard.py via herencia múltiple.
"""
import logging
from odoo import models, api
from odoo.exceptions import UserError
from ...models import planilla_const as K

_logger = logging.getLogger(__name__)

class ImportProcessorBonosEmbargos(models.AbstractModel):
    """Procesadores de embargos judiciales y bonos e incentivos."""
    _name = 'planilla.import.processor.bonos.embargos'
    _description = 'Procesador Importacion Bonos y Embargos'

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
            cedula = str(v('Cédula', 'Cedula') or '').strip()
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
                    calc_raw  = _normalize(v('Tipo de Cálculo', 'Tipo Calculo') or '')
                    calc_type = EMBARGO_CALC.get(calc_raw, 'fixed')
                    pct       = _parse_float(v('Porcentaje', 'Porcentaje (%)'))
                    monto     = _parse_float(v('Monto Fijo', 'Monto Fijo (₡)'))
                    expediente= str(v('N° Expediente', 'Expediente') or '').strip()
                    juzgado   = str(v('Juzgado', 'Juzgado / Tribunal') or '').strip()

                    if not expediente:
                        raise ValueError('El N° Expediente Judicial es obligatorio')
                    if calc_type == 'fixed' and monto <= 0:
                        raise ValueError('El Monto Fijo debe ser mayor a ₡0')
                    if calc_type == 'percentage' and not (0 < pct <= 25):
                        raise ValueError(f'El porcentaje ({pct}%) debe estar entre 0 y 25% (Art. 172 CT)')

                    vals = {
                        'employee_id':        emp.id,
                        'numero_expediente':  expediente,
                        'juzgado':            juzgado or 'Sin especificar',
                        'fecha_resolucion':   _parse_date(v('Fecha de Resolución', 'Fecha Resolucion')),
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

    # ══════════════════════════════════════════════════════════════════════════
    # PROCESADOR BONOS E INCENTIVOS
    # ══════════════════════════════════════════════════════════════════════════

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
            'antigüedad por años de servicio':     'antiguedad',
            'antiguedad':                          'antiguedad',
            'subsidio de transporte / kilometraje':'transporte',
            'transporte':                          'transporte',
            'subsidio de alimentación (en dinero)':'alimentacion',
            'alimentacion':                        'alimentacion',
            'alimentación':                        'alimentacion',
            'subsidio educativo':                  'educacion',
            'educacion':                           'educacion',
            'subsidio de salud / médico':          'salud',
            'salud':                               'salud',
            'gastos de representación':            'representacion',
            'representacion':                      'representacion',
            'comisión por ventas':                 'comision',
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
            cedula = str(v('Cédula', 'Cedula') or '').strip()
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
                    calc_raw  = _normalize(v('Tipo de Cálculo', 'Tipo Calculo') or '')
                    bono_type = BONO_TYPE_MAP.get(tipo_raw, 'otro')
                    calc_type = BONO_CALC_MAP.get(calc_raw, 'fixed')
                    monto     = _parse_float(v('Monto Fijo', 'Monto Fijo (₡)'))
                    pct       = _parse_float(v('Porcentaje', 'Porcentaje (%)'))
                    recurrente= _parse_bool(v('Es Recurrente', 'Recurrente'))
                    afecto_ccss  = _parse_bool(v('Afecto CCSS', 'CCSS'))
                    afecto_renta = _parse_bool(v('Afecto Renta', 'Renta'))
                    tope      = _parse_float(v('Tope Exento', 'Tope Exento (₡/mes)'))

                    if not concepto:
                        raise ValueError('El Concepto del bono es obligatorio')
                    if calc_type == 'fixed' and monto <= 0:
                        raise ValueError('El Monto Fijo debe ser mayor a ₡0')
                    if calc_type == 'percentage' and pct <= 0:
                        raise ValueError('El Porcentaje debe ser mayor a 0%')

                    # Si no se especificó afecto_ccss/renta, aplicar defaults del tipo
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

    # ══════════════════════════════════════════════════════════════════════════
    # REPORTE DE ERRORES EN EXCEL
    # ══════════════════════════════════════════════════════════════════════════

