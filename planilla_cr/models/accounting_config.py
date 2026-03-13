from odoo import models, fields, api


class PayrollAccountingConfig(models.Model):
    """Configuración de cuentas contables para planilla CR.
    Una configuración por compañía.
    """
    _name = 'planilla.accounting.config'
    _description = 'Configuración Contable de Planilla'
    _rec_name = 'company_id'

    company_id = fields.Many2one(
        'res.company', string='Compañía',
        required=True, default=lambda self: self.env.company,
        ondelete='cascade'
    )

    # ── Modo de generación de asientos ─────────────────────────────
    accounting_entry_mode = fields.Selection([
        ('per_employee', 'Por Empleado (un asiento por boleta)'),
        ('per_run', 'Por Planilla (un asiento consolidado por planilla)'),
    ], string='Modo de Asiento Contable',
        default='per_employee', required=True,
        help='Define si se genera un asiento contable por cada boleta de pago '
             'o un único asiento consolidado por planilla.'
    )

    # ── Diario ──────────────────────────────────────────────────────
    journal_id = fields.Many2one(
        'account.journal', string='Diario de Planilla',
        domain=[('type', 'in', ['general', 'purchase'])],
        help='Diario contable donde se registran los asientos de planilla.'
    )

    # ══════════════════════════════════════════════════════════════
    # CUENTAS DE GASTO (DÉBITO)
    # ══════════════════════════════════════════════════════════════
    account_salary_expense = fields.Many2one(
        'account.account', string='Salarios (Gasto)',
        help='DÉBITO — Salario bruto del empleado.\nEj: 630000 Sueldos y Salarios'
    )
    account_social_charges_expense = fields.Many2one(
        'account.account', string='Cargas Sociales Patronales (Gasto)',
        help='DÉBITO — CCSS Patronal (26.83%) + INS (1%).\nEj: 630100 Cargas Sociales'
    )
    account_vacation_expense = fields.Many2one(
        'account.account', string='Vacaciones (Gasto)',
        help='DÉBITO — Provisión de vacaciones (4.16%).\nEj: 630200 Vacaciones'
    )
    account_aguinaldo_expense = fields.Many2one(
        'account.account', string='Aguinaldo (Gasto)',
        help='DÉBITO — Provisión aguinaldo (8.33%).\nEj: 630300 Aguinaldo'
    )
    account_cesantia_expense = fields.Many2one(
        'account.account', string='Cesantía / Auxilio (Gasto)',
        help='DÉBITO — Provisión de auxilio de cesantía (5.33%).\nEj: 630400 Cesantía'
    )

    # ══════════════════════════════════════════════════════════════
    # CUENTAS POR PAGAR (CRÉDITO)
    # ══════════════════════════════════════════════════════════════
    account_ccss_payable = fields.Many2one(
        'account.account', string='CCSS por Pagar (Obrera + Patronal)',
        help='CRÉDITO — CCSS Obrera (10.83%) + Patronal (26.83%).\nEj: 230300 CCSS por Pagar'
    )
    account_ins_payable = fields.Many2one(
        'account.account', string='INS por Pagar',
        help='CRÉDITO — INS Riesgos del Trabajo (~1%).\nEj: 230400 INS por Pagar'
    )
    account_income_tax_payable = fields.Many2one(
        'account.account', string='Retención Renta por Pagar',
        help='CRÉDITO — Impuesto de renta retenido.\nEj: 230100 Retención Renta'
    )
    account_aguinaldo_provision = fields.Many2one(
        'account.account', string='Provisión Aguinaldo por Pagar',
        help='CRÉDITO — Pasivo acumulado de aguinaldo.\nEj: 230500 Provisión Aguinaldo'
    )
    account_cesantia_provision = fields.Many2one(
        'account.account', string='Provisión Cesantía por Pagar',
        help='CRÉDITO — Pasivo acumulado de cesantía.\nEj: 230600 Provisión Cesantía'
    )
    account_vacation_provision = fields.Many2one(
        'account.account', string='Provisión Vacaciones por Pagar',
        help='CRÉDITO — Pasivo acumulado de vacaciones.\nEj: 230700 Provisión Vacaciones'
    )
    account_salary_payable = fields.Many2one(
        'account.account', string='Salarios por Pagar',
        help='CRÉDITO — Salario neto pendiente de pago al empleado.\nEj: 230000 Salarios por Pagar'
    )
    account_loans_payable = fields.Many2one(
        'account.account', string='Cuotas de Préstamos Retenidos',
        help='CRÉDITO — Cuotas de préstamos retenidas al empleado pendientes de liquidar.\n'
             'Ej: 230900 Cuotas Préstamos Retenidos por Pagar\n\n'
             'Esta cuenta es necesaria para cuadrar el asiento cuando el empleado '
             'tiene préstamos activos con descuento en planilla.'
    )
    # BUG #11 FIX v50 — Cuenta explícita para Préstamos a Empleados por Cobrar
    account_loans_receivable = fields.Many2one(
        'account.account',
        string='Préstamos a Empleados por Cobrar',
        help='DÉBITO — Activo corriente por préstamos otorgados a empleados. '
             'Ej: 115000 Préstamos a Empleados por Cobrar. '
             'Si no se configura, employee_loan.py crea la cuenta automáticamente.'
    )
    # FIX B-05 v53 — Cuenta de Banco/Caja para desembolso de préstamos
    account_bank_disbursement = fields.Many2one(
        'account.account',
        string='Banco / Caja para Desembolso Préstamos',
        help='CRÉDITO — Cuenta de Banco o Caja desde la que se desembolsan los préstamos a empleados. '
             'Ej: 110100 Banco Nacional CR (cuenta corriente). '
             'Si no se configura, el sistema intentará encontrar la primera cuenta '
             'de tipo Caja/Banco disponible (menos confiable).'
    )

    # BUG #10 FIX v50 — Cuenta separada para Pensiones Alimentarias retenidas
    account_pension_alimentaria_payable = fields.Many2one(
        'account.account',
        string='Pensiones Alimentarias por Pagar',
        help='CRÉDITO — Pensiones alimentarias retenidas en planilla pendientes de girar al Juzgado. '
             'Mantener separado de Salarios por Pagar para control judicial (Ley 8590). '
             'Ej: 230950 Pensiones Alimentarias por Pagar. '
             'Si no se configura, usa la cuenta Salarios por Pagar como fallback.'
    )

    # FIX v49 Bug 5 — Cuenta específica para Subsidio CCSS por Cobrar (activo corriente)
    # Cuando el empleado está incapacitado > 3 días, la CCSS asume el pago.
    # El patrono registra un derecho de cobro contra la CCSS (activo) en el DEBE del asiento.
    # Si este campo queda vacío, el sistema usa account_ccss_payable como fallback (neteo).
    account_ccss_subsidy_receivable = fields.Many2one(
        'account.account',
        string='Subsidio CCSS por Cobrar',
        help='DÉBITO — Derecho de cobro del patrono ante la CCSS por subsidios de incapacidad '
             '(días 4+ a cargo de la CCSS, Art. 79 Reglamento CCSS).\n'
             'Tipo de cuenta: Activo Corriente.\n'
             'Ej: 120500 Subsidio CCSS por Cobrar\n\n'
             'Si no se configura, el asiento usa la cuenta CCSS por Pagar como contrapartida '
             '(neteo). Para mayor claridad contable se recomienda configurar esta cuenta.'
    )
    # ── Liquidaciones ────────────────────────────────────────────────
    account_preaviso_expense = fields.Many2one(
        'account.account', string='Gasto Preaviso',
        help='DÉBITO — Gasto por preaviso en liquidación.\nEj: 630500 Preaviso'
    )
    account_termination_payable = fields.Many2one(
        'account.account', string='Liquidaciones por Pagar',
        help='CRÉDITO — Pasivo por liquidaciones pendientes.\nEj: 230800 Liquidaciones por Pagar'
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

    # ── Helpers internos ──────────────────────────────────────────

    def _get_account(self, code):
        """Busca cuenta por código en la compañía actual."""
        return self.env['account.account'].search([
            ('code', '=', code),
            ('company_ids', 'in', self.env.company.id),
        ], limit=1)

    def _get_or_create_account(self, code, name, account_type):
        """
        Busca la cuenta por código. Si no existe, la crea.
        account_type Odoo 19: 'expense' | 'liability_current'
        """
        account = self._get_account(code)
        if not account:
            # FIX BUG-N05 v52: (4, id) es compatible Odoo 14-19. Command.link()
            # no se usa aquí porque requiere import adicional que puede fallar
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

    # ── Botón principal ───────────────────────────────────────────

    def action_autocompletar_cuentas(self):
        """
        Busca las cuentas estándar CR por código.
        Si no existen, las CREA automáticamente con tipo correcto.
        Solo toca campos vacíos — no sobreescribe los ya configurados.

        Cuentas que crea si no existen:
          GASTOS
            630000  Sueldos y Salarios
            630100  Cargas Sociales Patronales (CCSS+INS)
            630200  Provisión para Vacaciones
            630300  Provisión para Aguinaldo
            630400  Provisión para Cesantía
          PASIVOS CORRIENTES
            230000  Salarios por Pagar
            230100  Retención de Renta por Pagar
            230300  CCSS por Pagar
            230400  INS por Pagar (Riesgos del Trabajo)
            230500  Provisión Aguinaldo por Pagar
            230600  Provisión Cesantía por Pagar
            230700  Provisión Vacaciones por Pagar
        """
        # campo → (código, nombre, tipo_odoo19)
        ACCOUNT_MAP = {
            'account_salary_expense':         ('630000', 'Sueldos y Salarios',                    'expense'),
            'account_social_charges_expense': ('630100', 'Cargas Sociales Patronales (CCSS+INS)', 'expense'),
            'account_vacation_expense':       ('630200', 'Provisión para Vacaciones',             'expense'),
            'account_aguinaldo_expense':      ('630300', 'Provisión para Aguinaldo',              'expense'),
            'account_cesantia_expense':       ('630400', 'Provisión para Cesantía / Auxilio',     'expense'),
            'account_salary_payable':         ('230000', 'Salarios por Pagar',                            'liability_current'),
            'account_income_tax_payable':     ('230100', 'Retención de Renta por Pagar',                  'liability_current'),
            'account_ccss_payable':           ('230300', 'CCSS por Pagar',                                'liability_current'),
            'account_ins_payable':            ('230400', 'INS por Pagar (Riesgos del Trabajo)',            'liability_current'),
            'account_aguinaldo_provision':    ('230500', 'Provisión Aguinaldo por Pagar',                  'liability_current'),
            'account_cesantia_provision':     ('230600', 'Provisión Cesantía por Pagar',                   'liability_current'),
            'account_vacation_provision':     ('230700', 'Provisión Vacaciones por Pagar',                 'liability_current'),
            'account_loans_payable':          ('230900', 'Cuotas Préstamos Retenidos por Pagar',           'liability_current'),
            'account_termination_payable':         ('230800', 'Liquidaciones por Pagar',                         'liability_current'),
            'account_preaviso_expense':            ('630500', 'Gasto por Preaviso',                              'expense'),
            # FIX v49 Bug 5 — Cuenta para subsidio CCSS por cobrar (activo corriente)
            'account_ccss_subsidy_receivable':     ('120500', 'Subsidio CCSS por Cobrar',                        'asset_current'),
            # BUG #10 FIX v50 — Pensiones alimentarias separadas de salarios
            'account_pension_alimentaria_payable':  ('230950', 'Pensiones Alimentarias por Pagar',              'liability_current'),
            # BUG #11 FIX v50 — Cuenta préstamos por cobrar explícita
            'account_loans_receivable':             ('115000', 'Préstamos a Empleados por Cobrar',             'asset_current'),
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
                encontradas.append(f'{code} — {existing.name}')
            else:
                new_acc = self._get_or_create_account(code, name, acc_type)
                vals[field_name] = new_acc.id
                creadas.append(f'{code} — {name} (NUEVA)')

        if vals:
            self.write(vals)

        # Mensaje resultado
        msg_parts = []
        if creadas:
            msg_parts.append(
                f'✅ Creadas {len(creadas)} cuentas/diarios nuevos:\n' +
                '\n'.join(f'  • {x}' for x in creadas)
            )
        if encontradas:
            msg_parts.append(
                f'🔍 Encontradas {len(encontradas)} ya existentes y asignadas:\n' +
                '\n'.join(f'  • {x}' for x in encontradas)
            )
        if ya_configuradas:
            msg_parts.append(
                f'ℹ️ {len(ya_configuradas)} campo(s) ya tenían cuenta (no se modificaron).'
            )
        if not vals:
            msg_parts.append('ℹ️ Toda la configuración ya estaba completa.')

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': '⚡ Configuración Contable Completada',
                'message': '\n\n'.join(msg_parts),
                'type': 'success',
                'sticky': True,
            }
        }
