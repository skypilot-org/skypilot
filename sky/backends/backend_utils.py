"""Util constants/functions for the backends."""
import asyncio
from datetime import datetime
import enum
import fnmatch
import hashlib
import os
import pathlib
import pprint
import queue as queue_lib
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import typing
from typing import (Any, Callable, Dict, Iterator, List, Optional, Sequence,
                    Set, Tuple, TypeVar, Union)
import uuid

import aiohttp
from aiohttp import ClientTimeout
from aiohttp import TCPConnector
import colorama
from packaging import version
from typing_extensions import Literal

import sky
from sky import authentication as auth
from sky import backends
from sky import check as sky_check
from sky import clouds
from sky import exceptions
from sky import global_user_state
from sky import logs
from sky import provision as provision_lib
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import common as adaptors_common
from sky.jobs import utils as managed_job_utils
from sky.provision import common as provision_common
from sky.provision import instance_setup
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.serve import serve_utils
from sky.server.requests import requests as requests_lib
from sky.skylet import autostop_lib
from sky.skylet import constants
from sky.usage import usage_lib
from sky.utils import auth_utils
from sky.utils import cluster_utils
from sky.utils import command_runner
from sky.utils import common
from sky.utils import common_utils
from sky.utils import context as context_lib
from sky.utils import context_utils
from sky.utils import controller_utils
from sky.utils import env_options
from sky.utils import locks
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils import rich_utils
from sky.utils import schemas
from sky.utils import status_lib
from sky.utils import subprocess_utils
from sky.utils import tempstore
from sky.utils import timeline
from sky.utils import ux_utils
from sky.utils import volume as volume_utils
from sky.utils import yaml_utils
from sky.utils.plugin_extensions import ExternalFailureSource
from sky.workspaces import core as workspaces_core

if typing.TYPE_CHECKING:
    import grpc
    import requests
    from requests import adapters
    from requests.packages.urllib3.util import retry as retry_lib
    import rich.progress as rich_progress
    import yaml

    from sky import resources as resources_lib
    from sky import task as task_lib
    from sky.backends import cloud_vm_ray_backend
    from sky.backends import local_docker_backend
else:
    yaml = adaptors_common.LazyImport('yaml')
    requests = adaptors_common.LazyImport('requests')
    rich_progress = adaptors_common.LazyImport('rich.progress')
    adapters = adaptors_common.LazyImport('requests.adapters')
    retry_lib = adaptors_common.LazyImport(
        'requests.packages.urllib3.util.retry')
    # To avoid requiring grpcio to be installed on the client side.
    grpc = adaptors_common.LazyImport('grpc')

logger = sky_logging.init_logger(__name__)

# NOTE: keep in sync with the cluster template 'file_mounts'.
SKY_REMOTE_APP_DIR = '~/.sky/sky_app'
# Exclude subnet mask from IP address regex.
IP_ADDR_REGEX = r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?!/\d{1,2})\b'
SKY_REMOTE_PATH = '~/.sky/wheels'

# Do not use /tmp because it gets cleared on VM restart.
_SKY_REMOTE_FILE_MOUNTS_DIR = '~/.sky/file_mounts/'

_LAUNCHED_HEAD_PATTERN = re.compile(r'(\d+) ray[._]head[._]default')
_LAUNCHED_LOCAL_WORKER_PATTERN = re.compile(r'(\d+) node_')
_LAUNCHED_WORKER_PATTERN = re.compile(r'(\d+) ray[._]worker[._]default')
_LAUNCHED_RESERVED_WORKER_PATTERN = re.compile(
    r'(\d+) ray[._]worker[._]reserved')
# Intentionally not using prefix 'rf' for the string format because yapf have a
# bug with python=3.6.
# 10.133.0.5: ray.worker.default,
_LAUNCHING_IP_PATTERN = re.compile(
    r'({}): ray[._]worker[._](?:default|reserved)'.format(IP_ADDR_REGEX))
SSH_CONNECTION_ERROR_PATTERN = re.compile(
    r'^ssh:.*(timed out|connection refused)$', re.IGNORECASE)
_SSH_CONNECTION_TIMED_OUT_PATTERN = re.compile(r'^ssh:.*timed out$',
                                               re.IGNORECASE)
K8S_PODS_NOT_FOUND_PATTERN = re.compile(r'.*(NotFound|pods .* not found).*',
                                        re.IGNORECASE)
_RAY_CLUSTER_NOT_FOUND_MESSAGE = 'Ray cluster is not found'
WAIT_HEAD_NODE_IP_MAX_ATTEMPTS = 3

# We check network connection by going through _TEST_IP_LIST. We may need to
# check multiple IPs because some IPs may be blocked on certain networks.
# Fixed IP addresses are used to avoid DNS lookup blocking the check, for
# machine with no internet connection.
# Refer to: https://stackoverflow.com/questions/3764291/how-can-i-see-if-theres-an-available-and-active-network-connection-in-python # pylint: disable=line-too-long
_TEST_IP_LIST = ['https://8.8.8.8', 'https://1.1.1.1']

# Allow each CPU thread take 2 tasks.
# Note: This value cannot be too small, otherwise OOM issue may occur.
DEFAULT_TASK_CPU_DEMAND = 0.5

CLUSTER_STATUS_LOCK_TIMEOUT_SECONDS = 20

# Time that must elapse since the last status check before we should re-check if
# the cluster has been terminated or autostopped.
CLUSTER_STATUS_CACHE_DURATION_SECONDS = 2

CLUSTER_FILE_MOUNTS_LOCK_TIMEOUT_SECONDS = 10
WORKSPACE_LOCK_TIMEOUT_SECONDS = 10
CLUSTER_TUNNEL_LOCK_TIMEOUT_SECONDS = 10.0

# Remote dir that holds our runtime files.
_REMOTE_RUNTIME_FILES_DIR = '~/.sky/.runtime_files'

# The maximum size of a command line arguments is 128 KB, i.e. the command
# executed with /bin/sh should be less than 128KB.
# https://github.com/torvalds/linux/blob/master/include/uapi/linux/binfmts.h
#
# If a user have very long run or setup commands, the generated command may
# exceed the limit, as we directly include scripts in job submission commands.
# If the command is too long, we instead write it to a file, rsync and execute
# it.
#
# We use 100KB as a threshold to be safe for other arguments that
# might be added during ssh.
_MAX_INLINE_SCRIPT_LENGTH = 100 * 1024

_ENDPOINTS_RETRY_MESSAGE = ('If the cluster was recently started, '
                            'please retry after a while.')

# If a cluster is less than LAUNCH_DOUBLE_CHECK_WINDOW seconds old, and we don't
# see any instances in the cloud, the instances might be in the process of
# being created. We will wait LAUNCH_DOUBLE_CHECK_DELAY seconds and then double
# check to make sure there are still no instances. LAUNCH_DOUBLE_CHECK_DELAY
# should be set longer than the delay between (sending the create instance
# request) and (the instances appearing on the cloud).
# See https://github.com/skypilot-org/skypilot/issues/4431.
_LAUNCH_DOUBLE_CHECK_WINDOW = 60
_LAUNCH_DOUBLE_CHECK_DELAY = 1

# Include the fields that will be used for generating tags that distinguishes
# the cluster in ray, to avoid the stopped cluster being discarded due to
# updates in the yaml template.
# Some notes on the fields:
# - 'provider' fields will be used for bootstrapping and insert more new items
#   in 'node_config'.
# - keeping the auth is not enough becuase the content of the key file will be
#   used for calculating the hash.
# TODO(zhwu): Keep in sync with the fields used in https://github.com/ray-project/ray/blob/e4ce38d001dbbe09cd21c497fedd03d692b2be3e/python/ray/autoscaler/_private/commands.py#L687-L701
_RAY_YAML_KEYS_TO_RESTORE_FOR_BACK_COMPATIBILITY = {
    'cluster_name', 'provider', 'auth', 'node_config', 'docker'
}
# For these keys, don't use the old yaml's version and instead use the new yaml's.
#  - zone: The zone field of the old yaml may be '1a,1b,1c' (AWS) while the actual
#    zone of the launched cluster is '1a'. If we restore, then on capacity errors
#    it's possible to failover to 1b, which leaves a leaked instance in 1a. Here,
#    we use the new yaml's zone field, which is guaranteed to be the existing zone
#    '1a'.
# - docker_login_config: The docker_login_config field of the old yaml may be
#   outdated or wrong. Users may want to fix the login config if a cluster fails
#   to launch due to the login config.
# - UserData: The UserData field of the old yaml may be outdated, and we want to
#   use the new yaml's UserData field, which contains the authorized key setup as
#   well as the disabling of the auto-update with apt-get.
# - firewall_rule: This is a newly added section for gcp in provider section.
# - security_group: In #2485 we introduces the changed of security group, so we
#   should take the latest security group name.
_RAY_YAML_KEYS_TO_RESTORE_EXCEPTIONS = [
    ('provider', 'availability_zone'),
    # Clouds with new provisioner has docker_login_config in the
    # docker field, instead of the provider field.
    ('docker', 'docker_login_config'),
    ('docker', 'run_options'),
    # Other clouds
    ('provider', 'docker_login_config'),
    ('provider', 'firewall_rule'),
    # TPU node launched before #2943 does not have the `provider.tpu_node` set,
    # and our latest code need this field to be set to distinguish the node, so
    # we need to take this field from the new yaml.
    ('provider', 'tpu_node'),
    ('provider', 'security_group', 'GroupName'),
    ('available_node_types', 'ray.head.default', 'node_config',
     'IamInstanceProfile'),
    ('available_node_types', 'ray.head.default', 'node_config', 'UserData'),
    ('available_node_types', 'ray.head.default', 'node_config',
     'azure_arm_parameters', 'cloudInitSetupCommands'),
    ('available_node_types', 'ray_head_default', 'node_config', 'pvc_spec'),
    ('available_node_types', 'ray_head_default', 'node_config',
     'deployment_spec'),
]
# These keys are expected to change when provisioning on an existing cluster,
# but they don't actually represent a change that requires re-provisioning the
# cluster.  If the cluster yaml is the same except for these keys, we can safely
# skip reprovisioning. See _deterministic_cluster_yaml_hash.
_RAY_YAML_KEYS_TO_REMOVE_FOR_HASH = [
    # On first launch, availability_zones will include all possible zones. Once
    # the cluster exists, it will only include the zone that the cluster is
    # actually in.
    ('provider', 'availability_zone'),
]

_ACK_MESSAGE = 'ack'
_FORWARDING_FROM_MESSAGE = 'Forwarding from'


def is_command_length_over_limit(command: str) -> bool:
    """Check if the length of the command exceeds the limit.

    We calculate the length of the command after quoting the command twice as
    when it is executed by the CommandRunner, the command will be quoted twice
    to ensure the correctness, which will add significant length to the command.
    """

    quoted_length = len(shlex.quote(shlex.quote(command)))
    return quoted_length > _MAX_INLINE_SCRIPT_LENGTH


def is_ip(s: str) -> bool:
    """Returns whether this string matches IP_ADDR_REGEX."""
    return len(re.findall(IP_ADDR_REGEX, s)) == 1


def _get_yaml_path_from_cluster_name(cluster_name: str,
                                     prefix: str = constants.SKY_USER_FILE_PATH
                                    ) -> str:
    output_path = pathlib.Path(
        prefix).expanduser().resolve() / f'{cluster_name}.yml'
    os.makedirs(output_path.parents[0], exist_ok=True)
    return str(output_path)


# Add retry for the file mounts optimization, as the underlying cp command may
# experience transient errors, #4758.
@common_utils.retry
def _optimize_file_mounts(tmp_yaml_path: str) -> None:
    """Optimize file mounts in the given ray yaml file.

    Runtime files handling:
    List of runtime files to be uploaded to cluster:
      - yaml config (for autostopping)
      - wheel
      - credentials
    Format is {dst: src}.

    Raises:
        subprocess.CalledProcessError: If the file mounts are failed to be
            copied.
    """
    yaml_config = yaml_utils.read_yaml(tmp_yaml_path)

    file_mounts = yaml_config.get('file_mounts', {})
    # Remove the file mounts added by the newline.
    if '' in file_mounts:
        assert file_mounts[''] == '', file_mounts['']
        file_mounts.pop('')

    # Putting these in file_mounts hurts provisioning speed, as each file
    # opens/closes an SSH connection.  Instead, we:
    #  - cp them locally into a directory, each with a unique name to avoid
    #    basename conflicts
    #  - upload that directory as a file mount (1 connection)
    #  - use a remote command to move all runtime files to their right places.

    # Local tmp dir holding runtime files.
    local_runtime_files_dir = tempstore.mkdtemp()
    new_file_mounts = {_REMOTE_RUNTIME_FILES_DIR: local_runtime_files_dir}

    # Generate local_src -> unique_name.
    local_source_to_unique_name = {}
    for local_src in file_mounts.values():
        local_source_to_unique_name[local_src] = str(uuid.uuid4())

    # (For remote) Build a command that copies runtime files to their right
    # destinations.
    # NOTE: we copy rather than move, because when launching >1 node, head node
    # is fully set up first, and if moving then head node's files would already
    # move out of _REMOTE_RUNTIME_FILES_DIR, which would cause setting up
    # workers (from the head's files) to fail.  An alternative is softlink
    # (then we need to make sure the usage of runtime files follow links).
    commands = []
    basenames = set()
    for dst, src in file_mounts.items():
        src_basename = local_source_to_unique_name[src]
        dst_basename = os.path.basename(dst)
        dst_parent_dir = os.path.dirname(dst)

        # Validate by asserts here as these files are added by our backend.
        # Our runtime files (wheel, yaml, credentials) do not have backslashes.
        assert not src.endswith('/'), src
        assert not dst.endswith('/'), dst
        assert src_basename not in basenames, (
            f'Duplicated src basename: {src_basename}; mounts: {file_mounts}')
        basenames.add(src_basename)
        # Our runtime files (wheel, yaml, credentials) are not relative paths.
        assert dst_parent_dir, f'Found relative destination path: {dst}'

        mkdir_parent = f'mkdir -p {dst_parent_dir}'
        if os.path.isdir(os.path.expanduser(src)):
            # Special case for directories. If the dst already exists as a
            # folder, directly copy the folder will create a subfolder under
            # the dst.
            mkdir_parent = f'mkdir -p {dst}'
            src_basename = f'{src_basename}/*'
        mv = (f'cp -rf {_REMOTE_RUNTIME_FILES_DIR}/{src_basename} '
              f'{dst_parent_dir}/{dst_basename}')
        fragment = f'({mkdir_parent} && {mv})'
        commands.append(fragment)
    postprocess_runtime_files_command = '; '.join(commands)

    setup_commands = yaml_config.get('setup_commands', [])
    if setup_commands:
        setup_commands[
            0] = f'{postprocess_runtime_files_command}; {setup_commands[0]}'
    else:
        setup_commands = [postprocess_runtime_files_command]

    yaml_config['file_mounts'] = new_file_mounts
    yaml_config['setup_commands'] = setup_commands

    # (For local) Copy all runtime files, including the just-written yaml, to
    # local_runtime_files_dir/.
    # < 0.3s to cp 6 clouds' credentials.
    for local_src in file_mounts.values():
        # cp <local_src> <local_runtime_files_dir>/<unique name of local_src>.
        full_local_src = str(pathlib.Path(local_src).expanduser())
        unique_name = local_source_to_unique_name[local_src]
        # !r to add quotes for paths containing spaces.
        subprocess.run(
            f'cp -r {full_local_src!r} {local_runtime_files_dir}/{unique_name}',
            shell=True,
            check=True)

    yaml_utils.dump_yaml(tmp_yaml_path, yaml_config)


def path_size_megabytes(path: str) -> int:
    """Returns the size of 'path' (directory or file) in megabytes.

    Returns:
        If successful: the size of 'path' in megabytes, rounded down. Otherwise,
        -1.
    """
    git_exclude_filter = ''
    resolved_path = pathlib.Path(path).expanduser().resolve()
    if (resolved_path / constants.SKY_IGNORE_FILE).exists():
        rsync_filter = command_runner.RSYNC_FILTER_SKYIGNORE
    else:
        rsync_filter = command_runner.RSYNC_FILTER_GITIGNORE
        if (resolved_path / command_runner.GIT_EXCLUDE).exists():
            # Ensure file exists; otherwise, rsync will error out.
            #
            # We shlex.quote() because the path may contain spaces:
            #   'my dir/.git/info/exclude'
            # Without quoting rsync fails.
            git_exclude_filter = command_runner.RSYNC_EXCLUDE_OPTION.format(
                shlex.quote(str(resolved_path / command_runner.GIT_EXCLUDE)))
    rsync_command = (f'rsync {command_runner.RSYNC_DISPLAY_OPTION} '
                     f'{rsync_filter} '
                     f'{git_exclude_filter} --dry-run {path!r}')
    rsync_output = ''
    try:
        # rsync sometimes fails `--dry-run` for MacOS' rsync build, however this function is only used to display
        # a warning message to the user if the size of a file/directory is too
        # large, so we can safely ignore the error.
        rsync_output = str(
            subprocess.check_output(rsync_command,
                                    shell=True,
                                    stderr=subprocess.DEVNULL))
    except subprocess.CalledProcessError:
        logger.debug('Command failed, proceeding without estimating size: '
                     f'{rsync_command}')
        return -1
    # 3.2.3:
    #  total size is 250,957,728  speedup is 330.19 (DRY RUN)
    # 2.6.9:
    #  total size is 212627556  speedup is 2437.41
    match = re.search(r'total size is ([\d,]+)', rsync_output)
    if match is not None:
        try:
            total_bytes = int(float(match.group(1).replace(',', '')))
            return total_bytes // (1024**2)
        except ValueError:
            logger.debug('Failed to find "total size" in rsync output. Inspect '
                         f'output of the following command: {rsync_command}')
            pass  # Maybe different rsync versions have different output.
    return -1


class FileMountHelper(object):
    """Helper for handling file mounts."""

    @classmethod
    def wrap_file_mount(cls, path: str) -> str:
        """Prepends ~/<opaque dir>/ to a path to work around permission issues.

        Examples:
        /root/hello.txt -> ~/<opaque dir>/root/hello.txt
        local.txt -> ~/<opaque dir>/local.txt

        After the path is synced, we can later create a symlink to this wrapped
        path from the original path, e.g., in the initialization_commands of the
        ray autoscaler YAML.
        """
        return os.path.join(_SKY_REMOTE_FILE_MOUNTS_DIR, path.lstrip('/'))

    @classmethod
    def make_safe_symlink_command(cls, *, source: str, target: str) -> str:
        """Returns a command that safely symlinks 'source' to 'target'.

        All intermediate directories of 'source' will be owned by $(whoami),
        excluding the root directory (/).

        'source' must be an absolute path; both 'source' and 'target' must not
        end with a slash (/).

        This function is needed because a simple 'ln -s target source' may
        fail: 'source' can have multiple levels (/a/b/c), its parent dirs may
        or may not exist, can end with a slash, or may need sudo access, etc.

        Cases of <target: local> file mounts and their behaviors:

            /existing_dir: ~/local/dir
              - error out saying this cannot be done as LHS already exists
            /existing_file: ~/local/file
              - error out saying this cannot be done as LHS already exists
            /existing_symlink: ~/local/file
              - overwrite the existing symlink; this is important because `sky
                launch` can be run multiple times
            Paths that start with ~/ and /tmp/ do not have the above
            restrictions; they are delegated to rsync behaviors.
        """
        assert os.path.isabs(source), source
        assert not source.endswith('/') and not target.endswith('/'), (source,
                                                                       target)
        # Below, use sudo in case the symlink needs sudo access to create.
        # Prepare to create the symlink:
        #  1. make sure its dir(s) exist & are owned by $(whoami).
        dir_of_symlink = os.path.dirname(source)
        commands = [
            # mkdir, then loop over '/a/b/c' as /a, /a/b, /a/b/c.  For each,
            # chown $(whoami) on it so user can use these intermediate dirs
            # (excluding /).
            f'sudo mkdir -p {dir_of_symlink}',
            # p: path so far
            ('(p=""; '
             f'for w in $(echo {dir_of_symlink} | tr "/" " "); do '
             'p=${p}/${w}; sudo chown $(whoami) $p; done)')
        ]
        #  2. remove any existing symlink (ln -f may throw 'cannot
        #     overwrite directory', if the link exists and points to a
        #     directory).
        commands += [
            # Error out if source is an existing, non-symlink directory/file.
            f'((test -L {source} && sudo rm {source} &>/dev/null) || '
            f'(test ! -e {source} || '
            f'(echo "!!! Failed mounting because path exists ({source})"; '
            'exit 1)))',
        ]
        commands += [
            # Link.
            f'sudo ln -s {target} {source}',
            # chown.  -h to affect symlinks only.
            f'sudo chown -h $(whoami) {source}',
        ]
        return ' && '.join(commands)


def _replace_yaml_dicts(
        new_yaml: str, old_yaml: str, restore_key_names: Set[str],
        restore_key_names_exceptions: Sequence[Tuple[str, ...]]) -> str:
    """Replaces 'new' with 'old' for all keys in restore_key_names.

    The replacement will be applied recursively and only for the blocks
    with the key in key_names, and have the same ancestors in both 'new'
    and 'old' YAML tree.

    The restore_key_names_exceptions is a list of key names that should not
    be restored, i.e. those keys will be reset to the value in 'new' YAML
    tree after the replacement.
    """

    def _restore_block(new_block: Dict[str, Any], old_block: Dict[str, Any]):
        for key, value in new_block.items():
            if key in restore_key_names:
                if key in old_block:
                    new_block[key] = old_block[key]
                else:
                    del new_block[key]
            elif isinstance(value, dict):
                if key in old_block:
                    _restore_block(value, old_block[key])

    new_config = yaml_utils.safe_load(new_yaml)
    old_config = yaml_utils.safe_load(old_yaml)
    excluded_results = {}
    # Find all key values excluded from restore
    for exclude_restore_key_name_list in restore_key_names_exceptions:
        excluded_result = new_config
        found_excluded_key = True
        for key in exclude_restore_key_name_list:
            if (not isinstance(excluded_result, dict) or
                    key not in excluded_result):
                found_excluded_key = False
                break
            excluded_result = excluded_result[key]
        if found_excluded_key:
            excluded_results[exclude_restore_key_name_list] = excluded_result

    # Restore from old config
    _restore_block(new_config, old_config)

    # Revert the changes for the excluded key values
    for exclude_restore_key_name, value in excluded_results.items():
        curr = new_config
        for key in exclude_restore_key_name[:-1]:
            curr = curr[key]
        curr[exclude_restore_key_name[-1]] = value
    return yaml_utils.dump_yaml_str(new_config)


def get_expirable_clouds(
        enabled_clouds: Sequence[clouds.Cloud]) -> List[clouds.Cloud]:
    """Returns a list of clouds that use local credentials and whose credentials can expire.

    This function checks each cloud in the provided sequence to determine if it uses local credentials
    and if its credentials can expire. If both conditions are met, the cloud is added to the list of
    expirable clouds.

    Args:
        enabled_clouds (Sequence[clouds.Cloud]): A sequence of cloud objects to check.

    Returns:
        list[clouds.Cloud]: A list of cloud objects that use local credentials and whose credentials can expire.
    """
    expirable_clouds = []
    local_credentials_value = schemas.RemoteIdentityOptions.LOCAL_CREDENTIALS.value
    for cloud in enabled_clouds:
        # Kubernetes config might have context-specific properties
        if isinstance(cloud, clouds.Kubernetes):
            # get all custom contexts
            contexts = kubernetes_utils.get_custom_config_k8s_contexts()
            # add remote_identity of each context if it exists
            remote_identities: Optional[Union[str, List[Dict[str, str]]]] = None
            for context in contexts:
                context_remote_identity = skypilot_config.get_effective_region_config(
                    cloud='kubernetes',
                    region=context,
                    keys=('remote_identity',),
                    default_value=None)
                if context_remote_identity is not None:
                    if remote_identities is None:
                        remote_identities = []
                    if isinstance(context_remote_identity, str):
                        assert isinstance(remote_identities, list)
                        remote_identities.append(
                            {context: context_remote_identity})
                    elif isinstance(context_remote_identity, list):
                        assert isinstance(remote_identities, list)
                        remote_identities.extend(context_remote_identity)
            # add global kubernetes remote identity if it exists, if not, add default
            global_remote_identity = skypilot_config.get_effective_region_config(
                cloud='kubernetes',
                region=None,
                keys=('remote_identity',),
                default_value=None)
            if global_remote_identity is not None:
                if remote_identities is None:
                    remote_identities = []
                if isinstance(global_remote_identity, str):
                    assert isinstance(remote_identities, list)
                    remote_identities.append({'*': global_remote_identity})
                elif isinstance(global_remote_identity, list):
                    assert isinstance(remote_identities, list)
                    remote_identities.extend(global_remote_identity)
            if remote_identities is None:
                remote_identities = schemas.get_default_remote_identity(
                    str(cloud).lower())
        else:
            remote_identities = skypilot_config.get_effective_region_config(
                cloud=str(cloud).lower(),
                region=None,
                keys=('remote_identity',),
                default_value=None)
            if remote_identities is None:
                remote_identities = schemas.get_default_remote_identity(
                    str(cloud).lower())

        local_credential_expiring = cloud.can_credential_expire()
        if isinstance(remote_identities, str):
            if remote_identities == local_credentials_value and local_credential_expiring:
                expirable_clouds.append(cloud)
        elif isinstance(remote_identities, list):
            for profile in remote_identities:
                if list(profile.values(
                ))[0] == local_credentials_value and local_credential_expiring:
                    expirable_clouds.append(cloud)
                    break
    return expirable_clouds


def _get_volume_name(path: str, cluster_name_on_cloud: str) -> str:
    path_hash = hashlib.md5(path.encode()).hexdigest()[:6]
    return f'{cluster_name_on_cloud}-{path_hash}'


# TODO: too many things happening here - leaky abstraction. Refactor.
@timeline.event
def write_cluster_config(
    to_provision: 'resources_lib.Resources',
    num_nodes: int,
    cluster_config_template: str,
    cluster_name: str,
    local_wheel_path: pathlib.Path,
    wheel_hash: str,
    region: clouds.Region,
    zones: Optional[List[clouds.Zone]] = None,
    dryrun: bool = False,
    keep_launch_fields_in_existing_config: bool = True,
    volume_mounts: Optional[List['volume_utils.VolumeMount']] = None,
    cloud_specific_failover_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """Fills in cluster configuration templates and writes them out.

    Returns:
        Dict with the following keys:
        - 'ray': Path to the generated Ray yaml config file
        - 'cluster_name': Name of the cluster
        - 'cluster_name_on_cloud': Name of the cluster as it appears in the
          cloud provider
        - 'config_hash': Hash of the cluster config and file mounts contents.
          Can be missing if we unexpectedly failed to calculate the hash for
          some reason. In that case we will continue without the optimization to
          skip provisioning.

    Raises:
        exceptions.ResourcesUnavailableError: if the region/zones requested does
            not appear in the catalog, or an ssh_proxy_command is specified but
            not for the given region, or GPUs are requested in a Kubernetes
            cluster but the cluster does not have nodes labeled with GPU types.
        exceptions.InvalidCloudConfigs: if the user specifies some config for the
            cloud that is not valid, e.g. remote_identity: SERVICE_ACCOUNT
            for a cloud that does not support it, the caller should skip the
            cloud in this case.
    """
    # task.best_resources may not be equal to to_provision if the user
    # is running a job with less resources than the cluster has.
    cloud = to_provision.cloud
    assert cloud is not None, to_provision

    cluster_name_on_cloud = common_utils.make_cluster_name_on_cloud(
        cluster_name, max_length=cloud.max_cluster_name_length())

    # This can raise a ResourcesUnavailableError when:
    #  * The region/zones requested does not appear in the catalog. It can be
    #    triggered if the user changed the catalog file while there is a cluster
    #    in the removed region/zone.
    #  * GPUs are requested in a Kubernetes cluster but the cluster does not
    #    have nodes labeled with GPU types.
    #
    # TODO(zhwu): We should change the exception type to a more specific one, as
    # the ResourcesUnavailableError is overly used. Also, it would be better to
    # move the check out of this function, i.e. the caller should be responsible
    # for the validation.
    # TODO(tian): Move more cloud agnostic vars to resources.py.
    resources_vars = to_provision.make_deploy_variables(
        resources_utils.ClusterName(
            cluster_name,
            cluster_name_on_cloud,
        ), region, zones, num_nodes, dryrun, volume_mounts)
    config_dict = {}

    specific_reservations = set(
        skypilot_config.get_effective_region_config(
            cloud=str(to_provision.cloud).lower(),
            region=to_provision.region,
            keys=('specific_reservations',),
            default_value=set()))

    # Remote identity handling can have 4 cases:
    # 1. LOCAL_CREDENTIALS (default for most clouds): Upload local credentials
    # 2. SERVICE_ACCOUNT: SkyPilot creates and manages a service account
    # 3. Custom service account: Use specified service account
    # 4. NO_UPLOAD: Do not upload any credentials
    #
    # We need to upload credentials only if LOCAL_CREDENTIALS is specified. In
    # other cases, we exclude the cloud from credential file uploads after
    # running required checks.
    assert cluster_name is not None
    excluded_clouds: Set[clouds.Cloud] = set()
    remote_identity_config = skypilot_config.get_effective_region_config(
        cloud=str(cloud).lower(),
        region=region.name,
        keys=('remote_identity',),
        default_value=None,
        override_configs=to_provision.cluster_config_overrides)
    remote_identity = schemas.get_default_remote_identity(str(cloud).lower())
    if isinstance(remote_identity_config, str):
        remote_identity = remote_identity_config
    if isinstance(remote_identity_config, list):
        # Some clouds (e.g., AWS) support specifying multiple service accounts
        # chosen based on the cluster name. Do the matching here to pick the
        # correct one.
        for profile in remote_identity_config:
            if fnmatch.fnmatchcase(cluster_name, list(profile.keys())[0]):
                remote_identity = list(profile.values())[0]
                break
    if remote_identity != schemas.RemoteIdentityOptions.LOCAL_CREDENTIALS.value:
        # If LOCAL_CREDENTIALS is not specified, we add the cloud to the
        # excluded_clouds set, but we must also check if the cloud supports
        # service accounts.
        if remote_identity == schemas.RemoteIdentityOptions.NO_UPLOAD.value:
            # If NO_UPLOAD is specified, fall back to default remote identity
            # for downstream logic but add it to excluded_clouds to skip
            # credential file uploads.
            remote_identity = schemas.get_default_remote_identity(
                str(cloud).lower())
        elif not cloud.supports_service_account_on_remote():
            raise exceptions.InvalidCloudConfigs(
                'remote_identity: SERVICE_ACCOUNT is specified in '
                f'{skypilot_config.loaded_config_path!r} for {cloud}, but it '
                'is not supported by this cloud. Remove the config or set: '
                '`remote_identity: LOCAL_CREDENTIALS`.')
        if isinstance(cloud, clouds.Kubernetes):
            allowed_contexts = skypilot_config.get_workspace_cloud(
                'kubernetes').get('allowed_contexts', None)
            if allowed_contexts is None:
                allowed_contexts = skypilot_config.get_effective_region_config(
                    cloud='kubernetes',
                    region=None,
                    keys=('allowed_contexts',),
                    default_value=None)
            # Exclude both Kubernetes and SSH explicitly since:
            # 1. isinstance(cloud, clouds.Kubernetes) matches both (SSH
            #    inherits from Kubernetes)
            # 2. Both share the same get_credential_file_mounts() which
            #    returns the kubeconfig. So if we don't exclude both, the
            #    unexcluded one will upload the kubeconfig.
            # TODO(romilb): This is a workaround. The right long-term fix
            # is to have SSH Node Pools use its own kubeconfig instead of
            # sharing the global kubeconfig at ~/.kube/config. In the
            # interim, SSH Node Pools' get_credential_file_mounts can filter
            # contexts starting with ssh- and create a temp kubeconfig
            # to upload.
            # When allowed_contexts is not set, or when it is set for a
            # non-controller cluster, we exclude kubeconfig upload. Controller
            # clusters need kubeconfig to manage other K8s clusters.
            is_controller = controller_utils.Controllers.from_name(
                cluster_name, expect_exact_match=False) is not None
            if allowed_contexts is None or not is_controller:
                excluded_clouds.add(clouds.Kubernetes())
                excluded_clouds.add(clouds.SSH())
        else:
            excluded_clouds.add(cloud)

    for cloud_str, cloud_obj in registry.CLOUD_REGISTRY.items():
        remote_identity_config = skypilot_config.get_effective_region_config(
            cloud=cloud_str.lower(),
            region=region.name,
            keys=('remote_identity',),
            default_value=None)
        if remote_identity_config:
            if (remote_identity_config ==
                    schemas.RemoteIdentityOptions.NO_UPLOAD.value):
                excluded_clouds.add(cloud_obj)

    credentials = sky_check.get_cloud_credential_file_mounts(excluded_clouds)

    logging_agent = logs.get_logging_agent()
    if logging_agent:
        for k, v in logging_agent.get_credential_file_mounts().items():
            assert k not in credentials, f'{k} already in credentials'
            credentials[k] = v

    private_key_path, _ = auth_utils.get_or_generate_keys()
    auth_config = {'ssh_private_key': private_key_path}
    region_name = resources_vars.get('region')

    yaml_path = _get_yaml_path_from_cluster_name(cluster_name)

    # Retrieve the ssh_proxy_command for the given cloud / region.
    ssh_proxy_command_config = skypilot_config.get_effective_region_config(
        cloud=str(cloud).lower(),
        region=None,
        keys=('ssh_proxy_command',),
        default_value=None)
    if (isinstance(ssh_proxy_command_config, str) or
            ssh_proxy_command_config is None):
        ssh_proxy_command = ssh_proxy_command_config
    else:
        # ssh_proxy_command_config: Dict[str, str], region_name -> command
        # This type check is done by skypilot_config at config load time.

        # There are two cases:
        if keep_launch_fields_in_existing_config:
            # (1) We're re-provisioning an existing cluster.
            #
            # We use None for ssh_proxy_command, which will be restored to the
            # cluster's original value later by _replace_yaml_dicts().
            ssh_proxy_command = None
        else:
            # (2) We're launching a new cluster.
            #
            # Resources.get_valid_regions_for_launchable() respects the keys (regions)
            # in ssh_proxy_command in skypilot_config. So here we add an assert.
            assert region_name in ssh_proxy_command_config, (
                region_name, ssh_proxy_command_config)
            ssh_proxy_command = ssh_proxy_command_config[region_name]

    use_internal_ips = skypilot_config.get_effective_region_config(
        cloud=str(cloud).lower(),
        region=region.name,
        keys=('use_internal_ips',),
        default_value=False)
    if isinstance(cloud, clouds.AWS):
        # If the use_ssm flag is set to true, we use the ssm proxy command.
        use_ssm = skypilot_config.get_effective_region_config(
            cloud=str(cloud).lower(),
            region=region.name,
            keys=('use_ssm',),
            default_value=None)

        if use_ssm and ssh_proxy_command is not None:
            raise exceptions.InvalidCloudConfigs(
                'use_ssm is set to true, but ssh_proxy_command '
                f'is already set to {ssh_proxy_command!r}. Please remove '
                'ssh_proxy_command or set use_ssm to false.')

        if use_internal_ips and ssh_proxy_command is None:
            # Only if use_ssm is explicitly not set, we default to using SSM.
            if use_ssm is None:
                logger.warning(
                    f'{colorama.Fore.YELLOW}'
                    'use_internal_ips is set to true, '
                    'but ssh_proxy_command is not set. Defaulting to '
                    'using SSM. Specify ssh_proxy_command to use a different '
                    'https://docs.skypilot.co/en/latest/reference/config.html#'
                    f'aws.ssh_proxy_command.{colorama.Style.RESET_ALL}')
                use_ssm = True

        if use_ssm:
            aws_profile = os.environ.get('AWS_PROFILE', None)
            profile_str = f'--profile {aws_profile}' if aws_profile else ''
            ip_address_filter = ('Name=private-ip-address,Values=%h'
                                 if use_internal_ips else
                                 'Name=ip-address,Values=%h')
            get_instance_id_command = 'aws ec2 describe-instances ' + \
                f'--region {region_name} --filters {ip_address_filter} ' + \
                '--query \"Reservations[].Instances[].InstanceId\" ' + \
                f'{profile_str} --output text'
            ssm_proxy_command = 'aws ssm start-session --target ' + \
                f'\"$({get_instance_id_command})\" ' + \
                f'--region {region_name} {profile_str} ' + \
                '--document-name AWS-StartSSHSession ' + \
                '--parameters portNumber=%p'
            ssh_proxy_command = ssm_proxy_command
            region_name = 'ssm-session'
    logger.debug(f'Using ssh_proxy_command: {ssh_proxy_command!r}')

    # User-supplied global instance tags from ~/.sky/config.yaml.
    labels = skypilot_config.get_effective_region_config(
        cloud=str(cloud).lower(),
        region=region.name,
        keys=('labels',),
        default_value={})
    # labels is a dict, which is guaranteed by the type check in
    # schemas.py
    assert isinstance(labels, dict), labels

    # Get labels from resources and override from the labels to_provision.
    if to_provision.labels:
        labels.update(to_provision.labels)

    install_conda = skypilot_config.get_nested(('provision', 'install_conda'),
                                               True)

    # We disable conda auto-activation if the user has specified a docker image
    # to use, which is likely to already have a conda environment activated.
    conda_auto_activate = ('true' if to_provision.extract_docker_image() is None
                           else 'false')
    is_custom_docker = ('true' if to_provision.extract_docker_image()
                        is not None else 'false')

    # Check if the cluster name is a controller name.
    is_remote_controller = False
    controller = controller_utils.Controllers.from_name(
        cluster_name, expect_exact_match=False)
    if controller is not None:
        is_remote_controller = True

    # Here, if users specify the controller to be high availability, we will
    # provision a high availability controller. Whether the cloud supports
    # this feature has been checked by
    # CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS
    high_availability_specified = controller_utils.high_availability_specified(
        cluster_name)

    volume_mount_vars = []
    ephemeral_volume_mount_vars = []
    if volume_mounts is not None:
        for vol in volume_mounts:
            if vol.is_ephemeral:
                volume_name = _get_volume_name(vol.path, cluster_name_on_cloud)
                vol.volume_name = volume_name
                vol.volume_config.cloud = repr(cloud)
                vol.volume_config.region = region.name
                vol.volume_config.name = volume_name
                ephemeral_volume_mount_vars.append(vol.to_yaml_config())
            else:
                volume_info = volume_utils.VolumeInfo(
                    name=vol.volume_name,
                    path=vol.path,
                    volume_name_on_cloud=vol.volume_config.name_on_cloud,
                    volume_id_on_cloud=vol.volume_config.id_on_cloud,
                )
                volume_mount_vars.append(volume_info)

    runcmd = skypilot_config.get_effective_region_config(
        cloud=str(to_provision.cloud).lower(),
        region=to_provision.region,
        keys=('post_provision_runcmd',),
        default_value=None)

    # Use a tmp file path to avoid incomplete YAML file being re-used in the
    # future.
    tmp_yaml_path = yaml_path + '.tmp'
    variables = dict(
        resources_vars,
        **{
            'cluster_name_on_cloud': cluster_name_on_cloud,
            'num_nodes': num_nodes,
            'disk_size': to_provision.disk_size,
            # If the current code is run by controller, propagate the real
            # calling user which should've been passed in as the
            # SKYPILOT_USER env var (see
            # controller_utils.shared_controller_vars_to_fill().
            'user': common_utils.get_cleaned_username(
                os.environ.get(constants.USER_ENV_VAR, '')),

            # Networking configs
            'use_internal_ips': skypilot_config.get_effective_region_config(
                cloud=str(cloud).lower(),
                region=region.name,
                keys=('use_internal_ips',),
                default_value=False),
            'ssh_proxy_command': ssh_proxy_command,
            # TODO (kyuds): for backwards compatibility. If `vpc_names`
            # is set, this will be overridden. We can remove this after
            # v0.13.0 if all clouds that currently support `vpc_name`
            # migrates to `vpc_names` (ie: gcp)
            'vpc_name': skypilot_config.get_effective_region_config(
                cloud=str(cloud).lower(),
                region=region.name,
                keys=('vpc_name',),
                default_value=None),
            # User-supplied labels.
            'labels': labels,
            # User-supplied remote_identity
            'remote_identity': remote_identity,
            # The reservation pools that specified by the user. This is
            # currently only used by AWS and GCP.
            'specific_reservations': specific_reservations,

            # Conda setup
            # We should not use `.format`, as it contains '{}' as the bash
            # syntax.
            'conda_installation_commands':
                constants.CONDA_INSTALLATION_COMMANDS.replace(
                    '{conda_auto_activate}', conda_auto_activate).replace(
                        '{is_custom_docker}', is_custom_docker)
                if install_conda else '',
            # UV setup
            'uv_installation_commands': constants.UV_INSTALLATION_COMMANDS,
            # Currently only used by Slurm. For other clouds, it is
            # already part of ray_skypilot_installation_commands
            'setup_sky_dirs_commands': constants.SETUP_SKY_DIRS_COMMANDS,
            'ray_skypilot_installation_commands':
                (constants.RAY_SKYPILOT_INSTALLATION_COMMANDS.replace(
                    '{sky_wheel_hash}',
                    wheel_hash).replace('{cloud}',
                                        str(cloud).lower())),
            'skypilot_wheel_installation_commands':
                constants.SKYPILOT_WHEEL_INSTALLATION_COMMANDS.replace(
                    '{sky_wheel_hash}',
                    wheel_hash).replace('{cloud}',
                                        str(cloud).lower()),
            'copy_skypilot_templates_commands':
                constants.COPY_SKYPILOT_TEMPLATES_COMMANDS,
            # Port of Ray (GCS server).
            # Ray's default port 6379 is conflicted with Redis.
            'ray_port': constants.SKY_REMOTE_RAY_PORT,
            'ray_dashboard_port': constants.SKY_REMOTE_RAY_DASHBOARD_PORT,
            'ray_temp_dir': constants.SKY_REMOTE_RAY_TEMPDIR,
            'dump_port_command': instance_setup.DUMP_RAY_PORTS,
            # Sky-internal constants.
            'sky_ray_cmd': constants.SKY_RAY_CMD,
            # pip install needs to have python env activated to make sure
            # installed packages are within the env path.
            'sky_pip_cmd': f'{constants.SKY_PIP_CMD}',
            # Activate the SkyPilot runtime environment when starting ray
            # cluster, so that ray autoscaler can access cloud SDK and CLIs
            # on remote
            'sky_activate_python_env': constants.ACTIVATE_SKY_REMOTE_PYTHON_ENV,
            'ray_version': constants.SKY_REMOTE_RAY_VERSION,
            # Command for waiting ray cluster to be ready on head.
            'ray_head_wait_initialized_command':
                instance_setup.RAY_HEAD_WAIT_INITIALIZED_COMMAND,

            # Cloud credentials for cloud storage.
            'credentials': credentials,
            # Sky remote utils.
            'sky_remote_path': SKY_REMOTE_PATH,
            'sky_local_path': str(local_wheel_path),
            # Add yaml file path to the template variables.
            'sky_ray_yaml_remote_path':
                cluster_utils.SKY_CLUSTER_YAML_REMOTE_PATH,
            'sky_ray_yaml_local_path': tmp_yaml_path,
            'sky_version': str(version.parse(sky.__version__)),
            'sky_wheel_hash': wheel_hash,
            'ssh_max_sessions_config':
                constants.SET_SSH_MAX_SESSIONS_CONFIG_CMD,
            # Authentication (optional).
            **auth_config,

            # Controller specific configs
            'is_remote_controller': is_remote_controller,
            'high_availability': high_availability_specified,

            # Volume mounts
            'volume_mounts': volume_mount_vars,
            'ephemeral_volume_mounts': ephemeral_volume_mount_vars,

            # runcmd to run before any of the SkyPilot runtime setup commands.
            # This is currently only used by AWS and Kubernetes.
            'runcmd': runcmd,
        },
    )
    if cloud_specific_failover_overrides is not None:
        variables.update(cloud_specific_failover_overrides)
    common_utils.fill_template(cluster_config_template,
                               variables,
                               output_path=tmp_yaml_path)
    config_dict['cluster_name'] = cluster_name
    config_dict['ray'] = yaml_path

    # Add kubernetes config fields from ~/.sky/config
    if isinstance(cloud, clouds.Kubernetes):
        cluster_config_overrides = to_provision.cluster_config_overrides
        with open(tmp_yaml_path, 'r', encoding='utf-8') as f:
            tmp_yaml_str = f.read()
        cluster_yaml_obj = yaml_utils.safe_load(tmp_yaml_str)
        combined_yaml_obj = kubernetes_utils.combine_pod_config_fields_and_metadata(
            cluster_yaml_obj,
            cluster_config_overrides=cluster_config_overrides,
            cloud=cloud,
            context=region.name)
        # Write the updated YAML back to the file
        yaml_utils.dump_yaml(tmp_yaml_path, combined_yaml_obj)

        pod_config: Dict[str, Any] = combined_yaml_obj['available_node_types'][
            'ray_head_default']['node_config']
        # Check pod spec only. For high availability controllers, we deploy pvc & deployment for the controller. Read kubernetes-ray.yml.j2 for more details.
        pod_config.pop('deployment_spec', None)
        pod_config.pop('pvc_spec', None)
        valid, message = kubernetes_utils.check_pod_config(pod_config)
        if not valid:
            raise exceptions.InvalidCloudConfigs(
                f'Invalid pod_config. Details: {message}')

    if dryrun:
        # If dryrun, return the unfinished tmp yaml path.
        config_dict['ray'] = tmp_yaml_path
        try:
            config_dict['config_hash'] = _deterministic_cluster_yaml_hash(
                tmp_yaml_path)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to calculate config_hash: {e}')
            logger.debug('Full exception:', exc_info=e)
        return config_dict
    _add_auth_to_cluster_config(cloud, tmp_yaml_path)

    # Restore the old yaml content for backward compatibility.
    old_yaml_content = global_user_state.get_cluster_yaml_str(yaml_path)
    if old_yaml_content is not None and keep_launch_fields_in_existing_config:
        with open(tmp_yaml_path, 'r', encoding='utf-8') as f:
            new_yaml_content = f.read()
        restored_yaml_content = _replace_yaml_dicts(
            new_yaml_content, old_yaml_content,
            _RAY_YAML_KEYS_TO_RESTORE_FOR_BACK_COMPATIBILITY,
            _RAY_YAML_KEYS_TO_RESTORE_EXCEPTIONS)
        with open(tmp_yaml_path, 'w', encoding='utf-8') as f:
            f.write(restored_yaml_content)

    # Read the cluster_name_on_cloud from the restored yaml. This is a hack to
    # make sure that launching on the same cluster across multiple users works
    # correctly. See #8232.
    yaml_config = yaml_utils.read_yaml(tmp_yaml_path)
    config_dict['cluster_name_on_cloud'] = yaml_config['cluster_name']

    # Make sure to do this before we optimize file mounts. Optimization is
    # non-deterministic, but everything else before this point should be
    # deterministic.
    try:
        config_dict['config_hash'] = _deterministic_cluster_yaml_hash(
            tmp_yaml_path)
    except Exception as e:  # pylint: disable=broad-except
        logger.warning('Failed to calculate config_hash: '
                       f'{common_utils.format_exception(e)}')
        logger.debug('Full exception:', exc_info=e)

    # Optimization: copy the contents of source files in file_mounts to a
    # special dir, and upload that as the only file_mount instead. Delay
    # calling this optimization until now, when all source files have been
    # written and their contents finalized.
    #
    # Note that the ray yaml file will be copied into that special dir (i.e.,
    # uploaded as part of the file_mounts), so the restore for backward
    # compatibility should go before this call.
    _optimize_file_mounts(tmp_yaml_path)

    # commit the final yaml to the database
    global_user_state.set_cluster_yaml(
        cluster_name,
        open(tmp_yaml_path, 'r', encoding='utf-8').read())

    usage_lib.messages.usage.update_ray_yaml(tmp_yaml_path)

    # Remove the tmp file.
    if sky_logging.logging_enabled(logger, sky_logging.DEBUG):
        debug_yaml_path = yaml_path + '.debug'
        os.rename(tmp_yaml_path, debug_yaml_path)
    else:
        os.remove(tmp_yaml_path)

    return config_dict


def _add_auth_to_cluster_config(cloud: clouds.Cloud, tmp_yaml_path: str):
    """Adds SSH key info to the cluster config.

    This function's output removes comments included in the jinja2 template.
    """
    config = yaml_utils.read_yaml(tmp_yaml_path)
    # Check the availability of the cloud type.
    if isinstance(
            cloud,
        (
            clouds.AWS,
            clouds.OCI,
            clouds.SCP,
            # TODO(jwj): Handle Slurm-specific auth logic
            clouds.Slurm,
            clouds.Vsphere,
            clouds.Cudo,
            clouds.Paperspace,
            clouds.Azure,
            clouds.DO,
            clouds.Nebius,
            clouds.Yotta,
        )):
        config = auth.configure_ssh_info(config)
    elif isinstance(cloud, clouds.GCP):
        config = auth.setup_gcp_authentication(config)
    elif isinstance(cloud, clouds.Lambda):
        config = auth.setup_lambda_authentication(config)
    elif isinstance(cloud, clouds.Kubernetes):
        config = auth.setup_kubernetes_authentication(config)
    elif isinstance(cloud, clouds.IBM):
        config = auth.setup_ibm_authentication(config)
    elif isinstance(cloud, clouds.RunPod):
        config = auth.setup_runpod_authentication(config)
    elif isinstance(cloud, clouds.Vast):
        config = auth.setup_vast_authentication(config)
    elif isinstance(cloud, clouds.Fluidstack):
        config = auth.setup_fluidstack_authentication(config)
    elif isinstance(cloud, clouds.Hyperbolic):
        config = auth.setup_hyperbolic_authentication(config)
    elif isinstance(cloud, clouds.Shadeform):
        config = auth.setup_shadeform_authentication(config)
    elif isinstance(cloud, clouds.PrimeIntellect):
        config = auth.setup_primeintellect_authentication(config)
    elif isinstance(cloud, clouds.Seeweb):
        config = auth.setup_seeweb_authentication(config)
    else:
        assert False, cloud
    yaml_utils.dump_yaml(tmp_yaml_path, config)


def get_timestamp_from_run_timestamp(run_timestamp: str) -> float:
    return datetime.strptime(
        run_timestamp.partition('-')[2], '%Y-%m-%d-%H-%M-%S-%f').timestamp()


def _count_healthy_nodes_from_ray(output: str,
                                  is_local_cloud: bool = False
                                 ) -> Tuple[int, int]:
    """Count the number of healthy nodes from the output of `ray status`."""

    def get_ready_nodes_counts(pattern, output):
        result = pattern.findall(output)
        if not result:
            return 0
        assert len(result) == 1, result
        return int(result[0])

    # Check if the ray cluster is started with ray autoscaler. In new
    # provisioner (#1702) and local mode, we started the ray cluster without ray
    # autoscaler.
    # If ray cluster is started with ray autoscaler, the output will be:
    #  1 ray.head.default
    #  ...
    # TODO(zhwu): once we deprecate the old provisioner, we can remove this
    # check.
    ray_autoscaler_head = get_ready_nodes_counts(_LAUNCHED_HEAD_PATTERN, output)
    is_local_ray_cluster = ray_autoscaler_head == 0

    if is_local_ray_cluster or is_local_cloud:
        # Ray cluster is launched with new provisioner
        # For new provisioner and local mode, the output will be:
        #  1 node_xxxx
        #  1 node_xxxx
        ready_head = 0
        ready_workers = _LAUNCHED_LOCAL_WORKER_PATTERN.findall(output)
        ready_workers = len(ready_workers)
        if is_local_ray_cluster:
            ready_head = 1
            ready_workers -= 1
        return ready_head, ready_workers

    # Count number of nodes by parsing the output of `ray status`. The output
    # looks like:
    #   1 ray.head.default
    #   2 ray.worker.default
    ready_head = ray_autoscaler_head
    ready_workers = get_ready_nodes_counts(_LAUNCHED_WORKER_PATTERN, output)
    ready_reserved_workers = get_ready_nodes_counts(
        _LAUNCHED_RESERVED_WORKER_PATTERN, output)
    ready_workers += ready_reserved_workers
    assert ready_head <= 1, f'#head node should be <=1 (Got {ready_head}).'
    return ready_head, ready_workers


@timeline.event
def _deterministic_cluster_yaml_hash(tmp_yaml_path: str) -> str:
    """Hash the cluster yaml and contents of file mounts to a unique string.

    Two invocations of this function should return the same string if and only
    if the contents of the yaml are the same and the file contents of all the
    file_mounts specified in the yaml are the same.

    Limitations:
    - This function can be expensive if the file mounts are large. (E.g. a few
      seconds for ~1GB.) This should be okay since we expect that the
      file_mounts in the cluster yaml (the wheel and cloud credentials) will be
      small.
    - Symbolic links are not explicitly handled. Some symbolic link changes may
      not be detected.

    Implementation: We create a byte sequence that captures the state of the
    yaml file and all the files in the file mounts, then hash the byte sequence.

    The format of the byte sequence is:
    32 bytes - sha256 hash of the yaml
    for each file mount:
      file mount remote destination (UTF-8), \0
      if the file mount source is a file:
        'file' encoded to UTF-8
        32 byte sha256 hash of the file contents
      if the file mount source is a directory:
        'dir' encoded to UTF-8
        for each directory and subdirectory withinin the file mount (starting from
            the root and descending recursively):
          name of the directory (UTF-8), \0
          name of each subdirectory within the directory (UTF-8) terminated by \0
          \0
          for each file in the directory:
            name of the file (UTF-8), \0
            32 bytes - sha256 hash of the file contents
          \0
      if the file mount source is something else or does not exist, nothing
      \0\0

    Rather than constructing the whole byte sequence, which may be quite large,
    we construct it incrementally by using hash.update() to add new bytes.
    """
    # Load the yaml contents so that we can directly remove keys.
    yaml_config = yaml_utils.read_yaml(tmp_yaml_path)
    for key_list in _RAY_YAML_KEYS_TO_REMOVE_FOR_HASH:
        dict_to_remove_from = yaml_config
        found_key = True
        for key in key_list[:-1]:
            if (not isinstance(dict_to_remove_from, dict) or
                    key not in dict_to_remove_from):
                found_key = False
                break
            dict_to_remove_from = dict_to_remove_from[key]
        if found_key and key_list[-1] in dict_to_remove_from:
            dict_to_remove_from.pop(key_list[-1])

    def _hash_file(path: str) -> bytes:
        return common_utils.hash_file(path, 'sha256').digest()

    config_hash = hashlib.sha256()

    yaml_hash = hashlib.sha256(
        yaml_utils.dump_yaml_str(yaml_config).encode('utf-8'))
    config_hash.update(yaml_hash.digest())

    file_mounts = yaml_config.get('file_mounts', {})
    # Remove the file mounts added by the newline.
    if '' in file_mounts:
        assert file_mounts[''] == '', file_mounts['']
        file_mounts.pop('')

    for dst, src in sorted(file_mounts.items()):
        if src == tmp_yaml_path:
            # Skip the yaml file itself. We have already hashed a modified
            # version of it. The file may include fields we don't want to hash.
            continue

        expanded_src = os.path.expanduser(src)
        config_hash.update(dst.encode('utf-8') + b'\0')

        # If the file mount source is a symlink, this should be true. In that
        # case we hash the contents of the symlink destination.
        if os.path.isfile(expanded_src):
            config_hash.update('file'.encode('utf-8'))
            config_hash.update(_hash_file(expanded_src))

        # This can also be a symlink to a directory. os.walk will treat it as a
        # normal directory and list the contents of the symlink destination.
        elif os.path.isdir(expanded_src):
            config_hash.update('dir'.encode('utf-8'))

            # Aside from expanded_src, os.walk will list symlinks to directories
            # but will not recurse into them.
            for (dirpath, dirnames, filenames) in os.walk(expanded_src):
                config_hash.update(dirpath.encode('utf-8') + b'\0')

                # Note: inplace sort will also affect the traversal order of
                # os.walk. We need it so that the os.walk order is
                # deterministic.
                dirnames.sort()
                # This includes symlinks to directories. os.walk will recurse
                # into all the directories but not the symlinks. We don't hash
                # the link destination, so if a symlink to a directory changes,
                # we won't notice.
                for dirname in dirnames:
                    config_hash.update(dirname.encode('utf-8') + b'\0')
                config_hash.update(b'\0')

                filenames.sort()
                # This includes symlinks to files. We could hash the symlink
                # destination itself but instead just hash the destination
                # contents.
                for filename in filenames:
                    config_hash.update(filename.encode('utf-8') + b'\0')
                    config_hash.update(
                        _hash_file(os.path.join(dirpath, filename)))
                config_hash.update(b'\0')

        else:
            logger.debug(
                f'Unexpected file_mount that is not a file or dir: {src}')

        config_hash.update(b'\0\0')

    return config_hash.hexdigest()


def get_docker_user(ip: str, cluster_config_file: str) -> str:
    """Find docker container username."""
    ssh_credentials = ssh_credential_from_yaml(cluster_config_file)
    runner = command_runner.SSHCommandRunner(node=(ip, 22), **ssh_credentials)
    container_name = constants.DEFAULT_DOCKER_CONTAINER_NAME
    whoami_returncode, whoami_stdout, whoami_stderr = runner.run(
        f'sudo docker exec {container_name} whoami',
        stream_logs=False,
        require_outputs=True)
    assert whoami_returncode == 0, (
        f'Failed to get docker container user. Return '
        f'code: {whoami_returncode}, Error: {whoami_stderr}')
    docker_user = whoami_stdout.strip()
    logger.debug(f'Docker container user: {docker_user}')
    return docker_user


@timeline.event
def wait_until_ray_cluster_ready(
    cluster_config_file: str,
    num_nodes: int,
    log_path: str,
    is_local_cloud: bool = False,
    nodes_launching_progress_timeout: Optional[int] = None,
) -> Tuple[bool, Optional[str]]:
    """Wait until the ray cluster is set up on VMs or in containers.

    Returns:  whether the entire ray cluster is ready, and docker username
    if launched with docker.
    """
    # Manually fetching head ip instead of using `ray exec` to avoid the bug
    # that `ray exec` fails to connect to the head node after some workers
    # launched especially for Azure.
    try:
        head_ip = _query_head_ip_with_retries(
            cluster_config_file, max_attempts=WAIT_HEAD_NODE_IP_MAX_ATTEMPTS)
    except exceptions.FetchClusterInfoError as e:
        logger.error(common_utils.format_exception(e))
        return False, None  # failed

    config = global_user_state.get_cluster_yaml_dict(cluster_config_file)

    docker_user = None
    if 'docker' in config:
        docker_user = get_docker_user(head_ip, cluster_config_file)

    if num_nodes <= 1:
        return True, docker_user

    ssh_credentials = ssh_credential_from_yaml(cluster_config_file, docker_user)
    last_nodes_so_far = 0
    start = time.time()
    runner = command_runner.SSHCommandRunner(node=(head_ip, 22),
                                             **ssh_credentials)
    with rich_utils.safe_status(
            ux_utils.spinner_message('Waiting for workers',
                                     log_path=log_path)) as worker_status:
        while True:
            rc, output, stderr = runner.run(
                instance_setup.RAY_STATUS_WITH_SKY_RAY_PORT_COMMAND,
                log_path=log_path,
                stream_logs=False,
                require_outputs=True,
                separate_stderr=True)
            subprocess_utils.handle_returncode(
                rc, instance_setup.RAY_STATUS_WITH_SKY_RAY_PORT_COMMAND,
                'Failed to run ray status on head node.', stderr)
            logger.debug(output)

            ready_head, ready_workers = _count_healthy_nodes_from_ray(
                output, is_local_cloud=is_local_cloud)

            worker_status.update(
                ux_utils.spinner_message(
                    f'{ready_workers} out of {num_nodes - 1} '
                    'workers ready',
                    log_path=log_path))

            # In the local case, ready_head=0 and ready_workers=num_nodes. This
            # is because there is no matching regex for _LAUNCHED_HEAD_PATTERN.
            if ready_head + ready_workers == num_nodes:
                # All nodes are up.
                break

            # Pending workers that have been launched by ray up.
            found_ips = _LAUNCHING_IP_PATTERN.findall(output)
            pending_workers = len(found_ips)

            # TODO(zhwu): Handle the case where the following occurs, where ray
            # cluster is not correctly started on the cluster.
            # Pending:
            #  172.31.9.121: ray.worker.default, uninitialized
            nodes_so_far = ready_head + ready_workers + pending_workers

            # Check the number of nodes that are fetched. Timeout if no new
            # nodes fetched in a while (nodes_launching_progress_timeout),
            # though number of nodes_so_far is still not as expected.
            if nodes_so_far > last_nodes_so_far:
                # Reset the start time if the number of launching nodes
                # changes, i.e. new nodes are launched.
                logger.debug('Reset start time, as new nodes are launched. '
                             f'({last_nodes_so_far} -> {nodes_so_far})')
                start = time.time()
                last_nodes_so_far = nodes_so_far
            elif (nodes_launching_progress_timeout is not None and
                  time.time() - start > nodes_launching_progress_timeout and
                  nodes_so_far != num_nodes):
                logger.error(
                    'Timed out: waited for more than '
                    f'{nodes_launching_progress_timeout} seconds for new '
                    'workers to be provisioned, but no progress.')
                return False, None  # failed

            if '(no pending nodes)' in output and '(no failures)' in output:
                # Bug in ray autoscaler: e.g., on GCP, if requesting 2 nodes
                # that GCP can satisfy only by half, the worker node would be
                # forgotten. The correct behavior should be for it to error out.
                logger.error(
                    'Failed to launch multiple nodes on '
                    'GCP due to a nondeterministic bug in ray autoscaler.')
                return False, None  # failed
            time.sleep(10)
    return True, docker_user  # success


def ssh_credential_from_yaml(
    cluster_yaml: Optional[str],
    docker_user: Optional[str] = None,
    ssh_user: Optional[str] = None,
) -> Dict[str, Any]:
    """Returns ssh_user, ssh_private_key and ssh_control name.

    Args:
        cluster_yaml: path to the cluster yaml.
        docker_user: when using custom docker image, use this user to ssh into
            the docker container.
        ssh_user: override the ssh_user in the cluster yaml.
    """
    if cluster_yaml is None:
        return dict()
    config = global_user_state.get_cluster_yaml_dict(cluster_yaml)
    auth_section = config['auth']
    if ssh_user is None:
        ssh_user = auth_section['ssh_user'].strip()
    ssh_private_key_path = auth_section.get('ssh_private_key')
    ssh_control_name = config.get('cluster_name', '__default__')
    ssh_proxy_command = auth_section.get('ssh_proxy_command')

    # Update the ssh_user placeholder in proxy command, if required
    if (ssh_proxy_command is not None and
            constants.SKY_SSH_USER_PLACEHOLDER in ssh_proxy_command):
        ssh_proxy_command = ssh_proxy_command.replace(
            constants.SKY_SSH_USER_PLACEHOLDER, ssh_user)

    credentials = {
        'ssh_user': ssh_user,
        'ssh_private_key': ssh_private_key_path,
        'ssh_control_name': ssh_control_name,
        'ssh_proxy_command': ssh_proxy_command,
    }
    if docker_user is not None:
        credentials['docker_user'] = docker_user
    ssh_provider_module = config['provider']['module']
    # If we are running ssh command on kubernetes node.
    if 'kubernetes' in ssh_provider_module:
        credentials['disable_control_master'] = True
    return credentials


def ssh_credentials_from_handles(
    handles: List['cloud_vm_ray_backend.CloudVmRayResourceHandle'],
) -> List[Dict[str, Any]]:
    """Returns ssh_user, ssh_private_key and ssh_control name.
    """
    non_empty_cluster_yaml_paths = [
        handle.cluster_yaml
        for handle in handles
        if handle.cluster_yaml is not None
    ]
    cluster_yaml_dicts = global_user_state.get_cluster_yaml_dict_multiple(
        non_empty_cluster_yaml_paths)
    cluster_yaml_dicts_to_index = {
        cluster_yaml_path: cluster_yaml_dict
        for cluster_yaml_path, cluster_yaml_dict in zip(
            non_empty_cluster_yaml_paths, cluster_yaml_dicts)
    }

    credentials_to_return: List[Dict[str, Any]] = []
    for handle in handles:
        if handle.cluster_yaml is None:
            credentials_to_return.append(dict())
            continue
        ssh_user = handle.ssh_user
        docker_user = handle.docker_user
        config = cluster_yaml_dicts_to_index[handle.cluster_yaml]
        auth_section = config['auth']
        if ssh_user is None:
            ssh_user = auth_section['ssh_user'].strip()
        ssh_private_key_path = auth_section.get('ssh_private_key')
        ssh_control_name = config.get('cluster_name', '__default__')
        ssh_proxy_command = auth_section.get('ssh_proxy_command')

        # Update the ssh_user placeholder in proxy command, if required
        if (ssh_proxy_command is not None and
                constants.SKY_SSH_USER_PLACEHOLDER in ssh_proxy_command):
            ssh_proxy_command = ssh_proxy_command.replace(
                constants.SKY_SSH_USER_PLACEHOLDER, ssh_user)

        credentials = {
            'ssh_user': ssh_user,
            'ssh_private_key': ssh_private_key_path,
            'ssh_control_name': ssh_control_name,
            'ssh_proxy_command': ssh_proxy_command,
        }
        if docker_user is not None:
            credentials['docker_user'] = docker_user
        ssh_provider_module = config['provider']['module']
        # If we are running ssh command on kubernetes node.
        if 'kubernetes' in ssh_provider_module:
            credentials['disable_control_master'] = True
        credentials_to_return.append(credentials)

    return credentials_to_return


def parallel_data_transfer_to_nodes(
        runners: List[command_runner.CommandRunner],
        source: Optional[str],
        target: str,
        cmd: Optional[str],
        run_rsync: bool,
        *,
        action_message: str,
        # Advanced options.
        log_path: str = os.devnull,
        stream_logs: bool = False,
        source_bashrc: bool = False,
        num_threads: Optional[int] = None):
    """Runs a command on all nodes and optionally runs rsync from src->dst.

    Args:
        runners: A list of CommandRunner objects that represent multiple nodes.
        source: Optional[str]; Source for rsync on local node
        target: str; Destination on remote node for rsync
        cmd: str; Command to be executed on all nodes
        action_message: str; Message to be printed while the command runs
        log_path: str; Path to the log file
        stream_logs: bool; Whether to stream logs to stdout
        source_bashrc: bool; Source bashrc before running the command.
        num_threads: Optional[int]; Number of threads to use.
    """
    style = colorama.Style

    origin_source = source

    def _sync_node(runner: 'command_runner.CommandRunner') -> None:
        if cmd is not None:
            rc, stdout, stderr = runner.run(cmd,
                                            log_path=log_path,
                                            stream_logs=stream_logs,
                                            require_outputs=True,
                                            source_bashrc=source_bashrc)
            err_msg = (f'{colorama.Style.RESET_ALL}{colorama.Style.DIM}'
                       f'----- CMD -----\n'
                       f'{cmd}\n'
                       f'----- CMD END -----\n'
                       f'{colorama.Style.RESET_ALL}'
                       f'{colorama.Fore.RED}'
                       f'Failed to run command before rsync '
                       f'{origin_source} -> {target}. '
                       f'{colorama.Style.RESET_ALL}')
            if log_path != os.devnull:
                err_msg += ux_utils.log_path_hint(log_path)
            subprocess_utils.handle_returncode(rc,
                                               cmd,
                                               err_msg,
                                               stderr=stdout + stderr)

        if run_rsync:
            assert source is not None
            # TODO(zhwu): Optimize for large amount of files.
            # zip / transfer / unzip
            runner.rsync(
                source=source,
                target=target,
                up=True,
                log_path=log_path,
                stream_logs=stream_logs,
            )

    num_nodes = len(runners)
    plural = 's' if num_nodes > 1 else ''
    message = (f'  {style.DIM}{action_message} (to {num_nodes} node{plural})'
               f': {origin_source} -> {target}{style.RESET_ALL}')
    logger.info(message)
    subprocess_utils.run_in_parallel(_sync_node, runners, num_threads)


def check_local_gpus() -> bool:
    """Checks if GPUs are available locally.

    Returns whether GPUs are available on the local machine by checking
    if nvidia-smi is installed and returns zero return code.

    Returns True if nvidia-smi is installed and returns zero return code,
    False if not.
    """
    is_functional = False
    installation_check = subprocess.run(['which', 'nvidia-smi'],
                                        stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL,
                                        check=False)
    is_installed = installation_check.returncode == 0
    if is_installed:
        execution_check = subprocess.run(['nvidia-smi'],
                                         stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL,
                                         check=False)
        is_functional = execution_check.returncode == 0
    return is_functional


def _query_head_ip_with_retries(cluster_yaml: str,
                                max_attempts: int = 1) -> str:
    """Returns the IP of the head node by querying the cloud.

    Raises:
      exceptions.FetchClusterInfoError: if we failed to get the head IP.
    """
    backoff = common_utils.Backoff(initial_backoff=5, max_backoff_factor=5)
    for i in range(max_attempts):
        try:
            full_cluster_yaml = str(pathlib.Path(cluster_yaml).expanduser())
            out = subprocess_utils.run(
                f'ray get-head-ip {full_cluster_yaml!r}',
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL).stdout.decode().strip()
            head_ip_list = re.findall(IP_ADDR_REGEX, out)
            if len(head_ip_list) > 1:
                # This could be triggered if e.g., some logging is added in
                # skypilot_config, a module that has some code executed
                # whenever `sky` is imported.
                logger.warning(
                    'Detected more than 1 IP from the output of '
                    'the `ray get-head-ip` command. This could '
                    'happen if there is extra output from it, '
                    'which should be inspected below.\nProceeding with '
                    f'the last detected IP ({head_ip_list[-1]}) as head IP.'
                    f'\n== Output ==\n{out}'
                    f'\n== Output ends ==')
                head_ip_list = head_ip_list[-1:]
            assert 1 == len(head_ip_list), (out, head_ip_list)
            head_ip = head_ip_list[0]
            break
        except subprocess.CalledProcessError as e:
            if i == max_attempts - 1:
                raise exceptions.FetchClusterInfoError(
                    reason=exceptions.FetchClusterInfoError.Reason.HEAD) from e
            # Retry if the cluster is not up yet.
            logger.debug('Retrying to get head ip.')
            time.sleep(backoff.current_backoff())
    return head_ip


@timeline.event
def get_node_ips(cluster_yaml: str,
                 expected_num_nodes: int,
                 head_ip_max_attempts: int = 1,
                 worker_ip_max_attempts: int = 1,
                 get_internal_ips: bool = False) -> List[str]:
    """Returns the IPs of all nodes in the cluster, with head node at front.

    Args:
        cluster_yaml: Path to the cluster yaml.
        expected_num_nodes: Expected number of nodes in the cluster.
        head_ip_max_attempts: Max attempts to get head ip.
        worker_ip_max_attempts: Max attempts to get worker ips.
        get_internal_ips: Whether to get internal IPs. When False, it is still
            possible to get internal IPs if the cluster does not have external
            IPs.

    Raises:
        exceptions.FetchClusterInfoError: if we failed to get the IPs. e.reason is
            HEAD or WORKER.
    """
    ray_config = global_user_state.get_cluster_yaml_dict(cluster_yaml)
    # Use the new provisioner for AWS.
    provider_name = cluster_utils.get_provider_name(ray_config)
    cloud = registry.CLOUD_REGISTRY.from_str(provider_name)
    assert cloud is not None, provider_name

    if cloud.PROVISIONER_VERSION >= clouds.ProvisionerVersion.SKYPILOT:
        try:
            metadata = provision_lib.get_cluster_info(
                provider_name, ray_config['provider'].get('region'),
                ray_config['cluster_name'], ray_config['provider'])
        except Exception as e:  # pylint: disable=broad-except
            # This could happen when the VM is not fully launched, and a user
            # is trying to terminate it with `sky down`.
            logger.debug(
                'Failed to get cluster info for '
                f'{ray_config["cluster_name"]} from the new provisioner '
                f'with {common_utils.format_exception(e)}.')
            raise exceptions.FetchClusterInfoError(
                exceptions.FetchClusterInfoError.Reason.HEAD) from e
        if len(metadata.instances) < expected_num_nodes:
            # Simulate the exception when Ray head node is not up.
            raise exceptions.FetchClusterInfoError(
                exceptions.FetchClusterInfoError.Reason.HEAD)
        return metadata.get_feasible_ips(get_internal_ips)

    if get_internal_ips:
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            ray_config['provider']['use_internal_ips'] = True
            yaml.dump(ray_config, f)
            cluster_yaml = f.name

    # Check the network connection first to avoid long hanging time for
    # ray get-head-ip below, if a long-lasting network connection failure
    # happens.
    check_network_connection()
    head_ip = _query_head_ip_with_retries(cluster_yaml,
                                          max_attempts=head_ip_max_attempts)
    head_ip_list = [head_ip]
    if expected_num_nodes > 1:
        backoff = common_utils.Backoff(initial_backoff=5, max_backoff_factor=5)

        for retry_cnt in range(worker_ip_max_attempts):
            try:
                full_cluster_yaml = str(pathlib.Path(cluster_yaml).expanduser())
                proc = subprocess_utils.run(
                    f'ray get-worker-ips {full_cluster_yaml!r}',
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE)
                out = proc.stdout.decode()
                break
            except subprocess.CalledProcessError as e:
                if retry_cnt == worker_ip_max_attempts - 1:
                    raise exceptions.FetchClusterInfoError(
                        exceptions.FetchClusterInfoError.Reason.WORKER) from e
                # Retry if the ssh is not ready for the workers yet.
                backoff_time = backoff.current_backoff()
                logger.debug('Retrying to get worker ip '
                             f'[{retry_cnt}/{worker_ip_max_attempts}] in '
                             f'{backoff_time} seconds.')
                time.sleep(backoff_time)
        worker_ips = re.findall(IP_ADDR_REGEX, out)
        if len(worker_ips) != expected_num_nodes - 1:
            n = expected_num_nodes - 1
            if len(worker_ips) > n:
                # This could be triggered if e.g., some logging is added in
                # skypilot_config, a module that has some code executed whenever
                # `sky` is imported.
                logger.warning(
                    f'Expected {n} worker IP(s); found '
                    f'{len(worker_ips)}: {worker_ips}'
                    '\nThis could happen if there is extra output from '
                    '`ray get-worker-ips`, which should be inspected below.'
                    f'\n== Output ==\n{out}'
                    f'\n== Output ends ==')
                logger.warning(f'\nProceeding with the last {n} '
                               f'detected IP(s): {worker_ips[-n:]}.')
                worker_ips = worker_ips[-n:]
            else:
                raise exceptions.FetchClusterInfoError(
                    exceptions.FetchClusterInfoError.Reason.WORKER)
    else:
        worker_ips = []
    return head_ip_list + worker_ips


def check_network_connection():
    # Tolerate 3 retries as it is observed that connections can fail.
    http = requests.Session()
    http.mount('https://', adapters.HTTPAdapter())
    http.mount('http://', adapters.HTTPAdapter())

    # Alternate between IPs on each retry
    max_retries = 3
    timeout = 0.5

    for _ in range(max_retries):
        for ip in _TEST_IP_LIST:
            try:
                http.head(ip, timeout=timeout)
                return
            except (requests.Timeout, requests.exceptions.ConnectionError):
                continue

        timeout *= 2  # Double the timeout for next retry

    # If we get here, all IPs failed
    # Assume network connection is down
    raise exceptions.NetworkError('Could not refresh the cluster. '
                                  'Network seems down.')


async def async_check_network_connection():
    """Check if the network connection is available.

    Tolerates 3 retries as it is observed that connections can fail.
    Uses aiohttp for async HTTP requests.
    """
    # Create a session with retry logic
    timeout = ClientTimeout(total=15)
    connector = TCPConnector(limit=1)  # Limit to 1 connection at a time

    async with aiohttp.ClientSession(timeout=timeout,
                                     connector=connector) as session:
        for i, ip in enumerate(_TEST_IP_LIST):
            try:
                async with session.head(ip) as response:
                    if response.status < 400:  # Any 2xx or 3xx status is good
                        return
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if i == len(_TEST_IP_LIST) - 1:
                    raise exceptions.NetworkError(
                        'Could not refresh the cluster. '
                        'Network seems down.') from e
                # If not the last IP, continue to try the next one
                continue


@timeline.event
def check_owner_identity(cluster_name: str) -> None:
    """Check if current user is the same as the user who created the cluster.

    Raises:
        exceptions.ClusterOwnerIdentityMismatchError: if the current user is
          not the same as the user who created the cluster.
        exceptions.CloudUserIdentityError: if we fail to get the current user
          identity.
    """
    if env_options.Options.SKIP_CLOUD_IDENTITY_CHECK.get():
        return
    record = global_user_state.get_cluster_from_name(cluster_name,
                                                     include_user_info=False,
                                                     summary_response=True)
    if record is None:
        return
    _check_owner_identity_with_record(cluster_name, record)


def _check_owner_identity_with_record(cluster_name: str,
                                      record: Dict[str, Any]) -> None:
    if env_options.Options.SKIP_CLOUD_IDENTITY_CHECK.get():
        return
    handle = record['handle']
    if not isinstance(handle, backends.CloudVmRayResourceHandle):
        return
    active_workspace = skypilot_config.get_active_workspace()
    cluster_workspace = record.get('workspace',
                                   constants.SKYPILOT_DEFAULT_WORKSPACE)
    if active_workspace != cluster_workspace:
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ClusterOwnerIdentityMismatchError(
                f'{colorama.Fore.YELLOW}'
                f'The cluster {cluster_name!r} is in workspace '
                f'{cluster_workspace!r}, but the active workspace is '
                f'{active_workspace!r}.{colorama.Fore.RESET}')

    launched_resources = handle.launched_resources.assert_launchable()
    cloud = launched_resources.cloud
    user_identities = cloud.get_user_identities()
    owner_identity = record['owner']
    if user_identities is None:
        # Skip the check if the cloud does not support user identity.
        return
    # The user identity can be None, if the cluster is created by an older
    # version of SkyPilot. In that case, we set the user identity to the
    # current active one.
    # NOTE: a user who upgrades SkyPilot and switches to a new cloud identity
    # immediately without `sky status --refresh` first, will cause a leakage
    # of the existing cluster. We deem this an acceptable tradeoff mainly
    # because multi-identity is not common (at least at the moment).
    if owner_identity is None:
        global_user_state.set_owner_identity_for_cluster(
            cluster_name, user_identities[0])
    else:
        assert isinstance(owner_identity, list)
        # It is OK if the owner identity is shorter, which will happen when
        # the cluster is launched before #1808. In that case, we only check
        # the same length (zip will stop at the shorter one).
        for identity in user_identities:
            for i, (owner, current) in enumerate(zip(owner_identity, identity)):
                # Clean up the owner identity for the backslash and newlines, caused
                # by the cloud CLI output, e.g. gcloud.
                owner = owner.replace('\n', '').replace('\\', '')
                if owner == current:
                    if i != 0:
                        logger.warning(
                            f'The cluster was owned by {owner_identity}, but '
                            f'a new identity {identity} is activated. We still '
                            'allow the operation as the two identities are '
                            'likely to have the same access to the cluster. '
                            'Please be aware that this can cause unexpected '
                            'cluster leakage if the two identities are not '
                            'actually equivalent (e.g., belong to the same '
                            'person).')
                    if i != 0 or len(owner_identity) != len(identity):
                        # We update the owner of a cluster, when:
                        # 1. The strictest identty (i.e. the first one) does not
                        # match, but the latter ones match.
                        # 2. The length of the two identities are different,
                        # which will only happen when the cluster is launched
                        # before #1808. Update the user identity to avoid
                        # showing the warning above again.
                        global_user_state.set_owner_identity_for_cluster(
                            cluster_name, identity)
                    return  # The user identity matches.
        # Generate error message if no match found
        if len(user_identities) == 1:
            err_msg = f'the activated identity is {user_identities[0]!r}.'
        else:
            err_msg = (f'available identities are {user_identities!r}.')
        if cloud.is_same_cloud(clouds.Kubernetes()):
            err_msg += (' Check your kubeconfig file and make sure the '
                        'correct context is available.')
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ClusterOwnerIdentityMismatchError(
                f'{cluster_name!r} ({cloud}) is owned by account '
                f'{owner_identity!r}, but ' + err_msg)


def tag_filter_for_cluster(cluster_name: str) -> Dict[str, str]:
    """Returns a tag filter for the cluster."""
    return {
        'ray-cluster-name': cluster_name,
    }


@context_utils.cancellation_guard
def _query_cluster_status_via_cloud_api(
    handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle',
    retry_if_missing: bool,
) -> List[Tuple[status_lib.ClusterStatus, Optional[str]]]:
    """Returns the status of the cluster as a list of tuples corresponding
    to the node status and an optional reason string for said status.

    Raises:
        exceptions.ClusterStatusFetchingError: the cluster status cannot be
          fetched from the cloud provider.
    """
    cluster_name = handle.cluster_name
    cluster_name_on_cloud = handle.cluster_name_on_cloud
    cluster_name_in_hint = common_utils.cluster_name_in_hint(
        handle.cluster_name, cluster_name_on_cloud)
    # Use region and zone from the cluster config, instead of the
    # handle.launched_resources, because the latter may not be set
    # correctly yet.
    ray_config = global_user_state.get_cluster_yaml_dict(handle.cluster_yaml)
    provider_config = ray_config['provider']

    # Query the cloud provider.
    # TODO(suquark): move implementations of more clouds here
    cloud = handle.launched_resources.cloud
    assert cloud is not None, handle
    if cloud.STATUS_VERSION >= clouds.StatusVersion.SKYPILOT:
        cloud_name = repr(handle.launched_resources.cloud)
        try:
            node_status_dict = provision_lib.query_instances(
                cloud_name,
                cluster_name,
                cluster_name_on_cloud,
                provider_config,
                retry_if_missing=retry_if_missing)
            logger.debug(f'Querying {cloud_name} cluster '
                         f'{cluster_name_in_hint} '
                         f'status:\n{pprint.pformat(node_status_dict)}')
            node_statuses = list(node_status_dict.values())
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ClusterStatusFetchingError(
                    f'Failed to query {cloud_name} cluster '
                    f'{cluster_name_in_hint} '
                    f'status: {common_utils.format_exception(e, use_bracket=True)}'
                )
    else:
        region = provider_config.get('region') or provider_config.get(
            'location')
        zone = ray_config['provider'].get('availability_zone')
        # TODO (kyuds): refactor cloud.query_status api to include reason.
        # Currently not refactoring as this API is actually supposed to be
        # deprecated soon.
        node_statuses = cloud.query_status(
            cluster_name_on_cloud,
            tag_filter_for_cluster(cluster_name_on_cloud), region, zone)
        node_statuses = [(status, None) for status in node_statuses]
    return node_statuses


def _query_cluster_info_via_cloud_api(
    handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle'
) -> provision_common.ClusterInfo:
    """Returns the cluster info.

    Raises:
        exceptions.NotSupportedError: the cloud does not support the new provisioner.
        exceptions.FetchClusterInfoError: the cluster info cannot be
          fetched from the cloud provider.
    """
    cloud = handle.launched_resources.cloud
    assert cloud is not None, handle
    if cloud.STATUS_VERSION >= clouds.StatusVersion.SKYPILOT:
        try:
            cloud_name = repr(cloud)
            ray_config = global_user_state.get_cluster_yaml_dict(
                handle.cluster_yaml)
            provider_config = ray_config['provider']
            region = provider_config.get('region') or provider_config.get(
                'location')
            cluster_info = provision_lib.get_cluster_info(
                cloud_name, region, handle.cluster_name_on_cloud,
                provider_config)
            logger.debug(
                f'Querying {cloud_name} cluster '
                f'{handle.cluster_name_on_cloud} '
                f'head instance:\n{cluster_info.get_head_instance()}\n'
                f'worker instances:\n{cluster_info.get_worker_instances()}')
            return cluster_info
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.print_exception_no_traceback():
                raise exceptions.FetchClusterInfoError(
                    reason=exceptions.FetchClusterInfoError.Reason.UNKNOWN
                ) from e
    else:
        raise exceptions.NotSupportedError(
            f'The cloud {cloud} does not support the SkyPilot provisioner.')


def check_can_clone_disk_and_override_task(
    cluster_name: str, target_cluster_name: Optional[str], task: 'task_lib.Task'
) -> Tuple['task_lib.Task', 'cloud_vm_ray_backend.CloudVmRayResourceHandle']:
    """Check if the task is compatible to clone disk from the source cluster.

    Args:
        cluster_name: The name of the cluster to clone disk from.
        target_cluster_name: The name of the target cluster.
        task: The task to check.

    Returns:
        The task to use and the resource handle of the source cluster.

    Raises:
        exceptions.ClusterDoesNotExist: If the source cluster does not exist.
        exceptions.NotSupportedError: If the source cluster is not valid or the
            task is not compatible to clone disk from the source cluster.
    """
    source_cluster_status, handle = refresh_cluster_status_handle(cluster_name)
    if source_cluster_status is None:
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ClusterDoesNotExist(
                f'Cannot find cluster {cluster_name!r} to clone disk from.')

    if not isinstance(handle, backends.CloudVmRayResourceHandle):
        with ux_utils.print_exception_no_traceback():
            raise exceptions.NotSupportedError(
                f'Cannot clone disk from a non-cloud cluster {cluster_name!r}.')

    if source_cluster_status != status_lib.ClusterStatus.STOPPED:
        with ux_utils.print_exception_no_traceback():
            raise exceptions.NotSupportedError(
                f'Cannot clone disk from cluster {cluster_name!r} '
                f'({source_cluster_status.value!r}). Please stop the '
                f'cluster first: sky stop {cluster_name}')

    if target_cluster_name is not None:
        target_cluster_status, _ = refresh_cluster_status_handle(
            target_cluster_name)
        if target_cluster_status is not None:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.NotSupportedError(
                    f'The target cluster {target_cluster_name!r} already exists. Cloning '
                    'disk is only supported when creating a new cluster. To fix: specify '
                    'a new target cluster name.')

    new_task_resources = []
    launched_resources = handle.launched_resources.assert_launchable()
    original_cloud = launched_resources.cloud
    original_cloud.check_features_are_supported(
        launched_resources,
        {clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER})

    has_override = False
    has_disk_size_met = False
    has_cloud_met = False
    for task_resources in task.resources:
        if handle.launched_resources.disk_size > task_resources.disk_size:
            # The target cluster's disk should be at least as large as the source.
            continue
        has_disk_size_met = True
        if task_resources.cloud is not None and not original_cloud.is_same_cloud(
                task_resources.cloud):
            continue
        has_cloud_met = True

        override_param: Dict[str, Any] = {}
        if task_resources.cloud is None:
            override_param['cloud'] = original_cloud
        if task_resources.region is None:
            override_param['region'] = handle.launched_resources.region

        if override_param:
            logger.info(
                f'No cloud/region specified for the task {task_resources}. Using the same region '
                f'as source cluster {cluster_name!r}: '
                f'{handle.launched_resources.cloud}'
                f'({handle.launched_resources.region}).')
            has_override = True
        task_resources = task_resources.copy(**override_param)
        new_task_resources.append(task_resources)

    if not new_task_resources:
        if not has_disk_size_met:
            with ux_utils.print_exception_no_traceback():
                target_cluster_name_str = f' {target_cluster_name!r}'
                if target_cluster_name is None:
                    target_cluster_name_str = ''
                raise exceptions.NotSupportedError(
                    f'The target cluster{target_cluster_name_str} should have a disk size '
                    f'of at least {handle.launched_resources.disk_size} GB to clone the '
                    f'disk from {cluster_name!r}.')
        if not has_cloud_met:
            task_resources_cloud_str = '[' + ','.join(
                [f'{res.cloud}' for res in task.resources]) + ']'
            task_resources_str = '[' + ','.join(
                [f'{res}' for res in task.resources]) + ']'
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Cannot clone disk across cloud from {original_cloud} to '
                    f'{task_resources_cloud_str} for resources {task_resources_str}.'
                )
        assert False, 'Should not reach here.'
    # set the new_task_resources to be the same type (list or set) as the
    # original task.resources
    if has_override:
        task.set_resources(type(task.resources)(new_task_resources))
        # Reset the best_resources to triger re-optimization
        # later, so that the new task_resources will be used.
        task.best_resources = None
    return task, handle


def _update_cluster_status(
        cluster_name: str,
        record: Dict[str, Any],
        retry_if_missing: bool,
        include_user_info: bool = True,
        summary_response: bool = False) -> Optional[Dict[str, Any]]:
    """Update the cluster status.

    The cluster status is updated by checking ray cluster and real status from
    cloud.

    The function will update the cached cluster status in the global state. For
    the design of the cluster status and transition, please refer to the
    sky/design_docs/cluster_status.md

    Note: this function is only safe to be called when the caller process is
    holding the cluster lock, which means no other processes are modifying the
    cluster.

    Returns:
        If the cluster is terminated or does not exist, return None. Otherwise
        returns the input record with status and handle potentially updated.

    Raises:
        exceptions.ClusterOwnerIdentityMismatchError: if the current user is
          not the same as the user who created the cluster.
        exceptions.CloudUserIdentityError: if we fail to get the current user
          identity.
        exceptions.ClusterStatusFetchingError: the cluster status cannot be
          fetched from the cloud provider or there are leaked nodes causing
          the node number larger than expected.
    """
    handle = record['handle']
    if handle.cluster_yaml is None:
        # Remove cluster from db since this cluster does not have a config file
        # or any other ongoing requests
        global_user_state.add_cluster_event(
            cluster_name,
            None,
            'Cluster has no YAML file. Removing the cluster from cache.',
            global_user_state.ClusterEventType.STATUS_CHANGE,
            nop_if_duplicate=True)
        global_user_state.remove_cluster(cluster_name, terminate=True)
        logger.debug(f'Cluster {cluster_name!r} has no YAML file. '
                     'Removing the cluster from cache.')
        return None
    if not isinstance(handle, backends.CloudVmRayResourceHandle):
        return record
    cluster_name = handle.cluster_name

    node_statuses = _query_cluster_status_via_cloud_api(
        handle, retry_if_missing=retry_if_missing)

    all_nodes_up = (all(status[0] == status_lib.ClusterStatus.UP
                        for status in node_statuses) and
                    len(node_statuses) == handle.launched_nodes)

    external_cluster_failures = ExternalFailureSource.get(
        cluster_hash=record['cluster_hash'])
    logger.debug(f'Cluster {cluster_name} with cluster_hash '
                 f'{record["cluster_hash"]} has external cluster failures: '
                 f'{external_cluster_failures}')

    def get_node_counts_from_ray_status(
            runner: command_runner.CommandRunner) -> Tuple[int, int, str, str]:
        rc, output, stderr = runner.run(
            instance_setup.RAY_STATUS_WITH_SKY_RAY_PORT_COMMAND,
            stream_logs=False,
            require_outputs=True,
            separate_stderr=True)
        if rc:
            raise exceptions.CommandError(
                rc, instance_setup.RAY_STATUS_WITH_SKY_RAY_PORT_COMMAND,
                f'Failed to check ray cluster\'s healthiness.\n'
                '-- stdout --\n'
                f'{output}\n', stderr)
        return (*_count_healthy_nodes_from_ray(output), output, stderr)

    ray_status_details: Optional[str] = None

    def run_ray_status_to_check_ray_cluster_healthy() -> bool:
        nonlocal ray_status_details
        try:
            # NOTE: fetching the IPs is very slow as it calls into
            # `ray get head-ip/worker-ips`. Using cached IPs is safe because
            # in the worst case we time out in the `ray status` SSH command
            # below.
            runners = handle.get_command_runners(force_cached=True)
            # This happens when user interrupt the `sky launch` process before
            # the first time resources handle is written back to local database.
            # This is helpful when user interrupt after the provision is done
            # and before the skylet is restarted. After #2304 is merged, this
            # helps keep the cluster status to INIT after `sky status -r`, so
            # user will be notified that any auto stop/down might not be
            # triggered.
            if not runners:
                logger.debug(f'Refreshing status ({cluster_name!r}): No cached '
                             f'IPs found. Handle: {handle}')
                raise exceptions.FetchClusterInfoError(
                    reason=exceptions.FetchClusterInfoError.Reason.HEAD)
            head_runner = runners[0]

            total_nodes = handle.launched_nodes * handle.num_ips_per_node

            cloud_name = repr(handle.launched_resources.cloud).lower()
            # Initialize variables in case all retries fail
            ready_head = 0
            ready_workers = 0
            output = ''
            stderr = ''
            for i in range(5):
                try:
                    ready_head, ready_workers, output, stderr = (
                        get_node_counts_from_ray_status(head_runner))
                except exceptions.CommandError as e:
                    logger.debug(f'Refreshing status ({cluster_name!r}) attempt'
                                 f' {i}: {common_utils.format_exception(e)}')
                    if cloud_name != 'kubernetes':
                        # Non-k8s clusters can be manually restarted and:
                        # 1. Get new IP addresses, or
                        # 2. Not have the SkyPilot runtime setup
                        #
                        # So we should surface a message to the user to
                        # help them recover from this inconsistent state.
                        has_new_ip_addr = (
                            e.detailed_reason is not None and
                            _SSH_CONNECTION_TIMED_OUT_PATTERN.search(
                                e.detailed_reason.strip()) is not None)
                        runtime_not_setup = (_RAY_CLUSTER_NOT_FOUND_MESSAGE
                                             in e.error_msg)
                        if has_new_ip_addr or runtime_not_setup:
                            yellow = colorama.Fore.YELLOW
                            bright = colorama.Style.BRIGHT
                            reset = colorama.Style.RESET_ALL
                            ux_utils.console_newline()
                            logger.warning(
                                f'{yellow}Failed getting cluster status despite all nodes '
                                f'being up ({cluster_name!r}). '
                                f'If the cluster was restarted manually, try running: '
                                f'{reset}{bright}sky start {cluster_name}{reset} '
                                f'{yellow}to recover from INIT status.{reset}')
                            return False
                        raise e
                    # We retry for kubernetes because coreweave can have a
                    # transient network issue.
                    time.sleep(1)
                    continue
                if ready_head + ready_workers == total_nodes:
                    return True
                logger.debug(f'Refreshing status ({cluster_name!r}) attempt '
                             f'{i}: ray status not showing all nodes '
                             f'({ready_head + ready_workers}/{total_nodes});\n'
                             f'output:\n{output}\nstderr:\n{stderr}')

                # If cluster JUST started, maybe not all the nodes have shown
                # up. Try again for a few seconds.
                # Note: We are okay with this performance hit because it's very
                # rare to normally hit this case. It requires:
                # - All the instances in the cluster are up on the cloud side
                #   (not preempted), but
                # - The ray cluster is somehow degraded so not all instances are
                #   showing up
                time.sleep(1)

            ray_status_details = (
                f'{ready_head + ready_workers}/{total_nodes} ready')
            raise RuntimeError(
                f'Refreshing status ({cluster_name!r}): ray status not showing '
                f'all nodes ({ready_head + ready_workers}/'
                f'{total_nodes});\noutput:\n{output}\nstderr:\n{stderr}')

        except exceptions.FetchClusterInfoError:
            ray_status_details = 'failed to get IPs'
            logger.debug(
                f'Refreshing status ({cluster_name!r}) failed to get IPs.')
        except RuntimeError as e:
            if ray_status_details is None:
                ray_status_details = str(e)
            logger.debug(common_utils.format_exception(e))
        except Exception as e:  # pylint: disable=broad-except
            # This can be raised by `external_ssh_ports()`, due to the
            # underlying call to kubernetes API.
            ray_status_details = str(e)
            logger.debug(f'Refreshing status ({cluster_name!r}) failed: ',
                         exc_info=e)
        return False

    def _handle_autostopping_cluster(
            print_newline: bool = False) -> Optional[Dict[str, Any]]:
        """Handle cluster that is autostopping/autodowning.

        Sets the cluster status to AUTOSTOPPING and returns the cluster record.

        Args:
            print_newline: Whether to print a newline before logging (for UX).

        Returns:
            Cluster record if autostopping, None otherwise.
        """
        # The cluster is autostopping - set to AUTOSTOPPING status
        if print_newline:
            ux_utils.console_newline()
        operation_str = 'autodowning' if record.get('to_down',
                                                    False) else 'autostopping'
        logger.info(f'Cluster {cluster_name!r} is {operation_str}.')

        # Set cluster to AUTOSTOPPING status
        record['status'] = status_lib.ClusterStatus.AUTOSTOPPING
        global_user_state.add_cluster_event(
            cluster_name,
            status_lib.ClusterStatus.AUTOSTOPPING,
            f'Cluster is {operation_str}.',
            global_user_state.ClusterEventType.STATUS_CHANGE,
            nop_if_duplicate=True)
        # Use set_cluster_status() to directly update the status in DB
        # instead of add_or_update_cluster() which only supports INIT/UP
        global_user_state.set_cluster_status(
            cluster_name, status_lib.ClusterStatus.AUTOSTOPPING)
        return global_user_state.get_cluster_from_name(
            cluster_name,
            include_user_info=include_user_info,
            summary_response=summary_response)

    # Determining if the cluster is healthy (UP):
    #
    # For non-spot clusters: If ray status shows all nodes are healthy, it is
    # safe to set the status to UP as starting ray is the final step of sky
    # launch. But we found that ray status is way too slow (see NOTE below) so
    # we always query the cloud provider first which is faster.
    #
    # For spot clusters: the above can be unsafe because the Ray cluster may
    # remain healthy for a while before the cloud completely preempts the VMs.
    # We have mitigated this by again first querying the VM state from the cloud
    # provider.
    cloud = handle.launched_resources.cloud

    # For Slurm, skip Ray health check since it doesn't use Ray.
    should_check_ray = cloud is not None and cloud.uses_ray()
    if (all_nodes_up and (not should_check_ray or
                          run_ray_status_to_check_ray_cluster_healthy()) and
            not external_cluster_failures):
        # NOTE: all_nodes_up calculation is fast due to calling cloud CLI;
        # run_ray_status_to_check_all_nodes_up() is slow due to calling `ray get
        # head-ip/worker-ips`.

        # Check if the cluster is in the process of autostopping
        backend = get_backend_from_handle(handle)
        if isinstance(backend, backends.CloudVmRayBackend):
            if backend.is_definitely_autostopping(handle, stream_logs=False):
                return _handle_autostopping_cluster(print_newline=False)

        record['status'] = status_lib.ClusterStatus.UP
        # Add cluster event for instance status check.
        global_user_state.add_cluster_event(
            cluster_name,
            status_lib.ClusterStatus.UP,
            'All nodes up; SkyPilot runtime healthy.',
            global_user_state.ClusterEventType.STATUS_CHANGE,
            nop_if_duplicate=True)
        global_user_state.add_or_update_cluster(
            cluster_name,
            handle,
            requested_resources=None,
            ready=True,
            is_launch=False,
            existing_cluster_hash=record['cluster_hash'])
        return global_user_state.get_cluster_from_name(
            cluster_name,
            include_user_info=include_user_info,
            summary_response=summary_response)

    # All cases below are transitioning the cluster to non-UP states.
    launched_resources = handle.launched_resources.assert_launchable()
    if (not node_statuses and launched_resources.cloud.STATUS_VERSION >=
            clouds.StatusVersion.SKYPILOT):
        # Note: launched_at is set during sky launch, even on an existing
        # cluster. This will catch the case where the cluster was terminated on
        # the cloud and restarted by sky launch.
        time_since_launch = time.time() - record['launched_at']
        if (record['status'] == status_lib.ClusterStatus.INIT and
                time_since_launch < _LAUNCH_DOUBLE_CHECK_WINDOW):
            # It's possible the instances for this cluster were just created,
            # and haven't appeared yet in the cloud API/console. Wait for a bit
            # and check again. This is a best-effort leak prevention check.
            # See https://github.com/skypilot-org/skypilot/issues/4431.
            time.sleep(_LAUNCH_DOUBLE_CHECK_DELAY)
            node_statuses = _query_cluster_status_via_cloud_api(
                handle, retry_if_missing=False)
            # Note: even if all the node_statuses are UP now, we will still
            # consider this cluster abnormal, and its status will be INIT.

    if len(node_statuses) > handle.launched_nodes:
        # Unexpected: in the queried region more than 1 cluster with the same
        # constructed name tag returned. This will typically not happen unless
        # users manually create a cluster with that constructed name or there
        # was a resource leak caused by different launch hash before #1671
        # was merged.
        #
        # (Technically speaking, even if returned num nodes <= num
        # handle.launched_nodes), not including the launch hash could mean the
        # returned nodes contain some nodes that do not belong to the logical
        # skypilot cluster. Doesn't seem to be a good way to handle this for
        # now?)
        #
        # We have not experienced the above; adding as a safeguard.
        #
        # Since we failed to refresh, raise the status fetching error.
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ClusterStatusFetchingError(
                f'Found {len(node_statuses)} node(s) with the same cluster name'
                f' tag in the cloud provider for cluster {cluster_name!r}, '
                f'which should have {handle.launched_nodes} nodes. This '
                f'normally should not happen. {colorama.Fore.RED}Please check '
                'the cloud console and fix any possible resources leakage '
                '(e.g., if there are any stopped nodes and they do not have '
                'data or are unhealthy, terminate them).'
                f'{colorama.Style.RESET_ALL}')
    assert len(node_statuses) <= handle.launched_nodes

    # If the node_statuses is empty, it should mean that all the nodes are
    # terminated and we can set the cluster status to TERMINATED. This handles
    # the edge case where the cluster is terminated by the user manually through
    # the UI.
    to_terminate = not node_statuses

    # A cluster is considered "abnormal", if some (but not all) nodes are
    # TERMINATED, or not all nodes are STOPPED. We check that with the following
    # logic:
    #   * Not all nodes are terminated and there's at least one node
    #     terminated; or
    #   * Any of the non-TERMINATED nodes is in a non-STOPPED status.
    #
    # This includes these special cases:
    #   * All stopped are considered normal and will be cleaned up at the end
    #     of the function.
    #   * Some of the nodes UP should be considered abnormal, because the ray
    #     cluster is probably down.
    #   * The cluster is partially terminated or stopped should be considered
    #     abnormal.
    #   * The cluster is partially or completely in the INIT state, which means
    #     that provisioning was interrupted. This is considered abnormal.
    #
    # An abnormal cluster will transition to INIT, and one of the following will happen:
    #  (1) If the SkyPilot provisioner is used AND the head node is alive, we
    #      will not reset the autostop setting. Because autostop is handled by
    #      the skylet through the cloud APIs, and will continue to function
    #      regardless of the ray cluster's health.
    #  (2) Otherwise, we will reset the autostop setting, unless the cluster is
    #      autostopping/autodowning.
    some_nodes_terminated = 0 < len(node_statuses) < handle.launched_nodes
    some_nodes_not_stopped = any(status[0] != status_lib.ClusterStatus.STOPPED
                                 for status in node_statuses)
    is_abnormal = (some_nodes_terminated or some_nodes_not_stopped)

    if is_abnormal and not external_cluster_failures:
        # If all nodes are up and ray cluster is healthy, we would have returned
        # earlier. So if all_nodes_up is True and we are here, it means the ray
        # cluster must have been unhealthy.
        ray_cluster_unhealthy = all_nodes_up
        status_reason = ', '.join(
            [status[1] for status in node_statuses if status[1] is not None])

        if some_nodes_terminated:
            init_reason = 'one or more nodes terminated'
        elif ray_cluster_unhealthy:
            init_reason = f'ray cluster is unhealthy ({ray_status_details})'
        elif some_nodes_not_stopped:
            init_reason = 'some but not all nodes are stopped'
        logger.debug('The cluster is abnormal. Setting to INIT status. '
                     f'node_statuses: {node_statuses}')
        if record['autostop'] >= 0:
            is_head_node_alive = False
            if launched_resources.cloud.PROVISIONER_VERSION >= clouds.ProvisionerVersion.SKYPILOT:
                # Check if the head node is alive
                try:
                    cluster_info = _query_cluster_info_via_cloud_api(handle)
                    is_head_node_alive = cluster_info.get_head_instance(
                    ) is not None
                except Exception as e:  # pylint: disable=broad-except
                    logger.debug(
                        f'Failed to get cluster info for {cluster_name!r}: '
                        f'{common_utils.format_exception(e)}')

            backend = get_backend_from_handle(handle)
            if isinstance(backend, backends.CloudVmRayBackend):
                # Check autostopping first, before head_node_alive check
                # This ensures we detect AUTOSTOPPING even when Ray becomes
                # unhealthy during hook execution, or if the actual nodes are
                # partially autostopped but not completely yet.
                is_autostopping = backend.is_definitely_autostopping(
                    handle, stream_logs=False)

                if is_autostopping:
                    logger.debug(
                        f'The cluster {cluster_name!r} is abnormal '
                        f'({init_reason}) but is definitely autostopping. '
                        'Returning AUTOSTOPPING status.')
                    return _handle_autostopping_cluster(print_newline=True)
                elif is_head_node_alive:
                    logger.debug(
                        f'Skipping autostop reset for cluster {cluster_name!r} '
                        'because the head node is alive.')
                elif not is_autostopping:
                    # Friendly hint.
                    autostop = record['autostop']
                    maybe_down_str = ' --down' if record['to_down'] else ''
                    noun = 'autodown' if record['to_down'] else 'autostop'

                    # Reset the autostopping as the cluster is abnormal, and may
                    # not correctly autostop. Resetting the autostop will let
                    # the user know that the autostop may not happen to avoid
                    # leakages from the assumption that the cluster will autostop.
                    success = True
                    reset_local_autostop = True
                    try:
                        backend.set_autostop(
                            handle,
                            -1,
                            autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                            stream_logs=False)
                    except (exceptions.CommandError,
                            grpc.FutureTimeoutError) as e:
                        success = False
                        if isinstance(e, grpc.FutureTimeoutError) or (
                                isinstance(e, exceptions.CommandError) and
                                e.returncode == 255):
                            word = 'autostopped' if noun == 'autostop' else 'autodowned'
                            logger.debug(f'The cluster is likely {word}.')
                            reset_local_autostop = False
                    except (Exception, SystemExit) as e:  # pylint: disable=broad-except
                        success = False
                        logger.debug(f'Failed to reset autostop. Due to '
                                     f'{common_utils.format_exception(e)}')
                    if reset_local_autostop:
                        global_user_state.set_cluster_autostop_value(
                            handle.cluster_name, -1, to_down=False)

                    if success:
                        operation_str = (f'Canceled {noun} on the cluster '
                                         f'{cluster_name!r}')
                    else:
                        operation_str = (
                            f'Attempted to cancel {noun} on the '
                            f'cluster {cluster_name!r} with best effort')
                    yellow = colorama.Fore.YELLOW
                    bright = colorama.Style.BRIGHT
                    reset = colorama.Style.RESET_ALL
                    ux_utils.console_newline()
                    logger.warning(
                        f'{yellow}{operation_str}, since it is found to be in an '
                        f'abnormal state. To fix, try running: {reset}{bright}sky '
                        f'start -f -i {autostop}{maybe_down_str} {cluster_name}'
                        f'{reset}')

        # If the user starts part of a STOPPED cluster, we still need a status
        # to represent the abnormal status. For spot cluster, it can also
        # represent that the cluster is partially preempted.
        # TODO(zhwu): the definition of INIT should be audited/changed.
        # Adding a new status UNHEALTHY for abnormal status can be a choice.
        init_reason_regex = None
        if not status_reason:
            # If there is not a status reason, don't re-add (and overwrite) the
            # event if there is already an event with the same reason which may
            # have a status reason.
            # Some status reason clears after a certain time (e.g. k8s events
            # are only stored for an hour by default), so it is possible that
            # the previous event has a status reason, but now it does not.
            init_reason_regex = (f'^Cluster is abnormal because '
                                 f'{re.escape(init_reason)}.*')
        log_message = f'Cluster is abnormal because {init_reason}'
        if status_reason:
            log_message += f' ({status_reason})'
        log_message += '. Transitioned to INIT.'
        global_user_state.add_cluster_event(
            cluster_name,
            status_lib.ClusterStatus.INIT,
            log_message,
            global_user_state.ClusterEventType.STATUS_CHANGE,
            nop_if_duplicate=True,
            duplicate_regex=init_reason_regex)
        global_user_state.add_or_update_cluster(
            cluster_name,
            handle,
            requested_resources=None,
            ready=False,
            is_launch=False,
            existing_cluster_hash=record['cluster_hash'])
        return global_user_state.get_cluster_from_name(
            cluster_name,
            include_user_info=include_user_info,
            summary_response=summary_response)
    # Now either:
    # (1) is_abnormal is False: either node_statuses is empty or all nodes are
    #                           STOPPED
    # or
    # (2) there are external cluster failures reported by a plugin.

    # If there are external cluster failures and the cluster has not been
    # terminated on cloud (to_terminate), we can return the cluster record as is.
    # This is because when an external failure is detected, the cluster will be
    # marked as INIT with a reason indicating the details of the failure. So, we
    # do not want to modify the cluster status in this function except for in the
    # case where the cluster has been terminated on cloud, in which case we should
    # clean up the cluster from SkyPilot's global state.
    if external_cluster_failures and not to_terminate:
        return global_user_state.get_cluster_from_name(
            cluster_name,
            include_user_info=include_user_info,
            summary_response=summary_response)

    verb = 'terminated' if to_terminate else 'stopped'
    backend = backends.CloudVmRayBackend()
    global_user_state.add_cluster_event(
        cluster_name,
        None,
        f'All nodes {verb}, cleaning up the cluster.',
        global_user_state.ClusterEventType.STATUS_CHANGE,
        # This won't do anything for a terminated cluster, but it's needed for a
        # stopped cluster.
        nop_if_duplicate=True,
    )
    backend.post_teardown_cleanup(handle, terminate=to_terminate, purge=False)
    return global_user_state.get_cluster_from_name(
        cluster_name,
        include_user_info=include_user_info,
        summary_response=summary_response)


def _must_refresh_cluster_status(
        record: Dict[str, Any],
        force_refresh_statuses: Optional[Set[status_lib.ClusterStatus]]
) -> bool:
    force_refresh_for_cluster = (force_refresh_statuses is not None and
                                 record['status'] in force_refresh_statuses)

    use_spot = record['handle'].launched_resources.use_spot
    has_autostop = (record['status'] != status_lib.ClusterStatus.STOPPED and
                    record['autostop'] >= 0)
    # If cluster is AUTOSTOPPING, always refresh to check if it transitioned to STOPPED
    is_autostopping = record['status'] == status_lib.ClusterStatus.AUTOSTOPPING
    recently_refreshed = (record['status_updated_at'] is not None and
                          time.time() - record['status_updated_at'] <
                          CLUSTER_STATUS_CACHE_DURATION_SECONDS)
    is_stale = (use_spot or has_autostop or
                is_autostopping) and not recently_refreshed

    return force_refresh_for_cluster or is_stale


def refresh_cluster_record(
        cluster_name: str,
        *,
        force_refresh_statuses: Optional[Set[status_lib.ClusterStatus]] = None,
        cluster_lock_already_held: bool = False,
        cluster_status_lock_timeout: int = CLUSTER_STATUS_LOCK_TIMEOUT_SECONDS,
        include_user_info: bool = True,
        summary_response: bool = False,
        retry_if_missing: bool = True) -> Optional[Dict[str, Any]]:
    """Refresh the cluster, and return the possibly updated record.

    The function will update the cached cluster status in the global state. For
    the design of the cluster status and transition, please refer to the
    sky/design_docs/cluster_status.md

    Args:
        cluster_name: The name of the cluster.
        force_refresh_statuses: if specified, refresh the cluster if it has one
          of the specified statuses. Additionally, clusters satisfying the
          following conditions will be refreshed no matter the argument is
          specified or not:
            - the most latest available status update is more than
              CLUSTER_STATUS_CACHE_DURATION_SECONDS old, and one of:
                1. the cluster is a spot cluster, or
                2. cluster autostop is set and the cluster is not STOPPED.
        cluster_lock_already_held: Whether the caller is already holding the
          per-cluster lock. You MUST NOT set this to True if the caller does not
          already hold the lock. If True, we will not acquire the lock before
          updating the status. Failing to hold the lock while updating the
          status can lead to correctness issues - e.g. an launch in-progress may
          appear to be DOWN incorrectly. Even if this is set to False, the lock
          may not be acquired if the status does not need to be refreshed.
        cluster_status_lock_timeout: The timeout to acquire the per-cluster
          lock. If timeout, the function will use the cached status. If the
          value is <0, do not timeout (wait for the lock indefinitely). By
          default, this is set to CLUSTER_STATUS_LOCK_TIMEOUT_SECONDS. Warning:
          if correctness is required, you must set this to -1.
        retry_if_missing: Whether to retry the call to the cloud api if the
          cluster is not found when querying the live status on the cloud.

    Returns:
        If the cluster is terminated or does not exist, return None.
        Otherwise returns the cluster record.

    Raises:
        exceptions.ClusterOwnerIdentityMismatchError: if the current user is
          not the same as the user who created the cluster.
        exceptions.CloudUserIdentityError: if we fail to get the current user
          identity.
        exceptions.ClusterStatusFetchingError: the cluster status cannot be
          fetched from the cloud provider or there are leaked nodes causing
          the node number larger than expected.
    """

    ctx = context_lib.get()
    record = global_user_state.get_cluster_from_name(
        cluster_name,
        include_user_info=include_user_info,
        summary_response=summary_response)
    if record is None:
        return None
    # TODO(zhwu, 05/20): switch to the specific workspace to make sure we are
    # using the correct cloud credentials.
    workspace = record.get('workspace', constants.SKYPILOT_DEFAULT_WORKSPACE)
    with skypilot_config.local_active_workspace_ctx(workspace):
        # check_owner_identity returns if the record handle is
        # not a CloudVmRayResourceHandle
        _check_owner_identity_with_record(cluster_name, record)

        # The loop logic allows us to notice if the status was updated in the
        # global_user_state by another process and stop trying to get the lock.
        lock = locks.get_lock(cluster_status_lock_id(cluster_name))
        start_time = time.perf_counter()

        # Loop until we have an up-to-date status or until we acquire the lock.
        while True:
            # Check if the context is canceled.
            if ctx is not None and ctx.is_canceled():
                raise asyncio.CancelledError()
            # Check to see if we can return the cached status.
            if not _must_refresh_cluster_status(record, force_refresh_statuses):
                return record

            if cluster_lock_already_held:
                return _update_cluster_status(cluster_name, record,
                                              retry_if_missing,
                                              include_user_info,
                                              summary_response)

            # Try to acquire the lock so we can fetch the status.
            try:
                with lock.acquire(blocking=False):
                    # Check the cluster status again, since it could have been
                    # updated between our last check and acquiring the lock.
                    record = global_user_state.get_cluster_from_name(
                        cluster_name,
                        include_user_info=include_user_info,
                        summary_response=summary_response)
                    if record is None or not _must_refresh_cluster_status(
                            record, force_refresh_statuses):
                        return record
                    # Update and return the cluster status.
                    return _update_cluster_status(cluster_name, record,
                                                  retry_if_missing,
                                                  include_user_info,
                                                  summary_response)

            except locks.LockTimeout:
                # lock.acquire() will throw a Timeout exception if the lock is not
                # available and we have blocking=False.
                pass

            # Logic adapted from FileLock.acquire().
            # If cluster_status_lock_time is <0, we will never hit this. No timeout.
            # Otherwise, if we have timed out, return the cached status. This has
            # the potential to cause correctness issues, but if so it is the
            # caller's responsibility to set the timeout to -1.
            if 0 <= cluster_status_lock_timeout < time.perf_counter(
            ) - start_time:
                logger.debug(
                    'Refreshing status: Failed get the lock for cluster '
                    f'{cluster_name!r}. Using the cached status.')
                return record
            time.sleep(lock.poll_interval)

            # Refresh for next loop iteration.
            record = global_user_state.get_cluster_from_name(
                cluster_name,
                include_user_info=include_user_info,
                summary_response=summary_response)
            if record is None:
                return None


@timeline.event
@context_utils.cancellation_guard
def refresh_cluster_status_handle(
    cluster_name: str,
    *,
    force_refresh_statuses: Optional[Set[status_lib.ClusterStatus]] = None,
    cluster_lock_already_held: bool = False,
    cluster_status_lock_timeout: int = CLUSTER_STATUS_LOCK_TIMEOUT_SECONDS,
    retry_if_missing: bool = True,
) -> Tuple[Optional[status_lib.ClusterStatus],
           Optional[backends.ResourceHandle]]:
    """Refresh the cluster, and return the possibly updated status and handle.

    This is a wrapper of refresh_cluster_record, which returns the status and
    handle of the cluster.
    Please refer to the docstring of refresh_cluster_record for the details.
    """
    record = refresh_cluster_record(
        cluster_name,
        force_refresh_statuses=force_refresh_statuses,
        cluster_lock_already_held=cluster_lock_already_held,
        cluster_status_lock_timeout=cluster_status_lock_timeout,
        include_user_info=False,
        summary_response=True,
        retry_if_missing=retry_if_missing)
    if record is None:
        return None, None
    return record['status'], record['handle']


# =====================================


@typing.overload
def check_cluster_available(
    cluster_name: str,
    *,
    operation: str,
    check_cloud_vm_ray_backend: Literal[True] = True,
    dryrun: bool = ...,
) -> 'cloud_vm_ray_backend.CloudVmRayResourceHandle':
    ...


@typing.overload
def check_cluster_available(
    cluster_name: str,
    *,
    operation: str,
    check_cloud_vm_ray_backend: Literal[False],
    dryrun: bool = ...,
) -> backends.ResourceHandle:
    ...


@context_utils.cancellation_guard
def check_cluster_available(
    cluster_name: str,
    *,
    operation: str,
    check_cloud_vm_ray_backend: bool = True,
    dryrun: bool = False,
) -> backends.ResourceHandle:
    """Check if the cluster is available.

    Raises:
        exceptions.ClusterDoesNotExist: if the cluster does not exist.
        exceptions.ClusterNotUpError: if the cluster is not UP.
        exceptions.NotSupportedError: if the cluster is not based on
          CloudVmRayBackend.
        exceptions.ClusterOwnerIdentityMismatchError: if the current user is
          not the same as the user who created the cluster.
        exceptions.CloudUserIdentityError: if we fail to get the current user
          identity.
    """
    record = global_user_state.get_cluster_from_name(cluster_name,
                                                     include_user_info=False,
                                                     summary_response=True)
    if dryrun:
        assert record is not None, cluster_name
        return record['handle']

    previous_cluster_status = None
    if record is not None:
        previous_cluster_status = record['status']

    try:
        cluster_status, handle = refresh_cluster_status_handle(cluster_name)
    except exceptions.ClusterStatusFetchingError as e:
        # Failed to refresh the cluster status is not fatal error as the callers
        # can still be done by only using ssh, but the ssh can hang if the
        # cluster is not up (e.g., autostopped).

        # We do not catch the exception for cloud identity checking for now, in
        # order to disable all operations on clusters created by another user
        # identity.  That will make the design simpler and easier to
        # understand, but it might be useful to allow the user to use
        # operations that only involve ssh (e.g., sky exec, sky logs, etc) even
        # if the user is not the owner of the cluster.
        ux_utils.console_newline()
        logger.warning(
            f'Failed to refresh the status for cluster {cluster_name!r}. It is '
            f'not fatal, but {operation} might hang if the cluster is not up.\n'
            f'Detailed reason: {e}')
        if record is None:
            cluster_status, handle = None, None
        else:
            cluster_status, handle = record['status'], record['handle']

    bright = colorama.Style.BRIGHT
    reset = colorama.Style.RESET_ALL
    if handle is None:
        if previous_cluster_status is None:
            error_msg = f'Cluster {cluster_name!r} does not exist.'
        else:
            error_msg = (f'Cluster {cluster_name!r} not found on the cloud '
                         'provider.')
            assert record is not None, previous_cluster_status
            actions = []
            if record['handle'].launched_resources.use_spot:
                actions.append('preempted')
            if record['autostop'] > 0 and record['to_down']:
                actions.append('autodowned')
            actions.append('manually terminated in console')
            if len(actions) > 1:
                actions[-1] = 'or ' + actions[-1]
            actions_str = ', '.join(actions)
            message = f' It was likely {actions_str}.'
            if len(actions) > 1:
                message = message.replace('likely', 'either')
            error_msg += message

        with ux_utils.print_exception_no_traceback():
            raise exceptions.ClusterDoesNotExist(
                f'{colorama.Fore.YELLOW}{error_msg}{reset}')
    assert cluster_status is not None, 'handle is not None but status is None'
    backend = get_backend_from_handle(handle)
    if check_cloud_vm_ray_backend and not isinstance(
            backend, backends.CloudVmRayBackend):
        with ux_utils.print_exception_no_traceback():
            raise exceptions.NotSupportedError(
                f'{colorama.Fore.YELLOW}{operation.capitalize()}: skipped for '
                f'cluster {cluster_name!r}. It is only supported by backend: '
                f'{backends.CloudVmRayBackend.NAME}.'
                f'{reset}')
    if cluster_status not in (status_lib.ClusterStatus.UP,
                              status_lib.ClusterStatus.AUTOSTOPPING):
        with ux_utils.print_exception_no_traceback():
            hint_for_init = ''
            if cluster_status == status_lib.ClusterStatus.INIT:
                hint_for_init = (
                    f'{reset} Wait for a launch to finish, or use this command '
                    f'to try to transition the cluster to UP: {bright}sky '
                    f'start {cluster_name}{reset}')
            raise exceptions.ClusterNotUpError(
                f'{colorama.Fore.YELLOW}{operation.capitalize()}: skipped for '
                f'cluster {cluster_name!r} (status: {cluster_status.value}). '
                'It is only allowed for '
                f'{status_lib.ClusterStatus.UP.value} and '
                f'{status_lib.ClusterStatus.AUTOSTOPPING.value} clusters.'
                f'{hint_for_init}'
                f'{reset}',
                cluster_status=cluster_status,
                handle=handle)

    if handle.head_ip is None:
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ClusterNotUpError(
                f'Cluster {cluster_name!r} has been stopped or not properly '
                'set up. Please re-launch it with `sky start`.',
                cluster_status=cluster_status,
                handle=handle)
    return handle


# TODO(tian): Refactor to controller_utils. Current blocker: circular import.
def is_controller_accessible(
    controller: controller_utils.Controllers,
    stopped_message: str,
    non_existent_message: Optional[str] = None,
    exit_if_not_accessible: bool = False,
) -> 'backends.CloudVmRayResourceHandle':
    """Check if the jobs/serve controller is up.

    The controller is accessible when it is in UP or INIT state, and the ssh
    connection is successful.

    It can be used to check if the controller is accessible (since the autostop
    is set for the controller) before the jobs/serve commands interact with the
    controller.

    ClusterNotUpError will be raised whenever the controller cannot be accessed.

    Args:
        type: Type of the controller.
        stopped_message: Message to print if the controller is STOPPED.
        non_existent_message: Message to show if the controller does not exist.
        exit_if_not_accessible: Whether to exit directly if the controller is not
          accessible. If False, the function will raise ClusterNotUpError.

    Returns:
        handle: The ResourceHandle of the controller.

    Raises:
        exceptions.ClusterOwnerIdentityMismatchError: if the current user is not
          the same as the user who created the cluster.
        exceptions.CloudUserIdentityError: if we fail to get the current user
          identity.
        exceptions.ClusterNotUpError: if the controller is not accessible, or
          failed to be connected.
    """
    if (managed_job_utils.is_consolidation_mode() and
            controller == controller_utils.Controllers.JOBS_CONTROLLER
       ) or (serve_utils.is_consolidation_mode() and
             controller == controller_utils.Controllers.SKY_SERVE_CONTROLLER):
        cn = 'local-controller-consolidation'
        return backends.LocalResourcesHandle(
            cluster_name=cn,
            cluster_name_on_cloud=cn,
            cluster_yaml=None,
            launched_nodes=1,
            launched_resources=sky.Resources(cloud=clouds.Cloud(),
                                             instance_type=cn),
        )
    if non_existent_message is None:
        non_existent_message = controller.value.default_hint_if_non_existent
    cluster_name = controller.value.cluster_name
    need_connection_check = False
    controller_status, handle = None, None
    try:
        # Set force_refresh_statuses=[INIT] to make sure the refresh happens
        # when the controller is INIT/UP (triggered in these statuses as the
        # autostop is always set for the controller). The controller can be in
        # following cases:
        # * (UP, autostop set): it will be refreshed without force_refresh set.
        # * (UP, no autostop): very rare (a user ctrl-c when the controller is
        #   launching), does not matter if refresh or not, since no autostop. We
        #   don't include UP in force_refresh_statuses to avoid overheads.
        # * (INIT, autostop set)
        # * (INIT, no autostop): very rare (_update_cluster_status_no_lock may
        #   reset local autostop config), but force_refresh will make sure
        #   status is refreshed.
        #
        # We avoids unnecessary costly refresh when the controller is already
        # STOPPED. This optimization is based on the assumption that the user
        # will not start the controller manually from the cloud console.
        #
        # The acquire_lock_timeout is set to 0 to avoid hanging the command when
        # multiple jobs.launch commands are running at the same time. Our later
        # code will check if the controller is accessible by directly checking
        # the ssh connection to the controller, if it fails to get accurate
        # status of the controller.
        controller_status, handle = refresh_cluster_status_handle(
            cluster_name,
            force_refresh_statuses=[status_lib.ClusterStatus.INIT],
            cluster_status_lock_timeout=0)
    except exceptions.ClusterStatusFetchingError as e:
        # We do not catch the exceptions related to the cluster owner identity
        # mismatch, please refer to the comment in
        # `backend_utils.check_cluster_available`.
        controller_name = controller.value.name.replace(' controller', '')
        logger.warning(
            'Failed to get the status of the controller. It is not '
            f'fatal, but {controller_name} commands/calls may hang or return '
            'stale information, when the controller is not up.\n'
            f'  Details: {common_utils.format_exception(e, use_bracket=True)}')
        record = global_user_state.get_cluster_from_name(
            cluster_name, include_user_info=False, summary_response=True)
        if record is not None:
            controller_status, handle = record['status'], record['handle']
            # We check the connection even if the cluster has a cached status UP
            # to make sure the controller is actually accessible, as the cached
            # status might be stale.
            need_connection_check = True

    error_msg = None
    if controller_status == status_lib.ClusterStatus.STOPPED:
        error_msg = stopped_message
    elif controller_status is None or handle is None or handle.head_ip is None:
        # We check the controller is STOPPED before the check for handle.head_ip
        # None because when the controller is STOPPED, handle.head_ip can also
        # be None, but we only want to catch the case when the controller is
        # being provisioned at the first time and have no head_ip.
        error_msg = non_existent_message
    elif (controller_status == status_lib.ClusterStatus.INIT or
          need_connection_check):
        # Check ssh connection if (1) controller is in INIT state, or (2) we failed to fetch the
        # status, both of which can happen when controller's status lock is held by another `sky jobs launch` or
        # `sky serve up`. If we have controller's head_ip available and it is ssh-reachable,
        # we can allow access to the controller.
        ssh_credentials = ssh_credential_from_yaml(handle.cluster_yaml,
                                                   handle.docker_user,
                                                   handle.ssh_user)

        runner = command_runner.SSHCommandRunner(node=(handle.head_ip,
                                                       handle.head_ssh_port),
                                                 **ssh_credentials)
        if not runner.check_connection():
            error_msg = controller.value.connection_error_hint
    else:
        assert controller_status in (
            status_lib.ClusterStatus.UP,
            status_lib.ClusterStatus.AUTOSTOPPING), handle

    if error_msg is not None:
        if exit_if_not_accessible:
            sky_logging.print(error_msg)
            sys.exit(1)
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ClusterNotUpError(error_msg,
                                               cluster_status=controller_status,
                                               handle=handle)
    assert handle is not None and handle.head_ip is not None, (
        handle, controller_status)
    return handle


class CloudFilter(enum.Enum):
    # Filter for all types of clouds.
    ALL = 'all'
    # Filter for Sky's main clouds (aws, gcp, azure, docker).
    CLOUDS_AND_DOCKER = 'clouds-and-docker'
    # Filter for only local clouds.
    LOCAL = 'local'


def _get_glob_clusters(
        clusters: List[str],
        silent: bool = False,
        workspaces_filter: Optional[Dict[str, Any]] = None) -> List[str]:
    """Returns a list of clusters that match the glob pattern."""
    glob_clusters = []
    for cluster in clusters:
        glob_cluster = global_user_state.get_glob_cluster_names(
            cluster, workspaces_filter=workspaces_filter)
        if len(glob_cluster) == 0 and not silent:
            logger.info(f'Cluster {cluster} not found.')
        glob_clusters.extend(glob_cluster)
    return list(set(glob_clusters))


def _refresh_cluster(
        cluster_name: str,
        force_refresh_statuses: Optional[Set[status_lib.ClusterStatus]],
        include_user_info: bool = True,
        summary_response: bool = False) -> Optional[Dict[str, Any]]:
    try:
        record = refresh_cluster_record(
            cluster_name,
            force_refresh_statuses=force_refresh_statuses,
            cluster_lock_already_held=False,
            include_user_info=include_user_info,
            summary_response=summary_response)
    except (exceptions.ClusterStatusFetchingError,
            exceptions.CloudUserIdentityError,
            exceptions.ClusterOwnerIdentityMismatchError) as e:
        # Do not fail the entire refresh process. The caller will
        # handle the 'UNKNOWN' status, and collect the errors into
        # a table.
        record = {'status': 'UNKNOWN', 'error': e}
    return record


def refresh_cluster_records() -> None:
    """Refreshes the status of all clusters, except managed clusters.

    Used by the background status refresh daemon.
    This function is a stripped-down version of get_clusters, with only the
    bare bones refresh logic.

    Returns:
        None

    Raises:
        None
    """
    # We force to exclude managed clusters to avoid multiple sources
    # manipulating them. For example, SkyServe assumes the replica manager
    # is the only source of truth for the cluster status.
    cluster_names = set(
        global_user_state.get_cluster_names(exclude_managed_clusters=True))

    # TODO(syang): we should try not to leak
    # request info in backend_utils.py.
    # Refactor this to use some other info to
    # determine if a launch is in progress.
    cluster_names_with_launch_request = {
        request.cluster_name for request in requests_lib.get_request_tasks(
            req_filter=requests_lib.RequestTaskFilter(
                status=[requests_lib.RequestStatus.RUNNING],
                include_request_names=['sky.launch'],
                fields=['cluster_name']))
    }
    cluster_names_without_launch_request = (cluster_names -
                                            cluster_names_with_launch_request)

    def _refresh_cluster_record(cluster_name):
        return _refresh_cluster(cluster_name,
                                force_refresh_statuses=set(
                                    status_lib.ClusterStatus),
                                include_user_info=False,
                                summary_response=True)

    if len(cluster_names_without_launch_request) > 0:
        # Do not refresh the clusters that have an active launch request.
        subprocess_utils.run_in_parallel(_refresh_cluster_record,
                                         cluster_names_without_launch_request)


def _get_records_with_handle(
        records: List[Optional[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Filter for records that have a handle"""
    return [
        record for record in records
        if record is not None and record['handle'] is not None
    ]


def _update_records_with_handle_info(records_with_handle: List[Dict[str, Any]],
                                     summary_response: bool = False) -> None:
    """Add resource str to record"""
    for record in records_with_handle:
        handle = record['handle']
        resource_str_simple, resource_str_full = (
            resources_utils.get_readable_resources_repr(handle,
                                                        simplified_only=False))
        record['resources_str'] = resource_str_simple
        record['resources_str_full'] = resource_str_full
        if not summary_response:
            record['cluster_name_on_cloud'] = handle.cluster_name_on_cloud


def get_clusters(
    refresh: common.StatusRefreshMode,
    cluster_names: Optional[Union[str, List[str]]] = None,
    all_users: bool = True,
    include_credentials: bool = False,
    summary_response: bool = False,
    include_handle: bool = True,
    # Internal only:
    # pylint: disable=invalid-name
    _include_is_managed: bool = False,
) -> List[Dict[str, Any]]:
    """Returns a list of cached or optionally refreshed cluster records.

    Combs through the database (in ~/.sky/state.db) to get a list of records
    corresponding to launched clusters (filtered by `cluster_names` if it is
    specified). The refresh flag can be used to force a refresh of the status
    of the clusters.

    Args:
        refresh: Whether to refresh the status of the clusters. (Refreshing will
            set the status to STOPPED if the cluster cannot be pinged.)
        cluster_names: If provided, only return records for the given cluster
            names.
        all_users: If True, return clusters from all users. If False, only
            return clusters from the current user.
        include_credentials: If True, include cluster ssh credentials in the
            return value.
        _include_is_managed: Whether to force include clusters created by the
            controller.

    Returns:
        A list of cluster records. If the cluster does not exist or has been
        terminated, the record will be omitted from the returned list.
    """
    accessible_workspaces = workspaces_core.get_workspaces()
    if cluster_names is not None:
        if isinstance(cluster_names, str):
            cluster_names = [cluster_names]
        non_glob_cluster_names = []
        glob_cluster_names = []
        for cluster_name in cluster_names:
            if ux_utils.is_glob_pattern(cluster_name):
                glob_cluster_names.append(cluster_name)
            else:
                non_glob_cluster_names.append(cluster_name)
        cluster_names = non_glob_cluster_names
        if glob_cluster_names:
            cluster_names += _get_glob_clusters(
                glob_cluster_names,
                silent=True,
                workspaces_filter=accessible_workspaces)

    exclude_managed_clusters = False
    if not (_include_is_managed or env_options.Options.SHOW_DEBUG_INFO.get()):
        exclude_managed_clusters = True
    user_hashes_filter = None
    if not all_users:
        user_hashes_filter = {common_utils.get_current_user().id}
    records = global_user_state.get_clusters(
        exclude_managed_clusters=exclude_managed_clusters,
        user_hashes_filter=user_hashes_filter,
        workspaces_filter=accessible_workspaces,
        cluster_names=cluster_names,
        summary_response=summary_response)

    yellow = colorama.Fore.YELLOW
    bright = colorama.Style.BRIGHT
    reset = colorama.Style.RESET_ALL

    if cluster_names is not None:
        record_names = {record['name'] for record in records}
        not_found_clusters = ux_utils.get_non_matched_query(
            cluster_names, record_names)
        if not_found_clusters:
            clusters_str = ', '.join(not_found_clusters)
            logger.info(f'Cluster(s) not found: {bright}{clusters_str}{reset}.')

    def _update_records_with_credentials(
            records: List[Optional[Dict[str, Any]]]) -> None:
        """Add the credentials to the record.

        This is useful for the client side to setup the ssh config of the
        cluster.
        """
        records_with_handle = _get_records_with_handle(records)
        if len(records_with_handle) == 0:
            return

        handles = [record['handle'] for record in records_with_handle]
        credentials = ssh_credentials_from_handles(handles)
        cached_private_keys: Dict[str, str] = {}
        for record, credential in zip(records_with_handle, credentials):
            if not credential:
                continue
            ssh_private_key_path = credential.get('ssh_private_key', None)
            if ssh_private_key_path is not None:
                expanded_private_key_path = os.path.expanduser(
                    ssh_private_key_path)
                if not os.path.exists(expanded_private_key_path):
                    success = auth_utils.create_ssh_key_files_from_db(
                        ssh_private_key_path)
                    if not success:
                        # If the ssh key files are not found, we do not
                        # update the record with credentials.
                        logger.debug(
                            f'SSH keys not found for cluster {record["name"]} '
                            f'at key path {ssh_private_key_path}')
                        continue
            else:
                private_key_path, _ = auth_utils.get_or_generate_keys()
                expanded_private_key_path = os.path.expanduser(private_key_path)
            if expanded_private_key_path in cached_private_keys:
                credential['ssh_private_key_content'] = cached_private_keys[
                    expanded_private_key_path]
            else:
                with open(expanded_private_key_path, 'r',
                          encoding='utf-8') as f:
                    credential['ssh_private_key_content'] = f.read()
                    cached_private_keys[expanded_private_key_path] = credential[
                        'ssh_private_key_content']
            record['credentials'] = credential

    def _update_records_with_resources(
        records: List[Optional[Dict[str, Any]]],) -> None:
        """Add the resources to the record."""
        for record in _get_records_with_handle(records):
            handle = record['handle']
            record['nodes'] = handle.launched_nodes
            if handle.launched_resources is None:
                # Set default values when launched_resources is None
                record['labels'] = {}
                continue
            record['cloud'] = (f'{handle.launched_resources.cloud}'
                               if handle.launched_resources.cloud else None)
            record['region'] = (f'{handle.launched_resources.region}'
                                if handle.launched_resources.region else None)
            record['cpus'] = (f'{handle.launched_resources.cpus}'
                              if handle.launched_resources.cpus else None)
            record['memory'] = (f'{handle.launched_resources.memory}'
                                if handle.launched_resources.memory else None)
            record['accelerators'] = (
                f'{handle.launched_resources.accelerators}'
                if handle.launched_resources.accelerators else None)
            record['labels'] = (handle.launched_resources.labels
                                if handle.launched_resources.labels else {})
            if not include_handle:
                record.pop('handle', None)

    # Add handle info to the records
    _update_records_with_handle_info(_get_records_with_handle(records),
                                     summary_response=summary_response)
    if include_credentials:
        _update_records_with_credentials(records)
    if refresh == common.StatusRefreshMode.NONE:
        # Add resources to the records
        _update_records_with_resources(records)
        return records

    plural = 's' if len(records) > 1 else ''
    progress = rich_progress.Progress(transient=True,
                                      redirect_stdout=False,
                                      redirect_stderr=False)
    task = progress.add_task(ux_utils.spinner_message(
        f'Refreshing status for {len(records)} cluster{plural}'),
                             total=len(records))

    if refresh == common.StatusRefreshMode.FORCE:
        force_refresh_statuses = set(status_lib.ClusterStatus)
    else:
        force_refresh_statuses = None

    def _refresh_cluster_record(cluster_name):
        record = _refresh_cluster(cluster_name,
                                  force_refresh_statuses=force_refresh_statuses,
                                  include_user_info=True,
                                  summary_response=summary_response)
        # record may be None if the cluster is deleted during refresh,
        # e.g. all the Pods of a cluster on Kubernetes have been
        # deleted before refresh.
        if record is not None and 'error' not in record:
            if record['handle'] is not None:
                _update_records_with_handle_info(
                    [record], summary_response=summary_response)
            if include_credentials:
                _update_records_with_credentials([record])
            progress.update(task, advance=1)
        return record

    cluster_names = [record['name'] for record in records]
    # TODO(syang): we should try not to leak
    # request info in backend_utils.py.
    # Refactor this to use some other info to
    # determine if a launch is in progress.
    cluster_names_with_launch_request = {
        request.cluster_name for request in requests_lib.get_request_tasks(
            req_filter=requests_lib.RequestTaskFilter(
                status=[requests_lib.RequestStatus.RUNNING],
                include_request_names=['sky.launch'],
                cluster_names=cluster_names,
                fields=['cluster_name']))
    }
    # Preserve the index of the cluster name as it appears on "records"
    cluster_names_without_launch_request = [
        (i, cluster_name)
        for i, cluster_name in enumerate(cluster_names)
        if cluster_name not in cluster_names_with_launch_request
    ]
    # for clusters that have an active launch request, we do not refresh the status
    updated_records = []
    if len(cluster_names_without_launch_request) > 0:
        with progress:
            updated_records = subprocess_utils.run_in_parallel(
                _refresh_cluster_record, [
                    cluster_name
                    for _, cluster_name in cluster_names_without_launch_request
                ])
    # Preserve the index of the cluster name as it appears on "records"
    # before filtering for clusters being launched.
    updated_records_dict: Dict[int, Optional[Dict[str, Any]]] = {
        cluster_names_without_launch_request[i][0]: updated_records[i]
        for i in range(len(cluster_names_without_launch_request))
    }
    # Show information for removed clusters.
    kept_records = []
    autodown_clusters, remaining_clusters, failed_clusters = [], [], []
    for i, record in enumerate(records):
        if i not in updated_records_dict:
            # record was not refreshed, keep the original record
            kept_records.append(record)
            continue
        updated_record = updated_records_dict[i]
        if updated_record is None:
            if record['to_down']:
                autodown_clusters.append(record['name'])
            else:
                remaining_clusters.append(record['name'])
        elif updated_record['status'] == 'UNKNOWN':
            failed_clusters.append((record['name'], updated_record['error']))
            # Keep the original record if the status is unknown,
            # so that the user can still see the cluster.
            kept_records.append(record)
        else:
            kept_records.append(updated_record)

    if autodown_clusters:
        plural = 's' if len(autodown_clusters) > 1 else ''
        cluster_str = ', '.join(autodown_clusters)
        logger.info(f'Autodowned cluster{plural}: '
                    f'{bright}{cluster_str}{reset}')
    if remaining_clusters:
        plural = 's' if len(remaining_clusters) > 1 else ''
        cluster_str = ', '.join(name for name in remaining_clusters)
        logger.warning(f'{yellow}Cluster{plural} terminated on '
                       f'the cloud: {reset}{bright}{cluster_str}{reset}')

    if failed_clusters:
        plural = 's' if len(failed_clusters) > 1 else ''
        logger.warning(f'{yellow}Failed to refresh status for '
                       f'{len(failed_clusters)} cluster{plural}:{reset}')
        for cluster_name, e in failed_clusters:
            logger.warning(f'  {bright}{cluster_name}{reset}: {e}')

    # Add resources to the records
    _update_records_with_resources(kept_records)
    return kept_records


@typing.overload
def get_backend_from_handle(
    handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle'
) -> 'cloud_vm_ray_backend.CloudVmRayBackend':
    ...


@typing.overload
def get_backend_from_handle(
    handle: 'local_docker_backend.LocalDockerResourceHandle'
) -> 'local_docker_backend.LocalDockerBackend':
    ...


@typing.overload
def get_backend_from_handle(
        handle: backends.ResourceHandle) -> backends.Backend:
    ...


def get_backend_from_handle(
        handle: backends.ResourceHandle) -> backends.Backend:
    """Gets a Backend object corresponding to a handle.

    Inspects handle type to infer the backend used for the resource.
    """
    backend: backends.Backend
    if isinstance(handle, backends.CloudVmRayResourceHandle):
        backend = backends.CloudVmRayBackend()
    elif isinstance(handle, backends.LocalDockerResourceHandle):
        backend = backends.LocalDockerBackend()
    else:
        raise NotImplementedError(
            f'Handle type {type(handle)} is not supported yet.')
    return backend


def get_task_demands_dict(task: 'task_lib.Task') -> Dict[str, float]:
    """Returns the resources dict of the task.

    Returns:
        A dict of the resources of the task. The keys are the resource names
        and the values are the number of the resources. It always contains
        the CPU resource (to control the maximum number of tasks), and
        optionally accelerator demands.
    """
    # TODO: Custom CPU and other memory resources are not supported yet.
    # For sky jobs/serve controller task, we set the CPU resource to a smaller
    # value to support a larger number of managed jobs and services.
    resources_dict = {
        'CPU': (constants.CONTROLLER_PROCESS_CPU_DEMAND
                if task.is_controller_task() else DEFAULT_TASK_CPU_DEMAND)
    }
    if task.best_resources is not None:
        resources = task.best_resources
    else:
        # Task may (e.g., sky launch) or may not (e.g., sky exec) have undergone
        # sky.optimize(), so best_resources may be None.
        assert len(task.resources) == 1, task.resources
        resources = list(task.resources)[0]
    if resources is not None and resources.accelerators is not None:
        resources_dict.update(resources.accelerators)
    return resources_dict


def get_task_resources_str(task: 'task_lib.Task',
                           is_managed_job: bool = False) -> str:
    """Returns the resources string of the task.

    The resources string is only used as a display purpose, so we only show
    the accelerator demands (if any). Otherwise, the CPU demand is shown.
    """
    spot_str = ''
    is_controller_task = task.is_controller_task()
    task_cpu_demand = (str(constants.CONTROLLER_PROCESS_CPU_DEMAND)
                       if is_controller_task else str(DEFAULT_TASK_CPU_DEMAND))
    if is_controller_task:
        resources_str = f'CPU:{task_cpu_demand}'
    elif task.best_resources is not None:
        accelerator_dict = task.best_resources.accelerators
        if is_managed_job:
            if task.best_resources.use_spot:
                spot_str = '[Spot]'
            assert task.best_resources.cpus is not None
            task_cpu_demand = task.best_resources.cpus
        if accelerator_dict is None:
            resources_str = f'CPU:{task_cpu_demand}'
        else:
            resources_str = ', '.join(
                f'{k}:{v}' for k, v in accelerator_dict.items())
    else:
        resource_accelerators = []
        min_cpus = float('inf')
        spot_type: Set[str] = set()
        for resource in task.resources:
            task_cpu_demand = '1+'
            if resource.cpus is not None:
                task_cpu_demand = resource.cpus
            min_cpus = min(min_cpus, float(task_cpu_demand.strip('+ ')))
            if resource.use_spot:
                spot_type.add('Spot')
            else:
                spot_type.add('On-demand')

            if resource.accelerators is None:
                continue
            for k, v in resource.accelerators.items():
                resource_accelerators.append(f'{k}:{v}')

        if is_managed_job:
            if len(task.resources) > 1:
                task_cpu_demand = f'{min_cpus}+'
            if 'Spot' in spot_type:
                spot_str = '|'.join(sorted(spot_type))
                spot_str = f'[{spot_str}]'
        if resource_accelerators:
            resources_str = ', '.join(set(resource_accelerators))
        else:
            resources_str = f'CPU:{task_cpu_demand}'
    resources_str = f'{task.num_nodes}x[{resources_str}]{spot_str}'
    return resources_str


# Handle ctrl-c
def interrupt_handler(signum, frame):
    del signum, frame
    subprocess_utils.kill_children_processes()
    # Avoid using logger here, as it will print the stack trace for broken
    # pipe, when the output is piped to another program.
    print(f'{colorama.Style.DIM}Tip: The job will keep '
          f'running after Ctrl-C.{colorama.Style.RESET_ALL}')
    with ux_utils.print_exception_no_traceback():
        raise KeyboardInterrupt(exceptions.KEYBOARD_INTERRUPT_CODE)


# Handle ctrl-z
def stop_handler(signum, frame):
    del signum, frame
    subprocess_utils.kill_children_processes()
    # Avoid using logger here, as it will print the stack trace for broken
    # pipe, when the output is piped to another program.
    print(f'{colorama.Style.DIM}Tip: The job will keep '
          f'running after Ctrl-Z.{colorama.Style.RESET_ALL}')
    with ux_utils.print_exception_no_traceback():
        raise KeyboardInterrupt(exceptions.SIGTSTP_CODE)


def check_rsync_installed() -> None:
    """Checks if rsync is installed.

    Raises:
        RuntimeError: if rsync is not installed in the machine.
    """
    try:
        subprocess.run('rsync --version',
                       shell=True,
                       check=True,
                       stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)
    except subprocess.CalledProcessError:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                '`rsync` is required for provisioning and'
                ' it is not installed. For Debian/Ubuntu system, '
                'install it with:\n'
                '  $ sudo apt install rsync') from None


def check_stale_runtime_on_remote(returncode: int, stderr: str,
                                  cluster_name: str) -> None:
    """Raises RuntimeError if remote SkyPilot runtime needs to be updated.

    We detect this by parsing certain backward-incompatible error messages from
    `stderr`. Typically due to the local client version just got updated, and
    the remote runtime is an older version.
    """
    if returncode != 0:
        if 'SkyPilot runtime is too old' in stderr:
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError(
                    f'{colorama.Fore.RED}SkyPilot runtime needs to be updated '
                    f'on the remote cluster: {cluster_name}. To update, run '
                    '(existing jobs will not be interrupted): '
                    f'{colorama.Style.BRIGHT}sky start -f -y '
                    f'{cluster_name}{colorama.Style.RESET_ALL}'
                    f'\n--- Details ---\n{stderr.strip()}\n') from None


def get_endpoints(cluster: str,
                  port: Optional[Union[int, str]] = None,
                  skip_status_check: bool = False) -> Dict[int, str]:
    """Gets the endpoint for a given cluster and port number (endpoint).

    Args:
        cluster: The name of the cluster.
        port: The port number to get the endpoint for. If None, endpoints
            for all ports are returned.
        skip_status_check: Whether to skip the status check for the cluster.
            This is useful when the cluster is known to be in a INIT state
            and the caller wants to query the endpoints. Used by serve
            controller to query endpoints during cluster launch when multiple
            services may be getting launched in parallel (and as a result,
            the controller may be in INIT status due to a concurrent launch).

    Returns: A dictionary of port numbers to endpoints. If endpoint is None,
        the dictionary will contain all ports:endpoints exposed on the cluster.
        If the endpoint is not exposed yet (e.g., during cluster launch or
        waiting for cloud provider to expose the endpoint), an empty dictionary
        is returned.

    Raises:
        ValueError: if the port is invalid or the cloud provider does not
            support querying endpoints.
        exceptions.ClusterNotUpError: if the cluster is not in UP status.
    """
    # Cast endpoint to int if it is not None
    if port is not None:
        try:
            port = int(port)
        except ValueError:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Invalid endpoint {port!r}.') from None
    cluster_records = get_clusters(refresh=common.StatusRefreshMode.NONE,
                                   cluster_names=[cluster],
                                   _include_is_managed=True)
    if not cluster_records:
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ClusterNotUpError(
                f'Cluster {cluster!r} not found.', cluster_status=None)
    assert len(cluster_records) == 1, cluster_records
    cluster_record = cluster_records[0]
    if (not skip_status_check and cluster_record['status']
            not in (status_lib.ClusterStatus.UP,
                    status_lib.ClusterStatus.AUTOSTOPPING)):
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ClusterNotUpError(
                f'Cluster {cluster_record["name"]!r} '
                'is not in UP status.',
                cluster_status=cluster_record['status'],
                handle=cluster_record['handle'])
    handle = cluster_record['handle']
    if not isinstance(handle, backends.CloudVmRayResourceHandle):
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Querying IP address is not supported '
                             f'for cluster {cluster!r} with backend '
                             f'{get_backend_from_handle(handle).NAME}.')

    launched_resources = handle.launched_resources.assert_launchable()
    cloud = launched_resources.cloud
    try:
        cloud.check_features_are_supported(
            launched_resources, {clouds.CloudImplementationFeatures.OPEN_PORTS})
    except exceptions.NotSupportedError:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Querying endpoints is not supported '
                             f'for {cluster!r} on {cloud}.') from None

    config = global_user_state.get_cluster_yaml_dict(handle.cluster_yaml)
    port_details = provision_lib.query_ports(repr(cloud),
                                             handle.cluster_name_on_cloud,
                                             handle.launched_resources.ports,
                                             head_ip=handle.head_ip,
                                             provider_config=config['provider'])

    launched_resources = handle.launched_resources.assert_launchable()
    # Validation before returning the endpoints
    if port is not None:
        # If the requested endpoint was not to be exposed
        port_set = resources_utils.port_ranges_to_set(launched_resources.ports)
        if port not in port_set:
            logger.warning(f'Port {port} is not exposed on '
                           f'cluster {cluster!r}.')
            return {}
        # If the user requested a specific port endpoint, check if it is exposed
        if port not in port_details:
            error_msg = (f'Port {port} not exposed yet. '
                         f'{_ENDPOINTS_RETRY_MESSAGE} ')
            if launched_resources.cloud.is_same_cloud(clouds.Kubernetes()):
                # Add Kubernetes specific debugging info
                error_msg += kubernetes_utils.get_endpoint_debug_message(
                    launched_resources.region)
            logger.warning(error_msg)
            return {}
        return {port: port_details[port][0].url()}
    else:
        if not port_details:
            # If cluster had no ports to be exposed
            if launched_resources.ports is None:
                logger.warning(f'Cluster {cluster!r} does not have any '
                               'ports to be exposed.')
                return {}
            # Else ports have not been exposed even though they exist.
            # In this case, ask the user to retry.
            else:
                error_msg = (f'No endpoints exposed yet. '
                             f'{_ENDPOINTS_RETRY_MESSAGE} ')
                if launched_resources.cloud.is_same_cloud(clouds.Kubernetes()):
                    # Add Kubernetes specific debugging info
                    error_msg += kubernetes_utils.get_endpoint_debug_message(
                        launched_resources.region)
                logger.warning(error_msg)
                return {}
        return {
            port_num: urls[0].url() for port_num, urls in port_details.items()
        }


def cluster_status_lock_id(cluster_name: str) -> str:
    """Get the lock ID for cluster status operations."""
    return f'{cluster_name}_status'


def cluster_file_mounts_lock_id(cluster_name: str) -> str:
    """Get the lock ID for cluster file mounts operations."""
    return f'{cluster_name}_file_mounts'


def workspace_lock_id(workspace_name: str) -> str:
    """Get the lock ID for workspace operations."""
    return f'{workspace_name}_workspace'


def cluster_tunnel_lock_id(cluster_name: str) -> str:
    """Get the lock ID for cluster tunnel operations."""
    return f'{cluster_name}_ssh_tunnel'


def open_ssh_tunnel(head_runner: Union[command_runner.SSHCommandRunner,
                                       command_runner.KubernetesCommandRunner],
                    port_forward: Tuple[int, int]) -> subprocess.Popen:
    local_port, remote_port = port_forward
    if isinstance(head_runner, command_runner.SSHCommandRunner):
        # Disabling ControlMaster makes things easier to reason about
        # with respect to resource management/ownership,
        # as killing the process will close the tunnel too.
        head_runner.disable_control_master = True
        head_runner.port_forward_execute_remote_command = True

    # The default connect_timeout of 1s is too short for
    # connecting to clusters using a jump server.
    # We use NON_INTERACTIVE mode to avoid allocating a pseudo-tty,
    # which is counted towards non-idleness.
    cmd: List[str] = head_runner.port_forward_command(
        [(local_port, remote_port)],
        connect_timeout=5,
        ssh_mode=command_runner.SshMode.NON_INTERACTIVE)
    if isinstance(head_runner, command_runner.SSHCommandRunner):
        # cat so the command doesn't exit until we kill it
        cmd += [f'"echo {_ACK_MESSAGE} && cat"']
    cmd_str = ' '.join(cmd)
    logger.debug(f'Running port forward command: {cmd_str}')
    ssh_tunnel_proc = subprocess.Popen(cmd_str,
                                       shell=True,
                                       stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE,
                                       start_new_session=True,
                                       text=True)
    # Wait until we receive an ack from the remote cluster or
    # the SSH connection times out.
    queue: queue_lib.Queue = queue_lib.Queue()
    stdout_thread = threading.Thread(
        target=lambda queue, stdout: queue.put(stdout.readline()),
        args=(queue, ssh_tunnel_proc.stdout),
        daemon=True)
    stdout_thread.start()
    while ssh_tunnel_proc.poll() is None:
        try:
            ack = queue.get_nowait()
        except queue_lib.Empty:
            ack = None
            time.sleep(0.1)
            continue
        assert ack is not None
        if isinstance(
                head_runner,
                command_runner.SSHCommandRunner) and ack == f'{_ACK_MESSAGE}\n':
            break
        elif isinstance(head_runner, command_runner.KubernetesCommandRunner
                       ) and _FORWARDING_FROM_MESSAGE in ack:
            # On kind clusters, this error occurs if we make a request
            # immediately after the port-forward is established on a new pod:
            # "Unhandled Error" err="an error occurred forwarding ... -> 46590:
            # failed to execute portforward in network namespace
            # "/var/run/netns/cni-...": failed to connect to localhost:46590
            # inside namespace "...", IPv4: dial tcp4 127.0.0.1:46590:
            # connect: connection refused
            # So we need to poll the port on the pod to check if it is open.
            # We did not observe this with real Kubernetes clusters.
            timeout = 5
            port_check_cmd = (
                # We install netcat in our ray-node container,
                # so we can use it here.
                # (See kubernetes-ray.yml.j2)
                f'end=$((SECONDS+{timeout})); '
                f'while ! nc -z -w 1 localhost {remote_port}; do '
                'if (( SECONDS >= end )); then exit 1; fi; '
                'sleep 0.1; '
                'done')
            returncode, stdout, stderr = head_runner.run(port_check_cmd,
                                                         require_outputs=True,
                                                         stream_logs=False)
            if returncode != 0:
                try:
                    ssh_tunnel_proc.terminate()
                    ssh_tunnel_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    ssh_tunnel_proc.kill()
                    ssh_tunnel_proc.wait()
                finally:
                    error_msg = (f'Failed to check remote port {remote_port}')
                    if stdout:
                        error_msg += f'\n-- stdout --\n{stdout}\n'
                    raise exceptions.CommandError(returncode=returncode,
                                                  command=cmd_str,
                                                  error_msg=error_msg,
                                                  detailed_reason=stderr)
            break

    if ssh_tunnel_proc.poll() is not None:
        stdout, stderr = ssh_tunnel_proc.communicate()
        error_msg = 'Port forward failed'
        if stdout:
            error_msg += f'\n-- stdout --\n{stdout}\n'
        raise exceptions.CommandError(returncode=ssh_tunnel_proc.returncode,
                                      command=cmd_str,
                                      error_msg=error_msg,
                                      detailed_reason=stderr)
    return ssh_tunnel_proc


T = TypeVar('T')


def invoke_skylet_with_retries(func: Callable[..., T]) -> T:
    """Generic helper for making Skylet gRPC requests.

    This method handles the common pattern of:
    1. Try the gRPC request
    2. If SSH tunnel is closed, recreate it and retry
    """
    max_attempts = 5
    backoff = common_utils.Backoff(initial_backoff=0.5)
    last_exception: Optional[Exception] = None

    for _ in range(max_attempts):
        try:
            return func()
        except grpc.RpcError as e:
            last_exception = e
            _handle_grpc_error(e, backoff.current_backoff())

    raise RuntimeError(
        f'Failed to invoke Skylet after {max_attempts} attempts: {last_exception}'
    ) from last_exception


def invoke_skylet_streaming_with_retries(
        stream_func: Callable[..., Iterator[T]]) -> Iterator[T]:
    """Generic helper for making Skylet streaming gRPC requests."""
    max_attempts = 3
    backoff = common_utils.Backoff(initial_backoff=0.5)
    last_exception: Optional[Exception] = None

    for _ in range(max_attempts):
        try:
            for response in stream_func():
                yield response
            return
        except grpc.RpcError as e:
            last_exception = e
            _handle_grpc_error(e, backoff.current_backoff())

    raise RuntimeError(
        f'Failed to stream Skylet response after {max_attempts} attempts'
    ) from last_exception


def _handle_grpc_error(e: 'grpc.RpcError', current_backoff: float) -> None:
    if e.code() == grpc.StatusCode.INTERNAL:
        with ux_utils.print_exception_no_traceback():
            raise exceptions.SkyletInternalError(e.details())
    elif e.code() == grpc.StatusCode.UNAVAILABLE:
        time.sleep(current_backoff)
    elif e.code() == grpc.StatusCode.UNIMPLEMENTED or e.code(
    ) == grpc.StatusCode.UNKNOWN:
        # Handle backwards compatibility: old server doesn't implement this RPC.
        # Let the caller fall back to legacy execution.
        raise exceptions.SkyletMethodNotImplementedError(
            f'gRPC method not implemented on server, falling back to legacy execution: {e.details()}'
        )
    else:
        raise e
