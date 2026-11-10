"""Tests for Modal cloud provider."""

import ast
import contextlib
import csv
import pathlib
import subprocess
import sys
from types import SimpleNamespace

import jinja2
import pandas as pd
import pytest

from sky import backends
from sky import catalog as sky_catalog
from sky import clouds
from sky import exceptions
from sky import models
from sky import resources as resources_lib
from sky.backends import backend_utils
from sky.backends import cloud_vm_ray_backend
from sky.catalog import common as catalog_common
from sky.catalog import modal_catalog
from sky.catalog.data_fetchers import fetch_modal
from sky.clouds import modal as modal_cloud
from sky.data import storage as storage_lib
from sky.provision import common
from sky.provision.modal import instance as modal_instance
from sky.provision.modal import modal_utils
from sky.provision.modal import volume as modal_volume
from sky.setup_files import dependencies
from sky.skylet import constants
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils import status_lib
from sky.utils import volume as volume_utils
from sky.volumes import volume as volume_lib

# Per-second prices as listed on modal.com/pricing.
_MODAL_PRICES = {
    'B300': 0.001972,
    'B200': 0.001736,
    'H200': 0.001261,
    'H100': 0.001097,
    'RTX-PRO-6000': 0.000842,
    'A100-80GB': 0.000694,
    'A100-40GB': 0.000583,
    'A100': 0.000583,
    'L40S': 0.000542,
    'A10': 0.000306,
    'L4': 0.000222,
    'T4': 0.000164,
    'cpu': 0.0000131,
    'memory': 0.00000222,
    'nonpreemptible_cpu': 0.00003942,
    'nonpreemptible_memory': 0.00000667,
}
_MODAL_REGION_MULTIPLIERS = {
    'auto': 1.0,
    **{region: 1.15 for region in fetch_modal.BROAD_REGIONS},
    **{region: 1.75 for region in fetch_modal.NARROW_REGIONS},
}


def _write_modal_catalog(path, prices=None, keep_row=None):
    rows = fetch_modal.create_catalog(prices or _MODAL_PRICES,
                                      _MODAL_REGION_MULTIPLIERS)
    if keep_row is not None:
        rows = [row for row in rows if keep_row(row)]
    with open(path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fetch_modal.COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return catalog_common.LazyDataFrame(str(path), lambda: False)


@pytest.fixture(scope='module')
def _modal_catalog_df(tmp_path_factory):
    path = tmp_path_factory.mktemp('modal_catalog') / 'vms.csv'
    return _write_modal_catalog(path)


@pytest.fixture(autouse=True)
def _use_modal_catalog(monkeypatch, _modal_catalog_df):
    """Use a pricing table made by the fetcher instead of the hosted one."""
    monkeypatch.setattr(modal_catalog, '_df', _modal_catalog_df)


def _provision_config(node_config,
                      ports_to_open_on_launch=None,
                      provider_config=None):
    return common.ProvisionConfig(
        provider_config=provider_config or {},
        authentication_config={},
        docker_config={},
        node_config=node_config,
        count=1,
        tags={},
        resume_stopped_nodes=False,
        ports_to_open_on_launch=ports_to_open_on_launch)


def _check_modal_compute_credentials():
    # pylint: disable=protected-access
    return clouds.Modal._check_compute_credentials()


def _modal_unsupported_features(resources):
    # pylint: disable=protected-access
    return clouds.Modal._unsupported_features_for_resources(resources)


def _set_modal_config(monkeypatch, config, verify_error=None):
    """Fakes the modal SDK: its config and whether Modal accepts the token.

    Returns the list of token verifications made (one entry per call).
    """
    verifications = []

    class FakeWorkspace:

        def hydrate(self):
            verifications.append(1)
            if verify_error is not None:
                raise verify_error
            return self

    monkeypatch.setattr(
        modal_cloud.modal_adaptor, 'modal',
        SimpleNamespace(config=SimpleNamespace(config=config),
                        Workspace=SimpleNamespace(from_context=FakeWorkspace)))
    return verifications


class _NoSdk:
    """Stands in for the modal SDK where importing it must not happen."""

    def __getattr__(self, name):
        raise AssertionError(f'modal SDK imported via {name!r}')


def _set_active_modal_environment(monkeypatch, name='dev'):
    monkeypatch.setattr(clouds.Modal, '_get_active_environment_name',
                        classmethod(lambda cls: name))


def test_modal_cloud_registration():
    cloud = registry.CLOUD_REGISTRY.from_str('modal')

    assert isinstance(cloud, clouds.Modal)
    assert clouds.Modal.canonical_name() == 'modal'
    assert 'modal' in constants.ALL_CLOUDS


def test_modal_dependency_gating():
    assert dependencies.cloud_dependencies['modal'] == [
        'modal>=1.6.0; python_version>="3.10"',
    ]
    modal_in_all = any(
        dep.startswith('modal>=1.6.0')
        for dep in dependencies.extras_require['all'])
    assert modal_in_all == (sys.version_info >= (3, 10))


def test_modal_catalog_regions_and_prices():
    assert modal_catalog.validate_region_zone('auto', None) == ('auto', None)
    assert modal_catalog.validate_region_zone('us-west',
                                              None) == ('us-west', None)
    with pytest.raises(ValueError, match='does not support zones'):
        modal_catalog.validate_region_zone('auto', 'us-west-1')

    auto_price = modal_catalog.get_hourly_cost('4CPU--16GB--H100:1', False,
                                               'auto', None)
    broad_price = modal_catalog.get_hourly_cost('4CPU--16GB--H100:1', False,
                                                'us', None)
    narrow_price = modal_catalog.get_hourly_cost('4CPU--16GB--H100:1', False,
                                                 'us-west', None)
    assert broad_price == pytest.approx(auto_price * 1.15)
    assert narrow_price == pytest.approx(auto_price * 1.75)

    b300_price = modal_catalog.get_hourly_cost('4CPU--16GB--B300:1', False,
                                               'auto', None)
    expected_b300_price = (2 * 0.0000131 + 16 * 0.00000222 + 0.001972) * 3600
    assert b300_price == pytest.approx(expected_b300_price)
    assert modal_catalog.get_hourly_cost('4CPU--16GB--B300:1', False, 'us',
                                         None) == pytest.approx(b300_price *
                                                                1.15)


def test_modal_catalog_gpu_to_sandbox_args():
    assert modal_catalog.get_modal_args_from_instance_type('4CPU--16GB') == (
        None, 2.0, 16 * 1024)
    # GPU shapes have 4 vCPUs (2 Modal cores) and 16 GiB per GPU.
    assert modal_catalog.get_modal_args_from_instance_type(
        '8CPU--32GB--A100-80GB:2') == ('A100-80GB:2', 4.0, 32 * 1024)
    for count in (1, 2, 4, 8):
        instance_type = f'{4 * count}CPU--{16 * count}GB--B300:{count}'
        gpu_arg = 'B300' if count == 1 else f'B300:{count}'
        assert modal_catalog.get_modal_args_from_instance_type(
            instance_type) == (gpu_arg, 2.0 * count, 16 * 1024 * count)
        assert modal_catalog.get_accelerators_from_instance_type(
            instance_type) == {
                'B300': count
            }
        listings = modal_catalog.list_accelerators(gpus_only=True,
                                                   name_filter='^B300$',
                                                   region_filter='^auto$',
                                                   quantity_filter=count)
        assert listings['B300'][0].device_memory == 288


def test_modal_dynamic_instance_types_and_prices():
    assert modal_catalog.get_default_instance_type() == '4CPU--16GB'
    assert modal_catalog.get_default_instance_type(
        cpus='16+', memory='64+') == '16CPU--64GB'
    assert modal_catalog.get_default_instance_type(
        cpus='0.25', memory='0.125') == '0.25CPU--0.125GB'
    assert modal_catalog.get_modal_args_from_instance_type(
        '0.25CPU--0.125GB') == (None, 0.125, 128)
    assert modal_catalog.get_default_instance_type(
        cpus='128') == '128CPU--336GB'
    assert modal_catalog.get_default_instance_type(
        cpus='128', memory='336') == '128CPU--336GB'
    assert modal_catalog.get_modal_args_from_instance_type('128CPU--336GB') == (
        None, 64, 344064)
    assert modal_catalog.get_default_instance_type(cpus='129',
                                                   memory='336') is None
    assert modal_catalog.get_default_instance_type(cpus='128',
                                                   memory='337') is None
    assert not modal_catalog.instance_type_exists('129CPU--336GB')

    instance_type = '16CPU--64GB'
    assert modal_catalog.instance_type_exists(instance_type)
    assert modal_catalog.get_vcpus_mem_from_instance_type(instance_type) == (16,
                                                                             64)
    assert modal_catalog.get_modal_args_from_instance_type(instance_type) == (
        None, 8, 64 * 1024)
    expected_price = (8 * 0.00003942 + 64 * 0.00000667) * 3600
    assert modal_catalog.get_hourly_cost(
        instance_type, region='auto') == pytest.approx(expected_price)
    assert modal_catalog.get_hourly_cost(
        instance_type, region='us') == pytest.approx(expected_price * 1.15)
    assert modal_catalog.get_default_instance_type(
        cpus='16', memory='64', max_hourly_cost=expected_price - 0.01) is None
    assert modal_catalog.get_arch_from_instance_type(instance_type) is None
    assert modal_catalog.get_local_disk_from_instance_type(
        instance_type) is None
    assert 'us-west' in {
        region.name
        for region in modal_catalog.get_region_zones_for_instance_type(
            instance_type, False)
    }


def test_modal_dynamic_gpu_instance_type():
    instance_types, fuzzy = modal_catalog.get_instance_type_for_accelerator(
        'h100', 2, cpus='32+', memory='128+', region='us')

    assert instance_types == ['32CPU--128GB--H100:2']
    assert not fuzzy
    instance_type = instance_types[0]
    assert modal_catalog.get_accelerators_from_instance_type(instance_type) == {
        'H100': 2
    }
    assert modal_catalog.get_vcpus_mem_from_instance_type(instance_type) == (
        32, 128)
    assert modal_catalog.get_modal_args_from_instance_type(instance_type) == (
        'H100:2', 16, 128 * 1024)
    expected_auto_price = (16 * 0.0000131 + 128 * 0.00000222 +
                           2 * 0.001097) * 3600
    assert modal_catalog.get_hourly_cost(
        instance_type, region='us') == pytest.approx(expected_auto_price * 1.15)


@pytest.mark.parametrize(
    'acc_count,cpus,memory,expected',
    [
        # As on Kubernetes: 4 vCPUs per GPU, and 4 GiB per vCPU.
        (1, None, None, '4CPU--16GB--T4:1'),
        (2, None, None, '8CPU--32GB--T4:2'),
        (8, None, None, '32CPU--128GB--T4:8'),
        (8, '8', None, '8CPU--32GB--T4:8'),
        (8, None, '64', '32CPU--64GB--T4:8'),
        (8, '2', '8', '2CPU--8GB--T4:8'),
    ])
def test_modal_gpu_host_scales_with_gpu_count(acc_count, cpus, memory,
                                              expected):
    instance_types, _ = modal_catalog.get_instance_type_for_accelerator(
        'T4', acc_count, cpus=cpus, memory=memory)

    assert instance_types == [expected]


def test_modal_catalog_lists_scaled_gpu_shapes():
    """`sky gpus list` shows the shape and price a launch would pick."""
    listing = modal_catalog.list_accelerators(gpus_only=True,
                                              name_filter='^H100$',
                                              region_filter='^auto$',
                                              quantity_filter=8)['H100'][0]
    launched, _ = modal_catalog.get_instance_type_for_accelerator('H100', 8)

    assert (listing.cpu_count, listing.memory) == (32, 128)
    assert listing.price == pytest.approx(
        modal_catalog.get_hourly_cost(launched[0], region='auto'))


def test_modal_dynamic_resources_are_feasible():
    resources = resources_lib.Resources(cloud=clouds.Modal(),
                                        cpus='32',
                                        memory='128',
                                        accelerators={'H100': 2})

    feasible = clouds.Modal()._get_feasible_launchable_resources(  # pylint: disable=protected-access
        resources)

    assert not feasible.fuzzy_candidate_list
    assert len(feasible.resources_list) == 1
    launchable = feasible.resources_list[0]
    assert launchable.instance_type == '32CPU--128GB--H100:2'
    assert launchable.cpus == '32.0'
    assert launchable.memory == '128.0'
    assert launchable.accelerators == {'H100': 2}


def test_modal_catalog_generic_contract_methods():
    assert modal_catalog.get_arch_from_instance_type('4CPU--16GB') is None
    assert modal_catalog.get_local_disk_from_instance_type('4CPU--16GB') is None
    assert modal_catalog.get_accelerator_hourly_cost('H100', 1, False, 'auto',
                                                     None) == 0.0
    regions = modal_catalog.get_region_zones_for_accelerators('H100', 1, False)
    region_names = [region.name for region in regions]
    assert region_names[0] == 'auto'
    assert {'af', 'au', 'us-west'} <= set(region_names)
    modal_catalog.check_accelerator_attachable_to_host('4CPU--16GB--H100:1',
                                                       {'H100': 1})


def test_modal_infra_schema():
    resources = resources_lib.Resources.from_yaml_config({
        'infra': 'modal',
        'accelerators': 'H100:1',
    })

    resource = next(iter(resources))
    assert isinstance(resource.cloud, clouds.Modal)
    assert resource.accelerators == {'H100': 1}


def test_modal_default_region_does_not_expand_to_region_failover():
    implicit_regions = clouds.Modal.regions_with_offering('4CPU--16GB--H100:1',
                                                          accelerators=None,
                                                          use_spot=False,
                                                          region=None,
                                                          zone=None)
    explicit_regions = clouds.Modal.regions_with_offering('4CPU--16GB--H100:1',
                                                          accelerators=None,
                                                          use_spot=False,
                                                          region='af',
                                                          zone=None)

    assert [region.name for region in implicit_regions] == ['auto']
    assert [region.name for region in explicit_regions] == ['af']


def test_modal_label_validation():
    assert clouds.Modal.is_label_valid('plaintext',
                                       'plainvalue') == (True, None)
    valid, message = clouds.Modal.is_label_valid('domain/key', 'value')
    assert not valid
    assert 'Invalid label key' in message


def test_modal_unsupported_features():
    resources = resources_lib.Resources(cloud=clouds.Modal(),
                                        instance_type='4CPU--16GB')
    unsupported = _modal_unsupported_features(resources)

    expected = {
        clouds.CloudImplementationFeatures.STOP,
        clouds.CloudImplementationFeatures.MULTI_NODE,
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER,
        clouds.CloudImplementationFeatures.IMAGE_ID,
        clouds.CloudImplementationFeatures.SPOT_INSTANCE,
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER,
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER,
        clouds.CloudImplementationFeatures.HOST_CONTROLLERS,
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS,
        clouds.CloudImplementationFeatures.AUTO_TERMINATE,
        clouds.CloudImplementationFeatures.AUTOSTOP,
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK,
        clouds.CloudImplementationFeatures.LOCAL_DISK,
    }

    assert expected <= set(unsupported)
    assert all(
        isinstance(message, str) and message
        for message in unsupported.values())


@pytest.mark.parametrize('accelerators,expected_reason', [
    (None, 'need 8 GPUs per node, e.g. --gpus H100:8'),
    ({
        'T4': 1
    }, 'use --gpus T4:8 instead of T4:1'),
    ({
        'H100': 4
    }, 'use --gpus H100:8 instead of H100:4'),
    ({
        'A10': 4
    }, 'Modal offers at most 4 A10 per node'),
    ({
        'RTX-PRO-6000': 1
    }, 'Modal offers at most 1 RTX-PRO-6000 per node'),
    ({
        'H100': 8
    }, None),
    ({
        'B200': 8
    }, None),
])
def test_modal_multi_node_needs_full_gpu_nodes(accelerators, expected_reason):
    resources = resources_lib.Resources(cloud=clouds.Modal(),
                                        accelerators=accelerators)

    unsupported = _modal_unsupported_features(resources)

    reason = unsupported.get(clouds.CloudImplementationFeatures.MULTI_NODE)
    if expected_reason is None:
        assert reason is None
    else:
        assert expected_reason in reason


def test_modal_supports_docker_images_and_open_ports():
    resources = resources_lib.Resources(cloud=clouds.Modal(),
                                        instance_type='4CPU--16GB',
                                        image_id='docker:ubuntu:22.04',
                                        ports=['8080'])
    unsupported = _modal_unsupported_features(resources)

    assert resources.extract_docker_image() == 'ubuntu:22.04'
    assert clouds.CloudImplementationFeatures.DOCKER_IMAGE not in unsupported
    assert clouds.CloudImplementationFeatures.OPEN_PORTS not in unsupported
    assert (clouds.CloudImplementationFeatures.STORAGE_MOUNTING
            not in unsupported)
    assert clouds.CloudImplementationFeatures.AUTODOWN not in unsupported
    assert clouds.CloudImplementationFeatures.AUTOSTOP in unsupported


def test_modal_rejects_cloud_image_ids():
    resources = resources_lib.Resources(cloud=clouds.Modal(),
                                        instance_type='4CPU--16GB',
                                        image_id='ami-123')

    with pytest.raises(exceptions.NotSupportedError, match='image_id'):
        clouds.Modal.check_features_are_supported(
            resources, {clouds.CloudImplementationFeatures.IMAGE_ID})


def test_modal_user_identity_from_workspace(monkeypatch):

    class FakeWorkspace:

        name = 'openpipe'
        local_uuid = 'uuid'

        def hydrate(self):
            return self

    class FakeWorkspaceFactory:

        @staticmethod
        def from_context():
            return FakeWorkspace()

    class FakeEnvironment:

        name = 'dev'

        def hydrate(self):
            return self

    class FakeEnvironmentFactory:

        @staticmethod
        def from_context():
            return FakeEnvironment()

    config_version = [1000]
    monkeypatch.setattr(
        modal_cloud.modal_adaptor, 'modal',
        SimpleNamespace(Workspace=FakeWorkspaceFactory,
                        Environment=FakeEnvironmentFactory,
                        config_version=lambda: config_version[0]))

    # One identity whose second element matches across environments of the
    # workspace, like AWS's [IAM user, account].
    assert clouds.Modal.get_user_identities() == [[
        'Modal workspace openpipe, environment dev', 'Modal workspace openpipe'
    ]]
    assert clouds.Modal.get_active_user_identity_str() == (
        'Modal workspace openpipe, environment dev')

    # A token for another workspace bumps the config version, so the cached
    # identity is recomputed.
    FakeWorkspace.name = 'other'
    assert clouds.Modal.get_user_identities()[0][1] == (
        'Modal workspace openpipe')
    config_version[0] += 1
    assert clouds.Modal.get_user_identities()[0][1] == 'Modal workspace other'


def test_modal_owner_check_allows_other_environment_of_same_workspace(
        monkeypatch):
    # pylint: disable=protected-access
    identities = {
        'id': [['Modal workspace x, environment dev', 'Modal workspace x']]
    }
    monkeypatch.setattr(clouds.Modal, 'get_user_identities',
                        classmethod(lambda cls: identities['id']))
    handle = backends.CloudVmRayResourceHandle.__new__(
        backends.CloudVmRayResourceHandle)
    handle.launched_resources = resources_lib.Resources(
        cloud=clouds.Modal(), instance_type='4CPU--16GB')
    record = {
        'owner': ['Modal workspace x, environment main', 'Modal workspace x'],
        'handle': handle,
    }
    patched = []
    monkeypatch.setattr(backend_utils.global_user_state,
                        'set_owner_identity_for_cluster',
                        lambda name, identity: patched.append(identity))

    backend_utils._check_owner_identity_with_record('c', record)
    # The cluster is still manageable; the owner is updated with a warning.
    assert patched == [identities['id'][0]]

    identities['id'] = [[
        'Modal workspace y, environment main', 'Modal workspace y'
    ]]
    with pytest.raises(exceptions.ClusterOwnerIdentityMismatchError):
        backend_utils._check_owner_identity_with_record('c', record)


def test_modal_credentials_from_env(monkeypatch):
    monkeypatch.setenv('MODAL_TOKEN_ID', 'token-id')
    monkeypatch.setenv('MODAL_TOKEN_SECRET', 'token-secret')
    _set_modal_config(monkeypatch, {})

    ok, message = _check_modal_compute_credentials()

    assert ok
    assert message is None


def test_modal_credentials_require_modal_package(monkeypatch):

    class MissingModal:

        @property
        def config(self):
            raise ImportError('missing modal')

    monkeypatch.setenv('MODAL_TOKEN_ID', 'token-id')
    monkeypatch.setenv('MODAL_TOKEN_SECRET', 'token-secret')
    monkeypatch.setattr(modal_cloud.modal_adaptor, 'modal', MissingModal())

    ok, message = _check_modal_compute_credentials()

    assert not ok
    assert 'Failed to access Modal credentials' in message


@pytest.mark.parametrize('env_var', ['MODAL_TOKEN_ID', 'MODAL_TOKEN_SECRET'])
def test_modal_credentials_reject_partial_env(monkeypatch, env_var):
    monkeypatch.delenv('MODAL_TOKEN_ID', raising=False)
    monkeypatch.delenv('MODAL_TOKEN_SECRET', raising=False)
    monkeypatch.setenv(env_var, 'token')
    _set_modal_config(monkeypatch, {})

    ok, message = _check_modal_compute_credentials()

    assert not ok
    assert 'Set both MODAL_TOKEN_ID and MODAL_TOKEN_SECRET' in message


def test_modal_credentials_from_config(monkeypatch):
    monkeypatch.delenv('MODAL_TOKEN_ID', raising=False)
    monkeypatch.delenv('MODAL_TOKEN_SECRET', raising=False)
    _set_modal_config(monkeypatch, {
        'token_id': 'token-id',
        'token_secret': 'token-secret',
    })

    ok, message = _check_modal_compute_credentials()

    assert ok
    assert message is None


def test_modal_credentials_missing(monkeypatch):
    monkeypatch.delenv('MODAL_TOKEN_ID', raising=False)
    monkeypatch.delenv('MODAL_TOKEN_SECRET', raising=False)
    verifications = _set_modal_config(monkeypatch, {})

    ok, message = _check_modal_compute_credentials()

    assert not ok
    assert 'Modal credentials were not found' in message
    assert not verifications  # Nothing to ask Modal about.


class AuthError(Exception):
    """Named like modal.exception.AuthError, which the check looks for."""


@pytest.mark.parametrize('from_env', [True, False])
def test_modal_credentials_rejected_by_modal(monkeypatch, from_env):
    """A token that is present but expired or revoked is not enabled."""
    token = {'token_id': 'token-id', 'token_secret': 'token-secret'}
    if from_env:
        monkeypatch.setenv('MODAL_TOKEN_ID', 'token-id')
        monkeypatch.setenv('MODAL_TOKEN_SECRET', 'token-secret')
    else:
        monkeypatch.delenv('MODAL_TOKEN_ID', raising=False)
        monkeypatch.delenv('MODAL_TOKEN_SECRET', raising=False)
    verifications = _set_modal_config(
        monkeypatch, {} if from_env else token,
        verify_error=AuthError('Token has expired'))

    ok, message = _check_modal_compute_credentials()

    assert not ok
    assert verifications == [1]
    assert 'Modal rejected the credentials' in message
    assert 'Token has expired' in message
    assert '`modal token new`' in message


def test_modal_credentials_asks_modal_on_every_check(monkeypatch):
    """No cached verdict: a token that expires later must be noticed."""
    monkeypatch.delenv('MODAL_TOKEN_ID', raising=False)
    monkeypatch.delenv('MODAL_TOKEN_SECRET', raising=False)
    config = {'token_id': 'token-id', 'token_secret': 'token-secret'}
    assert _set_modal_config(monkeypatch, config) == []
    assert _check_modal_compute_credentials() == (True, None)

    _set_modal_config(monkeypatch,
                      config,
                      verify_error=AuthError('Token has expired'))
    ok, _ = _check_modal_compute_credentials()
    assert not ok


def test_modal_credentials_unverifiable(monkeypatch):
    """Modal being unreachable is reported as such, not as a bad token."""
    monkeypatch.delenv('MODAL_TOKEN_ID', raising=False)
    monkeypatch.delenv('MODAL_TOKEN_SECRET', raising=False)
    _set_modal_config(monkeypatch, {
        'token_id': 'token-id',
        'token_secret': 'token-secret',
    },
                      verify_error=ConnectionError('no route to host'))

    ok, message = _check_modal_compute_credentials()

    assert not ok
    assert 'Failed to verify the Modal credentials' in message
    assert 'no route to host' in message
    assert 'rejected' not in message


def test_modal_credential_file_mounts_from_env(monkeypatch, tmp_path):
    credential_file = tmp_path / 'modal.toml'
    credential_file.write_text('[default]\ntoken_id = "token-id"\n')
    monkeypatch.setenv('MODAL_TOKEN_ID', 'token-id')
    monkeypatch.setenv('MODAL_TOKEN_SECRET', 'token-secret')
    monkeypatch.setattr(modal_cloud.os.path, 'expanduser',
                        lambda path: str(credential_file))

    assert not clouds.Modal().get_credential_file_mounts()


def test_modal_credential_file_mounts_from_file(monkeypatch, tmp_path):
    credential_file = tmp_path / 'modal.toml'
    credential_file.write_text('[default]\ntoken_id = "token-id"\n')
    monkeypatch.delenv('MODAL_TOKEN_ID', raising=False)
    monkeypatch.delenv('MODAL_TOKEN_SECRET', raising=False)
    monkeypatch.delenv('MODAL_CONFIG_PATH', raising=False)
    # Runs for every enabled cloud at launch, so it must work without the
    # modal SDK installed.
    monkeypatch.setattr(modal_cloud.modal_adaptor, 'modal', _NoSdk())
    monkeypatch.setattr(modal_cloud.os.path, 'expanduser',
                        lambda path: str(credential_file))

    assert clouds.Modal().get_credential_file_mounts() == {
        '~/.modal.toml': str(credential_file),
    }


def test_modal_credential_file_mounts_follow_modal_config_path(
        monkeypatch, tmp_path):
    # MODAL_CONFIG_PATH moves the file; the cluster reads the default path.
    credential_file = tmp_path / 'secrets' / 'modal.toml'
    credential_file.parent.mkdir()
    credential_file.write_text('[default]\ntoken_id = "token-id"\n')
    monkeypatch.delenv('MODAL_TOKEN_ID', raising=False)
    monkeypatch.delenv('MODAL_TOKEN_SECRET', raising=False)
    monkeypatch.setenv('MODAL_CONFIG_PATH', str(credential_file))
    monkeypatch.setattr(modal_cloud.modal_adaptor, 'modal', _NoSdk())

    assert clouds.Modal().get_credential_file_mounts() == {
        '~/.modal.toml': str(credential_file),
    }


def test_modal_deploy_variables_auto_region(monkeypatch):
    _set_active_modal_environment(monkeypatch)
    cloud = clouds.Modal()
    resources = resources_lib.Resources(cloud=cloud, instance_type='4CPU--16GB')

    variables = cloud.make_deploy_resources_variables(
        resources=resources,
        cluster_name=resources_utils.ClusterName('test', 'test'),
        region=clouds.Region('auto'),
        zones=None,
        num_nodes=1)

    assert variables['modal_region'] is None
    assert variables['modal_environment_name'] == 'dev'
    assert variables['modal_gpu'] is None
    assert variables['modal_cpu'] == 2.0
    assert variables['modal_memory'] == 16 * 1024
    assert variables['modal_docker_image'] is None


def test_modal_template_persists_environment():
    repo_root = pathlib.Path(__file__).parents[2]
    template = jinja2.Template(
        (repo_root /
         'sky/templates/modal-ray.yml.j2').read_text(encoding='utf-8'))

    rendered = template.render(modal_environment_name='dev',
                               credentials={},
                               num_nodes=3)

    assert 'environment_name: "dev"' in rendered
    assert 'max_workers: 2' in rendered


def test_modal_deploy_variables_gpu_region(monkeypatch):
    _set_active_modal_environment(monkeypatch)
    cloud = clouds.Modal()
    resources = resources_lib.Resources(cloud=cloud,
                                        instance_type='8CPU--32GB--A100-80GB:2',
                                        image_id='docker:ubuntu:22.04')

    variables = cloud.make_deploy_resources_variables(
        resources=resources,
        cluster_name=resources_utils.ClusterName('test', 'test'),
        region=clouds.Region('us-west'),
        zones=None,
        num_nodes=1)

    assert variables['modal_region'] == 'us-west'
    assert variables['modal_gpu'] == 'A100-80GB:2'
    assert variables['modal_docker_image'] == 'ubuntu:22.04'


def test_modal_deploy_variables_dynamic_resources(monkeypatch):
    _set_active_modal_environment(monkeypatch)
    cloud = clouds.Modal()
    resources = resources_lib.Resources(cloud=cloud,
                                        instance_type='32CPU--128GB--H100:2')

    variables = cloud.make_deploy_resources_variables(
        resources=resources,
        cluster_name=resources_utils.ClusterName('test', 'test'),
        region=clouds.Region('auto'),
        zones=None,
        num_nodes=1)

    assert variables['modal_gpu'] == 'H100:2'
    assert variables['modal_cpu'] == 16
    assert variables['modal_memory'] == 128 * 1024


def test_modal_deploy_variables_volume_mounts(monkeypatch):
    _set_active_modal_environment(monkeypatch)
    cloud = clouds.Modal()
    resources = resources_lib.Resources(cloud=cloud, instance_type='4CPU--16GB')
    volume_config = models.VolumeConfig(name='cache',
                                        type='modal-volume',
                                        cloud='modal',
                                        region=None,
                                        zone=None,
                                        name_on_cloud='cache-on-cloud',
                                        size=None,
                                        config={'environment_name': 'prod'})
    volume_mount = volume_utils.VolumeMount('/cache',
                                            'cache',
                                            volume_config,
                                            sub_path='models')

    variables = cloud.make_deploy_resources_variables(
        resources=resources,
        cluster_name=resources_utils.ClusterName('test', 'test'),
        region=clouds.Region('auto'),
        zones=None,
        num_nodes=1,
        volume_mounts=[volume_mount])

    assert variables['modal_volume_mounts'] == [{
        'Path': '/cache',
        'VolumeNameOnCloud': 'cache-on-cloud',
        'EnvironmentName': 'prod',
        'SubPath': 'models',
    }]


def test_modal_volume_mount_inherits_cluster_environment(monkeypatch):
    _set_active_modal_environment(monkeypatch)
    cloud = clouds.Modal()
    resources = resources_lib.Resources(cloud=cloud, instance_type='4CPU--16GB')
    volume_config = models.VolumeConfig(name='cache',
                                        type='modal-volume',
                                        cloud='modal',
                                        region=None,
                                        zone=None,
                                        name_on_cloud='cache-on-cloud',
                                        size=None,
                                        config={})
    volume_mount = volume_utils.VolumeMount('/cache', 'cache', volume_config)

    variables = cloud.make_deploy_resources_variables(
        resources=resources,
        cluster_name=resources_utils.ClusterName('test', 'test'),
        region=clouds.Region('auto'),
        zones=None,
        num_nodes=1,
        volume_mounts=[volume_mount])

    assert variables['modal_volume_mounts'][0]['EnvironmentName'] == 'dev'


def test_modal_volume_mount_paths_are_absolute(monkeypatch):
    _set_active_modal_environment(monkeypatch)
    cloud = clouds.Modal()
    resources = resources_lib.Resources(cloud=cloud, instance_type='4CPU--16GB')
    volume_config = models.VolumeConfig(name='cache',
                                        type='modal-volume',
                                        cloud='modal',
                                        region=None,
                                        zone=None,
                                        name_on_cloud='cache-on-cloud',
                                        size=None,
                                        config={})
    volume_mount = volume_utils.VolumeMount('~/cache', 'cache', volume_config)

    variables = cloud.make_deploy_resources_variables(
        resources=resources,
        cluster_name=resources_utils.ClusterName('test', 'test'),
        region=clouds.Region('auto'),
        zones=None,
        num_nodes=1,
        volume_mounts=[volume_mount])

    assert variables['modal_volume_mounts'][0]['Path'] == '/root/cache'


@pytest.mark.parametrize('path,expected', [
    ('/data', '/data'),
    ('~', '/root'),
    ('~/data', '/root/data'),
    ('data', '/root/sky_workdir/data'),
    ('data/sub', '/root/sky_workdir/data/sub'),
])
def test_modal_resolve_mount_path(path, expected):
    assert modal_utils.resolve_mount_path(path) == expected


def test_modal_volume_type_validation(monkeypatch):
    mock_infra_info = SimpleNamespace(cloud='modal', region=None, zone=None)
    monkeypatch.setattr('sky.utils.infra_utils.InfraInfo.from_str',
                        lambda _: mock_infra_info)

    volume = volume_lib.Volume.from_yaml_config({
        'name': 'cache',
        'type': 'modal-volume',
        'infra': 'modal',
    })
    volume.validate()
    assert volume.cloud == 'modal'

    with pytest.raises(ValueError, match='do not support size'):
        volume_lib.Volume.from_yaml_config({
            'name': 'cache',
            'type': 'modal-volume',
            'infra': 'modal',
            'size': '10Gi',
        }).validate()


def _node(host='ssh.modal.host',
          port=2222,
          internal_ip='127.0.0.1',
          port_urls=None):
    return {
        'ssh_host': host,
        'ssh_port': port,
        'internal_ip': internal_ip,
        'port_urls': port_urls or {},
    }


def _cluster_state(call_id='fc-1', nodes=None, preempted=False):
    nodes = {0: _node()} if nodes is None else nodes
    return {
        'call_id': call_id,
        'num_nodes': len(nodes),
        'preempted': preempted,
        'nodes': nodes,
    }


def test_modal_run_instances_launches_function_call(monkeypatch):
    launches = []
    waits = []
    image_calls = []
    secret_calls = []
    volume_calls = []
    bucket_calls = []

    class FakeVolume:

        @staticmethod
        def from_name(name, environment_name=None, create_if_missing=False):
            volume_calls.append((name, environment_name, create_if_missing))
            return FakeVolume()

        def with_mount_options(self, sub_path=None):
            volume_calls.append(('with_mount_options', sub_path))
            return f'volume:{sub_path}'

    class FakeCloudBucketMount:

        def __init__(self, **kwargs):
            bucket_calls.append(kwargs)

    class FakeSecret:

        @staticmethod
        def from_dict(env_dict):
            secret_calls.append(env_dict)
            return f'secret:{len(secret_calls)}'

    def fake_launch_cluster(cluster_name_on_cloud, environment_name, **kwargs):
        launches.append((cluster_name_on_cloud, environment_name, kwargs))
        return 'fc-1'

    def fake_wait_for_nodes(cluster_name_on_cloud, environment_name, call_id,
                            num_nodes):
        waits.append(
            (cluster_name_on_cloud, environment_name, call_id, num_nodes))
        return {0: _node()}

    monkeypatch.setattr(modal_instance.modal_utils, 'get_cluster_state',
                        lambda name, environment_name: None)
    monkeypatch.setattr(modal_instance.modal_utils, 'launch_cluster',
                        fake_launch_cluster)
    monkeypatch.setattr(modal_instance.modal_utils, 'wait_for_nodes',
                        fake_wait_for_nodes)
    monkeypatch.setattr(
        modal_instance.modal_utils,
        'get_image',
        lambda docker_image=None: image_calls.append(docker_image) or 'image')
    monkeypatch.setattr(modal_utils, '_get_s3_secret',
                        lambda region: f's3-secret:{region}')
    monkeypatch.setattr(
        modal_instance.modal_adaptor, 'modal',
        SimpleNamespace(Volume=FakeVolume,
                        CloudBucketMount=FakeCloudBucketMount,
                        Secret=FakeSecret))
    monkeypatch.setenv('MODAL_TOKEN_ID', 'token-id')
    monkeypatch.setenv('MODAL_TOKEN_SECRET', 'token-secret')

    record = modal_instance.run_instances(
        region='auto',
        cluster_name='test',
        cluster_name_on_cloud='test-on-cloud',
        config=_provision_config(
            {
                'PublicKey': 'ssh-ed25519 test',
                'ModalRegion': None,
                'Gpu': 'H100',
                'Cpu': 2.0,
                'Memory': 16 * 1024,
                'Timeout': 24 * 60 * 60,
                'DockerImage': 'ubuntu:22.04',
                'ModalVolumes': [{
                    'Path': '/cache',
                    'VolumeNameOnCloud': 'cache-on-cloud',
                    'EnvironmentName': 'dev',
                    'SubPath': 'models',
                }],
                'CloudBucketMounts': [{
                    'Path': '/bucket',
                    'StoreType': 'S3',
                    'BucketName': 'bucket',
                    'BucketEndpointUrl': None,
                    'KeyPrefix': None,
                    'Region': 'us-east-1',
                    'ReadOnly': True,
                    'ForcePathStyle': False,
                }],
            }, [22, 8081, 8080],
            provider_config={'environment_name': 'dev'}))

    assert record.provider_name == 'modal'
    assert record.head_instance_id == 'fc-1-0'
    assert record.created_instance_ids == ['fc-1-0']
    [(name, environment_name, kwargs)] = launches
    assert (name, environment_name) == ('test-on-cloud', 'dev')
    assert kwargs['num_nodes'] == 1
    assert kwargs['public_key'] == 'ssh-ed25519 test'
    assert kwargs['user_ports'] == [8080, 8081]
    function_kwargs = kwargs['function_kwargs']
    assert function_kwargs['image'] == 'image'
    assert function_kwargs['region'] is None
    assert function_kwargs['gpu'] == 'H100'
    assert function_kwargs['cpu'] == (2.0, 2.0)
    assert function_kwargs['memory'] == (16 * 1024, 16 * 1024)
    assert function_kwargs['timeout'] == 24 * 60 * 60
    assert function_kwargs['secrets'] == ['secret:1']
    # Modal does not support non-preemptible GPU Functions.
    assert function_kwargs['nonpreemptible'] is False
    assert function_kwargs['volumes']['/cache'] == 'volume:models'
    assert isinstance(function_kwargs['volumes']['/bucket'],
                      FakeCloudBucketMount)
    assert waits == [('test-on-cloud', 'dev', 'fc-1', 1)]
    assert image_calls == ['ubuntu:22.04']
    assert secret_calls == [{
        'MODAL_TOKEN_ID': 'token-id',
        'MODAL_TOKEN_SECRET': 'token-secret',
    }]
    assert volume_calls == [('cache-on-cloud', 'dev', False),
                            ('with_mount_options', 'models')]
    assert bucket_calls == [{
        'bucket_name': 'bucket',
        'bucket_endpoint_url': None,
        'key_prefix': None,
        'secret': 's3-secret:us-east-1',
        'read_only': True,
        'force_path_style': False,
    }]


def test_modal_cpu_clusters_are_nonpreemptible(monkeypatch):
    monkeypatch.setattr(modal_instance.modal_utils,
                        'get_image',
                        lambda docker_image=None: 'image')
    monkeypatch.delenv('MODAL_TOKEN_ID', raising=False)
    monkeypatch.delenv('MODAL_TOKEN_SECRET', raising=False)

    # pylint: disable=protected-access
    kwargs = modal_instance._function_kwargs({'Cpu': 2.0, 'Memory': 1024})

    assert kwargs['nonpreemptible'] is True
    assert kwargs['gpu'] is None


@pytest.mark.parametrize('modal_error,expected', [
    ('InvalidError', exceptions.ProvisionUnsupportedError),
    ('AuthError', exceptions.InvalidCloudCredentials),
    ('PermissionDeniedError', exceptions.InvalidCloudCredentials),
])
def test_modal_run_instances_translates_account_errors(monkeypatch, modal_error,
                                                       expected):
    # Errors that retrying cannot fix must reach the user instead of a generic
    # "failed to acquire resources".
    modal_exception = pytest.importorskip('modal.exception')
    message = 'Please add a payment method to use T4 GPUs.'

    def fake_launch_cluster(*args, **kwargs):
        del args, kwargs
        raise getattr(modal_exception, modal_error)(message)

    monkeypatch.setattr(modal_instance.modal_utils, 'get_cluster_state',
                        lambda name, environment_name: None)
    monkeypatch.setattr(modal_instance.modal_utils, 'launch_cluster',
                        fake_launch_cluster)
    monkeypatch.setattr(modal_instance.modal_utils,
                        'get_image',
                        lambda docker_image=None: 'image')
    monkeypatch.setattr(modal_instance.modal_adaptor, 'modal',
                        SimpleNamespace(exception=modal_exception))
    monkeypatch.delenv('MODAL_TOKEN_ID', raising=False)
    monkeypatch.delenv('MODAL_TOKEN_SECRET', raising=False)

    with pytest.raises(expected, match='payment method'):
        modal_instance.run_instances(region='auto',
                                     cluster_name='test',
                                     cluster_name_on_cloud='test-on-cloud',
                                     config=_provision_config({
                                         'PublicKey': 'ssh-ed25519 test',
                                         'Gpu': 'T4',
                                         'Cpu': 2.0,
                                         'Memory': 16 * 1024,
                                         'Timeout': 24 * 60 * 60,
                                     }))


def test_modal_run_instances_cancels_call_if_nodes_never_come_up(monkeypatch):
    cancelled = []

    def fake_wait_for_nodes(*args):
        del args
        raise RuntimeError('Timed out')

    monkeypatch.setattr(modal_instance.modal_utils, 'get_cluster_state',
                        lambda name, environment_name: None)
    monkeypatch.setattr(modal_instance.modal_utils, 'launch_cluster',
                        lambda *args, **kwargs: 'fc-1')
    monkeypatch.setattr(modal_instance.modal_utils, 'wait_for_nodes',
                        fake_wait_for_nodes)
    monkeypatch.setattr(modal_instance.modal_utils, 'cancel_call',
                        cancelled.append)
    monkeypatch.setattr(modal_instance, '_function_kwargs',
                        lambda node_config: {})

    with pytest.raises(RuntimeError, match='Timed out'):
        modal_instance.run_instances(region='auto',
                                     cluster_name='test',
                                     cluster_name_on_cloud='test-on-cloud',
                                     config=_provision_config(
                                         {'PublicKey': 'ssh-ed25519 test'}))

    assert cancelled == ['fc-1']


def test_modal_run_instances_reuses_running_cluster(monkeypatch):
    monkeypatch.setattr(modal_instance.modal_utils, 'get_cluster_state',
                        lambda name, environment_name: _cluster_state('fc-9'))
    monkeypatch.setattr(modal_instance.modal_utils, 'call_is_running',
                        lambda call_id: True)
    monkeypatch.setattr(modal_instance.modal_utils, 'launch_cluster',
                        lambda *args, **kwargs: pytest.fail('relaunched'))

    record = modal_instance.run_instances(
        region='auto',
        cluster_name='test',
        cluster_name_on_cloud='test-on-cloud',
        config=_provision_config({
            'PublicKey': 'ssh-ed25519 test',
        },
                                 provider_config={'environment_name': 'dev'}))

    assert record.head_instance_id == 'fc-9-0'
    assert not record.created_instance_ids
    assert not record.resumed_instance_ids


def test_modal_run_instances_relaunches_after_call_ended(monkeypatch):
    launches = []
    monkeypatch.setattr(modal_instance.modal_utils, 'get_cluster_state',
                        lambda name, environment_name: _cluster_state('fc-old'))
    monkeypatch.setattr(modal_instance.modal_utils, 'call_is_running',
                        lambda call_id: False)
    monkeypatch.setattr(
        modal_instance.modal_utils, 'launch_cluster', lambda *args, **kwargs:
        launches.append(kwargs['num_nodes']) or 'fc-new')
    monkeypatch.setattr(modal_instance.modal_utils, 'wait_for_nodes',
                        lambda *args: {0: _node()})
    monkeypatch.setattr(modal_instance, '_function_kwargs',
                        lambda node_config: {})

    record = modal_instance.run_instances(
        region='auto',
        cluster_name='test',
        cluster_name_on_cloud='test-on-cloud',
        config=_provision_config({'PublicKey': 'ssh-ed25519 test'}))

    assert launches == [1]
    assert record.created_instance_ids == ['fc-new-0']


def test_modal_apply_and_delete_volume(monkeypatch):
    calls = []

    class FakeVolume:
        """Fake Modal Volume."""

        object_id = 'vo-123'
        objects = None

        @staticmethod
        def from_name(name, environment_name=None, create_if_missing=False):
            calls.append(
                ('from_name', name, environment_name, create_if_missing))
            return FakeVolume()

        def hydrate(self):
            calls.append(('hydrate',))

    class FakeVolumeObjects:

        @staticmethod
        def delete(name, allow_missing=False, environment_name=None):
            calls.append(('delete', name, allow_missing, environment_name))

    FakeVolume.objects = FakeVolumeObjects
    monkeypatch.setattr(modal_volume.modal_adaptor, 'modal',
                        SimpleNamespace(Volume=FakeVolume))
    config = models.VolumeConfig(name='cache',
                                 type='modal-volume',
                                 cloud='modal',
                                 region=None,
                                 zone=None,
                                 name_on_cloud='cache-on-cloud',
                                 size=None,
                                 config={'environment_name': 'dev'})

    applied = modal_volume.apply_volume(config)
    deleted = modal_volume.delete_volume(applied)

    assert applied.id_on_cloud == 'vo-123'
    assert deleted is applied
    assert calls == [
        ('from_name', 'cache-on-cloud', 'dev', True),
        ('hydrate',),
        ('delete', 'cache-on-cloud', True, 'dev'),
    ]


def test_modal_volume_apply_records_active_environment(monkeypatch):
    calls = []

    class FakeVolume:

        object_id = 'vo-123'

        @staticmethod
        def from_name(name, environment_name=None, create_if_missing=False):
            calls.append(('from_name', name, environment_name))
            return FakeVolume()

        def hydrate(self):
            pass

    class FakeVolumeObjects:

        @staticmethod
        def delete(name, allow_missing=False, environment_name=None):
            calls.append(('delete', name, environment_name))

    FakeVolume.objects = FakeVolumeObjects
    monkeypatch.setattr(modal_volume.modal_adaptor, 'modal',
                        SimpleNamespace(Volume=FakeVolume))
    _set_active_modal_environment(monkeypatch, 'dev')
    config = models.VolumeConfig(name='cache',
                                 type='modal-volume',
                                 cloud='modal',
                                 region=None,
                                 zone=None,
                                 name_on_cloud='cache-on-cloud',
                                 size=None,
                                 config={})

    applied = modal_volume.apply_volume(config)
    # Switching environments later must not redirect the delete.
    _set_active_modal_environment(monkeypatch, 'prod')
    modal_volume.delete_volume(applied)

    assert applied.config['environment_name'] == 'dev'
    assert calls == [
        ('from_name', 'cache-on-cloud', 'dev'),
        ('delete', 'cache-on-cloud', 'dev'),
    ]


@pytest.mark.parametrize(
    'volume_environment,mount_environment,used',
    [
        ('dev', 'dev', True),
        ('prod', 'dev', False),
        # Volumes created before their environment was recorded.
        (None, 'dev', True),
    ])
def test_modal_volume_usedby_matches_environment(monkeypatch,
                                                 volume_environment,
                                                 mount_environment, used):
    cluster_yaml = {
        'available_node_types': {
            'ray_head_default': {
                'node_config': {
                    'ModalVolumes': [{
                        'VolumeNameOnCloud': 'cache-on-cloud',
                        'EnvironmentName': mount_environment,
                    }]
                }
            }
        }
    }
    handle = SimpleNamespace(
        launched_resources=SimpleNamespace(cloud=clouds.Modal()),
        cluster_yaml='/sky/generated/modal-a.yml')
    monkeypatch.setattr(modal_volume.global_user_state, 'get_clusters',
                        lambda: [{
                            'name': 'modal-a',
                            'handle': handle
                        }])
    monkeypatch.setattr(modal_volume.global_user_state, 'get_cluster_yaml_dict',
                        lambda path: cluster_yaml)
    volume_config = {}
    if volume_environment is not None:
        volume_config['environment_name'] = volume_environment
    config = models.VolumeConfig(name='cache',
                                 type='modal-volume',
                                 cloud='modal',
                                 region=None,
                                 zone=None,
                                 name_on_cloud='cache-on-cloud',
                                 size=None,
                                 config=volume_config)

    assert modal_volume.get_volume_usedby(config) == ([], ['modal-a']
                                                      if used else [])


def test_modal_volume_usedby_reads_cluster_yaml_by_path(monkeypatch):
    # Handles store the cluster YAML's path, not its contents.
    yamls = {
        '/sky/generated/modal-a.yml': {
            'available_node_types': {
                'ray_head_default': {
                    'node_config': {
                        'ModalVolumes': [{
                            'VolumeNameOnCloud': 'cache-on-cloud'
                        }]
                    }
                }
            }
        },
        '/sky/generated/modal-b.yml': {},
    }

    def handle(cloud, cluster_yaml):
        return SimpleNamespace(launched_resources=SimpleNamespace(cloud=cloud),
                               cluster_yaml=cluster_yaml)

    records = [
        {
            'name': 'modal-a',
            'handle': handle(clouds.Modal(), '/sky/generated/modal-a.yml')
        },
        {
            'name': 'modal-b',
            'handle': handle(clouds.Modal(), '/sky/generated/modal-b.yml')
        },
        # Non-Modal clusters are skipped without reading their YAML.
        {
            'name': 'aws-dev',
            'handle': handle(clouds.AWS(), '/sky/generated/aws-dev.yml')
        },
    ]
    monkeypatch.setattr(modal_volume.global_user_state, 'get_clusters',
                        lambda: records)
    monkeypatch.setattr(modal_volume.global_user_state, 'get_cluster_yaml_dict',
                        lambda path: yamls[path])
    config = models.VolumeConfig(name='cache',
                                 type='modal-volume',
                                 cloud='modal',
                                 region=None,
                                 zone=None,
                                 name_on_cloud='cache-on-cloud',
                                 size=None,
                                 config={})

    assert modal_volume.get_volume_usedby(config) == ([], ['modal-a'])


def test_modal_cloud_bucket_mount_specs():
    # pylint: disable=protected-access

    class FakeStorage:
        """Fake SkyPilot Storage."""

        mode = storage_lib.StorageMode.MOUNT
        name = 'bucket'
        mount_config = storage_lib.MountConfig(read_only=True)

        def __init__(self):
            self.constructed = False
            self.stores = {
                storage_lib.StoreType.S3: SimpleNamespace(
                    bucket=SimpleNamespace(name='bucket'),
                    bucket_sub_path='prefix',
                    region='us-east-1')
            }

        def construct(self):
            self.constructed = True

    storage = FakeStorage()
    specs = backend_utils._get_modal_cloud_bucket_mounts({'/bucket': storage})
    # Relative destinations live under the remote workdir, as on other clouds.
    relative_specs = backend_utils._get_modal_cloud_bucket_mounts(
        {'data': FakeStorage()})
    assert relative_specs[0]['Path'] == '/root/sky_workdir/data'

    assert storage.constructed
    assert specs == [{
        'Path': '/bucket',
        'StoreType': 'S3',
        'BucketName': 'bucket',
        'BucketEndpointUrl': None,
        'KeyPrefix': 'prefix/',
        'Region': 'us-east-1',
        'ReadOnly': True,
        'ForcePathStyle': False,
    }]


def test_modal_cloud_bucket_mount_dryrun_does_not_construct():
    # pylint: disable=protected-access

    class FakeStorage:
        """Fake SkyPilot Storage."""

        mode = storage_lib.StorageMode.MOUNT
        name = 'bucket'
        stores = None
        mount_config = storage_lib.MountConfig(read_only=True)

        def construct(self):
            raise AssertionError('dryrun should not construct storage')

    specs = backend_utils._get_modal_cloud_bucket_mounts(
        {'/bucket': FakeStorage()}, dryrun=True)

    assert not specs


def test_modal_cloud_bucket_mount_rejects_mount_cached():
    # pylint: disable=protected-access

    storage = SimpleNamespace(mode=storage_lib.StorageMode.MOUNT_CACHED)

    with pytest.raises(exceptions.NotSupportedError, match='MOUNT_CACHED'):
        backend_utils._get_modal_cloud_bucket_mounts({'/bucket': storage})


def test_modal_get_image_from_registry(monkeypatch):
    calls = []

    class FakeImage:

        def __init__(self, source):
            self.source = source

        def apt_install(self, *packages):
            calls.append((self.source, packages))
            return 'built-image'

    class FakeImageFactory:

        @staticmethod
        def debian_slim(python_version):
            return FakeImage(('debian_slim', python_version))

        @staticmethod
        def from_registry(tag, add_python):
            return FakeImage(('from_registry', tag, add_python))

    monkeypatch.setattr(modal_utils.modal_adaptor, 'modal',
                        SimpleNamespace(Image=FakeImageFactory))

    assert modal_utils.get_image('ubuntu:22.04') == 'built-image'
    local_python = f'{sys.version_info.major}.{sys.version_info.minor}'
    assert calls == [(('from_registry', 'ubuntu:22.04', local_python),
                      ('openssh-server', 'sudo', 'rsync', 'curl', 'procps',
                       'patch', 'lsof'))]


class _FakeModal:
    """In-memory stand-in for the Modal Dict and FunctionCall APIs."""

    class NotFoundError(Exception):
        pass

    class RemoteError(Exception):
        pass

    class FunctionTimeoutError(Exception):
        pass

    class OutputExpiredError(Exception):
        pass

    PENDING = 'pending'
    TERMINATED = 'terminated'

    def __init__(self):
        self.dicts = {}
        self.call_status = {}
        self.cancelled = []
        self.dict_lookups = []
        fake = self

        class FakeDict:

            def __init__(self, key):
                self._key = key

            @staticmethod
            def from_name(name, environment_name=None, create_if_missing=False):
                key = (environment_name, name)
                fake.dict_lookups.append(key)
                if create_if_missing:
                    fake.dicts.setdefault(key, {})
                return FakeDict(key)

            def _data(self):
                if self._key not in fake.dicts:
                    raise fake.NotFoundError(self._key)
                return fake.dicts[self._key]

            def items(self):
                return list(self._data().items())

            def hydrate(self):
                self._data()

            def __setitem__(self, key, value):
                self._data()[key] = value

        def delete(name, allow_missing=False, environment_name=None):
            assert allow_missing
            fake.dicts.pop((environment_name, name), None)

        FakeDict.objects = SimpleNamespace(delete=delete)

        class FakeCall:

            def __init__(self, call_id):
                self.object_id = call_id

            @staticmethod
            def from_id(call_id):
                return FakeCall(call_id)

            def get_call_graph(self):
                status = fake.call_status[self.object_id]
                if isinstance(status, Exception):
                    raise status
                if status is None:
                    return []
                return [SimpleNamespace(status=status)]

            def cancel(self, terminate_containers=False):
                assert terminate_containers
                fake.cancelled.append(self.object_id)
                fake.call_status[self.object_id] = fake.TERMINATED

            def get(self, timeout=None):
                assert timeout == 0
                status = fake.call_status[self.object_id]
                if isinstance(status, Exception):
                    raise status
                if status in (fake.PENDING, None):
                    raise TimeoutError()
                if status == fake.TERMINATED:
                    raise fake.RemoteError('Function call was cancelled')
                return status

        self.module = SimpleNamespace(
            Dict=FakeDict,
            FunctionCall=FakeCall,
            call_graph=SimpleNamespace(InputStatus=SimpleNamespace(
                PENDING=self.PENDING)),
            exception=SimpleNamespace(
                NotFoundError=self.NotFoundError,
                RemoteError=self.RemoteError,
                FunctionTimeoutError=self.FunctionTimeoutError,
                OutputExpiredError=self.OutputExpiredError))

    def add_cluster(self,
                    name='test-on-cloud',
                    environment_name=None,
                    call_id='fc-1',
                    nodes=None,
                    status=PENDING,
                    preempted=False):
        nodes = {0: _node()} if nodes is None else nodes
        data = {'call_id': call_id, 'num_nodes': len(nodes)}
        if preempted:
            data['preempted'] = True
        for rank, node in nodes.items():
            data[f'node-{rank}'] = node
        self.dicts[(environment_name, modal_utils.state_name(name))] = data
        self.call_status[call_id] = status


@pytest.fixture
def fake_modal(monkeypatch):
    fake = _FakeModal()
    monkeypatch.setattr(modal_utils.modal_adaptor, 'modal', fake.module)
    return fake


def test_modal_state_name_fits_modal_limits():
    # Modal object names are at most 64 characters.
    longest = 'x' * clouds.Modal.max_cluster_name_length()
    assert len(modal_utils.state_name(longest)) <= 64


def test_modal_get_cluster_state(fake_modal):
    fake_modal.add_cluster(environment_name='dev',
                           nodes={
                               0: _node(),
                               1: _node(port=2223)
                           })

    state = modal_utils.get_cluster_state('test-on-cloud', 'dev')

    assert state == {
        'call_id': 'fc-1',
        'num_nodes': 2,
        'preempted': False,
        'nodes': {
            0: _node(),
            1: _node(port=2223)
        },
    }
    assert modal_utils.get_cluster_state('missing', 'dev') is None


def test_modal_get_cluster_state_propagates_api_errors(monkeypatch):

    class FakeDict:

        @staticmethod
        def from_name(name, environment_name=None, create_if_missing=False):
            del name, environment_name, create_if_missing  # unused
            return SimpleNamespace(items=lambda: (_ for _ in ()).throw(
                RuntimeError('rate limited')))

    monkeypatch.setattr(modal_utils.modal_adaptor, 'modal',
                        SimpleNamespace(Dict=FakeDict))

    with pytest.raises(RuntimeError, match='rate limited'):
        modal_utils.get_cluster_state('test-on-cloud', None)


@pytest.mark.parametrize(
    'status,running',
    [
        # Running, or not scheduled yet: no output.
        (_FakeModal.PENDING, True),
        (None, True),
        # Cancelled.
        (_FakeModal.TERMINATED, False),
        # The node returned (preemption guard, or sshd exited).
        ('success', False),
        # Hit the Function timeout.
        (_FakeModal.FunctionTimeoutError('24h'), False),
        # Ended long ago.
        (_FakeModal.OutputExpiredError(), False),
        (_FakeModal.NotFoundError('gone'), False),
    ])
def test_modal_call_is_running(fake_modal, status, running):
    fake_modal.call_status['fc-1'] = status

    assert modal_utils.call_is_running('fc-1') == running


def test_modal_call_is_running_propagates_api_errors(fake_modal):
    # A transient API error must not make SkyPilot drop a live cluster.
    fake_modal.call_status['fc-1'] = RuntimeError('unavailable')

    with pytest.raises(RuntimeError, match='unavailable'):
        modal_utils.call_is_running('fc-1')


def _fake_app(fake_modal, calls):

    class FakeApp:

        def __init__(self, name):
            calls['app'] = name

        def function(self, **kwargs):
            del kwargs
            return lambda body: SimpleNamespace(
                spawn=lambda *args: SimpleNamespace(object_id='fc-1'))

        @contextlib.contextmanager
        def run(self, detach, environment_name):
            del detach, environment_name
            calls['in_run'] = True
            try:
                yield
            finally:
                calls['in_run'] = False

    fake_modal.module.App = FakeApp
    fake_modal.module.clustered = lambda size: (lambda body: body)


class _StateProxy:
    """Wraps the fake state Dict to observe or fail writes."""

    def __init__(self, state, on_set):
        self._state = state
        self._on_set = on_set

    def __getattr__(self, name):
        return getattr(self._state, name)

    def __setitem__(self, key, value):
        self._on_set(key)
        self._state[key] = value


def test_modal_launch_cluster_records_call_before_leaving_app_run(
        fake_modal, monkeypatch):
    calls = {}
    _fake_app(fake_modal, calls)
    recorded = {}
    get_state_dict = modal_utils._get_state_dict  # pylint: disable=protected-access

    def observe(key):
        recorded[key] = calls['in_run']

    monkeypatch.setattr(
        modal_utils, '_get_state_dict', lambda *args, **kwargs: _StateProxy(
            get_state_dict(*args, **kwargs), observe))

    modal_utils.launch_cluster('test-on-cloud',
                               None,
                               num_nodes=1,
                               public_key='k',
                               user_ports=[],
                               function_kwargs={})

    assert recorded == {'num_nodes': True, 'call_id': True}


def test_modal_launch_cluster_cancels_call_if_recording_fails(
        fake_modal, monkeypatch):
    # Otherwise the running call could never be found or terminated.
    calls = {}
    _fake_app(fake_modal, calls)
    get_state_dict = modal_utils._get_state_dict  # pylint: disable=protected-access

    def fail_on_call_id(key):
        if key == 'call_id':
            raise RuntimeError('Modal API unavailable')

    monkeypatch.setattr(
        modal_utils, '_get_state_dict', lambda *args, **kwargs: _StateProxy(
            get_state_dict(*args, **kwargs), fail_on_call_id))

    with pytest.raises(RuntimeError, match='unavailable'):
        modal_utils.launch_cluster('test-on-cloud',
                                   None,
                                   num_nodes=1,
                                   public_key='k',
                                   user_ports=[],
                                   function_kwargs={})

    assert fake_modal.cancelled == ['fc-1']


def test_modal_storage_mounts_must_be_part_of_launch(monkeypatch):
    # pylint: disable=protected-access
    cluster_yaml = {
        'available_node_types': {
            'ray_head_default': {
                'node_config': {
                    'CloudBucketMounts': [{
                        'Path': '/root/sky_workdir/data'
                    }]
                }
            }
        }
    }
    monkeypatch.setattr(cloud_vm_ray_backend.global_user_state,
                        'get_cluster_yaml_dict', lambda path: cluster_yaml)
    handle = SimpleNamespace(cluster_name='c',
                             cluster_yaml='/sky/generated/c.yml')
    check = cloud_vm_ray_backend.CloudVmRayBackend._check_modal_storage_mounts

    # Mounted at launch: fine, whichever spelling of the path.
    check(handle, {'data': object()})
    check(handle, {'~/sky_workdir/data': object()})

    with pytest.raises(exceptions.NotSupportedError,
                       match='/mnt/new cannot be mounted on the running '
                       "cluster 'c'"):
        check(handle, {'data': object(), '/mnt/new': object()})


def test_modal_node_returns_after_preemption_rerun(monkeypatch):
    # A node never raises on its own, so call_is_running can treat any
    # unexpected exception as an API error.
    state = {'node-0': {'ssh_host': 'h'}}
    fake_modal = SimpleNamespace(
        Dict=SimpleNamespace(from_name=lambda name, environment_name: state),
        experimental=SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'modal', fake_modal)
    monkeypatch.setitem(sys.modules, 'modal.experimental',
                        fake_modal.experimental)
    node = modal_utils._make_node_function()  # pylint: disable=protected-access

    result = node('true', 22, 'sky-c', None, False, [])

    assert 'preemption' in result
    assert state['preempted'] is True


def test_modal_wait_for_nodes(fake_modal, monkeypatch):
    monkeypatch.setattr(modal_utils, '_POLL_INTERVAL_SECONDS', 0)
    fake_modal.add_cluster(nodes={0: _node(), 1: _node(port=2223)})

    nodes = modal_utils.wait_for_nodes('test-on-cloud', None, 'fc-1', 2)

    assert set(nodes) == {0, 1}


def test_modal_wait_for_nodes_fails_when_call_ends(fake_modal, monkeypatch):
    monkeypatch.setattr(modal_utils, '_POLL_INTERVAL_SECONDS', 0)
    fake_modal.add_cluster(nodes={}, status=_FakeModal.TERMINATED)

    with pytest.raises(RuntimeError, match='ended before all nodes'):
        modal_utils.wait_for_nodes('test-on-cloud', None, 'fc-1', 1)


def test_modal_launch_cluster(fake_modal, monkeypatch):
    calls = {}

    class FakeApp:

        def __init__(self, name):
            calls['app'] = name

        def function(self, **kwargs):
            calls['function'] = kwargs
            return lambda body: SimpleNamespace(
                spawn=lambda *args: calls.setdefault(
                    'spawn', args) and SimpleNamespace(object_id='fc-1'))

        def run(self, detach, environment_name):
            calls['run'] = (detach, environment_name)
            return contextlib.nullcontext()

    def fake_clustered(size):
        calls['clustered'] = size
        return lambda body: body

    fake_modal.module.App = FakeApp
    fake_modal.module.clustered = fake_clustered
    # A stale state from an earlier cluster with the same name is replaced.
    fake_modal.add_cluster(environment_name='dev',
                           call_id='fc-old',
                           status=_FakeModal.TERMINATED)

    call_id = modal_utils.launch_cluster('test-on-cloud',
                                         'dev',
                                         num_nodes=2,
                                         public_key='ssh-ed25519 test',
                                         user_ports=[8080],
                                         function_kwargs={'gpu': 'H100:8'})

    assert call_id == 'fc-1'
    assert calls['app'] == 'sky-test-on-cloud'
    assert calls['clustered'] == 2
    assert calls['run'] == (True, 'dev')
    assert calls['function'] == {
        'name': 'skypilot-node',
        'serialized': True,
        'retries': 0,
        'gpu': 'H100:8',
    }
    ssh_setup, ssh_port, state_name, environment_name, clustered, ports = (
        calls['spawn'])
    assert 'ssh-ed25519 test' in ssh_setup
    assert (ssh_port, state_name, environment_name, clustered,
            ports) == (22, 'sky-test-on-cloud', 'dev', True, [8080])
    assert fake_modal.dicts[('dev', 'sky-test-on-cloud')] == {
        'num_nodes': 2,
        'call_id': 'fc-1',
    }


def test_modal_node_function_runs_without_skypilot(tmp_path):
    # The node body is shipped with cloudpickle; Modal containers do not have
    # SkyPilot installed, so it must not import anything from `sky`.
    cloudpickle = pytest.importorskip('modal._vendor.cloudpickle')
    payload = tmp_path / 'node.pkl'
    # pylint: disable=protected-access
    payload.write_bytes(cloudpickle.dumps(modal_utils._make_node_function()))
    script = ('import pickle, sys\n'
              'sys.modules["sky"] = None\n'
              f'fn = pickle.loads(open({str(payload)!r}, "rb").read())\n'
              'assert callable(fn)\n')

    subprocess.run([sys.executable, '-I', '-c', script],
                   check=True,
                   cwd=tmp_path)


def test_modal_query_ports(fake_modal):
    fake_modal.add_cluster(environment_name='dev',
                           nodes={
                               0: _node(
                                   port_urls={
                                       8080: 'https://web.modal.host',
                                       9000: 'https://api.modal.host:8443/path',
                                   })
                           })

    endpoints = modal_instance.query_ports(
        'test-on-cloud', ['22', '8080', '9000'],
        provider_config={'environment_name': 'dev'})

    assert endpoints[22][0] == common.SocketEndpoint(host='ssh.modal.host',
                                                     port=2222)
    assert endpoints[8080][0] == common.HTTPSEndpoint(host='web.modal.host',
                                                      port=None,
                                                      path='')
    assert endpoints[9000][0] == common.HTTPSEndpoint(host='api.modal.host',
                                                      port=8443,
                                                      path='path')


def test_modal_lifecycle_uses_provider_environment(fake_modal):
    provider_config = {'environment_name': 'dev'}

    assert not modal_instance.query_instances(
        'test', 'test-on-cloud', provider_config=provider_config)
    modal_instance.get_cluster_info('auto',
                                    'test-on-cloud',
                                    provider_config=provider_config)
    modal_instance.terminate_instances('test-on-cloud',
                                       provider_config=provider_config)

    assert fake_modal.dict_lookups == [('dev', 'sky-test-on-cloud')] * 3


def test_modal_lifecycle_supports_legacy_provider_config(fake_modal):
    assert not modal_instance.query_instances(
        'test', 'test-on-cloud', provider_config={})
    assert fake_modal.dict_lookups == [(None, 'sky-test-on-cloud')]


def test_modal_get_cluster_info_multi_node(fake_modal):
    fake_modal.add_cluster(
        nodes={
            1: _node(host='w.modal.host', port=3333, internal_ip='10.0.0.2'),
            0: _node(host='h.modal.host', port=2222, internal_ip='10.0.0.1'),
        })

    info = modal_instance.get_cluster_info('auto', 'test-on-cloud')

    assert info.head_instance_id == 'fc-1-0'
    assert set(info.instances) == {'fc-1-0', 'fc-1-1'}
    worker = info.instances['fc-1-1'][0]
    assert (worker.external_ip, worker.ssh_port,
            worker.internal_ip) == ('w.modal.host', 3333, '10.0.0.2')
    assert info.ssh_user == 'root'


def test_modal_query_instances_running(fake_modal):
    fake_modal.add_cluster(nodes={0: _node(), 1: _node()})

    statuses = modal_instance.query_instances('test', 'test-on-cloud')

    assert statuses == {
        'fc-1-0': (status_lib.ClusterStatus.UP, None),
        'fc-1-1': (status_lib.ClusterStatus.UP, None),
    }


def test_modal_query_instances_cleans_up_ended_call(fake_modal):
    fake_modal.add_cluster(status=_FakeModal.TERMINATED)

    assert not modal_instance.query_instances('test', 'test-on-cloud')
    # A status refresh drops the cluster without terminate_instances().
    assert not fake_modal.dicts


def test_modal_query_instances_can_include_terminated(fake_modal):
    fake_modal.add_cluster(status=_FakeModal.TERMINATED)

    statuses = modal_instance.query_instances('test',
                                              'test-on-cloud',
                                              non_terminated_only=False)

    assert statuses == {'fc-1-0': (None, None)}


def test_modal_query_instances_reports_preemption(fake_modal):
    # Modal reran the node after a preemption; the node ended the call.
    fake_modal.add_cluster(preempted=True)

    statuses = modal_instance.query_instances('test',
                                              'test-on-cloud',
                                              non_terminated_only=False)

    assert statuses == {'fc-1-0': (None, 'Preempted by Modal.')}


def test_modal_terminate_instances_idempotent(fake_modal):
    fake_modal.add_cluster()

    modal_instance.terminate_instances('test-on-cloud')
    modal_instance.terminate_instances('test-on-cloud')

    assert fake_modal.cancelled == ['fc-1']
    assert not fake_modal.dicts


def test_modal_terminate_workers_only_is_a_no_op(fake_modal):
    fake_modal.add_cluster()

    modal_instance.terminate_instances('test-on-cloud', worker_only=True)

    assert not fake_modal.cancelled


def test_modal_adaptor_follows_config_file_changes(tmp_path, monkeypatch):
    # modal reads its config once at import; a long-lived API server worker
    # must still see `modal token new` or an uploaded token.
    pytest.importorskip('modal')
    from sky.adaptors import modal as modal_adaptor
    modal_sdk = modal_adaptor.modal.load_module()
    path = tmp_path / '.modal.toml'
    monkeypatch.setattr(modal_sdk.config, 'user_config_path', str(path))
    monkeypatch.setattr(modal_sdk.config, '_user_config', {})
    monkeypatch.setattr(modal_sdk.config, '_profile', 'default')
    monkeypatch.delenv('MODAL_TOKEN_ID', raising=False)
    monkeypatch.delenv('MODAL_PROFILE', raising=False)
    # pylint: disable=protected-access
    lazy_modal = modal_adaptor._ModalImport()

    def token_id():
        return lazy_modal.config.config.get('token_id')

    assert token_id() is None
    path.write_text('[default]\ntoken_id = "ak-first"\nactive = true\n')
    assert token_id() == 'ak-first'
    # An atomic replace, as `modal token set` and the credential manager do.
    replacement = tmp_path / 'replacement.toml'
    replacement.write_text('[default]\ntoken_id = "ak-second"\nactive = true\n')
    replacement.replace(path)
    assert token_id() == 'ak-second'
    path.unlink()
    assert token_id() is None


def test_modal_adaptor_first_access_reads_a_config_written_after_import(
        tmp_path, monkeypatch):
    # Other code in an API server worker (a plugin, say) can import modal
    # directly before any token exists; the token is then stored by another
    # process. This worker's first access through the adaptor must not trust
    # the config modal read at import.
    pytest.importorskip('modal')
    from sky.adaptors import modal as modal_adaptor
    modal_sdk = modal_adaptor.modal.load_module()
    path = tmp_path / '.modal.toml'
    monkeypatch.setattr(modal_sdk.config, 'user_config_path', str(path))
    # What modal holds after being imported with no config file.
    monkeypatch.setattr(modal_sdk.config, '_user_config', {})
    monkeypatch.setattr(modal_sdk.config, '_profile', 'default')
    monkeypatch.delenv('MODAL_TOKEN_ID', raising=False)
    monkeypatch.delenv('MODAL_PROFILE', raising=False)
    dropped_clients = []
    monkeypatch.setattr(modal_sdk.Client, 'set_env_client',
                        dropped_clients.append)
    path.write_text('[uploaded]\ntoken_id = "ak-uploaded"\nactive = true\n')
    # pylint: disable=protected-access
    lazy_modal = modal_adaptor._ModalImport()

    assert lazy_modal.config.config.get('token_id') == 'ak-uploaded'
    # The active profile and the cached client are refreshed as well.
    assert modal_sdk.config._profile == 'uploaded'
    assert dropped_clients == [None]
    # Nothing was derived from the stale config, so the version is unchanged
    # and the unchanged file is not reloaded again.
    assert lazy_modal.config_version() == 0
    assert dropped_clients == [None]


def test_modal_adaptor_keeps_config_if_reload_internals_change(
        tmp_path, monkeypatch):
    # The reload uses private modal internals; if they go away, Modal must
    # keep working with the config it already loaded.
    pytest.importorskip('modal')
    from sky.adaptors import modal as modal_adaptor
    modal_sdk = modal_adaptor.modal.load_module()
    path = tmp_path / '.modal.toml'
    path.write_text('[default]\ntoken_id = "ak-first"\nactive = true\n')
    monkeypatch.setattr(modal_sdk.config, 'user_config_path', str(path))
    monkeypatch.setattr(modal_sdk.config, '_user_config',
                        {'default': {
                            'token_id': 'ak-first',
                            'active': True
                        }})
    monkeypatch.setattr(modal_sdk.config, '_profile', 'default')
    monkeypatch.delenv('MODAL_TOKEN_ID', raising=False)
    monkeypatch.delenv('MODAL_PROFILE', raising=False)
    monkeypatch.delattr(modal_sdk.config, '_read_user_config')
    # pylint: disable=protected-access
    lazy_modal = modal_adaptor._ModalImport()

    assert lazy_modal.config.config.get('token_id') == 'ak-first'
    path.write_text('[default]\ntoken_id = "ak-second"\nactive = true\n')
    assert lazy_modal.config.config.get('token_id') == 'ak-first'


def _isolated_modal_adaptor(tmp_path, monkeypatch, token_id):
    pytest.importorskip('modal')
    from sky.adaptors import modal as modal_adaptor
    modal_sdk = modal_adaptor.modal.load_module()
    path = tmp_path / '.modal.toml'
    path.write_text(f'[default]\ntoken_id = "{token_id}"\nactive = true\n')
    monkeypatch.setattr(modal_sdk.config, 'user_config_path', str(path))
    monkeypatch.setattr(modal_sdk.config, '_user_config',
                        {'default': {
                            'token_id': token_id,
                            'active': True
                        }})
    monkeypatch.setattr(modal_sdk.config, '_profile', 'default')
    monkeypatch.delenv('MODAL_TOKEN_ID', raising=False)
    monkeypatch.delenv('MODAL_PROFILE', raising=False)
    # pylint: disable=protected-access
    return modal_adaptor._ModalImport(), modal_sdk, path


def test_modal_adaptor_retries_a_failed_reload(tmp_path, monkeypatch):
    lazy_modal, modal_sdk, path = _isolated_modal_adaptor(
        tmp_path, monkeypatch, 'ak-first')
    assert lazy_modal.config_version() == 0
    read_user_config = modal_sdk.config._read_user_config
    failures = [PermissionError('unreadable')]

    def flaky_read():
        if failures:
            raise failures.pop()
        return read_user_config()

    monkeypatch.setattr(modal_sdk.config, '_read_user_config', flaky_read)
    path.write_text('[default]\ntoken_id = "ak-second"\nactive = true\n')

    # The failed reload keeps the old config and does not mark the file as
    # loaded, so the next access retries without the file changing again.
    assert lazy_modal.config.config.get('token_id') == 'ak-first'
    assert lazy_modal.config_version() == 1
    assert lazy_modal.config.config.get('token_id') == 'ak-second'


def test_modal_adaptor_publishes_new_config_after_reload(tmp_path, monkeypatch):
    # Threads skip the lock once the signature matches, so the signature must
    # not change before the new config and client are in place.
    lazy_modal, modal_sdk, path = _isolated_modal_adaptor(
        tmp_path, monkeypatch, 'ak-first')
    assert lazy_modal.config_version() == 0
    # pylint: disable=protected-access
    old_signature = lazy_modal._config_signature
    read_user_config = modal_sdk.config._read_user_config
    seen = []

    def observed_read():
        seen.append(lazy_modal._config_signature)
        return read_user_config()

    def observed_set_env_client(client):
        seen.append(lazy_modal._config_signature)
        del client

    monkeypatch.setattr(modal_sdk.config, '_read_user_config', observed_read)
    monkeypatch.setattr(modal_sdk.Client, 'set_env_client',
                        observed_set_env_client)
    path.write_text('[default]\ntoken_id = "ak-second"\nactive = true\n')

    assert lazy_modal.config_version() == 1
    assert seen == [old_signature, old_signature]
    assert lazy_modal._config_signature != old_signature


def test_modal_catalog_prices_follow_catalog_rates(tmp_path, monkeypatch):
    prices = dict(_MODAL_PRICES, H100=0.002, cpu=0.00002)
    monkeypatch.setattr(modal_catalog, '_df',
                        _write_modal_catalog(tmp_path / 'vms.csv', prices))

    assert modal_catalog.get_hourly_cost(
        '4CPU--16GB--H100:1', region='auto') == (pytest.approx(
            (2 * 0.00002 + 16 * 0.00000222 + 0.002) * 3600))
    assert modal_catalog.get_hourly_cost(
        '32CPU--128GB--H100:2', region='us-west') == pytest.approx(
            (16 * 0.00002 + 128 * 0.00000222 + 2 * 0.002) * 3600 * 1.75)
    # CPU-only shapes keep the non-preemptible rates.
    assert modal_catalog.get_hourly_cost(
        '16CPU--64GB', region='auto') == pytest.approx(
            (8 * 0.00003942 + 64 * 0.00000667) * 3600)


def test_modal_catalog_gpu_types_and_counts_come_from_catalog(
        tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_modal, 'GPU_MAX_COUNTS', {'H100': 4})
    monkeypatch.setattr(modal_catalog, '_df',
                        _write_modal_catalog(tmp_path / 'vms.csv'))

    assert modal_catalog.get_max_gpu_count('h100') == 4
    assert modal_catalog.get_max_gpu_count('T4') is None
    assert modal_catalog.get_instance_type_for_accelerator(
        'H100', 4) == (['16CPU--64GB--H100:4'], [])
    assert modal_catalog.get_instance_type_for_accelerator('H100',
                                                           8) == ([], [])
    assert modal_catalog.get_instance_type_for_accelerator('T4',
                                                           1) == (None, [])
    assert not modal_catalog.instance_type_exists('4CPU--16GB--T4:1')
    assert modal_catalog.list_accelerators(
        gpus_only=True,
        name_filter=None,
        region_filter='^auto$',
        quantity_filter=None).keys() == {'H100'}


def test_modal_any_gpu_count_up_to_the_maximum():
    """Modal takes `GPU:n` for any n up to the type's maximum (8; A10: 4)."""
    assert modal_catalog.get_instance_type_for_accelerator(
        'T4', 3) == (['12CPU--48GB--T4:3'], [])
    assert modal_catalog.get_instance_type_for_accelerator(
        'A10', 4) == (['16CPU--64GB--A10:4'], [])
    assert modal_catalog.get_instance_type_for_accelerator('A10', 5) == ([], [])
    assert not modal_catalog.instance_type_exists('8CPU--32GB--RTX-PRO-6000:2')
    assert modal_catalog.get_region_zones_for_accelerators('A10', 5) == []
    with pytest.raises(ValueError, match='No accelerator A10:5'):
        modal_catalog.get_accelerator_hourly_cost('A10', 5)
    # Fuzzy candidates still come from the listed shapes.
    instance_types, fuzzy = modal_catalog.get_instance_type_for_accelerator(
        'H10', 1)
    assert instance_types is None
    assert 'H100:1' in fuzzy


def test_modal_instance_type_names_round_trip():
    """Instance types parse from and render to the Kubernetes-style name."""
    for name in ('4CPU--16GB', '0.25CPU--0.125GB', '12CPU--48GB--T4:3',
                 '32CPU--128GB--H100:8', '4CPU--1.5GB--A10:4'):
        assert modal_catalog.ModalInstanceType.from_instance_type(
            name).name == name
        assert modal_catalog.instance_type_exists(name)

    instance = modal_catalog.ModalInstanceType.from_instance_type(
        '12CPU--48GB--t4:3')
    assert instance.name == '12CPU--48GB--T4:3'
    assert (instance.vcpus, instance.modal_cpu, instance.memory_gib,
            instance.memory_mib) == (12, 6, 48, 48 * 1024)
    assert (instance.accelerator_type, instance.accelerator_count) == ('T4', 3)
    # Memory is rounded up to whole MiB, as Modal takes MiB.
    assert modal_catalog.ModalInstanceType(4, 1.0001).memory_mib == 1025
    with pytest.raises(ValueError, match='must be set together'):
        modal_catalog.ModalInstanceType(4, 16, accelerator_count=1)
    with pytest.raises(ValueError, match='must be set together'):
        modal_catalog.ModalInstanceType(4, 16, accelerator_type='T4')


@pytest.mark.parametrize(
    'name,reason',
    [
        ('modal-h100-8x', 'Invalid Modal instance type'),
        ('4CPU', 'Invalid Modal instance type'),
        ('4CPU--16GB--H100', 'Invalid Modal instance type'),
        ('4CPU--16GB--H100:0', 'H100 in counts of 1-8 per container, not 0'),
        ('4CPU--16GB--H100:9', 'H100 in counts of 1-8 per container, not 9'),
        ('4CPU--16GB--Foo:1', 'Unsupported Modal accelerator'),
        # Modal's bounds: 0.125-64 cores (0.25-128 vCPUs), 128 MiB-336 GiB.
        ('0.1CPU--16GB', 'CPU request is out of bounds'),
        ('130CPU--16GB', 'CPU request is out of bounds'),
        ('4CPU--0.1GB', 'memory request is out of bounds'),
        ('4CPU--337GB', 'memory request is out of bounds'),
    ])
def test_modal_invalid_instance_types(name, reason):
    with pytest.raises(ValueError, match=reason):
        modal_catalog.ModalInstanceType.from_instance_type(name)
    assert not modal_catalog.instance_type_exists(name)
    assert not clouds.Modal().instance_type_exists(name)
    with pytest.raises(ValueError, match=f'No instance type {name} found'):
        modal_catalog.get_modal_args_from_instance_type(name)
    with pytest.raises(ValueError, match='Invalid instance type'):
        resources_lib.Resources(cloud=clouds.Modal(),
                                instance_type=name).validate()


def test_modal_catalog_rejects_spot_and_zones():
    instance_type = '4CPU--16GB--H100:1'
    with pytest.raises(ValueError, match='does not support spot'):
        modal_catalog.get_hourly_cost(instance_type, use_spot=True)
    with pytest.raises(ValueError, match='does not support zones'):
        modal_catalog.get_hourly_cost(instance_type, zone='us-west-1')
    with pytest.raises(ValueError, match='does not support spot'):
        modal_catalog.get_accelerator_hourly_cost('H100', 1, use_spot=True)
    with pytest.raises(ValueError, match='does not support zones'):
        modal_catalog.get_accelerator_hourly_cost('H100', 1, zone='us-west-1')
    with pytest.raises(ValueError, match='does not support zones'):
        modal_catalog.get_instance_type_for_accelerator('H100',
                                                        1,
                                                        zone='us-west-1')
    with pytest.raises(ValueError, match='does not support zones'):
        modal_catalog.check_accelerator_attachable_to_host(instance_type,
                                                           {'H100': 1},
                                                           zone='us-west-1')
    assert modal_catalog.get_default_instance_type(use_spot=True) is None
    assert modal_catalog.get_default_instance_type(zone='us-west-1') is None
    assert modal_catalog.get_instance_type_for_accelerator(
        'H100', 1, use_spot=True) == (None, [])
    assert not modal_catalog.get_region_zones_for_instance_type(instance_type,
                                                                use_spot=True)
    assert not modal_catalog.get_region_zones_for_accelerators(
        'H100', 1, use_spot=True)
    assert clouds.Modal.regions_with_offering(instance_type,
                                              None,
                                              use_spot=True,
                                              region=None,
                                              zone=None) == []


def test_modal_catalog_unknown_region():
    assert modal_catalog.validate_region_zone(None, None) == (None, None)
    with pytest.raises(ValueError, match="Invalid region 'mars'"):
        modal_catalog.validate_region_zone('mars', None)
    with pytest.raises(ValueError, match='Did you mean.*us-west'):
        modal_catalog.validate_region_zone('us-wst', None)
    with pytest.raises(ValueError, match="Invalid Modal region 'mars'"):
        modal_catalog.get_hourly_cost('4CPU--16GB', region='mars')
    assert modal_catalog.get_default_instance_type(region='mars') is None
    assert modal_catalog.get_instance_type_for_accelerator(
        'H100', 1, region='mars') == (None, [])
    with pytest.raises(ValueError, match='No accelerator H100:1'):
        modal_catalog.get_accelerator_hourly_cost('H100', 1, region='mars')
    # Every region in the table validates and prices.
    for region in modal_catalog.regions():
        assert modal_catalog.validate_region_zone(region.name,
                                                  None) == (region.name, None)
        assert modal_catalog.get_hourly_cost('4CPU--16GB',
                                             region=region.name) > 0


@pytest.mark.parametrize(
    'cpus,memory,expected',
    [
        (None, None, '4CPU--16GB'),
        ('8', None, '8CPU--32GB'),  # 4 GiB per vCPU by default
        ('8+', '32+', '8CPU--32GB'),
        ('8', '2x', '8CPU--16GB'),
        ('8', '0.5x', '8CPU--4GB'),
        ('100', None, '100CPU--336GB'),  # default memory capped at the max
        ('0.1', None, None),
        ('8', '0.01', None),
    ])
def test_modal_cpu_and_memory_requests(cpus, memory, expected):
    assert modal_catalog.get_default_instance_type(cpus=cpus,
                                                   memory=memory) == expected
    assert clouds.Modal.get_default_instance_type(cpus=cpus,
                                                  memory=memory) == expected

    gpu_types, fuzzy = modal_catalog.get_instance_type_for_accelerator(
        'T4', 2, cpus=cpus, memory=memory)

    assert not fuzzy
    if cpus is None:
        # GPU shapes default to 4 vCPUs per GPU instead.
        assert gpu_types == ['8CPU--32GB--T4:2']
    elif expected is None:
        assert gpu_types == []
    else:
        assert gpu_types == [f'{expected}--T4:2']


def test_modal_max_hourly_cost_is_region_aware():
    gpu_price = modal_catalog.get_hourly_cost('4CPU--16GB--T4:1', region='auto')
    assert modal_catalog.get_instance_type_for_accelerator(
        'T4', 1, max_hourly_cost=gpu_price + 0.01) == (['4CPU--16GB--T4:1'], [])
    assert modal_catalog.get_instance_type_for_accelerator(
        'T4', 1, max_hourly_cost=gpu_price - 0.01) == ([], [])
    # A narrow region costs 1.75x, so the same cap no longer fits there.
    assert modal_catalog.get_instance_type_for_accelerator(
        'T4', 1, region='us-west', max_hourly_cost=gpu_price + 0.01) == ([], [])
    assert modal_catalog.get_instance_type_for_accelerator(
        'T4', 1, region='us-west',
        max_hourly_cost=gpu_price * 1.75 + 0.01) == (['4CPU--16GB--T4:1'], [])

    cpu_price = modal_catalog.get_hourly_cost('4CPU--16GB', region='auto')
    assert modal_catalog.get_default_instance_type(max_hourly_cost=cpu_price +
                                                   0.01) == '4CPU--16GB'
    assert modal_catalog.get_default_instance_type(
        region='us-west', max_hourly_cost=cpu_price + 0.01) is None


def test_modal_fuzzy_candidates_come_from_listed_shapes():
    instance_types, fuzzy = modal_catalog.get_instance_type_for_accelerator(
        'H1', 1)
    assert instance_types is None
    assert fuzzy == ['H100:1', 'H100:2', 'H100:4', 'H100:8']

    _, fuzzy = modal_catalog.get_instance_type_for_accelerator('H1', 4)
    assert fuzzy == ['H100:4', 'H100:8']

    _, fuzzy = modal_catalog.get_instance_type_for_accelerator('a100-8', 1)
    assert fuzzy[0] == 'A100-80GB:1'

    assert modal_catalog.get_instance_type_for_accelerator('Z9',
                                                           1) == (None, [])


def test_modal_listed_shapes_follow_gpu_limits():
    """`sky gpus list` shows 1/2/4/8 of each GPU, up to the GPU's maximum."""
    listed = modal_catalog.list_accelerators(gpus_only=True,
                                             name_filter=None,
                                             region_filter='^auto$',
                                             quantity_filter=None)

    counts = {
        name: sorted(int(info.accelerator_count) for info in infos)
        for name, infos in listed.items()
    }
    assert set(counts) == set(fetch_modal.GPU_MAX_COUNTS)
    assert counts['H100'] == [1, 2, 4, 8]
    assert counts['A10'] == [1, 2, 4]
    assert counts['RTX-PRO-6000'] == [1]
    for name, infos in listed.items():
        for info in infos:
            count = int(info.accelerator_count)
            assert info.instance_type == (
                f'{4 * count}CPU--{16 * count}GB--{name}:{count}')
            assert (info.cpu_count, info.memory) == (4 * count, 16 * count)
            assert info.device_memory == fetch_modal.GPU_MEMORY_GIB[name]
            assert info.price == pytest.approx(
                modal_catalog.get_hourly_cost(info.instance_type,
                                              region='auto'))
            assert pd.isna(info.spot_price)
            assert info.region == 'auto'
    # Launchable counts that are not listed are still not shown.
    assert modal_catalog.list_accelerators(gpus_only=True,
                                           name_filter='^T4$',
                                           region_filter='^auto$',
                                           quantity_filter=3) == {}

    per_region = modal_catalog.list_accelerators(gpus_only=True,
                                                 name_filter='t4',
                                                 region_filter=None,
                                                 quantity_filter=1,
                                                 case_sensitive=False,
                                                 all_regions=True)['T4']
    price_by_region = {info.region: info.price for info in per_region}
    assert set(price_by_region) == set(_MODAL_REGION_MULTIPLIERS)
    assert price_by_region['us'] == pytest.approx(price_by_region['auto'] *
                                                  1.15)
    assert price_by_region['us-west'] == pytest.approx(price_by_region['auto'] *
                                                       1.75)

    # What `sky gpus list --infra modal L4` calls; the cheapest region wins.
    l4 = sky_catalog.list_accelerators(gpus_only=True,
                                       name_filter='^L4$',
                                       clouds='modal')['L4']
    assert [(info.instance_type, info.region) for info in l4] == [
        ('4CPU--16GB--L4:1', 'auto'),
        ('8CPU--32GB--L4:2', 'auto'),
        ('16CPU--64GB--L4:4', 'auto'),
        ('32CPU--128GB--L4:8', 'auto'),
    ]


def test_modal_regions_are_cheapest_first():
    names = [region.name for region in modal_catalog.regions()]

    assert names == (['auto'] + sorted(fetch_modal.BROAD_REGIONS) +
                     sorted(fetch_modal.NARROW_REGIONS))
    assert all(region.zones is None for region in modal_catalog.regions())
    assert [region.name for region in clouds.Modal.regions()] == names
    assert [
        region.name
        for region in modal_catalog.get_region_zones_for_accelerators('A10', 4)
    ] == names
    assert [
        region.name
        for region in modal_catalog.get_region_zones_for_instance_type(
            '2CPU--8GB', False)
    ] == names


def test_modal_catalog_price_missing_in_a_region(tmp_path, monkeypatch):
    """A GPU priced only in `auto` exists but cannot be priced elsewhere."""
    monkeypatch.setattr(
        modal_catalog, '_df',
        _write_modal_catalog(tmp_path / 'vms.csv',
                             keep_row=lambda row: row['AcceleratorName'] != 'T4'
                             or row['Region'] == 'auto'))

    assert modal_catalog.instance_type_exists('4CPU--16GB--T4:1')
    assert modal_catalog.get_hourly_cost('4CPU--16GB--T4:1', region='auto') > 0
    with pytest.raises(ValueError,
                       match="No Modal price for T4:1 in region 'us'"):
        modal_catalog.get_hourly_cost('4CPU--16GB--T4:1', region='us')


def test_modal_cloud_prices_resources_from_the_instance_type():
    cloud = clouds.Modal()
    instance_type = '12CPU--48GB--T4:3'
    expected = (6 * 0.0000131 + 48 * 0.00000222 + 3 * 0.000164) * 3600

    assert cloud.instance_type_exists(instance_type)
    assert cloud.get_vcpus_mem_from_instance_type(instance_type) == (12, 48)
    assert cloud.get_accelerators_from_instance_type(instance_type) == {'T4': 3}
    assert cloud.accelerators_to_hourly_cost({'T4': 3}, False, 'auto') == 0.0
    assert cloud.instance_type_to_hourly_cost(
        instance_type, False, 'us') == pytest.approx(expected * 1.15)
    assert cloud.validate_region_zone('us', None) == ('us', None)

    resources = resources_lib.Resources(cloud=cloud,
                                        instance_type=instance_type,
                                        region='us')
    assert resources.accelerators == {'T4': 3}
    assert resources.get_cost(3600) == pytest.approx(expected * 1.15)
    # Without a region, the cluster runs in `auto` and is priced there.
    resources = resources_lib.Resources(cloud=cloud,
                                        instance_type=instance_type)
    assert resources.get_cost(3600) == pytest.approx(expected)


@pytest.mark.parametrize('request_kwargs,expected_types,fuzzy_contains', [
    (dict(accelerators={'T4': 3}), ['12CPU--48GB--T4:3'], None),
    (dict(accelerators={'t4': 3}, cpus='2', memory='4'), ['2CPU--4GB--T4:3'
                                                         ], None),
    (dict(accelerators={'A10': 5}), [], None),
    (dict(accelerators={'H10': 1}), [], 'H100:1'),
    (dict(cpus='2'), ['2CPU--8GB'], None),
    (dict(cpus='2', memory='64'), ['2CPU--64GB'], None),
    (dict(region='us-west', accelerators={'L4': 1}), ['4CPU--16GB--L4:1'
                                                     ], None),
    (dict(use_spot=True, accelerators={'L4': 1}), [], None),
    (dict(use_spot=True), [], None),
])
def test_modal_feasible_resources(request_kwargs, expected_types,
                                  fuzzy_contains):
    resources = resources_lib.Resources(cloud=clouds.Modal(), **request_kwargs)

    feasible = clouds.Modal()._get_feasible_launchable_resources(  # pylint: disable=protected-access
        resources)

    assert [r.instance_type for r in feasible.resources_list] == expected_types
    for launchable in feasible.resources_list:
        assert launchable.region == request_kwargs.get('region')
        assert launchable.accelerators == (
            modal_catalog.get_accelerators_from_instance_type(
                launchable.instance_type))
    if fuzzy_contains is None:
        assert not feasible.fuzzy_candidate_list
    else:
        assert fuzzy_contains in feasible.fuzzy_candidate_list


def test_modal_feasible_resources_keep_a_given_instance_type():
    resources = resources_lib.Resources(cloud=clouds.Modal(),
                                        instance_type='2CPU--4GB--T4:3')

    feasible = clouds.Modal()._get_feasible_launchable_resources(  # pylint: disable=protected-access
        resources)

    assert [r.instance_type for r in feasible.resources_list
           ] == ['2CPU--4GB--T4:3']
    assert feasible.resources_list[0].accelerators == {'T4': 3}


def _pricing_page(gpus):
    """Mimics modal.com/pricing: each price is drawn twice, once as
    screen-reader text and once as aria-hidden animated digits."""

    def price(value):
        digits = ''.join(f'<span>{c}</span>' for c in value)
        return (f'$<span class="inline-flex tabular-nums">'
                f'<span class="sr-only">{value}</span> '
                f'<span class="inline-flex" aria-hidden="true">{digits}'
                f'</span></span>')

    rows = ''.join(f'<div><p>Nvidia {name}</p><p>{price(value)} / sec</p>'
                   '</div>' for name, value in gpus.items())
    return (f'<html><head><script>var x = "Nvidia T4 $ 9 / sec";</script>'
            f'</head><body><h2>Resource costs</h2><p>GPU Tasks</p>{rows}'
            f'<div><p>CPU</p><p>{price("0.0000131")} / core / sec</p>'
            '<p>Physical core (2 vCPU equivalent)</p><br></div>'
            f'<div><p>Memory</p><p>{price("0.00000222")} / GiB / sec</p></div>'
            '<h2>Modal Sandbox pricing</h2><div><p>CPU (non-preemptible)</p>'
            '<p>Physical core (2 vCPU equivalent)</p>'
            f'<p>{price("0.00003942")} / core / sec</p></div>'
            '<div><p>Memory (non-preemptible)</p>'
            f'<p>{price("0.00000667")} / GiB / sec</p></div></body></html>')


_PAGE_GPUS = {
    'B300': '0.001972',
    'B200': '0.001736',
    'H200 SXM': '0.001261',
    'H100 SXM5': '0.001097',
    'RTX PRO 6000': '0.000842',
    'A100, 80 GB': '0.000694',
    'A100, 40 GB': '0.000583',
    'L40S': '0.000542',
    'A10': '0.000306',
    'L4': '0.000222',
    'T4': '0.000164',
}


def test_fetch_modal_parses_pricing_page():
    prices = fetch_modal.parse_pricing_page(
        _pricing_page(dict(_PAGE_GPUS, **{'X9000': '0.5'})))

    assert prices == _MODAL_PRICES


def test_fetch_modal_fails_when_a_gpu_price_is_missing():
    gpus = dict(_PAGE_GPUS)
    del gpus['H100 SXM5']
    with pytest.raises(fetch_modal.ModalPricingError, match='H100'):
        fetch_modal.parse_pricing_page(_pricing_page(gpus))
    with pytest.raises(fetch_modal.ModalPricingError, match='GPU Tasks'):
        fetch_modal.parse_pricing_page('<html><body>Pricing</body></html>')


def test_fetch_modal_parses_region_multipliers():
    page = ('<table><tr><td>Broad (e.g. <code>us</code>)</td><td>1.2x</td>'
            '</tr><tr><td>Narrow (e.g. <code>us-west</code>)</td>'
            '<td>1.8x</td></tr></table>')

    multipliers = fetch_modal.parse_region_multipliers(page)

    assert multipliers['auto'] == 1.0
    assert multipliers['eu'] == 1.2
    assert multipliers['us-west'] == 1.8
    with pytest.raises(fetch_modal.ModalPricingError, match='broad'):
        fetch_modal.parse_region_multipliers('<p>no prices</p>')


def test_fetch_modal_catalog_rows():
    """The standard catalog columns, GCP-style: accelerator rows per GPU and
    count, plus two per-unit rate rows."""
    rows = fetch_modal.create_catalog(_MODAL_PRICES, _MODAL_REGION_MULTIPLIERS)

    assert fetch_modal.COLUMNS == [
        'InstanceType', 'vCPUs', 'MemoryGiB', 'AcceleratorName',
        'AcceleratorCount', 'GpuInfo', 'Region', 'AvailabilityZone', 'Price',
        'SpotPrice'
    ]
    assert all(set(row) == set(fetch_modal.COLUMNS) for row in rows)
    assert len(rows) == len(_MODAL_REGION_MULTIPLIERS) * (
        2 + sum(fetch_modal.GPU_MAX_COUNTS.values()))
    assert {row['Region'] for row in rows} == set(_MODAL_REGION_MULTIPLIERS)
    assert all(row['AvailabilityZone'] is None for row in rows)
    assert (fetch_modal.CPU_RESOURCE,
            fetch_modal.MEMORY_RESOURCE) == (modal_catalog.CPU_RESOURCE,
                                             modal_catalog.MEMORY_RESOURCE)
    assert set(fetch_modal.GPU_MEMORY_GIB) == set(fetch_modal.GPU_MAX_COUNTS)
    assert {name for names in fetch_modal.GPU_NAMES.values() for name in names
           } == set(fetch_modal.GPU_MAX_COUNTS)

    rates = {(row['InstanceType'], row['Region']): row
             for row in rows
             if row['InstanceType'] is not None}
    cpu = rates[('cpu-core', 'us')]
    assert (cpu['vCPUs'], cpu['MemoryGiB']) == (2, 0)
    assert cpu['AcceleratorName'] is None and cpu['GpuInfo'] is None
    # Price: non-preemptible rate; SpotPrice: Modal's list (preemptible) rate.
    assert cpu['Price'] == pytest.approx(0.00003942 * 3600 * 1.15)
    assert cpu['SpotPrice'] == pytest.approx(0.0000131 * 3600 * 1.15)
    memory = rates[('memory-gib', 'us-west')]
    assert (memory['vCPUs'], memory['MemoryGiB']) == (0, 1)
    assert memory['Price'] == pytest.approx(0.00000667 * 3600 * 1.75)
    assert memory['SpotPrice'] == pytest.approx(0.00000222 * 3600 * 1.75)

    gpus = {(row['AcceleratorName'], row['AcceleratorCount'], row['Region']):
            row for row in rows if row['InstanceType'] is None}
    h100x2 = gpus[('H100', 2, 'auto')]
    assert (h100x2['vCPUs'], h100x2['MemoryGiB']) == (None, None)
    assert h100x2['Price'] == pytest.approx(2 * 0.001097 * 3600)
    assert h100x2['SpotPrice'] is None
    assert ast.literal_eval(h100x2['GpuInfo']) == {
        'Gpus': [{
            'Name': 'H100',
            'Manufacturer': 'NVIDIA',
            'Count': 2,
            'MemoryInfo': {
                'SizeInMiB': 80 * 1024
            },
        }],
        'TotalGpuMemoryInMiB': 2 * 80 * 1024,
    }
    assert gpus[('H100', 8,
                 'us')]['Price'] == pytest.approx(8 * 0.001097 * 3600 * 1.15)

    def counts(name):
        return sorted(c for n, c, r in gpus if n == name and r == 'auto')

    assert counts('H100') == list(range(1, 9))
    assert counts('A10') == [1, 2, 3, 4]
    assert counts('RTX-PRO-6000') == [1]


def test_modal_catalog_offers_only_the_listed_gpu_counts(tmp_path, monkeypatch):
    """Like gcp/vms.csv, each accelerator row is a count Modal offers; a
    count with no row is rejected even below the maximum."""
    monkeypatch.setattr(
        modal_catalog, '_df',
        _write_modal_catalog(tmp_path / 'vms.csv',
                             keep_row=lambda row: row['AcceleratorName'] !=
                             'H100' or row['AcceleratorCount'] in (1, 2, 4)))

    assert modal_catalog.get_max_gpu_count('H100') == 4
    assert modal_catalog.get_instance_type_for_accelerator(
        'H100', 2) == (['8CPU--32GB--H100:2'], [])
    assert modal_catalog.get_instance_type_for_accelerator('H100',
                                                           3) == ([], [])
    with pytest.raises(ValueError,
                       match='H100 in counts of 1, 2, 4 per container, not 3'):
        modal_catalog.ModalInstanceType.from_instance_type(
            '12CPU--48GB--H100:3')
    with pytest.raises(ValueError, match='No accelerator H100:3'):
        modal_catalog.get_accelerator_hourly_cost('H100', 3)
    listed = modal_catalog.list_accelerators(gpus_only=True,
                                             name_filter='^H100$',
                                             region_filter='^auto$',
                                             quantity_filter=None)['H100']
    assert sorted(int(info.accelerator_count) for info in listed) == [1, 2, 4]


def test_fetch_modal_main_writes_a_table_the_catalog_reads(
        tmp_path, monkeypatch):
    """The fetcher's vms.csv (what skypilot-catalog hosts) round-trips."""
    region_page = (
        '<table><tr><td>Broad (e.g. <code>us</code>)</td><td>1.2x</td></tr>'
        '<tr><td>Narrow (e.g. <code>us-west</code>)</td><td>1.8x</td></tr>'
        '</table>')
    pages = {
        fetch_modal.PRICING_URL: _pricing_page(_PAGE_GPUS),
        fetch_modal.REGION_SELECTION_URL: region_page,
    }
    monkeypatch.setattr(fetch_modal, '_get', lambda url: pages[url])
    output = tmp_path / 'modal' / 'vms.csv'
    monkeypatch.setattr(sys, 'argv', ['fetch_modal', '--output', str(output)])

    assert fetch_modal.main() == 0

    with open(output, encoding='utf-8', newline='') as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0]) == fetch_modal.COLUMNS
    assert len(rows) == len(_MODAL_REGION_MULTIPLIERS) * (
        2 + sum(fetch_modal.GPU_MAX_COUNTS.values()))
    assert rows[0]['InstanceType'] == 'cpu-core'
    assert rows[0]['AcceleratorName'] == ''  # empty, not 'None'
    gpu_row = next(row for row in rows if row['AcceleratorName'] == 'A10')
    assert (gpu_row['InstanceType'], gpu_row['SpotPrice']) == ('', '')

    monkeypatch.setattr(
        modal_catalog, '_df',
        catalog_common.LazyDataFrame(str(output), lambda: False))
    assert modal_catalog.get_max_gpu_count('A10') == 4
    assert modal_catalog.get_hourly_cost(
        '4CPU--16GB--H100:1', region='us') == pytest.approx(
            (2 * 0.0000131 + 16 * 0.00000222 + 0.001097) * 3600 * 1.2)
    assert modal_catalog.get_hourly_cost(
        '4CPU--16GB', region='us-west') == pytest.approx(
            (2 * 0.00003942 + 16 * 0.00000667) * 3600 * 1.8)
    assert [region.name for region in modal_catalog.regions()][0] == 'auto'
    listing = modal_catalog.list_accelerators(gpus_only=True,
                                              name_filter='^A100$',
                                              region_filter='^auto$',
                                              quantity_filter=1)['A100'][0]
    assert listing.device_memory == 40
    assert listing.instance_type == '4CPU--16GB--A100:1'
