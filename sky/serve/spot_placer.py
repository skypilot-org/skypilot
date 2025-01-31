"""Spot Placer for SpotHedge."""

import dataclasses
import enum
import typing
from typing import Any, Dict, List, Optional

from sky import check as sky_check
from sky import sky_logging
from sky.utils import resources_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky import clouds as sky_clouds
    from sky import resources as resources_lib
    from sky import task as task_lib
    from sky.serve import service_spec

logger = sky_logging.init_logger(__name__)

SPOT_PLACERS = {}
DEFAULT_SPOT_PLACER = None
SPOT_HEDGE_PLACER = 'dynamic_fallback'


@dataclasses.dataclass
class Location:
    """Location class of a spot instance."""
    cloud: 'sky_clouds.Cloud'
    region: str
    zone: Optional[str]

    def __eq__(self, other) -> bool:
        if isinstance(other, Location):
            return (self.cloud.is_same_cloud(other.cloud) and
                    self.region == other.region and self.zone == other.zone)
        return False

    def __hash__(self) -> int:
        return hash(
            str(self.cloud) + self.region +
            (self.zone if self.zone is not None else ''))

    @classmethod
    def from_resources(cls, resources: 'resources_lib.Resources') -> 'Location':
        return cls(resources.cloud, resources.region, resources.zone)

    def to_dict(self) -> Dict[str, Any]:
        return {'cloud': self.cloud, 'region': self.region, 'zone': self.zone}


class LocationStatus(enum.Enum):
    """Location Spot Status."""
    ACTIVE = 'ACTIVE'
    PREEMPTED = 'PREEMPTED'


def _get_possible_location_from_resources(resources: 'resources_lib.Resources',
                                          num_nodes: int) -> List[Location]:
    enabled_clouds = sky_check.get_cached_enabled_clouds_or_refresh(
        raise_if_no_cloud_access=False)
    clouds_list: List['sky_clouds.Cloud'] = (
        [resources.cloud] if resources.cloud is not None else enabled_clouds)
    all_possible_resources = []
    for cloud in clouds_list:
        feasible_resources = cloud.get_feasible_launchable_resources(
            resources, num_nodes=num_nodes)
        for feasible in feasible_resources.resources_list:
            all_possible_resources.extend(
                resources_utils.make_launchables_for_valid_region_zones(
                    feasible))
    return [
        Location.from_resources(resource) for resource in all_possible_resources
    ]


class SpotPlacer:
    """Spot Placement specification."""

    def __init__(self, task: 'task_lib.Task') -> None:
        # TODO(tian): Allow multiple resources on only differences in locations.
        if len(task.resources) != 1:
            with ux_utils.print_exception_no_traceback():
                raise ValueError('Multiple resources are not supported '
                                 'for smart spot placement.')
        self.resources = list(task.resources)[0]
        possible_locations = _get_possible_location_from_resources(
            self.resources, task.num_nodes)
        logger.info(f'{len(possible_locations)} possible location candidates '
                    'are enabled for spot placement.')
        logger.debug(f'All possible locations: {possible_locations}')
        self.location2status = {
            location: LocationStatus.ACTIVE for location in possible_locations
        }

    def __init_subclass__(cls, name: str, default: bool = False):
        SPOT_PLACERS[name] = cls
        if default:
            global DEFAULT_SPOT_PLACER
            assert DEFAULT_SPOT_PLACER is None, (
                'Only one policy can be default.')
            DEFAULT_SPOT_PLACER = name

    def select_next_location(
            self, current_resources: List['resources_lib.Resources']
    ) -> Dict[str, Any]:
        """Select next location to place spot instance."""
        raise NotImplementedError

    def set_active(self, resources: 'resources_lib.Resources') -> None:
        location = Location.from_resources(resources)
        assert location in self.location2status, location
        self.location2status[location] = LocationStatus.ACTIVE

    def set_preemptive(self, resources: 'resources_lib.Resources') -> None:
        location = Location.from_resources(resources)
        assert location in self.location2status, location
        self.location2status[location] = LocationStatus.PREEMPTED

    def clear_preemptive_locations(self) -> None:
        for location in self.location2status:
            self.location2status[location] = LocationStatus.ACTIVE

    def _min_cost_location(self, locations: List[Location]) -> Location:

        def _get_cost_per_hour(location: Location) -> float:
            r: 'resources_lib.Resources' = self.resources.copy(
                **location.to_dict())
            return r.get_cost(seconds=3600)

        return min(locations, key=_get_cost_per_hour)

    def _location_with_status(self, status: LocationStatus) -> List[Location]:
        return [
            location
            for location, location_type in self.location2status.items()
            if location_type == status
        ]

    def active_locations(self) -> List[Location]:
        return self._location_with_status(LocationStatus.ACTIVE)

    def preemptive_locations(self) -> List[Location]:
        return self._location_with_status(LocationStatus.PREEMPTED)

    @classmethod
    def from_task(cls, spec: 'service_spec.SkyServiceSpec',
                  task: 'task_lib.Task') -> Optional['SpotPlacer']:
        if spec.spot_placer is None:
            return None
        return SPOT_PLACERS[spec.spot_placer](task)


class DynamicFallbackSpotPlacer(SpotPlacer,
                                name=SPOT_HEDGE_PLACER,
                                default=True):
    """Dynamic Fallback Placer."""

    def select_next_location(
            self, current_resources: List['resources_lib.Resources']
    ) -> Dict[str, Any]:
        current_locations = [
            Location.from_resources(resource) for resource in current_resources
        ]
        active_locations = self.active_locations()
        # Prioritize locations that are not currently used.
        candidate_locations: List[Location] = [
            location for location in current_locations
            if location not in active_locations
        ]
        # If no candidate locations, use all active locations.
        if not candidate_locations:
            candidate_locations = active_locations
        return self._min_cost_location(candidate_locations).to_dict()

    def set_preemptive(self, resources: 'resources_lib.Resources') -> None:
        super().set_preemptive(resources)
        # Prevent the case with only one active location.
        if len(self.active_locations()) < 2:
            self.clear_preemptive_locations()
