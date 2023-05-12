"""
Oracle Cloud Infrastructure (OCI)

History:
 - Hysun He (hysun.he@oracle.com) @ Apr, 2023: Initial implementation
 - Hysun He (hysun.he@oracle.com) @ May 4, 2023: Support use the default
   image_id (configurable) if no image_id specified in the task yaml.
"""
import json
import typing
import logging
from typing import Dict, Iterator, List, Optional, Tuple

from sky import clouds
from sky.clouds import service_catalog
from sky import exceptions
from sky.adaptors import oci as oci_adaptor
from sky.skylet.providers.oci.config import oci_conf
from sky.skylet.providers.oci import utils as oci_utils

if typing.TYPE_CHECKING:
    # Renaming to avoid shadowing variables.
    from sky import resources as resources_lib

logger = logging.getLogger(__name__)


@clouds.CLOUD_REGISTRY.register
class OCI(clouds.Cloud):
    """ OCI: Oracle Cloud Infrastructure """

    _REPR = 'OCI'

    _MAX_CLUSTER_NAME_LEN_LIMIT = 200

    _regions: List[clouds.Region] = []

    @classmethod
    def _cloud_unsupported_features(
            cls) -> Dict[clouds.CloudImplementationFeatures, str]:
        return dict()

    @classmethod
    def _max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    @classmethod
    def regions_with_offering(cls, instance_type: str,
                              accelerators: Optional[Dict[str, int]],
                              use_spot: bool, region: Optional[str],
                              zone: Optional[str]) -> List[clouds.Region]:
        del accelerators  # unused

        regions = service_catalog.get_region_zones_for_instance_type(
            instance_type, use_spot, 'oci')

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
        return service_catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                                clouds='oci')

    @classmethod
    def zones_provision_loop(
        cls,
        *,
        region: str,
        num_nodes: int,
        instance_type: str,
        accelerators: Optional[Dict[str, int]] = None,
        use_spot: bool = False,
    ) -> Iterator[List[clouds.Zone]]:
        del num_nodes  # unused
        regions = cls.regions_with_offering(instance_type,
                                            accelerators,
                                            use_spot,
                                            region=region,
                                            zone=None)
        for r in regions:
            assert r.zones is not None, r
            for zone in r.zones:
                yield [zone]

    def instance_type_to_hourly_cost(self,
                                     instance_type: str,
                                     use_spot: bool,
                                     region: Optional[str] = None,
                                     zone: Optional[str] = None) -> float:
        return service_catalog.get_hourly_cost(instance_type,
                                               use_spot=use_spot,
                                               region=region,
                                               zone=zone,
                                               clouds='oci')

    def accelerators_to_hourly_cost(self,
                                    accelerators: Dict[str, int],
                                    use_spot: bool,
                                    region: Optional[str] = None,
                                    zone: Optional[str] = None) -> float:
        del accelerators, use_spot, region, zone  # unused
        return 0.0

    def get_egress_cost(self, num_gigabytes: float) -> float:
        """
        https://www.oracle.com/cis/cloud/networking/pricing/
        """
        # Free for first 10T (per month)
        if num_gigabytes <= 10 * 1024:
            return 0.0

        # We need to calculate the egress cost by region.
        # Fortunately, most of time, this cost looks not a
        # big deal comparing to the price of GPU instances.
        # # Calculate cost for over 10T (per month)
        # logger.debug(f"* get_egress_cost. region {region}")
        # if region in REGIONS_NorthAmerica_Europe_UK:
        #     return (num_gigabytes - 10 * 1024) * 0.0085
        # elif region in REGIONS_JAPAC_SouthAmerica:
        #     return (num_gigabytes - 10 * 1024) * 0.025
        # elif region in REGIONS_MidEast_Africa:
        #     return (num_gigabytes - 10 * 1024) * 0.05
        # else:
        #     logger.debug(f"* ! Region {region} is not listed for cost calc!")
        #     return 0.0
        return (num_gigabytes - 10 * 1024) * 0.0085

    def __repr__(self):
        return 'oci'

    def is_same_cloud(self, other: clouds.Cloud) -> bool:
        # Returns true if the two clouds are the same cloud type.
        return isinstance(other, OCI)

    @classmethod
    def get_default_instance_type(
            cls,
            cpus: Optional[str] = None,
            memory: Optional[str] = None,
            disk_tier: Optional[str] = None) -> Optional[str]:
        return service_catalog.get_default_instance_type(cpus=cpus,
                                                         memory=memory,
                                                         disk_tier=disk_tier,
                                                         clouds='oci')

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, int]]:
        return service_catalog.get_accelerators_from_instance_type(
            instance_type, clouds='oci')

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    def make_deploy_resources_variables(
            self, resources: 'resources_lib.Resources',
            region: Optional['clouds.Region'],
            zones: Optional[List['clouds.Zone']]) -> Dict[str, Optional[str]]:
        assert region is not None, resources

        r = resources
        acc_dict = self.get_accelerators_from_instance_type(r.instance_type)
        if acc_dict is not None:
            custom_resources = json.dumps(acc_dict, separators=(',', ':'))
        else:
            custom_resources = None

        image_str = self._get_image_id(resources.image_id, region.name,
                                       r.instance_type)
        image_cols = image_str.split(oci_conf.IMAGE_TAG_SPERATOR)
        if len(image_cols) == 3:
            image_id = image_cols[0]
            listing_id = image_cols[1]
            res_ver = image_cols[2]
        else:
            image_id = resources.image_id
            listing_id = None
            res_ver = None

        cpus = resources.cpus
        if resources.instance_type.startswith(oci_conf.VM_PREFIX):
            cpus = f'{oci_conf.DEFAULT_NUM_VCPUS}' if cpus is None else cpus

        zone = resources.zone
        # TODO(Hysun): error-and-try all zones.
        if zone is None:
            # If zone is not specified, try to get the first zone.
            if zones is None:
                regions = service_catalog.get_region_zones_for_instance_type(
                    instance_type=resources.instance_type,
                    use_spot=resources.use_spot,
                    clouds='oci')
                zones = [r for r in iter(regions) if r.name == region.name
                        ][0].zones

            if zones is not None:
                zone = zones[0].name

        instance_type = resources.instance_type.split(
            oci_conf.INSTANCE_TYPE_RES_SPERATOR)[0]

        return {
            'instance_type': instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
            'cpus': cpus,
            'memory': resources.memory,
            'zone': zone,
            'image': image_id,
            'app_catalog_listing_id': listing_id,
            'resource_version': res_ver,
            'use_spot': resources.use_spot
        }

    def get_feasible_launchable_resources(self,
                                          resources: 'resources_lib.Resources'):
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            resources = resources.copy(accelerators=None)
            return ([resources], [])

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                r = resources.copy(
                    cloud=OCI(),
                    instance_type=instance_type,
                    # Setting this to None as OCI doesn't separately bill /
                    # attach the accelerators.  Billed as part of the VM type.
                    accelerators=None,
                    cpus=None,
                    memory=None,
                )
                resource_list.append(r)
            return resource_list

        # Currently, handle a filter on accelerators only.
        accelerators = resources.accelerators
        if accelerators is None:
            # Return a default instance type with the given number of vCPUs.
            default_instance_type = OCI.get_default_instance_type(
                cpus=resources.cpus,
                memory=resources.memory,
                disk_tier=resources.disk_tier)

            if default_instance_type is None:
                return ([], [])
            else:
                return (_make([default_instance_type]), [])

        assert len(accelerators) == 1, resources

        acc, acc_count = list(accelerators.items())[0]
        (instance_list, fuzzy_candidate_list
        ) = service_catalog.get_instance_type_for_accelerator(
            acc,
            acc_count,
            use_spot=resources.use_spot,
            cpus=resources.cpus,
            memory=resources.memory,
            region=resources.region,
            zone=resources.zone,
            clouds='oci')
        if instance_list is None:
            return ([], fuzzy_candidate_list)

        return (_make(instance_list), fuzzy_candidate_list)

    @classmethod
    def check_credentials(cls) -> Tuple[bool, Optional[str]]:
        try:
            user = oci_adaptor.get_identity_client().get_user(
                oci_adaptor.get_oci_config()['user']).data
            logger.info('* check_credentials passed.')
            return True, (f'User name is {user.name}'
                          f'Status is {user.lifecycle_state}')
        except oci_adaptor.service_exception() as e:
            logger.error(f'!!! check_credentials: {str(e)}')
            return False, str(e)

    def get_credential_file_mounts(self) -> Dict[str, str]:
        """Returns a dict of credential file paths to mount paths."""
        return dict()

    @classmethod
    def get_current_user_identity(cls) -> Optional[List[str]]:
        # NOTE: used for very advanced SkyPilot functionality
        # Can implement later if desired
        return None

    def instance_type_exists(self, instance_type: str) -> bool:
        return service_catalog.instance_type_exists(instance_type, 'oci')

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return service_catalog.validate_region_zone(region, zone, clouds='oci')

    def accelerator_in_region_or_zone(self,
                                      accelerator: str,
                                      acc_count: int,
                                      region: Optional[str] = None,
                                      zone: Optional[str] = None) -> bool:
        return service_catalog.accelerator_in_region_or_zone(
            accelerator, acc_count, region, zone, 'oci')

    def get_image_size(self, image_id: str, region: Optional[str]) -> float:
        # We ignore checking the image size because most of situations the
        # boot volume size is larger than the image size. For specific rare
        # situations, the configuration/setup commands should make sure the
        # correct size of the disk.
        return 0

    def _get_image_id(
        self,
        image_id: Optional[Dict[Optional[str], str]],
        region_name: str,
        instance_type: str,
    ) -> str:
        if image_id is None:
            return self._get_default_image(region_name=region_name,
                                           instance_type=instance_type)
        if None in image_id:
            image_id_str = image_id[None]
        else:
            assert region_name in image_id, image_id
            image_id_str = image_id[region_name]
        if image_id_str.startswith('skypilot:'):
            image_id_str = service_catalog.get_image_id_from_tag(image_id_str,
                                                                 region_name,
                                                                 clouds='oci')
            if image_id_str is None:
                logger.critical(
                    '! Real image_id not found! - {region_name}:{image_id}')
                # Raise ResourcesUnavailableError to make sure the failover
                # in CloudVMRayBackend will be correctly triggered.
                # TODO(zhwu): This is a information leakage to the cloud
                # implementor, we need to find a better way to handle this.
                raise exceptions.ResourcesUnavailableError(
                    '! ERR: No image found in catalog for region '
                    f'{region_name}. Try setting a valid image_id.')

        logger.debug(f'* Got real image_id {image_id_str}')
        return image_id_str

    @oci_utils.debug_enabled(logger=logger)
    def _get_default_image(self, region_name: str, instance_type: str) -> str:
        acc = self.get_accelerators_from_instance_type(instance_type)

        if acc is None:
            image_tag = oci_conf.get_default_image_tag()
            image_id_str = service_catalog.get_image_id_from_tag(image_tag,
                                                                 region_name,
                                                                 clouds='oci')
        else:
            assert len(acc) == 1, acc
            image_tag = oci_conf.get_default_gpu_image_tag()
            image_id_str = service_catalog.get_image_id_from_tag(image_tag,
                                                                 region_name,
                                                                 clouds='oci')

        if image_id_str is not None:
            logger.debug(
                f'* Got default image_id {image_id_str} from tag {image_tag}')
            return image_id_str

        # Raise ResourcesUnavailableError to make sure the failover in
        # CloudVMRayBackend will be correctly triggered.
        # TODO(zhwu): This is a information leakage to the cloud implementor,
        # we need to find a better way to handle this.
        raise exceptions.ResourcesUnavailableError(
            '! ERR: No image found in catalog for region '
            f'{region_name}. Try update your default image_id settings.')
