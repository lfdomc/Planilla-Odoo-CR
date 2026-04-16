# -*- coding: utf-8 -*-
"""Migracion: asignar codigos consecutivos a registros existentes sin codigo."""


def migrate_codes(env):
    """Asigna codigos EMB/BON/LIC/HE/COB a registros sin codigo."""
    migrations = [
        ('planilla.embargo',         'planilla_embargo',         'EMB'),
        ('planilla.bono',            'planilla_bono',            'BON'),
        ('planilla.leave.cr',        'planilla_leave_cr',        'LIC'),
        ('planilla.overtime',        'planilla_overtime',        'HE'),
        ('planilla.employee.charge', 'planilla_employee_charge', 'COB'),
    ]
    for model_name, table, prefix in migrations:
        env.cr.execute(
            f'SELECT id FROM {table} WHERE code IS NULL OR code = %s ORDER BY id ASC',
            ('',)
        )
        ids_sin_codigo = [row[0] for row in env.cr.fetchall()]
        if not ids_sin_codigo:
            continue
        env.cr.execute(
            f'SELECT code FROM {table} WHERE code LIKE %s ORDER BY code DESC LIMIT 1',
            (prefix + '-%',)
        )
        row = env.cr.fetchone()
        if row and row[0]:
            try:
                start = int(row[0].split('-')[-1]) + 1
            except (ValueError, IndexError):
                start = 1
        else:
            start = 1
        for i, rec_id in enumerate(ids_sin_codigo):
            code = f'{prefix}-{(start + i):04d}'
            env.cr.execute(
                f'UPDATE {table} SET code = %s WHERE id = %s',
                (code, rec_id)
            )
