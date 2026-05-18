import logging
_logger = logging.getLogger(__name__)

MODELS = [
    ('access_nombramiento_all',    'nombramientos.nombramiento'),
    ('access_turno_all',           'nombramientos.turno'),
    ('access_generar_wiz',         'nombramientos.generar.planilla.wizard'),
    ('access_nombramientos_config','nombramientos.config'),
    ('access_shift_template',      'nombramientos.shift.template'),
    ('access_sede_turno',          'nombramientos.sede.turno'),
    ('access_calendario_view',     'nombramientos.calendario'),
]

def post_init_hook(env):
    IrModel = env['ir.model']
    Access  = env['ir.model.access']
    for xml_id, model_name in MODELS:
        model = IrModel.search([('model', '=', model_name)], limit=1)
        if not model:
            _logger.warning('nombramientos_cr: modelo %s no encontrado, omitiendo acceso', model_name)
            continue
        full_id = f'nombramientos_cr.{xml_id}'
        existing = env['ir.model.data'].search([
            ('module', '=', 'nombramientos_cr'),
            ('name',   '=', xml_id),
        ], limit=1)
        if existing:
            continue
        rec = Access.create({
            'name':       xml_id,
            'model_id':   model.id,
            'perm_read':   True,
            'perm_write':  True,
            'perm_create': True,
            'perm_unlink': True,
        })
        env['ir.model.data'].create({
            'module':    'nombramientos_cr',
            'name':      xml_id,
            'model':     'ir.model.access',
            'res_id':    rec.id,
            'noupdate':  False,
        })
        _logger.info('nombramientos_cr: acceso creado para %s', model_name)
    _create_record_rules(env)


def _create_record_rules(env):
    RULES = [
        ('rule_nombramiento_company', 'nombramientos.nombramiento',
         "[('company_id','in',company_ids)]"),
        ('rule_turno_company', 'nombramientos.turno',
         "[('nombramiento_id.company_id','in',company_ids)]"),
        ('rule_config_company', 'nombramientos.config',
         "[('company_id','in',company_ids)]"),
    ]
    IrModel   = env['ir.model']
    IrRule    = env['ir.rule']
    IrData    = env['ir.model.data']
    group_user = env.ref('base.group_user', raise_if_not_found=False)
    for xml_id, model_name, domain in RULES:
        model = IrModel.search([('model', '=', model_name)], limit=1)
        if not model:
            continue
        existing = IrData.search([
            ('module', '=', 'nombramientos_cr'), ('name', '=', xml_id)
        ], limit=1)
        if existing:
            continue
        vals = {
            'name':         f'nombramientos_cr: {xml_id}',
            'model_id':     model.id,
            'domain_force': domain,
            'active':       True,
        }
        if group_user:
            vals['groups'] = [(4, group_user.id)]
        rec = IrRule.create(vals)
        IrData.create({
            'module':   'nombramientos_cr',
            'name':     xml_id,
            'model':    'ir.rule',
            'res_id':   rec.id,
            'noupdate': False,
        })
        _logger.info('nombramientos_cr: regla creada para %s', model_name)
