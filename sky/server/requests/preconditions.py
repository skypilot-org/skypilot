"""Precondition for a request to be executed.

Preconditions are introduced so that:
- Wait for precondition does not block executor process, which is expensive;
- Cross requests knowledge (e.g. waiting for other requests to be completed)
  can be handled at precondition level, instead of invading the execution
  logic of specific requests.
"""
import abc
import asyncio
import time
from typing import Callable, Optional, Tuple

from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky.server.requests import event_loop
from sky.server.requests import requests as api_requests
from sky.utils import common_utils
from sky.utils import status_lib

# The default interval seconds to check the precondition.
_PRECONDITION_CHECK_INTERVAL = 1
# The default timeout seconds to wait for the precondition to be met.
_PRECONDITION_TIMEOUT = 60 * 60

logger = sky_logging.init_logger(__name__)


class Precondition(abc.ABC):
    """Abstract base class for a precondition for a request to be executed.

    A Precondition can be waited in either of the following ways:
    - await Precondition: wait for the precondition to be met.
    - Precondition.wait_async: wait for the precondition to be met in background
      and execute the given callback on met.
    """

    def __init__(self,
                 request_id: str,
                 check_interval: float = _PRECONDITION_CHECK_INTERVAL,
                 timeout: float = _PRECONDITION_TIMEOUT):
        self.request_id = request_id
        self.check_interval = check_interval
        self.timeout = timeout

    def __await__(self):
        """Make Precondition awaitable."""
        return self._wait().__await__()

    def wait_async(
            self,
            on_condition_met: Optional[Callable[[], None]] = None) -> None:
        """Wait precondition asynchronously and execute the callback on met."""

        async def wait_with_callback():
            met = await self
            if met and on_condition_met is not None:
                on_condition_met()

        event_loop.run(wait_with_callback())

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
        """Wait for the precondition to be met.

        Args:
            on_condition_met: Callback to execute when the precondition is met.
        """
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

    def __init__(self, request_id: str, cluster_name: str, **kwargs):
        super().__init__(request_id=request_id, **kwargs)
        self.cluster_name = cluster_name

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
                status=[
                    api_requests.RequestStatus.PENDING,
                    api_requests.RequestStatus.RUNNING
                ],
                include_request_names=['sky.launch', 'sky.start'],
                cluster_names=[self.cluster_name],
                # Only get the request ID to avoid fetching the whole request.
                # We're only interested in the count, not the whole request.
                fields=['request_id']))
        if len(requests) == 0:
            # No running or pending tasks, the start process is done.
            return True, None
        return False, f'Waiting for cluster {self.cluster_name} to be UP.'
