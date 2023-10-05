"""Local/On-premise."""
import typing
from typing import Dict, Iterator, List, Optional, Tuple

from sky import clouds
from sky import exceptions

if typing.TYPE_CHECKING:
    # Renaming to avoid shadowing variables.
    from sky import resources as resources_lib


# TODO(skypilot): remove Local now that we're using Kubernetes.
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
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.STOP:
            ('Local cloud does not support stopping instances.'),
        clouds.CloudImplementationFeatures.AUTOSTOP:
            ('Local cloud does not support stopping instances.'),
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER:
            ('Migrating disk is not supported for Local.'),
        clouds.CloudImplementationFeatures.DOCKER_IMAGE:
            ('Docker image is not supported in Local. '
             'You can try running docker command inside the '
             '`run` section in task.yaml.'),
        clouds.CloudImplementationFeatures.OPEN_PORTS:
            ('Opening ports is not supported for Local.'),
    }

    @classmethod
    def _cloud_unsupported_features(
            cls) -> Dict[clouds.CloudImplementationFeatures, str]:
        return cls._CLOUD_UNSUPPORTED_FEATURES

    @classmethod
    def max_cluster_name_length(cls) -> Optional[int]:
        return None

    @classmethod
    def regions_with_offering(cls, instance_type: str,
                              accelerators: Optional[Dict[str, int]],
                              use_spot: bool, region: Optional[str],
                              zone: Optional[str]) -> List[clouds.Region]:
        """Local cloud resources are placed in only one region."""
        del instance_type, accelerators, use_spot, region, zone
        return [cls.LOCAL_REGION]

    @classmethod
    def zones_provision_loop(
        cls,
        *,
        region: str,
        num_nodes: int,
        instance_type: str,
        accelerators: Optional[Dict[str, int]] = None,
        use_spot: bool = False,
    ) -> Iterator[None]:
        del num_nodes  # Unused.
        regions = cls.regions_with_offering(instance_type,
                                            accelerators,
                                            use_spot=use_spot,
                                            region=region,
                                            zone=None)
        for r in regions:
            assert r.zones is None, r
            yield r.zones

    #### Normal methods ####

    def instance_type_to_hourly_cost(self, instance_type: str, use_spot: bool,
                                     region: Optional[str],
                                     zone: Optional[str]) -> float:
        # On-prem machines on Sky are assumed free
        # (minus electricity/utility bills).
        return 0.0

    def accelerators_to_hourly_cost(self, accelerators, use_spot: bool,
                                    region: Optional[str],
                                    zone: Optional[str]) -> float:
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
    def get_default_instance_type(cls,
                                  cpus: Optional[str] = None,
                                  memory: Optional[str] = None,
                                  disk_tier: Optional[str] = None) -> str:
        # There is only "1" instance type for local cloud: on-prem
        del cpus, memory, disk_tier  # Unused.
        return Local._DEFAULT_INSTANCE_TYPE

    @classmethod
    def get_vcpus_mem_from_instance_type(
            cls, instance_type: str) -> Tuple[Optional[float], Optional[float]]:
        return None, None

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, int]]:
        # This function is called, as the instance_type is `on-prem`.
        # Local cloud will return no accelerators. This is deferred to
        # the ResourceHandle, which calculates the accelerators in the cluster.
        return None

    @classmethod
    def regions(cls) -> List[clouds.Region]:
        return [Local.LOCAL_REGION]

    def make_deploy_resources_variables(
            self, resources: 'resources_lib.Resources',
            cluster_name_on_cloud: str, region: Optional['clouds.Region'],
            zones: Optional[List['clouds.Zone']]) -> Dict[str, Optional[str]]:
        return {}

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> Tuple[List['resources_lib.Resources'], List[str]]:
        if resources.disk_tier is not None:
            return ([], [])
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

    @classmethod
    def check_credentials(cls) -> Tuple[bool, Optional[str]]:
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
        if zone is not None:
            raise ValueError('Local cloud does not support zones.')
        if region is None or region != Local.LOCAL_REGION.name:
            raise ValueError(f'Region {region!r} does not match the Local'
                             f' cloud region {Local.LOCAL_REGION.name!r}.')
        return region, zone

    @classmethod
    def check_disk_tier_enabled(cls, instance_type: str,
                                disk_tier: str) -> None:
        raise exceptions.NotSupportedError(
            'Local cloud does not support disk tiers.')
