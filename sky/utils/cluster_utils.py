"""Utility functions for cluster yaml file."""

import functools
import glob
import os
import platform
import re
import textwrap
from typing import Dict, List, Optional, Tuple
import uuid

from sky.skylet import constants
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import lock_events
from sky.utils import ux_utils

# The cluster yaml used to create the current cluster where the module is
# called.
SKY_CLUSTER_YAML_REMOTE_PATH = '~/.sky/sky_ray.yml'

# Cache for WSL detection result
_is_wsl_cached: Optional[bool] = None
_wsl_windows_home_cached: Optional[str] = None


def is_wsl() -> bool:
    """Detect if running inside Windows Subsystem for Linux (WSL).

    Returns:
        True if running in WSL, False otherwise.
    """
    global _is_wsl_cached
    if _is_wsl_cached is not None:
        return _is_wsl_cached

    _is_wsl_cached = False
    if platform.system() != 'Linux':
        return _is_wsl_cached

    # Check /proc/version for Microsoft/WSL indicators
    try:
        with open('/proc/version', 'r', encoding='utf-8') as f:
            version = f.read().lower()
            _is_wsl_cached = 'microsoft' in version or 'wsl' in version
    except (FileNotFoundError, PermissionError):
        pass

    return _is_wsl_cached


def get_wsl_windows_home() -> Optional[str]:
    """Get the Windows user's home directory path when running in WSL.

    Returns:
        The path to Windows home directory (e.g., '/mnt/c/Users/username')
        or None if not in WSL or cannot determine.
    """
    global _wsl_windows_home_cached
    if _wsl_windows_home_cached is not None:
        return _wsl_windows_home_cached

    if not is_wsl():
        return None

    # Try to get Windows username from environment or by running cmd.exe
    windows_home = None

    # Method 1: Check if USERPROFILE is set (sometimes available)
    userprofile = os.environ.get('USERPROFILE')
    if userprofile:
        # Convert Windows path to WSL path
        # e.g., C:\Users\username -> /mnt/c/Users/username
        windows_home = userprofile.replace('\\', '/')
        if windows_home[1] == ':':
            drive = windows_home[0].lower()
            windows_home = f'/mnt/{drive}{windows_home[2:]}'

    # Method 2: Try to find the Windows home via /mnt/c/Users
    if not windows_home or not os.path.isdir(windows_home):
        try:
            import subprocess
            result = subprocess.run(
                ['cmd.exe', '/c', 'echo', '%USERPROFILE%'],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                userprofile = result.stdout.strip()
                if userprofile and userprofile != '%USERPROFILE%':
                    windows_home = userprofile.replace('\\', '/')
                    if windows_home[1] == ':':
                        drive = windows_home[0].lower()
                        windows_home = f'/mnt/{drive}{windows_home[2:]}'
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

    # Verify the path exists
    if windows_home and os.path.isdir(windows_home):
        _wsl_windows_home_cached = windows_home
        return _wsl_windows_home_cached

    return None


def get_wsl_path_for_windows(wsl_path: str) -> Optional[str]:
    """Convert a WSL path to a Windows-compatible UNC path.

    Args:
        wsl_path: A path in WSL (e.g., '/home/user/.sky/ssh-keys/cluster.key'
            or '~/.sky/ssh-keys/cluster.key')

    Returns:
        A Windows UNC path (e.g., '\\\\wsl$\\Ubuntu\\home\\user\\.sky\\...')
        or None if conversion fails.
    """
    if not is_wsl():
        return None

    # Expand ~ to full path
    expanded_path = os.path.expanduser(wsl_path)

    # Get WSL distribution name
    distro_name = os.environ.get('WSL_DISTRO_NAME')
    if not distro_name:
        # Try to detect from /etc/os-release or default to 'Ubuntu'
        try:
            with open('/etc/os-release', 'r', encoding='utf-8') as f:
                for line in f:
                    if line.startswith('NAME='):
                        # Extract name, e.g., NAME="Ubuntu" -> Ubuntu
                        name = line.split('=')[1].strip().strip('"')
                        # Use first word as distro name
                        distro_name = name.split()[0]
                        break
        except (FileNotFoundError, PermissionError):
            pass
        if not distro_name:
            distro_name = 'Ubuntu'

    # Convert to UNC path: \\wsl$\<distro>\path
    # Note: We use forward slashes which Windows SSH also accepts
    unc_path = f'//wsl$/{distro_name}{expanded_path}'
    return unc_path


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

    # Windows paths (used when running in WSL)
    _windows_ssh_setup_attempted = False
    _windows_ssh_setup_warned = False

    @classmethod
    def _get_generated_config(cls, autogen_comment: str,
                              cluster_name_on_cloud: str, host_name: str,
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
              SetEnv {constants.SKY_CLUSTER_NAME_ENV_VAR_KEY}={cluster_name_on_cloud}
              {proxy}
            """.rstrip())
        codegen = codegen + '\n'
        return codegen

    @classmethod
    def _get_windows_ssh_paths(cls) -> Optional[Tuple[str, str, str]]:
        """Get Windows SSH config paths when running in WSL.

        Returns:
            A tuple of (windows_ssh_config_path, windows_sky_ssh_dir,
            windows_home) as WSL-accessible paths, or None if not in WSL
            or Windows home cannot be determined.
        """
        windows_home = get_wsl_windows_home()
        if not windows_home:
            return None

        windows_ssh_config = os.path.join(windows_home, '.ssh', 'config')
        windows_sky_ssh_dir = os.path.join(windows_home, '.sky', 'ssh')
        return (windows_ssh_config, windows_sky_ssh_dir, windows_home)

    @classmethod
    def _add_cluster_to_windows_ssh_config(
        cls,
        cluster_name: str,
        cluster_name_on_cloud: str,
        ips: List[str],
        username: str,
        key_path: str,
        ports: List[int],
        proxy_command: Optional[str],
        docker_user: Optional[str],
        docker_proxy_command_generator,
    ) -> None:
        """Add cluster SSH config to Windows SSH config when running in WSL.

        This enables VSCode on Windows to connect to SkyPilot clusters
        launched from WSL without additional configuration.
        """
        windows_paths = cls._get_windows_ssh_paths()
        if not windows_paths:
            return

        windows_ssh_config, windows_sky_ssh_dir, _ = windows_paths

        # Convert WSL key path to Windows UNC path
        windows_key_path = get_wsl_path_for_windows(key_path)
        if not windows_key_path:
            return

        try:
            # Ensure Windows .ssh directory exists
            windows_ssh_dir = os.path.dirname(windows_ssh_config)
            os.makedirs(windows_ssh_dir, exist_ok=True, mode=0o700)

            # Ensure Windows .sky/ssh directory exists
            os.makedirs(windows_sky_ssh_dir, exist_ok=True, mode=0o700)

            # Create/update Windows SSH config to include sky configs
            if not os.path.exists(windows_ssh_config):
                with open(windows_ssh_config,
                          'w',
                          encoding='utf-8',
                          opener=functools.partial(os.open,
                                                   mode=0o644)) as f:
                    f.write('\n')

            with open(windows_ssh_config, 'r', encoding='utf-8') as f:
                config = f.readlines()

            # Add Include directive for SkyPilot configs if not present
            # Use Windows-style path in the Include directive
            windows_home = get_wsl_windows_home()
            # Convert to Windows path format for the Include directive
            # From /mnt/c/Users/name -> C:/Users/name (Windows SSH accepts /)
            if windows_home and windows_home.startswith('/mnt/'):
                drive = windows_home[5].upper()
                win_path = f'{drive}:{windows_home[6:]}'
                include_path = f'{win_path}/.sky/ssh/*'
            else:
                include_path = f'{windows_sky_ssh_dir}/*'

            include_str = f'Include {include_path}'
            found = False
            for line in config:
                if line.strip() == include_str:
                    found = True
                    break
                if line.strip().startswith('Host '):
                    break

            if not found:
                with open(windows_ssh_config, 'w', encoding='utf-8') as f:
                    config.insert(
                        0,
                        '# Added by SkyPilot for ssh config of all clusters\n'
                        f'{include_str}\n')
                    f.write(''.join(config).strip())
                    f.write('\n\n')

            # Generate cluster config for Windows
            sky_autogen_comment = ('# Added by sky (use `sky stop/down '
                                   f'{cluster_name}` to remove)')

            codegen = ''
            for i, ip in enumerate(ips):
                node_ip = ip
                node_port = ports[i]
                node_proxy = proxy_command
                docker_proxy = None

                if docker_proxy_command_generator is not None:
                    docker_proxy = docker_proxy_command_generator(ip, node_port)
                    # Convert any WSL paths in proxy command to UNC paths
                    # This is complex and may not work reliably, so we skip
                    # docker configurations for Windows SSH config
                    continue

                node_name = (cluster_name
                             if i == 0 else f'{cluster_name}-worker{i}')

                if node_proxy is not None:
                    node_proxy = node_proxy.replace('%w', str(i))
                    # Skip nodes with proxy commands as they likely reference
                    # WSL-specific paths or binaries
                    continue

                codegen += cls._get_generated_config(
                    sky_autogen_comment,
                    cluster_name_on_cloud,
                    node_name,
                    node_ip,
                    username,
                    windows_key_path,
                    None,  # proxy_command - skip for Windows
                    node_port,
                    None,  # docker_proxy_command - skip for Windows
                ) + '\n'

            if codegen:
                cluster_config_path = os.path.join(windows_sky_ssh_dir,
                                                   cluster_name)
                with open(cluster_config_path,
                          'w',
                          encoding='utf-8',
                          opener=functools.partial(os.open,
                                                   mode=0o644)) as f:
                    f.write(codegen)

                if not cls._windows_ssh_setup_warned:
                    cls._windows_ssh_setup_warned = True
                    with ux_utils.print_exception_no_traceback():
                        print(
                            f'  WSL detected: SSH config also added to Windows '
                            f'({windows_ssh_config}) for VSCode Remote-SSH.')

        except (OSError, PermissionError) as e:
            # Silently ignore errors - Windows SSH config is optional
            if not cls._windows_ssh_setup_attempted:
                cls._windows_ssh_setup_attempted = True
                # Log only once to avoid spam
                import logging
                logger = logging.getLogger(__name__)
                logger.debug(f'Could not set up Windows SSH config: {e}')

    @classmethod
    def _remove_cluster_from_windows_ssh_config(cls,
                                                cluster_name: str) -> None:
        """Remove cluster SSH config from Windows when running in WSL."""
        windows_paths = cls._get_windows_ssh_paths()
        if not windows_paths:
            return

        _, windows_sky_ssh_dir, _ = windows_paths
        cluster_config_path = os.path.join(windows_sky_ssh_dir, cluster_name)

        try:
            common_utils.remove_file_if_exists(cluster_config_path)
        except (OSError, PermissionError):
            # Silently ignore errors - Windows SSH config is optional
            pass

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
    @lock_events.FileLockEvent(ssh_conf_lock_path)
    def add_cluster(
        cls,
        cluster_name: str,
        cluster_name_on_cloud: str,
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
            cluster_name_on_cloud: The cluster name as it appears in the cloud.
        """
        if ssh_user is None:
            username = auth_config['ssh_user']
        else:
            username = ssh_user
        if docker_user is not None:
            username = docker_user

        key_path = cls.generate_local_key_file(cluster_name, auth_config)
        # Keep the unexpanded path for SSH config (with ~)
        key_path_for_config = key_path
        # Expand the path for internal operations that need absolute path
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
        proxy_command_for_nodes = proxy_command
        if docker_user is not None:

            def _docker_proxy_cmd(ip: str, port: int) -> str:
                inner_proxy = proxy_command
                inner_port = port or 22
                if inner_proxy is not None:
                    inner_proxy = inner_proxy.replace('%h', ip)
                    inner_proxy = inner_proxy.replace('%p', str(inner_port))
                return ' '.join(['ssh'] + command_runner.ssh_options_list(
                    key_path,
                    ssh_control_name=None,
                    ssh_proxy_command=inner_proxy,
                    port=inner_port,
                    # ProxyCommand (ssh -W) is a forwarding tunnel, not an
                    # interactive session. ControlMaster would cache these
                    # processes, causing them to hang and block subsequent
                    # connections. Each ProxyCommand should be ephemeral.
                    disable_control_master=True
                ) + ['-W', '%h:%p', f'{auth_config["ssh_user"]}@{ip}'])

            docker_proxy_command_generator = _docker_proxy_cmd
            proxy_command_for_nodes = None

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
            node_proxy_command = proxy_command_for_nodes
            if node_proxy_command is not None:
                node_proxy_command = node_proxy_command.replace('%w', str(i))
            # TODO(romilb): Update port number when k8s supports multinode
            codegen += cls._get_generated_config(
                sky_autogen_comment, cluster_name_on_cloud, node_name, ip,
                username, key_path_for_config, node_proxy_command, port,
                docker_proxy_command) + '\n'

        cluster_config_path = os.path.expanduser(
            cls.ssh_cluster_path.format(cluster_name))

        with open(cluster_config_path,
                  'w',
                  encoding='utf-8',
                  opener=functools.partial(os.open, mode=0o644)) as f:
            f.write(codegen)

        # Also add to Windows SSH config if running in WSL
        # This enables VSCode on Windows to connect to clusters launched in WSL
        if is_wsl():
            cls._add_cluster_to_windows_ssh_config(
                cluster_name=cluster_name,
                cluster_name_on_cloud=cluster_name_on_cloud,
                ips=ips,
                username=username,
                key_path=key_path_for_config,
                ports=ports,
                proxy_command=proxy_command,
                docker_user=docker_user,
                docker_proxy_command_generator=docker_proxy_command_generator,
            )

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

        with lock_events.FileLockEvent(
                cls.ssh_conf_per_cluster_lock_path.format(cluster_name)):
            cluster_config_path = os.path.expanduser(
                cls.ssh_cluster_path.format(cluster_name))
            common_utils.remove_file_if_exists(cluster_config_path)

            # Also remove from Windows SSH config if running in WSL
            if is_wsl():
                cls._remove_cluster_from_windows_ssh_config(cluster_name)

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
