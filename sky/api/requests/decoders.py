"""Handlers for the REST API return values."""
from typing import Any, Dict, List

from sky import backends
from sky.utils import status_lib

handlers: Dict[str, Any] = {}


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
def default_decode_handler(return_value: Any) -> Any:
    """The default handler."""
    return return_value


@register_handler('status')
def decode_status(return_value: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    clusters = return_value
    for cluster in clusters:
        # TODO(zhwu): We should make backends.ResourceHandle serializable.
        cluster['handle'] = backends.CloudVmRayResourceHandle.from_config(
            cluster['handle'])
        cluster['status'] = status_lib.ClusterStatus(cluster['status'])

    return clusters
