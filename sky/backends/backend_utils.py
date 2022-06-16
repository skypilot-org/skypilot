"""Util constants/functions for the backends."""
import contextlib
import copy
import datetime
import difflib
import enum
import hashlib
import getpass
from multiprocessing import pool
import os
import pathlib
import random
import re
import shlex
import socket
import subprocess
import sys
import textwrap
import threading
import time
import typing
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
import uuid

import colorama
import filelock
import jinja2
import psutil
import requests
from requests import adapters
from requests.packages.urllib3.util import retry as retry_lib
from ray.autoscaler._private import commands as ray_commands
from ray.autoscaler._private import util as ray_util
import rich.console as rich_console
import rich.progress as rich_progress
import yaml

import sky
from sky import authentication as auth
from sky import backends
from sky import check as sky_check
from sky import clouds
from sky import global_user_state
from sky import exceptions
from sky import sky_logging
from sky import spot as spot_lib
from sky.skylet import log_lib
from sky.utils import timeline

if typing.TYPE_CHECKING:
    from sky import resources
    from sky import task as task_lib

logger = sky_logging.init_logger(__name__)
console = rich_console.Console()

# NOTE: keep in sync with the cluster template 'file_mounts'.
SKY_REMOTE_WORKDIR = log_lib.SKY_REMOTE_WORKDIR
SKY_REMOTE_APP_DIR = '~/.sky/sky_app'
SKY_RAY_YAML_REMOTE_PATH = '~/.sky/sky_ray.yml'
IP_ADDR_REGEX = r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}'
SKY_REMOTE_RAY_VERSION = '1.10.0'
SKY_REMOTE_PATH = '~/.sky/sky_wheels'
SKY_USER_FILE_PATH = '~/.sky/generated'

BOLD = '\033[1m'
RESET_BOLD = '\033[0m'

# Do not use /tmp because it gets cleared on VM restart.
_SKY_REMOTE_FILE_MOUNTS_DIR = '~/.sky/file_mounts/'

_LAUNCHED_HEAD_PATTERN = re.compile(r'(\d+) ray[._]head[._]default')
_LAUNCHED_WORKER_PATTERN = re.compile(r'(\d+) ray[._]worker[._]default')
# Intentionally not using prefix 'rf' for the string format because yapf have a
# bug with python=3.6.
# 10.133.0.5: ray.worker.default,
_LAUNCHING_IP_PATTERN = re.compile(
    r'({}): ray[._]worker[._]default'.format(IP_ADDR_REGEX))
WAIT_HEAD_NODE_IP_RETRY_COUNT = 3

# We use fixed IP address to avoid DNS lookup blocking the check, for machine
# with no internet connection.
# Refer to: https://stackoverflow.com/questions/3764291/how-can-i-see-if-theres-an-available-and-active-network-connection-in-python # pylint: disable=line-too-long
_TEST_IP = 'https://8.8.8.8'

# GCP has a 63 char limit; however, Ray autoscaler adds many
# characters. Through testing, 37 chars is the maximum length for the Sky
# cluster name on GCP.  Ref:
# https://cloud.google.com/compute/docs/naming-resources#resource-name-format
_MAX_CLUSTER_NAME_LEN = 37

# Allow each CPU thread take 2 tasks.
# Note: This value cannot be too small, otherwise OOM issue may occur.
DEFAULT_TASK_CPU_DEMAND = 0.5

SKY_RESERVED_CLUSTER_NAMES = [spot_lib.SPOT_CONTROLLER_NAME]

# Filelocks for the cluster status change.
CLUSTER_STATUS_LOCK_PATH = os.path.expanduser('~/.sky/.{}.lock')
CLUSTER_STATUS_LOCK_TIMEOUT_SECONDS = 10


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
        output_path = pathlib.Path(
            output_prefix).expanduser() / f'{cluster_name}.yml'
        os.makedirs(output_path.parents[0], exist_ok=True)
        output_path = str(output_path)
    output_path = os.path.abspath(output_path)

    # Add yaml file path to the template variables.
    variables['sky_ray_yaml_remote_path'] = SKY_RAY_YAML_REMOTE_PATH
    variables['sky_ray_yaml_local_path'] = output_path
    template = jinja2.Template(template)
    content = template.render(**variables)
    with open(output_path, 'w') as fout:
        fout.write(content)
    return output_path


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
                              ip: str, username: str, ssh_key_path: str):
        codegen = textwrap.dedent(f"""\
            {autogen_comment}
            Host {host_name}
              HostName {ip}
              User {username}
              IdentityFile {ssh_key_path}
              IdentitiesOnly yes
              ForwardAgent yes
              StrictHostKeyChecking no
              Port 22
            """)
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
            ips: List of IP addresses in the cluster. First IP is head node.
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

        codegen = cls._get_generated_config(sky_autogen_comment, host_name, ip,
                                            username, key_path)

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
        worker_ips: List[str],
        auth_config: Dict[str, str],
    ):
        username = auth_config['ssh_user']
        key_path = os.path.expanduser(auth_config['ssh_private_key'])
        host_name = cluster_name
        sky_autogen_comment = ('# Added by sky (use `sky stop/down '
                               f'{cluster_name}` to remove)')

        overwrites = [False] * len(worker_ips)
        overwrite_begin_idxs = [None] * len(worker_ips)
        codegens = [None] * len(worker_ips)
        worker_names = []
        extra_path_name = cls.ssh_multinode_path.format(cluster_name)

        for idx in range(len(worker_ips)):
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

        # Check if ~/.ssh/config contains existing names
        host_lines = [f'Host {c_name}' for c_name in worker_names]
        for i, line in enumerate(config):
            if line.strip() in host_lines:
                idx = host_lines.index(line.strip())
                prev_line = config[i - 1] if i > 0 else ''
                logger.warning(f'{cls.ssh_conf_path} contains '
                               f'host named {worker_names[idx]}.')
                host_name = worker_ips[idx]
                logger.warning(f'Using {host_name} to identify host instead.')
                codegens[idx] = cls._get_generated_config(
                    sky_autogen_comment, host_name, worker_ips[idx], username,
                    key_path)

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
                    sky_autogen_comment, host_name, worker_ips[idx], username,
                    key_path)

        # This checks if all codegens have been created.
        for idx, ip in enumerate(worker_ips):
            if not codegens[idx]:
                codegens[idx] = cls._get_generated_config(
                    sky_autogen_comment, worker_names[idx], ip, username,
                    key_path)

        for idx in range(len(worker_ips)):
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
        if os.path.exists(extra_config_path):
            os.remove(extra_config_path)

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


# TODO: too many things happening here - leaky abstraction. Refactor.
@timeline.event
def write_cluster_config(to_provision: 'resources.Resources',
                         num_nodes: int,
                         cluster_config_template: str,
                         cluster_name: str,
                         local_wheel_path: pathlib.Path,
                         region: Optional[clouds.Region] = None,
                         zones: Optional[List[clouds.Zone]] = None,
                         dryrun: bool = False) -> Dict[str, str]:
    """Fills in cluster configuration templates and writes them out.

    Returns: {provisioner: path to yaml, the provisioning spec}.
      'provisioner' can be
        - 'ray'
        - 'tpu-create-script' (if TPU is requested)
        - 'tpu-delete-script' (if TPU is requested)
    """
    # task.best_resources may not be equal to to_provision if the user
    # is running a job with less resources than the cluster has.
    cloud = to_provision.cloud
    resources_vars = cloud.make_deploy_resources_variables(to_provision)
    config_dict = {}

    if region is None:
        assert zones is None, 'Set either both or neither for: region, zones.'
        region = cloud.get_default_region()
        zones = region.zones
    else:
        assert isinstance(
            cloud, clouds.Azure
        ) or zones is not None, 'Set either both or neither for: region, zones.'
    region = region.name
    if isinstance(cloud, clouds.AWS):
        # Only AWS supports multiple zones in the 'availability_zone' field.
        zones = [zone.name for zone in zones]
    elif isinstance(cloud, clouds.Azure):
        # Azure does not support specific zones.
        zones = []
    else:
        zones = [zones[0].name]

    aws_default_ami = None
    if isinstance(cloud, clouds.AWS):
        instance_type = resources_vars['instance_type']
        aws_default_ami = cloud.get_default_ami(region, instance_type)

    azure_subscription_id = None
    if isinstance(cloud, clouds.Azure):
        azure_subscription_id = cloud.get_project_id(dryrun=dryrun)

    gcp_project_id = None
    if isinstance(cloud, clouds.GCP):
        gcp_project_id = cloud.get_project_id(dryrun=dryrun)

    assert cluster_name is not None

    credentials = sky_check.get_cloud_credential_file_mounts()
    yaml_path = fill_template(
        cluster_config_template,
        dict(
            resources_vars,
            **{
                'cluster_name': cluster_name,
                'num_nodes': num_nodes,
                'disk_size': to_provision.disk_size,
                # Region/zones.
                'region': region,
                'zones': ','.join(zones),
                # AWS only.
                'aws_default_ami': aws_default_ami,
                # Temporary measure, as deleting per-cluster SGs is too slow.
                # See https://github.com/sky-proj/sky/pull/742.
                # Generate the name of the security group we're looking for.
                # (username, last 4 chars of hash of hostname): for uniquefying
                # users on shared-account cloud providers. Using uuid.getnode()
                # is incorrect; observed to collide on Macs.
                'security_group': f'sky-sg-{user_and_hostname_hash()}',
                # Azure only.
                'azure_subscription_id': azure_subscription_id,
                'resource_group': f'{cluster_name}-{region}',
                # GCP only.
                'gcp_project_id': gcp_project_id,
                # Ray version.
                'ray_version': SKY_REMOTE_RAY_VERSION,
                # Cloud credentials for cloud storage.
                'credentials': credentials,
                # Sky remote utils.
                'sky_remote_path': SKY_REMOTE_PATH,
                'sky_local_path': str(local_wheel_path),
            }))
    config_dict['cluster_name'] = cluster_name
    config_dict['ray'] = yaml_path
    if dryrun:
        return config_dict
    _add_auth_to_cluster_config(cloud, yaml_path)
    if resources_vars.get('tpu_type') is not None:
        tpu_name = resources_vars.get('tpu_name')
        if tpu_name is None:
            tpu_name = cluster_name

        user_file_dir = os.path.expanduser(f'{SKY_USER_FILE_PATH}/')
        scripts = tuple(
            fill_template(
                template_name,
                dict(
                    resources_vars, **{
                        'zones': ','.join(zones),
                        'tpu_name': tpu_name,
                        'gcp_project_id': gcp_project_id,
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


def _add_auth_to_cluster_config(cloud_type, cluster_config_file):
    """Adds SSH key info to the cluster config.

    This function's output removes comments included in the jinja2 template.
    """
    with open(cluster_config_file, 'r') as f:
        config = yaml.safe_load(f)
    cloud_type = str(cloud_type)
    if cloud_type == 'AWS':
        config = auth.setup_aws_authentication(config)
    elif cloud_type == 'GCP':
        config = auth.setup_gcp_authentication(config)
    elif cloud_type == 'Azure':
        config = auth.setup_azure_authentication(config)
    else:
        raise ValueError('Cloud type not supported, must be [AWS, GCP, Azure]')
    dump_yaml(cluster_config_file, config)


def read_yaml(path):
    with open(path, 'r') as f:
        config = yaml.safe_load(f)
    return config


def dump_yaml(path, config):
    # https://github.com/yaml/pyyaml/issues/127
    class LineBreakDumper(yaml.SafeDumper):

        def write_line_break(self, data=None):
            super().write_line_break(data)
            if len(self.indents) == 1:
                super().write_line_break()

    with open(path, 'w') as f:
        yaml.dump(config,
                  f,
                  Dumper=LineBreakDumper,
                  sort_keys=False,
                  default_flow_style=False)


def get_run_timestamp() -> str:
    return 'sky-' + datetime.datetime.now().strftime('%Y-%m-%d-%H-%M-%S-%f')


@timeline.event
def wait_until_ray_cluster_ready(
        cluster_config_file: str,
        num_nodes: int,
        log_path: str,
        nodes_launching_progress_timeout: Optional[int] = None) -> bool:
    """Returns whether the entire ray cluster is ready."""
    if num_nodes <= 1:
        return

    # Manually fetching head ip instead of using `ray exec` to avoid the bug
    # that `ray exec` fails to connect to the head node after some workers
    # launched especially for Azure.
    try:
        head_ip = query_head_ip_with_retries(
            cluster_config_file, retry_count=WAIT_HEAD_NODE_IP_RETRY_COUNT)
    except RuntimeError as e:
        logger.error(e)
        return False  # failed

    ssh_user, ssh_key = ssh_credential_from_yaml(cluster_config_file)
    last_nodes_so_far = 0
    start = time.time()
    with console.status('[bold cyan]Waiting for workers...') as worker_status:
        while True:
            rc, output, stderr = run_command_on_ip_via_ssh(
                head_ip,
                'ray status',
                ssh_user=ssh_user,
                ssh_private_key=ssh_key,
                log_path=log_path,
                stream_logs=False,
                require_outputs=True)
            handle_returncode(rc, 'ray status',
                              'Failed to run ray status on head node.', stderr)
            logger.debug(output)

            # Workers that are ready
            result = _LAUNCHED_WORKER_PATTERN.findall(output)
            ready_workers = 0
            if result:
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
                    'Timed out when waiting for workers to be provisioned.')
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


def ssh_options_list(ssh_private_key: Optional[str],
                     ssh_control_name: Optional[str],
                     *,
                     timeout=30) -> List[str]:
    """Returns a list of sane options for 'ssh'."""
    # Forked from Ray SSHOptions:
    # https://github.com/ray-project/ray/blob/master/python/ray/autoscaler/_private/command_runner.py
    arg_dict = {
        # Supresses initial fingerprint verification.
        'StrictHostKeyChecking': 'no',
        # SSH IP and fingerprint pairs no longer added to known_hosts.
        # This is to remove a 'REMOTE HOST IDENTIFICATION HAS CHANGED'
        # warning if a new node has the same IP as a previously
        # deleted node, because the fingerprints will not match in
        # that case.
        'UserKnownHostsFile': os.devnull,
        # Try fewer extraneous key pairs.
        'IdentitiesOnly': 'yes',
        # Abort if port forwarding fails (instead of just printing to
        # stderr).
        'ExitOnForwardFailure': 'yes',
        # Quickly kill the connection if network connection breaks (as
        # opposed to hanging/blocking).
        'ServerAliveInterval': 5,
        'ServerAliveCountMax': 3,
        # ConnectTimeout.
        'ConnectTimeout': f'{timeout}s',
        # Agent forwarding for git.
        'ForwardAgent': 'yes',
    }
    if ssh_control_name is not None:
        arg_dict.update({
            # Control path: important optimization as we do multiple ssh in one
            # sky.launch().
            'ControlMaster': 'auto',
            'ControlPath': f'{_ssh_control_path(ssh_control_name)}/%C',
            'ControlPersist': '120s',
        })
    ssh_key_option = [
        '-i',
        ssh_private_key,
    ] if ssh_private_key is not None else []
    return ssh_key_option + [
        x for y in (['-o', f'{k}={v}']
                    for k, v in arg_dict.items()
                    if v is not None) for x in y
    ]


def _ssh_control_path(ssh_control_filename: Optional[str]) -> Optional[str]:
    """Returns a temporary path to be used as the ssh control path."""
    if ssh_control_filename is None:
        return None
    username = getpass.getuser()
    path = (f'/tmp/sky_ssh_{username}/{ssh_control_filename}')
    os.makedirs(path, exist_ok=True)
    return path


def ssh_credential_from_yaml(cluster_yaml: str) -> Tuple[str, str]:
    """Returns ssh_user and ssh_private_key."""
    config = read_yaml(cluster_yaml)
    auth_section = config['auth']
    ssh_user = auth_section['ssh_user'].strip()
    ssh_private_key = auth_section.get('ssh_private_key')
    return ssh_user, ssh_private_key


class SshMode(enum.Enum):
    """Enum for SSH mode."""
    # Do not allocating pseudo-tty to avoid user input corrupting the output.
    NON_INTERACTIVE = 0
    # Allocate a pseudo-tty, quit the ssh session after the cmd finishes.
    # Be careful of this mode, as ctrl-c will be passed to the remote process.
    INTERACTIVE = 1
    # Allocate a pseudo-tty and log into the ssh session.
    LOGIN = 2


def _ssh_base_command(ip: str, ssh_private_key: str, ssh_user: str, *,
                      ssh_mode: SshMode, port_forward: Optional[List[int]],
                      ssh_control_name: Optional[str]) -> List[str]:
    ssh = ['ssh']
    if ssh_mode == SshMode.NON_INTERACTIVE:
        # Disable pseudo-terminal allocation. Otherwise, the output of
        # ssh will be corrupted by the user's input.
        ssh += ['-T']
    else:
        # Force pseudo-terminal allocation for interactive/login mode.
        ssh += ['-tt']
    if port_forward is not None:
        for port in port_forward:
            local = remote = port
            logger.info(
                f'Forwarding port {local} to port {remote} on localhost.')
            ssh += ['-L', f'{remote}:localhost:{local}']
    return ssh + ssh_options_list(ssh_private_key,
                                  ssh_control_name) + [f'{ssh_user}@{ip}']


def run_command_on_ip_via_ssh(
        ip: str,
        cmd: Union[str, List[str]],
        *,
        ssh_user: str,
        ssh_private_key: str,
        port_forward: Optional[List[int]] = None,
        # Advanced options.
        require_outputs: bool = False,
        log_path: str = '/dev/null',
        # If False, do not redirect stdout/stderr to optimize performance.
        process_stream: bool = True,
        stream_logs: bool = True,
        ssh_mode: SshMode = SshMode.NON_INTERACTIVE,
        ssh_control_name: Optional[str] = None,
        **kwargs) -> Union[int, Tuple[int, str, str]]:
    """Uses 'ssh' to run 'cmd' on a node with ip.

    Args:
        ip: The IP address of the node.
        cmd: The command to run.
        ssh_private_key: The path to the private key to use for ssh.
        ssh_user: The user to use for ssh.
        port_forward: A list of ports to forward from the localhost to the
        remote host.

        Advanced options:

        require_outputs: Whether to return the stdout/stderr of the command.
        log_path: Redirect stdout/stderr to the log_path.
        stream_logs: Stream logs to the stdout/stderr.
        check: Check the success of the command.
        ssh_mode: The mode to use for ssh.
            See SSHMode for more details.
        ssh_control_name: The files name of the ssh_control to use. This is used
            for optimizing the ssh speed.

    Returns:
        returncode
        or
        A tuple of (returncode, stdout, stderr).
    """
    base_ssh_command = _ssh_base_command(ip,
                                         ssh_private_key,
                                         ssh_user=ssh_user,
                                         ssh_mode=ssh_mode,
                                         port_forward=port_forward,
                                         ssh_control_name=ssh_control_name)
    if ssh_mode == SshMode.LOGIN:
        assert isinstance(cmd, list), 'cmd must be a list for login mode.'
        command = base_ssh_command + cmd
        proc = run(command, shell=False, check=False)
        return proc.returncode, '', ''
    if isinstance(cmd, list):
        cmd = ' '.join(cmd)

    log_dir = os.path.expanduser(os.path.dirname(log_path))
    os.makedirs(log_dir, exist_ok=True)
    # We need this to correctly run the cmd, and get the output.
    command = [
        'bash',
        '--login',
        '-c',
        # Need this `-i` option to make sure `source ~/.bashrc` work.
        '-i',
    ]

    command += [
        shlex.quote(f'true && source ~/.bashrc && export OMP_NUM_THREADS=1 '
                    f'PYTHONWARNINGS=ignore && ({cmd})'),
        '2>&1',
    ]
    if not process_stream and ssh_mode == SshMode.NON_INTERACTIVE:
        command += [
            # A hack to remove the following bash warnings (twice):
            #  bash: cannot set terminal process group
            #  bash: no job control in this shell
            '| stdbuf -o0 tail -n +5',
            # This is required to make sure the executor of the command can get the
            # correct returncode, since linux pipe is used.
            '; exit ${PIPESTATUS[0]}'
        ]

    command = ' '.join(command)
    command = base_ssh_command + [shlex.quote(command)]

    executable = None
    if not process_stream:
        if stream_logs:
            command += [
                f'| tee {log_path}',
                # This also requires the executor to be '/bin/bash' instead
                # of the default '/bin/sh'.
                '; exit ${PIPESTATUS[0]}'
            ]
        else:
            command += [f'> {log_path}']
        executable = '/bin/bash'

    return log_lib.run_with_log(' '.join(command),
                                log_path,
                                stream_logs,
                                process_stream=process_stream,
                                require_outputs=require_outputs,
                                shell=True,
                                executable=executable,
                                **kwargs)


def handle_returncode(returncode: int,
                      command: str,
                      error_msg: str,
                      stderr: Optional[str] = None,
                      raise_error: bool = False,
                      stream_logs: bool = True) -> None:
    """Handle the returncode of a command.

    Args:
        returncode: The returncode of the command.
        command: The command that was run.
        error_msg: The error message to print.
        stderr: The stderr of the command.
        raise_error: Whether to raise an error instead of sys.exit.
    """
    echo = logger.error if stream_logs else lambda _: None
    if returncode != 0:
        if stderr is not None:
            echo(stderr)
        format_err_msg = (
            f'{colorama.Fore.RED}{error_msg}{colorama.Style.RESET_ALL}')
        if raise_error:
            raise exceptions.CommandError(returncode, command, format_err_msg)
        echo(f'Command failed with code {returncode}: {command}')
        echo(format_err_msg)
        sys.exit(returncode)


def run_in_parallel(func: Callable, args: List[Any]) -> List[Any]:
    """Run a function in parallel on a list of arguments.

    The function should raise a CommandError if the command fails.
    Returns a list of the return values of the function func, in the same order
    as the arguments.
    """
    # Reference: https://stackoverflow.com/questions/25790279/python-multiprocessing-early-termination # pylint: disable=line-too-long
    with pool.ThreadPool() as p:
        try:
            # Run the function in parallel on the arguments, keeping the order.
            return list(p.imap(func, args))
        except exceptions.CommandError as e:
            # Print the error message here, to avoid the other processes'
            # error messages mixed with the current one.
            logger.error(
                f'Command failed with code {e.returncode}: {e.command}')
            logger.error(e.error_msg)
            sys.exit(e.returncode)
        except KeyboardInterrupt:
            print()
            logger.error(
                f'{colorama.Fore.RED}Interrupted by user.{colorama.Style.RESET_ALL}'
            )
            sys.exit(1)


@timeline.event
def run(cmd, **kwargs):
    # Should be careful to use this function, as the child process cmd spawn may
    # keep running in the background after the current program is killed. To get
    # rid of this problem, use `log_lib.run_with_log`.
    shell = kwargs.pop('shell', True)
    check = kwargs.pop('check', True)
    executable = kwargs.pop('executable', '/bin/bash')
    if not shell:
        executable = None
    return subprocess.run(cmd,
                          shell=shell,
                          check=check,
                          executable=executable,
                          **kwargs)


def run_no_outputs(cmd, **kwargs):
    return run(cmd,
               stdout=subprocess.DEVNULL,
               stderr=subprocess.DEVNULL,
               **kwargs)


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


def user_and_hostname_hash() -> str:
    """Returns a string containing <user>-<hostname hash last 4 chars>.

    For uniquefying user clusters on shared-account cloud providers. Also used
    for AWS security group.

    Using uuid.getnode() instead of gethostname() is incorrect; observed to
    collide on Macs.

    NOTE: BACKWARD INCOMPATIBILITY NOTES

    Changing this string will render AWS clusters shown in `sky status`
    unreusable and potentially cause leakage:

    - If a cluster is STOPPED, any command restarting it (`sky launch`, `sky
      start`) will launch a NEW cluster.
    - If a cluster is UP, a `sky launch` command reusing it will launch a NEW
      cluster. The original cluster will be stopped and thus leaked from Sky's
      perspective.
    - `sky down/stop/exec` on these pre-change clusters still works, if no new
      clusters with the same name have been launched.

    The reason is AWS security group names are derived from this string, and
    thus changing the SG name makes these clusters unrecognizable.
    """
    hostname_hash = hashlib.md5(socket.gethostname().encode()).hexdigest()[-4:]
    return f'{getpass.getuser()}-{hostname_hash}'


def generate_cluster_name():
    # TODO: change this ID formatting to something more pleasant.
    # User name is helpful in non-isolated accounts, e.g., GCP, Azure.
    return f'sky-{uuid.uuid4().hex[:4]}-{getpass.getuser()}'


@timeline.event
def get_node_ips(
        cluster_yaml: str,
        expected_num_nodes: int,
        return_private_ips: bool = False,
        handle: Optional[backends.Backend.ResourceHandle] = None) -> List[str]:
    """Returns the IPs of all nodes in the cluster."""
    yaml_handle = cluster_yaml
    if return_private_ips:
        config = read_yaml(yaml_handle)
        # Add this field to a temp file to get private ips.
        config['provider']['use_internal_ips'] = True
        yaml_handle = cluster_yaml + '.tmp'
        dump_yaml(yaml_handle, config)

    # Try optimize for the common case where we have 1 node.
    if (not return_private_ips and expected_num_nodes == 1 and
            handle is not None and handle.head_ip is not None):
        return [handle.head_ip]

    # Check the network connection first to avoid long hanging time for
    # ray get-head-ip below, if a long-lasting network connection failure
    # happens.
    check_network_connection()
    try:
        proc = run(f'ray get-head-ip {yaml_handle}',
                   stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE)
        out = proc.stdout.decode().strip()
        head_ip = re.findall(IP_ADDR_REGEX, out)
    except subprocess.CalledProcessError as e:
        raise exceptions.FetchIPError(
            exceptions.FetchIPError.Reason.HEAD) from e
    if len(head_ip) != 1:
        raise exceptions.FetchIPError(exceptions.FetchIPError.Reason.HEAD)

    if expected_num_nodes > 1:
        try:
            proc = run(f'ray get-worker-ips {yaml_handle}',
                       stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)
            out = proc.stdout.decode()
            worker_ips = re.findall(IP_ADDR_REGEX, out)
        except subprocess.CalledProcessError as e:
            raise exceptions.FetchIPError(
                exceptions.FetchIPError.Reason.WORKER) from e
        if len(worker_ips) != expected_num_nodes - 1:
            raise exceptions.FetchIPError(exceptions.FetchIPError.Reason.WORKER)
    else:
        worker_ips = []
    if return_private_ips:
        os.remove(yaml_handle)
    return head_ip + worker_ips


@timeline.event
def get_head_ip(
    handle: backends.Backend.ResourceHandle,
    use_cached_head_ip: bool = True,
    retry_count: int = 1,
) -> str:
    """Returns the ip of the head node."""
    assert not use_cached_head_ip or retry_count == 1, (
        'Cannot use cached_head_ip when retry_count is not 1')
    if use_cached_head_ip:
        if handle.head_ip is None:
            # This happens for INIT clusters (e.g., exit 1 in setup).
            raise ValueError(
                'Cluster\'s head IP not found; is it up? To fix: '
                'run a successful launch first (`sky launch`) to ensure'
                ' the cluster status is UP (`sky status`).')
        head_ip = handle.head_ip
    else:
        head_ip = query_head_ip_with_retries(handle.cluster_yaml, retry_count)
    return head_ip


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
    if (cloud == str(sky.Azure()) and returncode == 2 and
            'argument --ids: expected at least one argument' in stderr):
        # Azure CLI has a returncode 2 when the cluster is not found, as
        # --ids <empty> is passed to the query command. In that case, the
        # cluster should be considered as DOWN.
        return []

    if returncode != 0:
        raise exceptions.ClusterStatusFetchingError(
            f'Failed to query {cloud} cluster {cluster!r} status: {stdout + stderr}'
        )

    cluster_status = stdout.strip()
    if cluster_status == '':
        return []
    return [
        status_map[s]
        for s in cluster_status.split(deliminiator)
        if status_map[s] is not None
    ]


@contextlib.contextmanager
def subpress_output():
    """Suppress stdout and stderr."""
    with open(os.devnull, 'w') as devnull:
        with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(
                devnull):
            yield


def _ray_launch_hash(cluster_name: str, ray_config: Dict[str, Any]) -> Set[str]:
    """Returns a set of Ray launch config hashes, one per node type."""
    # Use the cached Ray launch hashes if they exist.
    metadata = global_user_state.get_cluster_metadata(cluster_name)
    assert metadata is not None, cluster_name
    ray_launch_hashes = metadata.get('ray_launch_hashes', None)
    if ray_launch_hashes is not None:
        logger.debug('Using cached launch_caches')
        return set(ray_launch_hashes)
    with subpress_output():
        ray_config = ray_commands._bootstrap_config(ray_config)  # pylint: disable=protected-access
    # Adopted from https://github.com/ray-project/ray/blob/ray-1.10.0/python/ray/autoscaler/_private/node_launcher.py#L46-L54
    # TODO(zhwu): this logic is duplicated from the ray code above (keep in sync).
    launch_hashes = set()
    head_node_type = ray_config['head_node_type']
    for node_type, node_config in ray_config['available_node_types'].items():
        if node_type == head_node_type:
            launch_config = ray_config.get('head_node', {})
        else:
            launch_config = ray_config.get('worker_nodes', {})
        launch_config = copy.deepcopy(launch_config)

        launch_config.update(node_config['node_config'])
        with subpress_output():
            current_hash = ray_util.hash_launch_conf(launch_config,
                                                     ray_config['auth'])
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
    hash_filter_str = ','.join(launch_hashes)
    query_cmd = ('aws ec2 describe-instances --filters '
                 f'Name=tag:ray-cluster-name,Values={cluster} '
                 f'Name=tag:ray-launch-config,Values={hash_filter_str} '
                 f'--region {region} '
                 '--query "Reservations[].Instances[].State.Name" '
                 '--output text')
    return _process_cli_query('AWS', cluster, query_cmd, '\t', status_map)


def _query_status_gcp(
    cluster: str,
    ray_config: Dict[str, Any],
) -> List[global_user_state.ClusterStatus]:
    status_map = {
        'PROVISIONING': global_user_state.ClusterStatus.INIT,
        'STARTING': global_user_state.ClusterStatus.INIT,
        'RUNNING': global_user_state.ClusterStatus.UP,
        'REPAIRING': global_user_state.ClusterStatus.STOPPED,
        # 'TERMINATED' in GCP means stopped, with disk preserved.
        'STOPPING': global_user_state.ClusterStatus.STOPPED,
        'TERMINATED': global_user_state.ClusterStatus.STOPPED,
        # 'SUSPENDED' in GCP means stopped, with disk and OS memory preserved.
        'SUSPENDING': global_user_state.ClusterStatus.STOPPED,
        'SUSPENDED': global_user_state.ClusterStatus.STOPPED,
    }
    launch_hashes = _ray_launch_hash(cluster, ray_config)
    hash_filter_str = ' '.join(launch_hashes)
    # TODO(zhwu): The status of the TPU attached to the cluster should also be
    # checked, since TPUs are not part of the VMs.
    query_cmd = ('gcloud compute instances list '
                 f'--filter="(labels.ray-cluster-name={cluster} AND '
                 f'labels.ray-launch-config=({hash_filter_str}))" '
                 '--format="value(status)"')
    return _process_cli_query('GCP', cluster, query_cmd, '\n', status_map)


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


_QUERY_STATUS_FUNCS = {
    'AWS': _query_status_aws,
    'GCP': _query_status_gcp,
    'Azure': _query_status_azure,
}


def _get_cluster_status_via_cloud_cli(
    handle: 'backends.Backend.ResourceHandle'
) -> List[global_user_state.ClusterStatus]:
    """Returns the status of the cluster."""
    resources: sky.Resources = handle.launched_resources
    cloud = resources.cloud
    ray_config = read_yaml(handle.cluster_yaml)
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
        ips = get_node_ips(handle.cluster_yaml, handle.launched_nodes)
        if handle.launched_nodes == 1:
            # Check the ray cluster status. We have to check it for single node
            # case, since the get_node_ips() does not require ray cluster to be
            # running.
            ssh_user, ssh_key = ssh_credential_from_yaml(handle.cluster_yaml)
            returncode = run_command_on_ip_via_ssh(ips[0],
                                                   'ray status',
                                                   ssh_user=ssh_user,
                                                   ssh_private_key=ssh_key,
                                                   stream_logs=False)
            if returncode:
                raise exceptions.FetchIPError(
                    reason=exceptions.FetchIPError.Reason.HEAD)
        # If we get node ips correctly, the cluster is UP. It is safe to
        # set the status to UP, as the `get_node_ips` function uses ray
        # to fetch IPs and starting ray is the final step of sky launch.
        record['status'] = global_user_state.ClusterStatus.UP
        handle.head_ip = ips[0]
        global_user_state.add_or_update_cluster(cluster_name,
                                                handle,
                                                ready=True,
                                                is_launch=False)
        return record
    except exceptions.FetchIPError:
        logger.debug('Refreshing status: Failed to get IPs from cluster '
                     f'{cluster_name!r}, trying to fetch from provider.')

    # For all code below, ray fails to get IPs for the cluster.
    node_statuses = _get_cluster_status_via_cloud_cli(handle)

    # If the node_statuses is empty, all the nodes are terminated. We can
    # safely set the cluster status to TERMINATED. This handles the edge case
    # where the cluster is terminated by the user manually through the UI.
    to_terminate = not node_statuses

    # A cluster is considered "abnormal", if not all nodes are TERMINATED or not all
    # nodes are STOPPED. We check that with the following logic:
    #   * not all nodes are terminated and there's at least one node terminated; or
    #   * any of the non-TERMINATED nodes is in a non-STOPPED status.
    #
    # This includes these special cases:
    # All stopped are considered normal and will be cleaned up at the end of the function.
    # Some of the nodes UP should be considered abnormal, because the ray cluster is
    # probably down.
    # The cluster is partially terminated or stopped should be considered abnormal.
    #
    # An abnormal cluster will transition to INIT and have any autostop setting reset.
    is_abnormal = ((0 < len(node_statuses) < handle.launched_nodes) or
                   any(status != global_user_state.ClusterStatus.STOPPED
                       for status in node_statuses))
    if is_abnormal:
        # Reset the autostop to avoid false information with best effort.
        # Side effect: if the status is refreshed during autostopping, the
        # autostop field in the local cache will be reset, even though the
        # cluster will still be correctly stopped.
        try:
            backend = backends.CloudVmRayBackend()
            backend.set_autostop(handle, -1, stream_logs=False)
        except (Exception, SystemExit):  # pylint: disable=broad-except
            logger.debug('Failed to reset autostop.')
        global_user_state.set_cluster_autostop_value(handle.cluster_name, -1)

        # If the user starts part of a STOPPED cluster, we still need a status to
        # represent the abnormal status. For spot cluster, it can also represent
        # that the cluster is partially preempted.
        # TODO(zhwu): the definition of INIT should be audited/changed.
        # Adding a new status UNHEALTHY for abnormal status can be a choice.
        global_user_state.set_cluster_status(
            cluster_name, global_user_state.ClusterStatus.INIT)
        return global_user_state.get_cluster_from_name(cluster_name)
    # Now is_abnormal is False: either node_statuses is empty or all nodes are STOPPED.
    backend = backends.CloudVmRayBackend()
    # TODO(zhwu): adding output for the cluster removed by status refresh.
    backend.post_teardown_cleanup(handle, terminate=to_terminate, purge=False)
    return global_user_state.get_cluster_from_name(cluster_name)


def _update_cluster_status(
        cluster_name: str,
        acquire_per_cluster_status_lock: bool) -> Optional[Dict[str, Any]]:
    """Update the cluster status by checking ray cluster and real status from cloud.

    The function will update the cached cluster status in the global state. For the
    design of the cluster status and transition, please refer to the
    sky/design_docs/cluster_states.md

    Returns:
      If the cluster is terminated or does not exist, return None.
      Otherwise returns the input record with status and ip potentially updated.
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
        logger.debug(
            f'Refreshing status: Failed get the lock for cluster {cluster_name!r}.'
            ' Using the cached status.')
        return global_user_state.get_cluster_from_name(cluster_name)


@timeline.event
def refresh_cluster_status_handle(
    cluster_name: str,
    *,
    force_refresh: bool = False,
    acquire_per_cluster_status_lock: bool = True,
) -> Tuple[Optional[global_user_state.ClusterStatus],
           Optional[backends.Backend.ResourceHandle]]:
    record = global_user_state.get_cluster_from_name(cluster_name)
    if record is None:
        return None, None

    handle = record['handle']
    if isinstance(handle, backends.CloudVmRayBackend.ResourceHandle):
        if force_refresh or record['autostop'] >= 0:
            # Refresh the status only when force_refresh is True or the cluster
            # has autostopped turned on.
            record = _update_cluster_status(
                cluster_name,
                acquire_per_cluster_status_lock=acquire_per_cluster_status_lock)
            if record is None:
                return None, None
    return record['status'], handle


def get_clusters(include_reserved: bool, refresh: bool) -> List[Dict[str, Any]]:
    """Returns a list of cached cluster records.

    Args:
        include_reserved: Whether to include sky-reserved clusters, e.g. spot
            controller.
        refresh: Whether to refresh the status of the clusters. (Refreshing will
            set the status to STOPPED if the cluster cannot be pinged.)

    Returns:
        A list of cluster records.
    """
    records = global_user_state.get_clusters()
    if not include_reserved:
        records = [
            record for record in records
            if record['name'] not in SKY_RESERVED_CLUSTER_NAMES
        ]
    if not refresh:
        return records

    plural = 's' if len(records) > 1 else ''
    progress = rich_progress.Progress(transient=True,
                                      redirect_stdout=False,
                                      redirect_stderr=False)
    task = progress.add_task(
        f'[bold cyan]Refreshing status for {len(records)} cluster{plural}[/]',
        total=len(records))

    terminated_clusters = []

    def _refresh_cluster(cluster_name):
        record = _update_cluster_status(cluster_name,
                                        acquire_per_cluster_status_lock=True)
        if record is None:
            terminated_clusters.append(cluster_name)
        progress.update(task, advance=1)
        return record

    cluster_names = [record['name'] for record in records]
    with progress:
        updated_records = run_in_parallel(_refresh_cluster, cluster_names)
    if terminated_clusters:
        plural = 's were' if len(terminated_clusters) > 1 else ' was'
        cluster_str = ', '.join(repr(name) for name in terminated_clusters)
        logger.warning(f'The following cluster{plural} terminated on the cloud '
                       f'and removed from Sky\'s cluster table:\n{cluster_str}')
    updated_records = [
        record for record in updated_records if record is not None
    ]
    return updated_records


def query_head_ip_with_retries(cluster_yaml: str, retry_count: int = 1) -> str:
    """Returns the ip of the head node from yaml file."""
    for i in range(retry_count):
        try:
            out = run(f'ray get-head-ip {cluster_yaml}',
                      stdout=subprocess.PIPE).stdout.decode().strip()
            head_ip = re.findall(IP_ADDR_REGEX, out)
            assert 1 == len(head_ip), out
            head_ip = head_ip[0]
            break
        except subprocess.CalledProcessError as e:
            if i == retry_count - 1:
                raise RuntimeError('Failed to get head ip') from e
            # Retry if the cluster is not up yet.
            logger.debug('Retrying to get head ip.')
            time.sleep(5)
    return head_ip


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


def check_cluster_name_is_valid(cluster_name: str) -> None:
    """Errors out on invalid cluster names not supported by cloud providers.

    Bans (including but not limited to) names that:
    - are digits-only
    - contain underscore (_)
    """
    if cluster_name is None:
        return
    # GCP errors return this exact regex.  An informal description is also at:
    # https://cloud.google.com/compute/docs/naming-resources#resource-name-format
    valid_regex = '[a-z]([-a-z0-9]{0,61}[a-z0-9])?'
    if re.fullmatch(valid_regex, cluster_name) is None:
        raise ValueError(f'Cluster name "{cluster_name}" is invalid; '
                         f'ensure it is fully matched by regex: {valid_regex}')
    if len(cluster_name) > _MAX_CLUSTER_NAME_LEN:
        raise ValueError(
            f'Cluster name {cluster_name!r} has {len(cluster_name)}'
            f' chars; maximum length is {_MAX_CLUSTER_NAME_LEN} chars.')


def check_cluster_name_not_reserved(
        cluster_name: Optional[str],
        operation_str: Optional[str] = None) -> None:
    """Errors out if cluster name is reserved by sky.

    If the cluster name is reserved, return the error message. Otherwise,
    return None.
    """
    usage = 'internal use'
    if cluster_name == spot_lib.SPOT_CONTROLLER_NAME:
        usage = 'spot controller'
    msg = f'Cluster {cluster_name!r} is reserved for {usage}.'
    if operation_str is not None:
        msg += f' {operation_str} is not allowed.'
    if cluster_name in SKY_RESERVED_CLUSTER_NAMES:
        raise ValueError(msg)


def kill_children_processes():
    # We need to kill the children, so that the underlying subprocess
    # will not print the logs to the terminal, after this program
    # exits.
    parent_process = psutil.Process()
    for child in parent_process.children(recursive=True):
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            # The child process may have already been terminated.
            pass


# Handle ctrl-c
def interrupt_handler(signum, frame):
    del signum, frame
    logger.warning(f'{colorama.Fore.LIGHTBLACK_EX}The job will keep '
                   f'running after Ctrl-C.{colorama.Style.RESET_ALL}')
    kill_children_processes()
    sys.exit(exceptions.KEYBOARD_INTERRUPT_CODE)


# Handle ctrl-z
def stop_handler(signum, frame):
    del signum, frame
    logger.warning(f'{colorama.Fore.LIGHTBLACK_EX}The job will keep '
                   f'running after Ctrl-Z.{colorama.Style.RESET_ALL}')
    kill_children_processes()
    sys.exit(exceptions.SIGTSTP_CODE)


class Backoff:
    """Exponential backoff with jittering."""
    MULTIPLIER = 1.6
    JITTER = 0.4

    def __init__(self, initial_backoff: int = 5, max_backoff_factor: int = 5):
        self._initial = True
        self._backoff = None
        self._inital_backoff = initial_backoff
        self._max_backoff = max_backoff_factor * self._inital_backoff

    # https://github.com/grpc/grpc/blob/2d4f3c56001cd1e1f85734b2f7c5ce5f2797c38a/doc/connection-backoff.md
    # https://github.com/grpc/grpc/blob/5fc3ff82032d0ebc4bf252a170ebe66aacf9ed9d/src/core/lib/backoff/backoff.cc

    def current_backoff(self) -> float:
        """Backs off once and returns the current backoff in seconds."""
        if self._initial:
            self._initial = False
            self._backoff = min(self._inital_backoff, self._max_backoff)
        else:
            self._backoff = min(self._backoff * self.MULTIPLIER,
                                self._max_backoff)
        self._backoff += random.uniform(-self.JITTER * self._backoff,
                                        self.JITTER * self._backoff)
        return self._backoff


def check_fields(provided_fields, known_fields):
    known_fields = set(known_fields)
    unknown_fields = []
    for field in provided_fields:
        if field not in known_fields:
            unknown_fields.append(field)

    if len(unknown_fields) > 0:
        invalid_keys = 'The following fields are invalid:\n'
        for unknown_key in unknown_fields:
            similar_keys = difflib.get_close_matches(unknown_key, known_fields)
            key_invalid = f'    Unknown field \'{unknown_key}\'.'
            if len(similar_keys) == 1:
                key_invalid += f' Did you mean \'{similar_keys[0]}\'?'
            if len(similar_keys) > 1:
                key_invalid += f' Did you mean one of {similar_keys}?'
            key_invalid += '\n'
            invalid_keys += key_invalid
        with print_exception_no_traceback():
            raise ValueError(invalid_keys)


@contextlib.contextmanager
def print_exception_no_traceback():
    """A context manager that prints out an exception without traceback.

    Mainly for UX: user-facing errors, e.g., ValueError, should suppress long
    tracebacks.

    Example usage:

        with print_exception_no_traceback():
            if error():
                raise ValueError('...')
    """
    sys.tracebacklimit = 0
    yield
    sys.tracebacklimit = 1000
