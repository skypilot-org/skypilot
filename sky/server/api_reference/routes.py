"""Routes that serve the API reference."""

import functools
import hashlib
import json
import pathlib
import re
import threading
from typing import Optional, Tuple

import fastapi
from fastapi import responses
from fastapi import routing

from sky.server import constants as server_constants
from sky.server.api_reference import extensions
from sky.server.api_reference import schema

CATALOG_PATH = '/.well-known/api-catalog'
OPENAPI_PATH = '/api-reference/openapi.json'
GUIDE_PATH = '/api-reference/guide.md'
PAGE_PATH = '/api-reference/'
# RFC 9727 link relation to the catalog.
CATALOG_LINK = f'<{CATALOG_PATH}>; rel="api-catalog"'

# RFC 9727: the API catalog is a linkset (RFC 9264) with this profile.
_CATALOG_MEDIA_TYPE = ('application/linkset+json; '
                       'profile="https://www.rfc-editor.org/info/rfc9727"')
_DIR = pathlib.Path(__file__).parent
# swagger_ui/ holds files of swagger-ui-dist 5.34.1, unmodified.
_SWAGGER_UI_DIR = _DIR / 'swagger_ui'
_JS = 'text/javascript; charset=utf-8'
_INIT_SCRIPT_PATH = '/api-reference/swagger-init.js'
# Path -> (file, media type) of the static files of the page.
_PAGE_FILES = {
    PAGE_PATH: (_DIR / 'index.html', 'text/html; charset=utf-8'),
    '/api-reference/swagger-ui-bundle.js': (
        _SWAGGER_UI_DIR / 'swagger-ui-bundle.js',
        _JS,
    ),
    '/api-reference/swagger-ui.css': (
        _SWAGGER_UI_DIR / 'swagger-ui.css',
        'text/css; charset=utf-8',
    ),
}
# The page is behind authentication: a shared cache must not keep it, and the
# browser revalidates it with the ETag.
_CACHE_CONTROL = 'private, no-cache'
_SLOT_LINE = re.compile(r'^\{\{slot:(\w+)\}\}\n', re.MULTILINE)
_BLANK_LINES = re.compile(r'\n{3,}')

router = fastapi.APIRouter(include_in_schema=False)

_lock = threading.Lock()
_openapi_body: Optional[bytes] = None


def _slot_text(name: str) -> str:
    if name == 'request_headers':
        return '\n'.join(
            f'{header}: {value}'
            for header, value in extensions.request_headers().items())
    if name not in extensions.GUIDE_SLOTS:
        raise ValueError(f'Unknown API reference guide slot: {name}')
    return extensions.guide_slot(name)


def _fill_slot(match: 're.Match[str]') -> str:
    text = _slot_text(match.group(1)).strip('\n')
    return text + '\n' if text else ''


@functools.lru_cache(maxsize=None)
def guide() -> str:
    template = (_DIR / 'guide.md').read_text(encoding='utf-8')
    # An empty slot between two blank lines leaves two blank lines in a row.
    text = _BLANK_LINES.sub('\n\n', _SLOT_LINE.sub(_fill_slot, template))
    return text.replace('{{api_version}}', str(server_constants.API_VERSION))


def _catalog() -> dict:
    return {
        'linkset': [{
            'anchor': '/',
            'service-desc': [{
                'href': OPENAPI_PATH,
                'type': 'application/vnd.oai.openapi+json',
            }],
            'service-doc': [
                {
                    'href': GUIDE_PATH,
                    'type': 'text/markdown'
                },
                {
                    'href': PAGE_PATH,
                    'type': 'text/html'
                },
            ],
        }]
    }


@router.api_route(CATALOG_PATH, methods=['GET', 'HEAD'])
def api_catalog() -> fastapi.Response:
    return fastapi.Response(
        content=json.dumps(_catalog()),
        media_type=_CATALOG_MEDIA_TYPE,
        headers={'Link': CATALOG_LINK},
    )


# Sync handlers run in the threadpool: the first build reads the whole app
# schema, which takes about a second.
@router.get(OPENAPI_PATH)
def openapi_document(request: fastapi.Request) -> fastapi.Response:
    global _openapi_body
    with _lock:
        if _openapi_body is None:
            document = schema.build(request.app, guide())
            _openapi_body = json.dumps(document).encode()
    return fastapi.Response(content=_openapi_body,
                            media_type='application/json')


@router.get(GUIDE_PATH)
def guide_document() -> fastapi.Response:
    return fastapi.Response(content=guide(),
                            media_type='text/markdown; charset=utf-8')


def _etag(content: bytes) -> str:
    return f'W/"{hashlib.sha256(content).hexdigest()[:32]}"'


@functools.lru_cache(maxsize=None)
def _file(path: pathlib.Path) -> Tuple[bytes, str]:
    content = path.read_bytes()
    return content, _etag(content)


@functools.lru_cache(maxsize=None)
def _init_script() -> Tuple[bytes, str]:
    template = (_DIR / 'swagger-init.js').read_text(encoding='utf-8')
    content = template.replace('{{request_headers}}',
                               json.dumps(
                                   extensions.request_headers())).encode()
    return content, _etag(content)


def _if_none_match(header: Optional[str], etag: str) -> bool:
    """Whether ``If-None-Match`` matches ``etag``, compared weakly."""
    if not header:
        return False
    if header.strip() == '*':
        return True
    opaque = etag[2:] if etag.startswith('W/') else etag
    for candidate in header.split(','):
        candidate = candidate.strip()
        if candidate.startswith('W/'):
            candidate = candidate[2:]
        if candidate and candidate == opaque:
            return True
    return False


def _static_response(request: fastapi.Request, content: bytes, etag: str,
                     media_type: str) -> fastapi.Response:
    headers = {'ETag': etag, 'Cache-Control': _CACHE_CONTROL}
    if _if_none_match(request.headers.get('if-none-match'), etag):
        return fastapi.Response(status_code=304, headers=headers)
    return fastapi.Response(content=content,
                            media_type=media_type,
                            headers=headers)


def _file_endpoint(path: pathlib.Path, media_type: str):

    def endpoint(request: fastapi.Request) -> fastapi.Response:
        content, etag = _file(path)
        return _static_response(request, content, etag, media_type)

    return endpoint


for _path, (_page_file, _media_type) in _PAGE_FILES.items():
    router.add_api_route(_path,
                         _file_endpoint(_page_file, _media_type),
                         methods=['GET'])


@router.get(_INIT_SCRIPT_PATH)
def init_script(request: fastapi.Request) -> fastapi.Response:
    content, etag = _init_script()
    return _static_response(request, content, etag, _JS)


# FastAPI's own pages load their assets from a CDN, which the server's CSP
# blocks.
@router.get('/docs')
@router.get('/redoc')
def default_docs() -> responses.RedirectResponse:
    return responses.RedirectResponse(PAGE_PATH)


# (path, method) of every route above.
ROUTES: Tuple[Tuple[str, str], ...] = tuple(
    (route.path, method) for route in router.routes
    if isinstance(route, routing.APIRoute) for method in sorted(route.methods))


def clear_caches() -> None:
    """Forgets the rendered guide, page script and OpenAPI document."""
    global _openapi_body
    with _lock:
        _openapi_body = None
    guide.cache_clear()
    _init_script.cache_clear()
