"""Autoscalers: perform autoscaling by monitoring metrics."""
import bisect
import dataclasses
import enum
import math
import time
import typing
from typing import Any, Dict, List, Optional, Tuple, Union

from sky import sky_logging
from sky.serve import constants
from sky.serve import serve_state
from sky.serve import spot_policy

if typing.TYPE_CHECKING:
    from sky.serve import replica_managers
    from sky.serve import service_spec

logger = sky_logging.init_logger(__name__)

# TODO(tian): Expose this to config.
_UPSCALE_DELAY_S = 300
_DOWNSCALE_DELAY_S = 6000


class AutoscalerDecisionOperator(enum.Enum):
    SCALE_UP = 'scale_up'
    SCALE_DOWN = 'scale_down'


@dataclasses.dataclass
class AutoscalerDecision:
    """Autoscaling decisions.

    |-------------------------------------------------------------------------|
    | Operator   | TargetType                 | Meaning                       |
    |------------|----------------------------|-------------------------------|
    | SCALE_UP   | Tuple[int, Dict[str, Any]] | Num and override to add       |
    |------------|----------------------------|-------------------------------|
    | SCALE_DOWN | List[int]                  | List of replica ids to remove |
    |-------------------------------------------------------------------------|
    """
    operator: AutoscalerDecisionOperator
    target: Union[Tuple[int, Dict[str, Any]], List[int]]

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
            self, request_aggregator_info: Dict[str, Any]) -> None:
        """Collect request information from aggregator for autoscaling."""
        raise NotImplementedError

    def evaluate_scaling(
        self,
        replica_infos: List['replica_managers.ReplicaInfo'],
    ) -> List[AutoscalerDecision]:
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
            self, request_aggregator_info: Dict[str, Any]) -> None:
        """Collect request information from aggregator for autoscaling.

        request_aggregator_info should be a dict with the following format:

        {
            'timestamps': [timestamp1 (float), timestamp2 (float), ...]
        }
        """
        self.request_timestamps.extend(
            request_aggregator_info.get('timestamps', []))
        current_time = time.time()
        index = bisect.bisect_left(self.request_timestamps,
                                   current_time - self.rps_window_size)
        self.request_timestamps = self.request_timestamps[index:]

    def evaluate_scaling(
        self,
        replica_infos: List['replica_managers.ReplicaInfo'],
    ) -> List[AutoscalerDecision]:
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
                return []

        # Convert to requests per second.
        num_requests_per_second = len(
            self.request_timestamps) / self.rps_window_size
        # Edge case: num_replicas is zero.
        requests_per_replica = (num_requests_per_second / num_replicas
                                if num_replicas else num_requests_per_second)

        logger.info(f'Requests per replica: {requests_per_replica}')

        logger.info(f'Number of replicas: {num_replicas}')
        target_num_replicas = num_replicas
        if num_replicas < self.min_replicas:
            target_num_replicas = self.min_replicas
        elif (self.upper_threshold is not None and
              requests_per_replica > self.upper_threshold):
            scale_target = requests_per_replica / self.upper_threshold
            target_num_replicas = int(scale_target * num_replicas)
        elif (self.lower_threshold is not None and
              requests_per_replica < self.lower_threshold):
            scale_target = requests_per_replica / self.lower_threshold
            target_num_replicas = int(scale_target * num_replicas)

        target_num_replicas = max(self.min_replicas,
                                  min(self.max_replicas, target_num_replicas))
        num_replicas_delta = target_num_replicas - num_replicas
        if num_replicas_delta == 0:
            logger.info('No scaling needed.')
            return []
        elif num_replicas_delta > 0:
            logger.info(f'Scaling up by {num_replicas_delta} replicas.')
            return [
                AutoscalerDecision(AutoscalerDecisionOperator.SCALE_UP,
                                   target=(num_replicas_delta, {}))
            ]
        else:
            num_replicas_to_remove = -num_replicas_delta
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
            logger.info(f'Scaling down by {num_replicas_to_remove} replicas '
                        f'(id: {replica_ids_to_remove}).')
            return [
                AutoscalerDecision(AutoscalerDecisionOperator.SCALE_DOWN,
                                   target=replica_ids_to_remove)
            ]


class SpotRequestRateAutoscaler(RequestRateAutoscaler):
    """SpotRequestRateAutoscaler: Use spot to autoscale based on request rate.

    This autoscaler uses spot instances to save cost while maintaining the
    same performance as OnDemand instances.
    """

    def __init__(self, spec: 'service_spec.SkyServiceSpec', frequency: int,
                 cooldown: int, rps_window_size: int) -> None:
        super().__init__(spec, frequency, cooldown, rps_window_size)
        assert (spec.spot_placer is not None and spec.spot_mixer is not None and
                spec.spot_zones is not None and
                spec.target_qps_per_replica is not None)
        self.policy = spot_policy.SpotPolicy(spec)
        self.target_qps_per_replica = spec.target_qps_per_replica
        # TODO(tian): Maybe add init_replicas?
        self.target_num_replicas = spec.min_replicas

        self.upscale_counter: int = 0
        self.downscale_counter: int = 0

        self.scale_up_consecutive_periods: int = int(_UPSCALE_DELAY_S /
                                                     self.frequency)
        self.scale_down_consecutive_periods: int = int(_DOWNSCALE_DELAY_S /
                                                       self.frequency)

    def get_desired_num_replicas(self, current_num_replicas: int) -> int:
        # Convert to requests per second.
        num_requests_per_second = len(
            self.request_timestamps) / self.rps_window_size
        # Edge case: num_replicas is zero.
        requests_per_replica = (num_requests_per_second /
                                current_num_replicas if current_num_replicas
                                else num_requests_per_second)
        logger.info(f'Requests per replica: {requests_per_replica}')
        target_num_replicas = math.ceil(requests_per_replica /
                                        self.target_qps_per_replica)
        target_num_replicas = max(self.min_replicas,
                                  min(self.max_replicas, target_num_replicas))

        if target_num_replicas > self.target_num_replicas:
            self.upscale_counter += 1
            self.downscale_counter = 0
            if self.upscale_counter >= self.scale_up_consecutive_periods:
                return target_num_replicas
        elif target_num_replicas < self.target_num_replicas:
            self.downscale_counter += 1
            self.upscale_counter = 0
            if self.downscale_counter >= self.scale_down_consecutive_periods:
                return target_num_replicas
        return self.target_num_replicas

    def evaluate_scaling(
        self,
        replica_infos: List['replica_managers.ReplicaInfo'],
    ) -> List[AutoscalerDecision]:
        current_time = time.time()
        # TODO(tian): Consider non-alive replicas.
        alive_replica_infos = [
            info for info in replica_infos if info.is_alive or
            info.status == serve_state.ReplicaStatus.NOT_READY
        ]
        num_replicas = len(alive_replica_infos)

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
                return []
        else:
            # Bootstrap.
            return [
                AutoscalerDecision(
                    AutoscalerDecisionOperator.SCALE_UP,
                    (self.target_num_replicas,
                     self.policy.get_spot_resources_override_dict()))
            ]

        desired_num_replicas = self.get_desired_num_replicas(num_replicas)
        if desired_num_replicas == self.target_num_replicas:
            return []
        self.target_num_replicas = desired_num_replicas

        (num_on_demand_to_launch, spot_zones_to_launch,
         replica_ids_to_scale_down) = self.policy.generate_autoscaling_plan(
             self.target_num_replicas, replica_infos)

        scaling_options = []
        if num_on_demand_to_launch > 0:
            scaling_options.append(
                AutoscalerDecision(
                    AutoscalerDecisionOperator.SCALE_UP,
                    target=(
                        num_on_demand_to_launch,
                        self.policy.get_on_demand_resources_override_dict())))
        if spot_zones_to_launch:
            for zone in spot_zones_to_launch:
                spot_override = self.policy.get_spot_resources_override_dict()
                spot_override.update({'zone': zone})
                scaling_options.append(
                    AutoscalerDecision(AutoscalerDecisionOperator.SCALE_UP,
                                       target=(1, spot_override)))
        if replica_ids_to_scale_down:
            scaling_options.append(
                AutoscalerDecision(AutoscalerDecisionOperator.SCALE_DOWN,
                                   target=replica_ids_to_scale_down))
        return scaling_options
