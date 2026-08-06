"""Constants for SSH Node Pools"""
# pylint: disable=line-too-long
import os

DEFAULT_KUBECONFIG_PATH = os.path.expanduser('~/.kube/config')
SSH_CONFIG_PATH = os.path.expanduser('~/.ssh/config')
NODE_POOLS_INFO_DIR = os.path.expanduser('~/.sky/ssh_node_pools_info')
NODE_POOLS_KEY_DIR = os.path.expanduser('~/.sky/ssh_keys')
DEFAULT_SSH_NODE_POOLS_PATH = os.path.expanduser('~/.sky/ssh_node_pools.yaml')

K3S_TOKEN = os.environ.get('SKYPILOT_K3S_TOKEN')
if not K3S_TOKEN:
    raise ValueError('SKYPILOT_K3S_TOKEN must be set for SSH node pools.')
