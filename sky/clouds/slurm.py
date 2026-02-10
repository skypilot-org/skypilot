"""Slurm."""

import typing
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from sky import catalog
from sky import clouds
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import slurm
from sky.provision.slurm import utils as slurm_utils
from sky.skylet import constants
from sky.utils import annotations
from sky.utils import common_utils
from sky.utils import registry
from sky.utils import resources_utils

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib
    from sky.utils import volume as volume_lib

logger = sky_logging.init_logger(__name__)

CREDENTIAL_PATH = slurm_utils.DEFAULT_SLURM_PATH


@registry.CLOUD_REGISTRY.register
class Slurm(clouds.Cloud):
    """Slurm."""

    _REPR = 'Slurm'
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.AUTOSTOP: 'Slurm does not '
                                                     'support autostop.',
        clouds.CloudImplementationFeatures.STOP: 'Slurm does not support '
                                                 'stopping instances.',
        clouds.CloudImplementationFeatures.SPOT_INSTANCE: 'Spot instances are '
                                                          'not supported in '
                                                          'Slurm.',
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK:
            'Customized multiple network interfaces are not supported in '
            'Slurm.',
        clouds.CloudImplementationFeatures.OPEN_PORTS: 'Opening ports is not '
                                                       'supported in Slurm.',
        clouds.CloudImplementationFeatures.HOST_CONTROLLERS:
            'Running '
            'controllers is not '
            'well tested with '
            'Slurm.',
        clouds.CloudImplementationFeatures.LOCAL_DISK:
            (f'Local disk is not supported on {_REPR}'),
    }
    _MAX_CLUSTER_NAME_LEN_LIMIT = 120
    _regions: List[clouds.Region] = []
    _INDENT_PREFIX = '    '

    # Same as Kubernetes.
    _DEFAULT_NUM_VCPUS_WITH_GPU = 4
    _DEFAULT_MEMORY_CPU_RATIO_WITH_GPU = 4

    # Using the latest SkyPilot provisioner API to provision and check status.
    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT

    _SSH_CONFIG_KEY_MAPPING = {
        'user': 'User',
        'hostname': 'HostName',
    }

    @classmethod
    def _unsupported_features_for_resources(
        cls,
        resources: 'resources_lib.Resources',
        region: Optional[str] = None,
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        del region  # unused
        # logger.critical('[BYPASS] Check Slurm's unsupported features...')
        return cls._CLOUD_UNSUPPORTED_FEATURES

    @classmethod
    def _max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    @classmethod
    def uses_ray(cls) -> bool:
        return False

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
        """Iterate over partitions (zones) for provisioning with failover.

        Yields one partition at a time for failover retry logic.
        """
        del num_nodes  # unused

        regions = cls.regions_with_offering(instance_type,
                                            accelerators,
                                            use_spot,
                                            region=region,
                                            zone=None)

        for r in regions:
            if r.zones:
                # Yield one partition at a time for failover
                for zone in r.zones:
                    yield [zone]
            else:
                # No partitions discovered, use default
                yield None

    @classmethod
    @annotations.lru_cache(scope='global', maxsize=1)
    def _log_skipped_clusters_once(cls, skipped_clusters: Tuple[str,
                                                                ...]) -> None:
        """Log skipped clusters for only once.

        We don't directly cache the result of existing_allowed_clusters
        as the config may update the allowed clusters.
        """
        if skipped_clusters:
            logger.warning(
                f'Slurm clusters {set(skipped_clusters)!r} specified in '
                '"allowed_clusters" not found in ~/.slurm/config. '
                'Ignoring these clusters.')

    @classmethod
    def existing_allowed_clusters(cls, silent: bool = False) -> List[str]:
        """Get existing allowed clusters.

        Returns clusters based on the following logic:
        1. If 'allowed_clusters' is set to 'all' in ~/.sky/config.yaml,
           return all clusters from ~/.slurm/config
        2. If specific clusters are listed in 'allowed_clusters',
           return only those that exist in ~/.slurm/config
        3. If no configuration is specified, return all clusters
           from ~/.slurm/config (default behavior)
        """
        all_clusters = slurm_utils.get_all_slurm_cluster_names()
        if len(all_clusters) == 0:
            return []

        all_clusters = set(all_clusters)

        # Workspace-level allowed_clusters should take precedence over
        # the global allowed_clusters.
        allowed_clusters = skypilot_config.get_workspace_cloud('slurm').get(
            'allowed_clusters', None)
        if allowed_clusters is None:
            allowed_clusters = skypilot_config.get_effective_region_config(
                cloud='slurm',
                region=None,
                keys=('allowed_clusters',),
                default_value=None)

        allow_all_clusters = allowed_clusters == 'all'
        if allow_all_clusters:
            allowed_clusters = list(all_clusters)

        if allowed_clusters is None:
            # Default to all clusters if no configuration is specified
            allowed_clusters = list(all_clusters)

        existing_clusters = []
        skipped_clusters = []
        for cluster in allowed_clusters:
            if cluster in all_clusters:
                existing_clusters.append(cluster)
            else:
                skipped_clusters.append(cluster)

        if not silent:
            cls._log_skipped_clusters_once(tuple(sorted(skipped_clusters)))

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
        del accelerators, use_spot, resources  # unused
        existing_clusters = cls.existing_allowed_clusters()

        regions: List[clouds.Region] = []
        for cluster in existing_clusters:
            # Filter by region if specified
            if region is not None and cluster != region:
                continue

            # Fetch partitions for this cluster and attach as zones
            try:
                partitions = slurm_utils.get_partitions(cluster)
                if zone is not None:
                    # Filter by zone (partition) if specified
                    partitions = [p for p in partitions if p == zone]
                zones = [clouds.Zone(p) for p in partitions]
            except Exception as e:  # pylint: disable=broad-except
                logger.debug(f'Failed to get partitions for {cluster}: {e}')
                zones = []

            r = clouds.Region(cluster)
            if zones:
                r.set_zones(zones)
            regions.append(r)

        # Check if requested instance type will fit in the cluster.
        if instance_type is None:
            return regions

        regions_to_return = []
        for r in regions:
            cluster = r.name

            # Check each partition (zone) in the cluster
            partitions_to_check = [z.name for z in r.zones] if r.zones else []
            valid_zones = []

            # TODO(kevin): Batch this check to reduce number of roundtrips.
            for partition in partitions_to_check:
                fits, reason = slurm_utils.check_instance_fits(
                    cluster, instance_type, partition)
                if fits:
                    if partition:
                        valid_zones.append(clouds.Zone(partition))
                else:
                    logger.debug(
                        f'Instance type {instance_type} does not fit in '
                        f'{cluster}/{partition}: {reason}')

            if valid_zones:
                r.set_zones(valid_zones)
                regions_to_return.append(r)

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
                                  local_disk: Optional[str] = None,
                                  region: Optional[str] = None,
                                  zone: Optional[str] = None) -> Optional[str]:
        """Returns the default instance type for Slurm."""
        return catalog.get_default_instance_type(cpus=cpus,
                                                 memory=memory,
                                                 disk_tier=disk_tier,
                                                 local_disk=local_disk,
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
    ) -> Dict[str, Any]:
        del cluster_name, dryrun, volume_mounts  # Unused.
        if region is not None:
            cluster = region.name
        else:
            cluster = 'localcluster'
        assert cluster is not None, 'No available Slurm cluster found.'

        # Use zone as partition if specified, otherwise default
        if zones and len(zones) > 0:
            partition = zones[0].name
        else:
            partitions = slurm_utils.get_partitions(cluster)
            if not partitions:
                raise ValueError(f'No partitions found for cluster {cluster}.')
            # get_partitions returns the default partition first, then sorted
            # alphabetically, so this also handles the case where the cluster
            # does not have a default partition.
            partition = partitions[0]

        # cluster is our target slurmctld host.
        ssh_config = slurm_utils.get_slurm_ssh_config()
        ssh_config_dict = ssh_config.lookup(cluster)

        resources = resources.assert_launchable()
        acc_dict = self.get_accelerators_from_instance_type(
            resources.instance_type)
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
        # Resolve the actual GPU type as it appears in the cluster's GRES.
        # Slurm GRES types are case-sensitive.
        if acc_type:
            acc_type = slurm_utils.get_gres_gpu_type(cluster, acc_type)

        image_id = resources.extract_docker_image()

        provision_timeout = skypilot_config.get_effective_region_config(
            cloud='slurm',
            region=cluster,
            keys=('provision_timeout',),
            default_value=None)
        if provision_timeout is None:
            if resources.zone is not None:
                # When zone/partition is specified, there will be no failover,
                # so we can let Slurm hold on to the job and let it be queued
                # for a long time.
                provision_timeout = 24 * 60 * 60  # 24 hours
            else:
                # Otherwise, we still want failover, but also wait sufficiently
                # long for the Slurm scheduler to allocate the resources. We
                # have seen Slurm taking minutes to schedule a job, when there
                # are a lot of pending jobs to be processed.
                provision_timeout = 2 * 60  # 2 minutes

        deploy_vars = {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'cpus': str(cpus),
            'memory': str(mem),
            'accelerator_count': str(acc_count),
            'accelerator_type': acc_type,
            'slurm_cluster': cluster,
            'slurm_partition': partition,
            'provision_timeout': provision_timeout,
            # TODO(jwj): Pass SSH config in a smarter way
            'ssh_hostname': ssh_config_dict['hostname'],
            'ssh_port': str(ssh_config_dict.get('port', 22)),
            'ssh_user': ssh_config_dict['user'],
            'slurm_proxy_command': ssh_config_dict.get('proxycommand', None),
            'slurm_proxy_jump': ssh_config_dict.get('proxyjump', None),
            'slurm_identities_only':
                slurm_utils.get_identities_only(ssh_config_dict),
            # TODO(jwj): Solve naming collision with 'ssh_private_key'.
            # Please refer to slurm-ray.yml.j2 'ssh' and 'auth' sections.
            'slurm_private_key': slurm_utils.get_identity_file(ssh_config_dict),
            'slurm_sshd_host_key_filename':
                (slurm_utils.SLURM_SSHD_HOST_KEY_FILENAME),
            'slurm_cluster_name_env_var':
                (constants.SKY_CLUSTER_NAME_ENV_VAR_KEY),
            'image_id': image_id,
        }

        return deploy_vars

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> 'resources_utils.FeasibleResources':
        """Returns a list of feasible resources for the given resources."""
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            # Check if the instance type is available in at least one cluster
            available_regions = self.regions_with_offering(
                resources.instance_type,
                accelerators=None,
                use_spot=resources.use_spot,
                region=resources.region,
                zone=resources.zone)
            if not available_regions:
                return resources_utils.FeasibleResources([], [], None)

            # Return a single resource without region set.
            # The optimizer will call make_launchables_for_valid_region_zones()
            # which will create one resource per region/cluster.
            resources = resources.copy(accelerators=None)
            return resources_utils.FeasibleResources([resources], [], None)

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
            local_disk=resources.local_disk,
            region=resources.region,
            zone=resources.zone)
        if default_instance_type is None:
            return resources_utils.FeasibleResources([], [], None)

        if accelerators is None:
            chosen_instance_type = default_instance_type
        else:
            assert len(accelerators) == 1, resources

            # Build GPU-enabled instance type.
            acc_type, acc_count = list(accelerators.items())[0]

            slurm_instance_type = (slurm_utils.SlurmInstanceType.
                                   from_instance_type(default_instance_type))

            gpu_task_cpus = slurm_instance_type.cpus
            if resources.cpus is None:
                gpu_task_cpus = self._DEFAULT_NUM_VCPUS_WITH_GPU * acc_count
            # Special handling to bump up memory multiplier for GPU instances
            gpu_task_memory = (float(resources.memory.strip('+')) if
                               resources.memory is not None else gpu_task_cpus *
                               self._DEFAULT_MEMORY_CPU_RATIO_WITH_GPU)

            chosen_instance_type = (
                slurm_utils.SlurmInstanceType.from_resources(
                    gpu_task_cpus, gpu_task_memory, acc_count, acc_type).name)

        # Check the availability of the specified instance type in all
        # Slurm clusters.
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
    def _check_compute_credentials(
            cls) -> Tuple[bool, Optional[Union[str, Dict[str, str]]]]:
        """Checks if the user has access credentials to the Slurm cluster."""
        try:
            ssh_config = slurm_utils.get_slurm_ssh_config()
        except FileNotFoundError:
            return (
                False,
                f'Slurm configuration file {slurm_utils.DEFAULT_SLURM_PATH} '
                'does not exist.\n'
                f'{cls._INDENT_PREFIX}For more info: '
                'https://docs.skypilot.co/en/latest/getting-started/'
                'installation.html#slurm-installation')
        except Exception as e:  # pylint: disable=broad-except
            return (False, 'Failed to load SSH configuration from '
                    f'{slurm_utils.DEFAULT_SLURM_PATH}: '
                    f'{common_utils.format_exception(e)}.')
        existing_allowed_clusters = cls.existing_allowed_clusters()

        if not existing_allowed_clusters:
            return (False, 'No Slurm clusters found in ~/.slurm/config. '
                    'Please configure at least one Slurm cluster.')

        # Check credentials for each cluster and return ctx2text mapping
        ctx2text = {}
        success = False
        for cluster in existing_allowed_clusters:
            # Retrieve the config options for a given SlurmctldHost name alias.
            ssh_config_dict = ssh_config.lookup(cluster)
            try:
                client = slurm.SlurmClient(
                    ssh_config_dict['hostname'],
                    int(ssh_config_dict.get('port', 22)),
                    ssh_config_dict['user'],
                    slurm_utils.get_identity_file(ssh_config_dict),
                    ssh_proxy_command=ssh_config_dict.get('proxycommand', None),
                    ssh_proxy_jump=ssh_config_dict.get('proxyjump', None),
                    identities_only=slurm_utils.get_identities_only(
                        ssh_config_dict),
                )
                info = client.info()
                logger.debug(f'Slurm cluster {cluster} sinfo: {info}')
                ctx2text[cluster] = 'enabled'
                success = True
            except KeyError as e:
                key = e.args[0]
                ctx2text[cluster] = (
                    f'disabled. '
                    f'{cls._SSH_CONFIG_KEY_MAPPING.get(key, key.capitalize())} '
                    'is missing, please check your ~/.slurm/config '
                    'and try again.')
            except Exception as e:  # pylint: disable=broad-except
                error_msg = (f'Credential check failed: '
                             f'{common_utils.format_exception(e)}')
                ctx2text[cluster] = f'disabled. {error_msg}'

        return success, ctx2text

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
        """Validate region (cluster) and zone (partition).

        Args:
            region: Slurm cluster name.
            zone: Slurm partition name (optional).

        Returns:
            Tuple of (region, zone) if valid.

        Raises:
            ValueError: If cluster or partition not found.
        """
        all_clusters = slurm_utils.get_all_slurm_cluster_names()
        if region and region not in all_clusters:
            raise ValueError(
                f'Cluster {region} not found in Slurm config. Slurm only '
                'supports cluster names as regions. Available '
                f'clusters: {all_clusters}')

        # Validate partition (zone) if specified
        if zone is not None:
            if region is None:
                raise ValueError(
                    'Cannot specify partition (zone) without specifying '
                    'cluster (region) for Slurm.')

            partitions = slurm_utils.get_partitions(region)
            if zone not in partitions:
                raise ValueError(
                    f'Partition {zone!r} not found in cluster {region!r}. '
                    f'Available partitions: {partitions}')

        return region, zone

    def accelerator_in_region_or_zone(self,
                                      accelerator: str,
                                      acc_count: int,
                                      region: Optional[str] = None,
                                      zone: Optional[str] = None) -> bool:
        del zone  # unused for now
        regions = catalog.get_region_zones_for_accelerators(accelerator,
                                                            acc_count,
                                                            use_spot=False,
                                                            clouds='slurm')
        if not regions:
            return False
        if region is None:
            return True
        return any(r.name == region for r in regions)

    @classmethod
    def expand_infras(cls) -> List[str]:
        """Returns a list of enabled Slurm clusters.

        Each is returned as 'Slurm/cluster-name'.
        """
        infras = []
        for cluster in cls.existing_allowed_clusters(silent=True):
            infras.append(f'{cls.canonical_name()}/{cluster}')
        return infras
