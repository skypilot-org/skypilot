"""The public OpenAPI document, built from the API server's own schema."""

import ast
import copy
import dataclasses
import inspect
import textwrap
import typing
from typing import Any, Dict, Iterable, Iterator, Optional, Set, Tuple

import fastapi
from fastapi import routing

import sky
from sky.server import constants as server_constants
from sky.server.api_reference import extensions
from sky.server.api_reference import visibility

REQUEST_ID_HEADER = 'X-Skypilot-Request-ID'
_SCHEMA_REF = '#/components/schemas/'

_NO_BODY = (None, type(None), 'None', inspect.Signature.empty)

_BACKGROUND_DESCRIPTION = (
    'The request runs in the background under the ID in the '
    f'`{REQUEST_ID_HEADER}` response header. Get the result with '
    '`GET /api/get?request_id=<id>`.')


@dataclasses.dataclass(frozen=True)
class Submission:
    """How an operation runs its work as a background request.

    Attributes:
        has_body: Whether the response has a JSON body besides the request ID
            header.
        keywords: The keyword arguments of every call in the operation's
            handler, as (name, source text) in source order, e.g.
            ``('request_name', 'request_names.RequestName.CLUSTER_LAUNCH')``.
    """
    has_body: bool
    keywords: Tuple[Tuple[str, str], ...]


def _return_annotation(endpoint: Any) -> Any:
    try:
        return typing.get_type_hints(endpoint).get('return',
                                                   inspect.Signature.empty)
    except Exception:  # pylint: disable=broad-except
        return inspect.signature(endpoint).return_annotation


def _is_own_request_id(node: ast.AST, aliases: Set[str]) -> bool:
    """``request.state.request_id``, or a name assigned from it."""
    if isinstance(node, ast.Name):
        return node.id in aliases
    return (isinstance(node, ast.Attribute) and node.attr == 'request_id' and
            isinstance(node.value, ast.Attribute) and
            node.value.attr == 'state' and
            isinstance(node.value.value, ast.Name) and
            node.value.value.id == 'request')


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ''


def _submission(route: Any) -> Optional[Submission]:
    """How the route answers, when it runs its work as a background request.

    It does when its handler passes the HTTP request's own ID (the one in the
    response header) as ``request_id=`` or as the first argument of a
    scheduling call, or passes the request itself to a scheduling helper.
    """
    try:
        source = textwrap.dedent(inspect.getsource(route.endpoint))
        tree = ast.parse(source)
    except (OSError, TypeError, SyntaxError):
        return None
    calls = tuple(node for node in ast.walk(tree) if isinstance(node, ast.Call))
    aliases = {
        target.id for node in ast.walk(tree) if isinstance(node, ast.Assign) and
        _is_own_request_id(node.value, set()) for target in node.targets
        if isinstance(target, ast.Name)
    }
    own = False
    for call in calls:
        first = call.args[0] if call.args else None
        if ('schedule' in _call_name(call.func).lower() and
                first is not None and
            ((isinstance(first, ast.Name) and first.id == 'request') or
             _is_own_request_id(first, aliases))):
            own = True
        if any(keyword.arg == 'request_id' and
               _is_own_request_id(keyword.value, aliases)
               for keyword in call.keywords):
            own = True
    if not own:
        return None
    annotation = _return_annotation(route.endpoint)
    if inspect.isclass(annotation) and issubclass(annotation, fastapi.Response):
        # Streams the request's output instead.
        return None
    return Submission(
        has_body=(route.response_model is not None or
                  annotation not in _NO_BODY),
        keywords=tuple(
            (keyword.arg, ast.get_source_segment(source, keyword.value) or '')
            for call in calls
            for keyword in call.keywords
            if keyword.arg is not None),
    )


def _mark_submitting(operation: Dict[str, Any], submission: Submission) -> None:
    operation['x-skypilot-async'] = True
    response = operation.setdefault('responses', {}).setdefault('200', {})
    response.setdefault('headers', {})[REQUEST_ID_HEADER] = {
        'description': 'ID of the background request.',
        'schema': {
            'type': 'string'
        },
    }
    description = _BACKGROUND_DESCRIPTION
    if not submission.has_body:
        description = 'Accepted. The body is `null`. ' + description
        response.pop('content', None)
    response['description'] = description


def _collect_refs(node: Any, out: Set[str]) -> None:
    if isinstance(node, dict):
        ref = node.get('$ref')
        if isinstance(ref, str) and ref.startswith(_SCHEMA_REF):
            out.add(ref[len(_SCHEMA_REF):])
        for value in node.values():
            _collect_refs(value, out)
    elif isinstance(node, list):
        for value in node:
            _collect_refs(value, out)


def _referenced_schemas(roots: Iterable[Any],
                        schemas: Dict[str, Any]) -> Dict[str, Any]:
    pending: Set[str] = set()
    for root in roots:
        _collect_refs(root, pending)
    kept: Dict[str, Any] = {}
    while pending:
        name = pending.pop()
        if name in kept or name not in schemas:
            continue
        kept[name] = schemas[name]
        _collect_refs(schemas[name], pending)
    return dict(sorted(kept.items()))


def _api_routes(app: fastapi.FastAPI) -> Iterator[Any]:
    """Every API route of the app, with the prefixes of the routers it is under.

    FastAPI 0.16x keeps an included router as one entry of ``app.routes`` and
    resolves its routes through ``iter_route_contexts``; older versions copy
    them into ``app.routes``.
    """
    iter_route_contexts = getattr(routing, 'iter_route_contexts', None)
    if iter_route_contexts is None:
        yield from (r for r in app.routes if isinstance(r, routing.APIRoute))
        return
    for context in iter_route_contexts(app.routes):  # pylint: disable=not-callable
        if isinstance(context.original_route, routing.APIRoute):
            yield context


def routes_by_operation(app: fastapi.FastAPI) -> Dict[Tuple[str, str], Any]:
    """The route of each (path, lowercase method) in the app's schema."""
    routes: Dict[Tuple[str, str], Any] = {}
    for route in _api_routes(app):
        if route.include_in_schema:
            for method in route.methods:
                routes[(route.path_format, method.lower())] = route
    return routes


def build(app: fastapi.FastAPI, guide: str) -> Dict[str, Any]:
    """The OpenAPI document of the public operations of this server."""
    full = app.openapi()
    routes = routes_by_operation(app)
    paths: Dict[str, Any] = {}
    for path, operations in full.get('paths', {}).items():
        if visibility.classify(
                path).visibility is not visibility.Visibility.PUBLIC:
            continue
        kept = {}
        for method, operation in operations.items():
            route = routes.get((path, method))
            operation = copy.deepcopy(operation)
            submission = _submission(route) if route is not None else None
            if submission is not None:
                _mark_submitting(operation, submission)
                extensions.annotate(operation, submission)
            kept[method] = operation
        if kept:
            paths[path] = kept
    schemas = full.get('components', {}).get('schemas', {})
    return {
        'openapi': full.get('openapi', '3.1.0'),
        'info': {
            'title': 'SkyPilot API',
            'version': sky.__version__,
            'description': guide,
            'x-skypilot-api-version': server_constants.API_VERSION,
            'x-skypilot-min-compatible-api-version':
                (server_constants.MIN_COMPATIBLE_API_VERSION),
        },
        'servers': [{
            'url': '/'
        }],
        'security': [{
            'serviceAccountToken': []
        }],
        'paths': paths,
        'components': {
            'schemas': _referenced_schemas(paths.values(), schemas),
            'securitySchemes': {
                'serviceAccountToken': {
                    'type': 'http',
                    'scheme': 'bearer',
                    'description': 'A service account token (`sky_...`).',
                }
            },
        },
    }
