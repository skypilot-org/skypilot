"""Tests for SkyServe load balancer request accounting."""
import asyncio
from unittest import mock

import httpx
import pytest

from sky.serve import load_balancer
from sky.serve import load_balancing_policies


def _make_request():
    request = mock.MagicMock()
    request.method = 'GET'
    request.url.path = '/'
    request.url.query = ''
    request.headers.raw = []
    request.body = mock.AsyncMock(return_value=b'')
    return request


def _make_replica(replica_id=1, url='http://replica', gpu_type='unknown'):
    return load_balancing_policies.ReadyReplica(replica_id, url, gpu_type)


def _make_load_balancer():
    return load_balancer.SkyServeLoadBalancer(
        controller_url='http://controller',
        load_balancer_port=30001,
        load_balancing_policy_name='least_load')


@pytest.mark.asyncio
async def test_sync_builds_ready_replica_descriptors(monkeypatch):
    lb = _make_load_balancer()
    response = mock.MagicMock()
    response.raise_for_status = mock.Mock()
    response.json = mock.AsyncMock(
        return_value={
            'replica_info': [{
                'replica_id': 1,
                'url': 'http://replica-1',
                'gpu_type': 'A100',
            }, {
                'replica_id': 2,
                'url': 'http://replica-2',
            }]
        })
    response_context = mock.MagicMock()
    response_context.__aenter__.return_value = response
    session = mock.MagicMock()
    session.post.return_value = response_context
    session_context = mock.MagicMock()
    session_context.__aenter__.return_value = session
    monkeypatch.setattr(load_balancer.aiohttp, 'ClientSession',
                        mock.Mock(return_value=session_context))

    close_client_tasks = await lb._sync_with_controller_once()

    assert close_client_tasks == []
    assert lb._load_balancing_policy.ready_replicas == [
        _make_replica(1, 'http://replica-1', 'A100'),
        _make_replica(2, 'http://replica-2'),
    ]
    assert set(lb._client_pool) == {
        'http://replica-1',
        'http://replica-2',
    }


@pytest.mark.asyncio
async def test_sync_rejects_non_list_replica_info(monkeypatch):
    lb = _make_load_balancer()
    response = mock.MagicMock()
    response.raise_for_status = mock.Mock()
    response.json = mock.AsyncMock(return_value={'replica_info': {}})
    response_context = mock.MagicMock()
    response_context.__aenter__.return_value = response
    session = mock.MagicMock()
    session.post.return_value = response_context
    session_context = mock.MagicMock()
    session_context.__aenter__.return_value = session
    monkeypatch.setattr(load_balancer.aiohttp, 'ClientSession',
                        mock.Mock(return_value=session_context))

    with pytest.raises(ValueError, match='Expected replica_info to be a list'):
        await lb._sync_with_controller_once()


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


def _make_streaming_response(response_body, aclose_side_effect=None):
    proxy_response = mock.MagicMock()
    proxy_response.aiter_raw.return_value = response_body
    proxy_response.status_code = 200
    proxy_response.headers = {}
    proxy_response.aclose = mock.AsyncMock(side_effect=aclose_side_effect)
    return proxy_response


def _configure_multi_replica_clients(lb, replica_urls, failing_url):
    healthy_responses = []

    def make_proxy_response():

        async def response_body():
            await asyncio.Event().wait()
            yield b'unreachable'

        proxy_response = _make_streaming_response(response_body())
        healthy_responses.append(proxy_response)
        return proxy_response

    async def send_to_healthy_replica(proxy_request, **kwargs):
        del proxy_request, kwargs
        return make_proxy_response()

    clients = {}
    for replica_url in replica_urls:
        client = mock.MagicMock()
        client.build_request.return_value = mock.sentinel.proxy_request
        if replica_url == failing_url:
            client.send = mock.AsyncMock(
                side_effect=httpx.ReadTimeout('timed out'))
        else:
            client.send = mock.AsyncMock(side_effect=send_to_healthy_replica)
        clients[replica_url] = client
        lb._client_pool[replica_url] = client

    return clients, healthy_responses


@pytest.mark.asyncio
async def test_proxy_error_releases_least_load_accounting():
    lb = _make_load_balancer()
    replica = _make_replica()
    policy = lb._load_balancing_policy
    policy.set_ready_replicas([replica])
    client = mock.MagicMock()
    client.build_request.return_value = mock.sentinel.proxy_request
    client.send = mock.AsyncMock(side_effect=httpx.ReadTimeout('timed out'))
    lb._client_pool[replica.url] = client

    result = await lb._proxy_request_to(replica, _make_request())

    assert isinstance(result, httpx.ReadTimeout)
    assert policy.load_map.get(replica.replica_id) == 0


@pytest.mark.asyncio
async def test_begin_request_error_preserves_least_load_accounting():
    lb = _make_load_balancer()
    replica = _make_replica()
    request = _make_request()
    policy = lb._load_balancing_policy
    policy.set_ready_replicas([replica])
    policy.pre_execute_hook(replica, request)

    def raise_begin_request(replica_arg, request_arg):
        del replica_arg, request_arg
        raise RuntimeError('begin request failed')

    policy.begin_request = raise_begin_request

    with pytest.raises(RuntimeError, match='begin request failed'):
        await lb._proxy_request_to(replica, request)

    assert policy.load_map.get(replica.replica_id) == 1


@pytest.mark.parametrize('spec_version', ['2.3', '2.4'])
@pytest.mark.asyncio
async def test_streaming_response_releases_least_load_accounting_on_close(
        spec_version):
    lb = _make_load_balancer()
    replica = _make_replica()
    client = mock.MagicMock()
    client.build_request.return_value = mock.sentinel.proxy_request

    async def response_body():
        yield b'response'

    proxy_response = _make_streaming_response(response_body())
    client.send = mock.AsyncMock(return_value=proxy_response)
    lb._client_pool[replica.url] = client
    lb._load_balancing_policy.set_ready_replicas([replica])

    response = await lb._proxy_request_to(replica, _make_request())

    assert lb._load_balancing_policy.load_map.get(replica.replica_id) == 1
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

    assert lb._load_balancing_policy.load_map.get(replica.replica_id) == 0
    assert any(message.get('body') == b'response' for message in messages)
    proxy_response.aclose.assert_awaited_once()


@pytest.mark.parametrize('spec_version', ['2.3', '2.4'])
@pytest.mark.asyncio
async def test_streaming_response_error_releases_least_load_accounting(
        spec_version):
    lb = _make_load_balancer()
    replica = _make_replica()
    client = mock.MagicMock()
    client.build_request.return_value = mock.sentinel.proxy_request

    async def response_body():
        raise httpx.ReadTimeout('timed out')
        yield b'unreachable'

    proxy_response = _make_streaming_response(
        response_body(), aclose_side_effect=RuntimeError('aclose failed'))
    client.send = mock.AsyncMock(return_value=proxy_response)
    lb._client_pool[replica.url] = client
    lb._load_balancing_policy.set_ready_replicas([replica])

    response = await lb._proxy_request_to(replica, _make_request())

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
    assert lb._load_balancing_policy.load_map.get(replica.replica_id) == 0
    proxy_response.aclose.assert_awaited_once()


@pytest.mark.parametrize('spec_version', ['2.3', '2.4'])
@pytest.mark.asyncio
async def test_client_disconnect_before_streaming_releases_load_accounting(
        spec_version):
    lb = _make_load_balancer()
    replica = _make_replica()
    client = mock.MagicMock()
    client.build_request.return_value = mock.sentinel.proxy_request

    async def response_body():
        await asyncio.Event().wait()
        yield b'unreachable'

    proxy_response = _make_streaming_response(response_body())
    client.send = mock.AsyncMock(return_value=proxy_response)
    lb._client_pool[replica.url] = client
    lb._load_balancing_policy.set_ready_replicas([replica])

    response = await lb._proxy_request_to(replica, _make_request())

    await _run_response_with_client_disconnect(response, spec_version)

    assert lb._load_balancing_policy.load_map.get(replica.replica_id) == 0
    proxy_response.aclose.assert_awaited_once()


@pytest.mark.parametrize('spec_version', ['2.3', '2.4'])
@pytest.mark.asyncio
async def test_proxy_retries_spread_load_after_failure_and_disconnect(
        spec_version, monkeypatch):
    lb = _make_load_balancer()
    replicas = [
        _make_replica(1, 'http://failing-replica'),
        _make_replica(2, 'http://healthy-replica-1'),
        _make_replica(3, 'http://healthy-replica-2'),
    ]
    replica_urls = [replica.url for replica in replicas]
    failing_url = replica_urls[0]
    _, healthy_responses = _configure_multi_replica_clients(
        lb, replica_urls, failing_url)

    policy = lb._load_balancing_policy
    policy.set_ready_replicas(replicas)
    request = _make_request()
    request.is_disconnected = mock.AsyncMock(return_value=False)
    monkeypatch.setattr(load_balancer.asyncio, 'sleep', mock.AsyncMock())

    # The failed attempt and every disconnected response must release its
    # load before the next request is selected.
    for _ in range(4):
        response = await lb._proxy_with_retries(request)
        await _run_response_with_client_disconnect(response, spec_version)
        assert all(
            policy.load_map.get(replica.replica_id) == 0
            for replica in replicas)

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
    replicas = [
        _make_replica(1, 'http://failing-replica'),
        _make_replica(2, 'http://healthy-replica-1'),
        _make_replica(3, 'http://healthy-replica-2'),
    ]
    replica_urls = [replica.url for replica in replicas]
    clients, healthy_responses = _configure_multi_replica_clients(
        lb, replica_urls, replica_urls[0])

    policy = lb._load_balancing_policy
    policy.set_ready_replicas(replicas)
    request = _make_request()
    request.is_disconnected = mock.AsyncMock(return_value=True)

    result_statuses = []
    for _ in range(4):
        result = await lb._proxy_with_retries(request)
        result_statuses.append(result.status_code)
        if result.status_code != 499:
            await _run_response_with_client_disconnect(result, spec_version)
        assert all(
            policy.load_map.get(replica.replica_id) == 0
            for replica in replicas)

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
    replica = _make_replica()
    policy = lb._load_balancing_policy
    policy.set_ready_replicas([replica])

    result = await lb._proxy_request_to(replica, _make_request())

    assert isinstance(result, RuntimeError)
    assert policy.load_map.get(replica.replica_id) == 0


def test_late_completion_does_not_recreate_retired_replica_load():
    policy = load_balancing_policies.LeastLoadPolicy()
    replica = _make_replica()
    request = _make_request()

    policy.set_ready_replicas([replica])
    policy.pre_execute_hook(replica, request)
    policy.set_ready_replicas([])
    assert replica.replica_id in policy.load_map

    policy.post_execute_hook(replica, request)

    assert replica.replica_id not in policy.load_map


def test_late_completion_does_not_decrement_reused_url_replica():
    policy = load_balancing_policies.LeastLoadPolicy()
    old_replica = _make_replica(1, 'http://replica')
    new_replica = _make_replica(2, 'http://replica')
    request = _make_request()

    policy.set_ready_replicas([old_replica])
    policy.pre_execute_hook(old_replica, request)
    policy.set_ready_replicas([new_replica])
    policy.pre_execute_hook(new_replica, request)

    policy.post_execute_hook(old_replica, request)

    assert old_replica.replica_id not in policy.load_map
    assert policy.load_map[new_replica.replica_id] == 1


def test_endpoint_change_preserves_replica_load_accounting():
    policy = load_balancing_policies.LeastLoadPolicy()
    old_replica = _make_replica(1, 'http://old-replica')
    new_replica = _make_replica(1, 'http://new-replica')
    request = _make_request()

    policy.set_ready_replicas([old_replica])
    policy.pre_execute_hook(old_replica, request)
    policy.set_ready_replicas([new_replica])

    assert policy.load_map[new_replica.replica_id] == 1
    policy.post_execute_hook(old_replica, request)
    assert policy.load_map[new_replica.replica_id] == 0


def test_least_load_rotates_equal_load_ties():
    policy = load_balancing_policies.LeastLoadPolicy()
    replicas = [
        _make_replica(1, 'http://replica-1'),
        _make_replica(2, 'http://replica-2'),
        _make_replica(3, 'http://replica-3'),
    ]
    policy.set_ready_replicas(replicas)

    selected_replicas = [policy._select_replica(None) for _ in range(6)]

    assert selected_replicas == replicas * 2


def test_instance_aware_least_load_rotates_equal_load_ties():
    policy = load_balancing_policies.InstanceAwareLeastLoadPolicy()
    replicas = [
        _make_replica(1, 'http://replica-1', 'A100'),
        _make_replica(2, 'http://replica-2', 'A100'),
    ]
    policy.set_ready_replicas(replicas)

    selected_replicas = [policy._select_replica(None) for _ in range(4)]

    assert selected_replicas == replicas * 2
