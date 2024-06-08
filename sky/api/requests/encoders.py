"""Handlers for the REST API return values."""
import base64
import pickle
import typing
from typing import Any, Dict, List, Optional, Tuple

if typing.TYPE_CHECKING:
    from sky import backends

handlers: Dict[str, Any] = {}


def _pickle_and_encode(obj: Any) -> str:
    return base64.b64encode(pickle.dumps(obj)).decode('utf-8')


def register_handler(name: str):
    """Decorator to register a handler."""

    def decorator(func):
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
        cluster['handle'] = _pickle_and_encode(cluster['handle'])
    return clusters


@register_handler('launch')
def encode_launch(
    job_id_handle: Tuple[Optional[int], Optional['backends.ResourceHandle']]
) -> Dict[str, Any]:
    job_id, handle = job_id_handle
    return {
        'job_id': job_id,
        'handle': _pickle_and_encode(handle),
    }
