"""Tests for Modal cloud provider."""

from types import SimpleNamespace

import pytest

from sky import clouds
from sky import resources as resources_lib
from sky.catalog import modal_catalog
from sky.clouds import modal as modal_cloud
from sky.provision import common
from sky.provision.modal import instance as modal_instance
from sky.provision.modal import modal_utils
from sky.utils import resources_utils
from sky.utils import status_lib


def _provision_config(node_config):
    return common.ProvisionConfig(provider_config={},
                                  authentication_config={},
                                  docker_config={},
                                  node_config=node_config,
                                  count=1,
                                  tags={},
                                  resume_stopped_nodes=False,
                                  ports_to_open_on_launch=None)


def _check_modal_compute_credentials():
    # pylint: disable=protected-access
    return clouds.Modal._check_compute_credentials()


def _set_modal_config(monkeypatch, config):
    monkeypatch.setattr(modal_cloud.modal_adaptor, 'modal',
                        SimpleNamespace(config=SimpleNamespace(config=config)))


def test_modal_catalog_regions_and_prices():
    assert modal_catalog.validate_region_zone('auto', None) == ('auto', None)
    assert modal_catalog.validate_region_zone('us-west',
                                              None) == ('us-west', None)
    with pytest.raises(ValueError, match='does not support zones'):
        modal_catalog.validate_region_zone('auto', 'us-west-1')

    auto_price = modal_catalog.get_hourly_cost('modal-h100-1x', False, 'auto',
                                               None)
    broad_price = modal_catalog.get_hourly_cost('modal-h100-1x', False, 'us',
                                                None)
    narrow_price = modal_catalog.get_hourly_cost('modal-h100-1x', False,
                                                 'us-west', None)
    assert broad_price == pytest.approx(auto_price * 1.5)
    assert narrow_price == pytest.approx(auto_price * 1.75)


def test_modal_catalog_gpu_to_sandbox_args():
    assert modal_catalog.get_modal_args_from_instance_type(
        'modal-cpu-4x-16gb') == (None, 2.0, 16 * 1024)
    assert modal_catalog.get_modal_args_from_instance_type(
        'modal-a100-80gb-2x') == ('A100-80GB:2', 2.0, 16 * 1024)


def test_modal_infra_schema():
    resources = resources_lib.Resources.from_yaml_config({
        'infra': 'modal',
        'accelerators': 'H100:1',
    })

    resource = next(iter(resources))
    assert isinstance(resource.cloud, clouds.Modal)
    assert resource.accelerators == {'H100': 1}


def test_modal_default_region_does_not_expand_to_region_failover():
    implicit_regions = clouds.Modal.regions_with_offering('modal-h100-1x',
                                                          accelerators=None,
                                                          use_spot=False,
                                                          region=None,
                                                          zone=None)
    explicit_regions = clouds.Modal.regions_with_offering('modal-h100-1x',
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
    _set_modal_config(monkeypatch, {})

    ok, message = _check_modal_compute_credentials()

    assert not ok
    assert 'Modal credentials were not found' in message


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
    monkeypatch.setattr(modal_cloud.os.path, 'expanduser',
                        lambda path: str(credential_file))

    assert clouds.Modal().get_credential_file_mounts() == {
        '~/.modal.toml': '~/.modal.toml',
    }


def test_modal_deploy_variables_auto_region():
    cloud = clouds.Modal()
    resources = resources_lib.Resources(cloud=cloud,
                                        instance_type='modal-cpu-4x-16gb')

    variables = cloud.make_deploy_resources_variables(
        resources=resources,
        cluster_name=resources_utils.ClusterName('test', 'test'),
        region=clouds.Region('auto'),
        zones=None,
        num_nodes=1)

    assert variables['modal_region'] is None
    assert variables['modal_gpu'] is None
    assert variables['modal_cpu'] == 2.0
    assert variables['modal_memory'] == 16 * 1024


def test_modal_deploy_variables_gpu_region():
    cloud = clouds.Modal()
    resources = resources_lib.Resources(cloud=cloud,
                                        instance_type='modal-a100-80gb-2x')

    variables = cloud.make_deploy_resources_variables(
        resources=resources,
        cluster_name=resources_utils.ClusterName('test', 'test'),
        region=clouds.Region('us-west'),
        zones=None,
        num_nodes=1)

    assert variables['modal_region'] == 'us-west'
    assert variables['modal_gpu'] == 'A100-80GB:2'


def test_modal_run_instances_creates_sandbox(monkeypatch):
    created = {}

    class FakeSandbox:

        object_id = 'sb-created'

        @staticmethod
        def create(*args, **kwargs):
            created['args'] = args
            created['kwargs'] = kwargs
            return FakeSandbox()

    monkeypatch.setattr(modal_instance.modal_utils,
                        'get_active_sandboxes_by_name', lambda name: {})
    monkeypatch.setattr(modal_instance.modal_utils, 'get_app',
                        lambda create_if_missing: 'app')
    monkeypatch.setattr(modal_instance.modal_utils, 'get_image',
                        lambda: 'image')
    monkeypatch.setattr(modal_instance.modal_utils, 'get_ssh_tunnel',
                        lambda sandbox: ('host', 12345))
    monkeypatch.setattr(modal_instance.modal_adaptor, 'modal',
                        SimpleNamespace(Sandbox=FakeSandbox))

    record = modal_instance.run_instances(region='auto',
                                          cluster_name='test',
                                          cluster_name_on_cloud='test-on-cloud',
                                          config=_provision_config({
                                              'PublicKey': 'ssh-ed25519 test',
                                              'ModalRegion': None,
                                              'Gpu': 'H100',
                                              'Cpu': 2.0,
                                              'Memory': 16 * 1024,
                                              'Timeout': 24 * 60 * 60,
                                              'IdleTimeout': None,
                                          }))

    assert record.provider_name == 'modal'
    assert record.head_instance_id == 'sb-created'
    assert record.created_instance_ids == ['sb-created']
    assert created['args'][:2] == ('bash', '-lc')
    assert created['kwargs']['name'] == 'test-on-cloud'
    assert created['kwargs']['unencrypted_ports'] == [22]
    assert created['kwargs']['region'] is None
    assert created['kwargs']['gpu'] == 'H100'


def test_modal_run_instances_reuses_existing_sandbox(monkeypatch):
    sandbox = SimpleNamespace(object_id='sb-existing')
    monkeypatch.setattr(modal_instance.modal_utils,
                        'get_active_sandboxes_by_name',
                        lambda name: {'sb-existing': sandbox})
    monkeypatch.setattr(modal_instance.modal_utils, 'sandbox_status',
                        lambda sandbox: None)

    record = modal_instance.run_instances(region='auto',
                                          cluster_name='test',
                                          cluster_name_on_cloud='test-on-cloud',
                                          config=_provision_config({
                                              'PublicKey': 'ssh-ed25519 test',
                                          }))

    assert record.head_instance_id == 'sb-existing'
    assert not record.created_instance_ids
    assert not record.resumed_instance_ids


def test_modal_get_active_sandboxes_by_name(monkeypatch):
    sandbox = SimpleNamespace(object_id='sb-existing')

    class FakeSandbox:

        @staticmethod
        def from_name(app_name, name):
            assert app_name == modal_utils.APP_NAME
            assert name == 'test-on-cloud'
            return sandbox

    monkeypatch.setattr(modal_utils.modal_adaptor, 'modal',
                        SimpleNamespace(Sandbox=FakeSandbox))

    assert modal_utils.get_active_sandboxes_by_name('test-on-cloud') == {
        'sb-existing': sandbox
    }


def test_modal_query_instances_filters_terminated(monkeypatch):
    running = SimpleNamespace(object_id='sb-running')
    terminated = SimpleNamespace(object_id='sb-terminated')
    monkeypatch.setattr(
        modal_instance.modal_utils, 'get_active_sandboxes_by_name',
        lambda name: {
            'sb-running': running,
            'sb-terminated': terminated,
        })
    monkeypatch.setattr(modal_instance.modal_utils, 'sandbox_status',
                        lambda sandbox: None if sandbox is running else 137)

    statuses = modal_instance.query_instances('test', 'test-on-cloud')

    assert statuses == {'sb-running': (status_lib.ClusterStatus.UP, None)}
