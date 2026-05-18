import logging
from odoo import models, fields

_logger = logging.getLogger(__name__)


class PlanillaBranchNom(models.Model):
    """Extiende planilla.branch para agregar turnos de nombramiento."""
    _inherit = 'planilla.branch'

    nom_turno_ids = fields.Many2many(
        'nombramientos.shift.template',
        'nombramientos_branch_template_rel',
        'branch_id', 'template_id',
        string='Turnos de la Sede',
        help='Seleccioná las plantillas de turno que aplican a esta sede.',
    )
