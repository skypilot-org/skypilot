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
from sky.utils import rich_utils
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
    # We use <step>/<total> instead of strong formatting, as we need to update
    # the <total> at the end of the for loop, and python does not support
    # partial string formatting.
    prefix_str = ('[<step>/<total>] Check & install cloud dependencies '
                  'on controller: ')
    # This is to make sure the shorter checking message does not have junk
    # characters from the previous message.
    empty_str = ' ' * 10
    aws_dependencies_installation = (
        'pip list | grep boto3 > /dev/null 2>&1 || pip install '
        'botocore>=1.29.10 boto3>=1.26.1; '
        # Need to separate the installation of awscli from above because some
        # other clouds will install boto3 but not awscli.
        'pip list | grep awscli> /dev/null 2>&1 || pip install "urllib3<2" '
        'awscli>=1.27.10 "colorama<0.4.5" > /dev/null 2>&1')
    setup_clouds: List[str] = []
    for cloud in sky_check.get_cached_enabled_clouds_or_refresh():
        if isinstance(
                clouds,
            (clouds.Lambda, clouds.SCP, clouds.Fluidstack, clouds.Paperspace)):
            # no need to install any cloud dependencies for lambda, scp,
            # fluidstack and paperspace
            continue
        if isinstance(cloud, clouds.AWS):
            step_prefix = prefix_str.replace('<step>',
                                             str(len(setup_clouds) + 1))
            commands.append(f'echo -en "\\r{step_prefix}AWS{empty_str}" && ' +
                            aws_dependencies_installation)
            setup_clouds.append(str(cloud))
        elif isinstance(cloud, clouds.Azure):
            step_prefix = prefix_str.replace('<step>',
                                             str(len(setup_clouds) + 1))
            commands.append(
                f'echo -en "\\r{step_prefix}Azure{empty_str}" && '
                'pip list | grep azure-cli > /dev/null 2>&1 || '
                'pip install "azure-cli>=2.31.0" azure-core '
                '"azure-identity>=1.13.0" azure-mgmt-network > /dev/null 2>&1')
            # Have to separate this installation of az blob storage from above
            # because this is newly-introduced and not part of azure-cli. We
            # need a separate installed check for this.
            commands.append(
                'pip list | grep azure-storage-blob > /dev/null 2>&1 || '
                'pip install azure-storage-blob msgraph-sdk > /dev/null 2>&1')
            setup_clouds.append(str(cloud))
        elif isinstance(cloud, clouds.GCP):
            step_prefix = prefix_str.replace('<step>',
                                             str(len(setup_clouds) + 1))
            commands.append(
                f'echo -en "\\r{step_prefix}GCP{empty_str}" && '
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
            setup_clouds.append(str(cloud))
        elif isinstance(cloud, clouds.Kubernetes):
            step_prefix = prefix_str.replace('<step>',
                                             str(len(setup_clouds) + 1))
            commands.append(
                f'echo -en "\\r{step_prefix}Kubernetes{empty_str}" && '
                'pip list | grep kubernetes > /dev/null 2>&1 || '
                'pip install "kubernetes>=20.0.0" > /dev/null 2>&1 &&'
                # Install k8s + skypilot dependencies
                'sudo bash -c "if '
                '! command -v curl &> /dev/null || '
                '! command -v socat &> /dev/null || '
                '! command -v netcat &> /dev/null; '
                'then apt update &> /dev/null && '
                'apt install curl socat netcat -y &> /dev/null; '
                'fi" && '
                # Install kubectl
                '(command -v kubectl &>/dev/null || '
                '(curl -s -LO "https://dl.k8s.io/release/'
                '$(curl -L -s https://dl.k8s.io/release/stable.txt)'
                '/bin/linux/amd64/kubectl" && '
                'sudo install -o root -g root -m 0755 '
                'kubectl /usr/local/bin/kubectl))')
            setup_clouds.append(str(cloud))
        elif isinstance(cloud, clouds.Cudo):
            step_prefix = prefix_str.replace('<step>',
                                             str(len(setup_clouds) + 1))
            commands.append(
                f'echo -en "\\r{step_prefix}Cudo{empty_str}" && '
                'pip list | grep cudo-compute > /dev/null 2>&1 || '
                'pip install "cudo-compute>=0.1.10" > /dev/null 2>&1 && '
                'wget https://download.cudo.org/compute/cudoctl-0.3.2-amd64.deb -O ~/cudoctl.deb > /dev/null 2>&1 && '  # pylint: disable=line-too-long
                'sudo dpkg -i ~/cudoctl.deb > /dev/null 2>&1')
            setup_clouds.append(str(cloud))
        elif isinstance(cloud, clouds.RunPod):
            step_prefix = prefix_str.replace('<step>',
                                             str(len(setup_clouds) + 1))
            commands.append(f'echo -en "\\r{step_prefix}RunPod{empty_str}" && '
                            'pip list | grep runpod > /dev/null 2>&1 || '
                            'pip install "runpod>=1.5.1" > /dev/null 2>&1')
            setup_clouds.append(str(cloud))
        elif isinstance(cloud, clouds.OCI):
            step_prefix = prefix_str.replace('<step>',
                                             str(len(setup_clouds) + 1))
            commands.append(f'echo -en "\\r{prefix_str}OCI{empty_str}" && '
                            'pip list | grep oci > /dev/null 2>&1 || '
                            'pip install oci > /dev/null 2>&1')
            setup_clouds.append(str(cloud))
        if controller == Controllers.JOBS_CONTROLLER:
            if isinstance(cloud, clouds.IBM):
                step_prefix = prefix_str.replace('<step>',
                                                 str(len(setup_clouds) + 1))
                commands.append(
                    f'echo -en "\\r{step_prefix}IBM{empty_str}" '
                    '&& pip list | grep ibm-cloud-sdk-core > /dev/null 2>&1 || '
                    'pip install ibm-cloud-sdk-core ibm-vpc '
                    'ibm-platform-services ibm-cos-sdk > /dev/null 2>&1')
                setup_clouds.append(str(cloud))
    if (cloudflare.NAME
            in storage_lib.get_cached_enabled_storage_clouds_or_refresh()):
        step_prefix = prefix_str.replace('<step>', str(len(setup_clouds) + 1))
        commands.append(
            f'echo -en "\\r{step_prefix}Cloudflare{empty_str}" && ' +
            aws_dependencies_installation)
        setup_clouds.append(cloudflare.NAME)

    finish_prefix = prefix_str.replace('[<step>/<total>] ', '  ')
    commands.append(f'echo -e "\\r{finish_prefix}done.{empty_str}"')
    commands = [
        command.replace('<total>', str(len(setup_clouds)))
        for command in commands
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
            common_utils.dump_yaml(temp_file.name, dict(**local_user_config))
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
        constants.USER_ENV_VAR: getpass.getuser(),
        constants.USER_ID_ENV_VAR: common_utils.get_user_hash(),
        # Skip cloud identity check to avoid the overhead.
        env_options.Options.SKIP_CLOUD_IDENTITY_CHECK.env_key: '1',
        # Disable minimize logging to get more details on the controller.
        env_options.Options.MINIMIZE_LOGGING.env_key: '0',
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
    filtered_clouds = ({controller_cloud} if controller_cloud is not None else
                       requested_clouds_with_region_zone.keys())

    # Filter regions and zones and construct the result.
    result: Set[resources.Resources] = set()
    for cloud_name in filtered_clouds:
        regions = requested_clouds_with_region_zone.get(cloud_name,
                                                        {None: {None}})

        # Filter regions if controller_resources_to_use.region is specified.
        filtered_regions = ({controller_region} if controller_region is not None
                            else regions.keys())

        for region in filtered_regions:
            zones = regions.get(region, {None})

            # Filter zones if controller_resources_to_use.zone is specified.
            filtered_zones = ({controller_zone}
                              if controller_zone is not None else zones)

            # Create combinations of cloud, region, and zone.
            for zone in filtered_zones:
                resource_copy = controller_resources_to_use.copy(
                    cloud=clouds.CLOUD_REGISTRY.from_str(cloud_name),
                    region=region,
                    zone=zone)
                result.add(resource_copy)

    if not result:
        return {controller_resources_to_use}
    return result


def _setup_proxy_command_on_controller(
        controller_launched_cloud: 'clouds.Cloud',
        user_config: Dict[str, Any]) -> skypilot_config.Config:
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
    config = skypilot_config.Config.from_dict(user_config)
    proxy_command_key = (str(controller_launched_cloud).lower(),
                         'ssh_proxy_command')
    ssh_proxy_command = config.get_nested(proxy_command_key, None)
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
                user_config = common_utils.read_yaml(local_path)
                config = _setup_proxy_command_on_controller(cloud, user_config)
                common_utils.dump_yaml(f.name, dict(**config))
                file_mounts[remote_path] = f.name
                replaced = True
    if replaced:
        logger.debug(f'Replaced {_LOCAL_SKYPILOT_CONFIG_PATH_SUFFIX} '
                     f'with the real path in file mounts: {file_mounts}')


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
        logger.info(
            ux_utils.starting_message(f'Translating {msg} to '
                                      'SkyPilot Storage...'))
        rich_utils.force_update_status(
            ux_utils.spinner_message(
                f'Translating {msg} to SkyPilot Storage...'))

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
        logger.info(f'  {colorama.Style.DIM}Folder : {src!r} '
                    f'-> storage: {bucket_name!r}.{colorama.Style.RESET_ALL}')

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
        sources_str = '\n    '.join(sources)
        logger.info(f'  {colorama.Style.DIM}Files (listed below) '
                    f' -> storage: {file_bucket_name}:'
                    f'\n    {sources_str}{colorama.Style.RESET_ALL}')
    rich_utils.force_update_status(
        ux_utils.spinner_message('Uploading translated local files/folders'))
    task.update_storage_mounts(new_storage_mounts)

    # Step 4: Upload storage from sources
    # Upload the local source to a bucket. The task will not be executed
    # locally, so we need to upload the files/folders to the bucket manually
    # here before sending the task to the remote jobs controller.
    if task.storage_mounts:
        # There may be existing (non-translated) storage mounts, so log this
        # whenever task.storage_mounts is non-empty.
        rich_utils.force_update_status(
            ux_utils.spinner_message('Uploading local sources to storage[/]  '
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
        storage_obj = task.storage_mounts[file_mount_remote_tmp_dir]
        store_type = list(storage_obj.stores.keys())[0]
        store_object = storage_obj.stores[store_type]
        bucket_url = storage_lib.StoreType.get_endpoint_url(
            store_object, file_bucket_name)
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
            store_object = storage_obj.stores[store_type]
            storage_obj.source = storage_lib.StoreType.get_endpoint_url(
                store_object, storage_obj.name)
            storage_obj.force_delete = True

    # Step 7: Convert all `MOUNT` mode storages which don't specify a source
    # to specifying a source. If the source is specified with a local path,
    # it was handled in step 6.
    updated_mount_storages = {}
    for storage_path, storage_obj in task.storage_mounts.items():
        if (storage_obj.mode == storage_lib.StorageMode.MOUNT and
                not storage_obj.source):
            # Construct source URL with first store type and storage name
            # E.g., s3://my-storage-name
            store_types = list(storage_obj.stores.keys())
            assert len(store_types) == 1, (
                'We only support one store type for now.', storage_obj.stores)
            store_type = store_types[0]
            store_object = storage_obj.stores[store_type]
            source = storage_lib.StoreType.get_endpoint_url(
                store_object, storage_obj.name)
            new_storage = storage_lib.Storage.from_yaml_config({
                'source': source,
                'persistent': storage_obj.persistent,
                'mode': storage_lib.StorageMode.MOUNT.value,
                # We enable force delete to allow the controller to delete
                # the object store in case persistent is set to False.
                '_force_delete': True
            })
            updated_mount_storages[storage_path] = new_storage
    task.update_storage_mounts(updated_mount_storages)
    if msg:
        logger.info(ux_utils.finishing_message('Uploaded local files/folders.'))
