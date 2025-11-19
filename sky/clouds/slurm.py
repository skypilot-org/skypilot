"""Slurm."""

import os
import typing
from typing import Dict, Iterator, List, Optional, Tuple, Union

from paramiko.config import SSHConfig

from sky import catalog
from sky import clouds
from sky import sky_logging
from sky.adaptors import slurm
from sky.provision.slurm import utils as slurm_utils
from sky.utils import common_utils
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils import subprocess_utils
from sky.utils import volume as volume_lib

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib

logger = sky_logging.init_logger(__name__)

CREDENTIAL_PATH = slurm_utils.DEFAULT_SLURM_PATH


@registry.CLOUD_REGISTRY.register
class Slurm(clouds.Cloud):
    """Slurm."""

    _REPR = 'Slurm'
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.AUTOSTOP: 'Stopping not supported.',
        clouds.CloudImplementationFeatures.STOP: 'Stopping not supported.',
        clouds.CloudImplementationFeatures.SPOT_INSTANCE:
            ('Spot is not supported, as Slurm API does not implement spot .'),
        clouds.CloudImplementationFeatures.MULTI_NODE:
            ('Multi-node not supported yet, as the interconnection among nodes '
             'are non-trivial on Slurm.'),
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK:
            ('Customized multiple network interfaces are not supported in '
             'Slurm.'),
    }
    _MAX_CLUSTER_NAME_LEN_LIMIT = 120
    _regions: List[clouds.Region] = []

    # Using the latest SkyPilot provisioner API to provision and check status.
    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT

    @classmethod
    def _unsupported_features_for_resources(
        cls,
        resources: 'resources_utils.Resources',
        region: Optional[str] = None,
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        del region  # unused
        # logger.critical('[BYPASS] Check Slurm's unsupported features...')
        return cls._CLOUD_UNSUPPORTED_FEATURES

    @classmethod
    def _max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        inst = slurm_utils.SlurmInstanceType.from_instance_type(instance_type)
        return inst.cpus, inst.memory

    @classmethod
    def zones_provision_loop(
        cls,
        *,
        region: str,
        num_nodes: int,
        instance_type: str,
        accelerators: Optional[Dict[str, int]] = None,
        use_spot: bool = False,
    ) -> Iterator[Optional[List[clouds.Zone]]]:
        # Always yield None for zones because Slurm does not support zones.
        # This allows handling for any region without being restricted by zone logic.
        yield None

    @classmethod
    def existing_allowed_clusters(cls) -> List[str]:
        """Get existing allowed clusters."""
        all_clusters = slurm_utils.get_all_slurm_cluster_names()
        if len(all_clusters) == 0:
            return []

        all_clusters = set(all_clusters)

        # TOWO(jwj): Add conditions to filter existing allowed clusters if any.
        existing_clusters = []
        for cluster in all_clusters:
            existing_clusters.append(cluster)

        return existing_clusters

    @classmethod
    def regions_with_offering(
        cls,
        instance_type: Optional[str],
        accelerators: Optional[Dict[str, int]],
        use_spot: bool,
        region: Optional[str],
        zone: Optional[str],
        resources: Optional['resources_lib.Resources'] = None
    ) -> List[clouds.Region]:
        del accelerators, zone, use_spot, resources  # unused
        existing_clusters = cls.existing_allowed_clusters()

        regions = []
        for cluster in existing_clusters:
            regions.append(clouds.Region(cluster))

        if region is not None:
            # Filter the regions by the given region (cluster) name.
            regions = [r for r in regions if r.name == region]

        # Check if requested instance type will fit in the cluster.
        if instance_type is None:
            return regions

        regions_to_return = []
        for r in regions:
            cluster = r.name
            # try:
            fits, reason = slurm_utils.check_instance_fits(
                cluster, instance_type)
            # except exceptions.KubeAPIUnreachableError as e:
            #     cls._log_unreachable_context(cluster, str(e))
            #     continue
            if fits:
                regions_to_return.append(r)
                continue
            logger.debug(f'Instance type {instance_type} does '
                         'not fit in the existing Slurm cluster '
                         'with cluster: '
                         f'{cluster}. Reason: {reason}')

        return regions_to_return

    def instance_type_to_hourly_cost(self,
                                     instance_type: str,
                                     use_spot: bool,
                                     region: Optional[str] = None,
                                     zone: Optional[str] = None) -> float:
        """For now, we assume zero cost for Slurm clusters."""
        return 0.0

    def accelerators_to_hourly_cost(self,
                                    accelerators: Dict[str, int],
                                    use_spot: bool,
                                    region: Optional[str] = None,
                                    zone: Optional[str] = None) -> float:
        """Returns the hourly cost of the accelerators, in dollars/hour."""
        del accelerators, use_spot, region, zone  # unused
        return 0.0

    def get_egress_cost(self, num_gigabytes: float) -> float:
        return 0.0

    def __repr__(self):
        return self._REPR

    def is_same_cloud(self, other: clouds.Cloud) -> bool:
        # Returns true if the two clouds are the same cloud type.
        return isinstance(other, Slurm)

    @classmethod
    def get_default_instance_type(cls,
                                  cpus: Optional[str] = None,
                                  memory: Optional[str] = None,
                                  disk_tier: Optional[
                                      resources_utils.DiskTier] = None,
                                  region: Optional[str] = None,
                                  zone: Optional[str] = None) -> Optional[str]:
        """Returns the default instance type for Slurm."""
        return catalog.get_default_instance_type(cpus=cpus,
                                                 memory=memory,
                                                 disk_tier=disk_tier,
                                                 region=region,
                                                 zone=zone,
                                                 clouds='slurm')

    @classmethod
    def get_accelerators_from_instance_type(
            cls, instance_type: str) -> Optional[Dict[str, Union[int, float]]]:
        inst = slurm_utils.SlurmInstanceType.from_instance_type(instance_type)
        return {
            inst.accelerator_type: inst.accelerator_count
        } if (inst.accelerator_count is not None and
              inst.accelerator_type is not None) else None

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    def make_deploy_resources_variables(
        self,
        resources: 'resources_lib.Resources',
        cluster_name: 'resources_utils.ClusterName',
        region: Optional['clouds.Region'],
        zones: Optional[List['clouds.Zone']],
        num_nodes: int,
        dryrun: bool = False,
        volume_mounts: Optional[List['volume_lib.VolumeMount']] = None,
    ) -> Dict[str, Optional[str]]:
        del cluster_name, zones, dryrun, volume_mounts  # Unused.
        if region is not None:
            cluster = region.name
        else:
            cluster = 'localcluster'
        assert cluster is not None, 'No available Slurm cluster found.'

        # cluster is our target slurmctld host.
        ssh_config = SSHConfig.from_path(os.path.expanduser(CREDENTIAL_PATH))
        ssh_config_dict = ssh_config.lookup(cluster)

        r = resources
        acc_dict = self.get_accelerators_from_instance_type(r.instance_type)
        custom_resources = resources_utils.make_ray_custom_resources_str(
            acc_dict)

        # resources.memory and cpus are none if they are not explicitly set.
        # we fetch the default values for the instance type in that case.
        s = slurm_utils.SlurmInstanceType.from_instance_type(
            resources.instance_type)
        cpus = s.cpus
        mem = s.memory
        # Optionally populate accelerator information.
        acc_count = s.accelerator_count if s.accelerator_count else 0
        acc_type = s.accelerator_type if s.accelerator_type else None

        deploy_vars = {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'cpus': str(cpus),
            'memory': str(mem),
            'accelerator_count': str(acc_count),
            'accelerator_type': acc_type,
            'slurm_cluster': cluster,
            'slurm_partition': slurm_utils.DEFAULT_PARTITION,
            # TODO(jwj): Pass SSH config in a smarter way
            'ssh_hostname': ssh_config_dict['hostname'],
            'ssh_port': ssh_config_dict.get('port', 22),
            'ssh_user': ssh_config_dict['user'],
            'slurm_proxy_command': ssh_config_dict.get('proxycommand', None),
            # TODO(jwj): Solve naming collision with 'ssh_private_key'.
            # Please refer to slurm-ray.yml.j2 'ssh' and 'auth' sections.
            'slurm_private_key': ssh_config_dict['identityfile'][0],
        }

        return deploy_vars

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> 'resources_utils.FeasibleResources':
        """Returns a list of feasible resources for the given resources."""
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            resources = resources.copy(accelerators=None)
            return ([resources], [])

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                r = resources.copy(
                    cloud=Slurm(),
                    instance_type=instance_type,
                    accelerators=None,
                )
                resource_list.append(r)
            return resource_list

        # Currently, handle a filter on accelerators only.
        accelerators = resources.accelerators

        default_instance_type = Slurm.get_default_instance_type(
            cpus=resources.cpus,
            memory=resources.memory,
            disk_tier=resources.disk_tier,
            region=resources.region,
            zone=resources.zone)

        if accelerators is None:
            chosen_instance_type = default_instance_type
        else:
            assert len(accelerators) == 1, resources

            # Build GPU-enabled instance type.
            acc_type, acc_count = list(accelerators.items())[0]

            slurm_instance_type = (slurm_utils.SlurmInstanceType.
                                   from_instance_type(default_instance_type))

            gpu_task_cpus = slurm_instance_type.cpus
            gpu_task_memory = slurm_instance_type.memory
            # if resources.cpus is None:
            #     gpu_task_cpus = self._DEFAULT_NUM_VCPUS_WITH_GPU * acc_count
            # gpu_task_memory = (float(resources.memory.strip('+')) if
            #                    resources.memory is not None else gpu_task_cpus *
            #                    self._DEFAULT_MEMORY_CPU_RATIO_WITH_GPU)

            chosen_instance_type = (
                slurm_utils.SlurmInstanceType.from_resources(
                    gpu_task_cpus, gpu_task_memory, acc_count, acc_type).name)

        # Check the availability of the specified instance type in all Slurm clusters.
        available_regions = self.regions_with_offering(
            chosen_instance_type,
            accelerators=None,
            use_spot=resources.use_spot,
            region=resources.region,
            zone=resources.zone)
        if not available_regions:
            return resources_utils.FeasibleResources([], [], None)

        return resources_utils.FeasibleResources(_make([chosen_instance_type]),
                                                 [], None)

    @classmethod
    def _check_compute_credentials(cls) -> Tuple[bool, Optional[str]]:
        """Checks if the user has access credentials to the Slurm cluster."""
        ssh_config = SSHConfig.from_path(os.path.expanduser(CREDENTIAL_PATH))
        existing_allowed_clusters = cls.existing_allowed_clusters()

        for cluster in existing_allowed_clusters:
            # Retrieve the config options for a given SlurmctldHost name alias.
            ssh_config_dict = ssh_config.lookup(cluster)

            try:
                client = slurm.SlurmClient(
                    ssh_config_dict['hostname'],
                    ssh_config_dict.get('port', 22),
                    ssh_config_dict['user'],
                    ssh_config_dict['identityfile'][0],
                    ssh_proxy_command=ssh_config_dict.get('proxycommand', None))
                info = client.info()
                logger.debug(f'Slurm cluster {cluster} sinfo: {info}')
                return (True, '')
            except Exception as e:  # pylint: disable=broad-except
                return (False, f'Credential check failed for {cluster}: '
                        f'{common_utils.format_exception(e)}')

    def get_credential_file_mounts(self) -> Dict[str, str]:
        ########
        # TODO #
        ########
        # Return dictionary of credential file paths. This may look
        # something like:
        return {}

    @classmethod
    def get_current_user_identity(cls) -> Optional[List[str]]:
        # NOTE: used for very advanced SkyPilot functionality
        # Can implement later if desired
        return None

    def instance_type_exists(self, instance_type: str) -> bool:
        return catalog.instance_type_exists(instance_type, 'slurm')

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return catalog.validate_region_zone(region, zone, clouds='slurm')

    def accelerator_in_region_or_zone(self,
                                      accelerator: str,
                                      acc_count: int,
                                      region: Optional[str] = None,
                                      zone: Optional[str] = None) -> bool:
        return catalog.accelerator_in_region_or_zone(accelerator, acc_count,
                                                     region, zone, 'slurm')
