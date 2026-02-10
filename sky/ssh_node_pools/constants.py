"""Constants for SSH Node Pools"""
# pylint: disable=line-too-long
import os

DEFAULT_KUBECONFIG_PATH = os.path.expanduser('~/.kube/config')
SSH_CONFIG_PATH = os.path.expanduser('~/.ssh/config')
NODE_POOLS_INFO_DIR = os.path.expanduser('~/.sky/ssh_node_pools_info')
NODE_POOLS_KEY_DIR = os.path.expanduser('~/.sky/ssh_keys')
DEFAULT_SSH_NODE_POOLS_PATH = os.path.expanduser('~/.sky/ssh_node_pools.yaml')

# TODO (kyuds): make this configurable?
K3S_TOKEN = 'mytoken'  # Any string can be used as the token
