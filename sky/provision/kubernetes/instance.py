"""Kubernetes instance provisioning."""
import copy
import json
import time
from typing import Any, Dict, List, Optional
import uuid

from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
from sky import status_lib
from sky.adaptors import kubernetes
from sky.provision import common
from sky.provision import constants
from sky.provision import docker_utils
from sky.provision.kubernetes import config as config_lib
from sky.provision.kubernetes import network_utils
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import kubernetes_enums
from sky.utils import subprocess_utils
from sky.utils import ux_utils

POLL_INTERVAL = 2
_TIMEOUT_FOR_POD_TERMINATION = 60  # 1 minutes

logger = sky_logging.init_logger(__name__)
TAG_RAY_CLUSTER_NAME = 'ray-cluster-name'
TAG_SKYPILOT_CLUSTER_NAME = 'skypilot-cluster-name'
TAG_POD_INITIALIZED = 'skypilot-initialized'


def _get_head_pod_name(pods: Dict[str, Any]) -> Optional[str]:
    head_pod_name = None
    for pod_name, pod in pods.items():
        if pod.metadata.labels[constants.TAG_RAY_NODE_KIND] == 'head':
            head_pod_name = pod_name
            break
    return head_pod_name


def head_service_selector(cluster_name: str) -> Dict[str, str]:
    """Selector for Operator-configured head service."""
    return {'component': f'{cluster_name}-head'}


def _raise_pod_scheduling_errors(namespace, context, new_nodes):
    """Raise pod scheduling failure reason.

    When a pod fails to schedule in Kubernetes, the reasons for the failure
    are recorded as events. This function retrieves those events and raises
    descriptive errors for better debugging and user feedback.
    """

    def _formatted_resource_requirements(pod):
        # Returns a formatted string of resource requirements for a pod.
        resource_requirements = {}
        for container in pod.spec.containers:
            for resource, value in container.resources.requests.items():
                if resource not in resource_requirements:
                    resource_requirements[resource] = 0
                if resource == 'memory':
                    int_value = kubernetes_utils.parse_memory_resource(value)
                else:
                    int_value = kubernetes_utils.parse_cpu_or_gpu_resource(
                        value)
                resource_requirements[resource] += int_value
        return ', '.join(f'{resource}={value}'
                         for resource, value in resource_requirements.items())

    def _formatted_node_selector(pod) -> Optional[str]:
        # Returns a formatted string of node selectors for a pod.
        node_selectors = []
        if pod.spec.node_selector is None:
            return None
        for label_key, label_value in pod.spec.node_selector.items():
            node_selectors.append(f'{label_key}={label_value}')
        return ', '.join(node_selectors)

    def _lack_resource_msg(resource: str,
                           pod,
                           extra_msg: Optional[str] = None,
                           details: Optional[str] = None) -> str:
        resource_requirements = _formatted_resource_requirements(pod)
        node_selectors = _formatted_node_selector(pod)
        node_selector_str = f' and labels ({node_selectors})' if (
            node_selectors) else ''
        msg = (
            f'Insufficient {resource} capacity on the cluster. '
            f'Required resources ({resource_requirements}){node_selector_str} '
            'were not found in a single node. Other SkyPilot tasks or pods may '
            'be using resources. Check resource usage by running '
            '`kubectl describe nodes`.')
        if extra_msg:
            msg += f' {extra_msg}'
        if details:
            msg += f'\nFull error: {details}'
        return msg

    for new_node in new_nodes:
        pod = kubernetes.core_api(context).read_namespaced_pod(
            new_node.metadata.name, namespace)
        pod_status = pod.status.phase
        # When there are multiple pods involved while launching instance,
        # there may be a single pod causing issue while others are
        # successfully scheduled. In this case, we make sure to not surface
        # the error message from the pod that is already scheduled.
        if pod_status != 'Pending':
            continue
        pod_name = pod._metadata._name  # pylint: disable=protected-access
        events = kubernetes.core_api(context).list_namespaced_event(
            namespace,
            field_selector=(f'involvedObject.name={pod_name},'
                            'involvedObject.kind=Pod'))
        # Events created in the past hours are kept by
        # Kubernetes python client and we want to surface
        # the latest event message
        events_desc_by_time = sorted(
            events.items,
            key=lambda e: e.metadata.creation_timestamp,
            reverse=True)

        event_message = None
        for event in events_desc_by_time:
            if event.reason == 'FailedScheduling':
                event_message = event.message
                break
        timeout_err_msg = ('Timed out while waiting for nodes to start. '
                           'Cluster may be out of resources or '
                           'may be too slow to autoscale.')
        if event_message is not None:
            if pod_status == 'Pending':
                logger.info(event_message)
                if 'Insufficient cpu' in event_message:
                    raise config_lib.KubernetesError(
                        _lack_resource_msg('CPU', pod, details=event_message))
                if 'Insufficient memory' in event_message:
                    raise config_lib.KubernetesError(
                        _lack_resource_msg('memory', pod,
                                           details=event_message))
                if 'Insufficient smarter-devices/fuse' in event_message:
                    raise config_lib.KubernetesError(
                        'Something went wrong with FUSE device daemonset.'
                        ' Try restarting your FUSE pods by running '
                        '`kubectl delete pods -n skypilot-system -l name=smarter-device-manager`.'  # pylint: disable=line-too-long
                        f' Full error: {event_message}')
                gpu_lf_keys = [
                    lf.get_label_key()
                    for lf in kubernetes_utils.LABEL_FORMATTER_REGISTRY
                ]
                if pod.spec.node_selector:
                    for label_key in pod.spec.node_selector.keys():
                        if label_key in gpu_lf_keys:
                            # TODO(romilb): We may have additional node
                            #  affinity selectors in the future - in that
                            #  case we will need to update this logic.
                            if (('Insufficient nvidia.com/gpu'
                                 in event_message) or
                                ('didn\'t match Pod\'s node affinity/selector'
                                 in event_message)):
                                extra_msg = (
                                    f'Verify if '
                                    f'{pod.spec.node_selector[label_key]}'
                                    ' is available in the cluster.')
                                raise config_lib.KubernetesError(
                                    _lack_resource_msg('GPU',
                                                       pod,
                                                       extra_msg,
                                                       details=event_message))
            raise config_lib.KubernetesError(f'{timeout_err_msg} '
                                             f'Pod status: {pod_status}'
                                             f'Details: \'{event_message}\' ')
    raise config_lib.KubernetesError(f'{timeout_err_msg}')


def _raise_command_running_error(message: str, command: str, pod_name: str,
                                 rc: int, stdout: str) -> None:
    if rc == 0:
        return
    raise config_lib.KubernetesError(
        f'Failed to {message} for pod {pod_name} with return '
        f'code {rc}: {command!r}\nOutput: {stdout}.')


def _wait_for_pods_to_schedule(namespace, context, new_nodes, timeout: int):
    """Wait for all pods to be scheduled.

    Wait for all pods including jump pod to be scheduled, and if it
    exceeds the timeout, raise an exception. If pod's container
    is ContainerCreating, then we can assume that resources have been
    allocated and we can exit.

    If timeout is set to a negative value, this method will wait indefinitely.
    """
    start_time = time.time()

    def _evaluate_timeout() -> bool:
        # If timeout is negative, retry indefinitely.
        if timeout < 0:
            return True
        return time.time() - start_time < timeout

    while _evaluate_timeout():
        all_pods_scheduled = True
        for node in new_nodes:
            # Iterate over each pod to check their status
            pod = kubernetes.core_api(context).read_namespaced_pod(
                node.metadata.name, namespace)
            if pod.status.phase == 'Pending':
                # If container_statuses is None, then the pod hasn't
                # been scheduled yet.
                if pod.status.container_statuses is None:
                    all_pods_scheduled = False
                    break

        if all_pods_scheduled:
            return
        time.sleep(1)

    # Handle pod scheduling errors
    try:
        _raise_pod_scheduling_errors(namespace, context, new_nodes)
    except config_lib.KubernetesError:
        raise
    except Exception as e:
        raise config_lib.KubernetesError(
            'An error occurred while trying to fetch the reason '
            'for pod scheduling failure. '
            f'Error: {common_utils.format_exception(e)}') from None


def _wait_for_pods_to_run(namespace, context, new_nodes):
    """Wait for pods and their containers to be ready.

    Pods may be pulling images or may be in the process of container
    creation.
    """

    def _check_init_containers(pod):
        # Check if any of the init containers failed
        # to start. Could be because the init container
        # command failed or failed to pull image etc.
        for init_status in pod.status.init_container_statuses:
            init_terminated = init_status.state.terminated
            if init_terminated:
                if init_terminated.exit_code != 0:
                    msg = init_terminated.message if (
                        init_terminated.message) else str(init_terminated)
                    raise config_lib.KubernetesError(
                        'Failed to run init container for pod '
                        f'{pod.metadata.name}. Error details: {msg}.')
                continue
            init_waiting = init_status.state.waiting
            if (init_waiting is not None and init_waiting.reason
                    not in ['ContainerCreating', 'PodInitializing']):
                # TODO(romilb): There may be more states to check for. Add
                #  them as needed.
                msg = init_waiting.message if (
                    init_waiting.message) else str(init_waiting)
                raise config_lib.KubernetesError(
                    'Failed to create init container for pod '
                    f'{pod.metadata.name}. Error details: {msg}.')

    while True:
        all_pods_running = True
        # Iterate over each pod to check their status
        for node in new_nodes:
            pod = kubernetes.core_api(context).read_namespaced_pod(
                node.metadata.name, namespace)

            # Continue if pod and all the containers within the
            # pod are successfully created and running.
            if pod.status.phase == 'Running' and all(
                    container.state.running
                    for container in pod.status.container_statuses):
                continue

            all_pods_running = False
            if pod.status.phase == 'Pending':
                # Iterate over each container in pod to check their status
                for container_status in pod.status.container_statuses:
                    # If the container wasn't in 'ContainerCreating'
                    # state, then we know pod wasn't scheduled or
                    # had some other error, such as image pull error.
                    # See list of possible reasons for waiting here:
                    # https://stackoverflow.com/a/57886025
                    waiting = container_status.state.waiting
                    if waiting is not None:
                        if waiting.reason == 'PodInitializing':
                            _check_init_containers(pod)
                        elif waiting.reason != 'ContainerCreating':
                            msg = waiting.message if waiting.message else str(
                                waiting)
                            raise config_lib.KubernetesError(
                                'Failed to create container while launching '
                                f'the node. Error details: {msg}.')
            # Reaching this point means that one of the pods had an issue,
            # so break out of the loop, and wait until next second.
            break

        if all_pods_running:
            break
        time.sleep(1)


def _set_env_vars_in_pods(namespace: str, context: Optional[str],
                          new_pods: List):
    """Setting environment variables in pods.

    Once all containers are ready, we can exec into them and set env vars.
    Kubernetes automatically populates containers with critical
    environment variables, such as those for discovering services running
    in the cluster and CUDA/nvidia environment variables. We need to
    make sure these env vars are available in every task and ssh session.
    This is needed for GPU support and service discovery.
    See https://github.com/skypilot-org/skypilot/issues/2287 for
    more details.

    To do so, we capture env vars from the pod's runtime and write them to
    /etc/profile.d/, making them available for all users in future
    shell sessions.
    """
    set_k8s_env_var_cmd = docker_utils.SETUP_ENV_VARS_CMD

    for new_pod in new_pods:
        runner = command_runner.KubernetesCommandRunner(
            ((namespace, context), new_pod.metadata.name))
        rc, stdout, _ = runner.run(set_k8s_env_var_cmd,
                                   require_outputs=True,
                                   stream_logs=False)
        _raise_command_running_error('set env vars', set_k8s_env_var_cmd,
                                     new_pod.metadata.name, rc, stdout)


def _check_user_privilege(namespace: str, context: Optional[str],
                          new_nodes: List) -> None:
    # Checks if the default user has sufficient privilege to set up
    # the kubernetes instance pod.
    check_k8s_user_sudo_cmd = (
        'if [ $(id -u) -eq 0 ]; then'
        # If user is root, create an alias for sudo used in skypilot setup
        '  echo \'alias sudo=""\' >> ~/.bashrc; echo succeed;'
        'else '
        '  if command -v sudo >/dev/null 2>&1; then '
        '    timeout 2 sudo -l >/dev/null 2>&1 && echo succeed || '
        f'    ( echo {exceptions.INSUFFICIENT_PRIVILEGES_CODE!r}; ); '
        '  else '
        f'    ( echo {exceptions.INSUFFICIENT_PRIVILEGES_CODE!r}; ); '
        '  fi; '
        'fi')

    for new_node in new_nodes:
        runner = command_runner.KubernetesCommandRunner(
            ((namespace, context), new_node.metadata.name))
        rc, stdout, stderr = runner.run(check_k8s_user_sudo_cmd,
                                        require_outputs=True,
                                        separate_stderr=True,
                                        stream_logs=False)
        _raise_command_running_error('check user privilege',
                                     check_k8s_user_sudo_cmd,
                                     new_node.metadata.name, rc,
                                     stdout + stderr)
        if stdout == str(exceptions.INSUFFICIENT_PRIVILEGES_CODE):
            raise config_lib.KubernetesError(
                'Insufficient system privileges detected. '
                'Ensure the default user has root access or '
                '"sudo" is installed and the user is added to the sudoers '
                'from the image.')


def _setup_ssh_in_pods(namespace: str, context: Optional[str],
                       new_nodes: List) -> None:
    # Setting up ssh for the pod instance. This is already setup for
    # the jump pod so it does not need to be run for it.
    set_k8s_ssh_cmd = (
        'set -ex; '
        'prefix_cmd() '
        '{ if [ $(id -u) -ne 0 ]; then echo "sudo"; else echo ""; fi; }; '
        'export DEBIAN_FRONTEND=noninteractive;'
        '$(prefix_cmd) apt-get update;'
        '$(prefix_cmd) apt install openssh-server rsync -y; '
        '$(prefix_cmd) mkdir -p /var/run/sshd; '
        '$(prefix_cmd) '
        'sed -i "s/PermitRootLogin prohibit-password/PermitRootLogin yes/" '
        '/etc/ssh/sshd_config; '
        '$(prefix_cmd) sed '
        '"s@session\\s*required\\s*pam_loginuid.so@session optional '
        'pam_loginuid.so@g" -i /etc/pam.d/sshd; '
        'cd /etc/ssh/ && $(prefix_cmd) ssh-keygen -A; '
        '$(prefix_cmd) mkdir -p ~/.ssh; '
        '$(prefix_cmd) chown -R $(whoami) ~/.ssh;'
        '$(prefix_cmd) chmod 700 ~/.ssh; '
        '$(prefix_cmd) cat /etc/secret-volume/ssh-publickey* > '
        '~/.ssh/authorized_keys; '
        '$(prefix_cmd) chmod 644 ~/.ssh/authorized_keys; '
        '$(prefix_cmd) service ssh restart; '
        # Eliminate the error
        # `mesg: ttyname failed: inappropriate ioctl for device`.
        # See https://www.educative.io/answers/error-mesg-ttyname-failed-inappropriate-ioctl-for-device  # pylint: disable=line-too-long
        '$(prefix_cmd) sed -i "s/mesg n/tty -s \\&\\& mesg n/" ~/.profile;')

    def _setup_ssh_thread(new_node):
        pod_name = new_node.metadata.name
        runner = command_runner.KubernetesCommandRunner(
            ((namespace, context), pod_name))
        logger.info(f'{"-"*20}Start: Set up SSH in pod {pod_name!r} {"-"*20}')
        rc, stdout, _ = runner.run(set_k8s_ssh_cmd,
                                   require_outputs=True,
                                   stream_logs=False)
        _raise_command_running_error('setup ssh', set_k8s_ssh_cmd, pod_name, rc,
                                     stdout)
        logger.info(f'{"-"*20}End: Set up SSH in pod {pod_name!r} {"-"*20}')

    subprocess_utils.run_in_parallel(_setup_ssh_thread, new_nodes)


def _label_pod(namespace: str, context: Optional[str], pod_name: str,
               label: Dict[str, str]) -> None:
    """Label a pod."""
    kubernetes.core_api(context).patch_namespaced_pod(
        pod_name,
        namespace, {'metadata': {
            'labels': label
        }},
        _request_timeout=kubernetes.API_TIMEOUT)


def _create_namespaced_pod_with_retries(namespace: str, pod_spec: dict,
                                        context: Optional[str]) -> Any:
    """Attempts to create a Kubernetes Pod and handle any errors.

    Currently, we handle errors due to the AppArmor annotation and retry if
    it fails due to the `FieldValueForbidden` error.
    See https://github.com/skypilot-org/skypilot/issues/4174 for details.

    Returns: The created Pod object.
    """
    try:
        # Attempt to create the Pod with the AppArmor annotation
        pod = kubernetes.core_api(context).create_namespaced_pod(
            namespace, pod_spec)
        return pod
    except kubernetes.api_exception() as e:
        try:
            error_body = json.loads(e.body)
            error_message = error_body.get('message', '')
        except json.JSONDecodeError:
            error_message = str(e.body)
        # Check if the error is due to the AppArmor annotation and retry.
        # We add an AppArmor annotation to set it as unconfined in our
        # base template in kubernetes-ray.yml.j2. This is required for
        # FUSE to work in the pod on most Kubernetes distributions.
        # However, some distributions do not support the AppArmor annotation
        # and will fail to create the pod. In this case, we retry without
        # the annotation.
        if (e.status == 422 and 'FieldValueForbidden' in error_message and
                'AppArmorProfile: nil' in error_message):
            logger.warning('AppArmor annotation caused pod creation to fail. '
                           'Retrying without the annotation. '
                           'Note: this may cause bucket mounting to fail.')

            # Remove the AppArmor annotation
            annotations = pod_spec.get('metadata', {}).get('annotations', {})
            if ('container.apparmor.security.beta.kubernetes.io/ray-node'
                    in annotations):
                del annotations[
                    'container.apparmor.security.beta.kubernetes.io/ray-node']
                pod_spec['metadata']['annotations'] = annotations
                logger.info('AppArmor annotation removed from Pod spec.')
            else:
                logger.warning('AppArmor annotation not found in pod spec, '
                               'retrying will not help. '
                               f'Current annotations: {annotations}')
                raise e

            # Retry Pod creation without the AppArmor annotation
            try:
                pod = kubernetes.core_api(context).create_namespaced_pod(
                    namespace, pod_spec)
                logger.info(f'Pod {pod.metadata.name} created successfully '
                            'without AppArmor annotation.')
                return pod
            except kubernetes.api_exception() as retry_exception:
                logger.info('Failed to create Pod without AppArmor annotation: '
                            f'{retry_exception}')
                raise retry_exception
        else:
            # Re-raise the exception if it's a different error
            raise e


def _create_pods(region: str, cluster_name_on_cloud: str,
                 config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Create pods based on the config."""
    provider_config = config.provider_config
    namespace = kubernetes_utils.get_namespace_from_config(provider_config)
    context = kubernetes_utils.get_context_from_config(provider_config)
    pod_spec = copy.deepcopy(config.node_config)
    tags = {
        TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud,
    }
    pod_spec['metadata']['namespace'] = namespace
    if 'labels' in pod_spec['metadata']:
        pod_spec['metadata']['labels'].update(tags)
    else:
        pod_spec['metadata']['labels'] = tags
    pod_spec['metadata']['labels'].update(
        {TAG_SKYPILOT_CLUSTER_NAME: cluster_name_on_cloud})

    terminating_pods = kubernetes_utils.filter_pods(namespace, context, tags,
                                                    ['Terminating'])
    start_time = time.time()
    while (len(terminating_pods) > 0 and
           time.time() - start_time < _TIMEOUT_FOR_POD_TERMINATION):
        logger.debug(f'run_instances: Found {len(terminating_pods)} '
                     'terminating pods. Waiting them to finish: '
                     f'{list(terminating_pods.keys())}')
        time.sleep(POLL_INTERVAL)
        terminating_pods = kubernetes_utils.filter_pods(namespace, context,
                                                        tags, ['Terminating'])

    if len(terminating_pods) > 0:
        # If there are still terminating pods, we force delete them.
        logger.debug(f'run_instances: Found {len(terminating_pods)} '
                     'terminating pods still in terminating state after '
                     f'timeout {_TIMEOUT_FOR_POD_TERMINATION}s. '
                     'Force deleting them.')
        for pod_name in terminating_pods.keys():
            # grace_period_seconds=0 means force delete the pod.
            # https://github.com/kubernetes-client/python/issues/508#issuecomment-1695759777
            kubernetes.core_api(context).delete_namespaced_pod(
                pod_name,
                namespace,
                _request_timeout=config_lib.DELETION_TIMEOUT,
                grace_period_seconds=0)

    running_pods = kubernetes_utils.filter_pods(namespace, context, tags,
                                                ['Pending', 'Running'])
    head_pod_name = _get_head_pod_name(running_pods)
    logger.debug(f'Found {len(running_pods)} existing pods: '
                 f'{list(running_pods.keys())}')

    to_start_count = config.count - len(running_pods)
    if to_start_count < 0:
        raise RuntimeError(
            'The number of running+pending pods '
            f'({config.count - to_start_count}) in cluster '
            f'"{cluster_name_on_cloud}" is greater than the number '
            f'requested by the user ({config.count}). '
            'This is likely a resource leak. '
            'Use "sky down" to terminate the cluster.')

    # Add nvidia runtime class if it exists
    nvidia_runtime_exists = False
    try:
        nvidia_runtime_exists = kubernetes_utils.check_nvidia_runtime_class(
            context)
    except kubernetes.kubernetes.client.ApiException as e:
        logger.warning('run_instances: Error occurred while checking for '
                       f'nvidia RuntimeClass - '
                       f'{common_utils.format_exception(e)}'
                       'Continuing without using nvidia RuntimeClass.\n'
                       'If you are on a K3s cluster, manually '
                       'override runtimeClassName in ~/.sky/config.yaml. '
                       'For more details, refer to https://skypilot.readthedocs.io/en/latest/reference/config.html')  # pylint: disable=line-too-long

    needs_gpus = (pod_spec['spec']['containers'][0].get('resources', {}).get(
        'limits', {}).get('nvidia.com/gpu', 0) > 0)
    if nvidia_runtime_exists and needs_gpus:
        pod_spec['spec']['runtimeClassName'] = 'nvidia'

    created_pods = {}
    logger.debug(f'run_instances: calling create_namespaced_pod '
                 f'(count={to_start_count}).')
    for _ in range(to_start_count):
        if head_pod_name is None:
            pod_spec['metadata']['labels'].update(constants.HEAD_NODE_TAGS)
            head_selector = head_service_selector(cluster_name_on_cloud)
            pod_spec['metadata']['labels'].update(head_selector)
            pod_spec['metadata']['name'] = f'{cluster_name_on_cloud}-head'
        else:
            pod_spec['metadata']['labels'].update(constants.WORKER_NODE_TAGS)
            pod_uuid = str(uuid.uuid4())[:4]
            pod_name = f'{cluster_name_on_cloud}-{pod_uuid}'
            pod_spec['metadata']['name'] = f'{pod_name}-worker'
            # For multi-node support, we put a soft-constraint to schedule
            # worker pods on different nodes than the head pod.
            # This is not set as a hard constraint because if different nodes
            # are not available, we still want to be able to schedule worker
            # pods on larger nodes which may be able to fit multiple SkyPilot
            # "nodes".
            pod_spec['spec']['affinity'] = {
                'podAntiAffinity': {
                    # Set as a soft constraint
                    'preferredDuringSchedulingIgnoredDuringExecution': [{
                        # Max weight to avoid scheduling on the
                        # same physical node unless necessary.
                        'weight': 100,
                        'podAffinityTerm': {
                            'labelSelector': {
                                'matchExpressions': [{
                                    'key': TAG_SKYPILOT_CLUSTER_NAME,
                                    'operator': 'In',
                                    'values': [cluster_name_on_cloud]
                                }]
                            },
                            'topologyKey': 'kubernetes.io/hostname'
                        }
                    }]
                }
            }

        pod = _create_namespaced_pod_with_retries(namespace, pod_spec, context)
        created_pods[pod.metadata.name] = pod
        if head_pod_name is None:
            head_pod_name = pod.metadata.name

    wait_pods_dict = kubernetes_utils.filter_pods(namespace, context, tags,
                                                  ['Pending'])
    wait_pods = list(wait_pods_dict.values())

    networking_mode = network_utils.get_networking_mode(
        config.provider_config.get('networking_mode'))
    if networking_mode == kubernetes_enums.KubernetesNetworkingMode.NODEPORT:
        # Adding the jump pod to the new_nodes list as well so it can be
        # checked if it's scheduled and running along with other pods.
        ssh_jump_pod_name = pod_spec['metadata']['labels']['skypilot-ssh-jump']
        jump_pod = kubernetes.core_api(context).read_namespaced_pod(
            ssh_jump_pod_name, namespace)
        wait_pods.append(jump_pod)
    provision_timeout = provider_config['timeout']

    wait_str = ('indefinitely'
                if provision_timeout < 0 else f'for {provision_timeout}s')
    logger.debug(f'run_instances: waiting {wait_str} for pods to schedule and '
                 f'run: {list(wait_pods_dict.keys())}')

    # Wait until the pods are scheduled and surface cause for error
    # if there is one
    _wait_for_pods_to_schedule(namespace, context, wait_pods, provision_timeout)
    # Wait until the pods and their containers are up and running, and
    # fail early if there is an error
    logger.debug(f'run_instances: waiting for pods to be running (pulling '
                 f'images): {list(wait_pods_dict.keys())}')
    _wait_for_pods_to_run(namespace, context, wait_pods)
    logger.debug(f'run_instances: all pods are scheduled and running: '
                 f'{list(wait_pods_dict.keys())}')

    running_pods = kubernetes_utils.filter_pods(namespace, context, tags,
                                                ['Running'])
    initialized_pods = kubernetes_utils.filter_pods(namespace, context, {
        TAG_POD_INITIALIZED: 'true',
        **tags
    }, ['Running'])
    uninitialized_pods = {
        pod_name: pod
        for pod_name, pod in running_pods.items()
        if pod_name not in initialized_pods
    }
    if len(uninitialized_pods) > 0:
        logger.debug(f'run_instances: Initializing {len(uninitialized_pods)} '
                     f'pods: {list(uninitialized_pods.keys())}')
        uninitialized_pods_list = list(uninitialized_pods.values())

        # Setup SSH and environment variables in pods.
        # Make sure commands used in these methods are generic and work
        # on most base images. E.g., do not use Python, since that may not
        # be installed by default.
        _check_user_privilege(namespace, context, uninitialized_pods_list)
        _setup_ssh_in_pods(namespace, context, uninitialized_pods_list)
        _set_env_vars_in_pods(namespace, context, uninitialized_pods_list)

        for pod in uninitialized_pods.values():
            _label_pod(namespace,
                       context,
                       pod.metadata.name,
                       label={
                           TAG_POD_INITIALIZED: 'true',
                           **pod.metadata.labels
                       })

    assert head_pod_name is not None, 'head_instance_id should not be None'
    return common.ProvisionRecord(
        provider_name='kubernetes',
        region=region,
        zone=None,
        cluster_name=cluster_name_on_cloud,
        head_instance_id=head_pod_name,
        resumed_instance_ids=[],
        created_instance_ids=list(created_pods.keys()),
    )


def run_instances(region: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Runs instances for the given cluster."""
    try:
        return _create_pods(region, cluster_name_on_cloud, config)
    except (kubernetes.api_exception(), config_lib.KubernetesError) as e:
        e_msg = common_utils.format_exception(e).replace('\n', ' ')
        logger.warning('run_instances: Error occurred when creating pods: '
                       f'{e_msg}')
        raise


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    del region, cluster_name_on_cloud, state


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    raise NotImplementedError()


def _terminate_node(namespace: str, context: Optional[str],
                    pod_name: str) -> None:
    """Terminate a pod."""
    logger.debug('terminate_instances: calling delete_namespaced_pod')
    try:
        kubernetes_utils.clean_zombie_ssh_jump_pod(namespace, context, pod_name)
    except Exception as e:  # pylint: disable=broad-except
        logger.warning('terminate_instances: Error occurred when analyzing '
                       f'SSH Jump pod: {e}')
    try:
        kubernetes.core_api(context).delete_namespaced_service(
            pod_name, namespace, _request_timeout=config_lib.DELETION_TIMEOUT)
        kubernetes.core_api(context).delete_namespaced_service(
            f'{pod_name}-ssh',
            namespace,
            _request_timeout=config_lib.DELETION_TIMEOUT)
    except kubernetes.api_exception():
        pass
    # Note - delete pod after all other resources are deleted.
    # This is to ensure there are no leftover resources if this down is run
    # from within the pod, e.g., for autodown.
    try:
        kubernetes.core_api(context).delete_namespaced_pod(
            pod_name, namespace, _request_timeout=config_lib.DELETION_TIMEOUT)
    except kubernetes.api_exception() as e:
        if e.status == 404:
            logger.warning('terminate_instances: Tried to delete pod '
                           f'{pod_name}, but the pod was not found (404).')
        else:
            raise


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Dict[str, Any],
    worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    namespace = kubernetes_utils.get_namespace_from_config(provider_config)
    context = kubernetes_utils.get_context_from_config(provider_config)
    tag_filters = {
        TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud,
    }
    pods = kubernetes_utils.filter_pods(namespace, context, tag_filters, None)

    def _is_head(pod) -> bool:
        return pod.metadata.labels[constants.TAG_RAY_NODE_KIND] == 'head'

    for pod_name, pod in pods.items():
        logger.debug(f'Terminating instance {pod_name}: {pod}')
        if _is_head(pod) and worker_only:
            continue
        _terminate_node(namespace, context, pod_name)


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    del region  # unused
    assert provider_config is not None
    namespace = kubernetes_utils.get_namespace_from_config(provider_config)
    context = kubernetes_utils.get_context_from_config(provider_config)
    tag_filters = {
        TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud,
    }

    running_pods = kubernetes_utils.filter_pods(namespace, context, tag_filters,
                                                ['Running'])

    pods: Dict[str, List[common.InstanceInfo]] = {}
    head_pod_name = None

    port_forward_mode = kubernetes_enums.KubernetesNetworkingMode.PORTFORWARD
    network_mode_str = skypilot_config.get_nested(('kubernetes', 'networking'),
                                                  port_forward_mode.value)
    network_mode = kubernetes_enums.KubernetesNetworkingMode.from_str(
        network_mode_str)
    external_ip = kubernetes_utils.get_external_ip(network_mode, context)
    port = 22
    if not provider_config.get('use_internal_ips', False):
        port = kubernetes_utils.get_head_ssh_port(cluster_name_on_cloud,
                                                  namespace, context)

    head_pod_name = None
    cpu_request = None
    for pod_name, pod in running_pods.items():
        internal_ip = pod.status.pod_ip
        pods[pod_name] = [
            common.InstanceInfo(
                instance_id=pod_name,
                internal_ip=internal_ip,
                external_ip=(None if network_mode == port_forward_mode else
                             external_ip),
                ssh_port=port,
                tags=pod.metadata.labels,
            )
        ]
        if pod.metadata.labels[constants.TAG_RAY_NODE_KIND] == 'head':
            head_pod_name = pod_name
            head_spec = pod.spec
            assert head_spec is not None, pod
            cpu_request = head_spec.containers[0].resources.requests['cpu']

    assert cpu_request is not None, 'cpu_request should not be None'

    ssh_user = 'sky'
    get_k8s_ssh_user_cmd = 'echo $(whoami)'
    assert head_pod_name is not None
    runner = command_runner.KubernetesCommandRunner(
        ((namespace, context), head_pod_name))
    rc, stdout, stderr = runner.run(get_k8s_ssh_user_cmd,
                                    require_outputs=True,
                                    separate_stderr=True,
                                    stream_logs=False)
    _raise_command_running_error('get ssh user', get_k8s_ssh_user_cmd,
                                 head_pod_name, rc, stdout + stderr)
    ssh_user = stdout.strip()
    logger.debug(
        f'Using ssh user {ssh_user} for cluster {cluster_name_on_cloud}')

    return common.ClusterInfo(
        instances=pods,
        head_instance_id=head_pod_name,
        ssh_user=ssh_user,
        # We manually set object-store-memory=500000000 to avoid ray from
        # allocating a very large object store in each pod that may cause
        # problems for other pods.
        custom_ray_options={
            'object-store-memory': 500000000,
            'num-cpus': cpu_request,
        },
        provider_name='kubernetes',
        provider_config=provider_config)


def query_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True
) -> Dict[str, Optional[status_lib.ClusterStatus]]:
    status_map = {
        'Pending': status_lib.ClusterStatus.INIT,
        'Running': status_lib.ClusterStatus.UP,
        'Failed': None,
        'Unknown': None,
        'Succeeded': None,
        'Terminating': None,
    }

    assert provider_config is not None
    namespace = kubernetes_utils.get_namespace_from_config(provider_config)
    context = kubernetes_utils.get_context_from_config(provider_config)

    # Get all the pods with the label skypilot-cluster: <cluster_name>
    try:
        pods = kubernetes.core_api(context).list_namespaced_pod(
            namespace,
            label_selector=f'skypilot-cluster={cluster_name_on_cloud}',
            _request_timeout=kubernetes.API_TIMEOUT).items
    except kubernetes.max_retry_error():
        with ux_utils.print_exception_no_traceback():
            ctx = kubernetes_utils.get_current_kube_config_context_name()
            raise exceptions.ClusterStatusFetchingError(
                f'Failed to query cluster {cluster_name_on_cloud!r} status. '
                'Network error - check if the Kubernetes cluster in '
                f'context {ctx} is up and accessible.') from None
    except Exception as e:  # pylint: disable=broad-except
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ClusterStatusFetchingError(
                f'Failed to query Kubernetes cluster {cluster_name_on_cloud!r} '
                f'status: {common_utils.format_exception(e)}')

    # Check if the pods are running or pending
    cluster_status = {}
    for pod in pods:
        pod_status = status_map[pod.status.phase]
        if non_terminated_only and pod_status is None:
            continue
        cluster_status[pod.metadata.name] = pod_status
    return cluster_status


def get_command_runners(
    cluster_info: common.ClusterInfo,
    **credentials: Dict[str, Any],
) -> List[command_runner.CommandRunner]:
    """Get a command runner for the given cluster."""
    assert cluster_info.provider_config is not None, cluster_info
    instances = cluster_info.instances
    namespace = kubernetes_utils.get_namespace_from_config(
        cluster_info.provider_config)
    context = kubernetes_utils.get_context_from_config(
        cluster_info.provider_config)
    node_list = []
    if cluster_info.head_instance_id is not None:
        node_list = [((namespace, context), cluster_info.head_instance_id)]
    node_list.extend(((namespace, context), pod_name)
                     for pod_name in instances.keys()
                     if pod_name != cluster_info.head_instance_id)
    return command_runner.KubernetesCommandRunner.make_runner_list(
        node_list=node_list, **credentials)
