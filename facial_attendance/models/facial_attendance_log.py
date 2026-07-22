# -*- coding: ascii -*-
import base64
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class FacialAttendanceLog(models.Model):
    _name = 'facial.attendance.log'
    _description = 'Registro de Reconocimiento Facial'
    _order = 'recognition_date desc'

    name = fields.Char(
        string='Referencia',
        compute='_compute_name',
        store=True,
    )

    employee_id = fields.Many2one(
        'hr.employee',
        string='Empleado',
        required=False,
        ondelete='cascade',
        index=True,
        help='Vacio cuando el intento fallo antes de identificar a un empleado '
             '(rostro no detectado, sin coincidencia, etc.).',
    )
    attendance_id = fields.Many2one(
        'hr.attendance',
        string='Registro de Asistencia',
        ondelete='set null',
        readonly=True,
    )
    recognition_date = fields.Datetime(
        string='Fecha y Hora',
        default=fields.Datetime.now,
        required=True,
        index=True,
    )
    action_type = fields.Selection([
        ('check_in', 'Entrada'),
        ('check_out', 'Salida'),
    ], string='Tipo de Accion', required=False,
        help='Vacio en los intentos fallidos, donde no se determino accion alguna.')

    confidence = fields.Float(
        string='Confianza (%)',
        digits=(5, 2),
        help='Porcentaje de confianza del reconocimiento (0-100)',
    )
    captured_image = fields.Binary(
        string='Imagen Capturada',
        attachment=True,
        help='Imagen capturada en el momento del reconocimiento',
    )
    device_ip = fields.Char(string='IP del Dispositivo')
    notes = fields.Text(string='Notas')
    state = fields.Selection([
        ('success', 'Exitoso'),
        ('failed', 'Fallido'),
        ('manual', 'Manual'),
    ], string='Estado', default='success', required=True)
    error_message = fields.Char(string='Mensaje de Error')

    department_id = fields.Many2one(
        related='employee_id.department_id',
        string='Departamento',
        store=True,
    )
    job_id = fields.Many2one(
        related='employee_id.job_id',
        string='Puesto',
        store=True,
    )

    @api.depends('employee_id', 'action_type', 'recognition_date', 'state')
    def _compute_name(self):
        action_sel = dict(self._fields['action_type'].selection)
        state_sel = dict(self._fields['state'].selection)
        for rec in self:
            date_str = (
                rec.recognition_date.strftime('%d/%m/%Y %H:%M')
                if rec.recognition_date else ''
            )
            if rec.state == 'failed':
                employee_label = rec.employee_id.name or 'Desconocido'
                state_label = state_sel.get('failed', 'Fallido')
                rec.name = '%s - %s (%s)' % (employee_label, state_label, date_str)
            else:
                action_label = action_sel.get(rec.action_type, '')
                rec.name = '%s - %s (%s)' % (
                    rec.employee_id.name or '',
                    action_label,
                    date_str,
                )

    # display_name no se sobreescribe: Odoo usa el campo 'name' (definido
    # arriba con _compute_name) como _rec_name por defecto, asi que una
    # implementacion propia de _compute_display_name solo duplicaria logica.

    @api.model
    def create_from_recognition(self, employee_id, action_type, confidence,
                                 captured_image_b64=None, device_ip=None):
        """
        Crea un log de reconocimiento exitoso y actualiza hr.attendance.
        """
        employee = self.env['hr.employee'].browse(employee_id)
        if not employee.exists():
            raise UserError(_('Empleado no encontrado.'))

        now = fields.Datetime.now()
        attendance = self._process_attendance(employee, action_type, now)

        log_vals = {
            'employee_id': employee_id,
            'recognition_date': now,
            'action_type': action_type,
            'confidence': confidence,
            'device_ip': device_ip,
            'state': 'success',
            'attendance_id': attendance.id if attendance else False,
        }
        if captured_image_b64:
            if ',' in captured_image_b64:
                captured_image_b64 = captured_image_b64.split(',')[1]
            log_vals['captured_image'] = captured_image_b64

        log = self.create(log_vals)
        _logger.info(
            'Reconocimiento facial: %s - %s (Confianza: %.1f%%)',
            employee.name, action_type, confidence,
        )
        return log

    @api.model
    def create_failed_log(self, error_code, error_detail, employee_id=False,
                           confidence=0.0, device_ip=None):
        """
        Registra un intento de reconocimiento fallido para dejar rastro
        auditable: rostro no detectado, sin coincidencia, error interno, etc.
        """
        log = self.create({
            'employee_id': employee_id or False,
            'recognition_date': fields.Datetime.now(),
            'confidence': confidence,
            'device_ip': device_ip,
            'state': 'failed',
            'error_message': error_detail,
        })
        _logger.info(
            'Intento de reconocimiento fallido (%s): %s',
            error_code, error_detail,
        )
        return log

    def _process_attendance(self, employee, action_type, timestamp):
        """
        Crea o actualiza el registro hr.attendance del empleado.
        """
        HrAttendance = self.env['hr.attendance']

        if action_type == 'check_in':
            open_attendance = HrAttendance.search([
                ('employee_id', '=', employee.id),
                ('check_out', '=', False),
            ], limit=1)
            if open_attendance:
                _logger.warning(
                    'Empleado %s ya tiene una entrada abierta (ID: %s).',
                    employee.name, open_attendance.id,
                )
                return open_attendance
            return HrAttendance.create({
                'employee_id': employee.id,
                'check_in': timestamp,
            })

        elif action_type == 'check_out':
            open_attendance = HrAttendance.search([
                ('employee_id', '=', employee.id),
                ('check_out', '=', False),
            ], limit=1, order='check_in desc')

            if open_attendance:
                open_attendance.write({'check_out': timestamp})
                return open_attendance
            else:
                _logger.warning(
                    'No se encontro entrada abierta para empleado %s. '
                    'Se crea registro manual.',
                    employee.name,
                )
                return HrAttendance.create({
                    'employee_id': employee.id,
                    'check_in': timestamp,
                    'check_out': timestamp,
                })

        return False

# Nota: el modelo 'facial.attendance.config' (configuracion de quiosco) se
# elimino en v19.0.1.1.0 porque no estaba conectado a ninguna logica real:
# el backend siempre lee configuracion desde ir.config_parameter (via
# res.config.settings), por lo que el modelo era una fuente de configuracion
# paralela sin efecto. La fuente unica de verdad es ahora res.config.settings.
