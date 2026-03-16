"""Mithril Cloud provider implementation for SkyPilot."""

import os
import typing
from typing import Any, Dict, List, Optional, Tuple, Union

from sky import catalog
from sky import clouds
from sky.provision.mithril import utils as mithril_utils
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils.resources_utils import DiskTier

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib
    from sky.utils import volume as volume_lib


@registry.CLOUD_REGISTRY.register
class Mithril(clouds.Cloud):
    """Mithril Cloud Provider."""

    _REPR = 'Mithril'
    _MAX_CLUSTER_NAME_LEN_LIMIT = 60

    @classmethod
    def get_credentials_path(cls) -> str:
        """Get the path to the Mithril credentials file.

        Respects XDG_CONFIG_HOME, otherwise defaults to
        ~/.config/mithril/config.yaml
        """
        return mithril_utils.get_credentials_path()

    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER:
            (f'Custom network tier is not supported yet on {_REPR}.'),
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
            (f'Custom disk tier is not supported yet on {_REPR}.'),
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS:
            (f'High availability controllers are not supported yet '
             f'on {_REPR}.'),
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER:
            (f'Disk cloning is not supported yet on {_REPR}.'),
        clouds.CloudImplementationFeatures.OPEN_PORTS:
            (f'Opening ports is not supported yet on {_REPR}.'),
        clouds.CloudImplementationFeatures.IMAGE_ID:
            (f'Custom image IDs are not supported yet on {_REPR}.'),
        clouds.CloudImplementationFeatures.HOST_CONTROLLERS:
            (f'Host controllers are not supported yet on {_REPR}.'),
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK:
            (f'Customized multiple network interfaces are not supported yet '
             f'on {_REPR}.'),
        clouds.CloudImplementationFeatures.LOCAL_DISK:
            (f'Local disk is not supported yet on {_REPR}.'),
    }

    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT

    @classmethod
    def _unsupported_features_for_resources(
        cls,
        resources: 'resources_lib.Resources',
        region: Optional[str] = None,
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        del resources, region
        return cls._CLOUD_UNSUPPORTED_FEATURES

    @classmethod
    def max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    def instance_type_exists(self, instance_type: str) -> bool:
        return catalog.instance_type_exists(instance_type, 'mithril')

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
        assert zone is None, 'Mithril does not support zones.'
        del accelerators, zone, resources  # Unused

        regions = catalog.get_region_zones_for_instance_type(
            instance_type, use_spot, 'mithril')
        if region is not None:
            regions = [r for r in regions if r.name == region]
        return regions

    @classmethod
    def get_vcpus_mem_from_instance_type(
            cls, instance_type: str) -> Tuple[Optional[float], Optional[float]]:
        return catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                        clouds='mithril')

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
                                       clouds='mithril')

    @classmethod
    def get_default_instance_type(
        cls,
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        disk_tier: Optional[DiskTier] = None,
        local_disk: Optional[str] = None,
        region: Optional[str] = None,
        zone: Optional[str] = None,
    ) -> Optional[str]:
        return catalog.get_default_instance_type(
            cpus=cpus,
            memory=memory,
            disk_tier=disk_tier,
            local_disk=local_disk,
            region=region,
            zone=zone,
            clouds='mithril',
        )

    @classmethod
    def get_accelerators_from_instance_type(
            cls, instance_type: str) -> Optional[Dict[str, Union[int, float]]]:
        return catalog.get_accelerators_from_instance_type(instance_type,
                                                           clouds='mithril')

    @classmethod
    def _check_credentials(cls) -> Tuple[bool, Optional[str]]:
        api_key = os.environ.get('MITHRIL_API_KEY')
        project_id = os.environ.get('MITHRIL_PROJECT')

        # If both env vars are set, credentials are valid
        if api_key and project_id:
            return True, None

        # Fall back to checking the config file
        credentials_path = cls.get_credentials_path()
        if os.path.exists(os.path.expanduser(credentials_path)):
            return True, None

        return False, (f'Mithril credentials not found at {credentials_path}.')

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
            # Instance type already describes the accelerator on Mithril.
            resources = resources.copy(accelerators=None)
            return resources_utils.FeasibleResources([resources], [], None)

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                r = resources.copy(
                    cloud=Mithril(),
                    instance_type=instance_type,
                    # Setting this to None as Mithril doesn't separately bill /
                    # attach the accelerators. Billed as part of the VM type.
                    accelerators=None,
                    cpus=None,
                    memory=None,
                )
                resource_list.append(r)
            return resource_list

        accelerators = resources.accelerators
        if accelerators is None:
            default_instance_type = Mithril.get_default_instance_type(
                cpus=resources.cpus,
                memory=resources.memory,
                disk_tier=resources.disk_tier,
                local_disk=resources.local_disk,
                region=resources.region,
                zone=resources.zone,
            )
            if default_instance_type is None:
                return resources_utils.FeasibleResources([], [], None)
            else:
                return resources_utils.FeasibleResources(
                    _make([default_instance_type]), [], None)

        assert len(accelerators) == 1, resources
        acc, acc_count = list(accelerators.items())[0]
        (instance_list,
         fuzzy_candidate_list) = (catalog.get_instance_type_for_accelerator(
             acc,
             acc_count,
             use_spot=resources.use_spot,
             cpus=resources.cpus,
             memory=resources.memory,
             local_disk=resources.local_disk,
             region=resources.region,
             zone=resources.zone,
             clouds='mithril',
         ))
        if instance_list is None:
            return resources_utils.FeasibleResources([], fuzzy_candidate_list,
                                                     None)
        return resources_utils.FeasibleResources(_make(instance_list),
                                                 fuzzy_candidate_list, None)

    def validate_region_zone(
            self, region: Optional[str],
            zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        if zone is not None:
            raise ValueError('Mithril does not support zones.')
        return catalog.validate_region_zone(region, zone, 'mithril')

    @classmethod
    def regions(cls) -> List[clouds.Region]:
        """Returns the list of regions in Mithril's catalog."""
        return catalog.regions('mithril')

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

    def get_egress_cost(self, num_gigabytes: float):
        return 0.0

    def accelerators_to_hourly_cost(
        self,
        accelerators: Dict[str, int],
        use_spot: bool,
        region: Optional[str],
        zone: Optional[str],
    ) -> float:
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
        del cluster_name  # unused
        assert zones is None, ('Mithril does not support zones', zones)

        resources = resources.assert_launchable()
        # resources.accelerators is cleared but .instance_type encodes the info.
        acc_dict = self.get_accelerators_from_instance_type(
            resources.instance_type)
        custom_resources = resources_utils.make_ray_custom_resources_str(
            acc_dict)

        if dryrun:
            profile = ''
            project_id = ''
        else:
            # Get current Mithril profile and project for storing in cluster
            # YAML. These are persisted so that status queries use the correct
            # API server even if the user switches profiles later.
            config = mithril_utils.resolve_current_config()
            profile = mithril_utils.get_current_profile() or ''
            project_id = config['project_id']

        resources_vars: Dict[str, Any] = {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
            'mithril_profile': profile,
            'mithril_project_id': project_id,
        }

        if acc_dict is not None:
            # Mithril's VM images may not register nvidia-container-runtime with
            # Docker in the standard way (e.g., via /etc/docker/daemon.json).
            # SkyPilot's automatic GPU detection checks `docker info` for
            # 'nvidia-container-runtime', which may not be present even though
            # `--gpus all` works. We explicitly add it here to ensure Docker
            # containers can access GPUs.
            resources_vars['docker_run_options'] = ['--gpus all']

        return resources_vars
