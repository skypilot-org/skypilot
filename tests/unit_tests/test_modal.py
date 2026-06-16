"""Tests for Modal cloud provider."""

import sys
from types import SimpleNamespace

import pytest

from sky import clouds
from sky import exceptions
from sky import resources as resources_lib
from sky.catalog import modal_catalog
from sky.clouds import modal as modal_cloud
from sky.provision import common
from sky.provision.modal import instance as modal_instance
from sky.provision.modal import modal_utils
from sky.setup_files import dependencies
from sky.skylet import constants
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils import status_lib


def _provision_config(node_config, ports_to_open_on_launch=None):
    return common.ProvisionConfig(
        provider_config={},
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


def _set_modal_config(monkeypatch, config):
    monkeypatch.setattr(modal_cloud.modal_adaptor, 'modal',
                        SimpleNamespace(config=SimpleNamespace(config=config)))


def test_modal_cloud_registration():
    cloud = registry.CLOUD_REGISTRY.from_str('modal')

    assert isinstance(cloud, clouds.Modal)
    assert clouds.Modal.canonical_name() == 'modal'
    assert 'modal' in constants.ALL_CLOUDS


def test_modal_dependency_gating():
    assert dependencies.cloud_dependencies['modal'] == [
        'modal>=1.5.0; python_version>="3.10"',
    ]
    modal_in_all = any(
        dep.startswith('modal>=1.5.0')
        for dep in dependencies.extras_require['all'])
    assert modal_in_all == (sys.version_info >= (3, 10))


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


def test_modal_unsupported_features():
    resources = resources_lib.Resources(cloud=clouds.Modal(),
                                        instance_type='modal-cpu-4x-16gb')
    unsupported = _modal_unsupported_features(resources)

    expected = {
        clouds.CloudImplementationFeatures.STOP,
        clouds.CloudImplementationFeatures.MULTI_NODE,
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER,
        clouds.CloudImplementationFeatures.IMAGE_ID,
        clouds.CloudImplementationFeatures.SPOT_INSTANCE,
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER,
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER,
        clouds.CloudImplementationFeatures.STORAGE_MOUNTING,
        clouds.CloudImplementationFeatures.HOST_CONTROLLERS,
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS,
        clouds.CloudImplementationFeatures.AUTO_TERMINATE,
        clouds.CloudImplementationFeatures.AUTOSTOP,
        clouds.CloudImplementationFeatures.AUTODOWN,
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK,
        clouds.CloudImplementationFeatures.LOCAL_DISK,
    }

    assert expected <= set(unsupported)
    assert all(
        isinstance(message, str) and message
        for message in unsupported.values())


def test_modal_supports_docker_images_and_open_ports():
    resources = resources_lib.Resources(cloud=clouds.Modal(),
                                        instance_type='modal-cpu-4x-16gb',
                                        image_id='docker:ubuntu:22.04',
                                        ports=['8080'])
    unsupported = _modal_unsupported_features(resources)

    assert resources.extract_docker_image() == 'ubuntu:22.04'
    assert clouds.CloudImplementationFeatures.DOCKER_IMAGE not in unsupported
    assert clouds.CloudImplementationFeatures.OPEN_PORTS not in unsupported


def test_modal_rejects_cloud_image_ids():
    resources = resources_lib.Resources(cloud=clouds.Modal(),
                                        instance_type='modal-cpu-4x-16gb',
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

    monkeypatch.setattr(modal_cloud.modal_adaptor, 'modal',
                        SimpleNamespace(Workspace=FakeWorkspaceFactory))

    assert clouds.Modal.get_user_identities() == [['Modal workspace openpipe']]


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
    assert variables['modal_docker_image'] is None


def test_modal_deploy_variables_gpu_region():
    cloud = clouds.Modal()
    resources = resources_lib.Resources(cloud=cloud,
                                        instance_type='modal-a100-80gb-2x',
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


def test_modal_run_instances_creates_sandbox(monkeypatch):
    created = {}
    image_calls = []

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
    monkeypatch.setattr(
        modal_instance.modal_utils,
        'get_image',
        lambda docker_image=None: image_calls.append(docker_image) or 'image')
    monkeypatch.setattr(modal_instance.modal_utils, 'get_ssh_tunnel',
                        lambda sandbox: ('host', 12345))
    monkeypatch.setattr(modal_instance.modal_adaptor, 'modal',
                        SimpleNamespace(Sandbox=FakeSandbox))

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
                'IdleTimeout': None,
                'DockerImage': 'ubuntu:22.04',
            }, [8080, 8081]))

    assert record.provider_name == 'modal'
    assert record.head_instance_id == 'sb-created'
    assert record.created_instance_ids == ['sb-created']
    assert created['args'][:2] == ('bash', '-lc')
    assert created['kwargs']['name'] == 'test-on-cloud'
    assert created['kwargs']['unencrypted_ports'] == [22]
    assert created['kwargs']['encrypted_ports'] == [8080, 8081]
    assert created['kwargs']['region'] is None
    assert created['kwargs']['gpu'] == 'H100'
    assert image_calls == ['ubuntu:22.04']


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
    assert calls == [(('from_registry', 'ubuntu:22.04', '3.12'),
                      ('openssh-server', 'sudo', 'rsync', 'curl', 'procps',
                       'patch', 'lsof'))]


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


def test_modal_query_ports(monkeypatch):

    class FakeTunnel:

        def __init__(self, url=None, tcp_socket=None):
            self.url = url
            self.tcp_socket = tcp_socket

    class FakeSandbox:

        def tunnels(self, timeout):
            del timeout  # unused
            return {
                22: FakeTunnel(tcp_socket=('ssh.modal.host', 2222)),
                8080: FakeTunnel(url='https://web.modal.host'),
                9000: FakeTunnel(url='https://api.modal.host:8443/path'),
            }

    monkeypatch.setattr(modal_instance.modal_utils, 'get_head_sandbox',
                        lambda cluster_name: FakeSandbox())

    endpoints = modal_instance.query_ports('test-on-cloud',
                                           ['22', '8080', '9000'])

    assert endpoints[22][0] == common.SocketEndpoint(host='ssh.modal.host',
                                                     port=2222)
    assert endpoints[8080][0] == common.HTTPSEndpoint(host='web.modal.host',
                                                      port=None,
                                                      path='')
    assert endpoints[9000][0] == common.HTTPSEndpoint(host='api.modal.host',
                                                      port=8443,
                                                      path='path')


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


def test_modal_query_instances_can_include_terminated(monkeypatch):
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

    statuses = modal_instance.query_instances('test',
                                              'test-on-cloud',
                                              non_terminated_only=False)

    assert statuses == {
        'sb-running': (status_lib.ClusterStatus.UP, None),
        'sb-terminated': (None, None),
    }


def test_modal_terminate_instances_idempotent(monkeypatch):
    terminated = []

    class FakeSandbox:

        def terminate(self, wait):
            terminated.append(wait)

    sandboxes_by_call = [
        {
            'sb-existing': FakeSandbox()
        },
        {},
    ]

    def fake_get_active_sandboxes_by_name(name):
        del name  # unused
        return sandboxes_by_call.pop(0)

    monkeypatch.setattr(modal_instance.modal_utils,
                        'get_active_sandboxes_by_name',
                        fake_get_active_sandboxes_by_name)

    modal_instance.terminate_instances('test-on-cloud')
    modal_instance.terminate_instances('test-on-cloud')

    assert terminated == [True]
