"""Util constants/functions for the backends."""
import colorama
import datetime
import enum
import hashlib
import http.client as httplib
import getpass
from multiprocessing import pool
import os
import pathlib
import psutil
import re
import shlex
import socket
import subprocess
import sys
import textwrap
import threading
import time
import typing
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
import uuid
import yaml

import jinja2
import rich.console as rich_console
import rich.progress as rich_progress

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
_TEST_IP = '1.1.1.1'

# GCP has a 63 char limit; however, Ray autoscaler adds many
# characters. Through testing, 37 chars is the maximum length for the Sky
# cluster name on GCP.  Ref:
# https://cloud.google.com/compute/docs/naming-resources#resource-name-format
_MAX_CLUSTER_NAME_LEN = 37

# Allow each CPU thread take 2 tasks.
# Note: This value cannot be too small, otherwise OOM issue may occur.
DEFAULT_TASK_CPU_DEMAND = 0.5

SKY_RESERVED_CLUSTER_NAMES = [spot_lib.SPOT_CONTROLLER_NAME]


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
                      raise_error: bool = False) -> None:
    """Handle the returncode of a command.

    Args:
        returncode: The returncode of the command.
        command: The command that was run.
        error_msg: The error message to print.
        stderr: The stderr of the command.
        raise_error: Whether to raise an error instead of sys.exit.
    """
    if returncode != 0:
        if stderr is not None:
            logger.error(stderr)
        format_err_msg = (
            f'{colorama.Fore.RED}{error_msg}{colorama.Style.RESET_ALL}')
        if raise_error:
            raise exceptions.CommandError(returncode, command, format_err_msg)
        logger.error(f'Command failed with code {returncode}: {command}')
        logger.error(format_err_msg)
        sys.exit(returncode)


def run_in_parallel(func: Callable, args: List[Any]):
    """Run a function in parallel on a list of arguments.

    The function should raise a CommandError if the command fails.
    """
    # Reference: https://stackoverflow.com/questions/25790279/python-multiprocessing-early-termination # pylint: disable=line-too-long
    with pool.ThreadPool() as p:
        try:
            list(p.imap_unordered(func, args))
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
    # A timeout of 1s seems to infrequently encounter 'socket.timeout'.
    conn = httplib.HTTPSConnection(_TEST_IP, timeout=3)
    try:
        conn.request('HEAD', '/')
    except OSError as e:
        raise exceptions.NetworkError(
            'Could not refresh the cluster. Network seems down.') from e


def _ping_cluster_and_set_status(
        record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Pings a cluster and potentially sets it to stopped.

    Setting it to STOPPED means (1) setting the clusters table, (2) removing it
    from the SSH config.

    Returns:
      If the cluster yaml is found to be concurrently removed, returns None.
      Otherwise returns the input record with status potentially updated.
    """
    handle = record['handle']
    cluster_name = handle.cluster_name

    check_network_connection()

    try:
        ips = get_node_ips(handle.cluster_yaml, handle.launched_nodes)
        # If we get node ips correctly, the cluster is UP. It is safe to
        # set the status to UP, as the `get_node_ips` function uses ray
        # to fetch IPs and starting ray is the final step of sky launch.
        record['status'] = global_user_state.ClusterStatus.UP
        handle.head_ip = ips[0]
        return record
    except exceptions.FetchIPError:
        logger.debug('Refreshing status: Failed to get IPs from cluster '
                     f'{cluster_name!r}, set to STOPPED')
    if record['status'] == global_user_state.ClusterStatus.INIT:
        # Should not set the cluster to STOPPED if INIT, since it may be
        # still launching.
        return record
    # Set the cluster status to STOPPED. It is safe to do so, even if the
    # cluster is still partially UP:
    # 1. Autostop case: the whole cluster will be properly stopped soon.
    # 2. Managed spot case: We will soon relaunch the cluster by the strategy
    #    on the same region or terminate the cluster.
    try:
        config = read_yaml(handle.cluster_yaml)
    except FileNotFoundError:
        # This happens e.g., during smoke tests. A test calls `sky status
        # --refresh`, processes another cluster (running this current func),
        # while a concurrent test has torn down itself.
        #
        # We know we can't ping the IP and the cluster yaml has been
        # removed. With high likelihood the cluster has been removed.
        return None
    auth_config = config['auth']
    global_user_state.remove_cluster(cluster_name, terminate=False)
    SSHConfigHelper.remove_cluster(cluster_name, handle.head_ip, auth_config)
    return global_user_state.get_cluster_from_name(cluster_name)


def refresh_cluster_status_handle(
    cluster_name: str,
    force_refresh: bool = False
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
            record = _ping_cluster_and_set_status(record)
            if record is None:
                return None, None
    return record['status'], handle


def get_clusters(refresh: bool) -> List[Dict[str, Any]]:
    records = global_user_state.get_clusters()
    if not refresh:
        return records
    updated_records = []
    for record in rich_progress.track(records,
                                      description='Refreshing cluster status'):
        record = _ping_cluster_and_set_status(record)
        if record is not None:
            updated_records.append(record)
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
