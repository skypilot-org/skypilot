"""Kubernetes instance provisioning."""
import copy
import json
import time
from typing import Any, Callable, Dict, List, Optional, Union
import uuid

from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
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
from sky.utils import status_lib
from sky.utils import subprocess_utils
from sky.utils import timeline
from sky.utils import ux_utils

POLL_INTERVAL = 2
_TIMEOUT_FOR_POD_TERMINATION = 60  # 1 minutes
_MAX_RETRIES = 3
_NUM_THREADS = subprocess_utils.get_parallel_threads('kubernetes')

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


def _formatted_resource_requirements(pod_or_spec: Union[Any, dict]) -> str:
    # Returns a formatted string of resource requirements for a pod.
    resource_requirements = {}

    if isinstance(pod_or_spec, dict):
        containers = pod_or_spec.get('spec', {}).get('containers', [])
    else:
        containers = pod_or_spec.spec.containers

    for container in containers:
        if isinstance(container, dict):
            resources = container.get('resources', {})
            requests = resources.get('requests', {})
        else:
            resources = container.resources
            requests = resources.requests or {}

        for resource, value in requests.items():
            if resource not in resource_requirements:
                resource_requirements[resource] = 0
            if resource == 'memory':
                int_value = kubernetes_utils.parse_memory_resource(value)
            else:
                int_value = kubernetes_utils.parse_cpu_or_gpu_resource(value)
            resource_requirements[resource] += int(int_value)
    return ', '.join(f'{resource}={value}'
                     for resource, value in resource_requirements.items())


def _formatted_node_selector(pod_or_spec: Union[Any, dict]) -> Optional[str]:
    # Returns a formatted string of node selectors for a pod.
    node_selectors = []

    if isinstance(pod_or_spec, dict):
        selectors = pod_or_spec.get('spec', {}).get('nodeSelector', {})
    else:
        selectors = pod_or_spec.spec.node_selector

    if not selectors:
        return None

    for label_key, label_value in selectors.items():
        node_selectors.append(f'{label_key}={label_value}')
    return ', '.join(node_selectors)


def _lack_resource_msg(resource: str,
                       pod_or_spec: Union[Any, dict],
                       extra_msg: Optional[str] = None,
                       details: Optional[str] = None) -> str:
    resource_requirements = _formatted_resource_requirements(pod_or_spec)
    node_selectors = _formatted_node_selector(pod_or_spec)
    node_selector_str = f' and labels ({node_selectors})' if (
        node_selectors) else ''
    msg = (f'Insufficient {resource} capacity on the cluster. '
           f'Required resources ({resource_requirements}){node_selector_str} '
           'were not found in a single node. Other SkyPilot tasks or pods may '
           'be using resources. Check resource usage by running '
           '`kubectl describe nodes`.')
    if extra_msg:
        msg += f' {extra_msg}'
    if details:
        msg += f'\nFull error: {details}'
    return msg


def _raise_pod_scheduling_errors(namespace, context, new_nodes):
    """Raise pod scheduling failure reason.

    When a pod fails to schedule in Kubernetes, the reasons for the failure
    are recorded as events. This function retrieves those events and raises
    descriptive errors for better debugging and user feedback.
    """
    timeout_err_msg = ('Timed out while waiting for nodes to start. '
                       'Cluster may be out of resources or '
                       'may be too slow to autoscale.')
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
                # TODO(aylei): after switching from smarter-device-manager to
                # fusermount-server, we need a new way to check whether the
                # fusermount-server daemonset is ready.
                gpu_lf_keys = [
                    key for lf in kubernetes_utils.LABEL_FORMATTER_REGISTRY
                    for key in lf.get_label_keys()
                ]
                if pod.spec.node_selector:
                    for label_key in pod.spec.node_selector.keys():
                        if label_key in gpu_lf_keys:
                            # TODO(romilb): We may have additional node
                            #  affinity selectors in the future - in that
                            #  case we will need to update this logic.
                            # TODO(Doyoung): Update the error message raised
                            # with the multi-host TPU support.
                            gpu_resource_key = kubernetes_utils.get_gpu_resource_key()  # pylint: disable=line-too-long
                            if 'Insufficient google.com/tpu' in event_message:
                                extra_msg = (
                                    f'Verify if '
                                    f'{pod.spec.node_selector[label_key]}'
                                    ' is available in the cluster. Note '
                                    'that multi-host TPU podslices are '
                                    'currently not unsupported.')
                                raise config_lib.KubernetesError(
                                    _lack_resource_msg('TPU',
                                                       pod,
                                                       extra_msg,
                                                       details=event_message))
                            elif ((f'Insufficient {gpu_resource_key}'
                                   in event_message) or
                                  ('didn\'t match Pod\'s node affinity/selector'
                                   in event_message)):
                                extra_msg = (
                                    f'Verify if any node matching label  '
                                    f'{pod.spec.node_selector[label_key]} and '
                                    f'sufficient resource {gpu_resource_key} '
                                    f'is available in the cluster.')
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


@timeline.event
def _wait_for_pods_to_schedule(namespace, context, new_nodes, timeout: int):
    """Wait for all pods to be scheduled.

    Wait for all pods including jump pod to be scheduled, and if it
    exceeds the timeout, raise an exception. If pod's container
    is ContainerCreating, then we can assume that resources have been
    allocated and we can exit.

    If timeout is set to a negative value, this method will wait indefinitely.
    """
    # Create a set of pod names we're waiting for
    if not new_nodes:
        return
    expected_pod_names = {node.metadata.name for node in new_nodes}
    start_time = time.time()

    def _evaluate_timeout() -> bool:
        # If timeout is negative, retry indefinitely.
        if timeout < 0:
            return True
        return time.time() - start_time < timeout

    while _evaluate_timeout():
        # Get all pods in a single API call using the cluster name label
        # which all pods in new_nodes should share
        cluster_name = new_nodes[0].metadata.labels[TAG_SKYPILOT_CLUSTER_NAME]
        pods = kubernetes.core_api(context).list_namespaced_pod(
            namespace,
            label_selector=f'{TAG_SKYPILOT_CLUSTER_NAME}={cluster_name}').items

        # Get the set of found pod names and check if we have all expected pods
        found_pod_names = {pod.metadata.name for pod in pods}
        missing_pods = expected_pod_names - found_pod_names
        if missing_pods:
            logger.info('Retrying waiting for pods: '
                        f'Missing pods: {missing_pods}')
            time.sleep(0.5)
            continue

        # Check if all pods are scheduled
        all_scheduled = True
        for pod in pods:
            if (pod.metadata.name in expected_pod_names and
                    pod.status.phase == 'Pending'):
                # If container_statuses is None, then the pod hasn't
                # been scheduled yet.
                if pod.status.container_statuses is None:
                    all_scheduled = False
                    break

        if all_scheduled:
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


@timeline.event
def _wait_for_pods_to_run(namespace, context, new_nodes):
    """Wait for pods and their containers to be ready.

    Pods may be pulling images or may be in the process of container
    creation.
    """
    if not new_nodes:
        return

    # Create a set of pod names we're waiting for
    expected_pod_names = {node.metadata.name for node in new_nodes}

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
        # Get all pods in a single API call
        cluster_name = new_nodes[0].metadata.labels[TAG_SKYPILOT_CLUSTER_NAME]
        all_pods = kubernetes.core_api(context).list_namespaced_pod(
            namespace,
            label_selector=f'{TAG_SKYPILOT_CLUSTER_NAME}={cluster_name}').items

        # Get the set of found pod names and check if we have all expected pods
        found_pod_names = {pod.metadata.name for pod in all_pods}
        missing_pods = expected_pod_names - found_pod_names
        if missing_pods:
            logger.info('Retrying running pods check: '
                        f'Missing pods: {missing_pods}')
            time.sleep(0.5)
            continue

        all_pods_running = True
        for pod in all_pods:
            if pod.metadata.name not in expected_pod_names:
                continue
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


def _run_function_with_retries(func: Callable,
                               operation_name: str,
                               max_retries: int = _MAX_RETRIES,
                               retry_delay: int = 5) -> Any:
    """Runs a function with retries on Kubernetes errors.

    Args:
        func: Function to retry
        operation_name: Name of the operation for logging
        max_retries: Maximum number of retry attempts
        retry_delay: Delay between retries in seconds

    Raises:
        The last exception encountered if all retries fail.
    """
    for attempt in range(max_retries + 1):
        try:
            return func()
        except config_lib.KubernetesError:
            if attempt < max_retries:
                logger.warning(f'Failed to {operation_name} - '
                               f'retrying in {retry_delay} seconds.')
                time.sleep(retry_delay)
            else:
                raise


@timeline.event
def pre_init(namespace: str, context: Optional[str], new_nodes: List) -> None:
    """Pre-initialization step for SkyPilot pods.

    This step is run in the pod right after it is created and before the
    SkyPilot runtime is setup.

    This step includes three key steps:

    1. Privilege check: Checks if the default user has sufficient privilege
    to set up the kubernetes instance pod.
    2. SSH setup: Sets up SSH for the pod instance.
    3. Environment variable setup to populate k8s env vars in the pod.

    Make sure commands used in these methods are generic and work
    on most base images. E.g., do not use Python, since that may not
    be installed by default.

    If you run any apt commands, be sure to check if the lock is available.
    It is possible the `apt update` run in the pod container args may still
    be running.

    Args:
        namespace (str): Kubernetes namespace.
        context (Optional[str]): Kubernetes context.
        new_nodes (List): List of new pod instances.

    Raises:
        config_lib.KubernetesError: If user privileges are insufficient or
          setup fails.
    """

    check_k8s_user_sudo_cmd = (
        'if [ $(id -u) -eq 0 ]; then'
        # If user is root, create an alias for sudo used in skypilot setup
        '  echo \'alias sudo=""\' >> ~/.bashrc; echo succeed;'
        'else '
        '  if command -v sudo >/dev/null 2>&1; then '
        '    timeout 2 sudo -l >/dev/null 2>&1 && echo succeed || '
        f'    ( echo {exceptions.INSUFFICIENT_PRIVILEGES_CODE!r}; '
        f'      exit {exceptions.INSUFFICIENT_PRIVILEGES_CODE}; ); '
        '  else '
        f'    ( echo {exceptions.INSUFFICIENT_PRIVILEGES_CODE!r}; '
        f'      exit {exceptions.INSUFFICIENT_PRIVILEGES_CODE}; ); '
        '  fi; '
        'fi;')

    # Kubernetes automatically populates containers with critical
    # environment variables, such as those for discovering services running
    # in the cluster and CUDA/nvidia environment variables. We need to
    # make sure these env vars are available in every task and ssh session.
    # This is needed for GPU support and service discovery.
    # See https://github.com/skypilot-org/skypilot/issues/2287 for more details.
    # To do so, we capture env vars from the pod's runtime and write them to
    # /etc/profile.d/, making them available for all users in future
    # shell sessions.
    set_k8s_env_var_cmd = docker_utils.SETUP_ENV_VARS_CMD

    check_apt_update_complete_cmd = (
        'echo "Checking if apt update from container init is complete..."; '
        'timeout_secs=600; '
        'start_time=$(date +%s); '
        'while ! grep -q "Fetched" /tmp/apt-update.log 2>/dev/null; do '
        '  echo "apt update still running. Logs:"; '
        '  cat /tmp/apt-update.log || true; '
        '  current_time=$(date +%s); '
        '  elapsed=$((current_time - start_time)); '
        '  if [ $elapsed -ge $timeout_secs ]; then '
        '    echo "Timed out waiting for apt update"; '
        '    exit 1; '
        '  fi; '
        '  sleep 5; '
        'done; '
        'echo "apt update complete."; ')

    install_ssh_k8s_cmd = (
        'prefix_cmd() '
        '{ if [ $(id -u) -ne 0 ]; then echo "sudo"; else echo ""; fi; }; '
        'export DEBIAN_FRONTEND=noninteractive;'
        'echo "Installing missing packages..."; '
        'for i in {1..5}; do '
        '  output=$($(prefix_cmd) apt install openssh-server rsync -y 2>&1); '
        '  rc=$?; '
        '  if [ $rc -eq 0 ]; then '
        '    break; '
        '  fi; '
        '  echo "$output" | grep -qi "could not get lock" || '
        '  grep -qi "Unable to acquire the dpkg frontend lock"; '
        '  if [ $? -eq 0 ]; then '
        '    echo "apt install failed due to lock, retrying. (Attempt $i/5)"; '
        '    sleep 5; '
        '  else '
        '    echo "apt install failed for a non-lock reason: $output"; '
        '    exit $rc; '
        '  fi; '
        'done; '
        'if [ $rc -ne 0 ]; then '
        '    echo "apt install failed after 5 attempts due to lock errors."; '
        '    exit $rc; '
        'fi; '
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

    pre_init_cmd = ('set -ex; ' + check_k8s_user_sudo_cmd +
                    set_k8s_env_var_cmd + check_apt_update_complete_cmd +
                    install_ssh_k8s_cmd)

    def _pre_init_thread(new_node):
        pod_name = new_node.metadata.name
        logger.info(f'{"-"*20}Start: Pre-init in pod {pod_name!r} {"-"*20}')
        runner = command_runner.KubernetesCommandRunner(
            ((namespace, context), pod_name))

        # Run the combined pre-init command
        rc, stdout, _ = runner.run(pre_init_cmd,
                                   require_outputs=True,
                                   stream_logs=False)
        if rc == exceptions.INSUFFICIENT_PRIVILEGES_CODE:
            raise config_lib.KubernetesError(
                'Insufficient system privileges detected. '
                'Ensure the default user has root access or '
                '"sudo" is installed and the user is added to the sudoers '
                'from the image.')

        op_name = 'pre-init'
        _raise_command_running_error(op_name, pre_init_cmd, pod_name, rc,
                                     stdout)

        logger.info(f'{"-"*20}End: Pre-init in pod {pod_name!r} {"-"*20}')

    # Run pre_init in parallel across all new_nodes
    subprocess_utils.run_in_parallel(_pre_init_thread, new_nodes, _NUM_THREADS)


def _label_pod(namespace: str, context: Optional[str], pod_name: str,
               label: Dict[str, str]) -> None:
    """Label a pod."""
    kubernetes.core_api(context).patch_namespaced_pod(
        pod_name,
        namespace, {'metadata': {
            'labels': label
        }},
        _request_timeout=kubernetes.API_TIMEOUT)


@timeline.event
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
        # Unlike other error from resource lackage on CPU/GPU/Memory, TPU
        # lackage error is raised when pod is attemtped to be created.
        # TODO(Doyoung): Update the error message raised with the multi-host
        # TPU support.
        elif 'Invalid resource requests for google.com/tpu.' in error_message:
            extra_message = ('Verify if the cluster has a TPU slice node with '
                             'a topology matching the number of TPU(s) '
                             'requested. Note that multi-host TPU podslices '
                             'are currently not unsupported.')
            raise config_lib.KubernetesError(
                _lack_resource_msg('TPU',
                                   pod_spec,
                                   details=error_message,
                                   extra_msg=extra_message))
        else:
            # Re-raise the exception if it's a different error
            raise e


@timeline.event
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
    while (terminating_pods and
           time.time() - start_time < _TIMEOUT_FOR_POD_TERMINATION):
        logger.debug(f'run_instances: Found {len(terminating_pods)} '
                     'terminating pods. Waiting them to finish: '
                     f'{list(terminating_pods.keys())}')
        time.sleep(POLL_INTERVAL)
        terminating_pods = kubernetes_utils.filter_pods(namespace, context,
                                                        tags, ['Terminating'])

    if terminating_pods:
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
                       'override runtimeClassName in ~/.sky/skyconfig.yaml. '
                       'For more details, refer to https://docs.skypilot.co/en/latest/reference/config.html')  # pylint: disable=line-too-long

    needs_gpus = False
    limits = pod_spec['spec']['containers'][0].get('resources',
                                                   {}).get('limits')
    if limits is not None:
        needs_gpus = limits.get(kubernetes_utils.get_gpu_resource_key(), 0) > 0

    # TPU pods provisioned on GKE use the default containerd runtime.
    # Reference: https://cloud.google.com/kubernetes-engine/docs/how-to/migrate-containerd#overview  # pylint: disable=line-too-long
    if nvidia_runtime_exists and needs_gpus:
        pod_spec['spec']['runtimeClassName'] = 'nvidia'

    created_pods = {}
    logger.debug(f'run_instances: calling create_namespaced_pod '
                 f'(count={to_start_count}).')

    def _create_pod_thread(i: int):
        pod_spec_copy = copy.deepcopy(pod_spec)
        if head_pod_name is None and i == 0:
            # First pod should be head if no head exists
            pod_spec_copy['metadata']['labels'].update(constants.HEAD_NODE_TAGS)
            head_selector = head_service_selector(cluster_name_on_cloud)
            pod_spec_copy['metadata']['labels'].update(head_selector)
            pod_spec_copy['metadata']['name'] = f'{cluster_name_on_cloud}-head'
        else:
            # Worker pods
            pod_spec_copy['metadata']['labels'].update(
                constants.WORKER_NODE_TAGS)
            pod_uuid = str(uuid.uuid4())[:6]
            pod_name = f'{cluster_name_on_cloud}-{pod_uuid}'
            pod_spec_copy['metadata']['name'] = f'{pod_name}-worker'
            # For multi-node support, we put a soft-constraint to schedule
            # worker pods on different nodes than the head pod.
            # This is not set as a hard constraint because if different nodes
            # are not available, we still want to be able to schedule worker
            # pods on larger nodes which may be able to fit multiple SkyPilot
            # "nodes".
            pod_spec_copy['spec']['affinity'] = {
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

        # TPU slice nodes are given a taint, google.com/tpu=present:NoSchedule.
        # This is to prevent from non-TPU workloads from being scheduled on TPU
        # slice nodes. We need this toleration to allow the pod to be scheduled
        # on TPU nodes.
        # Reference: https://cloud.google.com/kubernetes-engine/docs/concepts/tpus#how_tpus_work # pylint: disable=line-too-long
        tpu_label = kubernetes_utils.GKELabelFormatter.TPU_LABEL_KEY
        if tpu_label in config.node_config.get('spec',
                                               {}).get('nodeSelector', {}):
            tpu_toleration = {
                'key': kubernetes_utils.TPU_RESOURCE_KEY,
                'operator': 'Equal',
                'value': 'present',
                'effect': 'NoSchedule'
            }
            # Preserve existing tolerations if any
            existing_tolerations = pod_spec_copy['spec'].get('tolerations', [])
            pod_spec_copy['spec']['tolerations'] = existing_tolerations + [
                tpu_toleration
            ]

        return _create_namespaced_pod_with_retries(namespace, pod_spec_copy,
                                                   context)

    # Create pods in parallel
    pods = subprocess_utils.run_in_parallel(_create_pod_thread,
                                            list(range(to_start_count)),
                                            _NUM_THREADS)

    # Process created pods
    for pod in pods:
        created_pods[pod.metadata.name] = pod
        if head_pod_name is None and pod.metadata.labels.get(
                constants.TAG_RAY_NODE_KIND) == 'head':
            head_pod_name = pod.metadata.name

    networking_mode = network_utils.get_networking_mode(
        config.provider_config.get('networking_mode'))
    if networking_mode == kubernetes_enums.KubernetesNetworkingMode.NODEPORT:
        # Adding the jump pod to the new_nodes list as well so it can be
        # checked if it's scheduled and running along with other pods.
        ssh_jump_pod_name = pod_spec['metadata']['labels']['skypilot-ssh-jump']
        jump_pod = kubernetes.core_api(context).read_namespaced_pod(
            ssh_jump_pod_name, namespace)
        pods.append(jump_pod)
    provision_timeout = provider_config['timeout']

    wait_str = ('indefinitely'
                if provision_timeout < 0 else f'for {provision_timeout}s')
    logger.debug(f'run_instances: waiting {wait_str} for pods to schedule and '
                 f'run: {[pod.metadata.name for pod in pods]}')

    # Wait until the pods are scheduled and surface cause for error
    # if there is one
    _wait_for_pods_to_schedule(namespace, context, pods, provision_timeout)
    # Wait until the pods and their containers are up and running, and
    # fail early if there is an error
    logger.debug(f'run_instances: waiting for pods to be running (pulling '
                 f'images): {[pod.metadata.name for pod in pods]}')
    _wait_for_pods_to_run(namespace, context, pods)
    logger.debug(f'run_instances: all pods are scheduled and running: '
                 f'{[pod.metadata.name for pod in pods]}')

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

    def _delete_k8s_resource_with_retry(delete_func: Callable,
                                        resource_type: str,
                                        resource_name: str) -> None:
        """Helper to delete Kubernetes resources with 404 handling and retries.

        Args:
            delete_func: Function to call to delete the resource
            resource_type: Type of resource being deleted (e.g. 'service'),
                used in logging
            resource_name: Name of the resource being deleted, used in logging
        """
        max_retries = 3
        retry_delay = 5  # seconds

        for attempt in range(max_retries):
            try:
                delete_func()
                return
            except kubernetes.api_exception() as e:
                if e.status == 404:
                    logger.warning(
                        f'terminate_instances: Tried to delete {resource_type} '
                        f'{resource_name}, but the {resource_type} was not '
                        'found (404).')
                    return
                elif attempt < max_retries - 1:
                    logger.warning(f'terminate_instances: Failed to delete '
                                   f'{resource_type} {resource_name} (attempt '
                                   f'{attempt + 1}/{max_retries}). Error: {e}. '
                                   f'Retrying in {retry_delay} seconds...')
                    time.sleep(retry_delay)
                else:
                    raise

    # Delete services for the pod
    for service_name in [pod_name, f'{pod_name}-ssh']:
        _delete_k8s_resource_with_retry(
            delete_func=lambda name=service_name: kubernetes.core_api(
                context).delete_namespaced_service(name=name,
                                                   namespace=namespace,
                                                   _request_timeout=config_lib.
                                                   DELETION_TIMEOUT),
            resource_type='service',
            resource_name=service_name)

    # Note - delete pod after all other resources are deleted.
    # This is to ensure there are no leftover resources if this down is run
    # from within the pod, e.g., for autodown.
    _delete_k8s_resource_with_retry(
        delete_func=lambda: kubernetes.core_api(context).delete_namespaced_pod(
            name=pod_name,
            namespace=namespace,
            _request_timeout=config_lib.DELETION_TIMEOUT),
        resource_type='pod',
        resource_name=pod_name)


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

    # Clean up the SSH jump pod if in use
    networking_mode = network_utils.get_networking_mode(
        provider_config.get('networking_mode'))
    if networking_mode == kubernetes_enums.KubernetesNetworkingMode.NODEPORT:
        pod_name = list(pods.keys())[0]
        try:
            kubernetes_utils.clean_zombie_ssh_jump_pod(namespace, context,
                                                       pod_name)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning('terminate_instances: Error occurred when analyzing '
                           f'SSH Jump pod: {e}')

    def _is_head(pod) -> bool:
        return pod.metadata.labels[constants.TAG_RAY_NODE_KIND] == 'head'

    def _terminate_pod_thread(pod_info):
        pod_name, pod = pod_info
        if _is_head(pod) and worker_only:
            return
        logger.debug(f'Terminating instance {pod_name}: {pod}')
        _terminate_node(namespace, context, pod_name)

    # Run pod termination in parallel
    subprocess_utils.run_in_parallel(_terminate_pod_thread, list(pods.items()),
                                     _NUM_THREADS)


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
