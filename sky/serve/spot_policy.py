""" Sky Spot Policy for SkyServe."""
import enum
import logging
from math import ceil
import random
import typing
from typing import List

if typing.TYPE_CHECKING:
    from sky.serve import service_spec

logger = logging.getLogger(__name__)


class SpotPolicyName(enum.Enum):

    EVEN_SPREAD = 'EvenSpread'
    EAGER_FAILOVER = 'EagerFailover'
    DYNAMIC_FAILOVER = 'DynamicFailover'


class MixPolicyName(enum.Enum):

    SPOT_ONLY = 'SpotOnly'
    FALLBACK_ON_DEMAND = 'FallbackOnDemand'


class SpotPolicy:
    """Spot Policy specification."""

    def __init__(self, zones: List[str], spot_placement: str, mix_policy: str,
                 spec: 'service_spec.SkyServiceSpec') -> None:

        if spot_placement == 'EvenSpread':
            self.spot_placement = SpotPolicyName.EVEN_SPREAD
        elif spot_placement == 'EagerFailover':
            self.spot_placement = SpotPolicyName.EAGER_FAILOVER
        elif spot_placement == 'DynamicFailover':
            self.spot_placement = SpotPolicyName.DYNAMIC_FAILOVER
        else:
            raise NotImplementedError(
                f'Unknown spot_placement: {spot_placement}')

        if mix_policy == 'SpotOnly':
            self.mix_policy = MixPolicyName.SPOT_ONLY
        elif mix_policy == 'FallbackOnDemand':
            self.mix_policy = MixPolicyName.FALLBACK_ON_DEMAND
        else:
            raise NotImplementedError(f'Unknown mix_policy: {mix_policy}')

        self.zones = list(zones)
        self.current_zone_idx: int = 0

        # self.active_zones only used for DynamicFailover
        self.active_zones: List[str] = list(zones)
        self.preempted_zones: List[str] = []

        # For FallbackOnDemand
        self.fallback_ratio: int = 3  # Default. Todo
        if spec.min_replicas != spec.max_replicas:
            logger.info('self.min_replicas is not equal to spec.max_replicas')

        assert spec.max_replicas is not None
        self.max_replicas: int = spec.max_replicas
        self.num_active_replicas: int = 0
        self.num_on_demand_fallback_replicas: int = 0

    def get_next_zone(self) -> str:
        assert self.zones is not None
        if self.spot_placement == SpotPolicyName.EVEN_SPREAD:
            zone = self.zones[self.current_zone_idx % len(self.zones)]
            self.current_zone_idx += 1
        elif self.spot_placement == SpotPolicyName.EAGER_FAILOVER:
            zone = random.choice(self.zones)
            while zone in self.preempted_zones:
                zone = random.choice(self.zones)
            self._clear_preempted_zones()
        elif self.spot_placement == SpotPolicyName.DYNAMIC_FAILOVER:
            if len(self.active_zones) > 0:
                zone = random.choice(self.active_zones)
            else:
                zone = random.choice(self.preempted_zones)
                self._move_zone_to_active(zone)
            assert len(self.active_zones) + len(self.preempted_zones) == len(
                self.zones), (self.active_zones, self.preempted_zones,
                              self.zones)
        logger.info(f'Chosen zone: {zone}, policy: {self.spot_placement}')

        return zone

    def is_fallback_on_demand(self) -> bool:
        if self.mix_policy == MixPolicyName.SPOT_ONLY:
            return False
        elif self.mix_policy == MixPolicyName.FALLBACK_ON_DEMAND:
            # TODO(MaoZiming): Add self.fallback_ratio to yaml.
            preempted_num = self.max_replicas - self.num_active_replicas
            logger.info(f'preempted_num: {preempted_num}')
            if ceil(preempted_num /
                    self.fallback_ratio) > self.num_on_demand_fallback_replicas:
                return True
            return False
        else:
            raise NotImplementedError(
                f'Not implemented mix_policy: {self.mix_policy}')

    def is_scale_down_on_demand_backup(self) -> bool:
        if not self.mix_policy == MixPolicyName.FALLBACK_ON_DEMAND:
            raise NotImplementedError(f'Mix_policy: {self.mix_policy}'
                                      'does not support on-deamdn fallback')

        preempted_num = self.max_replicas - self.num_active_replicas
        logger.info(f'is_scale_down_on_demand_backup: {preempted_num}')
        return ceil(preempted_num /
                    self.fallback_ratio) < self.num_on_demand_fallback_replicas

    def handle_preemption(self, zone):
        logger.info(f'handle_preemption: {zone}')
        self._move_zone_to_preempted(zone)

    def handle_backup(self):
        logger.info('handle_backup')
        self.num_on_demand_fallback_replicas += 1

    def handle_backup_terminate(self):
        logger.info('handle_backup_terminate')
        self.num_on_demand_fallback_replicas -= 1

    def update_num_alive_spot_replicas(self, num_alive_replicas: int):
        logger.info(f'update_num_alive_spot_replicas: {num_alive_replicas}')
        self.num_active_replicas = num_alive_replicas

    def _clear_preempted_zones(self):
        self.preempted_zones = []
        self.active_zones = self.zones

    def _move_zone_to_active(self, zone):
        if zone in self.preempted_zones:
            self.preempted_zones.remove(zone)
        if zone not in self.active_zones:
            self.active_zones.append(zone)
        assert len(self.active_zones) + len(self.preempted_zones) == len(
            self.zones), (self.active_zones, self.preempted_zones, self.zones)

    def _move_zone_to_preempted(self, zone):
        if zone in self.active_zones:
            self.active_zones.remove(zone)
        if zone not in self.preempted_zones:
            self.preempted_zones.append(zone)
        assert len(self.active_zones) + len(self.preempted_zones) == len(
            self.zones), (self.active_zones, self.preempted_zones, self.zones)
