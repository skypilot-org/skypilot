"""Unit tests for sky.metrics.utils."""
import asyncio
import queue
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


def _clear_detection_state():
    # pylint: disable=protected-access
    with utils._local_context_cache_lock:
        utils._local_context_cache.clear()
        utils._probe_pending.clear()
    while True:
        try:
            utils._probe_queue.get_nowait()
        except queue.Empty:
            break
    utils._in_cluster_identity_uid = None
    utils._anchor_read_failed = False


@pytest.fixture(autouse=True)
def _reset_local_context_detection_state():
    """Reset the process-level detection state between tests.

    The probe worker thread (if one was started) is deliberately left
    running: it blocks on the queue when idle and each test starts from
    an empty queue. Jitter is disabled so re-probe intervals are exact.
    """
    _clear_detection_state()
    with mock.patch.object(utils.random, 'random', return_value=0.0):
        yield
    _clear_detection_state()


def _wait_probes_drained(timeout=5.0):
    """Waits until the probe worker has no queued or in-flight probes."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with utils._local_context_cache_lock:  # pylint: disable=protected-access
            if not utils._probe_pending:  # pylint: disable=protected-access
                return
        time.sleep(0.01)
    raise AssertionError('probe worker did not drain in time')


def _seed_verdict(context, verdict, next_probe_in=3600.0):
    """Writes a conclusive verdict, as a finished probe would."""
    # pylint: disable=protected-access
    entry = utils._ContextDetection()
    entry.verdict = verdict
    entry.next_probe_at = time.time() + next_probe_in
    with utils._local_context_cache_lock:
        utils._local_context_cache[context] = entry
    return verdict


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
    pin a wrong verdict for the full refresh interval.
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


def test_is_local_context_failure_backoff_doubles_to_cap():
    """Consecutive failures back off exponentially up to the cap.

    Without the backoff, a permanently failing context (dead cluster,
    missing RBAC) re-probes on every scrape cycle forever — the probe
    churn that starves the co-located /metrics endpoint.
    """
    # pylint: disable=protected-access
    base = utils._LOCAL_CONTEXT_FAILURE_RETRY_SECONDS
    cap = utils._LOCAL_CONTEXT_FAILURE_RETRY_MAX_SECONDS
    fake_now = [0.0]
    with _DetectionHarness(own_uid='uid-1',
                           probe_exc=_FakeApiException(403)) as h:
        with mock.patch.object(utils.time,
                               'time',
                               side_effect=lambda: fake_now[0]):
            expected_delay = base
            for expected_calls in range(1, 8):
                utils.is_local_context('ctx-a')
                assert len(h.probe_calls) == expected_calls
                entry = utils._local_context_cache['ctx-a']
                assert entry.consecutive_failures == expected_calls
                assert entry.next_probe_at == pytest.approx(fake_now[0] +
                                                            expected_delay)
                # Probing again before the backoff expires is a no-op.
                utils.is_local_context('ctx-a')
                assert len(h.probe_calls) == expected_calls
                fake_now[0] = entry.next_probe_at + 1.0
                expected_delay = min(expected_delay * 2, cap)
            # The schedule has settled at the cap.
            assert (utils._local_context_cache['ctx-a'].next_probe_at -
                    fake_now[0]) <= cap


def test_is_local_context_conclusive_verdict_sticky_across_failures():
    """An inconclusive refresh keeps the previous conclusive verdict.

    Flipping a known-local context to remote on a transient probe
    failure would silently start self-federation; the verdict must
    survive until a conclusive probe says otherwise.
    """
    ttl = utils._LOCAL_CONTEXT_CACHE_TTL_SECONDS  # pylint: disable=protected-access
    fake_now = [0.0]
    with _DetectionHarness(own_uid='uid-1',
                           probe_result=_fake_namespace('uid-1')) as h:
        with mock.patch.object(utils.time,
                               'time',
                               side_effect=lambda: fake_now[0]):
            assert utils.is_local_context('ctx-a') is True
            # The refresh probe fails: the local verdict is kept.
            h._probe_exc = _FakeApiException(403)  # pylint: disable=protected-access
            fake_now[0] = ttl + 1.0
            assert utils.is_local_context('ctx-a') is True
            assert len(h.probe_calls) == 2
            # A conclusive probe updates the verdict again.
            h._probe_exc = None  # pylint: disable=protected-access
            h._probe_result = _fake_namespace('uid-2')  # pylint: disable=protected-access
            entry = utils._local_context_cache['ctx-a']  # pylint: disable=protected-access
            fake_now[0] = entry.next_probe_at + 1.0
            assert utils.is_local_context('ctx-a') is False


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


class _InClusterName:
    """Patches the in-cluster context name for split tests."""

    def __init__(self, name='in-cluster'):
        self._patch = mock.patch(
            'sky.adaptors.kubernetes.in_cluster_context_name',
            return_value=name)

    def __enter__(self):
        self._patch.start()
        return self

    def __exit__(self, *args):
        self._patch.stop()
        return False


def test_split_local_remote_contexts():
    """Contexts are partitioned by cached verdicts, order preserved."""
    _seed_verdict('ctx-local', True)
    _seed_verdict('ctx-remote-1', False)
    _seed_verdict('ctx-remote-2', False)
    with _InClusterName():
        local, remote = utils.split_local_remote_contexts(
            ['ctx-remote-1', 'in-cluster', 'ctx-local', 'ctx-remote-2'])
    assert local == ['in-cluster', 'ctx-local']
    assert remote == ['ctx-remote-1', 'ctx-remote-2']


def test_split_local_remote_contexts_warm_path_never_probes():
    """A call with fresh verdicts answers inline and schedules nothing."""

    def _must_not_probe(context):
        raise AssertionError(f'unexpected probe for {context!r}')

    for ctx, verdict in (('ctx-local', True), ('ctx-remote', False)):
        _seed_verdict(ctx, verdict)
    with _InClusterName(), \
         mock.patch.object(utils, 'is_local_context',
                           side_effect=_must_not_probe):
        local, remote = utils.split_local_remote_contexts(
            ['ctx-local', 'ctx-remote'])
    assert local == ['ctx-local']
    assert remote == ['ctx-remote']
    # Nothing was handed to the probe worker.
    assert not utils._probe_pending  # pylint: disable=protected-access


def test_split_local_remote_contexts_unknown_served_remote_nonblocking():
    """A context with no verdict is remote for this call, without waiting.

    The probe runs in the background worker; a later call reads its
    verdict back from the cache.
    """
    probed = threading.Event()

    def _fake_is_local(context):
        _seed_verdict(context, True)
        probed.set()
        return True

    with _InClusterName(), \
         mock.patch.object(utils, 'is_local_context',
                           side_effect=_fake_is_local):
        start = time.monotonic()
        local, remote = utils.split_local_remote_contexts(['ctx-a'])
        elapsed = time.monotonic() - start
        # Answered immediately with the safe default.
        assert (local, remote) == ([], ['ctx-a'])
        assert elapsed < 1
        # The worker probes in the background; the next call sees the
        # verdict.
        assert probed.wait(5)
        _wait_probes_drained()
        local, remote = utils.split_local_remote_contexts(['ctx-a'])
    assert (local, remote) == (['ctx-a'], [])


def test_split_local_remote_contexts_dedupes_pending_probes():
    """Repeated calls while a probe is in flight schedule it only once."""
    gate = threading.Event()
    probe_calls = []

    def _fake_is_local(context):
        probe_calls.append(context)
        gate.wait(10)
        return _seed_verdict(context, False)

    with _InClusterName(), \
         mock.patch.object(utils, 'is_local_context',
                           side_effect=_fake_is_local):
        utils.split_local_remote_contexts(['ctx-a'])
        utils.split_local_remote_contexts(['ctx-a'])
        utils.split_local_remote_contexts(['ctx-a'])
        gate.set()
        _wait_probes_drained()
    # Exactly one probe ran despite three split calls.
    assert probe_calls == ['ctx-a']
    # The worker is a daemon so a hung probe never blocks shutdown.
    assert utils._probe_worker.daemon is True  # pylint: disable=protected-access


def test_split_local_remote_contexts_detection_unavailable():
    """Without detection, only the in-cluster context is local."""
    with _DetectionHarness(own_uid=None):
        local, remote = utils.split_local_remote_contexts(
            ['ctx-a', 'in-cluster'])
    assert local == ['in-cluster']
    assert remote == ['ctx-a']


def test_split_local_remote_contexts_hung_probe_never_blocks():
    """A hung probe must not block any call, ever.

    This is the probe-storm regression: the request path must stay a
    pure cache read no matter how a context's probe misbehaves.
    """
    release = threading.Event()

    def _fake_is_local(context):
        release.wait(10)
        return _seed_verdict(context, False)

    _seed_verdict('ctx-local', True)
    with _InClusterName(), \
         mock.patch.object(utils, 'is_local_context',
                           side_effect=_fake_is_local):
        start = time.monotonic()
        for _ in range(3):
            local, remote = utils.split_local_remote_contexts(
                ['ctx-hung', 'ctx-local'])
        elapsed = time.monotonic() - start
        release.set()
        _wait_probes_drained()
    # The hung probe is served as remote (the safe answer) and no call
    # waited on it.
    assert local == ['ctx-local']
    assert remote == ['ctx-hung']
    assert elapsed < 1


def test_split_local_remote_contexts_probe_exception_treated_remote():
    """An exception escaping a probe leaves that context remote."""

    def _fake_is_local(context):
        if context == 'ctx-broken':
            raise RuntimeError('kubeconfig exploded')
        return _seed_verdict(context, True)

    with _InClusterName(), \
         mock.patch.object(utils, 'is_local_context',
                           side_effect=_fake_is_local):
        local, remote = utils.split_local_remote_contexts(
            ['ctx-broken', 'ctx-local'])
        assert (local, remote) == ([], ['ctx-broken', 'ctx-local'])
        _wait_probes_drained()
        # ctx-broken raised (stays remote), ctx-local concluded local.
        local, remote = utils.split_local_remote_contexts(
            ['ctx-broken', 'ctx-local'])
    assert local == ['ctx-local']
    assert remote == ['ctx-broken']


def test_split_local_remote_contexts_serves_stale_while_refreshing():
    """A due verdict is refreshed in the background, not on the call.

    The hourly refresh must not take the request path back to probing:
    the stale verdict is served now and the worker updates it for later
    calls.
    """
    refreshed = threading.Event()

    def _fake_is_local(context):
        _seed_verdict(context, False)
        refreshed.set()
        return False

    # Conclusive verdict whose refresh interval has already elapsed.
    _seed_verdict('ctx-a', True, next_probe_in=-1.0)
    with _InClusterName(), \
         mock.patch.object(utils, 'is_local_context',
                           side_effect=_fake_is_local):
        local, remote = utils.split_local_remote_contexts(['ctx-a'])
        # Served the stale verdict without waiting for the refresh.
        assert (local, remote) == (['ctx-a'], [])
        assert refreshed.wait(5)
        _wait_probes_drained()
        local, remote = utils.split_local_remote_contexts(['ctx-a'])
    # The background refresh concluded remote; later calls see it.
    assert (local, remote) == ([], ['ctx-a'])


def test_split_local_remote_contexts_emits_served_and_probe_metrics():
    """The new instrumentation reflects what the request path served."""

    def _sample(metric, **labels):
        for family in metric.collect():
            for sample in family.samples:
                if (sample.name.endswith('_total') and all(
                        sample.labels.get(k) == v for k, v in labels.items())):
                    return sample.value
        return 0.0

    served = utils.SKY_APISERVER_LOCAL_CONTEXT_SERVED_TOTAL
    detected = utils.SKY_APISERVER_LOCAL_CONTEXT_DETECTION_TOTAL
    before_unknown = _sample(served, context='ctx-m', result='unknown')
    before_remote = _sample(served, context='ctx-m', result='remote')
    before_inconclusive = _sample(detected,
                                  context='ctx-m',
                                  result='inconclusive')
    with mock.patch.object(utils, 'METRICS_ENABLED', True), \
         _DetectionHarness(own_uid=None):
        utils.split_local_remote_contexts(['ctx-m'])
        _wait_probes_drained()
        utils.split_local_remote_contexts(['ctx-m'])
    # First call served 'unknown'; the probe concluded nothing (no
    # anchor), so the second call served 'unknown' again — and the
    # worker recorded an inconclusive probe.
    assert _sample(served, context='ctx-m',
                   result='unknown') >= before_unknown + 2
    assert _sample(detected, context='ctx-m',
                   result='inconclusive') >= before_inconclusive + 1
    assert _sample(served, context='ctx-m', result='remote') == before_remote


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
    # A metric line without a label section still gets a cluster label.
    assert lines[4] == 'no_labels_metric 2.0'


def test_add_cluster_name_label_skips_already_labeled():
    """Lines already carrying a cluster label are left untouched.

    Re-stamping would produce two cluster labels on one line, a hard
    duplicate-label error that rolls back the whole /gpu-metrics scrape.
    This is the safety net for a local context misdetected as remote.
    """
    text = ('foo{cluster="other",bar="baz"} 1.0\n'
            'foo{bar="baz",cluster="other"} 2.0\n'
            'foo{cluster=""} 3.0')
    result = asyncio.run(utils.add_cluster_name_label(text, 'ctx-a'))
    lines = result.split('\n')
    # Unchanged — not re-stamped, not replaced.
    assert lines == [
        'foo{cluster="other",bar="baz"} 1.0',
        'foo{bar="baz",cluster="other"} 2.0',
        'foo{cluster=""} 3.0',
    ]
    for line in lines:
        assert line.count('cluster=') == 1


def test_add_cluster_name_label_does_not_match_cluster_suffix_labels():
    """A label like `k8s_cluster` is not mistaken for a `cluster` label."""
    text = 'foo{k8s_cluster="other"} 1.0'
    result = asyncio.run(utils.add_cluster_name_label(text, 'ctx-a'))
    assert result == 'foo{cluster="ctx-a",k8s_cluster="other"} 1.0'


def test_add_cluster_name_label_brace_in_label_value():
    """A '}' inside a label value must not truncate the label section."""
    text = 'foo{bar="}",cluster="other"} 1.0'
    result = asyncio.run(utils.add_cluster_name_label(text, 'ctx-a'))
    # The real cluster label (after the '}' in bar's value) is detected,
    # so the line is skipped rather than double-stamped.
    assert result == 'foo{bar="}",cluster="other"} 1.0'


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
