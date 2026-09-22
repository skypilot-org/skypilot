"""Tests for how SkyPilot reconciles the RBAC objects it owns.

Two things are pinned here. That the overwrite of an operator's Role is
announced rather than silent, and that bootstrap tolerates the RBAC fields a
workload cluster's template deliberately omits -- the template renders the
provisioner-only roles for controller clusters only, so "may be absent" is a
state every consumer of provider_config has to handle.

The upsert is correct for picking up a new release's rules and equally undoes
a narrowing an operator applied on purpose; SkyPilot cannot tell them apart,
so it must at least say what it is doing.
"""
import json
import pathlib
from unittest import mock

import pytest

from sky.provision import common as provision_common
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


_GOLDEN_DIR = (pathlib.Path(__file__).parents[1] / 'test_sky' / 'clouds' /
               'testdata' / 'kubernetes_ray_template')


def _provider_config(case: str):
    """The provider block a rendered template actually produces."""
    rendered = json.loads((_GOLDEN_DIR / f'{case}.json').read_text())
    provider = dict(rendered['provider'])
    provider['namespace'] = 'default'
    provider['skypilot_system_namespace'] = 'skypilot-system'
    return provider


@pytest.mark.parametrize('case,service_account', [
    ('base_cpu', 'skypilot-service-account'),
    ('controller', 'skypilot-controller-service-account'),
])
def test_bootstrap_tolerates_omitted_rbac_fields(case, service_account):
    """bootstrap_instances survives whichever RBAC fields the template omits.

    A workload cluster renders none of the provisioner-only roles, so every
    configurer reached from bootstrap must cope with the field being absent.
    The template tests cannot catch a consumer that does not -- they assert
    what is rendered, not what reads it.
    """
    cfg = provision_common.ProvisionConfig(
        provider_config=_provider_config(case),
        authentication_config={},
        docker_config={},
        node_config={'spec': {
            'serviceAccountName': service_account
        }},
        count=1,
        tags={},
        resume_stopped_nodes=False,
        ports_to_open_on_launch=None,
    )
    with mock.patch.object(config, '_configure_services'), \
         mock.patch.object(config, '_create_or_patch_resource'), \
         mock.patch.object(config.kubernetes_utils, 'create_namespace'), \
         mock.patch.object(config.kubernetes_utils, 'dict_to_k8s_object'), \
         mock.patch.object(config.kubernetes_utils,
                           'get_namespace_from_config',
                           return_value='default'), \
         mock.patch.object(config.kubernetes_utils,
                           'get_context_from_config',
                           return_value=None):
        config.bootstrap_instances('kubernetes', 'test-cluster', cfg)
