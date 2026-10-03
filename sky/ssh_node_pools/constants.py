"""Constants for SSH Node Pools"""
# pylint: disable=line-too-long
import os, secrets

DEFAULT_KUBECONFIG_PATH = os.path.expanduser('~/.kube/config')
SSH_CONFIG_PATH = os.path.expanduser('~/.ssh/config')
NODE_POOLS_INFO_DIR = os.path.expanduser('~/.sky/ssh_node_pools_info')
NODE_POOLS_KEY_DIR = os.path.expanduser('~/.sky/ssh_keys')
DEFAULT_SSH_NODE_POOLS_PATH = os.path.expanduser('~/.sky/ssh_node_pools.yaml')

# TODO (kyuds): make this configurable?
def _get_k3s_token():
    token_file = os.path.join(NODE_POOLS_INFO_DIR, 'k3s_token')
    os.makedirs(NODE_POOLS_INFO_DIR, exist_ok=True)
    if os.path.exists(token_file):
        with open(token_file, 'r') as f:
            token = f.read().strip()
            if token:
                return token
    token = secrets.token_hex(32)
    with open(token_file, 'w') as f:
        f.write(token)
    os.chmod(token_file, 0o600)
    return token

K3S_TOKEN = _get_k3s_token()
