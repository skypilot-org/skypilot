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
        # Target number of replicas is initialized to min replicas.
        # TODO(MaoZiming): add init replica numbers in SkyServe spec.
        self.target_num_replicas: int = spec.min_replicas

    def update_spec(self, version: int, spec: 'service_spec.SkyServiceSpec',
                    mixed_replica_versions: bool) -> None:
        del version  # Unused.
        del mixed_replica_versions  # Unused.
        self.min_nodes = spec.min_replicas
        self.max_nodes = spec.max_replicas or spec.min_replicas
        self.target_num_replicas = spec.min_replicas

    def collect_request_information(
            self, request_aggregator_info: Dict[str, Any]) -> None:
        """Collect request information from aggregator for autoscaling."""
        raise NotImplementedError

    def evaluate_scaling(
        self, replica_infos: List['replica_managers.ReplicaInfo']
    ) -> List[AutoscalerDecision]:
        """Evaluate autoscale options based on replica information."""
        raise NotImplementedError


class RequestRateAutoscaler(Autoscaler):
    """RequestRateAutoscaler: Autoscale according to request rate.

    Scales when the number of requests in the given interval is above or below
    the threshold.
    """

    def __init__(self, spec: 'service_spec.SkyServiceSpec',
                 qps_window_size: int, initial_version: int) -> None:
        """Initialize the request rate autoscaler.

        Variables:
            target_qps_per_replica: Target qps per replica for autoscaling.
            qps_window_size: Window size for qps calculating.
            request_timestamps: All request timestamps within the window.
            upscale_counter: counter for upscale number of replicas.
            downscale_counter: counter for downscale number of replicas.
            scale_up_consecutive_periods: period for scaling up.
            scale_down_consecutive_periods: period for scaling down.
            bootstrap_done: whether bootstrap is done.
            latest_version: latest version of the service.
            mixed_replica_versions: whether mix replica versions.
        """
        super().__init__(spec)
        self.target_qps_per_replica: Optional[
            float] = spec.target_qps_per_replica
        self.qps_window_size: int = qps_window_size
        self.request_timestamps: List[float] = []
        self.upscale_counter: int = 0
        self.downscale_counter: int = 0
        self.scale_up_consecutive_periods: int = int(
            spec.upscale_delay_seconds /
            constants.AUTOSCALER_DEFAULT_DECISION_INTERVAL_SECONDS)
        self.scale_down_consecutive_periods: int = int(
            spec.downscale_delay_seconds /
            constants.AUTOSCALER_DEFAULT_DECISION_INTERVAL_SECONDS)

        self.bootstrap_done: bool = False
        self.latest_version: int = initial_version
        self.mixed_replica_versions: bool = True

    def update_spec(self, version: int, spec: 'service_spec.SkyServiceSpec',
                    mixed_replica_versions: bool) -> None:
        super().update_spec(version, spec, mixed_replica_versions)
        self.target_qps_per_replica = spec.target_qps_per_replica
        self.scale_up_consecutive_periods = int(
            spec.upscale_delay_seconds /
            constants.AUTOSCALER_DEFAULT_DECISION_INTERVAL_SECONDS)
        self.scale_down_consecutive_periods = int(
            spec.downscale_delay_seconds /
            constants.AUTOSCALER_DEFAULT_DECISION_INTERVAL_SECONDS)
        self.target_num_replicas = spec.min_replicas
        self.latest_version = version
        self.mixed_replica_versions = mixed_replica_versions

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

    def _get_desired_num_replicas(self) -> int:
        # Always return self.target_num_replicas when autoscaling
        # is not enabled, i.e. self.target_qps_per_replica is None.
        # In this case, self.target_num_replicas will be min_replicas.
        if self.target_qps_per_replica is None:
            return self.target_num_replicas

        # Convert to requests per second.
        num_requests_per_second = len(
            self.request_timestamps) / self.qps_window_size
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
        return self.target_num_replicas

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
        launched_new_replica_infos, ready_new_replica_infos = [], []
        ready_old_replica_infos, not_ready_old_replica_infos = [], []
        for info in replica_infos:
            if info.is_launched and info.version == self.latest_version:
                launched_new_replica_infos.append(info)
            if info.is_ready and info.version == self.latest_version:
                ready_new_replica_infos.append(info)
            if info.is_ready and info.version != self.latest_version:
                ready_old_replica_infos.append(info)
            if not info.is_ready and info.version != self.latest_version:
                not_ready_old_replica_infos.append(info)

        self.target_num_replicas = self._get_desired_num_replicas()
        logger.info(
            f'Final target number of replicas: {self.target_num_replicas} '
            f'Upscale counter: {self.upscale_counter}/'
            f'{self.scale_up_consecutive_periods}, '
            f'Downscale counter: {self.downscale_counter}/'
            f'{self.scale_down_consecutive_periods} ')

        scaling_options = []
        all_replica_ids_to_scale_down: List[int] = []

        def _get_replica_ids_to_scale_down(num_limit: int) -> List[int]:

            status_order = serve_state.ReplicaStatus.scale_down_decision_order()
            launched_replica_infos_sorted = sorted(
                launched_new_replica_infos,
                key=lambda info: status_order.index(info.status)
                if info.status in status_order else len(status_order))

            return [info.replica_id for info in launched_replica_infos_sorted
                   ][:num_limit]

        #   Case 1. We have ready new replicas, and
        #   mixed_replica_versions is False.
        #   In such case, since once there is a ready match replica,
        #   we will direct all traffic to it, we can scale down all
        #   old replicas.
        if (not self.mixed_replica_versions and
                len(ready_new_replica_infos) > 0):
            for info in ready_old_replica_infos:
                all_replica_ids_to_scale_down.append(info.replica_id)

        # Case 2. We have mixed_replica_versions is True
        #   In such case, we want to keep the old ready replicas,
        #   until total number of ready replicas is greater than
        #   target_num_replicas. Then we scale down the old replicas.
        if (self.mixed_replica_versions and
                len(ready_new_replica_infos) + len(ready_old_replica_infos) >
                self.target_num_replicas):
            num_old_replicas_to_scale_down = len(ready_new_replica_infos) + len(
                ready_old_replica_infos) - self.target_num_replicas
            for info in ready_old_replica_infos[:
                                                num_old_replicas_to_scale_down]:
                all_replica_ids_to_scale_down.append(info.replica_id)

        # Case 3. We have some not ready old replicas.
        #   In such case, we immediately scale down the replica with
        #   mismatched version to reduce cost.
        if len(not_ready_old_replica_infos) > 0:
            for info in not_ready_old_replica_infos:
                all_replica_ids_to_scale_down.append(info.replica_id)

        # Case 4. when launched_new_replica_infos is less
        # than target_num_replicas, we always scale up new replicas.
        if len(launched_new_replica_infos) < self.target_num_replicas:
            num_replicas_to_scale_up = (self.target_num_replicas -
                                        len(launched_new_replica_infos))

            for _ in range(num_replicas_to_scale_up):
                scaling_options.append(
                    AutoscalerDecision(AutoscalerDecisionOperator.SCALE_UP,
                                       target=None))
        # Case 5: when launched_new_replica_infos is more
        # than target_num_replicas, we always scale down new replicas.
        if len(launched_new_replica_infos) > self.target_num_replicas:
            num_replicas_to_scale_down = (len(launched_new_replica_infos) -
                                          self.target_num_replicas)
            all_replica_ids_to_scale_down.extend(
                _get_replica_ids_to_scale_down(
                    num_limit=num_replicas_to_scale_down))

        for replica_id in all_replica_ids_to_scale_down:
            scaling_options.append(
                AutoscalerDecision(AutoscalerDecisionOperator.SCALE_DOWN,
                                   target=replica_id))

        if not scaling_options:
            logger.info('No scaling needed.')
        return scaling_options
