""" Sky Spot Placer for SkyServe """

import collections
import enum
import os
import typing
from typing import Any, Dict, List, Optional

import sky
from sky import clouds
from sky import global_user_state
from sky import optimizer
from sky import sky_logging
from sky.serve.service_spec import SkyServiceSpec
from sky.utils import common_utils

if typing.TYPE_CHECKING:
    from sky.serve import replica_managers
    from sky.serve import service_spec

logger = sky_logging.init_logger(__name__)


class Location:
    """Location class of a spot instance."""

    def __init__(self, cloud: str, region: str, zone: Optional[str]) -> None:
        self.cloud = cloud
        self.region = region
        self.zone = zone if zone is not None else ''

    def __eq__(self, other) -> bool:
        if isinstance(other, Location):
            return (self.cloud == other.cloud and
                    self.region == other.region and self.zone == other.zone)
        return False

    def __hash__(self) -> int:
        return hash(self.cloud + self.region + self.zone)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'cloud': clouds.CLOUD_REGISTRY.from_str(self.cloud),
            'region': self.region,
            'zone': self.zone
        }

    def json(self) -> Dict[str, str]:
        return {'cloud': self.cloud, 'region': self.region, 'zone': self.zone}

    def __repr__(self) -> str:
        return (f'Location(cloud={self.cloud!r}, region={self.region!r}, '
                f'zone={self.zone!r})')


class LocationStatus(enum.Enum):
    """Location Spot Status."""
    ACTIVE = 'ACTIVE'
    PREEMPTED = 'PREEMPTED'


class SpotPlacer:
    """Spot Placement specification."""

    def __init__(self, spec: 'service_spec.SkyServiceSpec',
                 task_yaml_path: str) -> None:
        del spec
        self.task_yaml_path = task_yaml_path
        config = common_utils.read_yaml(os.path.expanduser(task_yaml_path))
        task = sky.Task.from_yaml_config(config)

        enabled_clouds = global_user_state.get_enabled_clouds()
        feasible_locations: List[Location] = []

        for resources in task.resources:
            clouds_list = ([resources.cloud]
                           if resources.cloud is not None else enabled_clouds)
            for cloud in clouds_list:
                feasible_resources, _ = (
                    cloud.get_feasible_launchable_resources(
                        resources, num_nodes=task.num_nodes))

                for feasible_resource in feasible_resources:
                    regions = (
                        feasible_resource.get_valid_regions_for_launchable())
                    for region in regions:
                        if region is None:
                            continue
                        # Some clouds, such as Azure, does not support zones.
                        if region.zones is None:
                            feasible_locations.append(
                                Location(str(cloud), region.name, None))
                        else:
                            for zone in region.zones:
                                feasible_locations.append(
                                    Location(str(cloud), region.name,
                                             zone.name))

        self.location2type: Dict[Location, LocationStatus] = {
            location: LocationStatus.ACTIVE for location in feasible_locations
        }

    def select(self, existing_replicas: List['replica_managers.ReplicaInfo'],
               num_replicas: int) -> List[Location]:
        """Select next location to place spot instance."""
        raise NotImplementedError

    def set_active(self, location: Optional[Location] = None) -> None:
        assert location in self.location2type
        self.location2type[location] = LocationStatus.ACTIVE

    def set_preempted(self, location: Optional[Location] = None) -> None:
        assert location in self.location2type
        self.location2type[location] = LocationStatus.PREEMPTED

    def clear_preemptive_locations(self) -> None:
        for location in self.location2type:
            self.set_active(location)

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
    def from_spec(cls, spec: 'service_spec.SkyServiceSpec',
                  task_yaml_path: str) -> 'SpotPlacer':
        return DynamicFailoverSpotPlacer(spec, task_yaml_path)


class DynamicFailoverSpotPlacer(SpotPlacer):
    """Dynamic failover to an active location when preempted."""

    def __init__(self, spec: SkyServiceSpec, task_yaml_path: str) -> None:
        super().__init__(spec, task_yaml_path)
        self.location2cost: Dict[Location, float] = {}

    def _get_location_cost(self, location: Location) -> float:
        if location in self.location2cost:
            return self.location2cost[location]

        estimated_runtime = 1 * 3600
        config = common_utils.read_yaml(os.path.expanduser(self.task_yaml_path))
        task = sky.Task.from_yaml_config(config)

        overrided_resources = [
            r.copy(**{
                'use_spot': True,
                **(location.to_dict())
            }) for r in task.resources
        ]
        task.set_resources(type(task.resources)(overrided_resources))

        launchable_resources, _, _ = (optimizer.fill_in_launchable_resources(
            task=task,
            blocked_resources=None,
            try_fix_with_sky_check=False,
            quiet=True))

        min_cost = float('inf')
        for _, launchable_list in launchable_resources.items():
            for resources in launchable_list:
                cost = resources.get_cost(estimated_runtime)
                min_cost = min(min_cost, cost)

        logger.info(f'Cost of {location} is {min_cost}')
        self.location2cost[location] = min_cost
        return min_cost

    def select(self, existing_replicas: List['replica_managers.ReplicaInfo'],
               num_replicas: int) -> List[Location]:
        # Prevent the case with only one active location.
        if len(self.active_locations()) <= 1 and len(
                self.preemptive_locations()) > 0:
            self.clear_preemptive_locations()

        existing_locations_to_count: Dict[Location,
                                          int] = collections.defaultdict(int)
        for replica in existing_replicas:
            if replica.is_spot and replica.location is not None:
                existing_locations_to_count[replica.location] += 1

        selected_locations = []
        for _ in range(num_replicas):
            # Select the location with the least number of replicas.
            # Tie break with the cost.
            selected_location = min(self.active_locations(),
                                    key=lambda location:
                                    (existing_locations_to_count[location],
                                     self._get_location_cost(location)))
            selected_locations.append(selected_location)
            existing_locations_to_count[selected_location] += 1
        return selected_locations
