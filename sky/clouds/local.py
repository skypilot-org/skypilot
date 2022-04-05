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
        for region in cls.regions():
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

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, int]]:
        return None

    def make_deploy_resources_variables(self,
                                        resources: 'resources_lib.Resources'):
        return

    def get_feasible_launchable_resources(self,
                                          resources: 'resources_lib.Resources'):
        return ([resources], [])

    def check_credentials(self) -> Tuple[bool, Optional[str]]:
        return True, None

    def get_credential_file_mounts(self) -> Tuple[Dict[str, str], List[str]]:
        return {}, []
