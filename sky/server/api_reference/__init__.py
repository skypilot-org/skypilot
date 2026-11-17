"""API reference for programmatic clients of the API server.

``/.well-known/api-catalog`` (RFC 9727) points to the OpenAPI document of the
public operations at ``/api-reference/openapi.json`` and to the client guide at
``/api-reference/guide.md``. ``/api-reference/`` renders the OpenAPI document
with Swagger UI. They need the same authentication as the rest of the API.

Plugins add their routes, headers and guide text with ``register``.
"""

from sky.server.api_reference.extensions import Extension
from sky.server.api_reference.extensions import register
from sky.server.api_reference.routes import CATALOG_LINK
from sky.server.api_reference.routes import router
from sky.server.api_reference.schema import Submission
from sky.server.api_reference.visibility import Rule
from sky.server.api_reference.visibility import Visibility

__all__ = [
    'CATALOG_LINK',
    'Extension',
    'register',
    'router',
    'Rule',
    'Submission',
    'Visibility',
]
