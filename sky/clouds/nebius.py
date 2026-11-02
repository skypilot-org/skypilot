""" Nebius Cloud. """
import fnmatch
import hashlib
import json
import os
import tempfile
import typing
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from sky import catalog
from sky import clouds
from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import nebius
from sky.provision.nebius import constants as nebius_constants
from sky.provision.nebius import utils
from sky.utils import annotations
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils import schemas
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib
    from sky.utils import volume as volume_lib

_INDENT_PREFIX = '    '
_SERVICE_ACCOUNT_ID_PREFIX = 'serviceaccount-'

_SERVICE_ACCOUNT_DOCS = ('https://docs.skypilot.co/en/latest/cloud-setup/'
                         'cloud-permissions/nebius.html')

logger = sky_logging.init_logger(__name__)


def _describe_credentials_load_error(e: Exception) -> str:
    """One line for an error raised while the SDK loads the credentials file.

    The SDK raises plain JSON/key/value errors here, which would otherwise be
    shown as a full traceback.
    """
    if isinstance(e, json.JSONDecodeError):
        return f'not valid JSON ({e})'
    if isinstance(e, KeyError):
        return f'missing field {e}'
    return str(e) or type(e).__name__


def _describe_request_error(e: Exception) -> Tuple[str, str]:
    """Returns (status code name, one-line message) for a Nebius RequestError.

    The SDK's own rendering also appends the trace ID and every nested
    service error, which makes the reason hard to read.
    """
    status = getattr(e, 'status', None)
    code = getattr(status, 'code', None)
    code_name = getattr(code, 'name', str(code))
    message = getattr(status, 'message', None)
    text = f'{code_name}: {message}' if message else code_name
    request_id = getattr(status, 'request_id', '')
    if request_id:
        text += f' (request ID: {request_id})'
    return code_name, text


def nebius_profile_in_aws_cred_and_config() -> bool:
    """Checks if Nebius Object Storage profile is set in aws credentials
    and profile."""

    credentials_path = os.path.expanduser('~/.aws/credentials')
    nebius_profile_exists_in_credentials = False
    if os.path.isfile(credentials_path):
        with open(credentials_path, 'r', encoding='utf-8') as file:
            for line in file:
                if f'[{nebius.NEBIUS_PROFILE_NAME}]' in line:
                    nebius_profile_exists_in_credentials = True

    config_path = os.path.expanduser('~/.aws/config')
    nebius_profile_exists_in_config = False
    if os.path.isfile(config_path):
        with open(config_path, 'r', encoding='utf-8') as file:
            for line in file:
                if f'[profile {nebius.NEBIUS_PROFILE_NAME}]' in line:
                    nebius_profile_exists_in_config = True

    return (nebius_profile_exists_in_credentials and
            nebius_profile_exists_in_config)


def _write_nebius_temp_credential_file(prefix: str, value: str) -> str:
    """Materialize effective Nebius config into a stable, uploadable file.

    The path is deterministic from ``value`` so repeated calls return the same
    path. ``Cloud.get_credential_file_mounts()`` runs on every launch for every
    enabled cloud (see ``sky/check.py::get_cloud_credential_file_mounts``), and
    its return value feeds the file-mounts hash that SkyPilot uses to dedupe
    rsync uploads to controllers and to decide whether ``sky launch --fast``
    may skip re-setup. A fresh ``NamedTemporaryFile`` per call would change
    that hash on every launch, busting the cache and breaking ``--fast`` for
    every enabled-cloud combo where Nebius is configured -- including launches
    on other clouds (AWS, etc).

    The path is also namespaced by UID so that two users on a host with a
    shared ``tempfile.gettempdir()`` (e.g. ``/tmp`` on Linux) cannot collide
    when they happen to share the same content -- one user's restrictive
    umask would otherwise lock the other user out.
    """
    uid = os.getuid() if hasattr(os, 'getuid') else 'default'
    digest = hashlib.sha1(value.encode('utf-8'),
                          usedforsecurity=False).hexdigest()[:16]
    path = os.path.join(tempfile.gettempdir(), f'{prefix}{uid}-{digest}')
    expected = value + '\n'
    try:
        with open(path, 'r', encoding='utf-8') as existing:
            if existing.read() == expected:
                return path
    except OSError:
        pass
    with open(path, 'w', encoding='utf-8') as new_file:
        new_file.write(expected)
    return path


def _get_service_account_id(
        region: str, cluster_name: str,
        override_configs: Optional[Dict[str, Any]]) -> Optional[str]:
    """Returns the service account to attach, from `nebius.remote_identity`.

    Mode names (LOCAL_CREDENTIALS, NO_UPLOAD) return None. SERVICE_ACCOUNT
    is rejected: SkyPilot does not create service accounts on Nebius.
    """
    remote_identity = skypilot_config.get_effective_workspace_region_config(
        cloud='nebius',
        region=region,
        keys=('remote_identity',),
        default_value=None,
        override_configs=override_configs)
    if isinstance(remote_identity, list):
        remote_identity = next((identity for profile in remote_identity
                                for pattern, identity in profile.items()
                                if fnmatch.fnmatchcase(cluster_name, pattern)),
                               None)
    if not isinstance(remote_identity, str):
        return None
    if (remote_identity.upper() ==
            schemas.RemoteIdentityOptions.SERVICE_ACCOUNT.value):
        raise exceptions.InvalidCloudConfigs(
            '`nebius.remote_identity: SERVICE_ACCOUNT` is not supported: '
            'SkyPilot does not create service accounts on Nebius. Set it to '
            'the ID of an existing service account (serviceaccount-...).')
    if remote_identity.startswith(_SERVICE_ACCOUNT_ID_PREFIX):
        return remote_identity
    return None


def _is_service_account_configured() -> bool:
    """Whether `nebius.remote_identity` attaches a service account anywhere.

    Checks the cloud-level value and every region in `region_configs`, for
    the active workspace and the global config, and any cluster name pattern.
    """
    regions = skypilot_config.get_workspace_cloud('nebius').get(
        'region_configs', None) or {}
    for region in [None, *regions]:
        remote_identity = skypilot_config.get_effective_workspace_region_config(
            cloud='nebius',
            region=region,
            keys=('remote_identity',),
            default_value=None)
        if isinstance(remote_identity, str):
            remote_identity = [{'*': remote_identity}]
        if not isinstance(remote_identity, list):
            continue
        for profile in remote_identity:
            if any(
                    isinstance(identity, str) and
                    identity.startswith(_SERVICE_ACCOUNT_ID_PREFIX)
                    for identity in profile.values()):
                return True
    return False


@registry.CLOUD_REGISTRY.register
class Nebius(clouds.Cloud):
    """Nebius GPU Cloud"""
    _REPR = 'Nebius'
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER:
            (f'Migrating disk is currently not supported on {_REPR}.'),
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER:
            ('Custom network tier is currently only supported for '
             'H100:8, H200:8, B200:8 and B300:8 on Nebius.'),
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS:
            ('High availability controllers are not supported on Nebius.'),
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK:
            ('Customized multiple network interfaces are not supported on '
             f'{_REPR}.'),
        clouds.CloudImplementationFeatures.LOCAL_DISK:
            (f'Local disk is not supported on {_REPR}'),
    }
    # Nebius maximum instance name length defined as <= 63 as a hostname length
    # 63 - 8 - 5 = 50 characters since
    # we add 4 character from UUID to make uniq `-xxxx`
    # our provisioner adds additional `-worker`.
    _MAX_CLUSTER_NAME_LEN_LIMIT = 50
    _regions: List[clouds.Region] = []
    # An existing service account can be attached to the VMs via
    # `nebius.remote_identity: serviceaccount-...`.
    _SUPPORTS_SERVICE_ACCOUNT_ON_REMOTE = True

    @classmethod
    def excludes_credentials_from_other_clouds(cls) -> bool:
        """Whether to keep Nebius credentials off clusters on other clouds.

        True when `nebius.remote_identity` attaches a service account, so the
        local Nebius credentials (e.g. the API server's own) are not handed to
        any cluster, like `NO_UPLOAD`.
        """
        return _is_service_account_configured()

    _BEST_DISK_TIER = resources_utils.DiskTier.HIGH
    _DEFAULT_DISK_TIER = resources_utils.DiskTier.MEDIUM
    # Nebius does not support ultra disk tier.
    _SUPPORTED_DISK_TIERS = (set(resources_utils.DiskTier) -
                             {resources_utils.DiskTier.ULTRA})

    # Using the latest SkyPilot provisioner API to provision and check status.
    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT
    # Ports are managed via Nebius security groups, mutated post-launch by
    # `sky/provision/nebius/instance.py:open_ports`.
    OPEN_PORTS_VERSION = clouds.OpenPortsVersion.UPDATABLE

    @classmethod
    def _unsupported_features_for_resources(
        cls,
        resources: 'resources_lib.Resources',
        region: Optional[str] = None,
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        unsupported = cls._CLOUD_UNSUPPORTED_FEATURES.copy()

        # Check if the accelerators support InfiniBand and come as 8 GPUs
        if resources.accelerators is not None:
            for acc_name, acc_count in resources.accelerators.items():
                if (acc_name.lower() in nebius_constants.INFINIBAND_ACCELERATORS
                        and acc_count == nebius_constants.INFINIBAND_GPU_COUNT):
                    # Remove CUSTOM_NETWORK_TIER from unsupported features for
                    # InfiniBand-capable accelerators. Refer to:
                    # https://docs.nebius.com/compute/clusters/gpu#fabrics
                    unsupported.pop(
                        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER,
                        None)
                    break

        return unsupported

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
        assert zone is None, 'Nebius does not support zones.'
        del accelerators, zone  # unused
        regions = catalog.get_region_zones_for_instance_type(
            instance_type, use_spot, 'nebius')

        if region is not None:
            regions = [r for r in regions if r.name == region]
        return regions

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        return catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                        clouds='nebius')

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
        regions = cls.regions_with_offering(instance_type,
                                            accelerators,
                                            use_spot,
                                            region=region,
                                            zone=None)
        for r in regions:
            assert r.zones is None, r
            yield r.zones

    @classmethod
    def check_disk_tier(
            cls, instance_type: Optional[str],
            disk_tier: Optional[resources_utils.DiskTier]) -> Tuple[bool, str]:
        del instance_type
        if (disk_tier is not None and
                disk_tier == resources_utils.DiskTier.ULTRA):
            return False, (
                'Nebius disk_tier=ultra is not supported now. '
                'Please use disk_tier={low, medium, high, best} instead.')
        return True, ''

    @classmethod
    def check_disk_tier_enabled(cls, instance_type: Optional[str],
                                disk_tier: resources_utils.DiskTier) -> None:
        ok, msg = cls.check_disk_tier(instance_type, disk_tier)
        if not ok:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.NotSupportedError(msg)

    def instance_type_to_hourly_cost(self,
                                     instance_type: str,
                                     use_spot: bool,
                                     region: Optional[str] = None,
                                     zone: Optional[str] = None) -> float:
        return catalog.get_hourly_cost(instance_type,
                                       use_spot=use_spot,
                                       region=region,
                                       zone=zone,
                                       clouds='nebius')

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
        return isinstance(other, Nebius)

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
        """Returns the default instance type for Nebius."""
        return catalog.get_default_instance_type(
            cpus=cpus,
            memory=memory,
            disk_tier=disk_tier,
            local_disk=local_disk,
            region=region,
            zone=zone,
            use_spot=use_spot,
            max_hourly_cost=max_hourly_cost,
            clouds='nebius')

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, Union[int, float]]]:
        return catalog.get_accelerators_from_instance_type(instance_type,
                                                           clouds='nebius')

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

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
        del dryrun
        assert zones is None, ('Nebius does not support zones', zones)

        resources = resources.assert_launchable()
        acc_dict = self.get_accelerators_from_instance_type(
            resources.instance_type)
        custom_resources = resources_utils.make_ray_custom_resources_str(
            acc_dict)
        platform, _ = resources.instance_type.split('_')

        # Selecting image_family by platform
        # https://docs.nebius.com/compute/storage/boot-disk-images
        if platform.startswith('cpu'):
            default_image_family = 'ubuntu24.04-driverless'
        elif platform.startswith('gpu'):
            default_image_family = 'ubuntu24.04-cuda13.0'
        else:
            raise RuntimeError('Unsupported instance type for Nebius cloud:'
                               f' {resources.instance_type}')

        cloud_image_id = resources.get_cloud_image_id()
        if cloud_image_id is None:
            image_id = default_image_family
        else:
            if None in cloud_image_id:
                image_id = cloud_image_id[None]
            else:
                image_id = cloud_image_id[region.name]

        config_fs = skypilot_config.get_effective_region_config(
            cloud='nebius',
            region=region.name,
            keys=('filesystems',),
            default_value=[])
        resources_vars_fs = []
        for i, fs in enumerate(config_fs):
            resources_vars_fs.append({
                'filesystem_id': fs['filesystem_id'],
                'filesystem_attach_mode': fs.get('attach_mode', 'READ_WRITE'),
                'filesystem_mount_path': fs.get(
                    'mount_path', f'/mnt/filesystem-skypilot-{i + 1}'),
                'filesystem_mount_tag': f'filesystem-skypilot-{i + 1}'
            })

        use_static_ip_address = skypilot_config.get_nested(
            ('nebius', 'use_static_ip_address'), default_value=False)
        disk_encrypted = skypilot_config.get_effective_region_config(
            cloud='nebius',
            region=region.name,
            keys=('disk_encrypted',),
            default_value=False)

        def _get_disk_tier() -> resources_utils.DiskTier:
            logger.debug(f'Getting disk tier for Nebius {resources.disk_tier}.')
            return Nebius._translate_disk_tier(resources.disk_tier)

        # Resolve BYO vs SkyPilot-managed security group. Mirrors the
        # `aws.security_group_name` resolution in `sky.clouds.aws.AWS`.
        # The result flows through the rendered Ray YAML
        # (`provider.security_group`) to bootstrap_instances and
        # terminate_instances, which branch on the ManagedBySkyPilot flag.
        user_sg_config = skypilot_config.get_effective_region_config(
            cloud='nebius',
            region=region.name,
            keys=('security_group_name',),
            default_value=None)
        user_sg: Optional[str] = None
        if isinstance(user_sg_config, str):
            user_sg = user_sg_config
        elif isinstance(user_sg_config, list):
            for profile in user_sg_config:
                pattern, sg_name = next(iter(profile.items()))
                if fnmatch.fnmatchcase(cluster_name.display_name, pattern):
                    user_sg = sg_name
                    break

        if user_sg is None:
            security_group = nebius_constants.SECURITY_GROUP_TEMPLATE.format(
                cluster_name.name_on_cloud)
            security_group_managed_by_skypilot = True
        else:
            security_group = user_sg
            security_group_managed_by_skypilot = False
            if resources.ports is not None:
                logger.warning(
                    f'Skip opening ports {resources.ports} for cluster '
                    f'{cluster_name.display_name!r}, as '
                    f'`nebius.security_group_name` in `~/.sky/config.yaml` '
                    f'is specified as {security_group!r}. Please ensure '
                    f'the specified security group has the requested ports '
                    f'configured; or, leave out '
                    f'`nebius.security_group_name` in '
                    f'`~/.sky/config.yaml`.')

        service_account_id = _get_service_account_id(
            region.name, cluster_name.display_name,
            resources.cluster_config_overrides)

        resources_vars: Dict[str, Any] = {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'use_static_ip_address': use_static_ip_address,
            'region': region.name,
            'project_id': utils.get_project_by_region(region.name),
            'image_id': image_id,
            # Nebius does not support specific zones.
            'zones': None,
            'use_spot': resources.use_spot,
            'filesystems': resources_vars_fs,
            'network_tier': resources.network_tier,
            'disk_tier': _get_disk_tier(),
            'disk_encrypted': disk_encrypted,
            'security_group': security_group,
            'security_group_managed_by_skypilot':
                str(security_group_managed_by_skypilot).lower(),
            'service_account_id': service_account_id,
        }

        docker_run_options = []

        if service_account_id is not None:
            # Let SkyPilot inside the container (e.g. autostop) authenticate
            # as the attached service account.
            docker_run_options.append(
                f'-v {nebius.VM_SERVICE_ACCOUNT_TOKEN_DIR}:'
                f'{nebius.VM_SERVICE_ACCOUNT_TOKEN_DIR}:ro')

        if acc_dict is not None:
            # Nebius cloud's docker runtime information does not contain
            # 'nvidia-container-runtime', causing no GPU option to be added to
            # the docker run command. We patch this by adding it here.
            docker_run_options.append('--gpus all')

            # Check for InfiniBand support with network_tier: best
            is_infiniband_capable = (
                platform in nebius_constants.INFINIBAND_INSTANCE_PLATFORMS)
            if (is_infiniband_capable and
                    resources.network_tier == resources_utils.NetworkTier.BEST):
                # For Docker containers, add InfiniBand device access and
                # IPC_LOCK capability
                if resources.extract_docker_image() is not None:
                    docker_run_options.extend(
                        nebius_constants.INFINIBAND_DOCKER_OPTIONS)

                    # Add InfiniBand environment variables to docker run options
                    for env_var, env_value in (
                            nebius_constants.INFINIBAND_ENV_VARS.items()):
                        docker_run_options.extend(
                            ['-e', f'{env_var}={env_value}'])

                # For all InfiniBand-capable instances, add env variables
                resources_vars[
                    'env_vars'] = nebius_constants.INFINIBAND_ENV_VARS

        if docker_run_options:
            resources_vars['docker_run_options'] = docker_run_options

        return resources_vars

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> 'resources_utils.FeasibleResources':
        """Returns a list of feasible resources for the given resources."""
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            ok, msg = Nebius.check_disk_tier(resources.instance_type,
                                             resources.disk_tier)
            if not ok:
                return resources_utils.FeasibleResources([], [], msg)
            return resources_utils.FeasibleResources([resources], [], None)

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                r = resources.copy(
                    cloud=Nebius(),
                    instance_type=instance_type,
                    accelerators=None,
                    cpus=None,
                )
                resource_list.append(r)
            return resource_list

        # Currently, handle a filter on accelerators only.
        accelerators = resources.accelerators
        if accelerators is None:
            # Return a default instance type
            default_instance_type = Nebius.get_default_instance_type(
                cpus=resources.cpus,
                memory=resources.memory,
                disk_tier=resources.disk_tier,
                local_disk=resources.local_disk,
                region=resources.region,
                zone=resources.zone,
                use_spot=resources.use_spot,
                max_hourly_cost=resources.max_hourly_cost)
            if default_instance_type is None:
                # TODO: Add hints to all return values in this method to help
                #  users understand why the resources are not launchable.
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
             memory=resources.memory,
             local_disk=resources.local_disk,
             region=resources.region,
             zone=resources.zone,
             max_hourly_cost=resources.max_hourly_cost,
             clouds='nebius')
        if instance_list is None:
            return resources_utils.FeasibleResources([], fuzzy_candidate_list,
                                                     None)
        return resources_utils.FeasibleResources(_make(instance_list),
                                                 fuzzy_candidate_list, None)

    @classmethod
    def _check_compute_credentials(
            cls) -> Tuple[bool, Optional[Union[str, Dict[str, str]]]]:
        """Checks if the user has access credentials to
        Nebius's compute service."""
        credentials_msg = (
            f'{_INDENT_PREFIX}Set up a service account key at '
            f'{nebius.credentials_path()} (see {_SERVICE_ACCOUNT_DOCS}), or '
            'save an access token:\n'
            f'{_INDENT_PREFIX}  $ nebius iam get-access-token > '
            f'{nebius.iam_token_path()}')
        tenant_msg = (
            f'{_INDENT_PREFIX}Save your tenant ID (from the web console, or '
            '`nebius iam tenant list`):\n'
            f'{_INDENT_PREFIX}  $ echo $NEBIUS_TENANT_ID > '
            f'{nebius.tenant_id_path()}')
        if not nebius.is_token_or_cred_file_exist():
            return False, f'Nebius credentials not found.\n{credentials_msg}'
        tenant_id = nebius.get_tenant_id()
        if tenant_id is None:
            return False, f'Nebius tenant ID not found.\n{tenant_msg}'
        try:
            # Parses the credentials file, so a malformed one fails here.
            sdk = nebius.sdk()
        except Exception as e:  # pylint: disable=broad-except
            return False, (
                f'Failed to load Nebius credentials from '
                f'{nebius.credentials_path()}: '
                f'{_describe_credentials_load_error(e)}\n{credentials_msg}')
        try:
            service = nebius.iam().ProjectServiceClient(sdk)
            # Without these, a key Nebius rejects keeps `sky check` waiting
            # for 15 minutes and then reports DEADLINE_EXCEEDED.
            service.list(nebius.iam().ListProjectsRequest(parent_id=tenant_id),
                         auth_timeout=nebius.CREDENTIAL_CHECK_TIMEOUT,
                         auth_options=nebius.fail_fast_auth_options()).wait()
        except nebius.request_error() as e:
            code_name, error = _describe_request_error(e)
            if code_name == 'PERMISSION_DENIED':
                hint = (f'{_INDENT_PREFIX}The service account needs the editor '
                        f'role in tenant {tenant_id}: add it to the tenant\'s '
                        f'editors group (see {_SERVICE_ACCOUNT_DOCS}).')
            elif code_name == 'NOT_FOUND':
                hint = (f'{_INDENT_PREFIX}Check the tenant ID in '
                        f'{nebius.tenant_id_path()}.\n{tenant_msg}')
            elif code_name in ('UNAUTHENTICATED', 'INVALID_ARGUMENT'):
                hint = (f'{_INDENT_PREFIX}Nebius rejected the credentials.\n'
                        f'{credentials_msg}')
            else:
                hint = f'{credentials_msg}\n{tenant_msg}'
            return False, f'{error}\n{hint}'
        return True, None

    @classmethod
    @annotations.lru_cache(scope='request')
    def _check_storage_credentials(
            cls) -> Tuple[bool, Optional[Union[str, Dict[str, str]]]]:
        """Checks if the user has access credentials to Nebius Object Storage.

        Returns:
            A tuple of a boolean value and a hint message where the bool
            is True when credentials needed for Nebius Object Storage is set.
            It is False when either of those are not set, which would hint
            with a string on unset credential.
        """
        hints = None
        if not nebius_profile_in_aws_cred_and_config():
            hints = (f'[{nebius.NEBIUS_PROFILE_NAME}] profile '
                     'is not set in ~/.aws/credentials.')
        if hints:
            hints += ' Run the following commands:'
            if not nebius_profile_in_aws_cred_and_config():
                hints += (
                    f'\n{_INDENT_PREFIX}  $ pip install boto3'
                    f'\n{_INDENT_PREFIX}  $ aws configure --profile nebius')
            hints += (
                f'\n{_INDENT_PREFIX}For more info: '
                'https://docs.skypilot.co/en/latest/getting-started/installation.html#nebius'  # pylint: disable=line-too-long
            )
        return (False, hints) if hints else (True, hints)

    def get_credential_file_mounts(self) -> Dict[str, str]:
        credential_file_mounts = {}
        tenant_id_path = nebius.tenant_id_path()
        tenant_id = nebius.get_tenant_id()
        if tenant_id is not None:
            # NOTE: We can't guarantee that the file will be deleted eventually,
            # but it doesn't contain sensitive information and should be cleaned
            # up by the OS eventually.
            credential_file_mounts[tenant_id_path] = (
                _write_nebius_temp_credential_file('nebius-tenant-id-',
                                                   tenant_id))

        for filepath in nebius.get_credential_file_paths():
            if filepath == tenant_id_path:
                continue
            credential_file_mounts[filepath] = filepath
        if nebius_profile_in_aws_cred_and_config():
            credential_file_mounts['~/.aws/credentials'] = '~/.aws/credentials'
            credential_file_mounts['~/.aws/config'] = '~/.aws/config'
        return credential_file_mounts

    @classmethod
    def get_current_user_identity(cls) -> Optional[List[str]]:
        # NOTE: used for very advanced SkyPilot functionality
        # Can implement later if desired
        return None

    def instance_type_exists(self, instance_type: str) -> bool:
        return catalog.instance_type_exists(instance_type, 'nebius')

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return catalog.validate_region_zone(region, zone, clouds='nebius')

    @classmethod
    def get_user_identities(cls) -> Optional[List[List[str]]]:
        """Returns the email address + project id of the active user."""
        nebius_workspace_config = json.dumps(
            skypilot_config.get_workspace_cloud('nebius'), sort_keys=True)
        return cls._get_user_identities(nebius_workspace_config)

    @classmethod
    @annotations.lru_cache(scope='request', maxsize=5)
    def _get_user_identities(
            cls, workspace_config: Optional[str]) -> Optional[List[List[str]]]:
        # We add workspace_config in args to avoid caching the identity for when
        # different workspace configs are used.
        del workspace_config  # Unused
        sdk = nebius.sdk()
        profile_client = nebius.iam().ProfileServiceClient(sdk)
        try:
            profile = nebius.sync_call(
                profile_client.get(nebius.iam().GetProfileRequest(),
                                   timeout=nebius.READ_TIMEOUT))
        except Exception as e:
            raise exceptions.CloudUserIdentityError(
                f'Error getting Nebius profile: {e}')
        if profile.user_profile is not None:
            if profile.user_profile.attributes is None:
                raise exceptions.CloudUserIdentityError(
                    'Nebius profile is a UserProfile, but has no attributes: '
                    f'{profile.user_profile}')
            if profile.user_profile.attributes.email is None:
                raise exceptions.CloudUserIdentityError(
                    'Nebius profile is a UserProfile, but has no email: '
                    f'{profile.user_profile}')
            return [[profile.user_profile.attributes.email]]
        if profile.service_account_profile is not None:
            if profile.service_account_profile.info is None:
                raise exceptions.CloudUserIdentityError(
                    'Nebius profile is a ServiceAccountProfile, but has no '
                    f'info: {profile.service_account_profile}')
            if profile.service_account_profile.info.metadata is None:
                raise exceptions.CloudUserIdentityError(
                    'Nebius profile is a ServiceAccountProfile, but has no '
                    f'metadata: {profile.service_account_profile}')
            if profile.service_account_profile.info.metadata.name is None:
                raise exceptions.CloudUserIdentityError(
                    'Nebius profile is a ServiceAccountProfile, but has no '
                    f'name: {profile.service_account_profile}')
            return [[profile.service_account_profile.info.metadata.name]]
        if profile.anonymous_profile is not None:
            return None
        unknown_profile_type = profile.which_field_in_oneof('profile')
        raise exceptions.CloudUserIdentityError(
            f'Nebius profile is of an unknown type - {unknown_profile_type}')

    # pylint: disable=import-outside-toplevel
    @classmethod
    def get_image_size(cls, image_id: str, region: Optional[str]) -> float:
        from grpc import StatusCode
        from nebius.aio.service_error import RequestError

        sdk = nebius.sdk()
        compute = nebius.compute()
        image_client = compute.ImageServiceClient(sdk)
        if image_id.startswith('computeimage-'):
            try:
                image = image_client.get(
                    compute.GetImageRequest(id=image_id)).wait()
            except RequestError as e:
                if e.status.code == StatusCode.NOT_FOUND:
                    raise ValueError(f'Image {image_id} does not exist') from e
                raise e
        else:
            parent_id = None
            if region is not None:
                parent_id = utils.get_project_by_region(region)
            request = compute.GetImageLatestByFamilyRequest(
                image_family=image_id, parent_id=parent_id)
            try:
                image = image_client.get_latest_by_family(request).wait()
            except RequestError as e:
                if e.status.code == StatusCode.NOT_FOUND:
                    raise ValueError(
                        f'Image family {image_id} does not exist') from e
                raise e

        return image.status.min_disk_size_bytes / 1024**3
