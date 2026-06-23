import logging
from odoo import models
from odoo.exceptions import UserError
from .. import planilla_const as K  # constantes CR 2026 -- disponible para uso futuro

_logger = logging.getLogger(__name__)

class PayslipAccountingMixin(models.AbstractModel):
    """
    Mixin: generacion del asiento contable de la boleta (per_employee mode).
    _create_accounting_entry -- construye el asiento DEBE/HABER con las 14 cuentas.
    """
    _name = 'planilla.payslip.accounting.mixin'
    _description = 'Mixin Contabilidad Boleta'

    def _create_accounting_entry(self):
        self.ensure_one()
        config = self.env['planilla.accounting.config'].sudo().get_config(self.company_id.id)

        # C3 -- Avisar explicitamente si no hay configuracion contable
        if not config:
            raise UserError(
                'No existe configuracion contable para esta compania. '
                'Configure las cuentas en Planilla -> Configuracion -> Contabilidad.'
            )
        if not config.journal_id:
            raise UserError(
                'No hay diario contable configurado para planilla. '
                'Configure el diario en Planilla -> Configuracion -> Contabilidad.'
            )

        lines = []
        _move_currency = config.journal_id.currency_id or self.company_id.currency_id

        def add_line(account, debit=0.0, credit=0.0, name=''):
            if not account:
                return
            debit, credit = round(debit, 2), round(credit, 2)
            if debit == 0.0 and credit == 0.0:
                return
            lines.append((0, 0, {
                'account_id': account.id, 'name': name,
                'debit': debit, 'credit': credit,
                'currency_id': _move_currency.id,
            }))

        emp = self.employee_id.name

        # ======================================================================
        # LOGICA DE CUADRE -- v48
        #
        # El asiento DEBE cuadrar con CUALQUIER combinacion de:
        #   - Horas extras (ya incluidas en gross_salary <- OK)
        #   - Incapacidades: ccss_subsidy_total (subsidio CCSS dias 4+)
        #                    employer_disability_cost (patrono paga dias 1-3)
        #   - Paternidad:    paternity_amount
        #   - Pensiones alimentarias
        #   - Prestamos
        #   - Ingresos adicionales (line_type='income')
        #   - Otras deducciones (sindicato, embargo, ausencias, etc.)
        #
        # REGLA FUNDAMENTAL:
        #   DEBE = HABER siempre.
        #   Todo lo que entra en salary_payable (HABER) debe tener contrapartida en DEBE.
        #   Todo lo que es gasto patronal (DEBE) debe tener contrapartida en HABER.
        # ======================================================================

        # -- Calcular cada componente localmente (no depender de campos compute) --
        gross         = round(self.gross_salary or 0.0, 2)
        ccss_emp      = round(self.ccss_employee or 0.0, 2)
        ccss_pat      = round(self.ccss_employer or 0.0, 2)
        ins_pat       = round(self.ins_employer or 0.0, 2)
        renta         = round(self.income_tax or 0.0, 2)
        vac_prov      = round(self.vacation_provision or 0.0, 2)
        agui_prov     = round(self.aguinaldo_provision or 0.0, 2)
        ces_prov      = round(self.cesantia_provision or 0.0, 2)
        subsidy       = round(self.ccss_subsidy_total or 0.0, 2)
        pat_amount    = round(self.paternity_amount or 0.0, 2)
        dis_cost      = round(self.employer_disability_cost or 0.0, 2)

        # Separar deducciones e ingresos adicionales con detalle para CR
        # Bonos salariales: afecto_ccss=True -> van a cuenta 630600
        # Subsidios exentos: afecto_ccss=False -> van a cuenta 630700
        bono_ids_en_boleta = self.deduction_line_ids.filtered(
            lambda l: l.line_type == 'income' and l.deduction_category == 'bonus'
        )

        # FIX C-02b v58: Pre-cargar todos los bonos activos del empleado en UNA query.
        # El loop anterior hacia search() por cada linea de bono (N+1).
        _bonos_emp = self.env['planilla.bono'].search([
            ('employee_id', '=', self.employee_id.id),
            ('state', '=', 'active'),
        ])
        _bono_map = {b.name: b for b in _bonos_emp}  # O(1) lookup

        # Separar bonos: salariales (afecto_ccss=True -> 630600) vs subsidios exentos (630700)
        bonos_salariales = 0.0
        subsidios_exentos = 0.0
        for line in bono_ids_en_boleta:
            concepto = line.description.replace('Bono: ', '').strip()
            bono_rec = _bono_map.get(concepto)  # O(1) -- sin query adicional
            if bono_rec and not bono_rec.afecto_ccss:
                subsidios_exentos = round(subsidios_exentos + line.amount, 2)
            else:
                bonos_salariales = round(bonos_salariales + line.amount, 2)

        # Otros ingresos adicionales (recurring_benefit tipo income, no bonos, no licencias)
        # FIX-F5: excluir 'licencia_con_goce' de otros_ingresos -- se contabiliza
        # por separado en account_licencia_expense (630800). Incluirla aqui causaba
        # doble DEBE: una vez en extra_income y otra en add_line(licencias_con_goce),
        # haciendo que el asiento no cuadrara cuando hay licencias especiales.
        otros_ingresos = round(sum(
            l.amount for l in self.deduction_line_ids
            if l.line_type == 'income'
            and l.deduction_category not in ('bonus', 'licencia_con_goce')
        ), 2)
        extra_income = round(bonos_salariales + subsidios_exentos + otros_ingresos, 2)

        pensiones = round(sum(
            l.amount for l in self.deduction_line_ids
            if l.deduction_category == 'pension_alimentaria'
        ), 2)
        prestamos = round(sum(
            l.amount for l in self.deduction_line_ids
            if l.deduction_category == 'loan'
        ), 2)
        embargos = round(sum(
            l.amount for l in self.deduction_line_ids
            if l.deduction_category == 'embargo'
        ), 2)
        ausencias = round(sum(
            l.amount for l in self.deduction_line_ids
            if l.deduction_category == 'ausencia'
        ), 2)
        # Licencias CON goce: ingreso adicional en la boleta (gasto patronal -> DEBE 630800)
        licencias_con_goce = round(sum(
            l.amount for l in self.deduction_line_ids
            if l.deduction_category == 'licencia_con_goce' and l.line_type == 'income'
        ), 2)
        # Licencias SIN goce: deduccion al empleado (reduce neto a pagar -> HABER 230000)
        licencias_sin_goce = round(sum(
            l.amount for l in self.deduction_line_ids
            if l.deduction_category == 'licencia_sin_goce' and l.line_type == 'deduction'
        ), 2)
        # FIX v512 BUG-CRITICO-01: 'rop' excluido de otras_ded.
        # El ROP obrero va a account_rop_payable (230350), no a account_salary_payable.
        # Sin esta exclusion, el ROP se descontaba dos veces de net_for_accounting
        # (una en otras_ded y otra en rop_obrero_net), haciendo que salary_payable
        # fuera incorrecto y la cuenta 230000 recibiera el monto equivocado.
        otras_ded = round(sum(
            l.amount for l in self.deduction_line_ids
            if l.line_type == 'deduction'
               and l.deduction_category not in (
                   'pension_alimentaria', 'loan', 'ausencia', 'embargo', 'rop',
                   'licencia_sin_goce', 'other',
               )
        ), 2)
        # Cobros al empleado (almuerzos, productos, uniformes, etc.)
        # Categoria 'other' con employee_charge_id -> van a cuenta 230970
        cobros_empleado = round(sum(
            l.amount for l in self.deduction_line_ids
            if l.line_type == 'deduction'
               and l.deduction_category == 'other'
               and l.employee_charge_id
        ), 2)
        # Otras deducciones 'other' sin employee_charge_id (manuales)
        otras_ded_manual = round(sum(
            l.amount for l in self.deduction_line_ids
            if l.line_type == 'deduction'
               and l.deduction_category == 'other'
               and not l.employee_charge_id
        ), 2)
        otras_ded = round(otras_ded + otras_ded_manual, 2)

        # deposito_patrono: lo que la empresa REALMENTE deposita al empleado
        # = gross - ccss_emp - renta + paternidad + extra_income
        #   + licencias_con_goce
        #   - pensiones - embargos - prestamos - ausencias - licencias_sg
        #   - rop_obrero - otras_ded
        # NOTA: el subsidio CCSS (subsidy) NO entra en deposito_patrono porque
        # la Caja lo deposita directamente al empleado. La empresa solo registra
        # el derecho de cobro (DEBE 120500) y la obligacion hacia el empleado (HABER).
        # ROP obrero va a 230350 (rop_payable), no a 230000 (salary_payable)
        rop_obrero_net = round(sum(
            l.amount for l in self.deduction_line_ids
            if l.deduction_category == 'rop' and l.line_type == 'deduction'
        ), 2)
        # deposito_contable = lo que la empresa deposita al empleado (HABER 230000)
        # CASO normal (sin subsidio):
        #   deposito_contable = gross - ccss_emp - renta +/- extras y deducciones
        # CASO con subsidio (incapacidad/maternidad):
        #   gross puede ser 0 (maternidad total) -> la formula daria negativo.
        #   En estos casos usar neto_por_patrono que ya tiene el calculo correcto
        #   del 50/50 y del desglose patrono/CCSS/INS.
        ins_subsidy_local = round(self.ins_subsidy_total or 0.0, 2)
        if subsidy > 0 or ins_subsidy_local > 0:
            deposito_contable = round(self.neto_por_patrono or 0.0, 2)
        else:
            # FIX-ACC-02: bono_salarial ya esta en gross, no incluirlo de nuevo
            # extra_income = bonos + subsidios + otros -> usamos solo lo que no esta en gross
            net_extras = round(subsidios_exentos + otros_ingresos, 2)
            deposito_contable = round(
                gross - ccss_emp - renta
                + pat_amount
                + net_extras
                + licencias_con_goce
                - pensiones
                - embargos
                - prestamos
                - ausencias
                - licencias_sin_goce
                - rop_obrero_net
                - cobros_empleado
                - otras_ded,
                2
            )
        net_for_accounting = deposito_contable  # alias limpio

        # -- DEBITOS (Gastos del patrono) -------------------------------------
        add_line(config.account_salary_expense,
                 debit=gross,
                 name=f'Salarios -- {emp}')

        add_line(config.account_social_charges_expense,
                 debit=round(ccss_pat + ins_pat, 2),
                 name=f'Cargas Sociales Patronales -- {emp}')
        # FIX v56: ROP patronal 3.25% (Ley 7983) -- costo del patrono
        if (self.rop_employer or 0.0) > 0:
            add_line(config.account_social_charges_expense,
                     debit=round(self.rop_employer, 2),
                     name=f'ROP Patronal 3.25% Ley 7983 -- {emp}')

        add_line(config.account_vacation_expense,
                 debit=vac_prov,
                 name=f'Provision Vacaciones -- {emp}')

        add_line(config.account_aguinaldo_expense,
                 debit=agui_prov,
                 name=f'Provision Aguinaldo -- {emp}')

        add_line(config.account_cesantia_expense,
                 debit=ces_prov,
                 name=f'Provision Cesantia -- {emp}')

        # FIX: Paternidad -- gasto patronal que entra en net pero no tenia DEBE
        if pat_amount > 0:
            add_line(config.account_salary_expense,
                     debit=pat_amount,
                     name=f'Paternidad (8 dias Art. 95 CT) -- {emp}')

        # FIX: Dias 1-3 incapacidad a cargo del patrono (Art. 79 Reg. CCSS)
        if dis_cost > 0:
            add_line(config.account_salary_expense,
                     debit=dis_cost,
                     name=f'Incapacidad dias 1-3 (cargo patrono) -- {emp}')

        # FIX v49 Bug 5: Subsidio CCSS -- la CCSS paga dias 4+ directamente al empleado.
        # El patrono registra un derecho de cobro (activo corriente) en el DEBE del asiento.
        # Jerarquia de cuentas:
        #   1. account_ccss_subsidy_receivable configurado en Planilla -> Configuracion -> Contabilidad
        #   2. Busqueda automatica de cuenta 120500 en el plan de cuentas de la compania
        #   3. Fallback: account_ccss_payable (neteo -- menos claro pero cuadra el asiento)
        if subsidy > 0:
            # Prioridad 1: cuenta configurada explicitamente por el contador
            ccss_subsidy_acct = config.account_ccss_subsidy_receivable

            # Prioridad 2: buscar cuenta 120500 en el plan de cuentas
            if not ccss_subsidy_acct:
                ccss_subsidy_acct = self.env['account.account'].search([
                    ('code', '=', '120500'),
                    ('company_ids', 'in', self.env.company.id),
                ], limit=1)

            # Prioridad 3: fallback a CCSS por pagar (neteo contable)
            if not ccss_subsidy_acct:
                ccss_subsidy_acct = config.account_ccss_payable
                _logger.info(
                    'planilla_cr: usando account_ccss_payable como fallback para subsidio CCSS '
                    '(empresa %s). Configure account_ccss_subsidy_receivable en '
                    'Planilla -> Configuracion -> Contabilidad para mayor claridad contable.',
                    self.company_id.name
                )

            add_line(ccss_subsidy_acct,
                     debit=subsidy,
                     name=f'Subsidio CCSS por Cobrar (incapacidad) -- {emp}')

        # FIX-ACC-01: bonos_salariales YA estan en gross (gross = sal_base + overtime + vacation + other_income + bono_salarial)
        # Agregar DEBE separado causaba doble contabilizacion del gasto.
        # Solo se registra como DEBE separado el subsidio exento (NO esta en gross).
        # Subsidios exentos (transporte hasta tope, representacion): cuenta 630700
        if subsidios_exentos > 0:
            subs_acct = config.account_subsidio_expense or config.account_salary_expense
            add_line(subs_acct,
                     debit=subsidios_exentos,
                     name=f'Subsidios al Personal (exentos CCSS/Renta) -- {emp}')
        # Otros ingresos adicionales (recurring_benefit): cuenta 630000
        if otros_ingresos > 0:
            add_line(config.account_salary_expense,
                     debit=otros_ingresos,
                     name=f'Ingresos Adicionales en Boleta -- {emp}')

        # Licencias con goce: gasto patronal a cuenta 630800 (o fallback 630000)
        # Duelo 1er grado, paternidad/adopcion, matrimonio, donacion de sangre, etc.
        if licencias_con_goce > 0:
            lic_acct = getattr(config, 'account_licencia_expense', None) or config.account_salary_expense
            add_line(lic_acct,
                     debit=licencias_con_goce,
                     name=f'Licencias con Goce (duelo/paternidad/matrimonio...) -- {emp}')

        # -- CREDITOS (Pasivos y retenciones) ---------------------------------
        add_line(config.account_ccss_payable,
                 credit=round(ccss_emp + ccss_pat, 2),
                 name=f'CCSS por Pagar (obrero + patronal) -- {emp}')

        add_line(config.account_ins_payable,
                 credit=ins_pat,
                 name=f'INS por Pagar -- {emp}')

        add_line(config.account_income_tax_payable,
                 credit=renta,
                 name=f'Retencion Renta -- {emp}')

        add_line(config.account_aguinaldo_provision,
                 credit=agui_prov,
                 name=f'Provision Aguinaldo por Pagar -- {emp}')

        add_line(config.account_cesantia_provision,
                 credit=ces_prov,
                 name=f'Provision Cesantia por Pagar -- {emp}')

        add_line(config.account_vacation_provision,
                 credit=vac_prov,
                 name=f'Provision Vacaciones por Pagar -- {emp}')

        if pensiones > 0:
            # Pensiones alimentarias van a cuenta separada para control judicial (Ley 8590)
            pension_account = (config.account_pension_alimentaria_payable
                               or config.account_salary_payable)
            add_line(pension_account,
                     credit=pensiones,
                     name=f'Pension Alimentaria Retenida -- {emp}')

        if embargos > 0:
            # Embargos judiciales van a cuenta separada para control judicial (Art. 172 CT)
            embargo_account = (config.account_embargo_payable
                               or config.account_salary_payable)
            add_line(embargo_account,
                     credit=embargos,
                     name=f'Embargo Judicial Retenido -- {emp}')

        if prestamos > 0:
            loan_account = config.account_loans_payable or config.account_salary_payable
            add_line(loan_account,
                     credit=prestamos,
                     name=f'Cuotas Prestamos Retenidos -- {emp}')

        if ausencias > 0:
            add_line(config.account_salary_payable,
                     credit=ausencias,
                     name=f'Descuento Ausencias Sin Goce -- {emp}')

        if licencias_sin_goce > 0:
            add_line(config.account_salary_payable,
                     credit=licencias_sin_goce,
                     name=f'Descuento Licencias Sin Goce -- {emp}')

        # FIX v56: ROP -- HABER para ambos tramos (obrero + patronal)
        # El ROP obrero reduce el neto; el ROP patronal es costo adicional del patrono.
        # Ambos se acumulan en account_rop_payable (230350) para deposito al operador.
        rop_obrero_acct_amt = sum(
            l.amount for l in self.deduction_line_ids
            if l.deduction_category == 'rop' and l.line_type == 'deduction'
        )
        rop_patronal_amt = round(self.rop_employer or 0.0, 2)
        total_rop_pagar = round(rop_obrero_acct_amt + rop_patronal_amt, 2)
        if total_rop_pagar > 0:
            rop_acct = (getattr(config, 'account_rop_payable', None)
                        or config.account_ccss_payable)
            add_line(rop_acct, credit=total_rop_pagar,
                     name=f'ROP por Pagar (obrero 1% + patronal 3.25%) -- {emp}')

        if otras_ded > 0:
            add_line(config.account_salary_payable,
                     credit=otras_ded,
                     name=f'Otras Deducciones Retenidas -- {emp}')

        # Cobros al empleado: van a cuenta 230970 (separada de 230000 para control)
        if cobros_empleado > 0:
            cobro_acct = (getattr(config, 'account_cobro_empleado_payable', None)
                          or config.account_salary_payable)
            add_line(cobro_acct,
                     credit=cobros_empleado,
                     name=f'Cobros al Empleado Retenidos (almuerzos/productos...) -- {emp}')

        # Dias 1-3 de incapacidad se pagan al empleado pero son gasto patronal
        if dis_cost > 0:
            add_line(config.account_salary_payable,
                     credit=dis_cost,
                     name=f'Incapacidad dias 1-3 (por pagar al empleado) -- {emp}')

        # Deposito real de la empresa al empleado (SIN subsidios CCSS/INS)
        if deposito_contable > 0:
            add_line(config.account_salary_payable,
                     credit=deposito_contable,
                     name=f'Salarios por Pagar (deposito empresa) -- {emp}')

        # Subsidio CCSS/INS: obligacion hacia el empleado via Caja/INS
        # La empresa actua como intermediaria:
        #   DEBE  120500 Subsidio CCSS x Cobrar (ya registrado arriba)
        #   HABER 230300 CCSS por Pagar (obligacion de transferir al empleado)
        # Cuando la Caja deposita el subsidio, se cancela: DEBE 230300 / HABER Banco
        if subsidy > 0:
            add_line(config.account_ccss_payable,
                     credit=subsidy,
                     name=f'Subsidio CCSS x Pagar al Empleado (la Caja deposita directo) -- {emp}')
        if ins_subsidy_local > 0:
            add_line(config.account_ins_payable,
                     credit=ins_subsidy_local,
                     name=f'Subsidio INS x Pagar al Empleado (INS deposita directo) -- {emp}')

        if not lines:
            return

        # -- Verificacion de cuadre matematico antes de postear ---------------
        total_debit  = round(sum(l[2]['debit']  for l in lines), 2)
        total_credit = round(sum(l[2]['credit'] for l in lines), 2)
        if abs(total_debit - total_credit) > 0.02:
            # Generar diagnostico detallado para facilitar depuracion
            detail = '\n'.join(
                f"  {'DEBE' if l[2]['debit'] else 'HABER'} CRC{max(l[2]['debit'], l[2]['credit']):>12,.2f}  {l[2]['name']}"
                for l in lines
            )
            raise UserError(
                f'El asiento contable no cuadra para {emp}:\n'
                f'  Debitos:  CRC{total_debit:,.2f}\n'
                f'  Creditos: CRC{total_credit:,.2f}\n'
                f'  Diferencia: CRC{abs(total_debit - total_credit):,.2f}\n\n'
                f'Detalle de lineas:\n{detail}\n\n'
                f'Verifique la configuracion contable en Planilla -> Configuracion -> Contabilidad.'
            )

        move = self.env['account.move'].sudo().create({
            'journal_id': config.journal_id.id,
            'company_id': self.company_id.id,
            'date': self.date_to,
            'ref': f'Planilla: {self.name}',
            'move_type': 'entry',
            'currency_id': _move_currency.id,
            'line_ids': lines,
        })
        move.action_post()
        self.move_id = move.id
        # FIX A-03 v58: Logging de trazabilidad del asiento contable
        _logger.info(
            'planilla_cr._create_accounting_entry: asiento %s (id=%d) -- '
            'DEBE=CRC%.2f HABER=CRC%.2f empleado=%s fecha=%s',
            move.name, move.id, total_debit, total_credit,
            self.employee_id.name, self.date_to
        )


