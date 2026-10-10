"""Unit tests for job.wait_for_scheduling_timeout of managed jobs.

The task YAML's top-level ``job:`` block holds managed-job lifecycle
settings; ``wait_for_scheduling_timeout`` cancels a job whose task has not
started running within that long of being submitted. It is enforced by
``ControllerManager.deadline_loop``, a loop of its own in each controller
process, rather than by the per-job monitor loop: a job coroutine can block
for arbitrarily long inside the strategy executor's ``launch()`` /
``recover()``. These tests cover the config path (the ``job:`` schema,
validation, YAML round trips for single tasks, pipelines and job groups, the
pipeline header detection, the persisted task specs), the pure breach check,
and the loop itself against a real (temp SQLite) managed-jobs state DB,
including a job whose coroutine never returns from ``launch()``, the races
with the task starting and with a pending user cancel, and the client
dropping the ``job:`` block for an older server.
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
from sky.client import sdk as client_sdk
from sky.jobs import constants as jobs_constants
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
from sky.utils import yaml_utils

ManagedJobStatus = managed_job_state.ManagedJobStatus
_MIN_VERSION = (
    server_constants.MIN_JOBS_WAIT_FOR_SCHEDULING_TIMEOUT_API_VERSION)
_KIND = managed_job_state.DeadlineKind.WAIT_FOR_SCHEDULING_TIMEOUT

_FIELD = 'wait_for_scheduling_timeout'
# The task specs key the controller persists the timeout under, in seconds.
_SPECS_KEY = 'wait_for_scheduling_timeout_seconds'
# The reason recorded for a 1m timeout names the field as written in YAML.
_REASON_1M = 'job.wait_for_scheduling_timeout=1m'


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


@pytest.fixture(autouse=True)
def _signal_dir(tmp_path, monkeypatch):
    """A private cancel-signal directory (where `sky jobs cancel` writes)."""
    signal_dir = tmp_path / 'signals'
    signal_dir.mkdir()
    monkeypatch.setattr(jobs_constants, 'CONSOLIDATED_SIGNAL_PATH',
                        str(signal_dir))
    return signal_dir


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
                      timeout_seconds: Optional[int] = None) -> None:
    """PENDING -> STARTING, writing the specs the controller writes.

    ``timeout_seconds`` is the persisted job.wait_for_scheduling_timeout.
    """
    specs: Dict[str, Any] = {
        'max_restarts_on_errors': 0,
        'recover_on_exit_codes': [],
    }
    if timeout_seconds is not None:
        specs[_SPECS_KEY] = timeout_seconds
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


def _timeout_of(task: task_lib.Task) -> Any:
    return task.job.get(_FIELD)


# ---------------------------------------------------------------------------
# The job: block's schema and validation.
# ---------------------------------------------------------------------------


def _task_from_yaml(wait_for_scheduling_timeout: Any) -> task_lib.Task:
    return task_lib.Task.from_yaml_config({
        'run': 'echo hi',
        'job': {
            _FIELD: wait_for_scheduling_timeout
        },
    })


@contextlib.contextmanager
def _strict_server_schemas(monkeypatch):
    """As on an API server after plugins are loaded: unknown keys rejected
    wherever the schemas allow them on the client."""
    monkeypatch.setenv(constants.ENV_VAR_IS_SKYPILOT_SERVER, '1')
    monkeypatch.setattr('sky.server.plugins.plugins_loaded', lambda: True)
    yield


@pytest.mark.parametrize('value', ['2h', '90s', '30m', '1d', '1w', 30, '30'])
def test_wait_for_scheduling_timeout_accepted(value):
    task = _task_from_yaml(value)
    task.validate()
    assert task.job == {_FIELD: value}


@pytest.mark.parametrize('value', ['banana', '-1', -1, '2 hours', '1.5h', 1.5])
def test_wait_for_scheduling_timeout_rejected_by_schema(value):
    with pytest.raises(ValueError):
        _task_from_yaml(value)


@pytest.mark.parametrize('value', [0, '0', '0s', '0h'])
def test_wait_for_scheduling_timeout_zero_rejected(value):
    # '0' and '0s' match TIME_PATTERN_SECONDS and 0 is rejected by the
    # integer minimum, but validation must reject all of them.
    with pytest.raises(ValueError, match='job.wait_for_scheduling_timeout'):
        _task_from_yaml(value).validate()


def test_wait_for_scheduling_timeout_rejected_by_validation_without_schema():
    # A Task built directly (e.g. through the Python SDK) skips the YAML
    # schema; validate() still rejects a bad value with a clear error.
    for value in ['banana', 0, '-1', True, '1.5h', 1.5, '2 hours']:
        task = task_lib.Task(run='echo hi', job={_FIELD: value})
        with pytest.raises(ValueError, match='job'):
            task.validate()


@pytest.mark.parametrize('strict', [False, True])
def test_job_block_rejects_unknown_keys(monkeypatch, strict):
    # Strict on the client as well as on the server: a setting that this
    # version does not know is an error, not silently ignored.
    with contextlib.ExitStack() as stack:
        if strict:
            stack.enter_context(_strict_server_schemas(monkeypatch))
        with pytest.raises(ValueError, match='wait_for_scheduling_timeout'):
            # The error suggests the closest known key.
            task_lib.Task.from_yaml_config({
                'run': 'echo hi',
                'job': {
                    'wait_for_scheduling_timout': '2h'
                },
            })
        with pytest.raises(ValueError, match='max_duration'):
            task_lib.Task.from_yaml_config({
                'run': 'echo hi',
                'job': {
                    'max_duration': '2h'
                },
            })


def test_job_block_rejects_unknown_keys_set_through_the_sdk():
    task = task_lib.Task(run='echo hi', job={'max_duration': '5h'})
    with pytest.raises(ValueError, match='max_duration'):
        task.validate()


@pytest.mark.parametrize('value', ['2h', 7200, ['2h'], True])
def test_job_block_must_be_an_object(value):
    with pytest.raises(ValueError):
        task_lib.Task.from_yaml_config({'run': 'echo hi', 'job': value})
    # And through the Python SDK, at construction, so `task.job` is always a
    # dict.
    with pytest.raises(ValueError, match='Invalid job section'):
        task_lib.Task(run='echo hi', job=value)


@pytest.mark.parametrize('job', [None, {}])
def test_empty_job_block(job):
    # `job:` with nothing under it (YAML null) or `job: {}` sets nothing.
    task = task_lib.Task.from_yaml_config({'run': 'echo hi', 'job': job})
    task.validate()
    assert task.job == {}
    assert 'job' not in task.to_yaml_config()


def test_job_block_unset_by_default():
    task = task_lib.Task(run='echo hi')
    task.validate()
    assert task.job == {}
    assert 'job' not in task.to_yaml_config()


def test_old_top_level_name_is_not_accepted():
    # No alias: the field only ever lived under job:.
    with pytest.raises(ValueError):
        task_lib.Task.from_yaml_config({
            'run': 'echo hi',
            'queue_timeout': '2h'
        })
    with pytest.raises(ValueError):
        task_lib.Task.from_yaml_config({'run': 'echo hi', _FIELD: '2h'})


def test_wait_for_scheduling_timeout_is_not_a_job_recovery_key(monkeypatch):
    # It lives in the task's job: block, not in resources.job_recovery: on
    # the server, once plugins are loaded, the job_recovery schema is strict
    # and does not know it.
    with _strict_server_schemas(monkeypatch):
        with pytest.raises(ValueError):
            common_utils.validate_schema({'job_recovery': {
                _FIELD: '2h'
            }}, schemas.get_resources_schema(), 'Invalid resources YAML: ')


# ---------------------------------------------------------------------------
# YAML round trips: the block must survive client -> server -> controller.
# ---------------------------------------------------------------------------


def test_job_block_yaml_round_trip():
    task = _task_from_yaml('2h')
    config = task.to_yaml_config()
    assert config['job'] == {_FIELD: '2h'}
    assert task_lib.Task.from_yaml_config(config).job == {_FIELD: '2h'}
    # Integer seconds stay integers.
    assert _timeout_of(
        task_lib.Task.from_yaml_config(
            _task_from_yaml(30).to_yaml_config())) == 30
    # Set through the Python SDK.
    sdk_task = task_lib.Task(run='echo hi', job={_FIELD: '90s'})
    assert task_lib.Task.from_yaml_config(sdk_task.to_yaml_config()).job == {
        _FIELD: '90s'
    }


def test_job_block_survives_job_launch_defaults_and_dag_yaml():
    task = task_lib.Task.from_yaml_config({
        'run': 'echo hi',
        'job': {
            _FIELD: '2h'
        },
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
    assert _timeout_of(dag.tasks[0]) == '2h'

    # The DAG crosses client -> server -> controller as YAML.
    loaded = dag_utils.load_chain_dag_from_yaml_str(
        dag_utils.dump_chain_dag_to_yaml_str(dag))
    assert loaded.tasks[0].job == {_FIELD: '2h'}
    (loaded_resources,) = loaded.tasks[0].resources
    assert loaded_resources.job_recovery['max_restarts_on_errors'] == 1


def test_job_block_per_task_in_a_pipeline():
    dag = dag_utils.load_chain_dag_from_yaml_str(
        textwrap.dedent("""\
            name: pipeline
            ---
            name: prep
            run: echo prep
            ---
            name: train
            job:
              wait_for_scheduling_timeout: 2h
            run: echo train
            ---
            name: eval
            job:
              wait_for_scheduling_timeout: 600
            run: echo eval
            """))
    assert dag.name == 'pipeline'
    assert [t.name for t in dag.tasks] == ['prep', 'train', 'eval']
    assert [_timeout_of(t) for t in dag.tasks] == [None, '2h', 600]
    reloaded = dag_utils.load_chain_dag_from_yaml_str(
        dag_utils.dump_chain_dag_to_yaml_str(dag))
    assert [_timeout_of(t) for t in reloaded.tasks] == [None, '2h', 600]


def test_pipeline_first_document_with_name_and_job_is_a_task():
    """A pipeline's header is a first document with only `name` (and
    optionally `execution`). A first document with `name` + `job:` is a task,
    so its job: block is not lost as header metadata."""
    dag = dag_utils.load_chain_dag_from_yaml_str(
        textwrap.dedent("""\
            name: train
            job:
              wait_for_scheduling_timeout: 2h
            ---
            name: eval
            run: echo eval
            """))
    assert [t.name for t in dag.tasks] == ['train', 'eval']
    assert [_timeout_of(t) for t in dag.tasks] == ['2h', None]
    # A single document with name + job is a one-task DAG named after it.
    single = dag_utils.load_chain_dag_from_yaml_str(
        textwrap.dedent("""\
            name: train
            job:
              wait_for_scheduling_timeout: 2h
            """))
    assert single.name == 'train'
    assert [_timeout_of(t) for t in single.tasks] == ['2h']


def test_job_block_is_not_a_job_group_header_field():
    # job: is per task (each job of a group sets its own); it is not one of
    # the group header's fields.
    assert 'job' not in dag_utils._JOB_GROUP_HEADER_FIELDS


def test_job_block_per_task_in_a_job_group():
    dag = dag_utils.load_job_group_from_yaml_str(
        textwrap.dedent("""\
            name: group
            execution: parallel
            ---
            name: trainer
            job:
              wait_for_scheduling_timeout: 1h
            run: echo train
            ---
            name: server
            run: echo serve
            """))
    assert [_timeout_of(t) for t in dag.tasks] == ['1h', None]
    reloaded = dag_utils.load_job_group_from_yaml_str(
        dag_utils.dump_job_group_to_yaml_str(dag))
    assert [_timeout_of(t) for t in reloaded.tasks] == ['1h', None]


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


def test_specs_carry_wait_for_scheduling_timeout_seconds(monkeypatch):
    task = task_lib.Task(run='true', job={_FIELD: '2h'})
    task.set_resources(
        resources_lib.Resources(job_recovery={
            'strategy': 'EAGER_NEXT_REGION',
            'plugin_specific_key': 'x',
        }))
    executor = _make_executor(monkeypatch, task)
    # job_recovery is untouched by the job: block: plugin strategies still
    # get exactly their own keys.
    assert executor.received_config == {'plugin_specific_key': 'x'}
    specs = controller_module._build_task_specs(executor)
    assert specs[_SPECS_KEY] == 7200
    assert specs['max_restarts_on_errors'] == 0


def test_specs_without_job_block(monkeypatch):
    executor = _make_executor(monkeypatch, task_lib.Task(run='true'))
    specs = controller_module._build_task_specs(executor)
    assert specs[_SPECS_KEY] is None


def test_specs_integer_wait_for_scheduling_timeout(monkeypatch):
    executor = _make_executor(monkeypatch,
                              task_lib.Task(run='true', job={_FIELD: 45}))
    assert controller_module._build_task_specs(executor)[_SPECS_KEY] == 45


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
    breach = controller_module._deadline_breach({_SPECS_KEY: 60},
                                                _row(status,
                                                     submitted_at=_NOW - 61),
                                                _NOW)
    assert breach is not None
    assert breach.kind == _KIND
    assert breach.limit_seconds == 60
    assert _REASON_1M in breach.reason
    assert 'waited 1m 1s' in breach.reason


def test_no_breach_before_deadline():
    assert controller_module._deadline_breach(
        {_SPECS_KEY: 60}, _row(ManagedJobStatus.STARTING,
                               submitted_at=_NOW - 59), _NOW) is None


def test_breach_exactly_at_deadline():
    assert controller_module._deadline_breach({_SPECS_KEY: 60},
                                              _row(ManagedJobStatus.STARTING,
                                                   submitted_at=_NOW - 60),
                                              _NOW) is not None


def test_no_breach_once_started():
    # Started once: the timeout no longer applies, even while RECOVERING
    # long after the deadline.
    for status in (ManagedJobStatus.RUNNING, ManagedJobStatus.RECOVERING,
                   ManagedJobStatus.PENDING):
        assert controller_module._deadline_breach(
            {_SPECS_KEY: 60},
            _row(status, submitted_at=_NOW - 10_000,
                 start_at=_NOW - 9_000), _NOW) is None


@pytest.mark.parametrize('status', [
    ManagedJobStatus.SUCCEEDED, ManagedJobStatus.FAILED,
    ManagedJobStatus.CANCELLED, ManagedJobStatus.FAILED_NO_RESOURCE,
    ManagedJobStatus.CANCELLING
])
def test_no_breach_for_terminal_or_cancelling(status):
    assert controller_module._deadline_breach(
        {_SPECS_KEY: 60}, _row(status,
                               submitted_at=_NOW - 10_000), _NOW) is None


def test_no_breach_without_config_or_submission():
    assert controller_module._deadline_breach(
        {}, _row(ManagedJobStatus.STARTING, submitted_at=0), _NOW) is None
    assert controller_module._deadline_breach(
        {_SPECS_KEY: None}, _row(ManagedJobStatus.STARTING,
                                 submitted_at=0), _NOW) is None
    # PENDING with no submitted_at: never claimed by a controller.
    assert controller_module._deadline_breach(
        {_SPECS_KEY: 60}, _row(ManagedJobStatus.PENDING,
                               submitted_at=None), _NOW) is None


def test_format_duration():
    # Same rendering as the durations in `sky jobs queue`.
    assert controller_module._format_duration(7200) == '2h'
    assert controller_module._format_duration(5430) == '1h 30m 30s'


# ---------------------------------------------------------------------------
# The deadline loop, against a real state DB.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_scheduling_timeout_cancels_job_blocked_in_launch(
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
    task.job = {_FIELD: '1m'}
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
    assert managed_job_state.get_task_specs(job_id, 0)[_SPECS_KEY] == 60

    await manager._check_deadlines(time.time())

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(job_task, timeout=10)
    # Handed to run_job_loop's cancellation path as a non-graceful cancel.
    assert manager._cancel_info[job_id] == (False, None)
    row = _task_row(job_id, 0)
    assert row['failure_reason'].startswith(
        'Cancelled: task did not start within '
        'job.wait_for_scheduling_timeout=1m (waited 2m')
    reasons = [e['reason'] for e in _events(job_id)]
    assert any(
        _REASON_1M in r and
        r.startswith(managed_job_state.CANCEL_REQUESTED_EVENT_REASON_PREFIX)
        for r in reasons), reasons
    # The queue's details column shows it.
    assert _REASON_1M in managed_job_state.get_cancel_request_reasons([job_id
                                                                      ])[job_id]


@pytest.mark.asyncio
async def test_wait_for_scheduling_timeout_full_cancel_path_ends_cancelled(
        _mock_managed_jobs_db_conn, monkeypatch):
    """Through run_job_loop: the job ends CANCELLED via the same path as a
    user cancel (CANCELLING, cleanup, CANCELLED, schedule DONE)."""
    job_id = _create_job()
    await _start_task(job_id, 0, time.time() - 120, timeout_seconds=60)
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
    assert _REASON_1M in row['failure_reason']
    assert (managed_job_state.get_job_schedule_state(job_id) ==
            managed_job_state.ManagedJobScheduleState.DONE)
    statuses = [e['new_status'] for e in _events(job_id)]
    assert ManagedJobStatus.CANCELLING in statuses
    assert ManagedJobStatus.CANCELLED in statuses
    assert job_id not in manager.job_tasks


@pytest.mark.asyncio
async def test_no_config_is_a_no_op(_mock_managed_jobs_db_conn):
    """Backward compat: tasks without a job: block are never touched, and
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
    """A task whose specs predate the timeout's specs key (an upgraded
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
    await _start_task(job_id, 0, time.time() - 30, timeout_seconds=3600)
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
    """The timeout stops applying once the task has started; a later
    recovery (which never resets start_at) does not bring it back."""
    job_id = _create_job()
    submitted_at = time.time() - 10_000
    await _start_task(job_id, 0, submitted_at, timeout_seconds=60)
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
    submitted_at, so the timeout keeps counting."""
    job_id = _create_job()
    await _start_task(job_id, 0, time.time() - 120, timeout_seconds=60)
    await managed_job_state.set_backoff_pending_async(job_id, 0)
    assert _task_row(job_id, 0)['status'] == ManagedJobStatus.PENDING
    manager = _manager()
    job_task = asyncio.create_task(_RunningJob().run())
    manager.job_tasks[job_id] = job_task
    await manager._check_deadlines(time.time())
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(job_task, timeout=10)
    assert _FIELD in _task_row(job_id, 0)['failure_reason']


@pytest.mark.asyncio
async def test_terminal_and_cancelling_jobs_not_touched(
        _mock_managed_jobs_db_conn):
    # Job A: already CANCELLING (e.g. the user cancelled it) and overdue.
    job_a = _create_job()
    await _start_task(job_a, 0, time.time() - 10**4, timeout_seconds=60)
    await managed_job_state.set_cancelling_async(job_a, _noop_callback)
    # Job B: its only task already ended.
    job_b = _create_job()
    await _start_task(job_b, 0, time.time() - 10**4, timeout_seconds=60)
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
    await _start_task(job_id, 0, time.time() - 120, timeout_seconds=60)
    await managed_job_state.set_cancelling_async(job_id, _noop_callback)
    recorded = await managed_job_state.set_deadline_exceeded_async(
        job_id,
        0,
        kind=_KIND,
        limit_seconds=60,
        now=time.time(),
        failure_reason='Cancelled: x',
        event_reason='y')
    assert recorded is False
    assert _task_row(job_id, 0)['failure_reason'] is None
    assert all(e['reason'] != 'y' for e in _events(job_id))


@pytest.mark.asyncio
async def test_pipeline_uses_the_current_task_clock(_mock_managed_jobs_db_conn):
    """Task 0 SUCCEEDED after a long run; task 1 was just submitted. Only
    task 1's own submitted_at counts, so the job is not cancelled."""
    job_id = _create_job(num_tasks=2)
    now = time.time()
    await _start_task(job_id, 0, now - 10_000, timeout_seconds=60)
    await managed_job_state.set_started_async(job_id, 0, now - 9_990,
                                              _noop_callback)
    await managed_job_state.set_succeeded_async(job_id, 0, now - 20,
                                                _noop_callback)
    await _start_task(job_id, 1, now - 20, timeout_seconds=60)

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
    assert _REASON_1M in _task_row(job_id, 1)['failure_reason']


@pytest.mark.asyncio
async def test_pending_later_pipeline_task_has_no_clock(
        _mock_managed_jobs_db_conn):
    """A pipeline task that has not been reached yet (PENDING, no specs, no
    submitted_at) is not on the clock."""
    job_id = _create_job(num_tasks=2)
    now = time.time()
    await _start_task(job_id, 0, now - 30, timeout_seconds=60)
    manager = _manager()
    job_task = asyncio.create_task(_RunningJob().run())
    manager.job_tasks[job_id] = job_task
    await manager._check_deadlines(now + 10**6 - 100)
    # Task 0 is overdue at this time (so the job is cancelled because of it),
    # but task 1 never contributed: the reason is on task 0.
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(job_task, timeout=10)
    assert _FIELD in _task_row(job_id, 0)['failure_reason']
    assert _task_row(job_id, 1)['failure_reason'] is None


@pytest.mark.asyncio
async def test_job_group_any_task_breaching_cancels_the_job(
        _mock_managed_jobs_db_conn):
    job_id = _create_job(num_tasks=2)
    now = time.time()
    await _start_task(job_id, 0, now - 30, timeout_seconds=3600)
    await managed_job_state.set_started_async(job_id, 0, now - 20,
                                              _noop_callback)
    await _start_task(job_id, 1, now - 120, timeout_seconds=60)
    manager = _manager()
    job_task = asyncio.create_task(_RunningJob().run())
    manager.job_tasks[job_id] = job_task
    await manager._check_deadlines(now)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(job_task, timeout=10)
    assert _task_row(job_id, 0)['failure_reason'] is None
    assert _REASON_1M in _task_row(job_id, 1)['failure_reason']


@pytest.mark.asyncio
async def test_cancelled_once_and_user_cancel_race(_mock_managed_jobs_db_conn):
    """A job is cancelled for its deadline at most once, and a pending user
    cancel signal takes precedence (its graceful settings are kept)."""
    # Job A: deadline cancel, then more ticks.
    job_a = _create_job()
    await _start_task(job_a, 0, time.time() - 120, timeout_seconds=60)
    # Job B: overdue, but a user cancel signal has already been consumed by
    # cancel_job() and is waiting for run_job_loop.
    job_b = _create_job()
    await _start_task(job_b, 0, time.time() - 120, timeout_seconds=60)

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
    assert sum(_FIELD in r for r in reasons) == 1

    job_b_task.cancel.assert_not_called()
    assert manager._cancel_info[job_b] == (True, 60)
    assert _task_row(job_b, 0)['failure_reason'] is None


@pytest.mark.asyncio
async def test_one_job_failing_does_not_affect_others(
        _mock_managed_jobs_db_conn, monkeypatch):
    job_a = _create_job()
    await _start_task(job_a, 0, time.time() - 120, timeout_seconds=60)
    job_b = _create_job()
    await _start_task(job_b, 0, time.time() - 120, timeout_seconds=60)
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
    await _start_task(job_id, 0, time.time() - 7200, timeout_seconds=3600)
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
# Races: the task starting, and a user cancel, between the check and the
# cancel.
# ---------------------------------------------------------------------------


def _set_deadline_exceeded(job_id: int, task_id: int, limit_seconds: float,
                           now: float):
    return managed_job_state.set_deadline_exceeded_async(
        job_id,
        task_id,
        kind=_KIND,
        limit_seconds=limit_seconds,
        now=now,
        failure_reason='Cancelled: x',
        event_reason='y')


@pytest.mark.asyncio
async def test_task_starting_after_the_read_is_not_cancelled(
        _mock_managed_jobs_db_conn, monkeypatch):
    """The deadline loop read the row while the task had not started; the
    task then starts (set_started_async) before the cancel is recorded. The
    conditional write sees start_at and refuses: no reason, no event, no
    cancel."""
    job_id = _create_job()
    submitted_at = time.time() - 120
    await _start_task(job_id, 0, submitted_at, timeout_seconds=60)
    real_read = managed_job_state.get_unfinished_task_deadline_rows_async

    async def _read_then_start(job_ids):
        rows = await real_read(job_ids)
        assert rows[job_id][0]['start_at'] is None
        # The job coroutine marks the task started right after the read.
        await managed_job_state.set_started_async(job_id, 0, time.time(),
                                                  _noop_callback)
        return rows

    monkeypatch.setattr(managed_job_state,
                        'get_unfinished_task_deadline_rows_async',
                        _read_then_start)
    manager = _manager()
    job_task = asyncio.create_task(_RunningJob().run())
    manager.job_tasks[job_id] = job_task

    await manager._check_deadlines(time.time())

    await asyncio.sleep(0)
    assert not job_task.done()
    assert job_id not in manager._cancel_info
    row = _task_row(job_id, 0)
    assert row['status'] == ManagedJobStatus.RUNNING
    assert row['failure_reason'] is None
    # The start was recorded; no CANCELLING event was.
    statuses = [e['new_status'] for e in _events(job_id)]
    assert ManagedJobStatus.RUNNING in statuses
    assert ManagedJobStatus.CANCELLING not in statuses
    job_task.cancel()


@pytest.mark.asyncio
async def test_set_deadline_exceeded_refuses_a_started_task(
        _mock_managed_jobs_db_conn):
    job_id = _create_job()
    now = time.time()
    await _start_task(job_id, 0, now - 120, timeout_seconds=60)
    await managed_job_state.set_started_async(job_id, 0, now - 1,
                                              _noop_callback)
    events_before = _events(job_id)
    assert await _set_deadline_exceeded(job_id, 0, 60, now) is False
    assert _task_row(job_id, 0)['failure_reason'] is None
    assert _events(job_id) == events_before


@pytest.mark.asyncio
async def test_set_deadline_exceeded_refuses_when_not_yet_due(
        _mock_managed_jobs_db_conn):
    """submitted_at is newer than now - limit (e.g. a caller with a stale
    view of the clock, or a task re-submitted since): not recorded."""
    job_id = _create_job()
    now = time.time()
    await _start_task(job_id, 0, now - 30, timeout_seconds=60)
    events_before = _events(job_id)
    assert await _set_deadline_exceeded(job_id, 0, 60, now) is False
    assert _task_row(job_id, 0)['failure_reason'] is None
    assert _events(job_id) == events_before
    # A task never claimed by a controller (no submitted_at) has no clock.
    unclaimed = _create_job()
    assert await _set_deadline_exceeded(unclaimed, 0, 60, now) is False
    assert _task_row(unclaimed, 0)['failure_reason'] is None


@pytest.mark.asyncio
async def test_set_deadline_exceeded_records_when_due(
        _mock_managed_jobs_db_conn):
    job_id = _create_job()
    now = time.time()
    await _start_task(job_id, 0, now - 60, timeout_seconds=60)
    assert await _set_deadline_exceeded(job_id, 0, 60, now) is True
    assert _task_row(job_id, 0)['failure_reason'] == 'Cancelled: x'
    assert sum(e['reason'] == 'y' for e in _events(job_id)) == 1


def _write_cancel_signal(signal_dir, job_id: int, content: str) -> None:
    """As `sky jobs cancel` does (managed_job_utils.cancel_jobs_by_id)."""
    (signal_dir / str(job_id)).write_text(content, encoding='utf-8')


def _patch_run_job_loop_deps(monkeypatch, job_controller) -> None:
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


@pytest.mark.asyncio
async def test_pending_graceful_user_cancel_wins_over_the_deadline(
        _mock_managed_jobs_db_conn, monkeypatch, _signal_dir):
    """`sky jobs cancel --graceful` wrote its signal, but cancel_job() has
    not polled it yet when the overdue job's deadline tick runs. The deadline
    leaves the job to the user's cancel (records nothing), and the job ends
    CANCELLED with the user's graceful settings, not (False, None)."""
    job_id = _create_job()
    await _start_task(job_id, 0, time.time() - 120, timeout_seconds=60)
    running = _RunningJob()
    job_controller = mock.MagicMock()
    job_controller.load_dag = mock.AsyncMock()
    job_controller.run = running.run
    _patch_run_job_loop_deps(monkeypatch, job_controller)

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

    _write_cancel_signal(
        _signal_dir, job_id,
        f'{managed_job_utils._JOBS_GRACEFUL_CANCEL_SIGNAL}:60')
    events_before = _events(job_id)
    await manager._check_deadlines(time.time())

    # The deadline did not cancel or record anything.
    await asyncio.sleep(0)
    assert not running.cancelled.is_set()
    assert job_id not in manager._cancel_info
    assert _task_row(job_id, 0)['failure_reason'] is None
    assert _events(job_id) == events_before

    # cancel_job()'s next poll applies the user's cancel.
    cancel_loop = asyncio.create_task(manager.cancel_job())
    try:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(loop_task, timeout=10)
    finally:
        cancel_loop.cancel()
    manager._cleanup.assert_awaited_once_with(job_id,
                                              pool=None,
                                              graceful=True,
                                              graceful_timeout=60)
    row = _task_row(job_id, 0)
    assert row['status'] == ManagedJobStatus.CANCELLED
    assert row['failure_reason'] is None
    assert not (_signal_dir / str(job_id)).exists()


@pytest.mark.asyncio
async def test_user_cancel_arriving_during_the_record_keeps_its_settings(
        _mock_managed_jobs_db_conn, monkeypatch, _signal_dir):
    """The user's signal lands while the deadline is being recorded (after
    the pre-check). The deadline still does not hard-cancel: cancel_job()
    applies the user's settings on its next poll."""
    job_id = _create_job()
    await _start_task(job_id, 0, time.time() - 120, timeout_seconds=60)
    real_set = managed_job_state.set_deadline_exceeded_async

    async def _signal_then_set(*args, **kwargs):
        _write_cancel_signal(
            _signal_dir, job_id,
            f'{managed_job_utils._JOBS_GRACEFUL_CANCEL_SIGNAL}:30')
        return await real_set(*args, **kwargs)

    monkeypatch.setattr(managed_job_state, 'set_deadline_exceeded_async',
                        _signal_then_set)
    manager = _manager()
    job_task = asyncio.create_task(_RunningJob().run())
    manager.job_tasks[job_id] = job_task
    await manager._check_deadlines(time.time())
    await asyncio.sleep(0)
    assert not job_task.done()
    assert job_id not in manager._cancel_info
    assert job_id not in manager._deadline_cancelled

    cancel_loop = asyncio.create_task(manager.cancel_job())
    try:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(job_task, timeout=10)
    finally:
        cancel_loop.cancel()
    assert manager._cancel_info[job_id] == (True, 30)


@pytest.mark.asyncio
async def test_pending_hard_user_cancel_also_wins(_mock_managed_jobs_db_conn,
                                                  _signal_dir):
    """A plain (non-graceful) `sky jobs cancel` signal is an empty file; the
    deadline defers to it too, so the job's cancel is attributed to the
    user."""
    job_id = _create_job()
    await _start_task(job_id, 0, time.time() - 120, timeout_seconds=60)
    (_signal_dir / str(job_id)).touch()
    manager = _manager()
    job_task = asyncio.create_task(_RunningJob().run())
    manager.job_tasks[job_id] = job_task
    await manager._check_deadlines(time.time())
    await asyncio.sleep(0)
    assert not job_task.done()
    assert _task_row(job_id, 0)['failure_reason'] is None
    job_task.cancel()


# ---------------------------------------------------------------------------
# Client-side version gate.
# ---------------------------------------------------------------------------


def _unwrap(fn):
    while hasattr(fn, '__wrapped__'):
        fn = fn.__wrapped__
    return fn


def _timeout_task() -> task_lib.Task:
    return task_lib.Task(run='echo hi', job={_FIELD: '2h'})


def test_launch_wait_for_scheduling_timeout_refuses_an_old_server():
    raw_launch = _unwrap(jobs_sdk.launch)
    too_old = _MIN_VERSION - 1
    with mock.patch.object(jobs_sdk.versions,
                           'get_remote_api_version',
                           return_value=too_old), \
         mock.patch.object(jobs_sdk.server_common,
                           'make_authenticated_request') as mock_request:
        with pytest.raises(exceptions.NotSupportedError,
                           match='job.wait_for_scheduling_timeout'):
            raw_launch(_timeout_task())
        mock_request.assert_not_called()


def _job_blocks_in(dag_yaml: str) -> List[Any]:
    return [
        doc.get('job')
        for doc in yaml_utils.safe_load_all(dag_yaml)
        if isinstance(doc, dict) and 'run' in doc
    ]


def _sent_dags(mock_request) -> List[str]:
    sent = []
    for call in mock_request.call_args_list:
        body = call.kwargs['json']
        sent.append(body.get('dag', body.get('task')))
    return sent


@pytest.mark.parametrize('api_version,expected', [
    (_MIN_VERSION - 1, None),
    (_MIN_VERSION, {
        _FIELD: '2h'
    }),
])
def test_validate_omits_job_block_for_an_older_server(api_version, expected):
    """An older server's strict task schema rejects the job: block, so the
    client drops it (it only matters to managed jobs)."""
    with mock.patch.object(client_sdk.versions,
                           'get_remote_api_version',
                           return_value=api_version), \
         mock.patch.object(client_sdk.server_common,
                           'make_authenticated_request') as mock_request:
        mock_request.return_value.status_code = 200
        _unwrap(client_sdk.validate)(dag_utils.convert_entrypoint_to_dag(
            _timeout_task()))
    (dag_yaml,) = _sent_dags(mock_request)
    assert _job_blocks_in(dag_yaml) == [expected]


@pytest.mark.parametrize('api_version,expected', [
    (_MIN_VERSION - 1, None),
    (_MIN_VERSION, {
        _FIELD: '2h'
    }),
])
def test_optimize_omits_job_block_for_an_older_server(api_version, expected):
    """optimize() sends the DAG without validating it first (e.g. from a
    script), so it drops the block itself."""
    with mock.patch.object(client_sdk.versions,
                           'get_remote_api_version',
                           return_value=api_version), \
         mock.patch.object(client_sdk.server_common,
                           'make_authenticated_request') as mock_request, \
         mock.patch.object(client_sdk.server_common, 'get_request_id'):
        _unwrap(client_sdk.optimize)(dag_utils.convert_entrypoint_to_dag(
            _timeout_task()))
    (dag_yaml,) = _sent_dags(mock_request)
    assert _job_blocks_in(dag_yaml) == [expected]


@pytest.mark.parametrize('entrypoint', ['launch', 'exec'])
def test_launch_and_exec_omit_job_block_for_an_older_server(entrypoint):
    """`sky launch` / `sky exec` with a task YAML that sets a job: block
    work against an older server: every DAG sent omits the block."""
    too_old = _MIN_VERSION - 1
    with mock.patch.object(client_sdk.versions,
                           'get_remote_api_version',
                           return_value=too_old), \
         mock.patch.object(client_sdk.server_common,
                           'make_authenticated_request') as mock_request, \
         mock.patch.object(client_sdk.server_common, 'get_request_id'), \
         mock.patch.object(client_sdk.server_common,
                           'check_server_healthy_or_start_fn'), \
         mock.patch.object(client_sdk.client_common,
                           'upload_mounts_to_api_server',
                           side_effect=lambda dag, **_: (dag, None)):
        mock_request.return_value.status_code = 200
        fn = _unwrap(getattr(client_sdk, entrypoint))
        fn(_timeout_task(), cluster_name='c')
    sent = _sent_dags(mock_request)
    # validate, then the launch / exec request itself.
    assert len(sent) == 2, sent
    for dag_yaml in sent:
        assert _job_blocks_in(dag_yaml) == [None]


def test_jobs_launch_refuses_an_old_server_before_validate_could_strip():
    """The managed-jobs gate runs before sdk.validate(), so the block is never
    silently dropped from a managed job: the launch fails instead, with no
    request sent."""
    raw_launch = _unwrap(jobs_sdk.launch)
    too_old = _MIN_VERSION - 1
    task = _timeout_task()
    with mock.patch.object(jobs_sdk.versions,
                           'get_remote_api_version',
                           return_value=too_old), \
         mock.patch.object(client_sdk.versions,
                           'get_remote_api_version',
                           return_value=too_old), \
         mock.patch.object(jobs_sdk.sdk, 'validate') as mock_validate, \
         mock.patch.object(jobs_sdk.server_common,
                           'make_authenticated_request') as mock_request, \
         mock.patch.object(client_sdk.server_common,
                           'make_authenticated_request') as mock_sdk_request:
        with pytest.raises(exceptions.NotSupportedError,
                           match='job.wait_for_scheduling_timeout'):
            raw_launch(task)
        mock_validate.assert_not_called()
        mock_request.assert_not_called()
        mock_sdk_request.assert_not_called()
    assert task.job == {_FIELD: '2h'}


def test_uses_wait_for_scheduling_timeout_detection():
    assert jobs_sdk._uses_wait_for_scheduling_timeout(
        dag_utils.convert_entrypoint_to_dag(_timeout_task()))
    plain = task_lib.Task(run='echo hi')
    plain.set_resources(resources_lib.Resources(job_recovery='FAILOVER'))
    assert not jobs_sdk._uses_wait_for_scheduling_timeout(
        dag_utils.convert_entrypoint_to_dag(plain))
    assert not jobs_sdk._uses_wait_for_scheduling_timeout(
        dag_utils.convert_entrypoint_to_dag(task_lib.Task(run='echo hi')))
    # Any task of a pipeline setting it counts.
    with dag_lib.Dag() as dag:
        first = task_lib.Task(run='echo a')
        second = task_lib.Task(run='echo b', job={_FIELD: 60})
        first >> second  # pylint: disable=pointless-statement
    assert jobs_sdk._uses_wait_for_scheduling_timeout(dag)


def test_cancel_request_event_reason_format():
    """The deadline cancel is recorded as an attributed cancel request so
    the queue's details column picks it up."""
    reason = managed_job_utils.CancelRequestInfo(
        note='by the jobs controller: x').event_reason()
    assert reason == (
        f'{managed_job_state.CANCEL_REQUESTED_EVENT_REASON_PREFIX}'
        ' by the jobs controller: x')
