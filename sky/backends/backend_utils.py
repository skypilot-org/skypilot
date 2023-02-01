"""Util constants/functions for the backends."""
import copy
from datetime import datetime
import difflib
import enum
import getpass
import json
import os
import pathlib
import re
import subprocess
import tempfile
import textwrap
import threading
import time
import typing
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union
import uuid

import colorama
import filelock
import jinja2
import jsonschema
from packaging import version
import requests
from requests import adapters
from requests.packages.urllib3.util import retry as retry_lib
from ray.autoscaler._private import commands as ray_commands
from ray.autoscaler._private import util as ray_autoscaler_private_util
import rich.console as rich_console
import rich.progress as rich_progress
import yaml

import sky
from sky import authentication as auth
from sky import backends
from sky import check as sky_check
from sky import clouds
from sky import exceptions
from sky import global_user_state
from sky import skypilot_config
from sky import sky_logging
from sky import spot as spot_lib
from sky.backends import onprem_utils
from sky.skylet import constants
from sky.skylet import log_lib
from sky.skylet.providers.lambda_cloud import lambda_utils
from sky.utils import common_utils
from sky.utils import command_runner
from sky.utils import env_options
from sky.utils import subprocess_utils
from sky.utils import timeline
from sky.utils import tpu_utils
from sky.utils import ux_utils
from sky.utils import validator
from sky.usage import usage_lib

if typing.TYPE_CHECKING:
    from sky import resources
    from sky import task as task_lib

logger = sky_logging.init_logger(__name__)
console = rich_console.Console()

# NOTE: keep in sync with the cluster template 'file_mounts'.
SKY_REMOTE_APP_DIR = '~/.sky/sky_app'
SKY_RAY_YAML_REMOTE_PATH = '~/.sky/sky_ray.yml'
IP_ADDR_REGEX = r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}'
SKY_REMOTE_PATH = '~/.sky/wheels'
SKY_USER_FILE_PATH = '~/.sky/generated'

BOLD = '\033[1m'
RESET_BOLD = '\033[0m'

# Do not use /tmp because it gets cleared on VM restart.
_SKY_REMOTE_FILE_MOUNTS_DIR = '~/.sky/file_mounts/'

_LAUNCHED_HEAD_PATTERN = re.compile(r'(\d+) ray[._]head[._]default')
_LAUNCHED_LOCAL_WORKER_PATTERN = re.compile(r'(\d+) node_')
_LAUNCHED_WORKER_PATTERN = re.compile(r'(\d+) ray[._]worker[._]default')
# Intentionally not using prefix 'rf' for the string format because yapf have a
# bug with python=3.6.
# 10.133.0.5: ray.worker.default,
_LAUNCHING_IP_PATTERN = re.compile(
    r'({}): ray[._]worker[._]default'.format(IP_ADDR_REGEX))
WAIT_HEAD_NODE_IP_MAX_ATTEMPTS = 3

# We use fixed IP address to avoid DNS lookup blocking the check, for machine
# with no internet connection.
# Refer to: https://stackoverflow.com/questions/3764291/how-can-i-see-if-theres-an-available-and-active-network-connection-in-python # pylint: disable=line-too-long
_TEST_IP = 'https://8.8.8.8'

# Allow each CPU thread take 2 tasks.
# Note: This value cannot be too small, otherwise OOM issue may occur.
DEFAULT_TASK_CPU_DEMAND = 0.5

# Mapping from reserved cluster names to the corresponding group name (logging
# purpose).
# NOTE: each group can only have one reserved cluster name for now.
SKY_RESERVED_CLUSTER_NAMES = {
    spot_lib.SPOT_CONTROLLER_NAME: 'Managed spot controller'
}

# Filelocks for the cluster status change.
CLUSTER_STATUS_LOCK_PATH = os.path.expanduser('~/.sky/.{}.lock')
CLUSTER_STATUS_LOCK_TIMEOUT_SECONDS = 20

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
    'cluster_name', 'provider', 'auth', 'node_config'
}


def is_ip(s: str) -> bool:
    """Returns whether this string matches IP_ADDR_REGEX."""
    return len(re.findall(IP_ADDR_REGEX, s)) == 1


def _get_yaml_path_from_cluster_name(cluster_name: str,
                                     prefix: str = SKY_USER_FILE_PATH) -> str:
    output_path = pathlib.Path(
        prefix).expanduser().resolve() / f'{cluster_name}.yml'
    os.makedirs(output_path.parents[0], exist_ok=True)
    return str(output_path)


def fill_template(template_name: str,
                  variables: Dict,
                  output_path: Optional[str] = None,
                  output_prefix: str = SKY_USER_FILE_PATH) -> str:
    """Create a file from a Jinja template and return the filename."""
    assert template_name.endswith('.j2'), template_name
    template_path = os.path.join(sky.__root_dir__, 'templates', template_name)
    if not os.path.exists(template_path):
        raise FileNotFoundError(f'Template "{template_name}" does not exist.')
    with open(template_path) as fin:
        template = fin.read()
    if output_path is None:
        assert ('cluster_name' in variables), ('cluster_name is required.')
        cluster_name = variables.get('cluster_name')
        output_path = _get_yaml_path_from_cluster_name(cluster_name,
                                                       output_prefix)
    output_path = os.path.abspath(output_path)

    # Write out yaml config.
    template = jinja2.Template(template)
    content = template.render(**variables)
    with open(output_path, 'w') as fout:
        fout.write(content)
    return output_path


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
    #  - cp locally them into a directory
    #  - upload that directory as a file mount (1 connection)
    #  - use a remote command to move all runtime files to their right places.

    # Local tmp dir holding runtime files.
    local_runtime_files_dir = tempfile.mkdtemp()
    new_file_mounts = {_REMOTE_RUNTIME_FILES_DIR: local_runtime_files_dir}

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
        src_basename = os.path.basename(src)
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

    # (For local) Move all runtime files, including the just-written yaml, to
    # local_runtime_files_dir/.
    all_local_sources = ''
    for local_src in file_mounts.values():
        full_local_src = str(pathlib.Path(local_src).expanduser())
        # Add quotes for paths containing spaces.
        all_local_sources += f'{full_local_src!r} '
    # Takes 10-20 ms on laptop incl. 3 clouds' credentials.
    subprocess.run(f'cp -r {all_local_sources} {local_runtime_files_dir}/',
                   shell=True,
                   check=True)

    common_utils.dump_yaml(yaml_path, yaml_config)


def path_size_megabytes(path: str) -> int:
    """Returns the size of 'path' (directory or file) in megabytes."""
    resolved_path = pathlib.Path(path).expanduser().resolve()
    git_exclude_filter = ''
    if (resolved_path / command_runner.GIT_EXCLUDE).exists():
        # Ensure file exists; otherwise, rsync will error out.
        git_exclude_filter = command_runner.RSYNC_EXCLUDE_OPTION.format(
            str(resolved_path / command_runner.GIT_EXCLUDE))
    rsync_output = str(
        subprocess.check_output(
            f'rsync {command_runner.RSYNC_DISPLAY_OPTION} '
            f'{command_runner.RSYNC_FILTER_OPTION} '
            f'{git_exclude_filter} --dry-run {path!r}',
            shell=True).splitlines()[-1])
    total_bytes = rsync_output.split(' ')[3].replace(',', '')
    return int(total_bytes) // 10**6


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
                              proxy_command: Optional[str]):
        if proxy_command is not None:
            proxy = f'ProxyCommand {proxy_command}'
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
              Port 22
              {proxy}
            """.rstrip())
        return codegen

    @classmethod
    @timeline.FileLockEvent(ssh_conf_lock_path)
    def add_cluster(
        cls,
        cluster_name: str,
        ips: List[str],
        auth_config: Dict[str, str],
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
        """
        username = auth_config['ssh_user']
        key_path = os.path.expanduser(auth_config['ssh_private_key'])
        host_name = cluster_name
        sky_autogen_comment = ('# Added by sky (use `sky stop/down '
                               f'{cluster_name}` to remove)')
        overwrite = False
        overwrite_begin_idx = None
        ip = ips[0]

        config_path = os.path.expanduser(cls.ssh_conf_path)
        if os.path.exists(config_path):
            with open(config_path) as f:
                config = f.readlines()

            # If an existing config with `cluster_name` exists, raise a warning.
            for i, line in enumerate(config):
                if line.strip() == f'Host {cluster_name}':
                    prev_line = config[i - 1] if i - 1 > 0 else ''
                    if prev_line.strip().startswith(sky_autogen_comment):
                        overwrite = True
                        overwrite_begin_idx = i - 1
                    else:
                        logger.warning(f'{cls.ssh_conf_path} contains '
                                       f'host named {cluster_name}.')
                        host_name = ip
                        logger.warning(f'Using {ip} to identify host instead.')

                if line.strip() == f'Host {ip}':
                    prev_line = config[i - 1] if i - 1 > 0 else ''
                    if prev_line.strip().startswith(sky_autogen_comment):
                        overwrite = True
                        overwrite_begin_idx = i - 1
        else:
            config = ['\n']
            with open(config_path, 'w') as f:
                f.writelines(config)
            os.chmod(config_path, 0o644)

        proxy_command = auth_config.get('ssh_proxy_command', None)
        codegen = cls._get_generated_config(sky_autogen_comment, host_name, ip,
                                            username, key_path, proxy_command)

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
                                                  auth_config)

    @classmethod
    def _add_multinode_config(
        cls,
        cluster_name: str,
        external_worker_ips: List[str],
        auth_config: Dict[str, str],
    ):
        username = auth_config['ssh_user']
        key_path = os.path.expanduser(auth_config['ssh_private_key'])
        host_name = cluster_name
        sky_autogen_comment = ('# Added by sky (use `sky stop/down '
                               f'{cluster_name}` to remove)')

        # Ensure stableness of the aliases worker-<i> by sorting based on
        # public IPs.
        external_worker_ips = list(sorted(external_worker_ips))

        overwrites = [False] * len(external_worker_ips)
        overwrite_begin_idxs = [None] * len(external_worker_ips)
        codegens = [None] * len(external_worker_ips)
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
                codegens[idx] = cls._get_generated_config(
                    sky_autogen_comment, host_name, external_worker_ips[idx],
                    username, key_path, proxy_command)

        # All workers go to SKY_USER_FILE_PATH/ssh/{cluster_name}
        for i, line in enumerate(extra_config):
            if line.strip() in host_lines:
                idx = host_lines.index(line.strip())
                prev_line = extra_config[i - 1] if i > 0 else ''
                if prev_line.strip().startswith(sky_autogen_comment):
                    host_name = worker_names[idx]
                    overwrites[idx] = True
                    overwrite_begin_idxs[idx] = i - 1
                codegens[idx] = cls._get_generated_config(
                    sky_autogen_comment, host_name, external_worker_ips[idx],
                    username, key_path, proxy_command)

        # This checks if all codegens have been created.
        for idx, ip in enumerate(external_worker_ips):
            if not codegens[idx]:
                codegens[idx] = cls._get_generated_config(
                    sky_autogen_comment, worker_names[idx], ip, username,
                    key_path, proxy_command)

        for idx in range(len(external_worker_ips)):
            # Add (or overwrite) the new config.
            overwrite = overwrites[idx]
            overwrite_begin_idx = overwrite_begin_idxs[idx]
            codegen = codegens[idx]
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
    ):
        """Remove authentication information for cluster from local SSH config.

        If no existing host matching the provided specification is found, then
        nothing is removed.

        Args:
            ip: Head node's IP address.
            auth_config: read_yaml(handle.cluster_yaml)['auth']
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
            if (line.strip() == f'HostName {ip}' and
                    next_line.strip() == f'User {username}'):
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


def _replace_yaml_dicts(new_yaml: str, old_yaml: str,
                        key_names: Set[str]) -> str:
    """Replaces 'new' with 'old' for all keys in key_names.

    The replacement will be applied recursively and only for the blocks
    with the key in key_names, and have the same ancestors in both 'new'
    and 'old' YAML tree.
    """

    def _restore_block(new_block: Dict[str, Any], old_block: Dict[str, Any]):
        for key, value in new_block.items():
            if key in key_names:
                if key in old_block:
                    new_block[key] = old_block[key]
                else:
                    del new_block[key]
            elif isinstance(value, dict):
                if key in old_block:
                    _restore_block(value, old_block[key])

    new_config = yaml.safe_load(new_yaml)
    old_config = yaml.safe_load(old_yaml)
    _restore_block(new_config, old_config)
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
        auth_config: Optional[Dict[str, str]] = None,
        dryrun: bool = False,
        keep_launch_fields_in_existing_config: bool = True) -> Dict[str, str]:
    """Fills in cluster configuration templates and writes them out.

    Returns: {provisioner: path to yaml, the provisioning spec}.
      'provisioner' can be
        - 'ray'
        - 'tpu-create-script' (if TPU is requested)
        - 'tpu-delete-script' (if TPU is requested)
    Raises:
        ResourceUnavailableError: if the region/zones requested does not appear
            in the catalog, or an ssh_proxy_command is specified but not for the given region.
    """
    # task.best_resources may not be equal to to_provision if the user
    # is running a job with less resources than the cluster has.
    cloud = to_provision.cloud
    # This can raise a ResourceUnavailableError, when the region/zones requested
    # does not appear in the catalog. It can be triggered when the user changed
    # the catalog file, while there is a cluster in the removed region/zone.
    # TODO(zhwu): We should change the exception type to a more specific one,
    # as the ResourceUnavailableError is overly used. Also, it would be better
    # to move the check out of this function, i.e. the caller should be
    # responsible for the validation.
    resources_vars = cloud.make_deploy_resources_variables(
        to_provision, region, zones)
    config_dict = {}

    azure_subscription_id = None
    if isinstance(cloud, clouds.Azure):
        azure_subscription_id = cloud.get_project_id(dryrun=dryrun)

    gcp_project_id = None
    if isinstance(cloud, clouds.GCP):
        gcp_project_id = cloud.get_project_id(dryrun=dryrun)

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
    elif isinstance(ssh_proxy_command_config, dict):
        ssh_proxy_command = ssh_proxy_command_config.get(region_name, None)
        if ssh_proxy_command is None:
            # Skip this region. The upper layer will handle the failover to
            # other regions.
            raise exceptions.ResourcesUnavailableError(
                f'No ssh_proxy_command provided for region {region_name}. Skipped.'
            )
        elif not isinstance(ssh_proxy_command, str):
            raise ValueError(
                'Invalid ssh_proxy_command config (expected a str): '
                f'{ssh_proxy_command_config!r}')
    else:
        raise ValueError(
            'Invalid ssh_proxy_command config (expected a str or a dict with '
            f'region names as keys): {ssh_proxy_command_config!r}')
    logger.debug(f'Using ssh_proxy_command: {ssh_proxy_command!r}')

    # Use a tmp file path to avoid incomplete YAML file being re-used in the
    # future.
    tmp_yaml_path = yaml_path + '.tmp'
    tmp_yaml_path = fill_template(
        cluster_config_template,
        dict(
            resources_vars,
            **{
                'cluster_name': cluster_name,
                'num_nodes': num_nodes,
                'disk_size': to_provision.disk_size,
                # If the current code is run by controller, propagate the real
                # calling user which should've been passed in as the
                # SKYPILOT_USER env var (see spot-controller.yaml.j2).
                'user': os.environ.get('SKYPILOT_USER', getpass.getuser()),

                # AWS only:
                # Temporary measure, as deleting per-cluster SGs is too slow.
                # See https://github.com/skypilot-org/skypilot/pull/742.
                # Generate the name of the security group we're looking for.
                # (username, last 4 chars of hash of hostname): for uniquefying
                # users on shared-account scenarios.
                'security_group': skypilot_config.get_nested(
                    ('aws', 'security_group_name'),
                    f'sky-sg-{common_utils.user_and_hostname_hash()}'),
                'vpc_name': skypilot_config.get_nested(('aws', 'vpc_name'),
                                                       None),
                'use_internal_ips': skypilot_config.get_nested(
                    ('aws', 'use_internal_ips'), False),
                # Not exactly AWS only, but we only test it's supported on AWS
                # for now:
                'ssh_proxy_command': ssh_proxy_command,

                # Azure only:
                'azure_subscription_id': azure_subscription_id,
                'resource_group': f'{cluster_name}-{region_name}',

                # GCP only:
                'gcp_project_id': gcp_project_id,

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
    # Delay the optimization of the config until the authentication files is
    # added.
    if not isinstance(cloud, clouds.Local):
        # Only optimize the file mounts for public clouds now, as local has not
        # been fully tested yet.
        _optimize_file_mounts(tmp_yaml_path)

    # Restore the old yaml content for backward compatibility.
    if os.path.exists(yaml_path) and keep_launch_fields_in_existing_config:
        with open(yaml_path, 'r') as f:
            old_yaml_content = f.read()
        with open(tmp_yaml_path, 'r') as f:
            new_yaml_content = f.read()
        restored_yaml_content = _replace_yaml_dicts(
            new_yaml_content, old_yaml_content,
            _RAY_YAML_KEYS_TO_RESTORE_FOR_BACK_COMPATIBILITY)
        with open(tmp_yaml_path, 'w') as f:
            f.write(restored_yaml_content)

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

        from sky.skylet.providers.gcp import config as gcp_config  # pylint: disable=import-outside-toplevel
        config = common_utils.read_yaml(os.path.expanduser(config_dict['ray']))
        vpc_name = gcp_config.get_usable_vpc(config)

        scripts = tuple(
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
                output_path=os.path.join(user_file_dir, template_name).replace(
                    '.sh.j2', f'.{cluster_name}.sh'),
            ) for template_name in
            ['gcp-tpu-create.sh.j2', 'gcp-tpu-delete.sh.j2'])
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
    if isinstance(cloud, clouds.AWS):
        config = auth.setup_aws_authentication(config)
    elif isinstance(cloud, clouds.GCP):
        config = auth.setup_gcp_authentication(config)
    elif isinstance(cloud, clouds.Azure):
        config = auth.setup_azure_authentication(config)
    elif isinstance(cloud, clouds.Lambda):
        config = auth.setup_lambda_authentication(config)
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


@timeline.event
def wait_until_ray_cluster_ready(
    cluster_config_file: str,
    num_nodes: int,
    log_path: str,
    is_local_cloud: bool = False,
    nodes_launching_progress_timeout: Optional[int] = None,
) -> bool:
    """Returns whether the entire ray cluster is ready."""
    if num_nodes <= 1:
        return

    # Manually fetching head ip instead of using `ray exec` to avoid the bug
    # that `ray exec` fails to connect to the head node after some workers
    # launched especially for Azure.
    try:
        head_ip = _query_head_ip_with_retries(
            cluster_config_file, max_attempts=WAIT_HEAD_NODE_IP_MAX_ATTEMPTS)
    except RuntimeError as e:
        logger.error(e)
        return False  # failed

    ssh_credentials = ssh_credential_from_yaml(cluster_config_file)
    last_nodes_so_far = 0
    start = time.time()
    runner = command_runner.SSHCommandRunner(head_ip, **ssh_credentials)
    with console.status('[bold cyan]Waiting for workers...') as worker_status:
        while True:
            rc, output, stderr = runner.run('ray status',
                                            log_path=log_path,
                                            stream_logs=False,
                                            require_outputs=True,
                                            separate_stderr=True)
            subprocess_utils.handle_returncode(
                rc, 'ray status', 'Failed to run ray status on head node.',
                stderr)
            logger.debug(output)

            # Workers that are ready
            ready_workers = 0
            # On-prem/local case is handled differently.
            # `ray status` produces different output for local case, and
            # we poll for number of nodes launched instead of counting for
            # head and number of worker nodes separately (it is impossible
            # to distinguish between head and worker node for local case).
            if is_local_cloud:
                result = _LAUNCHED_LOCAL_WORKER_PATTERN.findall(output)
                # In the local case, ready_workers mean the total number
                # of nodes launched, including head.
                ready_workers = len(result)
            else:
                result = _LAUNCHED_WORKER_PATTERN.findall(output)
                if len(result) == 0:
                    ready_workers = 0
                else:
                    assert len(result) == 1, result
                    ready_workers = int(result[0])

            result = _LAUNCHED_HEAD_PATTERN.findall(output)
            ready_head = 0
            if result:
                assert len(result) == 1, result
                ready_head = int(result[0])
                assert ready_head <= 1, ready_head

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
                worker_status.stop()
                logger.error(
                    'Timed out: waited for more than '
                    f'{nodes_launching_progress_timeout} seconds for new '
                    'workers to be provisioned, but no progress.')
                return False  # failed

            if '(no pending nodes)' in output and '(no failures)' in output:
                # Bug in ray autoscaler: e.g., on GCP, if requesting 2 nodes
                # that GCP can satisfy only by half, the worker node would be
                # forgotten. The correct behavior should be for it to error out.
                worker_status.stop()
                logger.error(
                    'Failed to launch multiple nodes on '
                    'GCP due to a nondeterministic bug in ray autoscaler.')
                return False  # failed
            time.sleep(10)
    return True  # success


def ssh_credential_from_yaml(cluster_yaml: str) -> Dict[str, str]:
    """Returns ssh_user, ssh_private_key and ssh_control name."""
    config = common_utils.read_yaml(cluster_yaml)
    auth_section = config['auth']
    ssh_user = auth_section['ssh_user'].strip()
    ssh_private_key = auth_section.get('ssh_private_key')
    ssh_control_name = config.get('cluster_name', '__default__')
    ssh_proxy_command = auth_section.get('ssh_proxy_command')
    return {
        'ssh_user': ssh_user,
        'ssh_private_key': ssh_private_key,
        'ssh_control_name': ssh_control_name,
        'ssh_proxy_command': ssh_proxy_command,
    }


def parallel_data_transfer_to_nodes(
    runners: List[command_runner.SSHCommandRunner],
    source: str,
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
        runners: A list of SSHCommandRunner's that represent multiple nodes.
        source_target: Tuple[str, str]; Source for rsync on local node and
            Destination on remote node for rsync
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
            subprocess_utils.handle_returncode(
                rc,
                cmd, ('Failed to run command before rsync '
                      f'{origin_source} -> {target}.'),
                stderr=stdout + stderr)

        if run_rsync:
            # TODO(zhwu): Optimize for large amount of files.
            # zip / transfer/ unzip
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
    with safe_console_status(f'[bold cyan]{action_message}[/]'):
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


def get_cleaned_username() -> str:
    """Cleans the current username to be used as part of a cluster name.

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
    username = getpass.getuser()
    username = username.lower()
    username = re.sub(r'[^a-z0-9-]', '', username)
    username = re.sub(r'^[0-9-]+', '', username)
    username = re.sub(r'-$', '', username)
    return username


def _query_head_ip_with_retries(cluster_yaml: str,
                                max_attempts: int = 1) -> str:
    """Returns the IP of the head node by querying the cloud.

    Raises:
      RuntimeError: if we failed to get the head IP.
    """
    backoff = common_utils.Backoff(initial_backoff=5, max_backoff_factor=5)
    for i in range(max_attempts):
        try:
            full_cluster_yaml = str(pathlib.Path(cluster_yaml).expanduser())
            out = subprocess_utils.run(
                f'ray get-head-ip {full_cluster_yaml!r}',
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL).stdout.decode().strip()
            head_ip = re.findall(IP_ADDR_REGEX, out)
            if len(head_ip) > 1:
                # This could be triggered if e.g., some logging is added in
                # skypilot_config, a module that has some code executed
                # whenever `sky` is imported.
                logger.warning(
                    'Detected more than 1 IP from the output of '
                    'the `ray get-head-ip` command. This could '
                    'happen if there is extra output from it, '
                    'which should be inspected below.\nProceeding with '
                    f'the last detected IP ({head_ip[-1]}) as head IP.'
                    f'\n== Output ==\n{out}'
                    f'\n== Output ends ==')
                head_ip = head_ip[-1:]
            assert 1 == len(head_ip), (out, head_ip)
            head_ip = head_ip[0]
            break
        except subprocess.CalledProcessError as e:
            if i == max_attempts - 1:
                raise RuntimeError('Failed to get head ip') from e
            # Retry if the cluster is not up yet.
            logger.debug('Retrying to get head ip.')
            time.sleep(backoff.current_backoff())
    return head_ip


@timeline.event
def get_node_ips(cluster_yaml: str,
                 expected_num_nodes: int,
                 handle: Optional[backends.Backend.ResourceHandle] = None,
                 head_ip_max_attempts: int = 1,
                 worker_ip_max_attempts: int = 1,
                 get_internal_ips: bool = False) -> List[str]:
    """Returns the IPs of all nodes in the cluster, with head node at front."""
    # When ray up launches TPU VM Pod, Pod workers (except for the head)
    # won't be connected to Ray cluster. Thus "ray get-worker-ips"
    # won't work and we need to query the node IPs with gcloud as
    # implmented in _get_tpu_vm_pod_ips.
    ray_config = common_utils.read_yaml(cluster_yaml)
    use_tpu_vm = ray_config['provider'].get('_has_tpus', False)
    if use_tpu_vm:
        assert expected_num_nodes == 1, (
            'TPU VM only supports single node for now.')
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
    try:
        head_ip = _query_head_ip_with_retries(cluster_yaml,
                                              max_attempts=head_ip_max_attempts)
    except RuntimeError as e:
        raise exceptions.FetchIPError(
            exceptions.FetchIPError.Reason.HEAD) from e
    head_ip = [head_ip]
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
                if ip == head_ip[0]:
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
    return head_ip + worker_ips


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

        if not get_internal_ips:
            ips = [
                endpoint['accessConfig']['externalIp']
                for endpoint in tpuvm_json['networkEndpoints']
            ]
        else:
            ips = [
                endpoint['ipAddress']
                for endpoint in tpuvm_json['networkEndpoints']
            ]
        all_ips.extend(ips)

    return all_ips


@timeline.event
def get_head_ip(
    handle: backends.Backend.ResourceHandle,
    use_cached_head_ip: bool = True,
    max_attempts: int = 1,
) -> str:
    """Returns the ip of the head node."""
    if use_cached_head_ip:
        if handle.head_ip is None:
            # This happens for INIT clusters (e.g., exit 1 in setup).
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'Cluster\'s head IP not found; is it up? To fix: '
                    'run a successful launch first (`sky launch`) to ensure'
                    ' the cluster status is UP (`sky status`).')
        head_ip = handle.head_ip
    else:
        head_ip = _query_head_ip_with_retries(handle.cluster_yaml, max_attempts)
    return head_ip


def run_command_and_handle_ssh_failure(
        runner: command_runner.SSHCommandRunner,
        command: str,
        failure_message: Optional[str] = None) -> str:
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


def do_filemounts_and_setup_on_local_workers(
        cluster_config_file: str,
        worker_ips: List[str] = None,
        extra_setup_cmds: List[str] = None):
    """Completes filemounting and setup on worker nodes.

    Syncs filemounts and runs setup on worker nodes for a local cluster. This
    is a workaround for a Ray Autoscaler bug where `ray up` does not perform
    filemounting or setup for local cluster worker nodes.
    """
    config = common_utils.read_yaml(cluster_config_file)

    ssh_credentials = ssh_credential_from_yaml(cluster_config_file)
    if worker_ips is None:
        worker_ips = config['provider']['worker_ips']
    file_mounts = config['file_mounts']

    setup_cmds = config['setup_commands']
    if extra_setup_cmds is not None:
        setup_cmds += extra_setup_cmds
    setup_script = log_lib.make_task_bash_script('\n'.join(setup_cmds))

    worker_runners = command_runner.SSHCommandRunner.make_runner_list(
        worker_ips, **ssh_credentials)

    # Uploads setup script to the worker node
    with tempfile.NamedTemporaryFile('w', prefix='sky_setup_') as f:
        f.write(setup_script)
        f.flush()
        setup_sh_path = f.name
        setup_file = os.path.basename(setup_sh_path)
        file_mounts[f'/tmp/{setup_file}'] = setup_sh_path

        # Ray Autoscaler Bug: Filemounting + Ray Setup
        # does not happen on workers.
        def _setup_local_worker(runner: command_runner.SSHCommandRunner):
            for dst, src in file_mounts.items():
                mkdir_dst = f'mkdir -p {os.path.dirname(dst)}'
                run_command_and_handle_ssh_failure(
                    runner,
                    mkdir_dst,
                    failure_message=f'Failed to run {mkdir_dst} on remote.')
                if os.path.isdir(src):
                    src = os.path.join(src, '')
                runner.rsync(source=src, target=dst, up=True, stream_logs=False)

            setup_cmd = f'/bin/bash -i /tmp/{setup_file} 2>&1'
            rc, stdout, _ = runner.run(setup_cmd,
                                       stream_logs=False,
                                       require_outputs=True)
            subprocess_utils.handle_returncode(
                rc,
                setup_cmd,
                'Failed to setup Ray autoscaler commands on remote.',
                stderr=stdout)

        subprocess_utils.run_in_parallel(_setup_local_worker, worker_runners)


def check_network_connection():
    # Tolerate 3 retries as it is observed that connections can fail.
    adapter = adapters.HTTPAdapter(max_retries=retry_lib.Retry(total=3))
    http = requests.Session()
    http.mount('https://', adapter)
    http.mount('http://', adapter)
    try:
        http.head(_TEST_IP, timeout=3)
    except requests.Timeout as e:
        raise exceptions.NetworkError(
            'Could not refresh the cluster. Network seems down.') from e


def _process_cli_query(
    cloud: str, cluster: str, query_cmd: str, deliminiator: str,
    status_map: Dict[str, global_user_state.ClusterStatus]
) -> List[global_user_state.ClusterStatus]:
    """Run the cloud CLI query and returns cluster status.

    Args:
        cloud: The cloud provider name.
        cluster: The cluster name.
        query_cmd: The cloud CLI query command.
        deliminiator: The deliminiator separating the status in the output
            of the query command.
        status_map: A map from the CLI status string to the corresponding
            global_user_state.ClusterStatus.
    Returns:
        A list of global_user_state.ClusterStatus of all existing nodes in the
        cluster. The list can be empty if none of the nodes in the clusters are
        found, i.e. the nodes are all terminated.
    """
    returncode, stdout, stderr = log_lib.run_with_log(query_cmd,
                                                      '/dev/null',
                                                      require_outputs=True,
                                                      shell=True)
    logger.debug(f'{query_cmd} returned {returncode}.\n'
                 '**** STDOUT ****\n'
                 f'{stdout}\n'
                 '**** STDERR ****\n'
                 f'{stderr}')
    if (cloud == str(clouds.Azure()) and returncode == 2 and
            'argument --ids: expected at least one argument' in stderr):
        # Azure CLI has a returncode 2 when the cluster is not found, as
        # --ids <empty> is passed to the query command. In that case, the
        # cluster should be considered as DOWN.
        return []

    if returncode != 0:
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ClusterStatusFetchingError(
                f'Failed to query {cloud} cluster {cluster!r} status: '
                f'{stdout + stderr}')

    cluster_status = stdout.strip()
    if cluster_status == '':
        return []
    return [
        status_map[s]
        for s in cluster_status.split(deliminiator)
        if status_map[s] is not None
    ]


def _ray_launch_hash(cluster_name: str,
                     ray_config: Dict[str, Any]) -> Optional[Set[str]]:
    """Returns a set of Ray launch config hashes, one per node type.

    This returns None if ray's _bootstrap_config() failed to return, which can
    happen if node providers' bootstrapping phase (config.py) raises an error
    (which *should* only happen on errors prior to nodes launching, e.g.,
    VPC/subnet setup).
    """
    # Use the cached Ray launch hashes if they exist.
    metadata = global_user_state.get_cluster_metadata(cluster_name)
    assert metadata is not None, cluster_name
    ray_launch_hashes = metadata.get('ray_launch_hashes', None)
    if ray_launch_hashes is not None:
        logger.debug('Using cached launch_hashes.')
        return set(ray_launch_hashes)
    try:
        with ux_utils.suppress_output():
            ray_config = ray_commands._bootstrap_config(ray_config)  # pylint: disable=protected-access
    except RuntimeError as e:
        # TODO(zongheng): is this safe? Could it be node(s) are live but somehow a
        # separate status refresh hits such errors?
        if 'SKYPILOT_ERROR_NO_NODES_LAUNCHED' in str(e):
            logger.error(f'Error found when refreshing cluster status: {e}')
            return None
        raise e
    # Adopted from https://github.com/ray-project/ray/blob/ray-2.0.1/python/ray/autoscaler/_private/node_launcher.py#L87-L97
    # TODO(zhwu): this logic is duplicated from the ray code above (keep in
    # sync).
    launch_hashes = set()
    head_node_type = ray_config['head_node_type']
    for node_type, node_config in ray_config['available_node_types'].items():
        if node_type == head_node_type:
            launch_config = ray_config.get('head_node', {})
            auth_config = ray_config['auth']
        else:
            launch_config = ray_config.get('worker_nodes', {})
            auth_config = dict(ray_config['auth'])
        # Why pop ssh_proxy_command for both head and workers:
        #
        # When we launch the head node from the local client: our call to `ray
        # up` has a monkey-patched version of hash_launch_conf(), which drops
        # this field.
        #
        # When the head node launches worker nodes: On the head node,
        # ~/ray_bootstrap_config.yaml, which has any ssh_proxy_command field
        # removed (see Ray's autoscaler/_private/commands.py), is passed to the
        # autoscaler. Therefore when Ray calculates the hash for workers,
        # ssh_proxy_command is not included. Here we follow this (otherwise our
        # hash here would not match with what's on the console for workers).
        auth_config.pop('ssh_proxy_command', None)
        launch_config = copy.deepcopy(launch_config)
        launch_config.update(node_config['node_config'])
        with ux_utils.suppress_output():
            current_hash = ray_autoscaler_private_util.hash_launch_conf(
                launch_config, auth_config)
        launch_hashes.add(current_hash)
    # Cache the launch hashes for the cluster.
    metadata['ray_launch_hashes'] = list(launch_hashes)
    global_user_state.set_cluster_metadata(cluster_name, metadata)
    return launch_hashes


def _query_status_aws(
    cluster: str,
    ray_config: Dict[str, Any],
) -> List[global_user_state.ClusterStatus]:
    status_map = {
        'pending': global_user_state.ClusterStatus.INIT,
        'running': global_user_state.ClusterStatus.UP,
        # TODO(zhwu): stopping and shutting-down could occasionally fail
        # due to internal errors of AWS. We should cover that case.
        'stopping': global_user_state.ClusterStatus.STOPPED,
        'stopped': global_user_state.ClusterStatus.STOPPED,
        'shutting-down': None,
        'terminated': None,
    }
    region = ray_config['provider']['region']
    launch_hashes = _ray_launch_hash(cluster, ray_config)
    if launch_hashes is not None:
        hash_filter_str = ','.join(launch_hashes)
        hash_filter_line = (
            f'Name=tag:ray-launch-config,Values={hash_filter_str} ')
    else:
        hash_filter_line = ''
    query_cmd = ('aws ec2 describe-instances --filters '
                 f'Name=tag:ray-cluster-name,Values={cluster} '
                 f'{hash_filter_line}'
                 f'--region {region} '
                 '--query "Reservations[].Instances[].State.Name" '
                 '--output text')
    return _process_cli_query('AWS', cluster, query_cmd, '\t', status_map)


def _query_status_gcp(
    cluster: str,
    ray_config: Dict[str, Any],
) -> List[global_user_state.ClusterStatus]:
    # Note: we use ":" for filtering labels for gcloud, as the latest gcloud (v393.0)
    # fails to filter labels with "=".
    # Reference: https://cloud.google.com/sdk/gcloud/reference/topic/filters
    launch_hashes = _ray_launch_hash(cluster, ray_config)
    assert launch_hashes is not None
    hash_filter_str = ' '.join(launch_hashes)

    use_tpu_vm = ray_config['provider'].get('_has_tpus', False)
    zone = ray_config['provider'].get('availability_zone', '')
    if use_tpu_vm:
        # TPU VM's state definition is different from compute VM
        # https://cloud.google.com/tpu/docs/reference/rest/v2alpha1/projects.locations.nodes#State # pylint: disable=line-too-long
        status_map = {
            'CREATING': global_user_state.ClusterStatus.INIT,
            'STARTING': global_user_state.ClusterStatus.INIT,
            'RESTARTING': global_user_state.ClusterStatus.INIT,
            'READY': global_user_state.ClusterStatus.UP,
            'REPAIRING': global_user_state.ClusterStatus.INIT,
            # 'STOPPED' in GCP TPU VM means stopped, with disk preserved.
            'STOPPING': global_user_state.ClusterStatus.STOPPED,
            'STOPPED': global_user_state.ClusterStatus.STOPPED,
            'DELETING': None,
            'PREEMPTED': None,
        }
        tpu_utils.check_gcp_cli_include_tpu_vm()
        query_cmd = ('gcloud compute tpus tpu-vm list '
                     f'--zone {zone} '
                     f'--filter="(labels.ray-cluster-name={cluster} AND '
                     f'labels.ray-launch-config=({hash_filter_str}))" '
                     '--format="value(state)"')
    else:
        status_map = {
            'PROVISIONING': global_user_state.ClusterStatus.INIT,
            'STARTING': global_user_state.ClusterStatus.INIT,
            'RUNNING': global_user_state.ClusterStatus.UP,
            'REPAIRING': global_user_state.ClusterStatus.INIT,
            # 'TERMINATED' in GCP means stopped, with disk preserved.
            'STOPPING': global_user_state.ClusterStatus.STOPPED,
            'TERMINATED': global_user_state.ClusterStatus.STOPPED,
            # 'SUSPENDED' in GCP means stopped, with disk and OS memory
            # preserved.
            'SUSPENDING': global_user_state.ClusterStatus.STOPPED,
            'SUSPENDED': global_user_state.ClusterStatus.STOPPED,
        }
        # TODO(zhwu): The status of the TPU attached to the cluster should also
        # be checked, since TPUs are not part of the VMs.
        query_cmd = ('gcloud compute instances list '
                     f'--filter="(labels.ray-cluster-name={cluster} AND '
                     f'labels.ray-launch-config=({hash_filter_str}))" '
                     '--format="value(status)"')
    status_list = _process_cli_query('GCP', cluster, query_cmd, '\n',
                                     status_map)

    # GCP does not clean up preempted TPU VMs. We remove it ourselves.
    # TODO(wei-lin): handle multi-node cases.
    if use_tpu_vm and len(status_list) == 0:
        logger.debug(f'Terminating preempted TPU VM cluster {cluster}')
        backend = backends.CloudVmRayBackend()
        handle = global_user_state.get_handle_from_cluster_name(cluster)
        # Do not use refresh cluster status during teardown, as that will
        # cause inifinite recursion by calling cluster status refresh
        # again.
        # The caller of this function, `_update_cluster_status_no_lock() ->
        # _get_cluster_status_via_cloud_cli()`, will do the post teardown
        # cleanup, which will remove the cluster entry from the status table
        # & the ssh config file.
        backend.teardown_no_lock(handle,
                                 terminate=True,
                                 purge=False,
                                 post_teardown_cleanup=False,
                                 refresh_cluster_status=False)
    return status_list


def _query_status_azure(
    cluster: str,
    ray_config: Dict[str, Any],
) -> List[global_user_state.ClusterStatus]:
    status_map = {
        'VM starting': global_user_state.ClusterStatus.INIT,
        'VM running': global_user_state.ClusterStatus.UP,
        # 'VM stopped' in Azure means Stopped (Allocated), which still bills
        # for the VM.
        'VM stopping': global_user_state.ClusterStatus.INIT,
        'VM stopped': global_user_state.ClusterStatus.INIT,
        # 'VM deallocated' in Azure means Stopped (Deallocated), which does not
        # bill for the VM.
        'VM deallocating': global_user_state.ClusterStatus.STOPPED,
        'VM deallocated': global_user_state.ClusterStatus.STOPPED,
    }
    launch_hashes = _ray_launch_hash(cluster, ray_config)
    assert launch_hashes is not None
    hash_filter_str = ', '.join(f'\\"{h}\\"' for h in launch_hashes)
    query_cmd = (
        'az vm show -d --ids $(az vm list --query '
        f'"[?tags.\\"ray-cluster-name\\" == \'{cluster}\' && '
        f'contains(\'[{hash_filter_str}]\', tags.\\"ray-launch-config\\")].id" '
        '-o tsv) --query "powerState" -o tsv')
    # NOTE: Azure cli should be handled carefully. The query command above
    # takes about 1 second to run.
    # An alternative is the following command, but it will take more than
    # 20 seconds to run.
    # query_cmd = (
    #     f'az vm list --show-details --query "['
    #     f'?tags.\\"ray-cluster-name\\" == \'{handle.cluster_name}\' '
    #     '&& tags.\\"ray-node-type\\" == \'head\'].powerState" -o tsv'
    # )
    return _process_cli_query('Azure', cluster, query_cmd, '\t', status_map)


def _query_status_lambda(
        cluster: str,
        ray_config: Dict[str, Any],  # pylint: disable=unused-argument
) -> List[global_user_state.ClusterStatus]:
    status_map = {
        'booting': global_user_state.ClusterStatus.INIT,
        'active': global_user_state.ClusterStatus.UP,
        'unhealthy': global_user_state.ClusterStatus.INIT,
        'terminated': None,
    }
    # TODO(ewzeng): filter by hash_filter_string to be safe
    vms = lambda_utils.LambdaCloudClient().list_instances()
    for node in vms:
        if node['name'] == cluster:
            return [status_map[node['status']]]
    return []


_QUERY_STATUS_FUNCS = {
    'AWS': _query_status_aws,
    'GCP': _query_status_gcp,
    'Azure': _query_status_azure,
    'Lambda': _query_status_lambda,
}


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
    if not isinstance(handle, backends.CloudVmRayBackend.ResourceHandle):
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
    elif owner_identity != current_user_identity:
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ClusterOwnerIdentityMismatchError(
                f'{cluster_name!r} ({cloud}) is owned by account '
                f'{owner_identity!r}, but the activated account '
                f'is {current_user_identity!r}.')


def _get_cluster_status_via_cloud_cli(
    handle: 'backends.CloudVmRayBackend.ResourceHandle'
) -> List[global_user_state.ClusterStatus]:
    """Returns the status of the cluster."""
    resources: sky.Resources = handle.launched_resources
    cloud = resources.cloud
    ray_config = common_utils.read_yaml(handle.cluster_yaml)
    return _QUERY_STATUS_FUNCS[str(cloud)](handle.cluster_name, ray_config)


def _update_cluster_status_no_lock(
        cluster_name: str) -> Optional[Dict[str, Any]]:
    record = global_user_state.get_cluster_from_name(cluster_name)
    if record is None:
        return None
    handle = record['handle']
    if not isinstance(handle, backends.CloudVmRayBackend.ResourceHandle):
        return record

    cluster_name = handle.cluster_name
    try:
        # TODO(zhwu): This function cannot distinguish transient network error
        # in ray's get IPs vs. ray runtime failing.
        external_ips = handle.external_ips(use_cached_ips=False)
        # This happens to a stopped TPU VM as we use gcloud to query the IP.
        if external_ips is None or len(external_ips) == 0:
            raise exceptions.FetchIPError(
                reason=exceptions.FetchIPError.Reason.HEAD)
        if handle.launched_nodes == 1:
            # Check the ray cluster status. We have to check it for single node
            # case, since the get_node_ips() does not require ray cluster to be
            # running.
            ssh_credentials = ssh_credential_from_yaml(handle.cluster_yaml)
            runner = command_runner.SSHCommandRunner(external_ips[0],
                                                     **ssh_credentials)
            returncode = runner.run('ray status', stream_logs=False)
            if returncode:
                raise exceptions.FetchIPError(
                    reason=exceptions.FetchIPError.Reason.HEAD)
        # If we get node ips correctly, the cluster is UP. It is safe to
        # set the status to UP, as the `handle.external_ips` function uses ray
        # to fetch IPs and starting ray is the final step of sky launch.
        record['status'] = global_user_state.ClusterStatus.UP
        global_user_state.add_or_update_cluster(cluster_name,
                                                handle,
                                                requested_resources=None,
                                                ready=True,
                                                is_launch=False)
        return record
    except exceptions.FetchIPError:
        logger.debug('Refreshing status: Failed to get IPs from cluster '
                     f'{cluster_name!r}, trying to fetch from provider.')
    # For all code below, ray fails to get IPs for the cluster.
    node_statuses = _get_cluster_status_via_cloud_cli(handle)

    if len(node_statuses) > handle.launched_nodes:
        # Unexpected: this could mean ray launch hash is not calculated and we
        # used the cluster name as the only filter when querying the cloud
        # provider, and in the queried region more than 1 cluster with the same
        # constructed name tag returned. This will typically not happen unless
        # users manually create a cluster with that constructed name.
        #
        # (Technically speaking, even if returned num nodes <= num
        # handle.launched_nodes), not including the launch hash could mean the
        # returned nodes contain some nodes that do not belong to the logical
        # skypilot cluster. Doesn't seem to be a good way to handle this for
        # now?)
        #
        # We have not experienced the above; adding as a safeguard.
        #
        # Since we failed to refresh, warn and return old record.
        logger.warning(
            f'Failed to refresh status for cluster {cluster_name!r} '
            f'due to {len(node_statuses)} nodes being found with the '
            'same name tag, but the cluster should have '
            f'{handle.launched_nodes} nodes. Keeping the old status.')
        return record
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
    # reset (unless it's autostopping/autodowning.).
    is_abnormal = ((0 < len(node_statuses) < handle.launched_nodes) or
                   any(status != global_user_state.ClusterStatus.STOPPED
                       for status in node_statuses))
    if is_abnormal:
        backend = get_backend_from_handle(handle)
        if isinstance(backend,
                      backends.CloudVmRayBackend) and record['autostop'] >= 0:
            if not backend.is_definitely_autostopping(handle,
                                                      stream_logs=False):
                # Reset the autostopping as the cluster is abnormal, and may
                # not correctly autostop. Resetting the autostop will let
                # the user know that the autostop may not happen to avoid
                # leakages from the assumption that the cluster will autostop.
                try:
                    backend.set_autostop(handle, -1, stream_logs=False)
                except (Exception, SystemExit) as e:  # pylint: disable=broad-except
                    logger.debug(f'Failed to reset autostop. Due to '
                                 f'{common_utils.format_exception(e)}')
                global_user_state.set_cluster_autostop_value(
                    handle.cluster_name, -1, to_down=False)
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
          fetched from the cloud provider.
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
        force_refresh: bool = False,
        acquire_per_cluster_status_lock: bool = True
) -> Optional[Dict[str, Any]]:
    """Refresh the cluster, and return the possibly updated record.

    This function will also check the owner identity of the cluster, and raise
    exceptions if the current user is not the same as the user who created the
    cluster.

    Args:
        cluster_name: The name of the cluster.
        force_refresh: refresh the cluster status as long as the cluster exists.
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
          fetched from the cloud provider.
    """

    record = global_user_state.get_cluster_from_name(cluster_name)
    if record is None:
        return None
    check_owner_identity(cluster_name)

    handle = record['handle']
    if isinstance(handle, backends.CloudVmRayBackend.ResourceHandle):
        use_spot = handle.launched_resources.use_spot
        has_autostop = (
            record['status'] != global_user_state.ClusterStatus.STOPPED and
            record['autostop'] >= 0)
        if force_refresh or has_autostop or use_spot:
            record = _update_cluster_status(
                cluster_name,
                acquire_per_cluster_status_lock=acquire_per_cluster_status_lock)
    return record


@timeline.event
def refresh_cluster_status_handle(
    cluster_name: str,
    *,
    force_refresh: bool = False,
    acquire_per_cluster_status_lock: bool = True,
) -> Tuple[Optional[global_user_state.ClusterStatus],
           Optional[backends.Backend.ResourceHandle]]:
    """Refresh the cluster, and return the possibly updated status and handle.

    This is a wrapper of refresh_cluster_record, which returns the status and
    handle of the cluster.
    Please refer to the docstring of refresh_cluster_record for the details.
    """
    record = _refresh_cluster_record(
        cluster_name,
        force_refresh=force_refresh,
        acquire_per_cluster_status_lock=acquire_per_cluster_status_lock)
    if record is None:
        return None, None
    return record['status'], record['handle']


def check_cluster_available(
    cluster_name: str,
    *,
    operation: str,
    check_cloud_vm_ray_backend: bool = True,
) -> backends.Backend.ResourceHandle:
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
        record = global_user_state.get_cluster_from_name(cluster_name)
        cluster_status, handle = record['status'], record['handle']

    if handle is None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                f'{colorama.Fore.YELLOW}Cluster {cluster_name!r} does not '
                f'exist.{colorama.Style.RESET_ALL}')
    backend = get_backend_from_handle(handle)
    if check_cloud_vm_ray_backend and not isinstance(
            backend, backends.CloudVmRayBackend):
        with ux_utils.print_exception_no_traceback():
            raise exceptions.NotSupportedError(
                f'{colorama.Fore.YELLOW}{operation.capitalize()}: skipped for '
                f'cluster {cluster_name!r}. It is only supported by backend: '
                f'{backends.CloudVmRayBackend.NAME}.'
                f'{colorama.Style.RESET_ALL}')
    if cluster_status != global_user_state.ClusterStatus.UP:
        if onprem_utils.check_if_local_cloud(cluster_name):
            raise exceptions.ClusterNotUpError(
                constants.UNINITIALIZED_ONPREM_CLUSTER_MESSAGE.format(
                    cluster_name))
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ClusterNotUpError(
                f'{colorama.Fore.YELLOW}{operation.capitalize()}: skipped for '
                f'cluster {cluster_name!r} (status: {cluster_status.value}). '
                'It is only allowed for '
                f'{global_user_state.ClusterStatus.UP.value} clusters.'
                f'{colorama.Style.RESET_ALL}')

    if handle.head_ip is None:
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ClusterNotUpError(
                f'Cluster {cluster_name!r} has been stopped or not properly '
                'set up. Please re-launch it with `sky start`.')
    return handle


class CloudFilter(enum.Enum):
    # Filter for all types of clouds.
    ALL = 'all'
    # Filter for Sky's main clouds (aws, gcp, azure, docker).
    CLOUDS_AND_DOCKER = 'clouds-and-docker'
    # Filter for only local clouds.
    LOCAL = 'local'


def get_clusters(
    include_reserved: bool,
    refresh: bool,
    cloud_filter: str = CloudFilter.CLOUDS_AND_DOCKER,
    cluster_names: Optional[Union[str, Sequence[str]]] = None,
) -> List[Dict[str, Any]]:
    """Returns a list of cached or optionally refreshed cluster records.

    Combs through the database (in ~/.sky/state.db) to get a list of records
    corresponding to launched clusters (filtered by `cluster_names` if it is
    specified). The refresh flag can be used to force a refresh of the status
    of the clusters.

    Args:
        include_reserved: Whether to include reserved clusters, e.g. spot
            controller.
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

    if not include_reserved:
        records = [
            record for record in records
            if record['name'] not in SKY_RESERVED_CLUSTER_NAMES
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
        if isinstance(handle, backends.LocalDockerBackend.ResourceHandle):
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
                force_refresh=True,
                acquire_per_cluster_status_lock=True)
        except (exceptions.ClusterStatusFetchingError,
                exceptions.CloudUserIdentityError,
                exceptions.ClusterOwnerIdentityMismatchError,
                exceptions.ClusterStatusFetchingError) as e:
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


def get_backend_from_handle(
        handle: backends.Backend.ResourceHandle) -> backends.Backend:
    """Gets a Backend object corresponding to a handle.

    Inspects handle type to infer the backend used for the resource.
    """
    if isinstance(handle, backends.CloudVmRayBackend.ResourceHandle):
        backend = backends.CloudVmRayBackend()
    elif isinstance(handle, backends.LocalDockerBackend.ResourceHandle):
        backend = backends.LocalDockerBackend()
    else:
        raise NotImplementedError(
            f'Handle type {type(handle)} is not supported yet.')
    return backend


class NoOpConsole:
    """An empty class for multi-threaded console.status."""

    def __enter__(self):
        pass

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


def safe_console_status(msg: str):
    """A wrapper for multi-threaded console.status."""
    if threading.current_thread() is threading.main_thread():
        return console.status(msg)
    return NoOpConsole()


def get_task_demands_dict(
        task: 'task_lib.Task') -> Optional[Tuple[Optional[str], int]]:
    """Returns the accelerator dict of the task"""
    # TODO: CPU and other memory resources are not supported yet.
    accelerator_dict = None
    if task.best_resources is not None:
        resources = task.best_resources
    else:
        # Task may (e.g., sky launch) or may not (e.g., sky exec) have undergone
        # sky.optimize(), so best_resources may be None.
        assert len(task.resources) == 1, task.resources
        resources = list(task.resources)[0]
    if resources is not None:
        accelerator_dict = resources.accelerators
    return accelerator_dict


def get_task_resources_str(task: 'task_lib.Task') -> str:
    resources_dict = get_task_demands_dict(task)
    if resources_dict is None:
        resources_str = f'CPU:{DEFAULT_TASK_CPU_DEMAND}'
    else:
        resources_str = ', '.join(f'{k}:{v}' for k, v in resources_dict.items())
    resources_str = f'{task.num_nodes}x [{resources_str}]'
    return resources_str


def check_cluster_name_not_reserved(
        cluster_name: Optional[str],
        operation_str: Optional[str] = None) -> None:
    """Errors out if the cluster is a reserved cluster (spot controller).

    Raises:
      sky.exceptions.NotSupportedError: if the cluster name is reserved, raise
        with an error message explaining 'operation_str' is not allowed.

    Returns:
      None, if the cluster name is not reserved.
    """
    if cluster_name in SKY_RESERVED_CLUSTER_NAMES:
        msg = (f'Cluster {cluster_name!r} is reserved for the '
               f'{SKY_RESERVED_CLUSTER_NAMES[cluster_name].lower()}.')
        if operation_str is not None:
            msg += f' {operation_str} is not allowed.'
        with ux_utils.print_exception_no_traceback():
            raise exceptions.NotSupportedError(msg)


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


def validate_schema(obj, schema, err_msg_prefix=''):
    err_msg = None
    try:
        validator.SchemaValidator(schema).validate(obj)
    except jsonschema.ValidationError as e:
        if e.validator == 'additionalProperties':
            err_msg = err_msg_prefix + 'The following fields are invalid:'
            known_fields = set(e.schema.get('properties', {}).keys())
            for field in e.instance:
                if field not in known_fields:
                    most_similar_field = difflib.get_close_matches(
                        field, known_fields, 1)
                    if most_similar_field:
                        err_msg += (f'\nInstead of {field!r}, did you mean '
                                    f'{most_similar_field[0]!r}?')
                    else:
                        err_msg += f'\nFound unsupported field {field!r}.'
        else:
            err_msg = err_msg_prefix + e.message

    if err_msg:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(err_msg)


def check_public_cloud_enabled():
    """Checks if any of the public clouds is enabled."""

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
            raise RuntimeError(
                'Cloud access is not set up. Run: '
                f'{colorama.Style.BRIGHT}sky check{colorama.Style.RESET_ALL}')
