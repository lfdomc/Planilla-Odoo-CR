# -*- coding: utf-8 -*-
"""
facial.kiosk.activate.wizard -- Wizard de activacion de un kiosco
pendiente, con nombre editable por el usuario.

Antes, el boton "Usar Sugerencia" aplicaba el nombre autogenerado
(ej. "Sede 31/07/2026") directamente, sin que el usuario pudiera
escribir el suyo propio antes de confirmar. Este wizard resuelve eso:
se abre un dialogo con el nombre sugerido PRECARGADO pero EDITABLE, y
el usuario decide el nombre final antes de que se cree/vincule
cualquier cosa.
"""
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class FacialKioskActivateWizard(models.TransientModel):
    _name = 'facial.kiosk.activate.wizard'
    _description = 'Activar Kiosco (con nombre editable)'

    kiosk_id = fields.Many2one(
        'facial.attendance.kiosk', string='Kiosco',
        required=True, readonly=True,
    )
    kiosk_name = fields.Char(
        string='Nombre del Kiosco', required=True,
        help='Nombre descriptivo para este dispositivo '
             '(ej. "Bodega", "Entrada Principal Escazú").',
    )
    branch_option = fields.Selection([
        ('existing', 'Vincular a la sucursal existente sugerida'),
        ('new', 'Crear una sucursal nueva'),
        ('none', 'No vincular ninguna sucursal por ahora'),
    ], string='Sucursal', required=True, default='none')
    suggested_branch_id = fields.Many2one(
        'facial.attendance.branch', string='Sucursal Sugerida',
        readonly=True,
    )
    new_branch_name = fields.Char(
        string='Nombre de la Nueva Sucursal',
        help='Nombre para la sucursal nueva, si elige crear una. '
             'Precargado con una sugerencia, pero puede escribir '
             'el que prefiera.',
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        kiosk_id = self.env.context.get('default_kiosk_id')
        if kiosk_id:
            kiosk = self.env['facial.attendance.kiosk'].browse(kiosk_id)
            if kiosk.exists():
                # Nombre del kiosco: si ya tiene uno real (no el
                # generico), se respeta -- el usuario solo lo cambia si
                # quiere. Si sigue en "Dispositivo sin nombre", se
                # precarga con la sugerencia como punto de partida.
                if kiosk.name and kiosk.name != _('Dispositivo sin nombre'):
                    res['kiosk_name'] = kiosk.name
                else:
                    res['kiosk_name'] = kiosk.suggested_branch_name or ''
                res['suggested_branch_id'] = kiosk.suggested_branch_id.id
                res['new_branch_name'] = kiosk.suggested_branch_name or ''
                if kiosk.suggested_branch_id:
                    res['branch_option'] = 'existing'
                elif kiosk.facial_branch_id:
                    # Ya tenia una sucursal vinculada de antes (ej. el
                    # admin ya la habia asignado manualmente) -- no
                    # tocarla, dejar en "none" para que el wizard no
                    # cree ni cambie nada de sucursal.
                    res['branch_option'] = 'none'
        return res

    def action_confirm(self):
        """Aplica el nombre y la sucursal elegidos, y activa el kiosco."""
        self.ensure_one()
        kiosk = self.kiosk_id
        if not kiosk.exists() or kiosk.state != 'pending':
            raise UserError(_(
                'Este kiosco ya no esta pendiente de activacion -- '
                'puede que otro administrador ya lo haya procesado.'
            ))

        if self.branch_option == 'existing':
            if not self.suggested_branch_id:
                raise UserError(_(
                    'No hay ninguna sucursal sugerida para vincular. '
                    'Elija "Crear una sucursal nueva" o "No vincular '
                    'ninguna sucursal por ahora".'
                ))
            kiosk.facial_branch_id = self.suggested_branch_id.id
        elif self.branch_option == 'new':
            if not self.new_branch_name:
                raise UserError(_(
                    'Escriba un nombre para la nueva sucursal.'
                ))
            new_branch = self.env['facial.attendance.branch'].create({
                'name': self.new_branch_name,
                'company_id': self.env.company.id,
                'latitude': kiosk.kiosk_latitude or 0.0,
                'longitude': kiosk.kiosk_longitude or 0.0,
            })
            kiosk.facial_branch_id = new_branch.id
        # branch_option == 'none': no se toca facial_branch_id.

        kiosk.write({'name': self.kiosk_name})
        kiosk.action_activate()
        return {'type': 'ir.actions.act_window_close'}
