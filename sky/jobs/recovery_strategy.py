"""Strategies to handle launching/recovery/termination of managed job clusters.

In the YAML file, the user can specify the strategy to use for managed jobs.

resources:
    job_recovery: EAGER_NEXT_REGION
"""
import asyncio
import concurrent.futures
import contextlib
import logging
import os
import traceback
import typing
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set

from sky import backends
from sky import dag as dag_lib
from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky import skypilot_config
from sky.backends import backend_utils
from sky.client import sdk
from sky.jobs import file_content_utils
from sky.jobs import runtime as managed_job_runtime
from sky.jobs import scheduler
from sky.jobs import state
from sky.jobs import utils as managed_job_utils
from sky.serve import serve_utils
from sky.server import common as server_common
from sky.server.requests import requests as requests_lib
from sky.skylet import constants
from sky.skylet import job_lib
from sky.usage import usage_lib
from sky.utils import common_utils
from sky.utils import context_utils
from sky.utils import controller_utils
from sky.utils import dag_utils
from sky.utils import env_options
from sky.utils import instance_links as instance_links_utils
from sky.utils import registry
from sky.utils import status_lib
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky import resources
    from sky import task as task_lib

logger = sky_logging.init_logger(__name__)

# Waiting time for job from INIT/PENDING to RUNNING
# 10 * JOB_STARTED_STATUS_CHECK_GAP_SECONDS = 10 * 5 = 50 seconds
MAX_JOB_CHECKING_RETRY = 10

# Minutes to job cluster autodown. This should be significantly larger than
# managed_job_utils.JOB_STATUS_CHECK_GAP_SECONDS, to avoid tearing down the
# cluster before its status can be updated by the job controller.
_AUTODOWN_MINUTES = 10

# Substrings (case-insensitive) that identify an out-of-memory failure in a
# launch/setup exception message. The Kubernetes command runner enriches exec
# failures with the pod's termination reason (see
# kubernetes_utils.diagnose_terminated_pod), so 'OOMKilled' reliably appears
# here when a managed-job pod is OOM-killed during cluster/runtime setup.
_OOM_FAILURE_SIGNATURES = ('oomkilled', 'out of memory', 'out-of-memory')


def _is_oom_failure(exception: Exception) -> bool:
    """Whether `exception` indicates an out-of-memory pod termination."""
    message = common_utils.format_exception(exception).lower()
    return any(sig in message for sig in _OOM_FAILURE_SIGNATURES)


ENV_VARS_TO_CLEAR = [
    skypilot_config.ENV_VAR_SKYPILOT_CONFIG,
    constants.USER_ID_ENV_VAR,
    constants.USER_ENV_VAR,
    env_options.Options.SHOW_DEBUG_INFO.env_key,
    # If this is set, get_server_url() returns a non-local URL and
    # api_start refuses to start a local server. Always start local here.
    constants.SKY_API_SERVER_URL_ENV_VAR,
    # If this is set, api_start refuses to start a local server even when the
    # server URL is local (check_local_api_server_enabled_or_raise). The
    # controller always needs to start a local server here, so clear it too.
    env_options.Options.DISABLE_LOCAL_API_SERVER.env_key,
]

# Interval to poll the status of the underlying sky.launch request while
# streaming its logs, to detect that the request has been parked (WAITING).
_LAUNCH_REQUEST_STATUS_POLL_SECONDS = 30
# Poll backoff bounds while the job is parked waiting for its launch request
# to resume. The launch request resumes and completes on the API server
# independently of this poll, so the poll interval only affects how quickly
# the job re-acquires a launch slot and proceeds to job submission.
_PARKED_POLL_INITIAL_BACKOFF_SECONDS = 15
_PARKED_POLL_MAX_BACKOFF_FACTOR = 8
# Consecutive polls where a parked request is not found before concluding it
# is gone (a single empty response can be transient, e.g. the API server is
# briefly unreachable or mid-restart).
_PARKED_POLL_MAX_CONSECUTIVE_MISSING = 3
# Consecutive failed polls of a parked request before giving up on waiting
# for it (e.g. the API server is persistently unreachable). Unlike
# _PARKED_POLL_MAX_CONSECUTIVE_MISSING, this does NOT fall back to a fresh
# launch attempt - see _wait_for_parked_request.
_PARKED_POLL_MAX_CONSECUTIVE_ERRORS = 8

# Request statuses considered "still live" on the API server. Used to decide,
# on reattach, whether a carried stream that already failed can be replaced
# with a fresh one (request still live) or whether the request's outcome is
# already decided (request terminal or unknown) - see _launch's reattach
# path.
_LIVE_REQUEST_STATUS_VALUES = frozenset(
    s.value for s in requests_lib.RequestStatus.active_statuses())

# The blocking sync-SDK log stream of each inner launch request runs in a
# worker thread. Deliberately NOT asyncio.to_thread's default executor: a
# parked job keeps its stream attached across the park (the blocking stream
# cannot be interrupted from outside, and does not need to be), so the number
# of concurrent stream threads is bounded by the number of jobs with an
# in-flight launch request (up to MAX_JOBS_PER_WORKER per controller
# process), not by LAUNCHES_PER_WORKER - more than enough to exhaust the
# small default executor and stall every other to_thread call in the
# controller. The small headroom covers a vanished request's dying stream
# briefly overlapping its replacement.
_LAUNCH_STREAM_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=controller_utils.MAX_JOBS_PER_WORKER + 8,
    thread_name_prefix='launch-request-stream')


def _consume_task_exception(task: 'asyncio.Future') -> None:
    """Consume a finished task's exception to avoid asyncio warnings."""
    if task.cancelled():
        return
    exc = task.exception()
    if isinstance(exc, Exception):
        logger.debug('Abandoned launch request stream task failed: '
                     f'{common_utils.format_exception(exc)}')


class _LaunchRequestParked(Exception):
    """The underlying launch request was parked to wait for a condition.

    Raised while supervising the inner sky.launch request when the request is
    set to WAITING by the API server, i.e. the launch is waiting on some
    external condition (e.g. admission to a queue) and has yielded its
    executor worker. We mirror that at the job scheduling layer: exit the
    scheduled_launch context so the job releases its launch slot instead of
    holding it for the entire wait.

    Notably, this must not tear down the partially provisioned cluster: a
    parked launch keeps its resources (e.g. its position in an admission
    queue) and will reuse them on resume.
    """

    def __init__(self, request_id: str, status_msg: Optional[str]):
        super().__init__(status_msg or 'Launch request is waiting to resume.')
        self.request_id = request_id
        self.status_msg = status_msg


class StrategyExecutor:
    """Handle the launching, recovery and termination of managed job clusters"""

    RETRY_INIT_GAP_SECONDS = 60

    def __init__(
        self,
        cluster_name: Optional[str],
        backend: 'backends.Backend',
        task: 'task_lib.Task',
        max_restarts_on_errors: int,
        job_id: int,
        task_id: int,
        pool: Optional[str],
        starting: Set[int],
        starting_lock: asyncio.Lock,
        starting_signal: asyncio.Condition,
        recover_on_exit_codes: Optional[List[int]] = None,
        file_mounts_blob_id: Optional[str] = None,
    ) -> None:
        """Initialize the strategy executor.

        Args:
            cluster_name: The name of the cluster.
            backend: The backend to use. Only CloudVMRayBackend is supported.
            task: The task to execute.
            max_restarts_on_errors: Maximum number of restarts on errors.
            job_id: The ID of the job.
            task_id: The ID of the task.
            starting: Set of job IDs that are currently starting.
            starting_lock: Lock to synchronize starting jobs.
            starting_signal: Condition to signal when a job can start.
            recover_on_exit_codes: List of exit codes that should trigger
                recovery regardless of max_restarts_on_errors limit.
            file_mounts_blob_id: If set, the content-addressed blob id
                associated with this job's uploaded file mounts. It is
                forwarded to the inner ``sdk.launch`` so that whichever API
                server replica executes the launch can resolve the blob to
                its own extraction cache (critical under HA failover).
        """
        assert isinstance(backend, backends.CloudVmRayBackend), (
            'Only CloudVMRayBackend is supported.')
        self.dag = dag_lib.Dag()
        self.dag.add(task)
        # For jobs submitted to a pool, the cluster name might change after each
        # recovery. Initially this is set to an empty string to indicate that no
        # cluster is assigned yet, and in `_launch`, it will be set to one of
        # the cluster names in the pool.
        self.cluster_name = cluster_name
        self.backend = backend
        self.max_restarts_on_errors = max_restarts_on_errors
        self.recover_on_exit_codes = recover_on_exit_codes or []
        self.job_id = job_id
        self.task_id = task_id
        self.pool = pool
        self.restart_cnt_on_failure = 0
        self.job_id_on_pool_cluster: Optional[int] = None
        self.starting = starting
        self.starting_lock = starting_lock
        self.starting_signal = starting_signal
        self.file_mounts_blob_id = file_mounts_blob_id

    def set_strategy_config(self, config: dict) -> None:
        """Handle strategy-specific config from the job_recovery dict.

        Override in subclasses to accept custom parameters registered
        by plugins. Unknown keys are logged as warnings by default.

        Args:
            config: Remaining key-value pairs from the job_recovery dict
                after common keys (strategy, max_restarts_on_errors,
                recover_on_exit_codes) have been removed.
        """
        if config:
            logger.debug('Unused job_recovery config keys for strategy '
                         f'{type(self).__name__}: {list(config.keys())}')

    def extra_launch_context(self) -> Dict[str, Any]:
        """Return strategy-specific context for the launch pipeline.

        The returned dict is merged into ``_extra_launch_context``
        passed through ``sdk.launch()`` to the provisioner's
        ``template_override()``.
        """
        return {}

    def task_specs(self) -> Dict[str, Any]:
        """Return strategy-specific keys for the persisted task specs.

        Merged into the ``specs`` dict written by
        ``set_starting_async()``. Must not collide with base spec keys.
        """
        return {}

    async def on_resume(self, cluster_name: str) -> None:  # pylint: disable=unused-argument
        """Called before monitoring an already-launched task on resume.

        Subclasses use this to rehydrate state from persisted handles.
        """
        return None

    async def monitor_task(  # pylint: disable=unused-argument
        self,
        *,
        task_id: int,
        task: 'task_lib.Task',
        cluster_name: str,
        job_id_on_pool_cluster: Optional[int] = None,
        callback_func: Optional[Callable[..., Any]] = None,
        cleanup_cluster_on_success: bool = True,
        force_transit_to_recovering: bool = False,
        on_recovery: Optional[Callable[[], Coroutine[Any, Any, None]]] = None,
    ) -> Optional[bool]:
        """Strategy-owned monitoring loop override.

        # TODO(kevin): The default monitor (JobController._monitor_one_task)
        # bakes in cluster-level detection logic (skylet polling, cluster
        # status refresh, ExternalFailureSource). If we refactor detection
        # into pluggable strategy methods (e.g. check_status() returning a
        # uniform result), the controller could own a single generic loop
        # and this override would be unnecessary.

        Returns:
            None: fall back to OSS default monitor.
            True: task succeeded (strategy handled monitoring).
            False: task failed (strategy handled monitoring).
        """
        return None

    @classmethod
    def make(
        cls,
        cluster_name: Optional[str],
        backend: 'backends.Backend',
        task: 'task_lib.Task',
        job_id: int,
        task_id: int,
        pool: Optional[str],
        starting: Set[int],
        starting_lock: asyncio.Lock,
        starting_signal: asyncio.Condition,
        file_mounts_blob_id: Optional[str] = None,
    ) -> 'StrategyExecutor':
        """Create a strategy from a task."""

        # TODO(cooperc): Consider defaulting to FAILOVER if using k8s with a
        # single context, since there are not multiple clouds/regions to
        # failover through.
        resource_list = list(task.resources)
        # Copy to avoid mutating the original resources' job_recovery
        # dict, which would cause issues if make() is called more than
        # once on the same task.
        job_recovery = resource_list[0].job_recovery
        if isinstance(job_recovery, dict):
            job_recovery = dict(job_recovery)
        for resource in resource_list:
            if resource.job_recovery != job_recovery:
                raise ValueError(
                    'The job recovery strategy should be the same for all '
                    'resources.')
        # Remove the job_recovery field from the resources, as the strategy
        # will be handled by the strategy class.
        new_resources_list = [r.copy(job_recovery=None) for r in resource_list]
        # set the new_task_resources to be the same type (list or set) as the
        # original task.resources
        task.set_resources(type(task.resources)(new_resources_list))
        if isinstance(job_recovery, dict):
            name = job_recovery.pop(
                'strategy', registry.JOBS_RECOVERY_STRATEGY_REGISTRY.default)
            assert name is None or isinstance(name, str), (
                name, 'The job recovery strategy name must be a string or None')
            job_recovery_name: Optional[str] = name
            max_restarts_on_errors = job_recovery.pop('max_restarts_on_errors',
                                                      0)
            recover_exit_codes = job_recovery.pop('recover_on_exit_codes', None)
            # Normalize single integer to list
            recover_on_exit_codes: Optional[List[int]] = None
            if isinstance(recover_exit_codes, int):
                recover_on_exit_codes = [recover_exit_codes]
            elif isinstance(recover_exit_codes, list):
                recover_on_exit_codes = [
                    int(code) for code in recover_exit_codes
                ]
        else:
            job_recovery_name = job_recovery
            max_restarts_on_errors = 0
            recover_on_exit_codes = None
        # Remaining keys in the dict are strategy-specific config,
        # passed to the executor via set_strategy_config().
        strategy_config = dict(job_recovery) if isinstance(job_recovery,
                                                           dict) else {}

        job_recovery_strategy = (registry.JOBS_RECOVERY_STRATEGY_REGISTRY.
                                 from_str(job_recovery_name))
        assert job_recovery_strategy is not None, job_recovery_name
        executor = job_recovery_strategy(cluster_name, backend, task,
                                         max_restarts_on_errors, job_id,
                                         task_id, pool, starting, starting_lock,
                                         starting_signal, recover_on_exit_codes,
                                         file_mounts_blob_id)
        executor.set_strategy_config(strategy_config)
        return executor

    async def launch(self) -> float:
        """Launch the cluster for the first time.

        It can fail if resource is not available. Need to check the cluster
        status, after calling.

        Returns: The job's submit timestamp, on success (otherwise, an
            exception is raised).

        Raises: Please refer to the docstring of self._launch().
        """

        job_submit_at = await self._launch(max_retry=None)
        assert job_submit_at is not None
        return job_submit_at

    async def recover(self) -> float:
        """Relaunch the cluster after failure and wait until job starts.

        When recover() is called the cluster should be in STOPPED status (i.e.
        partially down).

        Returns: The timestamp job started.
        """
        raise NotImplementedError

    async def _try_cancel_jobs(self):
        if self.cluster_name is None:
            return
        handle = await asyncio.to_thread(
            global_user_state.get_handle_from_cluster_name, self.cluster_name)
        if handle is None or self.pool is not None:
            return
        try:
            usage_lib.messages.usage.set_internal()
            # Note that `sky.cancel()` may not go through for a variety of
            # reasons:
            # (1) head node is preempted; or
            # (2) somehow user programs escape the cancel codepath's kill.
            # The latter is silent and is a TODO.
            #
            # For the former, an exception will be thrown, in which case we
            # fallback to terminate_cluster() in the except block below. This
            # is because in the event of recovery on the same set of remaining
            # worker nodes, we don't want to leave some old job processes
            # running.
            # TODO(zhwu): This is non-ideal and we should figure out another way
            # to reliably cancel those processes and not have to down the
            # remaining nodes first.
            #
            # In the case where the worker node is preempted, the `sky.cancel()`
            # should be functional with the `_try_cancel_if_cluster_is_init`
            # flag, i.e. it sends the cancel signal to the head node, which will
            # then kill the user process on remaining worker nodes.
            # Only cancel the corresponding job for pool.
            if self.pool is None:
                request_id = await asyncio.to_thread(
                    sdk.cancel,
                    cluster_name=self.cluster_name,
                    all=True,
                    _try_cancel_if_cluster_is_init=True,
                )
            else:
                request_id = await asyncio.to_thread(
                    sdk.cancel,
                    cluster_name=self.cluster_name,
                    job_ids=[self.job_id_on_pool_cluster],
                    _try_cancel_if_cluster_is_init=True,
                )
            logger.debug(f'sdk.cancel request ID: {request_id}')
            await asyncio.to_thread(
                sdk.get,
                request_id,
            )
        except Exception as e:  # pylint: disable=broad-except
            logger.info('Failed to cancel the job on the cluster. The cluster '
                        'might be already down or the head node is preempted.'
                        '\n  Detailed exception: '
                        f'{common_utils.format_exception(e)}\n'
                        'Terminating the cluster explicitly to ensure no '
                        'remaining job process interferes with recovery.')
            await asyncio.to_thread(self._cleanup_cluster)

    async def _wait_until_job_starts_on_cluster(self) -> Optional[float]:
        """Wait for MAX_JOB_CHECKING_RETRY times until job starts on the cluster

        Returns:
            The timestamp of when the job is submitted, or None if failed to
            submit.
        """
        assert self.cluster_name is not None
        status = None
        job_checking_retry_cnt = 0
        while job_checking_retry_cnt < MAX_JOB_CHECKING_RETRY:
            # Avoid the infinite loop, if any bug happens.
            job_checking_retry_cnt += 1
            try:
                cluster_status, _ = (await asyncio.to_thread(
                    backend_utils.refresh_cluster_status_handle,
                    self.cluster_name,
                    force_refresh_statuses=set(status_lib.ClusterStatus)))
            except Exception as e:  # pylint: disable=broad-except
                # If any unexpected error happens, retry the job checking
                # loop.
                # TODO(zhwu): log the unexpected error to usage collection
                # for future debugging.
                logger.info(f'Unexpected exception: {e}\nFailed to get the '
                            'refresh the cluster status. Retrying.')
                continue
            if cluster_status not in (status_lib.ClusterStatus.UP,
                                      status_lib.ClusterStatus.AUTOSTOPPING):
                # The cluster can be preempted before the job is
                # launched.
                # Break to let the retry launch kick in.
                logger.info('The cluster is preempted before the job '
                            'is submitted.')
                # TODO(zhwu): we should recover the preemption with the
                # recovery strategy instead of the current while loop.
                break

            try:
                status, transient_error_reason = (
                    await managed_job_utils.get_job_status(
                        self.backend,
                        self.cluster_name,
                        job_id=self.job_id_on_pool_cluster))
            except Exception as e:  # pylint: disable=broad-except
                transient_error_reason = common_utils.format_exception(e)
                # If any unexpected error happens, retry the job checking
                # loop.
                # Note: the CommandError is already handled in the
                # get_job_status, so it should not happen here.
                # TODO(zhwu): log the unexpected error to usage collection
                # for future debugging.
                logger.info('Unexpected exception during fetching job status: '
                            f'{common_utils.format_exception(e)}')
                continue
            if transient_error_reason is not None:
                logger.info('Transient error when fetching the job status: '
                            f'{transient_error_reason}')
                continue

            # Check the job status until it is not in initialized status
            if status is not None and status > job_lib.JobStatus.INIT:
                if managed_job_runtime.is_registered():
                    handle = await asyncio.to_thread(
                        global_user_state.get_handle_from_cluster_name,
                        self.cluster_name)
                    runtime_submitted_at = await asyncio.to_thread(
                        managed_job_runtime.get_job_submitted_at, handle,
                        self.cluster_name)
                    if runtime_submitted_at is not None:
                        return runtime_submitted_at
                try:
                    job_submitted_at = await asyncio.to_thread(
                        managed_job_utils.get_job_timestamp,
                        self.backend,
                        self.cluster_name,
                        self.job_id_on_pool_cluster,
                        get_end_time=False)
                    return job_submitted_at
                except Exception as e:  # pylint: disable=broad-except
                    # If we failed to get the job timestamp, we will retry
                    # job checking loop.
                    logger.info(f'Unexpected Exception: {e}\nFailed to get '
                                'the job start timestamp. Retrying.')
                    continue
            # Wait for the job to be started
            await asyncio.sleep(
                managed_job_utils.JOB_STARTED_STATUS_CHECK_GAP_SECONDS)
        return None

    def _cleanup_cluster(self) -> None:
        if self.cluster_name is None:
            return
        if self.pool is None:
            managed_job_utils.terminate_cluster(self.cluster_name)

    def _refresh_priority_from_persisted_dag(self) -> None:
        """Re-read the persisted job DAG and apply any updated priority.

        A managed job's priority can be changed out of band after submission
        by rewriting the persisted DAG. The controller otherwise caches the
        DAG in memory for its lifetime, so without this refresh a recovery
        would relaunch at the original priority. Only the priority (and its
        optional priority class) is re-read here; the rest of the in-memory
        task — envs, file mounts, name — is preserved.
        """
        try:
            content = file_content_utils.get_job_dag_content(self.job_id)
            if content is None:
                return
            fresh_dag = dag_utils.load_dag_from_yaml_str(content)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(
                f'Failed to re-read persisted DAG for job {self.job_id}; '
                f'keeping current priority: {e}')
            return
        if self.task_id >= len(fresh_dag.tasks) or not self.dag.tasks:
            return
        fresh_resources = list(fresh_dag.tasks[self.task_id].resources)
        if not fresh_resources:
            return
        # Priority is uniform across a task's resources; take the first.
        new_priority = fresh_resources[0].priority
        new_priority_class = fresh_resources[0].priority_class
        task = self.dag.tasks[0]
        changed = False
        new_resources = []
        for r in task.resources:
            if (r.priority != new_priority or
                    r.priority_class != new_priority_class):
                r = r.copy(priority=new_priority,
                           priority_class=new_priority_class)
                changed = True
            new_resources.append(r)
        if changed:
            # task.resources may be a list or a set; rebuild with the original
            # container type so the semantics are preserved (mirrors
            # Task.set_resources_override).
            task.set_resources(type(task.resources)(new_resources))
            logger.info(
                f'Refreshed priority for job {self.job_id} to {new_priority} '
                f'(priority_class={new_priority_class}) from persisted DAG.')

    async def _cancel_launch_request(self, request_id: str) -> None:
        """Cancel an inner launch/exec request of this job."""
        try:
            req = await asyncio.to_thread(sdk.api_cancel, request_id)
        except Exception as e:  # pylint: disable=broad-except
            # If we can't even submit the cancel (e.g. the API server is
            # unreachable), there is nothing more we can do here. This must
            # not raise: callers - notably the asyncio.CancelledError
            # handler in _launch - rely on this being best-effort, so that
            # an unrelated failure here does not replace the in-flight
            # cancellation.
            logger.error(f'Failed to cancel the request: {e}')
            return
        logger.debug(f'sdk.api_cancel request ID: {req}')
        try:
            await asyncio.to_thread(sdk.get, req)
        except Exception as e:  # pylint: disable=broad-except
            # we must still propagate the cancellation
            logger.error(f'Failed to cancel the request: {e}')

    async def _get_request_payload(self, request_id: str) -> Optional[Any]:
        """Fetch the status and status_msg of a request.

        Returns:
            The request payload (with status and status_msg fields), or None
            if the request is unknown to the API server.

        Raises:
            Exception: If the API server could not be reached.
        """
        request_payloads = await asyncio.to_thread(
            sdk.api_status,
            request_ids=[request_id],
            fields=['status', 'status_msg'])
        if not request_payloads:
            return None
        return request_payloads[0]

    def _start_stream_task(self, request_id: str) -> 'asyncio.Future':
        """Start awaiting the inner launch request in a worker thread.

        Streams the launch request's logs into this controller's per-job log,
        relaying the encoded rich-status payloads so that `sky jobs launch` /
        `sky jobs logs` can re-render the provisioning spinner, matching the
        `sky launch` experience (see
        :func:`sky.utils.rich_utils.decode_rich_status`).

        The returned future completes with the request's result, or raises
        the request's own exception. Transient stream interruptions (e.g. an
        API server rolling restart or a connection reset) are retried
        transparently inside the SDK (see
        :func:`sky.server.rest.retry_transient_errors`).

        Uses context_utils.to_thread_with_executor (not
        loop.run_in_executor/asyncio.to_thread directly) so that the worker
        thread inherits a copy of this coroutine's contextvars context - in
        particular, the per-job log/stdout redirection is contextvars-based,
        and without it the request's log stream would bypass this job's log
        file and land in the shared controller log.
        """

        # A plain wrapper (rather than passing sdk.stream_and_get directly)
        # sidesteps a ParamSpec/@typing.overload interaction that mypy
        # cannot resolve through to_thread_with_executor.
        def _stream_and_get() -> Any:
            return sdk.stream_and_get(typing.cast(server_common.RequestId,
                                                  request_id),
                                      relay_rich_status=True)

        return context_utils.to_thread_with_executor(_LAUNCH_STREAM_EXECUTOR,
                                                     _stream_and_get)

    async def _await_launch_request(self, request_id: str,
                                    stream_task: 'asyncio.Future') -> None:
        """Wait for the inner launch request, detecting if it parks.

        While waiting on the request's log stream (started by
        _start_stream_task), periodically poll the request's status: if the
        API server has parked the request as WAITING (the request yielded its
        executor worker to wait for some external condition, e.g. admission
        to a queue), raise _LaunchRequestParked so that the caller can
        release this job's launch slot for the duration of the wait.

        The stream task is deliberately left running when parking: the
        blocking sync stream cannot be interrupted from outside, and it does
        not need to be - the caller carries it across the park and re-awaits
        it on resume. The server keeps the stream open (with heartbeats)
        while the request is WAITING, so the same stream picks up where it
        left off, with no reconnect and no replayed log lines.

        Raises:
            _LaunchRequestParked: The request was parked as WAITING.
            Exception: Any exception raised by the launch request itself.
        """
        while True:
            done, _ = await asyncio.wait(
                {stream_task}, timeout=_LAUNCH_REQUEST_STATUS_POLL_SECONDS)
            if done:
                # Surface the exception of the request, if any.
                stream_task.result()
                return
            try:
                request_payload = await self._get_request_payload(request_id)
            except Exception as e:  # pylint: disable=broad-except
                # Tolerate transient failures of the status poll - the
                # stream is still the authoritative wait.
                logger.debug('Failed to poll the status of launch '
                             f'request {request_id}: {e}')
                continue
            if request_payload is None:
                # Request unknown to the server. Keep waiting on the
                # stream, which will fail or complete on its own.
                continue
            if (request_payload.status ==
                    requests_lib.RequestStatus.WAITING.value):
                raise _LaunchRequestParked(request_id,
                                           request_payload.status_msg)

    async def _wait_for_parked_request(self, request_id: str) -> Optional[str]:
        """Wait until a parked launch request is no longer WAITING.

        The request resumes (and continues provisioning) on the API server on
        its own once the condition it is waiting for is met; this poll only
        determines when this job should re-acquire a launch slot and
        re-attach to the request.

        Cancellation is handled by the caller (_launch), which cancels the
        outstanding parked request on asyncio.CancelledError.

        Returns:
            The request id to re-attach to, or None if the request has
            genuinely vanished from the API server (e.g. lost across a
            server restart), in which case a fresh launch attempt should be
            made.

        Raises:
            Exception: If the status poll persistently fails (e.g. the API
                server is unreachable, as opposed to reachable-but-unaware-
                of-the-request). This is deliberately NOT treated the same
                as "vanished": the old request may still be parked
                server-side and could resume once the server is reachable
                again, so falling back to a fresh launch here could
                double-launch on the same cluster. The caller propagates
                this with parked_request_id still set, so it is best-effort
                cancelled instead.
        """
        poll_backoff = common_utils.Backoff(
            _PARKED_POLL_INITIAL_BACKOFF_SECONDS,
            _PARKED_POLL_MAX_BACKOFF_FACTOR)
        consecutive_missing = 0
        consecutive_errors = 0
        while True:
            await asyncio.sleep(poll_backoff.current_backoff())
            try:
                request_payload = await self._get_request_payload(request_id)
            except Exception as e:  # pylint: disable=broad-except
                consecutive_errors += 1
                if consecutive_errors >= _PARKED_POLL_MAX_CONSECUTIVE_ERRORS:
                    # Do not fall back to the vanished-request path here -
                    # see the Raises section of the docstring above.
                    logger.warning(
                        'Repeatedly failed to poll the status of parked '
                        f'launch request {request_id}. Last error: {e}')
                    raise
                logger.debug('Failed to poll the status of parked launch '
                             f'request {request_id}: {e}')
                continue
            consecutive_errors = 0
            if request_payload is None:
                # A single empty response can be transient (e.g. the
                # controller-local API server is mid-restart) - require
                # multiple consecutive misses before concluding the request
                # is gone.
                consecutive_missing += 1
                if consecutive_missing >= _PARKED_POLL_MAX_CONSECUTIVE_MISSING:
                    logger.info(f'Parked launch request {request_id} no '
                                'longer exists on the API server. Will make '
                                'a new launch attempt.')
                    break
                continue
            consecutive_missing = 0
            if (request_payload.status !=
                    requests_lib.RequestStatus.WAITING.value):
                return request_id
        # The request is genuinely gone (the persistently-unreachable case
        # above raises instead of reaching here). Best-effort cancel the old
        # request so that it cannot resume concurrently with the fresh
        # launch attempt on the same cluster.
        with contextlib.suppress(Exception):
            await self._cancel_launch_request(request_id)
        return None

    async def _launch(self,
                      max_retry: Optional[int] = 3,
                      raise_on_failure: bool = True,
                      recovery: bool = False) -> Optional[float]:
        """Implementation of launch().

        The function will wait until the job starts running, but will leave the
        handling for the preemption to the caller.

        Args:
            max_retry: The maximum number of retries. If None, retry forever.
            raise_on_failure: Whether to raise an exception if the launch fails.

        Returns:
            The job's submit timestamp, or None if failed to submit the job
            (either provisioning fails or any error happens in job submission)
            and raise_on_failure is False.

        Raises:
            non-exhaustive list of exceptions:
            exceptions.ProvisionPrechecksError: This will be raised when the
                underlying `sky.launch` fails due to precheck errors only.
                I.e., none of the failover exceptions, if
                any, is due to resources unavailability. This exception
                includes the following cases:
                1. The optimizer cannot find a feasible solution.
                2. Precheck errors: invalid cluster name, failure in getting
                cloud user identity, or unsupported feature.
            exceptions.ManagedJobReachedMaxRetriesError: This will be raised
                when all prechecks passed but the maximum number of retries is
                reached for `sky.launch`. The failure of `sky.launch` can be
                due to:
                1. Any of the underlying failover exceptions is due to resources
                unavailability.
                2. The cluster is preempted before the job is submitted.
                3. Any unexpected error happens during the `sky.launch`.
        Other exceptions may be raised depending on the backend.
        """
        # On recovery, re-read the persisted DAG so an out-of-band priority
        # change takes effect on this relaunch (the controller caches the DAG
        # in memory for its lifetime).
        if recovery:
            self._refresh_priority_from_persisted_dag()
        # TODO(zhwu): handle the failure during `preparing sky runtime`.
        retry_cnt = 0
        backoff = common_utils.Backoff(self.RETRY_INIT_GAP_SECONDS)
        # Request id (and its carried log-stream future) of a launch request
        # that was parked (WAITING) while we were waiting for it, to re-attach
        # to on the next attempt instead of submitting a new launch. Set by
        # the _LaunchRequestParked handler below; consumed by the inner
        # try/except once it takes ownership of the request. While set, the
        # asyncio.CancelledError handler below cancels the outstanding
        # request if the job is cancelled.
        parked_request_id: Optional[str] = None
        parked_stream_task: Optional['asyncio.Future'] = None
        parked_reason: Optional[str] = None
        while True:
            retry_cnt += 1
            # Whether this iteration is resuming from a park, i.e. the
            # top-of-loop block below runs and sets the task back to
            # PENDING. Reset every iteration; distinct from
            # reattach_request_id below, which is None when the parked
            # request has vanished even though the task was still set
            # PENDING for the wait.
            resuming_from_park = False
            # The stream future of this attempt's launch request; assigned in
            # the inner try below, referenced by the park handler.
            stream_task: Optional['asyncio.Future'] = None
            try:
                if parked_request_id is not None:
                    resuming_from_park = True
                    if parked_reason is not None:
                        # The task was STARTING/RECOVERING; set it back to
                        # PENDING (with the park reason) while we wait for
                        # the request to resume.
                        await state.set_backoff_pending_async(
                            self.job_id, self.task_id, reason=parked_reason)
                        parked_reason = None
                    resumed_request_id = await self._wait_for_parked_request(
                        parked_request_id)
                    if (resumed_request_id is None and
                            parked_stream_task is not None):
                        # The request vanished; its carried stream will fail
                        # or finish on its own - consume its result and make
                        # a fresh launch attempt.
                        parked_stream_task.add_done_callback(
                            _consume_task_exception)
                        parked_stream_task = None
                    parked_request_id = resumed_request_id
                async with scheduler.scheduled_launch(
                        self.job_id,
                        self.starting,
                        self.starting_lock,
                        self.starting_signal,
                ):
                    # Note: parked_request_id stays set until the inner
                    # try/except below takes ownership of the request, so
                    # that the outer asyncio.CancelledError handler can
                    # cancel the outstanding request if the job is cancelled
                    # in the meantime (e.g. while waiting for a launch slot
                    # above).
                    reattach_request_id = parked_request_id
                    reattach_stream_task = parked_stream_task
                    # The job state may have been PENDING during backoff or
                    # while the launch request was parked - update to STARTING
                    # or RECOVERING. Gate on resuming_from_park rather than
                    # reattach_request_id: even if the parked request
                    # vanished (reattach_request_id is None here), the
                    # top-of-loop block above already set the task PENDING
                    # while waiting, and it needs to be restored before the
                    # fresh launch attempt below.
                    # On the first attempt (when retry_cnt is 1 and we did
                    # not just resume from a park), we should already be in
                    # STARTING or RECOVERING.
                    if retry_cnt > 1 or resuming_from_park:
                        await state.set_restarting_async(
                            self.job_id, self.task_id, recovery)
                    try:
                        usage_lib.messages.usage.set_internal()
                        if self.pool is None:
                            assert self.cluster_name is not None

                            if reattach_request_id is None:
                                # sdk.launch will implicitly start the API
                                # server, but then the API server will inherit
                                # the current env vars/user, which we may not
                                # want.
                                # Instead, clear env vars here and call
                                # api_start explicitly.
                                vars_to_restore = {}
                                try:
                                    for env_var in ENV_VARS_TO_CLEAR:
                                        vars_to_restore[env_var] = (
                                            os.environ.pop(env_var, None))
                                        logger.debug('Cleared env var: '
                                                     f'{env_var}')
                                    logger.debug('Env vars for api_start: '
                                                 f'{os.environ}')
                                    await asyncio.to_thread(sdk.api_start)
                                    logger.info('API server started.')
                                finally:
                                    for env_var, value in (
                                            vars_to_restore.items()):
                                        if value is not None:
                                            logger.debug('Restored env var: '
                                                         f'{env_var}: {value}')
                                            os.environ[env_var] = value

                                # HA failover may land the controller on new
                                # hosts, ensure blob extraction on the current
                                # host
                                if self.file_mounts_blob_id is not None:
                                    await asyncio.to_thread(
                                        server_common.resolve_blob_dir,
                                        self.file_mounts_blob_id,
                                        common_utils.get_user_hash())

                            request_id: Optional[str] = None
                            try:
                                if reattach_request_id is None:
                                    extra_ctx = self.extra_launch_context()
                                    request_id = await asyncio.to_thread(
                                        sdk.launch,
                                        self.dag,
                                        cluster_name=self.cluster_name,
                                        # We expect to tear down the cluster as
                                        # soon as the job is finished. However,
                                        # in case the controller dies, set
                                        # autodown to try and avoid a resource
                                        # leak.
                                        idle_minutes_to_autostop=(
                                            _AUTODOWN_MINUTES),
                                        down=True,
                                        _is_launched_by_jobs_controller=True,
                                        _file_mounts_blob_id=(
                                            self.file_mounts_blob_id),
                                        _extra_launch_context=(
                                            extra_ctx if extra_ctx else None),
                                    )
                                    logger.debug('sdk.launch request ID: '
                                                 f'{request_id}')
                                    stream_task = self._start_stream_task(
                                        request_id)
                                else:
                                    request_id = reattach_request_id
                                    stream_task = reattach_stream_task
                                    # Ownership of the request transfers to
                                    # this try's CancelledError handler.
                                    parked_request_id = None
                                    parked_stream_task = None
                                    logger.info('Re-attaching to launch '
                                                f'request {request_id}.')
                                    if (stream_task is not None and
                                            stream_task.done() and
                                            stream_task.exception()
                                            is not None):
                                        # The carried stream already failed -
                                        # e.g. a transient transport error
                                        # while the API server was briefly
                                        # unreachable during the park - even
                                        # though the request itself may have
                                        # survived and already resumed.
                                        # Trusting this stale error would
                                        # tear down an otherwise healthy
                                        # cluster, so don't let it propagate
                                        # below: consume it and check the
                                        # request's real state instead.
                                        stream_task.add_done_callback(
                                            _consume_task_exception)
                                        request_payload = (
                                            await self._get_request_payload(
                                                request_id))
                                        if (request_payload is not None and
                                                request_payload.status
                                                in _LIVE_REQUEST_STATUS_VALUES):
                                            # Still live server-side - start a
                                            # fresh stream to finish waiting
                                            # on it instead of the dead one.
                                            stream_task = (
                                                self._start_stream_task(
                                                    request_id))
                                        else:
                                            # Terminal (or unknown to the
                                            # server): the outcome is already
                                            # decided. Surface it directly -
                                            # this raises the request's own
                                            # exception if it failed - rather
                                            # than waiting on a stream that
                                            # will never produce one.
                                            result: Any = (
                                                await asyncio.to_thread(
                                                    sdk.get,
                                                    typing.cast(
                                                        server_common.RequestId,
                                                        request_id)))
                                            stream_task = (
                                                asyncio.get_running_loop(
                                                ).create_future())
                                            stream_task.set_result(result)
                                assert stream_task is not None
                                await self._await_launch_request(
                                    request_id, stream_task)
                            except asyncio.CancelledError:
                                if request_id:
                                    await self._cancel_launch_request(request_id
                                                                     )
                                if stream_task is not None:
                                    # The stream ends once the cancelled
                                    # request goes terminal; consume its
                                    # result to avoid asyncio warnings.
                                    stream_task.add_done_callback(
                                        _consume_task_exception)
                                raise
                            logger.info('Managed job cluster launched.')
                        else:
                            # Get task resources from DAG for resource-aware
                            # scheduling.
                            task_resources = None
                            if self.dag.tasks:
                                task = self.dag.tasks[self.task_id]
                                task_resources = task.resources

                            self.cluster_name = await (asyncio.to_thread(
                                serve_utils.get_next_cluster_name, self.pool,
                                self.job_id, task_resources))
                            if self.cluster_name is None:
                                raise exceptions.NoClusterLaunchedError(
                                    'No cluster name found in the pool.')
                            request_id = None
                            try:
                                request_id = await asyncio.to_thread(
                                    sdk.exec,
                                    self.dag,
                                    cluster_name=self.cluster_name,
                                )
                                logger.debug('sdk.exec request ID: '
                                             f'{request_id}')
                                job_id_on_pool_cluster, _ = (await
                                                             asyncio.to_thread(
                                                                 sdk.get,
                                                                 request_id))
                            except asyncio.CancelledError:
                                if request_id:
                                    await self._cancel_launch_request(request_id
                                                                     )
                                raise
                            assert job_id_on_pool_cluster is not None, (
                                self.cluster_name, self.job_id)
                            self.job_id_on_pool_cluster = job_id_on_pool_cluster
                            await state.set_job_id_on_pool_cluster_async(
                                self.job_id, job_id_on_pool_cluster)
                        logger.info('Managed job cluster launched.')
                    except _LaunchRequestParked:
                        # Not a launch failure - handled by the outer loop,
                        # which releases the launch slot while the request is
                        # parked. Notably, this must not fall through to the
                        # teardown/backoff path below: the parked launch keeps
                        # its partially provisioned resources.
                        raise
                    except (exceptions.InvalidClusterNameError,
                            exceptions.NoCloudAccessError,
                            exceptions.ResourcesMismatchError,
                            exceptions.StorageSpecError,
                            exceptions.StorageError) as e:
                        logger.error('Failure happened before provisioning. '
                                     f'{common_utils.format_exception(e)}')
                        if raise_on_failure:
                            raise exceptions.ProvisionPrechecksError(
                                reasons=[e])
                        return None
                    except exceptions.ResourcesUnavailableError as e:
                        # This is raised when the launch fails due to prechecks
                        # or after failing over through all the candidates.
                        # Please refer to the docstring of `sky.launch` for more
                        # details of how the exception will be structured.
                        if not any(
                                isinstance(err,
                                           exceptions.ResourcesUnavailableError)
                                for err in e.failover_history):
                            # _launch() (this function) should fail/exit
                            # directly, if none of the failover reasons were
                            # because of resource unavailability or no failover
                            # was attempted (the optimizer cannot find feasible
                            # resources for requested resources), i.e.,
                            # e.failover_history is empty. Failing directly
                            # avoids the infinite loop of retrying the launch
                            # when, e.g., an invalid cluster name is used and
                            # --retry-until-up is specified.
                            reasons = (e.failover_history
                                       if e.failover_history else [e])
                            reasons_str = '; '.join(
                                common_utils.format_exception(err)
                                for err in reasons)
                            logger.error(
                                'Failure happened before provisioning. '
                                f'Failover reasons: {reasons_str}')
                            if raise_on_failure:
                                raise exceptions.ProvisionPrechecksError(
                                    reasons)
                            return None
                        logger.info('Failed to launch a cluster with error: '
                                    f'{common_utils.format_exception(e)})')
                    except Exception as e:  # pylint: disable=broad-except
                        # A pod OOM during cluster/runtime setup is
                        # deterministic (e.g. the requested memory is too
                        # low) -- retrying just OOMs again. Fail fast with a
                        # terminal error carrying the OOM reason instead of
                        # looping forever in the launch-retry path below. Other
                        # failures fall through and are recovered below.
                        if raise_on_failure and _is_oom_failure(e):
                            logger.error(
                                'Cluster setup failed due to out-of-memory: '
                                f'{common_utils.format_exception(e)}')
                            with ux_utils.print_exception_no_traceback():
                                raise exceptions.ClusterSetUpError(
                                    str(e)) from e
                        logger.info('Failed to launch a cluster with error: '
                                    f'{common_utils.format_exception(e)})')
                        with ux_utils.enable_traceback():
                            logger.info(
                                f'  Traceback: {traceback.format_exc()}')
                    else:  # No exception, the launch succeeds.
                        # At this point, a sky.launch() has succeeded. Cluster
                        # may be UP (no preemption since) or DOWN (newly
                        # preempted).
                        # Auto-populate instance links if cluster is on a real
                        # cloud
                        if self.cluster_name is not None and self.pool is None:
                            try:
                                handle = await asyncio.to_thread(
                                    global_user_state.
                                    get_handle_from_cluster_name,
                                    self.cluster_name)
                                if (handle is not None and hasattr(
                                        handle, 'cached_cluster_info') and
                                        handle.cached_cluster_info is not None):
                                    cluster_info = handle.cached_cluster_info
                                    instance_links = (instance_links_utils.
                                                      generate_instance_links(
                                                          cluster_info,
                                                          self.cluster_name))
                                    if instance_links:
                                        # Store instance links directly in
                                        # database
                                        await state.update_links_async(
                                            self.job_id, self.task_id,
                                            instance_links)
                                        logger.debug(
                                            f'Auto-populated instance links: '
                                            f'{instance_links}')
                                    else:
                                        logger.debug('Failed to generate '
                                                     'instance links')
                                else:
                                    logger.debug(
                                        'Cluster handle not found or '
                                        'cached cluster info is None so'
                                        'not populating instance links')
                            except Exception as e:  # pylint: disable=broad-except
                                # Don't fail the launch if we can't generate
                                # links
                                logger.debug(
                                    'Failed to auto-populate instance links: '
                                    f'{e}')
                        else:
                            if self.pool:
                                logger.debug('Not populating instance links '
                                             'since the cluster is for a pool')
                            else:
                                logger.debug('Not populating instance links '
                                             'since the cluster name is None')
                        job_submitted_at = await (
                            self._wait_until_job_starts_on_cluster())
                        if job_submitted_at is not None:
                            return job_submitted_at
                        # The job fails to start on the cluster, retry the
                        # launch.
                        # TODO(zhwu): log the unexpected error to usage
                        # collection for future debugging.
                        logger.info(
                            'Failed to successfully submit the job to the '
                            'launched cluster, due to unexpected submission '
                            'errors or the cluster being preempted during '
                            'job submission.')

                    # If we get here, the launch did not succeed. Tear down the
                    # cluster and retry.
                    await asyncio.to_thread(self._cleanup_cluster)
                    if max_retry is not None and retry_cnt >= max_retry:
                        # Retry forever if max_retry is None.
                        if raise_on_failure:
                            with ux_utils.print_exception_no_traceback():
                                raise (
                                    exceptions.ManagedJobReachedMaxRetriesError(
                                        'Resources unavailable: failed to '
                                        f'launch clusters after {max_retry} '
                                        'retries.'))
                        else:
                            return None

                    # Raise NoClusterLaunchedError to indicate that the job is
                    # in retry backoff. We will exit the scheduled_launch
                    # context so that the launch slot is released during the
                    # backoff. This allows other jobs to launch.
                    raise exceptions.NoClusterLaunchedError()

            except _LaunchRequestParked as e:
                # The underlying launch request yielded its executor worker
                # and is waiting to resume (e.g. waiting for admission to a
                # queue). Mirror it at this layer: we have exited the
                # scheduled_launch context above, releasing this job's launch
                # slot, so that other jobs (including higher-priority ones)
                # can launch while this job waits. This mirrors the
                # retry-backoff path below, except that:
                # - the cluster is NOT torn down: the parked launch keeps its
                #   partially provisioned resources (e.g. its position in an
                #   admission queue), and
                # - we wait for the request to resume instead of sleeping a
                #   fixed backoff, and then re-attach to the same request.
                # The waiting itself happens at the top of the next loop
                # iteration: this handler must not await anything, since an
                # exception raised inside an except block (e.g. a
                # cancellation delivered at an await) would bypass the
                # sibling CancelledError handler below.
                retry_cnt -= 1  # Parking is not a failed launch attempt.
                parked_request_id = e.request_id
                # Carry the stream across the park: the blocking stream
                # cannot (and need not) be interrupted; it is re-awaited on
                # resume.
                parked_stream_task = stream_task
                parked_reason = 'Job is waiting to launch'
                if e.status_msg:
                    parked_reason = f'{parked_reason}: {e.status_msg}'
                logger.info(f'Launch request {e.request_id} is parked '
                            f'({e.status_msg}). Releasing the launch slot '
                            'while waiting for it to resume.')
                continue
            except asyncio.CancelledError:
                # The job was cancelled while an inner launch request from
                # the park path is still outstanding (parked, or resumed but
                # not yet re-attached - e.g. while waiting for a launch slot
                # above). These windows are outside the inner try/except that
                # covers the request while we are attached to it, so cancel
                # the request here.
                if parked_request_id is not None:
                    await self._cancel_launch_request(parked_request_id)
                if parked_stream_task is not None:
                    # The carried stream ends once the cancelled request goes
                    # terminal; consume its result to avoid asyncio warnings.
                    parked_stream_task.add_done_callback(
                        _consume_task_exception)
                raise
            except exceptions.NoClusterLaunchedError:
                # Update the status to PENDING during backoff.
                await state.set_backoff_pending_async(self.job_id, self.task_id)
                # Calculate the backoff time and sleep.
                gap_seconds = (backoff.current_backoff()
                               if self.pool is None else 1)
                logger.info('Retrying to launch the cluster in '
                            f'{gap_seconds:.1f} seconds.')
                await asyncio.sleep(gap_seconds)
                continue
            except Exception:
                # Between the park handler above and the inner try/except
                # taking ownership of the request (parked_request_id = None),
                # several awaits run with the request still parked
                # (set_backoff_pending_async, scheduled_launch.__aenter__,
                # set_restarting_async) and can raise, e.g.
                # ManagedJobStatusError or a DB error. Unlike the
                # asyncio.CancelledError case above, this exception must
                # propagate unchanged - but without this handler it would
                # escape while the request is still parked (and possibly
                # about to resume) on the API server, unattended. Mirror the
                # CancelledError handler's cleanup, best-effort: a failure to
                # cancel here must not replace the original exception.
                if parked_request_id is not None:
                    with contextlib.suppress(Exception):
                        await self._cancel_launch_request(parked_request_id)
                    if parked_stream_task is not None:
                        # The carried stream ends once the cancelled request
                        # goes terminal (or on its own if the cancel above
                        # failed); consume its result to avoid asyncio
                        # warnings.
                        parked_stream_task.add_done_callback(
                            _consume_task_exception)
                raise
            else:
                # The inner loop should either return or throw
                # NoClusterLaunchedError.
                assert False, 'Unreachable'

    def should_restart_on_failure(self,
                                  exit_codes: Optional[List[int]] = None
                                 ) -> bool:
        """Increments counter & checks if job should be restarted on a failure.

        Args:
            exit_codes: List of exit codes from the failed job. If any exit code
                matches recover_on_exit_codes, recovery will be triggered
                regardless of max_restarts_on_errors limit.

        Returns:
            True if the job should be restarted, otherwise False.
        """
        # Check if any exit code matches the configured recover_on_exit_codes
        # This triggers recovery without incrementing the counter
        if exit_codes and self.recover_on_exit_codes:
            for exit_code in exit_codes:
                if exit_code in self.recover_on_exit_codes:
                    logger.info(f'Exit code {exit_code} matched '
                                'recover_on_exit_codes, triggering recovery')
                    return True

        # Otherwise, check the max_restarts_on_errors counter
        self.restart_cnt_on_failure += 1
        if self.restart_cnt_on_failure > self.max_restarts_on_errors:
            return False
        logger.info(f'Restart count {self.restart_cnt_on_failure} '
                    'is less than max_restarts_on_errors, '
                    'restarting job')
        return True


@registry.JOBS_RECOVERY_STRATEGY_REGISTRY.type_register(name='FAILOVER',
                                                        default=False)
class FailoverStrategyExecutor(StrategyExecutor):
    """Failover strategy: wait in same region and failover after timeout."""

    _MAX_RETRY_CNT = 240  # Retry for 4 hours.

    def __init__(
        self,
        cluster_name: Optional[str],
        backend: 'backends.Backend',
        task: 'task_lib.Task',
        max_restarts_on_errors: int,
        job_id: int,
        task_id: int,
        pool: Optional[str],
        starting: Set[int],
        starting_lock: asyncio.Lock,
        starting_signal: asyncio.Condition,
        recover_on_exit_codes: Optional[List[int]] = None,
        file_mounts_blob_id: Optional[str] = None,
    ) -> None:
        super().__init__(cluster_name, backend, task, max_restarts_on_errors,
                         job_id, task_id, pool, starting, starting_lock,
                         starting_signal, recover_on_exit_codes,
                         file_mounts_blob_id)
        # Note down the cloud/region of the launched cluster, so that we can
        # first retry in the same cloud/region. (Inside recover() we may not
        # rely on cluster handle, as it can be None if the cluster is
        # preempted.)
        self._launched_resources: Optional['resources.Resources'] = None

    async def _launch(self,
                      max_retry: Optional[int] = 3,
                      raise_on_failure: bool = True,
                      recovery: bool = False) -> Optional[float]:
        job_submitted_at = await super()._launch(max_retry, raise_on_failure,
                                                 recovery)
        if job_submitted_at is not None and self.cluster_name is not None:
            # Only record the cloud/region if the launch is successful.
            handle = await asyncio.to_thread(
                global_user_state.get_handle_from_cluster_name,
                self.cluster_name)
            assert isinstance(handle, backends.CloudVmRayResourceHandle), (
                'Cluster should be launched.', handle)
            launched_resources = handle.launched_resources
            self._launched_resources = launched_resources

            # Persist infra info to database for sorting/filtering
            if launched_resources is not None:
                cloud = str(launched_resources.cloud
                           ) if launched_resources.cloud else None
                # Get current node names for lineage tracking
                current_names = None
                if handle.cached_cluster_info is not None:
                    current_names = (
                        handle.cached_cluster_info.get_node_names())
                await asyncio.to_thread(
                    state.set_job_infra,
                    self.job_id,
                    cloud=cloud,
                    region=launched_resources.region,
                    zone=launched_resources.zone,
                    current_node_names=current_names,
                )
        else:
            self._launched_resources = None
        return job_submitted_at

    async def recover(self) -> float:
        # 1. Cancel the jobs and launch the cluster with the STOPPED status,
        #    so that it will try on the current region first until timeout.
        # 2. Tear down the cluster, if the step 1 failed to launch the cluster.
        # 3. Launch the cluster with no cloud/region constraint or respect the
        #    original user specification.

        # Step 1
        await self._try_cancel_jobs()

        while True:
            # Add region constraint to the task, to retry on the same region
            # first (if valid).
            if self._launched_resources is not None:
                task = self.dag.tasks[0]
                original_resources = task.resources
                launched_cloud = self._launched_resources.cloud
                launched_region = self._launched_resources.region
                new_resources = self._launched_resources.copy(
                    cloud=launched_cloud, region=launched_region, zone=None)
                task.set_resources({new_resources})
                # Not using self.launch to avoid the retry until up logic.
                job_submitted_at = await self._launch(raise_on_failure=False,
                                                      recovery=True)
                # Restore the original dag, i.e. reset the region constraint.
                task.set_resources(original_resources)
                if job_submitted_at is not None:
                    return job_submitted_at

            # Step 2
            logger.debug('Terminating unhealthy cluster and reset cloud '
                         'region.')
            await asyncio.to_thread(self._cleanup_cluster)

            # Step 3
            logger.debug('Relaunch the cluster  without constraining to prior '
                         'cloud/region.')
            # Not using self.launch to avoid the retry until up logic.
            job_submitted_at = await self._launch(max_retry=self._MAX_RETRY_CNT,
                                                  raise_on_failure=False,
                                                  recovery=True)
            if job_submitted_at is None:
                # Failed to launch the cluster.
                gap_seconds = self.RETRY_INIT_GAP_SECONDS
                logger.info('Retrying to recover the cluster in '
                            f'{gap_seconds:.1f} seconds.')
                await asyncio.sleep(gap_seconds)
                continue

            return job_submitted_at


@registry.JOBS_RECOVERY_STRATEGY_REGISTRY.type_register(
    name='EAGER_NEXT_REGION', default=True)
class EagerFailoverStrategyExecutor(FailoverStrategyExecutor):
    """Eager failover strategy.

    This strategy is an extension of the FAILOVER strategy. Instead of waiting
    in the same region when the preemption happens, it immediately terminates
    the cluster and relaunches it in a different region. This is based on the
    observation that the preemption is likely to happen again shortly in the
    same region, so trying other regions first is more likely to get a longer
    running cluster.

    Example: Assume the user has access to 3 regions, R1, R2, R3, in that price
    order. Then the following are some possible event sequences:

        R1Z1 (preempted) -> R2 (success)

        R1Z1 (preempted) -> R2 (failed to launch) -> R3 (success)

        R1Z1 (preempted) -> R2 (failed to launch) -> R3 (failed to launch)
                                                  -> R1Z2 (success)

        R1Z1 (preempted) -> R2 (failed to launch) -> R3 (failed to launch)
                                                  -> R1Z1 (success)
    """

    async def recover(self) -> float:
        # 1. Terminate the current cluster
        # 2. Launch again by explicitly blocking the previously launched region
        # (this will failover through the entire search space except the
        # previously launched region)
        # 3. (If step 2 failed) Retry forever: Launch again with no blocked
        # locations (this will failover through the entire search space)
        #
        # The entire search space is defined by the original task request,
        # task.resources.

        # Step 1
        logger.debug('Terminating unhealthy cluster and reset cloud region.')
        await asyncio.to_thread(self._cleanup_cluster)

        # Step 2
        logger.debug('Relaunch the cluster skipping the previously launched '
                     'cloud/region.')
        if self._launched_resources is not None:
            task = self.dag.tasks[0]
            requested_resources = self._launched_resources
            if (requested_resources.region is None and
                    requested_resources.zone is None):
                # Optimization: We only block the previously launched region,
                # if the requested resources does not specify a region or zone,
                # because, otherwise, we will spend unnecessary time for
                # skipping the only specified region/zone.
                launched_cloud = self._launched_resources.cloud
                launched_region = self._launched_resources.region
                task.blocked_resources = {
                    requested_resources.copy(cloud=launched_cloud,
                                             region=launched_region)
                }
                # Not using self.launch to avoid the retry until up logic.
                job_submitted_at = await self._launch(raise_on_failure=False,
                                                      recovery=True)
                task.blocked_resources = None
                if job_submitted_at is not None:
                    return job_submitted_at

        while True:
            # Step 3
            logger.debug('Relaunch the cluster without constraining to prior '
                         'cloud/region.')
            # Not using self.launch to avoid the retry until up logic.
            job_submitted_at = await self._launch(max_retry=self._MAX_RETRY_CNT,
                                                  raise_on_failure=False,
                                                  recovery=True)
            if job_submitted_at is None:
                # Failed to launch the cluster.
                gap_seconds = self.RETRY_INIT_GAP_SECONDS
                logger.info('Retrying to recover the cluster in '
                            f'{gap_seconds:.1f} seconds.')
                await asyncio.sleep(gap_seconds)
                continue

            return job_submitted_at


def _get_logger_file(file_logger: logging.Logger) -> Optional[str]:
    """Gets the file path that the logger writes to."""
    for handler in file_logger.handlers:
        if isinstance(handler, logging.FileHandler):
            return handler.baseFilename
    return None
