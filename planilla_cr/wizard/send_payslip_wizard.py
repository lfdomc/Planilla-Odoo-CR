from odoo import models, fields, api
from markupsafe import escape as _html_escape


class SendPayslipWizard(models.TransientModel):
    _name = 'planilla.send.payslip.wizard'
    _description = 'Enviar Boletas de Pago'

    payslip_ids = fields.Many2many(
        'planilla.payslip.cr', string='Boletas a Enviar'
    )
    send_all = fields.Boolean(string='Enviar Todas', default=False)
    email_subject = fields.Char(
        string='Asunto',
        default=lambda self: self._default_subject()
    )
    email_body = fields.Html(
        string='Mensaje',
        default=lambda self: self._default_body()
    )
    email_from = fields.Char(
        string='Remitente',
        default=lambda self: self._default_from()
    )

    def _get_config(self):
        return self.env['planilla.accounting.config'].get_config(
            self.env.company.id
        )

    def _default_subject(self):
        config = self._get_config()
        if config and config.email_payslip_subject:
            return config.email_payslip_subject
        return 'Boleta de Pago - {period}'

    def _default_body(self):
        config = self._get_config()
        if config and config.email_payslip_body:
            return config.email_payslip_body
        return (
            '<p>Estimado/a colaborador/a,</p>'
            '<p>Adjunto encontrara su boleta de pago del periodo <strong>{period}</strong>.</p>'
            '<p>Si tiene alguna consulta, contacte al departamento de Recursos Humanos.</p>'
            '<br/><p>Atentamente,<br/>{company}<br/>Recursos Humanos</p>'
        )

    mail_server_id = fields.Many2one(
        'ir.mail_server',
        string='Servidor de correo',
        default=lambda self: self._default_server()
    )

    def _default_server(self):
        config = self._get_config()
        if config and config.email_payslip_server_id:
            return config.email_payslip_server_id
        return False

    def _default_from(self):
        config = self._get_config()
        # Usar el remitente configurado explicitamente, o el smtp_user del servidor
        if config and config.email_payslip_from:
            return config.email_payslip_from
        if config and config.email_payslip_server_id:
            smtp_user = config.email_payslip_server_id.smtp_user
            if smtp_user:
                return smtp_user
        return self.env.user.email or self.env.company.email or ''

    def action_send(self):
        """Envia las boletas por correo: genera PDFs y encola en el servidor de correo.
        Los correos se procesan en background por el cron de Odoo (cada 1-5 min).
        El UI responde de inmediato con el resumen de encolados.
        """
        import base64
        queued_count = 0
        errors = []
        no_email = []

        report = self.env.ref('planilla_cr.action_report_payslip_cr')

        for payslip in self.payslip_ids:
            employee = payslip.employee_id
            email = employee.work_email or employee.private_email

            if not email:
                no_email.append(employee.name)
                continue

            try:
                pdf_content, _ = self.env['ir.actions.report']._render_qweb_pdf(
                    report, [payslip.id]
                )
                pdf_b64 = base64.b64encode(pdf_content).decode('utf-8')
            except Exception as e:
                errors.append(f'{employee.name}: error PDF - {str(e)[:60]}')
                continue

            period = (
                payslip.date_from.strftime('%d/%m/%Y')
                + ' al '
                + payslip.date_to.strftime('%d/%m/%Y')
            )
            company_name = payslip.company_id.name or self.env.company.name
            subject = self.email_subject.replace('{period}', period)

            mail_values = {
                'subject': subject,
                # SEC-A7 fix: escapar company_name -- viene de res.company.name
                # (editable en Ajustes por un admin), y se inyectaba tal cual
                # en el HTML del correo enviado a todos los empleados.
                'body_html': (str(self.email_body or ''))
                    .replace('{period}', _html_escape(period))
                    .replace('{company}', str(_html_escape(company_name))),
                'email_to': email,
                'email_from': self.email_from or self.env.user.email or self.env.company.email or '',
                # auto_delete=False: mantener registro del correo enviado
                'auto_delete': False,
                'attachment_ids': [(0, 0, {
                    'name': f'Boleta_{employee.name}_{payslip.date_to}.pdf',
                    'datas': pdf_b64,
                    'mimetype': 'application/pdf',
                })],
            }
            if self.mail_server_id:
                mail_values['mail_server_id'] = self.mail_server_id.id

            # Encolar sin enviar inmediatamente -- Odoo lo procesa en background
            # via el cron ir_cron_mail_scheduler. NO llamar mail.send() aqui:
            # eso fuerza el envio sincrono dentro del loop y puede causar
            # timeout del worker con boletas masivas (BUG-A4).
            self.env['mail.mail'].create(mail_values)
            queued_count += 1

            payslip.message_post(
                body=f'Boleta encolada para envio a {email}',
                message_type='notification'
            )

        # Construir resumen detallado
        lines = [f'<b>{queued_count} boleta(s) encoladas</b> para envio.']
        if no_email:
            lines.append(
                f'<br/><b>{len(no_email)} sin correo:</b> '
                + ', '.join(no_email)
            )
        if errors:
            lines.append(
                f'<br/><b>{len(errors)} error(es) de PDF:</b><br/>'
                + '<br/>'.join(errors)
            )
        total = len(self.payslip_ids)
        lines.append(
            f'<br/><small>Total procesadas: {queued_count + len(no_email) + len(errors)}'
            f' / {total}</small>'
        )

        notif_type = 'success'
        if errors:
            notif_type = 'danger'
        elif no_email:
            notif_type = 'warning'

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': f'Envio Boletas -- {queued_count}/{total} encoladas',
                'message': ''.join(lines),
                'type': notif_type,
                'sticky': True,
            }
        }
