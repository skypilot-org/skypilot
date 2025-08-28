"""Util constants/functions for SkyPilot Controllers."""
import copy
import dataclasses
import enum
import os
import tempfile
import typing
from typing import Any, Callable, Dict, Iterable, List, Optional, Set
import uuid

import colorama

from sky import check as sky_check
from sky import clouds
from sky import exceptions
from sky import global_user_state
from sky import resources
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import cloudflare
from sky.clouds import cloud as sky_cloud
from sky.clouds import gcp
from sky.data import data_utils
from sky.data import storage as storage_lib
from sky.jobs import constants as managed_job_constants
from sky.jobs import state as managed_job_state
from sky.provision.kubernetes import constants as kubernetes_constants
from sky.serve import constants as serve_constants
from sky.serve import serve_state
from sky.setup_files import dependencies
from sky.skylet import constants
from sky.skylet import log_lib
from sky.utils import annotations
from sky.utils import common
from sky.utils import common_utils
from sky.utils import config_utils
from sky.utils import env_options
from sky.utils import registry
from sky.utils import rich_utils
from sky.utils import ux_utils
from sky.utils import yaml_utils

if typing.TYPE_CHECKING:
    import psutil

    from sky import task as task_lib
    from sky.backends import cloud_vm_ray_backend
else:
    from sky.adaptors import common as adaptors_common
    psutil = adaptors_common.LazyImport('psutil')

logger = sky_logging.init_logger(__name__)

# Message thrown when APIs sky.jobs.launch(), sky.serve.up() received an invalid
# controller resources spec.
CONTROLLER_RESOURCES_NOT_VALID_MESSAGE = (
    '{controller_type} controller resources is not valid, please check '
    '~/.sky/config.yaml file and make sure '
    '{controller_type}.controller.resources is a valid resources spec. '
    'Details:\n  {err}')

# The suffix for local skypilot config path for a job/service in file mounts
# that tells the controller logic to update the config with specific settings,
# e.g., removing the ssh_proxy_command when a job/service is launched in a same
# cloud as controller.
_LOCAL_SKYPILOT_CONFIG_PATH_SUFFIX = (
    '__skypilot:local_skypilot_config_path.yaml')


@dataclasses.dataclass
class _ControllerSpec:
    """Spec for skypilot controllers."""
    controller_type: str
    name: str
    cluster_name: str
    in_progress_hint: Callable[[bool], str]
    decline_cancel_hint: str
    _decline_down_when_failed_to_fetch_status_hint: str
    decline_down_for_dirty_controller_hint: str
    _check_cluster_name_hint: str
    default_hint_if_non_existent: str
    connection_error_hint: str
    default_resources_config: Dict[str, Any]
    default_autostop_config: Dict[str, Any]

    @property
    def decline_down_when_failed_to_fetch_status_hint(self) -> str:
        return self._decline_down_when_failed_to_fetch_status_hint.format(
            cluster_name=self.cluster_name)

    @property
    def check_cluster_name_hint(self) -> str:
        return self._check_cluster_name_hint.format(
            cluster_name=self.cluster_name)


# TODO: refactor controller class to not be an enum.
class Controllers(enum.Enum):
    """Skypilot controllers."""
    # NOTE(dev): Keep this align with
    # sky/cli.py::_CONTROLLER_TO_HINT_OR_RAISE
    JOBS_CONTROLLER = _ControllerSpec(
        controller_type='jobs',
        name='managed jobs controller',
        cluster_name=common.JOB_CONTROLLER_NAME,
        in_progress_hint=lambda _:
        ('* {job_info}To see all managed jobs: '
         f'{colorama.Style.BRIGHT}sky jobs queue{colorama.Style.RESET_ALL}'),
        decline_cancel_hint=(
            'Cancelling the jobs controller\'s jobs is not allowed.\nTo cancel '
            f'managed jobs, use: {colorama.Style.BRIGHT}sky jobs cancel '
            f'<managed job IDs> [--all]{colorama.Style.RESET_ALL}'),
        _decline_down_when_failed_to_fetch_status_hint=(
            f'{colorama.Fore.RED}Tearing down the jobs controller while '
            'it is in INIT state is not supported (this means a job launch '
            'is in progress or the previous launch failed), as we cannot '
            'guarantee that all the managed jobs are finished. Please wait '
            'until the jobs controller is UP or fix it with '
            f'{colorama.Style.BRIGHT}sky start '
            '{cluster_name}'
            f'{colorama.Style.RESET_ALL}.'),
        decline_down_for_dirty_controller_hint=(
            f'{colorama.Fore.RED}In-progress managed jobs found. To avoid '
            f'resource leakage, cancel all jobs first: {colorama.Style.BRIGHT}'
            f'sky jobs cancel -a{colorama.Style.RESET_ALL}\n'),
        _check_cluster_name_hint=('Cluster {cluster_name} is reserved for '
                                  'managed jobs controller.'),
        default_hint_if_non_existent='No in-progress managed jobs.',
        connection_error_hint=(
            'Failed to connect to jobs controller, please try again later.'),
        default_resources_config=managed_job_constants.CONTROLLER_RESOURCES,
        default_autostop_config=managed_job_constants.CONTROLLER_AUTOSTOP)
    SKY_SERVE_CONTROLLER = _ControllerSpec(
        controller_type='serve',
        name='serve controller',
        cluster_name=common.SKY_SERVE_CONTROLLER_NAME,
        in_progress_hint=(
            lambda pool:
            (f'* To see detailed pool status: {colorama.Style.BRIGHT}'
             f'sky jobs pool status -v{colorama.Style.RESET_ALL}') if pool else
            (f'* To see detailed service status: {colorama.Style.BRIGHT}'
             f'sky serve status -v{colorama.Style.RESET_ALL}')),
        decline_cancel_hint=(
            'Cancelling the sky serve controller\'s jobs is not allowed.'),
        _decline_down_when_failed_to_fetch_status_hint=(
            f'{colorama.Fore.RED}Tearing down the sky serve controller '
            'while it is in INIT state is not supported (this means a sky '
            'serve up is in progress or the previous launch failed), as we '
            'cannot guarantee that all the services are terminated. Please '
            'wait until the sky serve controller is UP or fix it with '
            f'{colorama.Style.BRIGHT}sky start '
            '{cluster_name}'
            f'{colorama.Style.RESET_ALL}.'),
        decline_down_for_dirty_controller_hint=(
            f'{colorama.Fore.RED}Tearing down the sky serve controller is not '
            'supported, as it is currently serving the following services: '
            '{service_names}. Please terminate the services first with '
            f'{colorama.Style.BRIGHT}sky serve down -a'
            f'{colorama.Style.RESET_ALL}.'),
        _check_cluster_name_hint=('Cluster {cluster_name} is reserved for '
                                  'sky serve controller.'),
        default_hint_if_non_existent='No live services.',
        connection_error_hint=(
            'Failed to connect to serve controller, please try again later.'),
        default_resources_config=serve_constants.CONTROLLER_RESOURCES,
        default_autostop_config=serve_constants.CONTROLLER_AUTOSTOP)

    @classmethod
    def from_name(cls, name: Optional[str]) -> Optional['Controllers']:
        """Check if the cluster name is a controller name.

        Returns:
            The controller if the cluster name is a controller name.
            Otherwise, returns None.
        """
        if name is None:
            return None
        controller = None
        # The controller name is always the same. However, on the client-side,
        # we may not know the exact name, because we are missing the server-side
        # common.SERVER_ID. So, we will assume anything that matches the prefix
        # is a controller.
        prefix = None
        if name.startswith(common.SKY_SERVE_CONTROLLER_PREFIX):
            controller = cls.SKY_SERVE_CONTROLLER
            prefix = common.SKY_SERVE_CONTROLLER_PREFIX
        elif name.startswith(common.JOB_CONTROLLER_PREFIX):
            controller = cls.JOBS_CONTROLLER
            prefix = common.JOB_CONTROLLER_PREFIX
        if controller is not None and name != controller.value.cluster_name:
            # The client-side cluster_name is not accurate. Assume that `name`
            # is the actual cluster name, so need to set the controller's
            # cluster name to the input name.

            # Assert that the cluster name is well-formed. It should be
            # {prefix}{hash}, where prefix is set above, and hash is a valid
            # user hash.
            assert prefix is not None, prefix
            assert name.startswith(prefix), name
            assert common_utils.is_valid_user_hash(name[len(prefix):]), (name,
                                                                         prefix)

            # Update the cluster name.
            controller.value.cluster_name = name
        return controller

    @classmethod
    def from_type(cls, controller_type: str) -> Optional['Controllers']:
        """Get the controller by controller type.

        Returns:
            The controller if the controller type is valid.
            Otherwise, returns None.
        """
        for controller in cls:
            if controller.value.controller_type == controller_type:
                return controller
        return None


def get_controller_for_pool(pool: bool) -> Controllers:
    """Get the controller type."""
    if pool:
        return Controllers.JOBS_CONTROLLER
    return Controllers.SKY_SERVE_CONTROLLER


def high_availability_specified(cluster_name: Optional[str]) -> bool:
    """Check if the controller high availability is specified in user config.
    """
    controller = Controllers.from_name(cluster_name)
    if controller is None:
        return False

    if skypilot_config.loaded():
        return skypilot_config.get_nested((controller.value.controller_type,
                                           'controller', 'high_availability'),
                                          False)
    return False


# Install cli dependencies. Not using SkyPilot wheels because the wheel
# can be cleaned up by another process.
def _get_cloud_dependencies_installation_commands(
        controller: Controllers) -> List[str]:
    # We use <step>/<total> instead of strong formatting, as we need to update
    # the <total> at the end of the for loop, and python does not support
    # partial string formatting.
    prefix_str = ('[<step>/<total>] Check & install cloud dependencies '
                  'on controller: ')
    commands: List[str] = []
    # This is to make sure the shorter checking message does not have junk
    # characters from the previous message.
    empty_str = ' ' * 20

    # All python dependencies will be accumulated and then installed in one
    # command at the end. This is very fast if the packages are already
    # installed, so we don't check that.
    python_packages: Set[str] = set()

    # add flask to the controller dependencies for dashboard
    python_packages.add('flask')

    step_prefix = prefix_str.replace('<step>', str(len(commands) + 1))
    commands.append(f'echo -en "\\r{step_prefix}uv{empty_str}" &&'
                    f'{constants.SKY_UV_INSTALL_CMD} >/dev/null 2>&1')

    enabled_compute_clouds = set(
        sky_check.get_cached_enabled_clouds_or_refresh(
            sky_cloud.CloudCapability.COMPUTE))
    enabled_storage_clouds = set(
        sky_check.get_cached_enabled_clouds_or_refresh(
            sky_cloud.CloudCapability.STORAGE))
    enabled_clouds = enabled_compute_clouds.union(enabled_storage_clouds)
    enabled_k8s_and_ssh = [
        repr(cloud)
        for cloud in enabled_clouds
        if isinstance(cloud, clouds.Kubernetes)
    ]
    k8s_and_ssh_label = ' and '.join(sorted(enabled_k8s_and_ssh))
    k8s_dependencies_installed = False

    for cloud in enabled_clouds:
        cloud_python_dependencies: List[str] = copy.deepcopy(
            dependencies.extras_require[cloud.canonical_name()])

        if isinstance(cloud, clouds.Azure):
            # azure-cli cannot be normally installed by uv.
            # See comments in sky/skylet/constants.py.
            cloud_python_dependencies.remove(dependencies.AZURE_CLI)

            step_prefix = prefix_str.replace('<step>', str(len(commands) + 1))
            commands.append(
                f'echo -en "\\r{step_prefix}azure-cli{empty_str}" &&'
                f'{constants.SKY_UV_PIP_CMD} install --prerelease=allow '
                f'"{dependencies.AZURE_CLI}" > /dev/null 2>&1')
        elif isinstance(cloud, clouds.GCP):
            step_prefix = prefix_str.replace('<step>', str(len(commands) + 1))
            commands.append(f'echo -en "\\r{step_prefix}GCP SDK{empty_str}" &&'
                            f'{gcp.GOOGLE_SDK_INSTALLATION_COMMAND}')
            if clouds.cloud_in_iterable(clouds.Kubernetes(), enabled_clouds):
                # Install gke-gcloud-auth-plugin used for exec-auth with GKE.
                # We install the plugin here instead of the next elif branch
                # because gcloud is required to install the plugin, so the order
                # of command execution is critical.

                # We install plugin here regardless of whether exec-auth is
                # actually used as exec-auth may be used in the future.
                # TODO (kyuds): how to implement conservative installation?
                commands.append(
                    '(command -v gke-gcloud-auth-plugin &>/dev/null || '
                    '(gcloud components install gke-gcloud-auth-plugin --quiet &>/dev/null))')  # pylint: disable=line-too-long
        elif isinstance(cloud, clouds.Nebius):
            step_prefix = prefix_str.replace('<step>', str(len(commands) + 1))
            commands.append(
                f'echo -en "\\r{step_prefix}Nebius{empty_str}" && '
                'curl -sSL https://storage.eu-north1.nebius.cloud/cli/install.sh '  # pylint: disable=line-too-long
                '| sudo NEBIUS_INSTALL_FOLDER=/usr/local/bin bash &> /dev/null && '
                'nebius profile create --profile sky '
                '--endpoint api.nebius.cloud '
                '--service-account-file $HOME/.nebius/credentials.json '
                '&> /dev/null || echo "Unable to create Nebius profile."')
        elif (isinstance(cloud, clouds.Kubernetes) and
              not k8s_dependencies_installed):
            step_prefix = prefix_str.replace('<step>', str(len(commands) + 1))
            commands.append(
                f'echo -en "\\r{step_prefix}{k8s_and_ssh_label}{empty_str}" && '
                # Install k8s + skypilot dependencies
                'sudo bash -c "if '
                '! command -v curl &> /dev/null || '
                '! command -v socat &> /dev/null || '
                '! command -v netcat &> /dev/null; '
                'then apt update &> /dev/null && '
                'apt install curl socat netcat -y &> /dev/null; '
                'fi" && '
                # Install kubectl
                'ARCH=$(uname -m) && '
                'if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then '
                '  ARCH="arm64"; '
                'else '
                '  ARCH="amd64"; '
                'fi && '
                '(command -v kubectl &>/dev/null || '
                '(curl -s -LO "https://dl.k8s.io/release/v1.31.6'
                '/bin/linux/$ARCH/kubectl" && '
                'sudo install -o root -g root -m 0755 '
                'kubectl /usr/local/bin/kubectl)) && '
                f'echo -e \'#!/bin/bash\\nexport PATH="{kubernetes_constants.SKY_K8S_EXEC_AUTH_PATH}"\\nexec "$@"\' | sudo tee /usr/local/bin/{kubernetes_constants.SKY_K8S_EXEC_AUTH_WRAPPER} > /dev/null && '  # pylint: disable=line-too-long
                f'sudo chmod +x /usr/local/bin/{kubernetes_constants.SKY_K8S_EXEC_AUTH_WRAPPER}')  # pylint: disable=line-too-long
            k8s_dependencies_installed = True
        elif isinstance(cloud, clouds.Cudo):
            step_prefix = prefix_str.replace('<step>', str(len(commands) + 1))
            commands.append(
                f'echo -en "\\r{step_prefix}cudoctl{empty_str}" && '
                'wget https://download.cudo.org/compute/cudoctl-0.3.2-amd64.deb -O ~/cudoctl.deb > /dev/null 2>&1 && '  # pylint: disable=line-too-long
                'sudo dpkg -i ~/cudoctl.deb > /dev/null 2>&1')
        elif isinstance(cloud, clouds.IBM):
            if controller != Controllers.JOBS_CONTROLLER:
                # We only need IBM deps on the jobs controller.
                cloud_python_dependencies = []
        elif isinstance(cloud, clouds.Vast):
            step_prefix = prefix_str.replace('<step>', str(len(commands) + 1))
            commands.append(f'echo -en "\\r{step_prefix}Vast{empty_str}" && '
                            'pip list | grep vastai_sdk > /dev/null 2>&1 || '
                            'pip install "vastai_sdk>=0.1.12" > /dev/null 2>&1')

        python_packages.update(cloud_python_dependencies)

    if (cloudflare.NAME
            in storage_lib.get_cached_enabled_storage_cloud_names_or_refresh()):
        python_packages.update(dependencies.extras_require['cloudflare'])

    packages_string = ' '.join([f'"{package}"' for package in python_packages])
    step_prefix = prefix_str.replace('<step>', str(len(commands) + 1))
    commands.append(
        f'echo -en "\\r{step_prefix}cloud python packages{empty_str}" && '
        f'{constants.SKY_UV_PIP_CMD} install {packages_string} > /dev/null 2>&1'
    )

    total_commands = len(commands)
    finish_prefix = prefix_str.replace('[<step>/<total>] ', '  ')
    commands.append(f'echo -e "\\r{finish_prefix}done.{empty_str}"')

    commands = [
        command.replace('<total>', str(total_commands)) for command in commands
    ]
    return commands


def check_cluster_name_not_controller(
        cluster_name: Optional[str],
        operation_str: Optional[str] = None) -> None:
    """Errors out if the cluster name is a controller name.

    Raises:
      sky.exceptions.NotSupportedError: if the cluster name is a controller
        name, raise with an error message explaining 'operation_str' is not
        allowed.

    Returns:
      None, if the cluster name is not a controller name.
    """
    controller = Controllers.from_name(cluster_name)
    if controller is not None:
        msg = controller.value.check_cluster_name_hint
        if operation_str is not None:
            msg += f' {operation_str} is not allowed.'
        with ux_utils.print_exception_no_traceback():
            raise exceptions.NotSupportedError(msg)


# Internal only:
def download_and_stream_job_log(
        backend: 'cloud_vm_ray_backend.CloudVmRayBackend',
        handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle',
        local_dir: str,
        job_ids: Optional[List[str]] = None) -> Optional[str]:
    """Downloads and streams the latest job log.

    This function is only used by jobs controller and sky serve controller.

    If the log cannot be fetched for any reason, return None.
    """
    os.makedirs(os.path.expanduser(local_dir), exist_ok=True)
    log_file = None
    try:
        log_dirs = backend.sync_down_logs(
            handle,
            # Download the log of the latest job.
            # The job_id for the managed job running on the cluster is not
            # necessarily 1, as it is possible that the worker node in a
            # multi-node cluster is preempted, and we recover the managed job
            # on the existing cluster, which leads to a larger job_id. Those
            # job_ids all represent the same logical managed job.
            job_ids=job_ids,
            local_dir=local_dir)
    except Exception as e:  # pylint: disable=broad-except
        # We want to avoid crashing the controller. sync_down_logs() is pretty
        # complicated and could crash in various places (creating remote
        # runners, executing remote code, decoding the payload, etc.). So, we
        # use a broad except and just return None.
        logger.info(
            f'Failed to download the logs: '
            f'{common_utils.format_exception(e)}',
            exc_info=True)
        return None

    if not log_dirs:
        logger.error('Failed to find the logs for the user program.')
        return None

    log_dir = list(log_dirs.values())[0]
    log_file = os.path.expanduser(os.path.join(log_dir, 'run.log'))

    # Print the logs to the console.
    # TODO(zhwu): refactor this into log_utils, along with the refactoring for
    # the log_lib.tail_logs.
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            # Stream the logs to the console without reading the whole file into
            # memory.
            start_streaming = False
            for line in f:
                if log_lib.LOG_FILE_START_STREAMING_AT in line:
                    start_streaming = True
                if start_streaming:
                    print(line, end='', flush=True)
    except FileNotFoundError:
        logger.error('Failed to find the logs for the user '
                     f'program at {log_file}.')
    except Exception as e:  # pylint: disable=broad-except
        logger.error(
            f'Failed to stream the logs for the user program at '
            f'{log_file}: {common_utils.format_exception(e)}',
            exc_info=True)

    return log_file


def shared_controller_vars_to_fill(
        controller: Controllers, remote_user_config_path: str,
        local_user_config: Dict[str, Any]) -> Dict[str, str]:
    if not local_user_config:
        local_user_config_path = None
    else:
        # Remove admin_policy from local_user_config so that it is not applied
        # again on the controller. This is required since admin_policy is not
        # installed on the controller.
        local_user_config.pop('admin_policy', None)
        # Remove allowed_contexts from local_user_config since the controller
        # may be running in a Kubernetes cluster with in-cluster auth and may
        # not have kubeconfig available to it. This is the typical case since
        # remote_identity default for Kubernetes is SERVICE_ACCOUNT.
        # TODO(romilb): We should check the cloud the controller is running on
        # before popping allowed_contexts. If it is not on Kubernetes,
        # we may be able to use allowed_contexts.
        local_user_config.pop('allowed_contexts', None)
        with tempfile.NamedTemporaryFile(
                delete=False,
                suffix=_LOCAL_SKYPILOT_CONFIG_PATH_SUFFIX) as temp_file:
            yaml_utils.dump_yaml(temp_file.name, dict(**local_user_config))
        local_user_config_path = temp_file.name

    vars_to_fill: Dict[str, Any] = {
        'cloud_dependencies_installation_commands':
            _get_cloud_dependencies_installation_commands(controller),
        # We need to activate the python environment on the controller to ensure
        # cloud SDKs are installed in SkyPilot runtime environment and can be
        # accessed.
        'sky_activate_python_env': constants.ACTIVATE_SKY_REMOTE_PYTHON_ENV,
        'sky_python_cmd': constants.SKY_PYTHON_CMD,
        'local_user_config_path': local_user_config_path,
    }
    env_vars: Dict[str, str] = {
        env.env_key: str(int(env.get())) for env in env_options.Options
    }
    env_vars.update({
        # Should not use $USER here, as that env var can be empty when
        # running in a container.
        constants.USER_ENV_VAR: common_utils.get_current_user_name(),
        constants.USER_ID_ENV_VAR: common_utils.get_user_hash(),
        # Skip cloud identity check to avoid the overhead.
        env_options.Options.SKIP_CLOUD_IDENTITY_CHECK.env_key: '1',
        # Disable minimize logging to get more details on the controller.
        env_options.Options.MINIMIZE_LOGGING.env_key: '0',
        # Make sure the clusters launched by the controller are marked as
        # launched with a remote API server if the controller is launched
        # with a remote API server.
        constants.USING_REMOTE_API_SERVER_ENV_VAR: str(
            common_utils.get_using_remote_api_server()),
    })
    if skypilot_config.loaded():
        # Only set the SKYPILOT_CONFIG env var if the user has a config file.
        env_vars[
            skypilot_config.ENV_VAR_SKYPILOT_CONFIG] = remote_user_config_path
    vars_to_fill['controller_envs'] = env_vars
    return vars_to_fill


def get_controller_resources(
    controller: Controllers,
    task_resources: Iterable['resources.Resources'],
) -> Set['resources.Resources']:
    """Read the skypilot config and setup the controller resources.

    Returns:
        A set of controller resources that will be used to launch the
        controller. All fields are the same except for the cloud. If no
        controller exists and the controller resources has no cloud
        specified, the controller will be launched on one of the clouds
        of the task resources for better connectivity.
    """
    controller_resources_config_copied: Dict[str, Any] = copy.copy(
        controller.value.default_resources_config)
    if skypilot_config.loaded():
        # Override the controller resources with the ones specified in the
        # config.
        custom_controller_resources_config = skypilot_config.get_nested(
            (controller.value.controller_type, 'controller', 'resources'), None)
        if custom_controller_resources_config is not None:
            controller_resources_config_copied.update(
                custom_controller_resources_config)
        # Compatibility with the old way of specifying the controller autostop
        # config. TODO(cooperc): Remove this before 0.12.0.
        custom_controller_autostop_config = skypilot_config.get_nested(
            (controller.value.controller_type, 'controller', 'autostop'), None)
        if custom_controller_autostop_config is not None:
            logger.warning(
                f'{colorama.Fore.YELLOW}Warning: Config value '
                f'`{controller.value.controller_type}.controller.autostop` '
                'is deprecated. Please use '
                f'`{controller.value.controller_type}.controller.resources.'
                f'autostop` instead.{colorama.Style.RESET_ALL}')
            # Only set the autostop config if it is not already specified.
            if controller_resources_config_copied.get('autostop') is None:
                controller_resources_config_copied['autostop'] = (
                    custom_controller_autostop_config)
            else:
                logger.warning(f'{colorama.Fore.YELLOW}Ignoring the old '
                               'config, since it is already specified in '
                               f'resources.{colorama.Style.RESET_ALL}')
    # Set the default autostop config for the controller, if not already
    # specified.
    if controller_resources_config_copied.get('autostop') is None:
        controller_resources_config_copied['autostop'] = (
            controller.value.default_autostop_config)

    try:
        controller_resources = resources.Resources.from_yaml_config(
            controller_resources_config_copied)
    except ValueError as e:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                CONTROLLER_RESOURCES_NOT_VALID_MESSAGE.format(
                    controller_type=controller.value.controller_type,
                    err=common_utils.format_exception(
                        e, use_bracket=True)).capitalize()) from e
    # TODO(tian): Support multiple resources for the controller. One blocker
    # here is the semantic if controller resources use `ordered` and we want
    # to override it with multiple cloud from task resources.
    if len(controller_resources) != 1:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                CONTROLLER_RESOURCES_NOT_VALID_MESSAGE.format(
                    controller_type=controller.value.controller_type,
                    err=f'Expected exactly one resource, got '
                    f'{len(controller_resources)} resources: '
                    f'{controller_resources}').capitalize())
    controller_resources_to_use: resources.Resources = list(
        controller_resources)[0]

    controller_record = global_user_state.get_cluster_from_name(
        controller.value.cluster_name)
    if controller_record is not None:
        handle = controller_record.get('handle', None)
        if handle is not None:
            # Use the existing resources, but override the autostop config with
            # the one currently specified in the config.
            controller_resources_to_use = handle.launched_resources.copy(
                autostop=controller_resources_config_copied.get('autostop'))

    # If the controller and replicas are from the same cloud (and region/zone),
    # it should provide better connectivity. We will let the controller choose
    # from the clouds (and regions/zones) of the resources if the user does not
    # specify the cloud (and region/zone) for the controller.

    requested_clouds_with_region_zone: Dict[str, Dict[Optional[str],
                                                      Set[Optional[str]]]] = {}
    for resource in task_resources:
        if resource.cloud is not None:
            cloud_name = str(resource.cloud)
            if cloud_name not in requested_clouds_with_region_zone:
                try:
                    resource.cloud.check_features_are_supported(
                        resources.Resources(),
                        {clouds.CloudImplementationFeatures.HOST_CONTROLLERS})
                except exceptions.NotSupportedError:
                    # Skip the cloud if it does not support hosting controllers.
                    continue
                requested_clouds_with_region_zone[cloud_name] = {}
            if resource.region is None:
                # If one of the resource.region is None, this could represent
                # that the user is unsure about which region the resource is
                # hosted in. In this case, we allow any region for this cloud.
                requested_clouds_with_region_zone[cloud_name] = {None: {None}}
            elif None not in requested_clouds_with_region_zone[cloud_name]:
                if resource.region not in requested_clouds_with_region_zone[
                        cloud_name]:
                    requested_clouds_with_region_zone[cloud_name][
                        resource.region] = set()
                # If one of the resource.zone is None, allow any zone in the
                # region.
                if resource.zone is None:
                    requested_clouds_with_region_zone[cloud_name][
                        resource.region] = {None}
                elif None not in requested_clouds_with_region_zone[cloud_name][
                        resource.region]:
                    requested_clouds_with_region_zone[cloud_name][
                        resource.region].add(resource.zone)
        else:
            # if one of the resource.cloud is None, this could represent user
            # does not know which cloud is best for the specified resources.
            # For example:
            #   resources:
            #     - accelerators: L4     # Both available on AWS and GCP
            #     - cloud: runpod
            #       accelerators: A40
            # In this case, we allow the controller to be launched on any cloud.
            requested_clouds_with_region_zone.clear()
            break

    # Extract filtering criteria from the controller resources specified by the
    # user.
    controller_cloud = str(
        controller_resources_to_use.cloud
    ) if controller_resources_to_use.cloud is not None else None
    controller_region = controller_resources_to_use.region
    controller_zone = controller_resources_to_use.zone

    # Filter clouds if controller_resources_to_use.cloud is specified.
    filtered_clouds: Set[str] = {controller_cloud
                                } if controller_cloud is not None else set(
                                    requested_clouds_with_region_zone.keys())

    # Filter regions and zones and construct the result.
    result: Set[resources.Resources] = set()
    for cloud_name in filtered_clouds:
        regions = requested_clouds_with_region_zone.get(cloud_name,
                                                        {None: {None}})

        # Filter regions if controller_resources_to_use.region is specified.
        filtered_regions: Set[Optional[str]] = ({
            controller_region
        } if controller_region is not None else set(regions.keys()))

        for region in filtered_regions:
            zones = regions.get(region, {None})

            # Filter zones if controller_resources_to_use.zone is specified.
            filtered_zones: Set[Optional[str]] = ({
                controller_zone
            } if controller_zone is not None else set(zones))

            # Create combinations of cloud, region, and zone.
            for zone in filtered_zones:
                resource_copy = controller_resources_to_use.copy(
                    cloud=registry.CLOUD_REGISTRY.from_str(cloud_name),
                    region=region,
                    zone=zone)
                result.add(resource_copy)

    if not result:
        return {controller_resources_to_use}
    return result


def _setup_proxy_command_on_controller(
        controller_launched_cloud: 'clouds.Cloud',
        user_config: Dict[str, Any]) -> config_utils.Config:
    """Sets up proxy command on the controller.

    This function should be called on the controller (remote cluster), which
    has the `~/.sky/sky_ray.yaml` file.
    """
    # Look up the contents of the already loaded configs via the
    # 'skypilot_config' module. Don't simply read the on-disk file as
    # it may have changed since this process started.
    #
    # Set any proxy command to None, because the controller would've
    # been launched behind the proxy, and in general any nodes we
    # launch may not have or need the proxy setup. (If the controller
    # needs to launch mew clusters in another region/VPC, the user
    # should properly set up VPC peering, which will allow the
    # cross-region/VPC communication. The proxy command is orthogonal
    # to this scenario.)
    #
    # This file will be uploaded to the controller node and will be
    # used throughout the managed job's / service's recovery attempts
    # (i.e., if it relaunches due to preemption, we make sure the
    # same config is used).
    #
    # NOTE: suppose that we have a controller in old VPC, then user
    # changes 'vpc_name' in the config and does a 'job launch' /
    # 'serve up'. In general, the old controller may not successfully
    # launch the job in the new VPC. This happens if the two VPCs don't
    # have peering set up. Like other places in the code, we assume
    # properly setting up networking is user's responsibilities.
    # TODO(zongheng): consider adding a basic check that checks
    # controller VPC (or name) == the managed job's / service's VPC
    # (or name). It may not be a sufficient check (as it's always
    # possible that peering is not set up), but it may catch some
    # obvious errors.
    config = config_utils.Config.from_dict(user_config)
    proxy_command_key = (str(controller_launched_cloud).lower(),
                         'ssh_proxy_command')
    ssh_proxy_command = skypilot_config.get_effective_region_config(
        cloud=str(controller_launched_cloud).lower(),
        region=None,
        keys=('ssh_proxy_command',),
        default_value=None)
    if isinstance(ssh_proxy_command, str):
        config.set_nested(proxy_command_key, None)
    elif isinstance(ssh_proxy_command, dict):
        # Instead of removing the key, we set the value to empty string
        # so that the controller will only try the regions specified by
        # the keys.
        ssh_proxy_command = {k: None for k in ssh_proxy_command}
        config.set_nested(proxy_command_key, ssh_proxy_command)

    return config


def replace_skypilot_config_path_in_file_mounts(
        cloud: 'clouds.Cloud', file_mounts: Optional[Dict[str, str]]):
    """Replaces the SkyPilot config path in file mounts with the real path."""
    # TODO(zhwu): This function can be moved to `backend_utils` once we have
    # more predefined file mounts that needs to be replaced after the cluster
    # is provisioned, e.g., we may need to decide which cloud to create a bucket
    # to be mounted to the cluster based on the cloud the cluster is actually
    # launched on (after failover).
    if file_mounts is None:
        return
    replaced = False
    for remote_path, local_path in list(file_mounts.items()):
        if local_path is None:
            del file_mounts[remote_path]
            continue
        if local_path.endswith(_LOCAL_SKYPILOT_CONFIG_PATH_SUFFIX):
            with tempfile.NamedTemporaryFile('w', delete=False) as f:
                user_config = yaml_utils.read_yaml(local_path)
                config = _setup_proxy_command_on_controller(cloud, user_config)
                yaml_utils.dump_yaml(f.name, dict(**config))
                file_mounts[remote_path] = f.name
                replaced = True
    if replaced:
        logger.debug(f'Replaced {_LOCAL_SKYPILOT_CONFIG_PATH_SUFFIX} '
                     f'with the real path in file mounts: {file_mounts}')


def _generate_run_uuid() -> str:
    """Generates a unique run id for the job."""
    return common_utils.base36_encode(uuid.uuid4().hex)[:8]


def translate_local_file_mounts_to_two_hop(
        task: 'task_lib.Task') -> Dict[str, str]:
    """Translates local->VM mounts into two-hop file mounts.

    This strategy will upload the local files to the controller first, using a
    normal rsync as part of sky.launch() for the controller. Then, when the
    controller launches the task, it will also use local file_mounts from the
    destination path of the first hop.

    Local machine/API server        Controller              Job cluster
    ------------------------  -----------------------  --------------------
    |      local path  ----|--|-> controller path --|--|-> job dst path   |
    ------------------------  -----------------------  --------------------

    Returns:
        A dict mapping from controller file mount path to local file mount path
          for the first hop. The task is updated in-place to do the second hop.
    """
    first_hop_file_mounts = {}
    second_hop_file_mounts = {}

    run_id = _generate_run_uuid()
    base_tmp_dir = os.path.join(constants.FILE_MOUNTS_CONTROLLER_TMP_BASE_PATH,
                                run_id)

    # Use a simple counter to create unique paths within the base_tmp_dir for
    # each mount.
    file_mount_id = 0

    file_mounts_to_translate = task.file_mounts or {}
    if task.workdir is not None and isinstance(task.workdir, str):
        file_mounts_to_translate[constants.SKY_REMOTE_WORKDIR] = task.workdir
        task.workdir = None

    for job_cluster_path, local_path in file_mounts_to_translate.items():
        if data_utils.is_cloud_store_url(
                local_path) or data_utils.is_cloud_store_url(job_cluster_path):
            raise exceptions.NotSupportedError(
                'Cloud-based file_mounts are specified, but no cloud storage '
                'is available. Please specify local file_mounts only.')

        controller_path = os.path.join(base_tmp_dir, f'{file_mount_id}')
        file_mount_id += 1
        first_hop_file_mounts[controller_path] = local_path
        second_hop_file_mounts[job_cluster_path] = controller_path

    # Use set_file_mounts to override existing file mounts, if they exist.
    task.set_file_mounts(second_hop_file_mounts)

    # Return the first hop info so that it can be added to the jobs-controller
    # YAML.
    return first_hop_file_mounts


# (maybe translate local file mounts) and (sync up)
def maybe_translate_local_file_mounts_and_sync_up(task: 'task_lib.Task',
                                                  task_type: str) -> None:
    """Translates local->VM mounts into Storage->VM, then syncs up any Storage.

    Eagerly syncing up local->Storage ensures Storage->VM would work at task
    launch time.

    If there are no local source paths to be translated, this function would
    still sync up any storage mounts with local source paths (which do not
    undergo translation).

    When jobs.bucket or serve.bucket is not specified, an intermediate storage
    dedicated for the job is created for the workdir and local file mounts and
    the storage is deleted when the job finishes. We don't share the storage
    between jobs, because jobs might have different resources requirements, and
    sharing storage between jobs may cause egress costs or slower transfer
    speeds.
    """

    # ================================================================
    # Translate the workdir and local file mounts to cloud file mounts.
    # ================================================================

    def _sub_path_join(sub_path: Optional[str], path: str) -> str:
        if sub_path is None:
            return path
        return os.path.join(sub_path, path).strip('/')

    # We use uuid to generate a unique run id for the job, so that the bucket/
    # subdirectory name is unique across different jobs/services.
    # We should not use common_utils.get_usage_run_id() here, because when
    # Python API is used, the run id will be the same across multiple
    # jobs.launch/serve.up calls after the sky is imported.
    run_id = _generate_run_uuid()
    user_hash = common_utils.get_user_hash()
    original_file_mounts = task.file_mounts if task.file_mounts else {}
    original_storage_mounts = task.storage_mounts if task.storage_mounts else {}

    copy_mounts = task.get_local_to_remote_file_mounts()
    if copy_mounts is None:
        copy_mounts = {}

    has_local_source_paths_file_mounts = bool(copy_mounts)
    has_local_source_paths_workdir = (task.workdir is not None and
                                      isinstance(task.workdir, str))

    msg = None
    if has_local_source_paths_workdir and has_local_source_paths_file_mounts:
        msg = 'workdir and file_mounts with local source paths'
    elif has_local_source_paths_file_mounts:
        msg = 'file_mounts with local source paths'
    elif has_local_source_paths_workdir:
        msg = 'workdir'
    if msg:
        logger.info(
            ux_utils.starting_message(f'Translating {msg} to '
                                      'SkyPilot Storage...'))
        rich_utils.force_update_status(
            ux_utils.spinner_message(
                f'Translating {msg} to SkyPilot Storage...'))

    # Get the bucket name for the workdir and file mounts,
    # we store all these files in same bucket from config.
    bucket_wth_prefix = skypilot_config.get_nested((task_type, 'bucket'), None)
    store_kwargs: Dict[str, Any] = {}
    if bucket_wth_prefix is None:
        store_type = sub_path = None
        storage_account_name = region = None
        bucket_name = constants.FILE_MOUNTS_BUCKET_NAME.format(
            username=common_utils.get_cleaned_username(),
            user_hash=user_hash,
            id=run_id)
    else:
        (store_type, bucket_name, sub_path, storage_account_name, region) = (
            storage_lib.StoreType.get_fields_from_store_url(bucket_wth_prefix))
        cloud_str = store_type.to_cloud()
        if (cloud_str not in storage_lib.
                get_cached_enabled_storage_cloud_names_or_refresh()):
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'`{task_type}.bucket` is specified in SkyPilot config '
                    f'with {bucket_wth_prefix}, but {cloud_str} is not '
                    'enabled. Please avoid specifying the bucket or enable the '
                    f'cloud by: sky check {cloud_str}')

        if storage_account_name is not None:
            store_kwargs['storage_account_name'] = storage_account_name
        if region is not None:
            store_kwargs['region'] = region

    # Step 1: Translate the workdir to SkyPilot storage.
    new_storage_mounts = {}
    if task.workdir is not None and isinstance(task.workdir, str):
        workdir = task.workdir
        task.workdir = None
        if (constants.SKY_REMOTE_WORKDIR in original_file_mounts or
                constants.SKY_REMOTE_WORKDIR in original_storage_mounts):
            raise ValueError(
                f'Cannot mount {constants.SKY_REMOTE_WORKDIR} as both the '
                'workdir and file_mounts contains it as the target.')
        bucket_sub_path = _sub_path_join(
            sub_path,
            constants.FILE_MOUNTS_WORKDIR_SUBPATH.format(run_id=run_id))
        stores = None
        if store_type is not None:
            stores = [store_type]

        storage_obj = storage_lib.Storage(
            name=bucket_name,
            source=workdir,
            persistent=False,
            mode=storage_lib.StorageMode.COPY,
            stores=stores,
            # Set `_is_sky_managed` to False when `bucket_with_prefix` is
            # specified, so that the storage is not deleted when job finishes,
            # but only the sub path is deleted.
            _is_sky_managed=bucket_wth_prefix is None,
            _bucket_sub_path=bucket_sub_path)
        new_storage_mounts[constants.SKY_REMOTE_WORKDIR] = storage_obj
        # Check of the existence of the workdir in file_mounts is done in
        # the task construction.
        logger.info(f'  {colorama.Style.DIM}Workdir: {workdir!r} '
                    f'-> storage: {bucket_name!r}.{colorama.Style.RESET_ALL}')

    # Step 2: Translate the local file mounts with folder in src to SkyPilot
    # storage.
    # TODO(zhwu): Optimize this by:
    # 1. Use the same bucket for all the mounts.
    # 2. When the src is the same, use the same bucket.
    copy_mounts_with_file_in_src = {}
    for i, (dst, src) in enumerate(copy_mounts.items()):
        assert task.file_mounts is not None
        task.file_mounts.pop(dst)
        if os.path.isfile(os.path.abspath(os.path.expanduser(src))):
            copy_mounts_with_file_in_src[dst] = src
            continue
        bucket_sub_path = _sub_path_join(
            sub_path, constants.FILE_MOUNTS_SUBPATH.format(i=i, run_id=run_id))
        stores = None
        if store_type is not None:
            stores = [store_type]
        storage_obj = storage_lib.Storage(name=bucket_name,
                                          source=src,
                                          persistent=False,
                                          mode=storage_lib.StorageMode.COPY,
                                          stores=stores,
                                          _is_sky_managed=not bucket_wth_prefix,
                                          _bucket_sub_path=bucket_sub_path)
        new_storage_mounts[dst] = storage_obj
        logger.info(f'  {colorama.Style.DIM}Folder : {src!r} '
                    f'-> storage: {bucket_name!r}.{colorama.Style.RESET_ALL}')

    # Step 3: Translate local file mounts with file in src to SkyPilot storage.
    # Hard link the files in src to a temporary directory, and upload folder.
    file_mounts_tmp_subpath = _sub_path_join(
        sub_path, constants.FILE_MOUNTS_TMP_SUBPATH.format(run_id=run_id))
    base_tmp_dir = os.path.expanduser(constants.FILE_MOUNTS_LOCAL_TMP_BASE_PATH)
    os.makedirs(base_tmp_dir, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base_tmp_dir) as temp_path:
        local_fm_path = os.path.join(
            temp_path, constants.FILE_MOUNTS_LOCAL_TMP_DIR.format(id=run_id))
        os.makedirs(local_fm_path, exist_ok=True)
        file_mount_remote_tmp_dir = constants.FILE_MOUNTS_REMOTE_TMP_DIR.format(
            task_type)
        if copy_mounts_with_file_in_src:
            src_to_file_id = {}
            for i, src in enumerate(set(copy_mounts_with_file_in_src.values())):
                src_to_file_id[src] = i
                os.link(os.path.abspath(os.path.expanduser(src)),
                        os.path.join(local_fm_path, f'file-{i}'))
            stores = None
            if store_type is not None:
                stores = [store_type]
            storage_obj = storage_lib.Storage(
                name=bucket_name,
                source=local_fm_path,
                persistent=False,
                mode=storage_lib.DEFAULT_STORAGE_MODE,
                stores=stores,
                _is_sky_managed=not bucket_wth_prefix,
                _bucket_sub_path=file_mounts_tmp_subpath)

            new_storage_mounts[file_mount_remote_tmp_dir] = storage_obj
            if file_mount_remote_tmp_dir in original_storage_mounts:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        'Failed to translate file mounts, due to the default '
                        f'destination {file_mount_remote_tmp_dir} '
                        'being taken.')
            sources = list(src_to_file_id.keys())
            sources_str = '\n    '.join(sources)
            logger.info(f'  {colorama.Style.DIM}Files (listed below) '
                        f' -> storage: {bucket_name}:'
                        f'\n    {sources_str}{colorama.Style.RESET_ALL}')

        rich_utils.force_update_status(
            ux_utils.spinner_message(
                'Uploading translated local files/folders'))
        task.update_storage_mounts(new_storage_mounts)

        # Step 4: Upload storage from sources
        # Upload the local source to a bucket. The task will not be executed
        # locally, so we need to upload the files/folders to the bucket manually
        # here before sending the task to the remote jobs controller.  This will
        # also upload any storage mounts that are not translated.  After
        # sync_storage_mounts, we will also have file_mounts in the task, but
        # these aren't used since the storage_mounts for the same paths take
        # precedence.
        if task.storage_mounts:
            # There may be existing (non-translated) storage mounts, so log this
            # whenever task.storage_mounts is non-empty.
            rich_utils.force_update_status(
                ux_utils.spinner_message(
                    'Uploading local sources to storage[/]  '
                    '[dim]View storages: sky storage ls'))
        try:
            task.sync_storage_mounts()
        except (ValueError, exceptions.NoCloudAccessError) as e:
            if 'No enabled cloud for storage' in str(e) or isinstance(
                    e, exceptions.NoCloudAccessError):
                data_src = None
                if has_local_source_paths_file_mounts:
                    data_src = 'file_mounts'
                if has_local_source_paths_workdir:
                    if data_src:
                        data_src += ' and workdir'
                    else:
                        data_src = 'workdir'
                store_enabled_clouds = ', '.join(
                    storage_lib.STORE_ENABLED_CLOUDS)
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.NotSupportedError(
                        f'Unable to use {data_src} - no cloud with object '
                        'store support is enabled. Please enable at least one '
                        'cloud with object store support '
                        f'({store_enabled_clouds}) by running `sky check`, or '
                        f'remove {data_src} from your task.'
                        '\nHint: If you do not have any cloud access, you may '
                        'still download data and code over the network using '
                        'curl or other tools in the `setup` section of the '
                        'task.') from None

    # Step 5: Add the file download into the file mounts, such as
    #  /original-dst: s3://spot-fm-file-only-bucket-name/file-0
    new_file_mounts = {}
    if copy_mounts_with_file_in_src:
        # file_mount_remote_tmp_dir will only exist when there are files in
        # the src for copy mounts.
        storage_obj = task.storage_mounts[file_mount_remote_tmp_dir]
        assert storage_obj.stores, (storage_obj.__dict__, task.to_yaml_config())
        curr_store_type = list(storage_obj.stores.keys())[0]
        store_object = storage_obj.stores[curr_store_type]
        assert store_object is not None, (storage_obj.__dict__,
                                          task.to_yaml_config())
        bucket_url = storage_lib.StoreType.get_endpoint_url(
            store_object, bucket_name)
        bucket_url += f'/{file_mounts_tmp_subpath}'
        for dst, src in copy_mounts_with_file_in_src.items():
            file_id = src_to_file_id[src]
            new_file_mounts[dst] = bucket_url + f'/file-{file_id}'
    task.update_file_mounts(new_file_mounts)

    # Step 6: Replace the source field that is local path in all storage_mounts
    # with bucket URI and remove the name field.
    for storage_obj in task.storage_mounts.values():
        if (storage_obj.source is not None and
                not data_utils.is_cloud_store_url(storage_obj.source)):
            # Need to replace the local path with bucket URI, and remove the
            # name field, so that the storage mount can work on the jobs
            # controller.
            store_types = list(storage_obj.stores.keys())
            assert len(store_types) == 1, (
                'We only support one store type for now.', storage_obj.stores)
            curr_store_type = store_types[0]
            store_object = storage_obj.stores[curr_store_type]
            assert store_object is not None and storage_obj.name is not None, (
                store_object, storage_obj.name)
            storage_obj.source = storage_lib.StoreType.get_endpoint_url(
                store_object, storage_obj.name)
            storage_obj.force_delete = True

    # Step 7: Convert all `MOUNT` mode storages which don't specify a source
    # to specifying a source. If the source is specified with a local path,
    # it was handled in step 6.
    updated_mount_storages = {}
    for storage_path, storage_obj in task.storage_mounts.items():
        if (storage_obj.mode in storage_lib.MOUNTABLE_STORAGE_MODES and
                not storage_obj.source):
            # Construct source URL with first store type and storage name
            # E.g., s3://my-storage-name
            store_types = list(storage_obj.stores.keys())
            assert len(store_types) == 1, (
                'We only support one store type for now.', storage_obj.stores)
            curr_store_type = store_types[0]
            store_object = storage_obj.stores[curr_store_type]
            assert store_object is not None and storage_obj.name is not None, (
                store_object, storage_obj.name)
            source = storage_lib.StoreType.get_endpoint_url(
                store_object, storage_obj.name)
            assert store_object is not None and storage_obj.name is not None, (
                store_object, storage_obj.name)
            new_storage = storage_lib.Storage.from_yaml_config({
                'source': source,
                'persistent': storage_obj.persistent,
                'mode': storage_obj.mode.value,
                # We enable force delete to allow the controller to delete
                # the object store in case persistent is set to False.
                '_force_delete': True
            })
            updated_mount_storages[storage_path] = new_storage
    task.update_storage_mounts(updated_mount_storages)
    if msg:
        logger.info(ux_utils.finishing_message('Uploaded local files/folders.'))


# ======================= Resources Management Functions =======================

# Based on testing, assume a running job process uses 350MB memory. We use the
# same estimation for service controller process.
JOB_MEMORY_MB = 350
# Monitoring process for service is 1GB. This is based on an old estimation but
# we keep it here for now.
# TODO(tian): Remeasure this.
SERVE_MONITORING_MEMORY_MB = 1024
# The ratio of service controller process to job process. We will treat each
# service as SERVE_PROC_RATIO job processes.
SERVE_PROC_RATIO = SERVE_MONITORING_MEMORY_MB / JOB_MEMORY_MB
# Past 2000 simultaneous jobs, we become unstable.
# See https://github.com/skypilot-org/skypilot/issues/4649.
MAX_JOB_LIMIT = 2000
# Number of ongoing launches launches allowed per CPU, for managed jobs.
JOB_LAUNCHES_PER_CPU = 4
# Number of ongoing launches launches allowed per CPU, for services. This is
# also based on an old estimation, but SKyServe indeed spawn a new process
# for each launch operation, so it should be slightly more resources demanding
# than managed jobs.
SERVE_LAUNCHES_PER_CPU = 2
# The ratio of service launch to job launch. This is inverted as the parallelism
# is determined by 1 / LAUNCHES_PER_CPU.
SERVE_LAUNCH_RATIO = JOB_LAUNCHES_PER_CPU / SERVE_LAUNCHES_PER_CPU

# The _RESOURCES_LOCK should be held whenever we are checking the parallelism
# control or updating the schedule_state of any job or service. Any code that
# takes this lock must conclude by calling maybe_schedule_next_jobs.
_RESOURCES_LOCK = '~/.sky/locks/controller_resources.lock'


@annotations.lru_cache(scope='global', maxsize=1)
def get_resources_lock_path() -> str:
    path = os.path.expanduser(_RESOURCES_LOCK)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


@annotations.lru_cache(scope='request')
def _get_job_parallelism() -> int:
    job_memory = JOB_MEMORY_MB * 1024 * 1024
    job_limit = min(psutil.virtual_memory().total // job_memory, MAX_JOB_LIMIT)
    return max(job_limit, 1)


@annotations.lru_cache(scope='request')
def _get_launch_parallelism() -> int:
    cpus = os.cpu_count()
    return cpus * JOB_LAUNCHES_PER_CPU if cpus is not None else 1


def can_provision() -> bool:
    # We always prioritize terminating over provisioning, to save the cost on
    # idle resources.
    if serve_state.total_number_scheduled_to_terminate_replicas() > 0:
        return False
    return can_terminate()


def can_start_new_process() -> bool:
    num_procs = (serve_state.get_num_services() * SERVE_PROC_RATIO +
                 managed_job_state.get_num_alive_jobs())
    return num_procs < _get_job_parallelism()


# We limit the number of terminating replicas to the number of CPUs. This is
# just a temporary solution to avoid overwhelming the controller. After one job
# controller PR, we should use API server to handle resources management.
def can_terminate() -> bool:
    num_terminating = (
        serve_state.total_number_provisioning_replicas() * SERVE_LAUNCH_RATIO +
        # Each terminate process will take roughly the same CPUs as job launch.
        serve_state.total_number_terminating_replicas() +
        managed_job_state.get_num_launching_jobs())
    return num_terminating < _get_launch_parallelism()
