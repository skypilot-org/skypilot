"""Command to manage SSH target clusters.

This module contains the commands for the 'sky ssh' command group which manages
SSH target clusters defined in ~/.sky/ssh_targets.yaml.
"""

import os
import sys
import click

from sky.client import sdk
from sky.utils.kubernetes import kubernetes_deploy_utils as kube_utils


#TODO: Plumb this through the API server.
#TODO: Fix the logging.

SSH_TARGETS_PATH = os.path.expanduser('~/.sky/ssh_targets.yaml')
# TODO: Add support for custom kubeconfig path.
SSH_KUBECONFIG_PATH = os.path.expanduser('~/.kube/config')

@click.group(name='ssh')
def ssh():
    """Commands for managing SSH targets."""
    pass

@ssh.command('up')
@click.option('--cluster', help='Name of the cluster to set up. If not specified, the first cluster in ssh_targets.yaml is used.')
@click.option('--kubeconfig', help=f'Path to save the Kubernetes configuration file. Default: {SSH_KUBECONFIG_PATH}')
@click.option('--async', 'async_call', is_flag=True, hidden=True, help='Run the command asynchronously.')
def up(cluster, kubeconfig, async_call):
    """Set up a cluster using SSH targets from ~/.sky/ssh_targets.yaml.
    
    This command sets up a Kubernetes cluster on the machines specified in
    ~/.sky/ssh_targets.yaml and configures SkyPilot to use it.
    """
    if not os.path.exists(SSH_TARGETS_PATH):
        print(f'Error: SSH targets file not found: {SSH_TARGETS_PATH}')
        print(f'Please create this file with your SSH targets.')
        print('See https://docs.skypilot.co/en/latest/reservations/existing-machines.html for details.')
        sys.exit(1)
    
    kubeconfig_path = kubeconfig if kubeconfig else SSH_KUBECONFIG_PATH
    
    try:
        request_id = sdk.ssh_up(cluster_name=cluster, kubeconfig_path=kubeconfig_path)
        if async_call:
            print(f'Request submitted with ID: {request_id}')
        else:
            sdk.stream_and_get(request_id)
    except Exception as e:
        print(f'Error setting up SSH cluster: {e}')
        sys.exit(1)

@ssh.command('down')
@click.option('--cluster', help='Name of the cluster to clean up. If not specified, the first cluster in ssh_targets.yaml is used.')
@click.option('--kubeconfig', help=f'Path to the Kubernetes configuration file to update. Default: {SSH_KUBECONFIG_PATH}')
@click.option('--async', 'async_call', is_flag=True, hidden=True, help='Run the command asynchronously.')
def down(cluster, kubeconfig, async_call):
    """Clean up a cluster set up with 'sky ssh up'.
    
    This command removes the Kubernetes installation from the machines specified
    in ~/.sky/ssh_targets.yaml.
    """
    if not os.path.exists(SSH_TARGETS_PATH):
        print(f'Error: SSH targets file not found: {SSH_TARGETS_PATH}')
        print(f'Please create this file with your SSH targets.')
        print('See https://docs.skypilot.co/en/latest/reservations/existing-machines.html for details.')
        sys.exit(1)
    
    kubeconfig_path = kubeconfig if kubeconfig else SSH_KUBECONFIG_PATH
    
    try:
        request_id = sdk.ssh_down(cluster_name=cluster, kubeconfig_path=kubeconfig_path)
        if async_call:
            print(f'Request submitted with ID: {request_id}')
        else:
            sdk.stream_and_get(request_id)
    except Exception as e:
        print(f'Error cleaning up SSH cluster: {e}')
        sys.exit(1) 