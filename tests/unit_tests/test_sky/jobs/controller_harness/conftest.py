"""Fixtures wiring the controller harness to a temp DB and the FakeCloud.

Patch policy: everything inside ``sky/jobs`` runs real code; we only patch
(a) the cloud/SDK/API-server boundary, (b) filesystem locations, and
(c) polling cadences that would make tests slow. Each patch below states
which real call site it stands in for.
"""
# Fixtures consuming other fixtures by name is how pytest works:
# pylint: disable=redefined-outer-name
import asyncio
import contextlib
import os
import uuid

from controller_harness import harness as harness_module
import filelock
import pytest
import pytest_asyncio
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

from sky import core
from sky import exceptions
from sky import global_user_state
from sky import resources as resources_lib
from sky import skypilot_config
from sky.backends import backend_utils
from sky.backends import cloud_vm_ray_backend
from sky.client import sdk
from sky.jobs import constants as jobs_constants
from sky.jobs import controller as controller_module
from sky.jobs import recovery_strategy
from sky.jobs import state as managed_job_state
from sky.jobs import utils as managed_job_utils
from sky.utils import controller_utils


@pytest.fixture
def jobs_db(tmp_path, monkeypatch):
    """Temp sqlite managed-jobs DB, schema created via the real alembic path.

    Same pattern as test_jobs_state.py. Also pins skypilot_config to empty
    files so harness behavior cannot depend on the developer's
    ~/.sky/config.yaml or a cwd .sky.yaml (config is consulted during task
    and Resources construction inside the exercised flows).
    """
    # Managed by hand rather than monkeypatch: monkeypatch's env restore runs
    # after this fixture's finalizer, so a teardown reload_config() would
    # still see the pinned values. The saved/restored pairs keep the
    # post-restore reload accurate for same-worker tests outside this
    # package.
    empty_config = tmp_path / 'empty_config.yaml'
    empty_config.touch()
    saved_env = {}
    for env_var in (skypilot_config.ENV_VAR_GLOBAL_CONFIG,
                    skypilot_config.ENV_VAR_PROJECT_CONFIG):
        saved_env[env_var] = os.environ.get(env_var)
        os.environ[env_var] = str(empty_config)
    skypilot_config.reload_config()

    db_path = tmp_path / 'managed_jobs_testing.db'
    engine = create_engine(f'sqlite:///{db_path}')
    async_engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}',
                                       connect_args={'timeout': 30})

    # Point the alembic DB lock at the workspace to avoid writing to ~/.sky.
    @contextlib.contextmanager
    def _tmp_db_lock(section: str):
        lock_path = tmp_path / f'.{section}.lock'
        with filelock.FileLock(str(lock_path), timeout=10):
            yield

    monkeypatch.setattr(managed_job_state.migration_utils, 'db_lock',
                        _tmp_db_lock)
    db_manager = managed_job_state._db_manager  # pylint: disable=protected-access
    monkeypatch.setattr(db_manager, '_engine', engine)
    monkeypatch.setattr(db_manager, '_engine_async', async_engine)

    managed_job_state.create_table(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        # The test's event loop is gone by sync-fixture teardown; dispose the
        # aiosqlite engine (and its per-connection threads) on a fresh one.
        asyncio.run(async_engine.dispose())
        for env_var, value in saved_env.items():
            if value is None:
                os.environ.pop(env_var, None)
            else:
                os.environ[env_var] = value
        skypilot_config.reload_config()


@pytest.fixture
def fake_cloud(jobs_db, monkeypatch):
    """A FakeCloud with every cloud/SDK seam monkeypatched to it."""
    del jobs_db  # Ordering only: the DB must exist before the controller runs.
    cloud = harness_module.FakeCloud()

    # --- Job/cluster status lookups (normally SSH/gRPC to the cluster). ---
    # controller._monitor_one_task + recovery_strategy._wait_until_job_starts.
    monkeypatch.setattr(managed_job_utils, 'get_job_status',
                        cloud.fake_get_job_status)
    monkeypatch.setattr(managed_job_utils, 'get_job_timestamp',
                        cloud.fake_get_job_timestamp)
    monkeypatch.setattr(managed_job_utils, 'try_to_get_job_end_time',
                        cloud.fake_try_to_get_job_end_time)
    monkeypatch.setattr(backend_utils, 'refresh_cluster_status_handle',
                        cloud.fake_refresh_cluster_status_handle)

    async def _fake_check_network() -> None:
        return None

    monkeypatch.setattr(backend_utils, 'async_check_network_connection',
                        _fake_check_network)
    # Log download enumerates clusters; returning [] skips the download
    # gracefully (same as a cluster that is already torn down).
    monkeypatch.setattr(backend_utils, 'get_clusters', lambda *a, **k: [])
    # The user-code-failure branch passes a live handle straight to the log
    # downloader (no get_clusters gate); the real one would SSH.
    monkeypatch.setattr(controller_utils, 'download_and_stream_job_log',
                        lambda *a, **k: None)

    # --- Exit-code probe (JobController._get_cluster_job_exit_codes). ---
    # Force the gRPC branch (taken when ENABLE_GRPC is set in the test env)
    # down to the legacy run_on_head path, which the FakeCloud answers from
    # the cluster's recorded exit codes.
    def _fake_invoke_skylet(*args, **kwargs):
        del args, kwargs
        raise exceptions.SkyletMethodNotImplementedError(
            'harness: use the legacy path')

    monkeypatch.setattr(backend_utils, 'invoke_skylet_with_retries',
                        _fake_invoke_skylet)
    monkeypatch.setattr(cloud_vm_ray_backend.CloudVmRayBackend, 'run_on_head',
                        cloud.fake_run_on_head)

    # --- The clusters DB (global_user_state) is not used by the harness. ---
    monkeypatch.setattr(global_user_state, 'get_handle_from_cluster_name',
                        cloud.fake_get_handle_from_cluster_name)
    monkeypatch.setattr(global_user_state, 'get_cluster_events',
                        lambda *a, **k: [])

    # --- Cluster teardown. ---
    # jobs.utils.terminate_cluster internally drives core.down with retries;
    # the fake just records and removes the cluster.
    monkeypatch.setattr(managed_job_utils, 'terminate_cluster',
                        cloud.fake_terminate_cluster)
    # ControllerManager._cleanup verifies termination via core.status; [] is
    # the "cluster is gone" answer it asserts on.
    monkeypatch.setattr(core, 'status', lambda *a, **k: [])
    monkeypatch.setattr(core, 'cancel', lambda *a, **k: None)

    # --- The SDK boundary (normally HTTP to the API server). ---
    monkeypatch.setattr(sdk, 'api_start', lambda *a, **k: None)
    monkeypatch.setattr(sdk, 'launch', cloud.fake_sdk_launch)
    monkeypatch.setattr(sdk, 'stream_and_get', lambda *a, **k: None)
    monkeypatch.setattr(sdk, 'cancel', lambda *a, **k: 'fake-cancel-request')
    monkeypatch.setattr(sdk, 'api_cancel', lambda *a, **k: 'fake-api-cancel')
    monkeypatch.setattr(sdk, 'get', lambda *a, **k: None)

    # The harness's skeleton launched_resources (no instance type) is not
    # launchable, and the real method asserts is_launchable() before
    # answering. False matches AWS semantics (no special post-preemption
    # cleanup needed).
    monkeypatch.setattr(resources_lib.Resources,
                        'need_cleanup_after_preemption_or_failure',
                        lambda self: False)

    # --- Cadences: poll fast so scenarios finish in seconds. ---
    monkeypatch.setattr(managed_job_utils, 'JOB_STATUS_CHECK_GAP_SECONDS', 0.05)
    monkeypatch.setattr(managed_job_utils,
                        'JOB_STARTED_STATUS_CHECK_GAP_SECONDS', 0.05)
    # A failed launch backs off Backoff(RETRY_INIT_GAP_SECONDS) before the
    # retry; the real 60s (+/- jitter) straddles the harness's 60s wait
    # timeout, so launch-failure scenarios would flake without this.
    monkeypatch.setattr(recovery_strategy.StrategyExecutor,
                        'RETRY_INIT_GAP_SECONDS', 0.1)

    yield cloud


@pytest_asyncio.fixture
async def make_controller_harness(fake_cloud, tmp_path, monkeypatch):
    """Factory for ControllerHarness instances around real ControllerManagers.

    Returns ``factory(pid=None, pid_started_at=None)``. Every in-process
    manager would otherwise stamp the identical live pytest pid into its
    claims; multi-controller scenarios pass distinct pid identities so claims
    are distinguishable in the DB (the pids are fake, so anything doing a
    psutil liveness check against them would see "dead" -- no such checker
    runs inside the harness).
    """
    # Keep controller artifacts (per-job logs, cancel-signal files) in the
    # test workspace instead of ~/.sky and ~/sky_logs.
    log_dir = tmp_path / 'controller_logs'
    signal_dir = tmp_path / 'signals'
    signal_dir.mkdir()
    monkeypatch.setattr(jobs_constants, 'JOBS_CONTROLLER_LOGS_DIR',
                        str(log_dir))
    monkeypatch.setattr(jobs_constants, 'CONSOLIDATED_SIGNAL_PATH',
                        str(signal_dir))

    # Deterministic job-count limit independent of the test machine's memory.
    monkeypatch.setattr(controller_utils, 'get_number_of_jobs_controllers',
                        lambda: 1)
    # The file-mount cleanup branch in ControllerManager._cleanup is skipped
    # in consolidation mode, which is what this in-process setup models.
    monkeypatch.setattr(managed_job_utils, 'is_consolidation_mode',
                        lambda: True)

    # The module-global lock is created at import time; give each test's
    # event loop a fresh one so it can never be bound to a previous loop.
    monkeypatch.setattr(controller_module, '_background_tasks_lock',
                        asyncio.Lock())
    # The DAG cache is keyed by job_id, which restarts from 1 in every test's
    # fresh DB -- a stale entry would hand a job another test's DAG.
    controller_module._get_dag.cache_clear()  # pylint: disable=protected-access

    harnesses = []

    def factory(pid=None, pid_started_at=None):
        manager = controller_module.ControllerManager(
            controller_uuid=str(uuid.uuid4()))
        if pid is not None:
            manager._pid = pid  # pylint: disable=protected-access
            manager._pid_started_at = (  # pylint: disable=protected-access
                pid_started_at if pid_started_at is not None else float(pid))
        harness = harness_module.ControllerHarness(manager, fake_cloud)
        harnesses.append(harness)
        return harness

    try:
        yield factory
    finally:
        for harness in harnesses:
            await harness.stop()
        # run_job_loop tasks are module-global, so they are drained once,
        # after every harness has cancelled its own jobs.
        await harness_module.drain_background_tasks()
        controller_module._get_dag.cache_clear()  # pylint: disable=protected-access


@pytest_asyncio.fixture
async def controller_harness(make_controller_harness):
    """A single ready-to-start ControllerHarness (the common case)."""
    return make_controller_harness()
