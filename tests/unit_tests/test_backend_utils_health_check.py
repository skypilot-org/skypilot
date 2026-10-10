"""Tests for the health probe retries in _update_cluster_status."""
# pylint: disable=protected-access
import time
from unittest import mock

import pytest

from sky import backends
from sky import clouds
from sky.backends import backend_utils
from sky.utils import common_utils
from sky.utils import schemas
from sky.utils import status_lib

_HEALTHY_1_NODE = 'Healthy:\n 1 node_aaaa\n'
_HEALTHY_2_NODES = 'Healthy:\n 1 node_aaaa\n 1 node_bbbb\n'
_SSM_NOT_CONNECTED = (255, '',
                      'An error occurred (TargetNotConnected) when calling the '
                      'StartSession operation: i-0123 is not connected.')
_BANNER_TIMEOUT = (255, '', 'Connection timed out during banner exchange')
_SSH_TIMED_OUT = (255, '',
                  'ssh: connect to host 1.2.3.4 port 22: Connection timed out')
_RAY_NOT_FOUND = (1, 'Ray cluster is not found at 127.0.0.1:6380', '')


def _make_handle(cloud, launched_nodes=1):
    handle = mock.Mock(spec=backends.CloudVmRayResourceHandle)
    handle.cluster_name = 'test-cluster'
    handle.cluster_name_on_cloud = 'test-cluster-1234'
    handle.cluster_yaml = '/fake/path/cluster.yaml'
    handle.head_ip = '1.2.3.4'
    handle.launched_nodes = launched_nodes
    handle.num_ips_per_node = 1
    handle.launched_resources = mock.Mock(unsafe=True)
    handle.launched_resources.cloud = cloud
    handle.launched_resources.use_spot = False
    handle.provision_runtime_metadata = mock.Mock()
    handle.provision_runtime_metadata.has_ray = True
    return handle


def _refresh(cloud,
             probe_results,
             timeout=None,
             launched_nodes=1,
             probe_seconds=0):
    """Runs a status refresh of an all-nodes-up cluster on a fake clock.

    Returns (ready, head_runner, sleeps, warning_mock, init_messages).
    """
    handle = _make_handle(cloud, launched_nodes=launched_nodes)
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
    handle.get_command_runners.return_value = [head_runner]

    record = {
        'handle': handle,
        'status': status_lib.ClusterStatus.UP,
        'cluster_hash': 'fake-hash',
        'autostop': -1,
        'to_down': False,
        'launched_at': time.time() - 3600,
    }
    backend = mock.Mock(spec=backends.CloudVmRayBackend)
    backend.is_definitely_autostopping.return_value = False
    external_failure = mock.Mock()
    external_failure.get.return_value = None
    init_messages = []

    def _capture_event(cluster_name, new_status, message, event_type, **kwargs):
        del cluster_name, event_type, kwargs
        if new_status == status_lib.ClusterStatus.INIT:
            init_messages.append(message)

    overrides = ({} if timeout is None else {
        ('provision', 'health_check_timeout'): timeout
    })
    add_or_update = mock.Mock()
    node_statuses = {
        f'node-{i}': (status_lib.ClusterStatus.UP, None)
        for i in range(launched_nodes)
    }
    sleep = mock.Mock(side_effect=_sleep)
    with mock.patch.object(backend_utils,
                           '_query_cluster_status_via_cloud_api',
                           return_value=node_statuses), \
         mock.patch.object(backend_utils, 'ExternalFailureSource',
                           external_failure), \
         mock.patch.object(backend_utils, 'get_backend_from_handle',
                           return_value=backend), \
         mock.patch.object(backend_utils.global_user_state,
                           'add_cluster_event', side_effect=_capture_event), \
         mock.patch.object(backend_utils.global_user_state,
                           'add_or_update_cluster', add_or_update), \
         mock.patch.object(backend_utils.global_user_state,
                           'get_cluster_from_name', return_value=record), \
         mock.patch.object(backend_utils.skypilot_config, 'get_nested',
                           side_effect=lambda keys, default_value, **_:
                           overrides.get(tuple(keys), default_value)), \
         mock.patch.object(backend_utils.time, 'sleep', sleep), \
         mock.patch.object(backend_utils.time, 'monotonic',
                           side_effect=lambda: now[0]), \
         mock.patch.object(backend_utils.logger, 'warning') as warning:
        backend_utils._update_cluster_status('test-cluster',
                                             record,
                                             retry_if_missing=False)
    ready = add_or_update.call_args.kwargs['ready']
    sleeps = [c.args[0] for c in sleep.call_args_list]
    return ready, head_runner, sleeps, warning, init_messages


def _recovery_hint_logged(warning):
    return any(
        'to recover from INIT status' in str(c) for c in warning.call_args_list)


@pytest.mark.parametrize('failure', [_SSM_NOT_CONNECTED, _BANNER_TIMEOUT],
                         ids=['ssm', 'banner'])
def test_transient_failures_are_retried(failure):
    ready, head_runner, sleeps, warning, init_messages = _refresh(
        clouds.AWS(), [failure] * 3 + [(0, _HEALTHY_1_NODE, '')])

    assert ready is True
    assert head_runner.run.call_count == 4
    assert sleeps == [1, 1, 1]
    assert not _recovery_hint_logged(warning)
    assert not init_messages


def test_persistent_failure_marks_init():
    ready, head_runner, sleeps, warning, init_messages = _refresh(
        clouds.AWS(), [_BANNER_TIMEOUT] * 10)

    assert ready is False
    assert head_runner.run.call_count == 6
    assert sleeps == [1] * 5
    assert not _recovery_hint_logged(warning)
    assert ('health probe failed: Connection timed out during banner '
            'exchange') in init_messages[0]


def test_slow_failure_is_still_retried():
    # The probe hangs past the whole timeout, but the timeout starts at the
    # failure.
    ready, head_runner, _, _, _ = _refresh(
        clouds.AWS(), [_BANNER_TIMEOUT, (0, _HEALTHY_1_NODE, '')],
        probe_seconds=30)

    assert ready is True
    assert head_runner.run.call_count == 2


def test_persistent_ssh_timeout_keeps_manual_restart_hint():
    ready, head_runner, _, warning, init_messages = _refresh(
        clouds.AWS(), [_SSH_TIMED_OUT] * 10)

    assert ready is False
    assert head_runner.run.call_count == 6
    assert _recovery_hint_logged(warning)
    assert 'health probe failed: ssh: connect to host' in init_messages[0]


@pytest.mark.parametrize('timeout,expected_sleeps', [(2.5, [1, 1, 1]), (0, [])])
def test_timeout_config(timeout, expected_sleeps):
    ready, head_runner, sleeps, _, _ = _refresh(clouds.AWS(),
                                                [_SSM_NOT_CONNECTED] * 10,
                                                timeout=timeout)

    assert ready is False
    assert head_runner.run.call_count == len(expected_sleeps) + 1
    assert sleeps == expected_sleeps


@pytest.mark.parametrize('timeout', [float('nan'), float('inf')])
def test_non_finite_timeout_uses_default(timeout):
    ready, head_runner, _, _, _ = _refresh(clouds.AWS(),
                                           [_SSM_NOT_CONNECTED] * 10,
                                           timeout=timeout)

    assert ready is False
    assert head_runner.run.call_count == 6


def test_missing_runtime_is_not_retried():
    ready, head_runner, sleeps, warning, _ = _refresh(clouds.AWS(),
                                                      [_RAY_NOT_FOUND])

    assert ready is False
    head_runner.run.assert_called_once()
    assert not sleeps
    assert _recovery_hint_logged(warning)


def test_kubernetes_retries_any_failure():
    ready, head_runner, sleeps, _, _ = _refresh(
        clouds.Kubernetes(),
        [_SSM_NOT_CONNECTED, _RAY_NOT_FOUND, (0, _HEALTHY_1_NODE, '')])

    assert ready is True
    assert head_runner.run.call_count == 3
    assert sleeps == [1, 1]


def test_kubernetes_persistent_failure_marks_init():
    ready, head_runner, sleeps, _, init_messages = _refresh(
        clouds.Kubernetes(), [_SSM_NOT_CONNECTED] * 10)

    assert ready is False
    assert head_runner.run.call_count == 6
    assert sleeps == [1] * 5
    assert '0/1 ready' in init_messages[0]


def test_partial_ray_cluster_is_retried():
    ready, _, sleeps, _, _ = _refresh(clouds.AWS(), [(0, _HEALTHY_1_NODE, ''),
                                                     (0, _HEALTHY_2_NODES, '')],
                                      launched_nodes=2)

    assert ready is True
    assert sleeps == [1]


def test_persistent_partial_ray_cluster_marks_init():
    ready, head_runner, sleeps, _, init_messages = _refresh(
        clouds.AWS(), [(0, _HEALTHY_1_NODE, '')] * 10, launched_nodes=2)

    assert ready is False
    assert head_runner.run.call_count == 6
    assert sleeps == [1] * 5
    assert '1/2 ready' in init_messages[0]


def _validate(provision):
    common_utils.validate_schema({'provision': provision},
                                 schemas.get_config_schema(),
                                 'Invalid sky config: ')


@pytest.mark.parametrize('timeout', [0, 30, 2.5])
def test_schema_accepts_valid_timeout(timeout):
    _validate({'health_check_timeout': timeout})


@pytest.mark.parametrize('provision', [{
    'health_check_timeout': -1
}, {
    'health_check_timeout': '30'
}, {
    'health_check': {
        'attempts': 5
    }
}])
def test_schema_rejects_invalid_config(provision):
    with pytest.raises(ValueError):
        _validate(provision)
