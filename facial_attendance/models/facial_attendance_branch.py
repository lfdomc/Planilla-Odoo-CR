# -*- coding: utf-8 -*-
"""
facial.attendance.branch -- Sucursal propia de Reconocimiento Facial.

Se creo para que los kioscos puedan vincularse a una sucursal SIN que
facial_attendance dependa de planilla_cr (ambos modulos son
intencionalmente independientes entre si, ver comentarios en
facial_attendance_kiosk.py).

Si planilla_cr SI esta instalado, action_sync_from_planilla() permite
importar/sincronizar las sucursales existentes en planilla.branch hacia
este modelo con un clic, en vez de tener que crearlas dos veces a mano.
Si planilla_cr no esta instalado, el usuario simplemente crea sus
sucursales aqui directamente -- el modulo funciona igual de bien solo.
"""
from odoo import models, fields, api


class FacialAttendanceBranch(models.Model):
    _name = 'facial.attendance.branch'
    _description = 'Sucursal (Reconocimiento Facial)'
    _order = 'name'

    name = fields.Char(string='Nombre', required=True)
    code = fields.Char(string='Codigo')
    address = fields.Char(string='Direccion')
    company_id = fields.Many2one(
        'res.company', string='Compania',
        required=True, default=lambda self: self.env.company
    )
    active = fields.Boolean(default=True)

    latitude = fields.Float(
        string='Latitud', digits=(10, 7),
        help='Coordenada GPS de la sede. Si esta definida, tiene '
             'prioridad sobre la ubicacion capturada automaticamente '
             'por cada kiosco individual al validar el radio permitido.',
    )
    longitude = fields.Float(string='Longitud', digits=(10, 7))

    # Referencia opcional a la sucursal de origen en planilla_cr, para
    # saber cuales sucursales ya se importaron y evitar duplicados al
    # volver a sincronizar. Se guarda como Integer generico (no
    # Many2one) por el mismo motivo que branch_res_id en
    # facial.attendance.kiosk: planilla.branch puede no existir en el
    # registro si planilla_cr no esta instalado, y un Many2one a un
    # modelo que no existe rompe la carga del modulo.
    planilla_branch_res_id = fields.Integer(
        string='ID Sucursal de Origen (Planilla)',
        help='Si esta sucursal se importo desde planilla_cr, guarda el '
             'ID de la sucursal de origen alli, para poder '
             'resincronizar sin crear duplicados. Vacio si la sucursal '
             'se creo directamente aqui.',
    )

    kiosk_ids = fields.One2many(
        'facial.attendance.kiosk', 'facial_branch_id', string='Kioscos'
    )
    kiosk_count = fields.Integer(
        string='Cantidad de Kioscos', compute='_compute_kiosk_count'
    )

    @api.depends('kiosk_ids')
    def _compute_kiosk_count(self):
        for rec in self:
            rec.kiosk_count = len(rec.kiosk_ids)

    @api.model
    def action_sync_from_planilla(self):
        """
        Importa/actualiza sucursales desde planilla.branch (si
        planilla_cr esta instalado). Para cada sucursal de planilla:
          - Si ya se importo antes (planilla_branch_res_id coincide),
            actualiza nombre/codigo/direccion/coordenadas por si
            cambiaron alli.
          - Si es la primera vez, crea una sucursal nueva aqui.
        Sucursales creadas directamente en facial_attendance (sin
        planilla_branch_res_id) NUNCA se tocan ni se duplican.

        No hace nada (sin error) si planilla_cr no esta instalado --
        pensado para llamarse desde un boton que ya esta oculto en ese
        caso, pero es seguro invocarlo de todas formas.
        """
        Branch = self.env.get('planilla.branch')
        if Branch is None:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Modulo Planilla CR no instalado',
                    'message': (
                        'No se encontro el modulo de Sucursales de '
                        'Planilla. Cree las sucursales directamente '
                        'aqui en Reconocimiento Facial.'
                    ),
                    'type': 'warning',
                },
            }

        planilla_branches = Branch.sudo().search([
            ('company_id', 'in', self.env.companies.ids),
        ])
        existing = self.sudo().search([
            ('planilla_branch_res_id', 'in', planilla_branches.ids),
        ])
        existing_by_source = {b.planilla_branch_res_id: b for b in existing}

        updated = 0
        create_vals_list = []
        for pb in planilla_branches:
            vals = {
                'name': pb.name,
                'code': pb.code,
                'address': pb.address,
                'company_id': pb.company_id.id,
                'latitude': pb.latitude,
                'longitude': pb.longitude,
                'planilla_branch_res_id': pb.id,
            }
            if pb.id in existing_by_source:
                # Odoo no soporta escribir valores DISTINTOS por
                # registro en una sola operacion nativa -- las
                # actualizaciones siguen siendo individuales. El caso
                # mas comun (primera sincronizacion, sin sucursales
                # existentes aun) ya no pasa por aqui.
                existing_by_source[pb.id].sudo().write(vals)
                updated += 1
            else:
                create_vals_list.append(vals)

        # FIX N+1: agrupar todas las creaciones en un solo create()
        # batch, en vez de una consulta de creacion por cada sucursal
        # nueva -- reduce N queries a 1 para el caso mas comun.
        created = len(create_vals_list)
        if create_vals_list:
            self.sudo().create(create_vals_list)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sincronizacion completa',
                'message': (
                    f'{created} sucursal(es) nueva(s) importada(s), '
                    f'{updated} actualizada(s) desde Planilla CR.'
                ),
                'type': 'success',
            },
        }
