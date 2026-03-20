from odoo import models, fields, api
import base64, io

# ── openpyxl ─────────────────────────────────────────────────────────────────
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.comments import Comment
from openpyxl.worksheet.datavalidation import DataValidation


class ImportTemplateWizard(models.TransientModel):
    """Genera y descarga el machote Excel para carga masiva de empleados."""
    _name        = 'planilla.import.template.wizard'
    _description = 'Machote de Importación de Empleados'

    company_id = fields.Many2one(
        'res.company', string='Empresa', required=True,
        default=lambda self: self.env.company,
    )
    include_employees    = fields.Boolean('Empleados (datos principales)', default=True)
    include_loans        = fields.Boolean('Préstamos y Adelantos',         default=True)
    include_pension      = fields.Boolean('Pensiones Alimentarias',        default=True)
    include_benefits     = fields.Boolean('Beneficios / Deducciones Recurrentes', default=True)
    include_disabilities = fields.Boolean('Incapacidades',                 default=True)
    include_vacations    = fields.Boolean('Saldo de Vacaciones',           default=True)
    include_overtime     = fields.Boolean('⏱️  Horas Extras (histórico)',   default=True)
    include_embargos     = fields.Boolean('⚖️  Embargos Judiciales',        default=True)
    include_bonos        = fields.Boolean('🎯  Bonos y Beneficios',          default=True)
    include_sample_data  = fields.Boolean(
        '🧪  Incluir fila de prueba (EMPLEADO PRUEBA)',
        default=False,
        help='Agrega una fila naranja de prueba en todas las hojas con cédula 1-0000-0001. '
             'Active solo cuando quiera verificar que la importación funciona correctamente. '
             'Luego use el botón "Eliminar Empleado de Prueba" para limpiar.'
    )

    # Cédula reservada para la fila de prueba — misma en template y en import wizard
    _SAMPLE_CEDULA = '1-0000-0001'

    # ── Listas de valores para dropdowns Excel ────────────────────────────────
    # Orden importa: cada key ocupa una columna en la hoja oculta _LISTAS
    _DV_LISTS = {
        # Identificación
        'id_type':      ['Cédula Nacional', 'Residencia / DIMEX',
                         'Permiso de Trabajo', 'Pasaporte', 'Indocumentado'],
        # INS
        'ins_risk':     ['I - Oficinas', 'II - Comercio', 'III - Industria',
                         'IV - Construcción', 'V - Alto Riesgo'],
        'ins_workday':  ['Ordinaria', 'Extraordinaria', 'Mixta',
                         'Tiempo Parcial', 'Por Horas', 'Ocasional'],
        'ins_civil':    ['Soltero/a', 'Casado/a', 'Divorciado/a',
                         'Viudo/a', 'Unión Libre', 'Separado/a'],
        'ins_nat':      ['Costarricense', 'Nicaragüense', 'Colombiano/a',
                         'Estadounidense', 'Hondureño/a', 'Salvadoreño/a',
                         'Guatemalteco/a', 'Panameño/a', 'Mexicano/a',
                         'Venezolano/a', 'Peruano/a', 'Ecuatoriano/a', 'Otra'],
        # Banco y cuenta
        'banco':        ['BNCR', 'BCR', 'BP', 'BAC', 'BCT', 'CATHAY', 'CMB',
                         'DAVIVIENDA', 'GENERAL', 'IMPROSA', 'LAFISE',
                         'PROMERICA', 'PRIVAL', 'SCOTIA', 'COOCIQUE',
                         'COOPENAE', 'MUTUAL_ALJ', 'Otro'],
        'account_type': ['Cuenta Corriente', 'Cuenta de Ahorros', 'SINPE Móvil'],
        # Nómina
        'frequency':    ['Mensual', 'Quincenal', 'Semanal', 'Bimensual'],
        'calc_method':  ['Salario Fijo', 'Por Horas Trabajadas'],
        # Género y si/no
        'gender':       ['Masculino', 'Femenino', 'Otro'],
        'si_no':        ['Si', 'No'],
        # Horarios — sincronizado con default_data.xml
        # Préstamos
        'loan_type':    ['Préstamo de Empresa', 'Adelanto de Salario'],
        'loan_state':   ['Aprobado', 'En Curso', 'Borrador', 'Pagado', 'Anulado'],
        # Pensión
        'pension_rel':  ['Hijo/a', 'Cónyuge', 'Padre', 'Madre', 'Otro'],
        'pension_calc': ['Porcentaje del Salario', 'Monto Fijo'],
        # Beneficios
        'benefit_type': ['Beneficio / Ingreso', 'Deducción / Descuento'],
        'amount_type':  ['Monto Fijo', 'Porcentaje'],
        # Incapacidades
        'disability':   ['Enfermedad Común (CCSS)', 'Accidente de Trabajo (CCSS)',
                         'Riesgo Laboral (INS)', 'Maternidad / Paternidad', 'Otro'],
        # Horas extras
        'overtime_type':['Simple (1.5x)', 'Doble (2.0x)', 'Día Feriado'],
        # Embargos
        'embargo_calc': ['Monto Fijo', 'Porcentaje del Neto Disponible'],
        # Bonos
        'bono_type':    ['Productividad / Rendimiento', 'Asistencia Perfecta',
                         'Antigüedad por Años de Servicio', 'Subsidio de Transporte / Kilometraje',
                         'Subsidio de Alimentación (en dinero)', 'Subsidio Educativo',
                         'Subsidio de Salud / Médico', 'Gastos de Representación',
                         'Comisión por Ventas', 'Incentivo / Premio Especial', 'Otro'],
        'bono_calc':    ['Monto Fijo', 'Porcentaje del Salario Base'],
        'si_no_recurrente': ['Si', 'No'],
        # Tipo de sangre
        'blood_type':   ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-'],
        # Ocupaciones INS — COCR-2023 (INEC), basada en CIUO-08 OIT
        'ins_occupation': [
            '[1111] Miembros del poder legislativo y ejecutivo',
            '[1112] Personal directivo de la administración pública',
            '[1113] Jefes de comunidades étnicas',
            '[1114] Dirigentes de organizaciones que presentan un interés especial',
            '[1120] Directores y gerentes generales',
            '[1211] Directores y gerentes de servicios financieros',
            '[1212] Directores y gerentes de recursos humanos',
            '[1213] Directores y gerentes de políticas y planificación',
            '[1219] Directores y gerentes de administración y servicios no clasificados bajo otros',
            '[1221] Directores y gerentes de venta y comercialización',
            '[1222] Directores y gerentes de publicidad y relaciones públicas',
            '[1223] Directores y gerentes de investigación y desarrollo',
            '[1311] Directores y gerentes de producción agropecuaria y silvicultura',
            '[1312] Directores y gerentes de producción acuícola, piscícola y de pesca',
            '[1321] Directores y gerentes de industrias manufactureras',
            '[1322] Directores y gerentes de explotaciones de minería',
            '[1323] Directores y gerentes de empresas de construcción',
            '[1324] Directores y gerentes de empresas de abastecimiento, distribución y afines',
            '[1330] Directores y gerentes de servicios de tecnología de la información',
            '[1341] Directores y gerentes de servicios de cuidados infantiles',
            '[1342] Directores y gerentes de servicios de salud',
            '[1343] Directores y gerentes de servicios de atención a personas adultas mayores',
            '[1344] Directores y gerentes de servicios de bienestar social',
            '[1345] Directores y gerentes de servicios de educación',
            '[1346] Directores y gerentes de sucursales de bancos y servicios financieros',
            '[1349] Directores y gerentes de servicios profesionales no clasificados bajo otros',
            '[1411] Directores y gerentes de hoteles',
            '[1412] Directores y gerentes de restaurantes',
            '[1420] Gerentes de comercios al por mayor y al por menor',
            '[1431] Directores y gerentes de centros deportivos, de esparcimiento y culturales',
            '[1439] Directores y gerentes de servicios no clasificados bajo otros',
            '[2111] Físicos y astrónomos', '[2112] Meteorólogos', '[2113] Químicos',
            '[2114] Geólogos y geofísicos', '[2120] Matemáticos, actuarios y estadísticos',
            '[2131] Biólogos, botánicos, zoólogos y afines',
            '[2132] Agrónomos, zootecnistas y afines',
            '[2133] Profesionales de la protección medioambiental',
            '[2141] Ingenieros industriales y de producción', '[2142] Ingenieros civiles',
            '[2143] Ingenieros medioambientales',
            '[2144] Ingenieros mecánicos, navales y aeronáuticos',
            '[2145] Ingenieros químicos', '[2146] Ingenieros de minas, metalúrgicos y afines',
            '[2149] Ingenieros no clasificados bajo otros epígrafes',
            '[2151] Ingenieros eléctricos', '[2152] Ingenieros electrónicos',
            '[2153] Ingenieros en telecomunicaciones, audio y sonido',
            '[2161] Arquitectos', '[2162] Arquitectos paisajistas',
            '[2163] Diseñadores industriales de productos y moda',
            '[2164] Urbanistas e ingenieros de tránsito', '[2165] Topógrafos',
            '[2166] Diseñadores gráficos y multimedia',
            '[2211] Médicos generales', '[2212] Médicos geriatras',
            '[2213] Médicos ginecólogos y obstetras', '[2214] Médicos psiquiatras',
            '[2215] Médicos ortopedistas y traumatólogos',
            '[2219] Especialistas médicos no clasificados bajo otros epígrafes',
            '[2220] Enfermeros profesionales y profesionales de partería',
            '[2230] Profesionales de medicina tradicional y alternativa',
            '[2250] Veterinarios', '[2261] Dentistas',
            '[2262] Cirujanos orales y maxilofaciales', '[2271] Farmacéuticos',
            '[2272] Profesionales de la salud y la higiene laboral y ambiental',
            '[2273] Fisioterapeutas', '[2274] Nutricionistas',
            '[2275] Audiólogos y terapeutas del lenguaje', '[2276] Optometristas',
            '[2279] Profesionales de la salud no clasificados bajo otros epígrafes',
            '[2310] Profesores de instituciones de educación superior',
            '[2320] Profesores de formación profesional',
            '[2330] Profesores de educación secundaria',
            '[2341] Profesores de educación primaria', '[2342] Profesores de educación preescolar',
            '[2351] Especialistas en métodos pedagógicos',
            '[2352] Profesores de educación especial', '[2353] Otros profesores de idiomas',
            '[2354] Otros profesores de música', '[2355] Otros profesores de artes',
            '[2356] Instructores en tecnología de la información',
            '[2359] Profesionales de la educación no clasificados bajo otros epígrafes',
            '[2411] Contadores y auditores financieros',
            '[2412] Asesores financieros y en inversiones', '[2413] Analistas financieros',
            '[2421] Analistas de gestión y organización',
            '[2422] Profesionales en políticas sociales y de administración',
            '[2423] Profesionales de gestión de talento humano',
            '[2424] Profesionales en formación, desarrollo de personal y evaluación',
            '[2431] Profesionales de la publicidad y la comercialización',
            '[2432] Profesionales de relaciones públicas',
            '[2433] Profesionales de ventas técnicas y médicas',
            '[2434] Profesionales de ventas de tecnología de la información',
            '[2511] Analistas de sistemas', '[2512] Desarrolladores de software',
            '[2513] Desarrolladores web y multimedia', '[2514] Programadores de aplicaciones',
            '[2519] Desarrolladores y analistas de software no clasificados bajo otros',
            '[2521] Diseñadores y administradores de bases de datos',
            '[2522] Administradores de sistemas', '[2523] Profesionales en redes de computadores',
            '[2529] Profesionales en bases de datos y redes no clasificados bajo otros',
            '[2611] Abogados', '[2612] Jueces',
            '[2619] Profesionales en derecho no clasificados bajo otros epígrafes',
            '[2621] Archivistas, curadores de arte y restauradores',
            '[2622] Bibliotecólogos, documentalistas y afines',
            '[2631] Economistas', '[2632] Sociólogos, antropólogos y afines',
            '[2633] Filósofos, historiadores y especialistas en ciencias políticas',
            '[2634] Psicólogos', '[2635] Profesionales del trabajo social',
            '[2636] Profesionales religiosos',
            '[2639] Profesionales en ciencias sociales no clasificados bajo otros',
            '[2641] Autores literarios y otros escritores',
            '[2642] Periodistas, editores y redactores',
            '[2643] Traductores, intérpretes, lingüistas y filólogos',
            '[2651] Escultores, pintores artísticos y afines',
            '[2652] Músicos, cantantes y compositores',
            '[2653] Coreógrafos, directores de danza y bailarines profesionales',
            '[2654] Directores y productores de cine, de teatro y afines', '[2655] Actores',
            '[3111] Técnicos en ciencias físicas y químicas', '[3112] Técnicos en ingeniería civil',
            '[3113] Electrotécnicos', '[3114] Técnicos en electrónica',
            '[3115] Técnicos en ingeniería mecánica', '[3116] Técnicos en química industrial',
            '[3117] Técnicos en ingeniería de minas y metalurgia',
            '[3118] Delineantes y dibujantes técnicos',
            '[3119] Otros técnicos en ciencias físicas, química e ingeniería no clasificados',
            '[3121] Supervisores en ingeniería de minas',
            '[3122] Supervisores en industrias manufactureras',
            '[3123] Supervisores de la construcción',
            '[3131] Operadores de plantas de generación y distribución de energía',
            '[3132] Operadores de incineradores y plantas de tratamiento de agua',
            '[3133] Controladores de instalaciones de procesamiento de productos químicos',
            '[3134] Operadores de instalaciones de refinación de petróleo y gas natural',
            '[3135] Controladores de procesos de producción de metales',
            '[3139] Técnicos en control de procesos no clasificados bajo otros',
            '[3141] Técnicos en ciencias biológicas',
            '[3142] Técnicos agropecuarios', '[3143] Técnicos forestales',
            '[3151] Maquinistas en navegación marítima',
            '[3152] Capitanes y oficiales de cubierta',
            '[3153] Pilotos de aviación y afines', '[3154] Controladores de tráfico aéreo',
            '[3155] Técnicos en seguridad aeronáutica',
            '[3211] Técnicos en aparatos de diagnóstico y tratamiento médico',
            '[3212] Técnicos de laboratorios médicos',
            '[3213] Técnicos y asistentes en farmacia',
            '[3214] Técnicos de prótesis médicas y dentales',
            '[3220] Profesionales de nivel medio de enfermería',
            '[3230] Profesionales de nivel medio de medicina tradicional y alternativa',
            '[3240] Técnicos y asistentes veterinarios',
            '[3250] Técnico en emergencias médicas',
            '[3261] Auxiliares y técnicos de odontología',
            '[3262] Técnicos en documentación sanitaria',
            '[3263] Trabajadores comunitarios de la salud',
            '[3264] Técnicos en optometría y ópticos',
            '[3265] Técnicos y asistentes fisioterapeutas',
            '[3266] Practicantes y asistentes médicos',
            '[3267] Inspectores de la salud laboral y medioambiental',
            '[3268] Auxiliar de ambulancias en emergencias médicas',
            '[3269] Técnicos de las ciencias de la salud no clasificados bajo otros',
            '[3311] Agentes de bolsa, cambio y otros servicios financieros',
            '[3312] Oficiales de préstamos y créditos',
            '[3313] Técnicos y auxiliares de contabilidad',
            '[3314] Profesionales de nivel medio de servicios estadísticos y matemáticos',
            '[3315] Tasadores', '[3316] Técnicos y asistentes en administración y economía',
            '[3321] Agentes de seguros', '[3322] Representantes comerciales',
            '[3323] Agentes de proveeduría', '[3324] Agentes de compras y consignatarios',
            '[3331] Declarantes o gestores de aduana',
            '[3332] Organizadores de conferencias y eventos',
            '[3333] Agentes de empleo y contratistas de mano de obra',
            '[3334] Agentes inmobiliarios',
            '[3339] Otros agentes comerciales y corredores no clasificados bajo otros',
            '[3341] Supervisores de oficina', '[3342] Secretarios jurídicos',
            '[3343] Secretarios administrativos y ejecutivos', '[3344] Secretarios médicos',
            '[3351] Inspectores de aduanas y fronteras',
            '[3352] Agentes de administración tributaria',
            '[3353] Agentes de servicios de seguridad social',
            '[3354] Funcionarios de servicios de expedición de licencias y permisos',
            '[3355] Inspectores de policía y detectives',
            '[3411] Profesionales de nivel medio del derecho y servicios legales',
            '[3412] Técnicos y asistentes en trabajo social',
            '[3421] Atletas y deportistas',
            '[3422] Entrenadores, instructores y árbitros de actividades deportivas',
            '[3423] Instructores de educación física y actividades recreativas',
            '[3431] Fotógrafos', '[3432] Diseñadores y decoradores de interior',
            '[3511] Técnicos en operaciones de tecnología de la información',
            '[3512] Técnicos en asistencia al usuario de tecnología de la información',
            '[3513] Técnicos en redes y sistemas de computadores', '[3514] Técnicos de la web',
            '[3521] Técnicos de radiodifusión y grabación audiovisual',
            '[3522] Técnicos de ingeniería de las telecomunicaciones',
            '[4110] Oficinistas generales', '[4120] Secretarios generales',
            '[4131] Operadores de máquinas de procesamiento de texto y mecanógrafos',
            '[4132] Digitadores de datos', '[4211] Cajeros de bancos y afines',
            '[4221] Recepcionistas',
            '[4222] Empleados de atención y asesoramiento de llamadas',
            '[4229] Empleados de servicios de información al cliente no clasificados bajo otros',
            '[4311] Empleados de contabilidad y cálculo de costos',
            '[4312] Empleados de servicios estadísticos, financieros y de seguros',
            '[4313] Empleados encargados de las nóminas',
            '[4321] Empleados de control de abastecimientos e inventario',
            '[4322] Empleados de servicios de apoyo a la producción',
            '[4323] Empleados de servicio de transporte',
            '[5111] Auxiliares de servicio abordo', '[5120] Cocineros', '[5131] Saloneros',
            '[5132] Bartenders',
            '[5141] Especialistas en tratamientos del cabello',
            '[5142] Especialistas en tratamientos de belleza estética y afines',
            '[5151] Supervisores limpieza en oficinas, hoteles y otros establecimientos',
            '[5153] Encargados de mantenimiento de edificios',
            '[5165] Instructores de manejo', '[5169] Otros trabajadores de servicios personales',
            '[5211] Vendedores de quioscos y de puestos de mercado',
            '[5221] Propietarios y comerciantes encargados de pequeñas tiendas',
            '[5222] Supervisores de tiendas y almacenes',
            '[5223] Asistentes de ventas de tiendas y almacenes',
            '[5230] Cajeros y expendedores de boletos y tiquetes',
            '[5243] Vendedores puerta a puerta', '[5244] Vendedores por teléfono',
            '[5246] Vendedores de comidas al mostrador',
            '[5249] Vendedores no clasificados bajo otros epígrafes',
            '[5311] Cuidadores de niños', '[5312] Ayudantes de maestros',
            '[5321] Trabajadores de los cuidados personales en instituciones',
            '[5322] Trabajadores de los cuidados personales a domicilio',
            '[5411] Bomberos', '[5412] Policías e inspectores de tránsito',
            '[5414] Guardas de protección en establecimientos',
            '[5415] Vigilante de casas particulares',
            '[5419] Otros trabajadores que prestan servicios de protección y vigilancia',
            '[6111] Agricultores y trabajadores calificados de cultivos',
            '[6121] Criadores de ganado', '[6122] Avicultores y trabajadores de avicultura',
            '[6130] Productores y trabajadores de explotaciones agropecuarias mixtas',
            '[6210] Trabajadores forestales calificados y afines',
            '[7111] Albañiles',
            '[7113] Operarios en cemento armado, encofradores y afines',
            '[7114] Carpinteros de armar y de obra blanca',
            '[7121] Techadores', '[7122] Revestidores e instaladores de pisos',
            '[7126] Fontaneros e instaladores de tuberías',
            '[7127] Mecánicos de instalaciones de refrigeración y aire acondicionado',
            '[7131] Pintores y empapeladores',
            '[7211] Moldeadores de metal', '[7212] Soldadores y oxicortadores',
            '[7214] Montadores de estructuras metálicas',
            '[7221] Herreros y forjadores', '[7222] Herramentistas y afines',
            '[7223] Reguladores y operadores de máquinas herramientas',
            '[7231] Mecánicos y reparadores de vehículos de motor',
            '[7232] Mecánicos y reparadores de motores de avión',
            '[7233] Mecánicos y reparadores de máquinas agrícolas e industriales',
            '[7311] Mecánicos y reparadores de instrumentos de precisión',
            '[7313] Joyeros, orfebres y plateros',
            '[7411] Electricistas de obras y afines',
            '[7412] Mecánicos y ajustadores electricistas',
            '[7413] Instaladores y reparadores de líneas eléctricas',
            '[7421] Mecánicos y reparadores en electrónica',
            '[7422] Instaladores y reparadores en tecnología de la información',
            '[7511] Carniceros, pescadores y afines',
            '[7512] Panaderos, pasteleros, golosineros y confiteros',
            '[8111] Mineros y operadores de instalaciones mineras',
            '[8211] Ensambladores de maquinaria mecánica',
            '[8212] Ensambladores de equipos eléctricos y electrónicos',
            '[8322] Conductores de automóviles, taxis y camionetas',
            '[8331] Conductores de autobuses y tranvías',
            '[8332] Conductores de camiones pesados',
            '[8341] Operadores de maquinaria agrícola y forestal móvil',
            '[8342] Operadores de máquinas de movimiento de tierras y afines',
            '[8343] Operadores de grúas, aparatos elevadores y afines',
            '[9111] Limpiadores y asistentes domésticos',
            '[9112] Limpiadores y asistentes de oficinas, hoteles y otros establecimientos',
            '[9211] Peones de explotaciones agrícolas',
            '[9311] Peones de minas y canteras',
            '[9312] Peones de obras públicas y mantenimiento',
            '[9313] Peones de la construcción de edificios',
            '[9321] Empacadores manuales',
            '[9329] Peones de la industria manufacturera no clasificados bajo otros',
            '[9333] Peones de carga', '[9334] Reponedores de estanterías',
            '[9411] Cocineros de comidas rápidas', '[9412] Ayudantes de cocina',
            '[9611] Recolectores de basura y material reciclable',
            '[9621] Mensajeros, mandaderos, maleteros y repartidores',
            '[9629] Ocupaciones elementales no clasificadas bajo otros epígrafes',
        ],
    }

    # ── paleta ────────────────────────────────────────────────────────────────
    _C = {
        'dark':     '1F3864',
        'med':      '2E75B6',
        'light':    'D6E4F0',
        'req':      'FFF2CC',
        'opt':      'FFFFFF',
        'example':  'E2EFDA',
        'border':   'BDD7EE',
        'white':    'FFFFFF',
        'red_hdr':  'C00000',
    }

    # ── catálogos para dropdowns ──────────────────────────────────────────────
    # Orden de columnas en la hoja oculta _LISTAS (A, B, C, ...)
    # Clave → (col_idx_0based, [valores])
    _LISTAS = {
        'id_type':          (0,  ['01','02','03','04','05']),
        'si_no':            (1,  ['si','no']),
        'ins_risk':         (2,  ['I','II','III','IV','V']),
        'ins_workday':      (3,  ['01','02','03','04','05','06']),
        'banco':            (4,  ['BNCR','BCR','BP','BAC','BCT','CATHAY','CMB',
                                  'DAVIVIENDA','GENERAL','IMPROSA','LAFISE',
                                  'PROMERICA','PRIVAL','SCOTIA','COOCIQUE',
                                  'COOPENAE','MUTUAL_ALJ','OTRO']),
        'account_type':     (5,  ['corriente','ahorros','sinpe']),
        'frequency':        (6,  ['monthly','biweekly','weekly','bimonthly']),
        'calc_method':      (7,  ['fixed','attendance']),
        'ins_nationality':  (8,  ['CR','NI','CO','US','HN','SV','GT','PA',
                                  'MX','VE','PE','EC','OT']),
        'ins_civil':        (9,  ['01','02','03','04','05','06']),
        'gender':           (10, ['masculino','femenino','otro']),
        'loan_type':        (11, ['loan','advance']),
        'loan_state':       (12, ['approved','active','draft','paid','cancelled']),
        'pension_relacion': (13, ['hijo','conyuge','padre','madre','otro']),
        'pension_calc':     (14, ['porcentaje','monto_fijo']),
        'benefit_type':     (15, ['beneficio','deduccion']),
        'amount_type':      (16, ['fijo','porcentaje']),
        'disability_type':  (17, ['ccss','ccss_accident','ins','maternity','other']),
        'overtime_type':    (18, ['simple','double','holiday']),
    }

    # ── helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _fill(hex_color):
        return PatternFill('solid', fgColor=hex_color)

    @staticmethod
    def _font(bold=False, color='000000', size=10, italic=False):
        return Font(bold=bold, color=color, size=size, italic=italic, name='Arial')

    @staticmethod
    def _border():
        s = Side(style='thin', color='BDD7EE')
        return Border(left=s, right=s, top=s, bottom=s)

    @staticmethod
    def _center():
        return Alignment(horizontal='center', vertical='center', wrap_text=True)

    @staticmethod
    def _left():
        return Alignment(horizontal='left', vertical='center', wrap_text=True)

    def _hdr(self, cell, text, bg=None, txt='FFFFFF', bold=True, size=10):
        C = self._C
        cell.value     = text
        cell.font      = self._font(bold=bold, color=txt, size=size)
        cell.fill      = self._fill(bg or C['med'])
        cell.border    = self._border()
        cell.alignment = self._center()

    def _col_hdr(self, cell, text, required, desc=''):
        C = self._C
        bg  = C['req'] if required else C['light']
        txt = '7B3F00' if required else C['dark']
        cell.value     = text
        cell.font      = self._font(bold=True, color=txt, size=9)
        cell.fill      = self._fill(bg)
        cell.border    = self._border()
        cell.alignment = self._center()
        if desc:
            c = Comment(desc, 'Planilla CR')
            c.width, c.height = 220, 55
            cell.comment = c

    def _example(self, cell, value):
        cell.value     = value
        cell.fill      = self._fill(self._C['example'])
        cell.border    = self._border()
        cell.alignment = self._left()
        cell.font      = self._font(italic=True, size=9, color='375623')

    def _data(self, cell, required=True):
        bg = self._C['req'] if required else self._C['opt']
        cell.fill      = self._fill(bg)
        cell.border    = self._border()
        cell.alignment = self._left()
        cell.font      = self._font(size=10)

    @staticmethod
    def _w(ws, col_idx, width):
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    # ── título de hoja ────────────────────────────────────────────────────────
    def _sheet_title(self, ws, text, ncols, bg=None):
        C = self._C
        col_letter = get_column_letter(ncols)
        ws.merge_cells(f'A1:{col_letter}1')
        c = ws['A1']
        c.value     = text
        c.font      = self._font(bold=True, color=C['white'], size=12)
        c.fill      = self._fill(bg or C['dark'])
        c.alignment = self._center()
        ws.row_dimensions[1].height = 28

    # ── filas de datos (vacías + ejemplo + prueba) ─────────────────────────────
    def _build_rows(self, ws, cols, data_rows=80, header_row=2, example_row=3,
                    sample_values=None):
        """
        cols = [(nombre, required, width, ejemplo, desc), ...]
        sample_values: lista de valores para la fila de prueba (naranja).
                       Si es None, no se dibuja fila de prueba.
        """
        # Encabezados
        for ci, (nombre, req, w, _, desc) in enumerate(cols, 1):
            self._col_hdr(ws.cell(header_row, ci), nombre, req, desc)
            self._w(ws, ci, w)
        ws.row_dimensions[header_row].height = 45

        # Fila de ejemplo (verde)
        for ci, (_, _, _, ej, _) in enumerate(cols, 1):
            self._example(ws.cell(example_row, ci), ej)
        ws.row_dimensions[example_row].height = 16

        # Fila de prueba (naranja) — opcional, justo debajo del ejemplo
        data_start = example_row + 1
        if sample_values:
            sample_row = example_row + 1
            data_start = sample_row + 1
            for ci, val in enumerate(sample_values, 1):
                self._sample(ws.cell(sample_row, ci), val)
            ws.row_dimensions[sample_row].height = 16

        # Filas vacías
        for r in range(data_start, data_start + data_rows):
            for ci, (_, req, _, _, _) in enumerate(cols, 1):
                self._data(ws.cell(r, ci), req)
            ws.row_dimensions[r].height = 16

        ws.freeze_panes = ws.cell(example_row, 1)
        ws.sheet_view.showGridLines = False

    def _sample(self, cell, value):
        """Estilo para la fila de prueba: fondo naranja, texto oscuro, itálica."""
        cell.value     = value
        cell.fill      = self._fill('F4B942')
        cell.border    = self._border()
        cell.alignment = self._left()
        cell.font      = self._font(italic=True, bold=True, size=9, color='7B2D00')

    def _build_listas_sheet(self, wb):
        """Crea hoja de listas para DataValidation.
        IMPORTANTE: la hoja se deja VISIBLE (no oculta) porque Excel 2016/2019
        y algunas versiones de Excel Online no muestran el dropdown cuando la
        hoja fuente está oculta. Se protege y se estiliza como hoja de sistema
        para que el usuario no la modifique accidentalmente.
        """
        ws = wb.create_sheet('⚙ LISTAS')
        C = self._C

        # Encabezado de advertencia fila 1
        ws.merge_cells('A1:Z1')
        c = ws['A1']
        c.value  = '⚠  HOJA DE SISTEMA — No modificar. Contiene las listas de los desplegables.'
        c.font   = self._font(bold=True, color='FFFFFF', size=9)
        c.fill   = self._fill('7F7F7F')
        c.alignment = self._center()
        ws.row_dimensions[1].height = 18

        # Escribir cada lista empezando en fila 2 con header de columna
        for col_idx, (key, vals) in enumerate(self._DV_LISTS.items(), 1):
            # Header de la columna
            hdr = ws.cell(2, col_idx)
            hdr.value = key
            hdr.font  = self._font(bold=True, color='FFFFFF', size=8)
            hdr.fill  = self._fill('404040')
            hdr.alignment = self._center()
            # Valores desde fila 3
            for row_idx, val in enumerate(vals, 3):
                cell = ws.cell(row_idx, col_idx, value=val)
                cell.font = Font(name='Arial', size=8, color='333333')
                cell.fill = PatternFill('solid', fgColor='F2F2F2')

        # Ajustar anchos mínimos
        ws.column_dimensions['A'].width = 20
        # Columna de ocupaciones — más ancha
        keys = list(self._DV_LISTS.keys())
        if 'ins_occupation' in keys:
            occ_col = get_column_letter(keys.index('ins_occupation') + 1)
            ws.column_dimensions[occ_col].width = 70

        # Proteger la hoja para que no se edite accidentalmente
        ws.protection.sheet     = True
        ws.protection.password  = 'planilla_cr_sys'
        ws.protection.enable()

        ws.sheet_properties.tabColor = '808080'

        return ws, len(self._DV_LISTS)  # retorna nro de columnas estáticas

    def _build_dynamic_lists(self, wb, company_id, static_cols):
        """Agrega listas dinámicas (desde BD) a la hoja ⚙ LISTAS.
        Se llama desde action_generate DESPUÉS de _build_listas_sheet.
        Retorna dict {clave: (col_letter, first_row, last_row)} para _dv_dynamic.
        """
        ws = wb['⚙ LISTAS']
        ws.protection.sheet = False  # desproteger temporalmente para escribir

        next_col = static_cols + 1  # columna donde empiezan las listas dinámicas

        HDR_FONT  = self._font(bold=True, color='FFFFFF', size=8)
        HDR_FILL  = self._fill('1F4E79')
        GREY_FILL = self._fill('999999')
        DATA_FONT = Font(name='Arial', size=8, color='333333')
        DATA_FILL = PatternFill('solid', fgColor='EBF3FB')
        GREY_FONT = Font(name='Arial', size=8, color='999999', italic=True)

        co = company_id
        dyn = {}  # key → (col_letter, first_row, last_row)

        def _write_list(key, values, width=35):
            """Escribe la lista en la columna actual.
            SIEMPRE incrementa next_col aunque values esté vacío,
            para que las columnas siguientes no se desplacen.
            """
            nonlocal next_col
            col_letter = get_column_letter(next_col)
            hdr = ws.cell(2, next_col)
            hdr.value = key
            hdr.font  = HDR_FONT if values else GREY_FONT
            hdr.fill  = HDR_FILL if values else GREY_FILL
            hdr.alignment = self._center()
            if values:
                first_r = 3
                for i, val in enumerate(values, first_r):
                    c = ws.cell(i, next_col, value=val)
                    c.font = DATA_FONT
                    c.fill = DATA_FILL
                last_r = first_r + len(values) - 1
                ws.column_dimensions[col_letter].width = width
                dyn[key] = (col_letter, first_r, last_r)
            else:
                ws.cell(3, next_col, value='(sin registros — créelos en Odoo primero)')
                ws.cell(3, next_col).font = GREY_FONT
                ws.column_dimensions[col_letter].width = 28
            next_col += 1  # siempre avanzar

        # Usamos sudo() para bypassear las reglas multi-empresa del ORM.
        # Sin sudo(), el ORM filtra automáticamente por las empresas del usuario
        # y puede excluir registros creados en otra sesión o empresa.
        # Filtramos explícitamente por empresa o sin empresa (registros globales).
        def _search(model, domain=None, order='name'):
            dom = domain or []
            return self.env[model].sudo().with_context(active_test=False).search(
                dom, order=order)

        # ── Tipos de horario ─────────────────────────────────────────────
        schedules = _search('planilla.schedule.type')
        _write_list('schedule', [s.name for s in schedules], width=40)

        # ── Calendarizaciones de planilla ─────────────────────────────────
        # Sin filtro de empresa: sudo() ya bypasea ir.rules. En un sistema
        # de una sola empresa todos los registros son del cliente.
        cals = _search('planilla.calendar')
        _write_list('calendar', [c.name for c in cals], width=28)

        # ── Tipos de empleado ─────────────────────────────────────────────
        etypes = _search('planilla.employee.type')
        _write_list('employee_type', [e.name for e in etypes], width=28)

        # ── Estados de empleado ───────────────────────────────────────────
        estatuses = _search('planilla.employee.status')
        _write_list('employee_status', [e.name for e in estatuses], width=24)

        # ── Sucursales ────────────────────────────────────────────────────
        branches = _search('planilla.branch')
        _write_list('branch', [b.name for b in branches], width=28)

        # ── Departamentos ─────────────────────────────────────────────────
        depts = _search('hr.department', [('parent_id', '=', False)])
        _write_list('department', [d.name for d in depts], width=30)

        # ── Sub-departamentos ─────────────────────────────────────────────
        subdepts = _search('hr.department', [('parent_id', '!=', False)])
        _write_list('subdepartment', [d.name for d in subdepts], width=30)

        # ── Puestos / Cargos ──────────────────────────────────────────────
        jobs = _search('hr.job')
        _write_list('job', [j.name for j in jobs], width=28)

        # ── Países ────────────────────────────────────────────────────────
        countries = self.env['res.country'].search([], order='name')
        _write_list('country', [c.name for c in countries], width=28)

        # Re-proteger
        ws.protection.sheet    = True
        ws.protection.password = 'planilla_cr_sys'
        ws.protection.enable()

        return dyn  # dict {key: (col_letter, first_row, last_row)}


    def _dv_dynamic(self, ws, col_idx, dyn_key, first_data_row,
                    dyn_lists, last_data_row=500, title='Opciones'):
        """Aplica dropdown usando una lista dinámica (de BD) en ⚙ LISTAS.
        dyn_lists: dict retornado por _build_dynamic_lists().
        """
        dyn = dyn_lists or {}
        if dyn_key not in dyn:
            return  # catálogo vacío en BD — no agrega dropdown
        col_letter_src, first_r, last_r = dyn[dyn_key]
        formula    = f"'⚙ LISTAS'!${col_letter_src}${first_r}:${col_letter_src}${last_r}"
        col_letter = get_column_letter(col_idx)
        sqref      = f'{col_letter}{first_data_row}:{col_letter}{last_data_row}'
        dv = DataValidation(
            type='list',
            formula1=formula,
            allow_blank=True,
            showDropDown=False,
            showErrorMessage=True,
            errorStyle='warning',
            errorTitle='Valor no reconocido',
            error='Seleccione un valor de la lista o créelo en Odoo primero.',
            showInputMessage=True,
            promptTitle=title,
            prompt='Seleccione de la lista (opciones cargadas desde Odoo)',
        )
        ws.add_data_validation(dv)
        dv.sqref = sqref

    def _dv(self, ws, col_idx, list_key, first_data_row, last_data_row=500,
            title='Opciones'):
        """Helper rápido que busca la lista en _DV_LISTS y aplica el dropdown.
        La hoja ⚙ LISTAS tiene: fila 1 = advertencia, fila 2 = headers,
        fila 3 en adelante = valores. Por eso el rango empieza en $3.
        """
        vals = self._DV_LISTS.get(list_key, [])
        if not vals:
            return
        keys = list(self._DV_LISTS.keys())
        listas_col = get_column_letter(keys.index(list_key) + 1)
        first_r    = 3                      # fila 3: primera fila de datos
        last_r     = 3 + len(vals) - 1      # última fila de datos
        formula    = f"'⚙ LISTAS'!${listas_col}${first_r}:${listas_col}${last_r}"
        col_letter = get_column_letter(col_idx)
        sqref      = f'{col_letter}{first_data_row}:{col_letter}{last_data_row}'

        dv = DataValidation(
            type='list',
            formula1=formula,
            allow_blank=True,
            showDropDown=False,
            showErrorMessage=True,
            errorStyle='warning',
            errorTitle='Valor no reconocido',
            error='El valor ingresado no está en el catálogo. Revise la hoja ⚙ LISTAS.',
            showInputMessage=True,
            promptTitle=title,
            prompt=f'Seleccione: {", ".join(str(v) for v in vals[:4])}{"…" if len(vals) > 4 else ""}',
        )
        ws.add_data_validation(dv)
        dv.sqref = sqref

    # ══════════════════════════════════════════════════════════════════════════
    # HOJA INSTRUCCIONES
    # ══════════════════════════════════════════════════════════════════════════
    def _build_instructions(self, wb):
        C = self._C
        ws = wb.active
        ws.title = '📋 INSTRUCCIONES'
        ws.sheet_view.showGridLines = False

        ws.merge_cells('A1:H1')
        c = ws['A1']
        c.value     = (f'MACHOTE DE IMPORTACIÓN — SISTEMA PLANILLA v5.4  '
                       f'|  {self.company_id.name}  |  Legislación CR 2026')
        c.font      = self._font(bold=True, color=C['white'], size=13)
        c.fill      = self._fill(C['dark'])
        c.alignment = self._center()
        ws.row_dimensions[1].height = 34

        ws.merge_cells('A2:H2')
        c = ws['A2']
        c.value     = 'Carga masiva de empleados — complete las hojas y entregue al implementador'
        c.font      = self._font(italic=True, color=C['white'], size=10)
        c.fill      = self._fill(C['med'])
        c.alignment = self._center()
        ws.row_dimensions[2].height = 18

        lines = [
            ('', ''),
            ('¿QUÉ ES ESTE ARCHIVO?', ''),
            ('', 'Permite cargar todos los empleados y sus datos al módulo Planilla CR de Odoo '
                 'de una sola vez, evitando la digitación manual uno a uno.'),
            ('', ''),
            ('HOJAS INCLUIDAS', ''),
            ('', '👤  EMPLEADOS            → Datos principales (obligatorio completar)'),
            ('', '💰  PRESTAMOS            → Préstamos y adelantos activos del empleado'),
            ('', '👨‍👧  PENSION_ALIMENTARIA   → Órdenes judiciales de pensión alimentaria'),
            ('', '➕  OTROS DESCUENTOS      → Cuota sindical, cooperativa, ahorro voluntario, seguro médico (no embargos ni bonos formales)'),
            ('', '🏥  INCAPACIDADES        → Incapacidades activas al momento de la carga'),
            ('', '🏖️  VACACIONES           → Saldo de vacaciones acumulado'),
            ('', '⏱️  HORAS EXTRAS         → Horas extras históricas'),
            ('', '⚖️  EMBARGOS             → Embargos judiciales (Art. 172 CT — máx. 25% neto)'),
            ('', '🎯  BONOS                → Bonos e incentivos (productividad, transporte, etc.)'),
            ('', '📚  CATALOGOS            → Valores válidos para campos de lista (NO editar)'),
            ('', ''),
            ('INSTRUCCIONES', ''),
            ('', '1. Complete la hoja EMPLEADOS — un empleado por fila.'),
            ('', '2. Use la cédula como llave: debe coincidir exactamente en todas las hojas.'),
            ('', '3. Para préstamos, pensiones o beneficios múltiples: agregue una fila por cada uno.'),
            ('', '4. Los campos de selección tienen menú desplegable — haga clic en la celda y elija de la lista.'),
            ('', '5. Fechas en formato DD/MM/AAAA  (ejemplo: 15/03/2020).'),
            ('', '6. Montos en colones (₡), sin símbolo ni comas  (ejemplo: 750000).'),
            ('', '7. La fila de PRUEBA (fondo naranja, cédula 1-0000-0001) sirve para verificar que la importación funciona. Elimine ese empleado luego.'),
            ('', '8. NO modifique los encabezados ni el nombre de las hojas.'),
            ('', ''),
            ('CÓDIGO DE COLORES', ''),
        ]

        for i, (label, text) in enumerate(lines, start=3):
            ws.row_dimensions[i].height = 18
            if label:
                ws.merge_cells(f'A{i}:H{i}')
                c = ws.cell(i, 1, value=label)
                c.font      = self._font(bold=True, color=C['dark'], size=10)
                c.fill      = self._fill(C['light'])
                c.alignment = self._left()
            else:
                ws.merge_cells(f'B{i}:H{i}')
                ws.cell(i, 2, value=text).font = self._font(size=10)

        # leyenda colores
        last = ws.max_row + 1
        leyenda = [
            (C['req'],     '🟡 Fondo AMARILLO → Campo OBLIGATORIO'),
            (C['opt'],     '⬜ Fondo BLANCO   → Campo OPCIONAL'),
            (C['example'], '🟢 Fondo VERDE    → Fila de EJEMPLO (solo referencia, no se importa)'),
            ('F4B942',     '🟠 Fondo NARANJA  → Fila de PRUEBA (cédula 1-0000-0001) — importar para verificar, luego eliminar'),
        ]
        for offset, (color, texto) in enumerate(leyenda):
            r = last + offset
            ws.cell(r, 1).fill   = self._fill(color)
            ws.cell(r, 1).border = self._border()
            ws.merge_cells(f'B{r}:H{r}')
            c = ws.cell(r, 2, value=texto)
            c.font = self._font(size=10)
            ws.row_dimensions[r].height = 18

        ws.column_dimensions['A'].width = 30
        ws.column_dimensions['B'].width = 90

    # ══════════════════════════════════════════════════════════════════════════
    # HOJA EMPLEADOS
    # ══════════════════════════════════════════════════════════════════════════
    def _build_employees(self, wb, sample=False, dyn_lists=None):
        ws = wb.create_sheet('👤 EMPLEADOS')
        self._sheet_title(ws, 'DATOS DE EMPLEADOS — Un empleado por fila', 43)

        # Secciones (fila 2) — 43 columnas totales
        secciones = [
            (1,  6,  'IDENTIFICACIÓN'),
            (7,  14, 'DATOS LABORALES'),
            (15, 23, 'DATOS INS'),
            (24, 29, 'CCSS Y BANCO'),
            (30, 34, 'CONFIGURACIÓN NÓMINA'),
            (35, 43, 'DATOS PERSONALES Y MÉDICOS'),
        ]
        for cs, ce, titulo in secciones:
            ws.merge_cells(start_row=2, start_column=cs,
                           end_row=2,   end_column=ce)
            self._hdr(ws.cell(2, cs), titulo)
        ws.row_dimensions[2].height = 18

        cols = [
            # ── Identificación (cols 1-6) ─────────────────────────────────
            ('Nombre Completo',           True,  28, 'Juan Pérez Rodríguez',
             'Nombre completo del empleado'),
            ('Cédula / Identificación',   True,  18, '1-2345-6789',
             'Cédula, DIMEX o pasaporte — llave entre hojas'),
            ('Tipo de Identificación',    True,  18, 'Cédula Nacional',
             'Seleccione del desplegable'),
            ('Fecha de Ingreso',          True,  14, '01/03/2020',
             'Formato DD/MM/AAAA'),
            ('Fecha de Salida',           False, 14, '',
             'Solo si ya no trabaja en la empresa'),
            ('Correo Corporativo',        False, 28, 'juan.perez@empresa.com',
             'Email de trabajo'),
            # ── Datos laborales (cols 7-14) ───────────────────────────────
            ('Departamento',              False, 26, '',
             'Seleccione del desplegable (cargado desde Odoo)'),
            ('Sub Departamento',          False, 26, '',
             'Seleccione del desplegable (cargado desde Odoo)'),
            ('Sucursal',                  False, 22, '',
             'Seleccione del desplegable (cargado desde Odoo)'),
            ('Puesto / Cargo',            False, 24, '',
             'Seleccione del desplegable o escriba el nombre exacto'),
            ('Tipo de Empleado',          True,  22, 'Empleado Indefinido',
             'Seleccione del desplegable (cargado desde Odoo)'),
            ('Estado del Empleado',       True,  18, '',
             'Seleccione del desplegable (cargado desde Odoo)'),
            ('Tipo de Horario',           True,  32, '',
             'Seleccione del desplegable (cargado desde Odoo — tipos de su empresa)'),
            ('Calendarización de Planilla', True, 26, '',
             'Seleccione del desplegable (cargado desde Odoo) — Ej: Mensual, Quincenal'),
            # ── Datos INS (cols 15-23) ────────────────────────────────────
            ('Incluir en INS',            True,  12, 'Si',
             'Si / No'),
            ('Número de Póliza INS',      False, 18, 'POL-12345',
             'Número de póliza del INS'),
            ('Nombre INS',                False, 18, 'Juan',
             'Nombre como aparece en el sistema INS'),
            ('Primer Apellido INS',       False, 16, 'Pérez',    ''),
            ('Segundo Apellido INS',      False, 16, 'Rodríguez',''),
            ('Clase de Riesgo INS',       True,  22, 'I - Oficinas',
             'Seleccione del desplegable'),
            ('Jornada INS',               True,  18, 'Ordinaria',
             'Seleccione del desplegable'),
            ('Ocupación INS',             True,  50, '[4110] Oficinistas generales',
             'Seleccione del desplegable — COCR-2023 (INEC)'),
            ('Tipo de Sangre',            False, 10, 'O+',
             'Seleccione del desplegable: A+, A-, B+, B-, AB+, AB-, O+, O-'),
            # ── CCSS y banco (cols 24-29) ─────────────────────────────────
            ('Número CCSS',               False, 16, '123456789',
             'Número de asegurado CCSS'),
            ('Asegurado CCSS',            True,  14, 'Si',
             'Si / No'),
            ('Cuenta Bancaria / IBAN',    False, 30, 'CR65015200000000000000',
             'IBAN de 22 caracteres'),
            ('SINPE Móvil',               False, 14, '88887777',
             'Teléfono de 8 dígitos registrado en SINPE Móvil'),
            ('Banco',                     False, 20, 'BNCR',
             'Seleccione del desplegable'),
            ('Tipo de Cuenta Banco',      False, 16, 'Cuenta Corriente',
             'Seleccione del desplegable'),
            # ── Configuración nómina (cols 30-34) ─────────────────────────
            ('Salario Base (₡)',          True,  18, '750000',
             'Salario mensual en colones, sin comas ni símbolo'),
            ('Fecha Vigencia Salarial',   False, 18, '01/01/2026',
             'Desde cuándo aplica el salario (DD/MM/AAAA)'),
            ('Método de Cálculo',         True,  18, 'Salario Fijo',
             'Seleccione del desplegable'),
            ('Nacionalidad INS',          False, 14, 'Costarricense',
             'Seleccione del desplegable'),
            ('Salario Variable',          True,  16, 'No',
             'Si / No — Active "Si" si recibe comisiones o HE recurrentes (Art. 153 CT).'),
            # ── Datos personales y médicos (cols 36-44) ───────────────────
            ('Estado Civil INS',          False, 16, 'Soltero/a',
             'Seleccione del desplegable'),
            ('Género',                    False, 14, 'Masculino',
             'Seleccione del desplegable'),
            ('País',                      False, 22, 'Costa Rica',
             'Seleccione del desplegable — país de residencia'),
            ('Número de Dependientes',    False, 12, '0',
             'Hijos u otros dependientes'),
            ('Dirección',                 False, 30, 'San José, Escazú',
             'Dirección de habitación'),
            ('Teléfono Personal',         False, 14, '88887777',
             'Número de teléfono personal'),
            ('Correo Personal',           False, 26, 'juan@gmail.com',
             'Correo electrónico personal (privado)'),
            ('Diagnóstico / Notas Médicas', False, 40, '',
             'Condiciones, alergias, medicamentos u otras notas para el INS o emergencias.'),
            ('Observaciones',             False, 30, '',
             'Notas internas del empleado'),
        ]

        sv = None
        if sample:
            sv = [
                # ── Identificación (cols 1-6)
                'Juan Pérez Rodríguez',      # Nombre Completo
                self._SAMPLE_CEDULA,          # Cédula (1-2345-6789)
                'Cédula Nacional',            # Tipo de Identificación
                '15/03/2021',                 # Fecha de Ingreso
                '',                           # Fecha de Salida (activo)
                'jperez@empresa.com',         # Correo Corporativo
                # ── Datos laborales (cols 7-14)
                'Administración',             # Departamento
                'Contabilidad',               # Sub Departamento
                'Principal',                  # Sucursal
                'Asistente Administrativo',   # Puesto / Cargo
                'Empleado Indefinido',        # Tipo de Empleado
                'Activo',                     # Estado del Empleado
                'Jornada Completa (8 horas - Lun a Vie)',  # Tipo de Horario — seleccione del desplegable
                'Quincenal',                  # Calendarización de Planilla
                # ── Datos INS (cols 15-23)
                'Si',                         # Incluir en INS
                'POL-2025-00123',             # Número de Póliza INS
                'Juan',                       # Nombre INS
                'Pérez',                      # Primer Apellido INS
                'Rodríguez',                  # Segundo Apellido INS
                'I - Oficinas',               # Clase de Riesgo INS
                'Ordinaria',                  # Jornada INS
                '[4313] Empleados encargados de las nóminas',  # Ocupación INS
                'O+',                         # Tipo de Sangre
                # ── CCSS y banco (cols 24-29)
                '1234567890',                 # Número CCSS
                'Si',                         # Asegurado CCSS
                'CR65015200000000000000',      # Cuenta Bancaria / IBAN
                '88887777',                   # SINPE Móvil
                'BNCR',                       # Banco
                'Cuenta Corriente',           # Tipo de Cuenta Banco
                # ── Configuración nómina (cols 30-34)
                '750000',                     # Salario Base (₡)
                '15/03/2021',                 # Fecha Vigencia Salarial
                'Salario Fijo',               # Método de Cálculo
                'Costarricense',              # Nacionalidad INS
                'No',                         # Salario Variable
                # ── Datos personales y médicos (cols 35-43)
                'Soltero/a',                  # Estado Civil INS
                'Masculino',                  # Género
                'Costa Rica',                 # País
                '1',                          # Número de Dependientes
                'San José, Escazú, Res. Los Laureles',  # Dirección
                '88990011',                   # Teléfono Personal
                '',                           # Correo Personal
                '',                           # Diagnóstico / Notas Médicas
                '⚠️ FILA DE PRUEBA — eliminar antes de importar',  # Observaciones
            ]

        self._build_rows(ws, cols, data_rows=100, header_row=3, example_row=4,
                         sample_values=sv)

        # ── Dropdowns estáticos ───────────────────────────────────────────
        self._dv(ws,  3, 'id_type',        5, title='Tipo de Identificación')
        # col 13: Tipo de Horario — DINÁMICO (lee planilla.schedule.type de BD)
        # (se aplica abajo junto con los demás dinámicos)
        self._dv(ws, 15, 'si_no',          5, title='Incluir en INS (si/no)')
        self._dv(ws, 20, 'ins_risk',       5, title='Clase de Riesgo INS')
        self._dv(ws, 21, 'ins_workday',    5, title='Tipo de Jornada INS')
        self._dv(ws, 22, 'ins_occupation', 5, title='Ocupación INS (COCR-2023)')
        self._dv(ws, 23, 'blood_type',     5, title='Tipo de Sangre')
        self._dv(ws, 25, 'si_no',          5, title='Asegurado CCSS (si/no)')
        self._dv(ws, 28, 'banco',          5, title='Banco')
        self._dv(ws, 29, 'account_type',   5, title='Tipo de Cuenta Banco')
        # col 30: Salario Base — sin dropdown
        # col 31: Fecha Vigencia — sin dropdown
        self._dv(ws, 32, 'calc_method',    5, title='Método de Cálculo')
        self._dv(ws, 33, 'ins_nat',        5, title='Nacionalidad INS')
        self._dv(ws, 34, 'si_no',          5, title='Salario Variable (si/no)')
        self._dv(ws, 35, 'ins_civil',      5, title='Estado Civil INS')
        self._dv(ws, 36, 'gender',         5, title='Género')

        # ── Dropdowns dinámicos (desde BD) ────────────────────────────────
        dl = dyn_lists or {}
        self._dv_dynamic(ws,  7, 'department',     5, dl, title='Departamento')
        self._dv_dynamic(ws,  8, 'subdepartment',  5, dl, title='Sub Departamento')
        self._dv_dynamic(ws,  9, 'branch',         5, dl, title='Sucursal')
        self._dv_dynamic(ws, 10, 'job',            5, dl, title='Puesto / Cargo')
        self._dv_dynamic(ws, 11, 'employee_type',  5, dl, title='Tipo de Empleado')
        self._dv_dynamic(ws, 12, 'employee_status',5, dl, title='Estado del Empleado')
        self._dv_dynamic(ws, 13, 'schedule',       5, dl, title='Tipo de Horario')
        self._dv_dynamic(ws, 14, 'calendar',       5, dl, title='Calendarización de Planilla')
        self._dv_dynamic(ws, 37, 'country',        5, dl, title='País')
    # HOJA PRÉSTAMOS
    def _build_loans(self, wb, sample=False):
        cols = [
            ('Cédula Empleado',          True,  18, '1-2345-6789',     'Debe coincidir con hoja EMPLEADOS'),
            ('Tipo de Préstamo',         True,  16, 'Préstamo de Empresa', 'Préstamo de Empresa / Adelanto de Salario'),
            ('Descripción / Motivo',     False, 30, 'Préstamo personal',''),
            ('Monto Total (₡)',          True,  16, '500000',          'Total del préstamo, sin comas'),
            ('Número de Cuotas',         True,  14, '10',              'Cantidad de cuotas a descontar'),
            ('Fecha de Otorgamiento',    True,  18, '15/01/2026',      'DD/MM/AAAA'),
            ('Fecha Primera Deducción',  True,  18, '01/02/2026',      'DD/MM/AAAA — primer boleta que descuenta'),
            ('Estado',                   True,  14, 'Aprobado',            'Ver CATALOGOS → loan_state'),
            ('Monto ya Pagado (₡)',      False, 16, '100000',          'Si ya se ha descontado algo'),
            ('Observaciones',            False, 28, '',                ''),
        ]
        sv = [self._SAMPLE_CEDULA, 'Préstamo de Empresa', 'Préstamo de prueba', '100000', '5',
              '01/01/2024', '01/02/2024', 'Aprobado', '0', '⚠️ PRUEBA'] if sample else None
        ws = wb.create_sheet('💰 PRESTAMOS')
        self._sheet_title(ws, 'PRÉSTAMOS Y ADELANTOS — Un préstamo por fila (puede haber varios por empleado)', len(cols))
        self._build_rows(ws, cols, data_rows=100, sample_values=sv)
        # col 2: Tipo de Préstamo, col 8: Estado
        self._dv(ws, 2, 'loan_type',   4, title='Tipo de Préstamo')
        self._dv(ws, 8, 'loan_state',  4, title='Estado del Préstamo')

    # ══════════════════════════════════════════════════════════════════════════
    # HOJA PENSIÓN ALIMENTARIA
    # ══════════════════════════════════════════════════════════════════════════
    def _build_pension(self, wb, sample=False):
        cols = [
            ('Cédula Empleado',        True,  18, '1-2345-6789',          'Cédula del empleado afectado'),
            ('Número de Expediente',   True,  22, '15-000123-0638-FA',    'Número del expediente judicial'),
            ('Juzgado',                True,  30, 'Juzgado de Familia SJ', ''),
            ('Fecha de Resolución',    True,  18, '10/06/2023',            'DD/MM/AAAA'),
            ('Nombre Beneficiario',    True,  26, 'María Rodríguez Solano','Nombre completo'),
            ('Relación Beneficiario',  True,  16, 'Hijo/a',                  'Ver CATALOGOS → pension_relacion'),
            ('Cuenta Beneficiario',    False, 28, 'CR21015108010018023571','IBAN del beneficiario (opcional)'),
            ('Tipo de Cálculo',        True,  16, 'Porcentaje del Salario', 'Porcentaje del Salario / Monto Fijo'),
            ('Porcentaje (%)',          False, 12, '25',                   'Si tipo=porcentaje, solo el número (ej: 25)'),
            ('Monto Fijo (₡)',         False, 14, '',                     'Si tipo=monto_fijo, monto en colones'),
            ('Fecha de Inicio',        True,  14, '01/07/2023',            'DD/MM/AAAA'),
            ('Fecha de Fin',           False, 14, '',                     'Dejar vacío si no tiene vencimiento'),
        ]
        sv = [self._SAMPLE_CEDULA, 'TEST-0000-PRUEBA', 'Juzgado Prueba', '01/01/2024',
              'Beneficiario Prueba', 'Hijo/a', '', 'Porcentaje del Salario', '10', '',
              '01/01/2024', ''] if sample else None
        ws = wb.create_sheet('👨‍👧 PENSION_ALIMENTARIA')
        self._sheet_title(ws, 'PENSIONES ALIMENTARIAS — Una resolución por fila', len(cols))
        self._build_rows(ws, cols, sample_values=sv)
        # col 6: Relación Beneficiario, col 8: Tipo de Cálculo
        self._dv(ws, 6, 'pension_rel',  4, title='Relación Beneficiario')
        self._dv(ws, 8, 'pension_calc', 4, title='Tipo de Cálculo')

    # ══════════════════════════════════════════════════════════════════════════
    # HOJA BENEFICIOS RECURRENTES
    # ══════════════════════════════════════════════════════════════════════════
    def _build_benefits(self, wb, sample=False):
        cols = [
            ('Cédula Empleado',   True,  18, '1-2345-6789',        'Cédula del empleado'),
            ('Concepto',          True,  28, 'Cuota Sindical',      'Nombre descriptivo del descuento o deducción'),
            ('Tipo',              True,  14, 'Deducción / Descuento','Deducción / Descuento   o   Beneficio / Ingreso'),
            ('Tipo de Monto',     True,  16, 'Monto Fijo',           'Monto Fijo / Porcentaje'),
            ('Monto (₡)',         False, 14, '15000',               'Si tipo_monto=fijo'),
            ('Porcentaje (%)',    False, 12, '',                    'Si tipo_monto=porcentaje, solo el número'),
            ('Código Deducción',  False, 16, '',                    'Código del concepto si el módulo lo requiere'),
            ('Vigente Desde',     True,  14, '01/01/2026',          'DD/MM/AAAA'),
            ('Vigente Hasta',     False, 14, '',                    'Dejar vacío si es indefinido'),
            ('Nota',              False, 28, 'Acuerdo colectivo 2026','Descripción o referencia'),
        ]
        sv = [self._SAMPLE_CEDULA, 'Cuota Sindical Prueba', 'Deducción / Descuento', 'Monto Fijo',
              '2000', '', '', '01/01/2024', '', '⚠️ PRUEBA'] if sample else None
        ws = wb.create_sheet('➕ OTROS DESCUENTOS')
        self._sheet_title(ws, 'OTROS DESCUENTOS / DEDUCCIONES RECURRENTES — Cuota sindical, cooperativa, ahorro voluntario, seguro médico, etc.', len(cols))
        self._build_rows(ws, cols, data_rows=100, sample_values=sv)
        # col 3: Tipo, col 4: Tipo de Monto
        self._dv(ws, 3, 'benefit_type', 4, title='Tipo (beneficio/deduccion)')
        self._dv(ws, 4, 'amount_type',  4, title='Tipo de Monto')

    def _build_disabilities(self, wb, sample=False):
        cols = [
            ('Cédula Empleado',      True,  18, '1-2345-6789',    ''),
            ('Tipo de Incapacidad',  True,  22, 'Enfermedad Común (CCSS)', 'Ver CATALOGOS → disability_type'),
            ('Fecha Inicio',         True,  14, '01/02/2026',     'DD/MM/AAAA'),
            ('Fecha Fin',            True,  14, '10/02/2026',     'DD/MM/AAAA'),
            ('% Subsidiado CCSS',    False, 14, '60',             'Porcentaje que paga la CCSS'),
            ('% a Cargo Patrono',    False, 14, '40',             'Porcentaje que asume el patrono'),
            ('Número Certificado',   False, 20, 'CCSS-2026-123',  'Número del certificado CCSS'),
            ('Diagnóstico',          False, 28, 'Gripa severa',   'Descripción del diagnóstico'),
            ('Salario Diario (₡)',   False, 16, '25000',          'Salario mensual ÷ 30'),
            ('Observaciones',        False, 28, '',               ''),
        ]
        sv = [self._SAMPLE_CEDULA, 'Enfermedad Común (CCSS)', '01/01/2024', '05/01/2024',
              '60', '40', 'PRUEBA-0000', 'Diagnóstico prueba', '16667',
              '⚠️ PRUEBA'] if sample else None
        ws = wb.create_sheet('🏥 INCAPACIDADES')
        self._sheet_title(ws, 'INCAPACIDADES — Solo las activas o dentro del período de carga', len(cols))
        self._build_rows(ws, cols, sample_values=sv)
        # col 2: Tipo de Incapacidad
        self._dv(ws, 2, 'disability', 4, title='Tipo de Incapacidad')

    def _build_vacations(self, wb, sample=False):
        cols = [
            # ── Identificación ────────────────────────────────────────────────
            ('Cédula Empleado',               True,  18, '1-2345-6789',   'Cédula del empleado tal como está en el sistema'),
            # ── Saldo inicial pre-implementación ─────────────────────────────
            ('Saldo Inicial (días)',           True,  18, '8.50',
             'OBLIGATORIO: días de vacaciones disponibles a la Fecha de Corte.\n'
             'Es el saldo REAL que tiene el empleado hoy (lo que le falta por disfrutar).\n'
             'Ejemplo: si tiene 8.5 días pendientes, escriba 8.5'),
            ('Fecha de Corte del Saldo',      True,  20, '31/12/2025',
             'OBLIGATORIO: fecha exacta hasta la cual se calculó el saldo inicial.\n'
             'El sistema acumulará días solo a partir de esta fecha.\n'
             'Use el último día antes de que empiece a usar el sistema.\n'
             'Formato: DD/MM/AAAA'),
            # ── Información de referencia (solo para documentación) ───────────
            ('Días Acumulados Totales (ref)', False, 20, '24.00',
             'Referencia: total de días que le correspondían desde su ingreso.\n'
             'No se importa, solo para documentar el cálculo.'),
            ('Días Tomados Historial (ref)',  False, 20, '15.50',
             'Referencia: días que ya disfrutó antes de la implementación.\n'
             'No se importa, solo para documentar el cálculo.\n'
             'Verificación: Acumulados − Tomados = Saldo Inicial'),
            ('Salario Diario Ref. (₡)',       False, 20, '25000',
             'Referencia: salario diario del empleado a la fecha de corte.\n'
             'No se importa, solo para documentar cuánto valdría cada día.'),
            ('Observaciones',                 False, 30, 'Saldo calculado por RRHH a dic-2025',
             'Notas internas, quien calculó el saldo, fuente del dato, etc.'),
        ]
        sv = [self._SAMPLE_CEDULA, '5.0', '31/12/2025', '17.0', '12.0',
              '16667', '⚠️ PRUEBA'] if sample else None
        ws = wb.create_sheet('🏖️ VACACIONES')
        self._sheet_title(
            ws,
            'SALDO INICIAL DE VACACIONES — Para empresas con empleados pre-existentes',
            len(cols)
        )
        self._build_rows(ws, cols, sample_values=sv)

        # Instrucciones adicionales al final de la hoja
        last_row = ws.max_row + 2
        inst_fill = PatternFill('solid', fgColor='EBF5FB')
        inst_font = Font(name='Calibri', size=10, italic=True, color='1A5276')

        instructions = [
            '📋  INSTRUCCIONES DE USO:',
            '',
            '  1. Complete CÉDULA + SALDO INICIAL + FECHA DE CORTE para cada empleado.',
            '  2. El "Saldo Inicial" es la cantidad de días disponibles en esa fecha exacta.',
            '     Ejemplo: Juan tiene derecho a 24 días y ha tomado 15.5 → Saldo = 8.5 días.',
            '  3. La "Fecha de Corte" debe ser el día anterior a que el sistema empiece a controlar.',
            '     Ejemplo: si arranca en Enero 2026 → use 31/12/2025.',
            '  4. A partir de esa fecha el sistema acumulará días nuevos automáticamente.',
            '  5. Las columnas "Días Acumulados" y "Días Tomados" son solo para documentar —',
            '     NO afectan la importación. Use la columna de verificación: Acumulados − Tomados = Saldo.',
            '  6. Si el empleado NO tiene saldo inicial (ingresó después del sistema), deje en 0.',
        ]
        for i, line in enumerate(instructions):
            cell = ws.cell(row=last_row + i, column=1, value=line)
            cell.fill = inst_fill
            cell.font = inst_font
            ws.merge_cells(
                start_row=last_row + i, start_column=1,
                end_row=last_row + i,   end_column=len(cols)
            )


    def _build_overtime(self, wb, sample=False):
        cols = [
            ('Cédula Empleado',      True,  18, '1-2345-6789',    ''),
            ('Fecha',                True,  14, '01/02/2026',      'DD/MM/AAAA'),
            ('Tipo de Hora Extra',   True,  20, 'Simple (1.5x)',        'Ver CATALOGOS → overtime_type'),
            ('Cantidad de Horas',    True,  16, '2.5',             'Número de horas extras trabajadas'),
            ('Salario por Hora (₡)', False, 18, '3500',            'Salario mensual ÷ 240 (o según contrato)'),
            ('Monto Total (₡)',      False, 16, '8750',            'Horas × Salario × Factor (1.5 / 2.0)'),
            ('Período de Planilla',  False, 22, 'Febrero 2026',    'Período al que se carga esta hora extra'),
            ('Observaciones',        False, 28, '',                 ''),
        ]
        sv = [self._SAMPLE_CEDULA, '15/01/2024', 'Simple (1.5x)', '2',
              '2083', '6250', 'Enero 2024', '⚠️ PRUEBA'] if sample else None
        ws = wb.create_sheet('⏱️ HORAS EXTRAS')
        self._sheet_title(ws, 'HORAS EXTRAS — Registros históricos a importar', len(cols))
        self._build_rows(ws, cols, sample_values=sv)
        # col 3: Tipo de Hora Extra
        self._dv(ws, 3, 'overtime_type', 4, title='Tipo de Hora Extra')

    # ══════════════════════════════════════════════════════════════════════════
    # HOJA OCULTA DE LISTAS (fuente de los dropdowns)
    # ══════════════════════════════════════════════════════════════════════════
    # ══════════════════════════════════════════════════════════════════════════
    # HOJA EMBARGOS JUDICIALES
    # ══════════════════════════════════════════════════════════════════════════
    def _build_embargos(self, wb, sample=False):
        cols = [
            ('Cédula Empleado',        True,  18, '1-2345-6789',          'Cédula del empleado afectado'),
            ('N° Expediente Judicial', True,  24, '15-000456-0638-CI',    'Número del expediente del juzgado'),
            ('Juzgado / Tribunal',     True,  30, 'Juzgado Civil SJ',     'Nombre completo del juzgado'),
            ('Fecha de Resolución',    False, 16, '15/01/2024',           'DD/MM/AAAA'),
            ('Nombre del Acreedor',    True,  28, 'Empresa XYZ S.A.',     'Nombre del beneficiario del embargo'),
            ('IBAN del Acreedor',      False, 30, 'CR21015108010018023571','IBAN para girar el embargo (opcional)'),
            ('Tipo de Cálculo',        True,  22, 'Monto Fijo',           'Ver CATALOGOS → embargo_calc'),
            ('Monto Fijo (₡)',         False, 16, '50000',                'Si tipo = Monto Fijo'),
            ('Porcentaje (%)',          False, 12, '',                     'Si tipo = Porcentaje. Máx 25% (Art. 172 CT)'),
            ('Vigente Desde',          True,  14, '01/02/2024',           'DD/MM/AAAA'),
            ('Vigente Hasta',          False, 14, '',                     'Dejar vacío si no tiene vencimiento'),
            ('Observaciones',          False, 28, '',                     ''),
        ]
        sv = [self._SAMPLE_CEDULA, 'TEST-EMB-0000', 'Juzgado Prueba', '01/01/2024',
              'Acreedor Prueba', '', 'Monto Fijo', '10000', '',
              '01/01/2024', '', '⚠️ PRUEBA'] if sample else None
        ws = wb.create_sheet('⚖️ EMBARGOS')
        self._sheet_title(ws, 'EMBARGOS JUDICIALES — Art. 172 CT (máx. 25% del neto disponible)', len(cols))
        self._build_rows(ws, cols, data_rows=80, sample_values=sv)
        self._dv(ws, 7, 'embargo_calc', 4, title='Tipo de Cálculo')

    # ══════════════════════════════════════════════════════════════════════════
    # HOJA BONOS E INCENTIVOS
    # ══════════════════════════════════════════════════════════════════════════
    def _build_bonos(self, wb, sample=False):
        cols = [
            ('Cédula Empleado',        True,  18, '1-2345-6789',                    'Cédula del empleado'),
            ('Concepto / Nombre',      True,  28, 'Bono de Productividad Q1 2024',  'Nombre descriptivo del bono'),
            ('Tipo de Bono',           True,  28, 'Productividad / Rendimiento',    'Ver CATALOGOS → bono_type'),
            ('Tipo de Cálculo',        True,  18, 'Monto Fijo',                     'Monto Fijo / Porcentaje del Salario Base'),
            ('Monto Fijo (₡)',         False, 16, '25000',                          'Si tipo cálculo = Monto Fijo'),
            ('Porcentaje (%)',          False, 12, '',                               'Si tipo cálculo = Porcentaje'),
            ('Es Recurrente',          True,  14, 'Si',                             'Si = se aplica cada boleta / No = solo una vez'),
            ('Afecto CCSS',            True,  12, 'Si',                             'Si = suma a base CCSS (bonos salariales)'),
            ('Afecto Renta',           True,  12, 'Si',                             'Si = suma a base de renta'),
            ('Tope Exento (₡/mes)',    False, 16, '',                               'Solo para transporte (₡74 000/mes) o similar'),
            ('Vigente Desde',          True,  14, '01/01/2024',                     'DD/MM/AAAA'),
            ('Vigente Hasta',          False, 14, '',                               'Dejar vacío para aplicar indefinidamente'),
            ('Observaciones',          False, 30, 'Acuerdo de junta 2024',          ''),
        ]
        sv = [self._SAMPLE_CEDULA, 'Bono Prueba', 'Productividad / Rendimiento',
              'Monto Fijo', '5000', '', 'Si', 'Si', 'Si', '',
              '01/01/2024', '', '⚠️ PRUEBA'] if sample else None
        ws = wb.create_sheet('🎯 BONOS')
        self._sheet_title(ws, 'BONOS E INCENTIVOS — Aplican automáticamente en cada boleta', len(cols))
        self._build_rows(ws, cols, data_rows=100, sample_values=sv)
        self._dv(ws, 3, 'bono_type',        4, title='Tipo de Bono')
        self._dv(ws, 4, 'bono_calc',        4, title='Tipo de Cálculo')
        self._dv(ws, 7, 'si_no_recurrente', 4, title='¿Es Recurrente?')
        self._dv(ws, 8, 'si_no',            4, title='¿Afecto CCSS?')
        self._dv(ws, 9, 'si_no',            4, title='¿Afecto Renta?')

    # ══════════════════════════════════════════════════════════════════════════
    # HOJA CATÁLOGOS
    # ══════════════════════════════════════════════════════════════════════════
    def _build_catalogs(self, wb):
        C = self._C
        ws = wb.create_sheet('📚 CATALOGOS')
        ws.sheet_view.showGridLines = False

        ws.merge_cells('A1:C1')
        c = ws['A1']
        c.value     = 'CATÁLOGOS DE VALORES VÁLIDOS — ⚠️ No editar esta hoja'
        c.font      = self._font(bold=True, color=C['white'], size=12)
        c.fill      = self._fill(C['red_hdr'])
        c.alignment = self._center()
        ws.row_dimensions[1].height = 28

        CATALOGS = [
            ('id_type — Tipo de Identificación', [
                ('01',  'Cédula Nacional'),
                ('02',  'Residencia / DIMEX'),
                ('03',  'Permiso de Trabajo'),
                ('04',  'Pasaporte'),
                ('05',  'Indocumentado'),
            ]),
            ('employee_type — Tipo de Empleado (buscar por nombre exacto en Odoo)', [
                ('planilla',    'Ejemplo: nombre del tipo tal como aparece en Configuración → Tipos de Empleado'),
                ('contratado',  'Use el nombre exacto del registro en Odoo'),
            ]),
            ('employee_status — Estado del Empleado (buscar por nombre en Odoo)', [
                ('activo',      'Use el nombre exacto del estado en Configuración → Estados'),
            ]),
            ('schedule_type — Tipo de Horario (nombre exacto en Odoo)', [
                ('',  'Use el nombre del horario tal como aparece en Configuración → Tipos de Horario'),
            ]),
            ('frequency — Frecuencia de Pago', [
                ('Mensual',    'Mensual — 1 pago al mes'),
                ('Quincenal',  'Quincenal — 2 pagos al mes'),
                ('Semanal',    'Semanal — 4 pagos al mes'),
                ('Bimensual',  'Bimensual — cada 2 meses'),
            ]),
            ('ins_risk_class — Clase de Riesgo INS', [
                ('I',   'Clase I   — Oficinas y administrativo (~0.87%)'),
                ('II',  'Clase II  — Comercio (~1.49%)'),
                ('III', 'Clase III — Industria liviana (~2.47%)'),
                ('IV',  'Clase IV  — Construcción / riesgo alto (~4.13%)'),
                ('V',   'Clase V   — Actividades de alto riesgo (~6.88%)'),
            ]),
            ('ins_workday_type — Tipo de Jornada INS', [
                ('Ordinaria',      'Jornada diurna regular'),
                ('Extraordinaria', 'Horas extra autorizadas'),
                ('Mixta',          'Parte diurna y parte nocturna'),
                ('Tiempo Parcial', 'Menos de jornada completa'),
                ('Por Horas',      'Según horas efectivamente trabajadas'),
                ('Ocasional',      'Trabajo esporádico o temporal'),
            ]),
            ('ins_id_type — Tipo de ID INS', [
                ('01', 'Cédula de Costa Rica'),
                ('02', 'Residencia de Costa Rica / DIMEX'),
                ('03', 'Permiso de Trabajo'),
                ('04', 'Pasaporte'),
                ('05', 'Indocumentado'),
            ]),
            ('ins_civil_status — Estado Civil INS', [
                ('Soltero/a',    ''),
                ('Casado/a',     ''),
                ('Divorciado/a', ''),
                ('Viudo/a',      ''),
                ('Unión Libre',  ''),
                ('Separado/a',   ''),
            ]),
            ('ins_nationality — Nacionalidad INS', [
                ('CR', 'Costarricense'),
                ('NI', 'Nicaragüense'),
                ('CO', 'Colombiana'),
                ('US', 'Estadounidense'),
                ('HN', 'Hondureña'),
                ('SV', 'Salvadoreña'),
                ('GT', 'Guatemalteca'),
                ('PA', 'Panameña'),
                ('MX', 'Mexicana'),
                ('VE', 'Venezolana'),
                ('PE', 'Peruana'),
                ('EC', 'Ecuatoriana'),
                ('OT', 'Otra nacionalidad'),
            ]),
            ('account_type — Tipo de Cuenta Banco', [
                ('Cuenta Corriente', 'Cuenta corriente o IBAN'),
                ('Cuenta de Ahorros','Cuenta de ahorros'),
                ('SINPE Móvil',      'SINPE Móvil'),
            ]),
            ('bank — Banco', [
                ('BNCR',       'Banco Nacional de Costa Rica'),
                ('BCR',        'Banco de Costa Rica'),
                ('BP',         'Banco Popular y de Desarrollo Comunal'),
                ('BAC',        'BAC San José'),
                ('BCT',        'Banco BCT'),
                ('CATHAY',     'Banco Cathay de Costa Rica'),
                ('CMB',        'Banco CMB'),
                ('DAVIVIENDA', 'Banco Davivienda'),
                ('GENERAL',    'Banco General'),
                ('IMPROSA',    'Banco Improsa'),
                ('LAFISE',     'Banco La Fise'),
                ('PROMERICA',  'Banco Promerica'),
                ('PRIVAL',     'Prival Bank'),
                ('SCOTIA',     'Scotiabank'),
                ('COOCIQUE',   'Coocique R.L.'),
                ('COOPENAE',   'Coopenae R.L.'),
                ('MUTUAL_ALJ', 'Mutual Alajuela'),
                ('OTRO',       'Otro banco / cooperativa'),
            ]),
            ('loan_type — Tipo de Préstamo', [
                ('Préstamo de Empresa', 'Préstamo otorgado por la empresa'),
                ('Adelanto de Salario', 'Adelanto sobre el salario del período'),
            ]),
            ('loan_state — Estado del Préstamo', [
                ('Aprobado', 'Se activará en la próxima boleta'),
                ('En Curso', 'Descuento activo'),
                ('Borrador', 'Pendiente de aprobación'),
                ('Pagado',   'Totalmente cancelado'),
                ('Anulado',  'Préstamo anulado'),
            ]),
            ('pension_relacion — Relación Beneficiario', [
                ('Hijo/a',   ''),
                ('Cónyuge',  'Cónyuge / Conviviente'),
                ('Padre',    ''),
                ('Madre',    ''),
                ('Otro',     ''),
            ]),
            ('pension_calc — Tipo de Cálculo Pensión', [
                ('Porcentaje del Salario', 'Porcentaje del salario bruto'),
                ('Monto Fijo',             'Monto fijo mensual en colones'),
            ]),
            ('benefit_type — Tipo de Descuento/Deducción Recurrente', [
                ('Beneficio / Ingreso',    'Suma al salario bruto (ej: plus informal no cubierto por BONOS)'),
                ('Deducción / Descuento',  'Resta del salario neto (ej: cuota sindical, cooperativa, ahorro)'),
            ]),
            ('amount_type — Tipo de Monto', [
                ('Monto Fijo',  'Monto fijo en colones'),
                ('Porcentaje',  'Porcentaje del salario base'),
            ]),
            ('disability_type — Tipo de Incapacidad', [
                ('Enfermedad Común (CCSS)',     'Enfermedad o accidente no laboral'),
                ('Accidente de Trabajo (CCSS)', 'Accidente en el lugar de trabajo'),
                ('Riesgo Laboral (INS)',         'Cubierto por póliza INS'),
                ('Maternidad / Paternidad',      'Licencia pre/post natal'),
                ('Otro',                         'Otro tipo de incapacidad'),
            ]),
            ('overtime_type — Tipo de Hora Extra', [
                ('Simple (1.5x)', 'Hora extra ordinaria — factor 1.5'),
                ('Doble (2.0x)',  'Hora extra nocturna o dominical — factor 2.0'),
                ('Día Feriado',   'Trabajo en día feriado nacional'),
            ]),
            ('embargo_calc — Tipo de Cálculo Embargo', [
                ('Monto Fijo',                  'Monto fijo en colones (₡) cada período'),
                ('Porcentaje del Neto Disponible', 'Porcentaje del neto (bruto − CCSS − renta − pensiones). Máx 25% Art. 172 CT'),
            ]),
            ('bono_type — Tipo de Bono', [
                ('Productividad / Rendimiento',          'Afecto CCSS y Renta — integra salario para aguinaldo/cesantía'),
                ('Asistencia Perfecta',                   'Afecto CCSS y Renta — integra salario para aguinaldo/cesantía'),
                ('Antigüedad por Años de Servicio',       'Afecto CCSS y Renta — integra salario para aguinaldo/cesantía'),
                ('Subsidio de Transporte / Kilometraje',  'Exento CCSS/Renta hasta ₡74 000/mes (Reglamento 2023)'),
                ('Subsidio de Alimentación (en dinero)',  'Afecto CCSS y Renta si se paga en dinero'),
                ('Subsidio Educativo',                    'Generalmente exento según convenio colectivo'),
                ('Subsidio de Salud / Médico',            'Exento CCSS (Art. 5 Ley 7983) si es póliza médica'),
                ('Gastos de Representación',              'Exento CCSS si están debidamente documentados'),
                ('Comisión por Ventas',                   'Afecto CCSS y Renta — integra salario'),
                ('Incentivo / Premio Especial',           'Afecto CCSS y Renta'),
                ('Otro',                                  'Consulte con su contador el tratamiento fiscal'),
            ]),
            ('calc_method — Método de Cálculo de Planilla', [
                ('Salario Fijo',         'Sin consultar asistencias'),
                ('Por Horas Trabajadas', 'Según módulo de asistencias'),
            ]),
        ]

        row = 3
        for titulo, valores in CATALOGS:
            # Encabezado de sección
            ws.merge_cells(start_row=row, start_column=1,
                           end_row=row,   end_column=3)
            c = ws.cell(row, 1, value=titulo)
            c.font      = self._font(bold=True, color=C['white'], size=10)
            c.fill      = self._fill(C['med'])
            c.border    = self._border()
            c.alignment = self._left()
            for ci in (2, 3):
                ws.cell(row, ci).fill   = self._fill(C['med'])
                ws.cell(row, ci).border = self._border()
            ws.row_dimensions[row].height = 20
            row += 1

            # Sub-encabezado
            for ci, hdr in enumerate(['Valor a usar (exacto)', 'Descripción'], 1):
                c = ws.cell(row, ci, value=hdr)
                c.font      = self._font(bold=True, color=C['dark'], size=9)
                c.fill      = self._fill(C['light'])
                c.border    = self._border()
                c.alignment = self._center()
            ws.row_dimensions[row].height = 16
            row += 1

            # Valores
            for val, desc in valores:
                cv = ws.cell(row, 1, value=val)
                cv.font      = self._font(bold=True, color='00008B', size=10)
                cv.fill      = self._fill(C['opt'])
                cv.border    = self._border()
                cv.alignment = self._left()

                cd = ws.cell(row, 2, value=desc)
                cd.font      = self._font(size=10)
                cd.fill      = self._fill(C['opt'])
                cd.border    = self._border()
                cd.alignment = self._left()
                ws.row_dimensions[row].height = 15
                row += 1

            row += 1  # separador

        ws.column_dimensions['A'].width = 24
        ws.column_dimensions['B'].width = 52
        ws.protection.sheet    = True
        ws.protection.password = 'planillacr2026'

    # ══════════════════════════════════════════════════════════════════════════
    # ACCIÓN PRINCIPAL
    # ══════════════════════════════════════════════════════════════════════════
    def action_generate(self):
        self.ensure_one()

        wb = Workbook()
        s = self.include_sample_data

        # Hoja de listas — debe crearse ANTES de las demás hojas
        _, static_cols = self._build_listas_sheet(wb)

        # Listas dinámicas desde la BD (catálogos que varían por empresa)
        dyn_lists = self._build_dynamic_lists(wb, self.company_id, static_cols)

        # Instrucciones siempre presentes
        self._build_instructions(wb)

        if self.include_employees:
            self._build_employees(wb, sample=s, dyn_lists=dyn_lists)
        if self.include_loans:
            self._build_loans(wb, sample=s)
        if self.include_pension:
            self._build_pension(wb, sample=s)
        if self.include_benefits:
            self._build_benefits(wb, sample=s)
        if self.include_disabilities:
            self._build_disabilities(wb, sample=s)
        if self.include_vacations:
            self._build_vacations(wb, sample=s)
        if self.include_overtime:
            self._build_overtime(wb, sample=s)
        if self.include_embargos:
            self._build_embargos(wb, sample=s)
        if self.include_bonos:
            self._build_bonos(wb, sample=s)

        self._build_catalogs(wb)

        # Serializar a bytes
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        file_data = base64.b64encode(buf.read())

        # Guardar como attachment y devolver descarga
        company_slug = self.company_id.name.replace(' ', '_')[:20]
        filename     = f'Machote_Planilla_{company_slug}_v54.xlsx'

        att = self.env['ir.attachment'].create({
            'name':     filename,
            'type':     'binary',
            'datas':    file_data,
            'res_model': self._name,
            'res_id':    self.id,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })

        return {
            'type':   'ir.actions.act_url',
            'url':    f'/web/content/{att.id}?download=true',
            'target': 'self',
        }
