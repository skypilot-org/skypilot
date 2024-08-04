"""Handlers for the REST API return values."""
import base64
import pickle
import typing
from typing import Any, Dict, List

from sky import jobs as managed_jobs
from sky.skylet import job_lib
from sky.utils import status_lib
from sky.serve import serve_state

if typing.TYPE_CHECKING:
    from sky import backends

handlers: Dict[str, Any] = {}


def decode_and_unpickle(obj: str) -> Any:
    return pickle.loads(base64.b64decode(obj.encode('utf-8')))


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
def default_decode_handler(return_value: Any) -> Any:
    """The default handler."""
    return return_value


@register_handler('status')
def decode_status(return_value: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    clusters = return_value
    for cluster in clusters:
        cluster['handle'] = decode_and_unpickle(cluster['handle'])
        cluster['status'] = status_lib.ClusterStatus(cluster['status'])

    return clusters


@register_handler('launch', 'exec')
def decode_launch(return_value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'job_id': return_value['job_id'],
        'handle': decode_and_unpickle(return_value['handle']),
    }


@register_handler('start')
def decode_start(return_value: str) -> 'backends.CloudVmRayResourceHandle':
    return decode_and_unpickle(return_value)


@register_handler('queue')
def decode_queue(return_value: List[dict],) -> List[Dict[str, Any]]:
    jobs = return_value
    for job in jobs:
        job['status'] = job_lib.JobStatus(job['status'])
    return jobs


@register_handler('jobs/queue')
def decode_jobs_queue(return_value: List[dict],) -> List[Dict[str, Any]]:
    jobs = return_value
    for job in jobs:
        job['status'] = managed_jobs.ManagedJobStatus(job['status'])
    return jobs


@register_handler('serve/status')
def decode_serve_status(return_value: List[dict]) -> List[Dict[str, Any]]:
    service_statuses = return_value
    for service_status in service_statuses:
        service_status['status'] = serve_state.ServiceStatus(service_status['status'])
        for replica_info in service_status.get('replica_info', []):
            replica_info['status'] = serve_state.ReplicaStatus(replica_info['status'])
            replica_info['handle'] = decode_and_unpickle(replica_info['handle'])
    return service_statuses
