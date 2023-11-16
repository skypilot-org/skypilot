"""Util constants/functions for SkyPilot Controllers."""
import copy
import dataclasses
import enum
import getpass
import os
import tempfile
import typing
from typing import Any, Dict, Optional, Tuple

import colorama

from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
from sky.data import data_utils
from sky.data import storage as storage_lib
from sky.serve import serve_utils
from sky.skylet import constants
from sky.spot import spot_utils
from sky.utils import common_utils
from sky.utils import env_options
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky import task as task_lib
    from sky.backends import cloud_vm_ray_backend

logger = sky_logging.init_logger(__name__)

# Message thrown when APIs sky.spot_launch(),sky.serve.up() received an invalid
# controller resources spec.
CONTROLLER_RESOURCES_NOT_VALID_MESSAGE = (
    '{controller_type} controller resources is not valid, please check '
    '~/.sky/config.yaml file and make sure '
    '{controller_type}.controller.resources is a valid resources spec. '
    'Details:\n  {err}')


@dataclasses.dataclass
class _ControllerSpec:
    """Spec for skypilot controllers."""
    name: str
    cluster_name: str
    in_progress_hint: str
    decline_cancel_hint: str
    decline_down_in_init_status_hint: str
    decline_down_for_dirty_controller_hint: str
    check_cluster_name_hint: str
    default_hint_if_non_existent: str


class Controllers(enum.Enum):
    """Skypilot controllers."""
    # NOTE(dev): Keep this align with
    # sky/cli.py::_CONTROLLER_TO_HINT_OR_RAISE
    SPOT_CONTROLLER = _ControllerSpec(
        name='managed spot controller',
        cluster_name=spot_utils.SPOT_CONTROLLER_NAME,
        in_progress_hint=(
            '* {job_info}To see all spot jobs: '
            f'{colorama.Style.BRIGHT}sky spot queue{colorama.Style.RESET_ALL}'),
        decline_cancel_hint=(
            'Cancelling the spot controller\'s jobs is not allowed.\nTo cancel '
            f'spot jobs, use: {colorama.Style.BRIGHT}sky spot cancel <spot '
            f'job IDs> [--all]{colorama.Style.RESET_ALL}'),
        decline_down_in_init_status_hint=(
            f'{colorama.Fore.RED}Tearing down the spot controller while '
            'it is in INIT state is not supported (this means a spot launch '
            'is in progress or the previous launch failed), as we cannot '
            'guarantee that all the spot jobs are finished. Please wait '
            'until the spot controller is UP or fix it with '
            f'{colorama.Style.BRIGHT}sky start '
            f'{spot_utils.SPOT_CONTROLLER_NAME}{colorama.Style.RESET_ALL}.'),
        decline_down_for_dirty_controller_hint=(
            f'{colorama.Fore.RED}In-progress spot jobs found. To avoid '
            f'resource leakage, cancel all jobs first: {colorama.Style.BRIGHT}'
            f'sky spot cancel -a{colorama.Style.RESET_ALL}\n'),
        check_cluster_name_hint=(
            f'Cluster {spot_utils.SPOT_CONTROLLER_NAME} is reserved for '
            'managed spot controller. '),
        default_hint_if_non_existent='No managed spot jobs are found.')
    SKY_SERVE_CONTROLLER = _ControllerSpec(
        name='sky serve controller',
        cluster_name=serve_utils.SKY_SERVE_CONTROLLER_NAME,
        in_progress_hint=(
            f'* To see detailed service status: {colorama.Style.BRIGHT}'
            f'sky serve status -a{colorama.Style.RESET_ALL}'),
        decline_cancel_hint=(
            'Cancelling the sky serve controller\'s jobs is not allowed.'),
        decline_down_in_init_status_hint=(
            f'{colorama.Fore.RED}Tearing down the sky serve controller '
            'while it is in INIT state is not supported (this means a sky '
            'serve up is in progress or the previous launch failed), as we '
            'cannot guarantee that all the services are terminated. Please '
            'wait until the sky serve controller is UP or fix it with '
            f'{colorama.Style.BRIGHT}sky start '
            f'{serve_utils.SKY_SERVE_CONTROLLER_NAME}'
            f'{colorama.Style.RESET_ALL}.'),
        decline_down_for_dirty_controller_hint=(
            f'{colorama.Fore.RED}Tearing down the sky serve controller is not '
            'supported, as it is currently serving the following services: '
            '{service_names}. Please terminate the services first with '
            f'{colorama.Style.BRIGHT}sky serve down -a'
            f'{colorama.Style.RESET_ALL}.'),
        check_cluster_name_hint=(
            f'Cluster {serve_utils.SKY_SERVE_CONTROLLER_NAME} is reserved for '
            'sky serve controller. '),
        default_hint_if_non_existent='No service is found.')

    @classmethod
    def from_name(cls, name: Optional[str]) -> Optional['Controllers']:
        """Check if the cluster name is a controller name.

        Returns:
            The controller if the cluster name is a controller name.
            Otherwise, returns None.
        """
        for controller in cls:
            if controller.value.cluster_name == name:
                return controller
        return None


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

    This function is only used by spot controller and sky serve controller.
    """
    os.makedirs(local_dir, exist_ok=True)
    log_file = None
    try:
        log_dirs = backend.sync_down_logs(
            handle,
            # Download the log of the latest job.
            # The job_id for the spot job running on the spot cluster is not
            # necessarily 1, as it is possible that the worker node in a
            # multi-node cluster is preempted, and we recover the spot job
            # on the existing cluster, which leads to a larger job_id. Those
            # job_ids all represent the same logical spot job.
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
                with open(log_file) as f:
                    print(f.read())
            except FileNotFoundError:
                logger.error('Failed to find the logs for the user '
                             f'program at {log_file}.')
    return log_file


def _shared_controller_env_vars() -> Dict[str, str]:
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
    return env_vars


def skypilot_config_setup(
    controller_type: str,
    controller_resources_config: Dict[str, Any],
    remote_user_config_path: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Read the skypilot config and setup the controller resources.

    Returns:
        A tuple of (vars_to_fill, controller_resources_config). `var_to_fill`
        is a dict of variables that will be filled in the controller template.
        The controller_resources_config is the resources config that will be
        used to launch the controller.
    """
    vars_to_fill: Dict[str, Any] = {}
    controller_envs = _shared_controller_env_vars()
    controller_resources_config_copied: Dict[str, Any] = copy.copy(
        controller_resources_config)
    if skypilot_config.loaded():
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
        # used throughout the spot job's / service's recovery attempts
        # (i.e., if it relaunches due to preemption, we make sure the
        # same config is used).
        #
        # NOTE: suppose that we have a controller in old VPC, then user
        # changes 'vpc_name' in the config and does a 'spot launch' /
        # 'serve up'. In general, the old controller may not successfully
        # launch the job in the new VPC. This happens if the two VPCs don’t
        # have peering set up. Like other places in the code, we assume
        # properly setting up networking is user's responsibilities.
        # TODO(zongheng): consider adding a basic check that checks
        # controller VPC (or name) == the spot job's / service's VPC
        # (or name). It may not be a sufficient check (as it's always
        # possible that peering is not set up), but it may catch some
        # obvious errors.
        # TODO(zhwu): hacky. We should only set the proxy command of the
        # cloud where the controller is launched (currently, only aws user
        # uses proxy_command).
        proxy_command_key = ('aws', 'ssh_proxy_command')
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

        with tempfile.NamedTemporaryFile(mode='w', delete=False) as tmpfile:
            common_utils.dump_yaml(tmpfile.name, config_dict)
            controller_envs[skypilot_config.ENV_VAR_SKYPILOT_CONFIG] = (
                remote_user_config_path)
            vars_to_fill.update({
                'user_config_path': tmpfile.name,
                'remote_user_config_path': remote_user_config_path,
            })

        # Override the controller resources with the ones specified in the
        # config.
        custom_controller_resources_config = skypilot_config.get_nested(
            (controller_type, 'controller', 'resources'), None)
        if custom_controller_resources_config is not None:
            controller_resources_config_copied.update(
                custom_controller_resources_config)
    else:
        # If the user config is not loaded, manually set this to None
        # so that the template won't render this.
        vars_to_fill['user_config_path'] = None

    vars_to_fill['controller_envs'] = controller_envs
    return vars_to_fill, controller_resources_config_copied


def maybe_translate_local_file_mounts_and_sync_up(task: 'task_lib.Task',
                                                  path: str):
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
            username=getpass.getuser(), id=run_id)
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
            username=getpass.getuser(),
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
        username=getpass.getuser(), id=run_id)
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
    # here before sending the task to the remote spot controller.
    if task.storage_mounts:
        # There may be existing (non-translated) storage mounts, so log this
        # whenever task.storage_mounts is non-empty.
        logger.info(f'{colorama.Fore.YELLOW}Uploading sources to cloud storage.'
                    f'{colorama.Style.RESET_ALL} See: sky storage ls')
    task.sync_storage_mounts()

    # Step 5: Add the file download into the file mounts, such as
    #  /original-dst: s3://spot-fm-file-only-bucket-name/file-0
    new_file_mounts = {}
    for dst, src in copy_mounts_with_file_in_src.items():
        storage = task.storage_mounts[file_mount_remote_tmp_dir]
        store_type = list(storage.stores.keys())[0]
        store_prefix = storage_lib.get_store_prefix(store_type)
        bucket_url = store_prefix + file_bucket_name
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
            if store_type == storage_lib.StoreType.S3:
                storage_obj.source = f's3://{storage_obj.name}'
            elif store_type == storage_lib.StoreType.GCS:
                storage_obj.source = f'gs://{storage_obj.name}'
            elif store_type == storage_lib.StoreType.R2:
                storage_obj.source = f'r2://{storage_obj.name}'
            else:
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.NotSupportedError(
                        f'Unsupported store type: {store_type}')
            storage_obj.force_delete = True
