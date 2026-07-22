"""
Funciones auxiliares puras de parseo para la importacion masiva por Excel.

Deliberadamente sin ninguna clase de modelo de Odoo (models.Model /
models.TransientModel) -- es un modulo "hoja", sin dependencias hacia
import_data_wizard.py ni hacia processors/*.py. Ambos importan DESDE aqui,
nunca entre si.

Por que existe este archivo separado:
Antes estas funciones vivian dentro de import_data_wizard.py, y los archivos
de processors/ las importaban de vuelta desde alli (`from ..import_data_wizard
import _map, ...`). Como ImportDataWizard tambien hereda (_inherit) de los
modelos definidos en processors/, eso creaba una dependencia circular real:
para registrar ImportDataWizard, Odoo necesita que los modelos de processors/
ya existan: pero para que processors/ pueda importar estas funciones, Python
necesita ejecutar todo import_data_wizard.py primero -- incluida la definicion
de ImportDataWizard, que todavia no puede registrarse. Cualquier orden de
carga en wizard/__init__.py fallaba con:
    TypeError: Model 'planilla.import.data.wizard' inherits from
    non-existing model 'planilla.import.processor.employees'.
Sacar las funciones aqui rompe el ciclo: ninguno de los dos lados depende
del otro, solo de este archivo.
"""
from datetime import date, datetime

BOOL_MAP = {'si': True, 'yes': True, '1': True, 'true': True, 'x': True}


def _normalize(val):
    """Convierte a string lowercase sin espacios extra."""
    if val is None:
        return ''
    return str(val).strip().lower()


def _parse_date(val):
    """Acepta DD/MM/AAAA, YYYY-MM-DD, datetime, date."""
    if not val:
        return None
    if isinstance(val, (date, datetime)):
        return val.date() if isinstance(val, datetime) else val
    s = str(val).strip()
    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y', '%Y/%m/%d'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_float(val):
    if val is None or str(val).strip() == '':
        return 0.0
    try:
        return float(str(val).replace(',', '').strip())
    except (ValueError, TypeError):
        return 0.0


def _parse_int(val):
    f = _parse_float(val)
    return int(f)


def _parse_bool(val):
    return BOOL_MAP.get(_normalize(val), False)


def _map(table, val, default=None):
    return table.get(_normalize(val), default)
