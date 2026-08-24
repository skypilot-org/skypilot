"""Lium instance provisioning."""

from typing import Any, Dict, List, Optional, Tuple

from sky import sky_logging
from sky.provision import common
from sky.provision.lium import lium_utils
from sky.utils import status_lib

logger = sky_logging.init_logger(__name__)

# A Lium pod pulls its image on the node it runs on, which is slower than a
# cloud boot volume.
POD_READY_TIMEOUT = 1800


def _head_pod_id(pods: Dict[str, lium_utils.LiumPod]) -> Optional[str]:
    for pod_id, pod in pods.items():
        if pod.name.endswith('-head'):
            return pod_id
    return None


def run_instances(region: str, cluster_name: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Rents the nodes of a cluster."""
    del cluster_name  # We name the pods after cluster_name_on_cloud.
    if config.count > 1:
        raise RuntimeError('Lium supports single-node clusters only.')

    pods = lium_utils.cluster_pods(cluster_name_on_cloud)
    head_pod_id = _head_pod_id(pods)
    if head_pod_id is not None:
        logger.info(f'Cluster {cluster_name_on_cloud} already has a head pod.')
        return common.ProvisionRecord(provider_name='lium',
                                      cluster_name=cluster_name_on_cloud,
                                      region=region,
                                      zone=None,
                                      head_instance_id=head_pod_id,
                                      resumed_instance_ids=[],
                                      created_instance_ids=[])

    node_config = config.node_config
    instance_type = node_config['InstanceType']
    node = lium_utils.find_node(instance_type, region)
    if node is None:
        raise RuntimeError(f'No free Lium node offers {instance_type} in '
                           f'region {region}.')

    pod_name = f'{cluster_name_on_cloud}-head'
    logger.info(f'Renting node {node.id} ({instance_type}) as {pod_name}.')
    pod_id = lium_utils.rent_node(node, pod_name, node_config['PublicKey'])

    if lium_utils.wait_pod_ready(pod_id, POD_READY_TIMEOUT) is None:
        raise RuntimeError(f'Pod {pod_id} did not become ready in '
                           f'{POD_READY_TIMEOUT}s.')

    return common.ProvisionRecord(provider_name='lium',
                                  cluster_name=cluster_name_on_cloud,
                                  region=region,
                                  zone=None,
                                  head_instance_id=pod_id,
                                  resumed_instance_ids=[],
                                  created_instance_ids=[pod_id])


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    """Waits for the pods to reach a state."""
    del region, cluster_name_on_cloud, state  # unused
    # run_instances already waits for the pod to run.


def stop_instances(cluster_name_on_cloud: str,
                   provider_config: Optional[Dict[str, Any]] = None,
                   worker_only: bool = False) -> None:
    """Stops the pods of a cluster."""
    del cluster_name_on_cloud, provider_config, worker_only  # unused
    raise NotImplementedError('Lium does not support stopping a pod.')


def terminate_instances(cluster_name_on_cloud: str,
                        provider_config: Optional[Dict[str, Any]] = None,
                        worker_only: bool = False) -> None:
    """Deletes the pods of a cluster."""
    del provider_config  # unused
    if worker_only:
        # A Lium cluster is one head pod, so there is no worker to delete.
        return

    pods = lium_utils.cluster_pods(cluster_name_on_cloud)
    for pod_id, pod in pods.items():
        logger.info(f'Deleting pod {pod_id} ({pod.name}).')
        lium_utils.terminate_pod(pod_id)


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    """Returns the SSH endpoints of a cluster."""
    del region  # unused
    # A pod without an SSH endpoint is not reachable yet, so it is left out.
    pods = {
        pod_id: pod for pod_id, pod in lium_utils.cluster_pods(
            cluster_name_on_cloud).items() if pod.host is not None
    }
    instances: Dict[str, List[common.InstanceInfo]] = {
        pod_id: [
            common.InstanceInfo(instance_id=pod_id,
                                internal_ip=pod.host,
                                external_ip=pod.host,
                                ssh_port=pod.ssh_port,
                                tags={},
                                node_name=pod.name)
        ] for pod_id, pod in pods.items()
    }

    return common.ClusterInfo(instances=instances,
                              head_instance_id=_head_pod_id(pods),
                              provider_name='lium',
                              provider_config=provider_config,
                              ssh_user='root')


def query_instances(
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
) -> Dict[str, Tuple[Optional['status_lib.ClusterStatus'], Optional[str]]]:
    """Returns the state of the pods of a cluster."""
    del cluster_name, provider_config  # unused
    statuses: Dict[str, Tuple[Optional[status_lib.ClusterStatus],
                              Optional[str]]] = {}
    for pod_id, pod in lium_utils.cluster_pods(cluster_name_on_cloud).items():
        pod_status = pod.status.upper()
        cluster_status = lium_utils.POD_STATUS_MAP.get(
            pod_status, status_lib.ClusterStatus.INIT)
        if non_terminated_only and cluster_status is None:
            continue
        statuses[pod_id] = (cluster_status, pod_status)
    return statuses


def open_ports(cluster_name_on_cloud: str,
               ports: List[str],
               provider_config: Optional[Dict[str, Any]] = None) -> None:
    """Opens ports on a cluster."""
    del cluster_name_on_cloud, ports, provider_config  # unused
    raise NotImplementedError('Lium maps its ports when the pod starts.')


def cleanup_ports(cluster_name_on_cloud: str,
                  ports: List[str],
                  provider_config: Optional[Dict[str, Any]] = None) -> None:
    """Cleans up the ports of a cluster."""
    del cluster_name_on_cloud, ports, provider_config  # unused
    # Lium deletes the port mappings with the pod.
