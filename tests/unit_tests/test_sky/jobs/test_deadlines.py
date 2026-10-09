"""Unit tests for the queue_timeout task field of managed jobs.

queue_timeout is enforced by ``ControllerManager.deadline_loop``, a loop of
its own in each controller process, rather than by the per-job monitor loop:
a job coroutine can block for arbitrarily long inside the strategy executor's
``launch()`` / ``recover()``. These tests cover the config path (schema,
validation, YAML round trips for single tasks, pipelines and job groups, the
persisted task specs), the pure breach check, and the loop itself against a
real (temp SQLite) managed-jobs state DB, including a job whose coroutine
never returns from ``launch()``.
"""
import asyncio
import contextlib
import textwrap
import time
from typing import Any, Dict, List, Optional
from unittest import mock

import filelock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

from sky import dag as dag_lib
from sky import exceptions
from sky import resources as resources_lib
from sky import task as task_lib
from sky.jobs import controller as controller_module
from sky.jobs import recovery_strategy
from sky.jobs import state as managed_job_state
from sky.jobs import utils as managed_job_utils
from sky.jobs.client import sdk as jobs_sdk
from sky.server import constants as server_constants
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import dag_utils
from sky.utils import schemas

ManagedJobStatus = managed_job_state.ManagedJobStatus


@pytest.fixture
def _mock_managed_jobs_db_conn(tmp_path, monkeypatch):
    """A temporary SQLite managed-jobs DB (same as in test_controller.py)."""
    db_path = tmp_path / 'managed_jobs_testing.db'
    engine = create_engine(f'sqlite:///{db_path}')
    async_engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}',
                                       connect_args={'timeout': 30})

    @contextlib.contextmanager
    def _tmp_db_lock(_section: str):
        lock_path = tmp_path / f'.{_section}.lock'
        with filelock.FileLock(str(lock_path), timeout=10):
            yield

    monkeypatch.setattr(managed_job_state.migration_utils, 'db_lock',
                        _tmp_db_lock)
    monkeypatch.setattr(managed_job_state._db_manager, '_engine', engine)
    monkeypatch.setattr(managed_job_state._db_manager, '_engine_async',
                        async_engine)
    managed_job_state.create_table(engine)
    yield engine


async def _noop_callback(status: str) -> None:
    del status


def _create_job(num_tasks: int = 1) -> int:
    job_id = managed_job_state.set_job_info_without_job_id(name='deadline-job',
                                                           workspace='default',
                                                           entrypoint='ep',
                                                           pool=None,
                                                           pool_hash=None,
                                                           user_hash='user1')
    for task_id in range(num_tasks):
        managed_job_state.set_pending(job_id,
                                      task_id=task_id,
                                      task_name=f'task-{task_id}',
                                      resources_str='{}',
                                      metadata='{}')
    return job_id


async def _start_task(job_id: int,
                      task_id: int,
                      submitted_at: float,
                      queue_timeout_seconds: Optional[int] = None) -> None:
    """PENDING -> STARTING, writing the specs the controller writes."""
    specs: Dict[str, Any] = {
        'max_restarts_on_errors': 0,
        'recover_on_exit_codes': [],
    }
    if queue_timeout_seconds is not None:
        specs['queue_timeout_seconds'] = queue_timeout_seconds
    await managed_job_state.set_starting_async(job_id, task_id,
                                               f'run_{task_id}', submitted_at,
                                               '{}', specs, _noop_callback)


def _task_row(job_id: int, task_id: int) -> Dict[str, Any]:
    for task in managed_job_state.get_managed_job_tasks(job_id):
        if task['task_id'] == task_id:
            return task
    raise AssertionError(f'No task {task_id} for job {job_id}')


def _events(job_id: int) -> List[Dict[str, Any]]:
    return managed_job_state.get_job_events(job_id)


class _RunningJob:
    """A stand-in for a job coroutine that never finishes on its own."""

    def __init__(self) -> None:
        self.cancelled = asyncio.Event()

    async def run(self) -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


def _manager() -> controller_module.ControllerManager:
    return controller_module.ControllerManager('test-uuid')


# ---------------------------------------------------------------------------
# Schema and validation.
# ---------------------------------------------------------------------------


def _task_from_yaml(queue_timeout: Any) -> task_lib.Task:
    return task_lib.Task.from_yaml_config({
        'run': 'echo hi',
        'queue_timeout': queue_timeout,
    })


@pytest.mark.parametrize('value', ['2h', '90s', '30m', '1d', '1w', 30, '30'])
def test_queue_timeout_accepted(value):
    task = _task_from_yaml(value)
    task.validate()
    assert task.queue_timeout == value


@pytest.mark.parametrize('value', ['banana', '-1', -1, '2 hours', '1.5h', 1.5])
def test_queue_timeout_rejected_by_schema(value):
    with pytest.raises(ValueError):
        _task_from_yaml(value)


@pytest.mark.parametrize('value', [0, '0', '0s', '0h'])
def test_queue_timeout_zero_rejected(value):
    # '0' and '0s' match TIME_PATTERN_SECONDS and 0 is rejected by the
    # integer minimum, but validation must reject all of them.
    with pytest.raises(ValueError, match='queue_timeout'):
        _task_from_yaml(value).validate()


def test_queue_timeout_rejected_by_validation_without_schema():
    # A Task built directly (e.g. through the Python SDK) skips the YAML
    # schema; validate() still rejects a bad value with a clear error.
    for value in ['banana', 0, '-1', True, '1.5h', 1.5, '2 hours']:
        task = task_lib.Task(run='echo hi', queue_timeout=value)
        with pytest.raises(ValueError, match='Invalid queue_timeout'):
            task.validate()


def test_queue_timeout_unset_by_default():
    task = task_lib.Task(run='echo hi')
    task.validate()
    assert task.queue_timeout is None
    assert 'queue_timeout' not in task.to_yaml_config()


def test_task_schema_still_rejects_unknown_keys():
    # queue_timeout is a declared top-level property; a typo is not.
    with pytest.raises(ValueError):
        task_lib.Task.from_yaml_config({'run': 'echo hi', 'queue_timout': '2h'})


def test_queue_timeout_is_not_a_job_recovery_key(monkeypatch):
    # It lives on the task, not in resources.job_recovery: on the server,
    # once plugins are loaded, the job_recovery schema is strict and does not
    # know it.
    monkeypatch.setenv(constants.ENV_VAR_IS_SKYPILOT_SERVER, '1')
    monkeypatch.setattr('sky.server.plugins.plugins_loaded', lambda: True)
    with pytest.raises(ValueError):
        common_utils.validate_schema({'job_recovery': {
            'queue_timeout': '2h'
        }}, schemas.get_resources_schema(), 'Invalid resources YAML: ')


# ---------------------------------------------------------------------------
# YAML round trips: the field must survive client -> server -> controller.
# ---------------------------------------------------------------------------


def test_queue_timeout_yaml_round_trip():
    task = _task_from_yaml('2h')
    config = task.to_yaml_config()
    assert config['queue_timeout'] == '2h'
    assert task_lib.Task.from_yaml_config(config).queue_timeout == '2h'
    # Integer seconds stay integers.
    assert task_lib.Task.from_yaml_config(
        _task_from_yaml(30).to_yaml_config()).queue_timeout == 30


def test_queue_timeout_survives_job_launch_defaults_and_dag_yaml():
    task = task_lib.Task.from_yaml_config({
        'run': 'echo hi',
        'queue_timeout': '2h',
        'resources': {
            'cpus': 1,
            'job_recovery': {
                'max_restarts_on_errors': 1
            }
        }
    })
    dag = dag_utils.convert_entrypoint_to_dag(task)
    dag_utils.maybe_infer_and_fill_dag_and_task_names(dag)
    dag_utils.fill_default_config_in_dag_for_job_launch(dag)
    assert dag.tasks[0].queue_timeout == '2h'

    # The DAG crosses client -> server -> controller as YAML.
    loaded = dag_utils.load_chain_dag_from_yaml_str(
        dag_utils.dump_chain_dag_to_yaml_str(dag))
    assert loaded.tasks[0].queue_timeout == '2h'
    (loaded_resources,) = loaded.tasks[0].resources
    assert loaded_resources.job_recovery['max_restarts_on_errors'] == 1


def test_queue_timeout_per_task_in_a_pipeline():
    dag = dag_utils.load_chain_dag_from_yaml_str(
        textwrap.dedent("""\
            name: pipeline
            ---
            name: prep
            run: echo prep
            ---
            name: train
            queue_timeout: 2h
            run: echo train
            ---
            name: eval
            queue_timeout: 600
            run: echo eval
            """))
    assert [t.queue_timeout for t in dag.tasks] == [None, '2h', 600]
    reloaded = dag_utils.load_chain_dag_from_yaml_str(
        dag_utils.dump_chain_dag_to_yaml_str(dag))
    assert [t.queue_timeout for t in reloaded.tasks] == [None, '2h', 600]


def test_queue_timeout_per_task_in_a_job_group():
    dag = dag_utils.load_job_group_from_yaml_str(
        textwrap.dedent("""\
            name: group
            execution: parallel
            ---
            name: trainer
            queue_timeout: 1h
            run: echo train
            ---
            name: server
            run: echo serve
            """))
    assert [t.queue_timeout for t in dag.tasks] == ['1h', None]
    reloaded = dag_utils.load_job_group_from_yaml_str(
        dag_utils.dump_job_group_to_yaml_str(dag))
    assert [t.queue_timeout for t in reloaded.tasks] == ['1h', None]


# ---------------------------------------------------------------------------
# The persisted specs.
# ---------------------------------------------------------------------------


class _RecordingExecutor(recovery_strategy.StrategyExecutor):
    """Records the config a (plugin) strategy is handed."""

    def __init__(self, cluster_name, backend, task, *args, **kwargs):  # pylint: disable=super-init-not-called
        del cluster_name, backend, args, kwargs
        self.max_restarts_on_errors = 0
        self.recover_on_exit_codes = []
        self.dag = dag_lib.Dag()
        self.dag.add(task)
        self.received_config: Optional[dict] = None

    def set_strategy_config(self, config: dict) -> None:
        self.received_config = dict(config)


def _make_executor(monkeypatch, task: task_lib.Task):
    monkeypatch.setattr(
        recovery_strategy.registry.JOBS_RECOVERY_STRATEGY_REGISTRY, 'from_str',
        lambda _: _RecordingExecutor)
    return recovery_strategy.StrategyExecutor.make('cluster', mock.Mock(),
                                                   task, 1, 0, None, set(),
                                                   mock.Mock(), mock.Mock())


def test_specs_carry_queue_timeout_seconds(monkeypatch):
    task = task_lib.Task(run='true', queue_timeout='2h')
    task.set_resources(
        resources_lib.Resources(job_recovery={
            'strategy': 'EAGER_NEXT_REGION',
            'plugin_specific_key': 'x',
        }))
    executor = _make_executor(monkeypatch, task)
    # job_recovery is untouched by queue_timeout: plugin strategies still get
    # exactly their own keys.
    assert executor.received_config == {'plugin_specific_key': 'x'}
    specs = controller_module._build_task_specs(executor)
    assert specs['queue_timeout_seconds'] == 7200
    assert specs['max_restarts_on_errors'] == 0


def test_specs_without_queue_timeout(monkeypatch):
    executor = _make_executor(monkeypatch, task_lib.Task(run='true'))
    specs = controller_module._build_task_specs(executor)
    assert specs['queue_timeout_seconds'] is None


def test_specs_integer_queue_timeout(monkeypatch):
    executor = _make_executor(monkeypatch,
                              task_lib.Task(run='true', queue_timeout=45))
    assert controller_module._build_task_specs(
        executor)['queue_timeout_seconds'] == 45


# ---------------------------------------------------------------------------
# _deadline_breach: the pure check.
# ---------------------------------------------------------------------------

_NOW = 1_000_000.0


def _row(status: ManagedJobStatus,
         submitted_at: Optional[float] = _NOW - 100,
         start_at: Optional[float] = None) -> Dict[str, Any]:
    return {
        'status': status,
        'submitted_at': submitted_at,
        'start_at': start_at,
    }


@pytest.mark.parametrize('status', [
    ManagedJobStatus.PENDING, ManagedJobStatus.STARTING,
    ManagedJobStatus.RECOVERING
])
def test_breach_when_not_started_in_time(status):
    reason = controller_module._deadline_breach({'queue_timeout_seconds': 60},
                                                _row(status,
                                                     submitted_at=_NOW - 61),
                                                _NOW)
    assert reason is not None
    assert 'queue_timeout=1m' in reason
    assert 'waited 1m1s' in reason


def test_no_breach_before_deadline():
    assert controller_module._deadline_breach(
        {'queue_timeout_seconds': 60},
        _row(ManagedJobStatus.STARTING, submitted_at=_NOW - 59), _NOW) is None


def test_breach_exactly_at_deadline():
    assert controller_module._deadline_breach({'queue_timeout_seconds': 60},
                                              _row(ManagedJobStatus.STARTING,
                                                   submitted_at=_NOW - 60),
                                              _NOW) is not None


def test_no_breach_once_started():
    # Started once: queue_timeout no longer applies, even while RECOVERING
    # long after the deadline.
    for status in (ManagedJobStatus.RUNNING, ManagedJobStatus.RECOVERING,
                   ManagedJobStatus.PENDING):
        assert controller_module._deadline_breach(
            {'queue_timeout_seconds': 60},
            _row(status, submitted_at=_NOW - 10_000,
                 start_at=_NOW - 9_000), _NOW) is None


@pytest.mark.parametrize('status', [
    ManagedJobStatus.SUCCEEDED, ManagedJobStatus.FAILED,
    ManagedJobStatus.CANCELLED, ManagedJobStatus.FAILED_NO_RESOURCE,
    ManagedJobStatus.CANCELLING
])
def test_no_breach_for_terminal_or_cancelling(status):
    assert controller_module._deadline_breach(
        {'queue_timeout_seconds': 60}, _row(
            status, submitted_at=_NOW - 10_000), _NOW) is None


def test_no_breach_without_config_or_submission():
    assert controller_module._deadline_breach(
        {}, _row(ManagedJobStatus.STARTING, submitted_at=0), _NOW) is None
    assert controller_module._deadline_breach(
        {'queue_timeout_seconds': None},
        _row(ManagedJobStatus.STARTING, submitted_at=0), _NOW) is None
    # PENDING with no submitted_at: never claimed by a controller.
    assert controller_module._deadline_breach(
        {'queue_timeout_seconds': 60},
        _row(ManagedJobStatus.PENDING, submitted_at=None), _NOW) is None


def test_format_seconds():
    assert controller_module._format_seconds(7200) == '2h'
    assert controller_module._format_seconds(5430) == '1h30m30s'
    assert controller_module._format_seconds(90061) == '1d1h1m1s'
    assert controller_module._format_seconds(0) == '0s'
    assert controller_module._format_seconds(-5) == '0s'


# ---------------------------------------------------------------------------
# The deadline loop, against a real state DB.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_timeout_cancels_job_blocked_in_launch(
        _mock_managed_jobs_db_conn, monkeypatch):
    """The core case: the job's coroutine is stuck in launch() forever (as a
    first launch retries until it gets capacity), and the deadline loop
    still cancels it through the normal cancellation path."""
    job_id = _create_job()
    submitted_at = time.time() - 120
    # A real JobController coroutine, with the strategy executor's launch()
    # never returning.
    launch_entered = asyncio.Event()

    async def _never_returning_launch():
        launch_entered.set()
        await asyncio.Event().wait()

    executor = mock.MagicMock()
    executor.launch = _never_returning_launch
    executor.max_restarts_on_errors = 0
    executor.recover_on_exit_codes = []
    executor.task_specs.return_value = {}

    controller = controller_module.JobController.__new__(
        controller_module.JobController)
    controller._job_id = job_id
    controller._pool = None
    controller._backend = mock.MagicMock()
    controller._backend.run_timestamp = 'sky-2024-01-01-00-00-00-000000'
    controller.starting = set()
    controller.starting_lock = asyncio.Lock()
    controller.starting_signal = mock.MagicMock()
    task = mock.MagicMock()
    task.name = 'task-0'
    task.metadata = {}
    task.run = 'echo hi'
    task.event_callback = None
    task.envs = {constants.TASK_ID_ENV_VAR: 'test-task-id'}
    task.resources = None
    task.queue_timeout = '1m'
    # As in StrategyExecutor.__init__: the executor's DAG holds its one task.
    executor.dag.tasks = [task]

    monkeypatch.setattr(controller_module, '_add_k8s_annotations',
                        lambda *a: None)
    monkeypatch.setattr(managed_job_state, 'get_file_mounts_blob_id',
                        lambda _: None)
    monkeypatch.setattr(recovery_strategy.StrategyExecutor, 'make',
                        mock.MagicMock(return_value=executor))
    monkeypatch.setattr(controller_module.backend_utils,
                        'get_timestamp_from_run_timestamp',
                        lambda _: submitted_at)
    monkeypatch.setattr(controller_module.backend_utils,
                        'get_task_resources_str', lambda *a, **k: '-')

    manager = _manager()
    job_task = asyncio.create_task(controller._run_one_task(0, task))
    manager.job_tasks[job_id] = job_task
    await asyncio.wait_for(launch_entered.wait(), timeout=10)

    # The task was claimed (STARTING) with the deadline persisted in specs.
    assert _task_row(job_id, 0)['status'] == ManagedJobStatus.STARTING
    assert managed_job_state.get_task_specs(job_id,
                                            0)['queue_timeout_seconds'] == 60

    await manager._check_deadlines(time.time())

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(job_task, timeout=10)
    # Handed to run_job_loop's cancellation path as a non-graceful cancel.
    assert manager._cancel_info[job_id] == (False, None)
    row = _task_row(job_id, 0)
    assert row['failure_reason'].startswith(
        'Cancelled: task did not start within queue_timeout=1m (waited 2m')
    reasons = [e['reason'] for e in _events(job_id)]
    assert any(
        'queue_timeout=1m' in r and
        r.startswith(managed_job_state.CANCEL_REQUESTED_EVENT_REASON_PREFIX)
        for r in reasons), reasons
    # The queue's details column shows it.
    assert 'queue_timeout=1m' in managed_job_state.get_cancel_request_reasons(
        [job_id])[job_id]


@pytest.mark.asyncio
async def test_queue_timeout_full_cancel_path_ends_cancelled(
        _mock_managed_jobs_db_conn, monkeypatch):
    """Through run_job_loop: the job ends CANCELLED via the same path as a
    user cancel (CANCELLING, cleanup, CANCELLED, schedule DONE)."""
    job_id = _create_job()
    await _start_task(job_id, 0, time.time() - 120, queue_timeout_seconds=60)
    running = _RunningJob()

    job_controller = mock.MagicMock()
    job_controller.load_dag = mock.AsyncMock()
    job_controller.run = running.run
    monkeypatch.setattr(controller_module, 'JobController',
                        mock.MagicMock(return_value=job_controller))
    monkeypatch.setattr(controller_module.context, 'get',
                        mock.MagicMock(return_value=mock.MagicMock()))
    monkeypatch.setattr(controller_module.file_content_utils,
                        'get_job_env_content',
                        mock.MagicMock(return_value=None))
    monkeypatch.setattr(controller_module.usage_lib,
                        'install_fresh_messages_for_current_context',
                        mock.MagicMock())
    dag = mock.MagicMock()
    dag.tasks = [mock.MagicMock(event_callback=None)]
    monkeypatch.setattr(controller_module, '_get_dag',
                        mock.MagicMock(return_value=dag))

    manager = _manager()
    manager._cleanup = mock.AsyncMock()
    manager._download_logs_for_cancelled_job = mock.AsyncMock()

    loop_task = asyncio.create_task(
        manager.run_job_loop.__wrapped__(manager, job_id, 'job.log'))
    for _ in range(100):
        if job_id in manager.job_tasks:
            break
        await asyncio.sleep(0.01)
    assert job_id in manager.job_tasks

    await manager._check_deadlines(time.time())
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(loop_task, timeout=10)

    assert running.cancelled.is_set()
    manager._cleanup.assert_awaited_once_with(job_id,
                                              pool=None,
                                              graceful=False,
                                              graceful_timeout=None)
    manager._download_logs_for_cancelled_job.assert_awaited_once()
    row = _task_row(job_id, 0)
    assert row['status'] == ManagedJobStatus.CANCELLED
    assert 'queue_timeout=1m' in row['failure_reason']
    assert (managed_job_state.get_job_schedule_state(job_id) ==
            managed_job_state.ManagedJobScheduleState.DONE)
    statuses = [e['new_status'] for e in _events(job_id)]
    assert ManagedJobStatus.CANCELLING in statuses
    assert ManagedJobStatus.CANCELLED in statuses
    assert job_id not in manager.job_tasks


@pytest.mark.asyncio
async def test_no_config_is_a_no_op(_mock_managed_jobs_db_conn):
    """Backward compat: tasks without queue_timeout are never touched, and
    the loop writes nothing to the DB."""
    job_id = _create_job()
    await _start_task(job_id, 0, time.time() - 10**6)
    manager = _manager()
    running = _RunningJob()
    job_task = asyncio.create_task(running.run())
    manager.job_tasks[job_id] = job_task
    events_before = _events(job_id)
    row_before = _task_row(job_id, 0)

    with mock.patch.object(managed_job_state,
                           'set_deadline_exceeded_async') as mock_set:
        await manager._check_deadlines(time.time())
        mock_set.assert_not_called()

    await asyncio.sleep(0)
    assert not job_task.done()
    assert job_id not in manager._cancel_info
    assert _events(job_id) == events_before
    row_after = _task_row(job_id, 0)
    assert row_after['failure_reason'] == row_before['failure_reason']
    assert row_after['status'] == row_before['status']
    job_task.cancel()


@pytest.mark.asyncio
async def test_specs_written_before_this_change_are_a_no_op(
        _mock_managed_jobs_db_conn):
    """A task whose specs predate queue_timeout_seconds (an upgraded
    controller resuming an old job) has no deadline."""
    job_id = _create_job()
    await managed_job_state.set_starting_async(job_id, 0, 'run_0',
                                               time.time() - 10**6, '{}',
                                               {'max_restarts_on_errors': 0},
                                               _noop_callback)
    manager = _manager()
    job_task = asyncio.create_task(_RunningJob().run())
    manager.job_tasks[job_id] = job_task
    await manager._check_deadlines(time.time())
    assert not job_task.done()
    assert job_id not in manager._cancel_info
    job_task.cancel()


@pytest.mark.asyncio
async def test_within_deadline_not_cancelled(_mock_managed_jobs_db_conn):
    job_id = _create_job()
    await _start_task(job_id, 0, time.time() - 30, queue_timeout_seconds=3600)
    manager = _manager()
    job_task = asyncio.create_task(_RunningJob().run())
    manager.job_tasks[job_id] = job_task
    await manager._check_deadlines(time.time())
    assert not job_task.done()
    assert _task_row(job_id, 0)['failure_reason'] is None
    job_task.cancel()


@pytest.mark.asyncio
async def test_started_task_not_cancelled_even_while_recovering(
        _mock_managed_jobs_db_conn):
    """queue_timeout stops applying once the task has started; a later
    recovery (which never resets start_at) does not bring it back."""
    job_id = _create_job()
    submitted_at = time.time() - 10_000
    await _start_task(job_id, 0, submitted_at, queue_timeout_seconds=60)
    await managed_job_state.set_started_async(job_id, 0, submitted_at + 30,
                                              _noop_callback)
    await managed_job_state.set_recovering_async(job_id, 0, False,
                                                 _noop_callback)
    await managed_job_state.set_recovered_async(job_id, 0,
                                                time.time() - 5, _noop_callback)
    await managed_job_state.set_recovering_async(job_id, 0, False,
                                                 _noop_callback)
    row = _task_row(job_id, 0)
    assert row['status'] == ManagedJobStatus.RECOVERING
    # start_at is the first start; recovery only moved last_recovered_at.
    assert row['start_at'] == pytest.approx(submitted_at + 30)

    manager = _manager()
    job_task = asyncio.create_task(_RunningJob().run())
    manager.job_tasks[job_id] = job_task
    await manager._check_deadlines(time.time())
    assert not job_task.done()
    job_task.cancel()


@pytest.mark.asyncio
async def test_backoff_pending_task_is_still_on_the_clock(
        _mock_managed_jobs_db_conn):
    """A launch in retry backoff sets the task back to PENDING; it keeps its
    submitted_at, so queue_timeout keeps counting."""
    job_id = _create_job()
    await _start_task(job_id, 0, time.time() - 120, queue_timeout_seconds=60)
    await managed_job_state.set_backoff_pending_async(job_id, 0)
    assert _task_row(job_id, 0)['status'] == ManagedJobStatus.PENDING
    manager = _manager()
    job_task = asyncio.create_task(_RunningJob().run())
    manager.job_tasks[job_id] = job_task
    await manager._check_deadlines(time.time())
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(job_task, timeout=10)
    assert 'queue_timeout' in _task_row(job_id, 0)['failure_reason']


@pytest.mark.asyncio
async def test_terminal_and_cancelling_jobs_not_touched(
        _mock_managed_jobs_db_conn):
    # Job A: already CANCELLING (e.g. the user cancelled it) and overdue.
    job_a = _create_job()
    await _start_task(job_a, 0, time.time() - 10**4, queue_timeout_seconds=60)
    await managed_job_state.set_cancelling_async(job_a, _noop_callback)
    # Job B: its only task already ended.
    job_b = _create_job()
    await _start_task(job_b, 0, time.time() - 10**4, queue_timeout_seconds=60)
    await managed_job_state.set_failed_async(
        job_b,
        0,
        failure_type=ManagedJobStatus.FAILED_NO_RESOURCE,
        failure_reason='no resources',
        callback_func=_noop_callback)

    manager = _manager()
    tasks = {}
    for job_id in (job_a, job_b):
        tasks[job_id] = asyncio.create_task(_RunningJob().run())
        manager.job_tasks[job_id] = tasks[job_id]
    events_before = {j: _events(j) for j in (job_a, job_b)}

    await manager._check_deadlines(time.time())

    await asyncio.sleep(0)
    for job_id, job_task in tasks.items():
        assert not job_task.done(), job_id
        assert job_id not in manager._cancel_info
        assert _events(job_id) == events_before[job_id]
        job_task.cancel()
    assert _task_row(job_a, 0)['failure_reason'] is None
    assert _task_row(job_b, 0)['failure_reason'] == 'no resources'


@pytest.mark.asyncio
async def test_set_deadline_exceeded_skips_cancelling_job(
        _mock_managed_jobs_db_conn):
    """The state write itself refuses a job that is already CANCELLING, so a
    user cancel landing between the check and the write wins."""
    job_id = _create_job()
    await _start_task(job_id, 0, time.time() - 120, queue_timeout_seconds=60)
    await managed_job_state.set_cancelling_async(job_id, _noop_callback)
    recorded = await managed_job_state.set_deadline_exceeded_async(
        job_id, 0, failure_reason='Cancelled: x', event_reason='y')
    assert recorded is False
    assert _task_row(job_id, 0)['failure_reason'] is None
    assert all(e['reason'] != 'y' for e in _events(job_id))


@pytest.mark.asyncio
async def test_pipeline_uses_the_current_task_clock(_mock_managed_jobs_db_conn):
    """Task 0 SUCCEEDED after a long run; task 1 was just submitted. Only
    task 1's own submitted_at counts, so the job is not cancelled."""
    job_id = _create_job(num_tasks=2)
    now = time.time()
    await _start_task(job_id, 0, now - 10_000, queue_timeout_seconds=60)
    await managed_job_state.set_started_async(job_id, 0, now - 9_990,
                                              _noop_callback)
    await managed_job_state.set_succeeded_async(job_id, 0, now - 20,
                                                _noop_callback)
    await _start_task(job_id, 1, now - 20, queue_timeout_seconds=60)

    manager = _manager()
    job_task = asyncio.create_task(_RunningJob().run())
    manager.job_tasks[job_id] = job_task
    await manager._check_deadlines(now)
    assert not job_task.done()

    # 41s later task 1 is past its own 60s deadline: the job is cancelled,
    # and the reason is recorded on task 1, not task 0.
    await manager._check_deadlines(now + 41)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(job_task, timeout=10)
    assert _task_row(job_id, 0)['failure_reason'] is None
    assert 'queue_timeout=1m' in _task_row(job_id, 1)['failure_reason']


@pytest.mark.asyncio
async def test_pending_later_pipeline_task_has_no_clock(
        _mock_managed_jobs_db_conn):
    """A pipeline task that has not been reached yet (PENDING, no specs, no
    submitted_at) is not on the clock."""
    job_id = _create_job(num_tasks=2)
    now = time.time()
    await _start_task(job_id, 0, now - 30, queue_timeout_seconds=60)
    manager = _manager()
    job_task = asyncio.create_task(_RunningJob().run())
    manager.job_tasks[job_id] = job_task
    await manager._check_deadlines(now + 10**6 - 100)
    # Task 0 is overdue at this time (so the job is cancelled because of it),
    # but task 1 never contributed: the reason is on task 0.
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(job_task, timeout=10)
    assert 'queue_timeout' in _task_row(job_id, 0)['failure_reason']
    assert _task_row(job_id, 1)['failure_reason'] is None


@pytest.mark.asyncio
async def test_job_group_any_task_breaching_cancels_the_job(
        _mock_managed_jobs_db_conn):
    job_id = _create_job(num_tasks=2)
    now = time.time()
    await _start_task(job_id, 0, now - 30, queue_timeout_seconds=3600)
    await managed_job_state.set_started_async(job_id, 0, now - 20,
                                              _noop_callback)
    await _start_task(job_id, 1, now - 120, queue_timeout_seconds=60)
    manager = _manager()
    job_task = asyncio.create_task(_RunningJob().run())
    manager.job_tasks[job_id] = job_task
    await manager._check_deadlines(now)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(job_task, timeout=10)
    assert _task_row(job_id, 0)['failure_reason'] is None
    assert 'queue_timeout=1m' in _task_row(job_id, 1)['failure_reason']


@pytest.mark.asyncio
async def test_cancelled_once_and_user_cancel_race(_mock_managed_jobs_db_conn):
    """A job is cancelled for its deadline at most once, and a pending user
    cancel signal takes precedence (its graceful settings are kept)."""
    # Job A: deadline cancel, then more ticks.
    job_a = _create_job()
    await _start_task(job_a, 0, time.time() - 120, queue_timeout_seconds=60)
    # Job B: overdue, but a user cancel signal has already been consumed by
    # cancel_job() and is waiting for run_job_loop.
    job_b = _create_job()
    await _start_task(job_b, 0, time.time() - 120, queue_timeout_seconds=60)

    manager = _manager()
    job_a_task = mock.MagicMock()
    job_a_task.done.return_value = False
    job_b_task = mock.MagicMock()
    job_b_task.done.return_value = False
    manager.job_tasks[job_a] = job_a_task
    manager.job_tasks[job_b] = job_b_task
    manager._cancel_info[job_b] = (True, 60)

    await manager._check_deadlines(time.time())
    await manager._check_deadlines(time.time())
    await manager._check_deadlines(time.time())

    assert job_a_task.cancel.call_count == 1
    assert manager._cancel_info[job_a] == (False, None)
    reasons = [e['reason'] for e in _events(job_a)]
    assert sum('queue_timeout' in r for r in reasons) == 1

    job_b_task.cancel.assert_not_called()
    assert manager._cancel_info[job_b] == (True, 60)
    assert _task_row(job_b, 0)['failure_reason'] is None


@pytest.mark.asyncio
async def test_one_job_failing_does_not_affect_others(
        _mock_managed_jobs_db_conn, monkeypatch):
    job_a = _create_job()
    await _start_task(job_a, 0, time.time() - 120, queue_timeout_seconds=60)
    job_b = _create_job()
    await _start_task(job_b, 0, time.time() - 120, queue_timeout_seconds=60)
    manager = _manager()
    tasks = {}
    for job_id in (job_a, job_b):
        tasks[job_id] = asyncio.create_task(_RunningJob().run())
        manager.job_tasks[job_id] = tasks[job_id]

    real = managed_job_state.set_deadline_exceeded_async

    async def _flaky(job_id, *args, **kwargs):
        if job_id == job_a:
            raise RuntimeError('boom')
        return await real(job_id, *args, **kwargs)

    monkeypatch.setattr(managed_job_state, 'set_deadline_exceeded_async',
                        _flaky)
    await manager._check_deadlines(time.time())
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(tasks[job_b], timeout=10)
    assert not tasks[job_a].done()
    tasks[job_a].cancel()


@pytest.mark.asyncio
async def test_deadline_loop_survives_errors(monkeypatch):
    """The loop logs and keeps going when a tick fails."""
    manager = _manager()
    ticks: List[float] = []

    async def _check(now: float) -> None:
        ticks.append(now)
        if len(ticks) == 1:
            raise RuntimeError('db down')
        if len(ticks) == 3:
            raise asyncio.CancelledError()

    sleeps: List[float] = []

    async def _fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(manager, '_check_deadlines', _check)
    monkeypatch.setattr(asyncio, 'sleep', _fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        await manager.deadline_loop()
    assert len(ticks) == 3
    assert sleeps == [controller_module._DEADLINE_CHECK_INTERVAL_SECONDS] * 2


@pytest.mark.asyncio
async def test_overdue_job_adopted_after_restart_is_cancelled_first_tick(
        _mock_managed_jobs_db_conn):
    """Deadlines come from the DB, so a fresh controller process (after a
    restart or failover) cancels an already-overdue job on its first tick
    with no other state."""
    job_id = _create_job()
    await _start_task(job_id, 0, time.time() - 7200, queue_timeout_seconds=3600)
    fresh_manager = _manager()
    job_task = asyncio.create_task(_RunningJob().run())
    fresh_manager.job_tasks[job_id] = job_task
    await fresh_manager._check_deadlines(time.time())
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(job_task, timeout=10)


def test_main_starts_the_deadline_loop(monkeypatch):
    started = []

    class _FakeManager:

        def __init__(self, controller_uuid):
            del controller_uuid

        async def cancel_job(self):
            started.append('cancel_job')

        async def monitor_loop(self):
            started.append('monitor_loop')

        async def deadline_loop(self):
            started.append('deadline_loop')

    monkeypatch.setattr(controller_module, 'ControllerManager', _FakeManager)
    monkeypatch.setattr(controller_module.plugins, 'load_plugins',
                        lambda *a: None)
    monkeypatch.setattr(controller_module.context_utils, 'hijack_sys_attrs',
                        lambda: None)
    monkeypatch.setattr(controller_module.os, 'makedirs', lambda *a, **k: None)
    monkeypatch.setattr(controller_module.threading, 'Thread', mock.MagicMock())
    asyncio.run(controller_module.main('uuid'))
    assert sorted(started) == ['cancel_job', 'deadline_loop', 'monitor_loop']


# ---------------------------------------------------------------------------
# Client-side version gate.
# ---------------------------------------------------------------------------


def _unwrap(fn):
    while hasattr(fn, '__wrapped__'):
        fn = fn.__wrapped__
    return fn


def _queue_timeout_task() -> task_lib.Task:
    return task_lib.Task(run='echo hi', queue_timeout='2h')


def test_launch_queue_timeout_refuses_an_old_server():
    raw_launch = _unwrap(jobs_sdk.launch)
    too_old = server_constants.MIN_JOBS_QUEUE_TIMEOUT_API_VERSION - 1
    with mock.patch.object(jobs_sdk.versions,
                           'get_remote_api_version',
                           return_value=too_old), \
         mock.patch.object(jobs_sdk.server_common,
                           'make_authenticated_request') as mock_request:
        with pytest.raises(exceptions.NotSupportedError, match='queue_timeout'):
            raw_launch(_queue_timeout_task())
        mock_request.assert_not_called()


def test_uses_queue_timeout_detection():
    assert jobs_sdk._uses_queue_timeout(
        dag_utils.convert_entrypoint_to_dag(_queue_timeout_task()))
    plain = task_lib.Task(run='echo hi')
    plain.set_resources(resources_lib.Resources(job_recovery='FAILOVER'))
    assert not jobs_sdk._uses_queue_timeout(
        dag_utils.convert_entrypoint_to_dag(plain))
    assert not jobs_sdk._uses_queue_timeout(
        dag_utils.convert_entrypoint_to_dag(task_lib.Task(run='echo hi')))
    # Any task of a pipeline setting it counts.
    with dag_lib.Dag() as dag:
        first = task_lib.Task(run='echo a')
        second = task_lib.Task(run='echo b', queue_timeout=60)
        first >> second  # pylint: disable=pointless-statement
    assert jobs_sdk._uses_queue_timeout(dag)


def test_cancel_request_event_reason_format():
    """The deadline cancel is recorded as an attributed cancel request so
    the queue's details column picks it up."""
    reason = managed_job_utils.CancelRequestInfo(
        note='by the jobs controller: x').event_reason()
    assert reason == (
        f'{managed_job_state.CANCEL_REQUESTED_EVENT_REASON_PREFIX}'
        ' by the jobs controller: x')
