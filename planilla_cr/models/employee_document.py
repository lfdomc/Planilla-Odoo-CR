from datetime import date, timedelta
from odoo import models, fields, api


class PlanillaEmployeeDocument(models.Model):
    """
    Documentos Personales del Empleado -- planilla.employee.document
    ====================================================================
    Expediente digital del empleado -- NO es dato de planilla (calculo de
    salario), es cumplimiento/RRHH general, por eso vive en su propia
    pestaña "Documentos" en la ficha del empleado, separada de "Planilla CR".

    Dos categorias, segun el tipo de documento (requires_expiry):
      - CON vencimiento: cedula, licencia, carne -- se les hace seguimiento
        de vencimiento y disparan alertas (cron_alert_document_expiry).
      - SIN vencimiento (documentos permanentes): contrato laboral, copia
        de cedula, curriculum, cartas de recomendacion -- solo se
        almacenan, sin fecha de vencimiento ni alertas.
    """
    _name = 'planilla.employee.document'
    _description = 'Documento Personal de Empleado'
    _order = 'expiry_date asc'
    _inherit = ['mail.thread']

    employee_id = fields.Many2one(
        'hr.employee', string='Empleado', required=True,
        ondelete='cascade', index=True,
    )
    company_id = fields.Many2one(
        related='employee_id.company_id', string='Compania',
        store=True, readonly=True,
    )
    document_type_id = fields.Many2one(
        'planilla.employee.document.type', string='Tipo de Documento',
        required=True, tracking=True,
    )
    document_number = fields.Char(string='Numero / Folio', tracking=True)
    issue_date = fields.Date(string='Fecha de Emision')
    expiry_date = fields.Date(
        string='Fecha de Vencimiento', tracking=True,
        help='Dejar vacio para documentos permanentes que no vencen '
             '(contrato laboral, copia de cedula, curriculum, etc.) -- '
             'segun lo configurado en el tipo de documento.'
    )
    alert_days_before = fields.Integer(
        string='Avisar (dias antes)',
        help='Dejar vacio para usar el valor por defecto del tipo de documento.'
    )
    file_data = fields.Binary(string='Archivo (escaneo)', attachment=True)
    file_name = fields.Char(string='Nombre del Archivo')
    notes = fields.Text(string='Notas')

    state = fields.Selection([
        ('permanente', 'Permanente'),
        ('vigente',    'Vigente'),
        ('por_vencer', 'Por Vencer'),
        ('vencido',    'Vencido'),
    ], string='Estado', compute='_compute_state', store=True)

    days_to_expiry = fields.Integer(
        string='Dias para Vencer', compute='_compute_state', store=True,
        help='Negativo si ya vencio. Vacio/0 para documentos permanentes.'
    )

    active = fields.Boolean(default=True)
    alert_sent = fields.Boolean(
        default=False,
        help='Se marca automaticamente cuando ya se envio la alerta de '
             'vencimiento -- evita notificar todos los dias por el mismo '
             'documento. Se reinicia solo si cambia la fecha de vencimiento '
             '(ej. al renovar el documento).'
    )

    def write(self, vals):
        if 'expiry_date' in vals:
            vals.setdefault('alert_sent', False)
        return super().write(vals)

    @api.depends('expiry_date', 'alert_days_before', 'document_type_id.alert_days_before')
    def _compute_state(self):
        hoy = date.today()
        for doc in self:
            if not doc.expiry_date:
                # Documento permanente (contrato, copia de cedula, etc.) --
                # o un documento con vencimiento al que aun no se le cargo
                # la fecha. En ambos casos no hay nada que vencer todavia.
                doc.state = 'permanente'
                doc.days_to_expiry = 0
                continue
            dias = (doc.expiry_date - hoy).days
            doc.days_to_expiry = dias
            umbral = doc.alert_days_before or (
                doc.document_type_id.alert_days_before if doc.document_type_id else 30)
            if dias < 0:
                doc.state = 'vencido'
            elif dias <= umbral:
                doc.state = 'por_vencer'
            else:
                doc.state = 'vigente'

    def name_get(self):
        result = []
        for doc in self:
            label = f'{doc.employee_id.name} - {doc.document_type_id.name}'
            if doc.expiry_date:
                label += f' (vence {doc.expiry_date.strftime("%d/%m/%Y")})'
            result.append((doc.id, label))
        return result
