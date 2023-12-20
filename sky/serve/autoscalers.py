"""Autoscalers: perform autoscaling by monitoring metrics."""
import bisect
import dataclasses
import enum
import math
import time
import typing
from typing import Any, Dict, List, Optional, Union

from sky import sky_logging
from sky.serve import constants
from sky.serve import serve_state

if typing.TYPE_CHECKING:
    from sky.serve import replica_managers
    from sky.serve import service_spec

logger = sky_logging.init_logger(__name__)


class AutoscalerDecisionOperator(enum.Enum):
    SCALE_UP = 'scale_up'
    SCALE_DOWN = 'scale_down'


@dataclasses.dataclass
class AutoscalerDecision:
    """Autoscaling decisions.

    |---------------------------------------------------------------|
    | Operator   | TargetType       | Meaning                       |
    |------------|------------------|-------------------------------|
    | SCALE_UP   | Dict[str, Any]   | Resource override to add      |
    |------------|------------------|-------------------------------|
    | SCALE_DOWN | int              | Replica id to remove          |
    |---------------------------------------------------------------|
    """
    operator: AutoscalerDecisionOperator
    target: Optional[Union[Dict[str, Any], int]]

    def __init__(self,
                 operator: AutoscalerDecisionOperator,
                 target: Optional[Union[Dict[str, Any], int]] = None):

        # Scale down requires replica ids to remove.
        assert not (operator == AutoscalerDecisionOperator.SCALE_DOWN and
                    target is None)
        self.operator = operator
        self.target = target

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
        self.frequency: int = frequency
        if self.frequency < constants.LB_CONTROLLER_SYNC_INTERVAL_SECONDS:
            logger.warning('Autoscaler frequency is less than '
                           'controller sync interval. It might '
                           'not always got the latest information.')
        self.target_num_replicas: int = spec.min_replicas

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
                 rps_window_size: int) -> None:
        """Initialize the request rate autoscaler.

        Variables:
            target_qps_per_replica: Target qps per replica for autoscaling
            rps_window_size: Window size for rps calculating.
            request_timestamps: All request timestamps within the window.
            upscale_counter: counter for upscale number of replicas.
            downscale_counter: counter for downscale number of replicas.
            scale_up_consecutive_periods: period for scaling up.
            scale_down_consecutive_periods: period for scaling down.
        """
        super().__init__(spec, frequency)
        self.target_qps_per_replica: Optional[
            float] = spec.target_qps_per_replica
        self.rps_window_size: int = rps_window_size
        self.request_timestamps: List[float] = []
        self.upscale_counter: int = 0
        self.downscale_counter: int = 0
        self.scale_up_consecutive_periods: int = int(
            spec.upscale_delay_seconds / self.frequency)
        self.scale_down_consecutive_periods: int = int(
            spec.downscale_delay_seconds / self.frequency)
        # Target number of replicas is initialized to min replicas.
        self.target_num_replicas: int = spec.min_replicas
        self.boostrap_done: bool = False

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

    def _get_desired_num_replicas(self) -> int:

        # If target_qps_per_replica is not set
        # Return default target_num_replicas.
        if self.target_qps_per_replica is None:
            return self.target_num_replicas

        # Convert to requests per second.
        num_requests_per_second = len(
            self.request_timestamps) / self.rps_window_size
        target_num_replicas = math.ceil(num_requests_per_second /
                                        self.target_qps_per_replica)
        target_num_replicas = max(self.min_replicas,
                                  min(self.max_replicas, target_num_replicas))
        logger.info(f'Requests per second: {num_requests_per_second}, '
                    f'Current target number of replicas: {target_num_replicas}')

        if not self.boostrap_done:
            self.boostrap_done = True
            return target_num_replicas
        elif target_num_replicas > self.target_num_replicas:
            self.upscale_counter += 1
            self.downscale_counter = 0
            if self.upscale_counter >= self.scale_up_consecutive_periods:
                self.upscale_counter = 0
                return target_num_replicas
        elif target_num_replicas < self.target_num_replicas:
            self.downscale_counter += 1
            self.upscale_counter = 0
            if self.downscale_counter >= self.scale_down_consecutive_periods:
                self.downscale_counter = 0
                return target_num_replicas
        else:
            self.upscale_counter = self.downscale_counter = 0
        return self.target_num_replicas

    def evaluate_scaling(
        self,
        replica_infos: List['replica_managers.ReplicaInfo'],
    ) -> List[AutoscalerDecision]:

        alive_replica_infos = [info for info in replica_infos if info.is_alive]
        num_alive_replicas = len(alive_replica_infos)

        self.target_num_replicas = self._get_desired_num_replicas()
        logger.info(
            f'Final target number of replicas: {self.target_num_replicas} '
            f'Upscale counter: {self.upscale_counter}/'
            f'{self.scale_up_consecutive_periods}, '
            f'Downscale counter: {self.downscale_counter}/'
            f'{self.scale_down_consecutive_periods}')
        logger.info(f'Number of alive replicas: {num_alive_replicas}')

        scaling_options = []
        all_replica_ids_to_scale_down: List[int] = []

        def _get_replica_ids_to_scale_down(
            status_order: List['serve_state.ReplicaStatus'],
            num_limit: int,
        ) -> List[int]:
            replica_ids_to_scale_down: List[int] = []
            for target_status in status_order:
                for info in alive_replica_infos:
                    if info.status == target_status:
                        if len(replica_ids_to_scale_down) >= num_limit:
                            return replica_ids_to_scale_down
                        replica_ids_to_scale_down.append(info.replica_id)
            for info in alive_replica_infos:
                if info.status not in status_order:
                    if len(replica_ids_to_scale_down) >= num_limit:
                        return replica_ids_to_scale_down
                    replica_ids_to_scale_down.append(info.replica_id)
            return replica_ids_to_scale_down

        if num_alive_replicas < self.target_num_replicas:
            num_replicas_to_scale_up = (self.target_num_replicas -
                                        num_alive_replicas)

            for _ in range(num_replicas_to_scale_up):
                scaling_options.append(
                    AutoscalerDecision(AutoscalerDecisionOperator.SCALE_UP))

        elif num_alive_replicas > self.target_num_replicas:
            num_replicas_to_scale_down = (num_alive_replicas -
                                          self.target_num_replicas)
            all_replica_ids_to_scale_down.extend(
                _get_replica_ids_to_scale_down(
                    status_order=serve_state.ReplicaStatus.
                    scale_down_decision_order(),
                    num_limit=num_replicas_to_scale_down,
                ))

        for replica_id in all_replica_ids_to_scale_down:
            scaling_options.append(
                AutoscalerDecision(AutoscalerDecisionOperator.SCALE_DOWN,
                                   target=replica_id))

        if not scaling_options:
            logger.info('No scaling needed.')
        return scaling_options
