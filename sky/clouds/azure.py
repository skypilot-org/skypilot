"""Azure."""
import os
import re
import subprocess
import textwrap
import typing
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

import colorama
from packaging import version as pversion

from sky import catalog
from sky import clouds
from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import azure
from sky.adaptors import common as adaptors_common
from sky.clouds.utils import azure_utils
from sky.utils import annotations
from sky.utils import common_utils
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky import resources
    from sky.utils import volume as volume_lib

logger = sky_logging.init_logger(__name__)

# Minimum set of files under ~/.azure that grant Azure access.
_CREDENTIAL_FILES = [
    'azureProfile.json',
    'clouds.config',
    'config',
    'msal_token_cache.json',
]

_MAX_IDENTITY_FETCH_RETRY = 10

_DEFAULT_AZURE_UBUNTU_HPC_IMAGE_GB = 30
_DEFAULT_AZURE_UBUNTU_2004_IMAGE_GB = 150
_DEFAULT_SKYPILOT_IMAGE_GB = 30

_DEFAULT_CPU_IMAGE_ID = 'skypilot:custom-cpu-ubuntu-v2'
_DEFAULT_GPU_IMAGE_ID = 'skypilot:custom-gpu-ubuntu-v2'
_DEFAULT_V1_IMAGE_ID = 'skypilot:custom-gpu-ubuntu-v1'
_DEFAULT_GPU_K80_IMAGE_ID = 'skypilot:k80-ubuntu-2004'
_FALLBACK_IMAGE_ID = 'skypilot:gpu-ubuntu-2204'
# This is used by Azure GPU VMs that use grid drivers (e.g. A10).
_DEFAULT_GPU_GRID_IMAGE_ID = 'skypilot:custom-gpu-ubuntu-v2-grid'

_COMMUNITY_IMAGE_PREFIX = '/CommunityGalleries'


def _run_output(cmd):
    proc = subprocess.run(cmd,
                          shell=True,
                          check=True,
                          stderr=subprocess.PIPE,
                          stdout=subprocess.PIPE)
    return proc.stdout.decode('ascii')


@registry.CLOUD_REGISTRY.register
class Azure(clouds.Cloud):
    """Azure."""

    _REPR = 'Azure'
    # Azure has a 90 char limit for resource group; however, SkyPilot adds the
    # suffix `-<region name>`. Azure also has a 64 char limit for VM names, and
    # ray adds addtional `ray-`, `-worker`, and `-<9 chars hash>` for the VM
    # names, so the limit is 64 - 4 - 7 - 10 = 43.
    # Reference: https://azure.github.io/PSRule.Rules.Azure/en/rules/Azure.ResourceGroup.Name/ # pylint: disable=line-too-long
    _MAX_CLUSTER_NAME_LEN_LIMIT = 42
    _BEST_DISK_TIER = resources_utils.DiskTier.HIGH
    _DEFAULT_DISK_TIER = resources_utils.DiskTier.MEDIUM
    # Azure does not support high disk and ultra disk tier.
    _SUPPORTED_DISK_TIERS = (set(resources_utils.DiskTier) -
                             {resources_utils.DiskTier.ULTRA})

    _INDENT_PREFIX = ' ' * 4

    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT

    @classmethod
    def _unsupported_features_for_resources(
        cls,
        resources: 'resources.Resources',
        region: Optional[str] = None,
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        features = {
            clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER:
                (f'Migrating disk is currently not supported on {cls._REPR}.'),
            clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS: (
                f'High availability controllers are not supported on {cls._REPR}.'
            ),
            clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER:
                (f'Custom network tier is not supported on {cls._REPR}.'),
            clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK: (
                f'Customized multiple network interfaces are not supported on {cls._REPR}.'
            ),
        }
        if resources.use_spot:
            features[clouds.CloudImplementationFeatures.STOP] = (
                'Stopping spot instances is currently not supported on'
                f' {cls._REPR}.')
        return features

    @classmethod
    def max_cluster_name_length(cls) -> int:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    def instance_type_to_hourly_cost(self,
                                     instance_type: str,
                                     use_spot: bool,
                                     region: Optional[str] = None,
                                     zone: Optional[str] = None) -> float:
        return catalog.get_hourly_cost(instance_type,
                                       use_spot=use_spot,
                                       region=region,
                                       zone=zone,
                                       clouds='azure')

    def accelerators_to_hourly_cost(self,
                                    accelerators: Dict[str, int],
                                    use_spot: bool,
                                    region: Optional[str] = None,
                                    zone: Optional[str] = None) -> float:
        del accelerators, use_spot, region, zone  # unused
        # Azure includes accelerators as part of the instance type.
        # Implementing this is also necessary for e.g., the instance may have 4
        # GPUs, while the task specifies to use 1 GPU.
        return 0

    def get_egress_cost(self, num_gigabytes: float):
        # In general, query this from the cloud:
        #   https://azure.microsoft.com/en-us/pricing/details/bandwidth/
        # NOTE: egress from US East.
        # NOTE: Not accurate as the pricing tier is based on cumulative monthly
        # usage.
        if num_gigabytes > 150 * 1024:
            return 0.05 * num_gigabytes
        cost = 0.0
        if num_gigabytes >= 50 * 1024:
            cost += (num_gigabytes - 50 * 1024) * 0.07
            num_gigabytes -= 50 * 1024

        if num_gigabytes >= 10 * 1024:
            cost += (num_gigabytes - 10 * 1024) * 0.083
            num_gigabytes -= 10 * 1024

        if num_gigabytes > 1:
            cost += (num_gigabytes - 1) * 0.0875

        cost += 0.0
        return cost

    @classmethod
    def get_default_instance_type(cls,
                                  cpus: Optional[str] = None,
                                  memory: Optional[str] = None,
                                  disk_tier: Optional[
                                      resources_utils.DiskTier] = None,
                                  region: Optional[str] = None,
                                  zone: Optional[str] = None) -> Optional[str]:
        return catalog.get_default_instance_type(cpus=cpus,
                                                 memory=memory,
                                                 disk_tier=disk_tier,
                                                 region=region,
                                                 zone=zone,
                                                 clouds='azure')

    @classmethod
    def get_image_size(cls, image_id: str, region: Optional[str]) -> float:
        # Process skypilot images.
        if image_id.startswith('skypilot:'):
            image_id = catalog.get_image_id_from_tag(image_id, clouds='azure')
            if image_id.startswith(_COMMUNITY_IMAGE_PREFIX):
                # Avoid querying the image size from Azure as
                # all skypilot custom images have the same size.
                return _DEFAULT_SKYPILOT_IMAGE_GB
            else:
                publisher, offer, sku, version = image_id.split(':')
                if offer == 'ubuntu-hpc':
                    return _DEFAULT_AZURE_UBUNTU_HPC_IMAGE_GB
                else:
                    return _DEFAULT_AZURE_UBUNTU_2004_IMAGE_GB

        # Process user-specified images.
        azure_utils.validate_image_id(image_id)
        try:
            compute_client = azure.get_client('compute', cls.get_project_id())
        except (azure.exceptions().AzureError, RuntimeError):
            # Fallback to default image size if no credentials are available.
            return 0.0

        # Community gallery image.
        if image_id.startswith(_COMMUNITY_IMAGE_PREFIX):
            if region is None:
                return 0.0
            _, _, gallery_name, _, image_name = image_id.split('/')
            try:
                return azure_utils.get_community_image_size(
                    compute_client, gallery_name, image_name, region)
            except exceptions.ResourcesUnavailableError:
                return 0.0

        # Marketplace image
        if region is None:
            # The region used here is only for where to send the query,
            # not the image location. Marketplace image is globally available.
            region = 'eastus'
        publisher, offer, sku, version = image_id.split(':')
        # Since the Azure SDK requires explicitly specifying the image version number,
        # when the version is "latest," we need to manually query the current latest version.
        # By querying the image size through a precise image version, while directly using the latest image version when creating a VM,
        # there might be a difference in image information, and the probability of this occurring is very small.
        if version == 'latest':
            versions = compute_client.virtual_machine_images.list(
                location=region,
                publisher_name=publisher,
                offer=offer,
                skus=sku)
            latest_version = max(versions, key=lambda x: pversion.parse(x.name))
            version = latest_version.name
        try:
            image = compute_client.virtual_machine_images.get(
                region, publisher, offer, sku, version)
        except azure.exceptions().ResourceNotFoundError as e:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Image not found: {image_id}') from e
        if image.os_disk_image is None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Retrieve image size for {image_id} failed.')
        ap = image.os_disk_image.additional_properties
        size_in_gb = ap.get('sizeInGb')
        if size_in_gb is not None:
            return float(size_in_gb)
        size_in_bytes = ap.get('sizeInBytes')
        if size_in_bytes is None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Retrieve image size for {image_id} failed. '
                                 f'Got additional_properties: {ap}')
        size_in_gb = size_in_bytes / (1024**3)
        return size_in_gb

    def _get_default_image_tag(self, gen_version, instance_type) -> str:
        # ubuntu-2004 v21.08.30, K80 requires image with old NVIDIA driver version
        acc = self.get_accelerators_from_instance_type(instance_type)
        if acc is not None:
            acc_name = list(acc.keys())[0]
            if acc_name == 'K80':
                return _DEFAULT_GPU_K80_IMAGE_ID
            if acc_name == 'A10':
                return _DEFAULT_GPU_GRID_IMAGE_ID
        # About Gen V1 vs V2:
        # In Azure, all instances with K80 (Standard_NC series), some
        # instances with M60 (Standard_NV series) and some cpu instances
        # (Basic_A, Standard_D, ...) are V1 instance.
        # All A100 instances are V2.
        if gen_version == 'V1':
            return _DEFAULT_V1_IMAGE_ID
        if acc is None:
            return _DEFAULT_CPU_IMAGE_ID
        return _DEFAULT_GPU_IMAGE_ID

    @classmethod
    def regions_with_offering(
        cls,
        instance_type: str,
        accelerators: Optional[Dict[str, int]],
        use_spot: bool,
        region: Optional[str],
        zone: Optional[str],
        resources: Optional['resources.Resources'] = None,
    ) -> List[clouds.Region]:
        del accelerators  # unused
        assert zone is None, 'Azure does not support zones'
        regions = catalog.get_region_zones_for_instance_type(
            instance_type, use_spot, 'azure')

        if region is not None:
            regions = [r for r in regions if r.name == region]
        return regions

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

    # TODO: factor the following three methods, as they are the same logic
    # between Azure and AWS.

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, Union[int, float]]]:
        return catalog.get_accelerators_from_instance_type(instance_type,
                                                           clouds='azure')

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        return catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                        clouds='azure')

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    def make_deploy_resources_variables(
        self,
        resources: 'resources.Resources',
        cluster_name: resources_utils.ClusterName,
        region: 'clouds.Region',
        zones: Optional[List['clouds.Zone']],
        num_nodes: int,
        dryrun: bool = False,
        volume_mounts: Optional[List['volume_lib.VolumeMount']] = None,
    ) -> Dict[str, Any]:
        assert zones is None, ('Azure does not support zones', zones)

        region_name = region.name

        resources = resources.assert_launchable()
        # resources.accelerators is cleared but .instance_type encodes the info.
        acc_dict = self.get_accelerators_from_instance_type(
            resources.instance_type)
        acc_count = None
        if acc_dict is not None:
            acc_count = str(sum(acc_dict.values()))
        custom_resources = resources_utils.make_ray_custom_resources_str(
            acc_dict)

        if (resources.image_id is None or
                resources.extract_docker_image() is not None):
            # pylint: disable=import-outside-toplevel
            from sky.catalog import azure_catalog
            gen_version = azure_catalog.get_gen_version_from_instance_type(
                resources.instance_type)
            image_id = self._get_default_image_tag(gen_version,
                                                   resources.instance_type)
        else:
            if None in resources.image_id:
                image_id = resources.image_id[None]
            else:
                assert region_name in resources.image_id, resources.image_id
                image_id = resources.image_id[region_name]

        # Checked basic image syntax in resources.py
        if image_id.startswith('skypilot:'):
            image_id = catalog.get_image_id_from_tag(image_id, clouds='azure')
            # Fallback if image does not exist in the specified region.
            # Putting fallback here instead of at image validation
            # when creating the resource because community images are
            # regional so we need the correct region when we check whether
            # the image exists.
            if image_id.startswith(
                    _COMMUNITY_IMAGE_PREFIX
            ) and region_name not in azure_catalog.COMMUNITY_IMAGE_AVAILABLE_REGIONS:
                logger.info(f'Azure image {image_id} does not exist in region '
                            f'{region_name} so use the fallback image instead.')
                image_id = catalog.get_image_id_from_tag(_FALLBACK_IMAGE_ID,
                                                         clouds='azure')

        if image_id.startswith(_COMMUNITY_IMAGE_PREFIX):
            image_config = {'community_gallery_image_id': image_id}
        else:
            publisher, offer, sku, version = image_id.split(':')
            image_config = {
                'image_publisher': publisher,
                'image_offer': offer,
                'image_sku': sku,
                'image_version': version,
            }

        # Determine resource group for deploying the instance.
        resource_group_name = skypilot_config.get_effective_region_config(
            cloud='azure',
            region=region_name,
            keys=('resource_group_vm',),
            default_value=None)
        use_external_resource_group = resource_group_name is not None
        if resource_group_name is None:
            resource_group_name = f'{cluster_name.name_on_cloud}-{region_name}'

        # Setup commands to eliminate the banner and restart sshd.
        # This script will modify /etc/ssh/sshd_config and add a bash script
        # into .bashrc. The bash script will restart sshd if it has not been
        # restarted, identified by a file /tmp/__restarted is existing.
        # Also, add default user to docker group.
        # pylint: disable=line-too-long
        cloud_init_setup_commands = textwrap.dedent("""\
            #cloud-config
            runcmd:
              - sed -i 's/#Banner none/Banner none/' /etc/ssh/sshd_config
              - echo '\\nif [ ! -f "/tmp/__restarted" ]; then\\n  sudo systemctl restart ssh\\n  sleep 2\\n  touch /tmp/__restarted\\nfi' >> /home/skypilot:ssh_user/.bashrc
            write_files:
              - path: /etc/apt/apt.conf.d/20auto-upgrades
                content: |
                  APT::Periodic::Update-Package-Lists "0";
                  APT::Periodic::Download-Upgradeable-Packages "0";
                  APT::Periodic::AutocleanInterval "0";
                  APT::Periodic::Unattended-Upgrade "0";
              - path: /etc/apt/apt.conf.d/10cloudinit-disable
                content: |
                  APT::Periodic::Enable "0";
            """).split('\n')

        def _failover_disk_tier() -> Optional[resources_utils.DiskTier]:
            if (resources.disk_tier is not None and
                    resources.disk_tier != resources_utils.DiskTier.BEST):
                return resources.disk_tier
            # Failover disk tier from high to low. Default disk tier
            # (Premium_LRS, medium) only support s-series instance types,
            # so we failover to lower tiers for non-s-series.
            all_tiers = list(reversed(resources_utils.DiskTier))
            start_index = all_tiers.index(
                Azure._translate_disk_tier(resources.disk_tier))
            while start_index < len(all_tiers):
                disk_tier = all_tiers[start_index]
                ok, _ = Azure.check_disk_tier(resources.instance_type,
                                              disk_tier)
                if ok:
                    return disk_tier
                start_index += 1
            assert False, 'Low disk tier should always be supported on Azure.'

        disk_tier = _failover_disk_tier()

        resources_vars: Dict[str, Any] = {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'num_gpus': acc_count,
            'use_spot': resources.use_spot,
            'region': region_name,
            # Azure does not support specific zones.
            'zones': None,
            **image_config,
            'disk_tier': Azure._get_disk_type(disk_tier),
            'cloud_init_setup_commands': cloud_init_setup_commands,
            'azure_subscription_id': self.get_project_id(dryrun),
            'resource_group': resource_group_name,
            'use_external_resource_group': use_external_resource_group,
        }

        # Setting disk performance tier for high disk tier.
        if disk_tier == resources_utils.DiskTier.HIGH:
            resources_vars['disk_performance_tier'] = 'P50'
        return resources_vars

    def _get_feasible_launchable_resources(
        self, resources: 'resources.Resources'
    ) -> 'resources_utils.FeasibleResources':
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            ok, _ = Azure.check_disk_tier(resources.instance_type,
                                          resources.disk_tier)
            if not ok:
                # TODO: Add hints to all return values in this method to help
                #  users understand why the resources are not launchable.
                return resources_utils.FeasibleResources([], [], None)
            # Treat Resources(Azure, Standard_NC4as_T4_v3, T4) as
            # Resources(Azure, Standard_NC4as_T4_v3).
            resources = resources.copy(accelerators=None)
            return resources_utils.FeasibleResources([resources], [], None)

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                ok, _ = Azure.check_disk_tier(instance_type,
                                              resources.disk_tier)
                if not ok:
                    continue
                r = resources.copy(
                    cloud=Azure(),
                    instance_type=instance_type,
                    # Setting this to None as Azure doesn't separately bill /
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
            default_instance_type = Azure.get_default_instance_type(
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
         fuzzy_candidate_list) = catalog.get_instance_type_for_accelerator(
             acc,
             acc_count,
             cpus=resources.cpus,
             memory=resources.memory,
             use_spot=resources.use_spot,
             region=resources.region,
             zone=resources.zone,
             clouds='azure')
        if instance_list is None:
            return resources_utils.FeasibleResources([], fuzzy_candidate_list,
                                                     None)
        return resources_utils.FeasibleResources(_make(instance_list),
                                                 fuzzy_candidate_list, None)

    @classmethod
    def _check_compute_credentials(
            cls) -> Tuple[bool, Optional[Union[str, Dict[str, str]]]]:
        """Checks if the user has access credentials to this cloud's compute service."""
        return cls._check_credentials()

    @classmethod
    def _check_storage_credentials(
            cls) -> Tuple[bool, Optional[Union[str, Dict[str, str]]]]:
        """Checks if the user has access credentials to this cloud's storage service."""
        # TODO(seungjin): Implement separate check for
        # if the user has access to Azure Blob Storage.
        return cls._check_credentials()

    @classmethod
    def _check_credentials(cls) -> Tuple[bool, Optional[str]]:
        """Checks if the user has access credentials to this cloud."""

        help_str = (
            ' Run the following commands:'
            f'\n{cls._INDENT_PREFIX}  $ az login'
            f'\n{cls._INDENT_PREFIX}  $ az account set -s <subscription_id>'
            f'\n{cls._INDENT_PREFIX}For more info: '
            'https://docs.microsoft.com/en-us/cli/azure/get-started-with-azure-cli'  # pylint: disable=line-too-long
        )
        # This file is required because it will be synced to remote VMs for
        # `az` to access private storage buckets.
        # `az account show` does not guarantee this file exists.
        azure_token_cache_file = '~/.azure/msal_token_cache.json'
        if not os.path.isfile(os.path.expanduser(azure_token_cache_file)):
            return (False,
                    f'{azure_token_cache_file} does not exist.' + help_str)

        dependency_installation_hints = (
            'Azure dependencies are not installed. '
            'Run the following commands:'
            f'\n{cls._INDENT_PREFIX}  $ pip install skypilot[azure]'
            f'\n{cls._INDENT_PREFIX}Credentials may also need to be set.')
        # Check if the azure blob storage dependencies are installed.
        if not adaptors_common.can_import_modules(
            ['azure.storage.blob', 'msgraph']):
            return False, dependency_installation_hints

        try:
            _run_output('az --version')
        except subprocess.CalledProcessError as e:
            return False, (
                # TODO(zhwu): Change the installation hint to from PyPI.
                'Azure CLI `az --version` errored. Run the following commands:'
                f'\n{cls._INDENT_PREFIX}  $ pip install skypilot[azure]'
                f'\n{cls._INDENT_PREFIX}Credentials may also need to be set.'
                f'{help_str}\n'
                f'{cls._INDENT_PREFIX}Details: '
                f'{common_utils.format_exception(e)}')
        # If Azure is properly logged in, this will return the account email
        # address + subscription ID.
        try:
            cls.get_active_user_identity()
        except exceptions.CloudUserIdentityError as e:
            return False, (f'Getting user\'s Azure identity failed.{help_str}\n'
                           f'{cls._INDENT_PREFIX}Details: '
                           f'{common_utils.format_exception(e)}')
        return True, None

    def get_credential_file_mounts(self) -> Dict[str, str]:
        """Returns a dict of credential file paths to mount paths."""
        return {
            f'~/.azure/{filename}': f'~/.azure/{filename}'
            for filename in _CREDENTIAL_FILES
        }

    def instance_type_exists(self, instance_type):
        return catalog.instance_type_exists(instance_type, clouds='azure')

    @classmethod
    @annotations.lru_cache(scope='global',
                           maxsize=1)  # Cache since getting identity is slow.
    def get_user_identities(cls) -> Optional[List[List[str]]]:
        """Returns the cloud user identity."""
        # This returns the user's email address + [subscription_id].
        retry_cnt = 0
        while True:
            retry_cnt += 1
            try:
                import knack  # pylint: disable=import-outside-toplevel
            except ModuleNotFoundError as e:
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.CloudUserIdentityError(
                        'Failed to import \'knack\'. To install the dependencies for Azure, '
                        'Please install SkyPilot with: '
                        f'{colorama.Style.BRIGHT}pip install skypilot[azure]'
                        f'{colorama.Style.RESET_ALL}') from e
            try:
                account_email = azure.get_current_account_user()
                break
            except (FileNotFoundError, knack.util.CLIError) as e:
                error = exceptions.CloudUserIdentityError(
                    'Failed to get activated Azure account.\n'
                    '  Reason: '
                    f'{common_utils.format_exception(e, use_bracket=True)}')
                if retry_cnt <= _MAX_IDENTITY_FETCH_RETRY:
                    logger.debug(f'{error}.\nRetrying...')
                    continue
                with ux_utils.print_exception_no_traceback():
                    raise error from None
            except Exception as e:  # pylint: disable=broad-except
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.CloudUserIdentityError(
                        'Failed to get Azure user identity with unknown '
                        f'exception.\n'
                        '  Reason: '
                        f'{common_utils.format_exception(e, use_bracket=True)}'
                    ) from e
        try:
            project_id = cls.get_project_id()
        except (ModuleNotFoundError, RuntimeError) as e:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.CloudUserIdentityError(
                    'Failed to get Azure project ID.') from e
        # TODO: Return a list of identities in the profile when we support
        #   automatic switching for Az. Currently we only support one identity.
        return [[f'{account_email} [subscription_id={project_id}]']]

    @classmethod
    def get_active_user_identity_str(cls) -> Optional[str]:
        user_identity = cls.get_active_user_identity()
        if user_identity is None:
            return None
        return user_identity[0]

    @classmethod
    def get_project_id(cls, dryrun: bool = False) -> str:
        if dryrun:
            return 'dryrun-project-id'
        try:
            azure_subscription_id = azure.get_subscription_id()
            if not azure_subscription_id:
                raise ValueError  # The error message will be replaced.
        except ModuleNotFoundError as e:
            with ux_utils.print_exception_no_traceback():
                raise ModuleNotFoundError('Unable to import azure python '
                                          'module. Is azure-cli python package '
                                          'installed? Try pip install '
                                          '.[azure] in the sky repo.') from e
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError(
                    'Failed to get subscription id from azure cli. '
                    'Make sure you have logged in and run this Azure '
                    'cli command: "az account set -s <subscription_id>".'
                ) from e
        return azure_subscription_id

    @classmethod
    def _is_s_series(cls, instance_type: Optional[str]) -> bool:
        # For azure naming convention, see https://learn.microsoft.com/en-us/azure/virtual-machines/vm-naming-conventions  # pylint: disable=line-too-long
        if instance_type is None:
            return True
        x = re.match(
            r'(Standard|Basic)_([A-Z]+)([0-9]+)(-[0-9]+)?'
            r'([a-z]*)(_[A-Z]+[0-9]+)?(_v[0-9])?(_Promo)?', instance_type)
        assert x is not None, f'Unknown instance type: {instance_type}'
        return 's' in x.group(5)

    @classmethod
    def check_disk_tier(
            cls, instance_type: Optional[str],
            disk_tier: Optional[resources_utils.DiskTier]) -> Tuple[bool, str]:
        if disk_tier is None or disk_tier == resources_utils.DiskTier.BEST:
            return True, ''
        if disk_tier == resources_utils.DiskTier.ULTRA:
            return False, (
                'Azure disk_tier=ultra is not supported now. '
                'Please use disk_tier={low, medium, high, best} instead.')
        # Only S-series supported premium ssd
        # see https://stackoverflow.com/questions/48590520/azure-requested-operation-cannot-be-performed-because-storage-account-type-pre  # pylint: disable=line-too-long
        if cls._get_disk_type(
                disk_tier
        ) == 'Premium_LRS' and not Azure._is_s_series(instance_type):
            return False, (
                'Azure premium SSDs are only supported for S-series '
                'instances. To use disk_tier>=medium, please make sure '
                'instance_type is specified to an S-series instance.')
        return True, ''

    @classmethod
    def check_disk_tier_enabled(cls, instance_type: Optional[str],
                                disk_tier: resources_utils.DiskTier) -> None:
        ok, msg = cls.check_disk_tier(instance_type, disk_tier)
        if not ok:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.NotSupportedError(msg)

    @classmethod
    def _get_disk_type(cls,
                       disk_tier: Optional[resources_utils.DiskTier]) -> str:
        tier = cls._translate_disk_tier(disk_tier)
        # TODO(tian): Maybe use PremiumV2_LRS/UltraSSD_LRS? Notice these two
        # cannot be used as OS disks so we might need data disk support
        tier2name = {
            resources_utils.DiskTier.ULTRA: 'Disabled',
            resources_utils.DiskTier.HIGH: 'Premium_LRS',
            resources_utils.DiskTier.MEDIUM: 'Premium_LRS',
            resources_utils.DiskTier.LOW: 'Standard_LRS',
        }
        return tier2name[tier]
