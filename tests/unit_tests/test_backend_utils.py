import os
import pathlib
from unittest import mock

import grpc
import pytest

from sky import backends
from sky import check as sky_check
from sky import clouds
from sky import dag as dag_lib
from sky import exceptions
from sky import global_user_state
from sky import skypilot_config
from sky import task as task_lib
from sky.backends import backend_utils
from sky.clouds.cloud import TeardownExecutionStrategy
from sky.data import storage as storage_lib
from sky.exceptions import ClusterNotUpError
from sky.resources import Resources
from sky.utils import common
from sky.utils import common_utils
from sky.utils import controller_utils
from sky.utils import registry
from sky.utils import schemas
from sky.utils import status_lib
from sky.utils import yaml_utils


class _DeadlineExceededRpcError(grpc.RpcError):
    """Minimal deadline error used to verify retry classification."""

    def code(self):
        return grpc.StatusCode.DEADLINE_EXCEEDED

    def details(self):
        return 'Deadline Exceeded'


def test_deadline_exceeded_is_not_converted_to_skylet_fallback():
    """Keep ambiguous deadlines raw so state-changing RPCs are not replayed."""
    error = _DeadlineExceededRpcError()

    with pytest.raises(grpc.RpcError) as raised:
        backend_utils._handle_grpc_error(error, current_backoff=0)

    assert raised.value is error


def _write_minimal_cluster_yaml(*args, **kwargs):
    output_path = pathlib.Path(kwargs['output_path'])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text('cluster_name: display\n', encoding='utf-8')


def _mock_credential_file_mounts(monkeypatch):
    credential_clouds = [
        (clouds.AWS(), {
            '~/.aws/credentials': '/credentials/aws'
        }),
        (clouds.GCP(), {
            '~/.config/gcloud': '/credentials/gcp'
        }),
        (clouds.Vast(), {
            '~/.config/vastai/vast_api_key': '/credentials/vast'
        }),
        (clouds.RunPod(), {
            '~/.runpod/config.toml': '/credentials/runpod'
        }),
    ]
    for cloud, file_mounts in credential_clouds:
        monkeypatch.setattr(cloud,
                            'get_credential_file_mounts',
                            lambda mounts=file_mounts: mounts)
    monkeypatch.setattr(registry.CLOUD_REGISTRY, 'values',
                        lambda: [cloud for cloud, _ in credential_clouds])
    monkeypatch.setattr(sky_check.cloudflare, 'check_storage_credentials',
                        lambda: (False, ''))
    monkeypatch.setattr(sky_check.coreweave, 'check_storage_credentials',
                        lambda: (False, ''))
    monkeypatch.setattr(sky_check.vastdata, 'check_storage_credentials', lambda:
                        (False, ''))
    monkeypatch.setattr(sky_check.huggingface, 'check_storage_credentials',
                        lambda: (False, ''))
    monkeypatch.setattr(sky_check.os.path, 'exists', lambda _: True)
    monkeypatch.setattr(sky_check.os.path, 'expanduser', lambda path: path)
    monkeypatch.setattr(sky_check.os.path, 'realpath', lambda path: path)
    return {str(cloud).lower(): cloud for cloud, _ in credential_clouds}


def test_runpod_no_upload_task_mounts_no_provider_credentials(monkeypatch):
    credential_clouds = _mock_credential_file_mounts(monkeypatch)
    task = task_lib.Task()

    allowed_clouds = backend_utils._get_credential_provider_allowlist(
        task=task,
        compute_cloud=credential_clouds['runpod'],
        remote_identity=schemas.RemoteIdentityOptions.NO_UPLOAD.value,
        cluster_name='ordinary-cluster')
    credentials = sky_check.get_cloud_credential_file_mounts(
        excluded_clouds=None, allowed_clouds=allowed_clouds)

    assert credentials == {}


def test_persist_redacted_debug_yaml_is_private_and_removes_raw(tmp_path):
    raw_yaml_path = tmp_path / 'cluster.yml.tmp'
    debug_yaml_path = tmp_path / 'cluster.yml.debug'
    raw_yaml_path.write_text(
        'provider:\n'
        '  registry:\n'
        '    password: registry-password\n'
        '    headers:\n'
        '      Authorization: Bearer provider-token\n'
        'secrets:\n'
        '  secrets:workspace.REFERENCED_SECRET: null\n',
        encoding='utf-8')
    os.chmod(raw_yaml_path, 0o600)

    backend_utils._persist_redacted_debug_yaml(str(raw_yaml_path),
                                               str(debug_yaml_path))

    assert not raw_yaml_path.exists()
    assert (debug_yaml_path.stat().st_mode & 0o777) == 0o600
    debug_yaml = debug_yaml_path.read_text(encoding='utf-8')
    assert 'registry-password' not in debug_yaml
    assert 'provider-token' not in debug_yaml
    assert 'secrets:workspace.REFERENCED_SECRET' in debug_yaml


def test_persist_redacted_debug_yaml_removes_stale_artifact_on_failure(
        monkeypatch, tmp_path):
    raw_yaml_path = tmp_path / 'cluster.yml.tmp'
    debug_yaml_path = tmp_path / 'cluster.yml.debug'
    raw_yaml_path.write_text('raw-secret', encoding='utf-8')
    debug_yaml_path.write_text('stale-raw-secret', encoding='utf-8')
    monkeypatch.setattr(backend_utils.debug_dump_helpers, 'redact_task_yaml',
                        mock.Mock(side_effect=ValueError('redaction failed')))

    with pytest.raises(ValueError, match='redaction failed'):
        backend_utils._persist_redacted_debug_yaml(str(raw_yaml_path),
                                                   str(debug_yaml_path))

    assert not raw_yaml_path.exists()
    assert not debug_yaml_path.exists()


def test_persist_redacted_debug_yaml_keeps_completed_redacted_artifact(
        monkeypatch, tmp_path):
    raw_yaml_path = tmp_path / 'cluster.yml.tmp'
    debug_yaml_path = tmp_path / 'cluster.yml.debug'
    raw_yaml_path.write_text('password: raw-secret', encoding='utf-8')
    original_remove = backend_utils.os.remove

    def fail_removing_raw_yaml(path):
        if path == str(raw_yaml_path):
            raise PermissionError('cannot remove raw YAML')
        original_remove(path)

    monkeypatch.setattr(backend_utils.os, 'remove', fail_removing_raw_yaml)

    with pytest.raises(PermissionError, match='cannot remove raw YAML'):
        backend_utils._persist_redacted_debug_yaml(str(raw_yaml_path),
                                                   str(debug_yaml_path))

    assert debug_yaml_path.exists()
    assert 'raw-secret' not in debug_yaml_path.read_text(encoding='utf-8')


def test_write_cluster_config_removes_stale_debug_yaml_when_debug_disabled(
        monkeypatch, tmp_path):
    cloud = clouds.AWS()
    resource = Resources(cloud=cloud, instance_type='fake-type')
    monkeypatch.setattr(
        resource, 'make_deploy_variables', lambda *args, **kwargs: {
            'instance_type': 'fake-type',
            'custom_resources': '{}',
            'region': 'fake-region',
            'zones': 'fake-zone',
            'image_id': 'fake-image',
            'security_group': 'fake-security-group',
            'security_group_managed_by_skypilot': 'true',
        })
    monkeypatch.setattr(backend_utils.auth_utils, 'get_or_generate_keys',
                        lambda: ('/tmp/fake-key', '/tmp/fake-key.pub'))
    yaml_path = tmp_path / 'cluster.yml'
    debug_yaml_path = pathlib.Path(str(yaml_path) + '.debug')
    debug_yaml_path.write_text('raw-secret', encoding='utf-8')
    monkeypatch.setattr(backend_utils, '_get_yaml_path_from_cluster_name',
                        lambda _: str(yaml_path))
    monkeypatch.setattr(backend_utils, '_add_auth_to_cluster_config',
                        lambda *args: None)
    monkeypatch.setattr(backend_utils, '_deterministic_cluster_yaml_hash',
                        lambda _: 'fake-hash')
    monkeypatch.setattr(backend_utils, '_optimize_file_mounts', lambda _: None)
    monkeypatch.setattr(common_utils, 'fill_template',
                        _write_minimal_cluster_yaml)
    monkeypatch.setattr(sky_check, 'get_cloud_credential_file_mounts',
                        lambda **kwargs: {})
    monkeypatch.setattr(backend_utils.global_user_state, 'get_cluster_yaml_str',
                        lambda _: None)
    monkeypatch.setattr(backend_utils.global_user_state, 'set_cluster_yaml',
                        lambda *args: None)
    monkeypatch.setattr(backend_utils.usage_lib.messages.usage,
                        'update_ray_yaml', lambda _: None)
    monkeypatch.setattr(backend_utils.sky_logging, 'logging_enabled',
                        lambda *args: False)

    backend_utils.write_cluster_config(
        to_provision=resource,
        num_nodes=1,
        cluster_config_template='aws-ray.yml.j2',
        cluster_name='legacy-debug-cleanup',
        local_wheel_path=pathlib.Path('/tmp/fake'),
        wheel_hash='fake-hash',
        region=clouds.Region(name='fake-region'),
        zones=[clouds.Zone(name='fake-zone')],
        dryrun=False)

    assert not pathlib.Path(str(yaml_path) + '.tmp').exists()
    assert not debug_yaml_path.exists()


def test_runpod_no_upload_s3_file_mount_includes_only_aws_credentials(
        monkeypatch):
    credential_clouds = _mock_credential_file_mounts(monkeypatch)
    task = task_lib.Task(file_mounts={'/dataset': 's3://datasets/train'})
    original_file_mounts = dict(task.file_mounts)
    original_storage_mounts = dict(task.storage_mounts)

    allowed_clouds = backend_utils._get_credential_provider_allowlist(
        task=task,
        compute_cloud=credential_clouds['runpod'],
        remote_identity=schemas.RemoteIdentityOptions.NO_UPLOAD.value,
        cluster_name='ordinary-cluster')
    credentials = sky_check.get_cloud_credential_file_mounts(
        excluded_clouds=None, allowed_clouds=allowed_clouds)

    assert credentials == {'~/.aws/credentials': '/credentials/aws'}
    assert task.file_mounts == original_file_mounts
    assert task.storage_mounts == original_storage_mounts


def test_runpod_no_upload_gcs_file_mount_includes_only_gcp_credentials(
        monkeypatch):
    credential_clouds = _mock_credential_file_mounts(monkeypatch)
    task = task_lib.Task(file_mounts={'/dataset': 'gs://datasets/train'})
    original_file_mounts = dict(task.file_mounts)
    original_storage_mounts = dict(task.storage_mounts)

    allowed_clouds = backend_utils._get_credential_provider_allowlist(
        task=task,
        compute_cloud=credential_clouds['runpod'],
        remote_identity=schemas.RemoteIdentityOptions.NO_UPLOAD.value,
        cluster_name='ordinary-cluster')
    credentials = sky_check.get_cloud_credential_file_mounts(
        excluded_clouds=None, allowed_clouds=allowed_clouds)

    assert credentials == {'~/.config/gcloud': '/credentials/gcp'}
    assert task.file_mounts == original_file_mounts
    assert task.storage_mounts == original_storage_mounts


@pytest.mark.parametrize('source', [
    'unknown://datasets/train',
    '/local/datasets/train',
])
def test_runpod_no_upload_unknown_file_mount_url_mounts_no_credentials(
        monkeypatch, source):
    credential_clouds = _mock_credential_file_mounts(monkeypatch)
    task = task_lib.Task(file_mounts={'/dataset': source})
    original_file_mounts = dict(task.file_mounts)
    original_storage_mounts = dict(task.storage_mounts)

    allowed_clouds = backend_utils._get_credential_provider_allowlist(
        task=task,
        compute_cloud=credential_clouds['runpod'],
        remote_identity=schemas.RemoteIdentityOptions.NO_UPLOAD.value,
        cluster_name='ordinary-cluster')
    credentials = sky_check.get_cloud_credential_file_mounts(
        excluded_clouds=None, allowed_clouds=allowed_clouds)

    assert credentials == {}
    assert task.file_mounts == original_file_mounts
    assert task.storage_mounts == original_storage_mounts


def test_credential_allowlist_mounts_selected_compute_and_storage(monkeypatch):
    credential_clouds = _mock_credential_file_mounts(monkeypatch)
    task = task_lib.Task(
        storage_mounts={
            '/datasets': storage_lib.Storage(
                name='datasets', stores=[storage_lib.StoreType.GCS]),
        })

    allowed_clouds = backend_utils._get_credential_provider_allowlist(
        task=task,
        compute_cloud=credential_clouds['aws'],
        remote_identity=schemas.RemoteIdentityOptions.LOCAL_CREDENTIALS.value,
        cluster_name='ordinary-cluster')
    credentials = sky_check.get_cloud_credential_file_mounts(
        excluded_clouds=None, allowed_clouds=allowed_clouds)

    assert credentials == {
        '~/.aws/credentials': '/credentials/aws',
        '~/.config/gcloud': '/credentials/gcp',
    }


def test_credential_allowlist_infers_storage_provider_from_source_url():
    task = task_lib.Task()
    task.storage_mounts = {
        '/datasets': mock.Mock(stores={}, source='gs://datasets/train'),
    }

    allowed_clouds = backend_utils._get_credential_provider_allowlist(
        task=task,
        compute_cloud=clouds.AWS(),
        remote_identity=schemas.RemoteIdentityOptions.SERVICE_ACCOUNT.value,
        cluster_name='ordinary-cluster')

    assert clouds.cloud_in_iterable(clouds.GCP(), allowed_clouds)
    assert not clouds.cloud_in_iterable(clouds.AWS(), allowed_clouds)


@pytest.mark.parametrize('remote_identity', [
    schemas.RemoteIdentityOptions.SERVICE_ACCOUNT.value,
    'custom-service-account',
])
def test_nonlocal_compute_identity_does_not_regain_unrelated_credentials(
        monkeypatch, remote_identity):
    credential_clouds = _mock_credential_file_mounts(monkeypatch)
    task = task_lib.Task(
        storage_mounts={
            '/datasets': storage_lib.Storage(
                name='datasets', stores=[storage_lib.StoreType.GCS]),
        })

    allowed_clouds = backend_utils._get_credential_provider_allowlist(
        task=task,
        compute_cloud=credential_clouds['aws'],
        remote_identity=remote_identity,
        cluster_name='ordinary-cluster')
    credentials = sky_check.get_cloud_credential_file_mounts(
        excluded_clouds=None, allowed_clouds=allowed_clouds)

    assert credentials == {'~/.config/gcloud': '/credentials/gcp'}


def test_controller_task_mounts_workload_provider_credentials(monkeypatch):
    credential_clouds = _mock_credential_file_mounts(monkeypatch)
    controller_task = task_lib.Task()
    workload_task = task_lib.Task(
        resources=[Resources(cloud=credential_clouds['vast'])])
    controller_task.managed_job_dag = dag_lib.Dag()
    controller_task.managed_job_dag.add(workload_task)

    allowed_clouds = backend_utils._get_credential_provider_allowlist(
        task=controller_task,
        compute_cloud=credential_clouds['runpod'],
        remote_identity=schemas.RemoteIdentityOptions.LOCAL_CREDENTIALS.value,
        cluster_name='ordinary-cluster')
    credentials = sky_check.get_cloud_credential_file_mounts(
        excluded_clouds=None, allowed_clouds=allowed_clouds)

    assert credentials == {
        '~/.config/vastai/vast_api_key': '/credentials/vast',
        '~/.runpod/config.toml': '/credentials/runpod',
    }


def test_controller_task_mounts_optimized_workload_provider_credentials(
        monkeypatch):
    credential_clouds = _mock_credential_file_mounts(monkeypatch)
    controller_task = task_lib.Task()
    aws_workload_task = task_lib.Task()
    aws_workload_task.best_resources = Resources(cloud=credential_clouds['aws'])
    vast_workload_task = task_lib.Task()
    vast_workload_task.best_resources = Resources(
        cloud=credential_clouds['vast'])
    controller_task.managed_job_dag = dag_lib.Dag()
    controller_task.managed_job_dag.add(aws_workload_task)
    controller_task.managed_job_dag.add(vast_workload_task)

    allowed_clouds = backend_utils._get_credential_provider_allowlist(
        task=controller_task,
        compute_cloud=credential_clouds['runpod'],
        remote_identity=schemas.RemoteIdentityOptions.NO_UPLOAD.value,
        cluster_name='ordinary-cluster')
    credentials = sky_check.get_cloud_credential_file_mounts(
        excluded_clouds=None, allowed_clouds=allowed_clouds)

    assert credentials == {
        '~/.aws/credentials': '/credentials/aws',
        '~/.config/vastai/vast_api_key': '/credentials/vast',
    }


def test_empty_credential_allowlist_fails_closed(monkeypatch):
    _mock_credential_file_mounts(monkeypatch)

    credentials = sky_check.get_cloud_credential_file_mounts(
        excluded_clouds=None, allowed_clouds=())

    assert credentials == {}


def test_credential_mounts_keep_logging_agent_credentials(monkeypatch):
    logging_agent = mock.Mock()
    logging_agent.get_credential_file_mounts.return_value = {
        '~/.logging-agent/config': '/credentials/logging-agent',
    }
    monkeypatch.setattr(
        sky_check, 'get_cloud_credential_file_mounts',
        lambda excluded_clouds, allowed_clouds: {
            '~/.aws/credentials': '/credentials/aws',
        })
    monkeypatch.setattr(backend_utils.logs, 'get_logging_agent',
                        lambda: logging_agent)

    credentials = backend_utils._get_credential_file_mounts(
        task=task_lib.Task(),
        compute_cloud=clouds.AWS(),
        remote_identity=schemas.RemoteIdentityOptions.LOCAL_CREDENTIALS.value,
        cluster_name='ordinary-cluster',
        region='us-east-1')

    assert credentials == {
        '~/.aws/credentials': '/credentials/aws',
        '~/.logging-agent/config': '/credentials/logging-agent',
    }


@pytest.mark.parametrize('cluster_name, expected_kubeconfig_exclusion', [
    (controller_utils.Controllers.JOBS_CONTROLLER.value.cluster_name, False),
    ('ordinary-cluster', True),
])
def test_credential_mounts_scope_controller_discovery_and_kubeconfig(
        monkeypatch, cluster_name, expected_kubeconfig_exclusion):
    """Scope provider credentials while preserving controller kubeconfig access."""
    captured_arguments = {}

    def capture_credential_mounts(excluded_clouds, allowed_clouds):
        captured_arguments['excluded_clouds'] = excluded_clouds
        captured_arguments['allowed_clouds'] = allowed_clouds
        return {}

    monkeypatch.setattr(sky_check, 'get_cloud_credential_file_mounts',
                        capture_credential_mounts)
    monkeypatch.setattr(registry.CLOUD_REGISTRY, 'items', lambda: [
        ('gcp', clouds.GCP()),
    ])
    monkeypatch.setattr(
        skypilot_config, 'get_effective_workspace_region_config',
        lambda cloud, **kwargs: (schemas.RemoteIdentityOptions.NO_UPLOAD.value
                                 if cloud == 'gcp' else None))
    monkeypatch.setattr(skypilot_config, 'get_workspace_cloud',
                        lambda _: {'allowed_contexts': ['production']})
    monkeypatch.setattr(backend_utils.logs, 'get_logging_agent', lambda: None)

    backend_utils._get_credential_file_mounts(
        task=task_lib.Task(),
        compute_cloud=clouds.AWS(),
        remote_identity=schemas.RemoteIdentityOptions.LOCAL_CREDENTIALS.value,
        cluster_name=cluster_name,
        region='us-east-1')

    if expected_kubeconfig_exclusion:
        assert captured_arguments['allowed_clouds'] is not None
    else:
        assert captured_arguments['allowed_clouds'] is None
    assert clouds.cloud_in_iterable(clouds.GCP(),
                                    captured_arguments['excluded_clouds'])
    assert (clouds.cloud_in_iterable(
        clouds.Kubernetes(),
        captured_arguments['excluded_clouds']) == expected_kubeconfig_exclusion)
    assert (clouds.cloud_in_iterable(
        clouds.SSH(),
        captured_arguments['excluded_clouds']) == expected_kubeconfig_exclusion)


def test_controller_credential_excludelist_honors_profile_override(monkeypatch):
    """Use cluster-specific remote-identity overrides for controller mounts."""
    override_configs = {'remote_identity': 'override'}
    controller_name = controller_utils.Controllers.JOBS_CONTROLLER.value.cluster_name
    monkeypatch.setattr(registry.CLOUD_REGISTRY, 'items', lambda: [
        ('gcp', clouds.GCP()),
    ])
    monkeypatch.setattr(
        skypilot_config, 'get_effective_workspace_region_config',
        lambda cloud, **kwargs: ([{
            controller_name: schemas.RemoteIdentityOptions.NO_UPLOAD.value
        }] if cloud == 'gcp' and kwargs['override_configs'] == override_configs
                                 else None))
    monkeypatch.setattr(skypilot_config, 'get_workspace_cloud',
                        lambda _: {'allowed_contexts': ['production']})

    excluded_clouds = backend_utils._get_credential_provider_excludelist(
        controller_name, 'us-east-1', override_configs)

    assert clouds.cloud_in_iterable(clouds.GCP(), excluded_clouds)


# Set env var to test config file.
@mock.patch.object(skypilot_config, '_global_config_context',
                   skypilot_config.ConfigContext())
@mock.patch('sky.catalog.instance_type_exists', return_value=True)
@mock.patch('sky.catalog.get_accelerators_from_instance_type',
            return_value={'fake-acc': 2})
@mock.patch('sky.catalog.get_image_id_from_tag', return_value='fake-image')
@mock.patch.object(clouds.aws, 'DEFAULT_SECURITY_GROUP_NAME', 'fake-default-sg')
@mock.patch('sky.check.get_cloud_credential_file_mounts',
            return_value='~/.aws/credentials')
@mock.patch('sky.catalog.get_arch_from_instance_type', return_value='fake-arch')
@mock.patch('sky.backends.backend_utils._get_yaml_path_from_cluster_name',
            return_value='/tmp/fake/path')
@mock.patch('sky.backends.backend_utils._deterministic_cluster_yaml_hash',
            return_value='fake-hash')
@mock.patch('sky.utils.common_utils.fill_template')
def test_write_cluster_config_w_remote_identity(mock_fill_template,
                                                *mocks) -> None:
    os.environ[
        skypilot_config.
        ENV_VAR_SKYPILOT_CONFIG] = './tests/test_yamls/test_aws_config.yaml'
    skypilot_config.reload_config()

    cloud = clouds.AWS()

    region = clouds.Region(name='fake-region')
    zones = [clouds.Zone(name='fake-zone')]
    resource = Resources(cloud=cloud, instance_type='fake-type: 3')

    cluster_config_template = 'aws-ray.yml.j2'
    mock_fill_template.side_effect = _write_minimal_cluster_yaml

    # test default
    backend_utils.write_cluster_config(
        to_provision=resource,
        num_nodes=2,
        cluster_config_template=cluster_config_template,
        cluster_name="display",
        local_wheel_path=pathlib.Path('/tmp/fake'),
        wheel_hash='b1bd84059bc0342f7843fcbe04ab563e',
        region=region,
        zones=zones,
        dryrun=True,
        keep_launch_fields_in_existing_config=True)

    expected_subset = {
        'instance_type': 'fake-type: 3',
        'custom_resources': '{"fake-acc":2}',
        'region': 'fake-region',
        'zones': 'fake-zone',
        'image_id': 'fake-image',
        'security_group': 'fake-default-sg',
        'security_group_managed_by_skypilot': 'true',
        'vpc_name': 'fake-vpc',
        'remote_identity': 'LOCAL_CREDENTIALS',  # remote identity
        'sky_local_path': '/tmp/fake',
        'sky_wheel_hash': 'b1bd84059bc0342f7843fcbe04ab563e',
    }

    mock_fill_template.assert_called_once()
    assert mock_fill_template.call_args[0][
        0] == cluster_config_template, "config template incorrect"
    assert mock_fill_template.call_args[0][1].items() >= expected_subset.items(
    ), "config fill values incorrect"

    # test using cluster matches regex, top
    mock_fill_template.reset_mock()
    expected_subset.update({
        'security_group': 'fake-1-sg',
        'security_group_managed_by_skypilot': 'false',
        'remote_identity': 'fake1-skypilot-role'
    })
    backend_utils.write_cluster_config(
        to_provision=resource,
        num_nodes=2,
        cluster_config_template=cluster_config_template,
        cluster_name="sky-serve-fake1-1234",
        local_wheel_path=pathlib.Path('/tmp/fake'),
        wheel_hash='b1bd84059bc0342f7843fcbe04ab563e',
        region=region,
        zones=zones,
        dryrun=True,
        keep_launch_fields_in_existing_config=True)

    mock_fill_template.assert_called_once()
    assert (mock_fill_template.call_args[0][0] == cluster_config_template,
            "config template incorrect")
    assert (mock_fill_template.call_args[0][1].items() >=
            expected_subset.items(), "config fill values incorrect")

    # test using cluster matches regex, middle
    mock_fill_template.reset_mock()
    expected_subset.update({
        'security_group': 'fake-2-sg',
        'security_group_managed_by_skypilot': 'false',
        'remote_identity': 'fake2-skypilot-role'
    })
    backend_utils.write_cluster_config(
        to_provision=resource,
        num_nodes=2,
        cluster_config_template=cluster_config_template,
        cluster_name="sky-serve-fake2-1234",
        local_wheel_path=pathlib.Path('/tmp/fake'),
        wheel_hash='b1bd84059bc0342f7843fcbe04ab563e',
        region=region,
        zones=zones,
        dryrun=True,
        keep_launch_fields_in_existing_config=True)

    mock_fill_template.assert_called_once()
    assert (mock_fill_template.call_args[0][0] == cluster_config_template,
            "config template incorrect")
    assert (mock_fill_template.call_args[0][1].items() >=
            expected_subset.items(), "config fill values incorrect")


@mock.patch.object(skypilot_config, '_global_config_context',
                   skypilot_config.ConfigContext())
def test_write_cluster_config_scopes_storage_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv(skypilot_config.ENV_VAR_SKYPILOT_CONFIG, raising=False)
    skypilot_config.reload_config()

    cloud = clouds.AWS()
    resource = Resources(cloud=cloud, instance_type='fake-type')
    task = task_lib.Task()
    task.storage_mounts = {
        '/datasets': mock.Mock(stores={storage_lib.StoreType.GCS: None},
                               source=None),
    }
    monkeypatch.setattr(
        resource, 'make_deploy_variables', lambda *args, **kwargs: {
            'instance_type': 'fake-type',
            'custom_resources': '{}',
            'region': 'fake-region',
            'zones': 'fake-zone',
            'image_id': 'fake-image',
            'security_group': 'fake-security-group',
            'security_group_managed_by_skypilot': 'true',
        })
    monkeypatch.setattr(backend_utils.auth_utils, 'get_or_generate_keys',
                        lambda: ('/tmp/fake-key', '/tmp/fake-key.pub'))
    yaml_path = tmp_path / 'fake-path'
    monkeypatch.setattr(backend_utils, '_get_yaml_path_from_cluster_name',
                        lambda _: str(yaml_path))
    monkeypatch.setattr(backend_utils, '_deterministic_cluster_yaml_hash',
                        lambda _: 'fake-hash')
    monkeypatch.setattr(common_utils, 'fill_template',
                        _write_minimal_cluster_yaml)
    credential_file_mounts = mock.Mock(return_value={})
    monkeypatch.setattr(sky_check, 'get_cloud_credential_file_mounts',
                        credential_file_mounts)

    backend_utils.write_cluster_config(
        to_provision=resource,
        num_nodes=1,
        cluster_config_template='aws-ray.yml.j2',
        cluster_name='credential-scope',
        local_wheel_path=pathlib.Path('/tmp/fake'),
        wheel_hash='fake-hash',
        region=clouds.Region(name='fake-region'),
        zones=[clouds.Zone(name='fake-zone')],
        dryrun=True,
        task=task)

    allowed_clouds = credential_file_mounts.call_args.kwargs['allowed_clouds']
    assert clouds.cloud_in_iterable(clouds.AWS(), allowed_clouds)
    assert clouds.cloud_in_iterable(clouds.GCP(), allowed_clouds)
    assert len(allowed_clouds) == 2
    assert ((yaml_path.with_name(yaml_path.name + '.tmp').stat().st_mode &
             0o777) == 0o600)


@mock.patch.object(skypilot_config, '_global_config_context',
                   skypilot_config.ConfigContext())
def test_write_cluster_config_defaults_runpod_to_server_backed_teardown(
        monkeypatch, tmp_path):
    cloud = clouds.RunPod()
    resource = Resources(cloud=cloud, instance_type='fake-type')
    monkeypatch.setattr(
        resource, 'make_deploy_variables', lambda *args, **kwargs: {
            'instance_type': 'fake-type',
            'custom_resources': '{}',
            'region': 'fake-region',
            'zones': None,
            'image_id': 'fake-image',
        })
    credential_file_mounts = mock.Mock(return_value={})
    monkeypatch.setattr(backend_utils, '_get_credential_file_mounts',
                        credential_file_mounts)
    monkeypatch.setattr(backend_utils.auth_utils, 'get_or_generate_keys',
                        lambda: ('/tmp/fake-key', '/tmp/fake-key.pub'))
    yaml_path = tmp_path / 'fake-path'
    monkeypatch.setattr(backend_utils, '_get_yaml_path_from_cluster_name',
                        lambda _: str(yaml_path))
    monkeypatch.setattr(backend_utils, '_deterministic_cluster_yaml_hash',
                        lambda _: 'fake-hash')
    monkeypatch.setattr(common_utils, 'fill_template',
                        _write_minimal_cluster_yaml)

    config = backend_utils.write_cluster_config(
        to_provision=resource,
        num_nodes=1,
        cluster_config_template='runpod-ray.yml.j2',
        cluster_name='strategy-capture',
        local_wheel_path=pathlib.Path('/tmp/fake'),
        wheel_hash='fake-hash',
        region=clouds.Region(name='fake-region'),
        dryrun=True)

    assert config['teardown_execution_strategy'] == (
        TeardownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK.value)
    assert credential_file_mounts.call_args.args[2] == (
        schemas.RemoteIdentityOptions.NO_UPLOAD.value)


@mock.patch.object(skypilot_config, '_global_config_context',
                   skypilot_config.ConfigContext())
@mock.patch('sky.catalog.instance_type_exists', return_value=True)
@mock.patch('sky.catalog.get_accelerators_from_instance_type',
            return_value={'fake-acc': 2})
@mock.patch('sky.catalog.get_image_id_from_tag', return_value='fake-image')
@mock.patch('sky.catalog.get_arch_from_instance_type', return_value='fake-arch')
@mock.patch('sky.backends.backend_utils._get_yaml_path_from_cluster_name',
            return_value='/tmp/fake/path')
@mock.patch('sky.utils.common_utils.fill_template')
def test_write_cluster_config_w_post_provision_runcmd_aws(
        mock_fill_template, *mocks):
    os.environ[
        skypilot_config.
        ENV_VAR_SKYPILOT_CONFIG] = './tests/test_yamls/test_aws_config_runcmd.yaml'
    skypilot_config.reload_config()

    cloud = clouds.AWS()
    region = clouds.Region(name='fake-region')
    zones = [clouds.Zone(name='fake-zone')]
    resource = Resources(cloud=cloud, instance_type='fake-type: 3')
    cluster_config_template = 'aws-ray.yml.j2'
    mock_fill_template.side_effect = _write_minimal_cluster_yaml

    backend_utils.write_cluster_config(
        to_provision=resource,
        num_nodes=2,
        cluster_config_template=cluster_config_template,
        cluster_name="display",
        local_wheel_path=pathlib.Path('/tmp/fake'),
        wheel_hash='b1bd84059bc0342f7843fcbe04ab563e',
        region=region,
        zones=zones,
        dryrun=True,
        keep_launch_fields_in_existing_config=True)

    expected_runcmd = [
        'echo "hello world!"',
        ['ls', '-l', '/'],
    ]
    mock_fill_template.assert_called_once()
    assert mock_fill_template.call_args[0][
        0] == cluster_config_template, "config template incorrect"
    assert mock_fill_template.call_args[0][1][
        'runcmd'] == expected_runcmd, "runcmd not passed correctly"


@mock.patch.object(skypilot_config, '_global_config_context',
                   skypilot_config.ConfigContext())
@mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
            return_value=[])
@mock.patch('sky.utils.common_utils.fill_template',
            wraps=common_utils.fill_template)
def test_write_cluster_config_w_post_provision_runcmd_kubernetes(
        mock_fill_template, *mocks):
    os.environ[
        skypilot_config.
        ENV_VAR_SKYPILOT_CONFIG] = './tests/test_yamls/test_k8s_config_runcmd.yaml'
    skypilot_config.reload_config()

    cloud = clouds.Kubernetes()
    region = clouds.Region(name='fake-context')
    resource = Resources(cloud=cloud, instance_type='4CPU--16GB')
    cluster_config_template = 'kubernetes-ray.yml.j2'
    backend_utils.write_cluster_config(
        to_provision=resource,
        num_nodes=2,
        cluster_config_template=cluster_config_template,
        cluster_name="display",
        local_wheel_path=pathlib.Path('/tmp/fake'),
        wheel_hash='b1bd84059bc0342f7843fcbe04ab563e',
        region=region,
        dryrun=True,
        keep_launch_fields_in_existing_config=True)
    expected_runcmd = ['echo "hello world!"']
    mock_fill_template.assert_called_once()
    assert mock_fill_template.call_args[0][
        0] == cluster_config_template, "config template incorrect"
    assert mock_fill_template.call_args[0][1][
        'runcmd'] == expected_runcmd, "runcmd not passed correctly"


@mock.patch.object(skypilot_config, '_global_config_context',
                   skypilot_config.ConfigContext())
def test_aws_template_applies_labels_to_volume_tags() -> None:
    template_path = pathlib.Path('sky/templates/aws-ray.yml.j2')
    template = template_path.read_text(encoding='utf-8')

    expected_block = """        - ResourceType: volume
          Tags:
            - Key: skypilot-user
              Value: {{ user }}
            {%- for label_key, label_value in labels.items() %}
            - Key: {{ label_key }}
              Value: {{ label_value|tojson }}
            {%- endfor %}"""

    assert expected_block in template


def test_get_clusters_launch_refresh(monkeypatch):
    # verifies that `get_clusters` works when one cluster is launching
    # and other is not.
    # https://github.com/skypilot-org/skypilot/pull/7624

    def _mock_cluster(launch, postfix=''):
        cluster_name = 'launch-cluster' if launch else 'up-cluster'
        cluster_name += postfix
        handle = mock.MagicMock()
        handle.cluster_name_on_cloud = f'{cluster_name}-cloud'
        handle.launched_nodes = 1
        handle.launched_resources = None

        if launch:
            status = status_lib.ClusterStatus.INIT
        else:
            status = status_lib.ClusterStatus.UP

        return {
            'name': cluster_name,
            'launched_at': '0',
            'handle': handle,
            'last_use': 'sky launch',
            'status': status,
            'autostop': 0,
            'to_down': False,
            'cluster_hash': '00000',
            'cluster_ever_up': not launch,
            'status_updated_at': 0,
            'user_hash': '00000',
            'user_name': 'pilot',
            'workspace': 'default',
            'is_managed': False,
            'nodes': 0,
        }

    def get_clusters_mock(*args, **kwargs):
        return [
            _mock_cluster(False),
            _mock_cluster(True),
            _mock_cluster(True, 'None')
        ]

    def get_readable_resources_repr(handle, simplified_only):
        return ('', None) if simplified_only else ('', '')

    def ssh_credentials_from_handles(handles):
        return []

    def refresh_cluster(cluster_name, force_refresh_statuses, include_user_info,
                        summary_response):
        if cluster_name == 'up-cluster':
            return _mock_cluster(False)
        elif cluster_name == 'launch-cluster':
            return _mock_cluster(True)
        else:
            return None

    def get_request_tasks(*args, **kwargs):
        magic_mock = mock.MagicMock()
        magic_mock.cluster_name = 'launch-cluster'
        return [magic_mock]

    monkeypatch.setattr('sky.global_user_state.get_clusters', get_clusters_mock)
    monkeypatch.setattr('sky.utils.resources_utils.get_readable_resources_repr',
                        get_readable_resources_repr)
    monkeypatch.setattr(
        'sky.backends.backend_utils.ssh_credentials_from_handles',
        ssh_credentials_from_handles)
    monkeypatch.setattr('sky.backends.backend_utils._refresh_cluster',
                        refresh_cluster)
    monkeypatch.setattr('sky.server.requests.requests.get_request_tasks',
                        get_request_tasks)

    assert len(
        backend_utils.get_clusters(refresh=common.StatusRefreshMode.FORCE)) == 2


def test_update_records_with_autodown_intents_is_hash_fenced(monkeypatch):
    intent = global_user_state.AutodownIntent(
        cluster_name='current',
        cluster_hash='current-hash',
        generation=4,
        state=global_user_state.AutodownIntentState.RETRY_WAIT,
        idle_minutes=5,
        to_down=True,
        execution_strategy=(
            TeardownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK.value),
        user_hash='user-hash',
        workspace='default',
        attempt_count=2,
        next_retry_at=123,
        last_error='Autodown reconciliation failed.',
        created_at=1,
        updated_at=2,
    )
    get_intents = mock.Mock(return_value={
        'current': intent,
        'replacement': intent,
    })
    monkeypatch.setattr(global_user_state, 'get_autodown_intents', get_intents)
    records = [{
        'name': 'current',
        'cluster_hash': 'current-hash',
    }, {
        'name': 'replacement',
        'cluster_hash': 'replacement-hash',
    }]

    backend_utils._update_records_with_autodown_intents(records)

    get_intents.assert_called_once_with(['current', 'replacement'])
    assert records[0]['autodown_recovery_state'] == 'RETRY_WAIT'
    assert records[0]['autodown_execution_strategy'] == (
        TeardownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK.value)
    assert records[0]['autodown_generation'] == 4
    assert records[0]['autodown_attempt_count'] == 2
    assert 'autodown_recovery_state' not in records[1]


def test_kubeconfig_upload_with_kubernetes_exclusion():
    """Tests kubeconfig upload behavior with Kubernetes/SSH cloud exclusion.

    This is a regression test for a bug where kubeconfig was uploaded even when
    `remote_identity: SERVICE_ACCOUNT` was set for a Kubernetes cluster. This
    happened because `SSH` inherits from `Kubernetes` and was not being
    explicitly excluded, causing it to upload the kubeconfig.
    """
    # Mock get_credential_file_mounts on Kubernetes to return kubeconfig.
    # SSH inherits from Kubernetes, so it will also return kubeconfig.
    kubeconfig_mounts = {'~/.kube/config': '~/.kube/config'}

    with mock.patch.object(clouds.Kubernetes,
                           'get_credential_file_mounts',
                           return_value=kubeconfig_mounts):
        # 1. Test the buggy behavior: only Kubernetes is excluded.
        # SSH is not excluded, and since it inherits from Kubernetes, it will
        # upload the kubeconfig via the (mocked) inherited method.
        excluded_clouds_buggy = {clouds.Kubernetes()}

        # Mock os.path functions for the credential collection loop
        with mock.patch('os.path.exists', return_value=True), \
             mock.patch('os.path.expanduser', side_effect=lambda x: x), \
             mock.patch('os.path.realpath', side_effect=lambda x: x):
            credentials_buggy = sky_check.get_cloud_credential_file_mounts(
                excluded_clouds_buggy)

        assert '~/.kube/config' in credentials_buggy, (
            'Kubeconfig should be uploaded when only Kubernetes is excluded. '
            'This demonstrates the buggy behavior that the fix in '
            'write_cluster_config() is meant to prevent.')

        # 2. Test the correct behavior: both Kubernetes and SSH are excluded.
        # Kubeconfig should not be in the returned credentials.
        excluded_clouds_fixed = {clouds.Kubernetes(), clouds.SSH()}

        with mock.patch('os.path.exists', return_value=True), \
             mock.patch('os.path.expanduser', side_effect=lambda x: x), \
             mock.patch('os.path.realpath', side_effect=lambda x: x):
            credentials_fixed = sky_check.get_cloud_credential_file_mounts(
                excluded_clouds_fixed)

        assert '~/.kube/config' not in credentials_fixed, (
            'Kubeconfig should not be uploaded when both Kubernetes and SSH '
            'are excluded.')


@mock.patch('sky.backends.backend_utils.get_backend_from_handle')
@mock.patch('sky.backends.backend_utils.refresh_cluster_status_handle')
def test_check_cluster_available_accepts_autostopping(mock_refresh,
                                                      mock_get_backend):
    """Verify check_cluster_available accepts AUTOSTOPPING status."""
    # Mock AUTOSTOPPING cluster
    mock_handle = mock.MagicMock()
    mock_refresh.return_value = (status_lib.ClusterStatus.AUTOSTOPPING,
                                 mock_handle)
    mock_get_backend.return_value = mock.MagicMock()

    # Should not raise ClusterNotUpError for AUTOSTOPPING
    result = backend_utils.check_cluster_available(
        'test-cluster',
        operation='test_operation',
        check_cloud_vm_ray_backend=False)
    assert result == mock_handle


@mock.patch('sky.backends.backend_utils.get_backend_from_handle')
@mock.patch('sky.backends.backend_utils.refresh_cluster_status_handle')
def test_check_cluster_available_rejects_init(mock_refresh, mock_get_backend):
    """Verify check_cluster_available rejects INIT status."""
    mock_handle = mock.MagicMock()
    mock_refresh.return_value = (status_lib.ClusterStatus.INIT, mock_handle)
    mock_get_backend.return_value = mock.MagicMock()

    # Should raise ClusterNotUpError for INIT
    try:
        backend_utils.check_cluster_available('test-cluster',
                                              operation='test_operation',
                                              check_cloud_vm_ray_backend=False)
        assert False, 'Expected ClusterNotUpError to be raised'
    except ClusterNotUpError:
        pass


def _k8s_owner_check_record(owner_identity):
    launchable = mock.MagicMock()
    launchable.cloud = clouds.Kubernetes()
    # `unsafe=True` so the `assert_launchable` attribute (which MagicMock
    # would otherwise guard as a misspelled assert method) is mockable.
    launched_resources = mock.MagicMock(unsafe=True)
    launched_resources.assert_launchable.return_value = launchable

    handle = mock.MagicMock()
    # Make `isinstance(handle, CloudVmRayResourceHandle)` pass without the
    # attribute restrictions that `spec=` imposes (launched_resources is set
    # in __init__, not on the class).
    handle.__class__ = backends.CloudVmRayResourceHandle
    handle.launched_resources = launched_resources
    return {
        'handle': handle,
        'workspace': 'default',
        'owner': owner_identity,
    }


def test_check_owner_identity_k8s_ignores_name_scope(monkeypatch):
    """A pre-scoping owner should still match the current scoped identity.

    Regression: a Kubernetes cluster whose owner was recorded before the
    kubeconfig `__sky__<context>` name-scoping convention existed must keep
    matching the current (scoped) identity instead of raising an owner
    mismatch, and the stored owner should self-heal to the scoped identity.
    """
    # Identity string shape is `<cluster>_<user>_<namespace>`. The scoped
    # variant appends `__sky__<context>` to the cluster and user names.
    old_identity = 'kube-cluster_kube-user_default'
    scoped_identity = ('kube-cluster__sky__my-context_'
                       'kube-user__sky__my-context_default')

    record = _k8s_owner_check_record([old_identity])

    # CI runs unit tests with SKYPILOT_SKIP_CLOUD_IDENTITY_CHECK=1, which would
    # short-circuit the check before our logic runs; ensure it is enabled here.
    monkeypatch.delenv('SKYPILOT_SKIP_CLOUD_IDENTITY_CHECK', raising=False)

    patched = {}

    def fake_set_owner(cluster_name, identity):
        patched['cluster_name'] = cluster_name
        patched['identity'] = identity

    monkeypatch.setattr('sky.skypilot_config.get_active_workspace',
                        lambda: 'default')
    monkeypatch.setattr('sky.global_user_state.set_owner_identity_for_cluster',
                        fake_set_owner)
    monkeypatch.setattr(clouds.Kubernetes, 'get_user_identities',
                        classmethod(lambda cls: [[scoped_identity]]))

    # Should not raise despite the stored owner predating name scoping.
    backend_utils._check_owner_identity_with_record(  # pylint: disable=protected-access
        'my-cluster', record)

    # The stale, pre-scoping owner should self-heal to the scoped identity.
    assert patched['cluster_name'] == 'my-cluster'
    assert patched['identity'] == [scoped_identity]


def test_check_owner_identity_k8s_name_scope_underscored_context(monkeypatch):
    """Scope stripping must work when the context name contains underscores.

    Default GKE contexts look like `gke_<project>_<zone>_<cluster>`, so the
    scope suffix itself carries underscores. A naive `__sky__[^_]*` strip would
    only remove up to the first underscore of the context and still report a
    mismatch. The cluster/user names here are underscore-free so the test
    isolates the underscored-context case.
    """
    ctx = 'gke_my-project_us-central1-a_my-cluster'
    old_identity = 'kube-cluster_kube-user_default'
    scoped_identity = f'kube-cluster__sky__{ctx}_kube-user__sky__{ctx}_default'

    record = _k8s_owner_check_record([old_identity])

    # CI runs unit tests with SKYPILOT_SKIP_CLOUD_IDENTITY_CHECK=1, which would
    # short-circuit the check before our logic runs; ensure it is enabled here.
    monkeypatch.delenv('SKYPILOT_SKIP_CLOUD_IDENTITY_CHECK', raising=False)

    patched = {}

    def fake_set_owner(cluster_name, identity):
        patched['cluster_name'] = cluster_name
        patched['identity'] = identity

    monkeypatch.setattr('sky.skypilot_config.get_active_workspace',
                        lambda: 'default')
    monkeypatch.setattr('sky.global_user_state.set_owner_identity_for_cluster',
                        fake_set_owner)
    monkeypatch.setattr(clouds.Kubernetes, 'get_user_identities',
                        classmethod(lambda cls: [[scoped_identity]]))

    backend_utils._check_owner_identity_with_record(  # pylint: disable=protected-access
        'my-cluster', record)

    assert patched['identity'] == [scoped_identity]


def test_check_owner_identity_k8s_scope_does_not_overmatch(monkeypatch):
    """Stripping scope suffixes must not let a different identity match."""
    owner_identity = ['ctx-a_user-a_default']
    # Different cluster/user; normalizing the scope suffix still leaves it
    # distinct from the stored owner.
    other_scoped = 'ctx-b__sky__ctx-b_user-b__sky__ctx-b_default'

    record = _k8s_owner_check_record(owner_identity)

    # CI runs unit tests with SKYPILOT_SKIP_CLOUD_IDENTITY_CHECK=1, which would
    # short-circuit the check before our logic runs; ensure it is enabled here.
    monkeypatch.delenv('SKYPILOT_SKIP_CLOUD_IDENTITY_CHECK', raising=False)

    monkeypatch.setattr('sky.skypilot_config.get_active_workspace',
                        lambda: 'default')
    monkeypatch.setattr('sky.global_user_state.set_owner_identity_for_cluster',
                        lambda *a, **k: None)
    monkeypatch.setattr(clouds.Kubernetes, 'get_user_identities',
                        classmethod(lambda cls: [[other_scoped]]))

    with pytest.raises(exceptions.ClusterOwnerIdentityMismatchError):
        backend_utils._check_owner_identity_with_record(  # pylint: disable=protected-access
            'my-cluster', record)


@mock.patch('sky.backends.backend_utils.refresh_cluster_status_handle')
def test_is_controller_accessible_accepts_autostopping(mock_refresh):
    """Verify is_controller_accessible accepts AUTOSTOPPING status."""
    from sky.utils import controller_utils

    mock_handle = mock.MagicMock()
    mock_handle.head_ip = '1.2.3.4'
    mock_refresh.return_value = (status_lib.ClusterStatus.AUTOSTOPPING,
                                 mock_handle)

    # Should not raise for AUTOSTOPPING controller
    result = backend_utils.is_controller_accessible(
        controller_utils.Controllers.JOBS_CONTROLLER,
        stopped_message='Test stopped',
        exit_if_not_accessible=False)
    assert result == mock_handle


def test_replace_yaml_dicts_restores_new_nested_field_for_legacy_cluster():
    """Restarting a cluster created before a nested provider field was added.

    Regression test for the Nebius `KeyError: 'security_group'` seen when
    restarting a STOPPED cluster after upgrading to a version that added
    `provider.security_group`. The old (stored) yaml's `provider` block is
    restored wholesale and lacks `security_group`, so reverting the
    `('provider', 'security_group', 'GroupName')` exception must not assume
    the intermediate key exists.
    """
    new_yaml = ('cluster_name: c\n'
                'provider:\n'
                '  type: external\n'
                '  region: r\n'
                '  security_group:\n'
                '    GroupName: new-name\n'
                '    ManagedBySkyPilot: true\n'
                'auth: {ssh_user: ubuntu}\n'
                'node_config: {InstanceType: t}\n')
    # Old yaml predates the security_group feature: no such key under provider.
    old_yaml = ('cluster_name: c\n'
                'provider:\n'
                '  type: external\n'
                '  region: r\n'
                'auth: {ssh_user: ubuntu}\n'
                'node_config: {InstanceType: t}\n')

    out = backend_utils._replace_yaml_dicts(
        new_yaml, old_yaml,
        backend_utils._RAY_YAML_KEYS_TO_RESTORE_FOR_BACK_COMPATIBILITY,
        backend_utils._RAY_YAML_KEYS_TO_RESTORE_EXCEPTIONS)
    result = yaml_utils.read_yaml_str(out)
    # The new GroupName is applied even though the restored provider block
    # had no security_group; no KeyError is raised.
    assert result['provider']['security_group']['GroupName'] == 'new-name'


def test_replace_yaml_dicts_preserves_old_subfield_on_restart():
    """Existing cluster restart keeps old sibling subfields, takes new GroupName."""
    new_yaml = ('cluster_name: c\n'
                'provider:\n'
                '  type: external\n'
                '  region: r\n'
                '  security_group:\n'
                '    GroupName: new-name\n'
                '    ManagedBySkyPilot: true\n'
                'auth: {ssh_user: ubuntu}\n'
                'node_config: {InstanceType: t}\n')
    old_yaml = ('cluster_name: c\n'
                'provider:\n'
                '  type: external\n'
                '  region: r\n'
                '  security_group:\n'
                '    GroupName: old-name\n'
                '    ManagedBySkyPilot: false\n'
                'auth: {ssh_user: ubuntu}\n'
                'node_config: {InstanceType: t}\n')

    out = backend_utils._replace_yaml_dicts(
        new_yaml, old_yaml,
        backend_utils._RAY_YAML_KEYS_TO_RESTORE_FOR_BACK_COMPATIBILITY,
        backend_utils._RAY_YAML_KEYS_TO_RESTORE_EXCEPTIONS)
    sg = yaml_utils.read_yaml_str(out)['provider']['security_group']
    # GroupName is an exception -> taken from new yaml.
    assert sg['GroupName'] == 'new-name'
    # ManagedBySkyPilot is not an exception -> restored from old yaml.
    assert sg['ManagedBySkyPilot'] is False


def test_replace_yaml_dicts_restores_new_nested_field_when_old_is_null():
    """Old yaml has the intermediate key present but null (e.g. `key:`).

    `dict.setdefault(key, {})` would return the existing None here, so the
    revert must explicitly treat a non-dict intermediate as absent and
    rebuild the path rather than crashing.
    """
    new_yaml = ('cluster_name: c\n'
                'provider:\n'
                '  type: external\n'
                '  region: r\n'
                '  security_group:\n'
                '    GroupName: new-name\n'
                '    ManagedBySkyPilot: true\n'
                'auth: {ssh_user: ubuntu}\n'
                'node_config: {InstanceType: t}\n')
    # `security_group:` with no value parses to None.
    old_yaml = ('cluster_name: c\n'
                'provider:\n'
                '  type: external\n'
                '  region: r\n'
                '  security_group:\n'
                'auth: {ssh_user: ubuntu}\n'
                'node_config: {InstanceType: t}\n')

    out = backend_utils._replace_yaml_dicts(
        new_yaml, old_yaml,
        backend_utils._RAY_YAML_KEYS_TO_RESTORE_FOR_BACK_COMPATIBILITY,
        backend_utils._RAY_YAML_KEYS_TO_RESTORE_EXCEPTIONS)
    result = yaml_utils.read_yaml_str(out)
    assert result['provider']['security_group']['GroupName'] == 'new-name'


def test_make_safe_symlink_command_default_uses_sudo():
    """By default the privileged steps are prefixed with sudo."""
    cmd = backend_utils.FileMountHelper.make_safe_symlink_command(
        source='/etc/config', target='/home/user/.sky/etc/config')
    assert 'sudo mkdir -p /etc' in cmd
    assert 'sudo ln -s /home/user/.sky/etc/config /etc/config' in cmd


def test_make_safe_symlink_command_empty_sudo_cmd_omits_sudo():
    """Passing sudo_cmd='' drops the prefix so the command does not depend on
    a sudo binary (e.g. a container already running as root)."""
    cmd = backend_utils.FileMountHelper.make_safe_symlink_command(
        source='/etc/config', target='/home/user/.sky/etc/config', sudo_cmd='')
    assert 'sudo' not in cmd
    assert cmd.startswith('mkdir -p /etc')
    assert 'ln -s /home/user/.sky/etc/config /etc/config' in cmd


def test_make_safe_symlink_command_leaves_target_unquoted():
    """The target is interpolated unquoted so a leading ~ still expands to
    $HOME at runtime -- the wrapped file-mount dir starts with ~/."""
    cmd = backend_utils.FileMountHelper.make_safe_symlink_command(
        source='/etc/config', target='~/.sky/file_mounts/etc/config')
    assert 'ln -s ~/.sky/file_mounts/etc/config /etc/config' in cmd
    assert "'~/.sky/file_mounts/etc/config'" not in cmd
