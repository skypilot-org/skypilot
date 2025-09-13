"""Autoscalers: perform autoscaling by monitoring metrics."""
import bisect
import copy
import dataclasses
import enum
import math
import time
import typing
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

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
        if operator == AutoscalerDecisionOperator.SCALE_UP:
            assert (target is None or isinstance(target, dict))
        else:
            assert isinstance(target, int)
        self.operator = operator
        self.target = target

    def __repr__(self) -> str:
        return f'AutoscalerDecision({self.operator}, {self.target})'


def _generate_scale_up_decisions(
        num: int, target: Optional[Dict[str, Any]]) -> List[AutoscalerDecision]:
    return [
        AutoscalerDecision(AutoscalerDecisionOperator.SCALE_UP,
                           copy.copy(target)) for _ in range(num)
    ]


def _generate_scale_down_decisions(
        replica_ids: List[int]) -> List[AutoscalerDecision]:
    return [
        AutoscalerDecision(AutoscalerDecisionOperator.SCALE_DOWN, replica_id)
        for replica_id in replica_ids
    ]


def _select_nonterminal_replicas_to_scale_down(
    num_replica_to_scale_down: int,
    replica_infos: Iterable['replica_managers.ReplicaInfo'],
) -> List[int]:
    """Select nonterminal replicas to scale down.

    We sort the replicas based on the following order:
        1. Based on the `scale_down_decision_order` of the status. We terminate
            the replicas that is in earlier stage first, as the replicas in
            later stage may become ready soon.
        2. Based on the version in ascending order, so we scale down the older
            versions first.
        3. Based on the replica_id in descending order, which is also the order
            of the replicas being launched. We scale down the replicas that are
            launched earlier first, as the replicas that are launched later may
            become ready soon.

    Args:
        num_replica_to_scale_down: The number of replicas to scale down.
        replica_infos: The list of replica informations to select from.

    Returns:
        The list of replica ids to scale down.
    """
    replicas = list(replica_infos)
    status_order = serve_state.ReplicaStatus.scale_down_decision_order()
    assert all(info.status in status_order for info in replicas), (
        'All replicas to scale down should be in provisioning or launched '
        'status.', replicas)
    replicas = sorted(
        replicas,
        key=lambda info: (
            status_order.index(info.status),
            # version in ascending order
            info.version,
            # replica_id in descending order, i.e. launched order
            -info.replica_id))
    assert len(replicas) >= num_replica_to_scale_down, (
        'Not enough replicas to scale down. Available replicas: ',
        f'{replicas}, num_replica_to_scale_down: {num_replica_to_scale_down}.')
    return [info.replica_id for info in replicas][:num_replica_to_scale_down]


class Autoscaler:
    """Abstract class for autoscalers."""

    # --------------- APIs to implement for custom autoscaler ---------------

    def __init__(self, service_name: str,
                 spec: 'service_spec.SkyServiceSpec') -> None:
        """Initialize the autoscaler.

        Variables:
            min_replicas: Minimum number of replicas.
            max_replicas: Maximum number of replicas. Default to fixed
                number of replicas, i.e. min_replicas == max_replicas.
            target_num_replicas: Target number of replicas output by autoscaler.
            latest_version: latest version of the service.
            latest_version_ever_ready: The latest version that is ever ready.
            update_mode: Update mode for the service.
        """
        self._service_name: str = service_name
        self.min_replicas: int = spec.min_replicas
        self.max_replicas: int = (spec.max_replicas if spec.max_replicas
                                  is not None else spec.min_replicas)
        self.num_overprovision: Optional[int] = spec.num_overprovision
        # Target number of replicas is initialized to min replicas
        self.target_num_replicas: int = spec.min_replicas
        self.latest_version: int = constants.INITIAL_VERSION
        # The latest_version_ever_ready should be smaller than the
        # latest_version, so we can fail early if the initial version got
        # unrecoverable failure.
        self.latest_version_ever_ready: int = self.latest_version - 1
        self.update_mode = serve_utils.DEFAULT_UPDATE_MODE

    def get_final_target_num_replicas(self) -> int:
        """Get the final target number of replicas."""
        if self.num_overprovision is None:
            return self.target_num_replicas
        return self.target_num_replicas + self.num_overprovision

    def _calculate_target_num_replicas(self) -> int:
        """Calculate target number of replicas."""
        raise NotImplementedError

    def update_version(self, version: int, spec: 'service_spec.SkyServiceSpec',
                       update_mode: serve_utils.UpdateMode) -> None:
        if version <= self.latest_version:
            logger.error(f'Invalid version: {version}, '
                         f'latest version: {self.latest_version}')
            return
        self.latest_version = version
        self.min_replicas = spec.min_replicas
        self.max_replicas = (spec.max_replicas if spec.max_replicas is not None
                             else spec.min_replicas)
        # Re-clip self.target_num_replicas with new min and max replicas.
        self.target_num_replicas = self._clip_target_num_replicas(
            self.target_num_replicas)
        self.update_mode = update_mode

    def collect_request_information(
            self, request_aggregator_info: Dict[str, Any]) -> None:
        """Collect request information from aggregator for autoscaling."""
        raise NotImplementedError

    def info(self) -> Dict[str, Any]:
        """Get information about the autoscaler."""
        return {
            'target_num_replicas': self.target_num_replicas,
            'min_replicas': self.min_replicas,
            'max_replicas': self.max_replicas,
        }

    def _generate_scaling_decisions(
        self,
        replica_infos: List['replica_managers.ReplicaInfo'],
    ) -> List[AutoscalerDecision]:
        """Generate Autoscaling decisions based on replica information."""
        raise NotImplementedError

    def _dump_dynamic_states(self) -> Dict[str, Any]:
        """Dump dynamic states from autoscaler."""
        raise NotImplementedError

    def _load_dynamic_states(self, dynamic_states: Dict[str, Any]) -> None:
        """Load dynamic states to autoscaler."""
        raise NotImplementedError

    # --------------- Utility Functions ---------------

    def _clip_target_num_replicas(self, target_num_replicas: int) -> int:
        """Clip target number of replicas with current minimal and maximum
        number of replicas.
        """
        return max(self.min_replicas, min(self.max_replicas,
                                          target_num_replicas))

    @classmethod
    def from_spec(cls, service_name: str,
                  spec: 'service_spec.SkyServiceSpec') -> 'Autoscaler':
        # TODO(MaoZiming): use NAME to get the class.
        if spec.use_ondemand_fallback:
            return FallbackRequestRateAutoscaler(service_name, spec)
        elif isinstance(spec.target_qps_per_replica, dict):
            # Use instance-aware autoscaler
            # when target_qps_per_replica is a dict
            return InstanceAwareRequestRateAutoscaler(service_name, spec)
        else:
            return RequestRateAutoscaler(service_name, spec)

    def get_decision_interval(self) -> int:
        """Get the decision interval for the autoscaler.

        We reduce the decision interval when the desired number of replicas is
        0, to make the service scale faster when the service is not running.
        This will happen when min_replicas = 0 and no traffic.
        """
        if self.get_final_target_num_replicas() == 0:
            return constants.AUTOSCALER_NO_REPLICA_DECISION_INTERVAL_SECONDS
        else:
            return constants.AUTOSCALER_DEFAULT_DECISION_INTERVAL_SECONDS

    def _select_outdated_replicas_to_scale_down(
        self,
        replica_infos: List['replica_managers.ReplicaInfo'],
        active_versions: List[int],
    ) -> List[int]:
        """Select outdated replicas to scale down."""

        if self.update_mode == serve_utils.UpdateMode.ROLLING:
            latest_ready_replicas: List['replica_managers.ReplicaInfo'] = []
            old_nonterminal_replicas: List['replica_managers.ReplicaInfo'] = []
            for info in replica_infos:
                if info.version == self.latest_version:
                    if info.is_ready:
                        latest_ready_replicas.append(info)
                elif not info.is_terminal:
                    old_nonterminal_replicas.append(info)

            num_latest_ready_replicas = len(latest_ready_replicas)

            # We compare to target_num_replicas instead of min_replicas, to
            # guarantee better service quality. Since mixing traffic across
            # old and latest versions are allowed in rolling update, this will
            # not affect the time it takes for the service to updated to the
            # latest version.
            if (num_latest_ready_replicas >=
                    self.get_final_target_num_replicas()):
                # Once the number of ready new replicas is greater than or equal
                # to the target, we can scale down all old replicas.
                return [info.replica_id for info in old_nonterminal_replicas]
            # If rolling update is in progress, we scale down old replicas
            # based on the number of ready new replicas.
            num_old_replicas_to_keep = (self.get_final_target_num_replicas() -
                                        num_latest_ready_replicas)
            # Remove old replicas (especially old launching replicas) and only
            # keep the required number of replicas, as we want to let the new
            # replicas to take over the provisioning old replicas faster.
            # `_select_replicas_to_scale_down` will make sure we scale the
            # replicas in initializing statuses first before scaling down the
            # READY old replicas.
            return _select_nonterminal_replicas_to_scale_down(
                max(0,
                    len(old_nonterminal_replicas) - num_old_replicas_to_keep),
                old_nonterminal_replicas,
            )

        if not active_versions:
            # active_versions can be empty when none of the replicas are ready
            # when the load balancer sync with the controller.
            return []
        # The active_versions should supposedly only having one version, but
        # we use min() here to make sure this works when rolling update and
        # blue-green update are mixed. min is used as we will scale down all old
        # replicas with version smaller than `latest_version_with_min_replicas`.
        latest_version_with_min_replicas = min(active_versions)
        # When it is blue green update, we scale down old replicas when the
        # number of ready new replicas is greater than or equal to the min
        # replicas instead of the target, to ensure the service being updated
        # to the latest version faster.
        return [
            info.replica_id
            for info in replica_infos
            if info.version < latest_version_with_min_replicas
        ]

    def generate_scaling_decisions(
        self,
        replica_infos: List['replica_managers.ReplicaInfo'],
        active_versions: List[int],
    ) -> List[AutoscalerDecision]:
        """Generate Autoscaling decisions based on replica information.
        If the number of launched replicas is less than the target, trigger a
        scale up. Else, trigger a scale down. This function also handles the
        version control of the replicas.

        For future compatibility, we return a list of AutoscalerDecision.
        Scale-up could include both spot and on-demand, each with a resource
        override dict. Active migration could require returning both SCALE_UP
        and SCALE_DOWN.
        """

        # Handle latest version unrecoverable failure first.
        latest_replicas: List['replica_managers.ReplicaInfo'] = []
        for info in replica_infos:
            if info.version == self.latest_version:
                latest_replicas.append(info)
                if info.is_ready:
                    self.latest_version_ever_ready = self.latest_version
        if self.latest_version_ever_ready < self.latest_version:
            for info in latest_replicas:
                if info.status_property.unrecoverable_failure():
                    # Stop scaling if one of replica of the latest version
                    # failed, it is likely that a fatal error happens to the
                    # user application and may lead to a infinte termination
                    # and restart.
                    return []

        scaling_decisions = []

        # If rolling update is in progress, we scale down old replicas based on
        # the number of ready new replicas and the traffic is directed to both
        # old and new replicas. Or, for blue_green update, once there is
        # min_replicas number of ready new replicas, we will direct all traffic
        # to them, we can scale down all old replicas.
        # TODO(MaoZiming,zhwu): corner case: We should make sure the fallback
        # replicas are ready before scaling down the old replicas to avoid the
        # situation that all the ready new replicas are preempted together.
        scaling_decisions.extend(
            _generate_scale_down_decisions(
                self._select_outdated_replicas_to_scale_down(
                    replica_infos, active_versions)))

        # If the latest version is ever ready, we can proceed to generate
        # decisions from the implementations in subclasses.
        scaling_decisions.extend(
            self._generate_scaling_decisions(replica_infos))

        if not scaling_decisions:
            logger.info('No scaling needed.')

        return scaling_decisions

    def dump_dynamic_states(self) -> Dict[str, Any]:
        """Dump dynamic states from autoscaler."""
        states = {'latest_version_ever_ready': self.latest_version_ever_ready}
        states.update(self._dump_dynamic_states())
        return states

    def load_dynamic_states(self, dynamic_states: Dict[str, Any]) -> None:
        """Load dynamic states to autoscaler."""
        self.latest_version_ever_ready = dynamic_states.pop(
            'latest_version_ever_ready', constants.INITIAL_VERSION)
        self._load_dynamic_states(dynamic_states)


class _AutoscalerWithHysteresis(Autoscaler):
    """_AutoscalerWithHysteresis: Autoscale with hysteresis.

    This is an internal class for developing autoscalers with hysteresis. It
    only scales when the number of replicas is above or below the target number
    of replicas for a certain number of consecutive periods.
    """

    def _setup_thresholds(self, spec: 'service_spec.SkyServiceSpec') -> None:
        upscale_delay_seconds = (
            spec.upscale_delay_seconds if spec.upscale_delay_seconds is not None
            else constants.AUTOSCALER_DEFAULT_UPSCALE_DELAY_SECONDS)
        self.scale_up_threshold: int = int(
            upscale_delay_seconds /
            constants.AUTOSCALER_DEFAULT_DECISION_INTERVAL_SECONDS)
        downscale_delay_seconds = (
            spec.downscale_delay_seconds
            if spec.downscale_delay_seconds is not None else
            constants.AUTOSCALER_DEFAULT_DOWNSCALE_DELAY_SECONDS)
        self.scale_down_threshold: int = int(
            downscale_delay_seconds /
            constants.AUTOSCALER_DEFAULT_DECISION_INTERVAL_SECONDS)

    def __init__(self, service_name: str,
                 spec: 'service_spec.SkyServiceSpec') -> None:
        """Initialize the hysteresis autoscaler.

        Variables:
            upscale_counter: Counter for upscale decisions of replicas.
            downscale_counter: Counter for downscale decisions of replicas.
            scale_up_threshold: The threshold to trigger a scale up.
            scale_down_threshold: The threshold to trigger a scale down.
        """
        super().__init__(service_name, spec)
        self.upscale_counter: int = 0
        self.downscale_counter: int = 0
        self._setup_thresholds(spec)

    def update_version(self, version: int, spec: 'service_spec.SkyServiceSpec',
                       update_mode: serve_utils.UpdateMode) -> None:
        super().update_version(version, spec, update_mode)
        # We directly set the target_num_replicas here instead of calling
        # `_set_target_num_replicas_with_hysteresis` to have the replicas
        # quickly scale after each update.
        self.target_num_replicas = self._calculate_target_num_replicas()
        # Cleanup hysteresis counters.
        self.upscale_counter = 0
        self.downscale_counter = 0
        self._setup_thresholds(spec)

    def _set_target_num_replicas_with_hysteresis(self) -> None:
        """Set target_num_replicas based on request rate with hysteresis."""
        target_num_replicas = self._calculate_target_num_replicas()
        old_target_num_replicas = self.target_num_replicas

        # Faster scale up when there is no replica.
        if self.target_num_replicas == 0:
            self.target_num_replicas = target_num_replicas
        elif target_num_replicas > self.target_num_replicas:
            self.upscale_counter += 1
            self.downscale_counter = 0
            if self.upscale_counter >= self.scale_up_threshold:
                self.upscale_counter = 0
                self.target_num_replicas = target_num_replicas
        elif target_num_replicas < self.target_num_replicas:
            self.downscale_counter += 1
            self.upscale_counter = 0
            if self.downscale_counter >= self.scale_down_threshold:
                self.downscale_counter = 0
                self.target_num_replicas = target_num_replicas
        else:
            self.upscale_counter = self.downscale_counter = 0

        logger.info(
            f'Old target number of replicas: {old_target_num_replicas}. '
            f'Current target number of replicas: {target_num_replicas}. '
            f'Final target number of replicas: {self.target_num_replicas}. '
            f'Num overprovision: {self.num_overprovision}. '
            f'Upscale counter: {self.upscale_counter}/'
            f'{self.scale_up_threshold}. '
            f'Downscale counter: {self.downscale_counter}/'
            f'{self.scale_down_threshold}. ')


class RequestRateAutoscaler(_AutoscalerWithHysteresis):
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
        """
        super().__init__(service_name, spec)
        self.target_qps_per_replica: Optional[Union[float, Dict[
            str, float]]] = spec.target_qps_per_replica
        self.qps_window_size: int = constants.AUTOSCALER_QPS_WINDOW_SIZE_SECONDS
        self.request_timestamps: List[float] = []

    def _calculate_target_num_replicas(self) -> int:
        if self.target_qps_per_replica is None:
            return self.min_replicas

        # RequestRateAutoscaler should only handle float values
        if isinstance(self.target_qps_per_replica, dict):
            raise ValueError('RequestRateAutoscaler does not support dict '
                             'target_qps_per_replica. Should use '
                             'InstanceAwareRequestRateAutoscaler instead.')

        num_requests_per_second = len(
            self.request_timestamps) / self.qps_window_size
        target_num_replicas = \
            math.ceil(num_requests_per_second / self.target_qps_per_replica)
        logger.info(f'Requests per second: {num_requests_per_second}. '
                    f'Target number of replicas: {target_num_replicas}.')

        return self._clip_target_num_replicas(target_num_replicas)

    def update_version(self, version: int, spec: 'service_spec.SkyServiceSpec',
                       update_mode: serve_utils.UpdateMode) -> None:
        super().update_version(version, spec, update_mode)
        self.target_qps_per_replica = spec.target_qps_per_replica

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
        logger.info(f'Num of requests in the last {self.qps_window_size} '
                    f'seconds: {len(self.request_timestamps)}')

    def _generate_scaling_decisions(
        self,
        replica_infos: List['replica_managers.ReplicaInfo'],
    ) -> List[AutoscalerDecision]:
        """Generate Autoscaling decisions based on request rate."""

        # Use standard hysteresis-based logic (non-instance-aware)
        self._set_target_num_replicas_with_hysteresis()

        latest_nonterminal_replicas: List['replica_managers.ReplicaInfo'] = []

        for info in replica_infos:
            if info.version == self.latest_version:
                if not info.is_terminal:
                    latest_nonterminal_replicas.append(info)

        scaling_decisions: List[AutoscalerDecision] = []

        # Case 1. when latest_nonterminal_replicas is less
        # than num_to_provision, we always scale up new replicas.
        target_num_replicas = self.get_final_target_num_replicas()
        if len(latest_nonterminal_replicas) < target_num_replicas:
            num_replicas_to_scale_up = (target_num_replicas -
                                        len(latest_nonterminal_replicas))
            logger.info('Number of replicas to scale up: '
                        f'{num_replicas_to_scale_up}')
            scaling_decisions.extend(
                _generate_scale_up_decisions(num_replicas_to_scale_up, None))

        # Case 2: when latest_nonterminal_replicas is more
        # than target_num_replicas, we scale down new replicas.
        replicas_to_scale_down = []
        if len(latest_nonterminal_replicas) > target_num_replicas:
            num_replicas_to_scale_down = (len(latest_nonterminal_replicas) -
                                          target_num_replicas)
            # Use standard downscaling logic
            replicas_to_scale_down = (
                _select_nonterminal_replicas_to_scale_down(
                    num_replicas_to_scale_down, latest_nonterminal_replicas))
            logger.info(
                'Number of replicas to scale down: '
                f'{num_replicas_to_scale_down} {replicas_to_scale_down}')

        scaling_decisions.extend(
            _generate_scale_down_decisions(replicas_to_scale_down))

        return scaling_decisions

    def _dump_dynamic_states(self) -> Dict[str, Any]:
        return {
            'request_timestamps': self.request_timestamps,
        }

    def _load_dynamic_states(self, dynamic_states: Dict[str, Any]) -> None:
        if 'request_timestamps' in dynamic_states:
            self.request_timestamps = dynamic_states.pop('request_timestamps')
        if dynamic_states:
            logger.info(f'Remaining dynamic states: {dynamic_states}')


class InstanceAwareRequestRateAutoscaler(RequestRateAutoscaler):
    """Instance-aware RequestRateAutoscaler:
    Autoscale based on each replica's GPU-specific QPS.

    This autoscaler considers different QPS targets for different GPU types
    when target_qps_per_replica is provided as a dictionary mapping GPU types
    to their respective QPS targets.
    """

    def __init__(self, service_name: str,
                 spec: 'service_spec.SkyServiceSpec') -> None:
        super().__init__(service_name, spec)
        # Ensure target_qps_per_replica is a dict for instance-aware logic
        assert isinstance(spec.target_qps_per_replica, dict), \
            'InstanceAware Autoscaler requires dict type target_qps_per_replica'
        # Re-assign with correct type using setattr to avoid typing issues
        self.target_qps_per_replica = spec.target_qps_per_replica

    def _generate_scaling_decisions(
        self,
        replica_infos: List['replica_managers.ReplicaInfo'],
    ) -> List[AutoscalerDecision]:
        """Generate autoscaling decisions with instance-aware logic."""
        # Always use instance-aware logic
        # since target_qps_per_replica is guaranteed to be dict
        self._set_target_num_replicas_with_instance_aware_logic(replica_infos)

        latest_nonterminal_replicas: List['replica_managers.ReplicaInfo'] = []

        for info in replica_infos:
            if not info.is_terminal and info.version == self.latest_version:
                latest_nonterminal_replicas.append(info)

        target_num_replicas = self.get_final_target_num_replicas()
        current_num_replicas = len(latest_nonterminal_replicas)

        scaling_decisions: List[AutoscalerDecision] = []

        # Decide if to scale up or down.
        if target_num_replicas > current_num_replicas:
            for _ in range(target_num_replicas - current_num_replicas):
                # No resources_override to use when scaling up
                scaling_decisions.append(
                    AutoscalerDecision(AutoscalerDecisionOperator.SCALE_UP,
                                       target=None))
        elif target_num_replicas < current_num_replicas:
            num_replicas_to_scale_down = \
                current_num_replicas - target_num_replicas

            # Use instance-aware scale down logic
            replicas_to_scale_down = self._select_replicas_to_scale_down_by_qps(
                num_replicas_to_scale_down, latest_nonterminal_replicas)
            for replica_id in replicas_to_scale_down:
                scaling_decisions.append(
                    AutoscalerDecision(AutoscalerDecisionOperator.SCALE_DOWN,
                                       target=replica_id))

        # Outdated replicas are handled by base class generate_scaling_decisions
        # No need to handle them here

        upscale_decisions = [
            d for d in scaling_decisions
            if d.operator == AutoscalerDecisionOperator.SCALE_UP
        ]
        downscale_decisions = [
            d for d in scaling_decisions
            if d.operator == AutoscalerDecisionOperator.SCALE_DOWN
        ]
        logger.info(f'Scaling decisions: '
                    f'{len(upscale_decisions)} scale up, '
                    f'{len(downscale_decisions)} scale down '
                    f'(latest nonterminal: {current_num_replicas}, '
                    f'target: {target_num_replicas})')

        return scaling_decisions

    def _set_target_num_replicas_with_instance_aware_logic(
            self, replica_infos: List['replica_managers.ReplicaInfo']) -> None:
        """Set target_num_replicas using instance-aware logic."""
        assert isinstance(self.target_qps_per_replica,
                          dict), 'Expected dict for instance-aware logic'
        target_qps_dict = self.target_qps_per_replica

        num_requests_per_second = len(
            self.request_timestamps) / self.qps_window_size

        total_qps = self._calculate_total_qps_from_replicas(replica_infos)
        if total_qps > 0:
            if num_requests_per_second >= total_qps:
                # for upscaling, max_target_qps is the standard qps
                max_target_qps = max(target_qps_dict.values())
                over_request_num = num_requests_per_second - total_qps
                current_num_replicas = len(replica_infos)
                raw_target_num = current_num_replicas + math.ceil(
                    over_request_num / max_target_qps)
                target_num_replicas = self._clip_target_num_replicas(
                    raw_target_num)
                logger.info(
                    f'Instance-aware autoscaling: total QPS {total_qps}, '
                    f'num_requests_per_second: {num_requests_per_second}, '
                    f'upscaling, using maximum QPS {max_target_qps} '
                    f'from {target_qps_dict}, '
                    f'target replicas: {target_num_replicas}')
            else:
                # for downscaling, use qps for every ready_target_qps_list
                # to calculate target_num_replicas
                ready_target_qps_list = \
                    self._extract_target_qps_list_from_ready_replicas(
                        replica_infos)
                ready_target_qps_list = sorted(ready_target_qps_list,
                                               reverse=True)
                if not ready_target_qps_list:
                    # Fallback to maximum QPS from config if no ready replicas
                    ready_target_qps_list = [max(target_qps_dict.values())]

                raw_target_num = 0
                qps_sum = 0.0
                for qps in ready_target_qps_list:
                    raw_target_num += 1
                    qps_sum += qps
                    if qps_sum > num_requests_per_second:
                        break

                target_num_replicas = self._clip_target_num_replicas(
                    raw_target_num)
                logger.info(
                    f'Instance-aware autoscaling: total QPS {total_qps}, '
                    f'num_requests_per_second: {num_requests_per_second}, '
                    f'downscaling, using ready QPS list '
                    f'{ready_target_qps_list}, '
                    f'target replicas: {target_num_replicas}')
        else:
            # no replica is ready; use the normal min_replicas
            target_num_replicas = self._clip_target_num_replicas(
                self.min_replicas)
            logger.info(f'Instance-aware autoscaling: no replica QPS available,'
                        f' target replicas: {target_num_replicas}')

        # Apply hysteresis logic
        old_target_num_replicas = self.target_num_replicas

        # Faster scale up when there is no replica.
        if self.target_num_replicas == 0:
            self.target_num_replicas = target_num_replicas
        elif target_num_replicas > self.target_num_replicas:
            self.upscale_counter += 1
            self.downscale_counter = 0
            if self.upscale_counter >= self.scale_up_threshold:
                self.upscale_counter = 0
                self.target_num_replicas = target_num_replicas
        elif target_num_replicas < self.target_num_replicas:
            self.downscale_counter += 1
            self.upscale_counter = 0
            if self.downscale_counter >= self.scale_down_threshold:
                self.downscale_counter = 0
                self.target_num_replicas = target_num_replicas
        else:
            self.upscale_counter = self.downscale_counter = 0

        logger.info(
            f'Instance-aware: Old target number of replicas: '
            f'{old_target_num_replicas}. '
            f'Current target number of replicas: {target_num_replicas}. '
            f'Final target number of replicas: {self.target_num_replicas}. '
            f'Num overprovision: {self.num_overprovision}. '
            f'Upscale counter: {self.upscale_counter}/'
            f'{self.scale_up_threshold}. '
            f'Downscale counter: {self.downscale_counter}/'
            f'{self.scale_down_threshold}. ')

    def _calculate_total_qps_from_replicas(
            self, replica_infos: List['replica_managers.ReplicaInfo']) -> float:
        """Calculate total QPS based on current replica GPU types."""
        total_qps = 0.0
        logger.info(f'Calculating total QPS from {len(replica_infos)} replicas')

        for replica_info in replica_infos:
            # Skip non-valid replicas
            valid_statuses = [
                serve_state.ReplicaStatus.READY,
                serve_state.ReplicaStatus.STARTING,
                serve_state.ReplicaStatus.PROVISIONING
            ]
            if replica_info.status not in valid_statuses:
                logger.info(f'Skipping replica {replica_info.replica_id} '
                            f'with status: {replica_info.status}')
                continue

            gpu_type = self._get_gpu_type_from_replica_info(replica_info)
            logger.info(f'Processing replica {replica_info.replica_id} '
                        f'with GPU type: {gpu_type}')

            # Use flexible matching logic
            qps_for_this_gpu = self._get_target_qps_for_gpu_type(gpu_type)
            total_qps += qps_for_this_gpu
            logger.info(f'GPU type {gpu_type} -> {qps_for_this_gpu} QPS')

        logger.info(f'Calculated total QPS: {total_qps}')
        return total_qps

    def _get_target_qps_for_gpu_type(self, gpu_type: str) -> float:
        """Get target QPS for a specific GPU type with flexible matching."""
        assert isinstance(self.target_qps_per_replica,
                          dict), 'Expected dict for instance-aware logic'
        target_qps_dict = self.target_qps_per_replica

        # Direct match first
        if gpu_type in target_qps_dict:
            return target_qps_dict[gpu_type]

        # Try matching by base name (e.g., 'A100' matches 'A100:1')
        for config_key in target_qps_dict.keys():
            # Remove count suffix (e.g., 'A100:1' -> 'A100')
            base_name = config_key.split(':')[0]
            if gpu_type == base_name:
                return target_qps_dict[config_key]

        # Fallback to minimum QPS
        logger.warning(f'No matching QPS found for GPU type: {gpu_type}. '
                       f'Available types: {list(target_qps_dict.keys())}. '
                       f'Using minimum QPS as fallback.')
        return min(target_qps_dict.values())

    def _get_gpu_type_from_replica_info(
            self, replica_info: 'replica_managers.ReplicaInfo') -> str:
        """Extract GPU type from ReplicaInfo object."""
        gpu_type = 'unknown'
        handle = replica_info.handle()
        if handle is not None:
            accelerators = handle.launched_resources.accelerators
            if accelerators and len(accelerators) > 0:
                # Get the first accelerator type
                gpu_type = list(accelerators.keys())[0]
        return gpu_type

    def _extract_target_qps_list_from_ready_replicas(
            self,
            replica_infos: List['replica_managers.ReplicaInfo']) -> List[float]:
        """Extract target QPS list from current READY replicas."""
        ready_replica_qps = []

        for replica_info in replica_infos:
            # Check if replica is READY
            if replica_info.status != serve_state.ReplicaStatus.READY:
                logger.info(
                    f'Replica {replica_info.replica_id} '
                    f'not ready (status: {replica_info.status}), skipping')
                continue

            gpu_type = self._get_gpu_type_from_replica_info(replica_info)

            # Use flexible matching logic
            qps_for_this_gpu = self._get_target_qps_for_gpu_type(gpu_type)
            ready_replica_qps.append(qps_for_this_gpu)
            logger.info(f'Ready replica {replica_info.replica_id} '
                        f'with GPU {gpu_type}: {qps_for_this_gpu} QPS')

        if ready_replica_qps:
            logger.info(
                f'Target QPS list from ready replicas: {ready_replica_qps}')
            return ready_replica_qps

        return []

    def _select_replicas_to_scale_down_by_qps(
            self, num_replicas_to_scale_down: int,
            replica_infos: List['replica_managers.ReplicaInfo']) -> List[int]:
        """Select replicas to scale down (lowest QPS first)."""
        # Create a list of (replica_info, target_qps) tuples
        replica_qps_pairs: List[Tuple['replica_managers.ReplicaInfo',
                                      float]] = []

        for info in replica_infos:
            # Include old-version replicas as well so they also get a target_qps
            # assigned. Skip terminal replicas only.
            if info.is_terminal:
                continue

            # Get GPU type directly from replica info
            gpu_type = self._get_gpu_type_from_replica_info(info)

            # Use flexible matching logic
            target_qps = self._get_target_qps_for_gpu_type(gpu_type)

            replica_qps_pairs.append((info, float(target_qps)))
            logger.info(f'Replica {info.replica_id} '
                        f'with GPU {gpu_type}: {target_qps} QPS')

        # Create a mapping from replica_id to target_qps for sorting
        replica_qps_map = {
            info.replica_id: target_qps
            for info, target_qps in replica_qps_pairs
        }

        # Sort replicas by: 1. status order, 2. target_qps (asc),
        # 3. version (asc), 4. replica_id (desc)
        sorted_replicas = sorted(
            replica_infos,
            key=lambda info: (
                info.status.scale_down_decision_order(),
                replica_qps_map.get(info.replica_id, float('inf')),
                info.version,
                -info.replica_id,
            ))

        selected_replica_ids = []
        for info in sorted_replicas:
            if info.is_terminal:
                continue
            selected_replica_ids.append(info.replica_id)
            if len(selected_replica_ids) >= num_replicas_to_scale_down:
                break

        logger.info(
            f'Selected {len(selected_replica_ids)} replicas to scale down: '
            f'{selected_replica_ids}')
        return selected_replica_ids

    def update_version(self, version: int, spec: 'service_spec.SkyServiceSpec',
                       update_mode: serve_utils.UpdateMode) -> None:
        super(RequestRateAutoscaler,
              self).update_version(version, spec, update_mode)
        # Ensure it's a dict and re-assign using setattr to avoid typing
        assert isinstance(spec.target_qps_per_replica, dict), \
            'InstanceAware Autoscaler requires dict type target_qps_per_replica'
        self.target_qps_per_replica = spec.target_qps_per_replica


class FallbackRequestRateAutoscaler(RequestRateAutoscaler):
    """FallbackRequestRateAutoscaler

    Autoscale based on request rate. It adds additional ability to
    RequestRateAutoscaler for having spot with on-demand fallback.

    When spec.base_ondemand_fallback_replicas is set, we make sure
    there are at least spec.base_ondemand_fallback_replicas on-demands
    to be always there to provide basic guarantee for the availability.

    When spec.dynamic_ondemand_fallback is set, on-demand instances
    will be scheduled to provision for any preempted spot instance, i.e.,
    on-demand instance are used as dynamic fallback of spot.
    """

    # job_recovery field is checked earlier in core
    SPOT_OVERRIDE = {'use_spot': True}
    ONDEMAND_OVERRIDE = {'use_spot': False}

    def _setup_fallback_options(self,
                                spec: 'service_spec.SkyServiceSpec') -> None:
        self.base_ondemand_fallback_replicas: int = (
            spec.base_ondemand_fallback_replicas
            if spec.base_ondemand_fallback_replicas is not None else 0)
        # Assert: Either dynamic_ondemand_fallback is set
        # or base_ondemand_fallback_replicas is greater than 0.
        assert spec.use_ondemand_fallback
        self.dynamic_ondemand_fallback: bool = (
            spec.dynamic_ondemand_fallback
            if spec.dynamic_ondemand_fallback is not None else False)

    def __init__(self, service_name: str,
                 spec: 'service_spec.SkyServiceSpec') -> None:
        """Initialize the fallback request rate autoscaler.

        Variables:
            base_ondemand_fallback_replicas: Minimum number of on-demand
                replicas to be always there.
            dynamic_ondemand_fallback: Whether to dynamically provision
                on-demand instances for preempted spot instances.
        """
        super().__init__(service_name, spec)
        self._setup_fallback_options(spec)

    def update_version(self, version: int, spec: 'service_spec.SkyServiceSpec',
                       update_mode: serve_utils.UpdateMode) -> None:
        super().update_version(version, spec, update_mode=update_mode)
        self._setup_fallback_options(spec)

    def _generate_scaling_decisions(
        self,
        replica_infos: List['replica_managers.ReplicaInfo'],
    ) -> List[AutoscalerDecision]:
        """Generate Autoscaling decisions based on request rate, with on-demand
        fallback.

        The autoscaler will make sure there are at least
        `base_ondemand_fallback_replicas` on-demand replicas to be always there,
        so the service can provide basic guarantee for the availability.
        """

        self._set_target_num_replicas_with_hysteresis()

        latest_nonterminal_replicas = list(
            filter(
                lambda info: not info.is_terminal and info.version == self.
                latest_version, replica_infos))
        num_nonterminal_spot, num_ready_spot = 0, 0
        num_nonterminal_ondemand, num_ready_ondemand = 0, 0

        for info in latest_nonterminal_replicas:
            if info.is_spot:
                if info.status == serve_state.ReplicaStatus.READY:
                    num_ready_spot += 1
                num_nonterminal_spot += 1
            else:
                if info.status == serve_state.ReplicaStatus.READY:
                    num_ready_ondemand += 1
                num_nonterminal_ondemand += 1

        logger.info(
            f'Number of alive spot instances: {num_nonterminal_spot}, '
            f'Number of ready spot instances: {num_ready_spot}, '
            f'Number of alive on-demand instances: {num_nonterminal_ondemand}, '
            f'Number of ready on-demand instances: {num_ready_ondemand}')

        scaling_decisions: List[AutoscalerDecision] = []
        all_replica_ids_to_scale_down: List[int] = []

        # Decide how many spot instances to launch.
        num_spot_to_provision = (self.get_final_target_num_replicas() -
                                 self.base_ondemand_fallback_replicas)
        if num_nonterminal_spot < num_spot_to_provision:
            # Not enough spot instances, scale up.
            num_spot_to_scale_up = (num_spot_to_provision -
                                    num_nonterminal_spot)
            logger.info('Number of spot instances to scale up: '
                        f'{num_spot_to_scale_up}')
            scaling_decisions.extend(
                _generate_scale_up_decisions(num_spot_to_scale_up,
                                             self.SPOT_OVERRIDE))
        elif num_nonterminal_spot > num_spot_to_provision:
            # Too many spot instances, scale down.
            # Get the replica to scale down with _select_replicas_to_scale_down
            num_spot_to_scale_down = (num_nonterminal_spot -
                                      num_spot_to_provision)
            replicas_to_scale_down = (
                _select_nonterminal_replicas_to_scale_down(
                    num_spot_to_scale_down,
                    filter(lambda info: info.is_spot,
                           latest_nonterminal_replicas)))
            logger.info('Number of spot instances to scale down: '
                        f'{num_spot_to_scale_down} {replicas_to_scale_down}')
            all_replica_ids_to_scale_down.extend(replicas_to_scale_down)

        # Decide how many on-demand instances to launch.
        num_ondemand_to_provision = self.base_ondemand_fallback_replicas
        if self.dynamic_ondemand_fallback:
            # `num_ready_spot` instead of `num_nonterminal_spot`
            # because the provisioning spot can fail to UP due to the capacity
            # issue, and on-demand should fill the gap between the required
            # number of spot and ready spot.
            # When scaling down spot instances, it is possible that the number
            # of ready spot is more than the number of spot to provision, thus
            # generate a negative number. In this case, we don't need to
            # provision on-demand instances.
            num_ondemand_to_provision += max(
                0, num_spot_to_provision - num_ready_spot)

        # Make sure we don't launch on-demand fallback for
        # overprovisioned replicas.
        num_ondemand_to_provision = min(num_ondemand_to_provision,
                                        self.target_num_replicas)
        if num_ondemand_to_provision > num_nonterminal_ondemand:
            num_ondemand_to_scale_up = (num_ondemand_to_provision -
                                        num_nonterminal_ondemand)
            logger.info('Number of on-demand instances to scale up: '
                        f'{num_ondemand_to_scale_up}')
            scaling_decisions.extend(
                _generate_scale_up_decisions(num_ondemand_to_scale_up,
                                             self.ONDEMAND_OVERRIDE))
        else:
            num_ondemand_to_scale_down = (num_nonterminal_ondemand -
                                          num_ondemand_to_provision)
            replicas_to_scale_down = (
                _select_nonterminal_replicas_to_scale_down(
                    num_ondemand_to_scale_down,
                    filter(lambda info: not info.is_spot,
                           latest_nonterminal_replicas)))
            logger.info(
                'Number of on-demand instances to scale down: '
                f'{num_ondemand_to_scale_down} {replicas_to_scale_down}')

            all_replica_ids_to_scale_down.extend(replicas_to_scale_down)

        scaling_decisions.extend(
            _generate_scale_down_decisions(all_replica_ids_to_scale_down))

        return scaling_decisions
