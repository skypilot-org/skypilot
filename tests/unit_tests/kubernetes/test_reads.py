"""Tests for sky.provision.kubernetes.reads."""
# pylint: disable=redefined-outer-name
import json
import threading
from unittest import mock

import pytest

from sky import exceptions
from sky.adaptors import kubernetes
from sky.provision.kubernetes import reads
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.utils import plugin_extensions
from sky.utils.plugin_extensions import cluster_state_source

_POD_LIST = {
    'kind': 'PodList',
    'apiVersion': 'v1',
    'metadata': {
        'resourceVersion': '42'
    },
    'items': [{
        'metadata': {
            'name': 'a',
            'namespace': 'ns'
        }
    }, {
        'metadata': {
            'name': 'b',
            'namespace': 'ns'
        }
    }],
}


class _Response:

    def __init__(self, body):
        self.data = json.dumps(body).encode()
        self.released = False

    def release_conn(self):
        self.released = True


class _ApiClient:
    """Answers GETs from a list of bodies or exceptions, in order."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def call_api(self, path, method, **kwargs):
        self.calls.append((path, method, kwargs))
        answer = self.answers.pop(0) if len(self.answers) > 1 else (
            self.answers[0])
        if isinstance(answer, Exception):
            raise answer
        return _Response(answer)


def _api_error(status):
    return kubernetes.api_exception()(status=status, reason='test')


@pytest.fixture
def api(monkeypatch):
    """Installs a fake API client; returns a function that sets its answers."""
    clients = {}

    def install(*answers):
        clients['client'] = _ApiClient(*answers)
        return clients['client']

    monkeypatch.setattr(reads.kubernetes,
                        'api_client',
                        lambda context=None: clients['client'])
    monkeypatch.setattr(kubernetes_utils, '_CONTEXT_UNREACHABLE_CHECKS', [])
    monkeypatch.setattr(reads, '_DIRECT_READ_OBSERVERS', [])
    monkeypatch.setattr(plugin_extensions.ClusterStateSource, '_provider_func',
                        None)
    return install


@pytest.fixture
def metrics(monkeypatch):
    recorded = mock.Mock()
    monkeypatch.setattr(reads.metrics_utils, 'count_kube_read',
                        recorded.count_kube_read)
    monkeypatch.setattr(reads.metrics_utils, 'observe_kube_read_api_call',
                        recorded.observe_kube_read_api_call)
    monkeypatch.setattr(reads.metrics_utils, 'observe_kube_read_staleness',
                        recorded.observe_kube_read_staleness)
    return recorded


def _register(provider):
    plugin_extensions.ClusterStateSource.register(provider)


@pytest.mark.parametrize('resource,namespace,name,path', [
    (reads.NODES, None, None, '/api/v1/nodes'),
    (reads.NODES, None, 'n1', '/api/v1/nodes/n1'),
    (reads.PODS, None, None, '/api/v1/pods'),
    (reads.PODS, 'ns', None, '/api/v1/namespaces/ns/pods'),
    (reads.PODS, 'ns', 'p', '/api/v1/namespaces/ns/pods/p'),
    (reads.Resource('kueue.x-k8s.io', 'v1beta2', 'localqueues', True), 'ns',
     None, '/apis/kueue.x-k8s.io/v1beta2/namespaces/ns/localqueues'),
])
def test_resource_path(resource, namespace, name, path):
    assert resource.path(namespace, name) == path


def test_list_reads_the_api_server_without_a_source(api, metrics):
    client = api(_POD_LIST)

    result = reads.list_objects('ctx',
                                reads.PODS,
                                namespace='ns',
                                label_selector='app=web',
                                field_selector='status.phase=Running')

    assert [p['metadata']['name'] for p in result.items] == ['a', 'b']
    assert result.resource_version == '42'
    assert result.source == reads.SOURCE_DIRECT
    assert result.staleness_seconds == 0
    assert result.reason == reads.REASON_NO_SOURCE
    path, method, kwargs = client.calls[0]
    assert (path, method) == ('/api/v1/namespaces/ns/pods', 'GET')
    assert kwargs['query_params'] == [('labelSelector', 'app=web'),
                                      ('fieldSelector', 'status.phase=Running')]
    assert kwargs['_preload_content'] is False
    assert kwargs['_request_timeout'] == kubernetes.API_TIMEOUT
    metrics.count_kube_read.assert_called_once_with('ctx', 'pods',
                                                    reads.SOURCE_DIRECT,
                                                    reads.REASON_NO_SOURCE)
    metrics.observe_kube_read_api_call.assert_called_once_with(
        'pods', reads.OUTCOME_OK, mock.ANY)


def test_cached_read_is_served_by_the_source(api, metrics):
    client = api(_POD_LIST)
    queries = []

    def provider(query):
        queries.append(query)
        return plugin_extensions.CachedObjects(items=[{
            'metadata': {
                'name': 'cached'
            }
        }],
                                               resource_version='7',
                                               staleness_seconds=12.5)

    _register(provider)

    result = reads.list_objects('ctx',
                                reads.PODS,
                                namespace='ns',
                                label_selector='app=web',
                                consistency=reads.Cached(max_age=30))

    assert result.items == [{'metadata': {'name': 'cached'}}]
    assert result.resource_version == '7'
    assert result.source == reads.SOURCE_CACHE
    assert result.staleness_seconds == 12.5
    assert result.reason is None
    assert client.calls == []
    assert queries == [
        plugin_extensions.ObjectQuery(context='ctx',
                                      group='',
                                      version='v1',
                                      resource='pods',
                                      namespace='ns',
                                      name=None,
                                      label_selector='app=web',
                                      field_selector=None,
                                      max_staleness_seconds=30)
    ]
    metrics.count_kube_read.assert_called_once_with('ctx', 'pods',
                                                    reads.SOURCE_CACHE, '')
    metrics.observe_kube_read_staleness.assert_called_once_with('pods', 12.5)


def test_default_consistency_accepts_a_minute_of_watch_failure(api):
    api(_POD_LIST)
    queries = []
    _register(lambda query: queries.append(query) or plugin_extensions.Refusal(
        cluster_state_source.WATCH_FAILING))

    reads.list_objects('ctx', reads.NODES)

    assert queries[0].max_staleness_seconds == 60


@pytest.mark.parametrize('reason', [
    cluster_state_source.CONTEXT_NOT_REGISTERED,
    cluster_state_source.RESOURCE_NOT_CACHED,
    cluster_state_source.UNSUPPORTED_SELECTOR,
    cluster_state_source.WATCH_FAILING,
    cluster_state_source.ERROR,
])
def test_refused_read_goes_to_the_api_server(api, metrics, reason):
    client = api(_POD_LIST)
    _register(lambda query: plugin_extensions.Refusal(reason, 'details'))

    result = reads.list_objects('ctx', reads.PODS)

    assert result.source == reads.SOURCE_DIRECT
    assert result.reason == reason
    assert len(result.items) == 2
    assert len(client.calls) == 1
    metrics.count_kube_read.assert_called_once_with('ctx', 'pods',
                                                    reads.SOURCE_DIRECT, reason)


def test_failing_source_counts_as_an_error_refusal(api):
    api(_POD_LIST)

    def provider(query):
        raise RuntimeError('boom')

    _register(provider)

    result = reads.list_objects('ctx', reads.PODS)

    assert result.source == reads.SOURCE_DIRECT
    assert result.reason == cluster_state_source.ERROR


def test_fresh_read_does_not_ask_the_source(api):
    client = api(_POD_LIST)
    provider = mock.Mock()
    _register(provider)

    result = reads.list_objects('ctx', reads.PODS, consistency=reads.FRESH)

    provider.assert_not_called()
    assert result.reason == reads.REASON_FRESH
    assert len(client.calls) == 1


def test_get_object(api):
    pod = {'metadata': {'name': 'p', 'namespace': 'ns', 'resourceVersion': '9'}}
    client = api(pod)

    result = reads.get_object('ctx', reads.PODS, 'p', namespace='ns')

    assert result.items == [pod]
    assert result.resource_version == '9'
    assert client.calls[0][0] == '/api/v1/namespaces/ns/pods/p'


def test_get_missing_object_returns_no_items(api, metrics):
    api(_api_error(404))

    result = reads.get_object('ctx', reads.NODES, 'gone')

    assert not result.items
    assert result.resource_version == ''
    metrics.observe_kube_read_api_call.assert_called_once_with(
        'nodes', reads.OUTCOME_OK, mock.ANY)


def test_get_from_the_source_passes_the_name(api):
    api(_POD_LIST)
    queries = []

    def provider(query):
        queries.append(query)
        return plugin_extensions.CachedObjects(items=[],
                                               resource_version='1',
                                               staleness_seconds=0)

    _register(provider)

    result = reads.get_object('ctx', reads.PODS, 'p', namespace='ns')

    assert not result.items
    assert result.source == reads.SOURCE_CACHE
    assert (queries[0].namespace, queries[0].name) == ('ns', 'p')


def test_list_of_an_unserved_resource_raises(api):
    api(_api_error(404))
    queues = reads.Resource('kueue.x-k8s.io', 'v1beta2', 'localqueues', True)

    with pytest.raises(reads.ResourceNotServedError):
        reads.list_objects('ctx', queues)


def test_namespace_rules(api):
    api(_POD_LIST)
    with pytest.raises(ValueError):
        reads.list_objects('ctx', reads.NODES, namespace='ns')
    with pytest.raises(ValueError):
        reads.get_object('ctx', reads.PODS, 'p')


def test_known_unreachable_context_fails_fast(api):
    client = api(_POD_LIST)
    observer = mock.Mock()
    reads.register_direct_read_observer(observer)
    kubernetes_utils.register_context_unreachable_check(
        lambda context: 'agent disconnected' if context == 'down' else None)

    with pytest.raises(exceptions.KubeAPIUnreachableError,
                       match='agent disconnected'):
        reads.list_objects('down', reads.PODS)

    assert client.calls == []
    observer.assert_not_called()


def test_observer_sees_every_api_call(api, monkeypatch):
    api(_api_error(500), _POD_LIST)
    monkeypatch.setattr(kubernetes_utils.time, 'sleep', lambda seconds: None)
    observer = mock.Mock()
    reads.register_direct_read_observer(observer)
    reads.register_direct_read_observer(observer)

    reads.list_objects('ctx', reads.PODS)

    assert [c.args[:2] for c in observer.call_args_list
           ] == [('ctx', reads.OUTCOME_ERROR), ('ctx', reads.OUTCOME_OK)]


def test_forbidden_read_raises_unreachable(api):
    api(_api_error(403))

    with pytest.raises(exceptions.KubeAPIUnreachableError):
        reads.list_objects('ctx', reads.PODS)


def test_failing_observer_does_not_fail_the_read(api):
    api(_POD_LIST)

    def observer(context, outcome, duration):
        raise RuntimeError('boom')

    reads.register_direct_read_observer(observer)

    assert len(reads.list_objects('ctx', reads.PODS).items) == 2


@pytest.mark.usefixtures('api')
def test_concurrent_identical_reads_share_one_api_call(monkeypatch):
    follower_waiting = threading.Event()
    release = threading.Event()

    class _FollowerAwareEvent(threading.Event):

        def wait(self, timeout=None):
            follower_waiting.set()
            return super().wait(timeout)

    class _Call(reads._Call):  # pylint: disable=protected-access

        def __init__(self):
            super().__init__()
            self.done = _FollowerAwareEvent()

    monkeypatch.setattr(reads, '_Call', _Call)

    class _BlockingClient(_ApiClient):

        def call_api(self, path, method, **kwargs):
            release.wait(timeout=10)
            return super().call_api(path, method, **kwargs)

    client = _BlockingClient(_POD_LIST)
    monkeypatch.setattr(reads.kubernetes,
                        'api_client',
                        lambda context=None: client)
    results = {}

    def read(key):
        results[key] = reads.list_objects('ctx', reads.PODS, namespace='ns')

    leader = threading.Thread(target=read, args=('leader',))
    leader.start()
    follower = threading.Thread(target=read, args=('follower',))
    follower.start()
    assert follower_waiting.wait(timeout=10)
    release.set()
    leader.join(timeout=10)
    follower.join(timeout=10)

    assert len(client.calls) == 1
    assert results['leader'].items == results['follower'].items
    assert results['leader'].items is not results['follower'].items


def test_reads_after_a_shared_call_make_a_new_call(api):
    client = api(_POD_LIST)

    reads.list_objects('ctx', reads.PODS)
    reads.list_objects('ctx', reads.PODS)

    assert len(client.calls) == 2
