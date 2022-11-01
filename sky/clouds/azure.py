"""Azure."""
import json
import os
import subprocess
import typing
from typing import Dict, Iterator, List, Optional, Tuple

from sky import clouds
from sky.adaptors import azure
from sky.clouds import service_catalog
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky import resources

# Minimum set of files under ~/.azure that grant Azure access.
_CREDENTIAL_FILES = [
    'azureProfile.json',
    'clouds.config',
    'config',
    'msal_token_cache.json',
]


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
    _regions: List[clouds.Region] = []

    def instance_type_to_hourly_cost(self, instance_type, use_spot):
        return service_catalog.get_hourly_cost(instance_type,
                                               region=None,
                                               use_spot=use_spot,
                                               clouds='azure')

    def accelerators_to_hourly_cost(self, accelerators, use_spot):
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
    def get_default_instance_type(cls) -> str:
        # 8 vCpus, 32 GB RAM.  Prev-gen (as of 2021) general purpose.
        return 'Standard_D8_v4'

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
    def region_zones_provision_loop(
        cls,
        *,
        instance_type: Optional[str] = None,
        accelerators: Optional[Dict[str, int]] = None,
        use_spot: bool,
    ) -> Iterator[Tuple[clouds.Region, List[clouds.Zone]]]:
        del accelerators  # unused

        if instance_type is None:
            # fallback to manually specified region/zones
            regions = cls.regions()
        else:
            regions = service_catalog.get_region_zones_for_instance_type(
                instance_type, use_spot, clouds='azure')
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
    ) -> float:
        return service_catalog.get_vcpus_from_instance_type(instance_type,
                                                            clouds='azure')

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    def make_deploy_resources_variables(
            self, resources: 'resources.Resources',
            region: Optional['clouds.Region'],
            zones: Optional[List['clouds.Zone']]) -> Dict[str, str]:
        if region is None:
            assert zones is None, (
                'Set either both or neither for: region, zones.')
            region = self._get_default_region()

        region_name = region.name
        # Azure does not support specific zones.
        zones = []

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
            'zones': zones,
            **image_config
        }

    def get_feasible_launchable_resources(self, resources):
        if resources.use_spot:
            # TODO(zhwu): our azure subscription offer ID does not support spot.
            # Need to support it.
            return ([], [])
        fuzzy_candidate_list = []
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            # Treat Resources(AWS, p3.2x, V100) as Resources(AWS, p3.2x).
            resources = resources.copy(accelerators=None)
            return ([resources], fuzzy_candidate_list)

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                r = resources.copy(
                    cloud=Azure(),
                    instance_type=instance_type,
                    # Setting this to None as Azure doesn't separately bill /
                    # attach the accelerators.  Billed as part of the VM type.
                    accelerators=None)
                resource_list.append(r)
            return resource_list

        # Currently, handle a filter on accelerators only.
        accelerators = resources.accelerators
        if accelerators is None:
            # No requirements to filter, so just return a default VM type.
            return (_make([Azure.get_default_instance_type()]),
                    fuzzy_candidate_list)

        assert len(accelerators) == 1, resources
        acc, acc_count = list(accelerators.items())[0]
        (instance_list, fuzzy_candidate_list
        ) = service_catalog.get_instance_type_for_accelerator(acc,
                                                              acc_count,
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
            output = _run_output('az account show --output=json')
        except subprocess.CalledProcessError:
            return False, (
                'Azure CLI returned error. Run the following commands:'
                '\n      $ pip install skypilot[azure]  # if installed from '
                'PyPI'
                '\n    Or:'
                '\n      $ pip install .[azure]  # if installed from source'
                '\n    Credentials may also need to be set.' + help_str)
        # If Azure is properly logged in, this will return something like:
        #   {"id": ..., "user": ...}
        # and if not, it will return:
        #   Please run 'az login' to setup account.
        if output.startswith('{'):
            return True, None
        return False, 'Azure credentials not set.' + help_str

    def get_credential_file_mounts(self) -> Dict[str, str]:
        """Returns a dict of credential file paths to mount paths."""
        return {
            f'~/.azure/{filename}': f'~/.azure/{filename}'
            for filename in _CREDENTIAL_FILES
        }

    def instance_type_exists(self, instance_type):
        return service_catalog.instance_type_exists(instance_type,
                                                    clouds='azure')

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return service_catalog.validate_region_zone(region,
                                                    zone,
                                                    clouds='azure')

    def accelerator_in_region_or_zone(self,
                                      accelerator: str,
                                      acc_count: int,
                                      region: Optional[str] = None,
                                      zone: Optional[str] = None) -> bool:
        return service_catalog.accelerator_in_region_or_zone(
            accelerator, acc_count, region, zone, 'azure')

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
