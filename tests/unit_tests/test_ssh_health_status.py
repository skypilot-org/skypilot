"""An unavailable SSH health probe is not evidence of a failed Ray runtime."""
import contextlib
import time
from unittest import mock

import pytest

from sky import backends
from sky import clouds
from sky import exceptions
from sky.backends import backend_utils
from sky.utils import command_runner
from sky.utils import status_lib


@pytest.fixture
def health_probe():
    handle = mock.Mock(spec=backends.CloudVmRayResourceHandle)
    handle.cluster_name = 'test-cluster'
    handle.cluster_name_on_cloud = 'test-cluster-1234'
    handle.cluster_yaml = '/fake/cluster.yaml'
    handle.launched_nodes = 1
    handle.num_ips_per_node = 1
    handle.launched_resources = mock.Mock(unsafe=True)
    handle.launched_resources.cloud = clouds.Azure()
    handle.launched_resources.use_spot = False
    handle.launched_resources.assert_launchable.return_value = handle.launched_resources
    handle.provision_runtime_metadata = mock.Mock(has_ray=True)
    runner = mock.Mock(spec=command_runner.SSHCommandRunner)
    handle.get_command_runners.return_value = [runner]
    record = dict(handle=handle,
                  status=status_lib.ClusterStatus.UP,
                  cluster_hash='fake-hash',
                  autostop=-1,
                  to_down=False,
                  launched_at=time.time() - 3600)
    with contextlib.ExitStack() as stack:

        def patch(name, **kwargs):
            return stack.enter_context(
                mock.patch.object(backend_utils, name, **kwargs))

        query = patch(
            '_query_cluster_status_via_cloud_api',
            return_value={'node': (status_lib.ClusterStatus.UP, None)})
        failures = patch('ExternalFailureSource')
        failures.get.return_value = None
        prefix = backend_utils.global_user_state.ABNORMAL_STATUS_REASON_PREFIX
        state = patch('global_user_state')
        state.ABNORMAL_STATUS_REASON_PREFIX = prefix
        state.get_cluster_from_name.return_value = record
        backend = patch('get_backend_from_handle').return_value
        backend.is_definitely_autostopping.return_value = False
        count = patch('_count_healthy_nodes_from_ray', return_value=(1, 0))
        stack.enter_context(
            mock.patch.object(backends.CloudVmRayBackend,
                              'post_teardown_cleanup'))
        stack.enter_context(mock.patch.object(backend_utils.time, 'sleep'))
        yield record, runner, state, query, failures, count


@pytest.mark.parametrize('cloud',
                         [clouds.AWS, clouds.GCP, clouds.Azure, clouds.Lambda])
@pytest.mark.parametrize(
    'prior', [status_lib.ClusterStatus.UP, status_lib.ClusterStatus.INIT])
@pytest.mark.parametrize('stderr', [
    'proxy dialer did not pass back a connection',
    'ssh: connect to host 10.0.0.1 port 22: Connection timed out',
    '',
])
def test_unavailable_ssh_does_not_change_cluster_state(health_probe, cloud,
                                                       prior, stderr):
    record, runner, state, _, _, _ = health_probe
    record['handle'].launched_resources.cloud = cloud()
    record['status'] = prior
    runner.run.return_value = (255, '', stderr)
    with pytest.raises(exceptions.ClusterStatusFetchingError,
                       match='SSH exit 255'):
        backend_utils._update_cluster_status('test-cluster',
                                             record,
                                             retry_if_missing=False)
    assert record['status'] == prior
    state.add_cluster_event.assert_not_called()
    state.add_or_update_cluster.assert_not_called()
    runner.run.assert_called_once()


def test_reachable_broken_ray_still_marks_init(health_probe):
    record, runner, state, _, _, _ = health_probe
    runner.run.return_value = (1, 'Ray cluster is not found', '')
    backend_utils._update_cluster_status('test-cluster',
                                         record,
                                         retry_if_missing=False)
    assert state.add_or_update_cluster.call_args.kwargs['ready'] is False
    assert state.add_cluster_event.call_args.args[
        1] == status_lib.ClusterStatus.INIT


def test_reachable_partial_ray_cluster_still_marks_init(health_probe):
    record, runner, state, _, _, count = health_probe
    record['handle'].launched_nodes = 2
    runner.run.return_value = (0, 'one healthy node', '')
    health_probe[3].return_value['node2'] = (status_lib.ClusterStatus.UP, None)
    count.return_value = (1, 0)
    backend_utils._update_cluster_status('test-cluster',
                                         record,
                                         retry_if_missing=False)
    assert state.add_or_update_cluster.call_args.kwargs['ready'] is False


def test_next_successful_probe_restores_normal_refresh(health_probe):
    record, runner, state, _, _, _ = health_probe
    runner.run.side_effect = [(255, '', 'unreachable'), (0, 'healthy', '')]
    with pytest.raises(exceptions.ClusterStatusFetchingError):
        backend_utils._update_cluster_status('test-cluster',
                                             record,
                                             retry_if_missing=False)
    backend_utils._update_cluster_status('test-cluster',
                                         record,
                                         retry_if_missing=False)
    assert state.add_or_update_cluster.call_args.kwargs['ready'] is True


def test_provider_stopped_node_does_not_require_ssh(health_probe):
    record, runner, _, query, _, _ = health_probe
    query.return_value = {'node': (status_lib.ClusterStatus.STOPPED, None)}
    backend_utils._update_cluster_status('test-cluster',
                                         record,
                                         retry_if_missing=False)
    runner.run.assert_not_called()


def test_concrete_external_failure_does_not_require_ssh(health_probe):
    record, runner, _, _, failures, _ = health_probe
    record['status'] = status_lib.ClusterStatus.INIT
    failures.get.return_value = [mock.Mock()]
    backend_utils._update_cluster_status('test-cluster',
                                         record,
                                         retry_if_missing=False)
    runner.run.assert_not_called()
