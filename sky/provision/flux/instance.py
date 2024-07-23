"""Flux MiniCluster provisioning."""
import copy
import time
from typing import Any, Dict, Optional

from kubernetes.client import ApiException
from sky import exceptions
from sky import sky_logging
from sky import status_lib
from sky.adaptors import kubernetes
from sky.provision import common
from sky.provision.kubernetes import network_utils
from sky.provision.kubernetes import config as config_lib
from sky.provision.kubernetes import utils as kubernetes_utils
import sky.provision.kubernetes.instance as instance_utils
from sky.utils import common_utils
from sky.utils import kubernetes_enums
from sky.utils import subprocess_utils
from sky.utils import ux_utils

POLL_INTERVAL = 2

# Flux Operator install
# TODO(vsoch): could someone want arm support here?
FLUX_OPERATOR_INSTALL = (
    'https://raw.githubusercontent.com/' +
    'flux-framework/flux-operator/' +
    'main/examples/dist/flux-operator.yaml'
)

# Flux Operator Metadata
_FLUX_OPERATOR_GROUP='flux-framework.org'
_FLUX_OPERATOR_VERSION='v1alpha2'
_MINICLUSTER_PLURAL='miniclusters'
_MINICLUSTER_KIND='MiniCluster'
_FLUX_OPERATOR_API_VERSION=f'{_FLUX_OPERATOR_GROUP}/{_FLUX_OPERATOR_VERSION}'

logger = sky_logging.init_logger(__name__)
TAG_RAY_CLUSTER_NAME = 'ray-cluster-name'
TAG_SKYPILOT_CLUSTER_NAME = 'skypilot-cluster-name'
TAG_POD_INITIALIZED = 'skypilot-initialized'

# Identifier in indexed job for "head"
_HEAD_INDEX_LABEL = 'job-index'
_HEAD_INDEX = '0'

POD_STATUSES = {
    'Pending', 'Running', 'Succeeded', 'Failed', 'Unknown', 'Terminating'
}

def _run_kubectl(command: str, args: str):
    """Run a kubectl command, check return"""
    p = subprocess_utils.run(f'kubectl {command} {args}')
    assert p.returncode == 0

def _get_namespace(provider_config: Dict[str, Any]) -> str:
    return provider_config.get(
        'namespace',
        kubernetes_utils.get_current_kube_config_context_namespace())


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
    pods = instance_utils.filter_pods(namespace, tag_filters, None)

    def _is_head(pod) -> bool:
        return pod.metadata.labels[_HEAD_INDEX_LABEL] == _HEAD_INDEX

    for pod_name, pod in pods.items():
        logger.debug(f'Terminating instance {pod_name}: {pod}')
        if _is_head(pod) and worker_only:
            continue
        _terminate_node(namespace, pod_name)


def _ensure_flux_operator_installed():
    """Check that the Flux Operator is installed, install if not."""
    # The flux operator lives in the operator-system namespace
    ns = 'operator-system'
    selector = 'control-plane=controller-manager'
    pod_list = kubernetes.core_api().list_namespaced_pod(
        ns, label_selector=selector)

    # If we have items, the operator is installed
    if pod_list.items:
        return

    # Easiest means to interact with complex apply is with subprocess
    _run_kubectl('apply', f'-f {FLUX_OPERATOR_INSTALL}')

    # Wait for pods to be running
    _run_kubectl('wait', f'-n {ns} --for condition=Ready pod -l {selector}')


def head_service_selector(cluster_name: str) -> Dict[str, str]:
    """Selector for Operator-configured head service."""
    return {'component': f'{cluster_name}-head'}


def _raise_command_running_error(message: str, command: str, pod_name: str,
                                 rc: int, stdout: str) -> None:
    if rc == 0:
        return
    raise config_lib.KubernetesError(
        f'Failed to {message} for pod {pod_name} with return '
        f'code {rc}: {command!r}\nOutput: {stdout}.')


def _create_minicluster(region: str, cluster_name_on_cloud: str,
                 config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Create pods based on the config."""
    provider_config = config.provider_config
    namespace = _get_namespace(provider_config)
    pod_spec = copy.deepcopy(config.node_config)
    tags = {
        TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud,
    }

    # No support for jump pod - ssh directly into the lead broker!
    mode = config.provider_config.get('networking_mode')
    networking_mode = network_utils.get_networking_mode(mode)
    if networking_mode == kubernetes_enums.KubernetesNetworkingMode.NODEPORT:
        raise ValueError('run_instances: node with jump pod is not supported')

    # Here we need to convert the provider config into a MiniCluster config
    # We use the custom objects api
    crd_api = kubernetes.custom_objects_api()

    # First see if we have an existing cluster of this name
    try:
        crd_api.get_namespaced_custom_object(
            name=cluster_name_on_cloud,
            group=_FLUX_OPERATOR_GROUP,
            version=_FLUX_OPERATOR_VERSION,
            namespace=namespace,
            plural=_MINICLUSTER_PLURAL,
        )
        # Assume for now we want to delete it. We could eventually
        # just change the size (update it) but I need to be more
        # familiar with skypilot first.
        logger.warning('run_instances: Cleaning up existing MiniCluster.')
        crd_api.delete_namespaced_custom_object(
            group=_FLUX_OPERATOR_GROUP,
            version=_FLUX_OPERATOR_VERSION,
            namespace=namespace,
            plural=_MINICLUSTER_PLURAL,
            name=cluster_name_on_cloud,
        )
    except ApiException as exc:
        if exc.status != 404:
            raise exc

    # Always wait for any potentially terminating pods
    instance_utils.wait_for_termination(namespace, tags)

    # Volumes are shared by the pod, create a lookup for containers to see here
    volumes = {}
    for volume in pod_spec['spec'].get('volumes', []):
        volumes[volume['name']] = volume

    # Here is our main container for the MiniCluster
    containers = []
    run_flux = True
    for i, container in enumerate(pod_spec['spec']['containers']):
        if i > 0:
            run_flux = False

        # Prepare volume mounts for the container
        mounts = {}
        for mount in container['volumeMounts']:
            volume = volumes[mount['name']]
            if 'secret' in volume:
                mounts[volume['name']] = {
                    'secretName': volume['secret']['secretName'],
                    'path': mount['mountPath'],
                }

            # Assuming for now emptyDir always means memory, so will have Medium
            elif 'emptyDir' in volume:
                mounts[volume['name']] = {
                    'emptyDir': True,
                    'emptyDirMedium': volume['emptyDir']['medium'],
                    'path': mount['mountPath'],
                }

            elif 'hostPath' in volume:
                mounts[volume['name']] = {'path': volume['hostPath']['path']}

        # Container ports
        ports = []
        for port in container['ports']:
            ports.append(port['containerPort'])

        # Prepare the container for the set. It looks like the pull policy is
        # if not present, let's leave the default and not set here. Note
        # That we don't need a command to keep it running with interactive
        containers.append({
            'image': container['image'],
            'run_flux': run_flux,
            'volumes': mounts,
            'ports': ports,
            'resources': container['resources'],
        })


    # Prepare labels for the podspec
    # Note that I'm not adding constants.HEAD_NODE_TAGS, and
    # constants.WORKER_NODE_TAGS - no quick way with indexed job.
    labels = pod_spec['metadata'].get('labels') or {}
    labels.update(tags)
    labels.update({TAG_SKYPILOT_CLUSTER_NAME: cluster_name_on_cloud})

    # flux view image must match container with sky
    view_image = 'ghcr.io/converged-computing/flux-view-ubuntu:tag-focal'

    # minicluster spec inherits from the pod_spec
    # If we are updating one (in terms of size) it will update
    # We could also explicitly terminate / recreate, will need
    # testing. Interactive must be true to keep it running.
    spec = {
        'size': config.count,
        'containers': containers,
        'interactive': True,
        'pod': {
            'labels': labels,
            'serviceAccountName': pod_spec['spec']['serviceAccountName'],
            'restartPolicy': pod_spec['spec']['restartPolicy']
        },

        # Skypilot base images seem to be ubuntu bases (bullseye, so sid)
        'flux': {
            'container': {
                'image': view_image,
            }
        }
    }

    # Add nvidia runtime class if it exists
    if instance_utils.nvidia_runtime_exists():
        spec['pod']['runtimeClassName'] = 'nvidia'

    # Wrap the spec in the MiniCluster
    minicluster = {
        'kind':_MINICLUSTER_KIND,
        'apiVersion': _FLUX_OPERATOR_API_VERSION,
        'metadata': {
            'name': cluster_name_on_cloud,
            'namespace': namespace,
        },
        'spec': spec,
    }
    crd_api.create_namespaced_custom_object(
        group=_FLUX_OPERATOR_GROUP,
        version=_FLUX_OPERATOR_VERSION,
        namespace=namespace,
        plural=_MINICLUSTER_PLURAL,
        body=minicluster,
    )
    # Get list of pods to wait for readiness.
    # If we eventually support ssh pod, should be -0 and not -head
    ssh_name = pod_spec['metadata']['labels']['skypilot-ssh-jump']
    instance_utils.wait_pods_running(
        config, provider_config, namespace, tags, ssh_name)
    created_pods = instance_utils.initialize_pods(namespace, tags)

    # Add created pod names to config - a hack to update ssh pod later
    config.provider_config['created_pods'] = list(created_pods.keys())

    # Update the config to have the correct selector name for ssh
    return common.ProvisionRecord(
        provider_name='flux',
        region=region,
        zone=None,
        cluster_name=cluster_name_on_cloud,
        head_instance_id=cluster_name_on_cloud+'-0',
        resumed_instance_ids=[],
        created_instance_ids=list(created_pods.keys()),
    )

def run_instances(region: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Runs instances for the given cluster."""
    try:
        _ensure_flux_operator_installed()
        return _create_minicluster(region, cluster_name_on_cloud, config)
    except (kubernetes.api_exception(), config_lib.KubernetesError) as e:
        logger.warning(f'run_instances: Error occurred when creating pods: {e}')
        raise

def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    raise NotImplementedError()

def _terminate_node(namespace: str, pod_name: str) -> None:
    """Terminate a pod (node).
    In the future we likely want to decrease the size of the cluster, but here
    we are going to delete the entire thing.
    """
    logger.debug('terminate_instances: calling delete_namespaced_pod')
    crd_api = kubernetes.custom_objects_api()
    cluster_name = pod_name.rsplit('-0-', 1)[0]

    # First see if we have an existing cluster of this name
    try:
        crd_api.delete_namespaced_custom_object(
            group=_FLUX_OPERATOR_GROUP,
            version=_FLUX_OPERATOR_VERSION,
            namespace=namespace,
            plural=_MINICLUSTER_PLURAL,
            name=cluster_name,
        )
    except Exception as e:  # pylint: disable=broad-except
        logger.warning('terminate_instances: Error occurred when analyzing '
                       f'SSH Jump pod: {e}')

    tags = {TAG_RAY_CLUSTER_NAME: cluster_name}
    instance_utils.wait_for_termination(namespace, tags)
    # TODO(vsoch): do we need to add back services deletion here?

def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    del region  # unused
    assert provider_config is not None
    namespace = _get_namespace(provider_config)
    tags = {
        TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud,
    }

    # Wait until pods are running, they go from init->pod initializing->running
    running_pods = {}
    while not running_pods:
        running_pods = instance_utils.filter_pods(namespace, tags, ['Running'])
        if running_pods:
            break
        logger.warning('get_cluster_info: pods not in running state yet.')
        time.sleep(10)

    return instance_utils.query_cluster_info(
        running_pods,
        namespace,
        _HEAD_INDEX_LABEL,
        _HEAD_INDEX,
        cluster_name_on_cloud,
        provider_config,
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
