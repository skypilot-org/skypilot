"""Azure."""
import json
import os
import subprocess
import typing
from typing import Dict, Iterator, List, Optional, Tuple

from sky import clouds
from sky import exceptions
from sky import sky_logging
from sky.adaptors import azure
from sky.clouds import service_catalog
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

    _regions: List[clouds.Region] = []

    @classmethod
    def _cloud_unsupported_features(
            cls) -> Dict[clouds.CloudImplementationFeatures, str]:
        return dict()

    @classmethod
    def _max_cluster_name_length(cls) -> int:
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

    def get_egress_cost(self, num_gigabytes):
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
    def get_default_instance_type(cls,
                                  cpus: Optional[str] = None) -> Optional[str]:
        return service_catalog.get_default_instance_type(cpus=cpus,
                                                         clouds='azure')

    def _get_image_config(self, gen_version, instance_type):
        # az vm image list \
        #  --publisher microsoft-dsvm --all --output table
        # nvidia-driver: 495.29.05, cuda: 11.5

        # The latest image 2022.09.14/2022.08.11/22.06.10/22.05.11/
        # 22.04.27/22.04.05 has even older nvidia driver 470.57.02,
        # cuda: 11.4
        image_config = {
            'image_publisher': 'microsoft-dsvm',
            'image_offer': 'ubuntu-2004',
            'image_sku': '2004-gen2',
            'image_version': '21.11.04'
        }

        # ubuntu-2004 v21.10.21 and v21.11.04 do not work on K80
        # due to an NVIDIA driver issue.
        acc = self.get_accelerators_from_instance_type(instance_type)
        if acc is not None:
            acc_name = list(acc.keys())[0]
            if acc_name == 'K80':
                image_config['image_version'] = '21.08.30'

        # ubuntu-2004 does not work on A100
        if instance_type in [
                'Standard_ND96asr_v4', 'Standard_ND96amsr_A100_v4'
        ]:
            image_config['image_offer'] = 'ubuntu-hpc'
            image_config['image_sku'] = '2004'
            image_config['image_version'] = '20.04.2021120101'
        if gen_version == 'V1':
            image_config['image_sku'] = '2004'
        return image_config

    @classmethod
    def regions(cls) -> List[clouds.Region]:
        # NOTE on zones: Ray Autoscaler does not support specifying
        # availability zones, and Azure CLI will try launching VMs in all
        # zones. Hence for our purposes we do not keep track of zones.
        if not cls._regions:
            cls._regions = [
                clouds.Region('centralus'),
                clouds.Region('eastus'),
                clouds.Region('eastus2'),
                clouds.Region('northcentralus'),
                clouds.Region('southcentralus'),
                clouds.Region('westcentralus'),
                clouds.Region('westus'),
                clouds.Region('westus2'),
            ]
        return cls._regions

    @classmethod
    def regions_with_offering(cls, instance_type: Optional[str],
                              accelerators: Optional[Dict[str, int]],
                              use_spot: bool, region: Optional[str],
                              zone: Optional[str]) -> List[clouds.Region]:
        del accelerators  # unused
        if instance_type is None:
            # Fall back to default regions
            regions = cls.regions()
        else:
            regions = service_catalog.get_region_zones_for_instance_type(
                instance_type, use_spot, 'azure')

        if region is not None:
            regions = [r for r in regions if r.name == region]
        if zone is not None:
            for r in regions:
                r.set_zones([z for z in r.zones if z.name == zone])
            regions = [r for r in regions if r.zones]
        return regions

    @classmethod
    def region_zones_provision_loop(
        cls,
        *,
        instance_type: Optional[str] = None,
        accelerators: Optional[Dict[str, int]] = None,
        use_spot: bool = False,
    ) -> Iterator[Tuple[clouds.Region, List[clouds.Zone]]]:
        regions = cls.regions_with_offering(instance_type,
                                            accelerators,
                                            use_spot,
                                            region=None,
                                            zone=None)
        for region in regions:
            yield region, region.zones

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
    def get_vcpus_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[float]:
        return service_catalog.get_vcpus_from_instance_type(instance_type,
                                                            clouds='azure')

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    def make_deploy_resources_variables(
            self, resources: 'resources.Resources',
            region: Optional['clouds.Region'],
            zones: Optional[List['clouds.Zone']]) -> Dict[str, Optional[str]]:
        if region is None:
            assert zones is None, (
                'Set either both or neither for: region, zones.')
            region = self._get_default_region()

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
        from sky.clouds.service_catalog import azure_catalog  # pylint: disable=import-outside-toplevel
        gen_version = azure_catalog.get_gen_version_from_instance_type(
            r.instance_type)
        image_config = self._get_image_config(gen_version, r.instance_type)
        return {
            'instance_type': r.instance_type,
            'custom_resources': custom_resources,
            'use_spot': r.use_spot,
            'region': region_name,
            # Azure does not support specific zones.
            'zones': None,
            **image_config
        }

    def get_feasible_launchable_resources(self, resources):
        if resources.use_spot:
            # TODO(zhwu): our azure subscription offer ID does not support spot.
            # Need to support it.
            return ([], [])
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            # Treat Resources(AWS, p3.2x, V100) as Resources(AWS, p3.2x).
            resources = resources.copy(accelerators=None)
            return ([resources], [])

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                r = resources.copy(
                    cloud=Azure(),
                    instance_type=instance_type,
                    # Setting this to None as Azure doesn't separately bill /
                    # attach the accelerators.  Billed as part of the VM type.
                    accelerators=None,
                    cpus=None,
                )
                resource_list.append(r)
            return resource_list

        # Currently, handle a filter on accelerators only.
        accelerators = resources.accelerators
        if accelerators is None:
            # Return a default instance type with the given number of vCPUs.
            default_instance_type = Azure.get_default_instance_type(
                cpus=resources.cpus)
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
            use_spot=resources.use_spot,
            region=resources.region,
            zone=resources.zone,
            clouds='azure')
        if instance_list is None:
            return ([], fuzzy_candidate_list)
        return (_make(instance_list), fuzzy_candidate_list)

    def check_credentials(self) -> Tuple[bool, Optional[str]]:
        """Checks if the user has access credentials to this cloud."""
        help_str = (
            ' Run the following commands:'
            '\n      $ az login'
            '\n      $ az account set -s <subscription_id>'
            '\n    For more info: '
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
        except subprocess.CalledProcessError:
            return False, (
                # TODO(zhwu): Change the installation hint to from PyPI.
                'Azure CLI returned error. Run the following commands:'
                '\n      $ pip install skypilot[azure]'
                '\n    Credentials may also need to be set.' + help_str)
        # If Azure is properly logged in, this will return the account email
        # address + subscription ID.
        try:
            self.get_current_user_identity()
        except exceptions.CloudUserIdentityError:
            return False, 'Azure credential is not set.' + help_str
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

    def get_current_user_identity(self) -> Optional[str]:
        """Returns the cloud user identity."""
        # This returns the user's email address + [subscription_id].
        retry_cnt = 0
        while True:
            retry_cnt += 1
            try:
                import knack  # pylint: disable=import-outside-toplevel
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
            project_id = self.get_project_id()
        except (ModuleNotFoundError, RuntimeError) as e:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.CloudUserIdentityError(
                    'Failed to get Azure project ID.') from e
        return f'{account_email} [subscription_id={project_id}]'

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
