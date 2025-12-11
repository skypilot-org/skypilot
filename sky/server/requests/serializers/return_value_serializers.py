"""Version-aware serializers for request return values.

These serializers run at encode() time when remote_api_version is available,
to handle backward compatibility for old clients.

The existing encoders.py handles object -> dict conversion at set_return_value()
time. This module handles dict -> JSON string serialization at encode() time,
with version-aware field filtering for backward compatibility.
"""
from typing import Any, Callable, Dict

import orjson

from sky.server import constants as server_constants
from sky.server import versions

handlers: Dict[str, Callable[[Any], str]] = {}


def register_serializer(*names: str):
    """Decorator to register a version-aware serializer."""

    def decorator(func):
        for name in names:
            if name != server_constants.DEFAULT_HANDLER_NAME:
                name = server_constants.REQUEST_NAME_PREFIX + name
            if name in handlers:
                raise ValueError(f'Serializer {name} already registered: '
                                 f'{handlers[name]}')
            handlers[name] = func
        return func

    return decorator


def get_serializer(name: str) -> Callable[[Any], str]:
    """Get the serializer for a request name."""
    return handlers.get(name, handlers[server_constants.DEFAULT_HANDLER_NAME])


@register_serializer(server_constants.DEFAULT_HANDLER_NAME)
def default_serializer(return_value: Any) -> str:
    """The default serializer."""
    return orjson.dumps(return_value).decode('utf-8')


@register_serializer('kubernetes_node_info')
def serialize_kubernetes_node_info(return_value: Dict[str, Any]) -> str:
    """Serialize kubernetes node info with version compatibility.

    The is_ready field was added in API version 25. Remove it for old clients
    that don't recognize it.
    """
    remote_api_version = versions.get_remote_api_version()
    if (return_value and remote_api_version is not None and
            remote_api_version < 25):
        # Remove is_ready field for old clients that don't recognize it
        for node_info in return_value.get('node_info_dict', {}).values():
            node_info.pop('is_ready', None)
    return orjson.dumps(return_value).decode('utf-8')
