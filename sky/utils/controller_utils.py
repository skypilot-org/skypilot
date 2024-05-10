"""Util constants/functions for SkyPilot Controllers."""
import copy
import dataclasses
import enum
import getpass
import os
import tempfile
import typing
from typing import Any, Dict, Iterable, List, Optional, Set

import colorama

from sky import check as sky_check
from sky import clouds
from sky import exceptions
from sky import global_user_state
from sky import resources
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import cloudflare
from sky.clouds import gcp
from sky.data import data_utils
from sky.data import storage as storage_lib
from sky.jobs import constants as managed_job_constants
from sky.jobs import utils as managed_job_utils
from sky.serve import constants as serve_constants
from sky.serve import serve_utils
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import env_options
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky import task as task_lib
    from sky.backends import cloud_vm_ray_backend

logger = sky_logging.init_logger(__name__)

# Message thrown when APIs sky.jobs.launch(), sky.serve.up() received an invalid
# controller resources spec.
CONTROLLER_RESOURCES_NOT_VALID_MESSAGE = (
    '{controller_type} controller resources is not valid, please check '
    '~/.sky/config.yaml file and make sure '
    '{controller_type}.controller.resources is a valid resources spec. '
    'Details:\n  {err}')

# The placeholder for the local skypilot config path in file mounts.
LOCAL_SKYPILOT_CONFIG_PATH_PLACEHOLDER = 'skypilot:local_skypilot_config_path'


@dataclasses.dataclass
class _ControllerSpec:
    """Spec for skypilot controllers."""
    controller_type: str
    name: str
    # Use a list of strings to support fallback to old names. The list is in the
    # fallback order.
    candidate_cluster_names: List[str]
    in_progress_hint: str
    decline_cancel_hint: str
    _decline_down_when_failed_to_fetch_status_hint: str
    decline_down_for_dirty_controller_hint: str
    _check_cluster_name_hint: str
    default_hint_if_non_existent: str
    connection_error_hint: str
    default_resources_config: Dict[str, Any]

    @property
    def cluster_name(self) -> str:
        """The name in candidate_cluster_names that exists, else the first."""
        for candidate_name in self.candidate_cluster_names:
            record = global_user_state.get_cluster_from_name(candidate_name)
            if record is not None:
                return candidate_name
        return self.candidate_cluster_names[0]

    @property
    def decline_down_when_failed_to_fetch_status_hint(self) -> str:
        return self._decline_down_when_failed_to_fetch_status_hint.format(
            cluster_name=self.cluster_name)

    @property
    def check_cluster_name_hint(self) -> str:
        return self._check_cluster_name_hint.format(
            cluster_name=self.cluster_name)


class Controllers(enum.Enum):
    """Skypilot controllers."""
    # NOTE(dev): Keep this align with
    # sky/cli.py::_CONTROLLER_TO_HINT_OR_RAISE
    JOBS_CONTROLLER = _ControllerSpec(
        controller_type='jobs',
        name='managed jobs controller',
        candidate_cluster_names=[
            managed_job_utils.JOB_CONTROLLER_NAME,
            managed_job_utils.LEGACY_JOB_CONTROLLER_NAME
        ],
        in_progress_hint=(
            '* {job_info}To see all managed jobs: '
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
        default_resources_config=managed_job_constants.CONTROLLER_RESOURCES)
    SKY_SERVE_CONTROLLER = _ControllerSpec(
        controller_type='serve',
        name='serve controller',
        candidate_cluster_names=[serve_utils.SKY_SERVE_CONTROLLER_NAME],
        in_progress_hint=(
            f'* To see detailed service status: {colorama.Style.BRIGHT}'
            f'sky serve status -a{colorama.Style.RESET_ALL}'),
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
        default_resources_config=serve_constants.CONTROLLER_RESOURCES)

    @classmethod
    def from_name(cls, name: Optional[str]) -> Optional['Controllers']:
        """Check if the cluster name is a controller name.

        Returns:
            The controller if the cluster name is a controller name.
            Otherwise, returns None.
        """
        for controller in cls:
            if name in controller.value.candidate_cluster_names:
                return controller
        return None

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


# Install cli dependencies. Not using SkyPilot wheels because the wheel
# can be cleaned up by another process.
# TODO(zhwu): Keep the dependencies align with the ones in setup.py
def _get_cloud_dependencies_installation_commands(
        controller: Controllers) -> List[str]:
    # TODO(tian): Make dependency installation command a method of cloud
    # class and get all installation command for enabled clouds.
    commands = []
    prefix_str = 'Check & install cloud dependencies on controller: '
    # This is to make sure the shorter checking message does not have junk
    # characters from the previous message.
    empty_str = ' ' * 5
    aws_dependencies_installation = (
        'pip list | grep boto3 > /dev/null 2>&1 || pip install '
        'botocore>=1.29.10 boto3>=1.26.1; '
        # Need to separate the installation of awscli from above because some
        # other clouds will install boto3 but not awscli.
        'pip list | grep awscli> /dev/null 2>&1 || pip install "urllib3<2" '
        'awscli>=1.27.10 "colorama<0.4.5" > /dev/null 2>&1')
    for cloud in sky_check.get_cached_enabled_clouds_or_refresh():
        if isinstance(
                clouds,
            (clouds.Lambda, clouds.SCP, clouds.Fluidstack, clouds.Paperspace)):
            # no need to install any cloud dependencies for lambda, scp,
            # fluidstack and paperspace
            continue
        if isinstance(cloud, clouds.AWS):
            commands.append(f'echo -n "{prefix_str}AWS{empty_str}" && ' +
                            aws_dependencies_installation)
        elif isinstance(cloud, clouds.Azure):
            commands.append(
                f'echo -en "\\r{prefix_str}Azure{empty_str}" && '
                'pip list | grep azure-cli > /dev/null 2>&1 || '
                'pip install "azure-cli>=2.31.0" azure-core '
                '"azure-identity>=1.13.0" azure-mgmt-network > /dev/null 2>&1')
        elif isinstance(cloud, clouds.GCP):
            commands.append(
                f'echo -en "\\r{prefix_str}GCP{empty_str}" && '
                'pip list | grep google-api-python-client > /dev/null 2>&1 || '
                'pip install "google-api-python-client>=2.69.0" '
                '> /dev/null 2>&1')
            # Have to separate the installation of google-cloud-storage from
            # above because for a VM launched on GCP, the VM may have
            # google-api-python-client installed alone.
            commands.append(
                'pip list | grep google-cloud-storage > /dev/null 2>&1 || '
                'pip install google-cloud-storage > /dev/null 2>&1')
            commands.append(f'{gcp.GOOGLE_SDK_INSTALLATION_COMMAND}')
        elif isinstance(cloud, clouds.Kubernetes):
            commands.append(
                f'echo -en "\\r{prefix_str}Kubernetes{empty_str}" && '
                'pip list | grep kubernetes > /dev/null 2>&1 || '
                'pip install "kubernetes>=20.0.0" > /dev/null 2>&1 &&'
                # Install k8s + skypilot dependencies
                'sudo bash -c "if '
                '! command -v curl &> /dev/null || '
                '! command -v socat &> /dev/null || '
                '! command -v netcat &> /dev/null; '
                'then apt update && apt install curl socat netcat -y; '
                'fi" && '
                # Install kubectl
                '(command -v kubectl &>/dev/null || '
                '(curl -s -LO "https://dl.k8s.io/release/'
                '$(curl -L -s https://dl.k8s.io/release/stable.txt)'
                '/bin/linux/amd64/kubectl" && '
                'sudo install -o root -g root -m 0755 '
                'kubectl /usr/local/bin/kubectl))')
        if controller == Controllers.JOBS_CONTROLLER:
            if isinstance(cloud, clouds.IBM):
                commands.append(
                    f'echo -en "\\r{prefix_str}IBM{empty_str}" '
                    '&& pip list | grep ibm-cloud-sdk-core > /dev/null 2>&1 || '
                    'pip install ibm-cloud-sdk-core ibm-vpc '
                    'ibm-platform-services ibm-cos-sdk > /dev/null 2>&1')
            elif isinstance(cloud, clouds.OCI):
                commands.append(f'echo -en "\\r{prefix_str}OCI{empty_str}" && '
                                'pip list | grep oci > /dev/null 2>&1 || '
                                'pip install oci > /dev/null 2>&1')
            elif isinstance(cloud, clouds.RunPod):
                commands.append(
                    f'echo -en "\\r{prefix_str}RunPod{empty_str}" && '
                    'pip list | grep runpod > /dev/null 2>&1 || '
                    'pip install "runpod>=1.5.1" > /dev/null 2>&1')
            elif isinstance(cloud, clouds.Cudo):
                # cudo doesn't support open port
                commands.append(
                    f'echo -en "\\r{prefix_str}Cudo{empty_str}" && '
                    'pip list | grep cudo-compute > /dev/null 2>&1 || '
                    'pip install "cudo-compute>=0.1.8" > /dev/null 2>&1')
    if (cloudflare.NAME
            in storage_lib.get_cached_enabled_storage_clouds_or_refresh()):
        commands.append(f'echo -en "\\r{prefix_str}Cloudflare{empty_str}" && ' +
                        aws_dependencies_installation)
    commands.append(f'echo -e "\\r{prefix_str}Done for {len(commands)} '
                    'clouds."')
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
def download_and_stream_latest_job_log(
        backend: 'cloud_vm_ray_backend.CloudVmRayBackend',
        handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle',
        local_dir: str) -> Optional[str]:
    """Downloads and streams the latest job log.

    This function is only used by jobs controller and sky serve controller.
    """
    os.makedirs(local_dir, exist_ok=True)
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
            job_ids=None,
            local_dir=local_dir)
    except exceptions.CommandError as e:
        logger.info(f'Failed to download the logs: '
                    f'{common_utils.format_exception(e)}')
    else:
        if not log_dirs:
            logger.error('Failed to find the logs for the user program.')
        else:
            log_dir = list(log_dirs.values())[0]
            log_file = os.path.join(log_dir, 'run.log')

            # Print the logs to the console.
            try:
                with open(log_file, 'r', encoding='utf-8') as f:
                    print(f.read())
            except FileNotFoundError:
                logger.error('Failed to find the logs for the user '
                             f'program at {log_file}.')
    return log_file


def shared_controller_vars_to_fill(
        controller: Controllers,
        remote_user_config_path: str) -> Dict[str, str]:
    vars_to_fill: Dict[str, Any] = {
        'cloud_dependencies_installation_commands':
            _get_cloud_dependencies_installation_commands(controller)
    }
    env_vars: Dict[str, str] = {
        env.value: '1' for env in env_options.Options if env.get()
    }
    env_vars.update({
        # Should not use $USER here, as that env var can be empty when
        # running in a container.
        constants.USER_ENV_VAR: getpass.getuser(),
        constants.USER_ID_ENV_VAR: common_utils.get_user_hash(),
        # Skip cloud identity check to avoid the overhead.
        env_options.Options.SKIP_CLOUD_IDENTITY_CHECK.value: '1',
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
        elif controller == Controllers.JOBS_CONTROLLER:
            controller_resources_config_copied.update(
                skypilot_config.get_nested(('spot', 'controller', 'resources'),
                                           {}))

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
            controller_resources_to_use = handle.launched_resources

    if controller_resources_to_use.cloud is not None:
        return {controller_resources_to_use}

    # If the controller and replicas are from the same cloud, it should
    # provide better connectivity. We will let the controller choose from
    # the clouds of the resources if the controller does not exist.
    # TODO(tian): Consider respecting the regions/zones specified for the
    # resources as well.
    requested_clouds: Set['clouds.Cloud'] = set()
    for resource in task_resources:
        # cloud is an object and will not be able to be distinguished by set.
        # Here we manually check if the cloud is in the set.
        if resource.cloud is not None:
            if not clouds.cloud_in_iterable(resource.cloud, requested_clouds):
                try:
                    resource.cloud.check_features_are_supported(
                        resources.Resources(),
                        {clouds.CloudImplementationFeatures.HOST_CONTROLLERS})
                except exceptions.NotSupportedError:
                    # Skip the cloud if it does not support hosting controllers.
                    continue
                requested_clouds.add(resource.cloud)
        else:
            # if one of the resource.cloud is None, this could represent user
            # does not know which cloud is best for the specified resources.
            # For example:
            #   resources:
            #     - accelerators: L4     # Both available on AWS and GCP
            #     - cloud: runpod
            #       accelerators: A40
            # In this case, we allow the controller to be launched on any cloud.
            requested_clouds.clear()
            break
    if not requested_clouds:
        return {controller_resources_to_use}
    return {
        controller_resources_to_use.copy(cloud=controller_cloud)
        for controller_cloud in requested_clouds
    }


def _setup_proxy_command_on_controller(
        controller_launched_cloud: 'clouds.Cloud') -> Dict[str, Any]:
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
    # launch the job in the new VPC. This happens if the two VPCs don’t
    # have peering set up. Like other places in the code, we assume
    # properly setting up networking is user's responsibilities.
    # TODO(zongheng): consider adding a basic check that checks
    # controller VPC (or name) == the managed job's / service's VPC
    # (or name). It may not be a sufficient check (as it's always
    # possible that peering is not set up), but it may catch some
    # obvious errors.
    proxy_command_key = (str(controller_launched_cloud).lower(),
                         'ssh_proxy_command')
    ssh_proxy_command = skypilot_config.get_nested(proxy_command_key, None)
    config_dict = skypilot_config.to_dict()
    if isinstance(ssh_proxy_command, str):
        config_dict = skypilot_config.set_nested(proxy_command_key, None)
    elif isinstance(ssh_proxy_command, dict):
        # Instead of removing the key, we set the value to empty string
        # so that the controller will only try the regions specified by
        # the keys.
        ssh_proxy_command = {k: None for k in ssh_proxy_command}
        config_dict = skypilot_config.set_nested(proxy_command_key,
                                                 ssh_proxy_command)

    return config_dict


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
    to_replace = True
    with tempfile.NamedTemporaryFile('w', delete=False) as f:
        if skypilot_config.loaded():
            new_skypilot_config = _setup_proxy_command_on_controller(cloud)
            common_utils.dump_yaml(f.name, new_skypilot_config)
            to_replace = True
        else:
            # Empty config. Remove the placeholder below.
            to_replace = False
        for remote_path, local_path in list(file_mounts.items()):
            if local_path == LOCAL_SKYPILOT_CONFIG_PATH_PLACEHOLDER:
                if to_replace:
                    file_mounts[remote_path] = f.name
                    replaced = True
                else:
                    del file_mounts[remote_path]
    if replaced:
        logger.debug(f'Replaced {LOCAL_SKYPILOT_CONFIG_PATH_PLACEHOLDER} with '
                     f'the real path in file mounts: {file_mounts}')


def maybe_translate_local_file_mounts_and_sync_up(task: 'task_lib.Task',
                                                  path: str) -> None:
    """Translates local->VM mounts into Storage->VM, then syncs up any Storage.

    Eagerly syncing up local->Storage ensures Storage->VM would work at task
    launch time.

    If there are no local source paths to be translated, this function would
    still sync up any storage mounts with local source paths (which do not
    undergo translation).
    """
    # ================================================================
    # Translate the workdir and local file mounts to cloud file mounts.
    # ================================================================
    run_id = common_utils.get_usage_run_id()[:8]
    original_file_mounts = task.file_mounts if task.file_mounts else {}
    original_storage_mounts = task.storage_mounts if task.storage_mounts else {}

    copy_mounts = task.get_local_to_remote_file_mounts()
    if copy_mounts is None:
        copy_mounts = {}

    has_local_source_paths_file_mounts = bool(copy_mounts)
    has_local_source_paths_workdir = task.workdir is not None

    msg = None
    if has_local_source_paths_workdir and has_local_source_paths_file_mounts:
        msg = 'workdir and file_mounts with local source paths'
    elif has_local_source_paths_file_mounts:
        msg = 'file_mounts with local source paths'
    elif has_local_source_paths_workdir:
        msg = 'workdir'
    if msg:
        logger.info(f'{colorama.Fore.YELLOW}Translating {msg} to SkyPilot '
                    f'Storage...{colorama.Style.RESET_ALL}')

    # Step 1: Translate the workdir to SkyPilot storage.
    new_storage_mounts = {}
    if task.workdir is not None:
        bucket_name = constants.WORKDIR_BUCKET_NAME.format(
            username=common_utils.get_cleaned_username(), id=run_id)
        workdir = task.workdir
        task.workdir = None
        if (constants.SKY_REMOTE_WORKDIR in original_file_mounts or
                constants.SKY_REMOTE_WORKDIR in original_storage_mounts):
            raise ValueError(
                f'Cannot mount {constants.SKY_REMOTE_WORKDIR} as both the '
                'workdir and file_mounts contains it as the target.')
        new_storage_mounts[
            constants.
            SKY_REMOTE_WORKDIR] = storage_lib.Storage.from_yaml_config({
                'name': bucket_name,
                'source': workdir,
                'persistent': False,
                'mode': 'COPY',
            })
        # Check of the existence of the workdir in file_mounts is done in
        # the task construction.
        logger.info(f'Workdir {workdir!r} will be synced to cloud storage '
                    f'{bucket_name!r}.')

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
        bucket_name = constants.FILE_MOUNTS_BUCKET_NAME.format(
            username=common_utils.get_cleaned_username(),
            id=f'{run_id}-{i}',
        )
        new_storage_mounts[dst] = storage_lib.Storage.from_yaml_config({
            'name': bucket_name,
            'source': src,
            'persistent': False,
            'mode': 'COPY',
        })
        logger.info(
            f'Folder in local file mount {src!r} will be synced to SkyPilot '
            f'storage {bucket_name}.')

    # Step 3: Translate local file mounts with file in src to SkyPilot storage.
    # Hard link the files in src to a temporary directory, and upload folder.
    local_fm_path = os.path.join(
        tempfile.gettempdir(),
        constants.FILE_MOUNTS_LOCAL_TMP_DIR.format(id=run_id))
    os.makedirs(local_fm_path, exist_ok=True)
    file_bucket_name = constants.FILE_MOUNTS_FILE_ONLY_BUCKET_NAME.format(
        username=common_utils.get_cleaned_username(), id=run_id)
    file_mount_remote_tmp_dir = constants.FILE_MOUNTS_REMOTE_TMP_DIR.format(
        path)
    if copy_mounts_with_file_in_src:
        src_to_file_id = {}
        for i, src in enumerate(set(copy_mounts_with_file_in_src.values())):
            src_to_file_id[src] = i
            os.link(os.path.abspath(os.path.expanduser(src)),
                    os.path.join(local_fm_path, f'file-{i}'))

        new_storage_mounts[
            file_mount_remote_tmp_dir] = storage_lib.Storage.from_yaml_config({
                'name': file_bucket_name,
                'source': local_fm_path,
                'persistent': False,
                'mode': 'MOUNT',
            })
        if file_mount_remote_tmp_dir in original_storage_mounts:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'Failed to translate file mounts, due to the default '
                    f'destination {file_mount_remote_tmp_dir} '
                    'being taken.')
        sources = list(src_to_file_id.keys())
        sources_str = '\n\t'.join(sources)
        logger.info('Source files in file_mounts will be synced to '
                    f'cloud storage {file_bucket_name}:'
                    f'\n\t{sources_str}')
    task.update_storage_mounts(new_storage_mounts)

    # Step 4: Upload storage from sources
    # Upload the local source to a bucket. The task will not be executed
    # locally, so we need to upload the files/folders to the bucket manually
    # here before sending the task to the remote jobs controller.
    if task.storage_mounts:
        # There may be existing (non-translated) storage mounts, so log this
        # whenever task.storage_mounts is non-empty.
        logger.info(f'{colorama.Fore.YELLOW}Uploading sources to cloud storage.'
                    f'{colorama.Style.RESET_ALL} See: sky storage ls')
    try:
        task.sync_storage_mounts()
    except ValueError as e:
        if 'No enabled cloud for storage' in str(e):
            data_src = None
            if has_local_source_paths_file_mounts:
                data_src = 'file_mounts'
            if has_local_source_paths_workdir:
                if data_src:
                    data_src += ' and workdir'
                else:
                    data_src = 'workdir'
            store_enabled_clouds = ', '.join(storage_lib.STORE_ENABLED_CLOUDS)
            with ux_utils.print_exception_no_traceback():
                raise exceptions.NotSupportedError(
                    f'Unable to use {data_src} - no cloud with object store '
                    'is enabled. Please enable at least one cloud with '
                    f'object store support ({store_enabled_clouds}) by running '
                    f'`sky check`, or remove {data_src} from your task.'
                    '\nHint: If you do not have any cloud access, you may still'
                    ' download data and code over the network using curl or '
                    'other tools in the `setup` section of the task.') from None

    # Step 5: Add the file download into the file mounts, such as
    #  /original-dst: s3://spot-fm-file-only-bucket-name/file-0
    new_file_mounts = {}
    if copy_mounts_with_file_in_src:
        # file_mount_remote_tmp_dir will only exist when there are files in
        # the src for copy mounts.
        storage = task.storage_mounts[file_mount_remote_tmp_dir]
        store_type = list(storage.stores.keys())[0]
        store_prefix = store_type.store_prefix()
        bucket_url = store_prefix + file_bucket_name
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
            # name field, so that the storage mount can work on the spot
            # controller.
            store_types = list(storage_obj.stores.keys())
            assert len(store_types) == 1, (
                'We only support one store type for now.', storage_obj.stores)
            store_type = store_types[0]
            store_prefix = store_type.store_prefix()
            storage_obj.source = f'{store_prefix}{storage_obj.name}'
            storage_obj.force_delete = True
