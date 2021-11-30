import copy
import json
from typing import Dict, Iterator, List, Optional, Tuple

from sky import clouds
from sky.clouds.service_catalog import azure_catalog


class Azure(clouds.Cloud):
    _REPR = 'Azure'
    _regions: List[clouds.Region] = []

    def instance_type_to_hourly_cost(self, instance_type, use_spot):
        return azure_catalog.get_hourly_cost(instance_type, use_spot=use_spot)

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
        return azure_catalog.get_accelerators_from_instance_type(instance_type)

    def make_deploy_resources_variables(self, task):
        r = task.best_resources
        assert not r.use_spot, f"We currently do not support spot instances for Azure"
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
        instance_type = azure_catalog.get_instance_type_for_accelerator(
            acc, acc_count)
        if instance_type is None:
            return []
        return _make(instance_type)
