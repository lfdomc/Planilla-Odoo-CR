"""
Procesadores de importacion masiva -- Planilla CR v5.6
Cada procesador es un metodo del wizard ImportDataWizard.
Se importan desde import_data_wizard.py via herencia multiple.
"""
import logging
import traceback  # FIX-L3: faltaba -- usado en bloques except
from datetime import date
from odoo import models, api
from odoo.exceptions import UserError
from ...models import planilla_const as K
from ..import_parse_utils import _map, _parse_bool, _parse_date, _parse_float

_logger = logging.getLogger(__name__)

class ImportProcessorNovedades(models.AbstractModel):
    """Procesadores de incapacidades, vacaciones y horas extras."""
    _name = 'planilla.import.processor.novedades'
    _description = 'Procesador Importacion Novedades'

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

            vals = {}  # BUG FIX: inicializar antes del try (el except usa vals.items())
            try:
                with self.env.cr.savepoint():
                    branch = self._find_m2o('planilla.branch', v('Sucursal'),
                                extra_domain=[('company_id', '=', self.company_id.id)])

                    dtype = _map(DISABILITY_TYPE, v('Tipo de Incapacidad', 'Tipo')) or 'ccss'
                    vals = {
                        'employee_id':          emp.id,
                        'disability_type':      dtype,
                        'date_start':           _parse_date(v('Fecha Inicio')) or date.today(),
                        'date_end':             _parse_date(v('Fecha Fin')) or date.today(),
                        'subsidy_percentage':   _parse_float(v('% Subsidiado', 'Subsidiado CCSS')),
                        'employer_percentage':  _parse_float(v('% Patrono', 'Cargo Patrono')) or 0.0,
                        'certificate_number':   str(v('Numero Certificado', 'Certificado') or '').strip() or False,
                        'diagnosis':            str(v('Diagnostico', 'Diagnostico') or '').strip() or False,
                        'note':                 str(v('Observaciones') or '').strip() or False,
                        'state':                'confirmed',
                    }
                    # Campos especiales maternidad
                    if dtype == 'maternity':
                        fecha_parto = _parse_date(v('Fecha de Parto', 'Parto', 'Fecha Parto'))
                        if fecha_parto:
                            vals['fecha_parto'] = fecha_parto
                        # Check 1: CCSS 50% + Patrono 50%
                        split_raw = v('Maternidad 50/50', 'Maternidad 50', 'Split 50')
                        if split_raw is not None:
                            vals['maternity_split_50'] = _parse_bool(split_raw)
                        # Check 2: Cobrar CCSS sobre parte patronal
                        ccss_raw = v('Cobrar CCSS', 'CCSS s/patronal', 'CCSS obrera')
                        if ccss_raw is not None:
                            vals['maternity_ccss_on_employer'] = _parse_bool(ccss_raw)
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

            vals = {}  # BUG FIX: inicializar antes del try (el except usa vals.items())
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

            vals = {}  # BUG FIX: inicializar antes del try (el except usa vals.items())
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

