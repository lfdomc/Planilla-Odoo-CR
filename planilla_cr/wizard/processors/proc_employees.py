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
            cedula = str(v('Cédula', 'Cedula', 'Identificación', 'Identificacion') or '').strip()
            nombre = str(v('Nombre') or '').strip()

            if not cedula or not nombre:
                continue
            if self._is_sample(cedula) and not self.import_sample_data:
                continue
            if self._find_employee(cedula):
                skipped += 1
                continue

            try:
                with self.env.cr.savepoint():
                    # Many2one lookups
                    company = self.company_id
                    dept_name    = v('Departamento')
                    subdept_name = v('Sub Departamento', 'Sub-Departamento', 'Subdepartamento')
                    branch_name  = v('Sucursal')
                    job_name     = v('Puesto', 'Cargo')
                    sched_name   = v('Tipo de Horario', 'Horario')
                    cal_name     = v('Frecuencia', 'Calendario')
                    etype_name   = v('Tipo de Empleado')
                    estatus_name = v('Estado del Empleado', 'Estado')

                    dept    = self._find_m2o('hr.department', dept_name,
                                extra_domain=[('company_id', '=', company.id)])
                    branch  = self._find_m2o('planilla.branch', branch_name,
                                extra_domain=[('company_id', '=', company.id)])
                    job     = self._find_m2o('hr.job', job_name,
                                extra_domain=[('company_id', '=', company.id)])
                    sched   = self._find_m2o('planilla.schedule.type', sched_name)
                    cal     = self._find_m2o('planilla.calendar', cal_name,
                                extra_domain=[('company_id', '=', company.id)])
                    etype   = self._find_m2o('planilla.employee.type', etype_name)
                    estatus = self._find_m2o('planilla.employee.status', estatus_name)

                    # Sub departamento — buscar dentro del dpto padre si se encontró
                    subdept = None
                    if subdept_name:
                        subdept_domain = [('company_id', '=', company.id)]
                        if dept:
                            subdept_domain.append(('parent_id', '=', dept.id))
                        subdept = self._find_m2o('hr.department', subdept_name,
                                    extra_domain=subdept_domain)

                    # Si no se encontró calendario por nombre, buscar por frecuencia
                    if not cal:
                        freq_raw = _normalize(v('Frecuencia', 'Calendario', 'Frecuencia de Pago') or '')
                        freq_val = FREQUENCY.get(freq_raw)
                        if freq_val:
                            cal = self.env['planilla.calendar'].search([
                                ('frequency', '=', freq_val),
                                ('company_id', '=', company.id),
                            ], limit=1) or None

                    # Identificación type
                    id_type_raw  = _normalize(v('Tipo de Identificación', 'Tipo Identificacion') or '')
                    id_type_code = INS_ID_TYPE.get(id_type_raw, '01')
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
                        'payroll_calculation_method': _map(CALC_METHOD, v('Método', 'Metodo', 'Método de Cálculo')) or 'fixed',
                        'ccss_number':                str(v('CCSS', 'Número CCSS', 'Numero CCSS') or '').strip() or False,
                        'ccss_insured':               _parse_bool(v('Asegurado CCSS', 'CCSS Asegurado')),
                        'bank_account_number':        str(v('Cuenta Bancaria', 'Cuenta') or '').strip() or False,
                        'bank_iban':                  str(v('IBAN') or '').strip() or False,
                        'sinpe_phone':                str(v('SINPE', 'Sinpe Móvil', 'Sinpe Movil') or '').strip() or False,
                        'bank_name':                  _map(BANK, v('Banco')) or False,
                        'bank_account_type':          _map(ACCOUNT_TYPE, v('Tipo de Cuenta Banco', 'Tipo de Cuenta')) or False,
                        # INS
                        'ins_include':               _parse_bool(v('Incluir INS', 'Incluir en INS')),
                        'ins_policy_number':         str(v('Póliza INS', 'Poliza INS', 'Número de Póliza') or '').strip() or False,
                        'ins_first_name':            str(v('Nombre INS') or '').strip() or False,
                        'ins_first_lastname':        str(v('Primer Apellido INS') or '').strip() or False,
                        'ins_second_lastname':       str(v('Segundo Apellido INS') or '').strip() or False,
                        'ins_risk_class':            _map(INS_RISK, v('Clase de Riesgo', 'Riesgo INS')) or False,
                        'ins_workday_type':          _map(INS_WORKDAY, v('Jornada INS', 'Tipo de Jornada INS', 'Tipo de Jornada')) or '01',
                        'ins_civil_status':          _map(INS_CIVIL, v('Estado Civil INS', 'Estado Civil')) or '01',
                        'ins_id_type':               id_type_code,
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

                    # INS occupation (código numérico de 4 dígitos)
                    ins_occ_raw = str(v('Ocupación INS', 'Ocupacion INS') or '').strip()
                    if ins_occ_raw:
                        vals['ins_occupation'] = ins_occ_raw

                    # Campos personales estándar de hr.employee — pueden no existir
                    # según la versión de Odoo o si están en hr.employee.private.
                    # Los agregamos solo si el campo existe en el modelo.
                    emp_fields = self.env['hr.employee']._fields
                    _personal = {
                        'gender':        _map(GENDER, v('Género', 'Genero')) or False,
                        'children':      _parse_int(v('Número de Dependientes', 'Dependientes')) or 0,
                        'private_street': str(v('Dirección', 'Direccion') or '').strip() or False,
                        'private_phone': str(v('Teléfono Personal', 'Telefono Personal') or '').strip() or False,
                        'notes':         str(v('Observaciones') or '').strip() or False,
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

                    self.env['hr.employee'].create(vals)
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

