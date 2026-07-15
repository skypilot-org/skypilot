"""Executor for the requests.

We start limited number of workers for long-running requests, and
significantly more workers for short-running requests. This is to optimize the
resource usage and the latency of the requests.

* Long-running requests are those requests that can take a long time to finish
and more resources are needed, such as cluster launching, starting, job
submission, managed job submission, etc.

* Short-running requests are those requests that can be done quickly, and
require a quick response, such as status check, job status check, etc.

With more short-running workers, we can serve more short-running requests in
parallel, and reduce the latency.

The number of the workers is determined by the system resources.

See the [README.md](../README.md) for detailed architecture of the executor.
"""
import asyncio
import concurrent.futures
import contextlib
import multiprocessing
import os
import signal
import sys
import threading
import time
import typing
from typing import Any, Callable, Dict, Generator, List, Optional, TextIO, Tuple

import psutil
import setproctitle

from sky import exceptions
from sky import global_user_state
from sky import models
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import kubernetes as kubernetes_adaptor
from sky.metrics import utils as metrics_utils
from sky.server import clean_env as clean_env_module
from sky.server import common as server_common
from sky.server import config as server_config
from sky.server import constants as server_constants
from sky.server import daemons
from sky.server import metrics as metrics_lib
from sky.server import plugins
from sky.server import versions
from sky.server.requests import payloads
from sky.server.requests import preconditions
from sky.server.requests import process
from sky.server.requests import request_names
from sky.server.requests import requests as api_requests
from sky.server.requests import threads
from sky.server.requests.queues import base as queue_base
from sky.skylet import constants
from sky.utils import annotations
from sky.utils import common_utils
from sky.utils import context
from sky.utils import context_utils
from sky.utils import subprocess_utils
from sky.utils import tempstore
from sky.utils import timeline
from sky.utils import yaml_utils
from sky.utils.db import db_utils
from sky.workspaces import constants as workspace_constants
from sky.workspaces import core as workspaces_core

if typing.TYPE_CHECKING:
    import types

# pylint: disable=ungrouped-imports
if sys.version_info >= (3, 10):
    from typing import ParamSpec
else:
    from typing_extensions import ParamSpec

P = ParamSpec('P')
logger = sky_logging.init_logger(__name__)

# On macOS, the default start method for multiprocessing is 'fork', which
# can cause issues with certain types of resources, including those used in
# the QueueManager in mp_queue.py.
# The 'spawn' start method is generally more compatible across different
# platforms, including macOS.
multiprocessing.set_start_method('spawn', force=True)

# An upper limit of max threads for request execution per server process that
# unlikely to be reached to allow higher concurrency while still prevent the
# server process become overloaded.
_REQUEST_THREADS_LIMIT = 128

# Max length of the retry reason in a request's backoff status message; the
# reason comes from the exception message, so truncate to keep it readable.
_RETRY_STATUS_MSG_REASON_MAX_LEN = 200

_REQUEST_THREAD_EXECUTOR_LOCK = threading.Lock()
# A dedicated thread pool executor for synced requests execution in coroutine to
# avoid:
# 1. blocking the event loop;
# 2. exhausting the default thread pool executor of event loop;
_REQUEST_THREAD_EXECUTOR: Optional[threads.OnDemandThreadExecutor] = None


def get_request_thread_executor() -> threads.OnDemandThreadExecutor:
    """Lazy init and return the request thread executor for current process."""
    global _REQUEST_THREAD_EXECUTOR
    if _REQUEST_THREAD_EXECUTOR is not None:
        return _REQUEST_THREAD_EXECUTOR
    with _REQUEST_THREAD_EXECUTOR_LOCK:
        if _REQUEST_THREAD_EXECUTOR is None:
            _REQUEST_THREAD_EXECUTOR = threads.OnDemandThreadExecutor(
                name='request_thread_executor',
                max_workers=_REQUEST_THREADS_LIMIT)
        return _REQUEST_THREAD_EXECUTOR


class RequestQueue:
    """The queue for the requests.

    Wraps a QueueBackend instance. The elements in the queue are tuples of
    (request_id, ignore_return_value, retryable).
    """

    def __init__(self, queue_backend_impl: queue_base.QueueBackend) -> None:
        self._backend = queue_backend_impl

    def put(self, request: Tuple[str, bool, bool]) -> None:
        """Put a request to the queue.

        Args:
            request: A tuple of request_id, ignore_return_value, and retryable.
        """
        self._backend.put(request)

    async def put_async(self, request: Tuple[str, bool, bool]) -> None:
        """Put a request to the queue, async.

        Args:
            request: A tuple of request_id, ignore_return_value, and retryable.
        """
        await self._backend.put_async(request)

    def get(self) -> Optional[Tuple[str, bool, bool]]:
        """Get a request from the queue.

        It is non-blocking if the queue is empty, and returns None.

        Returns:
            A tuple of request_id, ignore_return_value, and retryable.
        """
        return self._backend.get()

    def __len__(self) -> int:
        """Get the length of the queue."""
        return self._backend.qsize()


# The active queue factory, set during start().
_queue_factory: Optional[queue_base.QueueBackendFactory] = None


def executor_initializer(proc_group: str,
                         clean_env: Optional[Dict[str, str]] = None):
    setproctitle.setproctitle(f'SkyPilot:executor:{proc_group}:'
                              f'{multiprocessing.current_process().pid}')
    # Load plugins for executor process.
    plugins.load_plugins(
        plugins.ExtensionContext(context=plugins.PluginContext.EXECUTOR))
    # Same rationale as in sky.server.uvicorn.Server.run: reap this
    # executor's prometheus multiproc files when it exits.
    metrics_lib.register_multiproc_cleanup_atexit()
    # The main API server process captures its env at startup and forwards
    # it via initargs (see RequestWorker.run). Adopt that snapshot directly
    # so the worker doesn't depend on its own spawn-time os.environ, which
    # for a lazy-spawned burst worker could reflect a coroutine-path
    # request mid-pollution in the main process.
    if clean_env is not None:
        clean_env_module.set_clean_server_env(clean_env)
    # Executor never stops, unless the whole process is killed.
    threading.Thread(target=metrics_lib.process_monitor,
                     args=(f'worker:{proc_group}', threading.Event()),
                     daemon=True).start()


def _request_is_gone_or_cancelled(request_id: str) -> bool:
    """Cancellation check passed to ``ContinueCondition.wait()``.

    A request cancelled (or gone) while paused must not be re-queued.
    """
    request = api_requests.get_request(request_id, fields=['status'])
    return (request is None or
            request.status == api_requests.RequestStatus.CANCELLED)


class RequestWorker:
    """A worker that polls requests from the queue and runs them.

    The worker can run at least `garanteed_parallelism` requests in parallel.
    If there are more resources available, it can spin up extra workers up to
    `garanteed_parallelism + burstable_parallelism`.
    """

    # The type of queue this worker works on.
    schedule_type: api_requests.ScheduleType
    # The least number of requests that this worker can run in parallel.
    garanteed_parallelism: int
    # The extra number of requests that this worker can run in parallel
    # if there are available CPU/memory resources.
    burstable_parallelism: int = 0

    def __init__(self, schedule_type: api_requests.ScheduleType,
                 config: server_config.WorkerConfig) -> None:
        self.schedule_type = schedule_type
        self.garanteed_parallelism = config.garanteed_parallelism
        self.burstable_parallelism = config.burstable_parallelism
        self.num_db_connections_per_worker = (
            config.num_db_connections_per_worker)
        self._thread: Optional[threading.Thread] = None
        self._cancel_event = threading.Event()

    def __str__(self) -> str:
        return f'Worker(schedule_type={self.schedule_type.value})'

    def run_in_background(self) -> None:
        # Thread dispatcher is sufficient for current scale, refer to
        # tests/load_tests/test_queue_dispatcher.py for more details.
        # Use daemon thread for automatic cleanup.
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        self._thread = thread

    def cancel(self) -> None:
        if self._thread is not None:
            self._cancel_event.set()
            self._thread.join()

    def process_request(self, executor: process.BurstableExecutor,
                        queue: RequestQueue) -> None:
        try:
            request_element = queue.get()
            if request_element is None:
                time.sleep(0.1)
                return
            request_id, ignore_return_value, _ = request_element
            request = api_requests.get_request(request_id, fields=['status'])
            assert request is not None, f'Request with ID {request_id} is None'
            if request.status == api_requests.RequestStatus.CANCELLED:
                return
            del request
            logger.info(f'[{self}] Submitting request: {request_id}')
            # Start additional process to run the request, so that it can be
            # cancelled when requested by a user.
            # TODO(zhwu): since the executor is reusing the request process,
            # multiple requests can share the same process pid, which may cause
            # issues with SkyPilot core functions if they rely on the exit of
            # the process, such as subprocess_daemon.py.
            fut = executor.submit_until_success(
                _request_execution_wrapper, request_id, ignore_return_value,
                self.num_db_connections_per_worker)
            # Decrement the free executor count when a request starts
            if metrics_utils.METRICS_ENABLED:
                if self.schedule_type == api_requests.ScheduleType.LONG:
                    metrics_utils.SKY_APISERVER_LONG_EXECUTORS.dec()
                elif self.schedule_type == api_requests.ScheduleType.SHORT:
                    metrics_utils.SKY_APISERVER_SHORT_EXECUTORS.dec()
            # Monitor the result of the request execution.
            threading.Thread(target=self.handle_task_result,
                             args=(fut, request_element),
                             daemon=True).start()

            logger.info(f'[{self}] Submitted request: {request_id}')
        except (Exception, SystemExit) as e:  # pylint: disable=broad-except
            # Catch any other exceptions to avoid crashing the worker process.
            logger.error(
                f'[{self}] Error processing request: '
                f'{request_id if "request_id" in locals() else ""} '
                f'{common_utils.format_exception(e, use_bracket=True)}')

    def _mark_executor_free(self) -> None:
        """Increment the free-executor gauge for this worker's schedule type.

        Called the instant the worker process is released (i.e. the future
        completes), so the gauge stays accurate even while a retry/pause wait
        is still running in this monitor thread.
        """
        if not metrics_utils.METRICS_ENABLED:
            return
        if self.schedule_type == api_requests.ScheduleType.LONG:
            metrics_utils.SKY_APISERVER_LONG_EXECUTORS.inc()
        elif self.schedule_type == api_requests.ScheduleType.SHORT:
            metrics_utils.SKY_APISERVER_SHORT_EXECUTORS.inc()

    def handle_task_result(self, fut: concurrent.futures.Future,
                           request_element: Tuple[str, bool, bool]) -> None:
        try:
            try:
                fut.result()
            finally:
                # The worker process is released the instant the future
                # completes, before any retry/pause wait below. Account for it
                # here so the free-executor gauge reflects the idle process
                # during the wait, instead of staying decremented until the
                # request finishes or reschedules.
                self._mark_executor_free()
        except concurrent.futures.process.BrokenProcessPool as e:
            # Happens when the worker process dies unexpectedly, e.g. OOM
            # killed.
            request_id, _, retryable = request_element
            # Ensure the request status.
            api_requests.set_request_failed(request_id, e)
            logger.error(
                f'Request {request_id} failed to get processed '
                f'{common_utils.format_exception(e, use_bracket=True)}')
            if retryable:
                # If the request is retryable and disrupted by broken
                # process pool, reschedule it immediately to get it
                # retried in the new process pool.
                queue = _get_queue(self.schedule_type)
                queue.put(request_element)
        except exceptions.ExecutionRetryableError as e:
            request_id, _, _ = request_element
            # Clamp to avoid ValueError from time.sleep() on a negative wait.
            retry_wait_seconds = max(0, e.retry_wait_seconds)
            # A pause (ExecutionPausedError) may carry a continue condition that
            # owns how to wait for the resume signal; without one, fall back to
            # a fixed backoff. Either way the wait runs in this monitor thread,
            # not an executor worker.
            condition = getattr(e, 'continue_condition', None)
            # Surface why we are retrying, not just the wait time. status_msg
            # is a single-line field, so strip color and collapse whitespace.
            reason = ' '.join(common_utils.remove_color(str(e)).split())
            if len(reason) > _RETRY_STATUS_MSG_REASON_MAX_LEN:
                reason = reason[:_RETRY_STATUS_MSG_REASON_MAX_LEN].rstrip(
                ) + '...'
            retry_suffix = ('waiting to resume' if condition is not None else
                            f'retrying in {retry_wait_seconds}s')
            status_msg = (f'{reason} ({retry_suffix})'
                          if reason else retry_suffix.capitalize())
            # Set request to WAITING status for visibility
            with api_requests.update_request(request_id) as request_task:
                assert request_task is not None, request_id
                request_task.status = api_requests.RequestStatus.WAITING
                request_task.status_msg = status_msg
            try:
                if condition is not None:
                    should_reschedule = condition.wait(
                        is_cancelled=lambda: _request_is_gone_or_cancelled(
                            request_id),
                        fallback_wait_seconds=retry_wait_seconds)
                else:
                    time.sleep(retry_wait_seconds)
                    should_reschedule = True
            except Exception as wait_err:  # pylint: disable=broad-except
                logger.error(
                    f'Continue-condition wait failed for {request_id}: '
                    f'{common_utils.format_exception(wait_err)}')
                time.sleep(retry_wait_seconds)
                should_reschedule = True
            if should_reschedule:
                # Reschedule the request.
                queue = _get_queue(self.schedule_type)
                queue.put(request_element)
                logger.info(f'Rescheduled request {request_id} for retry')

    def run(self) -> None:
        # Handle the SIGTERM signal to abort the executor process gracefully.
        proc_group = f'{self.schedule_type.value}'
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, _sigterm_handler)
            setproctitle.setproctitle(f'SkyPilot:worker:{proc_group}')
        queue = _get_queue(self.schedule_type)

        # Use concurrent.futures.ProcessPoolExecutor instead of
        # multiprocessing.Pool because the former is more efficient with the
        # support of lazy creation of worker processes.
        # We use executor instead of individual multiprocessing.Process to avoid
        # the overhead of forking a new process for each request, which can be
        # about 1s delay.
        try:
            # Pass the main process's clean env snapshot so workers (incl.
            # lazy-spawned burst workers) record the same pre-pollution env
            # regardless of when they spawn.
            executor = process.BurstableExecutor(
                garanteed_workers=self.garanteed_parallelism,
                burst_workers=self.burstable_parallelism,
                initializer=executor_initializer,
                initargs=(proc_group, clean_env_module.get_clean_server_env()))
            # Initialize the appropriate gauge for the number of free executors
            total_executors = (self.garanteed_parallelism +
                               self.burstable_parallelism)
            if metrics_utils.METRICS_ENABLED:
                if self.schedule_type == api_requests.ScheduleType.LONG:
                    metrics_utils.SKY_APISERVER_LONG_EXECUTORS.set(
                        total_executors)
                elif self.schedule_type == api_requests.ScheduleType.SHORT:
                    metrics_utils.SKY_APISERVER_SHORT_EXECUTORS.set(
                        total_executors)
            while not self._cancel_event.is_set():
                self.process_request(executor, queue)
        # TODO(aylei): better to distinct between KeyboardInterrupt and SIGTERM.
        except KeyboardInterrupt:
            pass
        finally:
            # In most cases, here we receive either ctrl-c in foreground
            # execution or SIGTERM on server exiting. Gracefully exit the
            # worker process and the executor.
            # TODO(aylei): worker may also be killed by system daemons like
            # OOM killer, crash the API server or recreate the worker process
            # to avoid broken state in such cases.
            logger.info(f'[{self}] Worker process interrupted')
            executor.shutdown()


@annotations.lru_cache(scope='global', maxsize=None)
def _get_queue(schedule_type: api_requests.ScheduleType) -> RequestQueue:
    factory = _queue_factory
    if factory is None:
        factory = queue_base.get_queue_backend_factory()
    assert factory is not None
    return RequestQueue(factory.create_queue(schedule_type.value))


# Request names where a non-explicit workspace pick is worth surfacing
# at INFO level (i.e. visible in the streamed CLI output, not just debug
# logs). Resource-creating commands record the resolved workspace into
# durable state (cluster.workspace / job_info.workspace) — users care
# which workspace that ended up being. Read-only commands resolve the
# same way under the hood but the log line would just be noise.
#
# To extend coverage to other resource-creating verbs (e.g. SERVE_UP),
# add the request_name here.
_RESOURCE_CREATING_REQUEST_NAMES_FOR_RESOLUTION_LOG = {
    server_constants.REQUEST_NAME_PREFIX +
    request_names.RequestName.CLUSTER_LAUNCH.value,
    server_constants.REQUEST_NAME_PREFIX +
    request_names.RequestName.JOBS_LAUNCH.value,
}

# Sources we DON'T announce, even on a resource-creating request:
#   EXPLICIT          — the user already named the workspace; repeating
#                       it in the log is noise.
#   DEFAULT_FALLBACK  — landing on 'default' is the pre-existing implicit
#                       behavior; surfacing it on every launch for every
#                       single-default user would clutter output for the
#                       common case while telling them nothing new.
# PREFERRED / SINGLE_MEMBERSHIP are the cases worth surfacing — the user
# may not realize where the resource landed.
_SILENT_WORKSPACE_RESOLUTION_SOURCES = {
    workspace_constants.WORKSPACE_SOURCE_EXPLICIT,
    workspace_constants.WORKSPACE_SOURCE_DEFAULT_FALLBACK,
}


def _should_apply_workspace_resolver(is_daemon: bool,
                                     client_api_version: Optional[int]) -> bool:
    """Returns True iff the per-user workspace resolver should run for
    this request. Three gates, in order:

      (a) skip daemons / system-user requests — the system user is admin
          and would land on 'default' via the default-fallback step
          anyway; the resolver would add a DB read + permission check per
          daemon tick (thousands per hour) for zero behavioral change.
      (b) skip when the client API version is below the version that
          added /users/me/workspace + WorkspaceAmbiguousError handling —
          old clients wouldn't know how to interpret the new error
          format, so preserve the legacy permission-denied path that
          they already handle. The version travels on the RequestBody
          itself (`client_api_version` field) so it is available in the
          worker process; `versions.get_remote_api_version()` returns
          None in workers because the underlying ContextVar set by
          APIVersionMiddleware does not propagate across process
          boundaries.
      (c) skip when active_workspace was explicitly set on the wire
          (anywhere in the merged config) — respect explicit user intent;
          preferred MUST be ignored when the user names a workspace.
    """
    if is_daemon:
        return False
    if (client_api_version is None or client_api_version <
            server_constants.MIN_PREFERRED_WORKSPACE_API_VERSION):
        return False
    return not skypilot_config.is_active_workspace_set()


@contextlib.contextmanager
def override_request_env_and_config(
        request_body: payloads.RequestBody, request_id: str,
        request_name: str) -> Generator[None, None, None]:
    """Override the environment and SkyPilot config for a request."""
    # Daemons run AS the server, not as any client. Their persisted
    # request_body.env_vars came from whichever pod first scheduled them,
    # which may be a previous deployment generation with stale downward-API
    # values (e.g. SKYPILOT_POD_MEMORY_BYTES_LIMIT, SKYPILOT_APISERVER_UUID).
    # Overlaying those would clobber the current pod's actual values. So
    # for daemons, skip the env overlay and use the current process's
    # os.environ.
    is_daemon = daemons.is_daemon_request_id(request_id)
    original_env = os.environ.copy()
    try:
        if is_daemon:
            # The SkyPilot system user is already upserted at scheduling
            # time by prepare_request_async when is_skypilot_system=True,
            # so no add_or_update_user round-trip is needed per tick.
            user = models.User(id=constants.SKYPILOT_SYSTEM_USER_ID,
                               name=constants.SKYPILOT_SYSTEM_USER_ID,
                               user_type=models.UserType.SYSTEM.value)
            # Daemons always run in-process on the server, regardless of
            # what the persisted body recorded.
            using_remote_api_server = False
        else:
            # Unset SKYPILOT_DEBUG by default, to avoid the value set on the
            # API server affecting client requests. If set on the client
            # side, it will be overridden by the request body.
            os.environ.pop('SKYPILOT_DEBUG', None)
            # Remove the db connection uri from client supplied env vars, as
            # the client should not set the db string on server side.
            request_body.env_vars.pop(constants.ENV_VAR_DB_CONNECTION_URI, None)
            # Remove the in-cluster context name from client supplied env
            # vars. When a client runs inside a Kubernetes pod (e.g., a
            # managed job with api_server_access), its env has
            # SKYPILOT_IN_CLUSTER_CONTEXT_NAME set pod template. If this
            # leaks into the server's os.environ, it causes the server to
            # attempt in-cluster auth (load_incluster_config) instead of
            # using its own kubeconfig, which fails when the server is not
            # running in a Kubernetes pod.
            request_body.env_vars.pop(
                kubernetes_adaptor.IN_CLUSTER_CONTEXT_NAME_ENV_VAR, None)
            os.environ.update(request_body.env_vars)
            # Note: may be overridden by AuthProxyMiddleware.
            # TODO(zhwu): we need to make the entire request a context
            # available to the entire request execution, so that we can
            # access info like user through the execution.
            user = models.User(
                id=request_body.env_vars[constants.USER_ID_ENV_VAR],
                name=request_body.env_vars[constants.USER_ENV_VAR])
            _, user = global_user_state.add_or_update_user(user,
                                                           return_user=True)
            using_remote_api_server = request_body.using_remote_api_server

        # Force color to be enabled.
        os.environ['CLICOLOR_FORCE'] = '1'
        server_common.reload_for_new_request(
            client_entrypoint=request_body.entrypoint,
            client_command=request_body.entrypoint_command,
            using_remote_api_server=using_remote_api_server,
            user=user,
            request_id=request_id)
        logger.debug(
            f'override path: {request_body.override_skypilot_config_path}')
        with skypilot_config.override_skypilot_config(
                request_body.override_skypilot_config,
                request_body.override_skypilot_config_path):
            # Skip permission check for sky.workspaces.get request
            # as it is used to determine which workspaces the user
            # has access to.
            if request_name == 'sky.workspaces.get':
                logger.debug(f'{request_id} skipping workspace check for '
                             f'{request_name}')
                yield
            else:
                # If the client did not explicitly set active_workspace,
                # resolve it from the user's memberships (preferred ->
                # default if accessible -> single-membership) instead of
                # always landing on the bare 'default' literal. Explicit
                # intent (any value, including 'default') is passed through
                # unchanged. See _should_apply_workspace_resolver for the
                # exact gate conditions (daemon skip, client API version,
                # explicit-intent respect).
                workspace_ctx: contextlib.AbstractContextManager = (
                    contextlib.nullcontext())
                # Read the client's API version from the request body, not
                # from versions.get_remote_api_version() — the ContextVar
                # the latter reads is set by APIVersionMiddleware in the
                # FastAPI async context but does not propagate into worker
                # processes (BurstableExecutor = ProcessPoolExecutor).
                client_api_version = getattr(request_body, 'client_api_version',
                                             None)
                if _should_apply_workspace_resolver(is_daemon,
                                                    client_api_version):
                    resolution = workspaces_core.resolve_workspace_for_user(
                        user)
                    workspace_ctx = (skypilot_config.local_active_workspace_ctx(
                        resolution.workspace))
                    logger.debug(f'{request_id} resolved workspace '
                                 f'{resolution.workspace!r} from '
                                 f'{resolution.source} for user {user.name}')
                    # For resource-creating commands, surface the
                    # resolver's pick at INFO level so the user sees
                    # which workspace their cluster / job actually
                    # landed in. Two filters compose:
                    #   - request_name whitelist (resource-creating verbs)
                    #   - source NOT in the silent set (EXPLICIT /
                    #     DEFAULT_FALLBACK) — EXPLICIT repeats what the
                    #     user just said; DEFAULT_FALLBACK is the silent
                    #     pre-existing behavior. Only PREFERRED /
                    #     SINGLE_MEMBERSHIP are worth surfacing.
                    if (request_name in
                            _RESOURCE_CREATING_REQUEST_NAMES_FOR_RESOLUTION_LOG
                            and resolution.source
                            not in _SILENT_WORKSPACE_RESOLUTION_SOURCES):
                        logger.info(f'Using workspace {resolution.workspace!r} '
                                    f'(source: {resolution.source}).')
                with workspace_ctx:
                    try:
                        # Reject requests that the user does not have
                        # permission to access.
                        workspaces_core.reject_request_for_unauthorized_workspace(  # pylint: disable=line-too-long
                            user)
                    except exceptions.PermissionDeniedError as e:
                        logger.debug(
                            f'{request_id} permission denied to workspace: '
                            f'{skypilot_config.get_active_workspace()}: {e}')
                        raise e
                    logger.debug(f'{request_id} permission granted to '
                                 f'{request_name} request')
                    yield
    finally:
        # We need to call the save_timeline() since atexit will not be
        # triggered as multiple requests can be sharing the same process.
        timeline.save_timeline()
        # Restore the original environment variables, so that a new request
        # won't be affected by the previous request, e.g. SKYPILOT_DEBUG
        # setting, etc. This is necessary as our executor is reusing the
        # same process for multiple requests. The daemon path also relies
        # on this: daemons mutate os.environ from inside the with block
        # (e.g. setting SKYPILOT_DISABLE_LOGGING in
        # InternalRequestDaemon.run_event), and that mutation must not
        # leak to whichever request the worker handles next.
        os.environ.clear()
        os.environ.update(original_env)


def _sigterm_handler(signum: int, frame: Optional['types.FrameType']) -> None:
    raise KeyboardInterrupt


# Set by _request_execution_wrapper; read by _gated_sigterm_handler.
_in_request_execution: bool = False


def _gated_sigterm_handler(signum: int,
                           frame: Optional['types.FrameType']) -> None:
    """Raise KeyboardInterrupt only while actively executing a request.

    SIGTERM landing on an idle worker (blocked in
    concurrent.futures._process_worker's call_queue.get) would escape
    _process_worker unhandled and break the entire pool. Swallow it; the
    cancellation path already targets the worker by pid, so a stray SIGTERM
    on an idle worker just means we lost the race with the request finishing.
    """
    del signum, frame
    if _in_request_execution:
        raise KeyboardInterrupt
    # logger isn't async-signal-safe (re-entrant lock); use os.write.
    try:
        os.write(2, b'SIGTERM received while worker idle; ignored.\n')
    except Exception:  # pylint: disable=broad-except
        pass


def _request_execution_wrapper(request_id: str,
                               ignore_return_value: bool,
                               num_db_connections_per_worker: int = 0) -> None:
    """Wrapper for a request execution.

    It wraps the execution of a request to:
    1. Deserialize the request from the request database and serialize the
       return value/exception in the request database;
    2. Update the request status based on the execution result;
    3. Redirect the stdout and stderr of the execution to log file;
    4. Handle the SIGTERM signal to abort the request gracefully.
    5. Maintain the lifecycle of the temp dir used by the request.
    """
    pid = multiprocessing.current_process().pid
    proc = psutil.Process(pid)
    rss_begin = proc.memory_info().rss
    db_utils.set_max_connections(num_db_connections_per_worker)
    # Handle the SIGTERM signal to abort the request processing gracefully.
    # Only set up signal handlers in the main thread, as signal.signal() raises
    # ValueError if called from a non-main thread (e.g., in tests).
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _gated_sigterm_handler)

    logger.info(f'Running request {request_id} with pid {pid}')

    original_stdout = original_stderr = None

    def _save_current_output() -> None:
        """Save the current stdout and stderr file descriptors."""
        nonlocal original_stdout, original_stderr
        original_stdout = os.dup(sys.stdout.fileno())
        original_stderr = os.dup(sys.stderr.fileno())

    def _redirect_output(file: TextIO) -> None:
        """Redirect stdout and stderr to the log file."""
        # Get the file descriptor from the file object
        fd = file.fileno()
        # Copy this fd to stdout and stderr
        os.dup2(fd, sys.stdout.fileno())
        os.dup2(fd, sys.stderr.fileno())

    def _restore_output() -> None:
        """Restore stdout and stderr to their original file descriptors."""
        nonlocal original_stdout, original_stderr
        if original_stdout is not None:
            os.dup2(original_stdout, sys.stdout.fileno())
            os.close(original_stdout)
            original_stdout = None

        if original_stderr is not None:
            os.dup2(original_stderr, sys.stderr.fileno())
            os.close(original_stderr)
            original_stderr = None

    request_name = None
    # Set _in_request_execution inside the try so `finally` always clears it,
    # even if a SIGTERM lands before any wrapper code runs.
    global _in_request_execution  # pylint: disable=global-statement
    try:
        _in_request_execution = True
        # As soon as the request is updated with the executor PID, we can
        # receive SIGTERM from cancellation. So, we update the request inside
        # the try block to ensure we have the KeyboardInterrupt handling.
        with api_requests.update_request(request_id) as request_task:
            assert request_task is not None, request_id
            if (request_task.status
                    not in api_requests.RequestStatus.executable_statuses()):
                logger.warning(
                    f'Request is already {request_task.status.value}, '
                    f'skipping execution')
                return
            log_path = request_task.log_path
            request_task.pid = pid
            request_task.status = api_requests.RequestStatus.RUNNING
            # Clear any leftover retry-backoff message now that we are running.
            request_task.status_msg = None
            func = request_task.entrypoint
            request_body = request_task.request_body
            request_name = request_task.name

        # Store copies of the original stdout and stderr file descriptors
        # We do this in two steps because we should make sure to restore the
        # original values even if we are cancelled or fail during the redirect.
        _save_current_output()

        # Append to the log file instead of overwriting it since there might be
        # logs from previous retries.
        with log_path.open('a', encoding='utf-8') as f:
            # Redirect the stdout/stderr before overriding the environment and
            # config, as there can be some logs during override that needs to be
            # captured in the log file.
            _redirect_output(f)

            # Skip debug logging for daemon requests since the daemon
            # requests has its own log level config and we don't want to
            # duplicate the daemon logs.
            debug_log_ctx = (contextlib.nullcontext()
                             if daemons.is_daemon_request_id(request_id) else
                             sky_logging.add_debug_log_handler(request_id))
            with debug_log_ctx, \
                override_request_env_and_config(
                    request_body, request_id, request_name), \
                tempstore.tempdir():
                if sky_logging.logging_enabled(logger, sky_logging.DEBUG):
                    config = skypilot_config.to_dict()
                    logger.debug(f'request config: \n'
                                 f'{yaml_utils.dump_yaml_str(dict(config))}')
                (metrics_utils.SKY_APISERVER_PROCESS_EXECUTION_START_TOTAL.
                 labels(request=request_name, pid=pid).inc())
                with metrics_utils.time_it(name=request_name,
                                           group='request_execution'):
                    return_value = func(**request_body.to_kwargs())
                f.flush()
    except KeyboardInterrupt:
        logger.info(f'Request {request_id} cancelled by user')
        # Kill all children processes related to this request.
        # Each executor handles a single request, so we can safely kill all
        # children processes related to this request.
        # This is required as python does not pass the KeyboardInterrupt to the
        # threads that are not main thread.
        subprocess_utils.kill_children_processes()
        return
    except exceptions.ExecutionRetryableError as e:
        logger.error(e)
        logger.info(e.hint)
        with api_requests.update_request(request_id) as request_task:
            assert request_task is not None, request_id
            # Retried request will undergo rescheduling and a new execution,
            # clear the pid of the request.
            request_task.pid = None
        # Yield control to the scheduler for uniform handling of retries.
        _restore_output()
        raise
    except (Exception, SystemExit) as e:  # pylint: disable=broad-except
        api_requests.set_request_failed(request_id, e)
        # Manually reset the original stdout and stderr file descriptors early
        # so that the "Request xxxx failed due to ..." log message will be
        # written to the original stdout and stderr file descriptors.
        _restore_output()
        logger.error(f'Request {request_id} failed due to '
                     f'{common_utils.format_exception(e)}')
        return
    else:
        api_requests.set_request_succeeded(
            request_id, return_value if not ignore_return_value else None)
        # Manually reset the original stdout and stderr file descriptors early
        # so that the "Request xxxx failed due to ..." log message will be
        # written to the original stdout and stderr file descriptors.
        _restore_output()
        logger.info(f'Request {request_id} finished')
    finally:
        _in_request_execution = False
        _restore_output()
        try:
            # Capture the peak RSS before GC.
            peak_rss = max(proc.memory_info().rss, metrics_lib.peak_rss_bytes)
            # Clear request level cache to release all memory used by the
            # request.
            annotations.clear_request_level_cache()
            with metrics_utils.time_it(name='release_memory', group='internal'):
                common_utils.release_memory()
            if request_name is not None:
                _record_memory_metrics(request_name, proc, rss_begin, peak_rss)
        except Exception as e:  # pylint: disable=broad-except
            logger.error(f'Failed to record memory metrics: '
                         f'{common_utils.format_exception(e)}')


_first_request = True


def _record_memory_metrics(request_name: str, proc: psutil.Process,
                           rss_begin: int, peak_rss: int) -> None:
    """Record the memory metrics for a request."""
    # Do not record full memory delta for the first request as it
    # will loads the sky core modules and make the memory usage
    # estimation inaccurate.
    global _first_request
    if _first_request:
        _first_request = False
        return
    rss_end = proc.memory_info().rss

    # Answer "how much RSS this request contributed?"
    metrics_utils.SKY_APISERVER_REQUEST_RSS_INCR_BYTES.labels(
        name=request_name).observe(max(rss_end - rss_begin, 0))
    # Estimate the memory usage by the request by capturing the
    # peak memory delta during the request execution.
    metrics_utils.SKY_APISERVER_REQUEST_MEMORY_USAGE_BYTES.labels(
        name=request_name).observe(max(peak_rss - rss_begin, 0))


class CoroutineTask:
    """Wrapper of a background task runs in coroutine"""

    def __init__(self, task: asyncio.Task):
        self.task = task

    async def cancel(self):
        try:
            self.task.cancel()
            await self.task
        except asyncio.CancelledError:
            pass


def check_request_thread_executor_available() -> None:
    """Check if the request thread executor is available.

    This is a best effort check to hint the client to retry other server
    processes when there is no avaiable thread worker in current one. But
    a request may pass this check and still cannot get worker on execution
    time due to race condition. In this case, the client will see a failed
    request instead of retry.

    TODO(aylei): this can be refined with a refactor of our coroutine
    execution flow.
    """
    get_request_thread_executor().check_available()


def execute_request_in_coroutine(
        request: api_requests.Request) -> CoroutineTask:
    """Execute a request in current event loop.

    Args:
        request: The request to execute.

    Returns:
        A CoroutineTask handle to operate the background task.
    """
    task = asyncio.create_task(_execute_request_coroutine(request))
    return CoroutineTask(task)


def _execute_with_config_override(func: Callable,
                                  request_body: payloads.RequestBody,
                                  request_id: str, request_name: str,
                                  **kwargs) -> Any:
    """Execute a function with env and config override inside a thread."""
    # Override the environment and config within this thread's context,
    # which gets copied when we call to_thread.
    with override_request_env_and_config(request_body, request_id,
                                         request_name):
        return func(**kwargs)


async def _execute_request_coroutine(request: api_requests.Request):
    """Execute a request in current event loop.

    Similar to _request_execution_wrapper, but executed as coroutine in current
    event loop. This is designed for executing tasks that are not CPU
    intensive, e.g. sky logs.
    """
    context.initialize()
    ctx = context.get()
    assert ctx is not None, 'Context is not initialized'
    logger.info(f'Executing request {request.request_id} in coroutine')
    func = request.entrypoint
    request_body = request.request_body
    await api_requests.update_status_async(request.request_id,
                                           api_requests.RequestStatus.RUNNING)
    # Redirect stdout and stderr to the request log path.
    original_output = ctx.redirect_log(request.log_path)
    try:
        fut: asyncio.Future = context_utils.to_thread_with_executor(
            get_request_thread_executor(), _execute_with_config_override, func,
            request_body, request.request_id, request.name,
            **request_body.to_kwargs())
    except Exception as e:  # pylint: disable=broad-except
        ctx.redirect_log(original_output)
        await api_requests.set_request_failed_async(request.request_id, e)
        logger.error(f'Failed to run request {request.request_id} due to '
                     f'{common_utils.format_exception(e)}')
        return

    async def poll_task(request_id: str) -> bool:
        req_status = await api_requests.get_request_status_async(request_id)
        if req_status is None:
            raise RuntimeError('Request not found')

        if req_status.status == api_requests.RequestStatus.CANCELLED:
            ctx.cancel()
            return True

        if fut.done():
            try:
                result = await fut
                await api_requests.set_request_succeeded_async(
                    request_id, result)
            except asyncio.CancelledError:
                # The task is cancelled by ctx.cancel(), where the status
                # should already be set to CANCELLED.
                pass
            except Exception as e:  # pylint: disable=broad-except
                ctx.redirect_log(original_output)
                await api_requests.set_request_failed_async(request_id, e)
                logger.error(f'Request {request_id} failed due to '
                             f'{common_utils.format_exception(e)}')
            return True
        return False

    try:
        while True:
            res = await poll_task(request.request_id)
            if res:
                break
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        # Current coroutine is cancelled due to client disconnect, set the
        # request status for consistency.
        await api_requests.set_request_cancelled_async(request.request_id)
        pass
    # pylint: disable=broad-except
    except (Exception, KeyboardInterrupt, SystemExit) as e:
        # Handle any other error
        ctx.redirect_log(original_output)
        await api_requests.set_request_failed_async(request.request_id, e)
        logger.error(f'Request {request.request_id} interrupted due to '
                     f'unhandled exception: {common_utils.format_exception(e)}')
        raise
    finally:
        # Always cancel the context to kill potentially running background
        # routine.
        ctx.cancel()


async def prepare_request_async(
    request_id: str,
    request_name: request_names.RequestName,
    request_body: payloads.RequestBody,
    func: Callable[P, Any],
    request_cluster_name: Optional[str] = None,
    schedule_type: api_requests.ScheduleType = (api_requests.ScheduleType.LONG),
    is_skypilot_system: bool = False,
    auth_user: Optional[models.User] = None,
) -> api_requests.Request:
    """Prepare a request for execution."""
    if auth_user is not None:
        assert auth_user.name is not None
        # Use the authenticated user identity as the single source of truth
        # if present.
        user_id = auth_user.id
        # Set user identity for executors.
        request_body.env_vars[constants.USER_ID_ENV_VAR] = user_id
        request_body.env_vars[constants.USER_ENV_VAR] = auth_user.name
    else:
        # Fallback to legacy environment variable based identity if no
        # authentication is set.
        user_id = request_body.env_vars[constants.USER_ID_ENV_VAR]
    if is_skypilot_system:
        user_id = constants.SKYPILOT_SYSTEM_USER_ID
        global_user_state.add_or_update_user(
            models.User(id=user_id,
                        name=user_id,
                        user_type=models.UserType.SYSTEM.value))
    # Capture the client's API version from the FastAPI dispatch context
    # into the request body so it survives the process boundary into the
    # worker that runs the request. APIVersionMiddleware set the
    # ContextVar from the X-SkyPilot-API-Version header; reading it here
    # (still in the async dispatch process) and stamping the body is the
    # one place where header -> body translation happens, so neither the
    # Python SDK nor the dashboard need their own stamping logic. Old
    # clients (no header) yield None, which the worker-side gate treats
    # as "skip the workspace resolver".
    request_body.client_api_version = versions.get_remote_api_version()
    request = api_requests.Request(
        request_id=request_id,
        name=server_constants.REQUEST_NAME_PREFIX + request_name,
        entrypoint=func,
        request_body=request_body,
        status=api_requests.RequestStatus.PENDING,
        created_at=time.time(),
        schedule_type=schedule_type,
        user_id=user_id,
        cluster_name=request_cluster_name,
        file_mounts_blob_id=getattr(request_body, 'file_mounts_blob_id', None),
    )

    if not await api_requests.create_if_not_exists_async(request):
        raise exceptions.RequestAlreadyExistsError(
            f'Request {request_id} already exists.')

    request.log_path.touch()
    return request


async def schedule_request_async(
        request_id: str,
        request_name: request_names.RequestName,
        request_body: payloads.RequestBody,
        func: Callable[P, Any],
        request_cluster_name: Optional[str] = None,
        ignore_return_value: bool = False,
        schedule_type: api_requests.ScheduleType = (
            api_requests.ScheduleType.LONG),
        is_skypilot_system: bool = False,
        precondition: Optional[preconditions.Precondition] = None,
        retryable: bool = False,
        auth_user: Optional[models.User] = None) -> None:
    """Enqueue a request to the request queue.

    Args:
        request_id: ID of the request.
        request_name: Name of the request type, e.g. "sky.launch".
        request_body: The request body containing parameters and environment
            variables.
        func: The function to execute when the request is processed.
        request_cluster_name: The name of the cluster associated with this
            request, if any.
        ignore_return_value: If True, the return value of the function will be
            ignored.
        schedule_type: The type of scheduling to use for this request, refer to
            `api_requests.ScheduleType` for more details.
        is_skypilot_system: Denote whether the request is from SkyPilot system.
        precondition: If a precondition is provided, the request will only be
            scheduled for execution when the precondition is met (returns True).
            The precondition is waited asynchronously and does not block the
            caller.
    """
    request_task = await prepare_request_async(request_id,
                                               request_name,
                                               request_body,
                                               func,
                                               request_cluster_name,
                                               schedule_type,
                                               is_skypilot_system,
                                               auth_user=auth_user)
    await schedule_prepared_request(request_task, ignore_return_value,
                                    precondition, retryable)


async def schedule_internal_daemon_async(
        daemon: 'daemons.InternalRequestDaemon') -> None:
    """Submit an internal daemon's request to the executor.

    Idempotent under concurrent callers (multiple uvicorn workers in the
    same process; multiple replicas sharing a PG-backed request store):

    - First caller inserts a fresh PENDING row + enqueues onto the task
      queue.
    - Subsequent callers UPDATE `request_body` / `name` /
      `schedule_type` on the existing row (so the persisted env_vars
      reflect *this* process's `os.environ` rather than whatever the
      original creator captured) and skip the enqueue (the existing
      task_queue entry from the original creator remains in place).

    This replaces the previous "schedule_request_async → catch
    RequestAlreadyExistsError → log debug" pattern for daemon
    requests: the dedup contract is identical (exactly one concurrent
    caller wins the insert race and enqueues), but losing callers now
    actively refresh env-bearing columns on the existing row instead
    of leaving stale state in place.
    """
    request = api_requests.build_internal_daemon_request(daemon)
    inserted = await api_requests.create_or_refresh_internal_daemon_async(
        request)
    if inserted:
        await schedule_prepared_request(request, retryable=True)
    else:
        logger.debug(f'Internal daemon {daemon.id} row refreshed (existed); '
                     'enqueue skipped.')


async def schedule_prepared_request(request_task: api_requests.Request,
                                    ignore_return_value: bool = False,
                                    precondition: Optional[
                                        preconditions.Precondition] = None,
                                    retryable: bool = False) -> None:
    """Enqueue a request to the request queue

    Args:
        request_task: The prepared request task to schedule.
        ignore_return_value: If True, the return value of the function will be
            ignored.
        precondition: If a precondition is provided, the request will only be
            scheduled for execution when the precondition is met (returns True).
            The precondition is waited asynchronously and does not block the
            caller.
        retryable: Whether the request should be retried if it fails.
    """

    async def enqueue():
        input_tuple = (request_task.request_id, ignore_return_value, retryable)
        logger.info(f'Queuing request: {request_task.request_id}')
        await _get_queue(request_task.schedule_type).put_async(input_tuple)

    if precondition is not None:
        # Schedule precondition wait as a background task so the caller
        # returns immediately.  The task reference is stored in a
        # module-level set to prevent garbage collection.
        task = asyncio.create_task(
            precondition.wait_async(on_condition_met=enqueue))
        preconditions.background_tasks.add(task)
        task.add_done_callback(preconditions.background_tasks.discard)
    else:
        await enqueue()


def start(
    config: server_config.ServerConfig
) -> Tuple[Optional[multiprocessing.Process], List[RequestWorker]]:
    """Start the request workers.

    Request workers run in background, schedule the requests and delegate the
    request execution to executor processes.

    Returns:
        A tuple of the queue server process and the list of request worker
        threads.
    """
    global _queue_factory
    factory = queue_base.get_queue_backend_factory()
    # Use specified factory if any, and fallback to default impl
    if factory is not None:
        _queue_factory = factory
    elif config.queue_backend == server_config.QueueBackend.MULTIPROCESSING:
        _queue_factory = queue_base.MultiprocessingQueueFactory()
    elif config.queue_backend == server_config.QueueBackend.LOCAL:
        _queue_factory = queue_base.LocalQueueFactory()
    else:
        raise RuntimeError(f'Invalid queue backend: {config.queue_backend}')

    queue_server = _queue_factory.start()
    logger.info('Request queues created')

    workers = []
    # Start a worker for long requests.
    long_worker = RequestWorker(schedule_type=api_requests.ScheduleType.LONG,
                                config=config.long_worker_config)
    long_worker.run_in_background()
    workers.append(long_worker)

    # Start a worker for short requests.
    short_worker = RequestWorker(schedule_type=api_requests.ScheduleType.SHORT,
                                 config=config.short_worker_config)
    short_worker.run_in_background()
    workers.append(short_worker)
    return queue_server, workers
