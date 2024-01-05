""" Sky Spot Policy for SkyServe."""
import enum
import random
import typing
from typing import Dict, List, Optional, Type

from sky import sky_logging

if typing.TYPE_CHECKING:
    from sky.serve import replica_managers
    from sky.serve import service_spec

logger = sky_logging.init_logger(__name__)


class SpotZoneType(enum.Enum):
    """Spot Zone Type."""
    ACTIVE = 'ACTIVE'
    PREEMPTED = 'PREEMPTED'


class SpotPlacer:
    """Spot Placement specification."""
    NAME: Optional[str] = None
    REGISTRY: Dict[str, Type['SpotPlacer']] = dict()

    def __init__(self, spec: 'service_spec.SkyServiceSpec') -> None:
        assert spec.spot_zones is not None
        self.zones = list(spec.spot_zones)
        self.zone2type: Dict[str, SpotZoneType] = {
            zone: SpotZoneType.ACTIVE for zone in self.zones
        }

    def __init_subclass__(cls) -> None:
        if cls.NAME is None:
            # This is an abstract class, don't put it in the registry.
            return
        assert cls.NAME not in cls.REGISTRY, f'Name {cls.NAME} already exists'
        cls.REGISTRY[cls.NAME] = cls

    @classmethod
    def get_policy_names(cls) -> List[str]:
        return list(cls.REGISTRY.keys())

    def select(self, existing_replicas: List['replica_managers.ReplicaInfo'],
               num_zones: int) -> List[str]:
        """Select next zone to place spot instance."""
        raise NotImplementedError

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

    def __repr__(self) -> str:
        return f'{self.NAME}SpotPlacer()'

    @classmethod
    def from_spec(cls, spec: 'service_spec.SkyServiceSpec') -> 'SpotPlacer':
        assert (spec.spot_placer is not None and
                spec.spot_placer in cls.REGISTRY)
        return cls.REGISTRY[spec.spot_placer](spec)


class DynamicFailoverSpotPlacer(SpotPlacer):
    """Dynamic failover to an active zone when preempted."""
    NAME: Optional[str] = 'DynamicFailover'

    def select(self, existing_replicas: List['replica_managers.ReplicaInfo'],
               num_replicas: int) -> List[str]:
        # Prevent the case with only one active zones.
        selected_zones = []
        if (len(self.active_zones()) <= 1 and len(self.preempted_zones()) > 0):
            self.clear_preempted_zones()

        existing_zones = {
            info.zone
            for info in existing_replicas
            if not info.is_spot and info.zone is not None
        }
        unvisited_active_zones = [
            zone for zone in self.active_zones() if zone not in existing_zones
        ]

        logger.info(f'existing_zones: {existing_zones}')
        logger.info(f'unvisited_active_zones: {unvisited_active_zones}')

        if unvisited_active_zones:
            selected_zones.extend(
                random.sample(unvisited_active_zones,
                              min(num_replicas, len(unvisited_active_zones))))
            num_replicas -= len(selected_zones)
        if num_replicas > 0:
            selected_zones.extend(
                random.choices(self.active_zones(), k=num_replicas))
        return selected_zones
