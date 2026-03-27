from odoo import models, fields, api
from odoo.models import Constraint
from odoo.exceptions import ValidationError


class Disability(models.Model):
    _name = 'planilla.disability'
    _description = 'Incapacidad'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _unique_disability_employee_date_type = Constraint(
        'UNIQUE(employee_id, date_start, disability_type)',
        'Ya existe una incapacidad del mismo tipo para este empleado en esa fecha de inicio. Verifique si ya existe un registro duplicado.'
    )



    name = fields.Char(string='Referencia', compute='_compute_name', store=True)
    employee_id = fields.Many2one(
        'hr.employee', string='Empleado', required=True, tracking=True, index=True
    )
    branch_id = fields.Many2one(
        related='employee_id.branch_id', string='Sucursal', store=True
    )

    disability_type = fields.Selection([
        ('ccss',          'CCSS - Enfermedad'),
        ('ccss_accident', 'CCSS - Accidente Laboral'),
        ('ins',           'INS - Riesgo Laboral'),
        ('maternity',     'Maternidad'),
        ('other',         'Otra'),
    ], string='Tipo de Incapacidad', required=True, default='ccss', tracking=True)

    # Para maternidad: date_start = inicio prenatal, date_end = fin postnatal
    date_start = fields.Date(
        string='Fecha Inicio', required=True, tracking=True,
        default=fields.Date.today,
        help='Para maternidad: inicio de licencia prenatal (max 30 dias antes del parto)'
    )
    date_end = fields.Date(
        string='Fecha Fin', required=True, tracking=True,
        default=fields.Date.today,
        help='Para maternidad: fin de licencia postnatal (max 90 dias despues del parto)'
    )
    days = fields.Integer(string='Dias Totales', compute='_compute_days', store=True)

    subsidy_percentage = fields.Float(
        string='% Subsidio CCSS', default=60.0,
        help='Porcentaje que paga la CCSS del salario durante la incapacidad'
    )
    employer_percentage = fields.Float(
        string='% Complemento Patronal (días 4+)', default=0.0,
        help='Porcentaje adicional que el patrono paga a partir del día 4. '
             'Por defecto 0 %% — NO es obligatorio (Art. 79 Regl. CCSS). '
             'Active solo si su empresa tiene política voluntaria de complemento.'
    )

    daily_salary = fields.Monetary(
        string='Salario Diario', currency_field='currency_id',
        compute='_compute_daily_salary', store=True
    )
    maternity_avg_salary = fields.Monetary(
        string='Salario Diario Base (prom. 3 meses)', currency_field='currency_id',
        compute='_compute_daily_salary', store=True,
        help='Promedio de los ultimos 3 salarios brutos cotizados / 30. Base del subsidio CCSS por maternidad.'
    )
    currency_id = fields.Many2one(related='employee_id.currency_id', store=True)
    employer_cost = fields.Monetary(
        string='Costo Patrono', currency_field='currency_id',
        compute='_compute_costs', store=True,
        help='Costo a cargo del patrono durante la incapacidad.\n'
             '- CCSS (enfermedad): dias 1-3 → 50%% patrono + 50%% CCSS (Art. 79 CT). '
             'A partir del dia 4 la CCSS paga el 60%%, patrono puede complementar voluntariamente.\n'
             '- INS (riesgo laboral / accidente): el INS cubre desde el DIA 1 (Art. 218 '
             'Codigo de Trabajo y Reglamento del Seguro de Riesgos del Trabajo). '
             'Por eso employer_cost=0 es correcto para este tipo.\n'
             '- Maternidad: el subsidio lo paga la CCSS en su totalidad; costo patrono = 0.'
    )
    ccss_subsidy = fields.Monetary(
        string='Subsidio CCSS', currency_field='currency_id',
        compute='_compute_costs', store=True
    )

    # Campos Maternidad (Art. 94 CT) — un solo registro cubre prenatal + postnatal
    fecha_parto = fields.Date(
        string='Fecha de Parto', tracking=True,
        help='Fecha real o estimada del parto.\n'
             'Prenatal:  Fecha Inicio -> Fecha Parto (max 30 dias)\n'
             'Postnatal: Fecha Parto  -> Fecha Fin   (max 90 dias)'
    )
    prenatal_days = fields.Integer(
        string='Dias Prenatales', compute='_compute_maternity_days', store=True,
        help='Automatico: Fecha Parto - Fecha Inicio. Max 30 dias (Art. 94 CT).'
    )
    postnatal_days = fields.Integer(
        string='Dias Postnatales', compute='_compute_maternity_days', store=True,
        help='Automatico: Fecha Fin - Fecha Parto + 1. Max 90 dias (Art. 94 CT).'
    )

    certificate_number = fields.Char(string='Numero de Certificado CCSS')
    diagnosis = fields.Char(string='Diagnostico')
    note = fields.Text(string='Observaciones')

    # ── Prórroga / Continuidad ────────────────────────────────────────
    is_prorroga = fields.Boolean(
        string='Es Prórroga',
        default=False,
        tracking=True,
        help='Marque si esta incapacidad es una PRÓRROGA (continuación) de una '
             'incapacidad inmediatamente anterior del mismo empleado.\n\n'
             'Regla legal CR (CCSS): cuando una incapacidad inicia el día siguiente '
             'a que termina otra del mismo empleado, es prórroga del mismo evento. '
             'Los 3 días del tramo patronal (Art. 79 CT) NO se reinician — ya se '
             'agotaron en el certificado original.\n\n'
             'Si es prórroga: employer_cost = ₡0, todo el subsidio es a cargo de '
             'la CCSS (60%). El sistema detecta automáticamente si el registro '
             'inicia el día siguiente a uno existente y activa esta bandera.'
    )
    prorroga_de_id = fields.Many2one(
        'planilla.disability',
        string='Prórroga de',
        help='Referencia a la incapacidad original de la que este registro es prórroga.',
        ondelete='set null'
    )

    state = fields.Selection([
        ('draft',     'Borrador'),
        ('confirmed', 'Confirmado'),
        ('paid',      'Procesado en Planilla'),
        ('cancelled', 'Cancelado'),
    ], string='Estado', default='draft', tracking=True)

    payslip_id = fields.Many2one('planilla.payslip.cr', string='Boleta de Pago')


    @api.depends('employee_id', 'date_start')
    def _compute_name(self):
        for rec in self:
            emp = rec.employee_id.name or ''
            date_str = str(rec.date_start) if rec.date_start else ''
            rec.name = f'INC - {emp} - {date_str}'

    @api.depends('date_start', 'date_end')
    def _compute_days(self):
        for rec in self:
            if rec.date_start and rec.date_end:
                rec.days = (rec.date_end - rec.date_start).days + 1
            else:
                rec.days = 0

    @api.depends('date_start', 'date_end', 'fecha_parto', 'disability_type')
    def _compute_maternity_days(self):
        for rec in self:
            if rec.disability_type == 'maternity' and rec.fecha_parto:
                rec.prenatal_days = max(0, (rec.fecha_parto - rec.date_start).days) if rec.date_start else 0
                rec.postnatal_days = max(0, (rec.date_end - rec.fecha_parto).days + 1) if rec.date_end else 0
            else:
                rec.prenatal_days = 0
                rec.postnatal_days = 0

    @api.depends('employee_id', 'date_start', 'disability_type', 'fecha_parto')
    def _compute_daily_salary(self):
        for rec in self:
            if not rec.employee_id or not rec.employee_id.base_salary:
                rec.daily_salary = 0.0
                rec.maternity_avg_salary = 0.0
                continue

            rec.daily_salary = round(rec.employee_id.base_salary / 30, 2)

            if rec.disability_type == 'maternity':
                history = rec.env['planilla.salary.history'].search([
                    ('employee_id', '=', rec.employee_id.id),
                    ('effective_date', '<=', rec.date_start or fields.Date.context_today(rec)),
                    # FIX-F6: filtrar solo registros autorizados para consistencia
                    # con _compute_avg_last_4_weeks y _onchange_employee.
                    # Sin este filtro podían entrar registros en 'draft' o 'rejected'
                    # que distorsionarían el promedio de maternidad (Regl. CCSS).
                    ('state', '=', 'authorized'),
                ], order='effective_date desc', limit=3)
                if history:
                    # Promedio de ultimos 3 salarios brutos cotizados (Reglamento CCSS)
                    avg = sum(history.mapped('gross_salary')) / len(history)
                    rec.maternity_avg_salary = round(avg / 30, 2)
                else:
                    # Sin historial previo: usa salario base actual del empleado
                    rec.maternity_avg_salary = round(rec.employee_id.base_salary / 30, 2)
            else:
                rec.maternity_avg_salary = 0.0

    @api.depends('days', 'daily_salary', 'maternity_avg_salary',
                 'subsidy_percentage', 'employer_percentage',
                 'disability_type', 'is_prorroga')
    def _compute_costs(self):
        for rec in self:
            if rec.disability_type == 'maternity':
                # Art. 94 CT: 100% CCSS desde dia 1, patrono NO paga salario
                daily = rec.maternity_avg_salary or rec.daily_salary
                rec.employer_cost = 0.0
                rec.ccss_subsidy = round(rec.days * daily, 2)
            elif rec.disability_type == 'ins':
                # INS - Riesgo Laboral (Art. 218 CT / Regl. Seguro Riesgos del Trabajo):
                # • El INS cubre desde el DÍA 1 (sin período de carencia patronal).
                # • El INS paga 60% del salario asegurado (igual que CCSS días 4+).
                # • El patrono NO paga ningún subsidio — employer_cost = ₡0.
                # • El INS paga DIRECTAMENTE al empleado, fuera de planilla.
                # • En planilla solo se registra el subsidio como referencia informativa.
                # • Tasa: 60% del salario diario (subsidy_percentage configurado en 100
                #   por defecto — CORRECCIÓN: debe ser 60% para INS ordinario).
                rec.employer_cost = 0.0
                ins_rate = (rec.subsidy_percentage or 60.0) / 100.0
                rec.ccss_subsidy = round(rec.days * rec.daily_salary * ins_rate, 2)
            elif rec.is_prorroga:
                # Prórroga: los 3 días del tramo patronal ya se agotaron en el
                # certificado original. Todo el subsidio es a cargo de la CCSS.
                # Base legal: Art. 79 CT / Circular CCSS sobre continuidad de
                # incapacidades (sin brecha entre certificados = mismo evento).
                rec.employer_cost = 0.0
                rec.ccss_subsidy = round(
                    rec.days * rec.daily_salary * rec.subsidy_percentage / 100, 2
                )
            else:
                # Art. 79 CT: días 1-3 → 50% patrono + 50% CCSS.
                # días 4+ → subsidy_percentage% CCSS, patrono puede complementar.
                first_days    = min(rec.days, 3)
                remaining_days = max(rec.days - 3, 0)
                rec.employer_cost = round(
                    (first_days * rec.daily_salary * 0.50) +
                    (remaining_days * rec.daily_salary * rec.employer_percentage / 100), 2
                )
                rec.ccss_subsidy = round(
                    (first_days * rec.daily_salary * 0.50) +
                    (remaining_days * rec.daily_salary * rec.subsidy_percentage / 100), 2
                )

    @api.depends('employee_id', 'date_start', 'disability_type')
    def _compute_is_prorroga(self):
        """Detecta automáticamente si esta incapacidad es prórroga de otra.
        Condición: existe una incapacidad del mismo empleado cuyo date_end
        es exactamente el día anterior a este date_start.
        """
        from datetime import timedelta
        for rec in self:
            if not rec.employee_id or not rec.date_start or rec.disability_type == 'maternity':
                rec.is_prorroga = False
                rec.prorroga_de_id = False
                continue
            fecha_anterior = rec.date_start - timedelta(days=1)
            anterior = self.search([
                ('employee_id', '=', rec.employee_id.id),
                ('date_end', '=', fecha_anterior),
                ('disability_type', '!=', 'maternity'),
                ('state', 'not in', ['cancelled']),
                ('id', '!=', rec.id),
            ], limit=1)
            if anterior:
                rec.is_prorroga = True
                rec.prorroga_de_id = anterior.id
            else:
                # No forzar is_prorroga=False si ya fue marcado manualmente
                if not rec.is_prorroga:
                    rec.prorroga_de_id = False

    @api.onchange('employee_id', 'date_start')
    def _onchange_detect_prorroga(self):
        """Al ingresar empleado o fecha inicio, detectar si es prórroga."""
        from datetime import timedelta
        for rec in self:
            if not rec.employee_id or not rec.date_start or rec.disability_type == 'maternity':
                continue
            fecha_anterior = rec.date_start - timedelta(days=1)
            anterior = self.env['planilla.disability'].search([
                ('employee_id', '=', rec.employee_id.id),
                ('date_end', '=', fecha_anterior),
                ('disability_type', '!=', 'maternity'),
                ('state', 'not in', ['cancelled']),
            ], limit=1)
            if anterior and not rec.is_prorroga:
                rec.is_prorroga = True
                rec.prorroga_de_id = anterior.id
                return {
                    'warning': {
                        'title': '⚠️ Prórroga detectada',
                        'message': (
                            f'Esta incapacidad inicia el día siguiente a "{anterior.name}" '
                            f'({anterior.date_start} → {anterior.date_end}).\n\n'
                            f'Se marcó automáticamente como PRÓRROGA. El patrono no '
                            f'paga los primeros 3 días (ya se agotaron en el certificado '
                            f'original). Todo el subsidio es a cargo de la CCSS (60%).'
                        )
                    }
                }


    @api.onchange('disability_type')
    def _onchange_disability_type(self):
        if self.disability_type == 'maternity':
            self.subsidy_percentage = 100.0
            self.employer_percentage = 0.0
        elif self.disability_type in ('ccss', 'ccss_accident'):
            self.subsidy_percentage = 60.0
            self.employer_percentage = 0.0  # FIX v512 AUD: complemento patronal NO obligatorio
        elif self.disability_type == 'ins':
            self.subsidy_percentage = 60.0   # INS paga 60% del salario (Art. 218 CT)
            self.employer_percentage = 0.0

    @api.constrains('date_start', 'date_end')
    def _check_disability_dates(self):
        """FIX B-02 v53: Validar que la incapacidad tenga al menos 1 día y fechas coherentes."""
        for rec in self:
            if rec.date_start and rec.date_end:
                if rec.date_end < rec.date_start:
                    raise ValidationError(
                        f'La Fecha Fin ({rec.date_end}) no puede ser anterior '
                        f'a la Fecha Inicio ({rec.date_start}).'
                    )
                days = (rec.date_end - rec.date_start).days + 1
                if days <= 0:
                    raise ValidationError(
                        'La incapacidad debe tener al menos 1 día.'
                    )

    @api.constrains('date_start', 'date_end', 'fecha_parto', 'disability_type',
                    'prenatal_days', 'postnatal_days', 'days')
    def _check_maternity_rules(self):
        for rec in self:
            if rec.disability_type != 'maternity':
                continue
            if not rec.fecha_parto:
                raise ValidationError(
                    'Debe ingresar la Fecha de Parto para una licencia de maternidad (Art. 94 CT).'
                )
            if rec.date_start and rec.date_start > rec.fecha_parto:
                raise ValidationError(
                    f'La Fecha Inicio ({rec.date_start}) debe ser anterior o igual '
                    f'a la Fecha de Parto ({rec.fecha_parto}).'
                )
            if rec.date_end and rec.fecha_parto > rec.date_end:
                raise ValidationError(
                    f'La Fecha de Parto ({rec.fecha_parto}) debe ser anterior o igual '
                    f'a la Fecha Fin ({rec.date_end}).'
                )
            if rec.days > 120:
                raise ValidationError(
                    f'La licencia de maternidad no puede exceder 120 dias naturales '
                    f'(Art. 94 CT). Dias ingresados: {rec.days}.'
                )
            if rec.prenatal_days > 30:
                raise ValidationError(
                    f'El prenatal no puede exceder 30 dias (Art. 94 CT). '
                    f'Dias prenatales: {rec.prenatal_days}. '
                    f'Ajuste la Fecha Inicio para que sea maximo 30 dias antes del parto.'
                )
            if rec.postnatal_days > 90:
                raise ValidationError(
                    f'El postnatal no puede exceder 90 dias (Art. 94 CT). '
                    f'Dias postnatales: {rec.postnatal_days}. '
                    f'Ajuste la Fecha Fin para que sea maximo 90 dias despues del parto.'
                )

    def action_recompute(self):
        """
        FIX B-03 v51: Fuerza el recálculo usando ORM write() en vez de SQL directo.
        El SQL directo (env.cr.execute) dejaba inconsistencias en el cache ORM de Odoo 19:
        los campos se actualizaban en BD pero el recordset en memoria no se invalidaba
        correctamente, causando que la vista mostrara valores desactualizados.
        """
        for rec in self:
            if not rec.employee_id or not rec.employee_id.base_salary:
                continue
            daily = round(rec.employee_id.base_salary / 30, 2)
            history = rec.env['planilla.salary.history'].search([
                ('employee_id', '=', rec.employee_id.id),
                ('effective_date', '<=', rec.date_start or fields.Date.context_today(rec)),
                ('state', '=', 'authorized'),  # FIX-G3: solo registros autorizados
            ], order='effective_date desc', limit=3)
            if history:
                avg = sum(history.mapped('gross_salary')) / len(history)
                avg_daily = round(avg / 30, 2)
            else:
                avg_daily = daily

            # Calcular costos según el tipo de incapacidad (misma lógica que _compute_costs)
            days = rec.days or 0
            if rec.disability_type == 'maternity':
                employer_cost = 0.0
                ccss_subsidy = round(days * avg_daily, 2)
            elif rec.disability_type == 'ins':
                employer_cost = 0.0
                ccss_subsidy = round(days * daily, 2)
            else:
                # Días 1-3: 50% patrono + 50% CCSS (Art. 79 CT). Días 4+: según porcentajes configurados.
                first_days = min(days, 3)
                remaining_days = max(days - 3, 0)
                employer_cost = round(
                    (first_days * daily * 0.50) +
                    (remaining_days * daily * (rec.employer_percentage or 0.0) / 100), 2
                )
                ccss_subsidy = round(
                    (first_days * daily * 0.50) +
                    (remaining_days * daily * (rec.subsidy_percentage or 60.0) / 100), 2
                )

            # Usar write() ORM — actualiza BD e invalida cache correctamente en Odoo 19
            rec.write({
                'daily_salary': daily,
                'maternity_avg_salary': avg_daily,
                'ccss_subsidy': ccss_subsidy,
                'employer_cost': employer_cost,
            })
        return True

    def write(self, vals):
        res = super().write(vals)
        # Si cambia el empleado o tipo, forzar recálculo del salario promedio
        if any(f in vals for f in ('employee_id', 'disability_type', 'date_start', 'fecha_parto')):
            self._compute_daily_salary()
            self._compute_costs()
        return res

    def action_confirm(self):
        self.write({'state': 'confirmed'})

    def action_cancel(self):
        self.write({'state': 'cancelled'})
