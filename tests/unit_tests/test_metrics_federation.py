"""/gpu-metrics carries workload KSM series; /endpoints-metrics federates
serving-engine metrics.

The serving dashboards (vLLM Serving, Autoscaling) read these series from the
central Prometheus: vllm:* via /endpoints-metrics, and Deployment replica
counts + the HPA target via the /gpu-metrics federation.

Also covers the federation observability helpers: FederationStats.summary()
(the timing/size breakdown surfaced in logs) and _handle_federation_result()
(the per-context success/timeout/port-forward-error/error classification),
the phase budgets carved out of the per-context timeout, and the streamed
assembly of the federated body.
"""
import asyncio
import gc
import subprocess
import threading
import time
from unittest import mock
from unittest.mock import MagicMock
import weakref

import fastapi
import pytest

from sky import exceptions
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


def test_handle_result_success_returns_text():
    assert server_metrics._handle_federation_result(
        'ctx', 'gpu-metrics', 'metric_text',
        metrics_utils.FederationStats()) == 'metric_text'


def test_handle_result_empty_body_is_a_success_not_a_failure():
    # An empty federated body is valid. It has to come back as '' rather than
    # None, or the stream drops the separator that the join used to emit.
    assert server_metrics._handle_federation_result(
        'ctx', 'gpu-metrics', '', metrics_utils.FederationStats()) == ''


def test_handle_result_timeout_returns_none():
    assert server_metrics._handle_federation_result(
        'ctx', 'gpu-metrics', asyncio.TimeoutError(),
        metrics_utils.FederationStats()) is None


def test_handle_result_error_returns_none():
    assert server_metrics._handle_federation_result(
        'ctx', 'gpu-metrics', ValueError('boom'),
        metrics_utils.FederationStats()) is None


def test_handle_result_base_exception_reraised():
    # KeyboardInterrupt/SystemExit must propagate, not be swallowed.
    with pytest.raises(KeyboardInterrupt):
        server_metrics._handle_federation_result(
            'ctx', 'gpu-metrics', KeyboardInterrupt(),
            metrics_utils.FederationStats())


def test_handle_result_port_forward_startup_returns_none():
    assert server_metrics._handle_federation_result(
        'ctx', 'gpu-metrics', exceptions.PortForwardStartupError('no tunnel'),
        metrics_utils.FederationStats()) is None


def test_port_forward_startup_error_recorded_under_its_own_outcome(monkeypatch):
    """The tunnel failing to come up is not folded into 'error'.

    PortForwardStartupError subclasses RuntimeError, so without an explicit
    branch ahead of the generic one it classifies as 'error' and the locally
    actionable failure disappears into the one that is not.
    """
    recorded = []
    monkeypatch.setattr(
        metrics_utils, 'record_federation_outcome',
        lambda context, route, outcome: recorded.append(outcome))

    server_metrics._handle_federation_result(
        'ctx', 'gpu-metrics', exceptions.PortForwardStartupError('no tunnel'),
        metrics_utils.FederationStats())
    server_metrics._handle_federation_result('ctx', 'gpu-metrics',
                                             ValueError('bad body'),
                                             metrics_utils.FederationStats())

    assert recorded == ['port-forward-error', 'error']


# --- phase budgets derived from the per-context timeout ---


def test_port_forward_startup_budget_is_a_fraction_of_the_context_budget():
    # The relationship is the point: a startup wait only means something
    # relative to the budget it has to leave time inside of.
    assert (metrics_utils._PORT_FORWARD_STARTUP_TIMEOUT_SECONDS ==
            metrics_utils.PER_CONTEXT_TIMEOUT_SECONDS *
            metrics_utils._PORT_FORWARD_STARTUP_BUDGET_FRACTION)


def test_port_forward_startup_budget_preserves_five_seconds():
    # Deriving the constant was a refactor, not a retune.
    assert metrics_utils._PORT_FORWARD_STARTUP_TIMEOUT_SECONDS == 5.0


def test_port_forward_startup_budget_leaves_room_for_the_request():
    # A startup wait at or above the whole budget would leave the /federate
    # request, transfer and teardown with nothing.
    assert (0 < metrics_utils._PORT_FORWARD_STARTUP_TIMEOUT_SECONDS <
            metrics_utils.PER_CONTEXT_TIMEOUT_SECONDS)


def test_server_context_budget_is_the_shared_one():
    # The alias must track the definition, or the phase budgets are
    # fractions of a number the routes no longer use.
    assert (server_metrics._PER_CONTEXT_TIMEOUT_SECONDS ==
            metrics_utils.PER_CONTEXT_TIMEOUT_SECONDS)


def test_port_forward_that_never_reports_ready_raises_its_own_error(
        monkeypatch):
    # kubectl alive but silent: the loop runs out of budget with no local
    # port. Used to raise a bare RuntimeError.
    monkeypatch.setattr(metrics_utils, '_PORT_FORWARD_STARTUP_TIMEOUT_SECONDS',
                        0.3)

    # Bound before patching, or the replacement would recurse into itself.
    real_popen = subprocess.Popen

    def silent_process(cmd, **kwargs):
        del cmd
        return real_popen(['sleep', '30'], **kwargs)

    monkeypatch.setattr(metrics_utils.subprocess, 'Popen', silent_process)

    with pytest.raises(exceptions.PortForwardStartupError) as excinfo:
        metrics_utils.start_svc_port_forward('ctx', 'ns', 'svc', 80)

    assert '0.3s' in str(excinfo.value)


def test_port_forward_that_cannot_launch_raises_its_own_error(monkeypatch):
    # No tunnel and no request either, so it belongs in the same bucket as a
    # tunnel that came up but never reported ready.
    def missing_kubectl(cmd, **kwargs):
        del cmd, kwargs
        raise FileNotFoundError('kubectl')

    monkeypatch.setattr(metrics_utils.subprocess, 'Popen', missing_kubectl)

    with pytest.raises(exceptions.PortForwardStartupError) as excinfo:
        metrics_utils.start_svc_port_forward('ctx', 'ns', 'svc', 80)

    assert isinstance(excinfo.value.__cause__, FileNotFoundError)
    assert 'kubectl' in str(excinfo.value)


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


# ── the federated body is streamed, not assembled ──────────────────


async def _collect(route, settled):
    chunks = []
    stream = server_metrics._stream_federated_metrics(route, set(settled))
    async for chunk in stream:
        chunks.append(chunk)
    return b''.join(chunks)


def _settled(*pairs):
    # (context, result) -> the tasks _stream_federated_metrics consumes. Real
    # tasks, not bare coroutines: the stream cancels the unfinished ones.
    return [
        asyncio.create_task(
            server_metrics._settle(ctx, metrics_utils.FederationStats(),
                                   asyncio.sleep(0, result=res)))
        for ctx, res in pairs
    ]


def _entries(body):
    # The body is the entries joined by the separator, so splitting on it
    # recovers them. Compared as a multiset: streaming writes each context
    # out as it finishes, so the order is completion order, not input order.
    return sorted(body.split(b'\n\n'))


@pytest.mark.asyncio
async def test_stream_carries_exactly_the_entries_the_join_carried():
    """Same entries and same separator as '\\n\\n'.join(all_metrics).

    Order is deliberately not asserted -- see
    test_stream_order_follows_completion_not_input.
    """
    texts = ['a 1', 'b 2', 'c 3']
    body = await _collect(
        'gpu-metrics', _settled(*[(f'ctx{i}', t) for i, t in enumerate(texts)]))
    assert _entries(body) == _entries('\n\n'.join(texts).encode('utf-8'))


@pytest.mark.asyncio
async def test_stream_order_follows_completion_not_input():
    """The documented cost of streaming: contexts land as they finish.

    Prometheus does not require ordering within an exposition, and the
    federated body already repeated HELP/TYPE per context before this change,
    so nothing downstream depends on the input order this drops.
    """

    async def after(delay, text):
        await asyncio.sleep(delay)
        return text

    settled = [
        asyncio.create_task(
            server_metrics._settle('slow', metrics_utils.FederationStats(),
                                   after(0.05, 'slow 1'))),
        asyncio.create_task(
            server_metrics._settle('fast', metrics_utils.FederationStats(),
                                   after(0, 'fast 1'))),
    ]
    body = await _collect('gpu-metrics', settled)
    assert body == b'fast 1\n\nslow 1'


@pytest.mark.asyncio
async def test_stream_separates_only_between_entries():
    # No leading or trailing separator -- a stray one would parse as a blank
    # line at the head of the exposition.
    body = await _collect('gpu-metrics', _settled(('only', 'a 1')))
    assert body == b'a 1'
    assert not body.startswith(b'\n')
    assert not body.endswith(b'\n\n')


@pytest.mark.asyncio
async def test_stream_keeps_an_empty_success_in_the_sequence():
    # '' is a success, so the join emitted separators around it; dropping it
    # would silently merge its neighbours.
    body = await _collect('gpu-metrics',
                          _settled(('a', 'a 1'), ('empty', ''), ('b', 'b 2')))
    assert _entries(body) == _entries('\n\n'.join(['a 1', '',
                                                   'b 2']).encode('utf-8'))
    assert b'' in body.split(b'\n\n')


@pytest.mark.asyncio
async def test_stream_omits_failed_contexts_without_leaving_a_gap():
    # A dropped context must not leave a doubled separator behind, which
    # would read as an empty entry that was never federated.
    body = await _collect(
        'gpu-metrics',
        _settled(('ok', 'a 1'), ('bad', ValueError('boom')), ('ok2', 'b 2')))
    assert _entries(body) == [b'a 1', b'b 2']
    assert b'' not in body.split(b'\n\n')


@pytest.mark.asyncio
async def test_stream_yields_nothing_when_every_context_fails():
    body = await _collect(
        'gpu-metrics',
        _settled(('bad', ValueError('boom')),
                 ('worse', asyncio.TimeoutError())))
    assert body == b''


@pytest.mark.asyncio
async def test_settle_pairs_a_result_with_its_own_context():
    """Identity has to survive completion-order delivery.

    as_completed() yields in completion order and, before 3.12, does not hand
    back the tasks it was given, so a result carries no way home on its own.
    """

    async def slow():
        await asyncio.sleep(0.05)
        return 'slow-text'

    async def quick():
        return 'quick-text'

    settled = [
        server_metrics._settle('slow-ctx', metrics_utils.FederationStats(),
                               slow()),
        server_metrics._settle('quick-ctx', metrics_utils.FederationStats(),
                               quick()),
    ]
    seen = {}
    for settling in asyncio.as_completed(settled):
        context, _, result = await settling
        seen[context] = result

    assert seen == {'slow-ctx': 'slow-text', 'quick-ctx': 'quick-text'}


@pytest.mark.asyncio
async def test_settle_returns_exceptions_but_not_base_exceptions():
    # Mirrors gather(return_exceptions=True): an Exception becomes a value so
    # one context cannot cancel the scrape, while KeyboardInterrupt does not.
    async def boom():
        raise ValueError('boom')

    _, _, result = await server_metrics._settle('ctx',
                                                metrics_utils.FederationStats(),
                                                boom())
    assert isinstance(result, ValueError)

    async def interrupt():
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        await server_metrics._settle('ctx', metrics_utils.FederationStats(),
                                     interrupt())


def test_federated_routes_stream_their_response():
    # A buffered Response would reintroduce the whole-payload copy this
    # change removes; nginx would do the same if allowed to buffer.
    response = server_metrics._federated_metrics_response('gpu-metrics', [])
    assert isinstance(response, fastapi.responses.StreamingResponse)
    assert response.media_type == 'text/plain; version=0.0.4; charset=utf-8'
    assert response.headers['x-accel-buffering'] == 'no'


@pytest.mark.parametrize(
    'route,collector',
    [('/gpu-metrics', 'get_metrics_for_context'),
     ('/endpoints-metrics', 'get_endpoint_metrics_for_context')])
def test_route_streams_the_whole_body_over_http(monkeypatch, route, collector):
    """End to end through the ASGI stack, not just the generator.

    A StreamingResponse is assembled by the server rather than handed over as
    one buffer, so the bytes a scrape actually receives are worth asserting
    separately from what the generator yields.
    """
    monkeypatch.delenv('PROMETHEUS_MULTIPROC_DIR', raising=False)
    targets = server_metrics._FEDERATION_TARGETS
    monkeypatch.setattr(targets, 'snapshot', lambda: (['ctx-a', 'ctx-b'], []))
    monkeypatch.setattr(targets, 'start_refresh_if_idle', lambda: None)

    async def fake_collect(context, stats=None, **kwargs):
        del stats, kwargs
        return f'metric{{cluster="{context}"}} 1'

    monkeypatch.setattr(metrics_utils, collector, fake_collect)

    with fastapi.testclient.TestClient(server_metrics.metrics_app) as client:
        response = client.get(route)

    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/plain')
    # Both contexts arrive, separated, with nothing lost in the hand-off.
    assert _entries(response.content) == sorted([
        b'metric{cluster="ctx-a"} 1',
        b'metric{cluster="ctx-b"} 1',
    ])


@pytest.mark.asyncio
async def test_abandoned_scrape_cancels_the_contexts_still_running():
    """Closing the stream early stops the federation behind it.

    The tasks are started eagerly, and the handler now returns a streaming
    body instead of awaiting gather(), so cancelling the handler no longer
    reaches them. A scrape Prometheus gives up on would otherwise leave every
    unfinished context federating -- and holding whatever it had already
    pulled -- until its own per-context budget expired.
    """
    started = asyncio.Event()

    async def never_finishes():
        started.set()
        await asyncio.sleep(30)
        return 'unreachable'

    settled = [
        asyncio.create_task(
            server_metrics._settle('quick', metrics_utils.FederationStats(),
                                   asyncio.sleep(0, result='quick 1'))),
        asyncio.create_task(
            server_metrics._settle('stuck', metrics_utils.FederationStats(),
                                   never_finishes())),
    ]

    stream = server_metrics._stream_federated_metrics('gpu-metrics',
                                                      set(settled))
    assert await stream.__anext__() == b'quick 1'
    await started.wait()
    # What Starlette does when the client goes away mid-body.
    await stream.aclose()

    await asyncio.sleep(0)
    assert settled[1].cancelled() or settled[1].done(), (
        'the unfinished context kept running after the scrape was abandoned')


@pytest.mark.asyncio
async def test_written_payload_is_collectible_before_the_slowest_context():
    """An emitted body must not stay pinned until every context settles.

    A finished asyncio.Task holds its return value, so retaining the task set
    to cancel it would keep every context's text alive to the end of the
    scrape -- exactly the retention streaming is meant to remove.
    """

    class Payload(str):
        """str subclass so it can be weak-referenced."""

    payload = Payload('big 1')
    ref = weakref.ref(payload)

    async def never_finishes():
        await asyncio.sleep(30)
        return 'unreachable'

    settled = [
        asyncio.create_task(
            server_metrics._settle('quick', metrics_utils.FederationStats(),
                                   asyncio.sleep(0, result=payload))),
        asyncio.create_task(
            server_metrics._settle('stuck', metrics_utils.FederationStats(),
                                   never_finishes())),
    ]
    del payload

    stream = server_metrics._stream_federated_metrics('gpu-metrics',
                                                      set(settled))
    try:
        assert await stream.__anext__() == b'big 1'
        # The slow context is still running; only local refs remain.
        del settled
        gc.collect()
        assert ref() is None, (
            'the written payload is still reachable while another context runs')
    finally:
        await stream.aclose()


@pytest.mark.asyncio
async def test_disconnect_before_the_body_starts_still_cancels():
    """The generator's finally does not exist until the generator starts.

    A client that goes away while the headers are going out means the body
    iterator is never entered, so nothing inside the generator can clean up.
    """
    settled = [
        asyncio.create_task(
            server_metrics._settle('stuck', metrics_utils.FederationStats(),
                                   asyncio.sleep(30)))
    ]
    response = server_metrics._federated_metrics_response(
        'gpu-metrics', settled)

    async def receive():
        return {'type': 'http.disconnect'}

    async def send(message):
        del message
        raise ConnectionResetError('client went away')

    with pytest.raises(ConnectionResetError):
        await response(
            {
                'type': 'http',
                'method': 'GET',
                'path': '/gpu-metrics',
                'headers': [],
            }, receive, send)

    await asyncio.sleep(0)
    assert settled[0].cancelled() or settled[0].done(), (
        'federation kept running after the client disconnected early')


def test_port_forward_hands_back_its_child_before_it_is_ready(monkeypatch):
    """A caller cancelled mid-startup still needs a handle on kubectl.

    start_svc_port_forward runs in a thread that cancellation cannot stop, so
    without this the thread spawns a tunnel that nothing is left to reap.
    """
    monkeypatch.setattr(metrics_utils, '_PORT_FORWARD_STARTUP_TIMEOUT_SECONDS',
                        0.3)
    real_popen = subprocess.Popen
    seen = []

    def silent_process(cmd, **kwargs):
        del cmd
        return real_popen(['sleep', '30'], **kwargs)

    monkeypatch.setattr(metrics_utils.subprocess, 'Popen', silent_process)

    with pytest.raises(exceptions.PortForwardStartupError):
        metrics_utils.start_svc_port_forward('ctx',
                                             'ns',
                                             'svc',
                                             80,
                                             on_process_start=seen.append)

    assert seen, 'the spawned process was never handed back'


def test_port_forward_reaps_itself_when_abandoned(monkeypatch):
    # The waiter gave up before the tunnel was ready; the thread it cannot
    # stop has to tear the child down itself.
    real_popen = subprocess.Popen
    spawned = []

    def silent_process(cmd, **kwargs):
        del cmd
        process = real_popen(['sleep', '30'], **kwargs)
        spawned.append(process)
        return process

    monkeypatch.setattr(metrics_utils.subprocess, 'Popen', silent_process)
    abandoned = threading.Event()
    abandoned.set()

    with pytest.raises(exceptions.PortForwardStartupError, match='abandoned'):
        metrics_utils.start_svc_port_forward('ctx',
                                             'ns',
                                             'svc',
                                             80,
                                             abandoned=abandoned)

    assert spawned, 'no process was spawned'
    assert _wait_until(lambda: spawned[0].poll() is not None), (
        'the abandoned tunnel was left running')


# --- per-context budget follows the scrape timeout ---


def test_budget_derives_from_the_scrape_timeout_header():
    # The whole point: a deployment that changes scrape_timeout does not have
    # to keep a second number in step by hand.
    assert metrics_utils.resolve_per_context_timeout('45.000000') == 30.0
    assert metrics_utils.resolve_per_context_timeout('90') == 60.0


def test_budget_from_the_bundled_chart_is_unchanged():
    # The chart scrapes these routes with scrape_timeout: 45s, which has to
    # keep producing the 30s this was a fixed constant for.
    assert (metrics_utils.resolve_per_context_timeout('45.0') ==
            metrics_utils.PER_CONTEXT_TIMEOUT_SECONDS)


def test_budget_leaves_headroom_inside_the_scrape_timeout():
    # Contexts run concurrently, so the response lands at roughly the slowest
    # context's budget plus the write; it has to land before the scraper quits.
    for scrape_timeout in ('10', '45', '120'):
        assert (metrics_utils.resolve_per_context_timeout(scrape_timeout) <
                float(scrape_timeout))


@pytest.mark.parametrize('header', [None, 'not-a-number', '0', '-5'])
def test_budget_falls_back_when_the_header_is_absent_or_junk(header):
    # A manual curl sends no header; a broken one must not produce a
    # nonsensical budget.
    assert (metrics_utils.resolve_per_context_timeout(header) ==
            metrics_utils.PER_CONTEXT_TIMEOUT_SECONDS)


def test_budget_falls_back_to_config_before_the_constant(monkeypatch):
    monkeypatch.setattr(
        metrics_utils.skypilot_config, 'get_nested', lambda path, default: 12.5
        if path == ('metrics', 'per_context_timeout_seconds') else default)
    assert metrics_utils.resolve_per_context_timeout(None) == 12.5
    # The header still wins: it is what the scraper will actually do.
    assert metrics_utils.resolve_per_context_timeout('45') == 30.0


def test_port_forward_startup_stays_inside_a_shrunken_budget():
    # A short scrape timeout must not leave the startup wait longer than the
    # whole budget it is supposed to fit inside.
    budget = metrics_utils.resolve_per_context_timeout('6')
    startup = budget * metrics_utils._PORT_FORWARD_STARTUP_BUDGET_FRACTION
    assert 0 < startup < budget


# --- the cliff is visible before it is fallen off ---


def test_near_budget_warns_while_still_succeeding():
    with mock.patch.object(metrics_utils.logger, 'warning') as warn:
        metrics_utils.warn_if_near_budget('ctx', 'gpu-metrics', 27.0, 30.0)
    assert 'used 90% of its 30.0s budget' in warn.call_args[0][0]


def test_comfortable_context_does_not_warn():
    with mock.patch.object(metrics_utils.logger, 'warning') as warn:
        metrics_utils.warn_if_near_budget('ctx', 'gpu-metrics', 3.0, 30.0)
    warn.assert_not_called()


def test_near_budget_warning_reaches_the_success_path():
    stats = metrics_utils.FederationStats()
    stats.port_forward_seconds = 2.0
    stats.federate_seconds = 26.0
    with mock.patch.object(metrics_utils.logger, 'warning') as warn:
        out = server_metrics._handle_federation_result('ctx', 'gpu-metrics',
                                                       'metric 1', stats, 30.0)
    assert out == 'metric 1'
    assert 'of its 30.0s budget' in warn.call_args[0][0]


def test_timeout_log_names_the_budget_actually_used():
    with mock.patch.object(server_metrics.logger, 'error') as err:
        server_metrics._handle_federation_result(
            'ctx', 'gpu-metrics', asyncio.TimeoutError(),
            metrics_utils.FederationStats(), 60.0)
    assert 'timed out after 60.0s' in err.call_args[0][0]


@pytest.mark.parametrize(
    'route,collector',
    [('/gpu-metrics', 'get_metrics_for_context'),
     ('/endpoints-metrics', 'get_endpoint_metrics_for_context')])
def test_scrape_timeout_header_reaches_the_federation(monkeypatch, route,
                                                      collector):
    # End to end: the header Prometheus sends has to become the budget the
    # contexts are actually run under.
    monkeypatch.delenv('PROMETHEUS_MULTIPROC_DIR', raising=False)
    targets = server_metrics._FEDERATION_TARGETS
    monkeypatch.setattr(targets, 'snapshot', lambda: (['ctx-a'], []))
    monkeypatch.setattr(targets, 'start_refresh_if_idle', lambda: None)

    seen = []

    async def fake_collect(context, stats=None, **kwargs):
        del stats, kwargs
        return f'metric{{cluster="{context}"}} 1'

    monkeypatch.setattr(metrics_utils, collector, fake_collect)

    real_wait_for = asyncio.wait_for

    async def recording_wait_for(aw, timeout=None):
        seen.append(timeout)
        return await real_wait_for(aw, timeout)

    monkeypatch.setattr(server_metrics.asyncio, 'wait_for', recording_wait_for)

    with fastapi.testclient.TestClient(server_metrics.metrics_app) as client:
        response = client.get(
            route, headers={'X-Prometheus-Scrape-Timeout-Seconds': '90.000000'})

    assert response.status_code == 200
    assert seen == [60.0], f'contexts ran under {seen}, not the header budget'


@pytest.mark.parametrize(
    'route,collector',
    [('/gpu-metrics', 'get_metrics_for_context'),
     ('/endpoints-metrics', 'get_endpoint_metrics_for_context')])
def test_budget_reaches_the_collector_not_just_the_outer_wait(
        monkeypatch, route, collector):
    """The inner phases have to run under the same budget as the outer wait.

    The outer wait_for is only the deadline; the startup share and the httpx
    timeout are computed from what the collector is told, so a collector left
    on its default would cap federation at 30s no matter what the scraper
    reported.
    """
    monkeypatch.delenv('PROMETHEUS_MULTIPROC_DIR', raising=False)
    targets = server_metrics._FEDERATION_TARGETS
    monkeypatch.setattr(targets, 'snapshot', lambda: (['ctx-a'], []))
    monkeypatch.setattr(targets, 'start_refresh_if_idle', lambda: None)

    seen = {}

    async def fake_collect(context, stats=None, timeout=None, **kwargs):
        del stats, kwargs
        seen['timeout'] = timeout
        return f'metric{{cluster="{context}"}} 1'

    monkeypatch.setattr(metrics_utils, collector, fake_collect)

    with fastapi.testclient.TestClient(server_metrics.metrics_app) as client:
        response = client.get(
            route, headers={'X-Prometheus-Scrape-Timeout-Seconds': '90'})

    assert response.status_code == 200
    assert seen['timeout'] == 60.0, (
        f'collector ran under {seen["timeout"]}s, not the derived budget')


def test_elapsed_counts_the_whole_attempt_when_it_finished():
    # Stamping runs after federate_seconds is recorded but is still charged
    # to the budget, so the warning has to see it.
    stats = metrics_utils.FederationStats()
    stats.port_forward_seconds = 2.0
    stats.federate_seconds = 10.0
    stats.total_seconds = 27.0
    assert stats.elapsed_seconds == 27.0


def test_elapsed_falls_back_to_phases_for_a_cancelled_attempt():
    # A cancelled attempt never sets total_seconds; the phases that did
    # complete are all there is to report.
    stats = metrics_utils.FederationStats()
    stats.port_forward_seconds = 2.0
    stats.federate_seconds = 10.0
    assert stats.elapsed_seconds == 12.0


def test_stamping_time_reaches_the_near_budget_warning():
    # Devin's case: 12s of phases, 27s in total. The phase sum alone is under
    # the 80% threshold and would stay silent.
    stats = metrics_utils.FederationStats()
    stats.port_forward_seconds = 2.0
    stats.federate_seconds = 10.0
    stats.total_seconds = 27.0
    with mock.patch.object(metrics_utils.logger, 'warning') as warn:
        server_metrics._handle_federation_result('ctx', 'gpu-metrics',
                                                 'metric 1', stats, 30.0)
    assert 'used 90% of its 30.0s budget' in warn.call_args[0][0]


@pytest.mark.parametrize('header', ['inf', '-inf', 'nan', 'Infinity'])
def test_non_finite_headers_fall_back(header):
    # inf survives a bare > 0 test and would make the deadline never fire.
    assert (metrics_utils.resolve_per_context_timeout(header) ==
            metrics_utils.PER_CONTEXT_TIMEOUT_SECONDS)
