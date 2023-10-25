"""Handlers for the REST API return values."""
from typing import Any, Dict, List

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
def default_handler(return_value: Any) -> Any:
    """The default handler."""
    return return_value

@register_handler('status')
def encode_status(clusters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for cluster in clusters:
        cluster['status'] = cluster['status'].value
        cluster['handle'] = cluster['handle'].to_config()
    return clusters
