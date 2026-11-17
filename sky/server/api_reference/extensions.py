"""What plugins add to the API reference."""

import dataclasses
from typing import Any, Callable, Dict, Mapping, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from sky.server.api_reference import schema
    from sky.server.api_reference import visibility

# Places in guide.md that an extension can fill. A slot that no extension
# fills keeps its default text from DEFAULT_GUIDE_SLOTS, or disappears.
GUIDE_SLOTS = (
    'request_header_notes',
    'python_only_fields',
    'inline_reads',
    'error_rows',
    'efficient_reads',
)

DEFAULT_GUIDE_SLOTS = {
    'python_only_fields':
        ('## Fields only the Python client can read\n'
         '\n'
         'Cluster handles, resources and some other fields in responses are '
         'base64-encoded Python pickles. Only the SkyPilot Python client can '
         'read them. Leave them out where an operation allows it, such as '
         '`"include_handle": false` on `POST /status`.'),
}

# Called with each public operation that runs its work as a background
# request, after the reference has marked it.
OperationHook = Callable[[Dict[str, Any], 'schema.Submission'], None]


@dataclasses.dataclass(frozen=True)
class Extension:
    """What a plugin adds to the API reference.

    Attributes:
        rules: Visibility rules for the plugin's routes. They apply after the
            built-in rules.
        request_headers: Headers that clients send on every request, besides
            the API version headers. The guide lists them, and the Swagger UI
            page sends them.
        guide: Markdown for slots of the guide, by slot name (see
            ``GUIDE_SLOTS``). It replaces the slot's default text.
        annotate: Changes the operations that run as background requests.
    """
    rules: Tuple['visibility.Rule', ...] = ()
    request_headers: Mapping[str, str] = dataclasses.field(default_factory=dict)
    guide: Mapping[str, str] = dataclasses.field(default_factory=dict)
    annotate: Optional[OperationHook] = None


_EXTENSIONS: Dict[str, Extension] = {}


def register(extension_id: str, extension: Extension) -> None:
    """Adds to the API reference.

    Call it before the server serves requests, e.g. from a plugin's
    ``install``. Registering again with the same ID replaces the extension.

    Raises:
        ValueError: if ``extension.guide`` names a slot that does not exist.
    """
    unknown = set(extension.guide) - set(GUIDE_SLOTS)
    if unknown:
        raise ValueError(
            f'Unknown API reference guide slots: {sorted(unknown)}')
    _EXTENSIONS[extension_id] = extension


def rules() -> Tuple['visibility.Rule', ...]:
    return tuple(rule for ext in _EXTENSIONS.values() for rule in ext.rules)


def request_headers() -> Dict[str, str]:
    headers: Dict[str, str] = {}
    for ext in _EXTENSIONS.values():
        headers.update(ext.request_headers)
    return headers


def guide_slot(name: str) -> str:
    texts = [
        ext.guide[name] for ext in _EXTENSIONS.values() if name in ext.guide
    ]
    if not texts:
        return DEFAULT_GUIDE_SLOTS.get(name, '')
    return '\n'.join(text for text in texts if text)


def annotate(operation: Dict[str, Any],
             submission: 'schema.Submission') -> None:
    for ext in _EXTENSIONS.values():
        if ext.annotate is not None:
            ext.annotate(operation, submission)
