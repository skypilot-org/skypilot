"""Modal Cloud."""

from importlib import util as import_lib_util
import os
import typing
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from sky import catalog
from sky import clouds
from sky import sky_logging
from sky.utils import registry
from sky.utils import resources_utils

logger = sky_logging.init_logger(__name__)

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib
    from sky.utils import volume as volume_lib

_CREDENTIAL_FILE = '.modal.toml'
_REPR = 'Modal'


@registry.CLOUD_REGISTRY.register
class Modal(clouds.Cloud):
    """Modal Sandbox Cloud.

    Modal runs compute as ephemeral Sandboxes (max 24-hour lifetime).
    SSH access uses Modal's TCP tunnel (unencrypted_ports=[22]).
    """

    _REPR = _REPR
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.STOP:
            ('Modal Sandboxes have no stop/resume primitive. '
             'Use `sky down` to terminate the cluster.'),
        clouds.CloudImplementationFeatures.MULTI_NODE:
            ('Multi-node is not supported on Modal. Modal\'s @clustered API '
             '(private beta) is incompatible with SkyPilot\'s SSH-based '
             'multi-node model. Submit single-node jobs instead.'),
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER:
            ('Disk cloning from cluster is not supported on Modal.'),
        clouds.CloudImplementationFeatures.STORAGE_MOUNTING:
            ('Mounting object stores is not supported on Modal. Modal Sandbox '
             'uses gVisor which blocks FUSE mounts. Use `mode: COPY` to copy '
             'data to the container instead.'),
        clouds.CloudImplementationFeatures.HOST_CONTROLLERS: (
            'Modal Sandboxes have a 24-hour maximum lifetime and no persistent '
            'IP, making them unsuitable for hosting SkyPilot managed-job or '
            'SkyServe controllers.'),
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS:
            ('High availability controllers are not supported on Modal.'),
        clouds.CloudImplementationFeatures.AUTOSTOP:
            ('Modal Sandboxes cannot stop and resume; only terminate. '
             'Autostop is not supported.'),
        clouds.CloudImplementationFeatures.AUTODOWN:
            ('Autodown via SkyPilot is not supported on Modal. Modal Sandboxes '
             'terminate after 24 hours regardless.'),
        clouds.CloudImplementationFeatures.AUTO_TERMINATE:
            ('Auto-termination via SkyPilot is not supported on Modal.'),
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
            ('Customizing disk tier is not supported on Modal.'),
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER:
            ('Custom network tier is not supported on Modal.'),
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK: (
            'Customized multiple network interfaces are not supported on Modal.'
        ),
        clouds.CloudImplementationFeatures.LOCAL_DISK:
            (f'Local disk is not supported on {_REPR}.'),
    }
    _MAX_CLUSTER_NAME_LEN_LIMIT = 120

    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT
    OPEN_PORTS_VERSION = clouds.OpenPortsVersion.LAUNCH_ONLY

    @classmethod
    def _unsupported_features_for_resources(
        cls,
        resources: 'resources_lib.Resources',
        region: Optional[str] = None,
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        """The features not supported based on the resources provided.

        This method is used by check_features_are_supported() to check if the
        cloud implementation supports all the requested features.

        Returns:
            A dict of {feature: reason} for the features not supported by the
            cloud implementation.
        """
        del resources  # unused
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
        del accelerators  # unused
        regions = catalog.get_region_zones_for_instance_type(
            instance_type, use_spot, 'modal')

        if region is not None:
            regions = [r for r in regions if r.name == region]

        if zone is not None:
            for r in regions:
                assert r.zones is not None, r
                r.set_zones([z for z in r.zones if z.name == zone])
            regions = [r for r in regions if r.zones]
        return regions

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        return catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                        clouds='modal')

    @classmethod
    def zones_provision_loop(
        cls,
        *,
        region: str,
        num_nodes: int,
        instance_type: str,
        accelerators: Optional[Dict[str, int]] = None,
        use_spot: bool = False,
    ) -> Iterator[Optional[List['clouds.Zone']]]:
        del num_nodes  # unused
        regions = cls.regions_with_offering(instance_type,
                                            accelerators,
                                            use_spot,
                                            region=region,
                                            zone=None)
        for r in regions:
            assert r
            yield r.zones

    def instance_type_to_hourly_cost(self,
                                     instance_type: str,
                                     use_spot: bool,
                                     region: Optional[str] = None,
                                     zone: Optional[str] = None) -> float:
        return catalog.get_hourly_cost(instance_type,
                                       use_spot=use_spot,
                                       region=region,
                                       zone=zone,
                                       clouds='modal')

    def accelerators_to_hourly_cost(self,
                                    accelerators: Dict[str, int],
                                    use_spot: bool,
                                    region: Optional[str] = None,
                                    zone: Optional[str] = None) -> float:
        """Returns the hourly cost of the accelerators, in dollars/hour."""
        del accelerators, use_spot, region, zone  # unused
        return 0.0  # Modal includes accelerators in the hourly cost.

    def get_egress_cost(self, num_gigabytes: float) -> float:
        del num_gigabytes  # unused
        return 0.0

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
        """Returns the default instance type for Modal."""
        return catalog.get_default_instance_type(
            cpus=cpus,
            memory=memory,
            disk_tier=disk_tier,
            local_disk=local_disk,
            region=region,
            zone=zone,
            use_spot=use_spot,
            max_hourly_cost=max_hourly_cost,
            clouds='modal')

    @classmethod
    def get_accelerators_from_instance_type(
            cls, instance_type: str) -> Optional[Dict[str, Union[int, float]]]:
        return catalog.get_accelerators_from_instance_type(instance_type,
                                                           clouds='modal')

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
        """Emit Jinja template vars; also emits the 24h warning (PROV-04).

        Note: query_status stays as raise NotImplementedError (D-09) — it is
        never called at runtime because STATUS_VERSION=SKYPILOT routes all
        status queries through sky/provision/modal/instance.py:query_instances.
        """
        del dryrun, cluster_name, zones, num_nodes, volume_mounts  # unused

        # PROV-04: 24h lifetime warning at launch time.  Emitted before
        # provisioning starts so the user sees it regardless of whether the
        # launch succeeds.
        logger.warning(
            'Note: Modal Sandboxes have a maximum lifetime of 24 hours. '
            'The cluster will be terminated by Modal after 24 hours regardless '
            'of whether a job is still running. Use `sky down` to terminate '
            'early and save costs.')

        resources = resources.assert_launchable()
        acc_dict = self.get_accelerators_from_instance_type(
            resources.instance_type)
        custom_resources = resources_utils.make_ray_custom_resources_str(
            acc_dict)

        # Build gpu_str for Sandbox.create(gpu=): e.g. "H100" or "H100:4".
        gpu_str = None
        if acc_dict:
            acc_name, acc_count = list(acc_dict.items())[0]
            gpu_str = (acc_name if int(acc_count) == 1 else
                       f'{acc_name}:{int(acc_count)}')

        vcpus, mem_gib = self.get_vcpus_mem_from_instance_type(
            resources.instance_type)
        memory_mib = int(mem_gib * 1024) if mem_gib else None

        image_id = (resources.extract_docker_image() or
                    (resources.image_id or {}).get(region.name) or 'default')

        return {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
            'image_id': image_id,
            'gpu_str': gpu_str,
            'cpu': vcpus,
            'memory_mib': memory_mib,
            'use_spot': resources.use_spot,
        }

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> 'resources_utils.FeasibleResources':
        """Returns a list of feasible resources for the given resources."""
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            resources = resources.copy(accelerators=None)
            return resources_utils.FeasibleResources([resources], [], None)

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                r = resources.copy(
                    cloud=Modal(),
                    instance_type=instance_type,
                    accelerators=None,
                    cpus=None,
                )
                resource_list.append(r)
            return resource_list

        # Currently, handle a filter on accelerators only.
        accelerators = resources.accelerators
        if accelerators is None:
            default_instance_type = Modal.get_default_instance_type(
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
             local_disk=resources.local_disk,
             region=resources.region,
             zone=resources.zone,
             max_hourly_cost=resources.max_hourly_cost,
             clouds='modal')
        if instance_list is None:
            return resources_utils.FeasibleResources([], fuzzy_candidate_list,
                                                     None)
        return resources_utils.FeasibleResources(_make(instance_list),
                                                 fuzzy_candidate_list, None)

    @classmethod
    def _check_compute_credentials(
            cls) -> Tuple[bool, Optional[Union[str, Dict[str, str]]]]:
        """Checks if the user has access credentials to Modal's compute
        service."""
        return cls._check_credentials()

    @classmethod
    def _check_credentials(cls) -> Tuple[bool, Optional[str]]:
        """Verify that the user has valid credentials for Modal."""
        dependency_error_msg = ('Failed to import modal or TOML parser. '
                                'Install: pip install "skypilot[modal]".')
        try:
            modal_spec = import_lib_util.find_spec('modal')
            if modal_spec is None:
                return False, dependency_error_msg
            # Prefer stdlib tomllib (Python 3.11+); fallback to tomli
            tomllib_spec = import_lib_util.find_spec('tomllib')
            tomli_spec = import_lib_util.find_spec('tomli')
            if tomllib_spec is None and tomli_spec is None:
                return False, dependency_error_msg
        except ValueError:
            # docstring of importlib_util.find_spec:
            # First, sys.modules is checked to see if the module was already
            # imported. If so, then sys.modules[name].__spec__ is returned.
            # If that happens to be set to None, then ValueError is raised.
            return False, dependency_error_msg

        hint_msg = ('Credentials can be set up by running:\n'
                    '        $ pip install modal\n'
                    '        $ modal token set --token-id ak-... '
                    '--token-secret as-...\n'
                    '    For more information, see '
                    'https://modal.com/docs/reference/modal.config')

        # Modal env vars take full precedence over file-based credentials.
        # Check both before touching the filesystem (T-01-03: no value echo).
        token_id_env = os.environ.get('MODAL_TOKEN_ID')
        token_secret_env = os.environ.get('MODAL_TOKEN_SECRET')
        if token_id_env and token_secret_env:
            return True, None

        valid, error = cls._check_modal_credentials()
        if not valid:
            return False, f'{error}\n    {hint_msg}'
        return True, None

    @classmethod
    def _check_modal_credentials(cls,
                                 profile: str = 'default'
                                ) -> Tuple[bool, Optional[str]]:
        """Checks if ~/.modal.toml exists and contains token_id + token_secret.

        Respects the MODAL_CONFIG_PATH env var override for the config file
        location (Pitfall 5), and MODAL_PROFILE for section selection.
        Error messages reference only config path and missing key NAME - never
        the secret VALUE (T-01-01: no secret leakage).
        """
        config_path = os.environ.get('MODAL_CONFIG_PATH',
                                     os.path.expanduser('~/.modal.toml'))
        if not os.path.exists(config_path):
            return False, f'{config_path} does not exist.'

        # We don't need to import TOML parser if config file does not exist.
        # When needed, prefer stdlib tomllib (py>=3.11); otherwise use tomli.
        # TODO(jqueguiner): remove this fallback after dropping Python 3.10
        # support.
        try:
            try:
                import tomllib as toml  # pylint: disable=import-outside-toplevel
            except ModuleNotFoundError:  # py<3.11
                import tomli as toml  # pylint: disable=import-outside-toplevel
        except ModuleNotFoundError:
            # Should never happen. We already installed proper dependencies for
            # different Python versions in setup_files/dependencies.py.
            return False, (
                f'{config_path} exists but no TOML parser is available. '
                'Install tomli for Python < 3.11: pip install tomli.')

        # T-01-02: parse via stdlib tomllib/tomli only (no eval/custom parser);
        # wrap in try/except (TypeError, ValueError) -> return clean error.
        try:
            with open(config_path, 'rb') as cred_file:
                config = toml.load(cred_file)

            # Profile selection mirrors Modal's own config resolution:
            # 1. MODAL_PROFILE env var wins if set.
            # 2. Otherwise the profile flagged `active = true` (Modal marks the
            #    logged-in workspace this way; the section is rarely named
            #    'default').
            # 3. Otherwise a literal [default] section.
            # 4. Otherwise, if exactly one profile exists, use it.
            env_profile = os.environ.get('MODAL_PROFILE')
            if env_profile is not None:
                active_profile = env_profile
            else:
                active_profile = None
                for section, values in config.items():
                    if (isinstance(values, dict) and
                            values.get('active') is True):
                        active_profile = section
                        break
                if active_profile is None:
                    if profile in config:
                        active_profile = profile
                    elif len(config) == 1:
                        active_profile = next(iter(config))
                    else:
                        active_profile = profile
            if active_profile not in config:
                return False, (
                    f'{config_path} is missing [{active_profile}] section.')
            # Check key presence only - never read the secret value (T-01-01).
            if 'token_id' not in config[active_profile]:
                return False, (
                    f'{config_path} [{active_profile}] is missing token_id.')
            if 'token_secret' not in config[active_profile]:
                return False, (
                    f'{config_path} [{active_profile}] is missing token_secret.'
                )
        except (TypeError, ValueError):
            return False, f'{config_path} is not a valid TOML file.'

        return True, None

    def get_credential_file_mounts(self) -> Dict[str, str]:
        # CRED-02: mount ~/.modal.toml so skylet on the head node has Modal
        # credentials for subsequent API calls. Mirrors RunPod pattern.
        # T-01-04: intentional exposure to head node (accepted risk - same as
        # RunPod); recommend chmod 600 ~/.modal.toml.
        return {'~/.modal.toml': '~/.modal.toml'}

    @classmethod
    def get_user_identities(cls) -> Optional[List[List[str]]]:
        # NOTE: used for very advanced SkyPilot functionality
        # Can implement later if desired
        return None

    def instance_type_exists(self, instance_type: str) -> bool:
        return catalog.instance_type_exists(instance_type, 'modal')

    def validate_region_zone(
            self, region: Optional[str],
            zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        return catalog.validate_region_zone(region, zone, clouds='modal')

    @classmethod
    def get_image_size(cls, image_id: str, region: Optional[str]) -> float:
        # TODO(jqueguiner): use 0.0 for now to allow all images.
        del image_id, region  # unused
        return 0.0

    @classmethod
    def query_status(cls, name, tag_filters, region, zone, **kwargs):
        # Phase 3: only reachable when STATUS_VERSION=SKYPILOT after provisioner
        # exists and a Modal cluster has been created.
        del name, tag_filters, region, zone, kwargs  # unused
        raise NotImplementedError
