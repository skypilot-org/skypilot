"""Azure."""
import copy
import json
import os
import subprocess
from typing import Dict, Iterator, List, Optional, Tuple

from sky import clouds
from sky.clouds.service_catalog import azure_catalog


def _run_output(cmd):
    proc = subprocess.run(cmd,
                          shell=True,
                          check=True,
                          stderr=subprocess.PIPE,
                          stdout=subprocess.PIPE)
    return proc.stdout.decode('ascii')


class Azure(clouds.Cloud):
    """Azure."""

    _REPR = 'Azure'
    _regions: List[clouds.Region] = []

    def instance_type_to_hourly_cost(self, instance_type, use_spot):
        return azure_catalog.get_hourly_cost(instance_type, use_spot=use_spot)

    def accelerators_to_hourly_cost(self, accelerators):
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

    def __repr__(self):
        return Azure._REPR

    def is_same_cloud(self, other):
        return isinstance(other, Azure)

    @classmethod
    def get_default_instance_type(cls):
        # 8 vCpus, 32 GB RAM.  Prev-gen (as of 2021) general purpose.
        return 'Standard_D8_v4'

    @classmethod
    def _get_image_config(cls, gen_version, instance_type):
        image_config = {
            'image_publisher': 'microsoft-dsvm',
            'image_offer': 'ubuntu-2004',
            'image_sku': '2004-gen2',
            'image_version': '21.11.04'
        }

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
            regions = azure_catalog.get_region_zones_for_instance_type(
                instance_type, use_spot)
        for region in regions:
            yield region, region.zones

    # TODO: factor the following three methods, as they are the same logic
    # between Azure and AWS.

    def get_accelerators_from_instance_type(
        self,
        instance_type: str,
    ) -> Optional[Dict[str, int]]:
        return azure_catalog.get_accelerators_from_instance_type(instance_type)

    def make_deploy_resources_variables(self, resources):
        r = resources
        assert not r.use_spot, \
            'Our subscription offer ID does not support spot instances.'
        # r.accelerators is cleared but .instance_type encodes the info.
        acc_dict = self.get_accelerators_from_instance_type(r.instance_type)
        if acc_dict is not None:
            custom_resources = json.dumps(acc_dict, separators=(',', ':'))
        else:
            custom_resources = None
        gen_version = azure_catalog.get_gen_version_from_instance_type(
            r.instance_type)
        image_config = self._get_image_config(gen_version, r.instance_type)
        return {
            'instance_type': r.instance_type,
            'custom_resources': custom_resources,
            'use_spot': r.use_spot,
            **image_config
        }

    def get_feasible_launchable_resources(self, resources):
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            # Treat Resources(AWS, p3.2x, V100) as Resources(AWS, p3.2x).
            resources.accelerators = None
            return [resources]

        def _make(instance_type):
            r = copy.deepcopy(resources)
            r.cloud = Azure()
            r.instance_type = instance_type
            # Setting this to None as Azure doesn't separately bill / attach
            # the accelerators.  Billed as part of the VM type.
            r.accelerators = None
            return [r]

        # Currently, handle a filter on accelerators only.
        accelerators = resources.get_accelerators()
        if accelerators is None:
            # No requirements to filter, so just return a default VM type.
            return _make(Azure.get_default_instance_type())

        assert len(accelerators) == 1, resources
        acc, acc_count = list(accelerators.items())[0]
        instance_type = azure_catalog.get_instance_type_for_accelerator(
            acc, acc_count)
        if instance_type is None:
            return []
        return _make(instance_type)

    def check_credentials(self) -> Tuple[bool, Optional[str]]:
        """Checks if the user has access credentials to this cloud."""
        help_str = (
            '\n    For more info: '
            'https://docs.microsoft.com/en-us/cli/azure/get-started-with-azure-cli'  # pylint: disable=line-too-long
        )
        # This file is required because it will be synced to remote VMs for
        # `az` to access private storage buckets.
        # `az account show` does not guarantee this file exists.
        if not os.path.isfile(os.path.expanduser('~/.azure/accessTokens.json')):
            return (
                False,
                '~/.azure/accessTokens.json does not exist. Run `az login`.' +
                help_str)
        try:
            output = _run_output('az account show --output=json')
        except subprocess.CalledProcessError:
            return False, 'Azure CLI returned error.'
        # If Azure is properly logged in, this will return something like:
        #   {"id": ..., "user": ...}
        # and if not, it will return:
        #   Please run 'az login' to setup account.
        if output.startswith('{'):
            return True, None
        return False, 'Azure credentials not set. Run `az login`.' + help_str

    def get_credential_file_mounts(self) -> Dict[str, str]:
        return {'~/.azure': '~/.azure'}
