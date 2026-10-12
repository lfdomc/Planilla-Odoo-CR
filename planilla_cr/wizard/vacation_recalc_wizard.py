from odoo import models, fields, api
from odoo.exceptions import UserError
from datetime import date as _date


class VacationRecalcWizard(models.TransientModel):
    """Wizard para recalcular y corregir saldos iniciales de vacaciones."""
    _name = 'planilla.vacation.recalc.wizard'
    _description = 'Recalcular Saldos de Vacaciones'

    cutoff_date = fields.Date(
        string='Fecha de Corte Global',
        required=True,
        default=lambda self: _date.today(),
        help='Fecha de corte usada para calcular los saldos iniciales. '
             'Para empleados nuevos (ingresaron despues de esta fecha) se omiten.'
    )
    base_days_anniversary = fields.Float(
        string='Dias por Anio Laborado',
        required=True,
        default=2.0,
        help='Dias adicionales de vacaciones por cada anio completo de servicio.'
    )
    only_zero_balance = fields.Boolean(
        string='Solo empleados con saldo inicial en 0',
        default=False,
        help='Si activo, solo actualiza empleados cuyo saldo inicial es 0. '
             'Si desactivo, recalcula todos aunque ya tengan saldo.'
    )
    preview_line_ids = fields.One2many(
        'planilla.vacation.recalc.line', 'wizard_id',
        string='Vista Previa'
    )
    computed = fields.Boolean(default=False)

    def action_preview(self):
        """Calcula y muestra los saldos a actualizar sin guardar.

        LOGICA CORRECTA:
        El saldo_inicial que tiene cada empleado al corte ya refleja
        toda la historia previa (acumulado + vacaciones tomadas + ajustes).
        NO se recalcula ese saldo -- se respeta como la 'verdad' al corte.

        Lo unico que se agrega son los dias de aniversario que cayeron
        DESPUES de la fecha de corte y que el cron aun no aplico.
        """
        self.ensure_one()
        self.preview_line_ids.unlink()
        lines = []

        today = _date.today()
        employees = self.env['hr.employee'].search([('active', '=', True)])

        for emp in employees:
            if not emp.entry_date:
                continue

            cutoff = emp.vacation_initial_balance_date or self.cutoff_date
            saldo_actual = emp.vacation_initial_balance or 0.0

            # Calcular SOLO los aniversarios que caen DESPUES del corte
            # y ANTES o IGUAL a hoy (que el cron no pudo aplicar aun)
            annis_post_corte = []
            year = emp.entry_date.year + 1
            while True:
                try:
                    ann = emp.entry_date.replace(year=year)
                except ValueError:
                    ann = _date(year, 3, 1)
                if ann > today:
                    break
                # Solo los que caen despues del corte y hasta hoy
                # Y que NO hayan sido ya aplicados en este ano
                if ann > cutoff:
                    last_applied = emp.vacation_last_anniversary_year or 0
                    if last_applied < ann.year:  # No aplicado aun
                        anos = year - emp.entry_date.year
                        dias_extra = self.base_days_anniversary * anos
                        annis_post_corte.append((ann, anos, dias_extra))
                year += 1

            dias_aniversario = sum(a[2] for a in annis_post_corte)
            saldo_correcto = round(saldo_actual + dias_aniversario, 2)
            diferencia = round(dias_aniversario, 2)

            # Solo mostrar si hay cambio real
            if diferencia == 0 and self.only_zero_balance:
                continue
            if diferencia == 0:
                continue  # Nada que agregar

            anni_desc = ', '.join([
                f"{a[0].strftime('%d/%m/%y')}(+{a[2]:.0f}d)"
                for a in annis_post_corte
            ]) if annis_post_corte else 'ninguno'

            lines.append((0, 0, {
                'employee_id':       emp.id,
                'entry_date':        emp.entry_date,
                'cutoff_date':       cutoff,
                'saldo_actual':      saldo_actual,
                'acum_normal':       0.0,
                'aniversarios_desc': anni_desc,
                'saldo_correcto':    saldo_correcto,
                'diferencia':        diferencia,
                'apply':             True,
            }))

        self.write({
            'preview_line_ids': lines,
            'computed': True,
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'planilla.vacation.recalc.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_apply(self):
        """Aplica los saldos calculados a los empleados marcados."""
        self.ensure_one()
        applied = 0
        for line in self.preview_line_ids.filtered(lambda l: l.apply):
            emp = line.employee_id
            from datetime import date as _d
            emp.write({
                'vacation_initial_balance':       line.saldo_correcto,
                'vacation_initial_balance_date':  line.cutoff_date,
                'vacation_last_anniversary_year': _d.today().year,
            })
            emp.message_post(
                body=(
                    f'<b>Saldo inicial de vacaciones corregido:</b> '
                    f'{line.saldo_actual:.2f} dias -> {line.saldo_correcto:.2f} dias '
                    f'(acumulacion normal: {line.acum_normal:.2f}d, '
                    f'aniversarios: {line.aniversarios_desc})'
                ),
                message_type='notification',
            )
            applied += 1

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Saldos Actualizados',
                'message': f'{applied} empleado(s) actualizados correctamente.',
                'type': 'success',
                'sticky': True,
            }
        }

    def _calc_saldo(self, entry_date, cutoff_date, base_days):
        """Calcula saldo correcto al cutoff_date."""
        dias = (cutoff_date - entry_date).days
        semanas = dias / 7.0
        acum_normal = int((semanas * 7) // 29)  # días completos: floor(días/29)

        aniversarios = []
        year = entry_date.year + 1
        while True:
            try:
                ann = entry_date.replace(year=year)
            except ValueError:
                ann = _date(year, 3, 1)
            if ann > cutoff_date:
                break
            anos_completos = year - entry_date.year
            dias_extra = base_days * anos_completos
            aniversarios.append((ann, anos_completos, dias_extra))
            year += 1

        dias_aniversario = sum(a[2] for a in aniversarios)
        total = round(acum_normal + dias_aniversario, 2)
        return total, acum_normal, aniversarios


class VacationRecalcLine(models.TransientModel):
    _name = 'planilla.vacation.recalc.line'
    _description = 'Linea de Recalculo de Vacaciones'

    wizard_id        = fields.Many2one('planilla.vacation.recalc.wizard', ondelete='cascade')
    employee_id      = fields.Many2one('hr.employee', string='Empleado', readonly=True)
    entry_date       = fields.Date(string='Fecha Ingreso', readonly=True)
    cutoff_date      = fields.Date(string='Fecha Corte', readonly=True)
    saldo_actual     = fields.Float(string='Saldo Actual', readonly=True)
    acum_normal      = fields.Float(string='Acum. Normal (dias)', readonly=True)
    aniversarios_desc = fields.Char(string='Aniversarios', readonly=True)
    saldo_correcto   = fields.Float(string='Saldo Correcto', readonly=True)
    diferencia       = fields.Float(string='Diferencia', readonly=True)
    apply            = fields.Boolean(string='Aplicar', default=True)
