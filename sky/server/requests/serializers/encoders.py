"""Encoders for the REST API return values."""
# TODO(SKY-1211): we should evaluate that if we can move our return values to
# pydantic models, so we can take advantage of model_dump_json of pydantic,
# instead of implementing our own handlers.
import base64
import dataclasses
import pickle
import typing
from typing import Any, Dict, List, Optional, Tuple

from sky.schemas.api import responses
from sky.server import constants as server_constants
from sky.utils import serialize_utils

if typing.TYPE_CHECKING:
    from sky import backends
    from sky import clouds
    from sky import models
    from sky.provision.kubernetes import utils as kubernetes_utils

handlers: Dict[str, Any] = {}


def pickle_and_encode(obj: Any) -> str:
    try:
        # Apply backwards compatibility processing at the lowest level
        # to catch any handles that might have bypassed the encoders
        obj = serialize_utils.prepare_handle_for_backwards_compatibility(obj)
        return base64.b64encode(pickle.dumps(obj)).decode('utf-8')
    except TypeError as e:
        raise ValueError(f'Failed to pickle object: {obj}') from e


def register_encoder(*names: str):
    """Decorator to register an encoder."""

    def decorator(func):
        for name in names:
            if name != server_constants.DEFAULT_HANDLER_NAME:
                name = server_constants.REQUEST_NAME_PREFIX + name
            handlers[name] = func
        return func

    return decorator


def get_encoder(name: str):
    """Get the encoder for a request name."""
    return handlers.get(name, handlers[server_constants.DEFAULT_HANDLER_NAME])


@register_encoder(server_constants.DEFAULT_HANDLER_NAME)
def default_encoder(return_value: Any) -> Any:
    """The default encoder."""
    return return_value


@register_encoder('status')
def encode_status(
        clusters: List[responses.StatusResponse]) -> List[Dict[str, Any]]:
    response = []
    for cluster in clusters:
        response_cluster = cluster.model_dump()
        response_cluster['status'] = cluster['status'].value
        handle = serialize_utils.prepare_handle_for_backwards_compatibility(
            cluster['handle'])
        response_cluster['handle'] = pickle_and_encode(handle)
        response_cluster['storage_mounts_metadata'] = pickle_and_encode(
            response_cluster['storage_mounts_metadata'])
        response.append(response_cluster)
    return response


@register_encoder('launch', 'exec', 'jobs.launch')
def encode_launch(
    job_id_handle: Tuple[Optional[int], Optional['backends.ResourceHandle']]
) -> Dict[str, Any]:
    job_id, handle = job_id_handle
    handle = serialize_utils.prepare_handle_for_backwards_compatibility(handle)
    return {
        'job_id': job_id,
        'handle': pickle_and_encode(handle),
    }


@register_encoder('start')
def encode_start(resource_handle: 'backends.CloudVmRayResourceHandle') -> str:
    resource_handle = (
        serialize_utils.prepare_handle_for_backwards_compatibility(
            resource_handle))
    return pickle_and_encode(resource_handle)


@register_encoder('queue')
def encode_queue(jobs: List[dict],) -> List[Dict[str, Any]]:
    for job in jobs:
        job['status'] = job['status'].value
    return jobs


@register_encoder('status_kubernetes')
def encode_status_kubernetes(
    return_value: Tuple[
        List['kubernetes_utils.KubernetesSkyPilotClusterInfoPayload'],
        List['kubernetes_utils.KubernetesSkyPilotClusterInfoPayload'],
        List[Dict[str, Any]], Optional[str]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]],
           Optional[str]]:
    all_clusters, unmanaged_clusters, all_jobs, context = return_value
    encoded_all_clusters = []
    encoded_unmanaged_clusters = []
    for cluster in all_clusters:
        encoded_cluster = dataclasses.asdict(cluster)
        encoded_cluster['status'] = encoded_cluster['status'].value
        encoded_all_clusters.append(encoded_cluster)
    for cluster in unmanaged_clusters:
        encoded_cluster = dataclasses.asdict(cluster)
        encoded_cluster['status'] = encoded_cluster['status'].value
        encoded_unmanaged_clusters.append(encoded_cluster)
    return encoded_all_clusters, encoded_unmanaged_clusters, all_jobs, context


@register_encoder('jobs.queue')
def encode_jobs_queue(jobs_or_tuple):
    # Support returning either a plain jobs list or a (jobs, total) tuple
    status_counts = {}
    if isinstance(jobs_or_tuple, tuple):
        if len(jobs_or_tuple) == 2:
            jobs, total = jobs_or_tuple
            total_no_filter = total
        elif len(jobs_or_tuple) == 4:
            jobs, total, status_counts, total_no_filter = jobs_or_tuple
        else:
            raise ValueError(f'Invalid jobs tuple: {jobs_or_tuple}')
    else:
        jobs = jobs_or_tuple
        total = None
    for job in jobs:
        job['status'] = job['status'].value
    if total is None:
        return jobs
    return {
        'jobs': jobs,
        'total': total,
        'total_no_filter': total_no_filter,
        'status_counts': status_counts
    }


def _encode_serve_status(
        service_statuses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for service_status in service_statuses:
        service_status['status'] = service_status['status'].value
        for replica_info in service_status.get('replica_info', []):
            replica_info['status'] = replica_info['status'].value
            handle = serialize_utils.prepare_handle_for_backwards_compatibility(
                replica_info['handle'])
            replica_info['handle'] = pickle_and_encode(handle)
    return service_statuses


@register_encoder('serve.status')
def encode_serve_status(
        service_statuses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return _encode_serve_status(service_statuses)


@register_encoder('jobs.pool_status')
def encode_jobs_pool_status(
        pool_statuses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return _encode_serve_status(pool_statuses)


@register_encoder('cost_report')
def encode_cost_report(
        cost_report: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for cluster_report in cost_report:
        if cluster_report['status'] is not None:
            cluster_report['status'] = cluster_report['status'].value
        cluster_report['resources'] = pickle_and_encode(
            cluster_report['resources'])
    return cost_report


@register_encoder('enabled_clouds')
def encode_enabled_clouds(clouds: List['clouds.Cloud']) -> List[str]:
    enabled_clodus_list = [str(cloud) for cloud in clouds]
    return enabled_clodus_list


@register_encoder('storage_ls')
def encode_storage_ls(
        return_value: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for storage_info in return_value:
        storage_info['status'] = storage_info['status'].value
        storage_info['store'] = [store.value for store in storage_info['store']]
    return return_value


@register_encoder('job_status')
def encode_job_status(return_value: Dict[int, Any]) -> Dict[int, str]:
    for job_id in return_value.keys():
        if return_value[job_id] is not None:
            return_value[job_id] = return_value[job_id].value
    return return_value


@register_encoder('kubernetes_node_info')
def encode_kubernetes_node_info(
        return_value: 'models.KubernetesNodesInfo') -> Dict[str, Any]:
    return return_value.to_dict()


@register_encoder('endpoints')
def encode_endpoints(return_value: Dict[int, str]) -> Dict[str, str]:
    return {str(k): v for k, v in return_value.items()}
