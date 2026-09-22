"""Tests for how SkyPilot reconciles the RBAC objects it owns.

SkyPilot upserts its own Role/ClusterRole against the template on every
launch. That is correct for picking up a new release's rules, and it also
silently undoes a narrowing an operator applied on purpose -- so the one
thing these tests pin is that the overwrite is *announced*.
"""
from unittest import mock

from sky.provision.kubernetes import config


def _rule(verbs, resources=('pods',), groups=('',)):
    return mock.Mock(api_groups=list(groups),
                     resources=list(resources),
                     verbs=list(verbs))


def _upsert(existing_rules, new_rules):
    """Drive the upsert against an object that already exists.

    Asserts on the logger directly rather than via caplog: SkyPilot's
    loggers do not propagate to the root handler caplog installs.
    """
    existing = mock.Mock(rules=existing_rules)
    patch_fn = mock.Mock()
    with mock.patch.object(config, 'logger') as logger:
        config._create_or_patch_resource(
            log_prefix='_configure_autoscaler_role',
            resource_field='autoscaler_role',
            name='skypilot-service-account-role',
            create_fn=mock.Mock(),
            list_fn=lambda: mock.Mock(items=[existing]),
            patch_fn=patch_fn,
            needs_update_fn=lambda cur: cur.rules != new_rules,
            new_rules=new_rules,
        )
    return patch_fn, logger


def test_narrowed_rules_are_restored_with_a_warning():
    """An operator-narrowed Role is patched back, and says so at WARNING."""
    narrowed = [_rule(['get'])]
    template = [_rule(['get', 'list', 'delete'])]

    patch_fn, logger = _upsert(narrowed, template)

    patch_fn.assert_called_once()
    assert logger.warning.called, 'overwriting an existing role must not be silent'
    message = logger.warning.call_args[0][0]
    # Both sides of the overwrite, so the operator can see what was lost.
    assert "'get'" in message and "'delete'" in message
    # And the supported way to keep a narrowed set.
    assert 'remote_identity' in message


def test_unchanged_rules_do_not_warn():
    """The common case -- rules already match -- stays quiet and patches nothing."""
    same = [_rule(['get', 'list'])]

    patch_fn, logger = _upsert(same, same)

    patch_fn.assert_not_called()
    logger.warning.assert_not_called()
