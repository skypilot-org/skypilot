"""Local/On-Premise."""
import os
import subprocess
import typing
from typing import Dict, Iterator, List, Optional, Tuple
import yaml

from sky import clouds

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


def get_local_cloud(cloud: str):
    if not os.path.exists(os.path.expanduser(f'~/.sky/local/{cloud}.yml')):
        return None
    local_cloud_type = clouds.Local()
    local_cloud_type.set_cloud_name(cloud)
    return local_cloud_type


def get_local_ips(cloud: str):
    local_cluster_path = os.path.expanduser(f'~/.sky/local/{cloud}.yml')
    with open(local_cluster_path, 'r') as f:
        config = yaml.safe_load(f)
    ips = config['cluster']['ips']
    if isinstance(ips, str):
        ips = [ips]
    return ips


class Local(clouds.Cloud):
    """Amazon Web Services."""

    _REPR = 'Local'
    _regions: List[clouds.Region] = [clouds.Region('Local')]

    def __init__(self):
        self.cloud_name = Local._REPR

    def set_cloud_name(self, cloud: str):
        self.cloud_name = cloud

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
        assert instance_type is None and not use_spot
        for region in cls.regions():
            yield region, region.zones

    #### Normal methods ####

    def instance_type_to_hourly_cost(self, instance_type: str, use_spot: bool):
        # On-prem machines on Sky are assumed free
        return 0.0

    def accelerators_to_hourly_cost(self, accelerators):
        return 0

    def get_egress_cost(self, num_gigabytes: float):
        return 0.0

    def __repr__(self):
        return self.cloud_name

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

    def instance_type_exists(self, instance_type):
        return True

    def region_exists(self, region: str) -> bool:
        return True
