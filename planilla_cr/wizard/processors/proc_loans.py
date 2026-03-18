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

class ImportProcessorLoans(models.AbstractModel):
    """Procesadores de préstamos, pensiones alimentarias y beneficios recurrentes."""
    _name = 'planilla.import.processor.loans'
    _description = 'Procesador Importacion Prestamos y Pensiones'

    def _process_loans(self, wb, errors):
        created = err_count = 0
        hdrs, rows = self._sheet_rows(wb, ['PRESTAMO', 'LOAN'])
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
                    'hoja': 'PRESTAMOS', 'fila': row_num, 'cedula': cedula,
                    'nombre': '', 'error': 'Empleado no encontrado en Odoo',
                })
                continue

            try:
                with self.env.cr.savepoint():
                    amount_total = _parse_float(v('Monto Total', 'Monto'))
                    installments = _parse_int(v('Número de Cuotas', 'Cuotas', 'Installments'))
                    date_granted = _parse_date(v('Fecha de Otorgamiento', 'Fecha Otorgamiento'))
                    date_first   = _parse_date(v('Fecha Primera Deducción', 'Primera Deduccion'))
                    state_raw    = _map(LOAN_STATE, v('Estado')) or 'approved'
                    loan_type    = _map(LOAN_TYPE, v('Tipo de Préstamo', 'Tipo')) or 'loan'
                    amount_paid  = _parse_float(v('Monto ya Pagado', 'Monto Pagado'))

                    if amount_total <= 0 or installments <= 0:
                        raise ValueError('Monto total e instalamentos deben ser > 0')

                    loan = self.env['planilla.employee.loan'].create({
                        'employee_id':         emp.id,
                        'loan_type':           loan_type,
                        'description':         str(v('Descripción', 'Descripcion', 'Motivo') or '').strip() or False,
                        'amount_total':        amount_total,
                        'installments':        installments,
                        'date_granted':        date_granted or date.today(),
                        'date_first_deduction': date_first or date.today(),
                        'state':               'approved',
                        'note':                str(v('Observaciones') or '').strip() or False,
                    })

                    # Generar cuotas automáticamente
                    loan._generate_installments()

                    # Si ya tiene pagos parciales, marcar cuotas como descontadas
                    if amount_paid > 0 and loan.installment_ids:
                        remaining = amount_paid
                        for inst in loan.installment_ids.sorted('due_date'):
                            if remaining <= 0:
                                break
                            if remaining >= inst.amount:
                                inst.state = 'deducted'
                                remaining -= inst.amount
                            else:
                                break
                        loan._compute_amounts()

                    # Activar si el estado final solicitado es active
                    if state_raw == 'active' and loan.state == 'approved':
                        loan.state = 'active'

                    created += 1

            except Exception as e:
                err_count += 1
                errors.append({
                    'hoja': 'PRESTAMOS', 'fila': row_num, 'cedula': cedula,
                    'nombre': emp.name, 'error': str(e),
                    'traceback': traceback.format_exc(),
                    'vals': {k: str(val)[:120] for k, val in vals.items()},
                })
                _logger.warning('ImportDataWizard PRESTAMOS fila %s: %s', row_num, e)

        return created, err_count

    def _process_pension(self, wb, errors):
        created = err_count = 0
        hdrs, rows = self._sheet_rows(wb, ['PENSION', 'PENSIÓN'])
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
                    'hoja': 'PENSION_ALIMENTARIA', 'fila': row_num, 'cedula': cedula,
                    'nombre': '', 'error': 'Empleado no encontrado en Odoo',
                })
                continue

            try:
                with self.env.cr.savepoint():
                    calc_type = _map(PENSION_CALC, v('Tipo de Cálculo', 'Tipo Calculo')) or 'fixed'
                    pct_raw   = _parse_float(v('Porcentaje'))
                    monto_raw = _parse_float(v('Monto Fijo', 'Monto'))

                    branch = self._find_m2o('planilla.branch', v('Sucursal'),
                                extra_domain=[('company_id', '=', self.company_id.id)])

                    vals = {
                        'employee_id':          emp.id,
                        'numero_expediente':    str(v('Expediente', 'Número de Expediente') or '').strip() or False,
                        'juzgado':              str(v('Juzgado') or '').strip() or False,
                        'fecha_resolucion':     _parse_date(v('Fecha de Resolución', 'Fecha Resolucion')),
                        'beneficiario_nombre':  str(v('Beneficiario', 'Nombre Beneficiario') or '').strip() or False,
                        'beneficiario_relacion': _map(PENSION_RELATION, v('Relación', 'Relacion')) or 'hijo',
                        'beneficiario_cuenta':  str(v('Cuenta Beneficiario') or '').strip() or False,
                        'calculation_type':     calc_type,
                        'percentage':           pct_raw if calc_type == 'percentage' else 0.0,
                        'fixed_amount':         monto_raw if calc_type == 'fixed' else 0.0,
                        'date_start':           _parse_date(v('Fecha de Inicio', 'Fecha Inicio')) or date.today(),
                        'date_end':             _parse_date(v('Fecha de Fin', 'Fecha Fin')),
                        'state':                'active',
                        'active':               True,
                    }
                    if branch:
                        vals['branch_id'] = branch.id

                    self.env['planilla.pension.alimentaria'].create(vals)
                    created += 1

            except Exception as e:
                err_count += 1
                errors.append({
                    'hoja': 'PENSION_ALIMENTARIA', 'fila': row_num, 'cedula': cedula,
                    'nombre': emp.name, 'error': str(e),
                    'traceback': traceback.format_exc(),
                    'vals': {k: str(val)[:120] for k, val in vals.items()},
                })
                _logger.warning('ImportDataWizard PENSION fila %s: %s', row_num, e)

        return created, err_count

    def _process_benefits(self, wb, errors):
        created = err_count = 0
        hdrs, rows = self._sheet_rows(wb, ['OTROS DESCUENTOS', 'DESCUENTO', 'BENEFICIO', 'BENEFIT'])
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
                    'hoja': 'OTROS DESCUENTOS', 'fila': row_num, 'cedula': cedula,
                    'nombre': '', 'error': 'Empleado no encontrado en Odoo',
                })
                continue

            try:
                with self.env.cr.savepoint():
                    benefit_type = _map(BENEFIT_TYPE, v('Tipo')) or 'deduction'
                    amount_type  = _map(AMOUNT_TYPE, v('Tipo de Monto', 'Tipo Monto')) or 'fixed'
                    amount       = _parse_float(v('Monto'))
                    pct          = _parse_float(v('Porcentaje'))
                    concepto     = str(v('Concepto') or '').strip()

                    # Buscar código de deducción por código o por nombre
                    ded_code_raw = str(v('Código Deducción', 'Codigo', 'Código') or '').strip()
                    ded_code = None
                    if ded_code_raw:
                        ded_code = (
                            self.env['planilla.deduction.code'].search(
                                [('code', '=ilike', ded_code_raw)], limit=1) or
                            self.env['planilla.deduction.code'].search(
                                [('name', 'ilike', ded_code_raw)], limit=1)
                        ) or None

                    # deduction_code_id es required=True en el modelo.
                    # Si no se especificó, usar el primer código disponible del tipo correcto,
                    # o el primero que exista.
                    if not ded_code:
                        ded_type = 'employee' if benefit_type == 'deduction' else 'employer'
                        ded_code = (
                            self.env['planilla.deduction.code'].search(
                                [('deduction_type', '=', ded_type)], limit=1) or
                            self.env['planilla.deduction.code'].search([], limit=1)
                        ) or None

                    if not concepto:
                        raise ValueError('El campo Concepto es obligatorio')
                    if not ded_code:
                        raise ValueError('No existe ningún Código de Deducción en Odoo. '
                                         'Cree al menos uno en Configuración → Códigos de Deducción')

                    vals = {
                        'employee_id':      emp.id,
                        'name':             concepto,
                        'benefit_type':     benefit_type,
                        'amount_type':      amount_type,
                        'amount':           amount if amount_type == 'fixed' else 0.0,
                        'percentage':       pct    if amount_type == 'percentage' else 0.0,
                        'date_start':       _parse_date(v('Vigente Desde', 'Fecha Inicio')),
                        'date_end':         _parse_date(v('Vigente Hasta', 'Fecha Fin')),
                        'note':             str(v('Nota', 'Observaciones') or '').strip() or False,
                        'active':           True,
                        'deduction_code_id': ded_code.id,
                    }

                    self.env['planilla.recurring.benefit'].create(vals)
                    created += 1

            except Exception as e:
                err_count += 1
                errors.append({
                    'hoja': 'OTROS DESCUENTOS', 'fila': row_num, 'cedula': cedula,
                    'nombre': emp.name, 'error': str(e),
                    'traceback': traceback.format_exc(),
                    'vals': {k: str(val)[:120] for k, val in vals.items()},
                })
                _logger.warning('ImportDataWizard BENEFICIOS fila %s: %s', row_num, e)

        return created, err_count

