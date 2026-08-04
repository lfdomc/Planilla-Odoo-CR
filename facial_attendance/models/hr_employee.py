# -*- coding: utf-8 -*-
import base64
import json
import logging
import io

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import ormcache

_logger = logging.getLogger(__name__)

try:
    import face_recognition
    import numpy as np
    from PIL import Image
    FACE_RECOGNITION_AVAILABLE = True
except ImportError:
    FACE_RECOGNITION_AVAILABLE = False
    _logger.warning(
        "La libreria 'face_recognition' no esta instalada. "
        "Ejecute: pip install face_recognition numpy Pillow"
    )


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    face_encoding = fields.Text(
        string='Codificacion Facial',
        help='Datos internos de reconocimiento facial (no editar manualmente)',
        groups='hr.group_hr_user',
    )
    face_image = fields.Binary(
        string='Imagen Facial de Referencia',
        attachment=True,
        groups='hr.group_hr_user',
        help='Foto de referencia usada para el reconocimiento facial. '
             'Restringida al mismo grupo que face_encoding -- es un dato '
             'biometrico sensible, no debe ser visible para cualquier '
             'usuario con acceso de lectura a la ficha del empleado.',
    )
    face_image_filename = fields.Char(string='Nombre de archivo facial')
    face_registered = fields.Boolean(
        string='Rostro Registrado',
        compute='_compute_face_registered',
        store=True,
        help='Indica si el empleado tiene un rostro registrado para reconocimiento',
    )
    face_registration_date = fields.Datetime(
        string='Fecha de Registro Facial',
        readonly=True,
    )
    facial_attendance_ids = fields.One2many(
        'facial.attendance.log',
        'employee_id',
        string='Registros de Reconocimiento Facial',
    )
    facial_attendance_count = fields.Integer(
        string='Total Reconocimientos',
        compute='_compute_facial_attendance_count',
    )

    @api.depends('face_encoding')
    def _compute_face_registered(self):
        for rec in self:
            rec.face_registered = bool(rec.face_encoding)

    def _compute_facial_attendance_count(self):
        FacialLog = self.env['facial.attendance.log']
        for rec in self:
            rec.facial_attendance_count = FacialLog.search_count([
                ('employee_id', '=', rec.id),
            ])

    def action_register_face(self):
        """Abre el wizard para registrar el rostro del empleado."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Registrar Rostro - %s') % self.name,
            'res_model': 'facial.register.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_employee_id': self.id,
            },
        }

    def action_clear_face(self):
        """Elimina el registro facial del empleado."""
        self.ensure_one()
        self.write({
            'face_encoding': False,
            'face_image': False,
            'face_image_filename': False,
            'face_registration_date': False,
        })
        # Invalidar cache de encodings para que el quiosco no use datos obsoletos
        self.env.registry.clear_cache()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Registro facial eliminado'),
                'message': _('Se elimino el registro facial de %s.') % self.name,
                'type': 'warning',
                'sticky': False,
            },
        }

    def action_view_facial_logs(self):
        """Muestra los registros de reconocimiento facial del empleado."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Registros Faciales - %s') % self.name,
            'res_model': 'facial.attendance.log',
            'view_mode': 'list,form',
            'domain': [('employee_id', '=', self.id)],
            'context': {'default_employee_id': self.id},
        }

    def save_face_encoding(self, image_base64):
        """
        Procesa una imagen en base64, extrae la codificacion facial
        y la guarda en el empleado.
        """
        self.ensure_one()
        if not FACE_RECOGNITION_AVAILABLE:
            raise UserError(_(
                "La libreria 'face_recognition' no esta instalada en el servidor. "
                "Contacte al administrador del sistema."
            ))

        try:
            if ',' in image_base64:
                image_base64 = image_base64.split(',')[1]

            image_data = base64.b64decode(image_base64)
            image = Image.open(io.BytesIO(image_data)).convert('RGB')
            image_np = np.array(image)

            # Usar el mismo modelo configurado en Ajustes para consistencia
            # entre el registro y el reconocimiento posterior.
            recognition_model = self.env['ir.config_parameter'].sudo().get_param(
                'facial_attendance.recognition_model', 'hog'
            )
            face_locations = face_recognition.face_locations(image_np, model=recognition_model)
            if not face_locations:
                raise ValidationError(_(
                    "No se detecto ningun rostro en la imagen. "
                    "Asegurese de que la cara sea visible y bien iluminada."
                ))
            if len(face_locations) > 1:
                raise ValidationError(_(
                    "Se detectaron multiples rostros en la imagen (%d). "
                    "Por favor capture solo un rostro a la vez."
                ) % len(face_locations))

            face_encodings = face_recognition.face_encodings(image_np, face_locations)
            if not face_encodings:
                raise ValidationError(_(
                    "No se pudo calcular la codificacion facial. "
                    "Por favor intente con una imagen de mejor calidad."
                ))

            encoding_json = json.dumps(face_encodings[0].tolist())

            self.write({
                'face_encoding': encoding_json,
                'face_image': image_base64,
                'face_image_filename': 'face_%s.jpg' % self.id,
                'face_registration_date': fields.Datetime.now(),
            })
            # Invalidar cache para que el quiosco detecte el nuevo rostro
            self.env.registry.clear_cache()
            _logger.info(
                'Rostro registrado exitosamente para el empleado: %s (ID: %s)',
                self.name, self.id,
            )
            return True

        except (ValidationError, UserError):
            raise
        except Exception as e:
            _logger.error(
                'Error al procesar imagen facial para empleado %s: %s',
                self.name, str(e),
            )
            raise UserError(_("Error al procesar la imagen: %s") % str(e))

    @api.model
    @ormcache('self.env.company.id')
    def get_all_face_encodings(self):
        """
        Retorna todas las codificaciones faciales registradas PARA LA
        COMPANIA ACTIVA del usuario/kiosco que hace la llamada.

        El resultado se cachea en memoria (ormcache, con la compania
        activa como parte de la clave) para evitar deserializar el JSON
        de todos los empleados en cada ciclo del quiosco (~2.5 s). El
        cache se invalida explicitamente en save_face_encoding() y
        action_clear_face(), los dos unicos puntos donde face_encoding
        cambia.

        FIX AUDITORIA: antes no se filtraba por company_id ni la cache
        distinguia compania. En una instalacion multi-company (varias
        empresas en la misma base de datos), esto permitia que un
        empleado de la Compania A fuera reconocido en un kiosco
        configurado para la Compania B -- cruce de datos biometricos
        entre empresas distintas. Irrelevante si cada cliente corre en
        una base de datos separada, pero se corrige de forma defensiva
        ya que no cuesta nada y elimina el riesgo por completo.
        """
        employees = self.search([
            ('face_encoding', '!=', False),
            ('company_id', '=', self.env.company.id),
        ])
        result = []
        for emp in employees:
            try:
                encoding = json.loads(emp.face_encoding)
                result.append({
                    'employee_id': emp.id,
                    'employee_name': emp.name,
                    # FIX RENDIMIENTO: se guarda directamente como
                    # np.array (no como lista de Python plana) para
                    # que el ormcache almacene el array ya construido.
                    # Antes, aunque este metodo ya estaba cacheado, el
                    # controlador reconstruia np.array(encoding) para
                    # CADA empleado en CADA ciclo de reconocimiento
                    # (~cada 2.5s) -- trabajo repetido que el cache no
                    # evitaba porque devolvia listas planas. Con 50-100
                    # empleados registrados, esta reconstruccion
                    # repetida podia sumar una fraccion notable del
                    # tiempo total de cada intento de reconocimiento.
                    'encoding': np.array(encoding) if FACE_RECOGNITION_AVAILABLE else encoding,
                })
            except Exception as e:
                _logger.warning(
                    'Codificacion facial invalida para empleado %s: %s',
                    emp.name, str(e),
                )
        return result
