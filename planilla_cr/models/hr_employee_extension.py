from odoo import models, fields, api
from odoo.exceptions import ValidationError
import math
from datetime import date, timedelta


class HrEmployeeExtension(models.Model):
    _inherit = 'hr.employee'

    # -- Metodo de calculo de planilla -------------------------------
    payroll_calculation_method = fields.Selection([
        ('fixed', 'Salario Fijo (sin consultar asistencias)'),
        ('attendance', 'Por Horas Trabajadas (segun modulo de Asistencias)'),
    ], string='Metodo de Calculo de Planilla',
        default='fixed', required=True, tracking=True,
        help='Define como se calcula el salario base en cada boleta de pago:\n'
             '- Salario Fijo: usa siempre el salario base configurado.\n'
             '- Por Horas Trabajadas: calcula el pago segun las horas '
             'registradas en el modulo de Asistencias durante el periodo.'
    )

    # -- Identificacion ----------------------------------------------
    identification_type_id = fields.Many2one(
        'planilla.identification.type', string='Tipo de Identificacion'
    )

    # -- Clasificacion -----------------------------------------------
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
    sub_department_id = fields.Many2one(
        'hr.department',
        string='Sub Departamento',
        tracking=True,
        domain="[('parent_id', '=', department_id)]",
        help='Sub departamento dentro del departamento principal del empleado.',
    )
    schedule_type_id = fields.Many2one(
        'planilla.schedule.type', string='Tipo de Horario'
    )
    # -- ROP -- Regimen Obligatorio de Pensiones (Ley 7983) ------------------
    rop_applies = fields.Boolean(
        string='Aplicar ROP en Planilla',
        default=False,
        help='DESACTIVADO por defecto. Activar solo si el contador confirma que este '
             'empleado debe llevar el ROP procesado desde el modulo de planilla. '
             'Muchos contadores manejan el ROP con su propio proceso externo. '
             'Al activar: el sistema deducira 1%% obrero y registrara 3.25%% patronal '
             'automaticamente al sincronizar cada boleta (Ley 7983 Art. 6).'
    )

    # -- Creditos Fiscales -- Art. 34 Ley 7092 / Decreto 45333-H ------------
    income_tax_children = fields.Integer(
        string='Hijos con credito fiscal',
        default=0,
        help='Cantidad de hijos menores o dependientes con derecho a credito fiscal '
             '(Art. 34 LIR). Credito 2026: CRC1,710 por hijo/mes.\n'
             'Regla: si ambos conyuges trabajan, cada hijo solo puede ser aplicado '
             'por uno de ellos. El empleado debe presentar constancia de nacimiento.'
    )
    income_tax_spouse_credit = fields.Boolean(
        string='Credito fiscal por conyuge',
        default=False,
        help='Activa el credito fiscal de CRC2,590/mes por conyuge (Art. 34 LIR).\n'
             'Regla: solo uno de los dos conyuges puede aplicarlo. '
             'El empleado debe presentar constancia de matrimonio vigente.\n'
             'Si ambos conyuges trabajan para la misma empresa, '
             'asegurese de que solo uno tenga este campo activado.'
    )

    # -- Clasificacion de Pensionado ----------------------------------------
    pensioner_type = fields.Selection([
        ('none',   'No pensionado'),
        ('estado', 'Pensionado sector publico (Estado / Magisterio / Poder Judicial)'),
        ('ivm',    'Pensionado IVM / CCSS'),
    ], string='Tipo de pensionado',
        default='none', required=True,
        help='SECTOR PUBLICO (Tipo 1): exonerado del IVM obrero (Art. 4 Ley Const. CCSS). '
             'CCSS obrero: 6.50% (SEM + otros, sin IVM 4.33%). '
             'Se requiere Ndeg de resolucion o carne para respaldo ante auditoria CCSS. '
             'ROP desactivado automaticamente (pension ya existe).\n\n'
             'IVM/CCSS (Tipo 2): pensionado del regimen IVM que volvio al sector privado. '
             'Cotiza CCSS completa 10.83% (SEM + IVM) segun Art. 7 Regl. IVM. '
             'ROP desactivado automaticamente (pension ya existe).'
    )
    pension_resolution_number = fields.Char(
        string='Ndeg resolucion / carne de pensionado',
        help='Numero de resolucion de pension o carne del pensionado emitido por la CCSS, '
             'JUPEMA, Poder Judicial u otra entidad. '
             'REQUERIDO para pensionado sector publico -- el patrono debe justificar ante '
             'una auditoria CCSS por que no retuvo el IVM (Art. 4 Ley Const. CCSS).'
    )

    @api.onchange('pensioner_type')
    def _onchange_pensioner_type(self):
        """Al clasificar como pensionado (cualquier tipo), desactiva ROP automaticamente.
        El fin del ROP es crear la pension -- si ya existe, no aplica (Ley 7983)."""
        for rec in self:
            if rec.pensioner_type in ('estado', 'ivm'):
                rec.rop_applies = False

    @api.constrains('pensioner_type', 'pension_resolution_number')
    def _check_pension_resolution(self):
        """Pensionado sector publico REQUIERE numero de resolucion o carne.
        Ante auditoria CCSS, el patrono debe justificar la exoneracion del IVM."""
        for rec in self:
            if rec.pensioner_type == 'estado' and not rec.pension_resolution_number:
                raise ValidationError(
                    f'El empleado {rec.name} esta clasificado como Pensionado Sector Publico '
                    f'pero no tiene Ndeg de resolucion o carne registrado.\n\n'
                    f'Este dato es obligatorio: ante una auditoria de la CCSS, el patrono '
                    f'debe justificar por que no retuvo el IVM obrero (4.33%). '
                    f'Ingrese el numero de resolucion de pension o carne emitido por la CCSS, '
                    f'JUPEMA, Poder Judicial u otra entidad pagadora de la pension.'
                )

    has_variable_income = fields.Boolean(
        string='Salario Variable (comisiones / HE recurrentes)',
        default=False,
        help='Active si el empleado recibe comisiones por ventas, horas extras recurrentes\n'
             'u otros ingresos variables que fluctuan mes a mes.\n\n'
             'Con este flag activo el sistema:\n'
             '   Activa automaticamente "Usar Promedio 4 Semanas" al crear vacaciones\n'
             '    (Art. 153 CT -- obligatorio para salarios variables)\n'
             '   Calcula el "Salario Bruto Mensual" en liquidaciones como promedio\n'
             '    de los ultimos 4 meses del historial salarial\n'
             '   Muestra una advertencia en la boleta si no hay bonos del periodo\n\n'
             'NOTA: El sistema solo puede promediar lo que encuentra en el historial\n'
             'salarial (planilla.salary.history). Si las comisiones se registran como\n'
             'bonos en cada boleta y las boletas se pagan, el historial se actualiza\n'
             'automaticamente con el bruto real.'
    )

    payroll_calendar_id = fields.Many2one(
        'planilla.calendar', string='Calendarizacion de Planilla'
    )

    # -- Datos salariales --------------------------------------------
    base_salary = fields.Monetary(
        string='Salario Base', currency_field='currency_id', tracking=True
    )
    currency_id = fields.Many2one(
        'res.currency',
        string='Moneda'
    )
    salary_effective_date = fields.Date(string='Fecha Vigencia Salarial')

    # -- Datos INS - Riesgos del Trabajo ----------------------------
    ins_include = fields.Boolean(
        string='Incluir en Planilla INS',
        default=True,
    )
    ins_policy_number = fields.Char(string='Numero de Poliza INS')

    # Nombre separado para INS
    ins_first_name = fields.Char(string='Nombre (INS)')
    ins_first_lastname = fields.Char(string='Primer Apellido (INS)')
    ins_second_lastname = fields.Char(string='Segundo Apellido (INS)')

    ins_id_type = fields.Selection([
        ('01', 'Cedula de Costa Rica'),
        ('02', 'Residencia de Costa Rica'),
        ('03', 'Permiso de Trabajo'),
        ('04', 'Pasaporte'),
        ('05', 'Indocumentado'),
    ], string='Tipo de Identificacion (INS)', default='01',
       help='Tipos de identificacion segun formulario INS Planilla de Riesgos del Trabajo. Fuente: INS Costa Rica (ins-cr.com).')

    ins_nationality = fields.Selection([
        ('CR', 'Costarricense'),
        ('NI', 'Nicaraguense'),
        ('CO', 'Colombiana'),
        ('US', 'Estadounidense'),
        ('HN', 'Hondurena'),
        ('SV', 'Salvadorena'),
        ('GT', 'Guatemalteca'),
        ('PA', 'Panamena'),
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
        ('BR', 'Brasilena'),
        ('ES', 'Espanola'),
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
        ('05', 'Union Libre'),
        ('06', 'Separado/a'),
    ], string='Estado Civil (INS)', default='01',
       help='Estados civiles segun formulario INS Planilla de Riesgos del Trabajo. Fuente: INS Costa Rica.')

    ins_workday_type = fields.Selection([
        ('01', 'Ordinaria'),
        ('02', 'Extraordinaria'),
        ('03', 'Mixta'),
        ('04', 'Tiempo Parcial'),
        ('05', 'Por Horas'),
        ('06', 'Ocasional'),
    ], string='Tipo de Jornada (INS)', default='01',
       help='Tipos de jornada segun Codigo de Trabajo de Costa Rica y formulario INS. Fuente: Codigo de Trabajo, Ley N.deg 2 y formulario INS.')

    ins_occupation = fields.Selection([
        ('1111', '[1111] Miembros del poder legislativo y ejecutivo'),
        ('1112', '[1112] Personal directivo de la administracion publica'),
        ('1113', '[1113] Jefes de comunidades etnicas'),
        ('1114', '[1114] Dirigentes de organizaciones que presentan un interes especial'),
        ('1120', '[1120] Directores y gerentes generales'),
        ('1211', '[1211] Directores y gerentes de servicios financieros'),
        ('1212', '[1212] Directores y gerentes de recursos humanos'),
        ('1213', '[1213] Directores y gerentes de politicas y planificacion'),
        ('1219', '[1219] Directores y gerentes de administracion y servicios no clasificados bajo otros'),
        ('1221', '[1221] Directores y gerentes de venta y comercializacion'),
        ('1222', '[1222] Directores y gerentes de publicidad y relaciones publicas'),
        ('1223', '[1223] Directores y gerentes de investigacion y desarrollo'),
        ('1311', '[1311] Directores y gerentes de produccion agropecuaria y silvicultura'),
        ('1312', '[1312] Directores y gerentes de produccion acuicola, piscicola y de pesca'),
        ('1321', '[1321] Directores y gerentes de industrias manufactureras'),
        ('1322', '[1322] Directores y gerentes de explotaciones de mineria'),
        ('1323', '[1323] Directores y gerentes de empresas de construccion'),
        ('1324', '[1324] Directores y gerentes de empresas de abastecimiento, distribucion y afines'),
        ('1330', '[1330] Directores y gerentes de servicios de tecnologia de la informacion y las comunicaciones'),
        ('1341', '[1341] Directores y gerentes de servicios de cuidados infantiles'),
        ('1342', '[1342] Directores y gerentes de servicios de salud'),
        ('1343', '[1343] Directores y gerentes de servicios de atencion a personas adultas mayores'),
        ('1344', '[1344] Directores y gerentes de servicios de bienestar social'),
        ('1345', '[1345] Directores y gerentes de servicios de educacion'),
        ('1346', '[1346] Directores y gerentes de sucursales de bancos, de servicios financieros y de seguros'),
        ('1349', '[1349] Directores y gerentes de servicios profesionales no clasificados bajo otros'),
        ('1411', '[1411] Directores y gerentes de hoteles'),
        ('1412', '[1412] Directores y gerentes de restaurantes'),
        ('1420', '[1420] Gerentes de comercios al por mayor y al por menor'),
        ('1431', '[1431] Directores y gerentes de centros deportivos, de esparcimiento y culturales'),
        ('1439', '[1439] Directores y gerentes de servicios no clasificados bajo otros epigrafes'),
        ('2111', '[2111] Fisicos y astronomos'),
        ('2112', '[2112] Meteorologos'),
        ('2113', '[2113] Quimicos'),
        ('2114', '[2114] Geologos y geofisicos'),
        ('2120', '[2120] Matematicos, actuarios y estadisticos'),
        ('2131', '[2131] Biologos, botanicos, zoologos y afines'),
        ('2132', '[2132] Agronomos, zootecnistas y afines'),
        ('2133', '[2133] Profesionales de la proteccion medioambiental'),
        ('2141', '[2141] Ingenieros industriales y de produccion'),
        ('2142', '[2142] Ingenieros civiles'),
        ('2143', '[2143] Ingenieros medioambientales'),
        ('2144', '[2144] Ingenieros mecanicos, navales y aeronauticos'),
        ('2145', '[2145] Ingenieros quimicos'),
        ('2146', '[2146] Ingenieros de minas, metalurgicos y afines'),
        ('2149', '[2149] Ingenieros no clasificados bajo otros epigrafes'),
        ('2151', '[2151] Ingenieros electricos'),
        ('2152', '[2152] Ingenieros electronicos'),
        ('2153', '[2153] Ingenieros en telecomunicaciones, audio y sonido'),
        ('2161', '[2161] Arquitectos'),
        ('2162', '[2162] Arquitectos paisajistas'),
        ('2163', '[2163] Disenadores industriales de productos y moda'),
        ('2164', '[2164] Urbanistas e ingenieros de transito'),
        ('2165', '[2165] Topografos'),
        ('2166', '[2166] Disenadores graficos y multimedia'),
        ('2211', '[2211] Medicos generales'),
        ('2212', '[2212] Medicos geriatras'),
        ('2213', '[2213] Medicos ginecologos y obstetras'),
        ('2214', '[2214] Medicos psiquiatras'),
        ('2215', '[2215] Medicos ortopedistas y traumatologos'),
        ('2219', '[2219] Especialistas medicos no clasificados bajo otros epigrafes'),
        ('2220', '[2220] Enfermeros profesionales y profesionales de parteria'),
        ('2230', '[2230] Profesionales de medicina tradicional y alternativa'),
        ('2250', '[2250] Veterinarios'),
        ('2261', '[2261] Dentistas'),
        ('2262', '[2262] Cirujanos orales y maxilofaciales'),
        ('2271', '[2271] Farmaceuticos'),
        ('2272', '[2272] Profesionales de la salud y la higiene laboral y ambiental'),
        ('2273', '[2273] Fisioterapeutas'),
        ('2274', '[2274] Nutricionistas'),
        ('2275', '[2275] Audiologos y terapeutas del lenguaje'),
        ('2276', '[2276] Optometristas'),
        ('2279', '[2279] Profesionales de la salud no clasificados bajo otros epigrafes'),
        ('2310', '[2310] Profesores de instituciones de educacion superior'),
        ('2320', '[2320] Profesores de formacion profesional'),
        ('2330', '[2330] Profesores de educacion secundaria'),
        ('2341', '[2341] Profesores de educacion primaria'),
        ('2342', '[2342] Profesores de educacion preescolar'),
        ('2351', '[2351] Especialistas en metodos pedagogicos'),
        ('2352', '[2352] Profesores de educacion especial'),
        ('2353', '[2353] Otros profesores de idiomas'),
        ('2354', '[2354] Otros profesores de musica'),
        ('2355', '[2355] Otros profesores de artes'),
        ('2356', '[2356] Instructores en tecnologia de la informacion'),
        ('2359', '[2359] Profesionales de la educacion no clasificados bajo otros epigrafes'),
        ('2411', '[2411] Contadores y auditores financieros'),
        ('2412', '[2412] Asesores financieros y en inversiones'),
        ('2413', '[2413] Analistas financieros'),
        ('2421', '[2421] Analistas de gestion y organizacion'),
        ('2422', '[2422] Profesionales en politicas sociales y de administracion'),
        ('2423', '[2423] Profesionales de gestion de talento humano'),
        ('2424', '[2424] Profesionales en formacion, desarrollo de personal y evaluacion de competencias'),
        ('2431', '[2431] Profesionales de la publicidad y la comercializacion'),
        ('2432', '[2432] Profesionales de relaciones publicas'),
        ('2433', '[2433] Profesionales de ventas tecnicas y medicas (excluyendo las TIC)'),
        ('2434', '[2434] Profesionales de ventas de tecnologia de la informacion y las comunicaciones'),
        ('2511', '[2511] Analistas de sistemas'),
        ('2512', '[2512] Desarrolladores de software'),
        ('2513', '[2513] Desarrolladores web y multimedia'),
        ('2514', '[2514] Programadores de aplicaciones'),
        ('2519', '[2519] Desarrolladores y analistas de software y multimedia no clasificados bajo otros'),
        ('2521', '[2521] Disenadores y administradores de bases de datos'),
        ('2522', '[2522] Administradores de sistemas'),
        ('2523', '[2523] Profesionales en redes de computadores'),
        ('2529', '[2529] Profesionales en bases de datos y en redes de computadores no clasificados bajo otros'),
        ('2611', '[2611] Abogados'),
        ('2612', '[2612] Jueces'),
        ('2619', '[2619] Profesionales en derecho no clasificados bajo otros epigrafes'),
        ('2621', '[2621] Archivistas, curadores de arte y restauradores'),
        ('2622', '[2622] Bibliotecologos, documentalistas y afines'),
        ('2631', '[2631] Economistas'),
        ('2632', '[2632] Sociologos, antropologos y afines'),
        ('2633', '[2633] Filosofos, historiadores y especialistas en ciencias politicas'),
        ('2634', '[2634] Psicologos'),
        ('2635', '[2635] Profesionales del trabajo social'),
        ('2636', '[2636] Profesionales religiosos'),
        ('2639', '[2639] Profesionales en ciencias sociales no clasificados bajo otros epigrafes'),
        ('2641', '[2641] Autores literarios y otros escritores'),
        ('2642', '[2642] Periodistas, editores y redactores'),
        ('2643', '[2643] Traductores, interpretes, linguistas y filologos'),
        ('2651', '[2651] Escultores, pintores artisticos y afines'),
        ('2652', '[2652] Musicos, cantantes y compositores'),
        ('2653', '[2653] Coreografos, directores de danza y bailarines profesionales'),
        ('2654', '[2654] Directores y productores de cine, de teatro y afines'),
        ('2655', '[2655] Actores'),
        ('3111', '[3111] Tecnicos en ciencias fisicas y quimicas'),
        ('3112', '[3112] Tecnicos en ingenieria civil'),
        ('3113', '[3113] Electrotecnicos'),
        ('3114', '[3114] Tecnicos en electronica'),
        ('3115', '[3115] Tecnicos en ingenieria mecanica'),
        ('3116', '[3116] Tecnicos en quimica industrial'),
        ('3117', '[3117] Tecnicos en ingenieria de minas y metalurgia'),
        ('3118', '[3118] Delineantes y dibujantes tecnicos'),
        ('3119', '[3119] Otros tecnicos en ciencias fisicas, quimicas, ingenieria y arquitectura no clasificados'),
        ('3121', '[3121] Supervisores en ingenieria de minas'),
        ('3122', '[3122] Supervisores en industrias manufactureras'),
        ('3123', '[3123] Supervisores de la construccion'),
        ('3131', '[3131] Operadores de plantas de generacion y distribucion de energia'),
        ('3132', '[3132] Operadores de incineradores, instalaciones de tratamiento de agua y afines'),
        ('3133', '[3133] Controladores de instalaciones de procesamiento de productos quimicos'),
        ('3134', '[3134] Operadores de instalaciones de refinacion de petroleo y gas natural'),
        ('3135', '[3135] Controladores de procesos de produccion de metales'),
        ('3139', '[3139] Tecnicos en control de procesos no clasificados bajo otros epigrafes'),
        ('3141', '[3141] Tecnicos en ciencias biologicas (excluyendo la medicina)'),
        ('3142', '[3142] Tecnicos agropecuarios'),
        ('3143', '[3143] Tecnicos forestales'),
        ('3151', '[3151] Maquinistas en navegacion maritima'),
        ('3152', '[3152] Capitanes y oficiales de cubierta'),
        ('3153', '[3153] Pilotos de aviacion y afines'),
        ('3154', '[3154] Controladores de trafico aereo'),
        ('3155', '[3155] Tecnicos en seguridad aeronautica'),
        ('3156', '[3156] Controladores de trafico maritimo'),
        ('3211', '[3211] Tecnicos en aparatos de diagnostico y tratamiento medico'),
        ('3212', '[3212] Tecnicos de laboratorios medicos'),
        ('3213', '[3213] Tecnicos y asistentes en farmacia'),
        ('3214', '[3214] Tecnicos de protesis medicas y dentales'),
        ('3220', '[3220] Profesionales de nivel medio de enfermeria'),
        ('3230', '[3230] Profesionales de nivel medio de medicina tradicional y alternativa'),
        ('3240', '[3240] Tecnicos y asistentes veterinarios'),
        ('3250', '[3250] Tecnico en emergencias medicas'),
        ('3261', '[3261] Auxiliares y tecnicos de odontologia'),
        ('3262', '[3262] Tecnicos en documentacion sanitaria'),
        ('3263', '[3263] Trabajadores comunitarios de la salud'),
        ('3264', '[3264] Tecnicos en optometria y opticos'),
        ('3265', '[3265] Tecnicos y asistentes fisioterapeutas'),
        ('3266', '[3266] Practicantes y asistentes medicos'),
        ('3267', '[3267] Inspectores de la salud laboral, medioambiental y afines'),
        ('3268', '[3268] Auxiliar de ambulancias en emergencias medicas'),
        ('3269', '[3269] Tecnicos de las ciencias de la salud no clasificado bajo otros epigrafes'),
        ('3311', '[3311] Agentes de bolsa, cambio y otros servicios financieros'),
        ('3312', '[3312] Oficiales de prestamos y creditos'),
        ('3313', '[3313] Tecnicos y auxiliares de contabilidad'),
        ('3314', '[3314] Profesionales de nivel medio de servicios estadisticos, matematicos y afines'),
        ('3315', '[3315] Tasadores'),
        ('3316', '[3316] Tecnicos y asistentes en administracion y en economia'),
        ('3321', '[3321] Agentes de seguros'),
        ('3322', '[3322] Representantes comerciales'),
        ('3323', '[3323] Agentes de proveeduria'),
        ('3324', '[3324] Agentes de compras y consignatarios'),
        ('3331', '[3331] Declarantes o gestores de aduana'),
        ('3332', '[3332] Organizadores de conferencias y eventos'),
        ('3333', '[3333] Agentes de empleo y contratistas de mano de obra'),
        ('3334', '[3334] Agentes inmobiliarios'),
        ('3339', '[3339] Otros agentes comerciales y corredores no clasificados bajo otros epigrafes'),
        ('3341', '[3341] Supervisores de oficina'),
        ('3342', '[3342] Secretarios juridicos'),
        ('3343', '[3343] Secretarios administrativos y ejecutivos'),
        ('3344', '[3344] Secretarios medicos'),
        ('3351', '[3351] Inspectores de aduanas y fronteras'),
        ('3352', '[3352] Agentes de administracion tributaria'),
        ('3353', '[3353] Agentes de servicios de seguridad social'),
        ('3354', '[3354] Funcionarios de servicios de expedicion de licencias y permisos'),
        ('3355', '[3355] Inspectores de policia y detectives'),
        ('3359', '[3359] Agentes de la administracion publica para la aplicacion de la ley y afines no clasificados'),
        ('3411', '[3411] Profesionales de nivel medio del derecho, servicios legales y afines'),
        ('3412', '[3412] Tecnicos y asistentes en trabajo social'),
        ('3413', '[3413] Auxiliares laicos de las religiones'),
        ('3421', '[3421] Atletas y deportistas'),
        ('3422', '[3422] Entrenadores, instructores y arbitros de actividades deportivas'),
        ('3423', '[3423] Instructores de educacion fisica y actividades recreativas'),
        ('3431', '[3431] Fotografos'),
        ('3432', '[3432] Disenadores y decoradores de interior'),
        ('3433', '[3433] Tecnicos en galerias de arte, museos y bibliotecas'),
        ('3435', '[3435] Otros profesionales de nivel medio en actividades culturales y artisticas'),
        ('3511', '[3511] Tecnicos en operaciones de tecnologia de la informacion y las comunicaciones'),
        ('3512', '[3512] Tecnicos en asistencia al usuario de tecnologia de la informacion y las comunicaciones'),
        ('3513', '[3513] Tecnicos en redes y sistemas de computadores'),
        ('3514', '[3514] Tecnicos de la web'),
        ('3521', '[3521] Tecnicos de radiodifusion y grabacion audiovisual'),
        ('3522', '[3522] Tecnicos de ingenieria de las telecomunicaciones'),
        ('3610', '[3610] Profesionales de nivel medio de la ensenanza'),
        ('3711', '[3711] Tecnicos y asistentes en relaciones publicas y publicidad'),
        ('3712', '[3712] Tecnicos y asistentes en sociologia, antropologia, arqueologia, geografia y afines'),
        ('3713', '[3713] Tecnicos y asistentes en filosofia, historia y politologia'),
        ('3714', '[3714] Tecnicos y asistentes en filologia y linguistica y en traduccion'),
        ('3715', '[3715] Tecnicos y asistentes en psicologia'),
        ('3716', '[3716] Tecnicos en periodismo y locucion'),
        ('3719', '[3719] Tecnicos y asistentes en ciencias sociales no clasificados bajo otros epigrafes'),
        ('4110', '[4110] Oficinistas generales'),
        ('4120', '[4120] Secretarios generales'),
        ('4131', '[4131] Operadores de maquinas de procesamiento de texto y mecanografos'),
        ('4132', '[4132] Digitadores de datos'),
        ('4211', '[4211] Cajeros de bancos y afines'),
        ('4212', '[4212] Receptores de apuestas y afines'),
        ('4213', '[4213] Prestamistas'),
        ('4214', '[4214] Cobradores y afines'),
        ('4221', '[4221] Recepcionistas'),
        ('4222', '[4222] Empleados de atencion y asesoramiento de llamadas'),
        ('4223', '[4223] Telefonistas'),
        ('4227', '[4227] Entrevistadores de encuestas y de investigaciones de mercados'),
        ('4229', '[4229] Empleados de servicios de informacion al cliente no clasificados bajo otros'),
        ('4311', '[4311] Empleados de contabilidad y calculo de costos'),
        ('4312', '[4312] Empleados de servicios estadisticos, financieros y de seguros'),
        ('4313', '[4313] Empleados encargados de las nominas'),
        ('4321', '[4321] Empleados de control de abastecimientos e inventario'),
        ('4322', '[4322] Empleados de servicios de apoyo a la produccion'),
        ('4323', '[4323] Empleados de servicio de transporte'),
        ('4411', '[4411] Empleados de bibliotecas'),
        ('4412', '[4412] Empleados de servicios de correos'),
        ('4413', '[4413] Codificadores de datos, correctores de pruebas de imprenta y afines'),
        ('4414', '[4414] Escribientes publicos y afines'),
        ('4415', '[4415] Empleados de archivos'),
        ('4416', '[4416] Empleados del servicio de personal'),
        ('4419', '[4419] Personal de apoyo administrativo no clasificado bajo otros epigrafes'),
        ('5111', '[5111] Auxiliares de servicio abordo'),
        ('5112', '[5112] Revisores y cobradores de los transportes publicos'),
        ('5113', '[5113] Guias turisticos'),
        ('5120', '[5120] Cocineros'),
        ('5131', '[5131] Saloneros'),
        ('5132', '[5132] Bartenders'),
        ('5141', '[5141] Especialistas en tratamientos del cabello'),
        ('5142', '[5142] Especialistas en tratamientos de belleza estetica y afines'),
        ('5151', '[5151] Supervisores limpieza en oficinas, hoteles y otros establecimientos'),
        ('5152', '[5152] Economos y mayordomos'),
        ('5153', '[5153] Encargados de mantenimiento de edificios'),
        ('5161', '[5161] Astrologos, adivinadores y afines'),
        ('5162', '[5162] Acompanantes y ayudantes de camara'),
        ('5163', '[5163] Personal de servicios funerarios y embalsamadores (excepto sepultureros)'),
        ('5164', '[5164] Cuidadores y entrenadores de animales'),
        ('5165', '[5165] Instructores de manejo'),
        ('5168', '[5168] Trabajadores de servicios sexuales'),
        ('5169', '[5169] Otros trabajadores de servicios personales'),
        ('5170', '[5170] Propietarios y comerciantes encargados de pequenos establecimientos de servicios'),
        ('5211', '[5211] Vendedores de quioscos y de puestos de mercado'),
        ('5212', '[5212] Vendedores ambulantes de productos comestibles'),
        ('5213', '[5213] Vendedores ambulantes (excluyendo de comida para consumo inmediato)'),
        ('5221', '[5221] Propietarios y comerciantes encargados de pequenas tiendas y otros tipos de establecimientos'),
        ('5222', '[5222] Supervisores de tiendas y almacenes'),
        ('5223', '[5223] Asistentes de ventas de tiendas y almacenes'),
        ('5230', '[5230] Cajeros y expendedores de boletos y tiquetes'),
        ('5241', '[5241] Modelos de moda, arte y publicidad'),
        ('5242', '[5242] Demostradores de tiendas'),
        ('5243', '[5243] Vendedores puerta a puerta'),
        ('5244', '[5244] Vendedores por telefono'),
        ('5245', '[5245] Expendedores de gasolineras'),
        ('5246', '[5246] Vendedores de comidas al mostrador'),
        ('5249', '[5249] Vendedores no clasificados bajo otros epigrafes'),
        ('5311', '[5311] Cuidadores de ninos'),
        ('5312', '[5312] Ayudantes de maestros'),
        ('5321', '[5321] Trabajadores de los cuidados personales en instituciones'),
        ('5322', '[5322] Trabajadores de los cuidados personales a domicilio'),
        ('5329', '[5329] Otros trabajadores de los cuidados personales en servicios de salud'),
        ('5411', '[5411] Bomberos'),
        ('5412', '[5412] Policias e inspectores de transito'),
        ('5413', '[5413] Guardianes de prision'),
        ('5414', '[5414] Guardas de proteccion en establecimientos'),
        ('5415', '[5415] Vigilante de casas particulares'),
        ('5419', '[5419] Otros trabajadores que prestan servicios de proteccion y vigilancia'),
        ('6111', '[6111] Agricultores y trabajadores calificados de tuberculos, cereales, frutas, plantas'),
        ('6112', '[6112] Agricultores y trabajadores calificados de plantaciones de arboles y arbustos'),
        ('6113', '[6113] Agricultores y trabajadores calificados de jardines, hortalizas, follajes y otros'),
        ('6114', '[6114] Agricultores y trabajadores calificados de cultivos mixtos'),
        ('6121', '[6121] Criadores de ganado'),
        ('6122', '[6122] Avicultores y trabajadores calificados de la avicultura'),
        ('6123', '[6123] Apicultores y sericultores y trabajadores calificados de la apicultura y la sericicultura'),
        ('6129', '[6129] Otros criadores y trabajadores calificados de la cria de animales no incluidos'),
        ('6130', '[6130] Productores y trabajadores calificados de explotaciones agropecuarias mixtas'),
        ('6210', '[6210] Trabajadores forestales calificados y afines'),
        ('6221', '[6221] Trabajadores de explotaciones de acuicultura'),
        ('6222', '[6222] Pescadores de agua dulce y en aguas costeras'),
        ('6223', '[6223] Pescadores de alta mar'),
        ('6224', '[6224] Cazadores y tramperos'),
        ('6310', '[6310] Trabajadores agricolas de subsistencia'),
        ('6320', '[6320] Trabajadores pecuarios de subsistencia'),
        ('6330', '[6330] Trabajadores agropecuarios de subsistencia'),
        ('6340', '[6340] Pescadores, cazadores, tramperos y recolectores de subsistencia'),
        ('7111', '[7111] Albaniles'),
        ('7112', '[7112] Mamposteros, tronzadores, labrantes y grabadores de piedra'),
        ('7113', '[7113] Operarios en cemento armado, encofradores y afines'),
        ('7114', '[7114] Carpinteros de armar y de obra blanca'),
        ('7119', '[7119] Operarios de la construccion en obra gruesa y afines no clasificados bajo otros'),
        ('7121', '[7121] Techadores'),
        ('7122', '[7122] Revestidores e instaladores de pisos'),
        ('7123', '[7123] Revocadores'),
        ('7124', '[7124] Instaladores de material aislante y de insonorizacion'),
        ('7125', '[7125] Cristaleros'),
        ('7126', '[7126] Fontaneros e instaladores de tuberias'),
        ('7127', '[7127] Mecanicos de instalaciones de refrigeracion y aire acondicionado'),
        ('7131', '[7131] Pintores y empapeladores'),
        ('7132', '[7132] Barnizadores, pintores de vehiculos y afines'),
        ('7133', '[7133] Limpiadores de fachadas y deshollinadores'),
        ('7211', '[7211] Moldeadores de metal'),
        ('7212', '[7212] Soldadores y oxicortadores'),
        ('7213', '[7213] Chapistas y caldereros'),
        ('7214', '[7214] Montadores de estructuras metalicas'),
        ('7215', '[7215] Aparejadores y empalmadores de cables'),
        ('7221', '[7221] Herreros y forjadores'),
        ('7222', '[7222] Herramentistas y afines'),
        ('7223', '[7223] Reguladores y operadores de maquinas herramientas'),
        ('7224', '[7224] Pulidores de metales y afiladores de herramientas'),
        ('7231', '[7231] Mecanicos y reparadores de vehiculos de motor'),
        ('7232', '[7232] Mecanicos y reparadores de motores de avion'),
        ('7233', '[7233] Mecanicos y reparadores de maquinas agricolas e industriales'),
        ('7234', '[7234] Reparadores de bicicletas y afines'),
        ('7311', '[7311] Mecanicos y reparadores de instrumentos de precision'),
        ('7312', '[7312] Fabricantes y afinadores de instrumentos musicales'),
        ('7313', '[7313] Joyeros, orfebres y plateros'),
        ('7314', '[7314] Alfareros y afines (barro, arcilla y abrasivos)'),
        ('7315', '[7315] Sopladores, modeladores, laminadores, cortadores y pulidores de vidrio'),
        ('7316', '[7316] Escritores de carteles, pintores decorativos y grabadores'),
        ('7317', '[7317] Artesanos en madera, cesteria y materiales similares'),
        ('7318', '[7318] Artesanos de los textiles, el cuero y materiales similares'),
        ('7319', '[7319] Artesanos no clasificados bajo otros epigrafes'),
        ('7321', '[7321] Cajistas, tipografos y afines'),
        ('7322', '[7322] Impresores'),
        ('7323', '[7323] Encuadernadores y afines'),
        ('7411', '[7411] Electricistas de obras y afines'),
        ('7412', '[7412] Mecanicos y ajustadores electricistas'),
        ('7413', '[7413] Instaladores y reparadores de lineas electricas'),
        ('7421', '[7421] Mecanicos y reparadores en electronica'),
        ('7422', '[7422] Instaladores y reparadores en tecnologia de la informacion y las comunicaciones'),
        ('7511', '[7511] Carniceros, pescadores y afines'),
        ('7512', '[7512] Panaderos, pasteleros, golosineros y confiteros'),
        ('7513', '[7513] Operarios de la elaboracion de productos lacteos'),
        ('7514', '[7514] Operarios de la conservacion de frutas, legumbres, verduras y afines'),
        ('7515', '[7515] Catadores y clasificadores de alimentos y bebidas'),
        ('7516', '[7516] Preparadores y elaboradores de tabaco y sus productos'),
        ('7521', '[7521] Operarios del tratamiento de la madera'),
        ('7522', '[7522] Ebanistas y afines'),
        ('7523', '[7523] Reguladores y operadores de maquinas de labrar madera'),
        ('7531', '[7531] Sastres, modistas, peleteros, sombrereros y costureros'),
        ('7532', '[7532] Patronistas y cortadores de tela, cuero y afines'),
        ('7533', '[7533] Bordadores y afines'),
        ('7534', '[7534] Tapiceros, colchoneros y afines'),
        ('7535', '[7535] Apelambradores, pellejeros y curtidores'),
        ('7536', '[7536] Zapateros y afines'),
        ('7542', '[7542] Dinamiteros y pegadores'),
        ('7543', '[7543] Clasificadores y probadores de productos (excluyendo alimentos y bebidas)'),
        ('7544', '[7544] Fumigadores y otros controladores de plagas y malas hierbas'),
        ('7549', '[7549] Operarios y artesanos de artes mecanicas y de otros oficios no clasificados bajo otros'),
        ('8111', '[8111] Mineros y operadores de instalaciones mineras'),
        ('8112', '[8112] Operadores de instalaciones de procesamiento de minerales y rocas'),
        ('8113', '[8113] Perforadores y sondistas de pozos y afines'),
        ('8114', '[8114] Operadores de maquinas para fabricar cemento y otros productos minerales'),
        ('8121', '[8121] Operadores de instalaciones de procesamiento de metales'),
        ('8122', '[8122] Operadores de maquinas pulidoras, galvanizadoras y recubridoras de metales'),
        ('8131', '[8131] Operadores de plantas y maquinas de productos quimicos'),
        ('8132', '[8132] Operadores de maquinas para fabricar productos fotograficos'),
        ('8141', '[8141] Operadores de maquinas para fabricar productos de caucho'),
        ('8142', '[8142] Operadores de maquinas para fabricar productos de material plastico'),
        ('8143', '[8143] Operadores de maquinas para fabricar productos de papel'),
        ('8151', '[8151] Operadores de maquinas de preparacion de fibras, hilado y devanado'),
        ('8152', '[8152] Operadores de telares y otras maquinas tejedoras'),
        ('8153', '[8153] Operadores de maquinas de coser'),
        ('8154', '[8154] Operadores de maquinas de blanqueamiento, tenido y limpieza de tejidos'),
        ('8155', '[8155] Operadores de maquinas de tratamiento de pieles y cueros'),
        ('8156', '[8156] Operadores de maquinas para la fabricacion de calzado y afines'),
        ('8157', '[8157] Operadores de maquinas lavarropas'),
        ('8159', '[8159] Operarios de maquinas para fabricar productos textiles y articulos de piel y cuero'),
        ('8160', '[8160] Operadores de maquinas para elaborar alimentos y productos afines'),
        ('8171', '[8171] Operadores de instalaciones y maquinas para la preparacion de pasta para papel'),
        ('8172', '[8172] Operadores de instalaciones y maquinas de procesamiento de la madera'),
        ('8181', '[8181] Operadores de instalaciones y maquinas de vidrieria y ceramica'),
        ('8182', '[8182] Operadores de maquinas de vapor y calderas'),
        ('8183', '[8183] Operadores de maquinas de embalaje, embotellamiento y etiquetado'),
        ('8189', '[8189] Operadores de maquinas y de instalaciones fijas no clasificados bajo otros'),
        ('8211', '[8211] Ensambladores de maquinaria mecanica'),
        ('8212', '[8212] Ensambladores de equipos electricos y electronicos'),
        ('8219', '[8219] Ensambladores no clasificados bajo otros epigrafes'),
        ('8311', '[8311] Maquinistas de locomotoras'),
        ('8312', '[8312] Guardafrenos, guardagujas y agentes de maniobras en vias ferreas'),
        ('8321', '[8321] Conductores de motocicletas'),
        ('8322', '[8322] Conductores de automoviles, taxis y camionetas'),
        ('8331', '[8331] Conductores de autobuses y tranvias'),
        ('8332', '[8332] Conductores de camiones pesados'),
        ('8341', '[8341] Operadores de maquinaria agricola y forestal movil'),
        ('8342', '[8342] Operadores de maquinas de movimiento de tierras y afines'),
        ('8343', '[8343] Operadores de gruas, aparatos elevadores y afines'),
        ('8344', '[8344] Operadores de autoelevadoras'),
        ('8350', '[8350] Marineros de cubierta y afines'),
        ('9111', '[9111] Limpiadores y asistentes domesticos'),
        ('9112', '[9112] Limpiadores y asistentes de oficinas, hoteles y otros establecimientos'),
        ('9121', '[9121] Lavanderos y planchadores manuales'),
        ('9122', '[9122] Lavadores de vehiculos'),
        ('9123', '[9123] Lavadores de ventanas'),
        ('9129', '[9129] Otro personal de limpieza'),
        ('9211', '[9211] Peones de explotaciones agricolas'),
        ('9212', '[9212] Peones de explotaciones ganaderas'),
        ('9213', '[9213] Peones de explotaciones de cultivos mixtos y ganaderos'),
        ('9214', '[9214] Peones de jardineria'),
        ('9215', '[9215] Peones forestales'),
        ('9216', '[9216] Peones de pesca y acuicultura'),
        ('9311', '[9311] Peones de minas y canteras'),
        ('9312', '[9312] Peones de obras publicas y mantenimiento'),
        ('9313', '[9313] Peones de la construccion de edificios'),
        ('9321', '[9321] Empacadores manuales'),
        ('9329', '[9329] Peones de la industria manufacturera no clasificados bajo otros epigrafes'),
        ('9331', '[9331] Conductores de vehiculos accionados a pedal o a brazo'),
        ('9332', '[9332] Conductores de vehiculos y maquinas de traccion animal'),
        ('9333', '[9333] Peones de carga'),
        ('9334', '[9334] Reponedores de estanterias'),
        ('9411', '[9411] Cocineros de comidas rapidas'),
        ('9412', '[9412] Ayudantes de cocina'),
        ('9510', '[9510] Trabajadores ambulantes de servicios y afines'),
        ('9520', '[9520] Vendedores ambulantes (excluyendo de comida para consumo inmediato)'),
        ('9611', '[9611] Recolectores de basura y material reciclable'),
        ('9612', '[9612] Clasificadores de desechos'),
        ('9613', '[9613] Barrenderos y afines'),
        ('9621', '[9621] Mensajeros, mandaderos, maleteros y repartidores'),
        ('9622', '[9622] Recolectores de dinero en aparatos de venta automatica y lectores de medidores'),
        ('9623', '[9623] Acarreadores de agua y recolectores de lena'),
        ('9629', '[9629] Ocupaciones elementales no clasificadas bajo otros epigrafes'),
    ], string='Ocupacion COCR-2023 (INS)',
       help='Clasificacion de Ocupaciones de Costa Rica COCR-2023. Fuente: INEC Costa Rica.')

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

    # -- Datos Medicos (INS / Emergencias) ---------------------------
    blood_type = fields.Selection([
        ('A+',  'A+'),
        ('A-',  'A-'),
        ('B+',  'B+'),
        ('B-',  'B-'),
        ('AB+', 'AB+'),
        ('AB-', 'AB-'),
        ('O+',  'O+'),
        ('O-',  'O-'),
    ], string='Tipo de Sangre',
       help='Tipo de sangre del empleado. Requerido por el INS para el expediente de Riesgos del Trabajo.')

    medical_notes = fields.Text(
        string='Diagnostico / Notas Medicas',
        help='Informacion medica relevante del empleado: diagnosticos previos, alergias, '
             'condiciones cronicas, medicamentos, o cualquier nota relevante para '
             'el INS o en caso de accidente laboral.'
    )

    # -- Datos CCSS --------------------------------------------------
    ccss_number = fields.Char(string='Numero CCSS')
    ccss_insured = fields.Boolean(string='Asegurado CCSS', default=True)

    # -- Datos bancarios ---------------------------------------------
    bank_account_number = fields.Char(string='Numero de Cuenta Bancaria')
    bank_iban = fields.Char(
        string='IBAN',
        help='Formato IBAN costarricense: CR + 20 digitos. Ej: CR65015200000000000000'
    )
    sinpe_phone = fields.Char(
        string='Telefono SINPE Movil',
        size=8,
        help='Numero de 8 digitos registrado en SINPE Movil para pago de planilla.'
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

    # -- Fechas importantes ------------------------------------------
    entry_date = fields.Date(string='Fecha de Ingreso')
    exit_date = fields.Date(string='Fecha de Salida')

    # -- Saldo de Vacaciones (Art. 153 Codigo de Trabajo CR) ----------
    planilla_vacation_ids = fields.One2many(
        'planilla.vacation.payment', 'employee_id',
        string='Vacaciones'
    )

    # -- Saldo inicial pre-implementacion -----------------------------
    # Cuando una empresa instala el sistema con empleados ya activos,
    # estos tienen un saldo de vacaciones real que difiere del calculado
    # desde la fecha de ingreso (porque ya tomaron dias antes del sistema).
    # Estos dos campos permiten "arrancar" desde el saldo correcto.
    # -- Aguinaldo inicial pre-implementacion (Art. 228 CT) --------
    aguinaldo_initial_amount = fields.Float(
        string='Acumulado Aguinaldo Inicial (CRC)',
        default=0.0,
        help='Monto de aguinaldo acumulado ANTES de implementar el sistema. '
             'Ejemplo: si la empresa arranco en abril 2026, '
             'colocar aqui el acumulado diciembre 2025 - marzo 2026. '
             'El sistema sumara este monto al aguinaldo calculado con las boletas del sistema.'
    )
    aguinaldo_initial_date = fields.Date(
        string='Fecha de Corte Aguinaldo',
        help='Fecha hasta la cual fue calculado el acumulado inicial. '
             'El sistema usara este campo para saber a partir de cuando '
             'las boletas del sistema ya cubren el aguinaldo (evita doble conteo).'
    )

    vacation_initial_balance = fields.Float(
        string='Saldo Inicial de Vacaciones (dias)',
        default=0.0,
        help='Dias de vacaciones disponibles al momento de la implementacion del sistema.\n'
             'Use este campo cuando el saldo real del empleado difiere del calculado\n'
             'automaticamente desde la fecha de ingreso (empleados pre-existentes).\n'
             'El sistema sumara este saldo al calculo normal a partir de la fecha de corte.\n'
             'Si queda en 0, el sistema calcula todo desde la fecha de ingreso.'
    )
    vacation_initial_balance_date = fields.Date(
        string='Fecha de Corte del Saldo Inicial',
        help='Fecha exacta hasta la cual se calculo el saldo inicial.\n'
             'El sistema acumulara dias adicionales a partir de esta fecha.\n'
             'Ejemplo: si la empresa arranca en Enero 2026, ponga 31/12/2025\n'
             'y en "Saldo Inicial" los dias reales disponibles a esa fecha.'
    )

    vacation_days_accrued = fields.Float(
        string='Dias Acumulados',
        compute='_compute_vacation_balance', store=True,
        help='Dias ganados: 12 dias habiles por cada 50 semanas trabajadas (Art. 153 CT)'
    )
    vacation_days_taken = fields.Float(
        string='Dias Tomados',
        compute='_compute_vacation_balance', store=True,
        help='Dias de vacaciones ya utilizados en el sistema (estado aprobado o pagado)'
    )
    vacation_days_available = fields.Float(
        string='Dias Disponibles',
        compute='_compute_vacation_balance', store=True,
        help='Saldo disponible = Saldo Inicial + Acumulados desde corte  Tomados en sistema'
    )
    vacation_balance_alert = fields.Boolean(
        string='Alerta Vacaciones',
        compute='_compute_vacation_balance', store=True,
        help='True si el empleado tiene saldo negativo de vacaciones'
    )

    # -- Prestamos y Adelantos ---------------------------------------
    loan_ids = fields.One2many(
        'planilla.employee.loan', 'employee_id', string='Prestamos'
    )
    loan_active_count = fields.Integer(
        string='Prestamos Activos', compute='_compute_loan_summary', store=True
    )
    loan_pending_amount = fields.Monetary(
        string='Saldo Prestamos Pendiente', currency_field='currency_id',
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

    # -- Historial de salarios ---------------------------------------
    recurring_benefit_ids = fields.One2many(
        'planilla.recurring.benefit', 'employee_id',
        string='Beneficios/Deducciones Recurrentes'
    )
    embargo_ids = fields.One2many(
        'planilla.embargo', 'employee_id',
        string='Embargos Judiciales'
    )
    bono_ids = fields.One2many(
        'planilla.bono', 'employee_id',
        string='Bonos e Incentivos'
    )
    employee_charge_ids = fields.One2many(
        'planilla.employee.charge', 'employee_id',
        string='Cobros al Empleado'
    )
    salary_history_ids = fields.One2many(
        'planilla.salary.history', 'employee_id', string='Historial de Salarios'
    )

    # ------------------------------------------------------------------
    # SYNC CON MODULO NATIVO DE EMPLEADOS (hr module)
    # ------------------------------------------------------------------

    @api.onchange('base_salary')
    def _onchange_base_salary_sync_contract(self):
        """Sincroniza salario al contrato HR si hr_payroll esta instalado."""
        if not self.base_salary or 'hr.contract' not in self.env:
            return
        contract = self._get_or_create_contract()
        if contract and contract.wage != self.base_salary:
            contract.wage = self.base_salary

    @api.onchange('entry_date')
    def _onchange_entry_date_sync_contract(self):
        """Sincroniza fecha ingreso al contrato HR si hr_payroll esta instalado."""
        if not self.entry_date or 'hr.contract' not in self.env:
            return
        contract = self._get_or_create_contract()
        if contract and contract.date_start != self.entry_date:
            contract.date_start = self.entry_date

    def _get_active_contract(self):
        """Retorna el contrato activo/borrador del empleado si existe (sin crear).
        Retorna None si hr.contract no esta disponible (hr_payroll no instalado)."""
        self.ensure_one()
        if not self.id or 'hr.contract' not in self.env:
            return None
        return self.env['hr.contract'].search([
            ('employee_id', '=', self.id),
            ('state', 'in', ('open', 'draft')),
        ], limit=1, order='date_start desc') or None

    def _get_or_create_contract(self):
        """
        Retorna el contrato activo del empleado.
        Si no existe, CREA uno con salario, fecha ingreso y puesto de Planilla CR.
        Retorna None si hr.contract no esta disponible.
        """
        self.ensure_one()
        if not self.id or 'hr.contract' not in self.env:
            return None
        contract = self._get_active_contract()
        if contract:
            return contract
        # Crear contrato si no existe
        vals = {
            'employee_id':  self.id,
            'company_id':   self.company_id.id or self.env.company.id,
            'name':         'Contrato %s' % (self.name or ''),
            'date_start':   self.entry_date or fields.Date.context_today(self),
            'wage':         self.base_salary or 0.0,
            'state':        'open',
        }
        if self.job_id:
            vals['job_id'] = self.job_id.id
        if self.department_id:
            vals['department_id'] = self.department_id.id
        try:
            contract = self.env['hr.contract'].with_context(
                skip_salary_history=True
            ).create(vals)
            return contract
        except Exception:
            return None

    def action_sync_to_native_hr(self):
        """
        Sincroniza datos de Planilla CR al contrato nativo de HR.
        Si el empleado no tiene contrato, lo CREA automaticamente.
        Sincroniza: salario, fecha ingreso, puesto, departamento.
        """
        created = 0
        updated = 0
        for emp in self:
            existing = emp._get_active_contract()
            contract = emp._get_or_create_contract()
            if not contract:
                continue
            if not existing:
                created += 1
                continue  # ya se creo con los datos correctos
            # Actualizar si ya existia
            sync_vals = {}
            if emp.base_salary and contract.wage != emp.base_salary:
                sync_vals['wage'] = emp.base_salary
            if emp.entry_date and contract.date_start != emp.entry_date:
                sync_vals['date_start'] = emp.entry_date
            if emp.job_id and contract.job_id.id != emp.job_id.id:
                sync_vals['job_id'] = emp.job_id.id
            if emp.department_id and contract.department_id.id != emp.department_id.id:
                sync_vals['department_id'] = emp.department_id.id
            if sync_vals:
                contract.with_context(skip_salary_history=True).write(sync_vals)
                updated += 1
        msg_parts = []
        if created:
            msg_parts.append('%s contrato(s) creado(s)' % created)
        if updated:
            msg_parts.append('%s contrato(s) actualizado(s)' % updated)
        msg = ', '.join(msg_parts) if msg_parts else 'Todo ya estaba sincronizado'
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sincronizacion completada',
                'message': msg,
                'type': 'success',
                'sticky': False,
            }
        }
    salary_history_count = fields.Integer(
        compute='_compute_salary_history_count', string='Salarios Registrados'
    )

    # -- Computos ----------------------------------------------------
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

    amonestacion_ids = fields.One2many(
        'planilla.amonestacion', 'employee_id', string='Amonestaciones'
    )
    amonestacion_count = fields.Integer(
        string='Amonestaciones', compute='_compute_amonestacion_count', store=False
    )

    def _compute_amonestacion_count(self):
        for emp in self:
            emp.amonestacion_count = self.env['planilla.amonestacion'].search_count([
                ('employee_id', '=', emp.id),
                ('state', 'in', ('issued', 'acknowledged')),
            ])

    def action_view_amonestaciones(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Amonestaciones',
            'res_model': 'planilla.amonestacion',
            'view_mode': 'list,form',
            'domain': [('employee_id', '=', self.id)],
            'context': {'default_employee_id': self.id},
        }

    def action_print_constancia_laboral(self):
        return self.env.ref('planilla_cr.action_report_constancia_laboral').report_action(self)

    @api.model_create_multi
    def create(self, vals_list):
        employees = super().create(vals_list)
        for employee in employees:
            if employee.base_salary:
                # FIX BUG-N10 v52: guardar tambien gross_salary en el historial inicial.
                # Sin esto, el primer registro tiene gross_salary=0, lo que causa que
                # overtime._compute_hourly_rate() y disability._compute_daily_salary()
                # no encuentren salario historico valido para la fecha de ingreso.
                self.env['planilla.salary.history'].create({
                    'employee_id': employee.id,
                    'salary': employee.base_salary,
                    'gross_salary': employee.base_salary,  # FIX: tambien el bruto
                    'effective_date': employee.salary_effective_date or fields.Date.context_today(self),
                    'reason': 'Salario Inicial',
                    # FIX-G1: state='authorized' para que las consultas de promedio
                    # (vacaciones Art.153, liquidaciones, simulador) encuentren este
                    # registro. Sin esto queda en 'draft' y es invisible para el promedio.
                    'state': 'authorized',
                    'authorized_by': self.env.user.id,
                    'authorized_date': fields.Datetime.now(),
                })
        for employee in employees:
            self.env['planilla.employee.movement'].create({
                'employee_id':   employee.id,
                'movement_date': employee.entry_date or fields.Date.context_today(self),
                'movement_type': 'ingreso',
                'reason':        'Ingreso de empleado',
                'salary_after':  employee.base_salary or 0.0,
                'company_id':    employee.company_id.id,
            })
        return employees

    def write(self, vals):
        # Sync a contrato nativo al guardar cambios relevantes
        sync_fields = {'base_salary', 'entry_date', 'job_id'}
        needs_sync = bool(sync_fields & set(vals.keys()))
        old_salaries = {emp.id: emp.base_salary for emp in self} if 'base_salary' in vals else {}
        result = super().write(vals)
        if 'base_salary' in vals:
            # FIX-Q15: si skip_salary_history=True en contexto, no crear historial.
            # Evita duplicado cuando salary_history.action_authorize actualiza base_salary:
            # ese registro ya existe (el que se esta autorizando), no debe crearse otro.
            if self.env.context.get('skip_salary_history'):
                self._check_minimum_salary_warning()
                return result
            # FIX-Q5: leer razon del contexto para que wizards (salary_increase_wizard)
            # puedan personalizar la razon sin crear un registro duplicado.
            # Si no hay razon en el contexto, usar el valor por defecto 'Ajuste Salarial'.
            salary_reason = self.env.context.get('salary_history_reason', 'Ajuste Salarial')
            salary_note   = self.env.context.get('salary_history_note', False)
            for employee in self:
                old_sal = old_salaries.get(employee.id, 0.0)
                if employee.base_salary and employee.base_salary != old_sal:
                    self.env['planilla.salary.history'].create({
                        'employee_id': employee.id,
                        'salary': employee.base_salary,
                        'gross_salary': employee.base_salary,  # FIX BUG-N10 v52
                        'effective_date': vals.get('salary_effective_date') or fields.Date.context_today(self),
                        'reason': salary_reason,
                        'note':   salary_note,
                        # FIX-G1: state='authorized' -- mismo fix que action_pay (D1).
                        'state': 'authorized',
                        'authorized_by': self.env.user.id,
                        'authorized_date': fields.Datetime.now(),
                    })
            self._check_minimum_salary_warning()
        # Sync campos nativos de hr.employee (Odoo 19: wage y contract_date_start
        # son campos directos en hr.employee, no en hr.contract)
        if needs_sync:
            emp_fields = self[0]._fields if self else {}
            for emp in self:
                try:
                    direct_sync = {}
                    if 'base_salary' in vals and 'wage' in emp_fields:
                        if emp.base_salary and emp.wage != emp.base_salary:
                            direct_sync['wage'] = emp.base_salary
                    if 'entry_date' in vals and 'contract_date_start' in emp_fields:
                        if emp.entry_date and emp.contract_date_start != emp.entry_date:
                            direct_sync['contract_date_start'] = emp.entry_date
                    if direct_sync:
                        emp.with_context(skip_salary_history=True).write(direct_sync)
                except Exception:
                    pass  # No bloquear el guardado
        return result

    @api.depends('entry_date', 'exit_date',
                 'vacation_initial_balance', 'vacation_initial_balance_date',
                 'planilla_vacation_ids.state', 'planilla_vacation_ids.days',
                 'planilla_vacation_ids.vacation_type')
    def _compute_vacation_balance(self):
        """
        Art. 153 CT CR: 12 dias habiles por cada 50 semanas laboradas.

        Logica con saldo inicial pre-implementacion:
          Si el empleado tiene vacation_initial_balance (saldo real a una fecha de corte):
            1. Toma el saldo inicial como punto de partida.
            2. Calcula dias acumulados SOLO desde vacation_initial_balance_date hasta hoy.
            3. Resta los dias tomados en el sistema (vacation.payment aprobados/pagados).
            Saldo = vacation_initial_balance + dias_acumulados_desde_corte - dias_tomados

          Si NO tiene saldo inicial (instalacion desde cero):
            Calcula todo desde entry_date como antes.

        Art. 153 parrafo 2: incapacidades > 3 meses continuos NO cuentan
        como tiempo trabajado para el calculo de vacaciones.
        """
        for emp in self:
            if not emp.entry_date:
                emp.vacation_days_accrued = 0.0
                emp.vacation_days_taken = 0.0
                emp.vacation_days_available = 0.0
                emp.vacation_balance_alert = False
                continue

            cutoff = emp.exit_date or date.today()

            # -- Descontar incapacidades > 3 meses continuos (Art. 153 CT) --
            disability_days_excluded = 0
            long_disabilities = self.env['planilla.disability'].search([
                ('employee_id', '=', emp.id),
                ('state', 'in', ('confirmed', 'paid')),
                ('days', '>', 90),
            ])
            for dis in long_disabilities:
                disability_days_excluded += max(dis.days - 90, 0)

            # -- Determinar desde cuando acumular -----------------------------
            # FIX: usar logica con saldo inicial si se configuro una fecha de corte,
            # independientemente del valor del saldo (puede ser 0 o negativo).
            # Antes: solo activaba si vacation_initial_balance > 0, ignorando
            # empleados con saldo 0 o negativo que igual tienen fecha de corte.
            has_initial = bool(emp.vacation_initial_balance_date)

            if has_initial:
                # Acumular solo desde la fecha de corte del saldo inicial
                accrual_start = emp.vacation_initial_balance_date
                # No acumular si la fecha de corte es futura o igual a hoy
                if accrual_start >= cutoff:
                    accrued_since_cutoff = 0.0
                else:
                    days_since_cutoff = (cutoff - accrual_start).days
                    # Descontar solo incapacidades que caen DESPUES del corte
                    long_dis_after_cutoff = self.env['planilla.disability'].search([
                        ('employee_id', '=', emp.id),
                        ('state', 'in', ('confirmed', 'paid')),
                        ('days', '>', 90),
                        ('date_start', '>=', accrual_start),
                    ])
                    dis_after = sum(max(d.days - 90, 0) for d in long_dis_after_cutoff)
                    effective_days = max(days_since_cutoff - dis_after, 0)
                    weeks_since_cutoff = effective_days / 7.0
                    # Art. 153 CT: acumular con 2 decimales para precision
                    # pero el saldo disponible final se muestra como entero
                    accrued_since_cutoff = round((weeks_since_cutoff / 50.0) * 12.0, 2)

                # Saldo = inicial (puede tener decimales) + acumulado desde corte
                # Redondear al entero inferior para ser conservador (Art. 153 CT)
                accrued = int(emp.vacation_initial_balance + accrued_since_cutoff)
            else:
                # Calculo normal desde fecha de ingreso
                total_days = (cutoff - emp.entry_date).days
                effective_days = max(total_days - disability_days_excluded, 0)
                weeks_worked = effective_days / 7.0
                accrued = int((weeks_worked / 50.0) * 12.0)

            # Dias tomados en el sistema (solo registros creados en el sistema)
            taken_recs = self.env['planilla.vacation.payment'].search([
                ('employee_id', '=', emp.id),
                ('state', 'in', ['approved', 'paid']),
                ('vacation_type', 'in', ['disfrutadas', 'adelanto']),
            ])
            taken = int(sum(taken_recs.mapped('days')))

            available = accrued - taken  # Ya son enteros
            emp.vacation_days_accrued   = accrued
            emp.vacation_days_taken     = taken
            emp.vacation_days_available = available
            emp.vacation_balance_alert  = available < 0


    def _check_minimum_salary_warning(self):
        """FIX B-08 v53: Advertencia de salario minimo como notificacion (no UserError).
        UserError bloqueaba el wizard de aumento masivo si algun empleado quedaba bajo minimo,
        haciendo imposible hacer el ajuste. Ahora registra mensaje en el chatter del empleado.
        """
        min_salary = self.env['planilla.minimum.salary'].get_current_minimum()
        if not min_salary:
            return
        for emp in self:
            if emp.base_salary and emp.base_salary < min_salary:
                emp.message_post(
                    body=(
                        f'WARN <b>Advertencia Salario Minimo MTSS:</b> '
                        f'El salario base de {emp.name} (CRC{emp.base_salary:,.2f}) '
                        f'esta por debajo del minimo vigente (CRC{min_salary:,.2f}). '
                        f'Revise Configuracion -> Salarios Minimos MTSS.'
                    ),
                    message_type='notification',
                )

    # -- Validacion IBAN ----------------------------------------------
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
