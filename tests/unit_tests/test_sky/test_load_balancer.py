"""Tests for SkyServe load balancer request accounting."""
import asyncio
from unittest import mock

import httpx
import pytest

from sky.serve import load_balancer


def _make_request():
    request = mock.MagicMock()
    request.method = 'GET'
    request.url.path = '/'
    request.url.query = ''
    request.headers.raw = []
    request.body = mock.AsyncMock(return_value=b'')
    return request


def _make_load_balancer():
    return load_balancer.SkyServeLoadBalancer(
        controller_url='http://controller',
        load_balancer_port=30001,
        load_balancing_policy_name='least_load')


async def _run_response_with_client_disconnect(response, spec_version):

    async def receive():
        if spec_version == '2.3':
            return {'type': 'http.disconnect'}
        raise AssertionError('The request should not be received.')

    async def send(message):
        del message
        if spec_version == '2.4':
            raise OSError('client disconnected')

    scope = {'type': 'http', 'asgi': {'spec_version': spec_version}}
    if spec_version == '2.4':
        with pytest.raises(Exception):
            await response(scope, receive, send)
    else:
        await response(scope, receive, send)


@pytest.mark.asyncio
async def test_proxy_error_releases_least_load_accounting():
    lb = _make_load_balancer()
    replica_url = 'http://replica'
    client = mock.MagicMock()
    client.build_request.return_value = mock.sentinel.proxy_request
    client.send = mock.AsyncMock(side_effect=httpx.ReadTimeout('timed out'))
    lb._client_pool[replica_url] = client

    result = await lb._proxy_request_to(replica_url, _make_request())

    assert isinstance(result, httpx.ReadTimeout)
    assert lb._load_balancing_policy.load_map[replica_url] == 0


@pytest.mark.asyncio
async def test_pre_execute_error_preserves_least_load_accounting():
    lb = _make_load_balancer()
    replica_url = 'http://replica'
    request = _make_request()
    policy = lb._load_balancing_policy
    policy.set_ready_replicas([replica_url])
    policy.pre_execute_hook(replica_url, request)

    def raise_before_increment(replica_url_arg, request_arg):
        del replica_url_arg, request_arg
        raise RuntimeError('pre-execute failed')

    policy.pre_execute_hook = raise_before_increment

    with pytest.raises(RuntimeError, match='pre-execute failed'):
        await lb._proxy_request_to(replica_url, request)

    assert policy.load_map[replica_url] == 1


@pytest.mark.parametrize('spec_version', ['2.3', '2.4'])
@pytest.mark.asyncio
async def test_streaming_response_releases_least_load_accounting_on_close(
        spec_version):
    lb = _make_load_balancer()
    replica_url = 'http://replica'
    client = mock.MagicMock()
    client.build_request.return_value = mock.sentinel.proxy_request

    async def response_body():
        yield b'response'

    proxy_response = mock.MagicMock()
    proxy_response.aiter_raw.return_value = response_body()
    proxy_response.status_code = 200
    proxy_response.headers = {}
    proxy_response.aclose = mock.AsyncMock()
    client.send = mock.AsyncMock(return_value=proxy_response)
    lb._client_pool[replica_url] = client

    response = await lb._proxy_request_to(replica_url, _make_request())

    assert lb._load_balancing_policy.load_map[replica_url] == 1
    messages = []

    receive_event = asyncio.Event()

    async def receive():
        await receive_event.wait()

    async def send(message):
        messages.append(message)

    await response({
        'type': 'http',
        'asgi': {
            'spec_version': spec_version
        }
    }, receive, send)

    assert lb._load_balancing_policy.load_map[replica_url] == 0
    assert any(message.get('body') == b'response' for message in messages)
    proxy_response.aclose.assert_awaited_once()


@pytest.mark.parametrize('spec_version', ['2.3', '2.4'])
@pytest.mark.asyncio
async def test_streaming_response_error_releases_least_load_accounting(
        spec_version):
    lb = _make_load_balancer()
    replica_url = 'http://replica'
    client = mock.MagicMock()
    client.build_request.return_value = mock.sentinel.proxy_request

    async def response_body():
        raise httpx.ReadTimeout('timed out')
        yield b'unreachable'

    proxy_response = mock.MagicMock()
    proxy_response.aiter_raw.return_value = response_body()
    proxy_response.status_code = 200
    proxy_response.headers = {}
    proxy_response.aclose = mock.AsyncMock(
        side_effect=RuntimeError('aclose failed'))
    client.send = mock.AsyncMock(return_value=proxy_response)
    lb._client_pool[replica_url] = client

    response = await lb._proxy_request_to(replica_url, _make_request())

    receive_event = asyncio.Event()

    async def receive():
        await receive_event.wait()

    async def send(message):
        del message

    with pytest.raises(httpx.ReadTimeout, match='timed out'):
        await response({
            'type': 'http',
            'asgi': {
                'spec_version': spec_version
            }
        }, receive, send)
    assert lb._load_balancing_policy.load_map[replica_url] == 0
    proxy_response.aclose.assert_awaited_once()


@pytest.mark.parametrize('spec_version', ['2.3', '2.4'])
@pytest.mark.asyncio
async def test_client_disconnect_before_streaming_releases_load_accounting(
        spec_version):
    lb = _make_load_balancer()
    replica_url = 'http://replica'
    client = mock.MagicMock()
    client.build_request.return_value = mock.sentinel.proxy_request

    async def response_body():
        await asyncio.Event().wait()
        yield b'unreachable'

    proxy_response = mock.MagicMock()
    proxy_response.aiter_raw.return_value = response_body()
    proxy_response.status_code = 200
    proxy_response.headers = {}
    proxy_response.aclose = mock.AsyncMock()
    client.send = mock.AsyncMock(return_value=proxy_response)
    lb._client_pool[replica_url] = client

    response = await lb._proxy_request_to(replica_url, _make_request())

    await _run_response_with_client_disconnect(response, spec_version)

    assert lb._load_balancing_policy.load_map[replica_url] == 0
    proxy_response.aclose.assert_awaited_once()


@pytest.mark.parametrize('spec_version', ['2.3', '2.4'])
@pytest.mark.asyncio
async def test_proxy_retries_spread_load_after_failure_and_disconnect(
        spec_version, monkeypatch):
    lb = _make_load_balancer()
    replica_urls = [
        'http://failing-replica',
        'http://healthy-replica-1',
        'http://healthy-replica-2',
    ]
    failing_url = replica_urls[0]
    healthy_responses = []

    def make_proxy_response():

        async def response_body():
            await asyncio.Event().wait()
            yield b'unreachable'

        proxy_response = mock.MagicMock()
        proxy_response.aiter_raw.return_value = response_body()
        proxy_response.status_code = 200
        proxy_response.headers = {}
        proxy_response.aclose = mock.AsyncMock()
        healthy_responses.append(proxy_response)
        return proxy_response

    async def send_to_healthy_replica(proxy_request, **kwargs):
        del proxy_request, kwargs
        return make_proxy_response()

    for replica_url in replica_urls:
        client = mock.MagicMock()
        client.build_request.return_value = mock.sentinel.proxy_request
        if replica_url == failing_url:
            client.send = mock.AsyncMock(
                side_effect=httpx.ReadTimeout('timed out'))
        else:
            client.send = mock.AsyncMock(side_effect=send_to_healthy_replica)
        lb._client_pool[replica_url] = client

    policy = lb._load_balancing_policy
    policy.set_ready_replicas(replica_urls)
    request = _make_request()
    request.is_disconnected = mock.AsyncMock(return_value=False)
    monkeypatch.setattr(load_balancer.asyncio, 'sleep', mock.AsyncMock())

    # The failed attempt and every disconnected response must release its
    # load before the next request is selected.
    for _ in range(4):
        response = await lb._proxy_with_retries(request)
        await _run_response_with_client_disconnect(response, spec_version)
        assert all(
            policy.load_map[replica_url] == 0 for replica_url in replica_urls)

    assert [
        lb._client_pool[replica_url].send.await_count
        for replica_url in replica_urls
    ] == [2, 2, 2]
    assert all(
        response.aclose.await_count == 1 for response in healthy_responses)


@pytest.mark.parametrize('spec_version', ['2.3', '2.4'])
@pytest.mark.asyncio
async def test_proxy_stops_retrying_after_client_disconnect_without_leaking_load(
        spec_version):
    lb = _make_load_balancer()
    replica_urls = [
        'http://failing-replica',
        'http://healthy-replica-1',
        'http://healthy-replica-2',
    ]
    healthy_responses = []

    def make_proxy_response():

        async def response_body():
            await asyncio.Event().wait()
            yield b'unreachable'

        proxy_response = mock.MagicMock()
        proxy_response.aiter_raw.return_value = response_body()
        proxy_response.status_code = 200
        proxy_response.headers = {}
        proxy_response.aclose = mock.AsyncMock()
        healthy_responses.append(proxy_response)
        return proxy_response

    async def send_to_healthy_replica(proxy_request, **kwargs):
        del proxy_request, kwargs
        return make_proxy_response()

    clients = {}
    for replica_url in replica_urls:
        client = mock.MagicMock()
        client.build_request.return_value = mock.sentinel.proxy_request
        if replica_url == replica_urls[0]:
            client.send = mock.AsyncMock(
                side_effect=httpx.ReadTimeout('timed out'))
        else:
            client.send = mock.AsyncMock(side_effect=send_to_healthy_replica)
        clients[replica_url] = client
        lb._client_pool[replica_url] = client

    policy = lb._load_balancing_policy
    policy.set_ready_replicas(replica_urls)
    request = _make_request()
    request.is_disconnected = mock.AsyncMock(return_value=True)

    result_statuses = []
    for _ in range(4):
        result = await lb._proxy_with_retries(request)
        result_statuses.append(result.status_code)
        if result.status_code != 499:
            await _run_response_with_client_disconnect(result, spec_version)
        assert all(
            policy.load_map[replica_url] == 0 for replica_url in replica_urls)

    assert result_statuses == [499, 200, 200, 499]
    assert request.is_disconnected.await_count == 2
    assert [
        clients[replica_url].send.await_count for replica_url in replica_urls
    ] == [2, 1, 1]
    assert all(
        response.aclose.await_count == 1 for response in healthy_responses)


@pytest.mark.asyncio
async def test_missing_client_releases_least_load_accounting():
    lb = _make_load_balancer()
    replica_url = 'http://replica'

    result = await lb._proxy_request_to(replica_url, _make_request())

    assert isinstance(result, RuntimeError)
    assert lb._load_balancing_policy.load_map[replica_url] == 0


def test_retired_replica_load_is_removed_after_inflight_request_finishes():
    policy = load_balancer.lb_policies.LeastLoadPolicy()
    replica_url = 'http://replica'
    request = _make_request()

    policy.set_ready_replicas([replica_url])
    policy.pre_execute_hook(replica_url, request)
    policy.set_ready_replicas([])
    assert policy.load_map[replica_url] == 1
    policy.set_ready_replicas([replica_url])
    assert policy.load_map[replica_url] == 1

    policy.post_execute_hook(replica_url, request)

    assert policy.load_map[replica_url] == 0
    policy.set_ready_replicas([])
    assert replica_url not in policy.load_map


def test_least_load_rotates_equal_load_ties():
    policy = load_balancer.lb_policies.LeastLoadPolicy()
    replicas = ['http://replica-1', 'http://replica-2', 'http://replica-3']
    policy.set_ready_replicas(replicas)

    selected_replicas = [policy._select_replica(None) for _ in range(6)]

    assert selected_replicas == replicas * 2


def test_instance_aware_least_load_rotates_equal_load_ties():
    policy = load_balancer.lb_policies.InstanceAwareLeastLoadPolicy()
    replicas = ['http://replica-1', 'http://replica-2']
    policy.set_ready_replicas(replicas)

    selected_replicas = [policy._select_replica(None) for _ in range(4)]

    assert selected_replicas == replicas * 2
