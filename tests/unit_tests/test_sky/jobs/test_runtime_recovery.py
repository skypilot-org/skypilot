"""Runtime recovery observations through the controller and durable task state."""
import contextlib
import json
from unittest import mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

from sky.jobs import controller as controller_module
from sky.jobs import runtime
from sky.jobs import state
from sky.skylet import job_lib


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / 'jobs.db'
    engine = create_engine(f'sqlite:///{path}')
    async_engine = create_async_engine(f'sqlite+aiosqlite:///{path}')
    monkeypatch.setattr(state._db_manager, '_engine', engine)
    monkeypatch.setattr(state._db_manager, '_engine_async', async_engine)
    monkeypatch.setattr(state.migration_utils, 'db_lock',
                        lambda _: contextlib.nullcontext())
    state.create_table(engine)
    with engine.begin() as connection:
        connection.execute(state.spot_table.insert().values(
            spot_job_id=42,
            task_id=0,
            job_name='task',
            status='RUNNING',
            recovery_count=0,
            job_duration=0,
            last_recovered_at=100,
            metadata=json.dumps({'other_owner': {
                'kept': True
            }})))
    return engine


def task_row(engine):
    with engine.connect() as connection:
        return connection.execute(state.spot_table.select()).mappings().one()


async def observe(count, **kwargs):
    await state.observe_runtime_recovery_async(
        42,
        0,
        kwargs.pop('runtime_id', 'allocation-a'),
        count,
        user_restart_count=kwargs.pop('user_restart_count', 0),
        running=kwargs.pop('running', False),
        terminal=kwargs.pop('terminal', False),
        waiting=kwargs.pop('waiting', False),
        reason='platform restart',
        recovery_reasons={1: 'exit 7'},
        started_at=kwargs.pop('started_at', None),
        callback_func=mock.AsyncMock(),
        **kwargs)


@pytest.mark.asyncio
async def test_restart_counter_is_durable_and_counts_skipped_attempts(database):
    await observe(3)
    await observe(3)
    row = task_row(database)
    assert row['status'] == 'RECOVERING'
    assert row['recovery_count'] == 3
    await observe(3, running=True)
    await observe(3, running=True)
    row = task_row(database)
    assert row['status'] == 'RUNNING'
    assert row['recovery_count'] == 3
    assert json.loads(row['metadata'])['other_owner'] == {'kept': True}
    with database.connect() as connection:
        events = connection.execute(
            state.job_events_table.select()).mappings().all()
    assert [event['reason'] for event in events] == [
        'exit 7', 'platform restart', 'platform restart',
        'Runtime recovery completed'
    ]


@pytest.mark.asyncio
async def test_fast_terminal_and_new_allocation_counter(database):
    await observe(2, terminal=True)
    await observe(2, terminal=True)
    await observe(0, runtime_id='allocation-b', running=True)
    assert task_row(database)['recovery_count'] == 2
    await observe(1, runtime_id='allocation-b', terminal=True)
    assert task_row(database)['recovery_count'] == 3


@pytest.mark.asyncio
async def test_initial_pending_is_not_recovery(database):
    await observe(0)
    assert task_row(database)['status'] == 'RUNNING'
    assert task_row(database)['recovery_count'] == 0


class StopMonitoring(Exception):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize('nodes', [1, 2])
@pytest.mark.parametrize('forced', [False, True])
async def test_controller_observes_before_healthy_shortcut_and_refresh(
        database, monkeypatch, nodes, forced):
    controller = controller_module.JobController.__new__(
        controller_module.JobController)
    controller._job_id = 42
    controller._backend = mock.MagicMock()
    task = mock.MagicMock(num_nodes=nodes)
    observation = runtime.RuntimeRecoveryStatus('allocation-a', 2,
                                                job_lib.JobStatus.PENDING)
    monkeypatch.setattr(runtime, 'is_registered', lambda: True)
    hook = mock.Mock(side_effect=[observation, observation, StopMonitoring()])
    monkeypatch.setattr(runtime, 'get_recovery_status', hook)
    monkeypatch.setattr(controller_module.global_user_state,
                        'get_handle_from_cluster_name', mock.Mock())
    monkeypatch.setattr(controller_module.asyncio, 'sleep', mock.AsyncMock())
    monkeypatch.setattr(controller_module.backend_utils,
                        'async_check_network_connection', mock.AsyncMock())
    monkeypatch.setattr(
        controller_module.managed_job_utils, 'get_job_status',
        mock.AsyncMock(return_value=(job_lib.JobStatus.RUNNING, None)))
    refresh = mock.Mock(side_effect=AssertionError('must not refresh'))
    monkeypatch.setattr(controller_module.backend_utils,
                        'refresh_cluster_status_handle', refresh)
    controller._update_live_log_links = mock.AsyncMock(return_value=True)
    executor = mock.MagicMock()
    with pytest.raises(StopMonitoring):
        await controller._monitor_one_task_impl(
            0,
            task,
            'cluster',
            executor,
            mock.MagicMock(),
            callback_func=mock.AsyncMock(),
            force_transit_to_recovering=forced)
    assert task_row(database)['status'] == 'RECOVERING'
    assert task_row(database)['recovery_count'] == 2
    executor.recover.assert_not_called()
    refresh.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'terminal_status', [job_lib.JobStatus.SUCCEEDED, job_lib.JobStatus.FAILED])
async def test_controller_fast_terminal_does_not_repeat_user_retry(
        database, monkeypatch, terminal_status):
    controller = controller_module.JobController.__new__(
        controller_module.JobController)
    controller._job_id = 42
    controller._backend = mock.MagicMock()
    controller.download_log_and_stream = mock.Mock()
    controller._get_cluster_job_exit_codes = mock.AsyncMock(return_value=[7])
    controller._cleanup_cluster = mock.AsyncMock()
    controller._update_live_log_links = mock.AsyncMock(return_value=True)
    handle = mock.MagicMock()
    executor = mock.MagicMock()
    monkeypatch.setattr(runtime, 'is_registered', lambda: True)
    monkeypatch.setattr(
        runtime, 'get_recovery_status',
        mock.Mock(return_value=runtime.RuntimeRecoveryStatus(
            'allocation-a', 3, terminal_status, handles_user_retries=True)))
    monkeypatch.setattr(controller_module.global_user_state,
                        'get_handle_from_cluster_name',
                        mock.Mock(return_value=handle))
    monkeypatch.setattr(controller_module.asyncio, 'sleep', mock.AsyncMock())
    monkeypatch.setattr(controller_module.backend_utils,
                        'async_check_network_connection', mock.AsyncMock())
    monkeypatch.setattr(
        controller_module.managed_job_utils, 'get_job_status',
        mock.AsyncMock(return_value=(job_lib.JobStatus.RUNNING, None)))
    monkeypatch.setattr(controller_module.managed_job_utils,
                        'try_to_get_job_end_time',
                        mock.Mock(return_value=12345))
    monkeypatch.setattr(controller_module.backend_utils, 'get_clusters',
                        mock.Mock(return_value=[]))
    refresh = mock.Mock(side_effect=AssertionError('must not refresh'))
    monkeypatch.setattr(controller_module.backend_utils,
                        'refresh_cluster_status_handle', refresh)
    monkeypatch.setattr(state, 'set_failed_async', mock.AsyncMock())
    monkeypatch.setattr(state, 'set_succeeded_async', mock.AsyncMock())
    result = await controller._monitor_one_task_impl(
        0,
        mock.MagicMock(num_nodes=2),
        'cluster',
        executor,
        mock.MagicMock(),
        callback_func=mock.AsyncMock())
    assert result == (terminal_status == job_lib.JobStatus.SUCCEEDED)
    assert task_row(database)['recovery_count'] == 3
    executor.should_restart_on_failure.assert_not_called()
    executor.recover.assert_not_called()
    refresh.assert_not_called()


def test_recovery_dispatch_defers_and_preserves_explicit_record(monkeypatch):
    handle = mock.Mock()
    handle.provision_runtime_metadata.has_ray = False
    first = mock.Mock()
    first.owns.return_value = True
    first.get_recovery_status.return_value = None
    second = mock.Mock()
    second.owns.return_value = True
    record = runtime.RuntimeRecoveryStatus('allocation', 0, None)
    second.get_recovery_status.return_value = record
    monkeypatch.setattr(runtime, '_runtimes', [first, second])
    assert runtime.get_recovery_status(
        handle, 'cluster', job_id=42, task_id=0, task=mock.Mock()) is record
    handle.provision_runtime_metadata.has_ray = True
    assert runtime.get_recovery_status(
        handle, 'cluster', job_id=42, task_id=0, task=mock.Mock()) is None


@pytest.mark.asyncio
async def test_controller_runtime_failover_cleans_allocation_and_keeps_reason(
        database, monkeypatch):
    controller = controller_module.JobController.__new__(
        controller_module.JobController)
    controller._job_id = 42
    controller._pool = None
    controller._backend = mock.MagicMock()
    controller._cleanup_cluster = mock.AsyncMock()
    controller._update_live_log_links = mock.AsyncMock(return_value=True)
    handle = mock.MagicMock()
    handle.launched_resources.need_cleanup_after_preemption_or_failure.return_value = False
    executor = mock.MagicMock()
    executor.recover = mock.AsyncMock(side_effect=StopMonitoring())
    monkeypatch.setattr(runtime, 'is_registered', lambda: True)
    monkeypatch.setattr(
        runtime, 'get_recovery_status',
        mock.Mock(return_value=runtime.RuntimeRecoveryStatus(
            'allocation-a',
            1,
            job_lib.JobStatus.PENDING,
            reason='allocation cannot recover',
            should_relaunch=True)))
    capture = mock.Mock()
    monkeypatch.setattr(runtime, 'on_before_recovery', capture)
    monkeypatch.setattr(controller_module.global_user_state,
                        'get_handle_from_cluster_name',
                        mock.Mock(return_value=handle))
    monkeypatch.setattr(controller_module.global_user_state,
                        'get_cluster_events', mock.Mock(return_value=[]))
    monkeypatch.setattr(controller_module.ExternalFailureSource,
                        'is_registered', lambda: False)
    monkeypatch.setattr(controller_module.asyncio, 'sleep', mock.AsyncMock())
    monkeypatch.setattr(controller_module.backend_utils,
                        'async_check_network_connection', mock.AsyncMock())
    monkeypatch.setattr(
        controller_module.managed_job_utils, 'get_job_status',
        mock.AsyncMock(return_value=(job_lib.JobStatus.RUNNING, None)))
    refresh = mock.Mock(side_effect=AssertionError('must not refresh'))
    monkeypatch.setattr(controller_module.backend_utils,
                        'refresh_cluster_status_handle', refresh)
    with pytest.raises(StopMonitoring):
        await controller._monitor_one_task_impl(0,
                                                mock.MagicMock(num_nodes=1),
                                                'cluster',
                                                executor,
                                                mock.MagicMock(),
                                                callback_func=mock.AsyncMock())
    controller._cleanup_cluster.assert_awaited_once_with('cluster')
    capture.assert_called_once()
    refresh.assert_not_called()
    executor.recover.assert_awaited_once_with()
    assert task_row(database)['recovering_from_failure'] is True
    with database.connect() as connection:
        events = connection.execute(
            state.job_events_table.select()).mappings().all()
    assert events[-1]['reason'] == 'allocation cannot recover'


@pytest.mark.asyncio
async def test_queued_after_counter_was_observed_running(database):
    await observe(1, running=True)
    await observe(1, waiting=True)
    await observe(1, waiting=True)
    assert task_row(database)['status'] == 'RECOVERING'
    assert task_row(database)['recovery_count'] == 1
    await observe(1, running=True)
    assert task_row(database)['status'] == 'RUNNING'
    assert task_row(database)['recovery_count'] == 1


@pytest.mark.asyncio
async def test_unknown_observation_preserves_running_and_cancellation(database):
    await observe(1, running=True)
    await observe(1)
    assert task_row(database)['status'] == 'RUNNING'
    with database.begin() as connection:
        connection.execute(
            state.spot_table.update().values(status='CANCELLING'))
    await observe(2, running=True)
    assert task_row(database)['status'] == 'CANCELLING'
    assert task_row(database)['recovery_count'] == 1


@pytest.mark.asyncio
async def test_duration_excludes_observed_queue_wait(database, monkeypatch):
    clock = mock.Mock(return_value=200)
    monkeypatch.setattr(state.time, 'time', clock)
    await observe(0, running=True)
    clock.return_value = 220
    await observe(1, waiting=True)
    clock.return_value = 250
    await observe(1, running=True, started_at=240)
    row = task_row(database)
    assert row['job_duration'] == 100
    assert row['last_recovered_at'] == 240


@pytest.mark.asyncio
async def test_fast_terminal_uses_latest_actual_start(database, monkeypatch):
    monkeypatch.setattr(state.time, 'time', lambda: 300)
    await observe(3, terminal=True, started_at=280)
    row = task_row(database)
    assert row['last_recovered_at'] == 280
    assert row['job_duration'] == 0
    await state.set_succeeded_async(42,
                                    0,
                                    end_time=290,
                                    callback_func=mock.AsyncMock())
    row = task_row(database)
    assert (row['job_duration'] + row['end_at'] -
            row['last_recovered_at']) == 10


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal', [False, True])
async def test_controller_recovery_resumes_existing_runtime(
        database, monkeypatch, terminal):
    monkeypatch.setattr(state.time, 'time', lambda: 200)
    await observe(0, running=True)
    await state.set_emergency_recovering_async(42, 0, 'controller interrupted',
                                               mock.AsyncMock())
    assert task_row(database)['job_duration'] == 100
    monkeypatch.setattr(state.time, 'time', lambda: 220)
    await observe(0, running=not terminal, terminal=terminal, started_at=100)
    row = task_row(database)
    assert row['status'] == 'RUNNING'
    assert row['recovery_count'] == 0
    assert row['job_duration'] == 0
    assert row['last_recovered_at'] == 100


@pytest.mark.asyncio
async def test_user_budget_survives_allocations_without_double_count(database):
    await observe(3, user_restart_count=1, running=True)
    await observe(3, user_restart_count=1, running=True)
    assert await state.get_runtime_user_restarts_async(42, 0) == 1
    await observe(0, runtime_id='allocation-b', running=True)
    assert await state.get_runtime_user_restarts_async(42, 0) == 1
    await observe(0, runtime_id='allocation-b', user_restart_count=1)
    await observe(1,
                  runtime_id='allocation-b',
                  user_restart_count=1,
                  running=True)
    assert await state.get_runtime_user_restarts_async(42, 0) == 2
    assert task_row(database)['recovery_count'] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize('new_allocation', [False, True])
async def test_provisioning_restarts_count_before_handle_exists(
        database, new_allocation):
    with database.begin() as connection:
        connection.execute(state.spot_table.update().values(
            status='STARTING', start_at=None, last_recovered_at=-1))
    for count in (1, 2, 3, 3):
        state.observe_runtime_recovery_during_provisioning(
            42,
            0,
            'allocation-a',
            count,
            reason='launch failed requeued held',
            recovery_reasons={3: 'launch hold release limit reached'})
    row = task_row(database)
    assert row['status'] == 'STARTING'
    assert row['recovery_count'] == 3
    assert row['job_duration'] == 0
    assert row['last_recovered_at'] == -1
    assert json.loads(row['metadata'])['other_owner'] == {'kept': True}
    with database.connect() as connection:
        events = connection.execute(
            state.job_events_table.select()).mappings().all()
    assert [event['reason'] for event in events] == [
        'launch failed requeued held', 'launch failed requeued held',
        'launch hold release limit reached'
    ]
    await state.set_started_async(42, 0, 300, mock.AsyncMock())
    if new_allocation:
        await observe(0,
                      runtime_id='allocation-b',
                      running=True,
                      started_at=300)
    else:
        await observe(3, running=True, started_at=300)
    row = task_row(database)
    assert row['status'] == 'RUNNING'
    assert row['recovery_count'] == 3
    assert row['last_recovered_at'] == 300


def test_provisioning_observation_preserves_cancellation(database):
    with database.begin() as connection:
        connection.execute(
            state.spot_table.update().values(status='CANCELLING'))
    state.observe_runtime_recovery_during_provisioning(42, 0, 'allocation-a', 3)
    row = task_row(database)
    assert row['status'] == 'CANCELLING'
    assert row['recovery_count'] == 0


@pytest.mark.asyncio
async def test_provisioning_user_budget_survives_monitor_and_new_allocation(
        database):
    state.observe_runtime_recovery_during_provisioning(42,
                                                       0,
                                                       'allocation-a',
                                                       3,
                                                       user_restart_count=1)
    state.observe_runtime_recovery_during_provisioning(42,
                                                       0,
                                                       'allocation-a',
                                                       3,
                                                       user_restart_count=2)
    assert await state.get_runtime_user_restarts_async(42, 0) == 2
    await observe(3, user_restart_count=2, running=True)
    assert await state.get_runtime_user_restarts_async(42, 0) == 2
    state.observe_runtime_recovery_during_provisioning(42,
                                                       0,
                                                       'allocation-b',
                                                       1,
                                                       user_restart_count=1)
    await observe(1,
                  runtime_id='allocation-b',
                  user_restart_count=1,
                  running=True)
    assert await state.get_runtime_user_restarts_async(42, 0) == 3
    assert task_row(database)['recovery_count'] == 4
