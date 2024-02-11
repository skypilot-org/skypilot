""" Sky Spot Policy for SkyServe."""
import collections
import enum
import typing
from typing import Dict, List

from sky import sky_logging

if typing.TYPE_CHECKING:
    from sky.serve import replica_managers
    from sky.serve import service_spec

logger = sky_logging.init_logger(__name__)


class Location:
    """Location class of a spot instance."""

    def __init__(self, cloud: str, region: str, zone: str) -> None:
        self.cloud = cloud
        self.region = region
        self.zone = zone

    def __eq__(self, other):
        if isinstance(other, Location):
            return (self.cloud == other.cloud and
                    self.region == other.region and self.zone == other.zone)
        return False

    def __hash__(self):
        return hash(self.cloud + self.region + self.zone)


class LocationStatus(enum.Enum):
    """Location Spot Status."""
    ACTIVE = 'ACTIVE'
    PREEMPTED = 'PREEMPTED'


class SpotPlacer:
    """Spot Placement specification."""

    def __init__(self, spec: 'service_spec.SkyServiceSpec') -> None:
        assert spec.spot_locations is not None
        self.location2type: Dict[Location, LocationStatus] = {
            location: LocationStatus.ACTIVE for location in spec.spot_locations
        }

    def select(self, existing_replicas: List['replica_managers.ReplicaInfo'],
               num_replicas: int) -> List[Location]:
        """Select next zone to place spot instance."""
        raise NotImplementedError

    def move_location_to_active(self, location: Location) -> None:
        assert location in self.location2type
        self.location2type[location] = LocationStatus.ACTIVE

    def move_location_to_preempted(self, location: Location) -> None:
        assert location in self.location2type
        self.location2type[location] = LocationStatus.PREEMPTED

    def handle_active(self, location: Location) -> None:
        self.move_location_to_active(location)

    def handle_preemption(self, location: Location) -> None:
        self.move_location_to_preempted(location)

    def clear_preemptive_locations(self) -> None:
        for location in self.location2type:
            self.move_location_to_active(location)

    def active_locations(self) -> List[Location]:
        return [
            location for location, location_type in self.location2type.items()
            if location_type == LocationStatus.ACTIVE
        ]

    def preemptive_locations(self) -> List[Location]:
        return [
            location for location, location_type in self.location2type.items()
            if location_type == LocationStatus.PREEMPTED
        ]

    @classmethod
    def from_spec(cls, spec: 'service_spec.SkyServiceSpec') -> 'SpotPlacer':
        # TODO(MaoZiming): Support more spot placers.
        return DynamicFailoverSpotPlacer(spec)


class DynamicFailoverSpotPlacer(SpotPlacer):
    """Dynamic failover to an active zone when preempted."""

    def select(self, existing_replicas: List['replica_managers.ReplicaInfo'],
               num_replicas: int) -> List[Location]:
        # Prevent the case with only one active zones.
        if len(self.active_locations()) <= 1 and len(
                self.preemptive_locations()) > 0:
            self.clear_preemptive_locations()

        existing_locations = [
            info.location
            for info in existing_replicas
            if info.is_spot and info.location is not None
        ]
        existing_locations_to_count = collections.defaultdict(int)
        for location in existing_locations:
            existing_locations_to_count[location] = existing_locations.count(
                location)

        selected_locations = []
        for _ in range(num_replicas):
            # Select the zone with the least number of replicas.
            # TODO(MaoZiming): use cost to tie break.
            selected_location = min(
                self.active_locations(),
                key=lambda location: existing_locations_to_count[location])
            selected_locations.append(selected_location)
            existing_locations_to_count[selected_location] += 1
        return selected_locations
