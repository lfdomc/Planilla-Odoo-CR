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
    # Obtener el grupo de usuarios del módulo
    group = env.ref('nombramientos_cr.group_nombramientos_user',
                    raise_if_not_found=False)
    _create_access_rights(env, group)
    _create_record_rules(env, group)
    # Dar acceso automático a todos los usuarios internos existentes
    _grant_to_internal_users(env, group)


def _create_access_rights(env, group):
    for xml_id, model_name in MODELS_ACCESS:
        try:
            model = env['ir.model'].sudo().search(
                [('model', '=', model_name)], limit=1)
            if not model:
                _logger.warning(
                    'nombramientos_cr: modelo %s no encontrado', model_name)
                continue
            existing = env['ir.model.access'].sudo().search([
                ('name', '=', xml_id)], limit=1)
            if existing:
                existing.sudo().write({
                    'model_id':    model.id,
                    'group_id':    group.id if group else False,
                    'perm_read':   True, 'perm_write':   True,
                    'perm_create': True, 'perm_unlink':  True,
                })
                continue
            env['ir.model.access'].sudo().create({
                'name':        xml_id,
                'model_id':    model.id,
                'group_id':    group.id if group else False,
                'perm_read':   True, 'perm_write':   True,
                'perm_create': True, 'perm_unlink':  True,
            })
            _logger.info(
                'nombramientos_cr: acceso creado para %s', model_name)
        except Exception as e:
            _logger.error(
                'nombramientos_cr: error acceso %s: %s', model_name, e)


def _create_record_rules(env, group):
    RULES = [
        ('rule_nombramiento_company', 'nombramientos.nombramiento',
         "[('company_id','in',company_ids)]"),
        ('rule_turno_company', 'nombramientos.turno',
         "[('nombramiento_id.company_id','in',company_ids)]"),
        ('rule_config_company', 'nombramientos.config',
         "[('company_id','in',company_ids)]"),
    ]
    for xml_id, model_name, domain in RULES:
        try:
            model = env['ir.model'].sudo().search(
                [('model', '=', model_name)], limit=1)
            if not model:
                continue
            existing = env['ir.rule'].sudo().search(
                [('name', '=', xml_id)], limit=1)
            if existing:
                continue
            vals = {
                'name':         xml_id,
                'model_id':     model.id,
                'domain_force': domain,
                'active':       True,
            }
            if group:
                vals['groups'] = [(4, group.id)]
            env['ir.rule'].sudo().create(vals)
        except Exception as e:
            _logger.error(
                'nombramientos_cr: error regla %s: %s', xml_id, e)


def _grant_to_internal_users(env, group):
    if not group:
        return
    try:
        internal = env.ref('base.group_user', raise_if_not_found=False)
        if not internal:
            return
        users = env['res.users'].sudo().search([
            ('groups_id', 'in', [internal.id]),
            ('groups_id', 'not in', [group.id]),
        ])
        if users:
            group.sudo().write({'users': [(4, u.id) for u in users]})
            _logger.info(
                'nombramientos_cr: acceso dado a %d usuarios', len(users))
    except Exception as e:
        _logger.error('nombramientos_cr: error otorgando acceso: %s', e)
