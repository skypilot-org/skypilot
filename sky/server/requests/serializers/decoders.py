"""Decoders for the REST API return values."""
import base64
import pickle
import typing
from typing import Any, Dict, List, Optional, Tuple

from sky import jobs as managed_jobs
from sky import models
from sky.catalog import common
from sky.data import storage
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.serve import serve_state
from sky.server import constants as server_constants
from sky.skylet import job_lib
from sky.utils import status_lib

if typing.TYPE_CHECKING:
    from sky import backends
    from sky import clouds

handlers: Dict[str, Any] = {}


def decode_and_unpickle(obj: str) -> Any:
    return pickle.loads(base64.b64decode(obj.encode('utf-8')))


def register_decoders(*names: str):
    """Decorator to register a decoder."""

    def decorator(func):
        for name in names:
            if name != server_constants.DEFAULT_HANDLER_NAME:
                name = server_constants.REQUEST_NAME_PREFIX + name
            handlers[name] = func
        return func

    return decorator


def get_decoder(name: str):
    """Get the decoder for a request name name."""
    return handlers.get(name, handlers[server_constants.DEFAULT_HANDLER_NAME])


@register_decoders(server_constants.DEFAULT_HANDLER_NAME)
def default_decode_handler(return_value: Any) -> Any:
    """The default handler."""
    return return_value


@register_decoders('status')
def decode_status(return_value: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    clusters = return_value
    for cluster in clusters:
        cluster['handle'] = decode_and_unpickle(cluster['handle'])
        cluster['status'] = status_lib.ClusterStatus(cluster['status'])

    return clusters


@register_decoders('status_kubernetes')
def decode_status_kubernetes(
    return_value: Tuple[List[Dict[str, Any]], List[Dict[str, Any]],
                        List[Dict[str, Any]], Optional[str]]
) -> Tuple[List[kubernetes_utils.KubernetesSkyPilotClusterInfoPayload],
           List[kubernetes_utils.KubernetesSkyPilotClusterInfoPayload],
           List[Dict[str, Any]], Optional[str]]:
    (encoded_all_clusters, encoded_unmanaged_clusters, all_jobs,
     context) = return_value
    all_clusters = []
    for cluster in encoded_all_clusters:
        cluster['status'] = status_lib.ClusterStatus(cluster['status'])
        all_clusters.append(
            kubernetes_utils.KubernetesSkyPilotClusterInfoPayload(**cluster))
    unmanaged_clusters = []
    for cluster in encoded_unmanaged_clusters:
        cluster['status'] = status_lib.ClusterStatus(cluster['status'])
        unmanaged_clusters.append(
            kubernetes_utils.KubernetesSkyPilotClusterInfoPayload(**cluster))
    return all_clusters, unmanaged_clusters, all_jobs, context


@register_decoders('launch', 'exec', 'jobs.launch')
def decode_launch(
    return_value: Dict[str, Any]
) -> Tuple[str, 'backends.CloudVmRayResourceHandle']:
    return return_value['job_id'], decode_and_unpickle(return_value['handle'])


@register_decoders('start')
def decode_start(return_value: str) -> 'backends.CloudVmRayResourceHandle':
    return decode_and_unpickle(return_value)


@register_decoders('queue')
def decode_queue(return_value: List[dict],) -> List[Dict[str, Any]]:
    jobs = return_value
    for job in jobs:
        job['status'] = job_lib.JobStatus(job['status'])
    return jobs


@register_decoders('jobs.queue')
def decode_jobs_queue(return_value: List[dict],) -> List[Dict[str, Any]]:
    jobs = return_value
    for job in jobs:
        job['status'] = managed_jobs.ManagedJobStatus(job['status'])
    return jobs


@register_decoders('serve.status')
def decode_serve_status(return_value: List[dict]) -> List[Dict[str, Any]]:
    service_statuses = return_value
    for service_status in service_statuses:
        service_status['status'] = serve_state.ServiceStatus(
            service_status['status'])
        for replica_info in service_status.get('replica_info', []):
            replica_info['status'] = serve_state.ReplicaStatus(
                replica_info['status'])
            replica_info['handle'] = decode_and_unpickle(replica_info['handle'])
    return service_statuses


@register_decoders('cost_report')
def decode_cost_report(
        return_value: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for cluster_report in return_value:
        if cluster_report['status'] is not None:
            cluster_report['status'] = status_lib.ClusterStatus(
                cluster_report['status'])
        cluster_report['resources'] = decode_and_unpickle(
            cluster_report['resources'])
    return return_value


@register_decoders('list_accelerators')
def decode_list_accelerators(
    return_value: Dict[str, List[List[Any]]]
) -> Dict[str, List['common.InstanceTypeInfo']]:
    instance_dict: Dict[str, List['common.InstanceTypeInfo']] = {}
    for gpu, instance_type_infos in return_value.items():
        instance_dict[gpu] = []
        for instance_type_info in instance_type_infos:
            instance_dict[gpu].append(
                common.InstanceTypeInfo(*instance_type_info))
    return instance_dict


@register_decoders('storage_ls')
def decode_storage_ls(
        return_value: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for storage_info in return_value:
        storage_info['status'] = status_lib.StorageStatus(
            storage_info['status'])
        storage_info['store'] = [
            storage.StoreType(store) for store in storage_info['store']
        ]
    return return_value


@register_decoders('job_status')
def decode_job_status(
    return_value: Dict[str, Optional[str]]
) -> Dict[int, Optional['job_lib.JobStatus']]:
    job_statuses: Dict[int, Optional['job_lib.JobStatus']] = {}
    for job_id_str, status_str in return_value.items():
        # When we json serialize the job ID for storing in the requests db,
        # the job_id gets converted to a string. Here we convert it back to int.
        job_id = int(job_id_str)
        if status_str is not None:
            job_statuses[job_id] = job_lib.JobStatus(status_str)
        else:
            job_statuses[job_id] = None
    return job_statuses


@register_decoders('kubernetes_node_info')
def decode_kubernetes_node_info(
        return_value: Dict[str, Any]) -> models.KubernetesNodesInfo:
    return models.KubernetesNodesInfo.from_dict(return_value)
