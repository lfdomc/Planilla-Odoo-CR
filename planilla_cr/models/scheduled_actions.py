from odoo import models, api
import logging
from datetime import date
from dateutil.relativedelta import relativedelta

_logger = logging.getLogger(__name__)


class PlanillaScheduledActions(models.AbstractModel):
    """
    Acciones programadas (cron jobs) del módulo Planilla CR.
    Se ejecutan automáticamente según la frecuencia configurada.
    """
    _name = 'planilla.scheduled.actions'
    _description = 'Acciones Programadas Planilla CR'

    @api.model
    def cron_check_anniversaries(self):
        """
        Detecta empleados que cumplen aniversario laboral HOY y
        envía notificación al correo de la empresa (RRHH).
        Corre: diariamente a las 7am.
        """
        today = date.today()
        template = self.env.ref(
            'planilla_cr.email_template_anniversary', raise_if_not_found=False
        )
        if not template:
            _logger.warning('Planilla CR: plantilla email_template_anniversary no encontrada.')
            return

        employees = self.env['hr.employee'].search([
            ('active', '=', True),
            ('entry_date', '!=', False),
        ])

        count = 0
        for emp in employees:
            # Cumple aniversario si hoy es el mismo mes/día que su fecha de ingreso
            if (emp.entry_date.month == today.month and
                    emp.entry_date.day == today.day and
                    emp.entry_date.year < today.year):
                years = today.year - emp.entry_date.year
                _logger.info(
                    'Planilla CR: aniversario %s — %s cumple %d año(s)',
                    emp.name, today, years
                )
                try:
                    template.send_mail(emp.id, force_send=True)
                    count += 1
                except Exception as e:
                    _logger.error('Planilla CR: error enviando email aniversario %s: %s', emp.name, e)

        _logger.info('Planilla CR: cron_check_anniversaries — %d notificaciones enviadas.', count)

    @api.model
    def cron_close_completed_loans(self):
        """
        Marca como pagados los préstamos donde todas las cuotas
        ya fueron descontadas. Corre: diariamente.
        """
        loans = self.env['planilla.employee.loan'].search([
            ('state', 'in', ('approved', 'active')),
        ])
        closed = 0
        for loan in loans:
            if loan.installment_ids and all(
                i.state in ('deducted', 'cancelled') for i in loan.installment_ids
            ):
                loan.action_check_paid()
                closed += 1
                _logger.info('Planilla CR: préstamo cerrado automáticamente — %s / %s',
                             loan.employee_id.name, loan.name)


        _logger.info('Planilla CR: cron_close_completed_loans — %d préstamos cerrados.', closed)

    @api.model
    def cron_alert_embargo_expiry(self):
        """
        Alerta cuando un embargo judicial esta proximo a vencer (30 dias antes de date_end).
        El patrono debe notificar al juzgado y al empleado antes del vencimiento.
        Art. 172 CT: responsabilidad solidaria del patrono.
        Corre: diariamente.
        """
        from datetime import date as _date
        from dateutil.relativedelta import relativedelta
        today = _date.today()
        threshold = today + relativedelta(days=30)

        embargos = self.env['planilla.embargo'].search([
            ('state', '=', 'active'),
            ('date_end', '!=', False),
            ('date_end', '>=', today),
            ('date_end', '<=', threshold),
        ])
        if not embargos:
            return

        # Agrupar por empresa para enviar un email por empresa
        by_company = {}
        for emb in embargos:
            comp = emb.company_id or self.env.company
            by_company.setdefault(comp, []).append(emb)

        for company, embs in by_company.items():
            if not company.email:
                continue
            lines = '\n'.join(
                f'  • {e.employee_id.name}: expediente {e.numero_expediente} '
                f'— vence {e.date_end} ({e.juzgado})'
                for e in embs
            )
            body = (
                f'<p><strong>Alerta de Embargos Judiciales por Vencer — {company.name}</strong></p>'
                f'<p>Los siguientes <strong>{len(embs)}</strong> embargo(s) vencen '
                f'en los proximos 30 dias (Art. 172 CT):</p>'
                f'<pre style="background:#FFF3CD;padding:10px;">{lines}</pre>'
                f'<p>Contacte al juzgado correspondiente para renovar o finalizar el embargo.</p>'
            )
            try:
                self.env['mail.mail'].create({
                    'subject': f'⚖️ Embargos por vencer — {company.name}',
                    'email_to': company.email,
                    'body_html': body,
                    'auto_delete': True,
                }).send()
                _logger.info('Planilla CR: alerta embargo %d registros enviada a %s',
                             len(embs), company.name)
            except Exception as e:

                _logger.error('Planilla CR: error alerta embargo %s: %s', company.name, e)

    @api.model
    def cron_bono_antiguedad(self):
        """
        Crea automaticamente el bono de antiguedad para empleados que cumplen
        su aniversario laboral HOY, segun la tabla configurada en
        planilla.bono.antiguedad.config por empresa.
        
        En CR es comun que convenios colectivos o politicas de empresa
        reconozcan la antiguedad con un bono anual (porcentaje del salario
        o monto fijo segun tramo de anos).
        
        Corre: diariamente (misma frecuencia que cron_check_anniversaries).
        """
        today = date.today()
        BonoConfig = self.env['planilla.bono.antiguedad.config']
        BonoModel  = self.env['planilla.bono']

        employees = self.env['hr.employee'].search([
            ('active', '=', True),
            ('entry_date', '!=', False),
        ])

        created = 0
        for emp in employees:
            # Solo los que cumplen aniversario HOY y llevan >= 1 año
            if not (emp.entry_date.month == today.month and
                    emp.entry_date.day == today.day and
                    emp.entry_date.year < today.year):
                continue

            years = today.year - emp.entry_date.year
            company_id = emp.company_id.id if emp.company_id else self.env.company.id

            # Buscar configuracion de antigüedad aplicable
            cfg = BonoConfig.get_config_for_years(company_id, years)
            if not cfg:
                continue  # No hay configuracion para este tramo — no crear bono

            base_salary = emp.base_salary or 0.0
            if not base_salary:
                _logger.warning('Planilla CR: bono antiguedad — %s no tiene salario base', emp.name)
                continue

            monto = cfg.compute_bono_amount(base_salary, years)
            if monto <= 0:
                continue

            # Verificar si ya existe un bono de antigüedad para este aniversario
            # (evitar duplicados si el cron corre dos veces)
            bono_name = f'Bono Antiguedad — {years} año(s)'
            year_start = today.replace(month=1, day=1)
            existing = BonoModel.search([
                ('employee_id', '=', emp.id),
                ('bono_type', '=', 'antiguedad'),
                ('name', '=', bono_name),
                ('date_start', '>=', year_start),
            ], limit=1)
            if existing:
                continue

            try:
                BonoModel.create({
                    'employee_id':   emp.id,
                    'name':          bono_name,
                    'bono_type':     'antiguedad',
                    'amount_type':   cfg.amount_type,
                    'amount':        monto if cfg.amount_type == 'fixed' else 0.0,
                    'percentage':    cfg.percentage if cfg.amount_type == 'percentage' else 0.0,
                    'is_recurring':  False,  # Es puntual: solo en el mes del aniversario
                    'afecto_ccss':   True,   # Antigüedad es salarial (Art. 162 CT)
                    'afecto_renta':  True,
                    'date_start':    today,
                    'date_end':      today.replace(day=28) if today.month == 2 else
                                     today.replace(day=30) if today.month in (4,6,9,11) else
                                     today.replace(day=31),
                    'state':         'active',
                    'note':          f'Creado automaticamente por el sistema al cumplir {years} año(s) de servicio.',
                })
                created += 1
                _logger.info('Planilla CR: bono antiguedad creado para %s — %d año(s), ₡%s',
                             emp.name, years, f'{monto:,.2f}')
            except Exception as e:
                _logger.error('Planilla CR: error creando bono antiguedad para %s: %s', emp.name, e)

        _logger.info('Planilla CR: cron_bono_antiguedad — %d bonos creados.', created)

    @api.model
    def cron_alert_negative_vacations(self):
        """
        Envía un resumen semanal a cada empresa sobre empleados
        con saldo de vacaciones negativo. Corre: lunes a las 8am.
        """
        companies = self.env['res.company'].search([])
        for company in companies:
            employees_neg = self.env['hr.employee'].search([
                ('company_id', '=', company.id),
                ('active', '=', True),
                ('vacation_balance_alert', '=', True),
            ])
            if not employees_neg:
                continue

            if not company.email:
                _logger.warning(
                    'Planilla CR: empresa %s no tiene email configurado para alertas.', company.name
                )
                continue

            # Construir resumen en texto
            lines = '\n'.join(
                f"  • {e.name}: {e.vacation_days_available:.1f} días "
                f"(acumulados: {e.vacation_days_accrued:.1f}, tomados: {e.vacation_days_taken:.1f})"
                for e in employees_neg
            )
            body = (
                f"<p>Estimado equipo de RRHH de <strong>{company.name}</strong>,</p>"
                f"<p>Los siguientes <strong>{len(employees_neg)}</strong> empleado(s) tienen "
                f"saldo de vacaciones <strong style='color:#E74C3C;'>negativo</strong>:</p>"
                f"<pre style='background:#FDE8E8;padding:10px;border-radius:4px;'>{lines}</pre>"
                f"<p>Por favor revise y tome las acciones necesarias según el Art. 153 CT CR.</p>"
            )
            try:
                self.env['mail.mail'].create({
                    'subject': f'⚠️ Alerta: {len(employees_neg)} empleado(s) con vacaciones negativas — {company.name}',
                    'email_to': company.email,
                    'body_html': body,
                    'auto_delete': True,
                }).send()
                _logger.info(
                    'Planilla CR: alerta vacaciones negativas enviada a %s (%d empleados)',
                    company.name, len(employees_neg)
                )
            except Exception as e:
                _logger.error('Planilla CR: error enviando alerta vacaciones %s: %s', company.name, e)

    @api.model
    def cron_alert_prescribing_vacations(self):
        """
        Alerta sobre empleados con vacaciones próximas a prescribir.
        Art. 156 CT CR: vacaciones prescriben a los 2 años de ganadas.
        Corre: primer día de cada mes.
        """
        today = date.today()
        threshold = today + relativedelta(months=2)  # Alerta con 2 meses de anticipación

        companies = self.env['res.company'].search([])
        for company in companies:
            employees = self.env['hr.employee'].search([
                ('company_id', '=', company.id),
                ('active', '=', True),
                ('entry_date', '!=', False),
            ])
            # L2 FIX: batch query — traer última vacación de todos los empleados en 1 query
            emp_ids = employees.filtered(lambda e: e.vacation_days_available > 0).ids
            last_vacs = {}
            if emp_ids:
                vac_groups = self.env['planilla.vacation.payment'].read_group(
                    domain=[
                        ('employee_id', 'in', emp_ids),
                        ('state', 'in', ('approved', 'paid')),
                        ('vacation_type', '=', 'disfrutadas'),
                    ],
                    # FIX M-05 v51: el campo correcto es date_start, no date_from
                    # (date_from no existe en planilla.vacation.payment → error silencioso)
                    fields=['employee_id', 'date_start:max'],
                    groupby=['employee_id'],
                )
                last_vacs = {
                    g['employee_id'][0]: g['date_start']
                    for g in vac_groups if g.get('date_start')
                }
            at_risk = []
            for emp in employees:
                if emp.vacation_days_available > 0:
                    ref_date = last_vacs.get(emp.id) or emp.entry_date
                    if ref_date:
                        months_since = (today - ref_date).days / 30
                        if months_since >= 22:
                            at_risk.append((emp.name, emp.vacation_days_available, round(months_since, 0)))

            if not at_risk or not company.email:
                continue

            lines = '\n'.join(
                f"  • {name}: {days:.1f} días disponibles (sin tomar hace ~{months:.0f} meses)"
                for name, days, months in at_risk
            )
            body = (
                f"<p>Alerta de prescripción de vacaciones — <strong>{company.name}</strong></p>"
                f"<p><strong>{len(at_risk)}</strong> empleado(s) tienen vacaciones en riesgo de prescribir "
                f"(Art. 156 CT CR — prescriben a los 2 años):</p>"
                f"<pre style='background:#FFF3CD;padding:10px;border-radius:4px;'>{lines}</pre>"
                f"<p>Se recomienda programar sus vacaciones antes de que prescriban.</p>"
            )
            try:
                self.env['mail.mail'].create({
                    'subject': f'📅 Vacaciones próximas a prescribir — {company.name}',
                    'email_to': company.email,
                    'body_html': body,
                    'auto_delete': True,
                }).send()
            except Exception as e:
                _logger.error('Planilla CR: error alerta prescripción %s: %s', company.name, e)
