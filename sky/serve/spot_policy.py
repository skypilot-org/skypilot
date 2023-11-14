""" Sky Spot Policy for SkyServe."""
import enum
import logging
import random
from typing import List

logger = logging.getLogger(__name__)


class SpotPolicyName(enum.Enum):

    EVEN_SPREAD = 'EvenSpread'
    EAGER_FAILOVER = 'EagerFailover'


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
        logger.info(f'Chosen zone: {zone}, policy: {self.spot_placement}')
        self._clear_preempted_zones()
        return zone

    def is_fallback_on_demand(self) -> bool:
        if self.mix_policy == MixPolicyName.SPOT_ONLY:
            return False
        else:
            raise NotImplementedError(
                f'Not implemented mix_policy: {self.mix_policy}')

    def handle_preemption(self, zone):
        logger.info(f'handle_preemption: {zone}')
        self.preempted_zones.append(zone)

    def _clear_preempted_zones(self):
        self.preempted_zones = []
