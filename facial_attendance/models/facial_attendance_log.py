# -*- coding: utf-8 -*-
import base64
import logging
from datetime import timedelta

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
        group_operator='avg',
        help='Porcentaje de confianza del reconocimiento (0-100). '
             'Al agrupar registros, se muestra el PROMEDIO (no la suma) '
             '-- un porcentaje sumado entre varios registros no tiene '
             'significado real.',
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

    # -- Kiosco y GPS complementario ----------------------------------------
    kiosk_id = fields.Many2one(
        'facial.attendance.kiosk', string='Kiosco', ondelete='set null',
        index=True,
        help='Dispositivo desde el cual se realizo esta marcacion.',
    )
    facial_branch_id = fields.Many2one(
        related='kiosk_id.facial_branch_id', string='Sucursal',
        store=True, readonly=True,
        help='Sucursal vinculada al kiosco desde el cual se realizo '
             'esta marcacion. Vacio si el kiosco no tiene sucursal '
             'asignada, o si la marcacion no vino de ningun kiosco '
             '(ej. registrada directamente desde el backend).',
    )
    gps_latitude = fields.Float(
        string='Latitud GPS', digits=(10, 7),
        help='Ubicacion reportada por el navegador al momento de marcar, '
             'solo si el kiosco tiene GPS complementario activado y el '
             'usuario otorgo el permiso de ubicacion.',
    )
    gps_longitude = fields.Float(
        string='Longitud GPS', digits=(10, 7),
    )
    gps_distance_meters = fields.Float(
        string='Distancia a la Sede (m)',
        help='Distancia calculada entre la ubicacion GPS reportada y las '
             'coordenadas de referencia de la sede/kiosco.',
    )
    out_of_range = fields.Boolean(
        string='Fuera de Area', default=False, index=True,
        help='La marcacion se acepto igual (nunca se bloquea al '
             'empleado por esto), pero la ubicacion GPS reportada estaba '
             'fuera del radio permitido configurado en el kiosco. '
             'Requiere revision del supervisor.',
    )
    gps_status_label = fields.Char(
        string='Estado GPS', compute='_compute_gps_status_label',
        help='Resumen legible del estado GPS de esta marcacion: si '
             'quedo dentro del radio permitido, fuera de rango, o si '
             'el kiosco no tiene GPS complementario activado.',
    )

    @api.depends('out_of_range', 'gps_latitude', 'gps_longitude', 'gps_distance_meters')
    def _compute_gps_status_label(self):
        for rec in self:
            if not rec.gps_latitude and not rec.gps_longitude:
                rec.gps_status_label = 'Sin GPS'
            elif rec.out_of_range:
                dist = ''
                if rec.gps_distance_meters:
                    if rec.gps_distance_meters >= 1000:
                        dist = f' ({rec.gps_distance_meters / 1000:.1f}km)'
                    else:
                        dist = f' ({rec.gps_distance_meters:.0f}m)'
                rec.gps_status_label = f'Fuera de rango{dist}'
            else:
                rec.gps_status_label = 'Dentro del rango'

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
                                 captured_image_b64=None, device_ip=None,
                                 kiosk_id=None, gps_latitude=None,
                                 gps_longitude=None, gps_distance_meters=None,
                                 out_of_range=False):
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
            'kiosk_id': kiosk_id or False,
            'out_of_range': bool(out_of_range),
        }
        if gps_latitude is not None:
            log_vals['gps_latitude'] = gps_latitude
        if gps_longitude is not None:
            log_vals['gps_longitude'] = gps_longitude
        if gps_distance_meters is not None:
            log_vals['gps_distance_meters'] = gps_distance_meters
        if captured_image_b64:
            if ',' in captured_image_b64:
                captured_image_b64 = captured_image_b64.split(',')[1]
            log_vals['captured_image'] = captured_image_b64

        log = self.create(log_vals)
        if out_of_range:
            _logger.warning(
                'Reconocimiento facial FUERA DE AREA: %s - %s (distancia %.1fm)',
                employee.name, action_type, gps_distance_meters or 0.0,
            )
        else:
            _logger.info(
                'Reconocimiento facial: %s - %s (Confianza: %.1f%%)',
                employee.name, action_type, confidence,
            )
        return log

    @api.model
    def create_failed_log(self, error_code, error_detail, employee_id=False,
                           confidence=0.0, device_ip=None, kiosk_id=None):
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
            'kiosk_id': kiosk_id or False,
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

    @api.model
    def action_cleanup_old_images(self, days=30):
        """
        Borra las imagenes capturadas (captured_image) de registros con
        mas de 'days' dias de antiguedad, sin borrar los registros de
        log en si -- el historial de marcaciones (fecha, empleado,
        confianza, resultado) se conserva intacto, solo se libera el
        espacio de las fotos adjuntas.

        Pensado para dos casos:
          1. Limpieza periodica si 'Guardar imagenes capturadas' esta
             activo permanentemente (ej. cron mensual).
          2. Limpieza puntual de las imagenes que ya se acumularon
             mientras el toggle estuvo activo, antes de desactivarlo.
        """
        cutoff = fields.Datetime.now() - timedelta(days=days)
        old_logs = self.search([
            ('recognition_date', '<', cutoff),
            ('captured_image', '!=', False),
        ])
        count = len(old_logs)
        if count:
            old_logs.write({'captured_image': False})
            _logger.info(
                'facial_attendance: %d imagen(es) capturada(s) eliminada(s) '
                '(registros con mas de %d dias de antiguedad).',
                count, days,
            )
        return count

# Nota: el modelo 'facial.attendance.config' (configuracion de quiosco) se
# elimino en v19.0.1.1.0 porque no estaba conectado a ninguna logica real:
# el backend siempre lee configuracion desde ir.config_parameter (via
# res.config.settings), por lo que el modelo era una fuente de configuracion
# paralela sin efecto. La fuente unica de verdad es ahora res.config.settings.
