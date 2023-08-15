"""Script to label GPU nodes in a Kubernetes cluster for use with SkyPilot"""
import argparse
import os
import subprocess
from typing import Tuple

import click
from kubernetes import client
from kubernetes import config
import yaml

import sky
from sky.utils import log_utils


def prerequisite_check() -> Tuple[bool, str]:
    """Checks if kubectl is installed and kubeconfig is set up"""
    reason = ''
    prereq_ok = False
    try:
        subprocess.check_output(['kubectl', 'get', 'pods'])
        prereq_ok = True
    except FileNotFoundError:
        reason = 'kubectl not found. Please install kubectl and try again.'
    except subprocess.CalledProcessError as e:
        output = e.output.decode('utf-8')
        reason = 'Error running kubectl: ' + output
    return prereq_ok, reason


def cleanup() -> Tuple[bool, str]:
    """Deletes all Kubernetes resources created by this script

    Used to provide idempotency when the script is run multiple times. Also
    invoked if --cleanup is passed to the script.
    """
    # Delete any existing GPU labeler Kubernetes resources:
    del_command = ('kubectl delete pods,services,deployments,jobs,daemonsets,'
                   'replicasets,configmaps,secrets,pv,pvc,clusterrole,'
                   'serviceaccount,clusterrolebinding -n kube-system '
                   '-l job=sky-gpu-labeler')

    success = False
    reason = ''
    try:
        subprocess.check_output(del_command.split())
        success = True
    except subprocess.CalledProcessError as e:
        output = e.output.decode('utf-8')
        reason = 'Error deleting existing GPU labeler resources: ' + output
    return success, reason


def label():
    # Check if kubectl is installed and kubeconfig is set up
    prereq_ok, reason = prerequisite_check()
    if not prereq_ok:
        click.echo(reason)
        return

    deletion_success, reason = cleanup()
    if not deletion_success:
        click.echo(reason)
        return

    sky_dir = os.path.dirname(sky.__file__)
    manifest_dir = os.path.join(sky_dir, 'utils/kubernetes')

    # Apply the RBAC manifest using kubectl since it contains multiple resources
    with log_utils.safe_rich_status('Setting up GPU labeling'):
        rbac_manifest_path = os.path.join(manifest_dir,
                                          'k8s_gpu_labeler_setup.yaml')
        try:
            subprocess.check_output(
                ['kubectl', 'apply', '-f', rbac_manifest_path])
        except subprocess.CalledProcessError as e:
            output = e.output.decode('utf-8')
            click.echo('Error setting up GPU labeling: ' + output)
            return

    with log_utils.safe_rich_status('Creating GPU labeler jobs'):
        config.load_kube_config()

        v1 = client.CoreV1Api()
        batch_v1 = client.BatchV1Api()
        # Load the job manifest
        job_manifest_path = os.path.join(manifest_dir,
                                         'k8s_gpu_labeler_job.yaml')

        with open(job_manifest_path, 'r') as file:
            job_manifest = yaml.safe_load(file)

        # Iterate over nodes
        nodes = v1.list_node().items
        for node in nodes:
            node_name = node.metadata.name

            # Modify the job manifest for the current node
            job_manifest['metadata']['name'] = f'sky-gpu-labeler-{node_name}'
            job_manifest['spec']['template']['spec']['nodeSelector'] = {
                'kubernetes.io/hostname': node_name
            }
            namespace = job_manifest['metadata']['namespace']

            # Create the job for this node`
            batch_v1.create_namespaced_job(namespace, job_manifest)
            print(f'Created GPU labeler job for node {node_name}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        prog='sky-gpu-labeler',
        description='Labels GPU nodes in a Kubernetes cluster for use with '
        'SkyPilot. Operates by running a job on each node that '
        'parses nvidia-smi and patches the node with new labels. '
        'Labels created are of the format '
        'skypilot.co/accelerators: <gpu_name>. Automatically '
        'creates a service account and cluster role binding with '
        'permissions to list nodes and create labels.')
    parser.add_argument('--cleanup',
                        action='store_true',
                        help='Delete all GPU labeler resources in the '
                        'Kubernetes cluster.')
    args = parser.parse_args()
    if args.cleanup:
        cleanup()
    else:
        label()
