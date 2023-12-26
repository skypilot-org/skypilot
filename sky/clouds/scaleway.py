"""Scaleway
"""
import typing
from typing import Dict, List, Optional, Tuple, Iterator

import requests

from sky import clouds
from sky import sky_logging
from sky.adaptors.scaleway import get_client, get_instance
from sky.clouds import Region, Zone, service_catalog

if typing.TYPE_CHECKING:
    # Renaming to avoid shadowing variables.
    pass

logger = sky_logging.init_logger(__name__)


class ScalewayError(Exception):
    pass


@clouds.CLOUD_REGISTRY.register
class Scaleway(clouds.Cloud):
    """Scaleway GPU instances"""

    _REPR = 'Scaleway'
    # pylint: disable=line-too-long
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.SPOT_INSTANCE: 'Scaleway does not support spot VMs.',
        clouds.CloudImplementationFeatures.MULTI_NODE: 'Scaleway does not support multi nodes.',
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER: 'Scaleway does not support custom disk tiers',
    }

    _MAX_CLUSTER_NAME_LEN_LIMIT = 50

    _regions: List[clouds.Region] = ["fr-par"]
    _zones: List[clouds.Zone] = ["fr-par-1", "fr-par-2"]

    @classmethod
    def _cloud_unsupported_features(
            cls) -> Dict[clouds.CloudImplementationFeatures, str]:
        return cls._CLOUD_UNSUPPORTED_FEATURES

    @classmethod
    def regions_with_offering(cls, instance_type: str,
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
    def zones_provision_loop(
            cls,
            *,
            region: str,
            num_nodes: int,
            instance_type: str,
            accelerators: Optional[Dict[str, int]] = None,
            use_spot: bool = False,
    ) -> Iterator[Optional[List[Zone]]]:
        """Loops over zones to retry for provisioning in a given region.

        Certain clouds' provisioners may handle batched requests, retrying for
        itself a list of zones under a region.  Others may need a specific zone
        per provision request (in that case, yields a one-element list for each
        zone).
        Optionally, caller can filter the yielded region/zones by specifying the
        instance_type, accelerators, and use_spot.

        Args:
            region: The region to provision.
            num_nodes: The number of nodes to provision.
            instance_type: The instance type to provision.
            accelerators: The accelerators to provision.
            use_spot: Whether to use spot instances.

        Yields:
            A list of zones that offer the requested resources in the given
            region, in the order of price.
            (1) If there is no zone that offers the specified resources, nothing
                is yielded. For example, Azure does not support zone, and
                calling this method with non-existing instance_type in the given
                region, will yield nothing, i.e. raise StopIteration.
                ```
                for zone in Azure.zones_provision_loop(region=region,
                                           instance_type='non-existing'):
                    # Will not reach here.
                ```
            (2) If the cloud's provisioner does not support `Zone`s, `None` will
                be yielded.
                ```
                for zone in Azure.zones_provision_loop(region=region,
                                           instance_type='existing-instance'):
                    assert zone is None
                ```
            This means if something is yielded, either it's None (zones are not
            supported and the region offers the resources) or it's a non-empty
            list (zones are supported and they offer the resources).

        Typical usage:

            for zones in cloud.region_zones_provision_loop(
                region,
                num_nodes,
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

    def instance_type_to_hourly_cost(self, instance_type: str, use_spot: bool,
                                     region: Optional[str],
                                     zone: Optional[str]) -> float:
        """Returns the hourly on-demand/spot price for an instance type."""
        return get_instance().get_server_types()

    def accelerators_to_hourly_cost(self, accelerators: Dict[str, int],
                                    use_spot: bool, region: Optional[str],
                                    zone: Optional[str]) -> float:
        """Returns the hourly on-demand price for accelerators."""
        raise NotImplementedError

    def get_egress_cost(self, num_gigabytes: float) -> float:
        """Returns the egress cost.
        """
        return 0.0

    @classmethod
    def regions(cls) -> List[clouds.Region]:
        if not cls._regions:
            cls._regions = [
                clouds.Region(...),
            ]
        return cls._regions

    def __repr__(self):
        return 'Scaleway'

    def is_same_cloud(self, other: clouds.Cloud) -> bool:
        # Returns true if the two clouds are the same cloud type.
        return isinstance(other, Scaleway)

    @classmethod
    def check_credentials(cls) -> Tuple[bool, Optional[str]]:
        try:
            instance = get_instance()
            instance(client=get_client()).list_servers()
        except ScalewayError:
            return False, (
                'Failed to access Scaleway with credentials. '
                'To configure credentials, go to:\n    '
                '  https://console.scaleway.com/iam/api-keys\n    '
                'to generate API key and install it in your Scaleway Configuration file'
            )
        except requests.exceptions.ConnectionError:
            return False, ('Failed to verify Scaleway credentials. '
                           'Check your network connection '
                           'and try again.')
        return True, None

    def _get_feasible_launchable_resources(
            self, resources: 'resources_lib.Resources'
    ) -> Tuple[List['resources_lib.Resources'], List[str]]:
        """See get_feasible_launchable_resources()."""
        r = resources.copy(
            cloud=Scaleway,
            instance_type="RTX-3090",
            region="fr-par",
            zone="fr-par-2",
        )
        # TODO: We currently do not perform any search on instancetype
        return [r], []

    def make_deploy_resources_variables(
            self,
            resources: 'resources_lib.Resources',
            cluster_name_on_cloud: str,
            region: 'Region',
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
    def get_vcpus_mem_from_instance_type(
            cls, instance_type: str) -> Tuple[Optional[float], Optional[float]]:
        """Returns the #vCPUs and memory that the instance type offers."""
        raise NotImplementedError

    @classmethod
    def get_accelerators_from_instance_type(
            cls,
            instance_type: str,
    ) -> Optional[Dict[str, int]]:
        """Returns {acc: acc_count} held by 'instance_type', if any."""
        raise NotImplementedError

    @classmethod
    def get_default_instance_type(
            cls,
            cpus: Optional[str] = None,
            memory: Optional[str] = None,
            disk_tier: Optional[str] = None) -> Optional[str]:
        """Returns the default instance type with the given #vCPUs, memory and
        disk tier.

        For example, if cpus='4', this method returns the default instance type
        with 4 vCPUs.  If cpus='4+', this method returns the default instance
        type with 4 or more vCPUs.

        If 'memory=4', this method returns the default instance type with 4GB
        memory.  If 'memory=4+', this method returns the default instance
        type with 4GB or more memory.

        If disk_rier='medium', this method returns the default instance type
        that support medium disk tier.

        When cpus is None, memory is None or disk tier is None, this method will
        never return None. This method may return None if the cloud's default
        instance family does not have a VM with the given number of vCPUs
        (e.g., when cpus='7') or does not have a VM with the give disk tier
        (e.g. Azure, disk_tier='high').
        """
        raise NotImplementedError

    def get_credential_file_mounts(self) -> Dict[str, str]:
        """Returns the files necessary to access this cloud.

        Returns a dictionary that will be added to a task's file mounts.
        """
        raise NotImplementedError

    @classmethod
    def get_image_size(cls, image_id: str, region: Optional[str]) -> float:
        """Check the image size from the cloud.

        Returns: the image size in GB.
        Raises: ValueError if the image cannot be found.
        """
        raise NotImplementedError

    def instance_type_exists(self, instance_type):
        """Returns whether the instance type exists for this cloud."""
        raise NotImplementedError

    def accelerator_in_region_or_zone(self,
                                      accelerator: str,
                                      acc_count: int,
                                      region: Optional[str] = None,
                                      zone: Optional[str] = None) -> bool:
        """Returns whether the accelerator is valid in the region or zone."""
        raise NotImplementedError

    @classmethod
    def check_disk_tier_enabled(cls, instance_type: str,
                                disk_tier: str) -> None:
        """Errors out if the disk tier is not supported by the cloud provider.

        Raises:
            exceptions.NotSupportedError: If the disk tier is not supported.
        """
        return None

    @classmethod
    def query_status(cls, name: str, tag_filters: Dict[str, str],
                     region: Optional[str], zone: Optional[str],
                     **kwargs) -> List['status_lib.ClusterStatus']:
        """Queries the latest status of the cluster from the cloud.

        The global_user_state caches the status of the clusters, but the
        actual status of the clusters may change on the cloud, e.g., the
        autostop happens, or the user manually stops the cluster. This
        method queries the cloud to get the latest cluster status.

        Returns:
            A list of ClusterStatus representing the status of all the
            alive nodes in the cluster.

        Raises:
            exceptions.ClusterStatusFetchingError: raised if the status of the
                cluster cannot be fetched.
        """
        raise NotImplementedError

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return service_catalog.validate_region_zone(region,
                                                    zone,
                                                    clouds='scaleway')
