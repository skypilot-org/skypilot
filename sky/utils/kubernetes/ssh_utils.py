"""Utility functions for managing SSH node pools."""
import os
import re
import subprocess
from typing import Any, Callable, Dict, List, Optional
import uuid

import yaml

from sky.utils import ux_utils

DEFAULT_SSH_NODE_POOLS_PATH = os.path.expanduser('~/.sky/ssh_node_pools.yaml')
RED = '\033[0;31m'
NC = '\033[0m'  # No color


def check_host_in_ssh_config(hostname: str) -> bool:
    """Return True iff *hostname* matches at least one `Host`/`Match` stanza
    in the user's OpenSSH client configuration (including anything pulled in
    via Include).

    It calls:  ssh -vvG <hostname> -o ConnectTimeout=0
    which:
      • -G  expands the effective config without connecting
      • -vv prints debug lines that show which stanzas are applied
      • ConnectTimeout=0 avoids a DNS lookup if <hostname> is a FQDN/IP

    No config files are opened or parsed manually.

    Parameters
    ----------
    hostname : str
        The alias/IP/FQDN you want to test.

    Returns
    -------
    bool
        True  – a specific stanza matched the host
        False – nothing but the global defaults (`Host *`) applied
    """
    # We direct stderr→stdout because debug output goes to stderr.
    proc = subprocess.run(
        ['ssh', '-vvG', hostname, '-o', 'ConnectTimeout=0'],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,  # we only want the text, not to raise
    )

    # Look for lines like:
    #   debug1: ~/.ssh/config line 42: Applying options for <hostname>
    # Anything other than "*"
    pattern = re.compile(r'^debug\d+: .*Applying options for ([^*].*)$',
                         re.MULTILINE)

    return bool(pattern.search(proc.stdout))


class UniqueKeySafeLoader(yaml.SafeLoader):
    """Custom YAML loader that raises an error if there are duplicate keys."""

    def construct_mapping(self, node, deep=False):
        mapping = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in mapping:
                raise yaml.constructor.ConstructorError(
                    note=(f'Duplicate key found: {key!r}.\n'
                          'Please remove one of them from the YAML file.'))
            mapping.add(key)
        return super().construct_mapping(node, deep)


def load_ssh_targets(file_path: str) -> Dict[str, Any]:
    """Load SSH targets from YAML file."""
    if not os.path.exists(file_path):
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'SSH Node Pools file not found: {file_path}')

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            targets = yaml.load(f, Loader=UniqueKeySafeLoader)
        return targets
    except yaml.constructor.ConstructorError as e:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(e.note) from e
    except (yaml.YAMLError, IOError, OSError) as e:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'Error loading SSH Node Pools file: {e}') from e


def get_cluster_config(
        targets: Dict[str, Any],
        cluster_name: Optional[str] = None,
        file_path: str = DEFAULT_SSH_NODE_POOLS_PATH) -> Dict[str, Any]:
    """Get configuration for specific clusters or all clusters."""
    if not targets:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                f'No clusters defined in SSH Node Pools file {file_path}')

    if cluster_name:
        if cluster_name not in targets:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Cluster {cluster_name!r} not found in '
                                 f'SSH Node Pools file {file_path}')
        return {cluster_name: targets[cluster_name]}

    # Return all clusters if no specific cluster is specified
    return targets


def prepare_hosts_info(
    cluster_name: str,
    cluster_config: Dict[str, Any],
    upload_ssh_key_func: Optional[Callable[[str, str], str]] = None
) -> List[Dict[str, str]]:
    """Prepare list of hosts with resolved user, identity_file, and password.

    Args:
        cluster_name: The name of the cluster.
        cluster_config: The configuration for the cluster.
        upload_ssh_key_func: A function to upload the SSH key to the remote
            server and wait for the key to be uploaded. This function will take
            the key name and the local key file path as input, and return the
            path for the remote SSH key file on the API server. This function
            will only be set in `sky ssh up -f` mode, and if this function is
            set, any ssh config will not be allowed as we don't support
            uploading any ssh config to the API server.

    Returns:
        A list of hosts with resolved user, identity_file, and password.
    """
    if 'hosts' not in cluster_config or not cluster_config['hosts']:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                f'No hosts defined in cluster {cluster_name} configuration')

    # Get cluster-level defaults
    cluster_user = cluster_config.get('user', '')
    cluster_identity_file = os.path.expanduser(
        cluster_config.get('identity_file', ''))
    cluster_password = cluster_config.get('password', '')

    # Check if cluster identity file exists
    if cluster_identity_file and not os.path.isfile(cluster_identity_file):
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                f'SSH Identity File Missing: {cluster_identity_file}')

    use_cluster_config_msg = (f'Cluster {cluster_name} uses SSH config '
                              'for hostname {host}, which is not '
                              'supported by the -f flag. Please use a '
                              'dict with `ip` field instead.')

    def _maybe_hardcode_identity_file(i: int, identity_file: str) -> str:
        if upload_ssh_key_func is None:
            return identity_file
        if not os.path.exists(os.path.expanduser(identity_file)):
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Identity file {identity_file} does not exist.')
        key_name = f'{cluster_name}-{i}-{str(uuid.uuid4())[:4]}'
        key_file_on_api_server = upload_ssh_key_func(key_name, identity_file)
        return key_file_on_api_server

    hosts_info = []
    for i, host in enumerate(cluster_config['hosts']):
        # Host can be a string (IP or SSH config hostname) or a dict
        if isinstance(host, str):
            # Check if this is an SSH config hostname
            is_ssh_config_host = check_host_in_ssh_config(host)
            if upload_ssh_key_func is not None and is_ssh_config_host:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(use_cluster_config_msg.format(host=host))

            hosts_info.append({
                'ip': host,
                'user': '' if is_ssh_config_host else cluster_user,
                'identity_file': '' if is_ssh_config_host else
                                 _maybe_hardcode_identity_file(
                                     i, cluster_identity_file),
                'password': cluster_password,
                'use_ssh_config': is_ssh_config_host
            })
        else:
            # It's a dict with potential overrides
            if 'ip' not in host:
                print(f'{RED}Warning: Host missing \'ip\' field, '
                      f'skipping: {host}{NC}')
                continue

            # Check if this is an SSH config hostname
            is_ssh_config_host = check_host_in_ssh_config(host['ip'])
            if upload_ssh_key_func is not None and is_ssh_config_host:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(use_cluster_config_msg.format(host=host))

            # Use host-specific values or fall back to cluster defaults
            host_user = '' if is_ssh_config_host else host.get(
                'user', cluster_user)
            host_identity_file = '' if is_ssh_config_host else (
                _maybe_hardcode_identity_file(
                    i, host.get('identity_file', cluster_identity_file)))
            host_identity_file = os.path.expanduser(host_identity_file)
            host_password = host.get('password', cluster_password)

            if host_identity_file and not os.path.isfile(host_identity_file):
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        f'SSH Identity File Missing: {host_identity_file}')

            hosts_info.append({
                'ip': host['ip'],
                'user': host_user,
                'identity_file': host_identity_file,
                'password': host_password,
                'use_ssh_config': is_ssh_config_host
            })

    return hosts_info
