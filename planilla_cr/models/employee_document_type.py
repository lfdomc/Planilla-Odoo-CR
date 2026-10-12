from odoo import models, fields, api


class PlanillaEmployeeDocumentType(models.Model):
    """
    Catalogo de Tipos de Documento del Empleado -- planilla.employee.document.type
    ================================================================================
    Catalogo configurable de documentos personales con vencimiento que la
    empresa quiere controlar por empleado (cedula, licencia de conducir,
    carne de manipulacion de alimentos, permiso de trabajo, etc.).

    Configurable por diseno: agregar un tipo de documento nuevo (para
    cualquier cliente, en cualquier momento) es solo agregar una fila
    aqui -- no requiere tocar codigo ni migrar nada.
    """
    _name = 'planilla.employee.document.type'
    _description = 'Tipo de Documento de Empleado'
    _order = 'sequence, name'

    name = fields.Char(string='Nombre', required=True)
    sequence = fields.Integer(default=10)
    requires_number = fields.Boolean(
        string='Requiere Numero de Documento',
        default=True,
        help='Si esta activo, se pide el numero/folio del documento '
             '(ej. numero de licencia). Desactivar para documentos que '
             'no tienen un numero identificable propio.'
    )
    requires_expiry = fields.Boolean(
        string='Requiere Fecha de Vencimiento',
        default=True,
        help='Activo (default) para documentos que vencen y necesitan '
             'seguimiento/alertas (cedula, licencia, carne). Desactivar '
             'para documentos permanentes que no vencen (contrato laboral, '
             'copia de cedula, curriculum, cartas) -- estos no generan '
             'alertas y su estado siempre es "Permanente".'
    )
    alert_days_before = fields.Integer(
        string='Avisar (dias antes de vencer)',
        default=30,
        help='Cuantos dias antes del vencimiento se genera la alerta '
             'automatica. Se puede sobreescribir por documento individual.'
    )
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', string='Compania',
        default=lambda self: self.env.company,
        help='Dejar vacio para que el tipo este disponible en todas las '
             'compañias. Asignar una compañia especifica si es un '
             'requisito propio de esa empresa.'
    )
