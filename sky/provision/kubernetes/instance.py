"""Kubernetes instance provisioning."""
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
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.utils import common_utils
from sky.utils import kubernetes_enums
from sky.utils import ux_utils

POLL_INTERVAL = 2
_TIMEOUT_FOR_POD_TERMINATION = 60  # 1 minutes

logger = sky_logging.init_logger(__name__)
TAG_RAY_CLUSTER_NAME = 'ray-cluster-name'
TAG_SKYPILOT_CLUSTER_NAME = 'skypilot-cluster-name'
TAG_RAY_NODE_KIND = 'ray-node-type'  # legacy tag for backward compatibility
TAG_POD_INITIALIZED = 'skypilot-initialized'

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


def _run_command_on_pods(node_name, node_namespace, command):
    cmd_output = kubernetes.stream()(
        kubernetes.core_api().connect_get_namespaced_pod_exec,
        node_name,
        node_namespace,
        command=command,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
        _request_timeout=kubernetes.API_TIMEOUT)
    return cmd_output


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
        ('prefix_cmd() '
         '{ if [ $(id -u) -ne 0 ]; then echo "sudo"; else echo ""; fi; } && '
         'printenv | awk -F "=" \'{print "export " $1 "=\\047" $2 "\\047"}\' > '
         '~/k8s_env_var.sh && '
         'mv ~/k8s_env_var.sh /etc/profile.d/k8s_env_var.sh || '
         '$(prefix_cmd) mv ~/k8s_env_var.sh /etc/profile.d/k8s_env_var.sh')
    ]

    for new_pod in new_pods:
        _run_command_on_pods(new_pod.metadata.name, namespace,
                             set_k8s_env_var_cmd)


def _check_user_privilege(namespace: str, new_nodes: List) -> None:
    # Checks if the default user has sufficient privilege to set up
    # the kubernetes instance pod.
    check_k8s_user_sudo_cmd = [
        '/bin/sh',
        '-c',
        (
            'if [ $(id -u) -eq 0 ]; then'
            # If user is root, create an alias for sudo used in skypilot setup
            '  echo \'alias sudo=""\' >> ~/.bashrc; '
            'else '
            '  if command -v sudo >/dev/null 2>&1; then '
            '    timeout 2 sudo -l >/dev/null 2>&1 || '
            f'    ( echo {exceptions.INSUFFICIENT_PRIVILEGES_CODE!r}; ); '
            '  else '
            f'    ( echo {exceptions.INSUFFICIENT_PRIVILEGES_CODE!r}; ); '
            '  fi; '
            'fi')
    ]

    for new_node in new_nodes:
        privilege_check = _run_command_on_pods(new_node.metadata.name,
                                               namespace,
                                               check_k8s_user_sudo_cmd)
        if privilege_check == str(exceptions.INSUFFICIENT_PRIVILEGES_CODE):
            raise config_lib.KubernetesError(
                'Insufficient system privileges detected. '
                'Ensure the default user has root access or '
                '"sudo" is installed and the user is added to the sudoers '
                'from the image.')


def _setup_ssh_in_pods(namespace: str, new_nodes: List) -> None:
    # Setting up ssh for the pod instance. This is already setup for
    # the jump pod so it does not need to be run for it.
    set_k8s_ssh_cmd = [
        '/bin/sh',
        '-c',
        (
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
            '$(prefix_cmd) chmod 644 ~/.ssh/authorized_keys; '
            '$(prefix_cmd) cat /etc/secret-volume/ssh-publickey* > '
            '~/.ssh/authorized_keys; '
            '$(prefix_cmd) service ssh restart; '
            # Eliminate the error
            # `mesg: ttyname failed: inappropriate ioctl for device`.
            # See https://www.educative.io/answers/error-mesg-ttyname-failed-inappropriate-ioctl-for-device  # pylint: disable=line-too-long
            '$(prefix_cmd) sed -i "s/mesg n/tty -s \\&\\& mesg n/" ~/.profile;')
    ]
    # TODO(romilb): We need logging and surface errors here.
    for new_node in new_nodes:
        _run_command_on_pods(new_node.metadata.name, namespace, set_k8s_ssh_cmd)


def _label_pod(namespace: str, pod_name: str, label: Dict[str, str]) -> None:
    """Label a pod."""
    kubernetes.core_api().patch_namespaced_pod(
        pod_name,
        namespace, {'metadata': {
            'labels': label
        }},
        _request_timeout=kubernetes.API_TIMEOUT)


def _create_pods(region: str, cluster_name_on_cloud: str,
                 config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Create pods based on the config."""
    provider_config = config.provider_config
    namespace = _get_namespace(provider_config)
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

    terminating_pods = _filter_pods(namespace, tags, ['Terminating'])
    start_time = time.time()
    while (len(terminating_pods) > 0 and
           time.time() - start_time < _TIMEOUT_FOR_POD_TERMINATION):
        logger.debug(f'run_instances: Found {len(terminating_pods)} '
                     'terminating pods. Waiting them to finish: '
                     f'{list(terminating_pods.keys())}')
        time.sleep(POLL_INTERVAL)
        terminating_pods = _filter_pods(namespace, tags, ['Terminating'])

    if len(terminating_pods) > 0:
        # If there are still terminating pods, we force delete them.
        logger.debug(f'run_instances: Found {len(terminating_pods)} '
                     'terminating pods still in terminating state after '
                     f'timeout {_TIMEOUT_FOR_POD_TERMINATION}s. '
                     'Force deleting them.')
        for pod_name in terminating_pods.keys():
            # grace_period_seconds=0 means force delete the pod.
            # https://github.com/kubernetes-client/python/issues/508#issuecomment-1695759777
            kubernetes.core_api().delete_namespaced_pod(
                pod_name,
                namespace,
                _request_timeout=config_lib.DELETION_TIMEOUT,
                grace_period_seconds=0)

    running_pods = _filter_pods(namespace, tags, ['Pending', 'Running'])
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
        nvidia_runtime_exists = kubernetes_utils.check_nvidia_runtime_class()
    except kubernetes.get_kubernetes().client.ApiException as e:
        logger.warning('run_instances: Error occurred while checking for '
                       f'nvidia RuntimeClass - '
                       f'{common_utils.format_exception(e)}'
                       'Continuing without using nvidia RuntimeClass.\n'
                       'If you are on a K3s cluster, manually '
                       'override runtimeClassName in ~/.sky/config.yaml. '
                       'For more details, refer to https://skypilot.readthedocs.io/en/latest/reference/config.html')  # pylint: disable=line-too-long

    if nvidia_runtime_exists:
        pod_spec['spec']['runtimeClassName'] = 'nvidia'

    created_pods = {}
    logger.debug(f'run_instances: calling create_namespaced_pod '
                 f'(count={to_start_count}).')
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

    # Adding the jump pod to the new_nodes list as well so it can be
    # checked if it's scheduled and running along with other pods.
    ssh_jump_pod_name = pod_spec['metadata']['labels']['skypilot-ssh-jump']
    jump_pod = kubernetes.core_api().read_namespaced_pod(
        ssh_jump_pod_name, namespace)
    wait_pods_dict = _filter_pods(namespace, tags, ['Pending'])
    wait_pods = list(wait_pods_dict.values())
    wait_pods.append(jump_pod)
    logger.debug('run_instances: waiting for pods to schedule and run: '
                 f'{list(wait_pods_dict.keys())}')

    # Wait until the pods are scheduled and surface cause for error
    # if there is one
    _wait_for_pods_to_schedule(namespace, wait_pods, provider_config['timeout'])
    # Wait until the pods and their containers are up and running, and
    # fail early if there is an error
    _wait_for_pods_to_run(namespace, wait_pods)
    logger.debug(f'run_instances: all pods are scheduled and running: '
                 f'{list(wait_pods_dict.keys())}')

    running_pods = _filter_pods(namespace, tags, ['Running'])
    initialized_pods = _filter_pods(namespace, {
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
        _check_user_privilege(namespace, uninitialized_pods_list)
        _setup_ssh_in_pods(namespace, uninitialized_pods_list)
        _set_env_vars_in_pods(namespace, uninitialized_pods_list)
        for pod in uninitialized_pods.values():
            _label_pod(namespace,
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
        logger.warning(f'run_instances: Error occurred when creating pods: {e}')
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
    port = 22
    if not provider_config.get('use_internal_ips', False):
        port = kubernetes_utils.get_head_ssh_port(cluster_name_on_cloud,
                                                  namespace)

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
        if pod.metadata.labels[TAG_RAY_NODE_KIND] == 'head':
            head_pod_name = pod_name
            head_spec = pod.spec
            assert head_spec is not None, pod
            cpu_request = head_spec.containers[0].resources.requests['cpu']

    assert cpu_request is not None, 'cpu_request should not be None'

    ssh_user = 'sky'
    get_k8s_ssh_user_cmd = ['/bin/sh', '-c', ('echo $(whoami)')]
    assert head_pod_name is not None
    ssh_user = _run_command_on_pods(head_pod_name, namespace,
                                    get_k8s_ssh_user_cmd)
    ssh_user = ssh_user.strip()
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
        })


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
