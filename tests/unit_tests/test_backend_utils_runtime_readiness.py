"""Status refresh must not confuse failed setup with a Ray-free runtime."""
# pylint: disable=protected-access
import time
from unittest import mock

import pytest

from sky import backends
from sky import clouds
from sky.backends import backend_utils
from sky.provision import common as provision_common
from sky.utils import status_lib


def _make_handle(cloud):
    resources = mock.Mock(unsafe=True)
    resources.cloud = cloud
    resources.accelerators = None
    resources.use_spot = False
    resources.assert_launchable.return_value = resources
    return backends.CloudVmRayResourceHandle(
        cluster_name='test-cluster',
        cluster_name_on_cloud='test-cluster-1234',
        cluster_yaml='/fake/path/cluster.yaml',
        launched_nodes=1,
        launched_resources=resources)


def _refresh(handle, status=status_lib.ClusterStatus.INIT):
    record = {
        'handle': handle,
        'status': status,
        'cluster_hash': 'fake-hash',
        'autostop': -1,
        'to_down': False,
        'launched_at': time.time() - 3600,
    }
    with mock.patch.object(
            backend_utils, '_query_cluster_status_via_cloud_api',
            return_value={'node-0': (status_lib.ClusterStatus.UP, None)}), \
         mock.patch.object(backend_utils.ExternalFailureSource, 'get',
                           return_value=None), \
         mock.patch.object(backend_utils, 'get_backend_from_handle'), \
         mock.patch.object(backend_utils.global_user_state,
                           'add_cluster_event') as event, \
         mock.patch.object(backend_utils.global_user_state,
                           'add_or_update_cluster') as update, \
         mock.patch.object(backend_utils.global_user_state,
                           'get_cluster_from_name', return_value=record):
        backend_utils._update_cluster_status('test-cluster',
                                             record,
                                             retry_if_missing=False)
    return update.call_args.kwargs['ready'], event.call_args_list


@pytest.mark.parametrize(
    'status', [status_lib.ClusterStatus.INIT, status_lib.ClusterStatus.UP])
@pytest.mark.parametrize('has_cached_ips', [False, True])
def test_failed_runtime_setup_is_not_healthy(status, has_cached_ips):
    # The real constructor supplies the metadata persisted before provisioning.
    # A failed runtime install can leave this handle after the VM is running.
    handle = _make_handle(clouds.Azure())
    assert handle.cached_external_ips is None
    assert not handle.provision_runtime_metadata.has_ray
    if has_cached_ips:
        # RetryingVmProvisioner can retain the previous runtime's IPs even
        # though the new provisioning attempt never completed.
        handle.stable_internal_external_ips = [('10.0.0.1', '10.0.0.1')]
    runner = mock.Mock()
    runner.run.return_value = (0, 'healthy-ray-status', '')
    cached_runners = [runner] if has_cached_ips else []
    with mock.patch.object(handle, 'get_command_runners',
                           return_value=cached_runners) as runners, \
         mock.patch.object(backend_utils, '_count_healthy_nodes_from_ray',
                           return_value=(1, 0)):
        ready, events = _refresh(handle, status)
    assert not ready
    runners.assert_not_called()
    assert all(call.args[1] != status_lib.ClusterStatus.UP for call in events)
    if status == status_lib.ClusterStatus.UP:
        assert 'runtime setup did not complete' in events[-1].args[2]


@pytest.mark.parametrize('cloud', [clouds.Azure(), clouds.IBM()])
@pytest.mark.parametrize('runtime_setup_done', [False, True])
def test_ray_runtime_requires_successful_health_probe(cloud,
                                                      runtime_setup_done):
    handle = _make_handle(cloud)
    handle.provision_runtime_metadata = (
        provision_common.ProvisionRuntimeMetadata(
            runtime_setup_done=runtime_setup_done))
    runner = mock.Mock()
    runner.run.return_value = (0, 'healthy-ray-status', '')
    with mock.patch.object(handle, 'get_command_runners',
                           return_value=[runner]):
        with mock.patch.object(backend_utils,
                               '_count_healthy_nodes_from_ray',
                               return_value=(1, 0)):
            ready, _ = _refresh(handle)
    assert ready
    runner.run.assert_called_once()
    runner.run.return_value = (1, '', 'SSH connection refused')
    with mock.patch.object(handle, 'get_command_runners',
                           return_value=[runner]):
        ready, _ = _refresh(handle, status_lib.ClusterStatus.UP)
    assert not ready


@pytest.mark.parametrize(
    'status', [status_lib.ClusterStatus.INIT, status_lib.ClusterStatus.UP])
def test_legacy_provisioner_does_not_require_runtime_metadata(status):
    # The Ray autoscaler never replaces the constructor's empty metadata,
    # including after a successful launch. Preserve its existing refresh path.
    handle = _make_handle(clouds.IBM())
    assert (handle.launched_resources.cloud.PROVISIONER_VERSION ==
            clouds.ProvisionerVersion.RAY_AUTOSCALER)
    assert not handle.provision_runtime_metadata.has_ray
    assert not handle.provision_runtime_metadata.runtime_setup_done
    handle.stable_internal_external_ips = [('10.0.0.1', '10.0.0.1')]
    with mock.patch.object(handle, 'get_command_runners') as runners:
        ready, _ = _refresh(handle, status)
    assert ready
    runners.assert_not_called()


def test_provisioner_materialized_ray_free_runtime_remains_healthy():
    handle = _make_handle(clouds.Azure())
    handle.provision_runtime_metadata = (
        provision_common.ProvisionRuntimeMetadata(has_ray=False,
                                                  runtime_setup_done=True))
    with mock.patch.object(handle, 'get_command_runners') as runners:
        ready, _ = _refresh(handle)
    assert ready
    runners.assert_not_called()


def test_cloud_without_ray_does_not_require_a_ray_probe():
    handle = _make_handle(clouds.Slurm())
    with mock.patch.object(handle, 'get_command_runners') as runners:
        ready, _ = _refresh(handle)
    assert ready
    runners.assert_not_called()
