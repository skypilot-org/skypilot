""" Sky Spot Policy for SkyServe."""
import enum
import logging
import random
from typing import List

logger = logging.getLogger(__name__)


class SpotPolicy(enum.Enum):

    EVEN_SPREAD = 'EvenSpread'
    EAGER_FAILOVER = 'EagerFailover'

class SpotPlacer:
    """Spot Placer specification."""

    def __init__(self, zones: List[str], spot_policy: str) -> None:

        if spot_policy == 'EvenSpread':
            self.spot_policy = SpotPolicy.EVEN_SPREAD
        elif spot_policy == 'EagerFailover':
            self.spot_policy = SpotPolicy.EAGER_FAILOVER
        else:
            raise NotImplementedError(f'Unknown spot_policy: {spot_policy}')

        self.zones = zones
        self.current_zone_idx: int = 0
        self.preempted_zones: List[str] = []

    def get_next_zone(self) -> str:
        assert self.zones is not None
        if self.spot_policy == SpotPolicy.EVEN_SPREAD:
            zone = self.zones[self.current_zone_idx]
            self.current_zone_idx += 1
        elif self.spot_policy == SpotPolicy.EAGER_FAILOVER:
            zone = random.choice(self.zones)
            while zone in self.preempted_zones:
                zone = random.choice(self.zones)
        logger.info(f'Chosen zone: {zone}, policy: {self.spot_policy}')
        self._clear_preempted_zones()
        return zone

    def handle_preemption(self, zone):
        logger.info(f'handle_preemption: {zone}')
        self.preempted_zones.append(zone)

    def _clear_preempted_zones(self):
        self.preempted_zones = []
