"""Interfaces: clouds, regions, and zones."""
import collections
from typing import Dict, Iterator, List, Optional, Tuple


class Region(collections.namedtuple('Region', ['name'])):
    """A region."""
    name: str
    zones: List['Zone'] = []

    def set_zones(self, zones: List['Zone']):
        self.zones = zones
        for zone in self.zones:
            zone.region = self
        return self


class Zone(collections.namedtuple('Zone', ['name'])):
    """A zone, typically grouped under a region."""
    name: str
    region: Region


class Cloud(object):

    #### Regions/Zones ####

    @classmethod
    def regions(cls) -> List[Region]:
        raise NotImplementedError

    @classmethod
    def region_zones_provision_loop(cls) -> Iterator[Tuple[Region, List[Zone]]]:
        """Loops over (region, zones) to retry for provisioning.

        Certain clouds' provisioners may handle batched requests, retrying for
        itself a list of zones under a region.  Others may need a specific zone
        per provision request (in that case, yields (region, a one-element list
        for each zone)).

        Typical usage:

            for region, zones in cloud.region_zones_provision_loop():
                success = try_provision(region, zones, resources)
                if success:
                    break
        """
        raise NotImplementedError

    #### Normal methods ####

    # TODO: incorporate region/zone into the API.
    def instance_type_to_hourly_cost(self, instance_type, use_spot):
        """Returns the hourly on-demand price for an instance type."""
        raise NotImplementedError

    def accelerators_to_hourly_cost(self, accelerators):
        """Returns the hourly on-demand price for accelerators."""
        raise NotImplementedError

    def get_egress_cost(self, num_gigabytes):
        """Returns the egress cost.

        TODO: takes into account "per month" accumulation per account.
        """
        raise NotImplementedError

    def is_same_cloud(self, other):
        raise NotImplementedError

    def make_deploy_resources_variables(self, task):
        """Converts a planned sky.Task into cloud-specific resource variables.

        These variables are used to fill the node type section (instance type,
        any accelerators, etc.) in the cloud's deployment YAML template.

        Cloud-agnostic sections (e.g., commands to run) need not be returned by
        this function.

        Returns:
          A dictionary of cloud-specific node type variables.
        """
        raise NotImplementedError

    def get_accelerators_from_instance_type(
            self,
            instance_type: str,
    ) -> Optional[Dict[str, int]]:
        """Returns {acc: acc_count} held by 'instance_type', if any."""
        raise NotImplementedError

    @classmethod
    def get_default_instance_type(cls):
        raise NotImplementedError

    @classmethod
    def get_default_region(cls) -> Region:
        raise NotImplementedError

    def get_feasible_launchable_resources(self, resources):
        """Returns a list of feasible and launchable resources.

        Feasible resources refer to an offering respecting the resource
        requirements.  Currently, this function implements "filtering" the
        cloud's offerings only w.r.t. accelerators constraints.

        Launchable resources require a cloud and an instance type be assigned.
        """
        raise NotImplementedError
