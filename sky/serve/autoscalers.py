"""Autoscalers: perform autoscaling by monitoring metrics."""
import bisect
import dataclasses
import enum
import math
import time
import typing
from typing import Any, Callable, Dict, List, Optional, Union

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
_DEFAULT_OVER_PROVISION_NUM = 1


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
    target: Union[Optional[Dict[str, Any]], int]

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
                                   target={}) for _ in range(num_replicas_delta)
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
                                   target=replica_id)
                for replica_id in replica_ids_to_remove
            ]


class OnDemandRateAutoscaler(RequestRateAutoscaler):
    """OnDemandRateAutoscaler: Use on-demand to autoscale based on request rate.

    This autoscaler uses on-demand instances to autoscale based on request
    rate.
    """

    def __init__(self, spec: 'service_spec.SkyServiceSpec', frequency: int,
                 cooldown: int, rps_window_size: int) -> None:
        super().__init__(spec, frequency, cooldown, rps_window_size)

        self.target_qps_per_replica = spec.target_qps_per_replica
        assert self.target_qps_per_replica is not None
        self.target_num_replicas = spec.min_replicas

        self.upscale_counter: int = 0
        self.downscale_counter: int = 0

        self.scale_up_consecutive_periods: int = int(_UPSCALE_DELAY_S /
                                                     self.frequency)
        self.scale_down_consecutive_periods: int = int(_DOWNSCALE_DELAY_S /
                                                       self.frequency)

    def _get_on_demand_resources_override_dict(self) -> Dict[str, Any]:
        return {'use_spot': False, 'spot_recovery': None}

    def _get_desired_num_replicas(self, num_ready_replicas: int) -> int:

        assert self.target_qps_per_replica is not None
        # Convert to requests per second.
        num_requests_per_second = len(
            self.request_timestamps) / self.rps_window_size
        # Edge case: num_replicas is zero.
        requests_per_replica = (num_requests_per_second / num_ready_replicas if
                                num_ready_replicas else num_requests_per_second)
        target_num_replicas = math.ceil(requests_per_replica /
                                        self.target_qps_per_replica)
        target_num_replicas = max(self.min_replicas,
                                  min(self.max_replicas, target_num_replicas))
        logger.info(f'Requests per replica: {requests_per_replica}, '
                    f'Current target number of replicas: {target_num_replicas}')

        if target_num_replicas > self.target_num_replicas:
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
        # TODO(tian): Consider non-alive replicas.
        alive_replica_infos = [info for info in replica_infos if info.is_alive]
        num_ready_replicas = len([
            info for info in replica_infos
            if info.status == serve_state.ReplicaStatus.READY
        ])

        # Don't count over-provision here.
        self.target_num_replicas = self._get_desired_num_replicas(
            max(num_ready_replicas, 0))
        logger.info(
            f'Final target number of replicas: {self.target_num_replicas} '
            f'({self.target_num_replicas} with '
            f'over-provision), Upscale counter: {self.upscale_counter}/'
            f'{self.scale_up_consecutive_periods}, '
            f'Downscale counter: {self.downscale_counter}/'
            f'{self.scale_down_consecutive_periods}')

        num_on_demand = 0
        for info in alive_replica_infos:
            if info.is_spot:
                assert False, ('OnDemandRateAutoscaler',
                               'should not have spot instances.')
            else:
                num_on_demand += 1

        logger.info(f'Number of alive on-demand instances: {num_on_demand}')

        scaling_options = []
        all_replica_ids_to_scale_down: List[int] = []

        def _get_replica_ids_to_scale_down(
            info_filter: Callable[['replica_managers.ReplicaInfo'], bool],
            status_order: List['serve_state.ReplicaStatus'],
            num_limit: int,
        ) -> List[int]:
            replica_ids_to_scale_down: List[int] = []
            for target_status in status_order:
                for info in alive_replica_infos:
                    if info_filter(info) and info.status == target_status:
                        if len(replica_ids_to_scale_down) >= num_limit:
                            return replica_ids_to_scale_down
                        replica_ids_to_scale_down.append(info.replica_id)
            for info in alive_replica_infos:
                if info_filter(info) and info.status not in status_order:
                    if len(replica_ids_to_scale_down) >= num_limit:
                        return replica_ids_to_scale_down
                    replica_ids_to_scale_down.append(info.replica_id)
            return replica_ids_to_scale_down

        num_to_provision = self.target_num_replicas

        if num_on_demand < num_to_provision:
            num_on_demand_to_scale_up = num_to_provision - num_on_demand

            for _ in range(num_on_demand_to_scale_up):
                scaling_options.append(
                    AutoscalerDecision(
                        AutoscalerDecisionOperator.SCALE_UP,
                        target=self._get_on_demand_resources_override_dict()))

        elif num_on_demand > num_to_provision:

            num_on_demand_to_scale_down = num_on_demand - num_to_provision
            all_replica_ids_to_scale_down.extend(
                _get_replica_ids_to_scale_down(
                    info_filter=lambda info: not info.is_spot,
                    status_order=serve_state.ReplicaStatus.
                    scale_down_decision_order(),
                    num_limit=num_on_demand_to_scale_down,
                ))

        for replica_id in all_replica_ids_to_scale_down:
            scaling_options.append(
                AutoscalerDecision(AutoscalerDecisionOperator.SCALE_DOWN,
                                   target=replica_id))

        if not scaling_options:
            logger.info('No scaling needed.')
        return scaling_options


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
        # TODO(tian): Change spot_mixer to boolean and implement algorithm
        # without fallback.
        self.spot_placer = spot_policy.SpotPlacer.from_spec(spec)
        self.target_qps_per_replica = spec.target_qps_per_replica
        # TODO(tian): Maybe add init_replicas?
        self.target_num_replicas = spec.min_replicas

        self.upscale_counter: int = 0
        self.downscale_counter: int = 0

        self.scale_up_consecutive_periods: int = int(_UPSCALE_DELAY_S /
                                                     self.frequency)
        self.scale_down_consecutive_periods: int = int(_DOWNSCALE_DELAY_S /
                                                       self.frequency)

    def _get_spot_resources_override_dict(self) -> Dict[str, Any]:
        return {'use_spot': True, 'spot_recovery': None}

    def _get_on_demand_resources_override_dict(self) -> Dict[str, Any]:
        return {'use_spot': False, 'spot_recovery': None}

    def _get_desired_num_replicas(self, num_ready_replicas: int) -> int:
        # Convert to requests per second.
        num_requests_per_second = len(
            self.request_timestamps) / self.rps_window_size
        # Edge case: num_replicas is zero.
        requests_per_replica = (num_requests_per_second / num_ready_replicas if
                                num_ready_replicas else num_requests_per_second)
        target_num_replicas = math.ceil(requests_per_replica /
                                        self.target_qps_per_replica)
        target_num_replicas = max(self.min_replicas,
                                  min(self.max_replicas, target_num_replicas))
        logger.info(f'Requests per replica: {requests_per_replica}, '
                    f'Current target number of replicas: {target_num_replicas}')

        if target_num_replicas > self.target_num_replicas:
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

    def handle_active_history(self, history: List[str]) -> None:
        for zone in history:
            self.spot_placer.handle_active(zone)

    def handle_preemption_history(self, history: List[str]) -> None:
        for zone in history:
            self.spot_placer.handle_preemption(zone)

    def evaluate_scaling(
        self,
        replica_infos: List['replica_managers.ReplicaInfo'],
    ) -> List[AutoscalerDecision]:
        # TODO(tian): Consider non-alive replicas.
        alive_replica_infos = [info for info in replica_infos if info.is_alive]
        num_ready_replicas = len([
            info for info in replica_infos
            if info.status == serve_state.ReplicaStatus.READY
        ])

        # Don't count over-provision here.
        self.target_num_replicas = self._get_desired_num_replicas(
            max(num_ready_replicas - _DEFAULT_OVER_PROVISION_NUM, 0))
        logger.info(
            f'Final target number of replicas: {self.target_num_replicas} '
            f'({self.target_num_replicas + _DEFAULT_OVER_PROVISION_NUM} with '
            f'over-provision), Upscale counter: {self.upscale_counter}/'
            f'{self.scale_up_consecutive_periods}, '
            f'Downscale counter: {self.downscale_counter}/'
            f'{self.scale_down_consecutive_periods}')

        num_alive_spot, num_ready_spot, num_on_demand = 0, 0, 0
        for info in alive_replica_infos:
            if info.is_spot:
                if info.status == serve_state.ReplicaStatus.READY:
                    num_ready_spot += 1
                num_alive_spot += 1
            else:
                num_on_demand += 1
        logger.info(f'Number of alive spot instances: {num_alive_spot}, '
                    f'Number of ready spot instances: {num_ready_spot}, '
                    f'Number of alive on-demand instances: {num_on_demand}')

        scaling_options = []
        all_replica_ids_to_scale_down: List[int] = []

        def _get_replica_ids_to_scale_down(
            info_filter: Callable[['replica_managers.ReplicaInfo'], bool],
            status_order: List['serve_state.ReplicaStatus'],
            num_limit: int,
        ) -> List[int]:
            replica_ids_to_scale_down: List[int] = []
            for target_status in status_order:
                for info in alive_replica_infos:
                    if info_filter(info) and info.status == target_status:
                        if len(replica_ids_to_scale_down) >= num_limit:
                            return replica_ids_to_scale_down
                        replica_ids_to_scale_down.append(info.replica_id)
            for info in alive_replica_infos:
                if info_filter(info) and info.status not in status_order:
                    if len(replica_ids_to_scale_down) >= num_limit:
                        return replica_ids_to_scale_down
                    replica_ids_to_scale_down.append(info.replica_id)
            return replica_ids_to_scale_down

        num_to_provision = (self.target_num_replicas +
                            _DEFAULT_OVER_PROVISION_NUM)

        # Scale spot instances.
        if num_alive_spot < num_to_provision:
            # Not enough spot instances, scale up.
            num_spot_to_scale_up = num_to_provision - num_alive_spot
            for _ in range(num_spot_to_scale_up):
                spot_override = self._get_spot_resources_override_dict()
                zone = self.spot_placer.select()
                spot_override.update({'zone': zone})
                logger.info(f'Chosen zone {zone} with {self.spot_placer}')
                scaling_options.append(
                    AutoscalerDecision(AutoscalerDecisionOperator.SCALE_UP,
                                       target=spot_override))
        elif num_alive_spot > num_to_provision:
            # Too many spot instances, scale down.
            num_spot_to_scale_down = num_alive_spot - num_to_provision
            all_replica_ids_to_scale_down.extend(
                _get_replica_ids_to_scale_down(
                    info_filter=lambda info: info.is_spot,
                    status_order=serve_state.ReplicaStatus.
                    scale_down_decision_order(),
                    num_limit=num_spot_to_scale_down,
                ))

        # OnDemand fallback.
        if num_ready_spot + num_on_demand < num_to_provision:
            # Enable OnDemand fallback.
            num_on_demand_to_scale_up = min(
                self.target_num_replicas,
                num_to_provision - num_ready_spot) - num_on_demand
            for _ in range(num_on_demand_to_scale_up):
                scaling_options.append(
                    AutoscalerDecision(
                        AutoscalerDecisionOperator.SCALE_UP,
                        target=self._get_on_demand_resources_override_dict()))
        elif num_ready_spot + num_on_demand > num_to_provision:
            # OnDemand fallback is not needed.
            num_on_demand_to_scale_down = (num_ready_spot + num_on_demand -
                                           num_to_provision)
            all_replica_ids_to_scale_down.extend(
                _get_replica_ids_to_scale_down(
                    info_filter=lambda info: not info.is_spot,
                    status_order=serve_state.ReplicaStatus.
                    scale_down_decision_order(),
                    num_limit=num_on_demand_to_scale_down,
                ))

        # TODO(tian): Safety Net.

        for replica_id in all_replica_ids_to_scale_down:
            scaling_options.append(
                AutoscalerDecision(AutoscalerDecisionOperator.SCALE_DOWN,
                                   target=replica_id))
        if not scaling_options:
            logger.info('No scaling needed.')
        return scaling_options
