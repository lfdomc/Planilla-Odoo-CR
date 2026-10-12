from odoo import models, fields, api


class PublicHoliday(models.Model):
    _name = 'planilla.public.holiday'
    _description = 'Feriados Nacionales CR'
    _order = 'date asc'

    name = fields.Char(string='Feriado', required=True)
    date = fields.Date(string='Fecha', required=True)
    type = fields.Selection([
        ('national', 'Nacional Obligatorio (Art. 148 CT)'),
        ('optional', 'No Obligatorio / Trasladable (Ley 8886)'),
        ('civic',    'Civico No Laborable'),
        ('custom',   'Personalizado'),
    ], string='Tipo', default='national', required=True)

    # FIX BUG-N06 v52 (y correccion posterior): is_paid distingue si el
    # patrono DEBE conceder el dia libre pagado aunque no se trabaje
    # (feriado "obligatorio" en el sentido estricto del Art. 148 CT).
    # Feriado obligatorio (is_paid=True): dia libre pagado garantizado.
    # Feriado no obligatorio (is_paid=False): trasladable, el pago del
    # dia sin trabajar depende de la modalidad salarial del empleado.
    is_paid = fields.Boolean(
        string='Pago Obligatorio (dia libre garantizado)',
        default=True,
        help='Art. 148 CT: si el patrono DEBE conceder el dia libre '
             'pagado, se trabaje o no. Los feriados "no obligatorios" '
             '(2 de agosto, 31 de agosto, 1 de diciembre) son '
             'trasladables y su pago sin trabajar depende de la '
             'modalidad salarial del empleado. IMPORTANTE: esto es '
             'independiente de si genera pago doble al trabajarlo -- '
             'ver el campo "Genera Pago Doble" abajo.'
    )
    # FIX: campo separado para la pregunta legal real que determina el
    # pago doble -- confirmado con multiples fuentes legales actuales
    # (MTSS, AG Legal, ATC Auditores) que los feriados "no obligatorios"
    # SI generan pago doble para empleados con salario mensual/quincenal
    # que trabajan ese dia (el feriado ya esta incluido en el sueldo;
    # si se trabaja, se adiciona un dia sencillo para completar el
    # equivalente a doble). La distincion de "no obligatorio" solo
    # afecta el pago del dia SIN trabajar, no el pago doble AL trabajar.
    generates_double_pay = fields.Boolean(
        string='Genera Pago Doble al Trabajarlo',
        default=True,
        help='Si trabajar este dia genera pago doble (Art. 148 CT). '
             'Confirmado legalmente que esto aplica tanto a feriados '
             'obligatorios como a los "no obligatorios" (2 de agosto, '
             '31 de agosto, 1 de diciembre) para empleados con salario '
             'mensual o quincenal -- son preguntas legales '
             'independientes de si el dia libre esta garantizado sin '
             'trabajar.'
    )
    company_id = fields.Many2one(
        'res.company', string='Empresa',
        default=lambda self: self.env.company,
        help='Dejar vacio para aplicar a todas las empresas.'
    )
    active = fields.Boolean(default=True)
    note = fields.Text(string='Notas')

    @api.model
    def is_holiday(self, date_to_check, company_id=None):
        """Retorna True si la fecha es feriado nacional (obligatorio o no)."""
        domain = [('date', '=', date_to_check), ('active', '=', True)]
        if company_id:
            domain += ['|', ('company_id', '=', company_id), ('company_id', '=', False)]
        else:
            domain.append(('company_id', '=', False))
        return bool(self.search(domain, limit=1))

    @api.model
    def is_paid_holiday(self, date_to_check, company_id=None):
        """Retorna True si la fecha es feriado de pago obligatorio (Art. 148 CT).
        Usar para determinar si el patrono DEBE conceder el dia libre
        pagado, se trabaje o no. NO usar para determinar pago doble --
        ver generates_double_pay_holiday() para eso.
        """
        domain = [
            ('date', '=', date_to_check),
            ('active', '=', True),
            ('is_paid', '=', True),
        ]
        if company_id:
            domain += ['|', ('company_id', '=', company_id), ('company_id', '=', False)]
        else:
            domain.append(('company_id', '=', False))
        return bool(self.search(domain, limit=1))

    def generates_double_pay_holiday(self, date_to_check, company_id=None):
        """Retorna True si trabajar esta fecha genera pago doble (Art. 148 CT).

        Distinto de is_paid_holiday(): un feriado puede ser "no
        obligatorio" (el dia libre sin trabajar no esta garantizado
        para todas las modalidades salariales) y aun asi generar pago
        doble si se trabaja -- confirmado legalmente para los tres
        feriados no obligatorios (2 de agosto, 31 de agosto, 1 de
        diciembre) en empleados con salario mensual o quincenal.
        """
        domain = [
            ('date', '=', date_to_check),
            ('active', '=', True),
            ('generates_double_pay', '=', True),
        ]
        if company_id:
            domain += ['|', ('company_id', '=', company_id), ('company_id', '=', False)]
        else:
            domain.append(('company_id', '=', False))
        return bool(self.search(domain, limit=1))

    @api.model
    def get_holidays_in_range(self, date_from, date_to, company_id=None):
        """Retorna el conjunto de fechas feriadas en el rango dado (todos los tipos)."""
        domain = [
            ('date', '>=', date_from),
            ('date', '<=', date_to),
            ('active', '=', True),
        ]
        if company_id:
            domain += ['|', ('company_id', '=', company_id), ('company_id', '=', False)]
        return set(self.search(domain).mapped('date'))

    @api.model
    def get_paid_holidays_in_range(self, date_from, date_to, company_id=None):
        """Retorna solo feriados de pago obligatorio en el rango dado.
        Util para calculo de horas extras en dias feriados (tipo 'holiday' en overtime).
        """
        domain = [
            ('date', '>=', date_from),
            ('date', '<=', date_to),
            ('active', '=', True),
            ('is_paid', '=', True),
        ]
        if company_id:
            domain += ['|', ('company_id', '=', company_id), ('company_id', '=', False)]
        return set(self.search(domain).mapped('date'))

    @api.model
    def action_fix_generates_double_pay_defaults(self):
        """
        Garantiza que los 3 feriados NO obligatorios reales de Costa
        Rica (2 de agosto, 31 de agosto, 1 de diciembre -- Art. 148 CT,
        Ley 9803 y Ley 10050) tengan generates_double_pay=True.

        Se conecta via <function> en un archivo de datos SIN
        noupdate="1" (ver data/fix_holiday_defaults.xml), que Odoo SI
        garantiza ejecutar en cada actualizacion del modulo -- a
        diferencia de post_migrate_hook en el manifest, que se
        confirmo que NO es un mecanismo real/confiable de Odoo (solo
        post_init_hook lo es, y ese unicamente corre en la instalacion
        inicial, nunca en actualizaciones de un modulo ya instalado).

        Se identifica por mes/dia (no por nombre ni por ID interno),
        para funcionar sin importar como se haya nombrado el feriado o
        el external id que tenga en cada instalacion.
        """
        candidatos = self.search([
            ('active', '=', True),
        ])
        fechas_no_obligatorias = {(8, 2), (8, 31), (12, 1)}
        a_corregir = candidatos.filtered(
            lambda h: (h.date.month, h.date.day) in fechas_no_obligatorias
            and not h.generates_double_pay
        )
        if a_corregir:
            a_corregir.write({'generates_double_pay': True})
        return True
