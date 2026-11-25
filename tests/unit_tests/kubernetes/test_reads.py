"""Tests for sky.provision.kubernetes.reads."""
# pylint: disable=redefined-outer-name
from concurrent import futures
import io
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
        },
        'spec': {
            'priority': 1.5
        }
    }, {
        'metadata': {
            'name': 'b',
            'namespace': 'ns'
        }
    }],
}


class _Response(io.BytesIO):
    """A urllib3 response read with _preload_content=False."""

    @property
    def data(self):
        return self.read()

    def release_conn(self):
        pass


class _ApiClient:
    """Answers GETs with the given bodies or exceptions, in order."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []
        self.contexts = []

    def call_api(self, path, method, **kwargs):
        self.calls.append((path, method, kwargs))
        answer = self.answers.pop(0) if len(self.answers) > 1 else (
            self.answers[0])
        if isinstance(answer, Exception):
            raise answer
        return _Response(json.dumps(answer).encode())


def _api_error(status):
    return kubernetes.api_exception()(status=status, reason='test')


@pytest.fixture
def api(monkeypatch):
    """Installs a fake API client; returns a function that sets its answers."""
    clients = {}

    def install(*answers):
        clients['client'] = _ApiClient(*answers)
        return clients['client']

    def api_client(context=None):
        clients['client'].contexts.append(context)
        return clients['client']

    monkeypatch.setattr(reads.kubernetes, 'api_client', api_client)
    monkeypatch.setattr(kubernetes_utils, '_CONTEXT_UNREACHABLE_CHECKS', [])
    monkeypatch.setattr(kubernetes_utils, '_CONTEXT_CALL_OBSERVERS', [])
    monkeypatch.setattr(plugin_extensions.ClusterStateSource, '_provider_func',
                        None)
    return install


@pytest.fixture
def metrics(monkeypatch):
    recorded = mock.Mock()
    monkeypatch.setattr(reads, 'metrics_utils', recorded)
    return recorded


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

    assert result.items == _POD_LIST['items']
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
        'pods', 'ok', mock.ANY)


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

    plugin_extensions.ClusterStateSource.register(provider)

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
    assert not client.calls
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


def test_cached_reads_share_in_flight_source_calls(api, monkeypatch):
    api(_POD_LIST)
    plugin_extensions.ClusterStateSource.register(
        lambda query: plugin_extensions.CachedObjects(
            items=[], resource_version='1', staleness_seconds=0))
    keys = []
    in_flight = reads._IN_FLIGHT  # pylint: disable=protected-access

    class _Recording:

        def do(self, key, fn):
            keys.append(key)
            return in_flight.do(key, fn)

    monkeypatch.setattr(reads, '_IN_FLIGHT', _Recording())

    result = reads.list_objects('ctx', reads.PODS, namespace='ns')

    assert result.source == reads.SOURCE_CACHE
    assert keys == [
        plugin_extensions.ObjectQuery(context='ctx',
                                      group='',
                                      version='v1',
                                      resource='pods',
                                      namespace='ns',
                                      name=None,
                                      label_selector=None,
                                      field_selector=None,
                                      max_staleness_seconds=60)
    ]


def test_default_consistency_accepts_a_minute_of_watch_failure(api):
    api(_POD_LIST)
    queries = []
    plugin_extensions.ClusterStateSource.register(lambda query: queries.append(
        query) or plugin_extensions.Refusal(cluster_state_source.WATCH_FAILING))

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
    plugin_extensions.ClusterStateSource.register(
        lambda query: plugin_extensions.Refusal(reason, 'details'))

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

    plugin_extensions.ClusterStateSource.register(provider)

    result = reads.list_objects('ctx', reads.PODS)

    assert result.source == reads.SOURCE_DIRECT
    assert result.reason == cluster_state_source.ERROR


def test_fresh_read_does_not_ask_the_source(api):
    client = api(_POD_LIST)
    provider = mock.Mock()
    plugin_extensions.ClusterStateSource.register(provider)

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
        'nodes', 'ok', mock.ANY)


def test_get_from_the_source_passes_the_name(api):
    api(_POD_LIST)
    queries = []

    def provider(query):
        queries.append(query)
        return plugin_extensions.CachedObjects(items=[],
                                               resource_version='42',
                                               staleness_seconds=0)

    plugin_extensions.ClusterStateSource.register(provider)

    result = reads.get_object('ctx', reads.PODS, 'p', namespace='ns')

    assert not result.items
    assert result.resource_version == ''
    assert result.source == reads.SOURCE_CACHE
    assert (queries[0].namespace, queries[0].name) == ('ns', 'p')


def test_get_from_the_source_reports_the_object_version(api):
    api(_POD_LIST)
    node = {'metadata': {'name': 'n1', 'resourceVersion': '7'}}
    plugin_extensions.ClusterStateSource.register(
        lambda query: plugin_extensions.CachedObjects(
            items=[node], resource_version='42', staleness_seconds=0))

    result = reads.get_object('ctx', reads.NODES, 'n1')

    assert result.items == [node]
    assert result.resource_version == '7'


def test_unnamed_context_reads_the_default_api_server(api, metrics):
    client = api(_POD_LIST)
    provider = mock.Mock()
    plugin_extensions.ClusterStateSource.register(provider)

    result = reads.list_objects(None, reads.PODS)

    provider.assert_not_called()
    assert client.contexts == [None]
    assert result.reason == reads.REASON_UNNAMED_CONTEXT
    metrics.count_kube_read.assert_called_once_with(
        '', 'pods', reads.SOURCE_DIRECT, reads.REASON_UNNAMED_CONTEXT)


def test_list_of_an_unserved_resource_raises(api):
    api(_api_error(404))
    queues = reads.Resource('kueue.x-k8s.io', 'v1beta2', 'localqueues', True)

    with pytest.raises(exceptions.KubernetesResourceNotServedError):
        reads.list_objects('ctx', queues)


def test_list_of_a_core_resource_answering_404_is_unreachable(api):
    api(_api_error(404))

    with pytest.raises(exceptions.KubeAPIUnreachableError):
        reads.list_objects('ctx', reads.NODES)


def test_api_errors_name_the_resource(api, monkeypatch):
    monkeypatch.setattr(kubernetes_utils.time, 'sleep', lambda seconds: None)
    api(_api_error(500))

    with pytest.raises(exceptions.KubeAPIUnreachableError,
                       match='(?s)node info.*kubectl get nodes --context ctx'):
        reads.list_objects('ctx', reads.NODES)


def test_a_body_cut_off_mid_read_is_retried(api, monkeypatch):
    monkeypatch.setattr(kubernetes_utils.time, 'sleep', lambda seconds: None)
    client = api(_POD_LIST)

    class _CutOff(_Response):

        def read(self, *args, **kwargs):
            raise kubernetes.urllib3.exceptions.ProtocolError(
                'Connection broken')

    calls = []

    def call_api(path, method, **kwargs):
        del method, kwargs
        calls.append(path)
        return _CutOff(b'')

    monkeypatch.setattr(client, 'call_api', call_api)

    with pytest.raises(exceptions.KubeAPIUnreachableError):
        reads.list_objects('ctx', reads.PODS)
    assert len(calls) == kubernetes_utils.DEFAULT_MAX_RETRIES


def test_an_interrupted_attempt_is_observed_as_failed(api, monkeypatch):
    client = api(_POD_LIST)
    observed = []
    kubernetes_utils.register_context_call_observer(
        lambda context, error, duration: observed.append(error))
    monkeypatch.setattr(client, 'call_api',
                        mock.Mock(side_effect=KeyboardInterrupt))

    with pytest.raises(KeyboardInterrupt):
        reads.list_objects('ctx', reads.PODS)
    assert isinstance(observed[0], KeyboardInterrupt)


def test_reads_under_different_kubeconfigs_are_not_shared(api, monkeypatch):
    api(_POD_LIST)
    keys = []
    in_flight = reads._IN_FLIGHT  # pylint: disable=protected-access

    class _Recording:

        def do(self, key, fn):
            keys.append(key)
            return in_flight.do(key, fn)

    monkeypatch.setattr(reads, '_IN_FLIGHT', _Recording())
    for kubeconfig in ('/a/config', '/b/config'):
        monkeypatch.setenv('KUBECONFIG', kubeconfig)
        reads.list_objects('ctx', reads.PODS, consistency=reads.FRESH)

    assert keys[0] != keys[1]


def test_namespace_rules(api):
    api(_POD_LIST)
    with pytest.raises(ValueError):
        reads.list_objects('ctx', reads.NODES, namespace='ns')
    with pytest.raises(ValueError):
        reads.get_object('ctx', reads.PODS, 'p')


def test_known_unreachable_context_fails_fast(api):
    client = api(_POD_LIST)
    observer = mock.Mock()
    kubernetes_utils.register_context_call_observer(observer)
    kubernetes_utils.register_context_unreachable_check(
        lambda context: 'agent disconnected' if context == 'down' else None)

    with pytest.raises(exceptions.KubeAPIUnreachableError,
                       match='agent disconnected'):
        reads.list_objects('down', reads.PODS)

    assert not client.calls
    observer.assert_not_called()


def test_context_call_observer_sees_every_attempt(api, monkeypatch):
    server_error = _api_error(500)
    api(server_error, _POD_LIST)
    monkeypatch.setattr(kubernetes_utils.time, 'sleep', lambda seconds: None)
    observer = mock.Mock()
    kubernetes_utils.register_context_call_observer(observer)
    kubernetes_utils.register_context_call_observer(observer)

    reads.list_objects('ctx', reads.PODS)

    assert [c.args[:2] for c in observer.call_args_list] == [('ctx',
                                                              server_error),
                                                             ('ctx', None)]


def test_forbidden_read_raises_unreachable(api):
    api(_api_error(403))

    with pytest.raises(exceptions.KubeAPIUnreachableError):
        reads.list_objects('ctx', reads.PODS)


def test_failing_observer_does_not_fail_the_read(api):
    api(_POD_LIST)

    def observer(context, error, duration):
        raise RuntimeError('boom')

    kubernetes_utils.register_context_call_observer(observer)

    assert len(reads.list_objects('ctx', reads.PODS).items) == 2


def test_in_flight_shares_one_call(monkeypatch):
    in_flight = reads._InFlight()  # pylint: disable=protected-access
    entered = threading.Event()
    follower_waiting = threading.Event()
    release = threading.Event()
    calls = []

    class _Future(futures.Future):

        def result(self, timeout=None):
            follower_waiting.set()
            return super().result(timeout)

    monkeypatch.setattr(reads, 'futures', mock.Mock(Future=_Future))

    def fn():
        calls.append(1)
        entered.set()
        release.wait(timeout=10)
        return 'body'

    results = []
    leader = threading.Thread(
        target=lambda: results.append(in_flight.do('key', fn)))
    leader.start()
    assert entered.wait(timeout=10)
    follower = threading.Thread(
        target=lambda: results.append(in_flight.do('key', fn)))
    follower.start()
    assert follower_waiting.wait(timeout=10)
    release.set()
    leader.join(timeout=10)
    follower.join(timeout=10)

    assert calls == [1]
    assert results == ['body', 'body']
    assert in_flight.do('key', lambda: 'next') == 'next'


def test_in_flight_shares_the_error():
    in_flight = reads._InFlight()  # pylint: disable=protected-access

    def fn():
        raise RuntimeError('boom')

    with pytest.raises(RuntimeError, match='boom'):
        in_flight.do('key', fn)
    assert in_flight.do('key', lambda: 'next') == 'next'
