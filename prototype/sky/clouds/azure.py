import copy
import json
from typing import Dict, Optional

from sky import clouds


class Azure(clouds.Cloud):
    _REPR = 'Azure'

    # In general, query this from the cloud.
    # This pricing is for East US region.
    # https://azureprice.net/
    _ON_DEMAND_PRICES = {
        # V100 GPU series
        'Standard_NC6s_v3': 3.06,
        'Standard_NC12s_v3': 6.12,
        'Standard_NC24s_v3': 12.24,
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
            return clouds.Cloud.UNKNOWN_COST
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
