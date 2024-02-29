"""Autoscalers: perform autoscaling by monitoring metrics."""
import bisect
import collections
import dataclasses
import enum
import math
import time
import typing
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Union

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

    def __init__(self, service_name: str,
                 spec: 'service_spec.SkyServiceSpec') -> None:
        """Initialize the autoscaler.

        Variables:
            min_replicas: Minimum number of replicas.
            max_replicas: Maximum number of replicas. Default to fixed
                number of replicas, i.e. min_replicas == max_replicas.
            target_num_replicas: Target number of replicas output by autoscaler.
            latest_version: latest version of the service.
        """
        self._service_name: str = service_name
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
        # Reclip self.target_num_replicas with new min and max replicas.
        self.target_num_replicas = max(
            self.min_replicas, min(self.max_replicas, self.target_num_replicas))

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
    def from_spec(cls, service_name: str,
                  spec: 'service_spec.SkyServiceSpec') -> 'Autoscaler':
        # TODO(MaoZiming): use NAME to get the class.
        if spec.use_ondemand_fallback:
            return FallbackRequestRateAutoscaler(service_name, spec)
        else:
            return RequestRateAutoscaler(service_name, spec)

    def get_latest_version_with_min_replicas(
        self, replica_infos: Iterable['replica_managers.ReplicaInfo']
    ) -> Optional[int]:
        """Get the latest version with at least min_replicas replicas."""
        raise NotImplementedError

    def dump_dynamic_states(self) -> Dict[str, Any]:
        """Dump dynamic states from autoscaler."""
        raise NotImplementedError

    def load_dynamic_states(self, dynamic_states: Dict[str, Any]) -> None:
        """Load dynamic states to autoscaler."""
        raise NotImplementedError


class RequestRateAutoscaler(Autoscaler):
    """RequestRateAutoscaler: Autoscale according to request rate.

    Scales when the number of requests per replica in the given interval
    is above or below the target qps per replica. The instance can be
    either spot or on-demand, but not both.
    """

    def __init__(self, service_name: str,
                 spec: 'service_spec.SkyServiceSpec') -> None:
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
        super().__init__(service_name, spec)
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
        # Recalculate target_num_replicas based on QPS.
        # Reclip self.target_num_replicas with new min and max replicas.
        if self.target_qps_per_replica is None:
            return self.min_replicas
        target_num_replicas = math.ceil(
            len(self.request_timestamps) / self.qps_window_size /
            self.target_qps_per_replica)
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

        # We directly set the target_num_replicas here instead of
        # calling `_set_target_num_replica_with_hysteresis` to have the replicas
        # quickly scale after each update.
        self.target_num_replicas = self._cal_target_num_replicas_based_on_qps()
        # Cleanup hysteretic counters.
        self.upscale_counter = 0
        self.downscale_counter = 0

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

    def _set_target_num_replica_with_hysteresis(self) -> None:
        """Set target_num_replicas based on request rate with hysteresis."""
        # Keep self.target_num_replicas unchange when autoscaling
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
    def _select_replicas_to_scale_down(
            cls, num_limit: int,
            replica_infos: Iterable['replica_managers.ReplicaInfo']
    ) -> List[int]:

        status_order = serve_state.ReplicaStatus.scale_down_decision_order()
        # Also sort by provisioned time (indicated by replica_id) to make sure
        # we terminate the replicas that starts provisioning later first
        replica_infos_sorted = sorted(
            replica_infos,
            key=lambda info: (
                status_order.index(info.status)
                # Use -1 for other status that will be terminated first.
                # Including: NOT_READY, SHUTTING_DOWN, FAILED,
                # FAILED_CLEANUP, PREEMPTED, UNKNOWN.
                if info.status in status_order else -1,
                # `-info.replica_id` is to furhter sort the replicas with the
                # same state by the time it starts launching. In that case,
                # if two replicas are both in PROVISIONING state, we will scale
                # down the one that starts provisioning later, i.e., it will
                # take a longer time for it to be READY.
                -info.replica_id))

        return [info.replica_id for info in replica_infos_sorted][:num_limit]

    def get_decision_interval(self) -> int:
        # Reduce autoscaler interval when target_num_replicas = 0.
        # This will happen when min_replicas = 0 and no traffic.
        if self.target_num_replicas == 0:
            return constants.AUTOSCALER_NO_REPLICA_DECISION_INTERVAL_SECONDS
        else:
            return constants.AUTOSCALER_DEFAULT_DECISION_INTERVAL_SECONDS

    def get_latest_version_with_min_replicas(
        self, replica_infos: Iterable['replica_managers.ReplicaInfo']
    ) -> Optional[int]:
        # Find the latest version with at least min_replicas replicas.
        version2count: DefaultDict[int, int] = collections.defaultdict(int)
        for info in replica_infos:
            if info.is_ready:
                version2count[info.version] += 1

        version = self.latest_version
        while version >= constants.INITIAL_VERSION:
            spec = serve_state.get_spec(self._service_name, version)
            if (spec is not None and
                    version2count[version] >= spec.min_replicas):
                return version
            version -= 1
        return None

    def select_outdated_replicas_to_scale_down(
            self, replica_infos: Iterable['replica_managers.ReplicaInfo']
    ) -> List[int]:

        latest_version_with_min_replicas = (
            self.get_latest_version_with_min_replicas(replica_infos))

        # Select replicas earlier than latest_version_with_min_replicas to scale
        # down.
        all_replica_ids_to_scale_down: List[int] = []
        for info in replica_infos:
            if (latest_version_with_min_replicas is not None and
                    info.version < latest_version_with_min_replicas):
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
        latest_provisioning_and_launched_replicas: List[
            'replica_managers.ReplicaInfo'] = []

        for info in replica_infos:
            if info.version == self.latest_version:
                if info.is_provisioning_or_launched:
                    latest_provisioning_and_launched_replicas.append(info)

        self._set_target_num_replica_with_hysteresis()

        scaling_options: List[AutoscalerDecision] = []
        all_replica_ids_to_scale_down: List[int] = []

        # Case 1. Once there is min_replicas number of
        # ready new replicas, we will direct all traffic to them,
        # we can scale down all old replicas.
        all_replica_ids_to_scale_down.extend(
            self.select_outdated_replicas_to_scale_down(replica_infos))

        # Case 2. when latest_provisioning_and_launched_replicas is less
        # than num_to_provision, we always scale up new replicas.
        if len(latest_provisioning_and_launched_replicas
              ) < self.target_num_replicas:
            num_replicas_to_scale_up = (
                self.target_num_replicas -
                len(latest_provisioning_and_launched_replicas))

            for _ in range(num_replicas_to_scale_up):
                scaling_options.append(
                    AutoscalerDecision(AutoscalerDecisionOperator.SCALE_UP,
                                       target=None))

        # Case 3: when latest_provisioning_and_launched_replicas is more
        # than self.target_num_replicas, we scale down new replicas.
        if len(latest_provisioning_and_launched_replicas
              ) > self.target_num_replicas:
            num_replicas_to_scale_down = (
                len(latest_provisioning_and_launched_replicas) -
                self.target_num_replicas)
            all_replica_ids_to_scale_down.extend(
                RequestRateAutoscaler._select_replicas_to_scale_down(
                    num_limit=num_replicas_to_scale_down,
                    replica_infos=latest_provisioning_and_launched_replicas))

        for replica_id in all_replica_ids_to_scale_down:
            scaling_options.append(
                AutoscalerDecision(AutoscalerDecisionOperator.SCALE_DOWN,
                                   target=replica_id))

        if not scaling_options:
            logger.info('No scaling needed.')
        return scaling_options

    def dump_dynamic_states(self) -> Dict[str, Any]:
        return {
            'request_timestamps': self.request_timestamps,
        }

    def load_dynamic_states(self, dynamic_states: Dict[str, Any]) -> None:
        if 'request_timestamps' in dynamic_states:
            self.request_timestamps = dynamic_states.pop('request_timestamps')
        if dynamic_states:
            logger.info(f'Remaining dynamic states: {dynamic_states}')


class FallbackRequestRateAutoscaler(RequestRateAutoscaler):
    """FallbackRequestRateAutoscaler

    Autoscale based on request rate. It adds additional ability to
    RequestRateAutoscaler for having spot with on-demand fallback.

    When spec.base_ondemand_fallback_replicas is set, we make sure
    there are at least spec.base_ondemand_fallback_replicas on-demands
    to be always there to provide basic gurantee for the availability.

    When spec.dynamic_ondemand_fallback is set, on-demand instances
    will be scheduled to provision for any preempted spot instance, i.e.,
    on-demand instance are used as dynamic fallback of spot.
    """

    def __init__(self, service_name: str,
                 spec: 'service_spec.SkyServiceSpec') -> None:
        super().__init__(service_name, spec)
        self.base_ondemand_fallback_replicas: int = (
            spec.base_ondemand_fallback_replicas
            if spec.base_ondemand_fallback_replicas is not None else 0)
        # Assert: Either dynamic_ondemand_fallback is set
        # or base_ondemand_fallback_replicas is greater than 0.
        assert spec.use_ondemand_fallback
        self.dynamic_ondemand_fallback: bool = (
            spec.dynamic_ondemand_fallback
            if spec.dynamic_ondemand_fallback is not None else False)

    def update_version(self, version: int,
                       spec: 'service_spec.SkyServiceSpec') -> None:
        super().update_version(version, spec)
        self.base_ondemand_fallback_replicas = (
            spec.base_ondemand_fallback_replicas
            if spec.base_ondemand_fallback_replicas is not None else 0)
        # Assert: Either dynamic_ondemand_fallback is set
        # or base_ondemand_fallback_replicas is greater than 0.
        assert spec.use_ondemand_fallback
        self.dynamic_ondemand_fallback = (spec.dynamic_ondemand_fallback
                                          if spec.dynamic_ondemand_fallback
                                          is not None else False)

    # spot_recovery field is checked earlier in core
    def _get_spot_resources_override_dict(self) -> Dict[str, Any]:
        return {'use_spot': True}

    def _get_ondemand_resources_override_dict(self) -> Dict[str, Any]:
        return {'use_spot': False}

    def evaluate_scaling(
        self,
        replica_infos: List['replica_managers.ReplicaInfo'],
    ) -> List[AutoscalerDecision]:

        latest_provisioning_and_launched_replicas = list(
            filter(
                lambda info: info.is_provisioning_or_launched and info.version
                == self.latest_version, replica_infos))

        self._set_target_num_replica_with_hysteresis()
        num_provisioning_and_launched_spot, num_ready_spot = 0, 0
        num_provisioning_and_launched_ondemand, num_ready_ondemand = 0, 0

        for info in latest_provisioning_and_launched_replicas:
            if info.is_spot:
                if info.status == serve_state.ReplicaStatus.READY:
                    num_ready_spot += 1
                num_provisioning_and_launched_spot += 1
            else:
                if info.status == serve_state.ReplicaStatus.READY:
                    num_ready_ondemand += 1
                num_provisioning_and_launched_ondemand += 1

        logger.info(
            'Number of alive spot instances: '
            f'{num_provisioning_and_launched_spot}, '
            f'Number of ready spot instances: {num_ready_spot}, '
            'Number of alive on-demand instances: '
            f' {num_provisioning_and_launched_ondemand}, '
            f'Number of ready on-demand instances: {num_ready_ondemand}')

        scaling_options: List[AutoscalerDecision] = []
        all_replica_ids_to_scale_down: List[int] = []

        # Once there is min_replicas number of ready new replicas, we will
        # direct all traffic to them, we can scale down all old replicas.
        all_replica_ids_to_scale_down.extend(
            self.select_outdated_replicas_to_scale_down(replica_infos))

        # Decide how many spot instances to launch.
        num_spot_to_provision = (self.target_num_replicas -
                                 self.base_ondemand_fallback_replicas)
        if num_provisioning_and_launched_spot < num_spot_to_provision:
            # Not enough spot instances, scale up.
            num_spot_to_scale_up = (num_spot_to_provision -
                                    num_provisioning_and_launched_spot)
            for _ in range(num_spot_to_scale_up):
                scaling_options.append(
                    AutoscalerDecision(
                        AutoscalerDecisionOperator.SCALE_UP,
                        target=self._get_spot_resources_override_dict()))
        elif num_provisioning_and_launched_spot > num_spot_to_provision:
            # Too many spot instances, scale down.
            # Get the replica to scale down with _select_replicas_to_scale_down
            num_spot_to_scale_down = (num_provisioning_and_launched_spot -
                                      num_spot_to_provision)
            all_replica_ids_to_scale_down.extend(
                RequestRateAutoscaler._select_replicas_to_scale_down(
                    num_spot_to_scale_down,
                    filter(lambda info: info.is_spot,
                           latest_provisioning_and_launched_replicas)))

        # Decide how many on-demand instances to launch.
        num_ondemand_to_provision = self.base_ondemand_fallback_replicas
        if self.dynamic_ondemand_fallback:
            # `num_ready_spot` instead of `num_provisioning_and_launched_spot`
            # because the provisioning spot can fail to UP due to the capacity
            # issue, and on-demand should fill the gap between the required
            # number of spot and ready spot.
            num_ondemand_to_provision += (num_spot_to_provision -
                                          num_ready_spot)

        if num_ondemand_to_provision > num_provisioning_and_launched_ondemand:
            for _ in range(num_ondemand_to_provision -
                           num_provisioning_and_launched_ondemand):
                scaling_options.append(
                    AutoscalerDecision(
                        AutoscalerDecisionOperator.SCALE_UP,
                        target=self._get_ondemand_resources_override_dict()))
        else:
            all_replica_ids_to_scale_down.extend(
                RequestRateAutoscaler._select_replicas_to_scale_down(
                    num_provisioning_and_launched_ondemand -
                    num_ondemand_to_provision,
                    filter(lambda info: not info.is_spot,
                           latest_provisioning_and_launched_replicas)))

        for replica_id in all_replica_ids_to_scale_down:
            scaling_options.append(
                AutoscalerDecision(AutoscalerDecisionOperator.SCALE_DOWN,
                                   target=replica_id))

        return scaling_options
