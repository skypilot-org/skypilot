"""Unit tests for sky.metrics.utils."""
import asyncio
import subprocess
import threading
import time
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


def test_is_local_context_probe_failure_retried_soon():
    """A failed probe is retried after the short failure window.

    A transient error (RBAC not yet applied, API server hiccup) must not
    pin a local context as remote — with self-federation running — for
    the full cache TTL.
    """
    retry = utils._LOCAL_CONTEXT_FAILURE_RETRY_SECONDS  # pylint: disable=protected-access
    fake_now = [0.0]
    with _DetectionHarness(own_uid='uid-1',
                           probe_exc=_FakeApiException(403)) as h:
        with mock.patch.object(utils.time,
                               'time',
                               side_effect=lambda: fake_now[0]):
            assert utils.is_local_context('ctx-a') is False
            # Within the failure window: served from cache, no re-probe.
            fake_now[0] = retry - 1.0
            assert utils.is_local_context('ctx-a') is False
            assert len(h.probe_calls) == 1
            # After the failure window (well before the 1h TTL): the
            # probe now succeeds and the context flips to local.
            h._probe_exc = None  # pylint: disable=protected-access
            h._probe_result = _fake_namespace('uid-1')  # pylint: disable=protected-access
            fake_now[0] = retry + 1.0
            assert utils.is_local_context('ctx-a') is True
            assert len(h.probe_calls) == 2


def test_is_local_context_no_identity_retried_soon():
    """A missing identity anchor is also retried after the failure window."""
    retry = utils._LOCAL_CONTEXT_FAILURE_RETRY_SECONDS  # pylint: disable=protected-access
    fake_now = [0.0]
    with mock.patch.object(utils.time, 'time', side_effect=lambda: fake_now[0]):
        with _DetectionHarness(own_uid=None):
            assert utils.is_local_context('ctx-a') is False
        with _DetectionHarness(own_uid='uid-1',
                               probe_result=_fake_namespace('uid-1')) as h:
            # Still within the failure window: cached remote.
            assert utils.is_local_context('ctx-a') is False
            assert not h.probe_calls
            # Identity became available: detection recovers quickly.
            fake_now[0] = retry + 1.0
            assert utils.is_local_context('ctx-a') is True


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


def test_split_local_remote_contexts():
    """Contexts are partitioned by is_local_context, order preserved."""

    def _fake_is_local(context):
        return context in ('in-cluster', 'ctx-local')

    with mock.patch.object(utils,
                           'is_local_context',
                           side_effect=_fake_is_local):
        local, remote = utils.split_local_remote_contexts(
            ['ctx-remote-1', 'in-cluster', 'ctx-local', 'ctx-remote-2'])
    assert local == ['in-cluster', 'ctx-local']
    assert remote == ['ctx-remote-1', 'ctx-remote-2']


def test_split_local_remote_contexts_detection_unavailable():
    """Without detection, only the in-cluster context is local."""
    with _DetectionHarness(own_uid=None):
        local, remote = utils.split_local_remote_contexts(
            ['ctx-a', 'in-cluster'])
    assert local == ['in-cluster']
    assert remote == ['ctx-a']


def test_split_local_remote_contexts_hung_probe_treated_remote():
    """A probe stuck past the detection budget must not block the call."""
    release = threading.Event()

    def _fake_is_local(context):
        if context == 'ctx-hung':
            release.wait(10)
        return context == 'ctx-local'

    with mock.patch.object(utils, 'is_local_context',
                           side_effect=_fake_is_local), \
         mock.patch.object(utils, '_DETECTION_TIMEOUT_SECONDS', 0.2):
        start = time.monotonic()
        local, remote = utils.split_local_remote_contexts(
            ['ctx-hung', 'ctx-local'])
        elapsed = time.monotonic() - start
    release.set()
    # The hung probe is treated as remote (the safe answer) and the
    # call returns within the detection budget, not the probe duration.
    assert local == ['ctx-local']
    assert remote == ['ctx-hung']
    assert elapsed < 5


def test_split_local_remote_contexts_probe_exception_treated_remote():
    """An exception escaping a probe thread degrades that context to remote."""

    def _fake_is_local(context):
        if context == 'ctx-broken':
            raise RuntimeError('kubeconfig exploded')
        return context == 'ctx-local'

    with mock.patch.object(utils,
                           'is_local_context',
                           side_effect=_fake_is_local):
        local, remote = utils.split_local_remote_contexts(
            ['ctx-broken', 'ctx-local'])
    assert local == ['ctx-local']
    assert remote == ['ctx-broken']


class _IdentityUidHarness:
    """Patches the adaptor pieces _get_in_cluster_identity_uid depends on."""

    def __init__(self, core=None, core_exc=None):
        patch_core = (mock.patch('sky.adaptors.kubernetes.core_api',
                                 side_effect=core_exc) if core_exc is not None
                      else mock.patch('sky.adaptors.kubernetes.core_api',
                                      return_value=core))
        self._patches = [
            patch_core,
            mock.patch('sky.adaptors.kubernetes.config_exception',
                       return_value=_FakeConfigException),
            mock.patch('sky.adaptors.kubernetes.api_exception',
                       return_value=_FakeApiException),
            mock.patch('sky.adaptors.kubernetes.in_cluster_context_name',
                       return_value='in-cluster'),
        ]

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *args):
        for p in self._patches:
            p.stop()
        return False


def test_get_in_cluster_identity_uid_caches_success_only():
    core = mock.MagicMock()
    core.read_namespace.side_effect = [
        TimeoutError('api server not ready'),
        _fake_namespace('uid-1'),
    ]
    with _IdentityUidHarness(core=core):
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
    with _IdentityUidHarness(
            core_exc=_FakeConfigException('no in-cluster config')):
        assert utils._get_in_cluster_identity_uid() is None  # pylint: disable=protected-access


def test_get_in_cluster_identity_uid_permission_denied_not_cached():
    """401/403 disables detection for this attempt but is retried later."""
    core = mock.MagicMock()
    core.read_namespace.side_effect = [
        _FakeApiException(403),
        _fake_namespace('uid-1'),
    ]
    with _IdentityUidHarness(core=core):
        assert utils._get_in_cluster_identity_uid() is None  # pylint: disable=protected-access
        # After RBAC is fixed, the next attempt succeeds.
        assert utils._get_in_cluster_identity_uid() == 'uid-1'  # pylint: disable=protected-access


def test_get_in_cluster_identity_uid_empty_uid_is_none():
    """A namespace without a UID yields no identity anchor."""
    core = mock.MagicMock()
    core.read_namespace.return_value = _fake_namespace('')
    with _IdentityUidHarness(core=core):
        assert utils._get_in_cluster_identity_uid() is None  # pylint: disable=protected-access


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
    with mock.patch.object(utils, '_get_prometheus_target',
                           return_value=('monitoring', 'prometheus-server',
                                         9090)), \
         mock.patch.object(utils, 'send_metrics_request_with_port_forward',
                           send_port_forward):
        asyncio.run(utils.get_metrics_for_context('ctx-remote'))
    kwargs = send_port_forward.await_args.kwargs
    assert kwargs['namespace'] == 'monitoring'
    assert kwargs['service'] == 'prometheus-server'
    assert kwargs['service_port'] == 9090


def test_get_metrics_for_context_patterns_unchanged_and_stamps():
    """Federation sends the upstream match patterns and stamps the result."""
    send_port_forward = mock.AsyncMock(return_value='foo{bar="baz"} 1.0')
    with mock.patch.object(utils, '_get_prometheus_target',
                           return_value=('skypilot',
                                         'skypilot-prometheus-server', 80)), \
         mock.patch.object(utils, 'send_metrics_request_with_port_forward',
                           send_port_forward):
        result = asyncio.run(utils.get_metrics_for_context('ctx-remote'))

    send_port_forward.assert_awaited_once()
    kwargs = send_port_forward.await_args.kwargs
    assert kwargs['context'] == 'ctx-remote'
    assert kwargs['namespace'] == 'skypilot'
    assert kwargs['service'] == 'skypilot-prometheus-server'
    assert kwargs['service_port'] == 80
    assert kwargs['match_patterns'] == utils.GPU_METRICS_MATCH_PATTERNS
    assert result == 'foo{cluster="ctx-remote",bar="baz"} 1.0'


def test_get_endpoint_metrics_for_context_patterns_unchanged_and_stamps():
    """/endpoints-metrics shares the federation + stamping path."""
    send_port_forward = mock.AsyncMock(return_value='vllm:foo{bar="baz"} 1.0')
    with mock.patch.object(utils, '_get_prometheus_target',
                           return_value=('skypilot',
                                         'skypilot-prometheus-server', 80)), \
         mock.patch.object(utils, 'send_metrics_request_with_port_forward',
                           send_port_forward):
        result = asyncio.run(
            utils.get_endpoint_metrics_for_context('ctx-remote'))

    send_port_forward.assert_awaited_once()
    kwargs = send_port_forward.await_args.kwargs
    assert kwargs['route'] == 'endpoints-metrics'
    assert kwargs['match_patterns'] == utils.ENDPOINT_METRICS_MATCH_PATTERNS
    assert result == 'vllm:foo{cluster="ctx-remote",bar="baz"} 1.0'


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
