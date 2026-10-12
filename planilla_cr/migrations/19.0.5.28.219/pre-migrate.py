"""
Pre-migration script v5.28.219
Se ejecuta ANTES de que Odoo cargue los modelos del modulo.
Crea columnas faltantes en hr_employee que pueden causar error 500 al arrancar
si la BD viene de una version anterior del modulo.
"""
import logging
_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Crear columnas faltantes antes de que el ORM las necesite."""
    if not version:
        return  # primera instalacion, el ORM las creara solo

    columns = [
        ('hr_employee', 'vacation_last_anniversary_year', 'INTEGER',  '0'),
        ('hr_employee', 'vacation_balance_alert',         'BOOLEAN',  'FALSE'),
        ('hr_employee', 'vacation_days_accrued',          'NUMERIC',  '0'),
        ('hr_employee', 'vacation_days_taken',            'NUMERIC',  '0'),
        ('hr_employee', 'vacation_days_available',        'NUMERIC',  '0'),
        ('hr_employee', 'vacation_initial_balance',       'NUMERIC',  '0'),
        ('hr_employee', 'vacation_initial_balance_date',  'DATE',     'NULL'),
    ]

    created = []
    for table, col, col_type, default in columns:
        cr.execute("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name = %s AND column_name = %s
        """, (table, col))
        if not cr.fetchone():
            default_clause = f'DEFAULT {default}' if default != 'NULL' else ''
            cr.execute(
                f'ALTER TABLE {table} ADD COLUMN {col} {col_type} {default_clause}'
            )
            created.append(f'{table}.{col}')

    if created:
        _logger.info(
            'planilla_cr pre-migrate v5.28.219: columnas creadas: %s',
            ', '.join(created)
        )
    else:
        _logger.info(
            'planilla_cr pre-migrate v5.28.219: todas las columnas ya existen.'
        )
