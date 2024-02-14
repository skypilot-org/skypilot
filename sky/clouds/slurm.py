"""Slurm."""
import json
import os
import typing
from typing import Dict, Iterator, List, Optional, Tuple

from sky import clouds
from sky import exceptions
from sky import sky_logging
from sky.clouds import service_catalog
from sky.provision.slurm import utils as slurm_utils
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import resources_utils

if typing.TYPE_CHECKING:
    # Renaming to avoid shadowing variables.
    from sky import resources as resources_lib

logger = sky_logging.init_logger(__name__)

CREDENTIAL_PATH = '~/.slurm/config.yaml'


@clouds.CLOUD_REGISTRY.register
class Slurm(clouds.Cloud):
    """Slurm."""

    # Timeout for resource provisioning. This timeout determines how long to
    # wait for sbatch to be in pending status before giving up.
    # Note that this timeout includes time taken by the Slurm scheduler
    # itself, which can be upto 2-3 seconds.
    # TODO(zhwu): Make the timeout configurable.
    TIMEOUT = 10

    _DEFAULT_NUM_VCPUS = 2
    _DEFAULT_MEMORY_CPU_RATIO = 1
    _DEFAULT_MEMORY_CPU_RATIO_WITH_GPU = 4  # Allocate more memory for GPU tasks

    _REPR = 'Slurm'
    _SINGLETON_REGION = 'slurm'
    _regions: List[clouds.Region] = [clouds.Region(_SINGLETON_REGION)]
    _CLOUD_UNSUPPORTED_FEATURES = {
        # TODO(zhwu): Stopping might be possible to implement with
        # scontrol suspend <jobid> and scontrol resume <jobid>
        clouds.CloudImplementationFeatures.STOP: 'Slurm does not '
                                                 'support stopping VMs.',
        clouds.CloudImplementationFeatures.SPOT_INSTANCE: 'Spot instances are '
                                                          'not supported in '
                                                          'Slurm.',
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER: 'Custom disk '
                                                             'tiers are not '
                                                             'supported in '
                                                             'Slurm.',
    }

    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT

    @classmethod
    def _unsupported_features_for_resources(
        cls, resources: 'resources_lib.Resources'
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        del resources  # Unused.
        unsupported_features = cls._CLOUD_UNSUPPORTED_FEATURES
        return unsupported_features

    @classmethod
    def regions_with_offering(cls, instance_type: Optional[str],
                              accelerators: Optional[Dict[str, int]],
                              use_spot: bool, region: Optional[str],
                              zone: Optional[str]) -> List[clouds.Region]:
        # No notion of regions in Slurm - return a single region.
        return cls._regions

    def instance_type_to_hourly_cost(self,
                                     instance_type: str,
                                     use_spot: bool,
                                     region: Optional[str] = None,
                                     zone: Optional[str] = None) -> float:
        # TODO(zhwu): Investigate how users can provide their own cost catalog
        # for Slurm clusters.
        # For now, assume zero cost for Slurm clusters
        return 0.0

    def accelerators_to_hourly_cost(self,
                                    accelerators: Dict[str, int],
                                    use_spot: bool,
                                    region: Optional[str] = None,
                                    zone: Optional[str] = None) -> float:
        del accelerators, use_spot, region, zone  # unused
        return 0.0

    def get_egress_cost(self, num_gigabytes: float) -> float:
        return 0.0

    def __repr__(self):
        return self._REPR

    def is_same_cloud(self, other: clouds.Cloud) -> bool:
        return isinstance(other, Slurm)

    @classmethod
    def get_default_instance_type(
            cls,
            cpus: Optional[str] = None,
            memory: Optional[str] = None,
            disk_tier: Optional[resources_utils.DiskTier] = None) -> str:
        # TODO(zhwu): In the future, we may want to move the instance type
        #  selection + availability checking to a kubernetes_catalog module.
        del disk_tier  # Unused.
        # We strip '+' from resource requests since Slurm can provision
        # exactly the requested resources.
        instance_cpus = float(
            cpus.strip('+')) if cpus is not None else cls._DEFAULT_NUM_VCPUS
        instance_mem = float(memory.strip('+')) if memory is not None else \
            instance_cpus * cls._DEFAULT_MEMORY_CPU_RATIO
        virtual_instance_type = slurm_utils.SlurmInstanceType(
            instance_cpus, instance_mem).name
        return virtual_instance_type

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, int]]:
        inst = slurm_utils.SlurmInstanceType.from_instance_type(instance_type)
        return {
            inst.accelerator_type: inst.accelerator_count
        } if (inst.accelerator_count is not None and
              inst.accelerator_type is not None) else None

    @classmethod
    def get_vcpus_mem_from_instance_type(
            cls, instance_type: str) -> Tuple[Optional[float], Optional[float]]:
        """Returns the #vCPUs and memory that the instance type offers."""
        k = slurm_utils.SlurmInstanceType.from_instance_type(instance_type)
        return k.cpus, k.memory

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
        del num_nodes, region, instance_type, accelerators, use_spot  # Unused.
        for r in cls._regions:
            yield r.zones

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    @classmethod
    def get_image_size(cls, image_id: str, region: Optional[str]) -> int:
        del image_id, region  # Unused.
        # We don't limit the image by its size compared to the disk size, as
        # we don't have a notion of disk size in Slurm.
        return 0

    def make_deploy_resources_variables(
            self, resources: 'resources_lib.Resources',
            cluster_name_on_cloud: str, region: Optional['clouds.Region'],
            zones: Optional[List['clouds.Zone']]) -> Dict[str, Optional[str]]:
        del cluster_name_on_cloud, zones  # Unused.
        if region is None:
            region = self._regions[0]

        r = resources
        acc_dict = self.get_accelerators_from_instance_type(r.instance_type)
        if acc_dict is not None:
            custom_resources = json.dumps(acc_dict, separators=(',', ':'))
        else:
            custom_resources = None

        # resources.memory and cpus are None if they are not explicitly set.
        # We fetch the default values for the instance type in that case.
        instance = slurm_utils.SlurmInstanceType.from_instance_type(
            resources.instance_type)
        cpus = instance.cpus
        mem = instance.memory
        # Optionally populate accelerator information.
        acc_count = instance.accelerator_count if instance.accelerator_count else 0
        acc_type = instance.accelerator_type if instance.accelerator_type else None

        gpu_name = None
        if acc_count > 0 and acc_type is not None:
            gpu_name = slurm_utils.get_slurm_gpu_name(acc_type)

        deploy_vars = {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
            'cpus': str(cpus),
            'memory': str(mem),
            'gpu': gpu_name if acc_count > 0 else None,
            'gpu_count': str(acc_count),
            'timeout': str(self.TIMEOUT)
        }

        return deploy_vars

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> Tuple[List['resources_lib.Resources'], List[str]]:
        fuzzy_candidate_list: List[str] = []
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            resources = resources.copy(accelerators=None)
            return ([resources], fuzzy_candidate_list)

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
            disk_tier=resources.disk_tier)

        if accelerators is None:
            # For CPU only clusters, need no special handling
            chosen_instance_type = default_instance_type
        else:
            assert len(accelerators) == 1, resources
            # GPUs requested - build instance type.
            acc_type, acc_count = list(accelerators.items())[0]

            # Parse into SlurmInstanceType
            k8s_instance_type = (slurm_utils.SlurmInstanceType.
                                 from_instance_type(default_instance_type))

            gpu_task_cpus = k8s_instance_type.cpus
            # Special handling to bump up memory multiplier for GPU instances
            gpu_task_memory = (float(resources.memory.strip('+')) if
                               resources.memory is not None else gpu_task_cpus *
                               self._DEFAULT_MEMORY_CPU_RATIO_WITH_GPU)
            chosen_instance_type = (
                slurm_utils.SlurmInstanceType.from_resources(
                    gpu_task_cpus, gpu_task_memory, acc_count, acc_type).name)

        # Check if requested instance type will fit in the cluster.
        # TODO(zhwu): This will fail early for autoscaling clusters.
        fits, reason = slurm_utils.check_instance_fits(chosen_instance_type)
        if not fits:
            logger.debug(f'Instance type {chosen_instance_type} does '
                         'not fit in the Slurm cluster. '
                         f'Reason: {reason}')
            return [], []

        # No fuzzy lists for Slurm
        return _make([chosen_instance_type]), []

    @classmethod
    def check_credentials(cls) -> Tuple[bool, Optional[str]]:
        if os.path.exists(os.path.expanduser(CREDENTIAL_PATH)):
            # Test using python API
            try:
                config = common_utils.read_yaml(CREDENTIAL_PATH)
                cluster_type = config.get('cluster', 'local')

                pass
            except Exception as e:  # pylint: disable=broad-except
                return (False, 'Credential check failed: '
                        f'{common_utils.format_exception(e)}')
        else:
            return (False, f'Credentials not found - check if {CREDENTIAL_PATH}'
                    ' exists. To set up the credential, check: https://TODO')

    def get_credential_file_mounts(self) -> Dict[str, str]:
        return {CREDENTIAL_PATH: CREDENTIAL_PATH}

    def instance_type_exists(self, instance_type: str) -> bool:
        return slurm_utils.SlurmInstanceType.is_valid_instance_type(
            instance_type)

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        if region != self._SINGLETON_REGION:
            raise ValueError('Slurm support does not support setting region.'
                             ' Cluster used is determined by the kubeconfig.')
        if zone is not None:
            raise ValueError('Slurm support does not support setting zone.'
                             ' Cluster used is determined by the kubeconfig.')
        return region, zone

    def accelerator_in_region_or_zone(self,
                                      accelerator: str,
                                      acc_count: int,
                                      region: Optional[str] = None,
                                      zone: Optional[str] = None) -> bool:
        try:
            # Check if accelerator is available by checking node labels
            slurm_utils.get_slurm_gpu_name(accelerator)
            return True
        except exceptions.ResourcesUnavailableError:
            return False

    # @classmethod
    # def get_current_user_identity(cls) -> Optional[List[str]]:
    #     k8s = kubernetes.get_kubernetes()
    #     try:
    #         _, current_context = k8s.config.list_kube_config_contexts()

    #         user = current_context['context']['user']
    #         cluster = current_context['context']['cluster']
    #         return [f'{cluster}_{user}_{namespace}']
    #     except k8s.config.config_exception.ConfigException:
    #         return None
