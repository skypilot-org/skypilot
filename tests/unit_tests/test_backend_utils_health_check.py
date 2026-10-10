"""Tests for the retried cluster health probe in _update_cluster_status.

A status refresh of a cluster whose nodes are all up probes the SkyPilot
runtime on the head node (`ray status`). A transient probe failure (SSH
banner timeout, SSM agent restart, ...) must not immediately mark the cluster
INIT, since the managed jobs controller treats INIT as a preemption and
relaunches the job. The probe is retried at a fixed interval within the
`provision.health_check` budget, whose defaults match the retry Kubernetes
already had (5 attempts, 1s apart).
"""
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


def _refresh(cloud, probe_results, config=None, launched_nodes=1):
    """Runs a status refresh of an all-nodes-up cluster.

    Returns (ready, head_runner, sleeps, warning_mock, init_messages).
    """
    handle = _make_handle(cloud, launched_nodes=launched_nodes)
    results = list(probe_results)
    head_runner = mock.Mock()
    head_runner.run.side_effect = lambda *args, **kwargs: results.pop(0)
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

    overrides = config or {}
    add_or_update = mock.Mock()
    node_statuses = {
        f'node-{i}': (status_lib.ClusterStatus.UP, None)
        for i in range(launched_nodes)
    }
    sleep = mock.Mock()
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
         mock.patch.object(backend_utils.logger, 'warning') as warning:
        backend_utils._update_cluster_status('test-cluster',
                                             record,
                                             retry_if_missing=False)
    ready = add_or_update.call_args.kwargs['ready']
    sleeps = [c.args[0] for c in sleep.call_args_list]
    return ready, head_runner, sleeps, warning, init_messages


def _health_check(attempts=None, interval_seconds=None):
    config = {}
    if attempts is not None:
        config[('provision', 'health_check', 'attempts')] = attempts
    if interval_seconds is not None:
        config[('provision', 'health_check',
                'interval_seconds')] = interval_seconds
    return config


def _recovery_hint_logged(warning):
    return any(
        'to recover from INIT status' in str(c) for c in warning.call_args_list)


@pytest.mark.parametrize('failure', [_SSM_NOT_CONNECTED, _BANNER_TIMEOUT],
                         ids=['ssm', 'banner'])
def test_transient_failures_then_success_is_healthy(failure):
    ready, head_runner, sleeps, warning, init_messages = _refresh(
        clouds.AWS(), [failure, failure, failure, (0, _HEALTHY_1_NODE, '')])

    assert ready is True
    assert head_runner.run.call_count == 4
    assert sleeps == [1, 1, 1]
    assert not _recovery_hint_logged(warning)
    assert not init_messages


def test_persistent_failure_is_unhealthy_after_all_attempts():
    ready, head_runner, sleeps, warning, init_messages = _refresh(
        clouds.AWS(), [_BANNER_TIMEOUT] * 5)

    assert ready is False
    assert head_runner.run.call_count == 5
    assert sleeps == [1, 1, 1, 1]
    # Not a "restarted manually" signature, so no recovery hint.
    assert not _recovery_hint_logged(warning)
    assert len(init_messages) == 1
    assert ('health probe failed: Connection timed out during banner '
            'exchange') in init_messages[0]


def test_persistent_ssh_timeout_keeps_manual_restart_hint():
    ready, head_runner, _, warning, init_messages = _refresh(
        clouds.AWS(), [_SSH_TIMED_OUT] * 5)

    assert ready is False
    assert head_runner.run.call_count == 5
    assert _recovery_hint_logged(warning)
    assert 'health probe failed: ssh: connect to host' in init_messages[0]


def test_config_sets_attempts_and_interval():
    ready, head_runner, sleeps, _, _ = _refresh(
        clouds.AWS(), [_SSM_NOT_CONNECTED] * 3,
        config=_health_check(attempts=3, interval_seconds=5))

    assert ready is False
    assert head_runner.run.call_count == 3
    assert sleeps == [5, 5]


def test_single_attempt_is_single_shot():
    ready, head_runner, sleeps, _, _ = _refresh(
        clouds.AWS(), [_SSM_NOT_CONNECTED], config=_health_check(attempts=1))

    assert ready is False
    head_runner.run.assert_called_once()
    assert not sleeps


def test_runtime_not_setup_is_not_retried_outside_kubernetes():
    ready, head_runner, sleeps, warning, _ = _refresh(clouds.AWS(),
                                                      [_RAY_NOT_FOUND])

    assert ready is False
    head_runner.run.assert_called_once()
    assert not sleeps
    assert _recovery_hint_logged(warning)


def test_kubernetes_default_retry_is_unchanged():
    ready, head_runner, sleeps, _, _ = _refresh(
        clouds.Kubernetes(),
        [_SSM_NOT_CONNECTED, _RAY_NOT_FOUND, (0, _HEALTHY_1_NODE, '')])

    assert ready is True
    assert head_runner.run.call_count == 3
    assert sleeps == [1, 1]


def test_kubernetes_persistent_failure_skips_final_sleep():
    ready, head_runner, sleeps, _, init_messages = _refresh(
        clouds.Kubernetes(), [_SSM_NOT_CONNECTED] * 5)

    assert ready is False
    assert head_runner.run.call_count == 5
    assert sleeps == [1] * 4
    assert '0/1 ready' in init_messages[0]


def test_kubernetes_honours_config():
    ready, head_runner, sleeps, _, _ = _refresh(
        clouds.Kubernetes(), [_SSM_NOT_CONNECTED] * 2,
        config=_health_check(attempts=2, interval_seconds=3))

    assert ready is False
    assert head_runner.run.call_count == 2
    assert sleeps == [3]


@pytest.mark.parametrize('config,expected_sleeps',
                         [({}, [1]), (_health_check(interval_seconds=7), [7])],
                         ids=['default', 'configured'])
def test_partial_ray_cluster_is_retried(config, expected_sleeps):
    # Not all nodes show up in `ray status` yet; then they do.
    ready, head_runner, sleeps, _, _ = _refresh(clouds.AWS(),
                                                [(0, _HEALTHY_1_NODE, ''),
                                                 (0, _HEALTHY_2_NODES, '')],
                                                config=config,
                                                launched_nodes=2)

    assert ready is True
    assert head_runner.run.call_count == 2
    assert sleeps == expected_sleeps


def test_persistent_partial_ray_cluster_skips_final_sleep():
    ready, head_runner, sleeps, _, init_messages = _refresh(
        clouds.AWS(), [(0, _HEALTHY_1_NODE, '')] * 3,
        config=_health_check(attempts=3, interval_seconds=10),
        launched_nodes=2)

    assert ready is False
    assert head_runner.run.call_count == 3
    assert sleeps == [10, 10]
    assert '1/2 ready' in init_messages[0]


@pytest.mark.parametrize('interval_seconds', [float('nan'), float('inf')])
def test_non_finite_interval_falls_back_to_default(interval_seconds):
    ready, head_runner, sleeps, _, _ = _refresh(
        clouds.AWS(), [_SSM_NOT_CONNECTED, (0, _HEALTHY_1_NODE, '')],
        config=_health_check(interval_seconds=interval_seconds))

    assert ready is True
    assert head_runner.run.call_count == 2
    assert sleeps == [1]


@pytest.mark.parametrize('health_check', [{}, {
    'attempts': 1
}, {
    'attempts': 7,
    'interval_seconds': 2.5
}])
def test_schema_accepts_valid_config(health_check):
    common_utils.validate_schema({'provision': {
        'health_check': health_check
    }}, schemas.get_config_schema(), 'Invalid sky config: ')


@pytest.mark.parametrize('health_check', [{
    'attempts': 0
}, {
    'attempts': '3'
}, {
    'interval_seconds': -1
}, {
    'timeout_seconds': 60
}])
def test_schema_rejects_invalid_config(health_check):
    with pytest.raises(ValueError):
        common_utils.validate_schema(
            {'provision': {
                'health_check': health_check
            }}, schemas.get_config_schema(), 'Invalid sky config: ')
