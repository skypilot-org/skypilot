"""Azure."""
import base64
import functools
import json
import os
import re
import subprocess
import textwrap
import typing
from typing import Dict, Iterator, List, Optional, Tuple

import colorama

from sky import clouds
from sky import exceptions
from sky import sky_logging
from sky import status_lib
from sky.adaptors import azure
from sky.clouds import service_catalog
from sky.skylet import log_lib
from sky.utils import common_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky import resources

logger = sky_logging.init_logger(__name__)

# Minimum set of files under ~/.azure that grant Azure access.
_CREDENTIAL_FILES = [
    'azureProfile.json',
    'clouds.config',
    'config',
    'msal_token_cache.json',
]

_MAX_IDENTITY_FETCH_RETRY = 10


def _run_output(cmd):
    proc = subprocess.run(cmd,
                          shell=True,
                          check=True,
                          stderr=subprocess.PIPE,
                          stdout=subprocess.PIPE)
    return proc.stdout.decode('ascii')


@clouds.CLOUD_REGISTRY.register
class Azure(clouds.Cloud):
    """Azure."""

    _REPR = 'Azure'
    # Azure has a 90 char limit for resource group; however, SkyPilot adds the
    # suffix `-<region name>`. Azure also has a 64 char limit for VM names, and
    # ray adds addtional `ray-`, `-worker`, and `-<9 chars hash>` for the VM
    # names, so the limit is 64 - 4 - 7 - 10 = 43.
    # Reference: https://azure.github.io/PSRule.Rules.Azure/en/rules/Azure.ResourceGroup.Name/ # pylint: disable=line-too-long
    _MAX_CLUSTER_NAME_LEN_LIMIT = 42

    _INDENT_PREFIX = ' ' * 4

    @classmethod
    def _cloud_unsupported_features(
            cls) -> Dict[clouds.CloudImplementationFeatures, str]:
        return {
            clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER: f'Migrating disk is not supported in {cls._REPR}.',
            # TODO(zhwu): our azure subscription offer ID does not support spot.
            # Need to support it.
            clouds.CloudImplementationFeatures.SPOT_INSTANCE: f'Spot instances are not supported in {cls._REPR}.',
        }

    @classmethod
    def max_cluster_name_length(cls) -> int:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    def instance_type_to_hourly_cost(self,
                                     instance_type: str,
                                     use_spot: bool,
                                     region: Optional[str] = None,
                                     zone: Optional[str] = None) -> float:
        return service_catalog.get_hourly_cost(instance_type,
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

    def is_same_cloud(self, other):
        return isinstance(other, Azure)

    @classmethod
    def get_default_instance_type(
            cls,
            cpus: Optional[str] = None,
            memory: Optional[str] = None,
            disk_tier: Optional[str] = None) -> Optional[str]:
        return service_catalog.get_default_instance_type(cpus=cpus,
                                                         memory=memory,
                                                         disk_tier=disk_tier,
                                                         clouds='azure')

    def _get_image_config(self, gen_version, instance_type):
        # TODO(tian): images for Azure is not well organized. We should refactor
        # it to images.csv like AWS.
        # az vm image list \
        #  --publisher microsoft-dsvm --all --output table
        # nvidia-driver: 535.54.03, cuda: 12.2
        # see: https://github.com/Azure/azhpc-images/releases/tag/ubuntu-hpc-20230803
        # All A100 instances is of gen2, so it will always use
        # the latest ubuntu-hpc:2204 image.
        image_config = {
            'image_publisher': 'microsoft-dsvm',
            'image_offer': 'ubuntu-hpc',
            'image_sku': '2204',
            'image_version': '22.04.2023080201'
        }

        # ubuntu-2004 v21.08.30, K80 requires image with old NVIDIA driver version
        acc = self.get_accelerators_from_instance_type(instance_type)
        if acc is not None:
            acc_name = list(acc.keys())[0]
            if acc_name == 'K80':
                image_config = {
                    'image_publisher': 'microsoft-dsvm',
                    'image_offer': 'ubuntu-2004',
                    'image_sku': '2004-gen2',
                    'image_version': '21.08.30'
                }

        # ubuntu-2004 v21.11.04, the previous image we used in the past for
        # V1 HyperV instance before we change default image to ubuntu-hpc.
        # In Azure, all instances with K80 (Standard_NC series), some
        # instances with M60 (Standard_NV series) and some cpu instances
        # (Basic_A, Standard_D, ...) are V1 instance. For these instances,
        # we use the previous image.
        if gen_version == 'V1':
            image_config = {
                'image_publisher': 'microsoft-dsvm',
                'image_offer': 'ubuntu-2004',
                'image_sku': '2004',
                'image_version': '21.11.04'
            }

        return image_config

    @classmethod
    def regions_with_offering(cls, instance_type: str,
                              accelerators: Optional[Dict[str, int]],
                              use_spot: bool, region: Optional[str],
                              zone: Optional[str]) -> List[clouds.Region]:
        del accelerators  # unused
        assert zone is None, 'Azure does not support zones'
        regions = service_catalog.get_region_zones_for_instance_type(
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
    ) -> Optional[Dict[str, int]]:
        return service_catalog.get_accelerators_from_instance_type(
            instance_type, clouds='azure')

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        return service_catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                                clouds='azure')

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    def make_deploy_resources_variables(
            self, resources: 'resources.Resources', cluster_name_on_cloud: str,
            region: 'clouds.Region',
            zones: Optional[List['clouds.Zone']]) -> Dict[str, Optional[str]]:
        del cluster_name_on_cloud  # Unused.
        assert zones is None, ('Azure does not support zones', zones)

        region_name = region.name

        r = resources
        assert not r.use_spot, \
            'Our subscription offer ID does not support spot instances.'
        # r.accelerators is cleared but .instance_type encodes the info.
        acc_dict = self.get_accelerators_from_instance_type(r.instance_type)
        if acc_dict is not None:
            custom_resources = json.dumps(acc_dict, separators=(',', ':'))
        else:
            custom_resources = None
        # pylint: disable=import-outside-toplevel
        from sky.clouds.service_catalog import azure_catalog
        gen_version = azure_catalog.get_gen_version_from_instance_type(
            r.instance_type)
        image_config = self._get_image_config(gen_version, r.instance_type)
        # Setup commands to eliminate the banner and restart sshd.
        # This script will modify /etc/ssh/sshd_config and add a bash script
        # into .bashrc. The bash script will restart sshd if it has not been
        # restarted, identified by a file /tmp/__restarted is existing.
        # Also, add default user to docker group.
        # pylint: disable=line-too-long
        cloud_init_setup_commands = base64.b64encode(
            textwrap.dedent("""\
            #cloud-config
            runcmd:
              - sed -i 's/#Banner none/Banner none/' /etc/ssh/sshd_config
              - echo '\\nif [ ! -f "/tmp/__restarted" ]; then\\n  sudo systemctl restart ssh\\n  sleep 2\\n  touch /tmp/__restarted\\nfi' >> /home/skypilot:ssh_user/.bashrc
              - usermod -aG docker skypilot:ssh_user
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
            """).encode('utf-8')).decode('utf-8')
        return {
            'instance_type': r.instance_type,
            'custom_resources': custom_resources,
            'use_spot': r.use_spot,
            'region': region_name,
            # Azure does not support specific zones.
            'zones': None,
            **image_config,
            'disk_tier': Azure._get_disk_type(r.disk_tier),
            'cloud_init_setup_commands': cloud_init_setup_commands
        }

    def _get_feasible_launchable_resources(
        self, resources: 'resources.Resources'
    ) -> Tuple[List['resources.Resources'], List[str]]:

        def failover_disk_tier(
                instance_type: str,
                disk_tier: Optional[str]) -> Tuple[bool, Optional[str]]:
            """Figure out the actual disk tier to be used.

            Check the disk_tier specified by the user with the instance type to
            be used. If not valid, return False.

            When the disk_tier is not specified, failover through the possible
            disk tiers.

            Returns:
                A tuple of a boolean value and an optional string to represent
                the instance_type to use. If the boolean value is False, the
                specified configuration is not a valid combination, and should
                not be used for launching a VM.
            """
            if disk_tier is not None:
                ok, _ = Azure.check_disk_tier(instance_type, disk_tier)
                return (True, disk_tier) if ok else (False, None)
            disk_tier = clouds.Cloud._DEFAULT_DISK_TIER
            all_tiers = {'high', 'medium', 'low'}
            while not Azure.check_disk_tier(instance_type, disk_tier)[0]:
                all_tiers.remove(disk_tier)
                if not all_tiers:
                    # No available disk_tier found the specified instance_type
                    return (False, None)
                disk_tier = list(all_tiers)[0]
            if disk_tier != clouds.Cloud._DEFAULT_DISK_TIER:
                return True, disk_tier
            else:
                return True, None

        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            ok, disk_tier = failover_disk_tier(resources.instance_type,
                                               resources.disk_tier)
            if not ok:
                return ([], [])
            # Treat Resources(Azure, Standard_NC4as_T4_v3, T4) as
            # Resources(Azure, Standard_NC4as_T4_v3).
            resources = resources.copy(
                accelerators=None,
                disk_tier=disk_tier,
            )
            return ([resources], [])

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                ok, disk_tier = failover_disk_tier(instance_type,
                                                   resources.disk_tier)
                if not ok:
                    continue
                r = resources.copy(
                    cloud=Azure(),
                    instance_type=instance_type,
                    disk_tier=disk_tier,
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
            cpus=resources.cpus,
            memory=resources.memory,
            use_spot=resources.use_spot,
            region=resources.region,
            zone=resources.zone,
            clouds='azure')
        if instance_list is None:
            return ([], fuzzy_candidate_list)
        return (_make(instance_list), fuzzy_candidate_list)

    @classmethod
    def check_credentials(cls) -> Tuple[bool, Optional[str]]:
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
            cls.get_current_user_identity()
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
        return service_catalog.instance_type_exists(instance_type,
                                                    clouds='azure')

    def accelerator_in_region_or_zone(self,
                                      accelerator: str,
                                      acc_count: int,
                                      region: Optional[str] = None,
                                      zone: Optional[str] = None) -> bool:
        return service_catalog.accelerator_in_region_or_zone(
            accelerator, acc_count, region, zone, 'azure')

    @classmethod
    @functools.lru_cache(maxsize=1)  # Cache since getting identity is slow.
    def get_current_user_identity(cls) -> Optional[List[str]]:
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
        return [f'{account_email} [subscription_id={project_id}]']

    @classmethod
    def get_current_user_identity_str(cls) -> Optional[str]:
        user_identity = cls.get_current_user_identity()
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
    def check_disk_tier(cls, instance_type: Optional[str],
                        disk_tier: Optional[str]) -> Tuple[bool, str]:
        if disk_tier is None:
            return True, ''
        if disk_tier == 'high':
            return False, ('Azure disk_tier=high is not supported now. '
                           'Please use disk_tier={low, medium} instead.')
        # Only S-series supported premium ssd
        # see https://stackoverflow.com/questions/48590520/azure-requested-operation-cannot-be-performed-because-storage-account-type-pre  # pylint: disable=line-too-long
        if cls._get_disk_type(
                disk_tier
        ) == 'Premium_LRS' and not Azure._is_s_series(instance_type):
            return False, (
                'Azure premium SSDs are only supported for S-series '
                'instances. To use disk_tier=medium, please make sure '
                'instance_type is specified to an S-series instance.')
        return True, ''

    @classmethod
    def check_disk_tier_enabled(cls, instance_type: str,
                                disk_tier: str) -> None:
        ok, msg = cls.check_disk_tier(instance_type, disk_tier)
        if not ok:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(msg)

    @classmethod
    def _get_disk_type(cls, disk_tier: Optional[str]) -> str:
        tier = disk_tier or cls._DEFAULT_DISK_TIER
        # TODO(tian): Maybe use PremiumV2_LRS/UltraSSD_LRS? Notice these two
        # cannot be used as OS disks so we might need data disk support
        tier2name = {
            'high': 'Disabled',
            'medium': 'Premium_LRS',
            'low': 'Standard_LRS',
        }
        return tier2name[tier]

    @classmethod
    def query_status(cls, name: str, tag_filters: Dict[str, str],
                     region: Optional[str], zone: Optional[str],
                     **kwargs) -> List[status_lib.ClusterStatus]:
        del zone  # unused
        status_map = {
            'VM starting': status_lib.ClusterStatus.INIT,
            'VM running': status_lib.ClusterStatus.UP,
            # 'VM stopped' in Azure means Stopped (Allocated), which still bills
            # for the VM.
            'VM stopping': status_lib.ClusterStatus.INIT,
            'VM stopped': status_lib.ClusterStatus.INIT,
            # 'VM deallocated' in Azure means Stopped (Deallocated), which does not
            # bill for the VM.
            'VM deallocating': status_lib.ClusterStatus.STOPPED,
            'VM deallocated': status_lib.ClusterStatus.STOPPED,
        }
        tag_filter_str = ' '.join(
            f'tags.\\"{k}\\"==\'{v}\'' for k, v in tag_filters.items())

        query_node_id = (f'az vm list --query "[?{tag_filter_str}].id" -o json')
        returncode, stdout, stderr = log_lib.run_with_log(query_node_id,
                                                          '/dev/null',
                                                          require_outputs=True,
                                                          shell=True)
        logger.debug(f'{query_node_id} returned {returncode}.\n'
                     '**** STDOUT ****\n'
                     f'{stdout}\n'
                     '**** STDERR ****\n'
                     f'{stderr}')
        if returncode == 0:
            if not stdout.strip():
                return []
            node_ids = json.loads(stdout.strip())
            if not node_ids:
                return []
            state_str = '[].powerState'
            if len(node_ids) == 1:
                state_str = 'powerState'
            node_ids_str = '\t'.join(node_ids)
            query_cmd = (
                f'az vm show -d --ids {node_ids_str} --query "{state_str}" -o json'
            )
            returncode, stdout, stderr = log_lib.run_with_log(
                query_cmd, '/dev/null', require_outputs=True, shell=True)
            logger.debug(f'{query_cmd} returned {returncode}.\n'
                         '**** STDOUT ****\n'
                         f'{stdout}\n'
                         '**** STDERR ****\n'
                         f'{stderr}')

        # NOTE: Azure cli should be handled carefully. The query command above
        # takes about 1 second to run.
        # An alternative is the following command, but it will take more than
        # 20 seconds to run.
        # query_cmd = (
        #     f'az vm list --show-details --query "['
        #     f'?tags.\\"ray-cluster-name\\" == \'{handle.cluster_name}\' '
        #     '&& tags.\\"ray-node-type\\" == \'head\'].powerState" -o tsv'
        # )

        if returncode != 0:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ClusterStatusFetchingError(
                    f'Failed to query Azure cluster {name!r} status: '
                    f'{stdout + stderr}')

        assert stdout.strip(), f'No status returned for {name!r}'

        original_statuses_list = json.loads(stdout.strip())
        if not isinstance(original_statuses_list, list):
            original_statuses_list = [original_statuses_list]
        statuses = []
        for s in original_statuses_list:
            node_status = status_map[s]
            if node_status is not None:
                statuses.append(node_status)
        return statuses
