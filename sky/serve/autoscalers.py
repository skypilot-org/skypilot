"""Autoscalers: perform autoscaling by monitoring metrics."""
import bisect
import dataclasses
import enum
import logging
import time
import typing
from typing import List, Optional

from sky.serve import constants
from sky.serve import serve_state
from sky.serve import serve_utils

if typing.TYPE_CHECKING:
    from sky.serve import infra_providers

logger = logging.getLogger(__name__)

# Since sky.launch is very resource demanding, we limit the number of
# concurrent sky.launch process to avoid overloading the machine.
# TODO(tian): determine this value based on controller resources.
_MAX_BOOTSTRAPPING_NUM = 5


class AutoscalerDecisionOperator(enum.Enum):
    SCALE_UP = 'scale_up'
    SCALE_DOWN = 'scale_down'
    NO_OP = 'no_op'


@dataclasses.dataclass
class AutoscalerDecision:
    operator: AutoscalerDecisionOperator
    num_replicas: Optional[int]


class Autoscaler:
    """Abstract class for autoscalers."""

    def __init__(self,
                 auto_restart: bool,
                 frequency: int,
                 min_nodes: int = 1,
                 max_nodes: Optional[int] = None) -> None:
        self.auto_restart = auto_restart
        self.min_nodes: int = min_nodes
        # Default to fixed node, i.e. min_nodes == max_nodes.
        self.max_nodes: int = max_nodes or min_nodes
        self.frequency = frequency  # Time to sleep in seconds.
        if self.frequency < constants.CONTROLLER_SYNC_INTERVAL:
            logger.warning('Autoscaler frequency is less than '
                           'controller sync interval. It might '
                           'not always got the latest information.')

    def update_request_information(
            self, request_information: serve_utils.RequestInformation) -> None:
        raise NotImplementedError

    def evaluate_scaling(
            self,
            infos: List['infra_providers.ReplicaInfo']) -> AutoscalerDecision:
        raise NotImplementedError


class RequestRateAutoscaler(Autoscaler):
    """RequestRateAutoscaler: Autoscale according to request rate.

    Scales when the number of requests in the given interval is above or below
    the threshold.
    """

    def __init__(self, *args, upper_threshold: Optional[float],
                 lower_threshold: Optional[float], cooldown: int,
                 rps_window_size: int, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Cooldown between two scaling operations in seconds.
        self.cooldown: int = cooldown
        # Window size for rps calculating.
        self.rps_window_size: int = rps_window_size
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
                                   current_time - self.rps_window_size)
        self.request_timestamps = self.request_timestamps[index:]

    def evaluate_scaling(
            self,
            infos: List['infra_providers.ReplicaInfo']) -> AutoscalerDecision:
        current_time = time.time()
        if not self.auto_restart:
            num_nodes = len(infos)
        else:
            num_nodes = len([
                i for i in infos if i.status != serve_state.ReplicaStatus.FAILED
            ])

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
                return AutoscalerDecision(AutoscalerDecisionOperator.NO_OP,
                                          num_replicas=None)

        # Convert to requests per second.
        num_requests_per_second = len(
            self.request_timestamps) / self.rps_window_size
        # Edge case: num_nodes is zero.
        requests_per_node = (num_requests_per_second / num_nodes
                             if num_nodes else num_requests_per_second)

        logger.info(f'Requests per node: {requests_per_node}')

        # Bootstrap case
        logger.info(f'Number of nodes: {num_nodes}')
        if num_nodes < self.min_nodes:
            logger.info('Bootstrapping service.')
            self.last_scale_operation = current_time
            return AutoscalerDecision(AutoscalerDecisionOperator.SCALE_UP,
                                      num_replicas=min(
                                          self.min_nodes - num_nodes,
                                          _MAX_BOOTSTRAPPING_NUM))
        if (self.upper_threshold is not None and
                requests_per_node > self.upper_threshold):
            if num_nodes < self.max_nodes:
                scale_target = requests_per_node / self.upper_threshold
                num_nodes_to_add = int(scale_target * num_nodes) - num_nodes
                if num_nodes_to_add > 0:
                    plural = 's' if num_nodes_to_add > 1 else ''
                    logger.info(
                        'Requests per node is above upper threshold '
                        f'{self.upper_threshold}qps/node. '
                        f'Scaling up by {num_nodes_to_add} node{plural}.')
                    self.last_scale_operation = current_time
                    return AutoscalerDecision(
                        AutoscalerDecisionOperator.SCALE_UP,
                        num_replicas=num_nodes_to_add)
        if (self.lower_threshold is not None and
                requests_per_node < self.lower_threshold):
            if num_nodes > self.min_nodes:
                scale_target = requests_per_node / self.lower_threshold
                num_nodes_to_remove = num_nodes - int(scale_target * num_nodes)
                if num_nodes_to_remove > 0:
                    plural = 's' if num_nodes_to_remove > 1 else ''
                    logger.info(
                        'Requests per node is below lower threshold '
                        f'{self.lower_threshold}qps/node. '
                        f'Scaling down by {num_nodes_to_remove} node{plural}.')
                    self.last_scale_operation = current_time
                    return AutoscalerDecision(
                        AutoscalerDecisionOperator.SCALE_DOWN,
                        num_replicas=num_nodes_to_remove)
        logger.info('No scaling needed.')
        return AutoscalerDecision(AutoscalerDecisionOperator.NO_OP,
                                  num_replicas=None)
