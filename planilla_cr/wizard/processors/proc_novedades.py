"""
Procesadores de importación masiva — Planilla CR v5.6
Cada procesador es un método del wizard ImportDataWizard.
Se importan desde import_data_wizard.py via herencia múltiple.
"""
import logging
import traceback  # FIX-L3: faltaba — usado en bloques except
from datetime import date
from odoo import models, api
from odoo.exceptions import UserError
from ...models import planilla_const as K

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
                        'employer_percentage':  _parse_float(v('% Patrono', 'Cargo Patrono')) or 0.0,
                        # FIX-B3b: sync con fix A2 — default 0.0 (no 40.0). Art. 79 Regl. CCSS.
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
                    'vals': {k: str(val)[:120] for k, val in vals.items()},
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
                    'vals': {k: str(val)[:120] for k, val in vals.items()},
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
                    'vals': {k: str(val)[:120] for k, val in vals.items()},
                })
                _logger.warning('ImportDataWizard OVERTIME fila %s: %s', row_num, e)

        return created, err_count

    # ══════════════════════════════════════════════════════════════════════════
    # PROCESADOR EMBARGOS JUDICIALES
    # ══════════════════════════════════════════════════════════════════════════

