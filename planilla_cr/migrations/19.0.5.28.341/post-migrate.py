from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    """Agregar columnas faltantes de forma segura."""
    missing_cols = [
        ("planilla_accounting_config", "vacation_accrual_method", "VARCHAR DEFAULT 'monthly'"),
        ("planilla_accounting_config", "default_payroll_calendar_id", "INTEGER"),
    ]
    for table, col, col_type in missing_cols:
        cr.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name=%s AND column_name=%s",
            (table, col)
        )
        if not cr.fetchone():
            cr.execute(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}')
