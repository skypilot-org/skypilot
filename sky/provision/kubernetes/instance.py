"""Kubernetes instance provisioning."""
import copy
import datetime
import json
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import kubernetes
from sky.provision import common
from sky.provision import constants
from sky.provision import docker_utils
from sky.provision.kubernetes import config as config_lib
from sky.provision.kubernetes import constants as k8s_constants
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.provision.kubernetes import volume
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import config_utils
from sky.utils import kubernetes_enums
from sky.utils import rich_utils
from sky.utils import status_lib
from sky.utils import subprocess_utils
from sky.utils import timeline
from sky.utils import ux_utils
from sky.utils.db import db_utils

POLL_INTERVAL = 2
_TIMEOUT_FOR_POD_TERMINATION = 60  # 1 minutes
_MAX_RETRIES = 3
_MAX_MISSING_PODS_RETRIES = 5
_MAX_QUERY_INSTANCES_RETRIES = 5
_QUERY_INSTANCES_RETRY_INTERVAL = .5
_NUM_THREADS = subprocess_utils.get_parallel_threads('kubernetes')

COMMON_NON_PENDING_EVENT_REASONS = {
    'Scheduled', 'Created', 'Started', 'Failed', 'Pulled'
}

# Pattern to extract SSH user from command output, handling MOTD contamination
_SSH_USER_PATTERN = re.compile(r'SKYPILOT_SSH_USER: ([^\s\n]+)')

logger = sky_logging.init_logger(__name__)


def ray_tag_filter(cluster_name: str) -> Dict[str, str]:
    return {k8s_constants.TAG_RAY_CLUSTER_NAME: cluster_name}


def _is_head(pod) -> bool:
    return pod.metadata.labels.get(constants.TAG_RAY_NODE_KIND) == 'head'


def _get_head_pod_name(pods: Dict[str, Any]) -> Optional[str]:
    return next((pod_name for pod_name, pod in pods.items() if _is_head(pod)),
                None)


def _get_pvc_name(cluster_name: str, volume_name: str) -> str:
    return f'{cluster_name}-{volume_name}'


def _get_deployment_name(cluster_name: str) -> str:
    return f'{cluster_name}-deployment'


def _head_service_selector(cluster_name: str) -> Dict[str, str]:
    return {'component': f'{cluster_name}-head'}


def is_high_availability_cluster_by_kubectl(
        cluster_name: str,
        context: Optional[str] = None,
        namespace: Optional[str] = None) -> bool:
    """Check if a cluster is a high availability controller by calling
    `kubectl get deployment`.

    The deployment must have the label `skypilot-cluster-name` set to
    `cluster_name`.
    """
    try:
        deployment_list = kubernetes.apps_api(
            context).list_namespaced_deployment(
                namespace,
                label_selector=
                f'{constants.TAG_SKYPILOT_CLUSTER_NAME}={cluster_name}')
    except kubernetes.api_exception():
        return False
    # It is a high availability cluster if there is at least one deployment
    # matching the label selector.
    return bool(deployment_list.items)


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
                out_of = {}
                # key: resource name, value: (extra message, nice name)
                if 'Insufficient cpu' in event_message:
                    out_of['CPU'] = (': Run \'kubectl get nodes -o '
                                     'custom-columns=NAME:.metadata.name,'
                                     'CPU:.status.allocatable.cpu\' to check '
                                     'the available CPUs on the node.', 'CPUs')
                if 'Insufficient memory' in event_message:
                    out_of['memory'] = (': Run \'kubectl get nodes -o '
                                        'custom-columns=NAME:.metadata.name,'
                                        'MEMORY:.status.allocatable.memory\' '
                                        'to check the available memory on the '
                                        'node.', 'Memory')

                # TODO(aylei): after switching from smarter-device-manager to
                # fusermount-server, we need a new way to check whether the
                # fusermount-server daemonset is ready.
                gpu_lf_keys = [
                    key for lf in kubernetes_utils.LABEL_FORMATTER_REGISTRY
                    for key in lf.get_label_keys()
                ]
                for label_key in gpu_lf_keys:
                    # TODO(romilb): We may have additional node
                    #  affinity selectors in the future - in that
                    #  case we will need to update this logic.
                    # TODO(Doyoung): Update the error message raised
                    # with the multi-host TPU support.
                    gpu_resource_key = kubernetes_utils.get_gpu_resource_key(
                        context)  # pylint: disable=line-too-long
                    if ((f'Insufficient {gpu_resource_key}' in event_message) or
                        ('didn\'t match Pod\'s node affinity/selector'
                         in event_message) and pod.spec.node_selector):
                        if 'gpu' in gpu_resource_key.lower():
                            info_msg = (
                                ': Run \'sky show-gpus --infra kubernetes\' to '
                                'see the available GPUs.')
                        else:
                            info_msg = ': '
                        if (pod.spec.node_selector and
                                label_key in pod.spec.node_selector):
                            extra_msg = (
                                f'Verify if any node matching label '
                                f'{pod.spec.node_selector[label_key]} and '
                                f'sufficient resource {gpu_resource_key} '
                                f'is available in the cluster.')
                            extra_msg = info_msg + ' ' + extra_msg
                        else:
                            extra_msg = info_msg
                        if gpu_resource_key not in out_of or len(
                                out_of[gpu_resource_key][0]) < len(extra_msg):
                            out_of[f'{gpu_resource_key}'] = (extra_msg, 'GPUs')

            if len(out_of) > 0:
                # We are out of some resources. We should raise an error.
                rsrc_err_msg = 'Insufficient resource capacity on the '
                rsrc_err_msg += 'cluster:\n'
                out_of_keys = list(out_of.keys())
                for i in range(len(out_of_keys)):
                    rsrc = out_of_keys[i]
                    (extra_msg, nice_name) = out_of[rsrc]
                    extra_msg = extra_msg if extra_msg else ''
                    if i == len(out_of_keys) - 1:
                        indent = '└──'
                    else:
                        indent = '├──'
                    rsrc_err_msg += (f'{indent} Cluster does not have '
                                     f'sufficient {nice_name} for your request'
                                     f'{extra_msg}')
                    if i != len(out_of_keys) - 1:
                        rsrc_err_msg += '\n'

                # Emit the error message without logging prefixes for better UX.
                tmp_handler = sky_logging.EnvAwareHandler(sys.stdout)
                tmp_handler.flush = sys.stdout.flush
                tmp_handler.setFormatter(sky_logging.NO_PREFIX_FORMATTER)
                tmp_handler.setLevel(sky_logging.ERROR)
                prev_propagate = logger.propagate
                try:
                    logger.addHandler(tmp_handler)
                    logger.propagate = False
                    logger.error(ux_utils.error_message(f'{rsrc_err_msg}'))
                finally:
                    logger.removeHandler(tmp_handler)
                    logger.propagate = prev_propagate
                nice_names = [out_of[rsrc][1] for rsrc in out_of_keys]
                raise config_lib.KubernetesError(
                    f'{timeout_err_msg} '
                    f'Pod status: {pod_status} '
                    f'Details: \'{event_message}\' ',
                    insufficent_resources=nice_names,
                )

            raise config_lib.KubernetesError(f'{timeout_err_msg} '
                                             f'Pod status: {pod_status} '
                                             f'Details: \'{event_message}\' ')
    raise config_lib.KubernetesError(f'{timeout_err_msg}')


def _raise_command_running_error(message: str, command: str, pod_name: str,
                                 rc: int, stdout: str) -> None:
    if rc == 0:
        return
    raise config_lib.KubernetesError(
        f'Failed to {message} for pod {pod_name} with return '
        f'code {rc}: {command!r}\nOutput: {stdout}.')


def _detect_cluster_event_reason_occurred(namespace, context, search_start,
                                          reason) -> bool:

    def _convert_to_utc(timestamp):
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=datetime.timezone.utc)
        return timestamp.astimezone(datetime.timezone.utc)

    def _get_event_timestamp(event):
        if event.last_timestamp:
            return event.last_timestamp
        elif event.metadata.creation_timestamp:
            return event.metadata.creation_timestamp
        return None

    events = kubernetes.core_api(context).list_namespaced_event(
        namespace=namespace, field_selector=f'reason={reason}')
    for event in events.items:
        ts = _get_event_timestamp(event)
        if ts and _convert_to_utc(ts) > search_start:
            return True
    return False


def _cluster_had_autoscale_event(namespace, context, search_start) -> bool:
    """Detects whether the cluster had a autoscaling event after a
    specified datetime. This only works when using cluster-autoscaler.

    Args:
        namespace: kubernetes namespace
        context: kubernetes context
        search_start (datetime.datetime): filter for events that occurred
            after search_start

    Returns:
        A boolean whether the cluster has an autoscaling event or not.
    """
    assert namespace is not None

    try:
        return _detect_cluster_event_reason_occurred(namespace, context,
                                                     search_start,
                                                     'TriggeredScaleUp')
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Error occurred while detecting cluster autoscaler: {e}')
        return False


def _cluster_maybe_autoscaling(namespace, context, search_start) -> bool:
    """Detects whether a kubernetes cluster may have an autoscaling event.

    This is not a definitive detection. FailedScheduling, which is an
    event that can occur when not enough resources are present in the cluster,
    which is a trigger for cluster autoscaling. However, FailedScheduling may
    have occurred due to other reasons (cluster itself is abnormal).

    Hence, this should only be used for autoscalers that don't emit the
    TriggeredScaleUp event, e.g.: Karpenter.

    Args:
        namespace: kubernetes namespace
        context: kubernetes context
        search_start (datetime.datetime): filter for events that occurred
            after search_start

    Returns:
        A boolean whether the cluster has an autoscaling event or not.
    """
    assert namespace is not None

    try:
        return _detect_cluster_event_reason_occurred(namespace, context,
                                                     search_start,
                                                     'FailedScheduling')
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Error occurred while detecting cluster autoscaler: {e}')
        return False


@timeline.event
def _wait_for_pods_to_schedule(namespace, context, new_nodes, timeout: int,
                               cluster_name: str,
                               create_pods_start: datetime.datetime):
    """Wait for all pods to be scheduled.

    Wait for all pods including jump pod to be scheduled, and if it
    exceeds the timeout, raise an exception. If pod's container
    is ContainerCreating, then we can assume that resources have been
    allocated and we can exit.

    If timeout is set to a negative value, this method will wait indefinitely.

    Will update the spinner message to indicate autoscaling if autoscaling
    is happening.
    """
    # Create a set of pod names we're waiting for
    if not new_nodes:
        return
    expected_pod_names = {node.metadata.name for node in new_nodes}
    start_time = time.time()

    # Variables for autoscaler detection
    autoscaler_type = skypilot_config.get_effective_region_config(
        cloud='kubernetes',
        region=context,
        keys=('autoscaler',),
        default_value=None)
    autoscaler_is_set = autoscaler_type is not None
    use_heuristic_detection = (autoscaler_is_set and
                               not kubernetes_enums.KubernetesAutoscalerType(
                                   autoscaler_type).emits_autoscale_event())
    is_autoscaling = False

    def _evaluate_timeout() -> bool:
        # If timeout is negative, retry indefinitely.
        if timeout < 0:
            return True
        return time.time() - start_time < timeout

    while _evaluate_timeout():
        # Get all pods in a single API call using the cluster name label
        # which all pods in new_nodes should share
        cluster_name_on_cloud = new_nodes[0].metadata.labels[
            constants.TAG_SKYPILOT_CLUSTER_NAME]
        pods = kubernetes.core_api(context).list_namespaced_pod(
            namespace,
            label_selector=
            f'{constants.TAG_SKYPILOT_CLUSTER_NAME}={cluster_name_on_cloud}'
        ).items

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

        # Check if cluster is autoscaling and update spinner message.
        # Minor optimization to not query k8s api after autoscaling
        # event was detected. This is useful because there isn't any
        # autoscaling complete event.
        if autoscaler_is_set and not is_autoscaling:
            if use_heuristic_detection:
                is_autoscaling = _cluster_maybe_autoscaling(
                    namespace, context, create_pods_start)
                msg = 'Kubernetes cluster may be scaling up'
            else:
                is_autoscaling = _cluster_had_autoscale_event(
                    namespace, context, create_pods_start)
                msg = 'Kubernetes cluster is autoscaling'

            if is_autoscaling:
                rich_utils.force_update_status(
                    ux_utils.spinner_message(f'Launching ({msg})',
                                             cluster_name=cluster_name))

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
def _wait_for_pods_to_run(namespace, context, cluster_name, new_pods):
    """Wait for pods and their containers to be ready.

    Pods may be pulling images or may be in the process of container
    creation.
    """
    if not new_pods:
        return

    # Create a set of pod names we're waiting for
    expected_pod_names = {pod.metadata.name for pod in new_pods}

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

    def _inspect_pod_status(pod):
        # Check if pod is terminated/preempted/failed.
        if (pod.metadata.deletion_timestamp is not None or
                pod.status.phase == 'Failed'):
            # Get the reason and write to cluster events before
            # the pod gets completely deleted from the API.
            reason = _get_pod_termination_reason(pod, cluster_name)
            logger.warning(f'Pod {pod.metadata.name} terminated: {reason}')
            raise config_lib.KubernetesError(
                f'Pod {pod.metadata.name} has terminated or failed '
                f'unexpectedly. Run `sky logs --provision {cluster_name}` '
                'for more details.')

        container_statuses = pod.status.container_statuses
        # Continue if pod and all the containers within the
        # pod are successfully created and running.
        if (pod.status.phase == 'Running' and container_statuses is not None and
                all(container.state.running
                    for container in container_statuses)):
            return True, None

        reason = None
        if pod.status.phase == 'Pending':
            pending_reason = _get_pod_pending_reason(context, namespace,
                                                     pod.metadata.name)
            if pending_reason is not None:
                reason, message = pending_reason
                logger.debug(f'Pod {pod.metadata.name} is pending: '
                             f'{reason}: {message}')

            # Iterate over each container in pod to check their status
            if container_statuses is not None:
                for container_status in container_statuses:
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
                            msg = waiting.message if (
                                waiting.message) else str(waiting)
                            raise config_lib.KubernetesError(
                                'Failed to create container while launching '
                                f'the node. Error details: {msg}.')
        return False, reason

    missing_pods_retry = 0
    while True:
        # Get all pods in a single API call
        cluster_name_on_cloud = new_pods[0].metadata.labels[
            constants.TAG_SKYPILOT_CLUSTER_NAME]
        all_pods = kubernetes.core_api(context).list_namespaced_pod(
            namespace,
            label_selector=
            f'{constants.TAG_SKYPILOT_CLUSTER_NAME}={cluster_name_on_cloud}'
        ).items

        # Get the set of found pod names and check if we have all expected pods
        found_pod_names = {pod.metadata.name for pod in all_pods}
        missing_pod_names = expected_pod_names - found_pod_names
        if missing_pod_names:
            # In _wait_for_pods_to_schedule, we already wait for all pods to go
            # from pending to scheduled. So if a pod is missing here, it means
            # something unusual must have happened, and so should be treated as
            # an exception.
            # It is also only in _wait_for_pods_to_schedule that
            # provision_timeout is used.
            # TODO(kevin): Should we take provision_timeout into account here,
            # instead of hardcoding the number of retries?
            if missing_pods_retry >= _MAX_MISSING_PODS_RETRIES:
                for pod_name in missing_pod_names:
                    reason = _get_pod_missing_reason(context, namespace,
                                                     cluster_name, pod_name)
                    logger.warning(f'Pod {pod_name} missing: {reason}')
                raise config_lib.KubernetesError(
                    f'Failed to get all pods after {missing_pods_retry} '
                    f'retries. Some pods may have been terminated or failed '
                    f'unexpectedly. Run `sky logs --provision {cluster_name}` '
                    'for more details.')
            logger.info('Retrying running pods check: '
                        f'Missing pods: {missing_pod_names}')
            time.sleep(0.5)
            missing_pods_retry += 1
            continue

        pods_to_check = [
            pod for pod in all_pods if pod.metadata.name in expected_pod_names
        ]
        pod_statuses = subprocess_utils.run_in_parallel(_inspect_pod_status,
                                                        pods_to_check,
                                                        _NUM_THREADS)

        all_pods_running = True
        pending_reasons_count = {}
        for is_running, pending_reason in pod_statuses:
            if not is_running:
                all_pods_running = False
            if pending_reason is not None:
                pending_reasons_count[pending_reason] = (
                    pending_reasons_count.get(pending_reason, 0) + 1)

        if all_pods_running:
            break

        if pending_reasons_count:
            msg = ', '.join([
                f'{count} pod(s) pending due to {reason}'
                for reason, count in pending_reasons_count.items()
            ])
            rich_utils.force_update_status(
                ux_utils.spinner_message(f'Launching ({msg})',
                                         cluster_name=cluster_name))
        else:
            rich_utils.force_update_status(
                ux_utils.spinner_message('Launching',
                                         cluster_name=cluster_name))
        time.sleep(1)


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
def _wait_for_deployment_pod(context,
                             namespace,
                             deployment,
                             timeout=300) -> List:
    label_selector = ','.join([
        f'{key}={value}'
        for key, value in deployment.spec.selector.match_labels.items()
    ])
    target_replicas = deployment.spec.replicas
    deployment_name = deployment.metadata.name
    start_time = time.time()
    while time.time() - start_time < timeout:
        # Refresh the deployment status
        deployment = kubernetes.apps_api(
            context).read_namespaced_deployment_status(deployment_name,
                                                       namespace)
        if (deployment.status and
                deployment.status.ready_replicas is not None and
                deployment.status.ready_replicas >= target_replicas):
            pods = kubernetes.core_api(context).list_namespaced_pod(
                namespace, label_selector=label_selector).items
            return pods

        ready_replicas = (deployment.status.ready_replicas
                          if deployment.status is not None else 0)
        logger.debug(f'Waiting for deployment {deployment_name!r} to be ready. '
                     f'Ready replicas: {ready_replicas}/{target_replicas}')
        time.sleep(2)

    raise TimeoutError(
        f'Timeout: Deployment {deployment_name!r} did not become '
        'ready.')


@timeline.event
def _create_pods(region: str, cluster_name: str, cluster_name_on_cloud: str,
                 config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Create pods based on the config."""
    provider_config = config.provider_config
    namespace = kubernetes_utils.get_namespace_from_config(provider_config)
    context = kubernetes_utils.get_context_from_config(provider_config)
    pod_spec = copy.deepcopy(config.node_config)
    create_pods_start = datetime.datetime.now(datetime.timezone.utc)

    to_create_deployment = 'deployment_spec' in pod_spec
    if to_create_deployment:
        deployment_spec = pod_spec.pop('deployment_spec')
        pvc_spec = pod_spec.pop('pvc_spec')
        assert len(pod_spec['spec']['containers']) == 1, (
            'Only one container is supported for deployment')

    tags = ray_tag_filter(cluster_name_on_cloud)

    pod_spec['metadata']['namespace'] = namespace
    if 'labels' in pod_spec['metadata']:
        pod_spec['metadata']['labels'].update(tags)
    else:
        pod_spec['metadata']['labels'] = tags
    pod_spec['metadata']['labels'].update(
        {constants.TAG_SKYPILOT_CLUSTER_NAME: cluster_name_on_cloud})

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
    running_pod_statuses = [{
        pod.metadata.name: pod.status.phase
    } for pod in running_pods.values()]
    logger.debug(f'Found {len(running_pods)} existing pods: '
                 f'{running_pod_statuses}')

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
            context=context)
    except kubernetes.kubernetes.client.ApiException as e:
        logger.warning('run_instances: Error occurred while checking for '
                       f'nvidia RuntimeClass - '
                       f'{common_utils.format_exception(e)}'
                       'Continuing without using nvidia RuntimeClass.\n'
                       'If you are on a K3s cluster, manually '
                       'override runtimeClassName in ~/.sky/config.yaml. '
                       'For more details, refer to https://docs.skypilot.co/en/latest/reference/config.html')  # pylint: disable=line-too-long

    needs_gpus = False
    needs_gpus_nvidia = False
    limits = pod_spec['spec']['containers'][0].get('resources',
                                                   {}).get('limits')
    if limits is not None:
        needs_gpus = limits.get(kubernetes_utils.get_gpu_resource_key(context),
                                0) > 0
        needs_gpus_nvidia = limits.get(
            kubernetes_utils.SUPPORTED_GPU_RESOURCE_KEYS['nvidia'], 0) > 0

    # TPU pods provisioned on GKE use the default containerd runtime.
    # Reference: https://cloud.google.com/kubernetes-engine/docs/how-to/migrate-containerd#overview  # pylint: disable=line-too-long
    if nvidia_runtime_exists and needs_gpus_nvidia:
        pod_spec['spec']['runtimeClassName'] = 'nvidia'

    logger.debug(f'run_instances: calling create_namespaced_pod '
                 f'(count={to_start_count}).')

    def _create_resource_thread(i: int):
        pod_spec_copy = copy.deepcopy(pod_spec)
        # 0 is for head pod, while 1+ is for worker pods.
        if i == 0:
            if head_pod_name is None:
                # First pod should be head if no head exists
                pod_spec_copy['metadata']['labels'].update(
                    constants.HEAD_NODE_TAGS)
                head_selector = _head_service_selector(cluster_name_on_cloud)
                pod_spec_copy['metadata']['labels'].update(head_selector)
                pod_spec_copy['metadata'][
                    'name'] = f'{cluster_name_on_cloud}-head'
            else:
                # If head pod already exists, we skip creating it.
                return
        else:
            # Worker pods
            pod_spec_copy['metadata']['labels'].update(
                constants.WORKER_NODE_TAGS)
            pod_name = f'{cluster_name_on_cloud}-worker{i}'
            if pod_name in running_pods:
                # If the pod is already running, we skip creating it.
                return
            pod_spec_copy['metadata']['name'] = pod_name
            pod_spec_copy['metadata']['labels']['component'] = pod_name

        # We need to keep the following fields in the pod spec to be same for
        # head and worker pods.
        # So that Kueue can merge them into a single PodSet when creating
        # ProvisioningRequest to trigger scale up of the cluster autoscaler,
        # this is especially required for DWS queued provisioning mode in GKE.
        #  spec.containers[*].resources.requests
        #  spec.initContainers[*].resources.requests
        #  spec.resources
        #  spec.nodeSelector
        #  spec.tolerations
        #  spec.affinity
        #  resourceClaims
        # Refer to the following links for more details:
        # https://cloud.google.com/kubernetes-engine/docs/how-to/provisioningrequest#define_a_provisioningrequest_object # pylint: disable=line-too-long
        # https://kueue.sigs.k8s.io/docs/admission-check-controllers/provisioning/#podset-merge-policy # pylint: disable=line-too-long
        if config.count > 1:
            # For multi-node support, we put a soft-constraint to schedule
            # worker pods on different nodes than the head pod.
            # This is not set as a hard constraint because if different nodes
            # are not available, we still want to be able to schedule worker
            # pods on larger nodes which may be able to fit multiple SkyPilot
            # "nodes".
            pod_spec_config = config_utils.Config(pod_spec_copy['spec'].get(
                'affinity', {}))
            existing_rules = pod_spec_config.get_nested(
                ('podAntiAffinity',
                 'preferredDuringSchedulingIgnoredDuringExecution'), [])
            existing_rules.append({
                # Max weight to avoid scheduling on the
                # same physical node unless necessary.
                'weight': 100,
                'podAffinityTerm': {
                    'labelSelector': {
                        'matchExpressions': [{
                            'key': constants.TAG_SKYPILOT_CLUSTER_NAME,
                            'operator': 'In',
                            'values': [cluster_name_on_cloud]
                        }]
                    },
                    'topologyKey': 'kubernetes.io/hostname'
                }
            })
            pod_spec_config.set_nested(
                ('podAntiAffinity',
                 'preferredDuringSchedulingIgnoredDuringExecution'),
                existing_rules)
            pod_spec_copy['spec']['affinity'] = pod_spec_config

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
        # Add GPU toleration if GPU is requested.
        # The nodes provisioned by DWS with flex start with queued provisioning
        # mode have the GPU taint, so we have to add the GPU toleration.
        # No need to check if DWS is enabled here since this has no side effect
        # to the non-DWS case.
        if needs_gpus:
            gpu_toleration = {
                'key': kubernetes_utils.get_gpu_resource_key(context),
                'operator': 'Exists',
                'effect': 'NoSchedule'
            }
            # Preserve existing tolerations if any
            existing_tolerations = pod_spec_copy['spec'].get('tolerations', [])
            pod_spec_copy['spec']['tolerations'] = existing_tolerations + [
                gpu_toleration
            ]

        if to_create_deployment:
            volume.create_persistent_volume_claim(namespace, context, pvc_spec)

            # It's safe to directly modify the template spec in the deployment spec
            # because controller pod is singleton, i in [0].
            template_pod_spec = deployment_spec['spec']['template']
            # Add the deployment name as a label to the pod spec
            deployment_name = deployment_spec['metadata']['name']
            pod_spec_copy['metadata']['labels'][
                k8s_constants.TAG_SKYPILOT_DEPLOYMENT_NAME] = deployment_name
            template_pod_spec['metadata'] = pod_spec_copy['metadata']
            template_pod_spec['spec'].update(pod_spec_copy['spec'])
            # Propagate the labels to the deployment for identification.
            deployment_spec['metadata']['labels'] = pod_spec_copy['metadata'][
                'labels']
            try:
                return kubernetes.apps_api(
                    context).create_namespaced_deployment(
                        namespace, deployment_spec)
            except Exception as e:
                print('Deployment failed', e)
                raise e

        # Check if any PVCs with access mode ReadWriteOnce or ReadWriteOncePod
        # is used by any pod in the namespace.
        volume.check_pvc_usage_for_pod(context, namespace, pod_spec_copy)

        return _create_namespaced_pod_with_retries(namespace, pod_spec_copy,
                                                   context)

    if not to_start_count:
        is_provisioned_cluster_ha = is_high_availability_cluster_by_kubectl(
            cluster_name_on_cloud, context, namespace)
        if is_provisioned_cluster_ha != to_create_deployment:
            ha_str = lambda x: 'high availability' if x else 'non-high availability'

            message = (
                f'The cluster "{cluster_name_on_cloud}" is configured to be '
                f'{ha_str(to_create_deployment)} but the cluster has already been '
                f'provisioned as {ha_str(is_provisioned_cluster_ha)}. '
                'If you want to make the provisioned cluster '
                f'{ha_str(to_create_deployment)}, please first down the cluster '
                'and then up the cluster again.')
            raise exceptions.InconsistentHighAvailabilityError(message)

    created_resources = []
    if to_start_count > 0:
        # Create pods in parallel.
        # Use `config.count` instead of `to_start_count` to keep the index of
        # the Pods consistent especially for the case where some Pods are down
        # due to node failure or manual termination, etc. and then launch
        # again to create the Pods back.
        # The existing Pods will be skipped in _create_resource_thread.
        created_resources = subprocess_utils.run_in_parallel(
            _create_resource_thread, list(range(config.count)), _NUM_THREADS)

    if to_create_deployment:
        deployments = copy.deepcopy(created_resources)
        pods = [
            pod for deployment in deployments
            for pod in _wait_for_deployment_pod(context, namespace, deployment)
        ]
    else:
        # If not creating deployments, 'created_resources' already holds Pod objects
        pods = created_resources

    created_pods = {}
    valid_pods = []
    for pod in pods:
        # In case Pod is not created
        if pod is None:
            continue
        valid_pods.append(pod)
        created_pods[pod.metadata.name] = pod
        if head_pod_name is None and _is_head(pod):
            head_pod_name = pod.metadata.name
    pods = valid_pods

    # The running_pods may include Pending Pods, so we add them to the pods
    # list to wait for scheduling and running
    if running_pods:
        pods = pods + list(running_pods.values())

    provision_timeout = provider_config['timeout']

    wait_str = ('indefinitely'
                if provision_timeout < 0 else f'for {provision_timeout}s')
    logger.debug(f'run_instances: waiting {wait_str} for pods to schedule and '
                 f'run: {[pod.metadata.name for pod in pods]}')

    # Wait until the pods are scheduled and surface cause for error
    # if there is one
    _wait_for_pods_to_schedule(namespace, context, pods, provision_timeout,
                               cluster_name, create_pods_start)
    # Reset spinner message here because it might have hinted autoscaling
    # while waiting for pods to schedule.
    rich_utils.force_update_status(
        ux_utils.spinner_message('Launching', cluster_name=cluster_name))
    # Wait until the pods and their containers are up and running, and
    # fail early if there is an error
    logger.debug(f'run_instances: waiting for pods to be running: '
                 f'{[pod.metadata.name for pod in pods]}')
    _wait_for_pods_to_run(namespace, context, cluster_name, pods)
    # Reset spinner message here because it might have hinted the reason
    # pods were pending.
    rich_utils.force_update_status(
        ux_utils.spinner_message('Launching', cluster_name=cluster_name))
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


def run_instances(region: str, cluster_name: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Runs instances for the given cluster."""
    try:
        return _create_pods(region, cluster_name, cluster_name_on_cloud, config)
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


def _delete_services(name_prefix: str,
                     namespace: str,
                     context: Optional[str],
                     skip_ssh_service: bool = False) -> None:
    """Delete services with the given name prefix.

    Args:
        name_prefix: Prefix of the service names to delete
        namespace: Kubernetes namespace
        context: Kubernetes context
    """
    # TODO(andy): We should use tag for the service filter.
    services = ([name_prefix, f'{name_prefix}-ssh']
                if not skip_ssh_service else [name_prefix])
    for service_name in services:
        # Since we are not saving this lambda, it's a false positive.
        # TODO(andyl): Wait for
        # https://github.com/pylint-dev/pylint/issues/5263.
        # pylint: disable=cell-var-from-loop
        kubernetes_utils.delete_k8s_resource_with_retry(
            delete_func=lambda: kubernetes.core_api(
                context).delete_namespaced_service(name=service_name,
                                                   namespace=namespace,
                                                   _request_timeout=config_lib.
                                                   DELETION_TIMEOUT),
            resource_type='service',
            resource_name=service_name)


def _terminate_node(namespace: str,
                    context: Optional[str],
                    pod_name: str,
                    is_head: bool = False) -> None:
    """Terminate a pod and its associated services."""
    logger.debug('terminate_instances: calling delete_namespaced_pod')

    if is_head:
        # Delete services for the head pod
        # services are specified in sky/templates/kubernetes-ray.yml.j2
        _delete_services(pod_name, namespace, context)
    else:
        # No ssh service is created for worker pods
        _delete_services(pod_name, namespace, context, skip_ssh_service=True)

    # Note - delete pod after all other resources are deleted.
    # This is to ensure there are no leftover resources if this down is run
    # from within the pod, e.g., for autodown.
    # Note - some misbehaving pods may not terminate gracefully if they have
    # open file descriptors. We force delete pods to avoid this.
    kubernetes_utils.delete_k8s_resource_with_retry(
        delete_func=lambda: kubernetes.core_api(context).delete_namespaced_pod(
            name=pod_name,
            namespace=namespace,
            _request_timeout=config_lib.DELETION_TIMEOUT,
            grace_period_seconds=0),
        resource_type='pod',
        resource_name=pod_name)


def _terminate_deployment(cluster_name: str, namespace: str,
                          context: Optional[str]) -> None:
    """Terminate a deployment."""
    # Delete services first
    _delete_services(f'{cluster_name}-head', namespace, context)

    # Delete deployment
    deployment_name = _get_deployment_name(cluster_name)
    kubernetes_utils.delete_k8s_resource_with_retry(
        delete_func=lambda: kubernetes.apps_api(
            context).delete_namespaced_deployment(name=deployment_name,
                                                  namespace=namespace,
                                                  _request_timeout=config_lib.
                                                  DELETION_TIMEOUT),
        resource_type='deployment',
        resource_name=deployment_name)

    # Delete PVCs
    pvc_name = _get_pvc_name(
        cluster_name,
        kubernetes_utils.HIGH_AVAILABILITY_DEPLOYMENT_VOLUME_MOUNT_NAME)
    # pylint: disable=cell-var-from-loop
    kubernetes_utils.delete_k8s_resource_with_retry(
        delete_func=lambda: kubernetes.core_api(
            context).delete_namespaced_persistent_volume_claim(
                name=pvc_name,
                namespace=namespace,
                _request_timeout=config_lib.DELETION_TIMEOUT),
        resource_type='pvc',
        resource_name=pvc_name)


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Dict[str, Any],
    worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    namespace = kubernetes_utils.get_namespace_from_config(provider_config)
    context = kubernetes_utils.get_context_from_config(provider_config)
    pods = kubernetes_utils.filter_pods(namespace, context,
                                        ray_tag_filter(cluster_name_on_cloud),
                                        None)

    if is_high_availability_cluster_by_kubectl(cluster_name_on_cloud, context,
                                               namespace):
        # For high availability controllers, terminate the deployment
        logger.debug(f'Terminating deployment {cluster_name_on_cloud}')
        _terminate_deployment(cluster_name_on_cloud, namespace, context)
        return

    def _terminate_pod_thread(pod_info):
        pod_name, pod = pod_info
        if _is_head(pod) and worker_only:
            return
        logger.debug(f'Terminating instance {pod_name}: {pod}')
        _terminate_node(namespace, context, pod_name, _is_head(pod))

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

    running_pods = kubernetes_utils.filter_pods(
        namespace, context, ray_tag_filter(cluster_name_on_cloud), ['Running'])
    logger.debug(f'Running pods: {list(running_pods.keys())}')

    pods: Dict[str, List[common.InstanceInfo]] = {}
    head_pod_name = None

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
                external_ip=None,
                ssh_port=port,
                tags=pod.metadata.labels,
                # TODO(hailong): `cluster.local` may need to be configurable
                # Service name is same as the pod name for now.
                internal_svc=f'{pod_name}.{namespace}.svc.cluster.local',
            )
        ]
        if _is_head(pod):
            head_pod_name = pod_name
            head_spec = pod.spec
            assert head_spec is not None, pod
            cpu_request = head_spec.containers[0].resources.requests['cpu']

    if cpu_request is None:
        raise RuntimeError(f'Pod {cluster_name_on_cloud}-head not found'
                           ' or not Running, check the Pod status')

    ssh_user = 'sky'
    # Use pattern matching to extract SSH user, handling MOTD contamination.
    # Some container images (like CUDA-Q) print MOTD when login shells start,
    # which can contaminate command output. We use a unique pattern to extract
    # the actual username reliably.
    get_k8s_ssh_user_cmd = 'echo "SKYPILOT_SSH_USER: $(whoami)"'
    assert head_pod_name is not None
    runner = command_runner.KubernetesCommandRunner(
        ((namespace, context), head_pod_name))
    rc, stdout, stderr = runner.run(get_k8s_ssh_user_cmd,
                                    require_outputs=True,
                                    separate_stderr=True,
                                    stream_logs=False)
    _raise_command_running_error('get ssh user', get_k8s_ssh_user_cmd,
                                 head_pod_name, rc, stdout + stderr)

    # Extract SSH user using pattern matching
    ssh_user_match = _SSH_USER_PATTERN.search(stdout)
    if ssh_user_match:
        ssh_user = ssh_user_match.group(1)
    else:
        raise ValueError('Failed to find SSH user identifier: '
                         f'{stdout + stderr}')
    logger.debug(
        f'Using ssh user {ssh_user} for cluster {cluster_name_on_cloud}')

    # cpu_request may be a string like `100m`, need to parse and convert
    num_cpus = kubernetes_utils.parse_cpu_or_gpu_resource_to_float(cpu_request)
    # 'num-cpus' for ray must be an integer, but we should not set it to 0 if
    # cpus is <1.
    # Keep consistent with the logic in clouds/kubernetes.py
    str_cpus = str(max(int(num_cpus), 1))

    return common.ClusterInfo(
        instances=pods,
        head_instance_id=head_pod_name,
        ssh_user=ssh_user,
        # We manually set object-store-memory=500000000 to avoid ray from
        # allocating a very large object store in each pod that may cause
        # problems for other pods.
        custom_ray_options={
            'object-store-memory': 500000000,
            'num-cpus': str_cpus,
        },
        provider_name='kubernetes',
        provider_config=provider_config)


def _get_pod_termination_reason(pod: Any, cluster_name: str) -> str:
    """Get pod termination reason and write to cluster events.

    Checks both pod conditions (for preemption/disruption) and
    container statuses (for exit codes/errors).
    """
    latest_timestamp = pod.status.start_time or datetime.datetime.min
    ready_state = 'Unknown'
    termination_reason = 'Terminated unexpectedly'
    container_reasons = []

    # Check pod status conditions for high level overview.
    # No need to sort, as each condition.type will only appear once.
    for condition in pod.status.conditions:
        reason = condition.reason or 'Unknown reason'
        message = condition.message or ''

        # Get last known readiness state.
        if condition.type == 'Ready':
            ready_state = f'{reason} ({message})' if message else reason
        # Kueue preemption, as defined in:
        # https://pkg.go.dev/sigs.k8s.io/kueue/pkg/controller/jobs/pod#pkg-constants
        elif condition.type == 'TerminationTarget':
            termination_reason = f'Preempted by Kueue: {reason}'
            if message:
                termination_reason += f' ({message})'
        # Generic disruption.
        elif condition.type == 'DisruptionTarget':
            termination_reason = f'Disrupted: {reason}'
            if message:
                termination_reason += f' ({message})'

        if condition.last_transition_time is not None:
            latest_timestamp = max(latest_timestamp,
                                   condition.last_transition_time)

    pod_reason = (f'{termination_reason}.\n'
                  f'Last known state: {ready_state}.')

    # Check container statuses for exit codes/errors
    if pod.status and pod.status.container_statuses:
        for container_status in pod.status.container_statuses:
            terminated = container_status.state.terminated
            if terminated:
                exit_code = terminated.exit_code
                reason = terminated.reason
                if exit_code == 0:
                    # skip exit 0 (non-failed) just for sanity
                    logger.debug(f'{pod.metadata.name}/{container_status.name} '
                                 'had exit code 0. Skipping.')
                    continue
                if reason is None:
                    # just in-case reason is None, have default for debugging
                    reason = f'exit({exit_code})'
                container_reasons.append(reason)
                latest_timestamp = max(latest_timestamp, terminated.finished_at)

            # TODO (kyuds): later, if needed, query `last_state` too.

    # Normally we will have a single container per pod for skypilot
    # but doing this just in-case there are multiple containers.
    if container_reasons:
        pod_reason += f'\nContainer errors: {" | ".join(container_reasons)}'

    global_user_state.add_cluster_event(
        cluster_name,
        None,
        f'[kubernetes pod {pod.metadata.name} terminated] {pod_reason}',
        global_user_state.ClusterEventType.DEBUG,
        transitioned_at=int(latest_timestamp.timestamp()),
    )
    return pod_reason


def _get_pod_events(context: Optional[str], namespace: str,
                    pod_name: str) -> List[Any]:
    """Get the events for a pod, sorted by timestamp, most recent first."""
    pod_field_selector = (
        f'involvedObject.kind=Pod,involvedObject.name={pod_name}')
    pod_events = kubernetes.core_api(context).list_namespaced_event(
        namespace,
        field_selector=pod_field_selector,
        _request_timeout=kubernetes.API_TIMEOUT).items
    return sorted(
        pod_events,
        key=lambda event: event.metadata.creation_timestamp,
        # latest event appears first
        reverse=True)


def _get_pod_pending_reason(context: Optional[str], namespace: str,
                            pod_name: str) -> Optional[Tuple[str, str]]:
    """Get the reason why a pod is pending from its events.

    Returns a (reason, message) tuple about why the pod is pending (e.g.,
    ("FailedMount", "hostPath type check failed")) or None if no reason found.
    """
    try:
        pod_events = _get_pod_events(context, namespace, pod_name)
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Failed to get events for pod {pod_name}: {e}')
        return None

    if not pod_events:
        return None

    for event in pod_events:
        # Omit common events that does not indicate a pending reason.
        # We could also filter by event type 'Warning' or 'Error',
        # but there might be useful 'Normal' events such as pulling
        # image that we want to surface to the user.
        if event.reason not in COMMON_NON_PENDING_EVENT_REASONS:
            reason = event.reason or 'Unknown'
            message = event.message or ''
            return reason, message

    return None


def _get_pod_missing_reason(context: Optional[str], namespace: str,
                            cluster_name: str, pod_name: str) -> Optional[str]:
    """Get events for missing pod and write to cluster events."""
    logger.debug(f'Analyzing events for pod {pod_name}')
    pod_events = _get_pod_events(context, namespace, pod_name)
    last_scheduled_node = None
    insert_new_pod_event = True
    new_event_inserted = False
    inserted_pod_events = 0

    for event in pod_events:
        if event.reason == 'Scheduled':
            pattern = r'Successfully assigned (\S+) to (\S+)'
            match = re.search(pattern, event.message)
            if match:
                scheduled_node = match.group(2)
                last_scheduled_node = scheduled_node
        if insert_new_pod_event:
            # Try inserting the latest events first. If the event is a
            # duplicate, it means the event (and any previous events) have
            # already been inserted - so do not insert further events.
            try:
                global_user_state.add_cluster_event(
                    cluster_name,
                    None, f'[kubernetes pod {pod_name}] '
                    f'{event.reason} {event.message}',
                    global_user_state.ClusterEventType.DEBUG,
                    transitioned_at=int(
                        event.metadata.creation_timestamp.timestamp()),
                    expose_duplicate_error=True)
                logger.debug(f'[pod {pod_name}] encountered new pod event: '
                             f'{event.metadata.creation_timestamp} '
                             f'{event.reason} {event.message}')
            except db_utils.UniqueConstraintViolationError:
                insert_new_pod_event = False
            else:
                new_event_inserted = True
                inserted_pod_events += 1

    logger.debug(f'[pod {pod_name}] processed {len(pod_events)} pod events and '
                 f'inserted {inserted_pod_events} new pod events '
                 'previously unseen')

    if last_scheduled_node is not None:
        node_field_selector = ('involvedObject.kind=Node,'
                               f'involvedObject.name={last_scheduled_node}')
        node_events = kubernetes.core_api(context).list_namespaced_event(
            namespace,
            field_selector=node_field_selector,
            _request_timeout=kubernetes.API_TIMEOUT).items
        node_events = sorted(
            node_events,
            key=lambda event: event.metadata.creation_timestamp,
            # latest event appears first
            reverse=True)
        insert_new_node_event = True
        inserted_node_events = 0
        for event in node_events:
            if insert_new_node_event:
                # Try inserting the latest events first. If the event is a
                # duplicate, it means the event (and any previous events) have
                # already been inserted - so do not insert further events.
                try:
                    global_user_state.add_cluster_event(
                        cluster_name,
                        None, f'[kubernetes node {last_scheduled_node}] '
                        f'{event.reason} {event.message}',
                        global_user_state.ClusterEventType.DEBUG,
                        transitioned_at=int(
                            event.metadata.creation_timestamp.timestamp()),
                        expose_duplicate_error=True)
                    logger.debug(
                        f'[pod {pod_name}] encountered new node event: '
                        f'{event.metadata.creation_timestamp} '
                        f'{event.reason} {event.message}')
                except db_utils.UniqueConstraintViolationError:
                    insert_new_node_event = False
                else:
                    new_event_inserted = True
                    inserted_node_events += 1

        logger.debug(f'[pod {pod_name}: node {last_scheduled_node}] '
                     f'processed {len(node_events)} node events and '
                     f'inserted {inserted_node_events} new node events '
                     'previously unseen')
    else:
        logger.debug(f'[pod {pod_name}] could not determine the node '
                     'the pod was scheduled to')

    if not new_event_inserted:
        # If new event is not inserted, there is no useful information to
        # return. Return None.
        return None

    # Analyze the events for failure
    failure_reason = None
    failure_decisiveness = 0

    def _record_failure_reason(reason: str, decisiveness: int):
        nonlocal failure_reason, failure_decisiveness
        if decisiveness > failure_decisiveness:
            failure_reason = reason
            failure_decisiveness = decisiveness

    cluster_events = global_user_state.get_cluster_events(
        cluster_name, None, global_user_state.ClusterEventType.DEBUG)
    for event in cluster_events:
        if event.startswith('[kubernetes pod'):
            event = event.split(']')[1].strip()
        elif event.startswith('[kubernetes node'):
            event = event.split(']')[1].strip()

        if event.startswith('NodeNotReady '):
            _record_failure_reason(event[len('NodeNotReady '):], 1)
        elif event.startswith('TaintManagerEviction '):
            # usually the event message for TaintManagerEviction is not useful
            # so we record a more generic message.
            _record_failure_reason('pod was evicted by taint manager', 2)
        elif event.startswith('DeletingNode '):
            _record_failure_reason(event[len('DeletingNode '):], 3)
    return failure_reason


def list_namespaced_pod(context: Optional[str], namespace: str,
                        cluster_name_on_cloud: str, is_ssh: bool, identity: str,
                        label_selector: str) -> List[Any]:
    # Get all the pods with the label skypilot-cluster-name: <cluster_name>
    try:
        # log the query parameters we pass to the k8s api
        logger.debug(f'Querying k8s api for pods:\n'
                     f'context: {context}\n'
                     f'namespace: {namespace}\n'
                     f'label selector:`{label_selector}`.')

        response = kubernetes.core_api(context).list_namespaced_pod(
            namespace,
            label_selector=label_selector,
            _request_timeout=kubernetes.API_TIMEOUT)

        # log PodList response info
        if sky_logging.logging_enabled(logger, sky_logging.DEBUG):
            logger.debug(f'k8s api response for `{label_selector}`:\n'
                         f'apiVersion={response.api_version}, '
                         f'kind={response.kind},\n'
                         f'metadata={response.metadata}')

        pods = response.items

        # log detailed Pod info
        if sky_logging.logging_enabled(logger, sky_logging.DEBUG):
            logger.debug(f'k8s api response for `{label_selector}`: '
                         f'len(pods)={len(pods)}')
            for pod in pods:
                logger.debug(f'k8s pod info for `{label_selector}`: '
                             f'pod.apiVersion={pod.api_version}, '
                             f'pod.kind={pod.kind}, \n'
                             f'pod.name={pod.metadata.name}, '
                             f'pod.namespace={pod.metadata.namespace}, \n'
                             f'pod.labels={pod.metadata.labels}, \n'
                             f'pod.annotations={pod.metadata.annotations}, \n'
                             'pod.creationTimestamp='
                             f'{pod.metadata.creation_timestamp}, '
                             'pod.deletionTimestamp='
                             f'{pod.metadata.deletion_timestamp}, \n'
                             f'pod.status={pod.status}')
        return pods

    except kubernetes.max_retry_error():
        with ux_utils.print_exception_no_traceback():
            if is_ssh:
                node_pool = common_utils.removeprefix(context,
                                                      'ssh-') if context else ''
                msg = (
                    f'Cannot connect to SSH Node Pool {node_pool}. '
                    'Please check if the SSH Node Pool is up and accessible. '
                    'To debug, run `sky check ssh` to check the status of '
                    'the SSH Node Pool.')
            else:
                ctx = kubernetes_utils.get_current_kube_config_context_name()
                msg = (f'Network error - check if the {identity} in '
                       f'context {ctx} is up and accessible.')
            raise exceptions.ClusterStatusFetchingError(
                f'Failed to query cluster {cluster_name_on_cloud!r} status. ' +
                msg) from None
    except Exception as e:  # pylint: disable=broad-except
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ClusterStatusFetchingError(
                f'Failed to query {identity} {cluster_name_on_cloud!r} '
                f'status: {common_utils.format_exception(e)}')


def query_instances(
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
    retry_if_missing: bool = False,
) -> Dict[str, Tuple[Optional['status_lib.ClusterStatus'], Optional[str]]]:
    # Mapping from pod phase to skypilot status. These are the only valid pod
    # phases.
    # https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#pod-phase
    status_map = {
        'Pending': status_lib.ClusterStatus.INIT,
        'Running': status_lib.ClusterStatus.UP,
        'Failed': status_lib.ClusterStatus.INIT,
        'Unknown': None,
        'Succeeded': None,
    }

    assert provider_config is not None
    namespace = kubernetes_utils.get_namespace_from_config(provider_config)
    context = kubernetes_utils.get_context_from_config(provider_config)
    is_ssh = context.startswith('ssh-') if context else False
    identity = 'SSH Node Pool' if is_ssh else 'Kubernetes cluster'
    label_selector = (f'{constants.TAG_SKYPILOT_CLUSTER_NAME}='
                      f'{cluster_name_on_cloud}')

    attempts = 0
    pods = list_namespaced_pod(context, namespace, cluster_name_on_cloud,
                               is_ssh, identity, label_selector)
    # When we see no pods returned from the k8s api, we assume the pods have
    # been terminated by the user directly and mark the cluster as terminated
    # in the global user state.
    # We add retry logic here as an attempt to mitigate a leak caused by the
    # kubernetes api returning no pods despite the pods actually existing.
    while (retry_if_missing and not pods and
           attempts < _MAX_QUERY_INSTANCES_RETRIES):
        logger.debug(f'Retrying to query k8s api for {cluster_name_on_cloud} '
                     f'{attempts}/{_MAX_QUERY_INSTANCES_RETRIES} times.'
                     f'after {_QUERY_INSTANCES_RETRY_INTERVAL} seconds.')
        time.sleep(_QUERY_INSTANCES_RETRY_INTERVAL)
        attempts += 1
        pods = list_namespaced_pod(context, namespace, cluster_name_on_cloud,
                                   is_ssh, identity, label_selector)
        if len(pods) > 0:
            logger.info(f'Found {len(pods)} pods for {label_selector} after'
                        f'{attempts} retries.')

    # Check if the pods are running or pending
    cluster_status: Dict[str, Tuple[Optional['status_lib.ClusterStatus'],
                                    Optional[str]]] = {}
    for pod in pods:
        phase = pod.status.phase
        is_terminating = pod.metadata.deletion_timestamp is not None
        pod_status = status_map[phase]
        reason = None
        if phase in ('Failed', 'Unknown') or is_terminating:
            reason = _get_pod_termination_reason(pod, cluster_name)
            logger.debug(f'Pod Status ({phase}) Reason(s): {reason}')
        if non_terminated_only and pod_status is None:
            logger.debug(f'Pod {pod.metadata.name} is terminated, but '
                         'query_instances is called with '
                         f'non_terminated_only=True. Phase: {phase}')
            continue
        pod_name = pod.metadata.name
        reason = f'{pod_name}: {reason}' if reason is not None else None
        cluster_status[pod_name] = (pod_status, reason)

    # Find the list of pod names that should be there
    # from k8s services. Filter duplicates as -ssh service
    # creates a duplicate entry.
    target_pod_names = list(
        set([
            service['spec']['selector']['component']
            for service in provider_config.get('services', [])
        ]))

    for target_pod_name in target_pod_names:
        if target_pod_name not in cluster_status:
            # If the pod is not in the cluster_status, it means it's not
            # running.
            # Analyze what happened to the pod based on events.
            reason = _get_pod_missing_reason(context, namespace, cluster_name,
                                             target_pod_name)
            reason = (f'{target_pod_name}: {reason}'
                      if reason is not None else None)
            if not non_terminated_only:
                cluster_status[target_pod_name] = (None, reason)

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

    runners: List[command_runner.CommandRunner] = []
    if cluster_info.head_instance_id is not None:
        pod_name = cluster_info.head_instance_id

        # Try to get deployment name from label first
        head_instance_info = instances[pod_name][0]
        deployment = head_instance_info.tags.get(
            k8s_constants.TAG_SKYPILOT_DEPLOYMENT_NAME)

        node_list = [((namespace, context), pod_name)]
        head_runner = command_runner.KubernetesCommandRunner(
            node_list[0], deployment=deployment, **credentials)
        runners.append(head_runner)

    node_list = [((namespace, context), pod_name)
                 for pod_name in instances.keys()
                 if pod_name != cluster_info.head_instance_id]
    runners.extend(
        command_runner.KubernetesCommandRunner.make_runner_list(
            node_list, **credentials))

    return runners
