"""Oracle Cloud Infrastructure (OCI)

History:
 - Hysun He (hysun.he@oracle.com) @ Apr, 2023: Initial implementation
 - Hysun He (hysun.he@oracle.com) @ May 4, 2023: Support use the default
   image_id (configurable) if no image_id specified in the task yaml.
 - Hysun He (hysun.he@oracle.com) @ Oct 12, 2024:
   get_credential_file_mounts(): bug fix for sky config
   file path resolution (by os.path.expanduser) when construct the file
   mounts. This bug will cause the created workder nodes located in different
   compartment and VCN than the header node if user specifies compartment_id
   in the sky config file, because the ~/.sky/skyconfig.yaml is not
   sync-ed to the remote machine.
   The workaround is set the sky config file path using ENV before running
   the sky launch: export SKYPILOT_CONFIG=/home/ubuntu/.sky/config.yaml
 - Hysun He (hysun.he@oracle.com) @ Oct 12, 2024:
   make_deploy_resources_variables(): Bug fix for specify the image_id as
   the ocid of the image in the task.yaml file, in this case the image_id
   for the node config should be set to the ocid instead of a dict.
 - Hysun He (hysun.he@oracle.com) @ Oct 13, 2024:
   Support more OS types additional to ubuntu for OCI resources.
"""
import logging
import os
import typing
from typing import Dict, Iterator, List, Optional, Tuple, Union

from sky import clouds
from sky import exceptions
from sky.adaptors import oci as oci_adaptor
from sky.clouds import service_catalog
from sky.clouds.utils import oci_utils
from sky.provision.oci.query_utils import query_helper
from sky.utils import common_utils
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils import status_lib
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    # Renaming to avoid shadowing variables.
    from sky import resources as resources_lib

logger = logging.getLogger(__name__)

_tenancy_prefix: Optional[str] = None


@registry.CLOUD_REGISTRY.register
class OCI(clouds.Cloud):
    """OCI: Oracle Cloud Infrastructure """

    _REPR = 'OCI'

    _MAX_CLUSTER_NAME_LEN_LIMIT = 200

    _regions: List[clouds.Region] = []

    _INDENT_PREFIX = '    '

    _SUPPORTED_DISK_TIERS = (set(resources_utils.DiskTier) -
                             {resources_utils.DiskTier.ULTRA})
    _BEST_DISK_TIER = resources_utils.DiskTier.HIGH

    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT

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
    ) -> Optional[Dict[str, Union[int, float]]]:
        return service_catalog.get_accelerators_from_instance_type(
            instance_type, clouds='oci')

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    def make_deploy_resources_variables(
            self,
            resources: 'resources_lib.Resources',
            cluster_name: resources_utils.ClusterName,
            region: Optional['clouds.Region'],
            zones: Optional[List['clouds.Zone']],
            num_nodes: int,
            dryrun: bool = False) -> Dict[str, Optional[str]]:
        del cluster_name, dryrun  # Unused.
        assert region is not None, resources

        acc_dict = self.get_accelerators_from_instance_type(
            resources.instance_type)
        custom_resources = resources_utils.make_ray_custom_resources_str(
            acc_dict)

        image_str = self._get_image_id(resources.image_id, region.name,
                                       resources.instance_type)
        image_cols = image_str.split(oci_utils.oci_config.IMAGE_TAG_SPERATOR)
        if len(image_cols) == 3:
            image_id = image_cols[0]
            listing_id = image_cols[1]
            res_ver = image_cols[2]
        else:
            # Oct.12,2024 by HysunHe: Bug fix - resources.image_id is an
            # dict. The image_id here should be the ocid format.
            image_id = image_str
            listing_id = None
            res_ver = None

        os_type = None
        if ':' in image_id:
            # OS type provided in the --image-id. This is the case where
            # custom image's ocid provided in the --image-id parameter.
            #  - ocid1.image...aaa:oraclelinux (os type is oraclelinux)
            #  - ocid1.image...aaa (OS not provided)
            image_id, os_type = image_id.replace(' ', '').split(':')

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
            except (oci_adaptor.oci.exceptions.ConfigFileNotFound,
                    oci_adaptor.oci.exceptions.InvalidConfig) as e:
                # This should only happen in testing where oci config is
                # monkeypatched. In real use, if the OCI config is not
                # valid, the 'sky check' would fail (OCI disabled).
                logger.debug(f'It is OK goes here when testing: {str(e)}')
                pass

        # Disk performane: Volume Performance Units.
        vpu = self.get_vpu_from_disktier(
            cpus=None if cpus is None else float(cpus),
            disk_tier=resources.disk_tier)

        if os_type is None:
            # OS type is not determined yet. So try to get it from vms.csv
            image_str = self._get_image_str(
                image_id=resources.image_id,
                instance_type=resources.instance_type,
                region=region.name)

            # pylint: disable=import-outside-toplevel
            from sky.clouds.service_catalog import oci_catalog
            os_type = oci_catalog.get_image_os_from_tag(tag=image_str,
                                                        region=region.name)
        logger.debug(f'OS type for the image {image_id} is {os_type}')

        return {
            'instance_type': instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
            'os_type': os_type,
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
    ) -> 'resources_utils.FeasibleResources':
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            resources = resources.copy(accelerators=None)
            # TODO: Add hints to all return values in this method to help
            #  users understand why the resources are not launchable.
            return resources_utils.FeasibleResources([resources], [], None)

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
                return resources_utils.FeasibleResources([], [], None)
            else:
                return resources_utils.FeasibleResources(
                    _make([default_instance_type]), [], None)

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
            return resources_utils.FeasibleResources([], fuzzy_candidate_list,
                                                     None)

        return resources_utils.FeasibleResources(_make(instance_list),
                                                 fuzzy_candidate_list, None)

    @classmethod
    def _check_compute_credentials(cls) -> Tuple[bool, Optional[str]]:
        """Checks if the user has access credentials to
        OCI's compute service."""
        return cls._check_credentials()

    @classmethod
    def _check_storage_credentials(cls) -> Tuple[bool, Optional[str]]:
        """Checks if the user has access credentials to
        OCI's storage service."""
        # TODO(seungjin): Implement separate check for
        # if the user has access to OCI Object Storage.
        return cls._check_credentials()

    @classmethod
    def _check_credentials(cls) -> Tuple[bool, Optional[str]]:
        """Checks if the user has access credentials to this cloud."""

        short_credential_help_str = (
            'For more details, refer to: '
            # pylint: disable=line-too-long
            'https://docs.skypilot.co/en/latest/getting-started/installation.html#oracle-cloud-infrastructure-oci'
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
        except (oci_adaptor.oci.exceptions.ConfigFileNotFound,
                oci_adaptor.oci.exceptions.InvalidConfig,
                oci_adaptor.oci.exceptions.ServiceError) as e:
            return False, (
                f'OCI credential is not correctly set. '
                f'Check the credential file at {conf_file}\n'
                f'{cls._INDENT_PREFIX}{credential_help_str}\n'
                f'{cls._INDENT_PREFIX}Error details: '
                f'{common_utils.format_exception(e, use_bracket=True)}')

    @classmethod
    def check_disk_tier(
            cls, instance_type: Optional[str],
            disk_tier: Optional[resources_utils.DiskTier]) -> Tuple[bool, str]:
        del instance_type  # Unused.
        if disk_tier is None or disk_tier == resources_utils.DiskTier.BEST:
            return True, ''
        if disk_tier == resources_utils.DiskTier.ULTRA:
            return False, ('OCI disk_tier=ultra is not supported now. '
                           'Please use disk_tier={low, medium, high, best} '
                           'instead.')
        return True, ''

    def get_credential_file_mounts(self) -> Dict[str, str]:
        """Returns a dict of credential file paths to mount paths."""
        try:
            oci_cfg_file = oci_adaptor.get_config_file()
            # Pass-in a profile parameter so that multiple profile in oci
            # config file is supported (2023/06/09).
            oci_cfg = oci_adaptor.get_oci_config(
                profile=oci_utils.oci_config.get_profile())
            api_key_file = oci_cfg[
                'key_file'] if 'key_file' in oci_cfg else 'BadConf'
            sky_cfg_file = oci_utils.oci_config.get_sky_user_config_file()
        # Must catch ImportError before any oci_adaptor.oci.exceptions
        # because oci_adaptor.oci.exceptions can throw ImportError.
        except ImportError:
            return {}
        except oci_adaptor.oci.exceptions.ConfigFileNotFound:
            return {}

        # OCI config and API key file are mandatory
        credential_files = [oci_cfg_file, api_key_file]

        # Sky config file is optional
        if os.path.exists(os.path.expanduser(sky_cfg_file)):
            credential_files.append(sky_cfg_file)

        file_mounts = {
            f'{filename}': f'{filename}' for filename in credential_files
        }

        logger.debug(f'OCI credential file mounts: {file_mounts}')
        return file_mounts

    @classmethod
    def get_user_identities(cls) -> Optional[List[List[str]]]:
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
        image_id_str = self._get_image_str(image_id=image_id,
                                           instance_type=instance_type,
                                           region=region_name)

        if image_id_str.startswith('skypilot:'):
            image_id_str = service_catalog.get_image_id_from_tag(image_id_str,
                                                                 region_name,
                                                                 clouds='oci')

        # Image_id should be impossible be None, except for the case when
        # user specify an image tag which does not exist in the image.csv
        # catalog file which only possible in "test" / "evaluation" phase.
        # Therefore, we use assert here.
        assert image_id_str is not None

        logger.debug(f'Got real image_id {image_id_str}')
        return image_id_str

    def _get_image_str(self, image_id: Optional[Dict[Optional[str], str]],
                       instance_type: str, region: str):
        if image_id is None:
            image_str = self._get_default_image_tag(instance_type)
        elif None in image_id:
            image_str = image_id[None]
        else:
            assert region in image_id, image_id
            image_str = image_id[region]
        return image_str

    def _get_default_image_tag(self, instance_type: str) -> str:
        acc = self.get_accelerators_from_instance_type(instance_type)

        if acc is None:
            image_tag = oci_utils.oci_config.get_default_image_tag()
        else:
            assert len(acc) == 1, acc
            image_tag = oci_utils.oci_config.get_default_gpu_image_tag()

        return image_tag

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

        status_list = []
        try:
            vms = query_helper.query_instances_by_tags(tag_filters=tag_filters,
                                                       region=region)
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ClusterStatusFetchingError(
                    f'Failed to query OCI cluster {name!r} status. '
                    'Details: '
                    f'{common_utils.format_exception(e, use_bracket=True)}')

        for node in vms:
            vm_status = node.lifecycle_state
            sky_status = oci_utils.oci_config.STATE_MAPPING_OCI_TO_SKY.get(
                vm_status, None)
            if sky_status is not None:
                status_list.append(sky_status)

        return status_list
