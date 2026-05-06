"""Deprecation messages emitted to stderr when callers reach the
master ``autostop.hook`` / ``--autostop`` / ``tail_autostop_logs``
surfaces. The ``resources.hooks`` framework supersedes them; the
messages route users to the new APIs while we keep the old ones
working through the deprecation window.

# TODO(zpoint): remove this module ~2 minors after the lifecycle-hooks
# framework ships.
"""

AUTOSTOP_HOOK_YAML = (
    'WARNING: autostop.hook / autostop.hook_timeout are deprecated. '
    'Use resources.hooks: [{run, events: [autostop], timeout}] '
    'instead (routed for you).\n')

TAIL_AUTOSTOP_LOGS_SDK = (
    'WARNING: sky.tail_autostop_logs() is deprecated. '
    'Use sky.tail_hook_logs(cluster_name, event=\'autostop\') instead.\n')

LOGS_AUTOSTOP_FLAG = ('WARNING: `sky logs --autostop` is deprecated. '
                      'Use `sky logs --hook autostop` instead.\n')
