import logging
import time

from typing import Optional

from sky.serve.infra_providers import InfraProvider
from sky.serve.load_balancers import LoadBalancer

logger = logging.getLogger(__name__)


class Autoscaler:

    def __init__(self,
                 infra_provider: InfraProvider,
                 load_balancer: LoadBalancer,
                 frequency: int = 60):
        self.infra_provider = infra_provider
        self.load_balancer = load_balancer
        self.frequency = frequency  # Time to sleep in seconds.

    def evaluate_scaling(self):
        raise NotImplementedError

    def scale_up(self, num_nodes_to_add: int):
        logger.debug(f'Scaling up by {num_nodes_to_add} nodes')
        self.infra_provider.scale_up(num_nodes_to_add)

    def scale_down(self, num_nodes_to_remove):
        logger.debug(f'Scaling down by {num_nodes_to_remove} nodes')
        self.infra_provider.scale_down(num_nodes_to_remove)

    def monitor(self):
        logger.info('Starting autoscaler monitor.')
        while True:
            self.evaluate_scaling()
            time.sleep(self.frequency)


class LatencyThresholdAutoscaler(Autoscaler):

    def __init__(self,
                 *args,
                 upper_threshold: int = 50,
                 lower_threshold: int = 1,
                 min_nodes: int = 1,
                 **kwargs):
        """
        Autoscaler that scales up when the average latency of all servers is above the upper threshold and scales down
        when the average latency of all servers is below the lower threshold.
        :param args:
        :param upper_threshold: upper threshold for latency in seconds
        :param lower_threshold: lower threshold for latency in seconds
        :param min_nodes: minimum number of nodes to keep running
        :param kwargs:
        """
        super().__init__(*args, **kwargs)
        self.upper_threshold = upper_threshold
        self.lower_threshold = lower_threshold
        self.min_nodes = min_nodes

    def evaluate_scaling(self):
        server_loads = self.load_balancer.server_loads
        if not server_loads:
            return

        avg_latencies = [
            sum(latencies) / len(latencies)
            for latencies in server_loads.values()
        ]

        if all(latency > self.upper_threshold for latency in avg_latencies):
            self.scale_up(1)
        elif all(latency < self.lower_threshold for latency in avg_latencies):
            if self.infra_provider.total_servers() > self.min_nodes:
                self.scale_down(1)


class RequestRateAutoscaler(Autoscaler):

    def __init__(self,
                 *args,
                 min_nodes: int = 1,
                 max_nodes: Optional[int] = None,
                 upper_threshold: Optional[float] = None,
                 lower_threshold: Optional[float] = None,
                 cooldown: int = 60,
                 **kwargs):
        """
        Autoscaler that scales  when the number of requests in the given interval is above or below the upper threshold
        :param args:
        :param query_interval:
        :param upper_threshold:
        :param lower_threshold:
        :param min_nodes:
        :param cooldown: Seconds to wait before scaling again.
        :param kwargs:
        """
        super().__init__(*args, **kwargs)
        self.min_nodes = min_nodes
        self.max_nodes = max_nodes or min_nodes
        self.query_interval = 60  # Therefore thresholds represent queries per minute.
        self.upper_threshold = upper_threshold
        self.lower_threshold = lower_threshold
        self.cooldown = cooldown
        self.last_scale_operation = 0  # Time of last scale operation.

    def evaluate_scaling(self):
        current_time = time.time()

        # Check if cooldown period has passed since the last scaling operation
        if current_time - self.last_scale_operation < self.cooldown:
            logger.info(f'Current time: {current_time}, '
                        f'last scale operation: {self.last_scale_operation}, '
                        f'cooldown: {self.cooldown}')
            logger.info(
                'Cooldown period has not passed since last scaling operation. Skipping scaling.'
            )
            return

        while (self.load_balancer.request_timestamps and
               current_time - self.load_balancer.request_timestamps[0] >
               self.query_interval):
            self.load_balancer.request_timestamps.popleft()

        num_requests = len(self.load_balancer.request_timestamps)
        num_requests = float(
            num_requests) / 60  # Convert to requests per second.
        num_nodes = self.infra_provider.total_servers()
        requests_per_node = num_requests / num_nodes if num_nodes else num_requests  # To account for zero case.

        logger.info(f'Requests per node: {requests_per_node}')
        logger.info(f'Upper threshold: {self.upper_threshold} qps/node, '
                    f'lower threshold: {self.lower_threshold} qps/node, '
                    f'queries per node: {requests_per_node} qps/node')

        scaled = True
        # Bootstrap case
        logger.info(f'Number of nodes: {num_nodes}')
        if num_nodes < self.min_nodes:
            logger.info('Bootstrapping autoscaler.')
            self.scale_up(1)
            self.last_scale_operation = current_time
        elif self.upper_threshold is not None and requests_per_node > self.upper_threshold:
            if self.infra_provider.total_servers() < self.max_nodes:
                self.scale_up(1)
                self.last_scale_operation = current_time
        elif self.lower_threshold is not None and requests_per_node < self.lower_threshold:
            if self.infra_provider.total_servers() > self.min_nodes:
                self.scale_down(1)
                self.last_scale_operation = current_time
        else:
            logger.info('No scaling needed.')
