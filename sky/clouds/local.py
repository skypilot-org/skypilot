"""Local/On-premise."""
import subprocess
import typing
from typing import Dict, Iterator, List, Optional, Tuple

from sky import clouds

if typing.TYPE_CHECKING:
    # Renaming to avoid shadowing variables.
    from sky import resources as resources_lib


def _run_output(cmd):
    proc = subprocess.run(cmd,
                          shell=True,
                          check=True,
                          stderr=subprocess.PIPE,
                          stdout=subprocess.PIPE)
    return proc.stdout.decode('ascii')


@clouds.CLOUD_REGISTRY.register
class Local(clouds.Cloud):
    """Local/on-premise cloud.

    This Cloud has the following special treatment of Cloud concepts:

    - Catalog: Does not have service catalog.
    - Region: Only one region ('Local' region).
    - Cost: Treats all compute/egress as free.
    - Instance types: Only one instance type ('on-prem' instance type).
    - Cluster: All local clusters are part of the local cloud.
    - Credentials: No checking is done (in `sky check`) and users must
        provide their own credentials instead of Sky autogenerating
        cluster credentials.
    """
    _DEFAULT_INSTANCE_TYPE = 'on-prem'
    LOCAL_REGION = clouds.Region('Local')
    _regions: List[clouds.Region] = [LOCAL_REGION]

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
        del instance_type
        del use_spot
        del accelerators  # unused
        for region in cls.regions():
            yield region, region.zones

    #### Normal methods ####

    def instance_type_to_hourly_cost(self, instance_type: str,
                                     use_spot: bool) -> float:
        # On-prem machines on Sky are assumed free
        # (minus electricity/utility bills).
        return 0.0

    def accelerators_to_hourly_cost(self, accelerators,
                                    use_spot: bool) -> float:
        # Hourly cost of accelerators is 0 for local cloud.
        return 0.0

    def get_egress_cost(self, num_gigabytes: float) -> float:
        # Egress cost from a local cluster is assumed to be 0.
        return 0.0

    def __repr__(self):
        return 'Local'

    def is_same_cloud(self, other: clouds.Cloud) -> bool:
        # Returns true if the two clouds are the same cloud type.
        return isinstance(other, Local)

    @classmethod
    def get_default_instance_type(cls) -> str:
        # There is only "1" instance type for local cloud: on-prem
        return Local._DEFAULT_INSTANCE_TYPE

    @classmethod
    def get_vcpus_from_instance_type(cls,
                                     instance_type: str) -> Optional[float]:
        return None

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, int]]:
        # This function is called, as the instance_type is `on-prem`.
        # Local cloud will return no accelerators. This is deferred to
        # the ResourceHandle, which calculates the accelerators in the cluster.
        return None

    def make_deploy_resources_variables(
            self, resources: 'resources_lib.Resources',
            region: Optional['clouds.Region'],
            zones: Optional[List['clouds.Zone']]) -> Dict[str, str]:
        return {}

    def get_feasible_launchable_resources(self,
                                          resources: 'resources_lib.Resources'):
        # The entire local cluster's resources is considered launchable, as the
        # check for task resources is deferred later.
        # The check for task resources meeting cluster resources is run in
        # cloud_vm_ray_backend._check_task_resources_smaller_than_cluster.
        resources = resources.copy(
            instance_type=Local.get_default_instance_type(),
            # Setting this to None as AWS doesn't separately bill /
            # attach the accelerators.  Billed as part of the VM type.
            accelerators=None,
        )
        return [resources], []

    def accelerator_in_region_or_zone(self,
                                      accelerator: str,
                                      acc_count: int,
                                      region: Optional[str] = None,
                                      zone: Optional[str] = None) -> bool:
        # In the public cloud case, an accelerator may not be in a region/zone.
        # in the local cloud case, however, the region/zone is defined to be the
        # location of the local cluster. This means that the local cluster's
        # accelerators are guaranteed to be found within the region.
        return True

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    def check_credentials(self) -> Tuple[bool, Optional[str]]:
        # This method is called in `sky check`.
        # As credentials are not generated by Sky (supplied by user instead),
        # this method will always return True.
        return True, None

    def get_credential_file_mounts(self) -> Dict[str, str]:
        # There are no credentials to upload to remote in Local mode.
        return {}

    def instance_type_exists(self, instance_type: str) -> bool:
        # Checks if instance_type matches on-prem, the only instance type for
        # local cloud.
        return instance_type == self.get_default_instance_type()

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        # Returns true if the region name is same as Local cloud's
        # one and only region: 'Local'.
        assert zone is None
        if region is None or region != Local.LOCAL_REGION.name:
            raise ValueError(f'Region {region!r} does not match the Local'
                             ' cloud region {Local.LOCAL_REGION.name!r}.')
