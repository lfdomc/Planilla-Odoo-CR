from odoo import models, fields, api


class SyncHrWizard(models.TransientModel):
    _name = 'planilla.sync.hr.wizard'
    _description = 'Sincronizacion Masiva Planilla CR a HR Nativo'

    company_id = fields.Many2one(
        'res.company', string='Empresa',
        required=True, default=lambda self: self.env.company
    )
    branch_id = fields.Many2one(
        'planilla.branch', string='Sucursal (opcional)'
    )
    result_message = fields.Text(string='Resultado', readonly=True)
    computed = fields.Boolean(default=False)

    def action_sync(self):
        self.ensure_one()
        domain = [
            ('company_id', '=', self.company_id.id),
            ('active', '=', True),
        ]
        if self.branch_id:
            domain.append(('branch_id', '=', self.branch_id.id))

        emps = self.env['hr.employee'].search(domain)
        updated = 0
        skipped = 0
        errors = []
        emp_model_fields = self.env['hr.employee']._fields

        for emp in emps:
            try:
                changed = {}

                # wage (Salario en pestana Nomina) <- base_salary de Planilla CR
                if 'wage' in emp_model_fields and emp.base_salary:
                    if emp.wage != emp.base_salary:
                        changed['wage'] = emp.base_salary

                # contract_date_start (Fecha inicio contrato) <- entry_date
                if 'contract_date_start' in emp_model_fields and emp.entry_date:
                    if emp.contract_date_start != emp.entry_date:
                        changed['contract_date_start'] = emp.entry_date

                # resource_calendar_id (Horas laborables) <- asignar si vacio
                if 'resource_calendar_id' in emp_model_fields and not emp.resource_calendar_id:
                    cal = self.env['resource.calendar'].search([
                        ('company_id', '=', emp.company_id.id),
                    ], limit=1)
                    if cal:
                        changed['resource_calendar_id'] = cal.id

                # barcode (Numero de empleado) <- cedula sin guiones
                if 'barcode' in emp_model_fields and not emp.barcode and emp.identification_id:
                    clean = ''.join(c for c in emp.identification_id if c.isalnum())
                    if clean and len(clean) <= 18:
                        changed['barcode'] = clean

                if changed:
                    emp.with_context(skip_salary_history=True).write(changed)
                    updated += 1
                else:
                    skipped += 1

            except Exception as e:
                errors.append('%s: %s' % (emp.name, str(e)[:120]))

        lines = [
            'SINCRONIZACION COMPLETADA',
            '',
            'Empleados revisados:    %s' % len(emps),
            'Empleados actualizados: %s  (salario, fecha contrato, horario)' % updated,
            'Ya sincronizados:       %s' % skipped,
        ]
        if errors:
            lines += ['', 'Errores (%s):' % len(errors)] + ['  - ' + e for e in errors[:10]]

        self.write({
            'result_message': '\n'.join(lines),
            'computed': True,
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'planilla.sync.hr.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
