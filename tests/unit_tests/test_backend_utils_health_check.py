"""Tests for the health probe retries in _update_cluster_status."""
# pylint: disable=protected-access
import time
from unittest import mock

import pytest

from sky import backends
from sky import clouds
from sky.backends import backend_utils
from sky.utils import status_lib

_HEALTHY = (0, 'Healthy:\n 1 node_aaaa\n', '')
_SSM_NOT_CONNECTED = (255, '', 'TargetNotConnected: i-0123 is not connected.')
_BANNER_TIMEOUT = (255, '', 'Connection timed out during banner exchange')
_RAY_NOT_FOUND = (1, 'Ray cluster is not found at 127.0.0.1:6380', '')


def _refresh(probe_results, cloud=clouds.AWS(), timeout=None, probe_seconds=0):
    """Refreshes an UP cluster on a fake clock; returns (ready, probe count)."""
    results = list(probe_results)
    now = [0.0]

    def _probe(*args, **kwargs):
        del args, kwargs
        now[0] += probe_seconds
        return results.pop(0)

    def _sleep(seconds):
        now[0] += seconds

    head_runner = mock.Mock()
    head_runner.run.side_effect = _probe
    handle = mock.Mock(spec=backends.CloudVmRayResourceHandle)
    handle.cluster_name = 'test-cluster'
    handle.cluster_name_on_cloud = 'test-cluster-1234'
    handle.cluster_yaml = '/fake/path/cluster.yaml'
    handle.head_ip = '1.2.3.4'
    handle.launched_nodes = 1
    handle.num_ips_per_node = 1
    handle.launched_resources = mock.Mock(unsafe=True)
    handle.launched_resources.cloud = cloud
    handle.launched_resources.use_spot = False
    handle.provision_runtime_metadata = mock.Mock(has_ray=True)
    handle.get_command_runners.return_value = [head_runner]
    record = {
        'handle': handle,
        'status': status_lib.ClusterStatus.UP,
        'cluster_hash': 'fake-hash',
        'autostop': -1,
        'to_down': False,
        'launched_at': time.time() - 3600,
    }
    config = {} if timeout is None else {
        ('provision', 'health_check_timeout'): timeout
    }
    backend = mock.Mock(spec=backends.CloudVmRayBackend)
    backend.is_definitely_autostopping.return_value = False
    add_or_update = mock.Mock()
    with mock.patch.object(backend_utils,
                           '_query_cluster_status_via_cloud_api',
                           return_value={
                               'node-0': (status_lib.ClusterStatus.UP, None)
                           }), \
         mock.patch.object(backend_utils, 'ExternalFailureSource',
                           **{'get.return_value': None}), \
         mock.patch.object(backend_utils, 'get_backend_from_handle',
                           return_value=backend), \
         mock.patch.object(backend_utils.global_user_state,
                           'add_cluster_event'), \
         mock.patch.object(backend_utils.global_user_state,
                           'add_or_update_cluster', add_or_update), \
         mock.patch.object(backend_utils.global_user_state,
                           'get_cluster_from_name', return_value=record), \
         mock.patch.object(backend_utils.skypilot_config, 'get_nested',
                           side_effect=lambda keys, default, **_:
                           config.get(tuple(keys), default)), \
         mock.patch.object(backend_utils.time, 'sleep', side_effect=_sleep), \
         mock.patch.object(backend_utils.time, 'monotonic',
                           side_effect=lambda: now[0]):
        backend_utils._update_cluster_status('test-cluster',
                                             record,
                                             retry_if_missing=False)
    return add_or_update.call_args.kwargs['ready'], head_runner.run.call_count


def test_transient_failure_is_retried():
    assert _refresh([_SSM_NOT_CONNECTED] * 3 + [_HEALTHY]) == (True, 4)


@pytest.mark.parametrize('cloud', [clouds.AWS(), clouds.Kubernetes()])
def test_persistent_failure_gives_up_after_timeout(cloud):
    assert _refresh([_SSM_NOT_CONNECTED] * 10, cloud=cloud) == (False, 6)


def test_slow_failure_is_still_retried():
    # The probe outlasts the whole timeout, which starts at the failure.
    assert _refresh([_BANNER_TIMEOUT, _HEALTHY], probe_seconds=30) == (True, 2)


@pytest.mark.parametrize('timeout,probes', [(0, 1), (2.5, 4),
                                            (float('nan'), 6)])
def test_timeout_config(timeout, probes):
    assert _refresh([_SSM_NOT_CONNECTED] * 10,
                    timeout=timeout) == (False, probes)


def test_missing_runtime_is_not_retried():
    assert _refresh([_RAY_NOT_FOUND]) == (False, 1)
