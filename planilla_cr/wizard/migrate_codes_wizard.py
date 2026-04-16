from odoo import models, fields, api
from odoo.exceptions import UserError


class MigrateCodesWizard(models.TransientModel):
    _name = 'planilla.migrate.codes.wizard'
    _description = 'Asignar Codigos a Novedades Existentes'

    result_message = fields.Text(string='Resultado', readonly=True)

    def action_run(self):
        migrations = [
            ('planilla_embargo',         'EMB'),
            ('planilla_bono',            'BON'),
            ('planilla_leave_cr',        'LIC'),
            ('planilla_overtime',        'HE'),
            ('planilla_employee_charge', 'COB'),
        ]
        total = 0
        detalles = []
        for table, prefix in migrations:
            self._cr.execute(
                f'SELECT id FROM {table} WHERE code IS NULL OR code = %s ORDER BY id ASC',
                ('',)
            )
            ids_sin_codigo = [row[0] for row in self._cr.fetchall()]
            if not ids_sin_codigo:
                detalles.append(f'{prefix}: sin registros pendientes')
                continue
            self._cr.execute(
                f'SELECT code FROM {table} WHERE code LIKE %s ORDER BY code DESC LIMIT 1',
                (prefix + '-%',)
            )
            row = self._cr.fetchone()
            start = 1
            if row and row[0]:
                try:
                    start = int(row[0].split('-')[-1]) + 1
                except (ValueError, IndexError):
                    start = 1
            for i, rec_id in enumerate(ids_sin_codigo):
                code = f'{prefix}-{(start + i):04d}'
                self._cr.execute(
                    f'UPDATE {table} SET code = %s WHERE id = %s',
                    (code, rec_id)
                )
            n = len(ids_sin_codigo)
            total += n
            detalles.append(f'{prefix}: {n} registros actualizados')
        msg = f'Completado: {total} registros actualizados.\n' + '\n'.join(detalles)
        self.result_message = msg
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
