from odoo import models, fields, api


class SendPayslipWizard(models.TransientModel):
    _name = 'planilla.send.payslip.wizard'
    _description = 'Enviar Boletas de Pago'

    payslip_ids = fields.Many2many(
        'planilla.payslip.cr', string='Boletas a Enviar'
    )
    send_all = fields.Boolean(string='Enviar Todas', default=False)
    email_subject = fields.Char(
        string='Asunto',
        default='Boleta de Pago - {period}'
    )
    email_body = fields.Html(
        string='Mensaje',
        default="""
<p>Estimado/a colaborador/a,</p>
<p>Adjunto encontrará su boleta de pago correspondiente al período indicado.</p>
<p>Si tiene alguna consulta, no dude en contactar al departamento de Recursos Humanos.</p>
<br/>
<p>Atentamente,<br/>Departamento de Recursos Humanos</p>
        """
    )

    def action_send(self):
        """Envía las boletas por correo a cada empleado."""
        import base64
        sent_count = 0
        errors = []

        report = self.env.ref('planilla_cr.action_report_payslip_cr')

        for payslip in self.payslip_ids:
            employee = payslip.employee_id
            email = employee.work_email or employee.private_email

            if not email:
                errors.append(f'{employee.name}: sin correo registrado')
                continue

            try:
                # FIX-I9: usar mismo patrón que audit_zip_wizard — pasar el record,
                # no el xml_id del template. La firma correcta en Odoo 19 es:
                # env['ir.actions.report']._render_qweb_pdf(report_record, res_ids)
                pdf_content, _ = self.env['ir.actions.report']._render_qweb_pdf(
                    report, [payslip.id]
                )
                pdf_b64 = base64.b64encode(pdf_content).decode('utf-8')
            except Exception as e:
                errors.append(f'{employee.name}: error generando PDF - {str(e)}')
                continue

            period = f'{payslip.date_from} al {payslip.date_to}'
            subject = self.email_subject.replace('{period}', period)

            mail_values = {
                'subject': subject,
                'body_html': self.email_body,
                'email_to': email,
                'email_from': self.env.user.email or self.env.company.email,
                'attachment_ids': [(0, 0, {
                    'name': f'Boleta_{employee.name}_{payslip.date_to}.pdf',
                    'datas': pdf_b64,
                    'mimetype': 'application/pdf',
                })],
            }
            mail = self.env['mail.mail'].create(mail_values)
            mail.send()
            sent_count += 1

            payslip.message_post(
                body=f'Boleta enviada por correo a {email}',
                message_type='notification'
            )

        message = f'Se enviaron {sent_count} boleta(s) correctamente.'
        if errors:
            message += f'\nErrores: {", ".join(errors)}'

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Envío de Boletas',
                'message': message,
                'type': 'success' if not errors else 'warning',
                'sticky': True,
            }
        }
