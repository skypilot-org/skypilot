"""Deprecation messages emitted to stderr when callers reach a
deprecated lifecycle-hooks surface. The canonical form is
``config.hooks:`` at the top level of a task YAML; the legacy
``autostop.hook`` form from master is routed with a one-line stderr
warning so existing YAMLs keep working.

# TODO(zpoint): remove this module after v0.15.0 (aligned with
# the autostop.hook removal pinned at v0.15.0 in
# sky/utils/schemas.py:_AUTOSTOP_SCHEMA).
"""

AUTOSTOP_HOOK_YAML = (
    'WARNING: autostop.hook / autostop.hook_timeout are deprecated. '
    'Use config.hooks: [{run, events: [autostop], timeout}] '
    'instead (routed for you).\n')

TAIL_AUTOSTOP_LOGS_SDK = (
    'WARNING: sky.tail_autostop_logs() is deprecated. '
    'Use sky.tail_hook_logs(cluster_name, event=\'autostop\') instead.\n')

LOGS_AUTOSTOP_FLAG = ('WARNING: `sky logs --autostop` is deprecated. '
                      'Use `sky logs --hook autostop` instead.\n')
