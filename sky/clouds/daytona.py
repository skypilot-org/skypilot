"""Daytona provider implementation for SkyPilot.

Daytona (https://www.daytona.io) provides sandboxes: isolated cloud
environments with per-second billing and no minimum duration. GPU
sandboxes offer NVIDIA and AMD GPUs (1-8 GPUs per sandbox) in on-demand
and spot capacity; CPU sandboxes run in shared regions.
"""

import os
import typing
from typing import Any, Dict, List, Optional, Tuple, Union

from sky import catalog
from sky import clouds
from sky.provision.daytona import utils as daytona_utils
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils.resources_utils import DiskTier

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib
    from sky.utils import volume as volume_lib


@registry.CLOUD_REGISTRY.register
class Daytona(clouds.Cloud):
    """Daytona sandbox cloud provider."""

    _REPR = 'Daytona'
    _MAX_CLUSTER_NAME_LEN_LIMIT = 60

    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.STOP:
            ('Stopping clusters is not supported on '
             f'{_REPR}. Sandboxes are deleted on stop.'),
        clouds.CloudImplementationFeatures.AUTOSTOP:
            (f'Auto-stop is not supported on {_REPR}.'),
        clouds.CloudImplementationFeatures.AUTO_TERMINATE:
            (f'Auto-terminate is not supported on {_REPR}.'),
        clouds.CloudImplementationFeatures.AUTODOWN:
            (f'Auto-down is not supported on {_REPR}.'),
        clouds.CloudImplementationFeatures.MULTI_NODE:
            (f'Multi-node clusters are not supported on {_REPR}. '
             'Sandboxes are isolated; each cluster runs on one sandbox.'),
        clouds.CloudImplementationFeatures.OPEN_PORTS:
            (f'Opening ports is not supported on {_REPR}.'),
        clouds.CloudImplementationFeatures.STORAGE_MOUNTING:
            (f'Storage mounting is not supported on {_REPR}.'),
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
            (f'Custom disk tier is not supported on {_REPR}.'),
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER:
            (f'Custom network tier is not supported on {_REPR}.'),
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS:
            (f'High availability controllers are not supported on {_REPR}.'),
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER:
            (f'Disk cloning is not supported on {_REPR}.'),
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK:
            (f'Customized multiple network interfaces are not supported '
             f'on {_REPR}.'),
        clouds.CloudImplementationFeatures.LOCAL_DISK:
            (f'Local disk is not supported on {_REPR}.'),
    }

    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT

    @classmethod
    def _unsupported_features_for_resources(
        cls,
        resources: 'resources_lib.Resources',
        region: Optional[str] = None,
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        del resources, region  # unused
        return cls._CLOUD_UNSUPPORTED_FEATURES

    @classmethod
    def max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    @classmethod
    def get_credentials_path(cls) -> str:
        return daytona_utils.get_credentials_path()

    def instance_type_exists(self, instance_type: str) -> bool:
        return catalog.instance_type_exists(instance_type, 'daytona')

    @classmethod
    def regions_with_offering(
        cls,
        instance_type: str,
        accelerators: Optional[Dict[str, int]],
        use_spot: bool,
        region: Optional[str],
        zone: Optional[str],
        resources: Optional['resources_lib.Resources'] = None,
    ) -> List[clouds.Region]:
        assert zone is None, 'Daytona does not support zones.'
        del accelerators, zone, resources  # unused
        regions = catalog.get_region_zones_for_instance_type(
            instance_type, use_spot, 'daytona')
        if region is not None:
            regions = [r for r in regions if r.name == region]
        return regions

    @classmethod
    def get_vcpus_mem_from_instance_type(
            cls, instance_type: str) -> Tuple[Optional[float], Optional[float]]:
        return catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                        clouds='daytona')

    def instance_type_to_hourly_cost(
        self,
        instance_type: str,
        use_spot: bool,
        region: Optional[str] = None,
        zone: Optional[str] = None,
    ) -> float:
        return catalog.get_hourly_cost(instance_type,
                                       use_spot=use_spot,
                                       region=region,
                                       zone=zone,
                                       clouds='daytona')

    @classmethod
    def get_default_instance_type(
        cls,
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        disk_tier: Optional[DiskTier] = None,
        local_disk: Optional[str] = None,
        region: Optional[str] = None,
        zone: Optional[str] = None,
        use_spot: bool = False,
        max_hourly_cost: Optional[float] = None,
    ) -> Optional[str]:
        return catalog.get_default_instance_type(
            cpus=cpus,
            memory=memory,
            disk_tier=disk_tier,
            local_disk=local_disk,
            region=region,
            zone=zone,
            use_spot=use_spot,
            max_hourly_cost=max_hourly_cost,
            clouds='daytona',
        )

    @classmethod
    def get_accelerators_from_instance_type(
            cls, instance_type: str) -> Optional[Dict[str, Union[int, float]]]:
        return catalog.get_accelerators_from_instance_type(instance_type,
                                                           clouds='daytona')

    @classmethod
    def _check_credentials(cls) -> Tuple[bool, Optional[str]]:
        if os.environ.get(daytona_utils.ENV_API_KEY):
            return True, None
        credentials_path = cls.get_credentials_path()
        if os.path.exists(os.path.expanduser(credentials_path)):
            return True, None
        return False, (
            'Daytona API key not found. Set the '
            f'{daytona_utils.ENV_API_KEY} environment variable or create '
            f'{credentials_path} with the API key. '
            'Get an API key at https://app.daytona.io/dashboard/keys.')

    @classmethod
    def _check_compute_credentials(cls) -> Tuple[bool, Optional[str]]:
        return cls._check_credentials()

    @classmethod
    def get_credential_file_mounts(cls) -> Dict[str, str]:
        credentials_path = cls.get_credentials_path()
        expanded_path = os.path.expanduser(credentials_path)
        if os.path.exists(expanded_path):
            return {credentials_path: expanded_path}
        return {}

    def __repr__(self):
        return self._REPR

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> 'resources_utils.FeasibleResources':
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            # Instance type already encodes the accelerator on Daytona.
            resources = resources.copy(accelerators=None)
            return resources_utils.FeasibleResources([resources], [], None)

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                r = resources.copy(
                    cloud=Daytona(),
                    instance_type=instance_type,
                    # GPUs are included in the instance type.
                    accelerators=None,
                    cpus=None,
                    memory=None,
                )
                resource_list.append(r)
            return resource_list

        accelerators = resources.accelerators
        if accelerators is None:
            default_instance_type = Daytona.get_default_instance_type(
                cpus=resources.cpus,
                memory=resources.memory,
                disk_tier=resources.disk_tier,
                local_disk=resources.local_disk,
                region=resources.region,
                zone=resources.zone,
                use_spot=resources.use_spot,
                max_hourly_cost=resources.max_hourly_cost,
            )
            if default_instance_type is None:
                return resources_utils.FeasibleResources([], [], None)
            return resources_utils.FeasibleResources(
                _make([default_instance_type]), [], None)

        assert len(accelerators) == 1, resources
        acc, acc_count = list(accelerators.items())[0]
        (instance_list,
         fuzzy_candidate_list) = catalog.get_instance_type_for_accelerator(
             acc,
             acc_count,
             use_spot=resources.use_spot,
             cpus=resources.cpus,
             memory=resources.memory,
             local_disk=resources.local_disk,
             region=resources.region,
             zone=resources.zone,
             max_hourly_cost=resources.max_hourly_cost,
             clouds='daytona',
         )
        if instance_list is None:
            return resources_utils.FeasibleResources([], fuzzy_candidate_list,
                                                     None)
        return resources_utils.FeasibleResources(_make(instance_list),
                                                 fuzzy_candidate_list, None)

    def validate_region_zone(
            self, region: Optional[str],
            zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        if zone is not None:
            raise ValueError('Daytona does not support zones.')
        return catalog.validate_region_zone(region, zone, 'daytona')

    @classmethod
    def regions(cls) -> List[clouds.Region]:
        return catalog.regions('daytona')

    @classmethod
    def zones_provision_loop(
        cls,
        *,
        region: str,
        num_nodes: int,
        instance_type: str,
        accelerators: Optional[Dict[str, int]] = None,
        use_spot: bool = False,
    ):
        yield None

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    def get_egress_cost(self, num_gigabytes: float) -> float:
        return 0.0

    def accelerators_to_hourly_cost(
        self,
        accelerators: Dict[str, int],
        use_spot: bool,
        region: Optional[str],
        zone: Optional[str],
    ) -> float:
        # GPUs are billed as part of the instance type.
        return 0.0

    def make_deploy_resources_variables(
        self,
        resources: 'resources_lib.Resources',
        cluster_name: resources_utils.ClusterName,
        region: 'clouds.Region',
        zones: Optional[List['clouds.Zone']],
        num_nodes: int,
        dryrun: bool = False,
        volume_mounts: Optional[List['volume_lib.VolumeMount']] = None,
    ) -> Dict[str, Any]:
        """Returns a dict of variables for the deployment template."""
        del cluster_name, dryrun  # unused
        assert zones is None, ('Daytona does not support zones', zones)

        resources = resources.assert_launchable()
        acc_dict = self.get_accelerators_from_instance_type(
            resources.instance_type)
        custom_resources = resources_utils.make_ray_custom_resources_str(
            acc_dict)

        # When no image is specified, the Daytona default sandbox image is
        # used (it ships Python, sudo and rsync).
        image_id: Optional[str] = ''
        if resources.image_id is not None:
            docker_image = resources.extract_docker_image()
            if docker_image is not None:
                image_id = docker_image
            elif resources.region in resources.image_id:
                image_id = resources.image_id[resources.region]
            else:
                image_id = resources.image_id.get(None, '')
        image_id = image_id or ''

        return {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
            'image_id': image_id,
            'use_spot': resources.use_spot,
        }
