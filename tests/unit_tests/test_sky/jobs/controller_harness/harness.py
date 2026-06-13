"""In-process integration harness for the managed-jobs controller.

Boots a real ``ControllerManager`` (``sky/jobs/controller.py``) inside the
test's asyncio event loop, against a real sqlite-backed managed-jobs state DB,
with the cloud/SDK boundary replaced by an in-memory ``FakeCloud``. Everything
in ``sky/jobs`` runs real code -- the state machine (``state.py``), the
scheduler (``scheduler.py``), the controller and its monitor loops
(``controller.py``), and the recovery strategies (``recovery_strategy.py``).
Only the edges that would talk to a cloud or the API server are faked:

- ``sky.client.sdk`` (``launch``/``exec``/``cancel``/...)
- cluster and job status lookups (``backend_utils`` / ``jobs.utils``)
- cluster termination (``jobs.utils.terminate_cluster``)
- ``global_user_state`` handle lookups (the clusters DB is not used)

See ``conftest.py`` for the fixture that wires the patches up.

This exists because nothing else in the repo exercises a live controller:
the claim path (``get_waiting_job_async``), the recovery-reset path
(``reset_job_for_recovery``), and the monitor/recovery/cleanup loops
previously had no in-process coverage.

DB backend: sqlite over a temp file. Postgres coverage for these code paths
comes from the smoke-test ``--postgres`` lane, not from this harness.

Known fidelity gap: production controllers run ``context_utils.
hijack_sys_attrs()`` (``controller.main``), which gives each job coroutine a
context-local ``os.environ``. The harness does not (it would hijack the
pytest process), so the env-var pop/restore in ``StrategyExecutor._launch``
touches the real process environ; concurrent-launch tests that assert on
env vars would race here where production does not.
"""
import asyncio
import dataclasses
import json
import time
from typing import Any, Callable, Dict, List, Optional

from sky import backends
from sky import resources as resources_lib
from sky.jobs import controller as controller_module
from sky.jobs import state as managed_job_state
from sky.jobs import utils as managed_job_utils
from sky.skylet import constants as skylet_constants
from sky.skylet import job_lib
from sky.utils import common_utils
from sky.utils import dag_utils
from sky.utils import status_lib

# How long scenario helpers wait for the controller to make progress before
# failing the test. The controller's claim loop polls every ~10s when idle
# (a literal in ``ControllerManager.monitor_loop``), so this must comfortably
# cover several polls.
DEFAULT_TIMEOUT_SECONDS = 60


async def drain_background_tasks(timeout: float = 10) -> None:
    """Drain the controller module's run_job_loop background tasks.

    Called by the harness fixture after all harnesses are stopped. The first
    wait lets cancelled jobs run their cleanup; stragglers are hard-cancelled
    and given a bounded second wait so a wedged cleanup cannot hang the test
    session.
    """
    background = [
        task for task in controller_module._background_tasks  # pylint: disable=protected-access
        if not task.done()
    ]
    if not background:
        return
    _, pending = await asyncio.wait(background, timeout=timeout)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.wait(pending, timeout=timeout)


def _make_fake_handle(cluster_name: str) -> 'backends.CloudVmRayResourceHandle':
    """Build a minimal real CloudVmRayResourceHandle.

    ``FailoverStrategyExecutor._launch`` asserts the handle is a real
    ``CloudVmRayResourceHandle``, so a Mock won't do. The launched resources
    deliberately specify infra but no instance type: this keeps construction
    fully offline (an instance type would trigger a catalog fetch on first
    validation), and the non-None region keeps
    ``EagerFailoverStrategyExecutor.recover`` away from its
    region-blocking ``Resources.copy`` branch (which would also validate
    against the catalog).
    """
    return backends.CloudVmRayResourceHandle(
        cluster_name=cluster_name,
        cluster_name_on_cloud=cluster_name,
        cluster_yaml=None,
        launched_nodes=1,
        launched_resources=resources_lib.Resources(infra='aws/us-east-1'))


@dataclasses.dataclass
class FakeCluster:
    """An in-memory stand-in for a launched cluster and its job."""
    name: str
    status: status_lib.ClusterStatus
    job_status: job_lib.JobStatus
    handle: 'backends.CloudVmRayResourceHandle'
    job_submitted_at: float
    job_end_at: Optional[float] = None
    # Exit codes the controller's exit-code probe sees once the job is
    # terminal (None while the job runs).
    job_exit_codes: Optional[List[int]] = None


class FakeCloud:
    """In-memory cloud: a registry of clusters the fakes read and mutate.

    Tests drive scenarios by mutating cluster state (``finish_job``,
    ``preempt``) and assert on the recorded ``launch_calls`` /
    ``terminate_calls``.
    """

    def __init__(self) -> None:
        self.clusters: Dict[str, FakeCluster] = {}
        # Cluster names in call order, including repeat launches (recoveries).
        self.launch_calls: List[str] = []
        # Cluster names passed to terminate_cluster, in call order.
        self.terminate_calls: List[str] = []
        # If set, called with the cluster name before a fake launch
        # "provisions"; raise from it to simulate a launch failure.
        self.launch_hook: Optional[Callable[[str], None]] = None

    def launch(self, cluster_name: str) -> None:
        """Provision a fake cluster with its job already RUNNING."""
        self.launch_calls.append(cluster_name)
        if self.launch_hook is not None:
            self.launch_hook(cluster_name)  # pylint: disable=not-callable
        self.clusters[cluster_name] = FakeCluster(
            name=cluster_name,
            status=status_lib.ClusterStatus.UP,
            job_status=job_lib.JobStatus.RUNNING,
            handle=_make_fake_handle(cluster_name),
            job_submitted_at=time.time())

    def terminate(self, cluster_name: str) -> None:
        self.terminate_calls.append(cluster_name)
        self.clusters.pop(cluster_name, None)

    def finish_job(self,
                   cluster_name: str,
                   job_status: job_lib.JobStatus = job_lib.JobStatus.SUCCEEDED,
                   exit_codes: Optional[List[int]] = None) -> None:
        """Move the cluster's job to a terminal status."""
        cluster = self.clusters[cluster_name]
        cluster.job_status = job_status
        cluster.job_end_at = time.time()
        if exit_codes is None:
            exit_codes = ([0]
                          if job_status == job_lib.JobStatus.SUCCEEDED else [1])
        cluster.job_exit_codes = exit_codes

    def preempt(self, cluster_name: str) -> None:
        """Simulate a preemption that takes the whole cluster away.

        The cluster disappears from the cloud: status lookups return None,
        which drives the controller's recovery path. (INIT/STOPPED-style
        partial preemptions are not modeled yet.)
        """
        self.clusters.pop(cluster_name, None)

    # --- Implementations behind the monkeypatched seams (see conftest) ---

    async def fake_get_job_status(self, backend: Any, cluster_name: str,
                                  job_id: Optional[int]) -> Any:
        del backend, job_id
        cluster = self.clusters.get(cluster_name)
        if cluster is None:
            return (None, None)
        return (cluster.job_status, None)

    def fake_refresh_cluster_status_handle(self, cluster_name: str,
                                           **kwargs: Any) -> Any:
        del kwargs
        cluster = self.clusters.get(cluster_name)
        if cluster is None:
            return (None, None)
        return (cluster.status, cluster.handle)

    def fake_get_handle_from_cluster_name(self, cluster_name: str) -> Any:
        cluster = self.clusters.get(cluster_name)
        return cluster.handle if cluster is not None else None

    def fake_get_job_timestamp(self, backend: Any, cluster_name: str,
                               job_id: Optional[int],
                               get_end_time: bool) -> float:
        del backend, job_id
        cluster = self.clusters[cluster_name]
        if get_end_time:
            assert cluster.job_end_at is not None, cluster
            return cluster.job_end_at
        return cluster.job_submitted_at

    def fake_try_to_get_job_end_time(self, backend: Any, cluster_name: str,
                                     job_id: Optional[int]) -> float:
        del backend, job_id
        cluster = self.clusters.get(cluster_name)
        if cluster is None or cluster.job_end_at is None:
            return time.time()
        return cluster.job_end_at

    def fake_terminate_cluster(self,
                               cluster_name: str,
                               max_retry: int = 6,
                               graceful: bool = False,
                               graceful_timeout: Optional[int] = None) -> None:
        del max_retry, graceful, graceful_timeout
        self.terminate(cluster_name)

    def fake_sdk_launch(self, dag: Any, *, cluster_name: str,
                        **kwargs: Any) -> str:
        del dag, kwargs
        self.launch(cluster_name)
        return 'fake-launch-request-id'

    def fake_run_on_head(self, handle: Any, code: str, **kwargs: Any) -> Any:
        """Stands in for CloudVmRayBackend.run_on_head (exit-code probe).

        The only run_on_head use in the harness's flows is
        JobController._get_cluster_job_exit_codes' legacy SSH path; answer it
        from the cluster's recorded exit codes. (Patched as a bound method on
        the backend class, so no backend ``self`` arrives here.)
        """
        del code, kwargs
        cluster = self.clusters.get(handle.cluster_name)
        if cluster is None or cluster.job_exit_codes is None:
            return (1, '', 'fake cluster gone')
        return (0, json.dumps(cluster.job_exit_codes), '')


class ControllerHarness:
    """Drives a real ControllerManager against the FakeCloud.

    Use ``submit_job`` to put a WAITING job in the DB, ``start`` to run the
    controller's claim and cancel loops, and the ``wait_for*`` helpers to
    block on DB-observable progress.
    """

    def __init__(self, manager: 'controller_module.ControllerManager',
                 fake_cloud: FakeCloud) -> None:
        self.manager = manager
        self.fake_cloud = fake_cloud
        self._loop_tasks: List[asyncio.Task] = []

    @property
    def pid(self) -> int:
        return self.manager._pid  # pylint: disable=protected-access

    async def start(self) -> None:
        """Start the controller's background loops (claim + cancel)."""
        assert not self._loop_tasks, 'harness already started'
        self._loop_tasks = [
            asyncio.create_task(self.manager.monitor_loop(),
                                name='harness_monitor_loop'),
            asyncio.create_task(self.manager.cancel_job(),
                                name='harness_cancel_job'),
        ]

    async def stop(self) -> None:
        """Stop this controller's loops and cancel its running jobs.

        The enclosing run_job_loop coroutines are module-global (not
        per-manager); the fixture drains them via drain_background_tasks
        after stopping every harness, so two-controller tests don't have one
        harness's stop() cancel the other's jobs.
        """
        for task in self._loop_tasks:
            task.cancel()
        await asyncio.gather(*self._loop_tasks, return_exceptions=True)
        self._loop_tasks = []

        # Cancel the per-job controller.run() tasks; their enclosing
        # run_job_loop coroutines observe the cancellation and run their
        # (fake-backed) cleanup before exiting.
        async with self.manager._job_tasks_lock:  # pylint: disable=protected-access
            inner_tasks = list(self.manager.job_tasks.values())
        for task in inner_tasks:
            task.cancel()

    def submit_job(self,
                   name: str = 'test-job',
                   run: str = 'echo hello',
                   priority: int = skylet_constants.DEFAULT_PRIORITY) -> int:
        """Create a WAITING single-task managed job, mirroring jobs.launch.

        Performs the same DB writes as the real submission path
        (``sky.jobs.server.core.launch`` + ``scheduler.submit_jobs``) without
        the file-based plumbing: job_info row, PENDING task row, then
        WAITING schedule state with the DAG YAML stored inline.

        Deliberately skipped: scheduler.submit_jobs' skip-if-controller-alive
        guard. Scenarios that RE-submit a job (HA-recovery style) must drive
        the real submit_jobs, or they will submit where production skips.
        """
        user_yaml = f'name: {name}\n\nrun: |\n  {run}\n'
        # Persist the DAG the way the real submission path does
        # (sky.jobs.server.core.launch): names inferred and the default
        # job_recovery config filled in. StrategyExecutor.make requires the
        # filled form.
        dag = dag_utils.load_dag_from_yaml_str(user_yaml)
        dag_utils.maybe_infer_and_fill_dag_and_task_names(dag)
        dag_utils.fill_default_config_in_dag_for_job_launch(dag)
        dag_yaml = dag_utils.dump_chain_dag_to_yaml_str(dag)
        job_id = managed_job_state.set_job_info_without_job_id(
            name=name,
            workspace='default',
            entrypoint=f'sky jobs launch {name}',
            pool=None,
            pool_hash=None,
            user_hash=common_utils.get_user_hash())
        managed_job_state.set_pending(job_id,
                                      task_id=0,
                                      task_name=name,
                                      resources_str='-',
                                      metadata='{}')
        managed_job_state.scheduler_set_waiting(
            [job_id],
            dag_yaml_content=dag_yaml,
            original_user_yaml_content=user_yaml,
            env_file_content='',
            config_file_content=None,
            priority=priority)
        return job_id

    def cluster_name_for(self, job_id: int, task_name: str) -> str:
        """The cluster name the controller will generate for this task."""
        return managed_job_utils.generate_managed_job_cluster_name(
            task_name, job_id)

    async def wait_for(self,
                       predicate: Callable[[], bool],
                       description: str,
                       timeout: float = DEFAULT_TIMEOUT_SECONDS,
                       interval: float = 0.05) -> None:
        """Poll ``predicate`` until true or fail the test on timeout."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return
            await asyncio.sleep(interval)
        raise AssertionError(f'Timed out after {timeout}s waiting for: '
                             f'{description}')

    async def wait_for_job_status(
            self,
            job_id: int,
            statuses: List[managed_job_state.ManagedJobStatus],
            timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        last_seen: List[Any] = [None]

        def _check() -> bool:
            last_seen[0] = managed_job_state.get_status(job_id)
            return last_seen[0] in statuses

        try:
            await self.wait_for(_check,
                                f'job {job_id} to reach status in {statuses}',
                                timeout)
        except AssertionError as e:
            raise AssertionError(f'{e} (last status: {last_seen[0]})') from None

    async def wait_for_schedule_state(
            self,
            job_id: int,
            states: List[managed_job_state.ManagedJobScheduleState],
            timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        await self.wait_for(
            lambda: managed_job_state.get_job_schedule_state(job_id) in states,
            f'job {job_id} to reach schedule state in {states}', timeout)
