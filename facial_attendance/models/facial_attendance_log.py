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
        required=True,
        ondelete='cascade',
        index=True,
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
    ], string='Tipo de Accion', required=True)

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

    @api.depends('employee_id', 'action_type', 'recognition_date')
    def _compute_name(self):
        action_sel = dict(self._fields['action_type'].selection)
        for rec in self:
            action_label = action_sel.get(rec.action_type, '')
            date_str = (
                rec.recognition_date.strftime('%d/%m/%Y %H:%M')
                if rec.recognition_date else ''
            )
            rec.name = '%s - %s (%s)' % (
                rec.employee_id.name or '',
                action_label,
                date_str,
            )

    def _compute_display_name(self):
        action_sel = dict(self._fields['action_type'].selection)
        for rec in self:
            action_label = action_sel.get(rec.action_type, '')
            date_str = (
                rec.recognition_date.strftime('%d/%m/%Y %H:%M')
                if rec.recognition_date else ''
            )
            rec.display_name = '%s - %s (%s)' % (
                rec.employee_id.name or '',
                action_label,
                date_str,
            )

    @api.model
    def create_from_recognition(self, employee_id, action_type, confidence,
                                 captured_image_b64=None, device_ip=None):
        """
        Crea un log de reconocimiento y actualiza hr.attendance.
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


class FacialAttendanceConfig(models.Model):
    _name = 'facial.attendance.config'
    _description = 'Configuracion de Quiosco Facial'
    _rec_name = 'name'

    name = fields.Char(
        string='Nombre del Quiosco',
        required=True,
        default='Quiosco Principal',
    )
    active = fields.Boolean(default=True)
    location = fields.Char(string='Ubicacion')
    tolerance = fields.Float(
        string='Tolerancia de Reconocimiento',
        default=0.55,
        digits=(3, 2),
        help='Distancia maxima para considerar un match valido (0.4=estricto, 0.6=tolerante)',
    )
    confidence_threshold = fields.Float(
        string='Umbral de Confianza (%)',
        default=60.0,
        digits=(5, 2),
        help='Porcentaje minimo de confianza requerido para aceptar el reconocimiento',
    )
    save_captured_images = fields.Boolean(
        string='Guardar Imagenes Capturadas',
        default=True,
    )
    auto_action = fields.Boolean(
        string='Accion Automatica',
        default=True,
        help='Determinar automaticamente si es entrada o salida',
    )
    kiosk_mode = fields.Boolean(
        string='Modo Quiosco',
        default=False,
        help='Activar modo quiosco (pantalla completa, sin navegacion)',
    )
