"""Script to label GPU nodes in a Kubernetes cluster for use with SkyPilot"""
import argparse
import os
import subprocess
from typing import Tuple

from kubernetes import client
from kubernetes import config
import yaml

import sky
from sky.utils import rich_utils


def prerequisite_check() -> Tuple[bool, str]:
    """Checks if kubectl is installed and kubeconfig is set up"""
    reason = ''
    prereq_ok = False
    try:
        subprocess.run(['kubectl', 'get', 'pods'],
                       check=True,
                       capture_output=True)
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
    with rich_utils.safe_status('Cleaning up existing GPU labeling '
                                'resources'):
        try:
            subprocess.run(del_command.split(), check=True, capture_output=True)
            success = True
        except subprocess.CalledProcessError as e:
            output = e.output.decode('utf-8')
            reason = 'Error deleting existing GPU labeler resources: ' + output
        return success, reason


def label():
    deletion_success, reason = cleanup()
    if not deletion_success:
        print(reason)
        return

    sky_dir = os.path.dirname(sky.__file__)
    manifest_dir = os.path.join(sky_dir, 'utils/kubernetes')

    # Apply the RBAC manifest using kubectl since it contains multiple resources
    with rich_utils.safe_status('Setting up GPU labeling'):
        rbac_manifest_path = os.path.join(manifest_dir,
                                          'k8s_gpu_labeler_setup.yaml')
        try:
            subprocess.check_output(
                ['kubectl', 'apply', '-f', rbac_manifest_path])
        except subprocess.CalledProcessError as e:
            output = e.output.decode('utf-8')
            print('Error setting up GPU labeling: ' + output)
            return

    with rich_utils.safe_status('Creating GPU labeler jobs'):
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

        # Get the list of nodes with GPUs
        gpu_nodes = []
        for node in nodes:
            if 'nvidia.com/gpu' in node.status.capacity:
                gpu_nodes.append(node)

        print(f'Found {len(gpu_nodes)} GPU nodes in the cluster')

        for node in gpu_nodes:
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
    if len(gpu_nodes) == 0:
        print('No GPU nodes found in the cluster. If you have GPU nodes, '
              'please ensure that they have the label '
              '`nvidia.com/gpu: <number of GPUs>`')
    else:
        print('GPU labeling started - this may take a few minutes to complete.'
              '\nTo check the status of GPU labeling jobs, run '
              '`kubectl get jobs -n kube-system -l job=sky-gpu-labeler`'
              '\nYou can check if nodes have been labeled by running '
              '`kubectl describe nodes` and looking for labels of the format '
              '`skypilot.co/accelerators: <gpu_name>`. ')


def main():
    parser = argparse.ArgumentParser(
        description='Labels GPU nodes in a Kubernetes cluster for use with '
        'SkyPilot. Operates by running a job on each node that '
        'parses nvidia-smi and patches the node with new labels. '
        'Labels created are of the format '
        'skypilot.co/accelerators: <gpu_name>. Automatically '
        'creates a service account and cluster role binding with '
        'permissions to list nodes and create labels.')
    parser.add_argument('--cleanup',
                        action='store_true',
                        help='delete all GPU labeler resources in the '
                        'Kubernetes cluster.')
    args = parser.parse_args()

    # Check if kubectl is installed and kubeconfig is set up
    prereq_ok, reason = prerequisite_check()
    if not prereq_ok:
        print(reason)
        return

    if args.cleanup:
        cleanup()
    else:
        label()


if __name__ == '__main__':
    main()
