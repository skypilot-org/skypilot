""" Sky Spot Policy for SkyServe."""
import enum
import logging
import random
from typing import List

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

    def __init__(self, zones: List[str], spot_placement: str,
                 mix_policy: str) -> None:

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

        self.zones = zones
        self.current_zone_idx: int = 0

        # self.active_zones only used for DynamicFailover
        self.active_zones: List[str] = zones
        self.preempted_zones: List[str] = []

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
                self.zones)
        logger.info(f'Chosen zone: {zone}, policy: {self.spot_placement}')

        return zone

    def is_fallback_on_demand(self) -> bool:
        if self.mix_policy == MixPolicyName.SPOT_ONLY:
            return False
        else:
            raise NotImplementedError(
                f'Not implemented mix_policy: {self.mix_policy}')

    def handle_preemption(self, zone):
        logger.info(f'handle_preemption: {zone}')
        self._move_zone_to_preempted(zone)

    def _clear_preempted_zones(self):
        self.preempted_zones = []

    def _move_zone_to_active(self, zone):
        if zone in self.preempted_zones:
            self.preempted_zones.remove(zone)
        if zone not in self.active_zones:
            self.active_zones.append(zone)

    def _move_zone_to_preempted(self, zone):
        if zone in self.active_zones:
            self.active_zones.remove(zone)
        if zone not in self.preempted_zones:
            self.preempted_zones.append(zone)
