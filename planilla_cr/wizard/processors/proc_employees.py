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
from ..import_parse_utils import _map, _normalize, _parse_bool, _parse_date, _parse_float

_logger = logging.getLogger(__name__)

class ImportProcessorEmployees(models.AbstractModel):
    """Procesador de empleados (hoja EMPLEADOS del machote Excel)."""
    _name = 'planilla.import.processor.employees'
    _description = 'Procesador Importacion Empleados'

    def _process_employees(self, wb, errors):
        created = skipped = err_count = 0
        hdrs, rows = self._sheet_rows(wb, ['EMPLEADO', 'EMPLOYEE'])
        if not rows:
            return 0, 0, 0

        for row_num, row in enumerate(rows, start=1):
            v = lambda *cols: self._v(row, hdrs, *cols)
            cedula = str(v('Cedula', 'Cedula', 'Identificacion', 'Identificacion') or '').strip()
            nombre = str(v('Nombre') or '').strip()

            if not cedula or not nombre:
                continue
            if self._is_sample(cedula) and not self.import_sample_data:
                continue
            if self._find_employee(cedula):
                skipped += 1
                continue

            vals = {}  # BUG FIX: inicializar antes del try (el except usa vals.items())
            try:
                with self.env.cr.savepoint():
                    # Many2one lookups
                    company = self.company_id
                    dept_name    = v('Departamento')
                    subdept_name = v('Sub Departamento', 'Sub-Departamento', 'Subdepartamento')
                    branch_name  = v('Sucursal')
                    job_name     = v('Puesto', 'Cargo')
                    sched_name   = v('Tipo de Horario', 'Horario')
                    cal_name     = v('Calendarizacion de Planilla', 'Frecuencia', 'Calendario', 'Frecuencia de Pago')
                    etype_name   = v('Tipo de Empleado')
                    estatus_name = v('Estado del Empleado', 'Estado')

                    dept    = self._find_m2o('hr.department', dept_name,
                                extra_domain=['|', ('company_id', '=', company.id),
                                                   ('company_id', '=', False)])
                    branch  = self._find_m2o('planilla.branch', branch_name,
                                extra_domain=['|', ('company_id', '=', company.id),
                                                   ('company_id', '=', False)])
                    job     = self._find_m2o('hr.job', job_name,
                                extra_domain=['|', ('company_id', '=', company.id),
                                                   ('company_id', '=', False)])
                    sched   = self._find_m2o('planilla.schedule.type', sched_name)
                    # Si no encontro por nombre completo, intentar con las
                    # primeras palabras (ej: "Jornada Completa" de
                    # "Jornada Completa (8 horas - Lun a Vie)")
                    if not sched and sched_name:
                        short_name = sched_name.split('(')[0].strip()
                        if short_name != sched_name:
                            sched = self._find_m2o('planilla.schedule.type', short_name)
                    cal     = self._find_m2o('planilla.calendar', cal_name,
                                extra_domain=['|', ('company_id', '=', company.id),
                                                   ('company_id', '=', False)])
                    etype   = self._find_m2o('planilla.employee.type', etype_name)
                    estatus = self._find_m2o('planilla.employee.status', estatus_name)

                    # Sub departamento -- buscar dentro del dpto padre si se encontro
                    subdept = None
                    if subdept_name:
                        subdept_domain = ['|', ('company_id', '=', company.id),
                                               ('company_id', '=', False)]
                        if dept:
                            subdept_domain.append(('parent_id', '=', dept.id))
                        subdept = self._find_m2o('hr.department', subdept_name,
                                    extra_domain=subdept_domain)

                    # Si no se encontro calendario por nombre, buscar por frecuencia
                    if not cal:
                        freq_raw = _normalize(v('Calendarizacion de Planilla', 'Frecuencia', 'Calendario', 'Frecuencia de Pago') or '')
                        freq_val = FREQUENCY.get(freq_raw)
                        if freq_val:
                            cal = self.env['planilla.calendar'].sudo().search([
                                '|',
                                ('company_id', '=', company.id),
                                ('company_id', '=', False),
                                ('frequency', '=', freq_val),
                            ], limit=1) or None

                    # Identificacion type
                    id_type_raw  = _normalize(v('Tipo de Identificacion', 'Tipo Identificacion') or '')
                    id_type_code = INS_ID_TYPE.get(id_type_raw, 'CI')      # code en planilla.identification.type
                    ins_id_code  = INS_ID_TYPE_CODE.get(id_type_raw, '01') # codigo numerico para INS
                    id_type_rec  = self.env['planilla.identification.type'].search(
                        [('code', '=', id_type_code)], limit=1)

                    vals = {
                        'name':                       nombre,
                        'identification_id':          cedula,
                        'company_id':                 company.id,
                        'entry_date':                 _parse_date(v('Fecha de Ingreso', 'Fecha Ingreso')),
                        'exit_date':                  _parse_date(v('Fecha de Salida', 'Fecha Salida')),
                        'work_email':                 v('Correo', 'Email') or False,
                        'base_salary':                _parse_float(v('Salario Base', 'Salario')),
                        'salary_effective_date':      _parse_date(v('Fecha Vigencia', 'Vigencia Salarial')),
                        'payroll_calculation_method': _map(CALC_METHOD, v('Metodo', 'Metodo', 'Metodo de Calculo')) or 'fixed',
                        'ccss_number':                str(v('CCSS', 'Numero CCSS', 'Numero CCSS') or '').strip() or False,
                        'ccss_insured':               _parse_bool(v('Asegurado CCSS', 'CCSS Asegurado')),
                        'has_variable_income':        _parse_bool(v('Salario Variable', 'Comisiones', 'Ingreso Variable')),
                        'bank_account_number':        str(v('Cuenta Bancaria', 'Cuenta') or '').strip() or False,
                        'bank_iban':                  str(v('IBAN') or '').strip() or False,
                        'sinpe_phone': re.sub(r'\D', '', str(v('SINPE', 'Sinpe Movil', 'Sinpe Movil') or ''))[:8] or False,
                        'bank_name':                  _map(BANK, v('Banco')) or False,
                        'bank_account_type':          _map(ACCOUNT_TYPE, v('Tipo de Cuenta Banco', 'Tipo de Cuenta')) or False,
                        # INS
                        'ins_include':               _parse_bool(v('Incluir INS', 'Incluir en INS')),
                        'ins_policy_number':         str(v('Poliza INS', 'Poliza INS', 'Numero de Poliza') or '').strip() or False,
                        'ins_first_name':            str(v('Nombre INS') or '').strip() or False,
                        'ins_first_lastname':        str(v('Primer Apellido INS') or '').strip() or False,
                        'ins_second_lastname':       str(v('Segundo Apellido INS') or '').strip() or False,
                        'ins_risk_class':            _map(INS_RISK, v('Clase de Riesgo', 'Riesgo INS')) or False,
                        'ins_workday_type':          _map(INS_WORKDAY, v('Jornada INS', 'Tipo de Jornada INS', 'Tipo de Jornada')) or '01',
                        'ins_civil_status':          _map(INS_CIVIL, v('Estado Civil INS', 'Estado Civil')) or '01',
                        'ins_id_type':               ins_id_code,
                        'ins_nationality':           _map(INS_NATIONALITY, v('Nacionalidad INS', 'Nacionalidad')) or 'CR',
                    }

                    # Relacionales opcionales
                    if dept:    vals['department_id']       = dept.id
                    if subdept: vals['sub_department_id']   = subdept.id
                    if branch:  vals['branch_id']           = branch.id
                    if job:     vals['job_id']              = job.id
                    if sched:   vals['schedule_type_id']    = sched.id
                    if cal:     vals['payroll_calendar_id'] = cal.id
                    if etype:   vals['employee_type_id']    = etype.id
                    if estatus: vals['employee_status_id']  = estatus.id
                    if id_type_rec: vals['identification_type_id'] = id_type_rec.id

                    # INS occupation -- acepta codigo numerico (4 digitos) o
                    # texto completo del dropdown "[1120] Directores y gerentes generales"
                    ins_occ_raw = str(v('Ocupacion INS', 'Ocupacion INS') or '').strip()
                    if ins_occ_raw:
                        # Extraer solo el codigo si viene como "[1120] Descripcion..."
                        import re as _re
                        _occ_match = _re.match(r'\[(\d{4})\]', ins_occ_raw)
                        vals['ins_occupation'] = _occ_match.group(1) if _occ_match else ins_occ_raw

                    # Tipo de sangre y notas medicas
                    blood_raw = str(v('Tipo de Sangre', 'Sangre') or '').strip().upper()
                    if blood_raw in ('A+','A-','B+','B-','AB+','AB-','O+','O-'):
                        vals['blood_type'] = blood_raw
                    medical = str(v('Diagnostico', 'Diagnostico', 'Notas Medicas', 'Notas Medicas') or '').strip()
                    if medical:
                        vals['medical_notes'] = medical

                    # Campos personales estandar de hr.employee -- pueden no existir
                    # segun la version de Odoo o si estan en hr.employee.private.
                    # Los agregamos solo si el campo existe en el modelo.
                    emp_fields = self.env['hr.employee']._fields

                    # Pais: buscar por nombre en res.country
                    country_raw = str(v('Pais', 'Pais') or '').strip()
                    country_id  = False
                    if country_raw:
                        country = self.env['res.country'].search(
                            [('name', 'ilike', country_raw)], limit=1)
                        country_id = country.id if country else False

                    _personal = {
                        'gender':            _map(GENDER, v('Genero', 'Genero')) or False,
                        'children':          _parse_int(v('Numero de Dependientes', 'Dependientes')) or 0,
                        'private_street':    str(v('Direccion', 'Direccion') or '').strip() or False,
                        'private_phone':     str(v('Telefono Personal', 'Telefono Personal') or '').strip() or False,
                        'private_email':     str(v('Correo Personal', 'Email Personal') or '').strip() or False,
                        'private_country_id': country_id or False,
                        'notes':             str(v('Observaciones') or '').strip() or False,
                    }
                    for fname, fval in _personal.items():
                        if fname in emp_fields and fval:
                            vals[fname] = fval

                    # Work contact (requerido por hr.employee en Odoo 17+)
                    contact = self.env['res.partner'].create({
                        'name':  nombre,
                        'email': vals.get('work_email') or False,
                        'company_type': 'person',
                    })
                    vals['work_contact_id'] = contact.id

                    # Crear empleado. Si el IBAN falla la validacion del
                    # digito verificador, reintentar sin el IBAN para no
                    # bloquear toda la importacion -- el IBAN se puede
                    # corregir manualmente despues.
                    try:
                        new_emp = self.env['hr.employee'].create(vals)
                        # Crear contrato nativo de HR para sincronizar con pestaa Nmina
                        try:
                            new_emp._get_or_create_contract()
                        except Exception:
                            pass  # No bloquear importacion si el contrato falla
                    except Exception as e_create:
                        if 'iban' in str(e_create).lower() or 'digito verificador' in str(e_create).lower():
                            iban_original = vals.pop('bank_iban', None)
                            vals.pop('bank_account_number', None)
                            self.env['hr.employee'].create(vals)
                            created += 1
                            errors.append({
                                'hoja': 'EMPLEADOS', 'fila': row_num,
                                'cedula': cedula, 'nombre': nombre,
                                'error': f'ADVERTENCIA: Empleado creado SIN IBAN -- digito verificador invalido: {iban_original}. Corrija el IBAN manualmente en el empleado.',
                                'traceback': '',
                                'vals': {},
                            })
                            _logger.warning('ImportDataWizard EMPLEADOS fila %s cedula %s: IBAN invalido %s -- empleado creado sin IBAN',
                                            row_num, cedula, iban_original)
                            continue
                        else:
                            raise
                    created += 1

            except Exception as e:
                err_count += 1
                errors.append({
                    'hoja': 'EMPLEADOS', 'fila': row_num,
                    'cedula': cedula, 'nombre': nombre,
                    'error': str(e),
                    'traceback': traceback.format_exc(),
                    'vals': {k: str(val)[:120] for k, val in vals.items()},
                })
                _logger.warning('ImportDataWizard EMPLEADOS fila %s cedula %s: %s',
                                row_num, cedula, e)

        return created, skipped, err_count

