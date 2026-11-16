"""Server twists for the recipes, settings and navigation tests (see
framework/server_plugin.py).

Each one makes the server misbehave the way a backend regression would, for
the one test whose requests carry its id.
"""
from framework.server_plugin import register_body_twist

# The server reads the recipe list request as "pinned only", so a user's
# unpinned recipes vanish from the hub.
register_body_twist('srv-recipes-list-pinned-only', '/recipes/list', lambda b: {
    **b, 'pinned_only': True
})

# The server ignores the requested pin state and always pins.
register_body_twist('srv-recipes-pin-always-true', '/recipes/pin', lambda b: {
    **b, 'pinned': True
})

# The server stores every new recipe as a cluster recipe, whatever type the
# user picked.
register_body_twist('srv-recipes-create-as-cluster', '/recipes/create',
                    lambda b: {
                        **b, 'recipe_type': 'cluster'
                    })

# The server resolves the recipe to delete wrongly and deletes nothing.
register_body_twist(
    'srv-recipes-delete-wrong-name', '/recipes/delete', lambda b: {
        **b, 'recipe_name': f"{b.get('recipe_name', '')}-missing"
    })


def _drop_config_key(body):
    """The server drops part of an applied config: the kubernetes section."""
    config = body.get('config')
    if isinstance(config, dict):
        body = {
            **body, 'config': {
                k: v for k, v in config.items() if k != 'kubernetes'
            }
        }
    return body


register_body_twist('srv-config-apply-drops-section', '/workspaces/config',
                    _drop_config_key)

# The server rejects the recipes hub's list request: the dashboard and the
# server disagree on the request schema.
register_body_twist('srv-nav-recipes-list-rejected', '/recipes/list',
                    lambda b: {
                        **b, 'pinned_only': 'not-a-bool'
                    })
