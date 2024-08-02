"""Handlers for the REST API return values."""
import base64
import pickle
import typing
from typing import Any, Dict, List

from sky.skylet import job_lib
from sky.utils import status_lib

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
