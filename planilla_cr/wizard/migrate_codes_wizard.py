from odoo import models, fields, api
from odoo.exceptions import UserError
from psycopg2 import sql as _sql

# Whitelist explicita de tablas permitidas para esta migracion.
# Los nombres de tabla no se pueden parametrizar con %s en psycopg2,
# asi que se valida contra este set + se usa sql.Identifier para
# construir la query de forma segura (defensa en profundidad, aunque
# la lista de arriba ya es fija y no viene de input de usuario).
_ALLOWED_MIGRATION_TABLES = frozenset({
    'planilla_embargo',
    'planilla_bono',
    'planilla_leave_cr',
    'planilla_overtime',
    'planilla_employee_charge',
    'planilla_disability',
})


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
            ('planilla_disability',      'INC'),
        ]
        total = 0
        detalles = []
        for table, prefix in migrations:
            if table not in _ALLOWED_MIGRATION_TABLES:
                raise UserError(
                    f'Tabla no permitida en la migracion: {table}. '
                    f'Agreguela a _ALLOWED_MIGRATION_TABLES si es correcta.'
                )

            self._cr.execute(
                _sql.SQL(
                    'SELECT id FROM {} WHERE code IS NULL OR code = %s ORDER BY id ASC'
                ).format(_sql.Identifier(table)),
                ('',)
            )
            ids_sin_codigo = [row[0] for row in self._cr.fetchall()]
            if not ids_sin_codigo:
                detalles.append(f'{prefix}: sin registros pendientes')
                continue

            self._cr.execute(
                _sql.SQL(
                    'SELECT code FROM {} WHERE code LIKE %s ORDER BY code DESC LIMIT 1'
                ).format(_sql.Identifier(table)),
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
                    _sql.SQL('UPDATE {} SET code = %s WHERE id = %s').format(
                        _sql.Identifier(table)
                    ),
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
