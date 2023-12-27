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
from sky.serve import spot_policy
from sky.utils import ux_utils

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

    |------------------------------------------------------------------------|
    | Operator   | TargetType                | Meaning                       |
    |------------|---------------------------|-------------------------------|
    | SCALE_UP   | Optional[Dict[str, Any]   | Resource override to add      |
    |------------|---------------------------|-------------------------------|
    | SCALE_DOWN | int                       | Replica id to remove          |
    |------------------------------------------------------------------------|
    """
    operator: AutoscalerDecisionOperator
    target: Union[Optional[Dict[str, Any]], int]

    # TODO(MaoZiming): Add a doc to elaborate on autoscaling policies.
    def __init__(self, operator: AutoscalerDecisionOperator,
                 target: Union[Optional[Dict[str, Any]], int]):

        assert (operator == AutoscalerDecisionOperator.SCALE_UP and
                (target is None or isinstance(target, dict))) or (
                    operator == AutoscalerDecisionOperator.SCALE_DOWN and
                    isinstance(target, int))
        self.operator = operator
        self.target = target

    def __repr__(self) -> str:
        return f'AutoscalerDecision({self.operator}, {self.target})'


class Autoscaler:
    """Abstract class for autoscalers."""

    def __init__(self, spec: 'service_spec.SkyServiceSpec') -> None:
        """Initialize the autoscaler.

        Variables:
            min_replicas: Minimum number of replicas.
            max_replicas: Maximum number of replicas. Default to fixed
                number of replicas, i.e. min_replicas == max_replicas.
            target_num_replicas: Target number of replicas output by autoscaler.
        """
        self.min_replicas: int = spec.min_replicas
        self.max_replicas: int = spec.max_replicas or spec.min_replicas
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

    @classmethod
    def from_spec(cls, spec: 'service_spec.SkyServiceSpec'):

        if spec.spot_placer is None:
            return RequestRateAutoscaler(spec)

        if spec.spot_mixer not in ['DynamicFallback', 'StaticSpotProvision']:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Unsupported spot mixer: {spec.spot_mixer}.')

        return SpotRequestRateAutoscaler(spec)


class RequestRateAutoscaler(Autoscaler):
    """RequestRateAutoscaler: Autoscale according to request rate.

    Scales when the number of requests in the given interval is above or below
    the threshold.
    """

    def __init__(self, spec: 'service_spec.SkyServiceSpec') -> None:
        """Initialize the request rate autoscaler.

        Variables:
            target_qps_per_replica: Target qps per replica for autoscaling.
            request_timestamps: All request timestamps within the window.
            upscale_counter: counter for upscale number of replicas.
            downscale_counter: counter for downscale number of replicas.
            scale_up_consecutive_periods: period for scaling up.
            scale_down_consecutive_periods: period for scaling down.
        """
        super().__init__(spec)
        assert spec.target_qps_per_replica is not None
        self.target_qps_per_replica: float = spec.target_qps_per_replica
        self.request_timestamps: List[float] = []
        self.num_overprovision: int = (
            spec.num_overprovision if spec.num_overprovision is not None else 0)
        self.upscale_counter: int = 0
        self.downscale_counter: int = 0
        self.scale_up_consecutive_periods: int = int(
            spec.upscale_delay_seconds /
            constants.AUTOSCALER_DEFAULT_DECISION_INTERVAL_SECONDS)
        self.scale_down_consecutive_periods: int = int(
            spec.downscale_delay_seconds /
            constants.AUTOSCALER_DEFAULT_DECISION_INTERVAL_SECONDS)
        # Target number of replicas is initialized to min replicas.
        self.target_num_replicas: int = (spec.num_init_replicas
                                         if spec.num_init_replicas is not None
                                         else spec.min_replicas)
        self.bootstrap_done: bool = False

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
        index = bisect.bisect_left(
            self.request_timestamps,
            current_time - constants.AUTOSCALER_QPS_WINDOW_SIZE_SECONDS)
        self.request_timestamps = self.request_timestamps[index:]

    def _get_desired_num_replicas(self) -> int:
        # Always return self.target_num_replicas when autoscaling
        # is not enabled, i.e. self.target_qps_per_replica is None.
        # In this case, self.target_num_replicas will be min_replicas.
        if self.target_qps_per_replica is None:
            return self.target_num_replicas

        # Convert to requests per second.
        num_requests_per_second = len(
            self.request_timestamps
        ) / constants.AUTOSCALER_QPS_WINDOW_SIZE_SECONDS
        target_num_replicas = math.ceil(num_requests_per_second /
                                        self.target_qps_per_replica)
        target_num_replicas = max(self.min_replicas,
                                  min(self.max_replicas, target_num_replicas))
        logger.info(f'Requests per second: {num_requests_per_second}, '
                    f'Current target number of replicas: {target_num_replicas}')

        if not self.bootstrap_done:
            self.bootstrap_done = True
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

        logger.info(
            f'Final target number of replicas: {self.target_num_replicas} '
            f'({self.target_num_replicas + self.num_overprovision} with '
            f'over-provision), Upscale counter: {self.upscale_counter}/'
            f'{self.scale_up_consecutive_periods}, '
            f'Downscale counter: {self.downscale_counter}/'
            f'{self.scale_down_consecutive_periods}')
        return self.target_num_replicas

    def _get_replica_ids_to_scale_down(
        self, num_limit: int,
        launched_replica_infos: List['replica_managers.ReplicaInfo']
    ) -> List[int]:

        status_order = serve_state.ReplicaStatus.scale_down_decision_order()
        launched_replica_infos_sorted = sorted(
            launched_replica_infos,
            key=lambda info: status_order.index(info.status)
            if info.status in status_order else len(status_order))

        return [info.replica_id for info in launched_replica_infos_sorted
               ][:num_limit]

    def _get_launched_replica_infos(
            self, replica_infos: List['replica_managers.ReplicaInfo']):
        return [info for info in replica_infos if info.is_launched]

    def evaluate_scaling(
        self,
        replica_infos: List['replica_managers.ReplicaInfo'],
    ) -> List[AutoscalerDecision]:
        """Evaluate Autoscaling decisions based on replica information.
        If the number of launched replicas is less than the target,
        Trigger a scale up. Else, trigger a scale down.

        For future compatibility, we return a list of AutoscalerDecision.
        Scale-up could include both spot and on-demand, each with a resource
        override dict. Active migration could require returning both SCALE_UP
        and SCALE_DOWN.
        """
        launched_replica_infos = self._get_launched_replica_infos(replica_infos)
        num_launched_replicas = len(launched_replica_infos)

        self.target_num_replicas = self._get_desired_num_replicas()

        scaling_options = []
        all_replica_ids_to_scale_down: List[int] = []

        if num_launched_replicas < self.target_num_replicas:
            num_replicas_to_scale_up = (self.target_num_replicas -
                                        num_launched_replicas)

            for _ in range(num_replicas_to_scale_up):
                scaling_options.append(
                    AutoscalerDecision(AutoscalerDecisionOperator.SCALE_UP,
                                       target=None))

        elif num_launched_replicas > self.target_num_replicas:
            num_replicas_to_scale_down = (num_launched_replicas -
                                          self.target_num_replicas)
            all_replica_ids_to_scale_down.extend(
                self._get_replica_ids_to_scale_down(num_replicas_to_scale_down,
                                                    launched_replica_infos))

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

    def __init__(self, spec: 'service_spec.SkyServiceSpec') -> None:
        super().__init__(spec)
        self.spot_placer: 'spot_policy.SpotPlacer' = (
            spot_policy.SpotPlacer.from_spec(spec))
        self.is_on_demand_fallback = (spec.spot_mixer == 'DynamicFallback')

    def _get_spot_resources_override_dict(self) -> Dict[str, Any]:
        return {'use_spot': True, 'spot_recovery': None}

    def _get_on_demand_resources_override_dict(self) -> Dict[str, Any]:
        return {'use_spot': False, 'spot_recovery': None}

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

        launched_replica_infos = self._get_launched_replica_infos(replica_infos)
        self.target_num_replicas = self._get_desired_num_replicas()

        num_alive_spot, num_ready_spot = 0, 0
        num_alive_on_demand, num_ready_on_demand = 0, 0
        for info in launched_replica_infos:
            if info.is_spot:
                if info.status == serve_state.ReplicaStatus.READY:
                    num_ready_spot += 1
                num_alive_spot += 1
            else:
                if info.status == serve_state.ReplicaStatus.READY:
                    num_ready_on_demand += 1
                num_alive_on_demand += 1
        logger.info(
            f'Number of alive spot instances: {num_alive_spot}, '
            f'Number of ready spot instances: {num_ready_spot}, '
            f'Number of alive on-demand instances: {num_alive_on_demand}, '
            f'Number of ready on-demand instances: {num_ready_on_demand}')

        if isinstance(self.spot_placer, spot_policy.HistoricalSpotPlacer):
            log_zone_to_type = {
                zone: zone_type.value
                for zone, zone_type in self.spot_placer.zone2type.items()
            }
            logger.info(f'Current zone to type: {log_zone_to_type}')

        scaling_options = []
        all_replica_ids_to_scale_down: List[int] = []
        num_to_provision = (self.target_num_replicas + self.num_overprovision)

        # Scale spot instances.
        current_considered_zones: List[str] = []
        if num_alive_spot < num_to_provision:
            # Not enough spot instances, scale up.
            num_spot_to_scale_up = num_to_provision - num_alive_spot
            for _ in range(num_spot_to_scale_up):
                spot_override = self._get_spot_resources_override_dict()
                zone = self.spot_placer.select(launched_replica_infos,
                                               current_considered_zones)
                current_considered_zones.append(zone)
                spot_override.update({'zone': zone})
                logger.info(f'Chosen zone {zone} with {self.spot_placer}')
                scaling_options.append(
                    AutoscalerDecision(AutoscalerDecisionOperator.SCALE_UP,
                                       target=spot_override))
        elif num_alive_spot > num_to_provision:
            # Too many spot instances, scale down.
            num_spot_to_scale_down = num_alive_spot - num_to_provision
            all_replica_ids_to_scale_down.extend(
                self._get_replica_ids_to_scale_down(
                    num_spot_to_scale_down,
                    [info for info in launched_replica_infos if info.is_spot]))

        # OnDemand fallback.
        if self.is_on_demand_fallback:
            num_demand_to_scale_up, num_demand_to_scale_down = 0, 0
            if num_ready_spot + num_alive_on_demand < num_to_provision:
                # Enable OnDemand fallback.
                num_demand_to_scale_up = min(
                    self.target_num_replicas,
                    num_to_provision - num_ready_spot) - num_alive_on_demand

            elif num_ready_spot + num_alive_on_demand > num_to_provision:
                # OnDemand fallback is not needed.
                num_demand_to_scale_down = (num_ready_spot +
                                            num_alive_on_demand -
                                            num_to_provision)

        if num_demand_to_scale_up > 0:
            for _ in range(num_demand_to_scale_up):
                scaling_options.append(
                    AutoscalerDecision(
                        AutoscalerDecisionOperator.SCALE_UP,
                        target=self._get_on_demand_resources_override_dict()))
        elif num_demand_to_scale_down > 0:
            all_replica_ids_to_scale_down.extend(
                self._get_replica_ids_to_scale_down(num_spot_to_scale_down, [
                    info for info in launched_replica_infos if not info.is_spot
                ]))

        for replica_id in all_replica_ids_to_scale_down:
            scaling_options.append(
                AutoscalerDecision(AutoscalerDecisionOperator.SCALE_DOWN,
                                   target=replica_id))
        return scaling_options
