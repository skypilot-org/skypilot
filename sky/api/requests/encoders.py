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

handlers: Dict[str, Any] = {}


def pickle_and_encode(obj: Any) -> str:
    return base64.b64encode(pickle.dumps(obj)).decode('utf-8')


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
def encode_start(resource_handle: 'backends.CloudVmRayResourceHandle') -> bytes:
    return pickle_and_encode(resource_handle)


@register_handler('queue')
def encode_queue(
    jobs: List[dict],
) -> Dict[str, Any]:
    for job in jobs:
        job['status'] = job['status'].value
    return jobs
