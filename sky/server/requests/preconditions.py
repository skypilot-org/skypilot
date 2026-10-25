"""Precondition for a request to be executed.

Preconditions are introduced so that:
- Wait for precondition does not block executor process, which is expensive;
- Cross requests knowledge (e.g. waiting for other requests to be completed)
  can be handled at precondition level, instead of invading the execution
  logic of specific requests.

A request that waits for a precondition is stored in the request database
(see `requests.create_with_precondition_wait_async`) as a kind plus a small
set of parameters. One checker in the API server process rebuilds each waiting
precondition from that record, evaluates it and enqueues the request, so the
wait survives the death of the worker that accepted the request.
"""
import abc
import asyncio
import inspect
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple, Type, Union

from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky.server import constants as server_constants
from sky.server.requests import requests as api_requests
from sky.utils import common_utils
from sky.utils import status_lib

# The default timeout seconds to wait for the precondition to be met.
_PRECONDITION_TIMEOUT = 60 * 60

logger = sky_logging.init_logger(__name__)

# Strong references to background precondition tasks to prevent GC.
# asyncio only keeps weak references to tasks, so without this set a
# long-running precondition wait (up to 1 hour) could be collected.
# Deprecated: only the in-process wait of a precondition that cannot be stored
# in the request database adds tasks here.
background_tasks: set = set()


class Precondition(abc.ABC):
    """Abstract base class for a precondition for a request to be executed.

    The request database stores the wait of the built-in subclasses in this
    module: each sets `KIND` and implements `params`, and `build_precondition`
    rebuilds it later. Any other subclass still works, but the accepting
    process waits for it in memory, and the wait is lost if that process dies.
    No other subclass can be stored.

    Deprecated: awaiting a precondition and `wait_async`. For a built-in
    subclass, pass the precondition to `executor.schedule_request_async`
    instead, which stores the wait in the request database.
    """

    # A unique name that identifies a built-in subclass in the request
    # database. Other subclasses leave it empty.
    KIND: str = ''

    def __init__(self,
                 request_id: str,
                 check_interval: Optional[float] = None,
                 timeout: float = _PRECONDITION_TIMEOUT):
        """Create a precondition.

        Args:
            request_id: The request ID of the request this precondition gates.
            check_interval: Deprecated. Seconds between two checks of the
                in-process wait. Nothing else uses it.
            timeout: Seconds to wait before the request is failed.
        """
        if check_interval is not None:
            # TODO(tobocop2): Use the standard decorator once #11190 adds it.
            logger.warning(
                'The check_interval argument of Precondition is deprecated. '
                'The API server checks stored preconditions at its own '
                'interval.')
        self.request_id = request_id
        self.check_interval = (
            server_constants.PRECONDITION_CHECK_INTERVAL_SECONDS
            if check_interval is None else check_interval)
        self.timeout = timeout

    def params(self) -> Dict[str, Any]:
        """The constructor arguments, other than request_id, as JSON values."""
        raise NotImplementedError

    def __await__(self):
        """Make Precondition awaitable.

        Deprecated: use `executor.schedule_request_async`.
        """
        # TODO(tobocop2): Use the standard decorator once #11190 adds it.
        logger.warning(
            'Awaiting a Precondition is deprecated. Pass the precondition to '
            'executor.schedule_request_async. The wait then lives in the '
            'request database for a built-in precondition, and in the '
            'worker memory for any other.')
        return self._wait().__await__()

    async def wait_async(
        self,
        on_condition_met: Optional[Callable[[], Union[None,
                                                      Awaitable[Any]]]] = None
    ) -> None:
        """Wait for the precondition and execute the callback when met.

        This coroutine blocks until the precondition is met (or times out).
        Use ``create_task(precondition.wait_async(...))`` to run it in the
        background without blocking the caller.

        Deprecated: the wait lives in this process only. Pass the precondition
        to `executor.schedule_request_async` instead.
        """
        # TODO(tobocop2): Use the standard decorator once #11190 adds it.
        logger.warning(
            'Precondition.wait_async is deprecated. Pass the precondition to '
            'executor.schedule_request_async. The wait then lives in the '
            'request database for a built-in precondition, and in the '
            'worker memory for any other.')
        await self.wait_in_process(on_condition_met)

    async def wait_in_process(
        self,
        on_condition_met: Optional[Callable[[], Union[None,
                                                      Awaitable[Any]]]] = None
    ) -> None:
        """The in-memory wait, for what the request database cannot store."""
        met = await self._wait()
        if met and on_condition_met is not None:
            result = on_condition_met()
            if inspect.isawaitable(result):
                await result

    @abc.abstractmethod
    async def check(self) -> Tuple[bool, Optional[str]]:
        """Check if the precondition is met.

        Note that compared to _request_execution_wrapper, the env vars and
        skypilot config here are not overridden since the lack of process
        isolation, which may cause issues if the check accidentally depends on
        these. Make sure the check function is independent of the request
        environment.
        TODO(aylei): a new request context isolation mechanism is needed to
        enable more tasks/sub-tasks to be processed in coroutines or threads.

        Returns:
            A tuple of (bool, Optional[str]).
            The bool indicates if the precondition is met.
            The str is the current status of the precondition if any.
        """
        raise NotImplementedError

    async def _wait(self) -> bool:
        """Wait for the precondition to be met, in this process."""
        start_time = time.time()
        last_status_msg = ''
        while True:
            if self.timeout > 0 and time.time() - start_time > self.timeout:
                # Cancel the request on timeout.
                await api_requests.set_request_failed_async(
                    self.request_id,
                    exceptions.RequestCancelled(
                        f'Request {self.request_id} precondition wait timed '
                        f'out after {self.timeout}s'))
                return False

            # Check if the request has been cancelled
            request = await api_requests.get_request_async(self.request_id,
                                                           fields=['status'])
            if request is None:
                logger.error(f'Request {self.request_id} not found')
                return False
            if request.status == api_requests.RequestStatus.CANCELLED:
                logger.debug(f'Request {self.request_id} cancelled')
                return False
            del request

            try:
                met, status_msg = await self.check()
                if met:
                    return True
                if status_msg is not None and status_msg != last_status_msg:
                    # Update the status message if it has changed.
                    await api_requests.update_status_msg_async(
                        self.request_id, status_msg)
                    last_status_msg = status_msg
            except (Exception, SystemExit, KeyboardInterrupt) as e:  # pylint: disable=broad-except
                await api_requests.set_request_failed_async(self.request_id, e)
                logger.info(f'Request {self.request_id} failed due to '
                            f'{common_utils.format_exception(e)}')
                return False

            await asyncio.sleep(self.check_interval)


class RequestSucceededPrecondition(Precondition):
    """Met once another request has SUCCEEDED.

    Chains a follow-up request behind the request it depends on. If the
    awaited request ends FAILED or CANCELLED, or no longer exists, `check`
    raises so the follow-up request is marked FAILED with that reason rather
    than running against state the awaited request never produced.

    Args:
        request_id: The request ID of the follow-up request that this
            precondition gates.
        awaited_request_id: The request ID that must SUCCEED first.
    """

    KIND = 'request_succeeded'

    def __init__(self, request_id: str, awaited_request_id: str, **kwargs):
        super().__init__(request_id=request_id, **kwargs)
        self.awaited_request_id = awaited_request_id

    def params(self) -> Dict[str, Any]:
        return {'awaited_request_id': self.awaited_request_id}

    async def check(self) -> Tuple[bool, Optional[str]]:
        awaited = await api_requests.get_request_async(self.awaited_request_id,
                                                       fields=['status'])
        if awaited is None:
            raise exceptions.RequestCancelled(
                f'Skipped: request {self.awaited_request_id} was not found.')
        status = awaited.status
        if status == api_requests.RequestStatus.SUCCEEDED:
            return True, None
        if status in (api_requests.RequestStatus.FAILED,
                      api_requests.RequestStatus.CANCELLED):
            raise exceptions.RequestCancelled(
                f'Skipped: request {self.awaited_request_id} ended '
                f'{status.value}.')
        return False, (f'Waiting for request {self.awaited_request_id} '
                       'to finish')


class ClusterStartCompletePrecondition(Precondition):
    """Whether the start process of a cluster is complete.

    This condition only waits the start process of a cluster to complete, e.g.
    `sky launch` or `sky start`.
    For cluster that has been started but not in UP status, bypass the waiting
    in favor of:
    - allowing the task to refresh cluster status from cloud vendor;
    - unified error message in task handlers.

    Args:
        request_id: The request ID of the task.
        cluster_name: The name of the cluster to wait for.
    """

    KIND = 'cluster_start_complete'

    def __init__(self, request_id: str, cluster_name: str, **kwargs):
        super().__init__(request_id=request_id, **kwargs)
        self.cluster_name = cluster_name

    def params(self) -> Dict[str, Any]:
        return {'cluster_name': self.cluster_name}

    async def check(self) -> Tuple[bool, Optional[str]]:
        cluster_status = global_user_state.get_status_from_cluster_name(
            self.cluster_name)
        if cluster_status is status_lib.ClusterStatus.UP:
            # Shortcut for started clusters, ignore cluster not found
            # since the cluster record might not yet be created by the
            # launch task.
            return True, None
        # Check if there is a task starting the cluster, we do not check
        # SUCCEEDED requests since successfully launched cluster can be
        # restarted later on.
        # Note that since the requests are not persistent yet between restarts,
        # a cluster might be started in halfway and requests are lost.
        # We unify these situations into a single state: the process of starting
        # the cluster is done (either normally or abnormally) but cluster is not
        # in UP status.
        requests = await api_requests.get_request_tasks_async(
            req_filter=api_requests.RequestTaskFilter(
                status=api_requests.RequestStatus.active_statuses(),
                include_request_names=['sky.launch', 'sky.start'],
                cluster_names=[self.cluster_name],
                # Only get the request ID to avoid fetching the whole request.
                # We're only interested in the count, not the whole request.
                fields=['request_id']))
        if len(requests) == 0:
            # No running or pending tasks, the start process is done.
            return True, None
        return False, f'Waiting for cluster {self.cluster_name} to be UP.'


_KINDS: Dict[str, Type[Precondition]] = {
    cls.KIND: cls
    for cls in (RequestSucceededPrecondition, ClusterStartCompletePrecondition)
}


def build_precondition(kind: str, request_id: str,
                       params: Dict[str, Any]) -> Precondition:
    """Rebuild a stored precondition from its kind and parameters."""
    if kind not in _KINDS:
        raise ValueError(f'Unknown precondition kind: {kind!r}')
    return _KINDS[kind](request_id=request_id, **params)


def is_storable(precondition: Precondition) -> bool:
    """Whether the request database can store and rebuild the precondition.

    A subclass of a stored kind that overrides `check` is not storable: the
    checker would rebuild the base class and lose the override.
    """
    return _KINDS.get(precondition.KIND) is type(precondition)
