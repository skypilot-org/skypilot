"""Tests for sky/serve/replica_managers.py.

Currently focused on `SkyPilotReplicaManager.__init__` startup ordering:
the daemon threads (especially `_job_status_fetcher`) must NOT race the
main thread for `self.lock` before `_recover_replica_operations` runs.
"""
from unittest import mock

import pytest

from sky import exceptions
from sky.serve import replica_managers


def test_invalid_cloud_config_stops_replica_launch_retries(
        monkeypatch, tmp_path):
    monkeypatch.setattr(replica_managers.context, 'get', mock.MagicMock())
    monkeypatch.setattr(replica_managers.task_lib.Task, 'from_yaml_str',
                        mock.Mock(return_value=mock.Mock()))
    launch = mock.Mock(return_value='request-1')
    monkeypatch.setattr(replica_managers.sdk, 'launch', launch)
    error = exceptions.InvalidCloudConfigs(
        'Invalid Kubernetes Pod: containerPort')
    result = mock.Mock(side_effect=error)
    monkeypatch.setattr(replica_managers.sdk, 'stream_and_get', result)
    terminate = mock.Mock()
    monkeypatch.setattr(replica_managers, 'terminate_cluster', terminate)
    monkeypatch.setattr(replica_managers.common_utils.Backoff,
                        'current_backoff', lambda self: 0)

    with pytest.raises(RuntimeError) as exc:
        replica_managers.launch_cluster(1,
                                        'unused',
                                        'test-replica',
                                        str(tmp_path / 'launch.log'), {}, {},
                                        max_retry=2)

    assert exc.value.__cause__ is error
    launch.assert_called_once()
    result.assert_called_once_with('request-1')
    terminate.assert_not_called()


class TestSkyPilotReplicaManagerInitOrdering:
    """`SkyPilotReplicaManager.__init__` must run `_recover_replica_operations`
    BEFORE starting the `_job_status_fetcher` / `_thread_pool_refresher` /
    `_replica_prober` daemon threads.

    If the daemon threads start first, `_job_status_fetcher` will acquire
    `self.lock` (via the `@with_lock` decorator on `_fetch_job_status`)
    and perform a per-replica SSH/gRPC call to query job status. When a
    replica's head node is unreachable (pod / VM gone), each SSH connect
    hangs at the kernel TCP timeout (tens of seconds to minutes). The
    main thread then blocks on `_recover_replica_operations`'s
    `with self.lock:` for the full hang duration, never returns from
    `SkyPilotReplicaManager.__init__`, and `uvicorn.run` is never called.

    With HA recovery changes, `_wait_for_controller_ready`
    then times out (60s) → `_bail_on_boot_failure` → `os._exit(1)` →
    daemon retries → same race → infinite recovery loop.

    The fix: recovery first, daemon threads after.
    """

    def test_recover_called_before_threads_start(self):
        """Verify the call order: `_recover_replica_operations` first,
        then each daemon thread's `.start()`."""
        call_order = []

        def _record(name):

            def _fn(*_args, **_kwargs):
                call_order.append(name)

            return _fn

        # Patch the heavy deps so __init__ doesn't actually do work.
        # We only care about the call order.
        with mock.patch.object(
                replica_managers.ReplicaManager, '__init__',
                return_value=None), \
             mock.patch(
                 'sky.serve.replica_managers.serve_state.get_yaml_content',
                 return_value='dummy: yaml'), \
             mock.patch(
                 'sky.serve.replica_managers.task_lib.Task.from_yaml_str',
                 return_value=mock.MagicMock()), \
             mock.patch(
                 'sky.serve.replica_managers.spot_placer.SpotPlacer.from_task',
                 return_value=None), \
             mock.patch.object(
                 replica_managers.SkyPilotReplicaManager,
                 '_recover_replica_operations',
                 _record('recover')), \
             mock.patch(
                 'sky.serve.replica_managers.threading.Thread') as mock_thread:
            # Each Thread(target=...).start() records the target's name
            # via our side_effect on .start().
            def thread_factory(*_args, **kwargs):
                target = kwargs.get('target')
                t = mock.Mock()
                target_name = getattr(target, '__name__', repr(target))
                t.start.side_effect = _record(f'thread_start:{target_name}')
                return t

            mock_thread.side_effect = thread_factory

            spec = mock.MagicMock()
            replica_managers.SkyPilotReplicaManager(service_name='svc',
                                                    spec=spec,
                                                    version=1)

        # `recover` must come before any `thread_start:*` entry. The
        # daemon threads themselves may be created in any order relative
        # to each other (we don't constrain that), but ALL of them must
        # appear after `recover`.
        assert 'recover' in call_order, (
            f'_recover_replica_operations was never called; '
            f'call_order={call_order}')
        recover_idx = call_order.index('recover')
        for i, name in enumerate(call_order):
            if name.startswith('thread_start:'):
                assert i > recover_idx, (
                    f'{name} happened at index {i} before recover at '
                    f'index {recover_idx}; call_order={call_order}. '
                    f'Daemon threads must NOT start until '
                    f'_recover_replica_operations has finished — '
                    f'see the docstring of '
                    f'TestSkyPilotReplicaManagerInitOrdering.')

    def test_all_three_daemon_threads_are_started(self):
        """Sanity: regardless of ordering, the three daemon threads
        (_thread_pool_refresher / _job_status_fetcher / _replica_prober)
        still all start. The fix is purely a reorder, not a removal."""
        started_targets = []

        with mock.patch.object(
                replica_managers.ReplicaManager, '__init__',
                return_value=None), \
             mock.patch(
                 'sky.serve.replica_managers.serve_state.get_yaml_content',
                 return_value='dummy: yaml'), \
             mock.patch(
                 'sky.serve.replica_managers.task_lib.Task.from_yaml_str',
                 return_value=mock.MagicMock()), \
             mock.patch(
                 'sky.serve.replica_managers.spot_placer.SpotPlacer.from_task',
                 return_value=None), \
             mock.patch.object(
                 replica_managers.SkyPilotReplicaManager,
                 '_recover_replica_operations'), \
             mock.patch(
                 'sky.serve.replica_managers.threading.Thread') as mock_thread:

            def thread_factory(*_args, **kwargs):
                target = kwargs.get('target')
                started_targets.append(getattr(target, '__name__', None))
                t = mock.Mock()
                return t

            mock_thread.side_effect = thread_factory

            spec = mock.MagicMock()
            replica_managers.SkyPilotReplicaManager(service_name='svc',
                                                    spec=spec,
                                                    version=1)

        # Bound methods on the instance — verify by name.
        assert '_thread_pool_refresher' in started_targets
        assert '_job_status_fetcher' in started_targets
        assert '_replica_prober' in started_targets
