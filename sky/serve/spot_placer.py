"""Spot Placer for SpotHedge."""

import collections
import dataclasses
import enum
import typing
from typing import Any, Dict, List, Optional, Set

from sky import check as sky_check
from sky import clouds as sky_clouds
from sky import sky_logging
from sky.clouds import cloud as sky_cloud
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
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

    @classmethod
    def from_pickleable(
        cls,
        data: Optional[Dict[str, Optional[str]]],
    ) -> Optional['Location']:
        if data is None:
            return None
        cloud = registry.CLOUD_REGISTRY.from_str(data['cloud'])
        assert cloud is not None
        assert data['region'] is not None
        return cls(
            cloud=cloud,
            region=data['region'],
            zone=data['zone'],
        )

    def to_pickleable(self) -> Dict[str, Optional[str]]:
        return {
            'cloud': str(self.cloud),
            'region': self.region,
            'zone': self.zone,
        }


class LocationStatus(enum.Enum):
    """Location Spot Status."""
    ACTIVE = 'ACTIVE'
    PREEMPTED = 'PREEMPTED'


def _get_possible_location_from_task(task: 'task_lib.Task') -> List[Location]:

    def _without_location(
            resources: 'resources_lib.Resources') -> 'resources_lib.Resources':
        return resources.copy(cloud=None, region=None, zone=None)

    assert task.resources  # Guaranteed in task constructor
    empty_location_resources = _without_location(list(task.resources)[0])
    empty_location_resources_config = empty_location_resources.to_yaml_config()

    location_requirements: Dict[str, Dict[str, Set[str]]] = (
        collections.defaultdict(lambda: collections.defaultdict(set)))

    for r in task.resources:
        if (_without_location(r).to_yaml_config() !=
                empty_location_resources_config):
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'Different resource configurations are not supported '
                    'for spot placement. All resources must have the same '
                    'configuration except for cloud/region/zone.')
        if r.cloud is None:
            continue
        cloud_str = str(r.cloud)
        if r.region is None:
            # Access defaultdict to create empty entry if it doesn't exist.
            _ = location_requirements[cloud_str]
            continue
        if r.zone is None:
            # Same as above.
            _ = location_requirements[cloud_str][r.region]
            continue
        location_requirements[cloud_str][r.region].add(r.zone)

    clouds_list: List[sky_clouds.Cloud] = []
    for c in location_requirements.keys():
        cloud_obj = registry.CLOUD_REGISTRY.from_str(c)
        assert cloud_obj is not None
        clouds_list.append(cloud_obj)
    if not clouds_list:
        # If the cloud list is empty, that means the user has no location
        # related requirements. Then we start with all enabled clouds and
        # all possible regions and zones.
        clouds_list = sky_check.get_cached_enabled_clouds_or_refresh(
            capability=sky_cloud.CloudCapability.COMPUTE,
            raise_if_no_cloud_access=False)
        for cloud in clouds_list:
            # Create empty entry for each cloud.
            _ = location_requirements[str(cloud)]

    possible_locations = set()
    for cloud in clouds_list:
        feasible_resources: resources_utils.FeasibleResources = (
            cloud.get_feasible_launchable_resources(empty_location_resources,
                                                    num_nodes=task.num_nodes))
        for feasible in feasible_resources.resources_list:
            # We set override_optimize_by_zone=True to force the provisioner
            # to use zone-level provisioning. This is to get accurate location
            # information.
            launchables: List['resources_lib.Resources'] = (
                resources_utils.make_launchables_for_valid_region_zones(
                    feasible, override_optimize_by_zone=True))
            for launchable in launchables:
                cloud_str = str(launchable.cloud)
                region = launchable.region
                zone = launchable.zone
                if (cloud_str not in location_requirements and
                        location_requirements):
                    continue
                # We need to use .get() here to avoid creating extra entries in
                # location_requirements, and being treated as user's requirement
                # in the following regions.
                cloud_reqs = location_requirements.get(cloud_str, {})
                if region not in cloud_reqs and cloud_reqs:
                    continue
                region_reqs = cloud_reqs.get(region, set())
                if zone not in region_reqs and region_reqs:
                    continue
                possible_locations.add(Location.from_resources(launchable))

    return list(possible_locations)


class SpotPlacer:
    """Spot Placement specification."""

    def __init__(self, task: 'task_lib.Task') -> None:
        possible_locations = _get_possible_location_from_task(task)
        logger.info(f'{len(possible_locations)} possible location candidates '
                    'are enabled for spot placement.')
        logger.debug(f'All possible locations: {possible_locations}')
        self.location2status: Dict[Location, LocationStatus] = {
            location: LocationStatus.ACTIVE for location in possible_locations
        }
        self.location2cost: Dict[Location, float] = {}
        # Already checked there is only one resource in the task.
        self.resources = list(task.resources)[0]
        self.num_nodes = task.num_nodes

    def __init_subclass__(cls, name: str, default: bool = False):
        SPOT_PLACERS[name] = cls
        if default:
            global DEFAULT_SPOT_PLACER
            assert DEFAULT_SPOT_PLACER is None, (
                'Only one policy can be default.')
            DEFAULT_SPOT_PLACER = name

    def select_next_location(self,
                             current_locations: List[Location]) -> Location:
        """Select next location to place spot instance."""
        raise NotImplementedError

    def set_active(self, location: Location) -> None:
        assert location in self.location2status, location
        self.location2status[location] = LocationStatus.ACTIVE

    def set_preemptive(self, location: Location) -> None:
        assert location in self.location2status, location
        self.location2status[location] = LocationStatus.PREEMPTED

    def clear_preemptive_locations(self) -> None:
        for location in self.location2status:
            self.location2status[location] = LocationStatus.ACTIVE

    def _min_cost_location(self, locations: List[Location]) -> Location:

        def _get_cost_per_hour(location: Location) -> float:
            if location in self.location2cost:
                return self.location2cost[location]
            # TODO(tian): Is there a better way to do this? This is for filling
            # instance type so the get_cost() can operate normally.
            r: 'resources_lib.Resources' = self.resources.copy(
                **location.to_dict())
            assert r.cloud is not None
            rs = r.cloud.get_feasible_launchable_resources(
                r, num_nodes=self.num_nodes).resources_list
            # For some clouds, there might have multiple instance types
            # satisfying the resource request. In such case we choose the
            # cheapest one, as the optimizer does. Reference:
            # sky/optimizer.py::Optimizer::_print_candidates
            cost = min(r.get_cost(seconds=3600) for r in rs)
            self.location2cost[location] = cost
            return cost

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

    def select_next_location(self,
                             current_locations: List[Location]) -> Location:
        active_locations = self.active_locations()
        # Prioritize locations that are not currently used.
        candidate_locations: List[Location] = [
            location for location in active_locations
            if location not in current_locations
        ]
        # If no candidate locations, use all active locations.
        if not candidate_locations:
            candidate_locations = active_locations
        res = self._min_cost_location(candidate_locations)
        logger.info(f'Active locations: {active_locations}\n'
                    f'Current locations: {current_locations}\n'
                    f'Candidate locations: {candidate_locations}\n'
                    f'Selected location: {res}\n')
        return res

    def set_preemptive(self, location: Location) -> None:
        super().set_preemptive(location)
        # Prevent the case with only one active location.
        if len(self.active_locations()) < 2:
            self.clear_preemptive_locations()
