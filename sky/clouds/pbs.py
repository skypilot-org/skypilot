"""PBS Pro / OpenPBS."""

import typing
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

import colorama

from sky import catalog
from sky import clouds
from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
from sky.provision.pbs import utils as pbs_utils
from sky.skylet import constants
from sky.utils import annotations
from sky.utils import common_utils
from sky.utils import config_utils
from sky.utils import registry
from sky.utils import resources_utils

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib
    from sky.utils import volume as volume_lib

logger = sky_logging.init_logger(__name__)

CREDENTIAL_PATH = pbs_utils.DEFAULT_PBS_PATH


@registry.CLOUD_REGISTRY.register(aliases=['pbspro', 'openpbs'])
class PBS(clouds.Cloud):
    """PBS Pro / OpenPBS.

    A PBS cluster (a ``Host`` in ``~/.pbs/config``) is a region and its
    queues are zones. A SkyPilot cluster is one long-running batch job.
    """

    _REPR = 'PBS'
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.AUTOSTOP:
            ('Autostop is not supported on PBS.'),
        clouds.CloudImplementationFeatures.STOP:
            ('Stopping is not supported on PBS.'),
        clouds.CloudImplementationFeatures.SPOT_INSTANCE:
            ('Spot instances are not supported on PBS.'),
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK:
            'Customized multiple network interfaces are not supported on '
            'PBS.',
        clouds.CloudImplementationFeatures.OPEN_PORTS:
            ('Opening ports is not supported on PBS.'),
        clouds.CloudImplementationFeatures.HOST_CONTROLLERS:
            ('Running controllers is not well tested with PBS.'),
        clouds.CloudImplementationFeatures.LOCAL_DISK:
            (f'Local disk is not supported on {_REPR}'),
        clouds.CloudImplementationFeatures.DOCKER_IMAGE:
            ('Docker images are not supported on PBS.'),
        clouds.CloudImplementationFeatures.IMAGE_ID:
            ('Images are not supported on PBS.'),
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
            ('Disk tiers are not supported on PBS.'),
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER:
            ('Network tiers are not supported on PBS.'),
        clouds.CloudImplementationFeatures.STORAGE_MOUNTING:
            'Storage mounting is not supported on this PBS cluster because '
            'FUSE is not enabled (/dev/fuse not found). Please ask your '
            'cluster administrator to enable FUSE.',
    }
    # Checked per cluster over SSH.
    _DYNAMICALLY_CHECKED_FEATURES = {
        clouds.CloudImplementationFeatures.STORAGE_MOUNTING,
    }
    # PBS Pro accepts job names of up to 236 characters.
    _MAX_CLUSTER_NAME_LEN_LIMIT = 120
    _regions: List[clouds.Region] = []
    _INDENT_PREFIX = '    '
    # Shared filesystem types, as returned by `stat -f -c %T`.
    _SHARED_FS_TYPES = frozenset({
        'nfs',
        'nfs4',
        'lustre',
        'gpfs',
        'beegfs',
        'ceph',
        'fuse.ceph',
        'glusterfs',
        'fuse.glusterfs',
    })

    # Same as Kubernetes.
    _DEFAULT_NUM_VCPUS_WITH_GPU = 4
    _DEFAULT_MEMORY_CPU_RATIO_WITH_GPU = 4

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
        unsupported = cls._CLOUD_UNSUPPORTED_FEATURES.copy()
        # Without a region, a feature counts as supported if any allowed
        # cluster supports it; regions_with_offering() filters per cluster.
        cluster = region if region is not None else resources.region
        if cluster is None:
            clusters = cls.existing_allowed_clusters()
        else:
            clusters = [cluster]
        for c in clusters:
            try:
                if pbs_utils.check_fuse_enabled(c):
                    unsupported.pop(
                        clouds.CloudImplementationFeatures.STORAGE_MOUNTING,
                        None)
            except Exception as e:  # pylint: disable=broad-except
                logger.debug(f'Failed to check cluster features on {c}: '
                             f'{common_utils.format_exception(e)}')
            checked = cls._DYNAMICALLY_CHECKED_FEATURES
            if not any(f in unsupported for f in checked):
                break
        return unsupported

    @classmethod
    def _max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    @classmethod
    def uses_ray(cls) -> bool:
        return False

    @classmethod
    def optimize_by_zone(cls) -> bool:
        return True

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        inst = pbs_utils.PbsInstanceType.from_instance_type(instance_type)
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
        """Yield one queue (zone) at a time for failover."""
        del num_nodes  # unused
        regions = cls.regions_with_offering(instance_type,
                                            accelerators,
                                            use_spot,
                                            region=region,
                                            zone=None)
        for r in regions:
            if r.zones:
                for zone in r.zones:
                    yield [zone]
            else:
                yield None

    @classmethod
    @annotations.lru_cache(scope='global', maxsize=1)
    def _log_skipped_clusters_once(cls, skipped_clusters: Tuple[str,
                                                                ...]) -> None:
        if skipped_clusters:
            logger.warning(
                f'PBS clusters {set(skipped_clusters)!r} specified in '
                '"allowed_clusters" not found in ~/.pbs/config. '
                'Ignoring these clusters.')

    @classmethod
    def existing_allowed_clusters(cls, silent: bool = False) -> List[str]:
        """Clusters in ~/.pbs/config, narrowed by ``allowed_clusters``.

        The workspace-level ``allowed_clusters`` takes precedence over the
        global one; ``all`` or no setting allows every configured cluster.
        """
        all_clusters = pbs_utils.get_all_pbs_cluster_names()
        if not all_clusters:
            return []
        all_cluster_set = set(all_clusters)

        allowed_clusters = skypilot_config.get_workspace_cloud('pbs').get(
            'allowed_clusters', None)
        if allowed_clusters is None:
            allowed_clusters = skypilot_config.get_effective_region_config(
                cloud='pbs',
                region=None,
                keys=('allowed_clusters',),
                default_value=None)
        if allowed_clusters is None or allowed_clusters == 'all':
            allowed_clusters = list(all_clusters)

        existing_clusters = []
        skipped_clusters = []
        for cluster in allowed_clusters:
            if cluster in all_cluster_set:
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
        del accelerators, use_spot  # unused
        regions: List[clouds.Region] = []
        for cluster in cls.existing_allowed_clusters():
            if region is not None and cluster != region:
                continue
            try:
                queues = pbs_utils.get_queues(cluster)
                if zone is not None:
                    queues = [q for q in queues if q == zone]
                zones = [clouds.Zone(q) for q in queues]
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(f'Failed to get queues for {cluster}: {e}')
                zones = []
            r = clouds.Region(cluster)
            if zones:
                r.set_zones(zones)
            regions.append(r)

        if resources is not None:
            required_features = resources.get_required_cloud_features()
            filtered_regions = []
            for r in regions:
                try:
                    cls.check_features_are_supported(resources,
                                                     required_features, r.name)
                    filtered_regions.append(r)
                except exceptions.NotSupportedError as e:
                    logger.info(f'Excluding PBS cluster {r.name!r}: {e}')
            regions = filtered_regions

        if instance_type is None:
            return regions

        regions_to_return = []
        for r in regions:
            cluster = r.name
            queues_to_check = [z.name for z in r.zones] if r.zones else []
            valid_zones = []
            try:
                pit = pbs_utils.PbsInstanceType.from_instance_type(
                    instance_type)
            except ValueError:
                pass
            else:
                if pit.accelerator_type is not None:
                    mapped = pbs_utils.lookup_gpu_queue_map(
                        cluster, pit.accelerator_type)
                    if mapped is not None:
                        available = set(queues_to_check)
                        queues_to_check = [q for q in mapped if q in available]
                        if not queues_to_check:
                            zone_msg = (f' for the requested queue {zone!r}'
                                        if zone else '')
                            logger.warning(
                                f'{colorama.Fore.YELLOW}gpu_queue_map maps '
                                f'{pit.accelerator_type!r} to queue(s) '
                                f'{mapped}, but none of them is available on '
                                f'cluster {cluster!r}{zone_msg}.'
                                f'{colorama.Style.RESET_ALL}')
                else:
                    cpu_queue = None
                    if resources is not None:
                        get_override = (
                            config_utils.get_cloud_config_value_from_dict)
                        cpu_queue = get_override(
                            dict_config=resources.cluster_config_overrides,
                            cloud='pbs',
                            region=cluster,
                            keys=('cpu_queue',))
                    if cpu_queue is None:
                        cpu_queue = pbs_utils.lookup_cpu_queue(cluster)
                    if cpu_queue is not None:
                        if cpu_queue in set(queues_to_check):
                            queues_to_check = [cpu_queue]
                        else:
                            queues_to_check = []
                            logger.warning(
                                f'{colorama.Fore.YELLOW}cpu_queue is set to '
                                f'{cpu_queue!r}, but it does not exist on '
                                f'cluster {cluster!r}. Please double-check '
                                f'the queue name.{colorama.Style.RESET_ALL}')

            for queue in queues_to_check:
                fits, reason = pbs_utils.check_instance_fits(
                    cluster, instance_type, queue)
                if fits:
                    valid_zones.append(clouds.Zone(queue))
                else:
                    logger.debug(f'Instance type {instance_type} does not fit '
                                 f'in {cluster}/{queue}: {reason}')
            if valid_zones:
                r.set_zones(valid_zones)
                regions_to_return.append(r)
        return regions_to_return

    def instance_type_to_hourly_cost(self,
                                     instance_type: str,
                                     use_spot: bool,
                                     region: Optional[str] = None,
                                     zone: Optional[str] = None) -> float:
        # pylint: disable=import-outside-toplevel
        from sky.catalog import pbs_catalog
        return pbs_catalog.get_hourly_cost(instance_type, use_spot, region,
                                           zone)

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
        return isinstance(other, PBS)

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
        del max_hourly_cost  # Unused.
        return catalog.get_default_instance_type(cpus=cpus,
                                                 memory=memory,
                                                 disk_tier=disk_tier,
                                                 local_disk=local_disk,
                                                 region=region,
                                                 zone=zone,
                                                 use_spot=use_spot,
                                                 clouds='pbs')

    @classmethod
    def get_accelerators_from_instance_type(
            cls, instance_type: str) -> Optional[Dict[str, Union[int, float]]]:
        inst = pbs_utils.PbsInstanceType.from_instance_type(instance_type)
        if inst.accelerator_count is None or inst.accelerator_type is None:
            return None
        return {inst.accelerator_type: inst.accelerator_count}

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
        assert region is not None, 'No available PBS cluster found.'
        cluster = region.name

        if zones:
            queue = zones[0].name
        else:
            queues = pbs_utils.get_queues(cluster)
            if not queues:
                raise ValueError(f'No queues found for cluster {cluster}.')
            # get_queues returns the default queue first.
            queue = queues[0]

        ssh_config_dict = pbs_utils.get_pbs_ssh_config().lookup(cluster)

        resources = resources.assert_launchable()
        acc_dict = self.get_accelerators_from_instance_type(
            resources.instance_type)
        custom_resources = resources_utils.make_ray_custom_resources_str(
            acc_dict)

        inst = pbs_utils.PbsInstanceType.from_instance_type(
            resources.instance_type)
        acc_count = inst.accelerator_count if inst.accelerator_count else 0
        acc_type = inst.accelerator_type if inst.accelerator_type else None

        gpu_type_resource: Optional[str] = None
        if acc_type is not None:
            if pbs_utils.lookup_gpu_queue_map(cluster, acc_type) is not None:
                # The queue selects the GPU type; request GPUs by count.
                acc_type = None
            else:
                try:
                    raw_type = pbs_utils.resolve_gpu_type(
                        cluster, acc_type, acc_count, queue)
                except Exception as e:  # pylint: disable=broad-except
                    logger.warning(
                        f'Failed to determine the GPU type resource on PBS '
                        f'cluster {cluster!r}; requesting GPUs by count only. '
                        f'Error: {common_utils.format_exception(e)}')
                    raw_type = None
                if raw_type is None:
                    acc_type = None
                else:
                    acc_type = raw_type
                    gpu_type_resource = _detect_gpu_type_resource(
                        cluster, raw_type)

        provision_timeout = skypilot_config.get_effective_region_config(
            cloud='pbs',
            region=cluster,
            keys=('provision_timeout',),
            default_value=None)
        if provision_timeout is None:
            if resources.zone is not None:
                # No failover with a pinned queue: let the job wait.
                provision_timeout = 24 * 60 * 60
            else:
                provision_timeout = 2 * 60

        # qsub_options and select_resources: global < cluster < queue <
        # task config, merged key by key.
        pbs_config = skypilot_config.get_workspace_cloud('pbs')

        def _merged(key: str) -> Dict[str, Any]:
            merged: Dict[str, Any] = {}
            for config_keys in [
                (key,),
                ('cluster_configs', cluster, key),
                ('cluster_configs', cluster, 'queue_configs', queue, key),
            ]:
                level_config = pbs_config.get_nested(config_keys,
                                                     default_value=None)
                if level_config is not None:
                    merged.update(level_config)
            task_level = config_utils.get_cloud_config_value_from_dict(
                dict_config=resources.cluster_config_overrides,
                cloud='pbs',
                region=cluster,
                keys=(key,))
            if task_level is not None:
                merged.update(task_level)
            return merged

        qsub_options = _merged('qsub_options')
        select_resources = _merged('select_resources')
        # place: the most specific scope wins.
        place = config_utils.get_cloud_config_value_from_dict(
            dict_config=resources.cluster_config_overrides,
            cloud='pbs',
            region=cluster,
            keys=('place',))
        if place is None:
            for config_keys in [
                ('cluster_configs', cluster, 'queue_configs', queue, 'place'),
                ('cluster_configs', cluster, 'place'),
                ('place',),
            ]:
                place = pbs_config.get_nested(config_keys, default_value=None)
                if place is not None:
                    break
        # quota.account / quota.project outrank the qsub_options spelling.
        account = skypilot_config.get_effective_pbs_quota_value(
            'account',
            cluster=cluster,
            queue=queue,
            override_configs=resources.cluster_config_overrides)
        if account is not None:
            qsub_options['A'] = account
        project = skypilot_config.get_effective_pbs_quota_value(
            'project',
            cluster=cluster,
            queue=queue,
            override_configs=resources.cluster_config_overrides)
        if project is not None:
            qsub_options['P'] = project

        client = pbs_utils.make_client_from_ssh_config(ssh_config_dict)
        sky_base_dir = pbs_utils.resolve_sky_base_dir(cluster, client)
        tmpdir = skypilot_config.get_effective_region_config(cloud='pbs',
                                                             region=cluster,
                                                             keys=('tmpdir',),
                                                             default_value=None)

        return {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'cpus': str(inst.cpus),
            'memory': str(inst.memory),
            'accelerator_count': str(acc_count),
            'accelerator_type': acc_type,
            'gpu_resource': pbs_utils.get_gpu_resource(cluster),
            'gpu_type_resource': gpu_type_resource,
            'pbs_cluster': cluster,
            'pbs_queue': queue,
            'provision_timeout': provision_timeout,
            'sky_base_dir': sky_base_dir,
            'ssh_hostname': ssh_config_dict['hostname'],
            'ssh_port': str(ssh_config_dict.get('port', 22)),
            'ssh_user': ssh_config_dict['user'],
            'pbs_proxy_command': ssh_config_dict.get('proxycommand', None),
            'pbs_proxy_jump': ssh_config_dict.get('proxyjump', None),
            'pbs_identities_only':
                pbs_utils.get_identities_only(ssh_config_dict),
            'pbs_private_key': pbs_utils.get_identity_file(ssh_config_dict),
            'pbs_control_path': pbs_utils.get_ssh_control_path(ssh_config_dict),
            'pbs_tmpdir': tmpdir,
            'pbs_cluster_name_env_var': constants.SKY_CLUSTER_NAME_ENV_VAR_KEY,
            'qsub_options': qsub_options,
            'select_resources': select_resources,
            'place': place,
        }

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> 'resources_utils.FeasibleResources':
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            available_regions = self.regions_with_offering(
                resources.instance_type,
                accelerators=None,
                use_spot=resources.use_spot,
                region=resources.region,
                zone=resources.zone,
                resources=resources)
            if not available_regions:
                return resources_utils.FeasibleResources([], [], None)
            resources = resources.copy(accelerators=None)
            return resources_utils.FeasibleResources([resources], [], None)

        def _make(instance_list):
            return [
                resources.copy(cloud=PBS(),
                               instance_type=instance_type,
                               accelerators=None)
                for instance_type in instance_list
            ]

        accelerators = resources.accelerators
        default_instance_type = PBS.get_default_instance_type(
            cpus=resources.cpus,
            memory=resources.memory,
            disk_tier=resources.disk_tier,
            local_disk=resources.local_disk,
            region=resources.region,
            zone=resources.zone,
            use_spot=resources.use_spot,
            max_hourly_cost=resources.max_hourly_cost)
        if default_instance_type is None:
            return resources_utils.FeasibleResources([], [], None)

        if accelerators is None:
            chosen_instance_type = default_instance_type
        else:
            assert len(accelerators) == 1, resources
            acc_type, acc_count = list(accelerators.items())[0]
            pbs_instance_type = pbs_utils.PbsInstanceType.from_instance_type(
                default_instance_type)
            gpu_task_cpus = pbs_instance_type.cpus
            if resources.cpus is None:
                gpu_task_cpus = self._DEFAULT_NUM_VCPUS_WITH_GPU * acc_count
            if resources.memory is not None:
                memory = resources.memory.strip('+')
                if memory.endswith('x'):
                    # Memory given as a multiple of the CPU count.
                    gpu_task_memory = float(memory[:-1]) * gpu_task_cpus
                else:
                    gpu_task_memory = float(memory)
            else:
                gpu_task_memory = (gpu_task_cpus *
                                   self._DEFAULT_MEMORY_CPU_RATIO_WITH_GPU)
            chosen_instance_type = pbs_utils.PbsInstanceType.from_resources(
                gpu_task_cpus, gpu_task_memory, acc_count, acc_type).name

        available_regions = self.regions_with_offering(
            chosen_instance_type,
            accelerators=None,
            use_spot=resources.use_spot,
            region=resources.region,
            zone=resources.zone,
            resources=resources)
        if not available_regions:
            return resources_utils.FeasibleResources([], [], None)
        return resources_utils.FeasibleResources(_make([chosen_instance_type]),
                                                 [], None)

    @classmethod
    def _check_compute_credentials(
            cls) -> Tuple[bool, Optional[Union[str, Dict[str, str]]]]:
        """Checks SSH access to the login node of every allowed cluster."""
        try:
            ssh_config = pbs_utils.get_pbs_ssh_config()
        except FileNotFoundError:
            return (False,
                    f'PBS configuration file {pbs_utils.DEFAULT_PBS_PATH} '
                    'does not exist.\n'
                    f'{cls._INDENT_PREFIX}For more info: '
                    'https://docs.skypilot.co/en/latest/getting-started/'
                    'installation.html#pbs-installation')
        except Exception as e:  # pylint: disable=broad-except
            return (False, 'Failed to load SSH configuration from '
                    f'{pbs_utils.DEFAULT_PBS_PATH}: '
                    f'{common_utils.format_exception(e)}.')
        existing_allowed_clusters = cls.existing_allowed_clusters()
        if not existing_allowed_clusters:
            return (False, 'No PBS clusters found in ~/.pbs/config. '
                    'Please configure at least one PBS cluster.')

        ctx2text = {}
        success = False
        for cluster in existing_allowed_clusters:
            ssh_config_dict = ssh_config.lookup(cluster)
            try:
                client = pbs_utils.make_client_from_ssh_config(ssh_config_dict)
                info = client.info()
                logger.debug(f'PBS cluster {cluster} qstat -B: {info}')
                workdir = skypilot_config.get_effective_region_config(
                    cloud='pbs',
                    region=cluster,
                    keys=('workdir',),
                    default_value=None)
                remote_env = client.get_env()
                if workdir is not None:
                    check_path = pbs_utils.expand_path_vars(workdir, remote_env)
                else:
                    check_path = remote_env.get('HOME', '~')
                fs_type = client.check_dir_shared_fs(check_path)
                path_label = (f'workdir ({workdir})'
                              if workdir is not None else 'Home directory (~)')
                hint = (f' Set pbs.cluster_configs.{cluster}.workdir in '
                        '~/.sky/config.yaml to a shared filesystem path.')
                warnings = []
                if fs_type is None:
                    warnings.append(
                        f'Could not determine filesystem type for '
                        f'{path_label} ({check_path}). Ensure the working '
                        'directory is on a shared filesystem (e.g., NFS) '
                        f'visible to all nodes.{hint}')
                elif fs_type not in cls._SHARED_FS_TYPES:
                    warnings.append(
                        f'{path_label} filesystem type is {fs_type!r}, not '
                        'a shared filesystem. SkyPilot requires the working '
                        'directory to be on a shared filesystem (e.g., NFS) '
                        f'visible to all nodes.{hint}')
                if not client.check_sshd_available():
                    warnings.append(
                        'No sshd binary found on the login node. SkyPilot '
                        'runs a user-level OpenSSH server on the allocated '
                        'nodes; make sure the compute nodes have one.')
                if warnings:
                    ctx2text[cluster] = (
                        f'{colorama.Fore.GREEN}enabled.'
                        f'{colorama.Style.RESET_ALL} '
                        f'{colorama.Fore.LIGHTYELLOW_EX}Warning: ' +
                        ' '.join(warnings) + f'{colorama.Style.RESET_ALL}')
                else:
                    ctx2text[cluster] = (f'{colorama.Fore.GREEN}enabled'
                                         f'{colorama.Style.RESET_ALL}')
                success = True
            except KeyError as e:
                key = e.args[0]
                ctx2text[cluster] = (
                    f'disabled. '
                    f'{cls._SSH_CONFIG_KEY_MAPPING.get(key, key.capitalize())} '
                    'is missing, please check your ~/.pbs/config '
                    'and try again.')
            except Exception as e:  # pylint: disable=broad-except
                ctx2text[cluster] = ('disabled. Credential check failed: '
                                     f'{common_utils.format_exception(e)}')
        return success, ctx2text

    def get_credential_file_mounts(self) -> Dict[str, str]:
        return {}

    @classmethod
    def get_current_user_identity(cls) -> Optional[List[str]]:
        return None

    def instance_type_exists(self, instance_type: str) -> bool:
        return catalog.instance_type_exists(instance_type, 'pbs')

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        """Validate the cluster (region) and queue (zone)."""
        all_clusters = pbs_utils.get_all_pbs_cluster_names()
        if region and region not in all_clusters:
            raise ValueError(
                f'Cluster {region} not found in PBS config. PBS only '
                'supports cluster names as regions. Available '
                f'clusters: {self.existing_allowed_clusters(silent=True)}')
        if zone is not None:
            if region is None:
                raise ValueError('Cannot specify a queue (zone) without '
                                 'specifying the cluster (region) for PBS.')
            # Only probe clusters the caller may use.
            if region in self.existing_allowed_clusters(silent=True):
                queues = pbs_utils.get_queues(region)
                if zone not in queues:
                    raise ValueError(
                        f'Queue {zone!r} not found in cluster {region!r}. '
                        f'Available queues: {queues}')
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
                                                            clouds='pbs')
        if not regions:
            return False
        if region is None:
            return True
        return any(r.name == region for r in regions)

    @classmethod
    def expand_infras(cls) -> List[str]:
        """Enabled PBS clusters, each as 'PBS/cluster-name'."""
        return [
            f'{cls.canonical_name()}/{cluster}'
            for cluster in cls.existing_allowed_clusters(silent=True)
        ]


def _detect_gpu_type_resource(cluster: str, raw_type: str) -> Optional[str]:
    """The node resource under which ``raw_type`` is declared, if any."""
    for node in pbs_utils.get_pbs_nodes_info(cluster):
        if node.gpu_type == raw_type and node.gpu_type_resource is not None:
            return node.gpu_type_resource
    return None
