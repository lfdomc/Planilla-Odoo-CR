from odoo import models, fields, api


class PayrollAccountingConfig(models.Model):
    """Configuracion de cuentas contables para planilla CR.
    Una configuracion por compania.
    """
    _name = 'planilla.accounting.config'
    _description = 'Configuracion Contable de Planilla'
    _rec_name = 'company_id'

    _sql_constraints = [
        ('unique_company', 'UNIQUE(company_id)',
         'Solo puede existir una configuracion contable por empresa.'),
    ]

    company_id = fields.Many2one(
        'res.company', string='Compania',
        required=True, default=lambda self: self.env.company,
        ondelete='cascade'
    )

    # -- Modo de generacion de asientos -----------------------------
    accounting_entry_mode = fields.Selection([
        ('per_employee', 'Por Empleado (un asiento por boleta)'),
        ('per_run', 'Por Planilla (un asiento consolidado por planilla)'),
    ], string='Modo de Asiento Contable',
        default='per_run', required=True,
        help='Define si se genera un asiento contable por cada boleta de pago '
             'o un unico asiento consolidado por planilla.'
    )

    # -- Base de calculo del Impuesto de Renta ----------------------
    income_tax_base = fields.Selection([
        ('gross',    'Salario Bruto (Art. 33 LIR -- recomendado)'),
        ('net_ccss', 'Bruto menos CCSS Obrero (practica alternativa)'),
    ], string='Base de calculo de Renta',
        default='gross', required=True,
        help='Define sobre que monto se calcula el impuesto de renta mensual.\n\n'
             'SALARIO BRUTO (Art. 33 LIR): la base imponible es el salario bruto. '
             'Es la interpretacion oficial de la Direccion General de Tributacion.\n\n'
             'BRUTO MENOS CCSS: algunas empresas deducen la cuota obrera CCSS '
             'antes de calcular la renta. Esta practica no esta reconocida '
             'expresamente por la DGT -- el patrono asume la responsabilidad fiscal.\n\n'
             'El sistema aplica la opcion seleccionada sin validar cual es la correcta.'
    )
    # -- Método de acumulación de vacaciones ----------------------------------
    vacation_accrual_method = fields.Selection([
        ('monthly', 'Mismo día cada mes (1 día el día del aniversario mensual)'),
        ('days29',  'Cada 29 días calendario (método legal Art. 153 CT)'),
    ],
        string='Método de Acumulación de Vacaciones',
        default='monthly',
        required=True,
        help='MISMO DÍA CADA MES (default): el empleado gana 1 día de vacaciones '
             'el mismo día numérico de cada mes que corresponde a su fecha de ingreso. '
             'Ej: ingreso 9 ago → gana el 9 de sep, 9 de oct, 9 de nov, etc. '
             'Es la práctica más común en empresas costarricenses.\n\n'
             'CADA 29 DÍAS: método estrictamente legal (Art. 153 CT). '
             '1 día por cada 29 días calendario trabajados.',
    )

    # -- Beneficio extra de vacaciones por aniversario de empresa ------
    extra_vacation_days_enabled = fields.Boolean(
        string='Dias Extra de Vacaciones por Anio',
        default=False,
        help='Si esta activo, el 1 de enero de cada anio se acreditan dias '
             'adicionales de vacaciones a los empleados activos. '
             'Art. 58 CT: el patrono puede otorgar beneficios superiores al minimo legal.'
    )
    extra_vacation_days_mode = fields.Selection([
        ('fixed',    'Dias fijos para todos (ej: 2 dias a todos)'),
        ('per_year', 'Dias por anio laborado (ej: 2 dias x anio de servicio)'),
    ], string='Modalidad del beneficio',
        default='fixed', required=True,
        help='DIAS FIJOS: todos los empleados reciben la misma cantidad de dias. '
             'DIAS POR ANO LABORADO: cada empleado recibe N dias multiplicados '
             'por sus anos completos de servicio. Ej: 2 dias/anio x 3 anos = 6 dias.'
    )
    extra_vacation_days_amount = fields.Float(
        string='Dias base',
        default=2.0,
        help='Modalidad FIJA: dias a acreditar a cada empleado. '
             'Modalidad POR ANO: dias por cada anio completo de servicio.'
    )
    extra_vacation_last_applied_year = fields.Integer(
        string='Ultimo anio aplicado',
        default=0,
        help='Anio en que se aplico por ultima vez el beneficio. '
             'Evita aplicarlo dos veces en el mismo anio.'
    )


    # -- Representante RRHH para Constancias Laborales ---------------
    # ── Tasa de Provisión de Cesantía ─────────────────────────────────────
    cesantia_prov_mode = fields.Selection([
        ('custom', 'Tasa fija personalizada'),
        ('legal',  'Según años de servicio (Art. 29 CT)'),
    ],
        string='Modalidad provisión cesantía',
        default='legal',
        required=True,
        help='TASA FIJA: porcentaje único para todos. Permite planeación predecible.\n'
             'ART. 29 CT: varía por años de servicio (5.42%–6.11%).',
    )
    cesantia_prov_rate = fields.Float(
        string='Tasa fija cesantía (%)',
        default=4.8,
        digits=(5, 4),
        help='% a provisionar por cesantía en modalidad tasa fija. '
             'Default 4.80%% (criterio contable). Mínimo legal: 5.42%%.',
    )

    hr_rep_name = fields.Char(
        string='Nombre Representante RRHH',
        help='Nombre completo del encargado de RRHH que firma las constancias laborales.'
    )
    hr_rep_title = fields.Char(
        string='Titulo / Cargo',
        default='Encargado de Recursos Humanos',
        help='Titulo o cargo del representante que firma las constancias.'
    )
    hr_rep_phone = fields.Char(
        string='Telefono RRHH',
        help='Telefono del departamento de RRHH para las constancias laborales.'
    )
    hr_rep_email = fields.Char(
        string='Email RRHH',
        help='Correo electronico del departamento de RRHH para las constancias laborales.'
    )
    hr_rep_location = fields.Char(
        string='Lugar de emision',
        help='Ciudad o lugar donde se emiten las constancias. Ej: "INVU Las Canas, Alajuela".'
    )

    # == Configuracion de envio de boletas por correo ==================
    email_payslip_subject = fields.Char(
        string='Asunto del correo',
        default='Boleta de Pago - {period}',
        help='Asunto del correo. Use {period} para insertar el periodo automaticamente.'
    )
    email_payslip_from = fields.Char(
        string='Remitente (From)',
        help='Direccion de correo del remitente. Si se deja vacio usa el email '
             'de la empresa o del usuario que envia.'
    )
    email_payslip_body = fields.Html(
        string='Cuerpo del correo',
        default='''<p>Estimado/a colaborador/a,</p>
<p>Adjunto encontrara su boleta de pago correspondiente al periodo <strong>{period}</strong>.</p>
<p>Si tiene alguna consulta, no dude en contactar al departamento de Recursos Humanos.</p>
<br/>
<p>Atentamente,<br/><strong>{company}</strong><br/>Departamento de Recursos Humanos</p>''',
        help='Cuerpo HTML del correo. Use {period} para el periodo y {company} para la empresa.'
    )
    email_payslip_server_id = fields.Many2one(
        'ir.mail_server',
        string='Servidor de correo saliente',
        help='Servidor SMTP para enviar boletas. '
             'Si no aparece ninguno, configure uno en Ajustes > Servidores de correo saliente.'
    )
    email_payslip_signature = fields.Char(
        string='Firma del remitente',
        help='Nombre que aparece en la firma del correo. Ej: "Recursos Humanos".'
    )

    default_payroll_calendar_id = fields.Many2one(
        'planilla.calendar', string='Calendarizacion por Defecto',
        help='Al crear una nueva planilla, esta calendarizacion se seleccionara '
             'automaticamente. Evita tener que escogerla manualmente cada vez.'
    )

    # -- Diario ------------------------------------------------------
    journal_id = fields.Many2one(
        'account.journal', string='Diario de Planilla',
        domain=[('type', 'in', ['general', 'purchase'])],
        help='Diario contable donde se registran los asientos de planilla.'
    )

    # ==============================================================
    # CUENTAS DE GASTO (DEBITO)
    # ==============================================================
    account_salary_expense = fields.Many2one(
        'account.account', string='Salarios (Gasto)',
        help='DEBITO -- Salario bruto del empleado.\nEj: 630000 Sueldos y Salarios'
    )
    account_social_charges_expense = fields.Many2one(
        'account.account', string='Cargas Sociales Patronales (Gasto)',
        help='DEBITO -- CCSS Patronal (26.83%) + INS (1%).\nEj: 630100 Cargas Sociales'
    )
    account_vacation_expense = fields.Many2one(
        'account.account', string='Vacaciones (Gasto)',
        help='DEBITO -- Provision de vacaciones (4.16%).\nEj: 630200 Vacaciones'
    )
    account_aguinaldo_expense = fields.Many2one(
        'account.account', string='Aguinaldo (Gasto)',
        help='DEBITO -- Provision aguinaldo (8.33%).\nEj: 630300 Aguinaldo'
    )
    account_cesantia_expense = fields.Many2one(
        'account.account', string='Cesantia / Auxilio (Gasto)',
        help='DEBITO -- Provision de auxilio de cesantia (5.33%).\nEj: 630400 Cesantia'
    )

    # ==============================================================
    # CUENTAS POR PAGAR (CREDITO)
    # ==============================================================
    # Configuracion especial: omitir CCSS obrero en liquidaciones
    # Por defecto DESACTIVADO (la mayoria de empresas paga CCSS en liquidaciones)
    # Check para calcular HE con formula fija: salario/30/8
    # Por defecto DESACTIVADO (usa horas del tipo de horario del empleado)
    overtime_fixed_8h = fields.Boolean(
        string='Horas extra: usar formula salario/30/8h (fijo)',
        default=False,
        help='Si se activa, el calculo de horas extra usara SIEMPRE salario/30/8h,'
             ' ignorando el tipo de horario del empleado.'
             ' Por defecto DESACTIVADO (usa las horas del tipo de horario asignado).'
    )

    show_vacation_on_payslip = fields.Boolean(
        string='Mostrar saldo de vacaciones en boleta',
        default=True,
        help='Si está activo, la boleta PDF del empleado muestra el saldo '
             'de días de vacaciones disponibles. Desactive para ocultar este dato.'
    )

    exclude_disability_from_vacation = fields.Boolean(
        string='Excluir incapacidades del cómputo de vacaciones',
        default=False,
        help='Art. 153 CT: las vacaciones se calculan sobre tiempo laborado. '
             'Al activar este check, los días de incapacidad por enfermedad/accidente '
             'se descuentan de la base de acumulación de vacaciones. '
             'Las incapacidades por maternidad NUNCA se descuentan (Art. 95 CT). '
             'Desactivado por defecto (práctica habitual en CR es incluirlas).'
    )

    enable_overtime_exemption = fields.Boolean(
        string='Permitir excluir Horas Extra de CCSS/Renta',
        default=False,
        help='Por defecto, las horas extra SIEMPRE llevan CCSS y Renta '
             '(Art. 139 CT -- son salario ordinario). Active este check '
             'SOLO si tiene un criterio legal especifico para permitir '
             'excepciones. Al activarlo, apareceran los checks "Afecto CCSS" '
             'y "Afecto Renta" en cada hora extra individual.'
    )

    skip_ccss_on_termination = fields.Boolean(
        string='No descontar CCSS obrero en liquidaciones',
        default=False,
        help='Si se activa, el sistema no descontara la CCSS obrero en las'
             ' liquidaciones de empleados ni en el simulador de liquidaciones.'
             ' Por defecto DESACTIVADO. Activar solo si la empresa tiene un'
             ' acuerdo especial o politica interna al respecto.'
    )

    account_ccss_payable = fields.Many2one(
        'account.account', string='CCSS por Pagar (Obrera + Patronal)',
        help='CREDITO -- CCSS Obrera (10.83%) + Patronal (26.83%).\nEj: 230300 CCSS por Pagar'
    )
    account_ins_payable = fields.Many2one(
        'account.account', string='INS por Pagar',
        help='CREDITO -- INS Riesgos del Trabajo (~1%).\nEj: 230400 INS por Pagar'
    )
    account_income_tax_payable = fields.Many2one(
        'account.account', string='Retencion Renta por Pagar',
        help='CREDITO -- Impuesto de renta retenido.\nEj: 230100 Retencion Renta'
    )
    account_aguinaldo_provision = fields.Many2one(
        'account.account', string='Provision Aguinaldo por Pagar',
        help='CREDITO -- Pasivo acumulado de aguinaldo.\nEj: 230500 Provision Aguinaldo'
    )
    account_cesantia_provision = fields.Many2one(
        'account.account', string='Provision Cesantia por Pagar',
        help='CREDITO -- Pasivo acumulado de cesantia.\nEj: 230600 Provision Cesantia'
    )
    account_vacation_provision = fields.Many2one(
        'account.account', string='Provision Vacaciones por Pagar',
        help='CREDITO -- Pasivo acumulado de vacaciones.\nEj: 230700 Provision Vacaciones'
    )
    account_salary_payable = fields.Many2one(
        'account.account', string='Salarios por Pagar',
        help='CREDITO -- Salario neto pendiente de pago al empleado.\nEj: 230000 Salarios por Pagar'
    )
    account_loans_payable = fields.Many2one(
        'account.account', string='Cuotas de Prestamos Retenidos',
        help='CREDITO -- Cuotas de prestamos retenidas al empleado pendientes de liquidar.\n'
             'Ej: 230900 Cuotas Prestamos Retenidos por Pagar\n\n'
             'Esta cuenta es necesaria para cuadrar el asiento cuando el empleado '
             'tiene prestamos activos con descuento en planilla.'
    )
    # BUG #11 FIX v50 -- Cuenta explicita para Prestamos a Empleados por Cobrar
    account_loans_receivable = fields.Many2one(
        'account.account',
        string='Prestamos a Empleados por Cobrar',
        help='DEBITO -- Activo corriente por prestamos otorgados a empleados. '
             'Ej: 115000 Prestamos a Empleados por Cobrar. '
             'Si no se configura, employee_loan.py crea la cuenta automaticamente.'
    )
    # FIX B-05 v53 -- Cuenta de Banco/Caja para desembolso de prestamos
    account_bank_disbursement = fields.Many2one(
        'account.account',
        string='Banco / Caja para Desembolso Prestamos',
        help='CREDITO -- Cuenta de Banco o Caja desde la que se desembolsan los prestamos a empleados. '
             'Ej: 110100 Banco Nacional CR (cuenta corriente). '
             'Si no se configura, el sistema intentara encontrar la primera cuenta '
             'de tipo Caja/Banco disponible (menos confiable).'
    )

    # BUG #10 FIX v50 -- Cuenta separada para Pensiones Alimentarias retenidas
    account_rop_payable = fields.Many2one(
        'account.account', string='ROP por Pagar (230350)',
        domain=[('account_type', '=', 'liability_current')],
        help='Cuenta del pasivo donde se acumula el ROP obrero (1%) + patronal (3.25%) '
             'pendiente de depositar al operador de pensiones (Ley 7983). '
             'Si queda vacio, se usa account_ccss_payable como fallback.'
    )

    account_pension_alimentaria_payable = fields.Many2one(
        'account.account',
        string='Pensiones Alimentarias por Pagar',
        help='CREDITO -- Pensiones alimentarias retenidas en planilla pendientes de girar al Juzgado. '
             'Mantener separado de Salarios por Pagar para control judicial (Ley 8590). '
             'Ej: 230950 Pensiones Alimentarias por Pagar. '
             'Si no se configura, usa la cuenta Salarios por Pagar como fallback.'
    )

    # FIX v49 Bug 5 -- Cuenta especifica para Subsidio CCSS por Cobrar (activo corriente)
    # Cuando el empleado esta incapacitado > 3 dias, la CCSS asume el pago.
    # El patrono registra un derecho de cobro contra la CCSS (activo) en el DEBE del asiento.
    # Si este campo queda vacio, el sistema usa account_ccss_payable como fallback (neteo).
    account_ccss_subsidy_receivable = fields.Many2one(
        'account.account',
        string='Subsidio CCSS por Cobrar',
        help='DEBITO -- Derecho de cobro del patrono ante la CCSS por subsidios de incapacidad '
             '(dias 4+ a cargo de la CCSS, Art. 79 Reglamento CCSS).\n'
             'Tipo de cuenta: Activo Corriente.\n'
             'Ej: 120500 Subsidio CCSS por Cobrar\n\n'
             'Si no se configura, el asiento usa la cuenta CCSS por Pagar como contrapartida '
             '(neteo). Para mayor claridad contable se recomienda configurar esta cuenta.'
    )
    # -- Liquidaciones ------------------------------------------------
    account_preaviso_expense = fields.Many2one(
        'account.account', string='Gasto Preaviso',
        help='DEBITO -- Gasto por preaviso en liquidacion.\nEj: 630500 Preaviso'
    )
    account_termination_payable = fields.Many2one(
        'account.account', string='Liquidaciones por Pagar',
        help='CREDITO -- Pasivo por liquidaciones pendientes.\nEj: 230800 Liquidaciones por Pagar'
    )

    # -- Embargos Judiciales -------------------------------------------------
    account_embargo_payable = fields.Many2one(
        'account.account',
        string='Embargos Judiciales por Pagar',
        help='CREDITO -- Embargos judiciales retenidos en planilla pendientes de girar al juzgado.\n'
             'Mantener separado de Salarios por Pagar para control judicial (Art. 172 CT).\n'
             'Ej: 230960 Embargos Judiciales por Pagar\n'
             'Si no se configura, usa la cuenta Salarios por Pagar como fallback.'
    )

    # -- Bonos e Incentivos --------------------------------------------------
    account_bono_expense = fields.Many2one(
        'account.account',
        string='Bonos e Incentivos (Gasto)',
        help='DEBITO -- Gasto por bonos salariales (productividad, asistencia, antiguedad,\n'
             'comisiones). Estos bonos integran el salario para CCSS y renta.\n'
             'Ej: 630600 Bonos e Incentivos\n'
             'Si no se configura, usa la cuenta Sueldos y Salarios (630000).'
    )
    account_subsidio_expense = fields.Many2one(
        'account.account',
        string='Subsidios al Personal (Gasto)',
        help='DEBITO -- Gasto por subsidios exentos de CCSS/Renta (transporte hasta tope,\n'
             'alimentacion en especie, gastos de representacion documentados).\n'
             'Ej: 630700 Subsidios al Personal\n'
             'Si no se configura, usa la cuenta Sueldos y Salarios (630000).'
    )
    account_licencia_expense = fields.Many2one(
        'account.account',
        string='Licencias Especiales con Goce (Gasto)',
        help='DEBITO -- Gasto por licencias laborales con goce de sueldo pagadas por el\n'
             'patrono: duelo 1er grado (3 dias), paternidad (8 dias), matrimonio (2 dias),\n'
             'adopcion (3 meses), donacion de sangre (1 dia), etc.\n'
             'Ej: 630800 Licencias y Permisos con Goce\n'
             'Si no se configura, usa la cuenta Sueldos y Salarios (630000).'
    )

    # -- Cobros al Empleado --------------------------------------------------
    account_cobro_empleado_payable = fields.Many2one(
        'account.account',
        string='Cobros al Empleado por Liquidar',
        domain=[('account_type', 'in', ['liability_current', 'asset_current'])],
        help='CREDITO -- Cuenta donde se acumulan los cobros descontados al empleado\n'
             '(almuerzos, productos, uniformes, parqueo, etc.) pendientes de liquidar\n'
             'con el proveedor o de trasladar a la cuenta de ingresos correspondiente.\n'
             'Ej: 230970 Cobros al Empleado por Liquidar\n\n'
             'Si no se configura, el sistema usa la cuenta Salarios por Pagar (230000)\n'
             'como fallback. Se recomienda una cuenta separada para mayor control.'
    )

    def action_repair_cross_company_accounts(self):
        """Boton manual: repara cuentas mal asociadas entre empresas.
        Util cuando hay un error 'cruce entre empresas' al postear boletas.
        """
        from .. import hooks
        hooks._repair_cross_company_accounts(self.env)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Reparación completada',
                'message': 'Cuentas y diario contable verificados y corregidos por empresa.',
                'type': 'success',
                'sticky': False,
            }
        }

    @api.model
    def get_config(self, company_id=None):
        company = company_id or self.env.company.id
        config = self.search([('company_id', '=', company)], limit=1)
        return config

    @api.model
    def _ensure_default_config(self):
        """Llamado en post_init y post_update para garantizar config por defecto."""
        post_init_hook(self.env)

    # -- Helpers internos ------------------------------------------

    def _get_account(self, code):
        """Busca cuenta por codigo en la compania actual."""
        return self.env['account.account'].search([
            ('code', '=', code),
            ('company_ids', 'in', self.env.company.id),
        ], limit=1)

    def _get_or_create_account(self, code, name, account_type):
        """
        Busca la cuenta por codigo. Si no existe, la crea.
        account_type Odoo 19: 'expense' | 'liability_current'
        """
        account = self._get_account(code)
        if not account:
            # FIX BUG-N05 v52: (4, id) es compatible Odoo 14-19. Command.link()
            # no se usa aqui porque requiere import adicional que puede fallar
            # en algunas instalaciones. (4, id) funciona en todas las versiones.
            account = self.env['account.account'].create({
                'code': code,
                'name': name,
                'account_type': account_type,
                'company_ids': [(4, self.env.company.id)],  # (4,id) compatible Odoo 14-19',
            })
        return account

    def _get_or_create_journal(self):
        """
        Busca el diario de planilla. Si no existe lo crea.
        Retorna (journal, fue_creado).
        """
        # L2 FIX: una sola query con OR en vez de 3 queries separadas
        j = self.env['account.journal'].search([
            ('type', 'in', ['general', 'purchase']),
            '|', '|',
            ('name', 'ilike', 'salario'),
            ('name', 'ilike', 'nomina'),
            ('name', 'ilike', 'planilla'),
            ('company_id', '=', self.env.company.id),
        ], limit=1)
        if j:
            return j, False
        # Cualquier diario general
        j = self.env['account.journal'].search([
            ('type', '=', 'general'),
            ('company_id', '=', self.env.company.id),
        ], limit=1)
        if j:
            return j, False
        # Crear diario nuevo
        j = self.env['account.journal'].create({
            'name': 'Planilla de Salarios',
            'code': 'PLAN',
            'type': 'general',
            'company_id': self.env.company.id,
        })
        return j, True

    # Alias para hooks.py (compatibilidad)
    def _get_journal(self):
        j, _ = self._get_or_create_journal()
        return j

    # -- Boton principal -------------------------------------------

    def action_autocompletar_cuentas(self):
        """
        Busca las cuentas estandar CR por codigo.
        Si no existen, las CREA automaticamente con tipo correcto.
        Solo toca campos vacios -- no sobreescribe los ya configurados.

        Cuentas que crea si no existen:
          GASTOS
            630000  Sueldos y Salarios
            630100  Cargas Sociales Patronales (CCSS+INS)
            630200  Provision para Vacaciones
            630300  Provision para Aguinaldo
            630400  Provision para Cesantia
          PASIVOS CORRIENTES
            230000  Salarios por Pagar
            230100  Retencion de Renta por Pagar
            230300  CCSS por Pagar
            230400  INS por Pagar (Riesgos del Trabajo)
            230500  Provision Aguinaldo por Pagar
            230600  Provision Cesantia por Pagar
            230700  Provision Vacaciones por Pagar
        """
        # campo -> (codigo, nombre, tipo_odoo19)
        ACCOUNT_MAP = {
            'account_salary_expense':         ('630000', 'Sueldos y Salarios',                    'expense'),
            'account_social_charges_expense': ('630100', 'Cargas Sociales Patronales (CCSS+INS)', 'expense'),
            'account_vacation_expense':       ('630200', 'Provision para Vacaciones',             'expense'),
            'account_aguinaldo_expense':      ('630300', 'Provision para Aguinaldo',              'expense'),
            'account_cesantia_expense':       ('630400', 'Provision para Cesantia / Auxilio',     'expense'),
            'account_salary_payable':         ('230000', 'Salarios por Pagar',                            'liability_current'),
            'account_income_tax_payable':     ('230100', 'Retencion de Renta por Pagar',                  'liability_current'),
            'account_ccss_payable':           ('230300', 'CCSS por Pagar',                                'liability_current'),
            'account_ins_payable':            ('230400', 'INS por Pagar (Riesgos del Trabajo)',            'liability_current'),
            'account_aguinaldo_provision':    ('230500', 'Provision Aguinaldo por Pagar',                  'liability_current'),
            'account_cesantia_provision':     ('230600', 'Provision Cesantia por Pagar',                   'liability_current'),
            'account_vacation_provision':     ('230700', 'Provision Vacaciones por Pagar',                 'liability_current'),
            'account_loans_payable':          ('230900', 'Cuotas Prestamos Retenidos por Pagar',           'liability_current'),
            'account_termination_payable':         ('230800', 'Liquidaciones por Pagar',                         'liability_current'),
            'account_preaviso_expense':            ('630500', 'Gasto por Preaviso',                              'expense'),
            # Embargos judiciales separados de salarios por pagar (control judicial)
            'account_embargo_payable':             ('230960', 'Embargos Judiciales por Pagar',                   'liability_current'),
            # Bonos salariales (afectos CCSS/renta) y subsidios exentos
            'account_bono_expense':                ('630600', 'Bonos e Incentivos al Personal',                  'expense'),
            'account_subsidio_expense':            ('630700', 'Subsidios al Personal (Transporte/Alim.)',        'expense'),
            'account_licencia_expense':            ('630800', 'Licencias y Permisos con Goce',                   'expense'),
            # FIX v49 Bug 5 -- Cuenta para subsidio CCSS por cobrar (activo corriente)
            'account_ccss_subsidy_receivable':     ('120500', 'Subsidio CCSS por Cobrar',                        'asset_current'),
            # BUG #10 FIX v50 -- Pensiones alimentarias separadas de salarios
            'account_rop_payable':                 ('230350', 'ROP por Pagar (Obrero+Patronal)', 'liability_current'),
            'account_pension_alimentaria_payable':  ('230950', 'Pensiones Alimentarias por Pagar',              'liability_current'),
            'account_cobro_empleado_payable':       ('230970', 'Cobros al Empleado por Liquidar',               'liability_current'),
            # BUG #11 FIX v50 -- Cuenta prestamos por cobrar explicita
            'account_loans_receivable':             ('115000', 'Prestamos a Empleados por Cobrar',             'asset_current'),
        }

        vals = {}
        creadas = []
        encontradas = []
        ya_configuradas = []

        # Diario
        if not self.journal_id:
            journal, fue_creado = self._get_or_create_journal()
            vals['journal_id'] = journal.id
            if fue_creado:
                creadas.append(f'Diario: {journal.name} (NUEVO)')
            else:
                encontradas.append(f'Diario: {journal.name}')

        # Cuentas
        for field_name, (code, name, acc_type) in ACCOUNT_MAP.items():
            current = getattr(self, field_name)
            if current:
                ya_configuradas.append(f'{current.code} {current.name}')
                continue
            existing = self._get_account(code)
            if existing:
                vals[field_name] = existing.id
                encontradas.append(f'{code} -- {existing.name}')
            else:
                new_acc = self._get_or_create_account(code, name, acc_type)
                vals[field_name] = new_acc.id
                creadas.append(f'{code} -- {name} (NUEVA)')

        if vals:
            self.write(vals)

        # Mensaje resultado
        msg_parts = []
        if creadas:
            msg_parts.append(
                f'OK Creadas {len(creadas)} cuentas/diarios nuevos:\n' +
                '\n'.join(f'   {x}' for x in creadas)
            )
        if encontradas:
            msg_parts.append(
                f' Encontradas {len(encontradas)} ya existentes y asignadas:\n' +
                '\n'.join(f'   {x}' for x in encontradas)
            )
        if ya_configuradas:
            msg_parts.append(
                f'INFO {len(ya_configuradas)} campo(s) ya tenian cuenta (no se modificaron).'
            )
        if not vals:
            msg_parts.append('INFO Toda la configuracion ya estaba completa.')

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': ' Configuracion Contable Completada',
                'message': '\n\n'.join(msg_parts),
                'type': 'success',
                'sticky': True,
            }
        }

    def action_test_email(self):
        """Abre wizard para enviar correo de prueba con destinatario configurable."""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Enviar Correo de Prueba',
            'res_model': 'planilla.test.email.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_config_id': self.id,
                'default_recipient': self.env.user.email or '',
            },
        }

    def _do_send_test_email(self, recipient):
        """Logica real de envio del correo de prueba."""
        import base64
        from odoo.exceptions import UserError

        if not recipient:
            raise UserError('Ingrese un correo destinatario para la prueba.')

        company = self.env.company
        period_sample = 'Periodo de Prueba'

        subject = (self.email_payslip_subject or 'Boleta de Pago - {period}').replace(
            '{period}', period_sample
        )
        body_str = str(self.email_payslip_body or '').replace(
            '{period}', period_sample
        ).replace('{company}', company.name or '')

        attachments = []
        sample_slip = self.env['planilla.payslip.cr'].search([
            ('company_id', '=', company.id),
            ('state', 'in', ('confirmed', 'paid')),
        ], limit=1)
        if sample_slip:
            try:
                report = self.env.ref('planilla_cr.action_report_payslip_cr')
                pdf_content, _ = self.env['ir.actions.report']._render_qweb_pdf(
                    report, [sample_slip.id]
                )
                pdf_b64 = base64.b64encode(pdf_content).decode('utf-8')
                attachments = [(0, 0, {
                    'name': 'Boleta_PRUEBA.pdf',
                    'datas': pdf_b64,
                    'mimetype': 'application/pdf',
                })]
            except Exception:
                pass

        # Prioridad del remitente:
        # 1. Campo "Remitente" configurado explicitamente en el modulo
        # 2. smtp_user del servidor de salida seleccionado (garantiza que coincida con SMTP)
        # 3. Email del usuario actual como fallback
        from_email = self.email_payslip_from or ''
        if not from_email and self.email_payslip_server_id:
            from_email = self.email_payslip_server_id.smtp_user or ''
        if not from_email:
            from_email = self.env.user.email or company.email or ''
        mail_vals = {
            'subject': '[PRUEBA] ' + subject,
            'body_html': body_str,
            'email_to': recipient,
            'email_from': from_email,
            'attachment_ids': attachments,
        }
        if self.email_payslip_server_id:
            mail_vals['mail_server_id'] = self.email_payslip_server_id.id

        mail = self.env['mail.mail'].create(mail_vals)
        mail.send()
        slip_note = (
            ' Se adjunto la boleta de ' + sample_slip.employee_id.name + '.'
            if sample_slip else ''
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Correo de prueba enviado',
                'message': 'Correo enviado a ' + recipient + '. Revise su bandeja.' + slip_note,
                'type': 'success',
                'sticky': False,
            },
        }

    # ── Sincronización bidireccional con nombramientos.config ──────────────
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('company_id'):
                vals['company_id'] = self.env.company.id
        return super().create(vals_list)

    def write(self, vals):
        res = super().write(vals)
        if ('default_payroll_calendar_id' in vals
                and not self.env.context.get('skip_planilla_sync')):
            self._sync_to_nombramientos_config()
        return res

    def _sync_to_nombramientos_config(self):
        # SAFETY: nunca falla si nombramientos_cr no está instalado.
        # planilla_cr funciona completamente solo — la sync es opcional.
        try:
            if 'nombramientos.config' not in self.env:
                return
        except Exception:
            return
        try:
            for rec in self:
                if not rec.default_payroll_calendar_id:
                    continue
                nom_config = self.env['nombramientos.config'].search([
                    ('company_id', '=', rec.company_id.id),
                ], limit=1)
                if not nom_config:
                    continue
                cal = rec.default_payroll_calendar_id
                freq_map = {
                    'weekly': 'weekly', 'biweekly': 'biweekly', 'monthly': 'monthly',
                }
                update = {'payroll_calendar_id': cal.id}
                if cal.frequency in freq_map:
                    update['payment_frequency'] = freq_map[cal.frequency]
                nom_config.with_context(skip_planilla_sync=True).write(update)
        except Exception:
            # No propagar errores de sync — planilla_cr no depende de nombramientos
            pass
