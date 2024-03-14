""" Sky Spot Placer for SkyServe """

import enum
import os
import random
import typing
from typing import Dict, List, Optional

import sky
from sky import global_user_state
from sky import sky_logging
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

    def to_dict(self) -> Dict[str, str]:
        return {'cloud': self.cloud, 'region': self.region, 'zone': self.zone}


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
        self.resources2locations: Dict[str, Dict[Location, LocationStatus]]

        for resources in task.resources:
            resources_str = resources.hardware_str()
            if resources_str not in self.resources2locations:
                self.resources2locations[resources_str] = {}
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
                            self.resources2locations[resources_str][Location(
                                str(cloud), region.name,
                                None)] = LocationStatus.ACTIVE

                        else:
                            for zone in region.zones:
                                self.resources2locations[resources_str][
                                    Location(str(cloud), region.name,
                                             zone.name)] = LocationStatus.ACTIVE

    def select(
        self,
        resources: sky.Resources,
    ) -> Location:
        """Select next location to place spot instance."""
        raise NotImplementedError

    def _get_location_from_resources(self,
                                     resources: sky.Resources) -> Location:
        return Location(resources.cloud, resources.region, resources.zone)

    def set_active(self,
                   resources: Optional[sky.Resources],
                   location: Optional[Location] = None) -> None:
        if resources is None:
            return
        if location is None:
            location = self._get_location_from_resources(resources)
        self.resources2locations[
            resources.hardware_str()][location] = LocationStatus.ACTIVE

    def set_preemption(self,
                       resources: Optional[sky.Resources],
                       location: Optional[Location] = None) -> None:
        if resources is None:
            return
        if location is None:
            location = self._get_location_from_resources(resources)
        self.resources2locations[
            resources.hardware_str()][location] = LocationStatus.PREEMPTED

    def clear_preemptive_locations(self, resources: sky.Resources) -> None:
        for location in self.resources2locations[resources]:
            self.set_active(resources, location)

    def active_locations(self, resources: sky.Resources) -> List[Location]:
        return [
            location for location, location_type in
            self.resources2locations[resources].items()
            if location_type == LocationStatus.ACTIVE
        ]

    def preemptive_locations(self, resources: sky.Resources) -> List[Location]:
        return [
            location for location, location_type in
            self.resources2locations[resources].items()
            if location_type == LocationStatus.PREEMPTED
        ]

    @classmethod
    def from_spec(cls, spec: 'service_spec.SkyServiceSpec',
                  task_yaml_path: str) -> 'SpotPlacer':
        return DynamicFailoverSpotPlacer(spec, task_yaml_path)


class DynamicFailoverSpotPlacer(SpotPlacer):
    """Dynamic failover to an active location when preempted."""

    def select(
        self,
        resources: sky.Resources,
    ) -> Location:
        # Prevent the case with only one active location.
        if len(self.active_locations(resources)) <= 1 and len(
                self.preemptive_locations(resources)) > 0:
            self.clear_preemptive_locations(resources)

        return random.choice(self.active_locations(resources))
