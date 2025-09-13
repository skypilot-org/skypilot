"""Utility functions for cluster yaml file."""

import functools
import glob
import os
import re
import textwrap
from typing import Dict, List, Optional
import uuid

from sky.skylet import constants
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import timeline

# The cluster yaml used to create the current cluster where the module is
# called.
SKY_CLUSTER_YAML_REMOTE_PATH = '~/.sky/sky_ray.yml'


def get_provider_name(config: dict) -> str:
    """Return the name of the provider."""

    provider_module = config['provider']['module']
    # Examples:
    #   'sky.skylet.providers.aws.AWSNodeProviderV2' -> 'aws'
    #   'sky.provision.aws' -> 'aws'
    provider_search = re.search(r'(?:providers|provision)\.(\w+)\.?',
                                provider_module)
    assert provider_search is not None, config
    provider_name = provider_search.group(1).lower()
    # Special handling for lambda_cloud as Lambda cloud is registered as lambda.
    if provider_name == 'lambda_cloud':
        provider_name = 'lambda'
    return provider_name


class SSHConfigHelper(object):
    """Helper for handling local SSH configuration."""

    ssh_conf_path = '~/.ssh/config'
    ssh_conf_lock_path = os.path.expanduser('~/.sky/locks/.ssh_config.lock')
    ssh_conf_per_cluster_lock_path = os.path.expanduser(
        '~/.sky/locks/.ssh_config_{}.lock')
    ssh_cluster_path = constants.SKY_USER_FILE_PATH + '/ssh/{}'
    ssh_cluster_key_path = constants.SKY_USER_FILE_PATH + '/ssh-keys/{}.key'

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
        # Not adding SSH agent forwarding by default here to avoid implicitly
        # using users' SSH keys in their local agent. Plus on sky launch side we
        # are not default adding SSH agent forwarding either.
        codegen = textwrap.dedent(f"""\
            {autogen_comment}
            Host {host_name}
              HostName {ip}
              User {username}
              IdentityFile {ssh_key_path}
              IdentitiesOnly yes
              StrictHostKeyChecking no
              UserKnownHostsFile=/dev/null
              GlobalKnownHostsFile=/dev/null
              Port {port}
              {proxy}
            """.rstrip())
        codegen = codegen + '\n'
        return codegen

    @classmethod
    def generate_local_key_file(cls, cluster_name: str,
                                auth_config: Dict[str, str]) -> str:
        key_content = auth_config.pop('ssh_private_key_content', None)
        if key_content is not None:
            cluster_private_key_path = cls.ssh_cluster_key_path.format(
                cluster_name)
            expanded_cluster_private_key_path = os.path.expanduser(
                cluster_private_key_path)
            expanded_cluster_private_key_dir = os.path.dirname(
                expanded_cluster_private_key_path)
            os.makedirs(expanded_cluster_private_key_dir,
                        exist_ok=True,
                        mode=0o700)
            with open(expanded_cluster_private_key_path,
                      'w',
                      encoding='utf-8',
                      opener=functools.partial(os.open, mode=0o600)) as f:
                f.write(key_content)
            auth_config['ssh_private_key'] = cluster_private_key_path
        return auth_config['ssh_private_key']

    @classmethod
    @timeline.FileLockEvent(ssh_conf_lock_path)
    def add_cluster(
        cls,
        cluster_name: str,
        ips: List[str],
        auth_config: Dict[str, str],
        ports: List[int],
        docker_user: Optional[str] = None,
        ssh_user: Optional[str] = None,
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
            auth_config: `auth` in cluster yaml.
            ports: List of port numbers for SSH corresponding to ips
            docker_user: If not None, use this user to ssh into the docker
            ssh_user: Override the ssh_user in auth_config
        """
        if ssh_user is None:
            username = auth_config['ssh_user']
        else:
            username = ssh_user
        if docker_user is not None:
            username = docker_user

        key_path = cls.generate_local_key_file(cluster_name, auth_config)
        key_path = os.path.expanduser(key_path)
        sky_autogen_comment = ('# Added by sky (use `sky stop/down '
                               f'{cluster_name}` to remove)')
        ip = ips[0]
        if docker_user is not None:
            ip = 'localhost'

        config_path = os.path.expanduser(cls.ssh_conf_path)
        os.makedirs(os.path.dirname(config_path), exist_ok=True, mode=0o700)

        if not os.path.exists(config_path):
            config = ['\n']
            with open(config_path,
                      'w',
                      encoding='utf-8',
                      opener=functools.partial(os.open, mode=0o644)) as f:
                f.writelines(config)

        with open(config_path, 'r', encoding='utf-8') as f:
            config = f.readlines()

        ssh_dir = cls.ssh_cluster_path.format('')
        os.makedirs(os.path.expanduser(ssh_dir), exist_ok=True, mode=0o700)

        # Handle Include on top of Config file
        include_str = f'Include {cls.ssh_cluster_path.format("*")}'
        found = False
        for i, line in enumerate(config):
            config_str = line.strip()
            if config_str == include_str:
                found = True
                break
            if 'Host' in config_str:
                break
        if not found:
            # Did not find Include string. Insert `Include` lines.
            with open(config_path, 'w', encoding='utf-8') as f:
                config.insert(
                    0, '# Added by SkyPilot for ssh config of all clusters\n'
                    f'{include_str}\n')
                f.write(''.join(config).strip())
                f.write('\n' * 2)

        proxy_command = auth_config.get('ssh_proxy_command', None)

        docker_proxy_command_generator = None
        if docker_user is not None:
            docker_proxy_command_generator = lambda ip, port: ' '.join(
                ['ssh'] + command_runner.ssh_options_list(
                    key_path, ssh_control_name=None, port=port) +
                ['-W', '%h:%p', f'{auth_config["ssh_user"]}@{ip}'])

        codegen = ''
        # Add the nodes to the codegen
        for i, ip in enumerate(ips):
            docker_proxy_command = None
            port = ports[i]
            if docker_proxy_command_generator is not None:
                docker_proxy_command = docker_proxy_command_generator(ip, port)
                ip = 'localhost'
                port = constants.DEFAULT_DOCKER_PORT
            node_name = cluster_name if i == 0 else cluster_name + f'-worker{i}'
            # TODO(romilb): Update port number when k8s supports multinode
            codegen += cls._get_generated_config(
                sky_autogen_comment, node_name, ip, username, key_path,
                proxy_command, port, docker_proxy_command) + '\n'

        cluster_config_path = os.path.expanduser(
            cls.ssh_cluster_path.format(cluster_name))

        with open(cluster_config_path,
                  'w',
                  encoding='utf-8',
                  opener=functools.partial(os.open, mode=0o644)) as f:
            f.write(codegen)

    @classmethod
    def _remove_stale_cluster_config_for_backward_compatibility(
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
            auth_config: `auth` in cluster yaml.
            docker_user: If not None, use this user to ssh into the docker
        """
        username = auth_config['ssh_user']
        config_path = os.path.expanduser(cls.ssh_conf_path)
        cluster_config_path = os.path.expanduser(
            cls.ssh_cluster_path.format(cluster_name))
        if not os.path.exists(config_path):
            return

        with open(config_path, 'r', encoding='utf-8') as f:
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
                    # Find the line starting with ProxyCommand and contains ip
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

        if start_line_idx is not None:
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
            with open(config_path, 'w', encoding='utf-8') as f:
                f.write(''.join(config).strip())
                f.write('\n' * 2)

        # Delete include statement if it exists in the config.
        sky_autogen_comment = ('# Added by sky (use `sky stop/down '
                               f'{cluster_name}` to remove)')
        with open(config_path, 'r', encoding='utf-8') as f:
            config = f.readlines()

        for i, line in enumerate(config):
            config_str = line.strip()
            if f'Include {cluster_config_path}' in config_str:
                with open(config_path, 'w', encoding='utf-8') as f:
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

    @classmethod
    def remove_cluster(cls, cluster_name: str):
        """Remove auth information for cluster from ~/.sky/ssh/<cluster_name>.

        If no existing host matching the provided specification is found, then
        nothing is removed.

        Args:
            cluster_name: Cluster name.
        """

        with timeline.FileLockEvent(
                cls.ssh_conf_per_cluster_lock_path.format(cluster_name)):
            cluster_config_path = os.path.expanduser(
                cls.ssh_cluster_path.format(cluster_name))
            common_utils.remove_file_if_exists(cluster_config_path)

    @classmethod
    def list_cluster_names(cls) -> List[str]:
        """List all names of clusters with SSH config set up."""
        cluster_config_dir = os.path.expanduser(cls.ssh_cluster_path.format(''))
        return [
            os.path.basename(path)
            for path in glob.glob(os.path.join(cluster_config_dir, '*'))
        ]


def generate_cluster_name():
    # TODO: change this ID formatting to something more pleasant.
    # User name is helpful in non-isolated accounts, e.g., GCP, Azure.
    return f'sky-{uuid.uuid4().hex[:4]}-{common_utils.get_cleaned_username()}'
