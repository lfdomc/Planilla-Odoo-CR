"""
EDDI-7 — Exportación Declaración Mensual CCSS (Planilla Digital)
Formato oficial CCSS Costa Rica para declaración mensual de patronos.

Referencia: Manual Técnico EDDI-7, CCSS Costa Rica
Estructura: archivo .txt con filas de longitud fija, codificación UTF-8 o ISO-8859-1

Tipos de registro:
  Tipo 1 — Encabezado patrono (1 registro por archivo)
  Tipo 2 — Detalle por trabajador
  Tipo 9 — Totales / cierre
"""
import re
from datetime import date
from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError


class Eddi7Export(models.TransientModel):
    _name = 'planilla.eddi7.export'
    _description = 'Exportación EDDI-7 — Declaración Mensual CCSS'

    company_id = fields.Many2one(
        'res.company', string='Compañía',
        required=True, default=lambda self: self.env.company
    )
    year = fields.Integer(
        string='Año', required=True,
        default=lambda self: date.today().year
    )
    month = fields.Selection([
        ('01', 'Enero'), ('02', 'Febrero'), ('03', 'Marzo'),
        ('04', 'Abril'), ('05', 'Mayo'), ('06', 'Junio'),
        ('07', 'Julio'), ('08', 'Agosto'), ('09', 'Setiembre'),
        ('10', 'Octubre'), ('11', 'Noviembre'), ('12', 'Diciembre'),
    ], string='Mes', required=True,
        default=lambda self: f'{date.today().month:02d}'
    )
    patron_number = fields.Char(
        string='Número de Patrono CCSS',
        required=True,
        help='Número de patrono asignado por la CCSS (9 dígitos). '
             'Se encuentra en el carné patronal o en SICERE.'
    )
    branch_id = fields.Many2one(
        'planilla.branch', string='Sucursal',
        help='Filtrar por sucursal. Dejar vacío para todas.'
    )
    include_maternity = fields.Boolean(
        string='Incluir Subsidio Maternidad',
        default=True,
        help='Incluir registros de trabajadoras en licencia de maternidad'
    )

    # Resultado
    eddi7_file = fields.Binary(string='Archivo EDDI-7', readonly=True)
    eddi7_filename = fields.Char(string='Nombre Archivo', readonly=True)
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('generated', 'Generado'),
    ], default='draft')
    line_count = fields.Integer(string='Trabajadores incluidos', readonly=True)
    total_salaries = fields.Float(string='Total Salarios', readonly=True)
    validation_errors = fields.Text(string='Errores de Validación', readonly=True)

    # ── Helpers de formato ────────────────────────────────────────

    @staticmethod
    def _fmt_str(value, length, fill=' ', align='left'):
        """Formatea string a longitud exacta."""
        s = str(value or '').strip()
        if align == 'left':
            return s[:length].ljust(length, fill)
        return s[:length].rjust(length, fill)

    @staticmethod
    def _fmt_num(value, length, decimals=0):
        """Formatea número entero sin punto decimal (CCSS usa enteros en colones)."""
        try:
            v = int(round(float(value or 0)))
        except (ValueError, TypeError):
            v = 0
        s = str(abs(v))
        return s[:length].zfill(length)

    @staticmethod
    def _fmt_cedula(cedula):
        """Normaliza cédula a 9 dígitos (solo números)."""
        clean = re.sub(r'[^0-9]', '', str(cedula or ''))
        return clean.zfill(9)[:9]

    @staticmethod
    def _fmt_patron(patron):
        """Normaliza número de patrono a 9 dígitos."""
        clean = re.sub(r'[^0-9]', '', str(patron or ''))
        return clean.zfill(9)[:9]

    # ── Validaciones previas ──────────────────────────────────────

    def _validate_patron_number(self):
        """Valida que el número de patrono tenga formato correcto."""
        clean = re.sub(r'[^0-9]', '', self.patron_number or '')
        if len(clean) < 8:
            raise ValidationError(
                f'El número de patrono CCSS debe tener al menos 8 dígitos. '
                f'Ingresado: "{self.patron_number}".'
            )
        return clean.zfill(9)[:9]

    def _get_payslips_for_month(self):
        """Obtiene boletas pagadas del mes/año seleccionado."""
        from datetime import date as date_cls
        import calendar
        y = self.year
        m = int(self.month)
        last_day = calendar.monthrange(y, m)[1]
        date_from = date_cls(y, m, 1)
        date_to = date_cls(y, m, last_day)

        domain = [
            ('company_id', '=', self.company_id.id),
            ('date_from', '<=', date_to),
            ('date_to', '>=', date_from),
            ('state', '=', 'done'),
        ]
        if self.branch_id:
            domain.append(('branch_id', '=', self.branch_id.id))

        payslips = self.env['planilla.payslip.cr'].search(domain)
        if not payslips:
            raise UserError(
                f'No se encontraron boletas pagadas para {self.month}/{self.year}. '
                f'Verifique que las boletas estén en estado "Pagado" y '
                f'el mes/año seleccionados sean correctos.'
            )
        return payslips

    # ── Construcción del archivo ──────────────────────────────────

    def _build_tipo1(self, patron_num, year, month, total_workers, total_salaries):
        """
        Registro Tipo 1 — Encabezado de la declaración.
        Posiciones (1-indexed, longitud fija):
          01-01: Tipo registro = '1'
          02-10: Número patrono (9 dígitos)
          11-14: Año (4 dígitos)
          15-16: Mes (2 dígitos)
          17-22: Cantidad trabajadores (6 dígitos)
          23-35: Total salarios en colones (13 dígitos, sin decimales)
          36-80: Espacios (relleno)
        """
        line = (
            '1' +
            self._fmt_patron(patron_num) +
            str(year) +
            str(month).zfill(2) +
            self._fmt_num(total_workers, 6) +
            self._fmt_num(total_salaries, 13) +
            ' ' * 45
        )
        return line

    def _build_tipo2(self, patron_num, emp_data, year, month):
        """
        Registro Tipo 2 — Detalle por trabajador.
        Posiciones (longitud fija 120 caracteres):
          01-01:  Tipo registro = '2'
          02-10:  Número patrono (9 dígitos)
          11-19:  Cédula trabajador (9 dígitos)
          20-49:  Apellido 1 (30 chars, mayúsculas)
          50-79:  Apellido 2 (30 chars, mayúsculas)
          80-99:  Nombre (20 chars, mayúsculas)
          100-112: Salario bruto mensual (13 dígitos, sin decimales)
          113-116: Días trabajados (4 dígitos)
          117-117: Tipo trabajador: 1=regular, 2=parcial, 3=temporal
          118-118: Subsidio maternidad: S/N
          119-120: Relleno
        """
        cedula     = self._fmt_cedula(emp_data.get('cedula', ''))
        # FIX NEW-07 v54: usar _sanitize_ascii para eliminar tildes/enies (EDDI-7 v4.x)
        apellido1  = self._sanitize_ascii(emp_data.get('apellido1', ''), 30, align='upper')
        apellido2  = self._sanitize_ascii(emp_data.get('apellido2', ''), 30, align='upper')
        nombre     = self._sanitize_ascii(emp_data.get('nombre', ''), 20, align='upper')
        salario    = self._fmt_num(emp_data.get('salario_bruto', 0), 13)
        dias       = self._fmt_num(emp_data.get('dias_trabajados', 30), 4)
        tipo_trab  = str(emp_data.get('tipo_trabajador', '1'))
        maternidad = 'S' if emp_data.get('maternidad', False) else 'N'

        line = (
            '2' +
            self._fmt_patron(patron_num) +
            cedula +
            apellido1 +
            apellido2 +
            nombre +
            salario +
            dias +
            tipo_trab +
            maternidad +
            '  '   # relleno
        )
        assert len(line) == 120, f'Tipo 2 longitud incorrecta: {len(line)} (esperado 120)'
        return line

    def _build_tipo9(self, patron_num, year, month, total_workers, total_salaries,
                     total_ccss_obrero, total_ccss_patronal):
        """
        Registro Tipo 9 — Totales / cierre del archivo.
          01-01:  Tipo registro = '9'
          02-10:  Número patrono (9 dígitos)
          11-14:  Año
          15-16:  Mes
          17-22:  Total trabajadores
          23-35:  Total salarios
          36-48:  Total CCSS Obrero (10.83%)
          49-61:  Total CCSS Patronal (26.83%)
          62-80:  Relleno
        """
        line = (
            '9' +
            self._fmt_patron(patron_num) +
            str(year) +
            str(month).zfill(2) +
            self._fmt_num(total_workers, 6) +
            self._fmt_num(total_salaries, 13) +
            self._fmt_num(total_ccss_obrero, 13) +
            self._fmt_num(total_ccss_patronal, 13) +
            ' ' * 19
        )
        return line

    # ── Extracción de datos del empleado ─────────────────────────

    def _parse_employee_name(self, full_name):
        """
        Intenta separar nombre y apellidos del nombre completo.
        Convención CR: 'Apellido1 Apellido2 Nombre(s)' o 'Nombre Apellido1 Apellido2'
        Retorna (apellido1, apellido2, nombre).
        """
        parts = (full_name or '').strip().split()
        if len(parts) >= 3:
            return parts[0], parts[1], ' '.join(parts[2:])
        elif len(parts) == 2:
            return parts[0], '', parts[1]
        else:
            return full_name or '', '', ''

    def _get_employee_data(self, payslip):
        """Extrae datos del empleado para el registro Tipo 2."""
        emp = payslip.employee_id

        # Cédula: usar campo de identificación del empleado
        cedula = ''
        if hasattr(emp, 'identification_id') and emp.identification_id:
            cedula = emp.identification_id
        elif hasattr(emp, 'vat') and emp.vat:
            cedula = emp.vat

        apellido1, apellido2, nombre = self._parse_employee_name(emp.name)

        # Calcular días trabajados en el mes
        import calendar
        y, m = self.year, int(self.month)
        days_in_month = calendar.monthrange(y, m)[1]
        dias_trabajados = days_in_month

        # Verificar incapacidades en el mes
        incapacidades = self.env['planilla.disability'].search([
            ('employee_id', '=', emp.id),
            ('date_start', '<=', payslip.date_to),
            ('date_end', '>=', payslip.date_from),
            ('state', 'in', ('confirmed', 'paid')),
        ])
        dias_incapacidad = 0
        maternidad = False
        for inc in incapacidades:
            dias_incapacidad += inc.days or 0
            if inc.disability_type == 'maternity':
                maternidad = True

        dias_trabajados = max(0, days_in_month - dias_incapacidad)

        # Tipo de trabajador
        contract_type = getattr(emp, 'contract_type', None)
        if contract_type == 'temporal':
            tipo_trab = '3'
        elif dias_trabajados < days_in_month:
            tipo_trab = '2'  # parcial (incapacitado parte del mes)
        else:
            tipo_trab = '1'  # regular

        return {
            'cedula': cedula,
            'apellido1': apellido1,
            'apellido2': apellido2,
            'nombre': nombre,
            'salario_bruto': payslip.gross_salary or 0,
            'dias_trabajados': dias_trabajados,
            'tipo_trabajador': tipo_trab,
            'maternidad': maternidad,
        }

    # ── Validacion formato EDDI-7 v4.x ──────────────────────────

    # FIX NEW-07 v54: validar que cada linea generada cumple la especificacion
    # EDDI-7 version 4.x vigente CCSS 2026.
    # Referencia: Manual Tecnico EDDI-7 v4, CCSS Costa Rica (2024-2026).
    # Reglas principales:
    #   - Tipo 1: 80 caracteres exactos
    #   - Tipo 2: 120 caracteres exactos
    #   - Tipo 9: 80 caracteres exactos
    #   - Solo caracteres ASCII (a-z, A-Z, 0-9, espacios). Sin tildes ni enie.
    #   - CRLF como separador de lineas (\r\n)
    #   - Codificacion: ISO-8859-1 o UTF-8 sin BOM

    EDDI7_LINE_LENGTHS = {'1': 80, '2': 120, '9': 80}

    def _validate_eddi7_lines(self, lines):
        """FIX NEW-07 v54: valida longitud y caracteres de cada linea segun EDDI-7 v4.x.
        Retorna lista de errores de formato encontrados.
        """
        errors = []
        import unicodedata
        for i, line in enumerate(lines, start=1):
            tipo = line[0] if line else '?'
            expected_len = self.EDDI7_LINE_LENGTHS.get(tipo)
            if expected_len and len(line) != expected_len:
                errors.append(
                    f'Linea {i} (Tipo {tipo}): longitud {len(line)} caracteres '
                    f'(esperado {expected_len} segun EDDI-7 v4.x)'
                )
            # Verificar caracteres no-ASCII (tildes, enies, etc.) que pueden
            # causar rechazo en el sistema SICERE de la CCSS
            for j, ch in enumerate(line):
                if ord(ch) > 127:
                    normalized = unicodedata.normalize('NFKD', ch)
                    ascii_ch = normalized.encode('ascii', 'ignore').decode('ascii')
                    errors.append(
                        f'Linea {i}, pos {j+1}: caracter no-ASCII "{ch}" '
                        f'(sugerido: "{ascii_ch}"). CCSS/SICERE requiere ASCII puro.'
                    )
        return errors

    def _sanitize_ascii(self, value, length, align='left'):
        """FIX NEW-07 v54: elimina tildes y enie para cumplir ASCII puro de EDDI-7.
        Normaliza a NFD y descarta los combinadores diacriticos (categoria Mn).
        """
        import unicodedata
        s = str(value or '')
        # Normalizar NFD: separa caracter base del diacritico
        nfd = unicodedata.normalize('NFD', s)
        ascii_s = ''.join(c for c in nfd if unicodedata.category(c) != 'Mn')
        # Reemplazar enie manualmente (no se elimina solo con NFD)
        ascii_s = ascii_s.replace('\u00f1', 'n').replace('\u00d1', 'N')
        ascii_s = ascii_s.upper() if align == 'upper' else ascii_s
        return ascii_s[:length].ljust(length)

    # ── Acción principal ──────────────────────────────────────────

    def action_generate_eddi7(self):
        """
        Genera el archivo EDDI-7 para declaración mensual CCSS.
        Produce un archivo .txt con registros tipo 1, 2... y 9.
        """
        self.ensure_one()
        patron_num = self._validate_patron_number()
        payslips = self._get_payslips_for_month()

        rh = self.env['planilla.rate.helper']
        ccss_obrero_rate = rh.get_ccss_employee_rate()    # 10.83%
        ccss_patronal_rate = rh.get_ccss_employer_rate()  # 26.83%

        lines = []
        errors = []
        total_salaries = 0
        total_ccss_obrero = 0
        total_ccss_patronal = 0
        worker_count = 0

        # Agrupar boletas por empleado (tomar la última del mes si hay varias)
        payslips_by_emp = {}
        for ps in payslips:
            emp_id = ps.employee_id.id
            if emp_id not in payslips_by_emp:
                payslips_by_emp[emp_id] = ps
            else:
                # Tomar la más reciente
                if ps.date_to > payslips_by_emp[emp_id].date_to:
                    payslips_by_emp[emp_id] = ps

        # Construir registros Tipo 2
        for emp_id, payslip in sorted(payslips_by_emp.items()):
            emp = payslip.employee_id
            try:
                emp_data = self._get_employee_data(payslip)

                # Validar cédula obligatoria
                if not emp_data['cedula']:
                    errors.append(
                        f'Empleado {emp.name} (ID {emp_id}): '
                        f'Sin número de cédula. Configure en Empleado → Información Privada.'
                    )
                    continue

                # Excluir maternidad si no se solicita incluirla
                if not self.include_maternity and emp_data['maternidad']:
                    continue

                salario = float(emp_data.get('salario_bruto', 0))
                total_salaries += salario
                total_ccss_obrero += salario * ccss_obrero_rate
                total_ccss_patronal += salario * ccss_patronal_rate
                worker_count += 1

                tipo2_line = self._build_tipo2(patron_num, emp_data, self.year, self.month)
                lines.append(tipo2_line)

            except Exception as e:
                errors.append(f'Empleado {emp.name}: Error generando registro — {str(e)}')

        if not lines:
            error_detail = '\n'.join(errors) if errors else 'Sin boletas válidas.'
            raise UserError(
                f'No se generaron registros para el archivo EDDI-7.\n\n{error_detail}'
            )

        # Construir archivo completo
        tipo1 = self._build_tipo1(
            patron_num, self.year, self.month, worker_count, total_salaries
        )
        tipo9 = self._build_tipo9(
            patron_num, self.year, self.month, worker_count, total_salaries,
            total_ccss_obrero, total_ccss_patronal
        )

        all_lines = [tipo1] + lines + [tipo9]

        # FIX NEW-07 v54: validar estructura EDDI-7 v4.x antes de generar el archivo
        format_errors = self._validate_eddi7_lines(all_lines)
        if format_errors:
            errors.extend(format_errors)

        file_content = '\r\n'.join(all_lines) + '\r\n'  # CRLF segun EDDI-7 v4.x

        import base64
        filename = f'EDDI7_{patron_num}_{self.year}{self.month}.txt'
        self.write({
            'eddi7_file': base64.b64encode(file_content.encode('utf-8')),
            'eddi7_filename': filename,
            'state': 'generated',
            'line_count': worker_count,
            'total_salaries': total_salaries,
            'validation_errors': '\n'.join(errors) if errors else False,
        })

        # Notificación de advertencias
        if errors:
            self.env['bus.bus']._sendone(
                self.env.user.partner_id,
                'simple_notification',
                {
                    'title': 'EDDI-7 generado con advertencias',
                    'message': f'{worker_count} trabajadores incluidos. '
                               f'{len(errors)} con errores (ver campo Errores).',
                    'type': 'warning',
                    'sticky': True,
                }
            )

        # Retornar al mismo formulario para descargar
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'planilla.eddi7.export',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_download(self):
        """Descarga el archivo EDDI-7 generado."""
        self.ensure_one()
        if not self.eddi7_file:
            raise UserError('Genere primero el archivo EDDI-7.')
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/planilla.eddi7.export/{self.id}/eddi7_file/{self.eddi7_filename}?download=true',
            'target': 'self',
        }
