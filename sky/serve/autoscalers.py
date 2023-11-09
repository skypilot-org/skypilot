"""Autoscalers: perform autoscaling by monitoring metrics."""
import bisect
import dataclasses
import enum
import time
import typing
from typing import List, Optional, Union

from sky import sky_logging
from sky.serve import constants
from sky.serve import serve_state
from sky.serve import serve_utils

if typing.TYPE_CHECKING:
    from sky.serve import replica_managers
    from sky.serve import service_spec

logger = sky_logging.init_logger(__name__)


class AutoscalerDecisionOperator(enum.Enum):
    SCALE_UP = 'scale_up'
    SCALE_DOWN = 'scale_down'
    NO_OP = 'no_op'


@dataclasses.dataclass
class AutoscalerDecision:
    """Autoscaling decisions.

    |---------------------------------------------------------|
    | Operator   | TargetType | Meaning                       |
    |------------|------------|-------------------------------|
    | SCALE_UP   | int        | Number of replicas to add     |
    |------------|------------|-------------------------------|
    | SCALE_DOWN | List[int]  | List of replica ids to remove |
    |------------|------------|-------------------------------|
    | NO_OP      | None       | No scaling needed             |
    |---------------------------------------------------------|
    """
    operator: AutoscalerDecisionOperator
    target: Optional[Union[int, List[int]]]

    def __repr__(self) -> str:
        return f'AutoscalerDecision({self.operator}, {self.target})'


class Autoscaler:
    """Abstract class for autoscalers."""

    def __init__(self, spec: 'service_spec.SkyServiceSpec',
                 frequency: int) -> None:
        """Initialize the autoscaler.

        Variables:
            min_replicas: Minimum number of replicas.
            max_replicas: Maximum number of replicas. Default to fixed
                number of replicas, i.e. min_replicas == max_replicas.
            frequency: Frequency of autoscaling in seconds.
        """
        self.min_replicas: int = spec.min_replicas
        self.max_replicas: int = spec.max_replicas or spec.min_replicas
        self.frequency = frequency
        if self.frequency < constants.LB_CONTROLLER_SYNC_INTERVAL_SECONDS:
            logger.warning('Autoscaler frequency is less than '
                           'controller sync interval. It might '
                           'not always got the latest information.')

    def collect_request_information(
            self, request_aggregator: serve_utils.RequestsAggregator) -> None:
        """Collect request information from aggregator for autoscaling."""
        raise NotImplementedError

    def evaluate_scaling(
        self,
        replica_infos: List['replica_managers.ReplicaInfo'],
    ) -> AutoscalerDecision:
        """Evaluate autoscale options based on replica information."""
        raise NotImplementedError


class RequestRateAutoscaler(Autoscaler):
    """RequestRateAutoscaler: Autoscale according to request rate.

    Scales when the number of requests in the given interval is above or below
    the threshold.
    """

    def __init__(self, spec: 'service_spec.SkyServiceSpec', frequency: int,
                 cooldown: int, rps_window_size: int) -> None:
        """Initialize the request rate autoscaler.

        Variables:
            upper_threshold: Upper threshold for scale up. If None, no scale up.
            lower_threshold: Lower threshold for scale down. If None, no scale
                down.
            cooldown: Cooldown between two scaling operations in seconds.
            rps_window_size: Window size for rps calculating.
            last_scale_operation: Time of last scale operation.
            request_timestamps: All request timestamps within the window.
        """
        super().__init__(spec, frequency)
        self.upper_threshold: Optional[float] = spec.qps_upper_threshold
        self.lower_threshold: Optional[float] = spec.qps_lower_threshold
        self.cooldown: int = cooldown
        self.rps_window_size: int = rps_window_size
        self.last_scale_operation: float = 0.
        self.request_timestamps: List[float] = []

    def collect_request_information(
            self, request_aggregator: serve_utils.RequestsAggregator) -> None:
        if not isinstance(request_aggregator, serve_utils.RequestTimestamp):
            raise ValueError('Request aggregator must be of type '
                             'serve_utils.RequestTimestamp for '
                             'RequestRateAutoscaler.')
        self.request_timestamps.extend(request_aggregator.get())
        current_time = time.time()
        index = bisect.bisect_left(self.request_timestamps,
                                   current_time - self.rps_window_size)
        self.request_timestamps = self.request_timestamps[index:]

    def evaluate_scaling(
        self,
        replica_infos: List['replica_managers.ReplicaInfo'],
    ) -> AutoscalerDecision:
        current_time = time.time()
        num_replicas = len(replica_infos)

        # Check if cooldown period has passed since the last scaling operation.
        # Only cooldown if bootstrapping is done.
        if num_replicas >= self.min_replicas:
            if current_time - self.last_scale_operation < self.cooldown:
                logger.info(
                    f'Current time: {current_time}, '
                    f'last scale operation: {self.last_scale_operation}, '
                    f'cooldown: {self.cooldown}')
                logger.info('Cooldown period has not passed since last scaling '
                            'operation. Skipping scaling.')
                return AutoscalerDecision(AutoscalerDecisionOperator.NO_OP,
                                          target=None)

        # Convert to requests per second.
        num_requests_per_second = len(
            self.request_timestamps) / self.rps_window_size
        # Edge case: num_replicas is zero.
        requests_per_replica = (num_requests_per_second / num_replicas
                                if num_replicas else num_requests_per_second)

        logger.info(f'Requests per replica: {requests_per_replica}')

        # Bootstrap case
        logger.info(f'Number of replicas: {num_replicas}')
        if num_replicas < self.min_replicas:
            logger.info('Bootstrapping service.')
            self.last_scale_operation = current_time
            return AutoscalerDecision(AutoscalerDecisionOperator.SCALE_UP,
                                      target=self.min_replicas - num_replicas)
        if (self.upper_threshold is not None and
                requests_per_replica > self.upper_threshold):
            if num_replicas < self.max_replicas:
                scale_target = requests_per_replica / self.upper_threshold
                num_replicas_to_add = min(
                    max(int(scale_target * num_replicas), self.min_replicas),
                    self.max_replicas) - num_replicas
                if num_replicas_to_add > 0:
                    plural = 's' if num_replicas_to_add > 1 else ''
                    logger.info('Requests per replica is above upper threshold '
                                f'{self.upper_threshold}qps / replica. '
                                f'Scaling up by {num_replicas_to_add} '
                                f'replica{plural}.')
                    self.last_scale_operation = current_time
                    return AutoscalerDecision(
                        AutoscalerDecisionOperator.SCALE_UP,
                        target=num_replicas_to_add)
        if (self.lower_threshold is not None and
                requests_per_replica < self.lower_threshold):
            if num_replicas > self.min_replicas:
                scale_target = requests_per_replica / self.lower_threshold
                num_replicas_to_remove = num_replicas - min(
                    int(scale_target * num_replicas), self.min_replicas)
                if num_replicas_to_remove > 0:
                    plural = 's' if num_replicas_to_remove > 1 else ''
                    logger.info('Requests per replica is below lower threshold '
                                f'{self.lower_threshold}qps / replica. '
                                f'Scaling down by {num_replicas_to_remove} '
                                f'replica{plural}.')
                    self.last_scale_operation = current_time
                    # Remove FAILED replicas first.
                    replica_ids_to_remove: List[int] = []
                    for info in replica_infos:
                        if len(replica_ids_to_remove) >= num_replicas_to_remove:
                            break
                        if info.status == serve_state.ReplicaStatus.FAILED:
                            replica_ids_to_remove.append(info.replica_id)
                    # Then rest of them.
                    for info in replica_infos:
                        if len(replica_ids_to_remove) >= num_replicas_to_remove:
                            break
                        replica_ids_to_remove.append(info.replica_id)
                    return AutoscalerDecision(
                        AutoscalerDecisionOperator.SCALE_DOWN,
                        target=replica_ids_to_remove)
        logger.info('No scaling needed.')
        return AutoscalerDecision(AutoscalerDecisionOperator.NO_OP, target=None)
