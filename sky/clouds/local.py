"""Amazon Web Services."""
import copy
import json
import os
import subprocess
import typing
from typing import Dict, Iterator, List, Optional, Tuple

from sky import clouds
from sky.clouds import service_catalog

if typing.TYPE_CHECKING:
    # renaming to avoid shadowing variables
    from sky import resources as resources_lib


def _run_output(cmd):
    proc = subprocess.run(cmd,
                          shell=True,
                          check=True,
                          stderr=subprocess.PIPE,
                          stdout=subprocess.PIPE)
    return proc.stdout.decode('ascii')


class Local(clouds.Cloud):
    """Amazon Web Services."""

    _REPR = 'Local'
    _regions: List[clouds.Region] = [clouds.Region('Local')]

    @classmethod
    def regions(cls):
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
        for region in regions:
            yield region, region.zones

    #### Normal methods ####

    def instance_type_to_hourly_cost(self, instance_type: str, use_spot: bool):
        return 0.0

    def accelerators_to_hourly_cost(self, accelerators):
        return 0

    def get_egress_cost(self, num_gigabytes: float):
        return 0.0

    def __repr__(self):
        return Local._REPR

    def is_same_cloud(self, other: clouds.Cloud):
        return isinstance(other, Local)

    @classmethod
    def get_default_instance_type(cls) -> str:
        return 'on-prem'

    # TODO: factor the following three methods, as they are the same logic
    # between Azure and AWS.
    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, int]]:
        return service_catalog.get_accelerators_from_instance_type(
            instance_type, clouds='aws')

    def make_deploy_resources_variables(self,
                                        resources: 'resources_lib.Resources'):
        r = resources
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

    def get_feasible_launchable_resources(self,
                                          resources: 'resources_lib.Resources'):
        fuzzy_candidate_list = []
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            # Treat Resources(AWS, p3.2x, V100) as Resources(AWS, p3.2x).
            resources.accelerators = None
            return ([resources], fuzzy_candidate_list)

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                r = copy.deepcopy(resources)
                r.cloud = AWS()
                r.instance_type = instance_type
                # Setting this to None as AWS doesn't separately bill / attach
                # the accelerators.  Billed as part of the VM type.
                r.accelerators = None
                resource_list.append(r)
            return resource_list

        # Currently, handle a filter on accelerators only.
        accelerators = resources.accelerators
        if accelerators is None:
            # No requirements to filter, so just return a default VM type.
            return (_make([AWS.get_default_instance_type()]),
                    fuzzy_candidate_list)

        assert len(accelerators) == 1, resources
        acc, acc_count = list(accelerators.items())[0]
        (instance_list, fuzzy_candidate_list
        ) = service_catalog.get_instance_type_for_accelerator(acc,
                                                              acc_count,
                                                              clouds='aws')
        if instance_list is None:
            return ([], fuzzy_candidate_list)
        return (_make(instance_list), fuzzy_candidate_list)

    def check_credentials(self) -> Tuple[bool, Optional[str]]:
        return True, None

    def get_credential_file_mounts(self) -> Tuple[Dict[str, str], List[str]]:
        return {}, []
