from odoo import models, fields, api
from odoo.exceptions import ValidationError
import math
from datetime import date, timedelta


class HrEmployeeExtension(models.Model):
    _inherit = 'hr.employee'

    # ── Método de cálculo de planilla ───────────────────────────────
    payroll_calculation_method = fields.Selection([
        ('fixed', 'Salario Fijo (sin consultar asistencias)'),
        ('attendance', 'Por Horas Trabajadas (según módulo de Asistencias)'),
    ], string='Método de Cálculo de Planilla',
        default='fixed', required=True, tracking=True,
        help='Define cómo se calcula el salario base en cada boleta de pago:\n'
             '- Salario Fijo: usa siempre el salario base configurado.\n'
             '- Por Horas Trabajadas: calcula el pago según las horas '
             'registradas en el módulo de Asistencias durante el período.'
    )

    # ── Identificación ──────────────────────────────────────────────
    identification_type_id = fields.Many2one(
        'planilla.identification.type', string='Tipo de Identificación'
    )

    # ── Clasificación ───────────────────────────────────────────────
    employee_status_id = fields.Many2one(
        'planilla.employee.status', string='Estado del Empleado',
        tracking=True
    )
    employee_type_id = fields.Many2one(
        'planilla.employee.type', string='Tipo de Empleado', tracking=True
    )
    branch_id = fields.Many2one(
        'planilla.branch', string='Sucursal', tracking=True
    )
    schedule_type_id = fields.Many2one(
        'planilla.schedule.type', string='Tipo de Horario'
    )
    payroll_calendar_id = fields.Many2one(
        'planilla.calendar', string='Calendarización de Planilla'
    )

    # ── Datos salariales ────────────────────────────────────────────
    base_salary = fields.Monetary(
        string='Salario Base', currency_field='currency_id', tracking=True
    )
    currency_id = fields.Many2one(
        'res.currency',
        string='Moneda'
    )
    salary_effective_date = fields.Date(string='Fecha Vigencia Salarial')

    # ── Datos INS - Riesgos del Trabajo ────────────────────────────
    ins_include = fields.Boolean(
        string='Incluir en Planilla INS',
        default=True,
    )
    ins_policy_number = fields.Char(string='Número de Póliza INS')

    # Nombre separado para INS
    ins_first_name = fields.Char(string='Nombre (INS)')
    ins_first_lastname = fields.Char(string='Primer Apellido (INS)')
    ins_second_lastname = fields.Char(string='Segundo Apellido (INS)')

    ins_id_type = fields.Selection([
        ('01', 'Cédula de Costa Rica'),
        ('02', 'Residencia de Costa Rica'),
        ('03', 'Permiso de Trabajo'),
        ('04', 'Pasaporte'),
        ('05', 'Indocumentado'),
    ], string='Tipo de Identificación (INS)', default='01',
       help='Tipos de identificación según formulario INS Planilla de Riesgos del Trabajo. Fuente: INS Costa Rica (ins-cr.com).')

    ins_nationality = fields.Selection([
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
        ('CU', 'Cubana'),
        ('DO', 'Dominicana'),
        ('BO', 'Boliviana'),
        ('PY', 'Paraguaya'),
        ('UY', 'Uruguaya'),
        ('AR', 'Argentina'),
        ('CL', 'Chilena'),
        ('BR', 'Brasileña'),
        ('ES', 'Española'),
        ('IT', 'Italiana'),
        ('DE', 'Alemana'),
        ('FR', 'Francesa'),
        ('CN', 'China'),
        ('IN', 'India'),
        ('PH', 'Filipina'),
        ('OT', 'Otra'),
    ], string='Nacionalidad (INS)', default='CR',
       help='Basado en el formulario oficial de Planilla INS para Riesgos del Trabajo. Fuente: INS Costa Rica.')

    ins_civil_status = fields.Selection([
        ('01', 'Soltero/a'),
        ('02', 'Casado/a'),
        ('03', 'Divorciado/a'),
        ('04', 'Viudo/a'),
        ('05', 'Unión Libre'),
        ('06', 'Separado/a'),
    ], string='Estado Civil (INS)', default='01',
       help='Estados civiles según formulario INS Planilla de Riesgos del Trabajo. Fuente: INS Costa Rica.')

    ins_workday_type = fields.Selection([
        ('01', 'Ordinaria'),
        ('02', 'Extraordinaria'),
        ('03', 'Mixta'),
        ('04', 'Tiempo Parcial'),
        ('05', 'Por Horas'),
        ('06', 'Ocasional'),
    ], string='Tipo de Jornada (INS)', default='01',
       help='Tipos de jornada según Código de Trabajo de Costa Rica y formulario INS. Fuente: Código de Trabajo, Ley N.° 2 y formulario INS.')

    ins_occupation = fields.Selection([
        ('1111', '[1111] Miembros del poder legislativo y ejecutivo'),
        ('1112', '[1112] Personal directivo de la administración pública'),
        ('1113', '[1113] Jefes de comunidades étnicas'),
        ('1114', '[1114] Dirigentes de organizaciones que presentan un interés especial'),
        ('1120', '[1120] Directores y gerentes generales'),
        ('1211', '[1211] Directores y gerentes de servicios financieros'),
        ('1212', '[1212] Directores y gerentes de recursos humanos'),
        ('1213', '[1213] Directores y gerentes de políticas y planificación'),
        ('1219', '[1219] Directores y gerentes de administración y servicios no clasificados bajo otros'),
        ('1221', '[1221] Directores y gerentes de venta y comercialización'),
        ('1222', '[1222] Directores y gerentes de publicidad y relaciones públicas'),
        ('1223', '[1223] Directores y gerentes de investigación y desarrollo'),
        ('1311', '[1311] Directores y gerentes de producción agropecuaria y silvicultura'),
        ('1312', '[1312] Directores y gerentes de producción acuícola, piscícola y de pesca'),
        ('1321', '[1321] Directores y gerentes de industrias manufactureras'),
        ('1322', '[1322] Directores y gerentes de explotaciones de minería'),
        ('1323', '[1323] Directores y gerentes de empresas de construcción'),
        ('1324', '[1324] Directores y gerentes de empresas de abastecimiento, distribución y afines'),
        ('1330', '[1330] Directores y gerentes de servicios de tecnología de la información y las comunicaciones'),
        ('1341', '[1341] Directores y gerentes de servicios de cuidados infantiles'),
        ('1342', '[1342] Directores y gerentes de servicios de salud'),
        ('1343', '[1343] Directores y gerentes de servicios de atención a personas adultas mayores'),
        ('1344', '[1344] Directores y gerentes de servicios de bienestar social'),
        ('1345', '[1345] Directores y gerentes de servicios de educación'),
        ('1346', '[1346] Directores y gerentes de sucursales de bancos, de servicios financieros y de seguros'),
        ('1349', '[1349] Directores y gerentes de servicios profesionales no clasificados bajo otros'),
        ('1411', '[1411] Directores y gerentes de hoteles'),
        ('1412', '[1412] Directores y gerentes de restaurantes'),
        ('1420', '[1420] Gerentes de comercios al por mayor y al por menor'),
        ('1431', '[1431] Directores y gerentes de centros deportivos, de esparcimiento y culturales'),
        ('1439', '[1439] Directores y gerentes de servicios no clasificados bajo otros epígrafes'),
        ('2111', '[2111] Físicos y astrónomos'),
        ('2112', '[2112] Meteorólogos'),
        ('2113', '[2113] Químicos'),
        ('2114', '[2114] Geólogos y geofísicos'),
        ('2120', '[2120] Matemáticos, actuarios y estadísticos'),
        ('2131', '[2131] Biólogos, botánicos, zoólogos y afines'),
        ('2132', '[2132] Agrónomos, zootecnistas y afines'),
        ('2133', '[2133] Profesionales de la protección medioambiental'),
        ('2141', '[2141] Ingenieros industriales y de producción'),
        ('2142', '[2142] Ingenieros civiles'),
        ('2143', '[2143] Ingenieros medioambientales'),
        ('2144', '[2144] Ingenieros mecánicos, navales y aeronáuticos'),
        ('2145', '[2145] Ingenieros químicos'),
        ('2146', '[2146] Ingenieros de minas, metalúrgicos y afines'),
        ('2149', '[2149] Ingenieros no clasificados bajo otros epígrafes'),
        ('2151', '[2151] Ingenieros eléctricos'),
        ('2152', '[2152] Ingenieros electrónicos'),
        ('2153', '[2153] Ingenieros en telecomunicaciones, audio y sonido'),
        ('2161', '[2161] Arquitectos'),
        ('2162', '[2162] Arquitectos paisajistas'),
        ('2163', '[2163] Diseñadores industriales de productos y moda'),
        ('2164', '[2164] Urbanistas e ingenieros de tránsito'),
        ('2165', '[2165] Topógrafos'),
        ('2166', '[2166] Diseñadores gráficos y multimedia'),
        ('2211', '[2211] Médicos generales'),
        ('2212', '[2212] Médicos geriatras'),
        ('2213', '[2213] Médicos ginecólogos y obstetras'),
        ('2214', '[2214] Médicos psiquiatras'),
        ('2215', '[2215] Médicos ortopedistas y traumatólogos'),
        ('2219', '[2219] Especialistas médicos no clasificados bajo otros epígrafes'),
        ('2220', '[2220] Enfermeros profesionales y profesionales de partería'),
        ('2230', '[2230] Profesionales de medicina tradicional y alternativa'),
        ('2250', '[2250] Veterinarios'),
        ('2261', '[2261] Dentistas'),
        ('2262', '[2262] Cirujanos orales y maxilofaciales'),
        ('2271', '[2271] Farmacéuticos'),
        ('2272', '[2272] Profesionales de la salud y la higiene laboral y ambiental'),
        ('2273', '[2273] Fisioterapeutas'),
        ('2274', '[2274] Nutricionistas'),
        ('2275', '[2275] Audiólogos y terapeutas del lenguaje'),
        ('2276', '[2276] Optometristas'),
        ('2279', '[2279] Profesionales de la salud no clasificados bajo otros epígrafes'),
        ('2310', '[2310] Profesores de instituciones de educación superior'),
        ('2320', '[2320] Profesores de formación profesional'),
        ('2330', '[2330] Profesores de educación secundaria'),
        ('2341', '[2341] Profesores de educación primaria'),
        ('2342', '[2342] Profesores de educación preescolar'),
        ('2351', '[2351] Especialistas en métodos pedagógicos'),
        ('2352', '[2352] Profesores de educación especial'),
        ('2353', '[2353] Otros profesores de idiomas'),
        ('2354', '[2354] Otros profesores de música'),
        ('2355', '[2355] Otros profesores de artes'),
        ('2356', '[2356] Instructores en tecnología de la información'),
        ('2359', '[2359] Profesionales de la educación no clasificados bajo otros epígrafes'),
        ('2411', '[2411] Contadores y auditores financieros'),
        ('2412', '[2412] Asesores financieros y en inversiones'),
        ('2413', '[2413] Analistas financieros'),
        ('2421', '[2421] Analistas de gestión y organización'),
        ('2422', '[2422] Profesionales en políticas sociales y de administración'),
        ('2423', '[2423] Profesionales de gestión de talento humano'),
        ('2424', '[2424] Profesionales en formación, desarrollo de personal y evaluación de competencias'),
        ('2431', '[2431] Profesionales de la publicidad y la comercialización'),
        ('2432', '[2432] Profesionales de relaciones públicas'),
        ('2433', '[2433] Profesionales de ventas técnicas y médicas (excluyendo las TIC)'),
        ('2434', '[2434] Profesionales de ventas de tecnología de la información y las comunicaciones'),
        ('2511', '[2511] Analistas de sistemas'),
        ('2512', '[2512] Desarrolladores de software'),
        ('2513', '[2513] Desarrolladores web y multimedia'),
        ('2514', '[2514] Programadores de aplicaciones'),
        ('2519', '[2519] Desarrolladores y analistas de software y multimedia no clasificados bajo otros'),
        ('2521', '[2521] Diseñadores y administradores de bases de datos'),
        ('2522', '[2522] Administradores de sistemas'),
        ('2523', '[2523] Profesionales en redes de computadores'),
        ('2529', '[2529] Profesionales en bases de datos y en redes de computadores no clasificados bajo otros'),
        ('2611', '[2611] Abogados'),
        ('2612', '[2612] Jueces'),
        ('2619', '[2619] Profesionales en derecho no clasificados bajo otros epígrafes'),
        ('2621', '[2621] Archivistas, curadores de arte y restauradores'),
        ('2622', '[2622] Bibliotecólogos, documentalistas y afines'),
        ('2631', '[2631] Economistas'),
        ('2632', '[2632] Sociólogos, antropólogos y afines'),
        ('2633', '[2633] Filósofos, historiadores y especialistas en ciencias políticas'),
        ('2634', '[2634] Psicólogos'),
        ('2635', '[2635] Profesionales del trabajo social'),
        ('2636', '[2636] Profesionales religiosos'),
        ('2639', '[2639] Profesionales en ciencias sociales no clasificados bajo otros epígrafes'),
        ('2641', '[2641] Autores literarios y otros escritores'),
        ('2642', '[2642] Periodistas, editores y redactores'),
        ('2643', '[2643] Traductores, intérpretes, lingüistas y filólogos'),
        ('2651', '[2651] Escultores, pintores artísticos y afines'),
        ('2652', '[2652] Músicos, cantantes y compositores'),
        ('2653', '[2653] Coreógrafos, directores de danza y bailarines profesionales'),
        ('2654', '[2654] Directores y productores de cine, de teatro y afines'),
        ('2655', '[2655] Actores'),
        ('3111', '[3111] Técnicos en ciencias físicas y químicas'),
        ('3112', '[3112] Técnicos en ingeniería civil'),
        ('3113', '[3113] Electrotécnicos'),
        ('3114', '[3114] Técnicos en electrónica'),
        ('3115', '[3115] Técnicos en ingeniería mecánica'),
        ('3116', '[3116] Técnicos en química industrial'),
        ('3117', '[3117] Técnicos en ingeniería de minas y metalurgia'),
        ('3118', '[3118] Delineantes y dibujantes técnicos'),
        ('3119', '[3119] Otros técnicos en ciencias físicas, químicas, ingeniería y arquitectura no clasificados'),
        ('3121', '[3121] Supervisores en ingeniería de minas'),
        ('3122', '[3122] Supervisores en industrias manufactureras'),
        ('3123', '[3123] Supervisores de la construcción'),
        ('3131', '[3131] Operadores de plantas de generación y distribución de energía'),
        ('3132', '[3132] Operadores de incineradores, instalaciones de tratamiento de agua y afines'),
        ('3133', '[3133] Controladores de instalaciones de procesamiento de productos químicos'),
        ('3134', '[3134] Operadores de instalaciones de refinación de petróleo y gas natural'),
        ('3135', '[3135] Controladores de procesos de producción de metales'),
        ('3139', '[3139] Técnicos en control de procesos no clasificados bajo otros epígrafes'),
        ('3141', '[3141] Técnicos en ciencias biológicas (excluyendo la medicina)'),
        ('3142', '[3142] Técnicos agropecuarios'),
        ('3143', '[3143] Técnicos forestales'),
        ('3151', '[3151] Maquinistas en navegación marítima'),
        ('3152', '[3152] Capitanes y oficiales de cubierta'),
        ('3153', '[3153] Pilotos de aviación y afines'),
        ('3154', '[3154] Controladores de tráfico aéreo'),
        ('3155', '[3155] Técnicos en seguridad aeronáutica'),
        ('3156', '[3156] Controladores de tráfico marítimo'),
        ('3211', '[3211] Técnicos en aparatos de diagnóstico y tratamiento médico'),
        ('3212', '[3212] Técnicos de laboratorios médicos'),
        ('3213', '[3213] Técnicos y asistentes en farmacia'),
        ('3214', '[3214] Técnicos de prótesis médicas y dentales'),
        ('3220', '[3220] Profesionales de nivel medio de enfermería'),
        ('3230', '[3230] Profesionales de nivel medio de medicina tradicional y alternativa'),
        ('3240', '[3240] Técnicos y asistentes veterinarios'),
        ('3250', '[3250] Técnico en emergencias médicas'),
        ('3261', '[3261] Auxiliares y técnicos de odontología'),
        ('3262', '[3262] Técnicos en documentación sanitaria'),
        ('3263', '[3263] Trabajadores comunitarios de la salud'),
        ('3264', '[3264] Técnicos en optometría y ópticos'),
        ('3265', '[3265] Técnicos y asistentes fisioterapeutas'),
        ('3266', '[3266] Practicantes y asistentes médicos'),
        ('3267', '[3267] Inspectores de la salud laboral, medioambiental y afines'),
        ('3268', '[3268] Auxiliar de ambulancias en emergencias médicas'),
        ('3269', '[3269] Técnicos de las ciencias de la salud no clasificado bajo otros epígrafes'),
        ('3311', '[3311] Agentes de bolsa, cambio y otros servicios financieros'),
        ('3312', '[3312] Oficiales de préstamos y créditos'),
        ('3313', '[3313] Técnicos y auxiliares de contabilidad'),
        ('3314', '[3314] Profesionales de nivel medio de servicios estadísticos, matemáticos y afines'),
        ('3315', '[3315] Tasadores'),
        ('3316', '[3316] Técnicos y asistentes en administración y en economía'),
        ('3321', '[3321] Agentes de seguros'),
        ('3322', '[3322] Representantes comerciales'),
        ('3323', '[3323] Agentes de proveeduría'),
        ('3324', '[3324] Agentes de compras y consignatarios'),
        ('3331', '[3331] Declarantes o gestores de aduana'),
        ('3332', '[3332] Organizadores de conferencias y eventos'),
        ('3333', '[3333] Agentes de empleo y contratistas de mano de obra'),
        ('3334', '[3334] Agentes inmobiliarios'),
        ('3339', '[3339] Otros agentes comerciales y corredores no clasificados bajo otros epígrafes'),
        ('3341', '[3341] Supervisores de oficina'),
        ('3342', '[3342] Secretarios jurídicos'),
        ('3343', '[3343] Secretarios administrativos y ejecutivos'),
        ('3344', '[3344] Secretarios médicos'),
        ('3351', '[3351] Inspectores de aduanas y fronteras'),
        ('3352', '[3352] Agentes de administración tributaria'),
        ('3353', '[3353] Agentes de servicios de seguridad social'),
        ('3354', '[3354] Funcionarios de servicios de expedición de licencias y permisos'),
        ('3355', '[3355] Inspectores de policía y detectives'),
        ('3359', '[3359] Agentes de la administración pública para la aplicación de la ley y afines no clasificados'),
        ('3411', '[3411] Profesionales de nivel medio del derecho, servicios legales y afines'),
        ('3412', '[3412] Técnicos y asistentes en trabajo social'),
        ('3413', '[3413] Auxiliares laicos de las religiones'),
        ('3421', '[3421] Atletas y deportistas'),
        ('3422', '[3422] Entrenadores, instructores y árbitros de actividades deportivas'),
        ('3423', '[3423] Instructores de educación física y actividades recreativas'),
        ('3431', '[3431] Fotógrafos'),
        ('3432', '[3432] Diseñadores y decoradores de interior'),
        ('3433', '[3433] Técnicos en galerías de arte, museos y bibliotecas'),
        ('3435', '[3435] Otros profesionales de nivel medio en actividades culturales y artísticas'),
        ('3511', '[3511] Técnicos en operaciones de tecnología de la información y las comunicaciones'),
        ('3512', '[3512] Técnicos en asistencia al usuario de tecnología de la información y las comunicaciones'),
        ('3513', '[3513] Técnicos en redes y sistemas de computadores'),
        ('3514', '[3514] Técnicos de la web'),
        ('3521', '[3521] Técnicos de radiodifusión y grabación audiovisual'),
        ('3522', '[3522] Técnicos de ingeniería de las telecomunicaciones'),
        ('3610', '[3610] Profesionales de nivel medio de la enseñanza'),
        ('3711', '[3711] Técnicos y asistentes en relaciones públicas y publicidad'),
        ('3712', '[3712] Técnicos y asistentes en sociología, antropología, arqueología, geografía y afines'),
        ('3713', '[3713] Técnicos y asistentes en filosofía, historia y politología'),
        ('3714', '[3714] Técnicos y asistentes en filología y lingüística y en traducción'),
        ('3715', '[3715] Técnicos y asistentes en psicología'),
        ('3716', '[3716] Técnicos en periodismo y locución'),
        ('3719', '[3719] Técnicos y asistentes en ciencias sociales no clasificados bajo otros epígrafes'),
        ('4110', '[4110] Oficinistas generales'),
        ('4120', '[4120] Secretarios generales'),
        ('4131', '[4131] Operadores de máquinas de procesamiento de texto y mecanógrafos'),
        ('4132', '[4132] Digitadores de datos'),
        ('4211', '[4211] Cajeros de bancos y afines'),
        ('4212', '[4212] Receptores de apuestas y afines'),
        ('4213', '[4213] Prestamistas'),
        ('4214', '[4214] Cobradores y afines'),
        ('4221', '[4221] Recepcionistas'),
        ('4222', '[4222] Empleados de atención y asesoramiento de llamadas'),
        ('4223', '[4223] Telefonistas'),
        ('4227', '[4227] Entrevistadores de encuestas y de investigaciones de mercados'),
        ('4229', '[4229] Empleados de servicios de información al cliente no clasificados bajo otros'),
        ('4311', '[4311] Empleados de contabilidad y cálculo de costos'),
        ('4312', '[4312] Empleados de servicios estadísticos, financieros y de seguros'),
        ('4313', '[4313] Empleados encargados de las nóminas'),
        ('4321', '[4321] Empleados de control de abastecimientos e inventario'),
        ('4322', '[4322] Empleados de servicios de apoyo a la producción'),
        ('4323', '[4323] Empleados de servicio de transporte'),
        ('4411', '[4411] Empleados de bibliotecas'),
        ('4412', '[4412] Empleados de servicios de correos'),
        ('4413', '[4413] Codificadores de datos, correctores de pruebas de imprenta y afines'),
        ('4414', '[4414] Escribientes públicos y afines'),
        ('4415', '[4415] Empleados de archivos'),
        ('4416', '[4416] Empleados del servicio de personal'),
        ('4419', '[4419] Personal de apoyo administrativo no clasificado bajo otros epígrafes'),
        ('5111', '[5111] Auxiliares de servicio abordo'),
        ('5112', '[5112] Revisores y cobradores de los transportes públicos'),
        ('5113', '[5113] Guías turísticos'),
        ('5120', '[5120] Cocineros'),
        ('5131', '[5131] Saloneros'),
        ('5132', '[5132] Bartenders'),
        ('5141', '[5141] Especialistas en tratamientos del cabello'),
        ('5142', '[5142] Especialistas en tratamientos de belleza estética y afines'),
        ('5151', '[5151] Supervisores limpieza en oficinas, hoteles y otros establecimientos'),
        ('5152', '[5152] Ecónomos y mayordomos'),
        ('5153', '[5153] Encargados de mantenimiento de edificios'),
        ('5161', '[5161] Astrólogos, adivinadores y afines'),
        ('5162', '[5162] Acompañantes y ayudantes de cámara'),
        ('5163', '[5163] Personal de servicios funerarios y embalsamadores (excepto sepultureros)'),
        ('5164', '[5164] Cuidadores y entrenadores de animales'),
        ('5165', '[5165] Instructores de manejo'),
        ('5168', '[5168] Trabajadores de servicios sexuales'),
        ('5169', '[5169] Otros trabajadores de servicios personales'),
        ('5170', '[5170] Propietarios y comerciantes encargados de pequeños establecimientos de servicios'),
        ('5211', '[5211] Vendedores de quioscos y de puestos de mercado'),
        ('5212', '[5212] Vendedores ambulantes de productos comestibles'),
        ('5213', '[5213] Vendedores ambulantes (excluyendo de comida para consumo inmediato)'),
        ('5221', '[5221] Propietarios y comerciantes encargados de pequeñas tiendas y otros tipos de establecimientos'),
        ('5222', '[5222] Supervisores de tiendas y almacenes'),
        ('5223', '[5223] Asistentes de ventas de tiendas y almacenes'),
        ('5230', '[5230] Cajeros y expendedores de boletos y tiquetes'),
        ('5241', '[5241] Modelos de moda, arte y publicidad'),
        ('5242', '[5242] Demostradores de tiendas'),
        ('5243', '[5243] Vendedores puerta a puerta'),
        ('5244', '[5244] Vendedores por teléfono'),
        ('5245', '[5245] Expendedores de gasolineras'),
        ('5246', '[5246] Vendedores de comidas al mostrador'),
        ('5249', '[5249] Vendedores no clasificados bajo otros epígrafes'),
        ('5311', '[5311] Cuidadores de niños'),
        ('5312', '[5312] Ayudantes de maestros'),
        ('5321', '[5321] Trabajadores de los cuidados personales en instituciones'),
        ('5322', '[5322] Trabajadores de los cuidados personales a domicilio'),
        ('5329', '[5329] Otros trabajadores de los cuidados personales en servicios de salud'),
        ('5411', '[5411] Bomberos'),
        ('5412', '[5412] Policías e inspectores de tránsito'),
        ('5413', '[5413] Guardianes de prisión'),
        ('5414', '[5414] Guardas de protección en establecimientos'),
        ('5415', '[5415] Vigilante de casas particulares'),
        ('5419', '[5419] Otros trabajadores que prestan servicios de protección y vigilancia'),
        ('6111', '[6111] Agricultores y trabajadores calificados de tubérculos, cereales, frutas, plantas'),
        ('6112', '[6112] Agricultores y trabajadores calificados de plantaciones de árboles y arbustos'),
        ('6113', '[6113] Agricultores y trabajadores calificados de jardines, hortalizas, follajes y otros'),
        ('6114', '[6114] Agricultores y trabajadores calificados de cultivos mixtos'),
        ('6121', '[6121] Criadores de ganado'),
        ('6122', '[6122] Avicultores y trabajadores calificados de la avicultura'),
        ('6123', '[6123] Apicultores y sericultores y trabajadores calificados de la apicultura y la sericicultura'),
        ('6129', '[6129] Otros criadores y trabajadores calificados de la cría de animales no incluidos'),
        ('6130', '[6130] Productores y trabajadores calificados de explotaciones agropecuarias mixtas'),
        ('6210', '[6210] Trabajadores forestales calificados y afines'),
        ('6221', '[6221] Trabajadores de explotaciones de acuicultura'),
        ('6222', '[6222] Pescadores de agua dulce y en aguas costeras'),
        ('6223', '[6223] Pescadores de alta mar'),
        ('6224', '[6224] Cazadores y tramperos'),
        ('6310', '[6310] Trabajadores agrícolas de subsistencia'),
        ('6320', '[6320] Trabajadores pecuarios de subsistencia'),
        ('6330', '[6330] Trabajadores agropecuarios de subsistencia'),
        ('6340', '[6340] Pescadores, cazadores, tramperos y recolectores de subsistencia'),
        ('7111', '[7111] Albañiles'),
        ('7112', '[7112] Mamposteros, tronzadores, labrantes y grabadores de piedra'),
        ('7113', '[7113] Operarios en cemento armado, encofradores y afines'),
        ('7114', '[7114] Carpinteros de armar y de obra blanca'),
        ('7119', '[7119] Operarios de la construcción en obra gruesa y afines no clasificados bajo otros'),
        ('7121', '[7121] Techadores'),
        ('7122', '[7122] Revestidores e instaladores de pisos'),
        ('7123', '[7123] Revocadores'),
        ('7124', '[7124] Instaladores de material aislante y de insonorización'),
        ('7125', '[7125] Cristaleros'),
        ('7126', '[7126] Fontaneros e instaladores de tuberías'),
        ('7127', '[7127] Mecánicos de instalaciones de refrigeración y aire acondicionado'),
        ('7131', '[7131] Pintores y empapeladores'),
        ('7132', '[7132] Barnizadores, pintores de vehículos y afines'),
        ('7133', '[7133] Limpiadores de fachadas y deshollinadores'),
        ('7211', '[7211] Moldeadores de metal'),
        ('7212', '[7212] Soldadores y oxicortadores'),
        ('7213', '[7213] Chapistas y caldereros'),
        ('7214', '[7214] Montadores de estructuras metálicas'),
        ('7215', '[7215] Aparejadores y empalmadores de cables'),
        ('7221', '[7221] Herreros y forjadores'),
        ('7222', '[7222] Herramentistas y afines'),
        ('7223', '[7223] Reguladores y operadores de máquinas herramientas'),
        ('7224', '[7224] Pulidores de metales y afiladores de herramientas'),
        ('7231', '[7231] Mecánicos y reparadores de vehículos de motor'),
        ('7232', '[7232] Mecánicos y reparadores de motores de avión'),
        ('7233', '[7233] Mecánicos y reparadores de máquinas agrícolas e industriales'),
        ('7234', '[7234] Reparadores de bicicletas y afines'),
        ('7311', '[7311] Mecánicos y reparadores de instrumentos de precisión'),
        ('7312', '[7312] Fabricantes y afinadores de instrumentos musicales'),
        ('7313', '[7313] Joyeros, orfebres y plateros'),
        ('7314', '[7314] Alfareros y afines (barro, arcilla y abrasivos)'),
        ('7315', '[7315] Sopladores, modeladores, laminadores, cortadores y pulidores de vidrio'),
        ('7316', '[7316] Escritores de carteles, pintores decorativos y grabadores'),
        ('7317', '[7317] Artesanos en madera, cestería y materiales similares'),
        ('7318', '[7318] Artesanos de los textiles, el cuero y materiales similares'),
        ('7319', '[7319] Artesanos no clasificados bajo otros epígrafes'),
        ('7321', '[7321] Cajistas, tipógrafos y afines'),
        ('7322', '[7322] Impresores'),
        ('7323', '[7323] Encuadernadores y afines'),
        ('7411', '[7411] Electricistas de obras y afines'),
        ('7412', '[7412] Mecánicos y ajustadores electricistas'),
        ('7413', '[7413] Instaladores y reparadores de líneas eléctricas'),
        ('7421', '[7421] Mecánicos y reparadores en electrónica'),
        ('7422', '[7422] Instaladores y reparadores en tecnología de la información y las comunicaciones'),
        ('7511', '[7511] Carniceros, pescadores y afines'),
        ('7512', '[7512] Panaderos, pasteleros, golosineros y confiteros'),
        ('7513', '[7513] Operarios de la elaboración de productos lácteos'),
        ('7514', '[7514] Operarios de la conservación de frutas, legumbres, verduras y afines'),
        ('7515', '[7515] Catadores y clasificadores de alimentos y bebidas'),
        ('7516', '[7516] Preparadores y elaboradores de tabaco y sus productos'),
        ('7521', '[7521] Operarios del tratamiento de la madera'),
        ('7522', '[7522] Ebanistas y afines'),
        ('7523', '[7523] Reguladores y operadores de máquinas de labrar madera'),
        ('7531', '[7531] Sastres, modistas, peleteros, sombrereros y costureros'),
        ('7532', '[7532] Patronistas y cortadores de tela, cuero y afines'),
        ('7533', '[7533] Bordadores y afines'),
        ('7534', '[7534] Tapiceros, colchoneros y afines'),
        ('7535', '[7535] Apelambradores, pellejeros y curtidores'),
        ('7536', '[7536] Zapateros y afines'),
        ('7542', '[7542] Dinamiteros y pegadores'),
        ('7543', '[7543] Clasificadores y probadores de productos (excluyendo alimentos y bebidas)'),
        ('7544', '[7544] Fumigadores y otros controladores de plagas y malas hierbas'),
        ('7549', '[7549] Operarios y artesanos de artes mecánicas y de otros oficios no clasificados bajo otros'),
        ('8111', '[8111] Mineros y operadores de instalaciones mineras'),
        ('8112', '[8112] Operadores de instalaciones de procesamiento de minerales y rocas'),
        ('8113', '[8113] Perforadores y sondistas de pozos y afines'),
        ('8114', '[8114] Operadores de máquinas para fabricar cemento y otros productos minerales'),
        ('8121', '[8121] Operadores de instalaciones de procesamiento de metales'),
        ('8122', '[8122] Operadores de máquinas pulidoras, galvanizadoras y recubridoras de metales'),
        ('8131', '[8131] Operadores de plantas y máquinas de productos químicos'),
        ('8132', '[8132] Operadores de máquinas para fabricar productos fotográficos'),
        ('8141', '[8141] Operadores de máquinas para fabricar productos de caucho'),
        ('8142', '[8142] Operadores de máquinas para fabricar productos de material plástico'),
        ('8143', '[8143] Operadores de máquinas para fabricar productos de papel'),
        ('8151', '[8151] Operadores de máquinas de preparación de fibras, hilado y devanado'),
        ('8152', '[8152] Operadores de telares y otras máquinas tejedoras'),
        ('8153', '[8153] Operadores de máquinas de coser'),
        ('8154', '[8154] Operadores de máquinas de blanqueamiento, teñido y limpieza de tejidos'),
        ('8155', '[8155] Operadores de máquinas de tratamiento de pieles y cueros'),
        ('8156', '[8156] Operadores de máquinas para la fabricación de calzado y afines'),
        ('8157', '[8157] Operadores de máquinas lavarropas'),
        ('8159', '[8159] Operarios de máquinas para fabricar productos textiles y artículos de piel y cuero'),
        ('8160', '[8160] Operadores de máquinas para elaborar alimentos y productos afines'),
        ('8171', '[8171] Operadores de instalaciones y máquinas para la preparación de pasta para papel'),
        ('8172', '[8172] Operadores de instalaciones y máquinas de procesamiento de la madera'),
        ('8181', '[8181] Operadores de instalaciones y máquinas de vidriería y cerámica'),
        ('8182', '[8182] Operadores de máquinas de vapor y calderas'),
        ('8183', '[8183] Operadores de máquinas de embalaje, embotellamiento y etiquetado'),
        ('8189', '[8189] Operadores de máquinas y de instalaciones fijas no clasificados bajo otros'),
        ('8211', '[8211] Ensambladores de maquinaria mecánica'),
        ('8212', '[8212] Ensambladores de equipos eléctricos y electrónicos'),
        ('8219', '[8219] Ensambladores no clasificados bajo otros epígrafes'),
        ('8311', '[8311] Maquinistas de locomotoras'),
        ('8312', '[8312] Guardafrenos, guardagujas y agentes de maniobras en vías férreas'),
        ('8321', '[8321] Conductores de motocicletas'),
        ('8322', '[8322] Conductores de automóviles, taxis y camionetas'),
        ('8331', '[8331] Conductores de autobuses y tranvías'),
        ('8332', '[8332] Conductores de camiones pesados'),
        ('8341', '[8341] Operadores de maquinaria agrícola y forestal móvil'),
        ('8342', '[8342] Operadores de máquinas de movimiento de tierras y afines'),
        ('8343', '[8343] Operadores de grúas, aparatos elevadores y afines'),
        ('8344', '[8344] Operadores de autoelevadoras'),
        ('8350', '[8350] Marineros de cubierta y afines'),
        ('9111', '[9111] Limpiadores y asistentes domésticos'),
        ('9112', '[9112] Limpiadores y asistentes de oficinas, hoteles y otros establecimientos'),
        ('9121', '[9121] Lavanderos y planchadores manuales'),
        ('9122', '[9122] Lavadores de vehículos'),
        ('9123', '[9123] Lavadores de ventanas'),
        ('9129', '[9129] Otro personal de limpieza'),
        ('9211', '[9211] Peones de explotaciones agrícolas'),
        ('9212', '[9212] Peones de explotaciones ganaderas'),
        ('9213', '[9213] Peones de explotaciones de cultivos mixtos y ganaderos'),
        ('9214', '[9214] Peones de jardinería'),
        ('9215', '[9215] Peones forestales'),
        ('9216', '[9216] Peones de pesca y acuicultura'),
        ('9311', '[9311] Peones de minas y canteras'),
        ('9312', '[9312] Peones de obras públicas y mantenimiento'),
        ('9313', '[9313] Peones de la construcción de edificios'),
        ('9321', '[9321] Empacadores manuales'),
        ('9329', '[9329] Peones de la industria manufacturera no clasificados bajo otros epígrafes'),
        ('9331', '[9331] Conductores de vehículos accionados a pedal o a brazo'),
        ('9332', '[9332] Conductores de vehículos y máquinas de tracción animal'),
        ('9333', '[9333] Peones de carga'),
        ('9334', '[9334] Reponedores de estanterías'),
        ('9411', '[9411] Cocineros de comidas rápidas'),
        ('9412', '[9412] Ayudantes de cocina'),
        ('9510', '[9510] Trabajadores ambulantes de servicios y afines'),
        ('9520', '[9520] Vendedores ambulantes (excluyendo de comida para consumo inmediato)'),
        ('9611', '[9611] Recolectores de basura y material reciclable'),
        ('9612', '[9612] Clasificadores de desechos'),
        ('9613', '[9613] Barrenderos y afines'),
        ('9621', '[9621] Mensajeros, mandaderos, maleteros y repartidores'),
        ('9622', '[9622] Recolectores de dinero en aparatos de venta automática y lectores de medidores'),
        ('9623', '[9623] Acarreadores de agua y recolectores de leña'),
        ('9629', '[9629] Ocupaciones elementales no clasificadas bajo otros epígrafes'),
    ], string='Ocupación COCR-2023 (INS)',
       help='Clasificación de Ocupaciones de Costa Rica COCR-2023. Fuente: INEC Costa Rica.')

    ins_risk_class = fields.Selection([
        ('I',   'Clase I   - Oficinas y trabajo administrativo (~0.87%)'),
        ('II',  'Clase II  - Comercio, ventas y servicios (~1.49%)'),
        ('III', 'Clase III - Industria liviana y tecnicos (~2.47%)'),
        ('IV',  'Clase IV  - Construccion e industria pesada (~4.13%)'),
        ('V',   'Clase V   - Actividades de alto riesgo (~6.88%)'),
    ], string='Clase de Riesgo (INS)',
       help='Clasificacion de riesgo del INS para la poliza de Riesgos del Trabajo (Seguro Obligatorio). '\
            'Las clases I a V determinan la tasa de prima segun el nivel de riesgo de la actividad. '\
            'La clase aplicable a cada empresa es definida por el INS al contratar la poliza. '\
            'Fuente: INS Costa Rica - Manual de Clasificacion de Riesgos del Trabajo (ins-cr.com).')

    # ── Datos CCSS ──────────────────────────────────────────────────
    ccss_number = fields.Char(string='Número CCSS')
    ccss_insured = fields.Boolean(string='Asegurado CCSS', default=True)

    # ── Datos bancarios ─────────────────────────────────────────────
    bank_account_number = fields.Char(string='Número de Cuenta Bancaria')
    bank_iban = fields.Char(
        string='IBAN',
        help='Formato IBAN costarricense: CR + 20 digitos. Ej: CR15200001121513215152'
    )
    sinpe_phone = fields.Char(
        string='Teléfono SINPE Móvil',
        size=8,
        help='Número de 8 dígitos registrado en SINPE Móvil para pago de planilla.'
    )
    bank_name = fields.Selection([
        ('BCR', 'Banco de Costa Rica'),
        ('BNCR', 'Banco Nacional de Costa Rica'),
        ('BP', 'Banco Popular y de Desarrollo Comunal'),
        ('BAC', 'BAC San Jose'),
        ('BCT', 'Banco BCT'),
        ('CATHAY', 'Banco Cathay de Costa Rica'),
        ('CMB', 'Banco CMB'),
        ('DAVIVIENDA', 'Banco Davivienda'),
        ('GENERAL', 'Banco General'),
        ('IMPROSA', 'Banco Improsa'),
        ('LAFISE', 'Banco La Fise'),
        ('PROMERICA', 'Banco Promerica'),
        ('PRIVAL', 'Prival Bank'),
        ('SCOTIA', 'Scotiabank'),
        ('COOCIQUE', 'Coocique R.L.'),
        ('COOPENAE', 'Coopenae R.L.'),
        ('MUTUAL_ALJ', 'Mutual Alajuela'),
        ('OTRO', 'Otro'),
    ], string='Banco')
    bank_account_type = fields.Selection([
        ('corriente', 'Cuenta Corriente'),
        ('ahorros', 'Cuenta de Ahorros'),
        ('sinpe', 'SINPE Movil'),
    ], string='Tipo de Cuenta')

    # ── Fechas importantes ──────────────────────────────────────────
    entry_date = fields.Date(string='Fecha de Ingreso')
    exit_date = fields.Date(string='Fecha de Salida')

    # ── Saldo de Vacaciones (Art. 153 Código de Trabajo CR) ──────────
    planilla_vacation_ids = fields.One2many(
        'planilla.vacation.payment', 'employee_id',
        string='Vacaciones'
    )
    vacation_days_accrued = fields.Float(
        string='Días Acumulados',
        compute='_compute_vacation_balance', store=True,
        help='Días ganados: 12 días hábiles por cada 50 semanas trabajadas (Art. 153 CT)'
    )
    vacation_days_taken = fields.Float(
        string='Días Tomados',
        compute='_compute_vacation_balance', store=True,
        help='Días de vacaciones ya utilizados (estado aprobado o pagado)'
    )
    vacation_days_available = fields.Float(
        string='Días Disponibles',
        compute='_compute_vacation_balance', store=True,
        help='Saldo disponible = Acumulados − Tomados'
    )
    vacation_balance_alert = fields.Boolean(
        string='Alerta Vacaciones',
        compute='_compute_vacation_balance', store=True,
        help='True si el empleado tiene saldo negativo de vacaciones'
    )

    # ── Préstamos y Adelantos ───────────────────────────────────────
    loan_ids = fields.One2many(
        'planilla.employee.loan', 'employee_id', string='Préstamos'
    )
    loan_active_count = fields.Integer(
        string='Préstamos Activos', compute='_compute_loan_summary', store=True
    )
    loan_pending_amount = fields.Monetary(
        string='Saldo Préstamos Pendiente', currency_field='currency_id',
        compute='_compute_loan_summary', store=True
    )

    @api.depends('loan_ids.state', 'loan_ids.amount_pending')
    def _compute_loan_summary(self):
        for emp in self:
            active_loans = emp.loan_ids.filtered(
                lambda l: l.state in ('approved', 'active')
            )
            emp.loan_active_count   = len(active_loans)
            emp.loan_pending_amount = sum(active_loans.mapped('amount_pending'))

    # ── Historial de salarios ───────────────────────────────────────
    recurring_benefit_ids = fields.One2many(
        'planilla.recurring.benefit', 'employee_id',
        string='Beneficios/Deducciones Recurrentes'
    )
    salary_history_ids = fields.One2many(
        'planilla.salary.history', 'employee_id', string='Historial de Salarios'
    )
    salary_history_count = fields.Integer(
        compute='_compute_salary_history_count', string='Salarios Registrados'
    )

    # ── Cómputos ────────────────────────────────────────────────────
    @api.depends('salary_history_ids')
    def _compute_salary_history_count(self):
        for rec in self:
            rec.salary_history_count = len(rec.salary_history_ids)

    def action_view_salary_history(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Historial de Salarios',
            'res_model': 'planilla.salary.history',
            'view_mode': 'list,form',
            'domain': [('employee_id', '=', self.id)],
            'context': {'default_employee_id': self.id},
        }

    def action_print_salary_history(self):
        return self.env.ref('planilla_cr.action_report_salary_history').report_action(self)

    @api.model_create_multi
    def create(self, vals_list):
        employees = super().create(vals_list)
        for employee in employees:
            if employee.base_salary:
                self.env['planilla.salary.history'].create({
                    'employee_id': employee.id,
                    'salary': employee.base_salary,
                    'effective_date': employee.salary_effective_date or fields.Date.today(),
                    'reason': 'Salario Inicial',
                })
        return employees

    def write(self, vals):
        old_salaries = {emp.id: emp.base_salary for emp in self} if 'base_salary' in vals else {}
        result = super().write(vals)
        if 'base_salary' in vals:
            for employee in self:
                old_sal = old_salaries.get(employee.id, 0.0)
                if employee.base_salary and employee.base_salary != old_sal:
                    self.env['planilla.salary.history'].create({
                        'employee_id': employee.id,
                        'salary': employee.base_salary,
                        'effective_date': vals.get('salary_effective_date') or fields.Date.today(),
                        'reason': 'Ajuste Salarial',
                    })
            self._check_minimum_salary_warning()
        return result

    @api.depends('entry_date', 'exit_date',
                 'planilla_vacation_ids.state', 'planilla_vacation_ids.days',
                 'planilla_vacation_ids.vacation_type')
    def _compute_vacation_balance(self):
        """
        Art. 153 CT CR: 12 días hábiles por cada 50 semanas laboradas.
        Acumula proporcional: (semanas_trabajadas / 50) * 12 días.
        Art. 153 párrafo 2: incapacidades > 3 meses continuos NO cuentan
        como tiempo trabajado para el cálculo de vacaciones.
        """
        for emp in self:
            if not emp.entry_date:
                emp.vacation_days_accrued = 0.0
                emp.vacation_days_taken = 0.0
                emp.vacation_days_available = 0.0
                emp.vacation_balance_alert = False
                continue

            # Fecha de corte: salida si existe, hoy si no
            cutoff = emp.exit_date or date.today()
            total_days = (cutoff - emp.entry_date).days

            # ── Descontar incapacidades > 3 meses continuos (Art. 153 CT) ──
            # Solo incapacidades confirmadas/pagadas con más de 90 días
            disability_days_excluded = 0
            long_disabilities = self.env['planilla.disability'].search([
                ('employee_id', '=', emp.id),
                ('state', 'in', ('confirmed', 'paid')),
                ('days', '>', 90),  # > 3 meses = 90 días
            ])
            for dis in long_disabilities:
                # Solo descontar los días que EXCEDEN los 3 primeros meses
                disability_days_excluded += max(dis.days - 90, 0)

            effective_days = max(total_days - disability_days_excluded, 0)
            weeks_worked = effective_days / 7.0
            accrued = round((weeks_worked / 50.0) * 12.0, 2)

            # Días tomados: registros aprobados o pagados no cancelados
            taken_recs = self.env['planilla.vacation.payment'].search([
                ('employee_id', '=', emp.id),
                ('state', 'in', ['approved', 'paid']),
                ('vacation_type', 'in', ['disfrutadas', 'adelanto']),
            ])
            taken = round(sum(taken_recs.mapped('days')), 2)

            available = round(accrued - taken, 2)
            emp.vacation_days_accrued   = accrued
            emp.vacation_days_taken     = taken
            emp.vacation_days_available = available
            emp.vacation_balance_alert  = available < 0

    def _check_minimum_salary_warning(self):
        min_salary = self.env['planilla.minimum.salary'].get_current_minimum()
        if not min_salary:
            return
        warnings = []
        for emp in self:
            if emp.base_salary and emp.base_salary < min_salary:
                warnings.append(
                    f'  - {emp.name}: {emp.base_salary:,.2f} (minimo: {min_salary:,.2f})'
                )
        if warnings:
            msg = 'ADVERTENCIA - Salario por debajo del minimo MTSS vigente:\n' + '\n'.join(warnings)
            msg += '\n\nRevise Configuracion > Salarios Minimos MTSS.'
            raise UserError(msg)

    # ── Validacion IBAN ──────────────────────────────────────────────
    @api.constrains('bank_iban')
    def _check_bank_iban(self):
        for emp in self:
            if not emp.bank_iban:
                continue
            iban = emp.bank_iban.strip().replace(' ', '').replace('-', '').upper()
            # IBAN Costa Rica: CR + 2 digitos control + 4 banco + 16 cuenta = 22 chars total
            if not iban.startswith('CR'):
                raise ValidationError(
                    f'El IBAN del empleado {emp.name} debe comenzar con "CR". '
                    f'Valor ingresado: {emp.bank_iban}'
                )
            if len(iban) != 22:
                raise ValidationError(
                    f'El IBAN del empleado {emp.name} debe tener exactamente 22 caracteres (CR + 20 digitos). '
                    f'Longitud actual: {len(iban)} caracteres. Valor: {iban}'
                )
            if not iban[2:].isdigit():
                raise ValidationError(
                    f'El IBAN del empleado {emp.name} debe contener solo digitos despues de "CR". '
                    f'Valor ingresado: {emp.bank_iban}'
                )
            # Validacion del digito verificador segun ISO 7064 MOD 97-10
            # Reordenar: mover los 4 primeros chars al final, reemplazar letras por numeros
            iban_reorder = iban[4:] + iban[:4]
            iban_numeric = ''
            for ch in iban_reorder:
                if ch.isdigit():
                    iban_numeric += ch
                else:
                    iban_numeric += str(ord(ch) - 55)  # A=10, B=11, ..., R=27
            if int(iban_numeric) % 97 != 1:
                raise ValidationError(
                    f'El IBAN del empleado {emp.name} tiene un digito verificador invalido. '
                    f'Por favor verifique el numero IBAN: {emp.bank_iban}'
                )
