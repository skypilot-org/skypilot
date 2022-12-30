"""Interfaces: clouds, regions, and zones."""
import collections
import typing
from typing import Dict, Iterator, List, Optional, Tuple, Type

from sky.clouds import service_catalog
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky import resources


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


class _CloudRegistry(dict):
    """Registry of clouds."""

    def from_str(self, name: Optional[str]) -> Optional['Cloud']:
        if name is None:
            return None
        if name.lower() not in self:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Cloud {name} is not a valid cloud among '
                                 f'{list(self.keys())}')
        return self.get(name.lower())

    def register(self, cloud_cls: Type['Cloud']) -> Type['Cloud']:
        name = cloud_cls.__name__.lower()
        assert name not in self, f'{name} already registered'
        self[name] = cloud_cls()
        return cloud_cls


CLOUD_REGISTRY = _CloudRegistry()


class Cloud:
    """A cloud provider."""

    _REPR = '<Cloud>'

    #### Regions/Zones ####

    @classmethod
    def regions(cls) -> List[Region]:
        raise NotImplementedError

    @classmethod
    def regions_with_offering(cls, instance_type: Optional[str],
                              accelerators: Optional[Dict[str, int]],
                              use_spot: bool, region: Optional[str],
                              zone: Optional[str]) -> List[Region]:
        """Returns the regions that offer the specified resources.

        The order of the regions follow the order of the regions returned by
        service_catalog/common.py#get_region_zones().
        When region or zone is not None, the returned value will be limited to
        the specified region/zone.

        Returns:
            A set of `Region`s that have the offerings for the specified
            resources.
            For each `Region` in the set, `region.zones` is the list of `Zone`s
            which have the offerings. For the clouds that do not expose `Zone`s,
            `region.zones` is an empty list.
        """
        raise NotImplementedError

    @classmethod
    def region_zones_provision_loop(
        cls,
        *,
        instance_type: Optional[str] = None,
        accelerators: Optional[Dict[str, int]] = None,
        use_spot: bool = False,
    ) -> Iterator[Tuple[Region, List[Zone]]]:
        """Loops over (region, zones) to retry for provisioning.

        Certain clouds' provisioners may handle batched requests, retrying for
        itself a list of zones under a region.  Others may need a specific zone
        per provision request (in that case, yields (region, a one-element list
        for each zone)).
        Optionally, caller can filter the yielded region/zones by specifying the
        instance_type, accelerators, and use_spot.

        Args:
            instance_type: The instance type to provision.
            accelerators: The accelerators to provision.
            use_spot: Whether to use spot instances.

        Typical usage:

            for region, zones in cloud.region_zones_provision_loop(
                instance_type,
                accelerators,
                use_spot
            ):
                success = try_provision(region, zones, resources)
                if success:
                    break
        """
        raise NotImplementedError

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        """Returns the shell command to obtain the zone of instance."""
        raise NotImplementedError

    #### Normal methods ####

    def instance_type_to_hourly_cost(self, instance_type: str, use_spot: bool,
                                     region: Optional[str],
                                     zone: Optional[str]) -> float:
        """Returns the hourly on-demand/spot price for an instance type."""
        raise NotImplementedError

    def accelerators_to_hourly_cost(self, accelerators: Dict[str, int],
                                    use_spot: bool, region: Optional[str],
                                    zone: Optional[str]) -> float:
        """Returns the hourly on-demand price for accelerators."""
        raise NotImplementedError

    def get_egress_cost(self, num_gigabytes):
        """Returns the egress cost.

        TODO: takes into account "per month" accumulation per account.
        """
        raise NotImplementedError

    def is_same_cloud(self, other):
        raise NotImplementedError

    def make_deploy_resources_variables(
        self,
        resources: 'resources.Resources',
        region: Optional['Region'],
        zones: Optional[List['Zone']],
    ) -> Dict[str, Optional[str]]:
        """Converts planned sky.Resources to cloud-specific resource variables.

        These variables are used to fill the node type section (instance type,
        any accelerators, etc.) in the cloud's deployment YAML template.

        Cloud-agnostic sections (e.g., commands to run) need not be returned by
        this function.

        Returns:
          A dictionary of cloud-specific node type variables.
        """
        raise NotImplementedError

    @classmethod
    def get_vcpus_from_instance_type(cls,
                                     instance_type: str) -> Optional[float]:
        """Returns the number of virtual CPUs that the instance type offers."""
        raise NotImplementedError

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, int]]:
        """Returns {acc: acc_count} held by 'instance_type', if any."""
        raise NotImplementedError

    @classmethod
    def get_default_instance_type(cls) -> str:
        raise NotImplementedError

    @classmethod
    def _get_default_region(cls) -> Region:
        raise NotImplementedError

    @classmethod
    def is_image_tag_valid(cls, image_tag: str, region: Optional[str]) -> bool:
        """Validates that the image tag is valid for this cloud."""
        return service_catalog.is_image_tag_valid(image_tag,
                                                  region,
                                                  clouds=cls._REPR.lower())

    def get_feasible_launchable_resources(self, resources):
        """Returns a list of feasible and launchable resources.

        Feasible resources refer to an offering respecting the resource
        requirements.  Currently, this function implements "filtering" the
        cloud's offerings only w.r.t. accelerators constraints.

        Launchable resources require a cloud and an instance type be assigned.
        """
        raise NotImplementedError

    def check_credentials(self) -> Tuple[bool, Optional[str]]:
        """Checks if the user has access credentials to this cloud.

        Returns a boolean of whether the user can access this cloud, and a
        string describing the reason if the user cannot access.
        """
        raise NotImplementedError

    def get_current_user_identity(self) -> Optional[str]:
        """(Advanced) Returns currently active user identity of this cloud.

        The user "identity" is associated with each SkyPilot cluster they
        creates. This is used in protecting cluster operations, such as
        provision, teardown and status refreshing, in a multi-identity
        scenario, where the same user/device can switch between different
        cloud identities. We check that the user identity matches before:
            - Provisioning/starting a cluster
            - Stopping/tearing down a cluster
            - Refreshing the status of a cluster

        Design choice: we allow the operations that can correctly work with
        a different user identity, as a user should have full control over
        all their clusters (no matter which identity it belongs to), e.g.,
        submitting jobs, viewing logs, auto-stopping, etc.

        The choice of what constitutes an identity is up to each cloud's
        implementation. In general, to suffice for the above purposes,
        ensure that different identities should imply different sets of
        resources are used when the user invoked each cloud's default
        CLI/API.

        Example identities (see cloud implementations):
            - AWS: unique aws:user_id
            - GCP: email address + project ID
            - Azure: email address + subscription ID

        Returns:
            None if the cloud does not have a concept of user identity
            (access protection will be disabled for these clusters);
            otherwise the currently active user identity.
        Raises:
            exceptions.CloudUserIdentityError: If the user identity cannot be
                retrieved.
        """
        return None

    def get_credential_file_mounts(self) -> Dict[str, str]:
        """Returns the files necessary to access this cloud.

        Returns a dictionary that will be added to a task's file mounts.
        """
        raise NotImplementedError

    def get_image_size(self, image_id: str, region: Optional[str]) -> float:
        """Check the image size from the cloud.

        Returns: the image size in GB.
        Raises: ValueError if the image cannot be found.
        """
        raise NotImplementedError

    def instance_type_exists(self, instance_type):
        """Returns whether the instance type exists for this cloud."""
        raise NotImplementedError

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        """Validates the region and zone."""
        return service_catalog.validate_region_zone(region,
                                                    zone,
                                                    clouds=self._REPR.lower())

    def accelerator_in_region_or_zone(self,
                                      accelerator: str,
                                      acc_count: int,
                                      region: Optional[str] = None,
                                      zone: Optional[str] = None) -> bool:
        """Returns whether the accelerator is valid in the region or zone."""
        raise NotImplementedError

    def need_cleanup_after_preemption(self,
                                      resource: 'resources.Resources') -> bool:
        """Returns whether a spot resource needs cleanup after preeemption.

        In most cases, spot resources do not need cleanup after preemption,
        as long as the cluster can be relaunched with the same name and tag,
        no matter the preemption behavior is to terminate or stop the cluster.
        The only exception by far is GCP's Spot TPU VM. We override this method
        in gcp.py.
        """
        del resource
        return False

    def __repr__(self):
        return self._REPR
