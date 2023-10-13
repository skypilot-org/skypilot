"""Autoscalers: perform autoscaling by monitoring metrics."""
import bisect
import logging
import threading
import time
from typing import List, Optional

from sky.serve import constants
from sky.serve import infra_providers
from sky.serve import serve_utils

logger = logging.getLogger(__name__)

# Since sky.launch is very resource demanding, we limit the number of
# concurrent sky.launch process to avoid overloading the machine.
# TODO(tian): determine this value based on controller resources.
_MAX_BOOTSTRAPPING_NUM = 5


class Autoscaler:
    """Abstract class for autoscalers."""

    def __init__(self,
                 infra_provider: infra_providers.InfraProvider,
                 auto_restart: bool,
                 frequency: int,
                 min_nodes: int = 1,
                 max_nodes: Optional[int] = None) -> None:
        self.infra_provider = infra_provider
        self.auto_restart = auto_restart
        self.min_nodes: int = min_nodes
        # Default to fixed node, i.e. min_nodes == max_nodes.
        self.max_nodes: int = max_nodes or min_nodes
        self.frequency = frequency  # Time to sleep in seconds.
        if frequency < constants.CONTROLLER_SYNC_INTERVAL:
            logger.warning('Autoscaler frequency is less than '
                           'controller sync interval. It might '
                           'not always got the latest information.')

    def update_request_information(
            self, request_information: serve_utils.RequestInformation) -> None:
        raise NotImplementedError

    def evaluate_scaling(self) -> None:
        raise NotImplementedError

    def scale_up(self, num_nodes_to_add: int) -> None:
        logger.debug(f'Scaling up by {num_nodes_to_add} nodes')
        self.infra_provider.scale_up(num_nodes_to_add)

    def scale_down(self, num_nodes_to_remove: int) -> None:
        logger.debug(f'Scaling down by {num_nodes_to_remove} nodes')
        self.infra_provider.scale_down(num_nodes_to_remove)

    def run(self) -> None:
        logger.info('Starting autoscaler monitor.')
        while True:
            try:
                self.evaluate_scaling()
            except Exception as e:  # pylint: disable=broad-except
                # No matter what error happens, we should keep the
                # monitor running.
                logger.error(f'Error in autoscaler: {e}')
            for _ in range(self.frequency):
                if self.run_thread_stop_event.is_set():
                    return
                time.sleep(1)

    def start(self) -> None:
        self.run_thread_stop_event = threading.Event()
        self.run_thread = threading.Thread(target=self.run)
        self.run_thread.start()

    def terminate(self) -> None:
        self.run_thread_stop_event.set()
        self.run_thread.join()


class RequestRateAutoscaler(Autoscaler):
    """RequestRateAutoscaler: Autoscale according to request rate.

    Scales when the number of requests in the given interval is above or below
    the threshold.
    """

    def __init__(self, *args, upper_threshold: Optional[float],
                 lower_threshold: Optional[float], cooldown: int,
                 query_interval: int, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Cooldown between two scaling operations in seconds.
        self.cooldown: int = cooldown
        # Query interval for requests num. Every `query_interval` seconds,
        # Autoscaler will received an update for number of requests from
        # load balancer.
        self.query_interval: int = query_interval
        # Time of last scale operation
        self.last_scale_operation: float = 0.
        # All request timestamps
        self.request_timestamps: List[float] = []
        # Upper threshold for scale up. If None, no scale up.
        self.upper_threshold: Optional[float] = upper_threshold
        # Lower threshold for scale down. If None, no scale down.
        self.lower_threshold: Optional[float] = lower_threshold

    def update_request_information(
            self, request_information: serve_utils.RequestInformation) -> None:
        if not isinstance(request_information, serve_utils.RequestTimestamp):
            raise ValueError('Request information must be of type '
                             'serve_utils.RequestTimestamp for '
                             'RequestRateAutoscaler.')
        self.request_timestamps.extend(request_information.get())
        current_time = time.time()
        index = bisect.bisect_left(self.request_timestamps,
                                   current_time - self.query_interval)
        self.request_timestamps = self.request_timestamps[index:]

    def evaluate_scaling(self) -> None:
        current_time = time.time()
        num_nodes = self.infra_provider.total_replica_num(
            count_failed_replica=not self.auto_restart)

        # Check if cooldown period has passed since the last scaling operation.
        # Only cooldown if bootstrapping is done.
        if num_nodes >= self.min_nodes:
            if current_time - self.last_scale_operation < self.cooldown:
                logger.info(
                    f'Current time: {current_time}, '
                    f'last scale operation: {self.last_scale_operation}, '
                    f'cooldown: {self.cooldown}')
                logger.info('Cooldown period has not passed since last scaling '
                            'operation. Skipping scaling.')
                return

        # Convert to requests per second.
        num_requests_per_second = float(len(
            self.request_timestamps)) / self.query_interval
        # Edge case: num_nodes is zero.
        requests_per_node = (num_requests_per_second / num_nodes
                             if num_nodes else num_requests_per_second)

        logger.info(f'Requests per node: {requests_per_node}')

        # Bootstrap case
        logger.info(f'Number of nodes: {num_nodes}')
        if num_nodes < self.min_nodes:
            logger.info('Bootstrapping service.')
            self.scale_up(
                min(self.min_nodes - num_nodes, _MAX_BOOTSTRAPPING_NUM))
            self.last_scale_operation = current_time
        elif (self.upper_threshold is not None and
              requests_per_node > self.upper_threshold):
            if num_nodes < self.max_nodes:
                logger.info('Requests per node is above upper threshold '
                            f'{self.upper_threshold}qps/node. '
                            'Scaling up by 1 node.')
                self.scale_up(1)
                self.last_scale_operation = current_time
        elif (self.lower_threshold is not None and
              requests_per_node < self.lower_threshold):
            if num_nodes > self.min_nodes:
                logger.info('Requests per node is below lower threshold '
                            f'{self.lower_threshold}qps/node. '
                            'Scaling down by 1 node.')
                self.scale_down(1)
                self.last_scale_operation = current_time
        else:
            logger.info('No scaling needed.')
