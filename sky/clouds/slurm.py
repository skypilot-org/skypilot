"""Slurm."""

import os
import typing
from typing import Dict, Iterator, List, Optional, Tuple

from paramiko.config import SSHConfig

from sky import catalog
from sky import clouds
from sky import sky_logging
from sky.provision.slurm import utils as slurm_utils
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils import volume as volume_lib

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib

logger = sky_logging.init_logger(__name__)

DEFAULT_SLURM_PATH = '~/.slurm/config'
CREDENTIAL_PATH = DEFAULT_SLURM_PATH


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
        cls, resources: 'resources_utils.Resources'
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        # logger.critical('[BYPASS] Check Slurm's unsupported features...')
        return cls._CLOUD_UNSUPPORTED_FEATURES

    @classmethod
    def _max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    @classmethod
    def regions_with_offering(cls, instance_type: str,
                              accelerators: Optional[Dict[str, int]],
                              use_spot: bool, region: Optional[str],
                              zone: Optional[str]) -> List[clouds.Region]:
        assert zone is None, 'Slurm does not support zones.'
        del accelerators, zone  # unused
        if use_spot:
            return []
        else:
            regions = catalog.get_region_zones_for_instance_type(
                instance_type, use_spot, 'slurm')

        if region is not None:
            regions = [r for r in regions if r.name == region]
        return regions

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        return catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                        clouds='slurm')

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

    def instance_type_to_hourly_cost(self,
                                     instance_type: str,
                                     use_spot: bool,
                                     region: Optional[str] = None,
                                     zone: Optional[str] = None) -> float:
        return catalog.get_hourly_cost(instance_type,
                                       use_spot=use_spot,
                                       region=region,
                                       zone=zone,
                                       clouds='slurm')

    def accelerators_to_hourly_cost(self,
                                    accelerators: Dict[str, int],
                                    use_spot: bool,
                                    region: Optional[str] = None,
                                    zone: Optional[str] = None) -> float:
        """Returns the hourly cost of the accelerators, in dollars/hour."""
        del accelerators, use_spot, region, zone  # unused
        ########
        # TODO #
        ########
        # This function assumes accelerators are included as part of instance
        # type. If not, you will need to change this. (However, you can do
        # this later; `return 0.0` is a good placeholder.)
        return 0.0

    def get_egress_cost(self, num_gigabytes: float) -> float:
        ########
        # TODO #
        ########
        # Change if your cloud has egress cost. (You can do this later;
        # `return 0.0` is a good placeholder.)
        return 0.0

    def __repr__(self):
        return 'Slurm'

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
            cls, instance_type: str) -> Optional[Dict[str, int]]:
        return catalog.get_accelerators_from_instance_type(instance_type,
                                                           clouds='slurm')

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
        del cluster_name, zones, dryrun  # Unused.

        # Suppose 'localcluster' is our target SlurmctldHost alias
        ssh_config = SSHConfig.from_path(os.path.expanduser(DEFAULT_SLURM_PATH))
        ssh_config_dict = ssh_config.lookup('localcluster')

        r = resources
        acc_dict = self.get_accelerators_from_instance_type(r.instance_type)
        custom_resources = resources_utils.make_ray_custom_resources_str(
            acc_dict)

        # resources.memory and cpus are none if they are not explicitly set.
        # we fetch the default values for the instance type in that case.
        k = slurm_utils.SlurmInstanceType.from_instance_type(
            resources.instance_type)
        cpus = k.cpus
        mem = k.memory
        # Optionally populate accelerator information.
        acc_count = k.accelerator_count if k.accelerator_count else 0
        acc_type = k.accelerator_type if k.accelerator_type else None

        deploy_vars = {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'cpus': str(cpus),
            'memory': str(mem),
            'accelerator_count': str(acc_count),
            # TODO(jwj): Pass SSH config in a smarter way
            'ssh_hostname': ssh_config_dict['hostname'],
            'ssh_port': ssh_config_dict['port'],
            'ssh_user': ssh_config_dict['user'],
            # TODO(jwj): Solve naming collision with 'ssh_private_key'.
            # Please refer to slurm-ray.yml.j2 'ssh' and 'auth' sections.
            'slurm_private_key': ssh_config_dict['identityfile'][0],
        }

        return deploy_vars

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> 'resources_utils.FeasibleResources':
        """Returns a list of feasible resources for the given resources."""
        if resources.use_spot:
            return ([], [])
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
                    ########
                    # TODO #
                    ########
                    # Set to None if don't separately bill / attach
                    # accelerators.
                    accelerators=None,
                    cpus=None,
                )
                resource_list.append(r)
            return resource_list

        # Currently, handle a filter on accelerators only.
        accelerators = resources.accelerators
        if accelerators is None:
            # Return a default instance type
            default_instance_type = Slurm.get_default_instance_type(
                cpus=resources.cpus)
            if default_instance_type is None:
                return resources_utils.FeasibleResources([], [], None)
            else:
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
             region=resources.region,
             zone=resources.zone,
             clouds='slurm')
        if instance_list is None:
            return ([], fuzzy_candidate_list)
        return (_make(instance_list), fuzzy_candidate_list)

    @classmethod
    def _check_compute_credentials(cls) -> Tuple[bool, Optional[str]]:
        """Checks if the user has access credentials to the Slurm cluster."""
        # We can use ssh_config.get_hostnames to get a set of SlurmctldHost name aliases.
        # For now, we use a single-node Slurm cluster for demo.
        ssh_config = SSHConfig.from_path(os.path.expanduser(DEFAULT_SLURM_PATH))
        # existing_allowed_clusters = list(ssh_config.get_hostnames())
        existing_allowed_clusters = ['localcluster']

        for cluster in existing_allowed_clusters:
            # Retrieve the config options for a given SlurmctldHost name alias.
            ssh_config_dict = ssh_config.lookup(cluster)

            try:
                runner = command_runner.SlurmCommandRunner(
                    (ssh_config_dict['hostname'], ssh_config_dict['port']),
                    ssh_config_dict['user'],
                    ssh_config_dict['identityfile'][0],
                    cluster,
                    disable_control_master=True)
                returncode, stdout, stderr = runner.run('sinfo',
                                                        require_outputs=True)
                if returncode == 0:
                    logger.info(stdout)
                    return (True, '')
                raise RuntimeError(
                    f'sinfo command failed with return code {returncode}:\n{stderr}'
                )
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
