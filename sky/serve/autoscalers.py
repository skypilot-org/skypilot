"""Autoscalers: perform autoscaling by monitoring metrics."""
import logging
import threading
import time

from typing import Optional

from sky.serve.infra_providers import InfraProvider
from sky.serve.load_balancers import LoadBalancer

logger = logging.getLogger(__name__)


class Autoscaler:
    """Abstract class for autoscalers."""

    def __init__(self,
                 infra_provider: InfraProvider,
                 load_balancer: LoadBalancer,
                 frequency: int = 60) -> None:
        self.infra_provider = infra_provider
        self.load_balancer = load_balancer
        self.frequency = frequency  # Time to sleep in seconds.

    def evaluate_scaling(self) -> None:
        raise NotImplementedError

    def scale_up(self, num_nodes_to_add: int) -> None:
        logger.debug(f'Scaling up by {num_nodes_to_add} nodes')
        self.infra_provider.scale_up(num_nodes_to_add)

    def scale_down(self, num_nodes_to_remove: int) -> None:
        logger.debug(f'Scaling down by {num_nodes_to_remove} nodes')
        self.infra_provider.scale_down(num_nodes_to_remove)

    def monitor(self) -> None:
        logger.info('Starting autoscaler monitor.')
        while not self.monitor_thread_stop_event.is_set():
            try:
                self.evaluate_scaling()
            except Exception as e:  # pylint: disable=broad-except
                # No matter what error happens, we should keep the
                # monitor running.
                logger.error(f'Error in autoscaler monitor: {e}')
            time.sleep(self.frequency)

    def start_monitor(self) -> None:
        self.monitor_thread_stop_event = threading.Event()
        self.monitor_thread = threading.Thread(target=self.monitor)
        self.monitor_thread.start()

    def terminate_monitor(self) -> None:
        self.monitor_thread_stop_event.set()
        self.monitor_thread.join()


class RequestRateAutoscaler(Autoscaler):
    """
    Autoscaler that scales  when the number of requests in the given
    interval is above or below the upper threshold.
    """

    def __init__(self,
                 *args,
                 min_nodes: int = 1,
                 max_nodes: Optional[int] = None,
                 upper_threshold: Optional[float] = None,
                 lower_threshold: Optional[float] = None,
                 cooldown: int = 60,
                 **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.min_nodes: int = min_nodes
        # Default to fixed node, i.e. min_nodes == max_nodes.
        self.max_nodes: int = max_nodes or min_nodes
        self.query_interval: int = 60
        self.upper_threshold: Optional[float] = upper_threshold
        self.lower_threshold: Optional[float] = lower_threshold
        self.cooldown: int = cooldown
        self.last_scale_operation: float = 0.  # Time of last scale operation.

    def evaluate_scaling(self) -> None:
        current_time = time.time()

        # Check if cooldown period has passed since the last scaling operation
        if current_time - self.last_scale_operation < self.cooldown:
            logger.info(f'Current time: {current_time}, '
                        f'last scale operation: {self.last_scale_operation}, '
                        f'cooldown: {self.cooldown}')
            logger.info(
                'Cooldown period has not passed since last scaling operation.'
                ' Skipping scaling.')
            return

        while (self.load_balancer.request_timestamps and
               current_time - self.load_balancer.request_timestamps[0] >
               self.query_interval):
            self.load_balancer.request_timestamps.popleft()

        num_requests = len(self.load_balancer.request_timestamps)
        # Convert to requests per second.
        num_requests = float(num_requests) / self.query_interval
        num_nodes = self.infra_provider.total_servers()
        # Edge case: num_nodes is zero.
        requests_per_node = (num_requests /
                             num_nodes if num_nodes else num_requests)

        logger.info(f'Requests per node: {requests_per_node}')
        logger.info(f'Upper threshold: {self.upper_threshold} qps/node, '
                    f'lower threshold: {self.lower_threshold} qps/node, '
                    f'queries per node: {requests_per_node} qps/node')

        # Bootstrap case
        logger.info(f'Number of nodes: {num_nodes}')
        if num_nodes < self.min_nodes:
            logger.info('Bootstrapping autoscaler.')
            self.scale_up(1)
            self.last_scale_operation = current_time
        elif (self.upper_threshold is not None and
              requests_per_node > self.upper_threshold):
            if self.infra_provider.total_servers() < self.max_nodes:
                self.scale_up(1)
                self.last_scale_operation = current_time
        elif (self.lower_threshold is not None and
              requests_per_node < self.lower_threshold):
            if self.infra_provider.total_servers() > self.min_nodes:
                self.scale_down(1)
                self.last_scale_operation = current_time
        else:
            logger.info('No scaling needed.')
