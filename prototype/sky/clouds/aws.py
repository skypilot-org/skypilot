import copy
import json
from typing import Dict, Optional

from sky import clouds
from sky.clouds.service_catalog import aws_catalog


class AWS(clouds.Cloud):
    _REPR = 'AWS'

    # TODO: make aws_catalog support this.
    _ACCELERATORS_DIRECTORY = {
        ('V100', 1): 'p3.2xlarge',
        ('V100', 4): 'p3.8xlarge',
        ('V100', 8): 'p3.16xlarge',
    }

    def instance_type_to_hourly_cost(self, instance_type):
        return aws_catalog.get_hourly_cost(instance_type)

    def get_egress_cost(self, num_gigabytes):
        # In general, query this from the cloud:
        #   https://aws.amazon.com/s3/pricing/
        # NOTE: egress from US East (Ohio).
        # NOTE: Not accurate as the pricing tier is based on cumulative monthly usage.
        if num_gigabytes > 150 * 1024:
            return 0.05 * num_gigabytes
        cost = 0.0
        if num_gigabytes >= 50 * 1024:
            cost += (num_gigabytes - 50 * 1024) * 0.07
            num_gigabytes -= 50 * 1024

        if num_gigabytes >= 10 * 1024:
            cost += (num_gigabytes - 10 * 1024) * 0.085
            num_gigabytes -= 10 * 1024

        if num_gigabytes > 1:
            cost += (num_gigabytes - 1) * 0.09

        cost += 0.0
        return cost

    def __repr__(self):
        return AWS._REPR

    def is_same_cloud(self, other):
        return isinstance(other, AWS)

    @classmethod
    def get_default_instance_type(cls):
        return 'm4.2xlarge'

    # TODO: factor the following three methods, as they are the same logic
    # between Azure and AWS.

    def get_accelerators_from_instance_type(
            self,
            instance_type: str,
    ) -> Optional[Dict[str, int]]:
        """Returns {acc: acc_count} held by 'instance_type', if any."""
        inverse = {v: k for k, v in AWS._ACCELERATORS_DIRECTORY.items()}
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
            'num_nodes': task.num_nodes,
        }

    def get_feasible_launchable_resources(self, resources):
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            # Treat Resources(AWS, p3.2x, V100) as Resources(AWS, p3.2x).
            resources.accelerators = None
            return [resources]

        def _make(instance_type):
            r = copy.deepcopy(resources)
            r.cloud = AWS()
            r.instance_type = instance_type
            # Setting this to None as AWS doesn't separately bill / attach the
            # accelerators.  Billed as part of the VM type.
            r.accelerators = None
            return [r]

        # Currently, handle a filter on accelerators only.
        accelerators = resources.get_accelerators()
        if accelerators is None:
            # No requirements to filter, so just return a default VM type.
            return _make(AWS.get_default_instance_type())

        assert len(accelerators) == 1, resources
        acc, acc_count = list(accelerators.items())[0]
        instance_type = aws_catalog.get_instance_type_for_gpu(acc, acc_count)
        if instance_type is None:
            return []
        return _make(instance_type)
