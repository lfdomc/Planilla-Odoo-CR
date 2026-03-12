import io
import base64
import csv
from datetime import datetime
from odoo import models, fields, api
from odoo.exceptions import UserError


class BankPaymentWizard(models.TransientModel):
    _name = 'planilla.bank.payment'
    _description = 'Exportar Planilla para Banco'

    company_id = fields.Many2one('res.company', required=True,
                                  default=lambda self: self.env.company)
    date_from = fields.Date(string='Desde', required=True)
    date_to = fields.Date(string='Hasta', required=True)
    branch_id = fields.Many2one('planilla.branch', string='Sucursal')
    bank_format = fields.Selection([
        ('bcr_dav', 'BCR - Archivo DAV (CSV)'),
        ('bncr_sin', 'BNCR - Archivo SINPE (.SIN)'),
        ('sinpe_movil', 'SINPE Móvil — Todos los bancos (CSV)'),
    ], string='Formato Bancario', required=True, default='bcr_dav')

    # Campos SINPE Móvil
    sinpe_concept = fields.Char(
        string='Concepto SINPE Móvil',
        default='Pago de Planilla',
        help='Descripción que verá el empleado al recibir el pago (max 60 caracteres).'
    )

    # Campos especificos BNCR SIN
    bncr_client_id = fields.Char(
        string='ID Cliente BNCR',
        help='Identificacion unica del cliente segun canal BNCR (15 caracteres)'
    )
    bncr_client_id_type = fields.Selection([
        ('1', 'Cedula Fisica'),
        ('2', 'Cedula Juridica'),
        ('3', 'ID Extranjero'),
    ], string='Tipo ID Empresa', default='2')
    bncr_company_id_number = fields.Char(
        string='Cedula Juridica Empresa',
        help='Cedula juridica de la empresa (sin guiones)'
    )
    bncr_transfer_number = fields.Char(
        string='Numero de Transferencia',
        help='Consecutivo interno de 5 digitos para este archivo'
    )
    bncr_debit_iban = fields.Char(
        string='IBAN Cuenta Debito Empresa',
        help='Cuenta IBAN de la empresa desde la que se debita la planilla'
    )
    bncr_currency = fields.Selection([
        ('01', 'Colones (CRC)'),
        ('02', 'Dolares (USD)'),
    ], string='Moneda', default='01')
    bncr_concept = fields.Char(
        string='Concepto de Pago',
        default='Pago de Planilla',
        help='Concepto que apareceran en los estados de cuenta (max 45 caracteres)'
    )

    # Campo BCR DAV
    bcr_concept = fields.Char(
        string='Concepto de Pago BCR',
        help='Concepto que aparece en el estado de cuenta (max 45 caracteres)'
    )

    def _get_payslips(self):
        domain = [
            ('date_from', '<=', self.date_to),
            ('date_to', '>=', self.date_from),
            ('state', '=', 'done'),
            ('company_id', '=', self.company_id.id),
        ]
        if self.branch_id:
            domain.append(('branch_id', '=', self.branch_id.id))
        return self.env['planilla.payslip.cr'].search(domain)

    # ─────────────────────────────────────────────────────────────
    #  BCR  DAV  (CSV)
    # ─────────────────────────────────────────────────────────────
    def action_export_bcr_dav(self):
        self.ensure_one()
        payslips = self._get_payslips()
        if not payslips:
            raise UserError('No hay boletas aprobadas en el periodo seleccionado.')

        output = io.StringIO()
        writer = csv.writer(output, lineterminator='\r\n')

        date_str = self.date_to.strftime('%d-%m-%y') if self.date_to else ''
        concept = (self.bcr_concept or f'Pago Planilla {date_str}')[:45]

        errors = []
        for payslip in payslips:
            emp = payslip.employee_id
            iban = (emp.bank_iban or '').strip().replace(' ', '').replace('-', '')
            if not iban:
                errors.append(f'{emp.name}: sin IBAN registrado')
                continue
            if not iban.startswith('CR') or len(iban) != 22:
                errors.append(f'{emp.name}: IBAN invalido ({iban})')
                continue

            net = round(payslip.salary_payable, 2)  # B1 FIX: salary_payable (neto real despues de todas las deducciones)
            nombre = emp.name or ''
            writer.writerow([iban, nombre, f'{net:.2f}', concept, concept, concept])

        if errors and not output.getvalue():
            raise UserError(
                'No se generaron registros. Errores encontrados:\n' + '\n'.join(errors)
            )

        csv_bytes = output.getvalue().encode('utf-8')
        if errors:
            # Agregar comentario al inicio con advertencias
            warn = '# ADVERTENCIA: Empleados omitidos por falta de IBAN:\n'
            warn += '\n'.join(f'# - {e}' for e in errors) + '\n'
            csv_bytes = warn.encode('utf-8') + csv_bytes

        filename = f'Planilla_BCR_DAV_{self.date_from}_{self.date_to}.csv'
        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': base64.b64encode(csv_bytes),
            'mimetype': 'text/csv',
        })
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }

    # ─────────────────────────────────────────────────────────────
    #  BNCR  SINPE  (.SIN)  -  Formato posicional fijo
    # ─────────────────────────────────────────────────────────────
    def _pad(self, value, length, align='left', fill=' '):
        value = str(value) if value else ''
        if align == 'left':
            return value[:length].ljust(length, fill)
        else:
            return value[:length].rjust(length, fill)

    def _format_monto(self, amount, length=15):
        # Monto en centimos sin punto decimal, relleno con ceros a la izquierda
        centimos = int(round(amount * 100))
        return str(centimos).rjust(length, '0')

    def _format_id_bncr(self, id_number, id_type):
        # Fisica: 6 ceros + 9 digitos = 15 chars
        # Juridica/Extranjero: 3 ceros + 12 digitos = 15 chars
        id_clean = ''.join(filter(str.isdigit, id_number or ''))
        if id_type == '1':  # Fisica
            return id_clean.rjust(15, '0')[:15]
        else:  # Juridica o Extranjero
            return id_clean.rjust(15, '0')[:15]

    def action_export_bncr_sin(self):
        self.ensure_one()

        if not self.bncr_client_id:
            raise UserError('Debe ingresar el ID Cliente BNCR.')
        if not self.bncr_debit_iban:
            raise UserError('Debe ingresar el IBAN de la cuenta debito de la empresa.')
        if not self.bncr_transfer_number:
            raise UserError('Debe ingresar el Numero de Transferencia.')

        payslips = self._get_payslips()
        if not payslips:
            raise UserError('No hay boletas aprobadas en el periodo seleccionado.')

        lines = []
        errors = []
        total_monto = 0.0
        sum_correlativos = 0
        creditos = []

        line_num = 1
        for payslip in payslips:
            emp = payslip.employee_id
            iban = (emp.bank_iban or '').strip().replace(' ', '').replace('-', '')
            if not iban:
                errors.append(f'{emp.name}: sin IBAN')
                continue
            if len(iban) != 22:
                errors.append(f'{emp.name}: IBAN invalido ({iban})')
                continue

            net = payslip.salary_payable  # B1 FIX: salary_payable no net_salary
            total_monto += net

            # Correlativo = posicion 23-28 del IBAN (indices 22-28 en 0-based del string IBAN completo)
            # El IBAN tiene formato: CR + 2 digitos control + 4 banco + 16 cuenta
            # Segun BNCR: correlativo inicia en pos 23 y finaliza en 28 del registro
            # En el registro tipo 3, el IBAN va en la pos 04 (cols 9-30 aprox)
            # El correlativo que se suma es los digitos de posicion 23-28 dentro del registro
            # Por simplicidad usamos los ultimos 6 digitos del IBAN numerico
            iban_digits = ''.join(filter(str.isdigit, iban))
            correlativo = int(iban_digits[-6:]) if len(iban_digits) >= 6 else line_num
            sum_correlativos += correlativo

            # Tipo ID del beneficiario
            ins_id_type = getattr(emp, 'ins_id_type', '01') or '01'
            ben_id_type = '1' if ins_id_type == '01' else ('2' if ins_id_type == '02' else '3')
            id_num = emp.identification_id or ''

            concept = (self.bncr_concept or 'Pago de Planilla')[:45]
            comprobante = str(line_num).rjust(8, '0')

            creditos.append({
                'line_num': line_num,
                'iban': iban,
                'comprobante': comprobante,
                'monto': net,
                'concept': concept,
                'ben_id_type': ben_id_type,
                'id_num': id_num,
                'nombre': emp.name or '',
            })
            line_num += 1

        if not creditos:
            raise UserError(
                'No se generaron registros. Errores:\n' + '\n'.join(errors)
            )

        fecha = self.date_to.strftime('%d/%m/%Y') if self.date_to else datetime.today().strftime('%d/%m/%Y')
        moneda = self.bncr_currency or '01'
        client_id_padded = self._pad(self.bncr_client_id, 15)
        transfer_num = self._pad(self.bncr_transfer_number, 5, 'right', '0')
        company_id_fmt = self._format_id_bncr(self.bncr_company_id_number, self.bncr_client_id_type)
        debit_iban = self._pad(self.bncr_debit_iban.replace(' ', '').replace('-', ''), 22)
        monto_total_fmt = self._format_monto(total_monto, 16)
        sum_corr_fmt = str(sum_correlativos).rjust(10, '0')

        # ── Registro tipo 1: Encabezado ──
        r1 = (
            '1' +                           # pos 01: tipo registro
            client_id_padded +              # pos 02: ID cliente (15)
            (self.bncr_client_id_type or '2') +  # pos 03: tipo ID (1)
            company_id_fmt +               # pos 04: ID empresa (15)
            fecha +                         # pos 05: fecha dd/mm/yyyy (8) -> 10 con separadores
            transfer_num +                  # pos 06: num transferencia (5)
            '1' +                           # pos 07: tipo transaccion
            '0000' +                        # pos 08: codigo respuesta BNCR (4)
            '0000' +                        # pos 09: codigo error SINPE (4)
            monto_total_fmt +              # pos 10: monto total SFB (16)
            '0000000000000000' +           # pos 11: monto SINPE (16)
            '0000000' +                    # pos 12: tipo cambio compra (7)
            '0000000' +                    # pos 13: tipo cambio venta (7)
            monto_total_fmt +              # pos 14: sumatoria montos (16)
            sum_corr_fmt                   # pos 15: sumatoria correlativos (10)
        )
        lines.append(r1)

        # ── Registro tipo 2: Debito empresa ──
        debit_comprobante = '00000001'
        r2 = (
            '2' +                           # pos 01: tipo registro
            '00001' +                       # pos 02: numero linea (5)
            '1' +                           # pos 03: tipo procesamiento
            debit_iban +                    # pos 04: IBAN debito (22)
            debit_comprobante +            # pos 05: comprobante (8)
            self._format_monto(total_monto, 15) +  # pos 06: monto debito (15)
            moneda +                        # pos 07: moneda (2)
            self._pad(self.bncr_concept or 'Pago de Planilla', 45) +  # pos 08: concepto (45)
            '00'                            # pos 09: estado (2)
        )
        lines.append(r2)

        # ── Registros tipo 3: Creditos por empleado ──
        for i, cred in enumerate(creditos):
            # Tipo procesamiento: 1=BNCR, 2=otros bancos T+1
            # Detectamos si es BNCR por los primeros digitos del IBAN
            iban_banco = cred['iban'][5:9]  # posicion banco en IBAN CR
            tipo_proc = '1' if iban_banco == '0152' else '2'  # 0152 = BNCR

            # ID beneficiario segun tipo
            id_fisica = cred['id_num'].replace('-', '').replace(' ', '')
            if cred['ben_id_type'] == '1':
                ben_id = id_fisica.ljust(15)[:15]
            else:
                ben_id = id_fisica.rjust(12, '0').ljust(15)[:15]

            r3 = (
                '3' +                                           # pos 01
                str(i + 2).rjust(5, '0') +                    # pos 02: num linea credito (inicia en 2)
                tipo_proc +                                     # pos 03: tipo procesamiento
                self._pad(cred['iban'], 22) +                  # pos 04: IBAN (22)
                cred['comprobante'] +                          # pos 05: comprobante (8)
                self._format_monto(cred['monto'], 15) +       # pos 06: monto (15)
                moneda +                                        # pos 07: moneda (2)
                self._pad(cred['concept'], 45) +              # pos 08: concepto (45)
                '00' +                                         # pos 09: estado (2)
                cred['ben_id_type'] +                         # pos 10: tipo ID beneficiario (1)
                ben_id +                                       # pos 11: ID beneficiario (15)
                self._pad(cred['nombre'], 20)                 # pos 12: detalle especial (20)
            )
            lines.append(r3)

        content = '\r\n'.join(lines) + '\r\n'
        if errors:
            content = '** ADVERTENCIA: Omitidos por falta de IBAN:\r\n'
            content += '\r\n'.join(f'** {e}' for e in errors) + '\r\n'
            content += '\r\n'.join(lines) + '\r\n'

        filename = f'Planilla_BNCR_{self.date_from}_{self.date_to}.SIN'
        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': base64.b64encode(content.encode('latin-1', errors='replace')),
            'mimetype': 'application/octet-stream',
        })
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }

    # ─────────────────────────────────────────────────────────────
    #  SINPE  MÓVIL  —  CSV  universal  (todos los bancos)
    # ─────────────────────────────────────────────────────────────
    def action_export_sinpe_movil(self):
        """Genera CSV para pago masivo vía SINPE Móvil.
        Formato: teléfono, monto, concepto — compatible con portales
        de BAC, Scotiabank, Davivienda, BCR, BNCR y otros.
        """
        self.ensure_one()
        payslips = self._get_payslips()
        if not payslips:
            raise UserError('No hay boletas aprobadas en el periodo seleccionado.')

        output = io.StringIO()
        writer = csv.writer(output, lineterminator='\r\n')
        # Encabezado estándar compatible con plataformas de pago masivo CR
        writer.writerow(['telefono', 'monto', 'concepto', 'empleado', 'cedula'])

        concept = (self.sinpe_concept or 'Pago de Planilla')[:60]
        errors = []
        count = 0

        for payslip in payslips.sorted(key=lambda s: s.employee_id.name):
            emp = payslip.employee_id
            phone = (getattr(emp, 'sinpe_phone', None) or
                     getattr(emp, 'mobile_phone', None) or
                     getattr(emp, 'work_phone', None) or '').strip()
            # Limpiar teléfono — solo dígitos, 8 caracteres para CR
            phone_clean = ''.join(filter(str.isdigit, phone))
            if not phone_clean:
                errors.append(f'{emp.name}: sin teléfono SINPE registrado')
                continue
            if len(phone_clean) != 8:
                errors.append(f'{emp.name}: teléfono inválido ({phone_clean}) — debe tener 8 dígitos')
                continue

            net = round(payslip.salary_payable, 2)  # B1 FIX: salary_payable (neto real despues de todas las deducciones)
            cedula = emp.identification_id or ''
            writer.writerow([phone_clean, f'{net:.2f}', concept, emp.name, cedula])
            count += 1

        if count == 0:
            raise UserError(
                'No se generaron registros. Errores encontrados:\n' + '\n'.join(errors)
            )

        csv_content = output.getvalue()
        if errors:
            warn = '# ADVERTENCIA — Empleados omitidos por falta de teléfono SINPE:\n'
            warn += '\n'.join(f'# - {e}' for e in errors) + '\n'
            csv_content = warn + csv_content

        filename = f'Planilla_SINPE_Movil_{self.date_from}_{self.date_to}.csv'
        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': base64.b64encode(csv_content.encode('utf-8')),
            'mimetype': 'text/csv',
        })
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }

    def action_export(self):
        self.ensure_one()
        if self.bank_format == 'bcr_dav':
            return self.action_export_bcr_dav()
        elif self.bank_format == 'bncr_sin':
            return self.action_export_bncr_sin()
        elif self.bank_format == 'sinpe_movil':
            return self.action_export_sinpe_movil()
        raise UserError('Formato no implementado.')
