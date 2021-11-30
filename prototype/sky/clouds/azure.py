import copy
import json
from typing import Dict, Iterator, List, Optional, Tuple

from sky import clouds


class Azure(clouds.Cloud):
    _REPR = 'Azure'
    _regions: List[clouds.Region] = []

    # In general, query this from the cloud.
    # This pricing is for East US region.
    # https://azureprice.net/
    _ON_DEMAND_PRICES = {
        'Standard_D2_v4': 0.096,
        # V100 GPU series
        'Standard_NC6s_v3': 3.06,
        'Standard_NC12s_v3': 6.12,
        'Standard_NC24s_v3': 12.24,
    }
    _SPOT_PRICES = {
        'Standard_D2_v4': 0.0237,
        # V100 GPU series
        'Standard_NC6s_v3': 0.7154,
        'Standard_NC12s_v3': 1.4309,
        'Standard_NC24s_v3': 2.8617,
    }

    # TODO: add other GPUs
    _ACCELERATORS_DIRECTORY = {
        ('V100', 1): 'Standard_NC6s_v3',
        ('V100', 2): 'Standard_NC12s_v3',
        ('V100', 4): 'Standard_NC24s_v3',
    }

    def instance_type_to_hourly_cost(self, instance_type, use_spot):
        # TODO: use_spot support
        if use_spot:
            return Azure._SPOT_PRICES[instance_type]
        return Azure._ON_DEMAND_PRICES[instance_type]

    def get_egress_cost(self, num_gigabytes):
        # In general, query this from the cloud:
        #   https://azure.microsoft.com/en-us/pricing/details/bandwidth/
        # NOTE: egress from US East.
        # NOTE: Not accurate as the pricing tier is based on cumulative monthly usage.
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
        return 'Standard_D2_v4'

    @classmethod
    def regions(cls) -> List[clouds.Region]:
        if not cls._regions:
            # https://cloud.google.com/compute/docs/regions-zones
            cls._regions = [
                clouds.Region('centralus').set_zones([
                    clouds.Zone('1'),
                    clouds.Zone('2'),
                    clouds.Zone('3'),
                ]),
                clouds.Region('eastus').set_zones([
                    clouds.Zone('1'),
                    clouds.Zone('2'),
                    clouds.Zone('3'),
                ]),
                clouds.Region('eastus2').set_zones([
                    clouds.Zone('1'),
                    clouds.Zone('2'),
                    clouds.Zone('3'),
                ]),
                clouds.Region('northcentralus').set_zones([
                    clouds.Zone('1'),
                    clouds.Zone('2'),
                    clouds.Zone('3'),
                ]),
                clouds.Region('southcentralus').set_zones([
                    clouds.Zone('1'),
                    clouds.Zone('2'),
                    clouds.Zone('3'),
                ]),
                clouds.Region('westcentralus').set_zones([
                    clouds.Zone('1'),
                    clouds.Zone('2'),
                    clouds.Zone('3'),
                ]),
                clouds.Region('westus').set_zones([
                    clouds.Zone('1'),
                    clouds.Zone('2'),
                    clouds.Zone('3'),
                ]),
                clouds.Region('westus2').set_zones([
                    clouds.Zone('1'),
                    clouds.Zone('2'),
                    clouds.Zone('3'),
                ]),
            ]
        return cls._regions

    @classmethod
    def region_zones_provision_loop(
            cls) -> Iterator[Tuple[clouds.Region, List[clouds.Zone]]]:
        for region in cls.regions():
            yield region, region.zones

    # TODO: factor the following three methods, as they are the same logic
    # between Azure and AWS.

    def get_accelerators_from_instance_type(
            self,
            instance_type: str,
    ) -> Optional[Dict[str, int]]:
        """Returns {acc: acc_count} held by 'instance_type', if any."""
        inverse = {v: k for k, v in Azure._ACCELERATORS_DIRECTORY.items()}
        res = inverse.get(instance_type)
        if res is None:
            return res
        return {res[0]: res[1]}

    def make_deploy_resources_variables(self, task):
        r = task.best_resources
        # r.accelerators is cleared but .instance_type encodes the info.
        acc_dict = self.get_accelerators_from_instance_type(r.instance_type)
        if acc_dict is not None:
            custom_resources = json.dumps(acc_dict, separators=(',', ':'))
        else:
            custom_resources = None
        return {
            'instance_type': r.instance_type,
            'custom_resources': custom_resources,
            'use_spot': r.use_spot,
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
        instance_type = Azure._ACCELERATORS_DIRECTORY.get((acc, acc_count))
        if instance_type is None:
            return []
        return _make(instance_type)
