"""Lium Cloud."""

import json
import typing
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from sky import catalog
from sky import clouds
from sky.provision.lium import lium_utils
from sky.utils import registry
from sky.utils import resources_utils

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib
    from sky.utils import volume as volume_lib

_CREDENTIAL_FILE = lium_utils.CREDENTIAL_PATH


@registry.CLOUD_REGISTRY.register
class Lium(clouds.Cloud):
    """Lium GPU marketplace.

    Lium (https://lium.io) rents GPU nodes from the providers of Bittensor
    subnet 51. A node runs the workload in a container, which Lium calls a pod.
    """

    _REPR = 'Lium'
    # Lium pod names are free-form; the limit keeps the name readable.
    _MAX_CLUSTER_NAME_LEN_LIMIT = 120

    # yapf: disable
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.STOP:
            'Stopping a pod is not supported on Lium.',
        clouds.CloudImplementationFeatures.MULTI_NODE:
            'Multi-node clusters are not supported on Lium.',
        clouds.CloudImplementationFeatures.SPOT_INSTANCE:
            'Spot instances are not supported on Lium.',
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
            'Custom disk tiers are not supported on Lium.',
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER:
            'Custom network tiers are not supported on Lium.',
        clouds.CloudImplementationFeatures.STORAGE_MOUNTING:
            'Object storage mounting is not supported on Lium.',
        clouds.CloudImplementationFeatures.HOST_CONTROLLERS:
            'Host controllers are not supported on Lium.',
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS:
            'High availability controllers are not supported on Lium.',
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER:
            'Disk cloning is not supported on Lium.',
        clouds.CloudImplementationFeatures.IMAGE_ID:
            'Custom image IDs are not supported on Lium.',
        clouds.CloudImplementationFeatures.DOCKER_IMAGE:
            'Docker images are not supported on Lium yet.',
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK:
            'Custom network interfaces are not supported on Lium.',
        clouds.CloudImplementationFeatures.LOCAL_DISK:
            'Local disk is not supported on Lium.',
    }
    # yapf: enable

    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT
    OPEN_PORTS_VERSION = clouds.OpenPortsVersion.LAUNCH_ONLY

    @classmethod
    def _unsupported_features_for_resources(
        cls,
        resources: 'resources_lib.Resources',
        region: Optional[str] = None,
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        del resources, region  # unused
        return cls._CLOUD_UNSUPPORTED_FEATURES

    @classmethod
    def _max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

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
        del accelerators, resources  # unused
        assert zone is None, 'Lium does not support zones.'
        if use_spot:
            return []
        regions = catalog.get_region_zones_for_instance_type(instance_type,
                                                             use_spot,
                                                             clouds='lium')
        if region is not None:
            regions = [r for r in regions if r.name == region]
        return regions

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
        del num_nodes  # unused
        if use_spot:
            return
        regions = cls.regions_with_offering(instance_type, accelerators,
                                            use_spot, region, None)
        for r in regions:
            assert r.zones is None, r
            yield r.zones

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        return catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                        clouds='lium')

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, Union[int, float]]]:
        return catalog.get_accelerators_from_instance_type(instance_type,
                                                           clouds='lium')

    @classmethod
    def get_default_instance_type(
        cls,
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        disk_tier: Optional[resources_utils.DiskTier] = None,
        local_disk: Optional[str] = None,
        region: Optional[str] = None,
        zone: Optional[str] = None,
        use_spot: bool = False,
        max_hourly_cost: Optional[float] = None,
    ) -> Optional[str]:
        del disk_tier, local_disk  # Lium has no disk tiers.
        return catalog.get_default_instance_type(
            cpus=cpus,
            memory=memory,
            disk_tier=None,
            region=region,
            zone=zone,
            use_spot=use_spot,
            max_hourly_cost=max_hourly_cost,
            clouds='lium')

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    def instance_type_exists(self, instance_type: str) -> bool:
        return catalog.instance_type_exists(instance_type, 'lium')

    def instance_type_to_hourly_cost(self,
                                     instance_type: str,
                                     use_spot: bool,
                                     region: Optional[str] = None,
                                     zone: Optional[str] = None) -> float:
        return catalog.get_hourly_cost(instance_type,
                                       use_spot=use_spot,
                                       region=region,
                                       zone=zone,
                                       clouds='lium')

    def accelerators_to_hourly_cost(self,
                                    accelerators: Dict[str, int],
                                    use_spot: bool,
                                    region: Optional[str] = None,
                                    zone: Optional[str] = None) -> float:
        del accelerators, use_spot, region, zone  # unused
        # The GPUs are part of the node, so their price is in the node price.
        return 0.0

    def get_egress_cost(self, num_gigabytes: float) -> float:
        del num_gigabytes  # unused
        # Lium does not bill egress.
        return 0.0

    def __repr__(self) -> str:
        return self._REPR

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
        del cluster_name, zones, num_nodes, dryrun, volume_mounts  # unused
        resources = resources.assert_launchable()
        deploy_vars: Dict[str, Any] = {
            'instance_type': resources.instance_type,
            'region': region.name,
        }
        if resources.accelerators is not None:
            deploy_vars['custom_resources'] = json.dumps(resources.accelerators,
                                                         separators=(',', ':'))
        return deploy_vars

    def get_credential_file_mounts(self) -> Dict[str, str]:
        return {_CREDENTIAL_FILE: _CREDENTIAL_FILE}

    @classmethod
    def _check_compute_credentials(
            cls) -> Tuple[bool, Optional[Union[str, Dict[str, str]]]]:
        """Checks that an API key is in place."""
        if lium_utils.read_api_key() is None:
            return False, (
                'API key not found \n'  # First line is indented by 4 spaces
                '    Credentials can be set up by running: \n'
                '        $ pip install lium.io\n'
                '        $ lium init\n'
                f'    Get an API key at https://lium.io. The key is read from '
                f'{_CREDENTIAL_FILE} or '
                f'${lium_utils.API_KEY_ENV_VAR}.')

        return True, None

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> 'resources_utils.FeasibleResources':
        if resources.use_spot:
            return resources_utils.FeasibleResources(
                [], [], 'Lium does not support spot instances.')

        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            return resources_utils.FeasibleResources([resources],
                                                     [resources.instance_type],
                                                     None)

        accelerators = resources.accelerators
        if accelerators is None:
            instance_type = self.get_default_instance_type(
                cpus=resources.cpus,
                memory=resources.memory,
                disk_tier=resources.disk_tier,
                region=resources.region,
                zone=resources.zone,
                use_spot=resources.use_spot,
                max_hourly_cost=resources.max_hourly_cost)
            if instance_type is None:
                return resources_utils.FeasibleResources([], [], None)
            return resources_utils.FeasibleResources(
                [self._instance_type_resources(resources, instance_type)], [],
                None)

        acc_name, acc_count = list(accelerators.items())[0]
        instance_types, fuzzy_candidates = (
            catalog.get_instance_type_for_accelerator(
                acc_name,
                acc_count,
                cpus=resources.cpus,
                memory=resources.memory,
                use_spot=resources.use_spot,
                region=resources.region,
                zone=resources.zone,
                max_hourly_cost=resources.max_hourly_cost,
                clouds='lium'))
        if not instance_types:
            return resources_utils.FeasibleResources([], fuzzy_candidates, None)
        launchable = [
            self._instance_type_resources(resources, instance_type)
            for instance_type in instance_types
        ]
        return resources_utils.FeasibleResources(launchable, fuzzy_candidates,
                                                 None)

    def _instance_type_resources(
            self, resources: 'resources_lib.Resources',
            instance_type: str) -> 'resources_lib.Resources':
        """Pins a resource request to one instance type of this cloud."""
        return resources.copy(
            cloud=Lium(),
            instance_type=instance_type,
            accelerators=resources.accelerators,
            cpus=None,
            memory=None,
        )
