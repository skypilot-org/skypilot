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
from sky.serve import spot_policies

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
            latest_version: latest version of the service.
        """
        self.min_replicas: int = spec.min_replicas
        self.max_replicas: int = (spec.max_replicas if spec.max_replicas
                                  is not None else spec.min_replicas)
        # Target number of replicas is initialized to min replicas
        self.target_num_replicas: int = spec.min_replicas
        self.latest_version: int = constants.INITIAL_VERSION

    def update_version(self, version: int,
                       spec: 'service_spec.SkyServiceSpec') -> None:
        if version <= self.latest_version:
            logger.error(f'Invalid version: {version}, '
                         f'latest version: {self.latest_version}')
            return
        self.latest_version = version
        self.min_replicas = spec.min_replicas
        self.max_replicas = (spec.max_replicas if spec.max_replicas is not None
                             else spec.min_replicas)

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
    def from_spec(cls, spec: 'service_spec.SkyServiceSpec') -> 'Autoscaler':
        # TODO(MaoZiming): use NAME to get the class.
        if spec.use_fallback:
            return SpotRequestRateAutoscaler(spec)
        else:
            return RequestRateAutoscaler(spec)


class RequestRateAutoscaler(Autoscaler):
    """RequestRateAutoscaler: Autoscale according to request rate.

    Scales when the number of requests in the given interval is above or below
    the threshold.
    """

    def __init__(self, spec: 'service_spec.SkyServiceSpec') -> None:
        """Initialize the request rate autoscaler.

        Variables:
            target_qps_per_replica: Target qps per replica for autoscaling.
            qps_window_size: Window size for qps calculating.
            request_timestamps: All request timestamps within the window.
            upscale_counter: counter for upscale number of replicas.
            downscale_counter: counter for downscale number of replicas.
            scale_up_consecutive_periods: period for scaling up.
            scale_down_consecutive_periods: period for scaling down.
        """
        super().__init__(spec)
        self.target_qps_per_replica: Optional[
            float] = spec.target_qps_per_replica
        self.qps_window_size: int = constants.AUTOSCALER_QPS_WINDOW_SIZE_SECONDS
        self.request_timestamps: List[float] = []
        self.upscale_counter: int = 0
        self.downscale_counter: int = 0
        upscale_delay_seconds = (
            spec.upscale_delay_seconds if spec.upscale_delay_seconds is not None
            else constants.AUTOSCALER_DEFAULT_UPSCALE_DELAY_SECONDS)
        self.scale_up_consecutive_periods: int = int(
            upscale_delay_seconds /
            constants.AUTOSCALER_DEFAULT_DECISION_INTERVAL_SECONDS)
        downscale_delay_seconds = (
            spec.downscale_delay_seconds
            if spec.downscale_delay_seconds is not None else
            constants.AUTOSCALER_DEFAULT_DOWNSCALE_DELAY_SECONDS)
        self.scale_down_consecutive_periods: int = int(
            downscale_delay_seconds /
            constants.AUTOSCALER_DEFAULT_DECISION_INTERVAL_SECONDS)

    def _cal_target_num_replicas_based_on_qps(self) -> int:
        # Update self.target_num_replicas:
        # Recalculate target_num_replicas based on QPS.
        # Reclip self.target_num_replicas with new min and max replicas.
        target_num_replicas = math.ceil(
            len(self.request_timestamps) / self.qps_window_size /
            self.target_qps_per_replica
        ) if self.target_qps_per_replica is not None else self.min_replicas
        return max(self.min_replicas, min(self.max_replicas,
                                          target_num_replicas))

    def update_version(self, version: int,
                       spec: 'service_spec.SkyServiceSpec') -> None:
        super().update_version(version, spec)
        self.target_qps_per_replica = spec.target_qps_per_replica
        upscale_delay_seconds = (
            spec.upscale_delay_seconds if spec.upscale_delay_seconds is not None
            else constants.AUTOSCALER_DEFAULT_UPSCALE_DELAY_SECONDS)
        self.scale_up_consecutive_periods = int(
            upscale_delay_seconds /
            constants.AUTOSCALER_DEFAULT_DECISION_INTERVAL_SECONDS)
        downscale_delay_seconds = (
            spec.downscale_delay_seconds
            if spec.downscale_delay_seconds is not None else
            constants.AUTOSCALER_DEFAULT_DOWNSCALE_DELAY_SECONDS)
        self.scale_down_consecutive_periods = int(
            downscale_delay_seconds /
            constants.AUTOSCALER_DEFAULT_DECISION_INTERVAL_SECONDS)

        self.target_num_replicas = self._cal_target_num_replicas_based_on_qps()

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
                                   current_time - self.qps_window_size)
        self.request_timestamps = self.request_timestamps[index:]

    def _set_desired_num_replicas(self) -> None:
        """Set self.target_num_replicas based on request rate.
        """
        # Maintain self.target_num_replicas when autoscaling
        # is not enabled, i.e. self.target_qps_per_replica is None.
        # In this case, self.target_num_replicas will be min_replicas.
        if self.target_qps_per_replica is None:
            return

        # Convert to requests per second.
        target_num_replicas = self._cal_target_num_replicas_based_on_qps()
        old_target_num_replicas = self.target_num_replicas

        # Faster scale up when there is no replica.
        if self.target_num_replicas == 0:
            self.target_num_replicas = target_num_replicas
        elif target_num_replicas > self.target_num_replicas:
            self.upscale_counter += 1
            self.downscale_counter = 0
            if self.upscale_counter >= self.scale_up_consecutive_periods:
                self.upscale_counter = 0
                self.target_num_replicas = target_num_replicas
        elif target_num_replicas < self.target_num_replicas:
            self.downscale_counter += 1
            self.upscale_counter = 0
            if self.downscale_counter >= self.scale_down_consecutive_periods:
                self.downscale_counter = 0
                self.target_num_replicas = target_num_replicas
        else:
            self.upscale_counter = self.downscale_counter = 0

        num_requests_per_second = len(
            self.request_timestamps) / self.qps_window_size
        logger.info(
            f'Requests per second: {num_requests_per_second}. '
            f'Current target number of replicas: {old_target_num_replicas}. '
            f'Final target number of replicas: {self.target_num_replicas}. '
            f'Upscale counter: {self.upscale_counter}/'
            f'{self.scale_up_consecutive_periods}. '
            f'Downscale counter: {self.downscale_counter}/'
            f'{self.scale_down_consecutive_periods}')

    @classmethod
    def select_replicas_to_scale_down(
            cls, num_limit: int,
            replica_infos: List['replica_managers.ReplicaInfo']) -> List[int]:

        status_order = serve_state.ReplicaStatus.scale_down_decision_order()
        replica_infos_sorted = sorted(
            replica_infos,
            key=lambda info: status_order.index(info.status)
            if info.status in status_order else len(status_order))

        return [info.replica_id for info in replica_infos_sorted][:num_limit]

    def get_decision_interval(self) -> int:
        # Reduce autoscaler interval when target_num_replicas = 0.
        # This will happen when min_replicas = 0 and no traffic.
        if self.target_num_replicas == 0:
            return constants.AUTOSCALER_NO_REPLICA_DECISION_INTERVAL_SECONDS
        else:
            return constants.AUTOSCALER_DEFAULT_DECISION_INTERVAL_SECONDS

    def select_outdated_replicas_to_scale_down(
            self,
            replica_infos: List['replica_managers.ReplicaInfo']) -> List[int]:

        ready_new_replicas: List['replica_managers.ReplicaInfo'] = []
        old_replicas: List['replica_managers.ReplicaInfo'] = []
        for info in replica_infos:
            if info.version == self.latest_version:
                if info.is_ready:
                    ready_new_replicas.append(info)
            else:
                old_replicas.append(info)

        all_replica_ids_to_scale_down: List[int] = []
        if len(ready_new_replicas) >= self.min_replicas:
            for info in old_replicas:
                all_replica_ids_to_scale_down.append(info.replica_id)

        return all_replica_ids_to_scale_down

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
        provisioning_and_launched_new_replicas: List[
            'replica_managers.ReplicaInfo'] = []

        for info in replica_infos:
            if info.version == self.latest_version:
                if info.is_launched:
                    provisioning_and_launched_new_replicas.append(info)

        self._set_desired_num_replicas()

        scaling_options: List[AutoscalerDecision] = []
        all_replica_ids_to_scale_down: List[int] = []

        # Case 1. Once there is min_replicas number of
        # ready new replicas, we will direct all traffic to them,
        # we can scale down all old replicas.
        all_replica_ids_to_scale_down.extend(
            self.select_outdated_replicas_to_scale_down(replica_infos))

        # Case 2. when provisioning_and_launched_new_replicas is less
        # than num_to_provision, we always scale up new replicas.
        if len(provisioning_and_launched_new_replicas
              ) < self.target_num_replicas:
            num_replicas_to_scale_up = (
                self.target_num_replicas -
                len(provisioning_and_launched_new_replicas))

            for _ in range(num_replicas_to_scale_up):
                scaling_options.append(
                    AutoscalerDecision(AutoscalerDecisionOperator.SCALE_UP,
                                       target=None))

        # Case 3: when provisioning_and_launched_new_replicas is more
        # than self.target_num_replicas, we scale down new replicas.
        if len(provisioning_and_launched_new_replicas
              ) > self.target_num_replicas:
            num_replicas_to_scale_down = (
                len(provisioning_and_launched_new_replicas) -
                self.target_num_replicas)
            all_replica_ids_to_scale_down.extend(
                RequestRateAutoscaler.select_replicas_to_scale_down(
                    num_limit=num_replicas_to_scale_down,
                    replica_infos=provisioning_and_launched_new_replicas))

        for replica_id in all_replica_ids_to_scale_down:
            scaling_options.append(
                AutoscalerDecision(AutoscalerDecisionOperator.SCALE_DOWN,
                                   target=replica_id))

        if not scaling_options:
            logger.info('No scaling needed.')
        return scaling_options


class SpotRequestRateAutoscaler(RequestRateAutoscaler):
    """SpotRequestRateAutoscaler: Use spot to autoscale based on request rate.

    This autoscaler uses spot instances to save cost.
    """

    def __init__(self, spec: 'service_spec.SkyServiceSpec') -> None:
        super().__init__(spec)
        self.spot_placer: Optional['spot_policies.SpotPlacer'] = (
            spot_policies.SpotPlacer.from_spec(spec)
        ) if spec.use_spot_placer else None
        self.base_ondemand_fallback_replicas: int = (
            spec.base_ondemand_fallback_replicas
            if spec.base_ondemand_fallback_replicas is not None else 0)
        self.dynamic_ondemand_fallback: bool = (
            spec.dynamic_ondemand_fallback
            if spec.dynamic_ondemand_fallback is not None else False)
        self.is_initialized: bool = False

    def update_version(self, version: int,
                       spec: 'service_spec.SkyServiceSpec') -> None:
        super().update_version(version, spec)
        self.spot_placer = (spot_policies.SpotPlacer.from_spec(spec)
                            if spec.use_spot_placer else None)
        self.base_ondemand_fallback_replicas = (
            spec.base_ondemand_fallback_replicas
            if spec.base_ondemand_fallback_replicas is not None else 0)
        self.dynamic_ondemand_fallback = (spec.dynamic_ondemand_fallback
                                          if spec.dynamic_ondemand_fallback
                                          is not None else False)
        self.is_initialized = False

    def handle_active_history(self,
                              history: List[spot_policies.Location]) -> None:
        if self.spot_placer is None:
            return
        for location in history:
            self.spot_placer.handle_active(location)

    def handle_preemption_history(
            self, history: List[spot_policies.Location]) -> None:
        if self.spot_placer is None:
            return
        for location in history:
            self.spot_placer.handle_preemption(location)

    def _get_spot_resources_override_dict(
            self,
            location: Optional[spot_policies.Location] = None
    ) -> Dict[str, Any]:
        # We have checked before any_of can only be used to
        # specify multiple zones, regions and clouds.
        return {'use_spot': True, **(location.to_dict() if location else {})}

    def _get_on_demand_resources_override_dict(self) -> Dict[str, Any]:
        return {'use_spot': False}

    def evaluate_scaling(
        self,
        replica_infos: List['replica_managers.ReplicaInfo'],
    ) -> List[AutoscalerDecision]:

        provisioning_and_launched_new_replicas = list(
            filter(
                lambda info: info.is_launched and info.version == self.
                latest_version, replica_infos))

        self._set_desired_num_replicas()
        num_launched_spot, num_ready_spot = 0, 0
        num_launched_on_demand, num_ready_on_demand = 0, 0

        for info in provisioning_and_launched_new_replicas:
            if info.is_spot:
                if info.status == serve_state.ReplicaStatus.READY:
                    num_ready_spot += 1
                num_launched_spot += 1
            else:
                if info.status == serve_state.ReplicaStatus.READY:
                    num_ready_on_demand += 1
                num_launched_on_demand += 1

        logger.info(
            f'Number of alive spot instances: {num_launched_spot}, '
            f'Number of ready spot instances: {num_ready_spot}, '
            f'Number of alive on-demand instances: {num_launched_on_demand}, '
            f'Number of ready on-demand instances: {num_ready_on_demand}')

        scaling_options: List[AutoscalerDecision] = []
        all_replica_ids_to_scale_down: List[int] = []

        # Decide how many spot instances to launch.
        num_spot_to_provision = (self.target_num_replicas -
                                 self.base_ondemand_fallback_replicas)
        if num_launched_spot < num_spot_to_provision:
            # Not enough spot instances, scale up.
            # Consult spot_placer for the zone to launch spot instance.
            num_spot_to_scale_up = num_spot_to_provision - num_launched_spot
            if self.spot_placer is not None:
                locations = self.spot_placer.select(
                    provisioning_and_launched_new_replicas,
                    num_spot_to_scale_up)
                assert len(locations) == num_spot_to_scale_up
                for location in locations:
                    spot_override = (
                        self._get_spot_resources_override_dict(location))
                    logger.info(
                        f'Chosen location {location} with {self.spot_placer}')
                    scaling_options.append(
                        AutoscalerDecision(AutoscalerDecisionOperator.SCALE_UP,
                                           target=spot_override))
            else:
                for _ in range(num_spot_to_scale_up):
                    scaling_options.append(
                        AutoscalerDecision(
                            AutoscalerDecisionOperator.SCALE_UP,
                            target=self._get_spot_resources_override_dict()))
        elif num_launched_spot > num_spot_to_provision:
            # Too many spot instances, scale down.
            # Get the replica to scale down with select_replicas_to_scale_down
            num_spot_to_scale_down = num_launched_spot - num_spot_to_provision
            all_replica_ids_to_scale_down.extend(
                RequestRateAutoscaler.select_replicas_to_scale_down(
                    num_spot_to_scale_down,
                    list(
                        filter(lambda info: info.is_spot,
                               provisioning_and_launched_new_replicas))))
        elif (num_ready_spot == num_spot_to_provision and
              self.is_initialized is False):
            # Used to not launch on_demand fallback during initialization.
            self.is_initialized = True

        # Once there is min_replicas number of
        # ready new replicas, we will direct all traffic to them,
        # we can scale down all old replicas.
        all_replica_ids_to_scale_down.extend(
            self.select_outdated_replicas_to_scale_down(replica_infos))

        # Decide how many on-demand instances to launch.
        num_on_demand_to_provision = self.base_ondemand_fallback_replicas
        if self.dynamic_ondemand_fallback and self.is_initialized:
            num_on_demand_to_provision += (num_spot_to_provision -
                                           num_launched_spot)

        if num_on_demand_to_provision > num_launched_on_demand:
            for _ in range(num_on_demand_to_provision - num_launched_on_demand):
                scaling_options.append(
                    AutoscalerDecision(
                        AutoscalerDecisionOperator.SCALE_UP,
                        target=self._get_on_demand_resources_override_dict()))
        elif num_launched_on_demand > num_on_demand_to_provision:
            all_replica_ids_to_scale_down.extend(
                RequestRateAutoscaler.select_replicas_to_scale_down(
                    num_launched_on_demand - num_on_demand_to_provision,
                    list(
                        filter(lambda info: not info.is_spot,
                               provisioning_and_launched_new_replicas))))

        for replica_id in all_replica_ids_to_scale_down:
            scaling_options.append(
                AutoscalerDecision(AutoscalerDecisionOperator.SCALE_DOWN,
                                   target=replica_id))

        return scaling_options
