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
        string='% Complemento Patronal (dias 4+)', default=0.0,
        help='Porcentaje adicional que el patrono paga a partir del dia 4. '
             'Por defecto 0 %% -- NO es obligatorio (Art. 79 Regl. CCSS). '
             'Active solo si su empresa tiene politica voluntaria de complemento.'
    )

    # ── Opciones especiales maternidad ──────────────────────────────────────
    maternity_split_50 = fields.Boolean(
        string='CCSS 50% + Patrono 50%',
        default=True,
        help='Modalidad especial: el patrono paga el 50%% del salario y la CCSS '
             'paga el otro 50%%. Aplica cuando la empresa tiene convenio o politica '
             'de mantener el salario completo durante la licencia de maternidad. '
             'Si no esta activo: CCSS paga el 100%% (Art. 94 CT estandar).'
    )
    maternity_ccss_on_employer = fields.Boolean(
        string='Cobrar CCSS obrera (10.83%%) sobre subsidio patronal',
        default=False,
        help='Al monto que paga el patrono (50%% del salario) se descuenta el '
             '10.83%% de CCSS obrera, igual que cualquier salario ordinario. '
             'El empleado recibe el 50%% patronal menos la cota de caja.'
    )
    maternity_ccss_deduction = fields.Monetary(
        string='CCSS obrera sobre subsidio patronal (10.83%%)',
        currency_field='currency_id',
        compute='_compute_costs', store=True,
        help='Monto de CCSS obrera descontado sobre el 50%% patronal en modalidad 50/50.'
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
             '- CCSS (enfermedad): dias 1-3 -> 50%% patrono + 50%% CCSS (Art. 79 CT). '
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

    # Campos Maternidad (Art. 94 CT) -- un solo registro cubre prenatal + postnatal
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

    # -- Prorroga / Continuidad ----------------------------------------
    is_prorroga = fields.Boolean(
        string='Es Prorroga',
        default=False,
        tracking=True,
        help='Marque si esta incapacidad es una PRORROGA (continuacion) de una '
             'incapacidad inmediatamente anterior del mismo empleado.\n\n'
             'Regla legal CR (CCSS): cuando una incapacidad inicia el dia siguiente '
             'a que termina otra del mismo empleado, es prorroga del mismo evento. '
             'Los 3 dias del tramo patronal (Art. 79 CT) NO se reinician -- ya se '
             'agotaron en el certificado original.\n\n'
             'Si es prorroga: employer_cost = CRC0, todo el subsidio es a cargo de '
             'la CCSS (60%). El sistema detecta automaticamente si el registro '
             'inicia el dia siguiente a uno existente y activa esta bandera.'
    )
    prorroga_de_id = fields.Many2one(
        'planilla.disability',
        string='Prorroga de',
        help='Referencia a la incapacidad original de la que este registro es prorroga.',
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

            # FIX SALARIO-VARIABLE: usar promedio de ultimas 3 boletas confirmadas.
            # Captura comisiones, bonos y horas extra. Fallback: base_salary / 30.
            # Base legal: la CCSS calcula sobre el salario efectivamente cotizado
            # (Reglamento del Seguro de Salud, Art. 6).
            from odoo.fields import Date as _Date
            ultimas_boletas = rec.env['planilla.payslip.cr'].search([
                ('employee_id', '=', rec.employee_id.id),
                ('state', 'in', ('confirmed', 'paid')),
                ('date_to', '<=', rec.date_start or _Date.context_today(rec)),
            ], order='date_to desc', limit=3)

            if ultimas_boletas:
                from .. import planilla_const as _K
                gross_list = []
                for bol in ultimas_boletas:
                    freq = bol._get_effective_freq()
                    periodos = _K.PERIODOS_POR_MES.get(freq, 2)
                    gross_list.append(bol.gross_salary * periodos)
                avg_monthly = sum(gross_list) / len(gross_list)
                rec.daily_salary = round(avg_monthly / 30, 2)
            else:
                rec.daily_salary = round(rec.employee_id.base_salary / 30, 2)

            if rec.disability_type == 'maternity':
                history = rec.env['planilla.salary.history'].search([
                    ('employee_id', '=', rec.employee_id.id),
                    ('effective_date', '<=', rec.date_start or _Date.context_today(rec)),
                    ('state', '=', 'authorized'),
                ], order='effective_date desc', limit=3)
                if history:
                    avg = sum(history.mapped('gross_salary')) / len(history)
                    rec.maternity_avg_salary = round(avg / 30, 2)
                else:
                    rec.maternity_avg_salary = round(rec.daily_salary, 2)
            else:
                rec.maternity_avg_salary = 0.0

    @api.depends('days', 'daily_salary', 'maternity_avg_salary',
                 'subsidy_percentage', 'employer_percentage',
                 'disability_type', 'is_prorroga',
                 'maternity_split_50', 'maternity_ccss_on_employer')
    def _compute_costs(self):
        for rec in self:
            if rec.disability_type == 'maternity':
                daily = rec.maternity_avg_salary or rec.daily_salary
                total = round(rec.days * daily, 2)
                rec.maternity_ccss_deduction = 0.0  # default

                if rec.maternity_split_50:
                    # Modalidad 50/50: patrono paga 50%, CCSS paga 50%
                    mitad = round(total * 0.50, 2)
                    if rec.maternity_ccss_on_employer:
                        # Al 50% del patrono se le aplica CCSS obrera (10.83%%)
                        # igual que cualquier pago de salario ordinario.
                        # El empleado recibe: 50%% patronal - 10.83%% CCSS
                        ccss_sobre_patrono = round(mitad * 0.1083, 2)
                        rec.maternity_ccss_deduction = ccss_sobre_patrono
                        rec.employer_cost = mitad          # patrono paga el 50%%
                        rec.ccss_subsidy  = round(total * 0.50, 2)  # CCSS paga su 50%%
                    else:
                        # CCSS paga su 50%%, patrono absorbe su 50%% sin deduccion
                        rec.employer_cost = mitad
                        rec.ccss_subsidy  = mitad
                        rec.maternity_ccss_deduction = 0.0
                else:
                    # Art. 94 CT estandar: 100%% CCSS, patrono NO paga salario
                    rec.employer_cost = 0.0
                    rec.ccss_subsidy  = total
            elif rec.disability_type == 'ins':
                # INS - Riesgo Laboral (Art. 218 CT / Regl. Seguro Riesgos del Trabajo):
                #  El INS cubre desde el DIA 1 (sin periodo de carencia patronal).
                #  El INS paga 60% del salario asegurado (igual que CCSS dias 4+).
                #  El patrono NO paga ningun subsidio -- employer_cost = CRC0.
                #  El INS paga DIRECTAMENTE al empleado, fuera de planilla.
                #  En planilla solo se registra el subsidio como referencia informativa.
                #  Tasa: 60% del salario diario (subsidy_percentage configurado en 100
                #   por defecto -- CORRECCION: debe ser 60% para INS ordinario).
                rec.employer_cost = 0.0
                ins_rate = (rec.subsidy_percentage or 60.0) / 100.0
                rec.ccss_subsidy = round(rec.days * rec.daily_salary * ins_rate, 2)
            elif rec.is_prorroga:
                # Prorroga: los 3 dias del tramo patronal ya se agotaron en el
                # certificado original. Todo el subsidio es a cargo de la CCSS.
                # Base legal: Art. 79 CT / Circular CCSS sobre continuidad de
                # incapacidades (sin brecha entre certificados = mismo evento).
                rec.employer_cost = 0.0
                rec.ccss_subsidy = round(
                    rec.days * rec.daily_salary * rec.subsidy_percentage / 100, 2
                )
            else:
                # Art. 79 CT: dias 1-3 -> 50% patrono + 50% CCSS.
                # dias 4+ -> subsidy_percentage% CCSS, patrono puede complementar.
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
        """Detecta automaticamente si esta incapacidad es prorroga de otra.
        Condicion: existe una incapacidad del mismo empleado cuyo date_end
        es exactamente el dia anterior a este date_start.
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
        """Al ingresar empleado o fecha inicio, detectar si es prorroga."""
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
                        'title': 'WARN Prorroga detectada',
                        'message': (
                            f'Esta incapacidad inicia el dia siguiente a "{anterior.name}" '
                            f'({anterior.date_start} -> {anterior.date_end}).\n\n'
                            f'Se marco automaticamente como PRORROGA. El patrono no '
                            f'paga los primeros 3 dias (ya se agotaron en el certificado '
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
        """FIX B-02 v53: Validar que la incapacidad tenga al menos 1 dia y fechas coherentes."""
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
                        'La incapacidad debe tener al menos 1 dia.'
                    )

    @api.constrains('subsidy_percentage', 'disability_type')
    def _check_subsidy_percentage(self):
        """
        Validar que el % de subsidio sea legalmente correcto segun el tipo.
        Art. 79 CT: CCSS paga 60% a partir del dia 4.
        Ley 6727 + Art. 218 CT: INS paga minimo 60%.
        Art. 94 CT: Maternidad 100%.
        """
        for rec in self:
            if rec.disability_type in ('ccss', 'ccss_accident', 'ins'):
                if rec.subsidy_percentage < 60.0:
                    raise ValidationError(
                        f'El % de subsidio no puede ser menor al 60% para incapacidades '
                        f'CCSS/INS (Art. 79 CT / Ley 6727). Valor ingresado: {rec.subsidy_percentage}%.\n'
                        f'El minimo legal es 60%. Si su empresa tiene un convenio especial, '
                        f'use el campo "% Complemento Patronal" para el excedente.'
                    )
                if rec.subsidy_percentage > 100.0:
                    raise ValidationError(
                        f'El % de subsidio no puede exceder el 100%. Valor: {rec.subsidy_percentage}%.'
                    )
            elif rec.disability_type == 'maternity':
                if rec.subsidy_percentage != 100.0:
                    raise ValidationError(
                        f'La licencia de maternidad siempre es al 100% (Art. 94 CT). '
                        f'Valor ingresado: {rec.subsidy_percentage}%. Corrija a 100%.'
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
        FIX B-03 v51: Fuerza el recalculo usando ORM write() en vez de SQL directo.
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

            # Calcular costos segun el tipo de incapacidad (misma logica que _compute_costs)
            days = rec.days or 0
            if rec.disability_type == 'maternity':
                employer_cost = 0.0
                ccss_subsidy = round(days * avg_daily, 2)
            elif rec.disability_type == 'ins':
                employer_cost = 0.0
                ccss_subsidy = round(days * daily, 2)
            else:
                # Dias 1-3: 50% patrono + 50% CCSS (Art. 79 CT). Dias 4+: segun porcentajes configurados.
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

            # Usar write() ORM -- actualiza BD e invalida cache correctamente en Odoo 19
            rec.write({
                'daily_salary': daily,
                'maternity_avg_salary': avg_daily,
                'ccss_subsidy': ccss_subsidy,
                'employer_cost': employer_cost,
            })
        return True

    def write(self, vals):
        res = super().write(vals)
        # Si cambia el empleado o tipo, forzar recalculo del salario promedio
        if any(f in vals for f in ('employee_id', 'disability_type', 'date_start', 'fecha_parto')):
            self._compute_daily_salary()
            self._compute_costs()
        return res

    def action_confirm(self):
        self.write({'state': 'confirmed'})

    def action_cancel(self):
        self.write({'state': 'cancelled'})
