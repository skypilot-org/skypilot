"""Runtime recovery observations through the controller and durable task state."""
import contextlib
import json
from unittest import mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

from sky.jobs import controller as controller_module
from sky.jobs import recovery_strategy
from sky.jobs import runtime
from sky.jobs import state
from sky.provision import observation as provision_observation
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


def observation(count, **kwargs):
    running = kwargs.pop('running', False)
    terminal = kwargs.pop('terminal', False)
    waiting = kwargs.pop('waiting', False)
    if running:
        status = job_lib.JobStatus.RUNNING
    elif terminal:
        status = job_lib.JobStatus.SUCCEEDED
    elif waiting:
        status = job_lib.JobStatus.PENDING
    else:
        status = None
    return runtime.RuntimeObservation(kwargs.pop('runtime_id', 'allocation-a'),
                                      count,
                                      status,
                                      reason=kwargs.pop('reason',
                                                        'platform restart'),
                                      recovery_reasons=kwargs.pop(
                                          'recovery_reasons', {1: 'exit 7'}),
                                      **kwargs)


async def observe(count, **kwargs):
    await state.observe_runtime_async(42,
                                      0,
                                      observation(count, **kwargs),
                                      callback_func=mock.AsyncMock())


def provision(count, runtime_id='allocation-a', **kwargs):
    provision_observation.report(
        state.provisioning_observation_target(42, 0),
        runtime.RuntimeObservation(runtime_id, count, None, **kwargs))


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


class StopMonitoring(BaseException):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize('nodes', [1, 2])
@pytest.mark.parametrize('forced', [False, True])
@pytest.mark.parametrize('query_error', [False, True])
async def test_controller_observes_before_healthy_shortcut_and_refresh(
        database, monkeypatch, nodes, forced, query_error):
    controller = controller_module.JobController.__new__(
        controller_module.JobController)
    controller._job_id = 42
    controller._backend = mock.MagicMock()
    task = mock.MagicMock(num_nodes=nodes)
    observation = runtime.RuntimeObservation('allocation-a', 2,
                                             job_lib.JobStatus.PENDING)
    monkeypatch.setattr(runtime, 'is_registered', lambda: True)
    observations = [observation, observation, StopMonitoring()]
    if query_error:
        observations.insert(0, RuntimeError('shared storage unavailable'))
    hook = mock.Mock(side_effect=observations)
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
@pytest.mark.parametrize('initial_status', ['STARTING', 'RUNNING'])
@pytest.mark.parametrize('restarts', [0, 3])
async def test_controller_fast_terminal_does_not_repeat_user_retry(
        database, monkeypatch, terminal_status, initial_status, restarts):
    with database.begin() as connection:
        connection.execute(
            state.spot_table.update().values(status=initial_status))
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
        mock.Mock(
            return_value=runtime.RuntimeObservation('allocation-a',
                                                    restarts,
                                                    terminal_status,
                                                    handles_user_retries=True)))
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
    result = await controller._monitor_one_task_impl(
        0,
        mock.MagicMock(num_nodes=2),
        'cluster',
        executor,
        mock.MagicMock(),
        callback_func=mock.AsyncMock())
    assert result == (terminal_status == job_lib.JobStatus.SUCCEEDED)
    assert task_row(database)['recovery_count'] == restarts
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
    record = runtime.RuntimeObservation('allocation', 0, None)
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
        mock.Mock(return_value=runtime.RuntimeObservation(
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
        provision(count,
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
    provision(3)
    row = task_row(database)
    assert row['status'] == 'CANCELLING'
    assert row['recovery_count'] == 0


@pytest.mark.asyncio
async def test_provisioning_user_budget_survives_monitor_and_new_allocation(
        database):
    provision(3, user_restart_count=1)
    provision(3, user_restart_count=2)
    assert await state.get_runtime_user_restarts_async(42, 0) == 2
    await observe(3, user_restart_count=2, running=True)
    assert await state.get_runtime_user_restarts_async(42, 0) == 2
    provision(1, runtime_id='allocation-b', user_restart_count=1)
    await observe(1,
                  runtime_id='allocation-b',
                  user_restart_count=1,
                  running=True)
    assert await state.get_runtime_user_restarts_async(42, 0) == 3
    assert task_row(database)['recovery_count'] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal', [False, True])
async def test_starting_observation_can_finish(database, monkeypatch, terminal):
    with database.begin() as connection:
        connection.execute(state.spot_table.update().values(
            status='STARTING', start_at=None, last_recovered_at=-1))
    monkeypatch.setattr(state.time, 'time', lambda: 300)
    await observe(0, running=not terminal, terminal=terminal, started_at=280)
    row = task_row(database)
    assert row['status'] == 'RUNNING'
    assert row['start_at'] == row['last_recovered_at'] == 280
    await state.set_succeeded_async(42,
                                    0,
                                    end_time=290,
                                    callback_func=mock.AsyncMock())
    assert task_row(database)['status'] == 'SUCCEEDED'


@pytest.mark.asyncio
@pytest.mark.parametrize('recovery_path', ['controller', 'requeue', 'strategy'])
@pytest.mark.parametrize('start_at', [None, 100])
async def test_emergency_from_starting_records_first_start(
        database, monkeypatch, recovery_path, start_at):
    with database.begin() as connection:
        connection.execute(state.spot_table.update().values(
            status='STARTING', start_at=start_at, last_recovered_at=-1))
    monkeypatch.setattr(state.time, 'time', lambda: 200)
    await observe(0)
    await state.set_emergency_recovering_async(42, 0, 'controller interrupted',
                                               mock.AsyncMock())
    assert task_row(database)['status'] == 'RECOVERING'
    assert task_row(database)['start_at'] == start_at
    monkeypatch.setattr(state.time, 'time', lambda: 300)
    if recovery_path == 'strategy':
        await state.set_recovered_async(42, 0, 280, mock.AsyncMock())
    else:
        count = int(recovery_path == 'requeue')
        if count:
            await observe(count, waiting=True)
        await observe(count, running=True, started_at=280)
    row = task_row(database)
    assert row['status'] == 'RUNNING'
    assert row['start_at'] == (280 if start_at is None else start_at)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure_recovery', [False, True])
async def test_new_allocation_does_not_restore_previous_baseline(
        database, monkeypatch, failure_recovery):
    monkeypatch.setattr(state.time, 'time', lambda: 200)
    await observe(0, running=True, started_at=100)
    await state.set_emergency_recovering_async(42, 0, 'interrupted',
                                               mock.AsyncMock())
    with database.begin() as connection:
        connection.execute(state.spot_table.update().values(
            recovering_from_failure=failure_recovery))
    monkeypatch.setattr(state.time, 'time', lambda: 400)
    await observe(0, runtime_id='allocation-b', running=True, started_at=350)
    row = task_row(database)
    assert row['job_duration'] == 100
    assert row['last_recovered_at'] == 350
    assert row['recovery_count'] == int(failure_recovery)
    await observe(0, runtime_id='allocation-b', running=True, started_at=350)
    assert task_row(database)['recovery_count'] == int(failure_recovery)


@pytest.mark.asyncio
async def test_healthy_observation_throttles_metadata_writes(
        database, monkeypatch):
    monkeypatch.setattr(state.time, 'time', lambda: 200)
    await observe(0, running=True)
    before = task_row(database)['metadata']
    monkeypatch.setattr(state.time, 'time', lambda: 205)
    await observe(0, running=True)
    assert task_row(database)['metadata'] == before
    monkeypatch.setattr(state.time, 'time', lambda: 260)
    await observe(0, running=True)
    assert json.loads(task_row(
        database)['metadata'])['runtime_recovery']['last_running_at'] == 260


@pytest.mark.asyncio
async def test_provisioning_restarts_do_not_emit_recovered_after_start(
        database):
    with database.begin() as connection:
        connection.execute(state.spot_table.update().values(status='STARTING'))
    provision(1)
    await state.set_started_async(42, 0, 300, mock.AsyncMock())
    with database.connect() as connection:
        before = connection.execute(state.job_events_table.select()).all()
    await observe(1, running=True, started_at=310)
    assert task_row(database)['last_recovered_at'] == 300
    with database.connect() as connection:
        assert connection.execute(
            state.job_events_table.select()).all() == before


@pytest.mark.parametrize('status,should_relaunch,phase', [
    (job_lib.JobStatus.RUNNING, False, runtime.RuntimePhase.RUNNING),
    (job_lib.JobStatus.PENDING, False, runtime.RuntimePhase.WAITING),
    (job_lib.JobStatus.SETTING_UP, False, runtime.RuntimePhase.WAITING),
    (job_lib.JobStatus.FAILED, False, runtime.RuntimePhase.TERMINATED),
    (None, False, runtime.RuntimePhase.UNAVAILABLE),
    (job_lib.JobStatus.PENDING, True, runtime.RuntimePhase.NEEDS_REPLACEMENT),
    (None, True, runtime.RuntimePhase.NEEDS_REPLACEMENT),
])
def test_observation_phase(status, should_relaunch, phase):
    assert runtime.RuntimeObservation(
        'allocation', 0, status, should_relaunch=should_relaunch).phase == phase


@pytest.mark.asyncio
async def test_cursor_keeps_last_running_placement(database):
    assert state.get_runtime_cursor(42, 0) is None
    await observe(1, waiting=True, nodes=['node-a'])
    assert state.get_runtime_cursor(42, 0) == runtime.RuntimeCursor(
        'allocation-a', 1, 0, None)
    await observe(1, running=True, nodes=['node-a', 'node-b'])
    await observe(2, waiting=True, user_restart_count=1)
    cursor = await state.get_runtime_cursor_async(42, 0)
    assert cursor == runtime.RuntimeCursor('allocation-a', 2, 1,
                                           ['node-a', 'node-b'])
    assert cursor.baseline('allocation-a') is cursor
    assert cursor.baseline('allocation-b') == runtime.RuntimeCursor(
        'allocation-b')


@pytest.mark.asyncio
async def test_placement_is_merged_with_accepted_running_observations(database):
    with database.begin() as connection:
        connection.execute(state.job_info_table.insert().values(spot_job_id=42,
                                                                name='task'))
    infra = {'cloud': 'Slurm', 'region': 'cluster', 'zone': None}

    async def record(count, status, nodes, **kwargs):
        await state.observe_runtime_async(42,
                                          0,
                                          runtime.RuntimeObservation(
                                              'allocation-a',
                                              count,
                                              status,
                                              nodes=nodes,
                                              **kwargs),
                                          callback_func=mock.AsyncMock(),
                                          infra=infra)

    def placement():
        with database.connect() as connection:
            row = connection.execute(
                state.job_info_table.select()).mappings().one()
        return json.loads(row['node_names']), row['cloud'], row['zone']

    running = job_lib.JobStatus.RUNNING
    await record(0, running, ['node-a'])
    assert placement() == ([['node-a']], 'Slurm', None)
    await record(0, running, ['node-a'])
    await record(1, job_lib.JobStatus.PENDING, ['node-b'])
    # Stale: fewer restarts than the cursor.
    await record(0, running, ['node-old'])
    await record(1, running, ['node-b'], should_relaunch=True)
    assert placement()[0] == [['node-a']]
    await record(1, running, ['node-b'])
    assert placement()[0] == [['node-a', 'node-b']]


def test_recovery_dispatch_passes_previous_cursor(monkeypatch):
    handle = mock.Mock()
    handle.provision_runtime_metadata.has_ray = False
    owner = mock.Mock()
    owner.owns.return_value = True
    owner.get_recovery_status.return_value = None
    monkeypatch.setattr(runtime, '_runtimes', [owner])
    previous = runtime.RuntimeCursor('allocation', 2)
    runtime.get_recovery_status(handle,
                                'cluster',
                                job_id=42,
                                task_id=0,
                                task=mock.Mock(),
                                previous=previous)
    assert owner.get_recovery_status.call_args.kwargs['previous'] is previous


@pytest.mark.asyncio
async def test_replacement_observation_does_not_record_placement(database):
    await observe(1, running=True, nodes=['node-a'], should_relaunch=True)
    assert state.get_runtime_cursor(42, 0).nodes is None


def test_provisioning_observations_reach_the_managed_task(database):
    with database.begin() as connection:
        connection.execute(state.spot_table.update().values(status='STARTING'))
    target = state.provisioning_observation_target(42, 0)
    assert provision_observation.previous(target) is None
    provision_observation.report(
        target,
        runtime.RuntimeObservation('allocation-a',
                                   2,
                                   None,
                                   user_restart_count=1,
                                   recovery_reasons={2: 'exit 7'}))
    assert provision_observation.previous(target) == runtime.RuntimeCursor(
        'allocation-a', 2, 1, None)
    row = task_row(database)
    assert row['status'] == 'STARTING'
    assert row['recovery_count'] == 2
    provision_observation.report(
        None, runtime.RuntimeObservation('allocation-a', 5, None))
    assert provision_observation.previous(None) is None
    assert task_row(database)['recovery_count'] == 2
    with pytest.raises(ValueError, match='No runtime observation sink'):
        provision_observation.report({'kind': 'unknown'},
                                     runtime.RuntimeObservation('a', 1, None))


def _monitor_controller(monkeypatch, handle, hook):
    controller = controller_module.JobController.__new__(
        controller_module.JobController)
    controller._job_id = 42
    controller._pool = None
    controller._backend = mock.MagicMock()
    controller._cleanup_cluster = mock.AsyncMock()
    controller._update_live_log_links = mock.AsyncMock(return_value=True)
    controller._get_cluster_job_exit_codes = mock.AsyncMock(return_value=[7])
    controller.download_log_and_stream = mock.Mock()
    monkeypatch.setattr(runtime, 'is_registered', lambda: True)
    monkeypatch.setattr(runtime, 'get_recovery_status', hook)
    monkeypatch.setattr(runtime, 'on_before_recovery', mock.Mock())
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
    monkeypatch.setattr(controller_module.managed_job_utils, 'get_job_status',
                        mock.AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(controller_module.managed_job_utils,
                        'try_to_get_job_end_time',
                        mock.Mock(return_value=12345))
    monkeypatch.setattr(controller_module.backend_utils,
                        'refresh_cluster_status_handle',
                        mock.Mock(return_value=(None, None)))
    return controller


@pytest.mark.asyncio
async def test_forced_resume_survives_runtime_observation_error(
        database, monkeypatch):
    with database.begin() as connection:
        connection.execute(state.spot_table.update().values(status='STARTING'))
    hook = mock.Mock(side_effect=[RuntimeError('storage unavailable'), None])
    controller = _monitor_controller(monkeypatch, None, hook)
    executor = mock.MagicMock()
    executor.recover = mock.AsyncMock(side_effect=StopMonitoring())
    with pytest.raises(StopMonitoring):
        await controller._monitor_one_task_impl(
            0,
            mock.MagicMock(num_nodes=1),
            'cluster',
            executor,
            mock.MagicMock(),
            callback_func=mock.AsyncMock(),
            force_transit_to_recovering=True)
    assert hook.call_count == 2
    executor.recover.assert_awaited_once_with()
    assert task_row(database)['status'] == 'STARTING'


@pytest.mark.asyncio
async def test_restart_limit_counts_live_runtime_user_retries(
        database, monkeypatch):
    hook = mock.Mock(
        return_value=runtime.RuntimeObservation('allocation-a',
                                                0,
                                                job_lib.JobStatus.FAILED,
                                                user_restart_count=2,
                                                handles_user_retries=False))
    controller = _monitor_controller(monkeypatch, mock.MagicMock(), hook)
    set_failed = mock.AsyncMock()
    monkeypatch.setattr(state, 'set_failed_async', set_failed)
    executor = recovery_strategy.StrategyExecutor.__new__(
        recovery_strategy.StrategyExecutor)
    executor.max_restarts_on_errors = 2
    executor.recover_on_exit_codes = []
    executor.restart_cnt_on_failure = 0
    executor.runtime_restart_cnt_on_failure = 0
    executor.job_id = 42
    executor.task_id = 0
    executor.recover = mock.AsyncMock(side_effect=StopMonitoring())
    result = await controller._monitor_one_task_impl(
        0,
        mock.MagicMock(num_nodes=1),
        'cluster',
        executor,
        mock.MagicMock(),
        callback_func=mock.AsyncMock())
    assert result is False
    set_failed.assert_awaited_once()
    executor.recover.assert_not_called()
