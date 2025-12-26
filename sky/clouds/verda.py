"""Verda Cloud."""

import os
import typing
from typing import Dict, Iterator, List, Optional, Tuple, Union

from sky import catalog
from sky import clouds
from sky.adaptors.verda import get_verda_configuration
from sky.utils import registry
from sky.utils import resources_utils

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib
    from sky.utils import volume as volume_lib

# Default images for Verda Cloud
# These images are provided by Verda and include CUDA drivers
_DEFAULT_IMAGE = 'ubuntu-24.04-cuda-12.8-open-docker'


@registry.CLOUD_REGISTRY.register
class Verda(clouds.Cloud):
    """Verda Cloud

    _REPR | The string representation for the Verda Cloud object.
    """

    _REPR = 'Verda'
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.STOP: 'Stopping not supported.',
        clouds.CloudImplementationFeatures.MULTI_NODE:
            ('Multi-node not supported yet, as the interconnection among nodes '
             'are non-trivial on Verda.'),
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
            ('Customizing disk tier is not supported yet on Verda.'),
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER:
            ('Custom network tier is not supported yet on Verda.'),
        clouds.CloudImplementationFeatures.STORAGE_MOUNTING:
            ('Mounting object stores is not supported on Verda. To read data '
             'from object stores on Verda, use `mode: COPY` to copy the data '
             'to local disk.'),
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS:
            ('High availability controllers are not supported on Verda.'),
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK: (
            'Customized multiple network interfaces are not supported on Verda.'
        ),
    }
    _MAX_CLUSTER_NAME_LEN_LIMIT = 120
    _MAX_VOLUME_NAME_LEN_LIMIT = 30
    CREDENTIALS_PATH = os.path.expanduser('~/.verda/config.json')
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
        unsupported_features = cls._CLOUD_UNSUPPORTED_FEATURES.copy()

        # Hide MULTI_NODE feature behind experimental flag
        if os.getenv('SKYPILOT_EXPERIMENTAL_VERDA_MULTI_NODE', '') == '1':
            unsupported_features.pop(
                clouds.CloudImplementationFeatures.MULTI_NODE, None)

        return unsupported_features

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
        assert zone is None, 'Verda does not support zones.'
        del accelerators, zone  # unused
        regions = catalog.get_region_zones_for_instance_type(
            instance_type, use_spot, 'verda')
        if region is not None:
            regions = [r for r in regions if r.name == region]
        return regions

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        return catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                        clouds='verda')

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
            clouds='verda',
        )

    def accelerators_to_hourly_cost(
        self,
        accelerators: Dict[str, int],
        use_spot: bool,
        region: Optional[str] = None,
        zone: Optional[str] = None,
    ) -> float:
        """Returns the hourly cost of the accelerators, in dollars/hour."""
        del accelerators, use_spot, region, zone  # unused
        return 0.0  # Verda includes accelerators in the hourly cost.

    def get_egress_cost(self, num_gigabytes: float) -> float:
        return 0.0

    @classmethod
    def get_default_instance_type(
        cls,
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        disk_tier: Optional[resources_utils.DiskTier] = None,
        region: Optional[str] = None,
        zone: Optional[str] = None,
    ) -> Optional[str]:
        """Returns the default instance type for Verda."""
        return catalog.get_default_instance_type(
            cpus=cpus,
            memory=memory,
            disk_tier=disk_tier,
            region=region,
            zone=zone,
            clouds='verda',
        )

    @classmethod
    def get_accelerators_from_instance_type(
            cls, instance_type: str) -> Optional[Dict[str, Union[int, float]]]:
        return catalog.get_accelerators_from_instance_type(instance_type,
                                                           clouds='verda')

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
    ) -> Dict[str, Optional[Union[str, bool]]]:
        del zones, dryrun, cluster_name  # unused

        if num_nodes > 1:
            raise ValueError('Verda currently only supports one node, '
                             f'but {num_nodes} nodes were requested.')

        resources = resources.assert_launchable()
        acc_dict = self.get_accelerators_from_instance_type(
            resources.instance_type)
        custom_resources = resources_utils.make_ray_custom_resources_str(
            acc_dict)

        image_id: str = _DEFAULT_IMAGE

        # Image selection logic
        if resources.image_id is None:
            image_id_override = os.getenv('SKYPILOT_VERDA_IMAGE_ID', '')

            if image_id_override:
                image_id = image_id_override
        elif resources.extract_docker_image() is not None:
            # Docker image specified
            raise ValueError('Docker images are not supported on Verda.')
        else:
            # User specified an image_id
            if isinstance(resources.image_id, dict):
                # Region-specific image or region-agnostic image
                if None in resources.image_id:
                    # Region-agnostic image
                    image_id = resources.image_id[None]
                elif resources.region in resources.image_id:
                    # Region-specific image
                    image_id = resources.image_id[resources.region]
                else:
                    # Fallback to default if region not in dict
                    image_id = _DEFAULT_IMAGE
            else:
                # Direct string image_id
                image_id = resources.image_id

        instance_type = resources.instance_type
        use_spot = resources.use_spot

        return {
            'instance_type': instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
            'image_id': image_id,
            'use_spot': use_spot,
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
                    cloud=Verda(),
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
            default_instance_type = Verda.get_default_instance_type(
                cpus=resources.cpus,
                memory=resources.memory,
                disk_tier=resources.disk_tier,
                region=resources.region,
                zone=resources.zone,
            )
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
         fuzzy_candidate_list) = (catalog.get_instance_type_for_accelerator(
             acc,
             acc_count,
             use_spot=resources.use_spot,
             cpus=resources.cpus,
             region=resources.region,
             zone=resources.zone,
             clouds='verda',
         ))
        if instance_list is None:
            return resources_utils.FeasibleResources([], fuzzy_candidate_list,
                                                     None)
        return resources_utils.FeasibleResources(_make(instance_list),
                                                 fuzzy_candidate_list, None)

    @classmethod
    def check_credentials(
        cls, cloud_capability: clouds.CloudCapability
    ) -> Tuple[bool, Optional[Union[str, Dict[str, str]]]]:
        """Check if Verda Cloud credentials are properly configured."""
        del cloud_capability  # unused
        configured, error, _ = get_verda_configuration()
        return configured, error

    @property
    def name(self):
        return 'verda'

    def get_credential_file_mounts(self) -> Dict[str, str]:
        if os.path.exists(self.CREDENTIALS_PATH):
            return {f'{self.CREDENTIALS_PATH}': '~/.verda/config.json'}
        return {}

    @classmethod
    def get_user_identities(cls) -> Optional[List[List[str]]]:
        # NOTE: used for very advanced SkyPilot functionality
        # Can implement later if desired
        return None

    def instance_type_exists(self, instance_type: str) -> bool:
        return catalog.instance_type_exists(instance_type, 'verda')

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        if zone is not None:
            raise ValueError('Verda does not support zones.')
        return catalog.validate_region_zone(region, zone, clouds='verda')

    @classmethod
    def get_image_size(cls, image_id: str, region: Optional[str]) -> float:
        # TODO: use 0.0 for now to allow all images. We should change this to
        # return the docker image size.
        return 0.0

    @classmethod
    def is_volume_name_valid(cls,
                             volume_name: str) -> Tuple[bool, Optional[str]]:
        """Validates that the volume name is valid for this cloud.

        - must be <= 30 characters
        """
        if len(volume_name) > cls._MAX_VOLUME_NAME_LEN_LIMIT:
            return (
                False,
                f'Volume name exceeds the maximum length of '
                f'{cls._MAX_VOLUME_NAME_LEN_LIMIT} characters.',
            )
        return True, None
