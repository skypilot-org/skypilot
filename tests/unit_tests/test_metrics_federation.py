"""/gpu-metrics carries workload KSM series; /endpoints-metrics federates
serving-engine metrics.

The serving dashboards (vLLM Serving, Autoscaling) read these series from the
central Prometheus: vllm:* via /endpoints-metrics, and Deployment replica
counts + the HPA target via the /gpu-metrics federation.

Also covers the federation observability helpers: FederationStats.summary()
(the timing/size breakdown surfaced in logs) and _handle_federation_result()
(the per-context success/timeout/error classification).
"""
import asyncio
import subprocess
import threading
import time
from unittest.mock import MagicMock

import pytest

from sky.metrics import utils as metrics_utils
from sky.server import metrics as server_metrics

_MIB = 2**20


def test_endpoint_metrics_carries_autoscaling_dashboard_series():
    joined = '\n'.join(metrics_utils.ENDPOINT_METRICS_MATCH_PATTERNS)
    # Serving-engine series + replica panels + the HPA threshold line.
    assert 'vllm:' in joined
    assert 'kube_deployment_' in joined
    assert 'kube_horizontalpodautoscaler_spec_target_metric' in joined


def test_gpu_metrics_keeps_gpu_semantics():
    # The workload KSM series exist solely for endpoint observability and
    # ride /endpoints-metrics, not the GPU federation.
    joined = '\n'.join(metrics_utils.GPU_METRICS_MATCH_PATTERNS)
    assert 'kube_deployment_' not in joined
    assert 'kube_horizontalpodautoscaler' not in joined


def test_no_per_pod_phase_series():
    # kube_pod_status_phase is the expensive per-pod family and has no
    # consumer; it must not ride either federation.
    for pats in (metrics_utils.GPU_METRICS_MATCH_PATTERNS,
                 metrics_utils.ENDPOINT_METRICS_MATCH_PATTERNS):
        assert 'kube_pod_status_phase' not in '\n'.join(pats)


def test_endpoint_metrics_route_registered():
    paths = {
        getattr(r, 'path', None) for r in server_metrics.metrics_app.routes
    }
    assert '/endpoints-metrics' in paths
    assert '/gpu-metrics' in paths
    assert hasattr(metrics_utils, 'get_endpoint_metrics_for_context')


# --- FederationStats.summary() ---


def test_federation_stats_summary_empty():
    # No phase finished (e.g. timed out establishing the port-forward).
    stats = metrics_utils.FederationStats()
    assert stats.summary() == 'port_forward=incomplete, federate=incomplete'


def test_federation_stats_summary_port_forward_only():
    # Port-forward done, federate cancelled mid-transfer (the timeout case).
    stats = metrics_utils.FederationStats()
    stats.port_forward_seconds = 0.31
    assert stats.summary() == 'port_forward=0.31s, federate=incomplete'


def test_federation_stats_summary_full():
    stats = metrics_utils.FederationStats()
    stats.port_forward_seconds = 0.29
    stats.federate_seconds = 4.82
    stats.body_bytes = int(64.4 * _MIB)
    stats.wire_bytes = int(3.45 * _MIB)
    stats.content_encoding = 'gzip'
    out = stats.summary()
    assert 'port_forward=0.29s' in out
    assert 'federate=4.82s' in out
    assert 'body=64.4MiB' in out
    assert 'wire=3.45MiB' in out
    assert 'enc=gzip' in out


def test_federation_stats_summary_never_raises_on_missing_bytes():
    # Defensive: summary() runs inside log calls and must never raise, even in
    # the should-not-happen state where federate_seconds is set but the byte
    # fields are not.
    stats = metrics_utils.FederationStats()
    stats.federate_seconds = 1.0
    out = stats.summary()  # must not raise
    assert 'body=unknown' in out
    assert 'wire=unknown' in out


def test_federation_stats_summary_without_port_forward_phase():
    # Slurm federation runs curl on the login node over SSH: there is no
    # port-forward phase, so the breakdown must not report one as
    # 'incomplete'.
    stats = metrics_utils.FederationStats(has_port_forward=False)
    assert stats.summary() == 'federate=incomplete'
    stats.federate_seconds = 0.5
    stats.body_bytes = 2 * 1024 * 1024
    out = stats.summary()
    assert out.startswith('federate=0.50s, body=2.0MiB')
    assert 'port_forward' not in out


# --- _handle_federation_result() classification ---


def test_handle_result_success_appends():
    out = []
    server_metrics._handle_federation_result('ctx', 'gpu-metrics',
                                             'metric_text',
                                             metrics_utils.FederationStats(),
                                             out)
    assert out == ['metric_text']


def test_handle_result_empty_body_appends():
    # An empty federated body is valid and must still be appended.
    out = []
    server_metrics._handle_federation_result('ctx', 'gpu-metrics', '',
                                             metrics_utils.FederationStats(),
                                             out)
    assert out == ['']


def test_handle_result_timeout_not_appended():
    out = []
    server_metrics._handle_federation_result('ctx', 'gpu-metrics',
                                             asyncio.TimeoutError(),
                                             metrics_utils.FederationStats(),
                                             out)
    assert out == []


def test_handle_result_error_not_appended():
    out = []
    server_metrics._handle_federation_result('ctx', 'gpu-metrics',
                                             ValueError('boom'),
                                             metrics_utils.FederationStats(),
                                             out)
    assert out == []


def test_handle_result_base_exception_reraised():
    # KeyboardInterrupt/SystemExit must propagate, not be swallowed.
    out = []
    with pytest.raises(KeyboardInterrupt):
        server_metrics._handle_federation_result(
            'ctx', 'gpu-metrics', KeyboardInterrupt(),
            metrics_utils.FederationStats(), out)
    assert out == []


# ── port-forward teardown stays off the metrics event loop ──────────


def _wait_until(predicate, timeout=10.0, interval=0.01):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_stop_svc_port_forward_off_loop_does_not_wait_for_the_teardown():
    """The caller returns immediately, and the teardown still completes.

    The teardown runs in the `finally` of a coroutine that asyncio.wait_for()
    may be cancelling, so it cannot be awaited; running it inline instead
    charges terminate-and-wait to the loop that also serves /metrics.
    """
    reaped = threading.Event()
    process = MagicMock()

    def slow_wait(timeout=None):
        del timeout
        time.sleep(0.5)
        reaped.set()

    process.wait.side_effect = slow_wait

    start = time.monotonic()
    metrics_utils.stop_svc_port_forward_off_loop(process)
    handed_off_in = time.monotonic() - start

    assert handed_off_in < 0.2, (
        f'caller waited {handed_off_in:.2f}s for the teardown')
    assert _wait_until(reaped.is_set), 'teardown never ran'
    process.terminate.assert_called_once()


def test_stop_svc_port_forward_gives_up_on_an_unreapable_child():
    """The wait after SIGKILL is bounded.

    SIGKILL cannot be caught, so a child that is still unreaped afterwards is
    stuck in the kernel; waiting for it without a bound pins the thread for
    the life of the process.
    """
    process = MagicMock()
    process.pid = 4321
    process.wait.side_effect = subprocess.TimeoutExpired(cmd='kubectl',
                                                         timeout=1)

    # Returns rather than hanging, having escalated to kill().
    metrics_utils.stop_svc_port_forward(process, timeout=1)

    process.terminate.assert_called_once()
    process.kill.assert_called_once()
    # Once for the terminate grace period, once after the kill.
    assert process.wait.call_count == 2
