""" Yotta Cloud. """

import os
import typing
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from sky import catalog
from sky import clouds
from sky.provision.yotta.yotta_utils import CREDENTIAL_FILE
from sky.provision.yotta.yotta_utils import yotta_client
from sky.utils import registry
from sky.utils import resources_utils

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib
    from sky.utils import volume as volume_lib

_CLOUD = 'yotta'
_BASE_IMAGE = (
    'yottalabsai/pytorch:2.9.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04')


@registry.CLOUD_REGISTRY.register
class Yotta(clouds.Cloud):
    """ Yotta GPU Cloud

    _REPR | The string representation for the Yotta GPU cloud object.
    """
    _REPR = 'Yotta'
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.STOP: 'Stopping not supported.',
        clouds.CloudImplementationFeatures.MULTI_NODE:
            ('Multi-node not supported yet, as the interconnection among nodes '
             'are non-trivial on Yotta.'),
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER:
            ('Disk cloning not supported yet on Yotta.'),
        clouds.CloudImplementationFeatures.SPOT_INSTANCE:
            ('Spot instances not supported yet on Yotta.'),
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
            ('Customizing disk tier is not supported yet on Yotta.'),
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER:
            ('Custom network tier is not supported yet on Yotta.'),
        clouds.CloudImplementationFeatures.STORAGE_MOUNTING:
            ('Mounting object stores is not supported on Yotta. To read data '
             'from object stores on Yotta, use `mode: COPY` to copy the data '
             'to local disk.'),
        clouds.CloudImplementationFeatures.HOST_CONTROLLERS:
            ('Host controllers not supported yet on Yotta.'),
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS:
            ('High availability controllers are not supported yet on Yotta.'),
        clouds.CloudImplementationFeatures.AUTO_TERMINATE:
            ('Auto-termination not supported yet on Yotta.'),
        clouds.CloudImplementationFeatures.AUTOSTOP:
            ('Auto-stop not supported yet on Yotta.'),
        clouds.CloudImplementationFeatures.AUTODOWN:
            ('Auto-down not supported yet on Yotta.'),
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK:
            ('Customized multiple network interfaces are not supported yet on '
             'Yotta.'),
        clouds.CloudImplementationFeatures.LOCAL_DISK:
            ('Specifying local disks are not supported on Yotta.'),
    }

    _MAX_CLUSTER_NAME_LEN_LIMIT = 255
    _regions: List[clouds.Region] = []

    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT
    OPEN_PORTS_VERSION = clouds.OpenPortsVersion.LAUNCH_ONLY

    @classmethod
    def _unsupported_features_for_resources(
        cls,
        resources: 'resources_lib.Resources',
        region: Optional[str] = None,
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        """The features not supported based on the resources provided.

        This method is used by check_features_are_supported() to check if the
        cloud implementation supports all the requested features.

        Returns:
            A dict of {feature: reason} for the features not supported by the
            cloud implementation.
        """
        del resources  # unused
        return cls._CLOUD_UNSUPPORTED_FEATURES

    @classmethod
    def _max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    @classmethod
    def regions_with_offering(
        cls,
        instance_type: str,
        accelerators: Optional[Dict[str, int]],
        use_spot: bool,
        region: Optional[str],
        zone: Optional[str],
        resources: Optional['resources_lib.Resources'] = None,
    ) -> List[clouds.Region]:
        del accelerators  # unused
        regions = catalog.get_region_zones_for_instance_type(
            instance_type, use_spot, _CLOUD)

        if region is not None:
            regions = [r for r in regions if r.name == region]

        if zone is not None:
            for r in regions:
                assert r.zones is not None, r
                r.set_zones([z for z in r.zones if z.name == zone])
            regions = [r for r in regions if r.zones]
        return regions

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        return catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                        clouds=_CLOUD)

    @classmethod
    def zones_provision_loop(
        cls,
        *,
        region: str,
        num_nodes: int,
        instance_type: str,
        accelerators: Optional[Dict[str, int]] = None,
        use_spot: bool = False,
    ) -> Iterator[Optional[List['clouds.Zone']]]:
        del num_nodes  # unused
        regions = cls.regions_with_offering(instance_type,
                                            accelerators,
                                            use_spot,
                                            region=region,
                                            zone=None)
        for r in regions:
            assert r
            yield r.zones

    def instance_type_to_hourly_cost(self,
                                     instance_type: str,
                                     use_spot: bool,
                                     region: Optional[str] = None,
                                     zone: Optional[str] = None) -> float:
        return catalog.get_hourly_cost(instance_type,
                                       use_spot=use_spot,
                                       region=region,
                                       zone=zone,
                                       clouds=_CLOUD)

    def accelerators_to_hourly_cost(self,
                                    accelerators: Dict[str, int],
                                    use_spot: bool,
                                    region: Optional[str] = None,
                                    zone: Optional[str] = None) -> float:
        """Returns the hourly cost of the accelerators, in dollars/hour."""
        del accelerators, use_spot, region, zone  # unused
        return 0.0  # Yotta includes accelerators in the hourly cost.

    def get_egress_cost(self, num_gigabytes: float) -> float:
        return 0.0

    @classmethod
    def get_default_instance_type(cls,
                                  cpus: Optional[str] = None,
                                  memory: Optional[str] = None,
                                  disk_tier: Optional[
                                      resources_utils.DiskTier] = None,
                                  local_disk: Optional[str] = None,
                                  region: Optional[str] = None,
                                  zone: Optional[str] = None) -> Optional[str]:
        """Returns the default instance type for Yotta."""
        return catalog.get_default_instance_type(cpus=cpus,
                                                 memory=memory,
                                                 disk_tier=disk_tier,
                                                 local_disk=local_disk,
                                                 region=region,
                                                 zone=zone,
                                                 clouds=_CLOUD)

    @classmethod
    def get_accelerators_from_instance_type(
            cls, instance_type: str) -> Optional[Dict[str, Union[int, float]]]:
        return catalog.get_accelerators_from_instance_type(instance_type,
                                                           clouds=_CLOUD)

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    def make_deploy_resources_variables(
        self,
        resources: 'resources_lib.Resources',
        cluster_name: resources_utils.ClusterName,
        region: 'clouds.Region',
        zones: Optional[List['clouds.Zone']],
        num_nodes: int,
        dryrun: bool = False,
        volume_mounts: Optional[List['volume_lib.VolumeMount']] = None,
    ) -> Dict[str, Any]:
        del dryrun, cluster_name, zones, num_nodes  # unused
        resources = resources.assert_launchable()
        acc_dict = self.get_accelerators_from_instance_type(
            resources.instance_type)
        custom_resources = resources_utils.make_ray_custom_resources_str(
            acc_dict)

        if resources.image_id is None:
            image_id: Optional[str] = _BASE_IMAGE
        elif resources.extract_docker_image() is not None:
            image_id = resources.extract_docker_image()
        else:
            image_id = resources.image_id[resources.region]

        instance_type = resources.instance_type
        use_spot = resources.use_spot
        hourly_cost = self.instance_type_to_hourly_cost(
            instance_type=instance_type, use_spot=use_spot)

        return {
            'instance_type': instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
            'image_id': image_id,
            'use_spot': use_spot,
            'bid_per_gpu': str(hourly_cost),
            'docker_login_config': resources.docker_login_config,
        }

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> 'resources_utils.FeasibleResources':
        """Returns a list of feasible resources for the given resources."""
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            resources = resources.copy(accelerators=None)
            return resources_utils.FeasibleResources([resources], [], None)

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                r = resources.copy(
                    cloud=Yotta(),
                    instance_type=instance_type,
                    accelerators=None,
                    cpus=None,
                )
                resource_list.append(r)
            return resource_list

        # Currently, handle a filter on accelerators only.
        accelerators = resources.accelerators
        if accelerators is None:
            # Return a default instance type
            default_instance_type = Yotta.get_default_instance_type(
                cpus=resources.cpus,
                memory=resources.memory,
                disk_tier=resources.disk_tier,
                local_disk=resources.local_disk,
                region=resources.region,
                zone=resources.zone)
            if default_instance_type is None:
                # TODO: Add hints to all return values in this method to help
                #  users understand why the resources are not launchable.
                return resources_utils.FeasibleResources([], [], None)
            else:
                return resources_utils.FeasibleResources(
                    _make([default_instance_type]), [], None)

        assert len(accelerators) == 1, resources
        acc, acc_count = list(accelerators.items())[0]
        (instance_list,
         fuzzy_candidate_list) = catalog.get_instance_type_for_accelerator(
             acc,
             acc_count,
             use_spot=resources.use_spot,
             cpus=resources.cpus,
             local_disk=resources.local_disk,
             region=resources.region,
             zone=resources.zone,
             clouds=_CLOUD)
        if instance_list is None:
            return resources_utils.FeasibleResources([], fuzzy_candidate_list,
                                                     None)
        return resources_utils.FeasibleResources(_make(instance_list),
                                                 fuzzy_candidate_list, None)

    @classmethod
    def _check_compute_credentials(cls) -> Tuple[bool, Optional[str]]:
        """Checks if the user has access credentials to
        Yotta's compute service."""
        msg = ('Failed to access Yotta Cloud with credentials. '
               'To configure credentials, go to:\n    '
               '  https://console.yottalabs.ai \n    '
               'to obtain an API key, then add save the contents '
               f'to {CREDENTIAL_FILE} \n')
        if not os.path.exists(os.path.expanduser(CREDENTIAL_FILE)):
            return False, msg

        try:
            valid = yotta_client.check_api_key()
            if not valid:
                return False, msg
            return True, None
        except Exception as e:  # pylint: disable=broad-except
            return False, str(e)

    def get_credential_file_mounts(self) -> Dict[str, str]:
        return {CREDENTIAL_FILE: CREDENTIAL_FILE}

    @classmethod
    def get_user_identities(cls) -> Optional[List[List[str]]]:
        # NOTE: used for very advanced SkyPilot functionality
        # Can implement later if desired
        return None

    def instance_type_exists(self, instance_type: str) -> bool:
        return catalog.instance_type_exists(instance_type, _CLOUD)

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return catalog.validate_region_zone(region, zone, clouds=_CLOUD)

    @classmethod
    def get_image_size(cls, image_id: str, region: Optional[str]) -> float:
        # TODO: use 0.0 for now to allow all images. We should change this to
        # return the docker image size.
        del image_id, region  # unused
        return 0.0
