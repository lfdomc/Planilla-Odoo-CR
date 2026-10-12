"""
Procesadores de importacion masiva -- Planilla CR v5.6
Cada procesador es un metodo del wizard ImportDataWizard.
Se importan desde import_data_wizard.py via herencia multiple.
"""
import logging
import re
import traceback  # FIX-L3: faltaba -- usado en bloques except
from datetime import date
from odoo import models, api
from odoo.exceptions import UserError
from ...models import planilla_const as K
from ..import_parse_utils import _map, _normalize, _parse_bool, _parse_date, _parse_float, _parse_int

_logger = logging.getLogger(__name__)

class ImportProcessorEmployees(models.AbstractModel):
    """Procesador de empleados (hoja EMPLEADOS del machote Excel)."""
    _name = 'planilla.import.processor.employees'
    _description = 'Procesador Importacion Empleados'

    def _clean_iban(self, iban_raw, warnings_row=None):
        """Valida el IBAN con las mismas 3 reglas que
        hr_employee_extension.py::_check_bank_iban (empieza con 'CR',
        22 caracteres, solo digitos despues de 'CR'). Si no pasa, retorna
        False (campo vacio) y agrega una advertencia en vez de dejar que
        Odoo rechace el create() con una ValidationError -- evita disparar
        el flujo de reintento por completo (y con el, cualquier bug
        lateral de Odoo que pueda activarse durante el manejo de esa
        excepcion, como el bug de hr_skills que ya vimos con casos como
        IBAN='PENDIENTE').
        """
        if not iban_raw:
            return False
        iban_str = str(iban_raw).strip()
        if not iban_str:
            return False
        iban_norm = iban_str.replace(' ', '').replace('-', '').upper()
        motivo = None
        if not iban_norm.startswith('CR'):
            motivo = f'"{iban_str}" no es un IBAN valido (debe empezar con "CR")'
        elif len(iban_norm) != 22:
            motivo = (f'"{iban_str}" no tiene la longitud correcta '
                       f'(debe ser CR + 20 digitos = 22 caracteres)')
        elif not iban_norm[2:].isdigit():
            motivo = f'"{iban_str}" debe contener solo digitos despues de "CR"'
        if motivo:
            if warnings_row is not None:
                warnings_row.append((
                    'IBAN',
                    f'{motivo} -- campo dejado vacio, complete manualmente '
                    f'el IBAN correcto en el empleado',
                ))
            return False
        return iban_norm

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
            warnings_row = []  # (campo_excel, texto_no_encontrado) -- se reporta aunque el empleado se cree OK
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
                    if dept_name and not dept:
                        dept = self.env['hr.department'].create({
                            'name': dept_name,
                            'company_id': company.id,
                        })
                        warnings_row.append((
                            'Departamento',
                            f'"{dept_name}" no existia -- se creo automaticamente en Odoo',
                        ))

                    branch  = self._find_m2o('planilla.branch', branch_name,
                                extra_domain=['|', ('company_id', '=', company.id),
                                                   ('company_id', '=', False)])
                    if branch_name and not branch:
                        warnings_row.append(('Sucursal', branch_name))

                    job     = self._find_m2o('hr.job', job_name,
                                extra_domain=['|', ('company_id', '=', company.id),
                                                   ('company_id', '=', False)])
                    if job_name and not job:
                        job = self.env['hr.job'].create({
                            'name': job_name,
                            'company_id': company.id,
                        })
                        warnings_row.append((
                            'Puesto / Cargo',
                            f'"{job_name}" no existia -- se creo automaticamente en Odoo',
                        ))

                    sched   = self._find_m2o('planilla.schedule.type', sched_name)
                    # Si no encontro por nombre completo, intentar con las
                    # primeras palabras (ej: "Jornada Completa" de
                    # "Jornada Completa (8 horas - Lun a Vie)")
                    if not sched and sched_name:
                        short_name = sched_name.split('(')[0].strip()
                        if short_name != sched_name:
                            sched = self._find_m2o('planilla.schedule.type', short_name)
                    if sched_name and not sched:
                        warnings_row.append(('Tipo de Horario', sched_name))

                    cal     = self._find_m2o('planilla.calendar', cal_name,
                                extra_domain=['|', ('company_id', '=', company.id),
                                                   ('company_id', '=', False)])
                    etype   = self._find_m2o('planilla.employee.type', etype_name)
                    if etype_name and not etype:
                        warnings_row.append(('Tipo de Empleado', etype_name))

                    estatus = self._find_m2o('planilla.employee.status', estatus_name)
                    if estatus_name and not estatus:
                        warnings_row.append(('Estado del Empleado', estatus_name))

                    # Sub departamento -- buscar dentro del dpto padre si se encontro
                    subdept = None
                    if subdept_name:
                        subdept_domain = ['|', ('company_id', '=', company.id),
                                               ('company_id', '=', False)]
                        if dept:
                            subdept_domain.append(('parent_id', '=', dept.id))
                        subdept = self._find_m2o('hr.department', subdept_name,
                                    extra_domain=subdept_domain)
                        if not subdept:
                            create_vals_subdept = {
                                'name': subdept_name,
                                'company_id': company.id,
                            }
                            if dept:
                                create_vals_subdept['parent_id'] = dept.id
                            subdept = self.env['hr.department'].create(create_vals_subdept)
                            warnings_row.append((
                                'Sub Departamento',
                                f'"{subdept_name}" no existia -- se creo automaticamente en Odoo'
                                + (f' (dentro de "{dept_name}")' if dept else ''),
                            ))

                    # Si no se encontro calendario por nombre, buscar por frecuencia
                    if not cal:
                        freq_raw = _normalize(v('Calendarizacion de Planilla', 'Frecuencia', 'Calendario', 'Frecuencia de Pago') or '')
                        freq_val = K.FREQUENCY.get(freq_raw)
                        if freq_val:
                            cal = self.env['planilla.calendar'].sudo().search([
                                '|',
                                ('company_id', '=', company.id),
                                ('company_id', '=', False),
                                ('frequency', '=', freq_val),
                            ], limit=1) or None
                        if not cal and cal_name:
                            warnings_row.append((
                                'Calendarizacion de Planilla', cal_name,
                            ))

                    # Identificacion type
                    id_type_raw  = _normalize(v('Tipo de Identificacion', 'Tipo Identificacion') or '')
                    id_type_code = K.INS_ID_TYPE.get(id_type_raw, 'CI')      # code en planilla.identification.type
                    ins_id_code  = K.INS_ID_TYPE_CODE.get(id_type_raw, '01') # codigo numerico para INS
                    id_type_rec  = self.env['planilla.identification.type'].search(
                        [('code', '=', id_type_code)], limit=1)
                    if id_type_raw and not id_type_rec:
                        warnings_row.append((
                            'Tipo de Identificacion',
                            f'{v("Tipo de Identificacion", "Tipo Identificacion")} '
                            f'(no existe el catalogo "{id_type_code}" en Odoo)',
                        ))

                    vals = {
                        'name':                       nombre,
                        'identification_id':          cedula,
                        'company_id':                 company.id,
                        'entry_date':                 _parse_date(v('Fecha de Ingreso', 'Fecha Ingreso')),
                        'exit_date':                  _parse_date(v('Fecha de Salida', 'Fecha Salida')),
                        'work_email':                 v('Correo', 'Email') or False,
                        'base_salary':                _parse_float(v('Salario Base', 'Salario')),
                        'salary_effective_date':      _parse_date(v('Fecha Vigencia', 'Vigencia Salarial')),
                        'payroll_calculation_method': _map(K.CALC_METHOD, v('Metodo', 'Metodo', 'Metodo de Calculo')) or 'fixed',
                        'ccss_number':                str(v('CCSS', 'Numero CCSS', 'Numero CCSS') or '').strip() or False,
                        'ccss_insured':               _parse_bool(v('Asegurado CCSS', 'CCSS Asegurado')),
                        'has_variable_income':        _parse_bool(v('Salario Variable', 'Comisiones', 'Ingreso Variable')),
                        'bank_account_number':        str(v('Cuenta Bancaria', 'Cuenta') or '').strip() or False,
                        'bank_iban':                  self._clean_iban(v('IBAN'), warnings_row),
                        'sinpe_phone': re.sub(r'\D', '', str(v('SINPE', 'Sinpe Movil', 'Sinpe Movil') or ''))[:8] or False,
                        'bank_name':                  _map(K.BANK, v('Banco')) or False,
                        'bank_account_type':          _map(K.ACCOUNT_TYPE, v('Tipo de Cuenta Banco', 'Tipo de Cuenta')) or False,
                        # INS
                        'ins_include':               _parse_bool(v('Incluir INS', 'Incluir en INS')),
                        'ins_policy_number':         str(v('Poliza INS', 'Poliza INS', 'Numero de Poliza') or '').strip() or False,
                        'ins_first_name':            str(v('Nombre INS') or '').strip() or False,
                        'ins_first_lastname':        str(v('Primer Apellido INS') or '').strip() or False,
                        'ins_second_lastname':       str(v('Segundo Apellido INS') or '').strip() or False,
                        'ins_risk_class':            _map(K.INS_RISK, v('Clase de Riesgo', 'Riesgo INS')) or False,
                        'ins_workday_type':          _map(K.INS_WORKDAY, v('Jornada INS', 'Tipo de Jornada INS', 'Tipo de Jornada')) or '01',
                        'ins_civil_status':          _map(K.INS_CIVIL, v('Estado Civil INS', 'Estado Civil')) or '01',
                        'ins_id_type':               ins_id_code,
                        'ins_nationality':           _map(K.INS_NATIONALITY, v('Nacionalidad INS', 'Nacionalidad')) or 'CR',
                    }

                    # -- Advertencias de campos mapeados que no coincidieron --
                    # Estos campos usan _map(): si el texto del Excel no esta
                    # en el catalogo de valores conocidos, _map() devuelve
                    # None silenciosamente y el codigo de arriba lo reemplaza
                    # con False o un valor por defecto ('fixed', '01', 'CR').
                    # Sin esto, el usuario nunca se entera de que, por
                    # ejemplo, el banco quedo vacio porque escribio
                    # "Banco Nacional de CR" en vez de "Banco Nacional".
                    _mapped_checks = [
                        ('Metodo de Calculo',   v('Metodo', 'Metodo de Calculo'), K.CALC_METHOD),
                        ('Banco',               v('Banco'), K.BANK),
                        ('Tipo de Cuenta Banco', v('Tipo de Cuenta Banco', 'Tipo de Cuenta'), K.ACCOUNT_TYPE),
                        ('Clase de Riesgo INS',  v('Clase de Riesgo', 'Riesgo INS'), K.INS_RISK),
                        ('Tipo de Jornada INS',  v('Jornada INS', 'Tipo de Jornada INS', 'Tipo de Jornada'), K.INS_WORKDAY),
                        ('Estado Civil INS',     v('Estado Civil INS', 'Estado Civil'), K.INS_CIVIL),
                        ('Nacionalidad INS',     v('Nacionalidad INS', 'Nacionalidad'), K.INS_NATIONALITY),
                        ('Genero',               v('Genero'), K.GENDER),
                    ]
                    for campo_label, raw_val, tabla in _mapped_checks:
                        if raw_val and _map(tabla, raw_val) is None:
                            warnings_row.append((
                                campo_label,
                                f'"{raw_val}" no coincide con ningun valor conocido -- '
                                f'se uso el valor por defecto',
                            ))

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
                    elif blood_raw:
                        warnings_row.append((
                            'Tipo de Sangre',
                            f'"{blood_raw}" no es un tipo valido (A+, A-, B+, B-, AB+, AB-, O+, O-)',
                        ))
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
                        if not country:
                            warnings_row.append(('Pais', country_raw))

                    _personal = {
                        'gender':            _map(K.GENDER, v('Genero', 'Genero')) or False,
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

                    # Crear empleado. El IBAN ya se valido preventivamente
                    # en _clean_iban() al construir vals (mismas 3 reglas
                    # que _check_bank_iban en hr_employee_extension.py) --
                    # si el texto del Excel no era un IBAN valido, bank_iban
                    # ya llega como False aqui, con la advertencia
                    # correspondiente ya registrada en warnings_row. Este
                    # bloque de reintento se conserva solo como red de
                    # seguridad residual, por si algun caso de borde no
                    # cubierto exactamente igual por ambas validaciones
                    # llegara a fallar de todas formas.
                    #
                    # FIX BUG ODOO 19 (hr_skills): el modulo nativo hace
                    # algo equivalente a:
                    #   vals.pop('current_employee_skill_ids', []) + vals.pop('employee_skill_ids', [])
                    # Si CUALQUIERA de esas dos claves esta presente en vals
                    # con valor None (aunque nuestro codigo nunca la haya
                    # escrito asi), pop() retorna None en vez del default []
                    # porque la clave SI existe, y la concatenacion revienta
                    # con "can only concatenate list (not NoneType) to list".
                    # El log tecnico confirmo 'employee_skill_ids: None' en
                    # los vals reportados en el error -- se blinda AMBOS
                    # campos aqui, ANTES del primer create(), no solo en el
                    # reintento por IBAN invalido (el bug puede dispararse
                    # en el primer intento tambien, no solo en el segundo).
                    vals.setdefault('employee_skill_ids', [])
                    vals.setdefault('current_employee_skill_ids', [])
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
                            # Re-blindar por si el create() fallido dejo
                            # estas claves en un estado distinto (ver
                            # explicacion completa arriba).
                            vals['employee_skill_ids'] = []
                            vals['current_employee_skill_ids'] = []
                            self.env['hr.employee'].create(vals)
                            created += 1
                            errors.append({
                                'hoja': 'EMPLEADOS', 'fila': row_num,
                                'cedula': cedula, 'nombre': nombre,
                                'error': f'ADVERTENCIA: Empleado creado SIN IBAN -- {str(e_create).strip()} Corrija el IBAN manualmente en el empleado.',
                                'traceback': '',
                                'vals': {},
                            })
                            _logger.warning('ImportDataWizard EMPLEADOS fila %s cedula %s: IBAN invalido %s -- empleado creado sin IBAN',
                                            row_num, cedula, iban_original)
                            continue
                        else:
                            raise
                    created += 1

                    # -- Reportar advertencias de campos no vinculados --------
                    # El empleado SI se creo, pero uno o mas campos del Excel
                    # no coincidieron con ningun registro/catalogo en Odoo y
                    # quedaron vacios (o con el valor por defecto). Antes esto
                    # pasaba en silencio -- el usuario solo lo notaba al abrir
                    # el empleado y ver campos vacios, sin saber por que.
                    if warnings_row:
                        def _fmt(campo, valor):
                            if 'se creo automaticamente' in valor:
                                return f'{campo}: {valor}'
                            return f'{campo}: "{valor}" no encontrado -- campo dejado vacio'
                        detalle = '; '.join(_fmt(c, v) for c, v in warnings_row)
                        n_creados = sum(
                            1 for _, valor in warnings_row if 'se creo automaticamente' in valor
                        )
                        n_vacios = len(warnings_row) - n_creados
                        partes_resumen = []
                        if n_creados:
                            partes_resumen.append(f'{n_creados} campo(s) se crearon automaticamente')
                        if n_vacios:
                            partes_resumen.append(f'{n_vacios} campo(s) quedaron vacios')
                        errors.append({
                            'hoja': 'EMPLEADOS', 'fila': row_num,
                            'cedula': cedula, 'nombre': nombre,
                            'error': (
                                f'ADVERTENCIA: Empleado creado OK -- '
                                f'{" y ".join(partes_resumen)}: {detalle}. '
                                f'Revise que los valores creados automaticamente '
                                f'sean correctos (sin errores de tipeo ni '
                                f'duplicados), y complete manualmente los que '
                                f'quedaron vacios si son necesarios.'
                            ),
                            'traceback': '',
                            'vals': {},
                        })
                        _logger.warning(
                            'ImportDataWizard EMPLEADOS fila %s cedula %s: '
                            '%d campo(s) con advertencia -- %s',
                            row_num, cedula, len(warnings_row), detalle,
                        )

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

