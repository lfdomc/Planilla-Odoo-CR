from odoo import models, fields, api


class PayrollAccountingConfig(models.Model):
    """Configuracion de cuentas contables para planilla CR.
    Una configuracion por compania.
    """
    _name = 'planilla.accounting.config'
    _description = 'Configuracion Contable de Planilla'
    _rec_name = 'company_id'

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
        default='per_employee', required=True,
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
