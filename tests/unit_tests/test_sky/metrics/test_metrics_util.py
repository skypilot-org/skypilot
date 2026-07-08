"""Unit tests for sky.metrics.utils."""
import asyncio
import subprocess
from unittest import mock

import pytest

from sky.metrics import utils
from sky.utils import annotations


class _FakeApiException(Exception):
    """Stands in for kubernetes.client.rest.ApiException."""

    def __init__(self, status):
        super().__init__(f'fake api exception (status={status})')
        self.status = status


class _FakeConfigException(Exception):
    """Stands in for kubernetes.config.config_exception.ConfigException."""


def _fake_namespace(uid):
    namespace = mock.MagicMock()
    namespace.metadata.uid = uid
    return namespace


@pytest.fixture(autouse=True)
def _reset_local_context_detection_state():
    """Reset the process-level detection caches between tests."""
    utils._local_context_cache.clear()  # pylint: disable=protected-access
    utils._in_cluster_identity_uid = None  # pylint: disable=protected-access
    yield
    utils._local_context_cache.clear()  # pylint: disable=protected-access
    utils._in_cluster_identity_uid = None  # pylint: disable=protected-access


def test_start_svc_port_forward_terminates_on_exception():
    """Test subprocess is terminated when exception occurs."""
    mock_process = mock.MagicMock(spec=subprocess.Popen)
    mock_process.poll.return_value = None
    mock_process.stdout = mock.MagicMock()
    mock_process.stdout.fileno.return_value = 1

    mock_poller = mock.MagicMock()
    mock_poller.poll.side_effect = Exception('Test error')

    with mock.patch('subprocess.Popen',
                    return_value=mock_process), \
         mock.patch('time.time', side_effect=[0, 1, 2]), \
         mock.patch('select.poll',
                    return_value=mock_poller), \
         mock.patch('time.sleep'):

        with pytest.raises(Exception, match='Test error'):
            utils.start_svc_port_forward(context='test-context',
                                         namespace='test-ns',
                                         service='test-svc',
                                         service_port=8080)

        # Verify subprocess was terminated
        mock_process.terminate.assert_called_once()
        mock_process.wait.assert_called()


class _DetectionHarness:
    """Patches the pieces is_local_context() depends on."""

    def __init__(self, own_uid, probe_result=None, probe_exc=None):
        self._own_uid = own_uid
        self._probe_result = probe_result
        self._probe_exc = probe_exc
        self.probe_calls = []

    def __enter__(self):
        self._patches = []

        def _read_namespace(name, _request_timeout=None):
            self.probe_calls.append(name)
            if self._probe_exc is not None:
                raise self._probe_exc
            return self._probe_result

        core = mock.MagicMock()
        core.read_namespace.side_effect = _read_namespace
        self._patches = [
            mock.patch.object(utils,
                              '_get_in_cluster_identity_uid',
                              return_value=self._own_uid),
            mock.patch('sky.adaptors.kubernetes.core_api', return_value=core),
            mock.patch('sky.adaptors.kubernetes.api_exception',
                       return_value=_FakeApiException),
            mock.patch('sky.adaptors.kubernetes.in_cluster_context_name',
                       return_value='in-cluster'),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *args):
        for p in self._patches:
            p.stop()
        return False


def test_is_local_context_uid_match():
    with _DetectionHarness(own_uid='uid-1',
                           probe_result=_fake_namespace('uid-1')) as h:
        assert utils.is_local_context('ctx-a') is True
        assert h.probe_calls == ['kube-system']


def test_is_local_context_uid_mismatch():
    with _DetectionHarness(own_uid='uid-1',
                           probe_result=_fake_namespace('uid-2')):
        assert utils.is_local_context('ctx-a') is False


def test_is_local_context_404_is_remote():
    with _DetectionHarness(own_uid='uid-1', probe_exc=_FakeApiException(404)):
        assert utils.is_local_context('ctx-a') is False


def test_is_local_context_403_assumed_remote():
    with _DetectionHarness(own_uid='uid-1', probe_exc=_FakeApiException(403)):
        assert utils.is_local_context('ctx-a') is False


def test_is_local_context_error_assumed_remote():
    with _DetectionHarness(own_uid='uid-1',
                           probe_exc=TimeoutError('probe timed out')):
        assert utils.is_local_context('ctx-a') is False


def test_is_local_context_no_own_identity():
    """Not in a pod / cannot read kube-system: everything is remote."""
    with _DetectionHarness(own_uid=None) as h:
        assert utils.is_local_context('ctx-a') is False
        # No probe should be attempted without an identity anchor.
        assert not h.probe_calls


def test_is_local_context_cache_is_process_level():
    """Detection runs once per context and survives request-cache clears."""
    with _DetectionHarness(own_uid='uid-1',
                           probe_result=_fake_namespace('uid-1')) as h:
        assert utils.is_local_context('ctx-a') is True
        # gpu_metrics() clears the request-level cache on every scrape;
        # detection results must not be affected.
        annotations.clear_request_level_cache()
        assert utils.is_local_context('ctx-a') is True
        assert len(h.probe_calls) == 1


def test_is_local_context_cache_ttl_expiry():
    ttl = utils._LOCAL_CONTEXT_CACHE_TTL_SECONDS  # pylint: disable=protected-access
    fake_now = [0.0]
    with _DetectionHarness(own_uid='uid-1',
                           probe_result=_fake_namespace('uid-1')) as h:
        with mock.patch.object(utils.time,
                               'time',
                               side_effect=lambda: fake_now[0]):
            assert utils.is_local_context('ctx-a') is True
            fake_now[0] = ttl + 1.0
            assert utils.is_local_context('ctx-a') is True
        assert len(h.probe_calls) == 2


def test_is_local_context_in_cluster_is_always_local():
    """The in-cluster context is local by construction: no probe needed."""
    with _DetectionHarness(own_uid='uid-1',
                           probe_result=_fake_namespace('uid-2')) as h:
        assert utils.is_local_context('in-cluster') is True
        assert not h.probe_calls


def test_is_local_context_falls_back_to_in_cluster_on_broken_detection():
    """With detection unavailable, only the in-cluster context is local."""
    with _DetectionHarness(own_uid=None) as h:
        assert utils.is_local_context('ctx-a') is False
        assert utils.is_local_context('in-cluster') is True
        assert not h.probe_calls


def test_is_local_context_renamed_in_cluster_context():
    """A renamed in-cluster context is also local without probing."""
    with _DetectionHarness(own_uid=None) as h, \
         mock.patch('sky.adaptors.kubernetes.in_cluster_context_name',
                    return_value='my-renamed-context'):
        assert utils.is_local_context('my-renamed-context') is True
        assert not h.probe_calls


def test_get_in_cluster_identity_uid_caches_success_only():
    core = mock.MagicMock()
    core.read_namespace.side_effect = [
        TimeoutError('api server not ready'),
        _fake_namespace('uid-1'),
    ]
    with mock.patch('sky.adaptors.kubernetes.core_api', return_value=core), \
         mock.patch('sky.adaptors.kubernetes.config_exception',
                    return_value=_FakeConfigException), \
         mock.patch('sky.adaptors.kubernetes.in_cluster_context_name',
                    return_value='in-cluster'):
        # Failure is not cached; the next call retries and succeeds.
        assert utils._get_in_cluster_identity_uid() is None  # pylint: disable=protected-access
        assert utils._get_in_cluster_identity_uid() == 'uid-1'  # pylint: disable=protected-access
        # Success is cached: no further API calls.
        assert utils._get_in_cluster_identity_uid() == 'uid-1'  # pylint: disable=protected-access
        assert core.read_namespace.call_count == 2
        # The identity anchor is the kube-system namespace.
        assert core.read_namespace.call_args[0][0] == 'kube-system'


def test_get_in_cluster_identity_uid_not_in_pod():
    """Outside a pod there is no cluster identity: detection disabled."""
    with mock.patch('sky.adaptors.kubernetes.core_api',
                    side_effect=_FakeConfigException('no in-cluster config')), \
         mock.patch('sky.adaptors.kubernetes.config_exception',
                    return_value=_FakeConfigException), \
         mock.patch('sky.adaptors.kubernetes.in_cluster_context_name',
                    return_value='in-cluster'):
        assert utils._get_in_cluster_identity_uid() is None  # pylint: disable=protected-access


def test_add_empty_cluster_matcher():
    add = utils._add_empty_cluster_matcher  # pylint: disable=protected-access
    assert add('{__name__=~"DCGM_.*"}') == '{__name__=~"DCGM_.*",cluster=""}'
    assert add('node_cpu_seconds_total{mode="idle"}') == (
        'node_cpu_seconds_total{mode="idle",cluster=""}')
    assert add('kube_pod_labels') == 'kube_pod_labels{cluster=""}'
    assert add('metric{}') == 'metric{cluster=""}'


def test_add_cluster_name_label_basic():
    text = ('# HELP foo Foo metric\n'
            '# TYPE foo gauge\n'
            'foo{bar="baz"} 1.0\n'
            '\n'
            'no_labels_metric 2.0')
    result = asyncio.run(utils.add_cluster_name_label(text, 'ctx-a'))
    lines = result.split('\n')
    assert lines[0] == '# HELP foo Foo metric'
    assert lines[1] == '# TYPE foo gauge'
    assert lines[2] == 'foo{cluster="ctx-a",bar="baz"} 1.0'
    assert lines[3] == ''
    # Lines without a label section are kept as-is.
    assert lines[4] == 'no_labels_metric 2.0'


def test_add_cluster_name_label_idempotent():
    """An existing cluster label is replaced, never duplicated."""
    text = ('foo{cluster="old",bar="baz"} 1.0\n'
            'foo{bar="baz",cluster="old"} 2.0\n'
            'foo{cluster=""} 3.0')
    result = asyncio.run(utils.add_cluster_name_label(text, 'ctx-a'))
    lines = result.split('\n')
    assert lines[0] == 'foo{cluster="ctx-a",bar="baz"} 1.0'
    assert lines[1] == 'foo{bar="baz",cluster="ctx-a"} 2.0'
    assert lines[2] == 'foo{cluster="ctx-a"} 3.0'
    for line in lines:
        assert line.count('cluster=') == 1


def test_add_cluster_name_label_does_not_touch_other_labels():
    """Labels merely containing 'cluster' as a suffix are not replaced."""
    text = 'foo{k8s_cluster="other"} 1.0'
    result = asyncio.run(utils.add_cluster_name_label(text, 'ctx-a'))
    assert result == 'foo{cluster="ctx-a",k8s_cluster="other"} 1.0'


def test_add_cluster_name_label_escapes_context_name():
    """Quotes/backslashes/newlines in the context name are escaped."""
    text = 'foo{bar="baz"} 1.0'
    result = asyncio.run(utils.add_cluster_name_label(text, 'ctx"a\\b\nc'))
    assert result == 'foo{cluster="ctx\\"a\\\\b\\nc",bar="baz"} 1.0'


def test_add_cluster_name_label_brace_in_label_value():
    """A '}' inside a label value must not truncate the label section."""
    text = 'foo{bar="}",qux="v"} 1.0'
    result = asyncio.run(utils.add_cluster_name_label(text, 'ctx-a'))
    assert result == 'foo{cluster="ctx-a",bar="}",qux="v"} 1.0'


def test_add_cluster_name_label_cluster_inside_other_label_value():
    """A ',cluster=' substring inside another label's value is not a label.

    Regression test for matching on raw text instead of label tokens: the
    replacement must never start inside another label's (quoted) value.
    """
    # No real cluster label: prepend, leave the value untouched.
    text = 'foo{note="a,cluster=\\"x\\"",bar="b"} 1.0'
    result = asyncio.run(utils.add_cluster_name_label(text, 'ctx-a'))
    assert result == ('foo{cluster="ctx-a",note="a,cluster=\\"x\\"",bar="b"}'
                      ' 1.0')

    # Real cluster label present: replace only the actual label.
    text = 'foo{note="a,cluster=\\"x\\"",cluster="old"} 1.0'
    result = asyncio.run(utils.add_cluster_name_label(text, 'ctx-a'))
    assert result == ('foo{note="a,cluster=\\"x\\"",cluster="ctx-a"} 1.0')


class _FakeAsyncHttpClient:
    """Minimal async context-manager stand-in for httpx.AsyncClient."""

    calls = []

    def __init__(self, *args, **kwargs):
        del args, kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None):
        _FakeAsyncHttpClient.calls.append((url, params))
        response = mock.MagicMock()
        response.text = 'fake_metrics'
        response.content = b'fake_metrics'
        response.num_bytes_downloaded = len(b'fake_metrics')
        response.headers = {}
        response.raise_for_status = mock.MagicMock()
        return response


def test_send_local_metrics_request_builds_direct_url():
    _FakeAsyncHttpClient.calls = []
    with mock.patch.object(utils.httpx, 'AsyncClient', _FakeAsyncHttpClient):
        result = asyncio.run(
            utils.send_local_metrics_request(
                namespace='skypilot',
                service='skypilot-prometheus-server',
                service_port=80,
                endpoint_path='/federate',
                match_patterns=['{__name__=~"DCGM_.*",cluster=""}']))
    assert result == 'fake_metrics'
    assert _FakeAsyncHttpClient.calls == [
        ('http://skypilot-prometheus-server.skypilot.svc:80/federate',
         [('match[]', '{__name__=~"DCGM_.*",cluster=""}')]),
    ]


def test_get_prometheus_target_defaults():
    with mock.patch.object(utils.skypilot_config,
                           'get_nested',
                           side_effect=lambda keys, default: default):
        assert utils._get_prometheus_target() == (  # pylint: disable=protected-access
            'skypilot', 'skypilot-prometheus-server', 80)


def test_get_prometheus_target_configurable():
    config = {
        ('metrics', 'prometheus', 'namespace'): 'monitoring',
        ('metrics', 'prometheus', 'service'): 'prometheus-server',
        ('metrics', 'prometheus', 'port'): 9090,
    }
    with mock.patch.object(
            utils.skypilot_config,
            'get_nested',
            side_effect=lambda keys, default: config.get(keys, default)):
        assert utils._get_prometheus_target() == (  # pylint: disable=protected-access
            'monitoring', 'prometheus-server', 9090)


def test_get_metrics_for_context_uses_configured_prometheus_target():
    send_port_forward = mock.AsyncMock(return_value='foo{bar="baz"} 1.0')
    with mock.patch.object(utils, 'is_local_context', return_value=False), \
         mock.patch.object(utils, '_get_prometheus_target',
                           return_value=('monitoring', 'prometheus-server',
                                         9090)), \
         mock.patch.object(utils, 'send_metrics_request_with_port_forward',
                           send_port_forward):
        asyncio.run(utils.get_metrics_for_context('ctx-remote'))
    kwargs = send_port_forward.await_args.kwargs
    assert kwargs['namespace'] == 'monitoring'
    assert kwargs['service'] == 'prometheus-server'
    assert kwargs['service_port'] == 9090


def test_get_metrics_for_context_local_path():
    """Local context: direct HTTP, cluster="" guard, no port-forward."""
    send_local = mock.AsyncMock(return_value='foo{bar="baz"} 1.0')
    send_port_forward = mock.AsyncMock()
    with mock.patch.object(utils, 'is_local_context', return_value=True), \
         mock.patch.object(utils, '_get_prometheus_target',
                           return_value=('skypilot',
                                         'skypilot-prometheus-server', 80)), \
         mock.patch.object(utils, 'send_local_metrics_request', send_local), \
         mock.patch.object(utils, 'send_metrics_request_with_port_forward',
                           send_port_forward):
        result = asyncio.run(utils.get_metrics_for_context('ctx-local'))

    send_port_forward.assert_not_called()
    send_local.assert_awaited_once()
    kwargs = send_local.await_args.kwargs
    assert kwargs['namespace'] == 'skypilot'
    assert kwargs['service'] == 'skypilot-prometheus-server'
    assert kwargs['service_port'] == 80
    patterns = kwargs['match_patterns']
    assert patterns, 'expected non-empty match patterns'
    for pattern in patterns:
        assert 'cluster=""' in pattern, pattern
    # The result is stamped with the context name.
    assert result == 'foo{cluster="ctx-local",bar="baz"} 1.0'


def test_get_metrics_for_context_remote_path_unchanged():
    """Remote context: port-forward path with unmodified match patterns."""
    send_local = mock.AsyncMock()
    send_port_forward = mock.AsyncMock(return_value='foo{bar="baz"} 1.0')
    with mock.patch.object(utils, 'is_local_context', return_value=False), \
         mock.patch.object(utils, '_get_prometheus_target',
                           return_value=('skypilot',
                                         'skypilot-prometheus-server', 80)), \
         mock.patch.object(utils, 'send_local_metrics_request', send_local), \
         mock.patch.object(utils, 'send_metrics_request_with_port_forward',
                           send_port_forward):
        result = asyncio.run(utils.get_metrics_for_context('ctx-remote'))

    send_local.assert_not_called()
    send_port_forward.assert_awaited_once()
    kwargs = send_port_forward.await_args.kwargs
    assert kwargs['context'] == 'ctx-remote'
    assert kwargs['namespace'] == 'skypilot'
    assert kwargs['service'] == 'skypilot-prometheus-server'
    assert kwargs['service_port'] == 80
    for pattern in kwargs['match_patterns']:
        assert 'cluster=""' not in pattern, pattern
    assert result == 'foo{cluster="ctx-remote",bar="baz"} 1.0'


def test_get_endpoint_metrics_for_context_local_path():
    """/endpoints-metrics shares the local-path logic with /gpu-metrics."""
    send_local = mock.AsyncMock(return_value='vllm:foo{bar="baz"} 1.0')
    send_port_forward = mock.AsyncMock()
    with mock.patch.object(utils, 'is_local_context', return_value=True), \
         mock.patch.object(utils, '_get_prometheus_target',
                           return_value=('skypilot',
                                         'skypilot-prometheus-server', 80)), \
         mock.patch.object(utils, 'send_local_metrics_request', send_local), \
         mock.patch.object(utils, 'send_metrics_request_with_port_forward',
                           send_port_forward):
        result = asyncio.run(
            utils.get_endpoint_metrics_for_context('ctx-local'))

    send_port_forward.assert_not_called()
    send_local.assert_awaited_once()
    kwargs = send_local.await_args.kwargs
    assert kwargs['route'] == 'endpoints-metrics'
    for pattern in kwargs['match_patterns']:
        assert 'cluster=""' in pattern, pattern
    assert result == 'vllm:foo{cluster="ctx-local",bar="baz"} 1.0'


def test_start_svc_port_forward_terminates_on_timeout():
    """Test subprocess is terminated when no local port found."""
    mock_process = mock.MagicMock(spec=subprocess.Popen)
    mock_process.poll.return_value = None
    mock_process.stdout = mock.MagicMock()
    mock_process.stdout.fileno.return_value = 1

    mock_poller = mock.MagicMock()
    mock_poller.poll.return_value = []  # No events (timeout)

    # Simulate timeout by advancing time past the timeout threshold
    with mock.patch('subprocess.Popen',
                    return_value=mock_process), \
         mock.patch('time.time', side_effect=[0] + [11] * 10), \
         mock.patch('select.poll',
                    return_value=mock_poller), \
         mock.patch('time.sleep'):

        with pytest.raises(RuntimeError, match='Failed to extract local port'):
            utils.start_svc_port_forward(context='test-context',
                                         namespace='test-ns',
                                         service='test-svc',
                                         service_port=8080)

        # Verify subprocess was terminated
        mock_process.terminate.assert_called_once()
        mock_process.wait.assert_called()
