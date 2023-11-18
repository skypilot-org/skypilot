"""Util constants/functions for the backends."""
from datetime import datetime
import enum
import getpass
import json
import os
import pathlib
import pprint
import re
import shlex
import subprocess
import tempfile
import textwrap
import time
import typing
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union
import uuid

import colorama
import filelock
import jinja2
from packaging import version
import requests
from requests import adapters
from requests.packages.urllib3.util import retry as retry_lib
import rich.progress as rich_progress
from typing_extensions import Literal
import yaml

import sky
from sky import authentication as auth
from sky import backends
from sky import check as sky_check
from sky import clouds
from sky import exceptions
from sky import global_user_state
from sky import provision as provision_lib
from sky import serve as serve_lib
from sky import sky_logging
from sky import skypilot_config
from sky import status_lib
from sky.backends import onprem_utils
from sky.provision import instance_setup
from sky.skylet import constants
from sky.skylet import log_lib
from sky.usage import usage_lib
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import controller_utils
from sky.utils import env_options
from sky.utils import rich_utils
from sky.utils import subprocess_utils
from sky.utils import timeline
from sky.utils import tpu_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky import resources
    from sky import task as task_lib
    from sky.backends import cloud_vm_ray_backend
    from sky.backends import local_docker_backend

logger = sky_logging.init_logger(__name__)

# NOTE: keep in sync with the cluster template 'file_mounts'.
SKY_REMOTE_APP_DIR = '~/.sky/sky_app'
SKY_RAY_YAML_REMOTE_PATH = '~/.sky/sky_ray.yml'
# Exclude subnet mask from IP address regex.
IP_ADDR_REGEX = r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?!/\d{1,2})\b'
SKY_REMOTE_PATH = '~/.sky/wheels'
SKY_USER_FILE_PATH = '~/.sky/generated'

BOLD = '\033[1m'
RESET_BOLD = '\033[0m'

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
WAIT_HEAD_NODE_IP_MAX_ATTEMPTS = 3

# We check network connection by going through _TEST_IP_LIST. We may need to
# check multiple IPs because some IPs may be blocked on certain networks.
# Fixed IP addresses are used to avoid DNS lookup blocking the check, for
# machine with no internet connection.
# Refer to: https://stackoverflow.com/questions/3764291/how-can-i-see-if-theres-an-available-and-active-network-connection-in-python # pylint: disable=line-too-long
_TEST_IP_LIST = ['https://1.1.1.1', 'https://8.8.8.8']

# Allow each CPU thread take 2 tasks.
# Note: This value cannot be too small, otherwise OOM issue may occur.
DEFAULT_TASK_CPU_DEMAND = 0.5

# Filelocks for the cluster status change.
CLUSTER_STATUS_LOCK_PATH = os.path.expanduser('~/.sky/.{}.lock')
CLUSTER_STATUS_LOCK_TIMEOUT_SECONDS = 20

# Filelocks for updating cluster's file_mounts.
CLUSTER_FILE_MOUNTS_LOCK_PATH = os.path.expanduser(
    '~/.sky/.{}_file_mounts.lock')
CLUSTER_FILE_MOUNTS_LOCK_TIMEOUT_SECONDS = 10

# Remote dir that holds our runtime files.
_REMOTE_RUNTIME_FILES_DIR = '~/.sky/.runtime_files'

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
    # AWS with new provisioner has docker_login_config in the
    # docker field, instead of the provider field.
    ('docker', 'docker_login_config'),
    # Other clouds
    ('provider', 'docker_login_config'),
    ('provider', 'firewall_rule'),
    ('provider', 'security_group', 'GroupName'),
    ('available_node_types', 'ray.head.default', 'node_config', 'UserData'),
    ('available_node_types', 'ray.worker.default', 'node_config', 'UserData'),
]


def is_ip(s: str) -> bool:
    """Returns whether this string matches IP_ADDR_REGEX."""
    return len(re.findall(IP_ADDR_REGEX, s)) == 1


def _get_yaml_path_from_cluster_name(cluster_name: str,
                                     prefix: str = SKY_USER_FILE_PATH) -> str:
    output_path = pathlib.Path(
        prefix).expanduser().resolve() / f'{cluster_name}.yml'
    os.makedirs(output_path.parents[0], exist_ok=True)
    return str(output_path)


def fill_template(template_name: str, variables: Dict,
                  output_path: str) -> None:
    """Create a file from a Jinja template and return the filename."""
    assert template_name.endswith('.j2'), template_name
    template_path = os.path.join(sky.__root_dir__, 'templates', template_name)
    if not os.path.exists(template_path):
        raise FileNotFoundError(f'Template "{template_name}" does not exist.')
    with open(template_path) as fin:
        template = fin.read()
    output_path = os.path.abspath(os.path.expanduser(output_path))
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Write out yaml config.
    j2_template = jinja2.Template(template)
    content = j2_template.render(**variables)
    with open(output_path, 'w') as fout:
        fout.write(content)


def _optimize_file_mounts(yaml_path: str) -> None:
    """Optimize file mounts in the given ray yaml file.

    Runtime files handling:
    List of runtime files to be uploaded to cluster:
      - yaml config (for autostopping)
      - wheel
      - credentials
    Format is {dst: src}.
    """
    yaml_config = common_utils.read_yaml(yaml_path)

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
    local_runtime_files_dir = tempfile.mkdtemp()
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
        mv = (f'cp -r {_REMOTE_RUNTIME_FILES_DIR}/{src_basename} '
              f'{dst_parent_dir}/{dst_basename}')
        fragment = f'({mkdir_parent} && {mv})'
        commands.append(fragment)
    postprocess_runtime_files_command = ' && '.join(commands)

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

    common_utils.dump_yaml(yaml_path, yaml_config)


def path_size_megabytes(path: str) -> int:
    """Returns the size of 'path' (directory or file) in megabytes.

    Returns:
        If successful: the size of 'path' in megabytes, rounded down. Otherwise,
        -1.
    """
    resolved_path = pathlib.Path(path).expanduser().resolve()
    git_exclude_filter = ''
    if (resolved_path / command_runner.GIT_EXCLUDE).exists():
        # Ensure file exists; otherwise, rsync will error out.
        #
        # We shlex.quote() because the path may contain spaces:
        #   'my dir/.git/info/exclude'
        # Without quoting rsync fails.
        git_exclude_filter = command_runner.RSYNC_EXCLUDE_OPTION.format(
            shlex.quote(str(resolved_path / command_runner.GIT_EXCLUDE)))
    rsync_command = (f'rsync {command_runner.RSYNC_DISPLAY_OPTION} '
                     f'{command_runner.RSYNC_FILTER_OPTION} '
                     f'{git_exclude_filter} --dry-run {path!r}')
    rsync_output = ''
    try:
        rsync_output = str(subprocess.check_output(rsync_command, shell=True))
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

        All intermediate directories of 'source' will be owned by $USER,
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
        #  1. make sure its dir(s) exist & are owned by $USER.
        dir_of_symlink = os.path.dirname(source)
        commands = [
            # mkdir, then loop over '/a/b/c' as /a, /a/b, /a/b/c.  For each,
            # chown $USER on it so user can use these intermediate dirs
            # (excluding /).
            f'sudo mkdir -p {dir_of_symlink}',
            # p: path so far
            ('(p=""; '
             f'for w in $(echo {dir_of_symlink} | tr "/" " "); do '
             'p=${p}/${w}; sudo chown $USER $p; done)')
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
            f'sudo chown -h $USER {source}',
        ]
        return ' && '.join(commands)


class SSHConfigHelper(object):
    """Helper for handling local SSH configuration."""

    ssh_conf_path = '~/.ssh/config'
    ssh_conf_lock_path = os.path.expanduser('~/.sky/ssh_config.lock')
    ssh_multinode_path = SKY_USER_FILE_PATH + '/ssh/{}'

    @classmethod
    def _get_generated_config(cls, autogen_comment: str, host_name: str,
                              ip: str, username: str, ssh_key_path: str,
                              proxy_command: Optional[str], port: int,
                              docker_proxy_command: Optional[str]):
        if proxy_command is not None:
            # Already checked in resources
            assert docker_proxy_command is None, (
                'Cannot specify both proxy_command and docker_proxy_command.')
            proxy = f'ProxyCommand {proxy_command}'
        elif docker_proxy_command is not None:
            proxy = f'ProxyCommand {docker_proxy_command}'
        else:
            proxy = ''
        # StrictHostKeyChecking=no skips the host key check for the first
        # time. UserKnownHostsFile=/dev/null and GlobalKnownHostsFile/dev/null
        # prevent the host key from being added to the known_hosts file and
        # always return an empty file for known hosts, making the ssh think
        # this is a first-time connection, and thus skipping the host key
        # check.
        codegen = textwrap.dedent(f"""\
            {autogen_comment}
            Host {host_name}
              HostName {ip}
              User {username}
              IdentityFile {ssh_key_path}
              IdentitiesOnly yes
              ForwardAgent yes
              StrictHostKeyChecking no
              UserKnownHostsFile=/dev/null
              GlobalKnownHostsFile=/dev/null
              Port {port}
              {proxy}
            """.rstrip())
        codegen = codegen + '\n'
        return codegen

    @classmethod
    @timeline.FileLockEvent(ssh_conf_lock_path)
    def add_cluster(
        cls,
        cluster_name: str,
        ips: List[str],
        auth_config: Dict[str, str],
        ports: List[int],
        docker_user: Optional[str] = None,
    ):
        """Add authentication information for cluster to local SSH config file.

        If a host with `cluster_name` already exists and the configuration was
        not added by sky, then `ip` is used to identify the host instead in the
        file.

        If a host with `cluster_name` already exists and the configuration was
        added by sky (e.g. a spot instance), then the configuration is
        overwritten.

        Args:
            cluster_name: Cluster name (see `sky status`)
            ips: List of public IP addresses in the cluster. First IP is head
              node.
            auth_config: read_yaml(handle.cluster_yaml)['auth']
            ports: List of port numbers for SSH corresponding to ips
            docker_user: If not None, use this user to ssh into the docker
        """
        username = auth_config['ssh_user']
        if docker_user is not None:
            username = docker_user
        key_path = os.path.expanduser(auth_config['ssh_private_key'])
        host_name = cluster_name
        sky_autogen_comment = ('# Added by sky (use `sky stop/down '
                               f'{cluster_name}` to remove)')
        overwrite = False
        overwrite_begin_idx = None
        ip = ips[0]
        if docker_user is not None:
            ip = 'localhost'

        config_path = os.path.expanduser(cls.ssh_conf_path)
        if os.path.exists(config_path):
            with open(config_path) as f:
                config = f.readlines()

            # If an existing config with `cluster_name` exists, raise a warning.
            for i, line in enumerate(config):
                if line.strip() == f'Host {cluster_name}':
                    prev_line = config[i - 1] if i - 1 >= 0 else ''
                    if prev_line.strip().startswith(sky_autogen_comment):
                        overwrite = True
                        overwrite_begin_idx = i - 1
                    else:
                        logger.warning(f'{cls.ssh_conf_path} contains '
                                       f'host named {cluster_name}.')
                        host_name = ip
                        logger.warning(f'Using {ip} to identify host instead.')

                if line.strip() == f'Host {ip}':
                    prev_line = config[i - 1] if i - 1 >= 0 else ''
                    if prev_line.strip().startswith(sky_autogen_comment):
                        overwrite = True
                        overwrite_begin_idx = i - 1
        else:
            config = ['\n']
            with open(config_path, 'w') as f:
                f.writelines(config)
            os.chmod(config_path, 0o644)

        proxy_command = auth_config.get('ssh_proxy_command', None)
        docker_proxy_command = None
        head_port = ports[0]
        if docker_user is not None:
            docker_proxy_command = ' '.join(
                ['ssh'] + command_runner.ssh_options_list(key_path, None) +
                ['-W', '%h:%p', f'{auth_config["ssh_user"]}@{ips[0]}'])
            head_port = constants.DEFAULT_DOCKER_PORT
        codegen = cls._get_generated_config(sky_autogen_comment, host_name, ip,
                                            username, key_path, proxy_command,
                                            head_port, docker_proxy_command)

        # Add (or overwrite) the new config.
        if overwrite:
            assert overwrite_begin_idx is not None
            updated_lines = codegen.splitlines(keepends=True) + ['\n']
            config[overwrite_begin_idx:overwrite_begin_idx +
                   len(updated_lines)] = updated_lines
            with open(config_path, 'w') as f:
                f.write(''.join(config).strip())
                f.write('\n' * 2)
        else:
            with open(config_path, 'a') as f:
                if len(config) > 0 and config[-1] != '\n':
                    f.write('\n')
                f.write(codegen)
                f.write('\n')

        with open(config_path, 'r+') as f:
            config = f.readlines()
            if config[-1] != '\n':
                f.write('\n')

        if len(ips) > 1:
            SSHConfigHelper._add_multinode_config(cluster_name, ips[1:],
                                                  auth_config, docker_user)

    @classmethod
    def _add_multinode_config(
        cls,
        cluster_name: str,
        external_worker_ips: List[str],
        auth_config: Dict[str, str],
        docker_user: Optional[str] = None,
    ):
        username = auth_config['ssh_user']
        if docker_user is not None:
            username = docker_user
        key_path = os.path.expanduser(auth_config['ssh_private_key'])
        host_name = cluster_name
        sky_autogen_comment = ('# Added by sky (use `sky stop/down '
                               f'{cluster_name}` to remove)')

        # Ensure stableness of the aliases worker-<i> by sorting based on
        # public IPs.
        external_worker_ips = list(sorted(external_worker_ips))
        port = 22
        if docker_user is not None:
            port = constants.DEFAULT_DOCKER_PORT

        overwrites = [False] * len(external_worker_ips)
        overwrite_begin_idxs: List[Optional[int]] = [None
                                                    ] * len(external_worker_ips)
        codegens: List[Optional[str]] = [None] * len(external_worker_ips)
        worker_names = []
        extra_path_name = cls.ssh_multinode_path.format(cluster_name)

        for idx in range(len(external_worker_ips)):
            worker_names.append(cluster_name + f'-worker{idx+1}')

        config_path = os.path.expanduser(cls.ssh_conf_path)
        with open(config_path) as f:
            config = f.readlines()

        extra_config_path = os.path.expanduser(extra_path_name)
        os.makedirs(os.path.dirname(extra_config_path), exist_ok=True)
        if not os.path.exists(extra_config_path):
            extra_config = ['\n']
            with open(extra_config_path, 'w') as f:
                f.writelines(extra_config)
        else:
            with open(extra_config_path) as f:
                extra_config = f.readlines()

        # Handle Include on top of Config file
        include_str = f'Include {extra_config_path}'
        for i, line in enumerate(config):
            config_str = line.strip()
            if config_str == include_str:
                break
            # Did not find Include string
            if 'Host' in config_str:
                with open(config_path, 'w') as f:
                    config.insert(0, '\n')
                    config.insert(0, include_str + '\n')
                    config.insert(0, sky_autogen_comment + '\n')
                    f.write(''.join(config).strip())
                    f.write('\n' * 2)
                break

        with open(config_path) as f:
            config = f.readlines()

        proxy_command = auth_config.get('ssh_proxy_command', None)
        if docker_user is not None:
            docker_proxy_command_generator = lambda ip: ' '.join(
                ['ssh'] + command_runner.ssh_options_list(key_path, None) +
                ['-W', '%h:%p', f'{auth_config["ssh_user"]}@{ip}'])
        docker_proxy_command = None

        # Check if ~/.ssh/config contains existing names
        host_lines = [f'Host {c_name}' for c_name in worker_names]
        for i, line in enumerate(config):
            if line.strip() in host_lines:
                idx = host_lines.index(line.strip())
                prev_line = config[i - 1] if i > 0 else ''
                logger.warning(f'{cls.ssh_conf_path} contains '
                               f'host named {worker_names[idx]}.')
                host_name = external_worker_ips[idx]
                logger.warning(f'Using {host_name} to identify host instead.')
                ip = external_worker_ips[idx]
                if docker_user is not None:
                    docker_proxy_command = docker_proxy_command_generator(ip)
                    ip = 'localhost'
                # TODO(romilb): Update port number when k8s supports multinode
                codegens[idx] = cls._get_generated_config(
                    sky_autogen_comment, host_name, ip, username, key_path,
                    proxy_command, port, docker_proxy_command)

        # All workers go to SKY_USER_FILE_PATH/ssh/{cluster_name}
        for i, line in enumerate(extra_config):
            if line.strip() in host_lines:
                idx = host_lines.index(line.strip())
                prev_line = extra_config[i - 1] if i > 0 else ''
                if prev_line.strip().startswith(sky_autogen_comment):
                    host_name = worker_names[idx]
                    overwrites[idx] = True
                    overwrite_begin_idxs[idx] = i - 1
                ip = external_worker_ips[idx]
                if docker_user is not None:
                    docker_proxy_command = docker_proxy_command_generator(ip)
                    ip = 'localhost'
                # TODO(romilb): Update port number when k8s supports multinode
                codegens[idx] = cls._get_generated_config(
                    sky_autogen_comment, host_name, ip, username, key_path,
                    proxy_command, port, docker_proxy_command)

        # This checks if all codegens have been created.
        for idx, ip in enumerate(external_worker_ips):
            if docker_user is not None:
                docker_proxy_command = docker_proxy_command_generator(ip)
                ip = 'localhost'
            if not codegens[idx]:
                codegens[idx] = cls._get_generated_config(
                    sky_autogen_comment, worker_names[idx], ip, username,
                    key_path, proxy_command, port, docker_proxy_command)

        for idx in range(len(external_worker_ips)):
            # Add (or overwrite) the new config.
            overwrite = overwrites[idx]
            overwrite_begin_idx = overwrite_begin_idxs[idx]
            codegen = codegens[idx]
            assert codegen is not None, (codegens, idx)
            if overwrite:
                assert overwrite_begin_idx is not None
                updated_lines = codegen.splitlines(keepends=True) + ['\n']
                extra_config[overwrite_begin_idx:overwrite_begin_idx +
                             len(updated_lines)] = updated_lines
                with open(extra_config_path, 'w') as f:
                    f.write(''.join(extra_config).strip())
                    f.write('\n' * 2)
            else:
                with open(extra_config_path, 'a') as f:
                    f.write(codegen)
                    f.write('\n')

        # Add trailing new line at the end of the file if it doesn't exit
        with open(extra_config_path, 'r+') as f:
            extra_config = f.readlines()
            if extra_config[-1] != '\n':
                f.write('\n')

    @classmethod
    @timeline.FileLockEvent(ssh_conf_lock_path)
    def remove_cluster(
        cls,
        cluster_name: str,
        ip: str,
        auth_config: Dict[str, str],
        docker_user: Optional[str] = None,
    ):
        """Remove authentication information for cluster from local SSH config.

        If no existing host matching the provided specification is found, then
        nothing is removed.

        Args:
            ip: Head node's IP address.
            auth_config: read_yaml(handle.cluster_yaml)['auth']
            docker_user: If not None, use this user to ssh into the docker
        """
        username = auth_config['ssh_user']
        config_path = os.path.expanduser(cls.ssh_conf_path)
        if not os.path.exists(config_path):
            return

        with open(config_path) as f:
            config = f.readlines()

        start_line_idx = None
        # Scan the config for the cluster name.
        for i, line in enumerate(config):
            next_line = config[i + 1] if i + 1 < len(config) else ''
            if docker_user is None:
                found = (line.strip() == f'HostName {ip}' and
                         next_line.strip() == f'User {username}')
            else:
                found = (line.strip() == 'HostName localhost' and
                         next_line.strip() == f'User {docker_user}')
                if found:
                    # Find the line starting with ProxyCommand and contains the ip
                    found = False
                    for idx in range(i, len(config)):
                        # Stop if we reach an empty line, which means a new host
                        if not config[idx].strip():
                            break
                        if config[idx].strip().startswith('ProxyCommand'):
                            proxy_command_line = config[idx].strip()
                            if proxy_command_line.endswith(f'@{ip}'):
                                found = True
                                break
            if found:
                start_line_idx = i - 1
                break

        if start_line_idx is None:  # No config to remove.
            return

        # Scan for end of previous config.
        cursor = start_line_idx
        while cursor > 0 and len(config[cursor].strip()) > 0:
            cursor -= 1
        prev_end_line_idx = cursor

        # Scan for end of the cluster config.
        end_line_idx = None
        cursor = start_line_idx + 1
        start_line_idx -= 1  # remove auto-generated comment
        while cursor < len(config):
            if config[cursor].strip().startswith(
                    '# ') or config[cursor].strip().startswith('Host '):
                end_line_idx = cursor
                break
            cursor += 1

        # Remove sky-generated config and update the file.
        config[prev_end_line_idx:end_line_idx] = [
            '\n'
        ] if end_line_idx is not None else []
        with open(config_path, 'w') as f:
            f.write(''.join(config).strip())
            f.write('\n' * 2)

        SSHConfigHelper._remove_multinode_config(cluster_name)

    @classmethod
    def _remove_multinode_config(
        cls,
        cluster_name: str,
    ):
        config_path = os.path.expanduser(cls.ssh_conf_path)
        if not os.path.exists(config_path):
            return

        extra_path_name = cls.ssh_multinode_path.format(cluster_name)
        extra_config_path = os.path.expanduser(extra_path_name)
        common_utils.remove_file_if_exists(extra_config_path)

        # Delete include statement
        sky_autogen_comment = ('# Added by sky (use `sky stop/down '
                               f'{cluster_name}` to remove)')
        with open(config_path) as f:
            config = f.readlines()

        for i, line in enumerate(config):
            config_str = line.strip()
            if f'Include {extra_config_path}' in config_str:
                with open(config_path, 'w') as f:
                    if i < len(config) - 1 and config[i + 1] == '\n':
                        del config[i + 1]
                    # Delete Include string
                    del config[i]
                    # Delete Sky Autogen Comment
                    if i > 0 and sky_autogen_comment in config[i - 1].strip():
                        del config[i - 1]
                    f.write(''.join(config))
                break
            if 'Host' in config_str:
                break


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

    new_config = yaml.safe_load(new_yaml)
    old_config = yaml.safe_load(old_yaml)
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
    return common_utils.dump_yaml_str(new_config)


# TODO: too many things happening here - leaky abstraction. Refactor.
@timeline.event
def write_cluster_config(
        to_provision: 'resources.Resources',
        num_nodes: int,
        cluster_config_template: str,
        cluster_name: str,
        local_wheel_path: pathlib.Path,
        wheel_hash: str,
        region: Optional[clouds.Region] = None,
        zones: Optional[List[clouds.Zone]] = None,
        dryrun: bool = False,
        keep_launch_fields_in_existing_config: bool = True) -> Dict[str, str]:
    """Fills in cluster configuration templates and writes them out.

    Returns: {provisioner: path to yaml, the provisioning spec}.
      'provisioner' can be
        - 'ray'
        - 'tpu-create-script' (if TPU is requested)
        - 'tpu-delete-script' (if TPU is requested)
    Raises:
        exceptions.ResourcesUnavailableError: if the region/zones requested does
            not appear in the catalog, or an ssh_proxy_command is specified but
            not for the given region, or GPUs are requested in a Kubernetes
            cluster but the cluster does not have nodes labeled with GPU types.
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
    resources_vars = to_provision.make_deploy_variables(cluster_name_on_cloud,
                                                        region, zones)
    config_dict = {}

    azure_subscription_id = None
    if isinstance(cloud, clouds.Azure):
        azure_subscription_id = cloud.get_project_id(dryrun=dryrun)

    gcp_project_id = None
    if isinstance(cloud, clouds.GCP):
        gcp_project_id = cloud.get_project_id(dryrun=dryrun)

    specific_reservations = set(
        skypilot_config.get_nested(('gcp', 'specific_reservations'), set()))

    reservations = to_provision.get_reservations_available_resources(
        specific_reservations)

    filtered_specific_reservations = [
        r for r, available_resources in reservations.items()
        if r in specific_reservations and available_resources > 0
    ]
    available_specific_reservations = sum(
        available_resources for r, available_resources in reservations.items()
        if r in specific_reservations)
    num_specific_reserved_workers = max(
        min(available_specific_reservations - 1, num_nodes - 1), 0)

    assert cluster_name is not None
    credentials = sky_check.get_cloud_credential_file_mounts()

    ip_list = None
    auth_config = {'ssh_private_key': auth.PRIVATE_SSH_KEY_PATH}
    if isinstance(cloud, clouds.Local):
        ip_list = onprem_utils.get_local_ips(cluster_name)
        auth_config = onprem_utils.get_local_auth_config(cluster_name)
    region_name = resources_vars.get('region')

    yaml_path = _get_yaml_path_from_cluster_name(cluster_name)

    # Retrieve the ssh_proxy_command for the given cloud / region.
    ssh_proxy_command_config = skypilot_config.get_nested(
        (str(cloud).lower(), 'ssh_proxy_command'), None)
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
    logger.debug(f'Using ssh_proxy_command: {ssh_proxy_command!r}')

    # User-supplied instance tags.
    instance_tags = {}
    instance_tags = skypilot_config.get_nested(
        (str(cloud).lower(), 'instance_tags'), {})
    if not isinstance(instance_tags, dict):
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Custom instance_tags in config.yaml should '
                             f'be a dict, but received {type(instance_tags)}.')

    # Dump the Ray ports to a file for Ray job submission
    dump_port_command = (
        f'python -c \'import json, os; json.dump({constants.SKY_REMOTE_RAY_PORT_DICT_STR}, '
        f'open(os.path.expanduser("{constants.SKY_REMOTE_RAY_PORT_FILE}"), "w"))\''
    )

    # Use a tmp file path to avoid incomplete YAML file being re-used in the
    # future.
    tmp_yaml_path = yaml_path + '.tmp'
    fill_template(
        cluster_config_template,
        dict(
            resources_vars,
            **{
                'cluster_name_on_cloud': cluster_name_on_cloud,
                'num_nodes': num_nodes,
                'disk_size': to_provision.disk_size,
                # If the current code is run by controller, propagate the real
                # calling user which should've been passed in as the
                # SKYPILOT_USER env var (see
                # execution.py::_shared_controller_env_vars).
                'user': get_cleaned_username(
                    os.environ.get(constants.USER_ENV_VAR, '')),

                # AWS only:
                'aws_vpc_name': skypilot_config.get_nested(('aws', 'vpc_name'),
                                                           None),
                'use_internal_ips': skypilot_config.get_nested(
                    ('aws', 'use_internal_ips'), False),
                # Not exactly AWS only, but we only test it's supported on AWS
                # for now:
                'ssh_proxy_command': ssh_proxy_command,
                # User-supplied instance tags.
                'instance_tags': instance_tags,

                # Azure only:
                'azure_subscription_id': azure_subscription_id,
                'resource_group': f'{cluster_name}-{region_name}',

                # GCP only:
                'gcp_vpc_name': skypilot_config.get_nested(('gcp', 'vpc_name'),
                                                           None),
                'gcp_project_id': gcp_project_id,
                'specific_reservations': filtered_specific_reservations,
                'num_specific_reserved_workers': num_specific_reserved_workers,

                # Conda setup
                'conda_installation_commands':
                    constants.CONDA_INSTALLATION_COMMANDS,

                # Port of Ray (GCS server).
                # Ray's default port 6379 is conflicted with Redis.
                'ray_port': constants.SKY_REMOTE_RAY_PORT,
                'ray_dashboard_port': constants.SKY_REMOTE_RAY_DASHBOARD_PORT,
                'ray_temp_dir': constants.SKY_REMOTE_RAY_TEMPDIR,
                'dump_port_command': dump_port_command,
                # Ray version.
                'ray_version': constants.SKY_REMOTE_RAY_VERSION,
                # Cloud credentials for cloud storage.
                'credentials': credentials,
                # Sky remote utils.
                'sky_remote_path': SKY_REMOTE_PATH,
                'sky_local_path': str(local_wheel_path),
                # Add yaml file path to the template variables.
                'sky_ray_yaml_remote_path': SKY_RAY_YAML_REMOTE_PATH,
                'sky_ray_yaml_local_path':
                    tmp_yaml_path
                    if not isinstance(cloud, clouds.Local) else yaml_path,
                'sky_version': str(version.parse(sky.__version__)),
                'sky_wheel_hash': wheel_hash,
                # Local IP handling (optional).
                'head_ip': None if ip_list is None else ip_list[0],
                'worker_ips': None if ip_list is None else ip_list[1:],
                # Authentication (optional).
                **auth_config,
            }),
        output_path=tmp_yaml_path)
    config_dict['cluster_name'] = cluster_name
    config_dict['ray'] = yaml_path
    if dryrun:
        # If dryrun, return the unfinished tmp yaml path.
        config_dict['ray'] = tmp_yaml_path
        return config_dict
    _add_auth_to_cluster_config(cloud, tmp_yaml_path)

    # Restore the old yaml content for backward compatibility.
    if os.path.exists(yaml_path) and keep_launch_fields_in_existing_config:
        with open(yaml_path, 'r') as f:
            old_yaml_content = f.read()
        with open(tmp_yaml_path, 'r') as f:
            new_yaml_content = f.read()
        restored_yaml_content = _replace_yaml_dicts(
            new_yaml_content, old_yaml_content,
            _RAY_YAML_KEYS_TO_RESTORE_FOR_BACK_COMPATIBILITY,
            _RAY_YAML_KEYS_TO_RESTORE_EXCEPTIONS)
        with open(tmp_yaml_path, 'w') as f:
            f.write(restored_yaml_content)

    # Read the cluster name from the tmp yaml file, to take the backward
    # compatbility restortion above into account.
    # TODO: remove this after 2 minor releases, 0.5.0.
    yaml_config = common_utils.read_yaml(tmp_yaml_path)
    config_dict['cluster_name_on_cloud'] = yaml_config['cluster_name']

    # Optimization: copy the contents of source files in file_mounts to a
    # special dir, and upload that as the only file_mount instead. Delay
    # calling this optimization until now, when all source files have been
    # written and their contents finalized.
    #
    # Note that the ray yaml file will be copied into that special dir (i.e.,
    # uploaded as part of the file_mounts), so the restore for backward
    # compatibility should go before this call.
    if not isinstance(cloud, clouds.Local):
        # Only optimize the file mounts for public clouds now, as local has not
        # been fully tested yet.
        _optimize_file_mounts(tmp_yaml_path)

    # Rename the tmp file to the final YAML path.
    os.rename(tmp_yaml_path, yaml_path)
    usage_lib.messages.usage.update_ray_yaml(yaml_path)

    # For TPU nodes. TPU VMs do not need TPU_NAME.
    if (resources_vars.get('tpu_type') is not None and
            resources_vars.get('tpu_vm') is None):
        tpu_name = resources_vars.get('tpu_name')
        if tpu_name is None:
            tpu_name = cluster_name

        user_file_dir = os.path.expanduser(f'{SKY_USER_FILE_PATH}/')

        # We do not import the module under sky.skylet.providers globally as we
        # need to avoid importing ray module (extras like skypilot[aws] has
        # removed the Ray dependency).
        # pylint: disable=import-outside-toplevel
        from sky.skylet.providers.gcp import config as gcp_config
        config = common_utils.read_yaml(os.path.expanduser(config_dict['ray']))
        vpc_name = None
        try:
            vpc_name, _ = gcp_config.get_usable_vpc_and_subnet(config)
        except RuntimeError as e:
            # Launching a TPU and encountering a bootstrap-phase error, no point
            # in failover unless:
            # TODO(zongheng): handle failover when multi-resource is added.
            with ux_utils.print_exception_no_traceback():
                raise e

        scripts = []
        for template_name in ('gcp-tpu-create.sh.j2', 'gcp-tpu-delete.sh.j2'):
            script_path = os.path.join(user_file_dir, template_name).replace(
                '.sh.j2', f'.{cluster_name}.sh')
            fill_template(
                template_name,
                dict(
                    resources_vars, **{
                        'tpu_name': tpu_name,
                        'gcp_project_id': gcp_project_id,
                        'vpc_name': vpc_name,
                    }),
                # Use new names for TPU scripts so that different runs can use
                # different TPUs.  Put in SKY_USER_FILE_PATH to be consistent
                # with cluster yamls.
                output_path=script_path,
            )
            scripts.append(script_path)

        config_dict['tpu-create-script'] = scripts[0]
        config_dict['tpu-delete-script'] = scripts[1]
        config_dict['tpu_name'] = tpu_name
    return config_dict


def _add_auth_to_cluster_config(cloud: clouds.Cloud, cluster_config_file: str):
    """Adds SSH key info to the cluster config.

    This function's output removes comments included in the jinja2 template.
    """
    config = common_utils.read_yaml(cluster_config_file)
    # Check the availability of the cloud type.
    if isinstance(cloud, (clouds.AWS, clouds.OCI, clouds.SCP)):
        config = auth.configure_ssh_info(config)
    elif isinstance(cloud, clouds.GCP):
        config = auth.setup_gcp_authentication(config)
    elif isinstance(cloud, clouds.Azure):
        config = auth.setup_azure_authentication(config)
    elif isinstance(cloud, clouds.Lambda):
        config = auth.setup_lambda_authentication(config)
    elif isinstance(cloud, clouds.Kubernetes):
        config = auth.setup_kubernetes_authentication(config)
    elif isinstance(cloud, clouds.IBM):
        config = auth.setup_ibm_authentication(config)
    else:
        assert isinstance(cloud, clouds.Local), cloud
        # Local cluster case, authentication is already filled by the user
        # in the local cluster config (in ~/.sky/local/...). There is no need
        # for Sky to generate authentication.
        pass
    common_utils.dump_yaml(cluster_config_file, config)


def get_run_timestamp() -> str:
    return 'sky-' + datetime.now().strftime('%Y-%m-%d-%H-%M-%S-%f')


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


def get_docker_user(ip: str, cluster_config_file: str) -> str:
    """Find docker container username."""
    ssh_credentials = ssh_credential_from_yaml(cluster_config_file)
    runner = command_runner.SSHCommandRunner(ip, port=22, **ssh_credentials)
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
    except exceptions.FetchIPError as e:
        logger.error(common_utils.format_exception(e))
        return False, None  # failed

    config = common_utils.read_yaml(cluster_config_file)

    docker_user = None
    if 'docker' in config:
        docker_user = get_docker_user(head_ip, cluster_config_file)

    if num_nodes <= 1:
        return True, docker_user

    ssh_credentials = ssh_credential_from_yaml(cluster_config_file, docker_user)
    last_nodes_so_far = 0
    start = time.time()
    runner = command_runner.SSHCommandRunner(head_ip,
                                             port=22,
                                             **ssh_credentials)
    with rich_utils.safe_status(
            '[bold cyan]Waiting for workers...') as worker_status:
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

            worker_status.update('[bold cyan]'
                                 f'{ready_workers} out of {num_nodes - 1} '
                                 'workers ready')

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


def ssh_credential_from_yaml(cluster_yaml: str,
                             docker_user: Optional[str] = None
                            ) -> Dict[str, Any]:
    """Returns ssh_user, ssh_private_key and ssh_control name."""
    config = common_utils.read_yaml(cluster_yaml)
    auth_section = config['auth']
    ssh_user = auth_section['ssh_user'].strip()
    ssh_private_key = auth_section.get('ssh_private_key')
    ssh_control_name = config.get('cluster_name', '__default__')
    ssh_proxy_command = auth_section.get('ssh_proxy_command')
    credentials = {
        'ssh_user': ssh_user,
        'ssh_private_key': ssh_private_key,
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


def parallel_data_transfer_to_nodes(
    runners: List[command_runner.SSHCommandRunner],
    source: Optional[str],
    target: str,
    cmd: Optional[str],
    run_rsync: bool,
    *,
    action_message: str,
    # Advanced options.
    log_path: str = os.devnull,
    stream_logs: bool = False,
):
    """Runs a command on all nodes and optionally runs rsync from src->dst.

    Args:
        runners: A list of SSHCommandRunner objects that represent multiple nodes.
        source: Optional[str]; Source for rsync on local node
        target: str; Destination on remote node for rsync
        cmd: str; Command to be executed on all nodes
        action_message: str; Message to be printed while the command runs
        log_path: str; Path to the log file
        stream_logs: bool; Whether to stream logs to stdout
    """
    fore = colorama.Fore
    style = colorama.Style

    origin_source = source

    def _sync_node(runner: 'command_runner.SSHCommandRunner') -> None:
        if cmd is not None:
            rc, stdout, stderr = runner.run(cmd,
                                            log_path=log_path,
                                            stream_logs=stream_logs,
                                            require_outputs=True)
            err_msg = ('Failed to run command before rsync '
                       f'{origin_source} -> {target}. '
                       'Ensure that the network is stable, then retry.')
            if log_path != os.devnull:
                err_msg += f' See logs in {log_path}'
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
    message = (f'{fore.CYAN}{action_message} (to {num_nodes} node{plural})'
               f': {style.BRIGHT}{origin_source}{style.RESET_ALL} -> '
               f'{style.BRIGHT}{target}{style.RESET_ALL}')
    logger.info(message)
    with rich_utils.safe_status(f'[bold cyan]{action_message}[/]'):
        subprocess_utils.run_in_parallel(_sync_node, runners)


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


def generate_cluster_name():
    # TODO: change this ID formatting to something more pleasant.
    # User name is helpful in non-isolated accounts, e.g., GCP, Azure.
    return f'sky-{uuid.uuid4().hex[:4]}-{get_cleaned_username()}'


def get_cleaned_username(username: str = '') -> str:
    """Cleans the username to be used as part of a cluster name.

    Clean up includes:
     1. Making all characters lowercase
     2. Removing any non-alphanumeric characters (excluding hyphens)
     3. Removing any numbers and/or hyphens at the start of the username.
     4. Removing any hyphens at the end of the username

    e.g. 1SkY-PiLot2- becomes sky-pilot2.

    Returns:
      A cleaned username that will pass the regex in
      check_cluster_name_is_valid().
    """
    username = username or getpass.getuser()
    username = username.lower()
    username = re.sub(r'[^a-z0-9-]', '', username)
    username = re.sub(r'^[0-9-]+', '', username)
    username = re.sub(r'-$', '', username)
    return username


def _query_head_ip_with_retries(cluster_yaml: str,
                                max_attempts: int = 1) -> str:
    """Returns the IP of the head node by querying the cloud.

    Raises:
      exceptions.FetchIPError: if we failed to get the head IP.
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
                raise exceptions.FetchIPError(
                    reason=exceptions.FetchIPError.Reason.HEAD) from e
            # Retry if the cluster is not up yet.
            logger.debug('Retrying to get head ip.')
            time.sleep(backoff.current_backoff())
    return head_ip


@timeline.event
def get_node_ips(cluster_yaml: str,
                 expected_num_nodes: int,
                 handle: Optional[
                     'cloud_vm_ray_backend.CloudVmRayResourceHandle'] = None,
                 head_ip_max_attempts: int = 1,
                 worker_ip_max_attempts: int = 1,
                 get_internal_ips: bool = False) -> List[str]:
    """Returns the IPs of all nodes in the cluster, with head node at front.

    Args:
        cluster_yaml: Path to the cluster yaml.
        expected_num_nodes: Expected number of nodes in the cluster.
        handle: Cloud VM Ray resource handle. It is only required for TPU VM or
            on-prem clusters.
        head_ip_max_attempts: Max attempts to get head ip.
        worker_ip_max_attempts: Max attempts to get worker ips.
        get_internal_ips: Whether to get internal IPs. When False, it is still
            possible to get internal IPs if the cluster does not have external
            IPs.

    Raises:
        exceptions.FetchIPError: if we failed to get the IPs. e.reason is
            HEAD or WORKER.
    """
    # When ray up launches TPU VM Pod, Pod workers (except for the head)
    # won't be connected to Ray cluster. Thus "ray get-worker-ips"
    # won't work and we need to query the node IPs with gcloud as
    # implmented in _get_tpu_vm_pod_ips.
    ray_config = common_utils.read_yaml(cluster_yaml)
    # Use the new provisioner for AWS.
    if '.aws' in ray_config['provider']['module']:
        metadata = provision_lib.get_cluster_info(
            'aws', ray_config['provider']['region'], ray_config['cluster_name'])
        if len(metadata.instances) < expected_num_nodes:
            # Simulate the exception when Ray head node is not up.
            raise exceptions.FetchIPError(exceptions.FetchIPError.Reason.HEAD)
        return metadata.get_feasible_ips(get_internal_ips)

    use_tpu_vm = ray_config['provider'].get('_has_tpus', False)
    if use_tpu_vm:
        assert expected_num_nodes == 1, (
            'TPU VM only supports single node for now.')
        assert handle is not None, 'handle is required for TPU VM.'
        try:
            ips = _get_tpu_vm_pod_ips(ray_config, get_internal_ips)
        except exceptions.CommandError as e:
            raise exceptions.FetchIPError(
                exceptions.FetchIPError.Reason.HEAD) from e
        if len(ips) != tpu_utils.get_num_tpu_devices(handle.launched_resources):
            raise exceptions.FetchIPError(exceptions.FetchIPError.Reason.HEAD)
        return ips

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
                    raise exceptions.FetchIPError(
                        exceptions.FetchIPError.Reason.WORKER) from e
                # Retry if the ssh is not ready for the workers yet.
                backoff_time = backoff.current_backoff()
                logger.debug('Retrying to get worker ip '
                             f'[{retry_cnt}/{worker_ip_max_attempts}] in '
                             f'{backoff_time} seconds.')
                time.sleep(backoff_time)
        worker_ips = re.findall(IP_ADDR_REGEX, out)
        # Ray Autoscaler On-prem Bug: ray-get-worker-ips outputs nothing!
        # Workaround: List of IPs are shown in Stderr
        cluster_name = os.path.basename(cluster_yaml).split('.')[0]
        if ((handle is not None and hasattr(handle, 'local_handle') and
             handle.local_handle is not None) or
                onprem_utils.check_if_local_cloud(cluster_name)):
            out = proc.stderr.decode()
            worker_ips = re.findall(IP_ADDR_REGEX, out)
            # Remove head ip from worker ip list.
            for i, ip in enumerate(worker_ips):
                if ip == head_ip_list[0]:
                    del worker_ips[i]
                    break
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
                raise exceptions.FetchIPError(
                    exceptions.FetchIPError.Reason.WORKER)
    else:
        worker_ips = []
    return head_ip_list + worker_ips


@timeline.event
def _get_tpu_vm_pod_ips(ray_config: Dict[str, Any],
                        get_internal_ips: bool = False) -> List[str]:
    """Returns the IPs of all TPU VM Pod workers using gcloud."""

    cluster_name = ray_config['cluster_name']
    zone = ray_config['provider']['availability_zone']
    query_cmd = (f'gcloud compute tpus tpu-vm list --filter='
                 f'"(labels.ray-cluster-name={cluster_name})" '
                 f'--zone={zone} --format="value(name)"')
    returncode, stdout, stderr = log_lib.run_with_log(query_cmd,
                                                      '/dev/null',
                                                      shell=True,
                                                      stream_logs=False,
                                                      require_outputs=True)
    subprocess_utils.handle_returncode(
        returncode,
        query_cmd,
        'Failed to run gcloud to get TPU VM IDs.',
        stderr=stdout + stderr)
    if len(stdout) == 0:
        logger.debug('No TPU VMs found with cluster name '
                     f'{cluster_name} in zone {zone}.')
    if len(stdout.splitlines()) > 1:
        # Rare case, this could mean resource leakage. Hint user.
        logger.warning('Found more than one TPU VM/Pod with the same cluster '
                       f'name {cluster_name} in zone {zone}.')

    all_ips = []
    for tpu_id in stdout.splitlines():
        tpuvm_cmd = (f'gcloud compute tpus tpu-vm describe {tpu_id}'
                     f' --zone {zone} --format=json')
        returncode, stdout, stderr = log_lib.run_with_log(tpuvm_cmd,
                                                          os.devnull,
                                                          shell=True,
                                                          stream_logs=False,
                                                          require_outputs=True)
        subprocess_utils.handle_returncode(
            returncode,
            tpuvm_cmd,
            'Failed to run gcloud tpu-vm describe.',
            stderr=stdout + stderr)

        tpuvm_json = json.loads(stdout)
        if tpuvm_json['state'] != 'READY':
            # May be a leaked preempted resource, or terminated by user in the
            # console, or still in the process of being created.
            ux_utils.console_newline()
            logger.debug(f'TPU VM {tpu_id} is in {tpuvm_json["state"]} '
                         'state. Skipping IP query... '
                         'Hint: make sure it is not leaked.')
            continue

        ips = []
        for endpoint in tpuvm_json['networkEndpoints']:
            # Note: if TPU VM is being preempted, its IP field may not exist.
            # We use get() to avoid KeyError.
            if get_internal_ips:
                ip = endpoint.get('ipAddress', None)
            else:
                ip = endpoint['accessConfig'].get('externalIp', None)
            if ip is not None:
                ips.append(ip)
        all_ips.extend(ips)

    return all_ips


def check_network_connection():
    # Tolerate 3 retries as it is observed that connections can fail.
    adapter = adapters.HTTPAdapter(max_retries=retry_lib.Retry(total=3))
    http = requests.Session()
    http.mount('https://', adapter)
    http.mount('http://', adapter)
    for i, ip in enumerate(_TEST_IP_LIST):
        try:
            http.head(ip, timeout=3)
            return
        except (requests.Timeout, requests.exceptions.ConnectionError) as e:
            if i == len(_TEST_IP_LIST) - 1:
                raise exceptions.NetworkError('Could not refresh the cluster. '
                                              'Network seems down.') from e


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
    record = global_user_state.get_cluster_from_name(cluster_name)
    if record is None:
        return
    handle = record['handle']
    if not isinstance(handle, backends.CloudVmRayResourceHandle):
        return

    cloud = handle.launched_resources.cloud
    current_user_identity = cloud.get_current_user_identity()
    owner_identity = record['owner']
    if current_user_identity is None:
        # Skip the check if the cloud does not support user identity.
        return
    # The user identity can be None, if the cluster is created by an older
    # version of SkyPilot. In that case, we set the user identity to the
    # current one.
    # NOTE: a user who upgrades SkyPilot and switches to a new cloud identity
    # immediately without `sky status --refresh` first, will cause a leakage
    # of the existing cluster. We deem this an acceptable tradeoff mainly
    # because multi-identity is not common (at least at the moment).
    if owner_identity is None:
        global_user_state.set_owner_identity_for_cluster(
            cluster_name, current_user_identity)
    else:
        assert isinstance(owner_identity, list)
        # It is OK if the owner identity is shorter, which will happen when
        # the cluster is launched before #1808. In that case, we only check
        # the same length (zip will stop at the shorter one).
        for i, (owner,
                current) in enumerate(zip(owner_identity,
                                          current_user_identity)):
            # Clean up the owner identiy for the backslash and newlines, caused
            # by the cloud CLI output, e.g. gcloud.
            owner = owner.replace('\n', '').replace('\\', '')
            if owner == current:
                if i != 0:
                    logger.warning(
                        f'The cluster was owned by {owner_identity}, but '
                        f'a new identity {current_user_identity} is activated. We still '
                        'allow the operation as the two identities are likely to have '
                        'the same access to the cluster. Please be aware that this can '
                        'cause unexpected cluster leakage if the two identities are not '
                        'actually equivalent (e.g., belong to the same person).'
                    )
                if i != 0 or len(owner_identity) != len(current_user_identity):
                    # We update the owner of a cluster, when:
                    # 1. The strictest identty (i.e. the first one) does not
                    # match, but the latter ones match.
                    # 2. The length of the two identities are different, which
                    # will only happen when the cluster is launched before #1808.
                    # Update the user identity to avoid showing the warning above
                    # again.
                    global_user_state.set_owner_identity_for_cluster(
                        cluster_name, current_user_identity)
                return  # The user identity matches.
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ClusterOwnerIdentityMismatchError(
                f'{cluster_name!r} ({cloud}) is owned by account '
                f'{owner_identity!r}, but the activated account '
                f'is {current_user_identity!r}.')


def tag_filter_for_cluster(cluster_name: str) -> Dict[str, str]:
    """Returns a tag filter for the cluster."""
    return {
        'ray-cluster-name': cluster_name,
    }


def _query_cluster_status_via_cloud_api(
    handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle'
) -> List[status_lib.ClusterStatus]:
    """Returns the status of the cluster.

    Raises:
        exceptions.ClusterStatusFetchingError: the cluster status cannot be
          fetched from the cloud provider.
    """
    cluster_name_on_cloud = handle.cluster_name_on_cloud
    cluster_name_in_hint = common_utils.cluster_name_in_hint(
        handle.cluster_name, cluster_name_on_cloud)
    # Use region and zone from the cluster config, instead of the
    # handle.launched_resources, because the latter may not be set
    # correctly yet.
    ray_config = common_utils.read_yaml(handle.cluster_yaml)
    provider_config = ray_config['provider']
    region = provider_config.get('region') or provider_config.get('location')
    zone = ray_config['provider'].get('availability_zone')
    kwargs = {}
    if isinstance(handle.launched_resources.cloud, clouds.GCP):
        kwargs['use_tpu_vm'] = ray_config['provider'].get('_has_tpus', False)

    # Query the cloud provider.
    # TODO(suquark): move implementations of more clouds here
    if isinstance(handle.launched_resources.cloud, clouds.AWS):
        cloud_name = repr(handle.launched_resources.cloud)
        try:
            node_status_dict = provision_lib.query_instances(
                cloud_name, cluster_name_on_cloud, provider_config)
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
        node_statuses = handle.launched_resources.cloud.query_status(
            cluster_name_on_cloud,
            tag_filter_for_cluster(cluster_name_on_cloud), region, zone,
            **kwargs)
    # GCP does not clean up preempted TPU VMs. We remove it ourselves.
    # TODO(wei-lin): handle multi-node cases.
    # TODO(zhwu): this should be moved into the GCP class, after we refactor
    # the cluster termination, as the preempted TPU VM should always be
    # removed.
    if kwargs.get('use_tpu_vm', False) and len(node_statuses) == 0:
        logger.debug(
            f'Terminating preempted TPU VM cluster {cluster_name_in_hint}')
        backend = backends.CloudVmRayBackend()
        # Do not use refresh cluster status during teardown, as that will
        # cause infinite recursion by calling cluster status refresh
        # again.

        # Post teardown cleanup be done later in this function, which will
        # remove the cluster entry from the status table & the ssh config file.
        backend.teardown_no_lock(handle,
                                 terminate=True,
                                 purge=False,
                                 post_teardown_cleanup=False,
                                 refresh_cluster_status=False)
    return node_statuses


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
        ValueError: If the source cluster does not exist.
        exceptions.NotSupportedError: If the source cluster is not valid or the
            task is not compatible to clone disk from the source cluster.
    """
    source_cluster_status, handle = refresh_cluster_status_handle(cluster_name)
    if source_cluster_status is None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                f'Cannot find cluster {cluster_name!r} to clone disk from.')

    if not isinstance(handle, backends.CloudVmRayResourceHandle):
        with ux_utils.print_exception_no_traceback():
            raise exceptions.NotSupportedError(
                f'Cannot clone disk from a non-cloud cluster {cluster_name!r}.')

    if source_cluster_status != status_lib.ClusterStatus.STOPPED:
        with ux_utils.print_exception_no_traceback():
            raise exceptions.NotSupportedError(
                f'Cannot clone disk from cluster {cluster_name!r} '
                f'({source_cluster_status!r}). Please stop the '
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
    original_cloud = handle.launched_resources.cloud
    original_cloud.check_features_are_supported(
        {clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER})

    assert original_cloud is not None, handle.launched_resources
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

        override_param = {}
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


def _update_cluster_status_no_lock(
        cluster_name: str) -> Optional[Dict[str, Any]]:
    """Updates the status of the cluster.

    Raises:
        exceptions.ClusterStatusFetchingError: the cluster status cannot be
          fetched from the cloud provider.
    """
    record = global_user_state.get_cluster_from_name(cluster_name)
    if record is None:
        return None
    handle = record['handle']
    if not isinstance(handle, backends.CloudVmRayResourceHandle):
        return record
    cluster_name = handle.cluster_name

    node_statuses = _query_cluster_status_via_cloud_api(handle)

    all_nodes_up = (all(
        status == status_lib.ClusterStatus.UP for status in node_statuses) and
                    len(node_statuses) == handle.launched_nodes)

    def run_ray_status_to_check_ray_cluster_healthy() -> bool:
        try:
            # TODO(zhwu): This function cannot distinguish transient network
            # error in ray's get IPs vs. ray runtime failing.

            # NOTE: fetching the IPs is very slow as it calls into
            # `ray get head-ip/worker-ips`. Using cached IPs is safe because
            # in the worst case we time out in the `ray status` SSH command
            # below.
            external_ips = handle.cached_external_ips
            # This happens to a stopped TPU VM as we use gcloud to query the IP.
            # Or user interrupt the `sky launch` process before the first time
            # resources handle is written back to local database.
            # This is helpful when user interrupt after the provision is done
            # and before the skylet is restarted. After #2304 is merged, this
            # helps keep the cluster status to INIT after `sky status -r`, so
            # user will be notified that any auto stop/down might not be
            # triggered.
            if external_ips is None or len(external_ips) == 0:
                logger.debug(f'Refreshing status ({cluster_name!r}): No cached '
                             f'IPs found. Handle: {handle}')
                raise exceptions.FetchIPError(
                    reason=exceptions.FetchIPError.Reason.HEAD)

            # Potentially refresh the external SSH ports, in case the existing
            # cluster before #2491 was launched without external SSH ports
            # cached.
            external_ssh_ports = handle.external_ssh_ports()
            head_ssh_port = external_ssh_ports[0]

            # Check if ray cluster status is healthy.
            ssh_credentials = ssh_credential_from_yaml(handle.cluster_yaml,
                                                       handle.docker_user)

            runner = command_runner.SSHCommandRunner(external_ips[0],
                                                     **ssh_credentials,
                                                     port=head_ssh_port)
            rc, output, stderr = runner.run(
                instance_setup.RAY_STATUS_WITH_SKY_RAY_PORT_COMMAND,
                stream_logs=False,
                require_outputs=True,
                separate_stderr=True)
            if rc:
                raise RuntimeError(
                    f'Refreshing status ({cluster_name!r}): Failed to check '
                    f'ray cluster\'s healthiness with '
                    f'{instance_setup.RAY_STATUS_WITH_SKY_RAY_PORT_COMMAND}.\n'
                    f'-- stdout --\n{output}\n-- stderr --\n{stderr}')

            ready_head, ready_workers = _count_healthy_nodes_from_ray(output)

            if ready_head + ready_workers == handle.launched_nodes:
                return True
            raise RuntimeError(
                f'Refreshing status ({cluster_name!r}): ray status not showing '
                f'all nodes ({ready_head + ready_workers}/'
                f'{handle.launched_nodes}); output: {output}; stderr: {stderr}')
        except exceptions.FetchIPError:
            logger.debug(
                f'Refreshing status ({cluster_name!r}) failed to get IPs.')
        except RuntimeError as e:
            logger.debug(str(e))
        except Exception as e:  # pylint: disable=broad-except
            # This can be raised by `external_ssh_ports()`, due to the
            # underlying call to kubernetes API.
            logger.debug(
                f'Refreshing status ({cluster_name!r}) failed: '
                f'{common_utils.format_exception(e, use_bracket=True)}')
        return False

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
    if all_nodes_up and run_ray_status_to_check_ray_cluster_healthy():
        # NOTE: all_nodes_up calculation is fast due to calling cloud CLI;
        # run_ray_status_to_check_all_nodes_up() is slow due to calling `ray get
        # head-ip/worker-ips`.
        record['status'] = status_lib.ClusterStatus.UP
        global_user_state.add_or_update_cluster(cluster_name,
                                                handle,
                                                requested_resources=None,
                                                ready=True,
                                                is_launch=False)
        return record

    # All cases below are transitioning the cluster to non-UP states.

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

    # If the node_statuses is empty, all the nodes are terminated. We can
    # safely set the cluster status to TERMINATED. This handles the edge case
    # where the cluster is terminated by the user manually through the UI.
    to_terminate = not node_statuses

    # A cluster is considered "abnormal", if not all nodes are TERMINATED or
    # not all nodes are STOPPED. We check that with the following logic:
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
    #
    # An abnormal cluster will transition to INIT and have any autostop setting
    # reset (unless it's autostopping/autodowning).
    is_abnormal = ((0 < len(node_statuses) < handle.launched_nodes) or any(
        status != status_lib.ClusterStatus.STOPPED for status in node_statuses))
    if is_abnormal:
        logger.debug('The cluster is abnormal. Setting to INIT status. '
                     f'node_statuses: {node_statuses}')
        backend = get_backend_from_handle(handle)
        if isinstance(backend,
                      backends.CloudVmRayBackend) and record['autostop'] >= 0:
            if not backend.is_definitely_autostopping(handle,
                                                      stream_logs=False):
                # Reset the autostopping as the cluster is abnormal, and may
                # not correctly autostop. Resetting the autostop will let
                # the user know that the autostop may not happen to avoid
                # leakages from the assumption that the cluster will autostop.
                success = True
                try:
                    backend.set_autostop(handle, -1, stream_logs=False)
                except (Exception, SystemExit) as e:  # pylint: disable=broad-except
                    success = False
                    logger.debug(f'Failed to reset autostop. Due to '
                                 f'{common_utils.format_exception(e)}')
                global_user_state.set_cluster_autostop_value(
                    handle.cluster_name, -1, to_down=False)

                # Friendly hint.
                autostop = record['autostop']
                maybe_down_str = ' --down' if record['to_down'] else ''
                noun = 'autodown' if record['to_down'] else 'autostop'
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
            else:
                ux_utils.console_newline()
                operation_str = 'autodowning' if record[
                    'to_down'] else 'autostopping'
                logger.info(
                    f'Cluster {cluster_name!r} is {operation_str}. Setting to '
                    'INIT status; try refresh again in a while.')

        # If the user starts part of a STOPPED cluster, we still need a status
        # to represent the abnormal status. For spot cluster, it can also
        # represent that the cluster is partially preempted.
        # TODO(zhwu): the definition of INIT should be audited/changed.
        # Adding a new status UNHEALTHY for abnormal status can be a choice.
        global_user_state.add_or_update_cluster(cluster_name,
                                                handle,
                                                requested_resources=None,
                                                ready=False,
                                                is_launch=False)
        return global_user_state.get_cluster_from_name(cluster_name)
    # Now is_abnormal is False: either node_statuses is empty or all nodes are
    # STOPPED.
    backend = backends.CloudVmRayBackend()
    backend.post_teardown_cleanup(handle, terminate=to_terminate, purge=False)
    return global_user_state.get_cluster_from_name(cluster_name)


def _update_cluster_status(
        cluster_name: str,
        acquire_per_cluster_status_lock: bool) -> Optional[Dict[str, Any]]:
    """Update the cluster status.

    The cluster status is updated by checking ray cluster and real status from
    cloud.

    The function will update the cached cluster status in the global state. For
    the design of the cluster status and transition, please refer to the
    sky/design_docs/cluster_status.md

    Args:
        cluster_name: The name of the cluster.
        acquire_per_cluster_status_lock: Whether to acquire the per-cluster lock
            before updating the status.
        need_owner_identity_check: Whether to check the owner identity before
            updating

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
    if not acquire_per_cluster_status_lock:
        return _update_cluster_status_no_lock(cluster_name)

    try:
        # TODO(mraheja): remove pylint disabling when filelock
        # version updated
        # pylint: disable=abstract-class-instantiated
        with filelock.FileLock(CLUSTER_STATUS_LOCK_PATH.format(cluster_name),
                               CLUSTER_STATUS_LOCK_TIMEOUT_SECONDS):
            return _update_cluster_status_no_lock(cluster_name)
    except filelock.Timeout:
        logger.debug('Refreshing status: Failed get the lock for cluster '
                     f'{cluster_name!r}. Using the cached status.')
        return global_user_state.get_cluster_from_name(cluster_name)


def _refresh_cluster_record(
        cluster_name: str,
        *,
        force_refresh_statuses: Optional[Set[status_lib.ClusterStatus]] = None,
        acquire_per_cluster_status_lock: bool = True
) -> Optional[Dict[str, Any]]:
    """Refresh the cluster, and return the possibly updated record.

    This function will also check the owner identity of the cluster, and raise
    exceptions if the current user is not the same as the user who created the
    cluster.

    Args:
        cluster_name: The name of the cluster.
        force_refresh_statuses: if specified, refresh the cluster if it has one of
            the specified statuses. Additionally, clusters satisfying the
            following conditions will always be refreshed no matter the
            argument is specified or not:
                1. is a spot cluster, or
                2. is a non-spot cluster, is not STOPPED, and autostop is set.
        acquire_per_cluster_status_lock: Whether to acquire the per-cluster lock
            before updating the status.

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

    record = global_user_state.get_cluster_from_name(cluster_name)
    if record is None:
        return None
    check_owner_identity(cluster_name)

    handle = record['handle']
    if isinstance(handle, backends.CloudVmRayResourceHandle):
        use_spot = handle.launched_resources.use_spot
        has_autostop = (record['status'] != status_lib.ClusterStatus.STOPPED and
                        record['autostop'] >= 0)
        force_refresh_for_cluster = (force_refresh_statuses is not None and
                                     record['status'] in force_refresh_statuses)
        if force_refresh_for_cluster or has_autostop or use_spot:
            record = _update_cluster_status(
                cluster_name,
                acquire_per_cluster_status_lock=acquire_per_cluster_status_lock)
    return record


@timeline.event
def refresh_cluster_status_handle(
    cluster_name: str,
    *,
    force_refresh_statuses: Optional[Set[status_lib.ClusterStatus]] = None,
    acquire_per_cluster_status_lock: bool = True,
) -> Tuple[Optional[status_lib.ClusterStatus],
           Optional[backends.ResourceHandle]]:
    """Refresh the cluster, and return the possibly updated status and handle.

    This is a wrapper of refresh_cluster_record, which returns the status and
    handle of the cluster.
    Please refer to the docstring of refresh_cluster_record for the details.
    """
    record = _refresh_cluster_record(
        cluster_name,
        force_refresh_statuses=force_refresh_statuses,
        acquire_per_cluster_status_lock=acquire_per_cluster_status_lock)
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


def check_cluster_available(
    cluster_name: str,
    *,
    operation: str,
    check_cloud_vm_ray_backend: bool = True,
    dryrun: bool = False,
) -> backends.ResourceHandle:
    """Check if the cluster is available.

    Raises:
        ValueError: if the cluster does not exist.
        exceptions.ClusterNotUpError: if the cluster is not UP.
        exceptions.NotSupportedError: if the cluster is not based on
          CloudVmRayBackend.
        exceptions.ClusterOwnerIdentityMismatchError: if the current user is
          not the same as the user who created the cluster.
        exceptions.CloudUserIdentityError: if we fail to get the current user
          identity.
    """
    record = global_user_state.get_cluster_from_name(cluster_name)
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
            raise ValueError(f'{colorama.Fore.YELLOW}{error_msg}{reset}')
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
    if cluster_status != status_lib.ClusterStatus.UP:
        if onprem_utils.check_if_local_cloud(cluster_name):
            raise exceptions.ClusterNotUpError(
                constants.UNINITIALIZED_ONPREM_CLUSTER_MESSAGE.format(
                    cluster_name),
                cluster_status=cluster_status,
                handle=handle)
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
                f'{status_lib.ClusterStatus.UP.value} clusters.'
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
def is_controller_up(
    controller_type: controller_utils.Controllers,
    stopped_message: str,
    non_existent_message: Optional[str] = None,
) -> Tuple[Optional[status_lib.ClusterStatus],
           Optional['backends.CloudVmRayResourceHandle']]:
    """Check if the spot/serve controller is up.

    It can be used to check the actual controller status (since the autostop is
    set for the controller) before the spot/serve commands interact with the
    controller.

    Args:
        type: Type of the controller.
        stopped_message: Message to print if the controller is STOPPED.
        non_existent_message: Message to show if the controller does not exist.

    Returns:
        controller_status: The status of the controller. If it fails during
          refreshing the status, it will be the cached status. None if the
          controller does not exist.
        handle: The ResourceHandle of the controller. None if the
          controller is not UP or does not exist.

    Raises:
        exceptions.ClusterOwnerIdentityMismatchError: if the current user is not
          the same as the user who created the cluster.
        exceptions.CloudUserIdentityError: if we fail to get the current user
          identity.
    """
    if non_existent_message is None:
        non_existent_message = (
            controller_type.value.default_hint_if_non_existent)
    cluster_name = controller_type.value.cluster_name
    controller_name = controller_type.value.name.replace(' controller', '')
    try:
        # Set force_refresh_statuses=None to make sure the refresh only happens
        # when the controller is INIT/UP (triggered in these statuses as the
        # autostop is always set for the controller). This optimization avoids
        # unnecessary costly refresh when the controller is already stopped.
        # This optimization is based on the assumption that the user will not
        # start the controller manually from the cloud console.
        controller_status, handle = refresh_cluster_status_handle(
            cluster_name, force_refresh_statuses=None)
    except exceptions.ClusterStatusFetchingError as e:
        # We do not catch the exceptions related to the cluster owner identity
        # mismatch, please refer to the comment in
        # `backend_utils.check_cluster_available`.
        logger.warning(
            'Failed to get the status of the controller. It is not '
            f'fatal, but {controller_name} commands/calls may hang or return '
            'stale information, when the controller is not up.\n'
            f'  Details: {common_utils.format_exception(e, use_bracket=True)}')
        record = global_user_state.get_cluster_from_name(cluster_name)
        controller_status, handle = None, None
        if record is not None:
            controller_status, handle = record['status'], record['handle']

    if controller_status is None:
        sky_logging.print(non_existent_message)
    elif controller_status != status_lib.ClusterStatus.UP:
        msg = (f'{controller_name.capitalize()} controller {cluster_name} '
               f'is {controller_status.value}.')
        if controller_status == status_lib.ClusterStatus.STOPPED:
            msg += f'\n{stopped_message}'
        if controller_status == status_lib.ClusterStatus.INIT:
            msg += '\nPlease wait for the controller to be ready.'
        sky_logging.print(msg)
        handle = None
    return controller_status, handle


class CloudFilter(enum.Enum):
    # Filter for all types of clouds.
    ALL = 'all'
    # Filter for Sky's main clouds (aws, gcp, azure, docker).
    CLOUDS_AND_DOCKER = 'clouds-and-docker'
    # Filter for only local clouds.
    LOCAL = 'local'


def get_clusters(
    include_controller: bool,
    refresh: bool,
    cloud_filter: CloudFilter = CloudFilter.CLOUDS_AND_DOCKER,
    cluster_names: Optional[Union[str, List[str]]] = None,
) -> List[Dict[str, Any]]:
    """Returns a list of cached or optionally refreshed cluster records.

    Combs through the database (in ~/.sky/state.db) to get a list of records
    corresponding to launched clusters (filtered by `cluster_names` if it is
    specified). The refresh flag can be used to force a refresh of the status
    of the clusters.

    Args:
        include_controller: Whether to include controllers, e.g. spot controller
            or sky serve controller.
        refresh: Whether to refresh the status of the clusters. (Refreshing will
            set the status to STOPPED if the cluster cannot be pinged.)
        cloud_filter: Sets which clouds to filer through from the global user
            state. Supports three values, 'all' for all clouds, 'public' for
            public clouds only, and 'local' for only local clouds.
        cluster_names: If provided, only return records for the given cluster
            names.

    Returns:
        A list of cluster records. If the cluster does not exist or has been
        terminated, the record will be omitted from the returned list.
    """
    records = global_user_state.get_clusters()

    if not include_controller:
        records = [
            record for record in records
            if controller_utils.Controllers.from_name(record['name']) is None
        ]

    yellow = colorama.Fore.YELLOW
    bright = colorama.Style.BRIGHT
    reset = colorama.Style.RESET_ALL

    if cluster_names is not None:
        if isinstance(cluster_names, str):
            cluster_names = [cluster_names]
        new_records = []
        not_exist_cluster_names = []
        for cluster_name in cluster_names:
            for record in records:
                if record['name'] == cluster_name:
                    new_records.append(record)
                    break
            else:
                not_exist_cluster_names.append(cluster_name)
        if not_exist_cluster_names:
            clusters_str = ', '.join(not_exist_cluster_names)
            logger.info(f'Cluster(s) not found: {bright}{clusters_str}{reset}.')
        records = new_records

    def _is_local_cluster(record):
        handle = record['handle']
        if isinstance(handle, backends.LocalDockerResourceHandle):
            return False
        cluster_resources = handle.launched_resources
        return isinstance(cluster_resources.cloud, clouds.Local)

    if cloud_filter == CloudFilter.LOCAL:
        records = [record for record in records if _is_local_cluster(record)]
    elif cloud_filter == CloudFilter.CLOUDS_AND_DOCKER:
        records = [
            record for record in records if not _is_local_cluster(record)
        ]
    elif cloud_filter not in CloudFilter:
        raise ValueError(f'{cloud_filter} is not part of CloudFilter.')

    if not refresh:
        return records

    plural = 's' if len(records) > 1 else ''
    progress = rich_progress.Progress(transient=True,
                                      redirect_stdout=False,
                                      redirect_stderr=False)
    task = progress.add_task(
        f'[bold cyan]Refreshing status for {len(records)} cluster{plural}[/]',
        total=len(records))

    def _refresh_cluster(cluster_name):
        try:
            record = _refresh_cluster_record(
                cluster_name,
                force_refresh_statuses=set(status_lib.ClusterStatus),
                acquire_per_cluster_status_lock=True)
        except (exceptions.ClusterStatusFetchingError,
                exceptions.CloudUserIdentityError,
                exceptions.ClusterOwnerIdentityMismatchError) as e:
            # Do not fail the entire refresh process. The caller will
            # handle the 'UNKNOWN' status, and collect the errors into
            # a table.
            record = {'status': 'UNKNOWN', 'error': e}
        progress.update(task, advance=1)
        return record

    cluster_names = [record['name'] for record in records]
    with progress:
        updated_records = subprocess_utils.run_in_parallel(
            _refresh_cluster, cluster_names)

    # Show information for removed clusters.
    kept_records = []
    autodown_clusters, remaining_clusters, failed_clusters = [], [], []
    for i, record in enumerate(records):
        if updated_records[i] is None:
            if record['to_down']:
                autodown_clusters.append(cluster_names[i])
            else:
                remaining_clusters.append(cluster_names[i])
        elif updated_records[i]['status'] == 'UNKNOWN':
            failed_clusters.append(
                (cluster_names[i], updated_records[i]['error']))
            # Keep the original record if the status is unknown,
            # so that the user can still see the cluster.
            kept_records.append(record)
        else:
            kept_records.append(updated_records[i])

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
    # For sky serve controller task, we set the CPU resource to a smaller
    # value to support a larger number of services.
    resources_dict = {
        'CPU': (serve_lib.SERVICES_TASK_CPU_DEMAND
                if task.service_name is not None else DEFAULT_TASK_CPU_DEMAND)
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


def get_task_resources_str(task: 'task_lib.Task') -> str:
    """Returns the resources string of the task.

    The resources string is only used as a display purpose, so we only show
    the accelerator demands (if any). Otherwise, the CPU demand is shown.
    """
    task_cpu_demand = (serve_lib.SERVICES_TASK_CPU_DEMAND if task.service_name
                       is not None else DEFAULT_TASK_CPU_DEMAND)
    if task.best_resources is not None:
        accelerator_dict = task.best_resources.accelerators
        if accelerator_dict is None:
            resources_str = f'CPU:{task_cpu_demand}'
        else:
            resources_str = ', '.join(
                f'{k}:{v}' for k, v in accelerator_dict.items())
    elif len(task.resources) == 1:
        resources_dict = list(task.resources)[0].accelerators
        if resources_dict is None:
            resources_str = f'CPU:{task_cpu_demand}'
        else:
            resources_str = ', '.join(
                f'{k}:{v}' for k, v in resources_dict.items())
    else:
        resource_accelerators = []
        for resource in task.resources:
            if resource.accelerators is None:
                continue
            for k, v in resource.accelerators.items():
                resource_accelerators.append(f'{k}:{v}')

        if resource_accelerators:
            resources_str = ', '.join(set(resource_accelerators))
        else:
            resources_str = f'CPU:{task_cpu_demand}'
    resources_str = f'{task.num_nodes}x [{resources_str}]'
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


def check_public_cloud_enabled():
    """Checks if any of the public clouds is enabled.

    Exceptions:
        exceptions.NoCloudAccessError: if no public cloud is enabled.
    """

    def _no_public_cloud():
        enabled_clouds = global_user_state.get_enabled_clouds()
        return (len(enabled_clouds) == 0 or
                (len(enabled_clouds) == 1 and
                 isinstance(enabled_clouds[0], clouds.Local)))

    if not _no_public_cloud():
        return

    sky_check.check(quiet=True)
    if _no_public_cloud():
        with ux_utils.print_exception_no_traceback():
            raise exceptions.NoCloudAccessError(
                'Cloud access is not set up. Run: '
                f'{colorama.Style.BRIGHT}sky check{colorama.Style.RESET_ALL}')


def run_command_and_handle_ssh_failure(runner: command_runner.SSHCommandRunner,
                                       command: str,
                                       failure_message: str) -> str:
    """Runs command remotely and returns output with proper error handling."""
    rc, stdout, stderr = runner.run(command,
                                    require_outputs=True,
                                    stream_logs=False)
    if rc == 255:
        # SSH failed
        raise RuntimeError(
            f'SSH with user {runner.ssh_user} and key {runner.ssh_private_key} '
            f'to {runner.ip} failed. This is most likely due to incorrect '
            'credentials or incorrect permissions for the key file. Check '
            'your credentials and try again.')
    subprocess_utils.handle_returncode(rc,
                                       command,
                                       failure_message,
                                       stderr=stderr)
    return stdout


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
    pattern = re.compile(r'AttributeError: module \'sky\.(.*)\' has no '
                         r'attribute \'(.*)\'')
    if returncode != 0:
        attribute_error = re.findall(pattern, stderr)
        if attribute_error:
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError(
                    f'{colorama.Fore.RED}SkyPilot runtime needs to be updated '
                    'on the remote cluster. To update, run (existing jobs are '
                    f'not interrupted): {colorama.Style.BRIGHT}sky start -f -y '
                    f'{cluster_name}{colorama.Style.RESET_ALL}'
                    f'\n--- Details ---\n{stderr.strip()}\n')
