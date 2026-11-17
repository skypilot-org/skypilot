"""Tests for the public API reference."""

import ast
import json
import logging
import re
from typing import Any, Dict
import uuid

from casbin import util as casbin_util
import fastapi
from fastapi import testclient
import pydantic
import pytest

from sky.server import constants as server_constants
from sky.server.api_reference import extensions
from sky.server.api_reference import routes
from sky.server.api_reference import schema
from sky.server.api_reference import visibility
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.server.requests import request_names
from sky.users import rbac

V = visibility.Visibility


@pytest.fixture(autouse=True)
def _no_extensions(monkeypatch):
    monkeypatch.setattr(extensions, '_EXTENSIONS', {})
    routes.clear_caches()
    yield
    routes.clear_caches()


@pytest.mark.parametrize(
    'path,expected',
    [
        ('/launch', V.PUBLIC),
        ('/jobs/queue/v2', V.PUBLIC),
        ('/api/get', V.PUBLIC),
        ('/dashboard_config', V.INTERNAL),
        ('/dashboard/{full_path}', V.INTERNAL),
        ('/api/completion/cluster_name', V.INTERNAL),
        ('/token', V.INTERNAL),
        ('/api/v1/auth/token', V.INTERNAL),
        ('/local_up', V.INTERNAL),
        ('/', V.INTERNAL),
        # An internal pattern also covers the routes under it.
        ('/debug/dump_download/{dump_filename}', V.INTERNAL),
        # A route that no rule matches is public.
        ('/new/items', V.PUBLIC),
    ],
)
def test_classify(path, expected):
    assert visibility.classify(path).visibility is expected


def test_classify_applies_registered_rules():
    extensions.register(
        'test',
        extensions.Extension(rules=(visibility.Rule((
            r'/plugins/api/test/admin',), V.UNLISTED, 'admin view'),)))
    assert visibility.classify(
        '/plugins/api/test/admin/items').visibility is V.UNLISTED
    assert visibility.classify('/plugins/api/test/items').visibility is V.PUBLIC


# Handlers below are only inspected, never called.
async def _launch(request: fastapi.Request) -> None:
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.CLUSTER_LAUNCH,
        request_body=payloads.RequestBody(),
        func=_users,
    )


def _users() -> list:
    return []


def _dashboard_config() -> dict:
    return {}


def _new_items() -> list:
    return []


class _Record(pydantic.BaseModel):
    name: str


async def _upload(request: fastapi.Request) -> _Record:
    # Queues follow-up work under a request ID of its own.
    await executor.schedule_request_async(
        request_id=str(uuid.uuid4()),
        request_name=request_names.RequestName.CHECK,
        request_body=payloads.RequestBody(),
        func=_users,
    )
    return _Record(name=request.url.path)


async def _deploy(request: fastapi.Request) -> Dict[str, str]:
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.SSH_NODE_POOLS_UP,
        request_body=payloads.RequestBody(),
        func=_users,
    )
    return {'request_id': request.state.request_id}


async def _logs(
        request: fastapi.Request) -> fastapi.responses.StreamingResponse:
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.CLUSTER_JOB_LOGS,
        request_body=payloads.RequestBody(),
        func=_users,
    )
    return fastapi.responses.StreamingResponse(iter(()))


async def _schedule_install(request: fastapi.Request) -> None:
    del request


# A string annotation, as under `from __future__ import annotations`.
async def _install(request: fastapi.Request) -> 'None':
    await _schedule_install(request)


_logger = logging.getLogger(__name__)


async def _aliased(request: fastapi.Request) -> None:
    request_id = request.state.request_id
    await executor.schedule_request_async(
        request_id,
        request_names.RequestName.CLUSTER_LAUNCH,
        payloads.RequestBody(),
        _users,
    )


async def _logs_only(request: fastapi.Request) -> None:
    request_id = request.state.request_id
    _logger.info('Handled %s', request_id)


def test_build_follows_each_handlers_response_contract():
    app = fastapi.FastAPI()
    for path, func in [
        ('/upload', _upload),
        ('/deploy', _deploy),
        ('/logs', _logs),
        ('/install', _install),
        ('/aliased', _aliased),
        ('/logs_only', _logs_only),
    ]:
        app.add_api_route(path, func, methods=['POST'])
    paths = schema.build(app, '')['paths']

    upload = paths['/upload']['post']
    assert 'x-skypilot-async' not in upload
    assert upload['responses']['200']['content']['application/json'][
        'schema'] == {
            '$ref': '#/components/schemas/_Record'
        }

    deploy = paths['/deploy']['post']['responses']['200']
    assert paths['/deploy']['post']['x-skypilot-async'] is True
    assert 'application/json' in deploy['content']
    assert schema.REQUEST_ID_HEADER in deploy['headers']

    assert 'x-skypilot-async' not in paths['/logs']['post']

    install = paths['/install']['post']
    assert install['x-skypilot-async'] is True
    assert 'content' not in install['responses']['200']

    assert paths['/aliased']['post']['x-skypilot-async'] is True
    assert 'x-skypilot-async' not in paths['/logs_only']['post']


def _app() -> fastapi.FastAPI:
    app = fastapi.FastAPI(docs_url=None, redoc_url=None)
    for path, func, method in [
        ('/launch', _launch, 'POST'),
        ('/dashboard_config', _dashboard_config, 'GET'),
        ('/new/items', _new_items, 'GET'),
    ]:
        app.add_api_route(path, func, methods=[method])
    # Included with a prefix, as the API server includes its core routers.
    users = fastapi.APIRouter()
    users.add_api_route('/list', _users, methods=['GET'])
    app.include_router(users, prefix='/users')
    app.include_router(routes.router)
    return app


def test_build_keeps_public_operations_and_marks_requests():
    doc = schema.build(_app(), 'the guide')

    assert sorted(doc['paths']) == ['/launch', '/new/items', '/users/list']
    launch = doc['paths']['/launch']['post']
    assert launch['x-skypilot-async'] is True
    assert schema.REQUEST_ID_HEADER in launch['responses']['200']['headers']
    assert 'x-skypilot-async' not in doc['paths']['/users/list']['get']

    info = doc['info']
    assert info['description'] == 'the guide'
    assert info['x-skypilot-api-version'] == server_constants.API_VERSION
    assert info['x-skypilot-min-compatible-api-version'] == (
        server_constants.MIN_COMPATIBLE_API_VERSION)


def test_build_passes_background_operations_to_extensions():
    seen = []

    def annotate(operation: Dict[str, Any],
                 submission: schema.Submission) -> None:
        names = {
            keyword.value.attr
            for call in submission.calls
            for keyword in call.keywords
            if keyword.arg == 'request_name' and
            isinstance(keyword.value, ast.Attribute)
        }
        seen.append(names)
        operation['x-test'] = submission.has_body

    extensions.register('test', extensions.Extension(annotate=annotate))
    paths = schema.build(_app(), '')['paths']

    assert seen == [{'CLUSTER_LAUNCH'}]
    assert paths['/launch']['post']['x-test'] is False
    assert 'x-test' not in paths['/users/list']['get']


def test_build_drops_components_no_public_operation_uses():
    app = fastapi.FastAPI()

    class PublicBody(pydantic.BaseModel):
        name: str

    class InternalBody(pydantic.BaseModel):
        secret: str

    def public(body: PublicBody) -> None:
        del body

    def internal(body: InternalBody) -> None:
        del body

    app.add_api_route('/validate', public, methods=['POST'])
    app.add_api_route('/dashboard_config', internal, methods=['POST'])

    schemas = schema.build(app, '')['components']['schemas']
    assert 'PublicBody' in schemas
    assert 'InternalBody' not in schemas


@pytest.fixture
def client():
    with testclient.TestClient(_app()) as test_client:
        yield test_client


def test_api_catalog(client):
    response = client.get(routes.CATALOG_PATH)
    assert response.status_code == 200
    assert response.headers['content-type'].startswith(
        'application/linkset+json')
    assert response.headers['link'] == routes.CATALOG_LINK
    (entry,) = response.json()['linkset']
    assert entry['service-desc'][0]['href'] == routes.OPENAPI_PATH
    assert [doc['href'] for doc in entry['service-doc']] == [
        routes.GUIDE_PATH,
        routes.PAGE_PATH,
    ]

    head = client.head(routes.CATALOG_PATH)
    assert head.status_code == 200


def test_guide_has_this_servers_api_version(client):
    response = client.get(routes.GUIDE_PATH)
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/markdown')
    text = response.text
    assert (f'X-SkyPilot-API-Version: {server_constants.API_VERSION}\n'
            'X-SkyPilot-Version: <the "version" field of GET /api/health>\n'
            '```\n') in text
    assert '{{' not in text
    assert '## Fields only the Python client can read' in text


def test_guide_slots_take_the_text_extensions_register():
    extensions.register(
        'test',
        extensions.Extension(
            request_headers={'X-Test-Client': 'test'},
            guide={
                'python_only_fields': '',
                'error_rows': '| `418` | A teapot. | Brew. |',
            },
        ))
    text = routes.guide()
    assert ('X-SkyPilot-Version: <the "version" field of GET /api/health>\n'
            'X-Test-Client: test\n'
            '```\n') in text
    assert 'Python client can read' not in text
    assert 'body.\n\n## Two kinds of operations' in text
    assert '|---|---|---|\n| `418` | A teapot. | Brew. |\n| `503`' in text


def test_register_rejects_unknown_guide_slots():
    with pytest.raises(ValueError, match='no_such_slot'):
        extensions.register('test',
                            extensions.Extension(guide={'no_such_slot': 'x'}))


def test_guide_slots_are_the_ones_the_template_has():
    template = (routes._DIR / 'guide.md').read_text(encoding='utf-8')
    names = re.findall(r'^\{\{slot:(\w+)\}\}$', template, flags=re.MULTILINE)
    assert sorted(names) == sorted(extensions.GUIDE_SLOTS +
                                   ('request_headers',))


def test_openapi_document(client):
    response = client.get(routes.OPENAPI_PATH)
    assert response.status_code == 200
    doc = json.loads(response.content)
    assert '/launch' in doc['paths']
    assert '/dashboard_config' not in doc['paths']
    assert doc['info']['description'] == routes.guide()
    # The reference routes document others, not themselves.
    assert routes.OPENAPI_PATH not in doc['paths']


def test_page_loads_only_same_origin_assets(client):
    page = client.get(routes.PAGE_PATH)
    assert page.status_code == 200
    assert page.headers['content-type'].startswith('text/html')
    links = re.findall(r'(?:src|href)="([^"]+)"', page.text)
    assets = [link for link in links if not link.startswith('data:')]
    assert sorted(assets) == [
        '/api-reference/swagger-init.js',
        '/api-reference/swagger-ui-bundle.js',
        '/api-reference/swagger-ui.css',
    ]
    served = {}
    for asset in assets:
        response = client.get(asset)
        assert response.status_code == 200, asset
        media_type = 'text/css' if asset.endswith('.css') else 'text/javascript'
        assert response.headers['content-type'].startswith(media_type)
        # A CDN in front of the server caches .js and .css unless told not to.
        assert 'private' in response.headers['cache-control']
        served[asset] = response

    bundle = served['/api-reference/swagger-ui-bundle.js']
    revalidated = client.get(
        '/api-reference/swagger-ui-bundle.js',
        headers={'If-None-Match': bundle.headers['etag']},
    )
    assert revalidated.status_code == 304

    init = served['/api-reference/swagger-init.js'].text
    assert f"url: '{routes.OPENAPI_PATH}'" in init
    assert 'queryConfigEnabled: false' in init
    assert 'const REQUEST_HEADERS = {};' in init


def test_page_sends_the_headers_extensions_register():
    extensions.register(
        'test', extensions.Extension(request_headers={'X-Test-Client': 'a'}))
    with testclient.TestClient(_app()) as test_client:
        init = test_client.get('/api-reference/swagger-init.js').text
    assert 'const REQUEST_HEADERS = {"X-Test-Client": "a"};' in init


def test_default_docs_redirect_to_the_page(client):
    for path in ('/docs', '/redoc'):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 307
        assert response.headers['location'] == routes.PAGE_PATH
    assert client.get('/docs/oauth2-redirect').status_code == 404


def test_viewers_can_read_every_reference_route():
    allowlist = rbac.get_viewer_allowlist()
    for path, method in routes.ROUTES:
        assert any(rule['method'] == method and
                   casbin_util.key_match2(path, rule['path'])
                   for rule in allowlist), (path, method)


def _refs(node: Any, out: set) -> None:
    if isinstance(node, dict):
        if isinstance(node.get('$ref'), str):
            out.add(node['$ref'])
        for value in node.values():
            _refs(value, out)
    elif isinstance(node, list):
        for value in node:
            _refs(value, out)


def test_reference_of_the_api_server():
    # pylint: disable=import-outside-toplevel
    from sky.server import server

    test_client = testclient.TestClient(server.app)
    assert test_client.get('/api/health').headers['link'] == (
        routes.CATALOG_LINK)
    doc = test_client.get(routes.OPENAPI_PATH).json()
    paths = doc['paths']

    assert paths['/launch']['post']['x-skypilot-async'] is True
    assert paths['/jobs/queue/v2']['post']['x-skypilot-async'] is True
    assert 'x-skypilot-async' not in paths['/api/get']['get']
    ssh_deploy = paths['/ssh_node_pools/deploy']['post']
    assert ssh_deploy['x-skypilot-async'] is True
    assert 'application/json' in ssh_deploy['responses']['200']['content']
    for internal in ('/', '/dashboard_config', '/token', '/local_up',
                     '/api/completion/cluster_name'):
        assert internal not in paths

    refs: set = set()
    _refs(paths, refs)
    _refs(doc['components']['schemas'], refs)
    assert {
        ref for ref in refs
        if ref.split('/')[-1] not in doc['components']['schemas']
    } == set()
