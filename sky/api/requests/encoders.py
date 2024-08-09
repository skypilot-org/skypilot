"""Handlers for the REST API return values."""
# TODO(zhwu): we should evaluate that if we can move our return values to
# pydantic models, so we can take advantage of model_dump_json of pydantic,
# instead of implementing our own handlers.
import base64
import pickle
import typing
from typing import Any, Dict, List, Optional, Tuple

if typing.TYPE_CHECKING:
    from sky import backends
    from sky import clouds

handlers: Dict[str, Any] = {}


def pickle_and_encode(obj: Any) -> str:
    try:
        return base64.b64encode(pickle.dumps(obj)).decode('utf-8')
    except TypeError as e:
        raise ValueError(f'Failed to pickle object: {obj}') from e


def register_handler(*names: str):
    """Decorator to register a handler."""

    def decorator(func):
        for name in names:
            handlers[name] = func
        return func

    return decorator


def get_handler(name: str):
    """Get the handler for name."""
    return handlers.get(name, handlers['default'])


@register_handler('default')
def default_handler(return_value: Any) -> Any:
    """The default handler."""
    return return_value


@register_handler('status')
def encode_status(clusters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for cluster in clusters:
        cluster['status'] = cluster['status'].value
        cluster['handle'] = pickle_and_encode(cluster['handle'])
        cluster['storage_mounts_metadata'] = pickle_and_encode(
            cluster['storage_mounts_metadata'])
    return clusters


@register_handler('launch', 'exec')
def encode_launch(
    job_id_handle: Tuple[Optional[int], Optional['backends.ResourceHandle']]
) -> Dict[str, Any]:
    job_id, handle = job_id_handle
    return {
        'job_id': job_id,
        'handle': pickle_and_encode(handle),
    }


@register_handler('start')
def encode_start(resource_handle: 'backends.CloudVmRayResourceHandle') -> str:
    return pickle_and_encode(resource_handle)


@register_handler('queue')
def encode_queue(jobs: List[dict],) -> List[Dict[str, Any]]:
    for job in jobs:
        job['status'] = job['status'].value
    return jobs


@register_handler('jobs/queue')
def encode_jobs_queue(jobs: List[dict],) -> List[Dict[str, Any]]:
    for job in jobs:
        job['status'] = job['status'].value
    return jobs


@register_handler('serve/status')
def encode_serve_status(
        service_statuses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for service_status in service_statuses:
        service_status['status'] = service_status['status'].value
        for replica_info in service_status.get('replica_info', []):
            replica_info['status'] = replica_info['status'].value
            replica_info['handle'] = pickle_and_encode(replica_info['handle'])
    return service_statuses


@register_handler('cost_report')
def encode_cost_report(
        cost_report: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for cluster_report in cost_report:
        if cluster_report['status'] is not None:
            cluster_report['status'] = cluster_report['status'].value
        cluster_report['resources'] = pickle_and_encode(
            cluster_report['resources'])
    return cost_report


@register_handler('enabled_clouds')
def encode_enabled_clouds(clouds: List['clouds.Cloud']) -> List[str]:
    enabled_clodus_list = [str(cloud) for cloud in clouds]
    return enabled_clodus_list


@register_handler('storage_ls')
def encode_storage_ls(
        return_value: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for storage_info in return_value:
        storage_info['status'] = storage_info['status'].value
        storage_info['store'] = [store.value for store in storage_info['store']]
    return return_value
