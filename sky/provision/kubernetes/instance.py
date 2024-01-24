"""RunPod instance provisioning."""
import copy
import time
from typing import Any, Dict, List, Optional
import uuid

from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
from sky import status_lib
from sky.adaptors import kubernetes
from sky.provision import common
from sky.provision.kubernetes import config as config_lib
from sky.provision.kubernetes import kubernetes_utils
from sky.utils import common_utils
from sky.utils import kubernetes_enums
from sky.utils import ux_utils

POLL_INTERVAL = 5

logger = sky_logging.init_logger(__name__)
TAG_RAY_CLUSTER_NAME = 'ray-cluster-name'
TAG_SKYPILOT_CLUSTER_NAME = 'skypilot-cluster-name'
TAG_RAY_NODE_KIND = 'ray-node-type'  # legacy tag for backward compatibility

POD_STATUSES = {
    'Pending', 'Running', 'Succeeded', 'Failed', 'Unknown', 'Terminating'
}


def to_label_selector(tags):
    label_selector = ''
    for k, v in tags.items():
        if label_selector != '':
            label_selector += ','
        label_selector += '{}={}'.format(k, v)
    return label_selector


def _get_namespace(provider_config: Dict[str, Any]) -> str:
    return provider_config.get(
        'namespace',
        kubernetes_utils.get_current_kube_config_context_namespace())


def _filter_pods(namespace: str, tag_filters: Dict[str, str],
                 status_filters: Optional[List[str]]) -> Dict[str, Any]:
    """Filters pods by tags and status."""
    non_included_pod_statuses = POD_STATUSES.copy()

    field_selector = ''
    if status_filters is not None:
        non_included_pod_statuses -= set(status_filters)
        field_selector = ','.join(
            [f'status.phase!={status}' for status in non_included_pod_statuses])

    label_selector = to_label_selector(tag_filters)
    pod_list = kubernetes.core_api().list_namespaced_pod(
        namespace, field_selector=field_selector, label_selector=label_selector)

    # Don't return pods marked for deletion,
    # i.e. pods with non-null metadata.DeletionTimestamp.
    pods = [
        pod for pod in pod_list.items if pod.metadata.deletion_timestamp is None
    ]
    return {pod.metadata.name: pod for pod in pods}


def _get_head_pod_name(pods: Dict[str, Any]) -> Optional[str]:
    head_pod_name = None
    for pod_name, pod in pods.items():
        if pod.metadata.labels[TAG_RAY_NODE_KIND] == 'head':
            head_pod_name = pod_name
            break
    return head_pod_name


def head_service_selector(cluster_name: str) -> Dict[str, str]:
    """Selector for Operator-configured head service."""
    return {'component': f'{cluster_name}-head'}


def _raise_pod_scheduling_errors(namespace, new_nodes):
    """Raise pod scheduling failure reason.

    When a pod fails to schedule in Kubernetes, the reasons for the failure
    are recorded as events. This function retrieves those events and raises
    descriptive errors for better debugging and user feedback.
    """
    for new_node in new_nodes:
        pod = kubernetes.core_api().read_namespaced_pod(new_node.metadata.name,
                                                        namespace)
        pod_status = pod.status.phase
        # When there are multiple pods involved while launching instance,
        # there may be a single pod causing issue while others are
        # successfully scheduled. In this case, we make sure to not surface
        # the error message from the pod that is already scheduled.
        if pod_status != 'Pending':
            continue
        pod_name = pod._metadata._name  # pylint: disable=protected-access
        events = kubernetes.core_api().list_namespaced_event(
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
        lack_resource_msg = (
            'Insufficient {resource} capacity on the cluster. '
            'Other SkyPilot tasks or pods may be using resources. '
            'Check resource usage by running `kubectl describe nodes`.')
        if event_message is not None:
            if pod_status == 'Pending':
                if 'Insufficient cpu' in event_message:
                    raise config_lib.KubernetesError(
                        lack_resource_msg.format(resource='CPU'))
                if 'Insufficient memory' in event_message:
                    raise config_lib.KubernetesError(
                        lack_resource_msg.format(resource='memory'))
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
                                msg = lack_resource_msg.format(resource='GPU')
                                raise config_lib.KubernetesError(
                                    f'{msg} Verify if '
                                    f'{pod.spec.node_selector[label_key]}'
                                    ' is available in the cluster.')
            raise config_lib.KubernetesError(f'{timeout_err_msg} '
                                             f'Pod status: {pod_status}'
                                             f'Details: \'{event_message}\' ')
    raise config_lib.KubernetesError(f'{timeout_err_msg}')


def _wait_for_pods_to_schedule(namespace, new_nodes, timeout: int):
    """Wait for all pods to be scheduled.

    Wait for all pods including jump pod to be scheduled, and if it
    exceeds the timeout, raise an exception. If pod's container
    is ContainerCreating, then we can assume that resources have been
    allocated and we can exit.
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        all_pods_scheduled = True
        for node in new_nodes:
            # Iterate over each pod to check their status
            pod = kubernetes.core_api().read_namespaced_pod(
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
        _raise_pod_scheduling_errors(namespace, new_nodes)
    except config_lib.KubernetesError:
        raise
    except Exception as e:
        raise config_lib.KubernetesError(
            'An error occurred while trying to fetch the reason '
            'for pod scheduling failure. '
            f'Error: {common_utils.format_exception(e)}') from None


def _wait_for_pods_to_run(namespace, new_nodes):
    """Wait for pods and their containers to be ready.

    Pods may be pulling images or may be in the process of container
    creation.
    """
    while True:
        all_pods_running = True
        # Iterate over each pod to check their status
        for node in new_nodes:
            pod = kubernetes.core_api().read_namespaced_pod(
                node.metadata.name, namespace)

            # Continue if pod and all the containers within the
            # pod are succesfully created and running.
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
                    if (waiting is not None and
                            waiting.reason != 'ContainerCreating'):
                        raise config_lib.KubernetesError(
                            'Failed to create container while launching '
                            'the node. Error details: '
                            f'{container_status.state.waiting.message}.')
            # Reaching this point means that one of the pods had an issue,
            # so break out of the loop
            break

        if all_pods_running:
            break
        time.sleep(1)


def _set_env_vars_in_pods(namespace: str, new_pods: List):
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
    set_k8s_env_var_cmd = [
        '/bin/sh', '-c',
        ('printenv | awk -F "=" \'{print "export " $1 "=\\047" $2 "\\047"}\' > '
         '~/k8s_env_var.sh && '
         'mv ~/k8s_env_var.sh /etc/profile.d/k8s_env_var.sh || '
         'sudo mv ~/k8s_env_var.sh /etc/profile.d/k8s_env_var.sh')
    ]

    for new_pod in new_pods:
        kubernetes.stream()(
            kubernetes.core_api().connect_get_namespaced_pod_exec,
            new_pod.metadata.name,
            namespace,
            command=set_k8s_env_var_cmd,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
            _request_timeout=kubernetes.API_TIMEOUT)


def run_instances(region: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Runs instances for the given cluster."""
    provider_config = config.provider_config
    namespace = _get_namespace(provider_config)
    conf = copy.deepcopy(config.node_config)
    pod_spec = conf.get('pod', conf)
    service_spec = conf.get('service')
    node_uuid = str(uuid.uuid4())
    tags = {
        TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud,
        TAG_SKYPILOT_CLUSTER_NAME: cluster_name_on_cloud,
    }
    tags[TAG_RAY_CLUSTER_NAME] = cluster_name_on_cloud
    pod_spec['metadata']['namespace'] = namespace
    if 'labels' in pod_spec['metadata']:
        pod_spec['metadata']['labels'].update(tags)
    else:
        pod_spec['metadata']['labels'] = tags

    running_pods = _filter_pods(namespace, tags, ['Pending', 'Running'])
    head_pod_name = _get_head_pod_name(running_pods)

    to_start_count = config.count - len(running_pods)
    if to_start_count < 0:
        raise RuntimeError(
            'The number of running+pending pods '
            f'({config.count - to_start_count}) in cluster '
            f'"{cluster_name_on_cloud}" is greater than the number '
            f'requested by the user ({config.count}). '
            'This is likely a resource leak. '
            'Use "sky down" to terminate the cluster.')

    created_pods = {}
    for _ in range(to_start_count):
        if head_pod_name is None:
            pod_spec['metadata']['labels'][TAG_RAY_NODE_KIND] = 'head'
            head_selector = head_service_selector(cluster_name_on_cloud)
            pod_spec['metadata']['labels'].update(head_selector)
            pod_spec['metadata']['name'] = f'{cluster_name_on_cloud}-head'

        else:
            pod_spec['metadata']['labels'][TAG_RAY_NODE_KIND] = 'worker'
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
                    'requiredDuringSchedulingIgnoredDuringExecution': [{
                        'labelSelector': {
                            'matchExpressions': [{
                                'key': TAG_SKYPILOT_CLUSTER_NAME,
                                'operator': 'In',
                                'values': [cluster_name_on_cloud]
                            }]
                        },
                        'topologyKey': 'kubernetes.io/hostname'
                    }]
                }
            }
        pod = kubernetes.core_api().create_namespaced_pod(namespace, pod_spec)
        created_pods[pod.metadata.name] = pod
        if head_pod_name is None:
            head_pod_name = pod.metadata.name

    if to_start_count > 0:
        new_svcs = []
        if service_spec is not None:
            logger.info('run_instances: calling create_namespaced_service '
                        '(count={}).'.format(to_start_count))

            for new_node in created_pods:
                metadata = service_spec.get('metadata', {})
                metadata['name'] = new_node
                service_spec['metadata'] = metadata
                service_spec['spec']['selector'] = {'ray-node-uuid': node_uuid}
                svc = kubernetes.core_api().create_namespaced_service(
                    namespace, service_spec)
                new_svcs.append(svc)

        # Adding the jump pod to the new_nodes list as well so it can be
        # checked if it's scheduled and running along with other pods.
        ssh_jump_pod_name = conf['metadata']['labels']['skypilot-ssh-jump']
        jump_pod = kubernetes.core_api().read_namespaced_pod(
            ssh_jump_pod_name, namespace)
        wait_pods = list(created_pods.values())
        wait_pods.append(jump_pod)

        # Wait until the pods are scheduled and surface cause for error
        # if there is one
        _wait_for_pods_to_schedule(namespace, wait_pods,
                                   provider_config['timeout'])
        # Wait until the pods and their containers are up and running, and
        # fail early if there is an error
        _wait_for_pods_to_run(namespace, wait_pods)
        _set_env_vars_in_pods(namespace, wait_pods)

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


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    del region, cluster_name_on_cloud, state


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    raise NotImplementedError()


def _terminate_node(namespace: str, pod_name: str) -> None:
    """Terminate a pod."""
    logger.debug('terminate_instances: calling delete_namespaced_pod')
    try:
        kubernetes_utils.clean_zombie_ssh_jump_pod(namespace, pod_name)
    except Exception as e:  # pylint: disable=broad-except
        logger.warning('terminate_instances: Error occurred when analyzing '
                       f'SSH Jump pod: {e}')
    try:
        kubernetes.core_api().delete_namespaced_service(
            pod_name, namespace, _request_timeout=config_lib.DELETION_TIMEOUT)
        kubernetes.core_api().delete_namespaced_service(
            f'{pod_name}-ssh',
            namespace,
            _request_timeout=config_lib.DELETION_TIMEOUT)
    except kubernetes.api_exception():
        pass
    # Note - delete pod after all other resources are deleted.
    # This is to ensure there are no leftover resources if this down is run
    # from within the pod, e.g., for autodown.
    try:
        kubernetes.core_api().delete_namespaced_pod(
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
    namespace = _get_namespace(provider_config)
    tag_filters = {
        TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud,
    }
    pods = _filter_pods(namespace, tag_filters, None)

    def _is_head(pod) -> bool:
        return pod.metadata.labels[TAG_RAY_NODE_KIND] == 'head'

    for pod_name, pod in pods.items():
        logger.debug(f'Terminating instance {pod_name}: {pod}')
        if _is_head(pod) and worker_only:
            continue
        _terminate_node(namespace, pod_name)


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    del region  # unused
    assert provider_config is not None
    namespace = _get_namespace(provider_config)
    tag_filters = {
        TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud,
    }

    running_pods = _filter_pods(namespace, tag_filters, ['Running'])
    pods: Dict[str, List[common.InstanceInfo]] = {}
    head_pod_name = None

    port_forward_mode = kubernetes_enums.KubernetesNetworkingMode.PORTFORWARD
    network_mode_str = skypilot_config.get_nested(('kubernetes', 'networking'),
                                                  port_forward_mode.value)
    network_mode = kubernetes_enums.KubernetesNetworkingMode.from_str(
        network_mode_str)
    external_ip = kubernetes_utils.get_external_ip(network_mode)
    for pod_name, pod in running_pods.items():
        pods[pod_name] = [
            common.InstanceInfo(
                instance_id=pod_name,
                internal_ip=pod.status.pod_ip,
                external_ip=external_ip,
                ssh_port=kubernetes_utils.get_head_ssh_port(
                    cluster_name_on_cloud, namespace),
                tags=pod.metadata.labels,
            )
        ]
        if pod.metadata.labels[TAG_RAY_NODE_KIND] == 'head':
            head_pod_name = pod_name

    return common.ClusterInfo(
        instances=pods,
        head_instance_id=head_pod_name,
    )


def query_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True
) -> Dict[str, Optional[status_lib.ClusterStatus]]:
    del provider_config  # unused
    status_map = {
        'Pending': status_lib.ClusterStatus.INIT,
        'Running': status_lib.ClusterStatus.UP,
        'Failed': None,
        'Unknown': None,
        'Succeeded': None,
        'Terminating': None,
    }

    namespace = kubernetes_utils.get_current_kube_config_context_namespace()

    # Get all the pods with the label skypilot-cluster: <cluster_name>
    try:
        pods = kubernetes.core_api().list_namespaced_pod(
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
