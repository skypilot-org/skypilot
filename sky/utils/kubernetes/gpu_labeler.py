"""Script to label GPU nodes in a Kubernetes cluster for use with SkyPilot"""
import argparse
import hashlib
import os
import subprocess
from typing import Dict, Optional, Tuple

import colorama
import yaml

from sky.adaptors import kubernetes
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.utils import directory_utils
from sky.utils import rich_utils


def _format_string(str_to_format: str, colorama_format: str) -> str:
    return f'{colorama_format}{str_to_format}{colorama.Style.RESET_ALL}'


def cleanup(context: Optional[str] = None) -> Tuple[bool, str]:
    """Deletes all Kubernetes resources created by this script

    Used to provide idempotency when the script is run multiple times. Also
    invoked if --cleanup is passed to the script.
    """
    # Delete any existing GPU labeler Kubernetes resources:
    del_command = ('kubectl delete pods,services,deployments,jobs,daemonsets,'
                   'replicasets,configmaps,secrets,pv,pvc,clusterrole,'
                   'serviceaccount,clusterrolebinding -n kube-system '
                   '-l job=sky-gpu-labeler')
    if context:
        del_command += f' --context {context}'
    success = False
    reason = ''
    with rich_utils.client_status('Cleaning up existing GPU labeling '
                                  'resources'):
        try:
            subprocess.run(del_command.split(), check=True, capture_output=True)
            success = True
        except subprocess.CalledProcessError as e:
            output = e.output.decode('utf-8')
            reason = 'Error deleting existing GPU labeler resources: ' + output
        return success, reason


def get_node_hash(node_name: str):
    # Generates a 32 character md5 hash from a string
    md5_hash = hashlib.md5(node_name.encode()).hexdigest()
    return md5_hash[:32]


def label(context: Optional[str] = None, wait_for_completion: bool = True):
    deletion_success, reason = cleanup(context=context)
    if not deletion_success:
        print(reason)
        return

    unlabeled_gpu_nodes = kubernetes_utils.get_unlabeled_accelerator_nodes(
        context=context)

    if not unlabeled_gpu_nodes:
        print('No unlabeled GPU nodes found in the cluster. If you have '
              'unlabeled GPU nodes, please ensure that they have the resource '
              f'`{kubernetes_utils.get_gpu_resource_key(context)}: '
              '<number of GPUs>` in their capacity.')
        return

    print(
        _format_string(
            f'Found {len(unlabeled_gpu_nodes)} '
            'unlabeled GPU nodes in the cluster', colorama.Fore.YELLOW))

    manifest_dir = os.path.join(directory_utils.get_sky_dir(),
                                'utils/kubernetes')

    # Apply the RBAC manifest using kubectl since it contains multiple resources
    with rich_utils.client_status('Setting up GPU labeling'):
        rbac_manifest_path = os.path.join(manifest_dir,
                                          'k8s_gpu_labeler_setup.yaml')
        try:
            apply_command = ['kubectl', 'apply', '-f', rbac_manifest_path]
            if context:
                apply_command += ['--context', context]
            subprocess.check_output(apply_command)
        except subprocess.CalledProcessError as e:
            output = e.output.decode('utf-8')
            print('Error setting up GPU labeling: ' + output)
            return

    jobs_to_node_names: Dict[str, str] = {}
    with rich_utils.client_status('Creating GPU labeler jobs'):
        batch_v1 = kubernetes.batch_api(context=context)
        # Load the job manifest
        job_manifest_path = os.path.join(manifest_dir,
                                         'k8s_gpu_labeler_job.yaml')

        with open(job_manifest_path, 'r', encoding='utf-8') as file:
            job_manifest = yaml.safe_load(file)

        # Check if the 'nvidia' RuntimeClass exists
        try:
            nvidia_exists = kubernetes_utils.check_nvidia_runtime_class(
                context=context)
        except Exception as e:  # pylint: disable=broad-except
            print('Error occurred while checking for nvidia RuntimeClass: '
                  f'{str(e)}')
            print('Continuing without using nvidia RuntimeClass. '
                  'This may fail on K3s clusters. '
                  'For more details, refer to K3s deployment notes at: '
                  'https://docs.skypilot.co/en/latest/reference/kubernetes/kubernetes-setup.html')  # pylint: disable=line-too-long
            nvidia_exists = False

        if nvidia_exists:
            print('Using nvidia RuntimeClass for GPU labeling.')
            job_manifest['spec']['template']['spec'][
                'runtimeClassName'] = 'nvidia'
        else:
            print('Using default RuntimeClass for GPU labeling.')

        for node in unlabeled_gpu_nodes:
            node_name = node.metadata.name

            # Modify the job manifest for the current node
            job_name = ('sky-gpu-labeler-'
                        f'{get_node_hash(node_name)}')
            jobs_to_node_names[job_name] = node_name
            job_manifest['metadata']['name'] = job_name

            job_manifest['spec']['template']['spec']['nodeSelector'] = {
                'kubernetes.io/hostname': node_name
            }
            namespace = job_manifest['metadata']['namespace']

            # Create the job for this node`
            batch_v1.create_namespaced_job(namespace, job_manifest)
            print(
                _format_string(f'Created GPU labeler job for node {node_name}',
                               colorama.Style.DIM))

    context_str = f' --context {context}' if context else ''

    if wait_for_completion:
        # Wait for the job to complete
        with rich_utils.client_status(
                'Waiting for GPU labeler jobs to complete'):
            success = wait_for_jobs_completion(jobs_to_node_names,
                                               'kube-system',
                                               context=context)
        if success:
            print(
                _format_string('✅ GPU labeling completed successfully',
                               colorama.Fore.GREEN))
        else:
            print(_format_string('❌ GPU labeling failed', colorama.Fore.RED))
        cleanup(context=context)
    else:
        print(
            f'GPU labeling started - this may take 10 min or more to complete.'
            '\nTo check the status of GPU labeling jobs, run '
            f'`kubectl get jobs -n kube-system '
            f'-l job=sky-gpu-labeler{context_str}`'
            '\nYou can check if nodes have been labeled by running '
            f'`kubectl describe nodes{context_str}` '
            'and looking for labels of the format '
            '`skypilot.co/accelerator: <gpu_name>`. ')


def wait_for_jobs_completion(jobs_to_node_names: Dict[str, str],
                             namespace: str,
                             context: Optional[str] = None,
                             timeout: int = 60 * 20):
    """Waits for a Kubernetes Job to complete or fail.

    Args:
        jobs_to_node_names: A dictionary mapping job names to node names.
        namespace: The namespace the Job is in (default: "default").
        timeout: Timeout in seconds (default: 1200 seconds = 20 minutes).

    Returns:
        True if the Job completed successfully, False if it failed or timed out.
    """
    batch_v1 = kubernetes.batch_api(context=context)
    w = kubernetes.watch()
    completed_jobs = []
    for event in w.stream(func=batch_v1.list_namespaced_job,
                          namespace=namespace,
                          timeout_seconds=timeout):
        job = event['object']
        job_name = job.metadata.name
        if job_name in jobs_to_node_names:
            node_name = jobs_to_node_names[job_name]
            if job.status and job.status.completion_time:
                print(
                    _format_string(
                        f'GPU labeler job for node {node_name} '
                        'completed successfully', colorama.Style.DIM))
                completed_jobs.append(job_name)
                num_remaining_jobs = len(jobs_to_node_names) - len(
                    completed_jobs)
                if num_remaining_jobs == 0:
                    w.stop()
                    return True
            elif job.status and job.status.failed:
                print(
                    _format_string(
                        f'GPU labeler job for node {node_name} failed',
                        colorama.Style.DIM))
                w.stop()
                return False
    print(
        _format_string(
            f'Timed out after waiting {timeout} seconds '
            'for job to complete', colorama.Style.DIM))
    return False  #Timed out


def main():
    parser = argparse.ArgumentParser(
        description='Labels GPU nodes in a Kubernetes cluster for use with '
        'SkyPilot. Operates by running a job on each node that '
        'parses nvidia-smi and patches the node with new labels. '
        'Labels created are of the format '
        'skypilot.co/accelerator: <gpu_name>. Automatically '
        'creates a service account and cluster role binding with '
        'permissions to list nodes and create labels.')
    parser.add_argument('--cleanup',
                        action='store_true',
                        help='delete all GPU labeler resources in the '
                        'Kubernetes cluster.')
    parser.add_argument('--context',
                        type=str,
                        help='the context to use for the Kubernetes cluster.')
    parser.add_argument('--async',
                        dest='async_completion',
                        action='store_true',
                        help='do not wait for the GPU labeling to complete.')
    args = parser.parse_args()
    context = None
    if args.context:
        context = args.context

    # Check if kubectl is installed and kubeconfig is set up
    prereq_ok, reason = kubernetes_utils.check_credentials(context=context)
    if not prereq_ok:
        print(reason)
        return

    if args.cleanup:
        cleanup(context=context)
    else:
        label(context=context, wait_for_completion=not args.async_completion)


if __name__ == '__main__':
    main()
