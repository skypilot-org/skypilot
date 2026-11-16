"""Usage by caller: labels, the per-process cap and where calls are counted."""
# pylint: disable=protected-access
import asyncio
from unittest import mock

import fastapi
from fastapi.testclient import TestClient
import pytest

from sky import models
from sky.metrics import utils as metrics_utils
from sky.server import caller_usage
from sky.server import metrics
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.skylet import constants

_COUNTERS = (
    metrics_utils.SKY_APISERVER_CALLER_CALLS_TOTAL,
    metrics_utils.SKY_APISERVER_CALLER_EXECUTION_SECONDS_TOTAL,
    metrics_utils.SKY_APISERVER_CALLER_STORED_RESULT_BYTES_TOTAL,
    metrics_utils.SKY_APISERVER_CALLER_RETURNED_RESULT_BYTES_TOTAL,
    metrics_utils.SKY_APISERVER_CALLER_CLIENT_CALLS_TOTAL,
)

_SA = models.User(id='sa-0123456789abcdef', name='agent')


def _sample(counter, **labels) -> float:
    total = 0.0
    for family in counter.collect():
        for sample in family.samples:
            if not sample.name.endswith('_total'):
                continue
            if all(sample.labels.get(k) == v for k, v in labels.items()):
                total += sample.value
    return total


def _series(counter) -> list:
    return [
        sample.labels
        for family in counter.collect()
        for sample in family.samples
        if sample.name.endswith('_total')
    ]


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(metrics_utils, 'METRICS_ENABLED', True)
    monkeypatch.setattr(caller_usage, '_caller_names',
                        caller_usage._Admitted(caller_usage.MAX_CALLER_NAMES))
    monkeypatch.setattr(caller_usage, '_clients',
                        caller_usage._Admitted(caller_usage.MAX_CLIENTS))
    for counter in _COUNTERS:
        counter.clear()
    yield
    for counter in _COUNTERS:
        counter.clear()


def test_caller_types():
    assert caller_usage.caller_of(None, None) == caller_usage.ANONYMOUS
    assert caller_usage.caller_of('sa-abc', 'agent') == caller_usage.Caller(
        'sa-abc', 'agent', 'service_account')
    assert caller_usage.caller_of('ab12cd34', None) == caller_usage.Caller(
        'ab12cd34', 'ab12cd34', 'user')
    assert caller_usage.caller_of(constants.SKYPILOT_SYSTEM_USER_ID,
                                  'x').type == 'system'


def test_pairs_beyond_the_cap_are_recorded_as_other(monkeypatch):
    monkeypatch.setattr(caller_usage, '_caller_names',
                        caller_usage._Admitted(2))
    callers = [caller_usage.caller_of(f'user{i}', f'u{i}') for i in range(3)]
    for caller in callers:
        caller_usage.record_execution(caller, 'sky.status', 'short', 1.0)
    # An admitted pair keeps its labels after the cap is reached.
    caller_usage.record_execution(callers[0], 'sky.status', 'short', 1.0)

    counter = metrics_utils.SKY_APISERVER_CALLER_CALLS_TOTAL
    assert _sample(counter, caller_id='user0') == 2
    assert _sample(counter, caller_id='user1') == 1
    assert _sample(counter, caller_id='user2') == 0
    assert _sample(counter,
                   caller='other',
                   caller_id='other',
                   name='sky.status') == 1
    assert len(_series(counter)) == 3


def test_client_label_sets_beyond_the_cap_are_recorded_as_other(monkeypatch):
    monkeypatch.setattr(caller_usage, '_clients', caller_usage._Admitted(1))
    caller = caller_usage.caller_of('user0', 'u0')
    caller_usage.record_client(caller, None, 'curl/8.0', None)
    caller_usage.record_client(caller, None, 'python-requests/2.32', '12')
    counter = metrics_utils.SKY_APISERVER_CALLER_CLIENT_CALLS_TOTAL
    assert _sample(counter, caller_id='user0', ua_family='curl') == 1
    assert _sample(counter, caller='other', ua_family='python-requests') == 1


@pytest.mark.parametrize('user_agent,family', [
    (None, 'none'),
    ('curl/8.7.1', 'curl'),
    ('python-requests/2.32.3', 'python-requests'),
    ('python-httpx/0.27.0', 'python-httpx'),
    ('Python/3.11 aiohttp/3.9.5', 'python-aiohttp'),
    ('Go-http-client/1.1', 'go'),
    ('node', 'node'),
    ('Mozilla/5.0 (Macintosh)', 'browser'),
    ('kube-probe/1.31', 'kube-probe'),
    ('my-script/1.0', 'other'),
])
def test_ua_family(user_agent, family):
    assert caller_usage.ua_family(user_agent) == family


def test_client_type_and_api_version_are_bounded():
    assert caller_usage.client_type(None) == 'none'
    assert caller_usage.client_type('CLI') == 'cli'
    assert caller_usage.client_type('x' * 33) == 'other'
    assert caller_usage.client_type('a b') == 'other'
    assert caller_usage.api_version(None) == 'none'
    assert caller_usage.api_version('42') == '42'
    assert caller_usage.api_version('42abc') == 'invalid'
    assert caller_usage.api_version('12345') == 'invalid'


@pytest.mark.parametrize('path,params,template', [
    ('/users/list', {}, '/users/list'),
    ('/plugins/api/x/items/abc', {
        'item_id': 'abc'
    }, '/plugins/api/x/items/{item_id}'),
    ('/a/abc/b/abc', {
        'id': 'abc'
    }, '/a/abc/b/{id}'),
    ('/a/abcd', {
        'id': 'abc'
    }, '/a/abcd'),
])
def test_route_template(path, params, template):
    assert caller_usage.route_template(path, params) == template


class _AuthUser:
    """Stands in for the auth middlewares: sets request.state.auth_user."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            scope.setdefault('state', {})['auth_user'] = _SA
        await self.app(scope, receive, send)


def _app() -> fastapi.FastAPI:
    app = fastapi.FastAPI()

    @app.get('/items/{item_id}')
    def get_item(item_id: str):  # pylint: disable=unused-variable
        return {'id': item_id, 'data': 'x' * 100}

    @app.post('/schedule')
    async def schedule():  # pylint: disable=unused-variable
        caller_usage.mark_request_scheduled()
        return {'request_id': 'r'}

    @app.get('/result')
    async def result(request: fastapi.Request):  # pylint: disable=unused-variable
        request.state.request_name = 'sky.jobs.queue_v2'
        return {'return_value': 'y' * 1000}

    @app.get('/api/health')
    async def health():  # pylint: disable=unused-variable
        return {'status': 'healthy'}

    app.add_middleware(_AuthUser)
    app.add_middleware(metrics.PrometheusMiddleware)
    return app


def test_a_route_handler_is_counted_by_route():
    client = TestClient(_app())
    response = client.get('/items/abc',
                          headers={
                              'User-Agent': 'curl/8.0',
                              'X-SkyPilot-API-Version': '42',
                          })
    labels = dict(caller='agent',
                  caller_id=_SA.id,
                  caller_type='service_account',
                  name='GET /items/{item_id}')
    assert _sample(metrics_utils.SKY_APISERVER_CALLER_CALLS_TOTAL,
                   kind='handler',
                   **labels) == 1
    assert _sample(metrics_utils.SKY_APISERVER_CALLER_EXECUTION_SECONDS_TOTAL,
                   **labels) > 0
    assert _sample(
        metrics_utils.SKY_APISERVER_CALLER_RETURNED_RESULT_BYTES_TOTAL,
        **labels) == len(response.content)
    assert _sample(metrics_utils.SKY_APISERVER_CALLER_CLIENT_CALLS_TOTAL,
                   caller_id=_SA.id,
                   client_type='none',
                   ua_family='curl',
                   api_version='42') == 1


def test_a_route_that_scheduled_a_request_is_not_a_handler_call():
    TestClient(_app()).post('/schedule')
    assert _series(metrics_utils.SKY_APISERVER_CALLER_CALLS_TOTAL) == []
    assert _sample(metrics_utils.SKY_APISERVER_CALLER_CLIENT_CALLS_TOTAL) == 1


def test_a_returned_result_is_counted_by_request_name():
    response = TestClient(_app()).get('/result')
    assert _sample(
        metrics_utils.SKY_APISERVER_CALLER_RETURNED_RESULT_BYTES_TOTAL,
        caller_id=_SA.id,
        name='sky.jobs.queue_v2') == len(response.content)
    assert _series(metrics_utils.SKY_APISERVER_CALLER_CALLS_TOTAL) == []


def test_health_probes_are_not_usage():
    TestClient(_app()).get('/api/health')
    assert _series(metrics_utils.SKY_APISERVER_CALLER_CALLS_TOTAL) == []


def test_schedule_request_marks_the_http_call():
    call, token = caller_usage.begin_http_call()
    try:
        with mock.patch.object(executor,
                               'stamp_request_body',
                               side_effect=RuntimeError('stop')):
            with pytest.raises(RuntimeError):
                asyncio.run(
                    executor.schedule_request_async('r', 'status',
                                                    payloads.RequestBody(),
                                                    lambda: None))
    finally:
        caller_usage.end_http_call(token)
    assert call.scheduled_request


def test_execution_is_counted_for_the_request_caller():
    body = payloads.RequestBody()
    body.env_vars[constants.USER_ENV_VAR] = 'agent'
    caller = executor._caller_of(_SA.id, body)
    with executor._caller_execution('r1', caller, 'sky.jobs.queue_v2',
                                    caller_usage.KIND_SYNC):
        pass
    assert _sample(metrics_utils.SKY_APISERVER_CALLER_CALLS_TOTAL,
                   caller='agent',
                   caller_type='service_account',
                   name='sky.jobs.queue_v2',
                   kind='sync') == 1


def test_daemon_requests_are_not_counted():
    caller = caller_usage.caller_of(constants.SKYPILOT_SYSTEM_USER_ID, None)
    with executor._caller_execution('skypilot-status-refresh-daemon', caller,
                                    'sky.status', 'long'):
        pass
    assert _series(metrics_utils.SKY_APISERVER_CALLER_CALLS_TOTAL) == []
