""" CloudRift Cloud Provider. """

import json
import os
import typing
from typing import Dict, Iterator, List, Optional, Tuple, Union

from sky import catalog
from sky import clouds
from sky.provision.cloudrift import utils as cloudrift_utils
from sky.utils import registry
from sky.utils import resources_utils

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib
    from sky.utils import volume as volume_lib

_CREDENTIAL_FILE = 'config.yaml'


@registry.CLOUD_REGISTRY.register(aliases=['cloudrift'])
class CloudRift(clouds.Cloud):
    """CloudRift Cloud Provider"""

    _REPR = 'CloudRift'
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.STOP: 'Stopping not supported.',
        clouds.CloudImplementationFeatures.MULTI_NODE:
            ('Multi-node not supported yet, as the interconnection among nodes '),
        clouds.CloudImplementationFeatures.SPOT_INSTANCE: f'Spot instances are not supported in {_REPR}.',
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
            (f'Customizing disk tier is not supported yet on {_REPR}.'),
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER:
            (f'Custom network tier is not supported yet on {_REPR}.'),
        clouds.CloudImplementationFeatures.STORAGE_MOUNTING:
            (f'Mounting object stores is not supported on {_REPR}. To read data '
             f'from object stores on {_REPR}, use `mode: COPY` to copy the data '
             'to local disk.'),
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS:
            ('High availability controllers are not supported on RunPod.'),
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK:
            ('Customized multiple network interfaces are not supported on '
             'RunPod.'),
    }
    # CloudRift maximum node name length
    _MAX_CLUSTER_NAME_LEN_LIMIT = 255
    _regions: List[clouds.Region] = []

    # Using the latest SkyPilot provisioner API to provision and check status.
    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT

    @classmethod
    def _unsupported_features_for_resources(
        cls, resources: 'resources_lib.Resources'
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
    ) -> List[clouds.Region]:
        assert zone is None, 'CloudRift does not support zones.'
        del zone  # unused
        regions = catalog.get_region_zones_for_instance_type(
            instance_type, use_spot, 'CloudRift')
        if region is not None:
            regions = [r for r in regions if r.name == region]
        return regions

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        return catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                        clouds='CloudRift')

    @classmethod
    def zones_provision_loop(
        cls,
        *,
        region: str,
        num_nodes: int,
        instance_type: str,
        accelerators: Optional[Dict[str, int]] = None,
        use_spot: bool = False,
    ) -> Iterator[None]:
        del num_nodes  # unused
        regions = cls.regions_with_offering(instance_type,
                                            accelerators,
                                            use_spot,
                                            region=region,
                                            zone=None)
        for r in regions:
            assert r.zones is None, r
            yield r.zones

    def instance_type_to_hourly_cost(
        self,
        instance_type: str,
        use_spot: bool,
        region: Optional[str] = None,
        zone: Optional[str] = None,
    ) -> float:
        return catalog.get_hourly_cost(
            instance_type,
            use_spot=use_spot,
            region=region,
            zone=zone,
            clouds='CloudRift',
        )

    def accelerators_to_hourly_cost(
        self,
        accelerators: Dict[str, int],
        use_spot: bool,
        region: Optional[str] = None,
        zone: Optional[str] = None,
    ) -> float:
        """Returns the hourly cost of the accelerators, in dollars/hour."""
        # The accelerator price is included in the instance price.
        del accelerators, use_spot, region, zone  # unused
        return 0.0

    def get_egress_cost(self, num_gigabytes: float) -> float:
        # Define CloudRift egress cost here
        return num_gigabytes * 0.01  # Example: $0.01 per GB

    def __repr__(self):
        return self._REPR

    @classmethod
    def get_default_instance_type(cls,
                                  cpus: Optional[str] = None,
                                  memory: Optional[str] = None,
                                  disk_tier: Optional[
                                      resources_utils.DiskTier] = None,
                                  region: Optional[str] = None,
                                  zone: Optional[str] = None) -> Optional[str]:
        """Returns the default instance type for CloudRift."""
        return catalog.get_default_instance_type(cpus=cpus,
                                                 memory=memory,
                                                 disk_tier=disk_tier,
                                                 region=region,
                                                 zone=zone,
                                                 clouds='CloudRift')

    @classmethod
    def get_accelerators_from_instance_type(
            cls, instance_type: str) -> Optional[Dict[str, Union[int, float]]]:
        return catalog.get_accelerators_from_instance_type(instance_type,
                                                           clouds='CloudRift')

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
    ) -> Dict[str, Optional[str]]:
        del zones, dryrun, cluster_name

        resources = resources.assert_launchable()
        acc_dict = self.get_accelerators_from_instance_type(
            resources.instance_type)
        if acc_dict is not None:
            custom_resources = json.dumps(acc_dict, separators=(',', ':'))
        else:
            custom_resources = None
        image_id = None
        if (resources.image_id is not None and
                resources.extract_docker_image() is None):
            if None in resources.image_id:
                image_id = resources.image_id[None]
            else:
                assert region.name in resources.image_id
                image_id = resources.image_id[region.name]
        return {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
            **({
                'image_id': image_id
            } if image_id else {})
        }

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> resources_utils.FeasibleResources:
        """Returns a list of feasible resources for the given resources."""
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            resources = resources.copy(accelerators=None)
            return resources_utils.FeasibleResources([resources], [], None)

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                r = resources.copy(
                    cloud=CloudRift(),
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
            default_instance_type = CloudRift.get_default_instance_type(
                cpus=resources.cpus,
                memory=resources.memory,
                disk_tier=resources.disk_tier,
                region=resources.region,
                zone=resources.zone)
            if default_instance_type is None:
                return resources_utils.FeasibleResources([], [], None)
            else:
                return resources_utils.FeasibleResources(
                    _make([default_instance_type]), [], None)

        assert len(accelerators) == 1, resources
        acc, acc_count = list(accelerators.items())[0]
        (instance_list,
         fuzzy_candidate_list) = (catalog.get_instance_type_for_accelerator(
             acc,
             acc_count,
             use_spot=resources.use_spot,
             cpus=resources.cpus,
             memory=resources.memory,
             region=resources.region,
             zone=resources.zone,
             clouds='CloudRift',
         ))
        if instance_list is None:
            return resources_utils.FeasibleResources([], fuzzy_candidate_list,
                                                     None)
        return resources_utils.FeasibleResources(_make(instance_list),
                                                 fuzzy_candidate_list, None)

    @classmethod
    def _check_compute_credentials(
            cls) -> Tuple[bool, Optional[Union[str, Dict[str, str]]]]:
        """Verify that the user has valid credentials for
        CloudRift's compute service."""
        
        # installed, err_msg = cloudrift.check_exceptions_dependencies_installed()
        # if not installed:
        #     return False, err_msg

        try:
            # attempt to make a request for listing instances
            cloudrift_utils.client().instances.list()
        except cloudrift_utils.CloudRiftError as err:
            return False, str(err)
        except cloudrift.exceptions().HttpResponseError as err:
            return False, str(err)

        return True, None

    def get_credential_file_mounts(self) -> Dict[str, str]:
        credential_path = cloudrift_utils.get_credentials_path()
        if credential_path is None:
            return {}
        if not os.path.exists(os.path.expanduser(credential_path)):
            return {}
        return {f'~/.config/cloudrift/{_CREDENTIAL_FILE}': credential_path}

    @classmethod
    def get_current_user_identity(cls) -> Optional[List[str]]:
        # Implementation for retrieving current user identity
        return None

    @classmethod
    def get_image_size(cls, image_id: str, region: Optional[str]) -> float:
        # TODO: Implement image size retrieval for CloudRift
        # For now, return default size
        del image_id, region
        return 10.0  # Default size in GB

    def instance_type_exists(self, instance_type: str) -> bool:
        return catalog.instance_type_exists(instance_type, 'CloudRift')

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return catalog.validate_region_zone(region, zone, clouds='CloudRift')
