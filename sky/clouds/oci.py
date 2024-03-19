"""Oracle Cloud Infrastructure (OCI)

History:
 - Hysun He (hysun.he@oracle.com) @ Apr, 2023: Initial implementation
 - Hysun He (hysun.he@oracle.com) @ May 4, 2023: Support use the default
   image_id (configurable) if no image_id specified in the task yaml.
"""
import json
import logging
import os
import typing
from typing import Dict, Iterator, List, Optional, Tuple

from sky import clouds
from sky import exceptions
from sky import status_lib
from sky.adaptors import oci as oci_adaptor
from sky.clouds import service_catalog
from sky.clouds.utils import oci_utils
from sky.utils import common_utils
from sky.utils import resources_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    # Renaming to avoid shadowing variables.
    from sky import resources as resources_lib

logger = logging.getLogger(__name__)

_tenancy_prefix: Optional[str] = None


@clouds.CLOUD_REGISTRY.register
class OCI(clouds.Cloud):
    """OCI: Oracle Cloud Infrastructure """

    _REPR = 'OCI'

    _MAX_CLUSTER_NAME_LEN_LIMIT = 200

    _regions: List[clouds.Region] = []

    _INDENT_PREFIX = '    '

    _SUPPORTED_DISK_TIERS = set(resources_utils.DiskTier)

    @classmethod
    def _unsupported_features_for_resources(
        cls, resources: 'resources_lib.Resources'
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        features = {
            clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER:
                (f'Migrating disk is currently not supported on {cls._REPR}.'),
            clouds.CloudImplementationFeatures.DOCKER_IMAGE:
                (f'Docker image is currently not supported on {cls._REPR}. '
                 'You can try running docker command inside the '
                 '`run` section in task.yaml.'),
            clouds.CloudImplementationFeatures.OPEN_PORTS:
                (f'Opening ports is currently not supported on {cls._REPR}.'),
        }
        if resources.use_spot:
            features[clouds.CloudImplementationFeatures.STOP] = (
                f'Stopping spot instances is currently not supported on '
                f'{cls._REPR}.')
        return features

    @classmethod
    def max_cluster_name_length(cls) -> Optional[int]:
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
        """Get the egress cost for the given number of gigabytes.

        Reference: https://www.oracle.com/cis/cloud/networking/pricing/
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

    def is_same_cloud(self, other: clouds.Cloud) -> bool:
        # Returns true if the two clouds are the same cloud type.
        return isinstance(other, OCI)

    @classmethod
    def get_default_instance_type(
            cls,
            cpus: Optional[str] = None,
            memory: Optional[str] = None,
            disk_tier: Optional[resources_utils.DiskTier] = None
    ) -> Optional[str]:
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
            self,
            resources: 'resources_lib.Resources',
            cluster_name_on_cloud: str,
            region: Optional['clouds.Region'],
            zones: Optional[List['clouds.Zone']],
            dryrun: bool = False) -> Dict[str, Optional[str]]:
        del cluster_name_on_cloud, dryrun  # Unused.
        assert region is not None, resources

        acc_dict = self.get_accelerators_from_instance_type(
            resources.instance_type)
        if acc_dict is not None:
            custom_resources = json.dumps(acc_dict, separators=(',', ':'))
        else:
            custom_resources = None

        image_str = self._get_image_id(resources.image_id, region.name,
                                       resources.instance_type)
        image_cols = image_str.split(oci_utils.oci_config.IMAGE_TAG_SPERATOR)
        if len(image_cols) == 3:
            image_id = image_cols[0]
            listing_id = image_cols[1]
            res_ver = image_cols[2]
        else:
            image_id = resources.image_id
            listing_id = None
            res_ver = None

        cpus = resources.cpus
        instance_type_arr = resources.instance_type.split(
            oci_utils.oci_config.INSTANCE_TYPE_RES_SPERATOR)
        instance_type = instance_type_arr[0]

        # Improvement:
        # Fault-tolerant to the catalog file: special shapes does
        # not need cpu/memory configuration, so ignore these info
        # from the catalog file to avoid inconsistence (mainly due
        # to the shape changed in future.)
        if len(instance_type_arr) < 2:
            cpus = None
        else:
            if cpus is None:
                cpus, mems = OCI.get_vcpus_mem_from_instance_type(
                    resources.instance_type)
                resources = resources.copy(
                    cpus=cpus,
                    memory=mems,
                )
            if cpus is None and resources.instance_type.startswith(
                    oci_utils.oci_config.VM_PREFIX):
                cpus = f'{oci_utils.oci_config.DEFAULT_NUM_VCPUS}'

        zone = resources.zone
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

        global _tenancy_prefix
        if _tenancy_prefix is None:
            try:
                identity_client = oci_adaptor.get_identity_client(
                    region=region.name,
                    profile=oci_utils.oci_config.get_profile())

                ad_list = identity_client.list_availability_domains(
                    compartment_id=oci_adaptor.get_oci_config(
                        profile=oci_utils.oci_config.get_profile())
                    ['tenancy']).data

                first_ad = ad_list[0]
                _tenancy_prefix = str(first_ad.name).split(':', maxsplit=1)[0]
            except (oci_adaptor.get_oci().exceptions.ConfigFileNotFound,
                    oci_adaptor.get_oci().exceptions.InvalidConfig) as e:
                # This should only happen in testing where oci config is
                # monkeypatched. In real use, if the OCI config is not
                # valid, the 'sky check' would fail (OCI disabled).
                logger.debug(f'It is OK goes here when testing: {str(e)}')
                pass

        # Disk performane: Volume Performance Units.
        vpu = self.get_vpu_from_disktier(
            cpus=None if cpus is None else float(cpus),
            disk_tier=resources.disk_tier)

        return {
            'instance_type': instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
            'cpus': str(cpus),
            'memory': resources.memory,
            'disk_size': resources.disk_size,
            'vpu': str(vpu),
            'zone': f'{_tenancy_prefix}:{zone}',
            'image': image_id,
            'app_catalog_listing_id': listing_id,
            'resource_version': res_ver,
            'use_spot': resources.use_spot
        }

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> Tuple[List['resources_lib.Resources'], List[str]]:
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
        """Checks if the user has access credentials to this cloud."""

        short_credential_help_str = (
            'For more details, refer to: '
            # pylint: disable=line-too-long
            'https://skypilot.readthedocs.io/en/latest/getting-started/installation.html#oracle-cloud-infrastructure-oci'
        )
        credential_help_str = (
            'To configure credentials, go to: '
            'https://docs.oracle.com/en-us/iaas/Content/API/Concepts/'
            'apisigningkey.htm\n'
            f'{cls._INDENT_PREFIX}Please make sure the API keys and the config '
            'files are placed under ~/.oci:\n'
            f'{cls._INDENT_PREFIX}  ~/.oci/config\n'
            f'{cls._INDENT_PREFIX}  ~/.oci/oci_api_key.pem\n'
            f'{cls._INDENT_PREFIX}The ~/.oci/config file should have the '
            'following format:\n'
            f'{cls._INDENT_PREFIX}  [DEFAULT]\n'
            f'{cls._INDENT_PREFIX}  user=ocid1.user.oc1..aaaaaaaa\n'
            f'{cls._INDENT_PREFIX}  '
            'fingerprint=aa:bb:cc:dd:ee:ff:gg:hh:ii:jj:kk:ll:mm:nn:oo:pp\n'
            f'{cls._INDENT_PREFIX}  tenancy=ocid1.tenancy.oc1..aaaaaaaa\n'
            f'{cls._INDENT_PREFIX}  region=us-sanjose-1\n'
            f'{cls._INDENT_PREFIX}  key_file=~/.oci/oci_api_key.pem')

        try:
            # pylint: disable=import-outside-toplevel,unused-import
            import oci
        except ImportError:
            return False, ('`oci` is not installed. Install it with: '
                           'pip install oci\n'
                           f'{cls._INDENT_PREFIX}{short_credential_help_str}')

        conf_file = oci_adaptor.get_config_file()

        help_str = (f'Missing credential file at {conf_file}. '
                    f'{short_credential_help_str}')
        if not os.path.isfile(os.path.expanduser(conf_file)):
            return (False, help_str)

        try:
            user = oci_adaptor.get_identity_client(
                region=None,
                profile=oci_utils.oci_config.get_profile()).get_user(
                    oci_adaptor.get_oci_config(profile=oci_utils.oci_config.
                                               get_profile())['user']).data
            del user
            # TODO[Hysun]: More privilege check can be added
            return True, None
        except (oci_adaptor.get_oci().exceptions.ConfigFileNotFound,
                oci_adaptor.get_oci().exceptions.InvalidConfig,
                oci_adaptor.service_exception()) as e:
            return False, (
                f'OCI credential is not correctly set. '
                f'Check the credential file at {conf_file}\n'
                f'{cls._INDENT_PREFIX}{credential_help_str}\n'
                f'{cls._INDENT_PREFIX}Error details: '
                f'{common_utils.format_exception(e, use_bracket=True)}')

    def get_credential_file_mounts(self) -> Dict[str, str]:
        """Returns a dict of credential file paths to mount paths."""
        oci_cfg_file = oci_adaptor.get_config_file()
        # Pass-in a profile parameter so that multiple profile in oci
        # config file is supported (2023/06/09).
        oci_cfg = oci_adaptor.get_oci_config(
            profile=oci_utils.oci_config.get_profile())
        api_key_file = oci_cfg[
            'key_file'] if 'key_file' in oci_cfg else 'BadConf'
        sky_cfg_file = oci_utils.oci_config.get_sky_user_config_file()

        # OCI config and API key file are mandatory
        credential_files = [oci_cfg_file, api_key_file]

        # Sky config file is optional
        if os.path.exists(sky_cfg_file):
            credential_files.append(sky_cfg_file)

        file_mounts = {
            f'{filename}': f'{filename}' for filename in credential_files
        }

        logger.debug(f'OCI credential file mounts: {file_mounts}')
        return file_mounts

    @classmethod
    def get_current_user_identity(cls) -> Optional[List[str]]:
        # NOTE: used for very advanced SkyPilot functionality
        # Can implement later if desired
        # If the user switches the compartment_ocid, the existing clusters
        # might be leaked, as `sky status -r` will fail to find the original
        # clusters in the new compartment and the identity check is missing.
        return None

    def instance_type_exists(self, instance_type: str) -> bool:
        return service_catalog.instance_type_exists(instance_type, 'oci')

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return service_catalog.validate_region_zone(region, zone, clouds='oci')

    @classmethod
    def get_image_size(cls, image_id: str, region: Optional[str]) -> float:
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

        logger.debug(f'Got real image_id {image_id_str}')
        return image_id_str

    def _get_default_image(self, region_name: str, instance_type: str) -> str:
        acc = self.get_accelerators_from_instance_type(instance_type)

        if acc is None:
            image_tag = oci_utils.oci_config.get_default_image_tag()
            image_id_str = service_catalog.get_image_id_from_tag(image_tag,
                                                                 region_name,
                                                                 clouds='oci')
        else:
            assert len(acc) == 1, acc
            image_tag = oci_utils.oci_config.get_default_gpu_image_tag()
            image_id_str = service_catalog.get_image_id_from_tag(image_tag,
                                                                 region_name,
                                                                 clouds='oci')

        if image_id_str is not None:
            logger.debug(
                f'Got default image_id {image_id_str} from tag {image_tag}')
            return image_id_str

        # Raise ResourcesUnavailableError to make sure the failover in
        # CloudVMRayBackend will be correctly triggered.
        # TODO(zhwu): This is a information leakage to the cloud implementor,
        # we need to find a better way to handle this.
        raise exceptions.ResourcesUnavailableError(
            'ERR: No image found in catalog for region '
            f'{region_name}. Try update your default image_id settings.')

    def get_vpu_from_disktier(
            self, cpus: Optional[float],
            disk_tier: Optional[resources_utils.DiskTier]) -> int:
        # Only normalize the disk_tier if it is not None, since OCI have
        # different default disk tier according to #vCPU.
        if disk_tier is not None:
            disk_tier = OCI._translate_disk_tier(disk_tier)
        vpu = oci_utils.oci_config.BOOT_VOLUME_VPU[disk_tier]
        if cpus is None:
            return vpu

        if cpus <= 2:
            if disk_tier is None:
                vpu = oci_utils.oci_config.DISK_TIER_LOW
            if vpu > oci_utils.oci_config.DISK_TIER_LOW:
                # If only 1 OCPU is configured, best to use the OCI default
                # VPU (10) for the boot volume. Even if the VPU is configured
                # to higher value (no error to launch the instance), we cannot
                # fully achieve its IOPS/throughput performance.
                logger.warning(
                    'Automatically set the VPU to '
                    f'{oci_utils.oci_config.DISK_TIER_LOW} as only 2x vCPU is '
                    'configured.')
                vpu = oci_utils.oci_config.DISK_TIER_LOW
        elif cpus < 8:
            # If less than 4 OCPU is configured, best not to set the disk_tier
            # to 'high' (vpu=100). Even if the disk_tier is configured to high
            # (no error to launch the instance), we cannot fully achieve its
            # IOPS/throughput performance.
            if vpu > oci_utils.oci_config.DISK_TIER_MEDIUM:
                logger.warning(
                    'Automatically set the VPU to '
                    f'{oci_utils.oci_config.DISK_TIER_MEDIUM} as less than 8x '
                    'vCPU is configured.')
                vpu = oci_utils.oci_config.DISK_TIER_MEDIUM
        return vpu

    @classmethod
    def query_status(cls, name: str, tag_filters: Dict[str, str],
                     region: Optional[str], zone: Optional[str],
                     **kwargs) -> List[status_lib.ClusterStatus]:
        del zone, kwargs  # Unused.
        # Check the lifecycleState definition from the page
        # https://docs.oracle.com/en-us/iaas/api/#/en/iaas/latest/Instance/
        status_map = {
            'PROVISIONING': status_lib.ClusterStatus.INIT,
            'STARTING': status_lib.ClusterStatus.INIT,
            'RUNNING': status_lib.ClusterStatus.UP,
            'STOPPING': status_lib.ClusterStatus.STOPPED,
            'STOPPED': status_lib.ClusterStatus.STOPPED,
            'TERMINATED': None,
            'TERMINATING': None,
        }

        # pylint: disable=import-outside-toplevel
        from sky.skylet.providers.oci.query_helper import oci_query_helper

        status_list = []
        try:
            vms = oci_query_helper.query_instances_by_tags(
                tag_filters=tag_filters, region=region)
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ClusterStatusFetchingError(
                    f'Failed to query OCI cluster {name!r} status. '
                    'Details: '
                    f'{common_utils.format_exception(e, use_bracket=True)}')

        for node in vms:
            vm_status = node.lifecycle_state
            if vm_status in status_map:
                sky_status = status_map[vm_status]
                if sky_status is not None:
                    status_list.append(sky_status)

        return status_list
