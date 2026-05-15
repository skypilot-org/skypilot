"""Deprecation messages emitted to stderr when callers reach a
deprecated lifecycle-hooks surface. The canonical form is
``config.hooks:`` at the top level of a task YAML; the legacy
``autostop.hook`` YAML field from master is routed with a one-line
stderr warning so existing YAMLs keep working.

# TODO(zpoint): remove this module after v0.15.0 (aligned with
# the autostop.hook removal pinned at v0.15.0 in
# sky/utils/schemas.py:_AUTOSTOP_SCHEMA).
"""

AUTOSTOP_HOOK_YAML = (
    'WARNING: autostop.hook / autostop.hook_timeout are deprecated. '
    'Use config.hooks: [{run, events: [stop|down], timeout}] '
    'instead (routed for you — autodown maps to `down`, autostop maps '
    'to `stop`).\n')
