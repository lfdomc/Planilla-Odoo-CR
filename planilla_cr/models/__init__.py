# -- Constantes (primero, sin dependencias de modelos) -------------------------
from . import planilla_const

# -- Mixins (deben cargarse ANTES de payslip_cr que los hereda) ----------------
from . import mixins

# -- Catalogos y configuracion -------------------------------------------------
from . import branch
from . import identification_type
from . import employee_status
from . import employee_type
from . import deduction_code
from . import schedule_type
from . import payroll_calendar
from . import accounting_config
from . import income_tax_bracket
from . import minimum_salary
from . import rate_helper
from . import closed_period
from . import public_holiday

# -- Empleados -----------------------------------------------------------------
from . import hr_employee_extension

# -- Novedades -----------------------------------------------------------------
from . import overtime
from . import disability
from . import vacation_payment
from . import pension_alimentaria
from . import embargo
from . import bono
from . import bono_antiguedad_config
from . import recurring_benefit
from . import employee_loan
from . import employee_charge
from . import employee_termination
from . import leave_cr

# -- Boleta y Planilla (dependen de mixins + catalogos + novedades) ------------
from . import payslip_cr
from . import payroll_run_cr

# -- Reportes y auxiliares -----------------------------------------------------
from . import salary_history
from . import payroll_report
from . import payroll_dashboard
from . import ins_report
from . import bank_payment
from . import ccss_report
from . import scheduled_actions
from . import employer_cost_report
from . import overtime_report
from . import eddi7_export
from . import amonestacion

from . import migrate_codes
