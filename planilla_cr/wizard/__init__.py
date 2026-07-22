from . import import_parse_utils
from . import send_payslip_wizard

from . import salary_increase_wizard

from . import aguinaldo_wizard
from . import import_overtime_wizard
from . import vacation_balance_wizard
from . import termination_simulator
from . import audit_zip_wizard
from . import import_template_wizard
# ORDEN CRITICO: processors debe cargar ANTES que import_data_wizard.
# ImportDataWizard usa _inherit para heredar de los 4 modelos definidos en
# processors/ (planilla.import.processor.*) -- Odoo exige que esos modelos
# ya esten registrados antes de procesar la clase que hereda de ellos, o
# falla con: "Model 'planilla.import.data.wizard' inherits from
# non-existing model 'planilla.import.processor.employees'."
from . import processors
from . import import_data_wizard
from . import sync_hr_wizard

from . import test_email_wizard

from . import migrate_codes_wizard

from . import vacation_recalc_wizard
from . import vacation_audit_wizard
from . import payroll_accounting_review
from . import vacation_initial_balance_wizard
from . import resumen_ejecutivo_wizard
from . import confirm_warnings_wizard
from . import reporte_208_wizard
