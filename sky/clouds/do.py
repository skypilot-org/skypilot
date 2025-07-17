""" Digital Ocean Cloud. """

import json
import os
import typing
from typing import Dict, Iterator, List, Optional, Tuple, Union

from sky import catalog
from sky import clouds
from sky.adaptors import do
from sky.provision.do import utils as do_utils
from sky.utils import registry
from sky.utils import resources_utils

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib
    from sky.volumes import volume as volume_lib

_CREDENTIAL_FILE = 'config.yaml'


@registry.CLOUD_REGISTRY.register(aliases=['digitalocean'])
class DO(clouds.Cloud):
    """Digital Ocean Cloud"""

    _REPR = 'DO'
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER:
            'Migrating '
            f'disk is not supported in {_REPR}.',
        clouds.CloudImplementationFeatures.SPOT_INSTANCE:
            'Spot instances are '
            f'not supported in {_REPR}.',
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
            'Custom disk tiers'
            f' is not supported in {_REPR}.',
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER:
            ('Custom network tier is currently not supported in '
             f'{_REPR}.'),
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS:
            ('High availability controllers are not supported in '
             f'{_REPR}.'),
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK:
            ('Customized multiple network interfaces are not supported in '
             f'{_REPR}.'),
    }
    # DO maximum node name length defined as <= 255
    # https://docs.digitalocean.com/reference/api/api-reference/#operation/droplets_create
    # 255 - 8 = 247 characters since
    # our provisioner adds additional `-worker`.
    _MAX_CLUSTER_NAME_LEN_LIMIT = 247
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
        assert zone is None, 'DO does not support zones.'
        del accelerators, zone  # unused
        if use_spot:
            return []
        regions = catalog.get_region_zones_for_instance_type(
            instance_type, use_spot, 'DO')
        if region is not None:
            regions = [r for r in regions if r.name == region]
        return regions

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        return catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                        clouds='DO')

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
            clouds='DO',
        )

    def accelerators_to_hourly_cost(
        self,
        accelerators: Dict[str, int],
        use_spot: bool,
        region: Optional[str] = None,
        zone: Optional[str] = None,
    ) -> float:
        """Returns the hourly cost of the accelerators, in dollars/hour."""
        # the acc price is include in the instance price.
        del accelerators, use_spot, region, zone  # unused
        return 0.0

    def get_egress_cost(self, num_gigabytes: float) -> float:
        return 0.0

    def __repr__(self):
        return self._REPR

    @classmethod
    def get_default_instance_type(
        cls,
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        disk_tier: Optional[resources_utils.DiskTier] = None,
    ) -> Optional[str]:
        """Returns the default instance type for DO."""
        return catalog.get_default_instance_type(cpus=cpus,
                                                 memory=memory,
                                                 disk_tier=disk_tier,
                                                 clouds='DO')

    @classmethod
    def get_accelerators_from_instance_type(
            cls, instance_type: str) -> Optional[Dict[str, Union[int, float]]]:
        return catalog.get_accelerators_from_instance_type(instance_type,
                                                           clouds='DO')

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
        if resources.use_spot:
            # TODO: Add hints to all return values in this method to help
            #  users understand why the resources are not launchable.
            return resources_utils.FeasibleResources([], [], None)
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            resources = resources.copy(accelerators=None)
            return resources_utils.FeasibleResources([resources], [], None)

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                r = resources.copy(
                    cloud=DO(),
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
            default_instance_type = DO.get_default_instance_type(
                cpus=resources.cpus,
                memory=resources.memory,
                disk_tier=resources.disk_tier)
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
             clouds='DO',
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
        DO's compute service."""

        try:
            do.exceptions()
        except ImportError as err:
            return False, str(err)

        try:
            # attempt to make a CURL request for listing instances
            do_utils.client().droplets.list()
        except do.exceptions().HttpResponseError as err:
            return False, str(err)
        except do_utils.DigitalOceanError as err:
            return False, str(err)

        return True, None

    def get_credential_file_mounts(self) -> Dict[str, str]:
        credential_path = do_utils.get_credentials_path()
        if credential_path is None:
            return {}
        if not os.path.exists(os.path.expanduser(credential_path)):
            return {}
        return {f'~/.config/doctl/{_CREDENTIAL_FILE}': credential_path}

    @classmethod
    def get_current_user_identity(cls) -> Optional[List[str]]:
        # NOTE: used for very advanced SkyPilot functionality
        # Can implement later if desired
        return None

    @classmethod
    def get_image_size(cls, image_id: str, region: Optional[str]) -> float:
        del region
        try:
            response = do_utils.client().images.get(image_id=image_id)
            return response['image']['size_gigabytes']
        except do.exceptions().HttpResponseError as err:
            raise do_utils.DigitalOceanError(
                'HTTP error while retrieving size of '
                f'image_id {response}: {err.error.message}') from err
        except KeyError as err:
            raise do_utils.DigitalOceanError(
                f'No image_id `{image_id}` found') from err

    def instance_type_exists(self, instance_type: str) -> bool:
        return catalog.instance_type_exists(instance_type, 'DO')

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return catalog.validate_region_zone(region, zone, clouds='DO')
