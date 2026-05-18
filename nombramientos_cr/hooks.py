import logging
_logger = logging.getLogger(__name__)

MODELS_ACCESS = [
    ('access_nombramiento_all',     'nombramientos.nombramiento'),
    ('access_turno_all',            'nombramientos.turno'),
    ('access_generar_wiz',          'nombramientos.generar.planilla.wizard'),
    ('access_nombramientos_config', 'nombramientos.config'),
    ('access_shift_template',       'nombramientos.shift.template'),
    ('access_sede_turno',           'nombramientos.sede.turno'),
    ('access_calendario_view',      'nombramientos.calendario'),
]

def post_init_hook(env):
    _create_access_rights(env)
    _create_record_rules(env)


def _create_access_rights(env):
    for xml_id, model_name in MODELS_ACCESS:
        try:
            model = env['ir.model'].sudo().search(
                [('model', '=', model_name)], limit=1)
            if not model:
                _logger.warning('nombramientos_cr: modelo %s no encontrado', model_name)
                continue
            # Check if already exists
            existing = env['ir.model.access'].sudo().search([
                ('name', '=', xml_id)], limit=1)
            if existing:
                # Ensure it has no group restriction
                existing.sudo().write({
                    'model_id':   model.id,
                    'group_id':   False,
                    'perm_read':  True, 'perm_write':  True,
                    'perm_create':True, 'perm_unlink': True,
                })
                continue
            env['ir.model.access'].sudo().create({
                'name':       xml_id,
                'model_id':   model.id,
                'group_id':   False,
                'perm_read':  True, 'perm_write':  True,
                'perm_create':True, 'perm_unlink': True,
            })
            _logger.info('nombramientos_cr: acceso creado para %s', model_name)
        except Exception as e:
            _logger.error('nombramientos_cr: error creando acceso %s: %s', model_name, e)


def _create_record_rules(env):
    RULES = [
        ('rule_nombramiento_company', 'nombramientos.nombramiento',
         "[('company_id','in',company_ids)]"),
        ('rule_turno_company', 'nombramientos.turno',
         "[('nombramiento_id.company_id','in',company_ids)]"),
        ('rule_config_company', 'nombramientos.config',
         "[('company_id','in',company_ids)]"),
    ]
    group_user = env.ref('base.group_user', raise_if_not_found=False)
    for xml_id, model_name, domain in RULES:
        try:
            model = env['ir.model'].sudo().search(
                [('model', '=', model_name)], limit=1)
            if not model:
                continue
            existing = env['ir.rule'].sudo().search([
                ('name', 'like', xml_id)], limit=1)
            if existing:
                continue
            vals = {
                'name':         xml_id,
                'model_id':     model.id,
                'domain_force': domain,
                'active':       True,
                'global':       not group_user,
            }
            if group_user:
                vals['groups'] = [(4, group_user.id)]
            env['ir.rule'].sudo().create(vals)
            _logger.info('nombramientos_cr: regla creada para %s', model_name)
        except Exception as e:
            _logger.error('nombramientos_cr: error creando regla %s: %s', xml_id, e)
