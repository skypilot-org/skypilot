""" Sky Spot Policy for SkyServe."""
import enum
import random
import typing
from typing import Dict, List, Optional, Type

from sky import sky_logging

if typing.TYPE_CHECKING:
    from sky.serve import service_spec

logger = sky_logging.init_logger(__name__)


class SpotPlacer:
    """Spot Placement specification."""
    NAME: Optional[str] = None
    REGISTRY: Dict[str, Type['SpotPlacer']] = dict()

    def __init__(self, spec: 'service_spec.SkyServiceSpec') -> None:
        assert spec.spot_zones is not None
        self.zones = list(spec.spot_zones)

    def __init_subclass__(cls) -> None:
        if cls.NAME is None:
            # This is an abstract class, don't put it in the registry.
            return
        assert cls.NAME not in cls.REGISTRY, f'Name {cls.NAME} already exists'
        cls.REGISTRY[cls.NAME] = cls

    def select(self) -> str:
        """Select next zone to place spot instance."""
        raise NotImplementedError

    def handle_active(self, zone: str) -> None:
        """Handle active of spot instance in given zone."""
        del zone  # Unused.

    def handle_preemption(self, zone: str) -> None:
        """Handle preemption of spot instance in given zone."""
        del zone  # Unused.

    def __repr__(self) -> str:
        return f'{self.NAME}SpotPlacer()'

    @classmethod
    def from_spec(cls, spec: 'service_spec.SkyServiceSpec') -> 'SpotPlacer':
        assert spec.spot_placer is not None
        return cls.REGISTRY[spec.spot_placer](spec)


class EvenSpreadSpotPlacer(SpotPlacer):
    """Evenly spread spot instances across zones."""
    NAME: Optional[str] = 'EvenSpread'

    def __init__(self, spec: 'service_spec.SkyServiceSpec') -> None:
        super().__init__(spec)
        self.current_zone_idx: int = 0

    def select(self) -> str:
        zone = self.zones[self.current_zone_idx % len(self.zones)]
        self.current_zone_idx += 1
        return zone


class SpotZoneType(enum.Enum):
    """Spot Zone Type."""
    ACTIVE = 'ACTIVE'
    PREEMPTED = 'PREEMPTED'


class HistoricalSpotPlacer(SpotPlacer):
    """SpotPlacer with historical information."""
    NAME: Optional[str] = None

    def __init__(self, spec: 'service_spec.SkyServiceSpec') -> None:
        super().__init__(spec)
        self.zone2type: Dict[str, SpotZoneType] = {
            zone: SpotZoneType.ACTIVE for zone in self.zones
        }

    def move_zone_to_active(self, zone: str) -> None:
        assert zone in self.zone2type
        self.zone2type[zone] = SpotZoneType.ACTIVE

    def move_zone_to_preempted(self, zone: str) -> None:
        assert zone in self.zone2type
        self.zone2type[zone] = SpotZoneType.PREEMPTED

    def handle_active(self, zone: str) -> None:
        self.move_zone_to_active(zone)

    def handle_preemption(self, zone: str) -> None:
        self.move_zone_to_preempted(zone)

    def clear_preempted_zones(self) -> None:
        for zone in self.zones:
            self.move_zone_to_active(zone)

    def active_zones(self) -> List[str]:
        return [
            zone for zone, zone_type in self.zone2type.items()
            if zone_type == SpotZoneType.ACTIVE
        ]

    def preempted_zones(self) -> List[str]:
        return [
            zone for zone, zone_type in self.zone2type.items()
            if zone_type == SpotZoneType.PREEMPTED
        ]


class EagerFailoverSpotPlacer(HistoricalSpotPlacer):
    """Eagerly failover to a different zone when preempted."""
    NAME: Optional[str] = 'EagerFailover'

    def select(self) -> str:
        zone = random.choice(self.zones)
        while zone in self.preempted_zones():
            zone = random.choice(self.zones)
        self.clear_preempted_zones()
        return zone


class DynamicFailoverSpotPlacer(HistoricalSpotPlacer):
    """Dynamic failover to an active zone when preempted."""
    NAME: Optional[str] = 'DynamicFailover'

    def select(self) -> str:
        if len(self.active_zones()) == 0:
            self.clear_preempted_zones()
        return random.choice(self.active_zones())
