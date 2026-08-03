"""Unit tests for the open-loop event loop lag harness.

The end-to-end cases drive the harness against a real HTTP server on a loopback
port rather than a mocked transport, so the scheduling and timing behaviour
under test is the behaviour that runs in a benchmark.
"""
import asyncio
import json
import pathlib
import socket
import threading
import time

import httpx
from load_tests import loop_lag_harness as harness
import pytest
import uvicorn

# Bucket boundaries the server's lag histogram actually uses; the harness
# refuses thresholds that are not boundaries.
_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60,
            120, 300, 600, 1000, float('inf'))


def _quiet_metrics(total=100, **kwargs) -> str:
    """A page where every recorded tick landed in the fastest bucket."""
    return _metrics_text({0.005: total, float('inf'): total}, **kwargs)


def _metrics_text(lag_counts, lag_max_by_pid=None, cpu_by_pid=None) -> str:
    """Render a prometheus exposition page for the metrics the harness reads.

    lag_counts maps a bucket boundary to the cumulative count at that boundary;
    boundaries left unspecified carry the previous boundary's count forward,
    exactly as a real cumulative histogram does.
    """
    lines = [
        '# HELP sky_apiserver_event_loop_lag_seconds Scheduling delay',
        '# TYPE sky_apiserver_event_loop_lag_seconds histogram',
    ]
    total = 0.0
    for bound in _BUCKETS:
        total = lag_counts.get(bound, total)
        label = '+Inf' if bound == float('inf') else repr(bound)
        lines.append('sky_apiserver_event_loop_lag_seconds_bucket'
                     f'{{le="{label}"}} {total}')
    lines.append(f'sky_apiserver_event_loop_lag_seconds_count {total}')
    lines.append('sky_apiserver_event_loop_lag_seconds_sum 0.0')

    lines += [
        '# HELP sky_apiserver_event_loop_lag_max_seconds Peak lag',
        '# TYPE sky_apiserver_event_loop_lag_max_seconds gauge',
    ]
    for pid, value in (lag_max_by_pid or {}).items():
        lines.append(
            f'sky_apiserver_event_loop_lag_max_seconds{{pid="{pid}"}} {value}')

    lines += [
        '# HELP sky_apiserver_process_cpu_total CPU times',
        '# TYPE sky_apiserver_process_cpu_total gauge',
    ]
    for pid, value in (cpu_by_pid or {}).items():
        lines.append('sky_apiserver_process_cpu_total'
                     f'{{pid="{pid}",type="worker",mode="user"}} {value}')
    return '\n'.join(lines) + '\n'


# ---------------------------------------------------------------------------
# Open-loop scheduling math
# ---------------------------------------------------------------------------


def test_schedule_offsets_are_evenly_spaced_at_the_target_rate():
    offsets = harness.schedule_offsets(target_qps=10, duration_seconds=1.0)
    assert len(offsets) == 10
    assert offsets[0] == 0.0
    gaps = [b - a for a, b in zip(offsets, offsets[1:])]
    assert all(abs(gap - 0.1) < 1e-9 for gap in gaps)


def test_schedule_offsets_never_runs_past_the_trial_duration():
    offsets = harness.schedule_offsets(target_qps=3, duration_seconds=1.0)
    assert len(offsets) == 3
    assert offsets[-1] < 1.0


def test_schedule_offsets_handles_fractional_rates():
    offsets = harness.schedule_offsets(target_qps=0.5, duration_seconds=10.0)
    assert offsets == [0.0, 2.0, 4.0, 6.0, 8.0]


def test_schedule_offsets_rejects_a_non_positive_rate():
    with pytest.raises(ValueError):
        harness.schedule_offsets(target_qps=0, duration_seconds=1.0)


def test_select_spec_interleaves_deterministically_by_weight():
    specs = [
        harness.RequestSpec(path='/a', weight=3),
        harness.RequestSpec(path='/b', weight=1),
    ]
    picked = [harness.select_spec(specs, i).path for i in range(8)]
    assert picked == ['/a', '/a', '/a', '/b'] * 2


def test_select_spec_rejects_a_zero_weight():
    specs = [harness.RequestSpec(path='/a', weight=0)]
    with pytest.raises(ValueError):
        harness.select_spec(specs, 0)


# ---------------------------------------------------------------------------
# Percentiles over raw samples
# ---------------------------------------------------------------------------


def test_percentile_interpolates_between_raw_samples():
    samples = [1.0, 2.0, 3.0, 4.0]
    assert harness.percentile(samples, 0) == 1.0
    assert harness.percentile(samples, 100) == 4.0
    assert harness.percentile(samples, 50) == pytest.approx(2.5)


def test_percentile_is_order_independent():
    assert harness.percentile([5.0, 1.0, 3.0], 50) == 3.0


def test_percentile_reports_the_raw_tail_value_not_a_bucket_edge():
    # 0.37s falls between the server histogram's 0.25 and 0.5 boundaries, so a
    # bucket-derived percentile could only have said "somewhere under 0.5".
    samples = [0.001] * 990 + [0.37] * 10
    assert harness.percentile(samples, 99.5) == pytest.approx(0.37)
    assert harness.percentile(samples, 50) == pytest.approx(0.001)


def test_percentile_rejects_an_empty_sample_set():
    with pytest.raises(ValueError):
        harness.percentile([], 50)


# ---------------------------------------------------------------------------
# INVALID vs FAIL classification
# ---------------------------------------------------------------------------


def test_trial_is_valid_when_requests_departed_on_schedule():
    status, reason = harness.classify_trial(100, 0.01, 0.2)
    assert status is harness.Status.OK
    assert reason is None


def test_trial_is_invalid_when_the_client_fell_behind_schedule():
    status, reason = harness.classify_trial(100, 0.5, 0.2)
    assert status is harness.Status.INVALID
    assert 'fell behind schedule' in reason


def test_trial_is_invalid_when_nothing_completed():
    status, _ = harness.classify_trial(0, None, 0.2)
    assert status is harness.Status.INVALID


def test_a_brief_client_stall_survives_a_percentile_but_not_the_gate():
    """Why the gate is the maximum and not p99.

    A 400ms client stall in a 60s/100qps trial delays ~40 of 6000 requests --
    0.67%, so p99 sees nothing at all, while those 40 requests each carry
    400ms of client-side delay in their reported latency. Gating on the
    percentile would discard exactly the event the gate exists to catch.
    """
    lateness = [0.0] * 5960 + [0.4] * 40
    assert harness.percentile(lateness, 99) == pytest.approx(0.0)
    assert harness.classify_trial(6000, harness.percentile(lateness, 99),
                                  0.2)[0] is harness.Status.OK
    status, reason = harness.classify_trial(6000, max(lateness), 0.2)
    assert status is harness.Status.INVALID
    assert 'fell behind schedule' in reason


def test_trial_is_invalid_when_held_streams_closed_early():
    status, reason = harness.classify_trial(100,
                                            0.01,
                                            0.2,
                                            streams_expected=32,
                                            streams_live=31)
    assert status is harness.Status.INVALID
    assert '1 of 32' in reason


@pytest.mark.asyncio
async def test_held_streams_liveness_counts_only_running_readers():
    held = harness._HeldStreams()  # pylint: disable=protected-access
    forever = asyncio.ensure_future(asyncio.sleep(30))
    finished = asyncio.ensure_future(asyncio.sleep(0))
    held.readers = [forever, finished]
    held.opened = 2
    await asyncio.sleep(0.05)
    # A reader that returned (server closed the stream) is not holding
    # anything, which is the whole distinction opened/live draws.
    assert held.live() == 1
    forever.cancel()
    await asyncio.gather(forever, finished, return_exceptions=True)


@pytest.mark.asyncio
async def test_streams_that_never_opened_invalidate_the_trial():
    """Zero opened streams must not read as "nothing to check".

    The scenario's premise is N held streams; if every open failed, the run
    measured a server under no concurrency at all, which is an invalid
    environment rather than a passing one.
    """
    app = _StubApp()
    app.metrics_pages = [_quiet_metrics(10)]
    app.stream_status = 500
    with _StubServer(app) as server:
        result = await harness.run_async(
            _config(server.url,
                    num_trials=1,
                    streams=harness.StreamSpec(path='/stream', count=3)),
            harness.Auth())

    assert result.streams_opened == 0
    assert result.streams_failed == 3
    assert result.status is harness.Status.INVALID
    assert 'held streams closed' in result.trials[0]['invalid_reason']


def test_summary_ignores_invalid_trials():

    def _trial(index, status, p99):
        return harness.TrialResult(index=index,
                                   status=status,
                                   invalid_reason=None,
                                   duration_seconds=1.0,
                                   offered_requests=10,
                                   completed_requests=10,
                                   offered_qps=10.0,
                                   achieved_qps=10.0,
                                   status_counts={'200': 10},
                                   failure_count=0,
                                   send_lateness_p99=0.001,
                                   send_lateness_max=0.002,
                                   streams_live=0,
                                   latency_p50=0.001,
                                   latency_p90=0.002,
                                   latency_p99=p99,
                                   latency_p999=0.01,
                                   latency_max=0.02,
                                   latency_histogram={},
                                   slow_request_rate=0.0,
                                   lag_bucket_deltas={},
                                   lag_observations_above_threshold=0.0,
                                   lag_max_peak_seconds=0.0,
                                   cpu_seconds_delta=1.0,
                                   cpu_seconds_per_request=0.1)

    trials = [
        _trial(0, harness.Status.OK, 0.01),
        _trial(1, harness.Status.OK, 0.03),
        _trial(2, harness.Status.INVALID, 99.0),
    ]
    summary = harness.summarize(trials)
    assert summary['latency_p99']['median'] == pytest.approx(0.02)
    assert summary['latency_p99']['n'] == 2


# ---------------------------------------------------------------------------
# Latency distribution
# ---------------------------------------------------------------------------


def test_latency_histogram_buckets_by_upper_bound():
    hist = harness.latency_histogram([0.0005, 0.003, 0.003, 0.2, 99.0])
    assert hist['0.001'] == 1
    assert hist['0.005'] == 2
    assert hist['0.25'] == 1
    assert hist['inf'] == 1
    assert sum(hist.values()) == 5


def test_latency_histogram_conserves_every_sample():
    samples = [0.001 * i for i in range(1, 500)]
    assert sum(harness.latency_histogram(samples).values()) == len(samples)


def test_rate_above_counts_only_slower_samples():
    samples = [0.01] * 99 + [0.5]
    assert harness.rate_above(samples, 0.1) == pytest.approx(0.01)
    assert harness.rate_above(samples, 1.0) == 0.0


def test_rate_above_is_stable_where_a_percentile_flips():
    """The reason slow_request_rate exists.

    Two runs whose slow-mode weight differs by a hair (0.9% vs 1.1%) put p99
    on opposite sides of the mode -- 10ms vs 500ms -- while the rate reports
    the small difference that is actually there.
    """
    below = [0.01] * 991 + [0.5] * 9
    above = [0.01] * 989 + [0.5] * 11
    assert harness.percentile(below, 99) == pytest.approx(0.01)
    assert harness.percentile(above, 99) == pytest.approx(0.5)
    assert harness.rate_above(below, 0.1) == pytest.approx(0.009)
    assert harness.rate_above(above, 0.1) == pytest.approx(0.011)


def test_pooled_histogram_sums_valid_trials_only():
    artifact = {
        'trials': [
            {
                'status': 'ok',
                'latency_histogram': {
                    '0.01': 5,
                    '0.25': 1
                }
            },
            {
                'status': 'ok',
                'latency_histogram': {
                    '0.01': 3,
                    '0.25': 2
                }
            },
            {
                'status': 'invalid',
                'latency_histogram': {
                    '0.01': 99
                }
            },
        ]
    }
    pooled = harness.pooled_histogram(artifact)
    assert pooled['0.01'] == 8
    assert pooled['0.25'] == 3


def test_latency_chart_makes_a_rare_mode_visible():
    """The reason the bars are log-scaled.

    A mode holding 1% of requests would be under one character wide against a
    99% mode on a linear scale -- i.e. invisible in the one view whose whole
    job is to show it.
    """
    chart = harness.format_histogram_chart({'0.01': 9900, '0.5': 100}, 'x')
    lines = {
        line.split()[0]: line
        for line in chart.splitlines()
        if line.strip().startswith('<=')
    }
    fast, slow = lines['<=10ms'], lines['<=500ms']
    fast_bar = fast.count('#')
    slow_bar = slow.count('#')
    assert fast_bar > slow_bar > fast_bar // 3
    assert '9900' in fast and '100' in slow


def test_latency_chart_never_renders_a_used_bucket_as_blank():
    chart = harness.format_histogram_chart({'0.01': 100000, '1.0': 1}, 'x')
    tail = [line for line in chart.splitlines() if '<=1s' in line][0]
    assert '#' in tail or '.' in tail


def test_latency_chart_reports_the_slow_rate_and_marks_the_threshold():
    chart = harness.format_histogram_chart({
        '0.01': 90,
        '0.1': 0,
        '0.5': 10
    },
                                           'x',
                                           threshold=0.1)
    assert 'over 100ms: 10 (10.00%)' in chart
    # The threshold bucket is marked so a reader can see where the count is
    # taken from.
    marked = [line for line in chart.splitlines() if '<=100ms' in line][0]
    assert '<' in marked


def test_latency_chart_is_empty_without_samples():
    assert harness.format_histogram_chart({}, 'x') == ''


def test_show_cli_draws_the_distribution(tmp_path, capsys):
    artifact = _artifact('run', 0.01)
    artifact['trials'] = [{
        'status': 'ok',
        'latency_histogram': {
            '0.01': 90,
            '0.5': 10
        }
    }]
    path = tmp_path / 'run.json'
    path.write_text(json.dumps(artifact))
    assert harness.main(['show', str(path)]) == 0
    out = capsys.readouterr().out
    assert 'Client-observed latency' in out
    assert 'over 100ms' in out


def test_decumulate_turns_cumulative_buckets_into_per_bucket_counts():
    # The server's histogram counts observations at or below each bound.
    cumulative = {'0.005': 100, '0.05': 108, '0.25': 110, 'inf': 110}
    assert harness.decumulate(cumulative) == {
        '0.005': 100,
        '0.05': 8,
        '0.25': 2,
        'inf': 0,
    }


def test_decumulate_clamps_a_counter_that_went_backwards():
    # A worker restarting mid-run resets its counters; a negative bucket must
    # not subtract from the observations the other workers recorded.
    assert harness.decumulate({'0.005': 10, '0.05': 4})['0.05'] == 0


def test_pooled_lag_buckets_sums_trials_then_decumulates():
    artifact = {
        'trials': [
            {
                'status': 'ok',
                'lag_bucket_deltas': {
                    '0.005': 10,
                    '0.25': 12,
                    'inf': 12
                }
            },
            {
                'status': 'ok',
                'lag_bucket_deltas': {
                    '0.005': 20,
                    '0.25': 21,
                    'inf': 21
                }
            },
            {
                'status': 'invalid',
                'lag_bucket_deltas': {
                    '0.005': 999,
                    'inf': 999
                }
            },
        ]
    }
    pooled = harness.pooled_lag_buckets(artifact)
    assert pooled['0.005'] == 30
    assert pooled['0.25'] == 3
    assert pooled['inf'] == 0


def test_run_charts_cover_both_the_client_and_the_loop():
    artifact = {
        'name': 'run',
        'config': {
            'slow_request_threshold_seconds': 0.1,
            'lag_threshold_seconds': 0.25,
        },
        'trials': [{
            'status': 'ok',
            'latency_histogram': {
                '0.01': 90,
                '0.5': 10
            },
            'lag_bucket_deltas': {
                '0.005': 100,
                '0.5': 105,
                'inf': 105
            },
        }],
    }
    latency, lag = harness.format_run_charts(artifact)
    assert 'Client-observed latency' in latency and 'requests' in latency
    assert 'Server event loop lag' in lag and 'loop ticks' in lag
    # The lag chart must show per-bucket counts, not the cumulative ones: the
    # 0.5 bucket holds 105-100=5 ticks, not 105.
    tail = [line for line in lag.splitlines() if '<=500ms' in line][0]
    assert tail.split()[-2] == '5'


def test_format_histograms_shows_both_distributions():

    def _hist_artifact(slow):
        return {
            'trials': [{
                'status': 'ok',
                'latency_histogram': {
                    '0.01': 100 - slow,
                    '0.25': slow
                }
            }]
        }

    out = harness.format_histograms(_hist_artifact(1), _hist_artifact(20))
    assert 'Pooled latency distribution' in out
    assert '1.00%' in out
    assert '20.00%' in out


# ---------------------------------------------------------------------------
# Server-side metric parsing
# ---------------------------------------------------------------------------


def test_lag_buckets_are_parsed_and_summed_across_workers():
    text = _metrics_text({0.005: 100, 0.25: 120, float('inf'): 125})
    buckets = harness.parse_lag_buckets(text)
    assert buckets[0.005] == 100
    assert buckets[0.25] == 120
    assert buckets[float('inf')] == 125


def test_observations_above_counts_only_the_slow_ticks():
    before = harness.parse_lag_buckets(
        _metrics_text({
            0.005: 100,
            0.25: 100,
            float('inf'): 100
        }))
    after = harness.parse_lag_buckets(
        _metrics_text({
            0.005: 190,
            0.25: 195,
            float('inf'): 198
        }))
    deltas = harness.bucket_deltas(before, after)
    assert harness.observations_above(deltas, 0.25) == 3


def test_observations_above_rejects_a_non_boundary_threshold():
    deltas = harness.bucket_deltas({},
                                   harness.parse_lag_buckets(
                                       _metrics_text({float('inf'): 5})))
    with pytest.raises(ValueError):
        harness.observations_above(deltas, 0.3)


def test_bucket_deltas_clamp_a_counter_reset():
    before = {0.25: 100.0, float('inf'): 100.0}
    after = {0.25: 5.0, float('inf'): 5.0}
    assert harness.bucket_deltas(before, after)[0.25] == 0.0


def test_lag_max_takes_the_worst_pid():
    text = _metrics_text({float('inf'): 1},
                         lag_max_by_pid={
                             '11': 0.02,
                             '12': 0.4
                         })
    assert harness.parse_lag_max(text) == pytest.approx(0.4)


def test_cpu_total_sums_across_pids():
    text = _metrics_text({float('inf'): 1}, cpu_by_pid={'11': 1.5, '12': 2.5})
    assert harness.parse_cpu_total(text) == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def _artifact(name, p99, lag_above=0.0, status=harness.Status.OK):
    return {
        'artifact_version': harness.ARTIFACT_VERSION,
        'name': name,
        'created_at': '2026-01-01T00:00:00+00:00',
        'status': status.value,
        'invalid_reason': None,
        'config': {},
        'baseline': None,
        'trials': [],
        'summary': {
            'latency_p99': {
                'median': p99,
                'q1': p99,
                'q3': p99,
                'iqr': 0.0,
                'min': p99,
                'max': p99,
                'n': 5.0,
            },
            'lag_observations_above_threshold': {
                'median': lag_above,
                'q1': lag_above,
                'q3': lag_above,
                'iqr': 0.0,
                'min': lag_above,
                'max': lag_above,
                'n': 5.0,
            },
        },
        'streams_opened': 0,
        'streams_live_at_end': 0,
        'streams_failed': 0,
    }


def test_compare_flags_a_regression_that_clears_the_noise_floor():
    base = _artifact('master', 0.010)
    cand = _artifact('branch', 0.030)
    noise = _artifact('master-again', 0.011)
    result = {c.metric: c for c in harness.compare(base, cand, noise)}
    p99 = result['latency_p99']
    assert p99.delta == pytest.approx(0.020)
    assert p99.noise_delta == pytest.approx(0.001)
    assert p99.verdict == 'worse'


def test_compare_calls_a_delta_inside_the_noise_floor_noise():
    base = _artifact('master', 0.010)
    cand = _artifact('branch', 0.0105)
    noise = _artifact('master-again', 0.012)
    result = {c.metric: c for c in harness.compare(base, cand, noise)}
    assert result['latency_p99'].verdict == 'noise'


def test_compare_without_a_noise_floor_still_reports_direction():
    result = {
        c.metric: c
        for c in harness.compare(_artifact('a', 0.02), _artifact('b', 0.01))
    }
    assert result['latency_p99'].verdict == 'better'
    assert result['latency_p99'].noise_delta is None


def test_compare_treats_a_higher_achieved_qps_as_better():
    base = _artifact('a', 0.01)
    cand = _artifact('b', 0.01)
    for artifact, qps in ((base, 100.0), (cand, 120.0)):
        artifact['summary']['achieved_qps'] = {
            'median': qps,
            'q1': qps,
            'q3': qps,
            'iqr': 0.0,
            'min': qps,
            'max': qps,
            'n': 5.0,
        }
    result = {c.metric: c for c in harness.compare(base, cand)}
    assert result['achieved_qps'].verdict == 'better'


def test_compare_cli_prints_a_table(tmp_path, capsys):
    base_path = tmp_path / 'master.json'
    cand_path = tmp_path / 'branch.json'
    noise_path = tmp_path / 'master-again.json'
    base_path.write_text(json.dumps(_artifact('master', 0.010)))
    cand_path.write_text(json.dumps(_artifact('branch', 0.030)))
    noise_path.write_text(json.dumps(_artifact('master-again', 0.011)))

    exit_code = harness.main([
        'compare',
        str(base_path),
        str(cand_path), '--noise-floor',
        str(noise_path)
    ])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert 'latency_p99' in out
    assert 'worse' in out


def test_config_mismatches_reports_only_load_shape_keys():
    base = _artifact('a', 0.01)
    cand = _artifact('b', 0.01)
    base['config'] = {'target_qps': 100.0, 'trial_seconds': 60.0}
    cand['config'] = {'target_qps': 50.0, 'trial_seconds': 60.0}
    assert harness.config_mismatches(base, cand) == ['target_qps']
    cand['config'] = dict(base['config'])
    assert not harness.config_mismatches(base, cand)


def test_compare_cli_warns_when_the_workloads_differ(tmp_path, capsys):
    base = _artifact('master', 0.010)
    cand = _artifact('branch', 0.030)
    base['config'] = {'target_qps': 100.0}
    cand['config'] = {'target_qps': 50.0}
    base_path = tmp_path / 'master.json'
    cand_path = tmp_path / 'branch.json'
    base_path.write_text(json.dumps(base))
    cand_path.write_text(json.dumps(cand))

    assert harness.main(['compare', str(base_path), str(cand_path)]) == 0
    out = capsys.readouterr().out
    assert 'different workloads' in out
    assert 'target_qps' in out


def test_compare_cli_rejects_an_artifact_from_another_version(tmp_path):
    stale = _artifact('old', 0.01)
    stale['artifact_version'] = harness.ARTIFACT_VERSION + 1
    path = tmp_path / 'old.json'
    path.write_text(json.dumps(stale))
    with pytest.raises(ValueError, match='artifact version'):
        harness.main(['compare', str(path), str(path)])


# ---------------------------------------------------------------------------
# End-to-end against a real HTTP server
# ---------------------------------------------------------------------------


class _StubServer:
    """A loopback HTTP server exposing an app-controlled /metrics page.

    Binds its own socket before handing it to uvicorn so parallel xdist workers
    cannot race for the same port.
    """

    def __init__(self, app):
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(('127.0.0.1', 0))
        self.port = self._socket.getsockname()[1]
        self._server = uvicorn.Server(
            uvicorn.Config(app,
                           log_level='critical',
                           access_log=False,
                           lifespan='off'))
        self._thread = threading.Thread(
            target=lambda: self._server.run(sockets=[self._socket]),
            daemon=True)

    @property
    def url(self) -> str:
        return f'http://127.0.0.1:{self.port}'

    def __enter__(self):
        self._thread.start()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self._server.started:
                return self
            time.sleep(0.02)
        raise RuntimeError('stub server did not start')

    def __exit__(self, *exc):
        self._server.should_exit = True
        self._thread.join(timeout=10)
        self._socket.close()


class _StubApp:
    """ASGI app serving /ok, /slow, /metrics and an endless /stream."""

    def __init__(self):
        self.metrics_pages = []
        self.request_counts = {}
        self.open_streams = 0
        self.slow_seconds = 0.0
        self.stream_status = 200

    def _next_metrics(self) -> str:
        if len(self.metrics_pages) > 1:
            return self.metrics_pages.pop(0)
        return self.metrics_pages[0] if self.metrics_pages else ''

    async def __call__(self, scope, receive, send):
        assert scope['type'] == 'http'
        path = scope['path']
        self.request_counts[path] = self.request_counts.get(path, 0) + 1

        if path == '/metrics':
            body = self._next_metrics().encode()
        elif path == '/stream':
            if self.stream_status != 200:
                await send({
                    'type': 'http.response.start',
                    'status': self.stream_status,
                    'headers': [(b'content-type', b'text/plain')],
                })
                await send({'type': 'http.response.body', 'body': b''})
                return
            self.open_streams += 1
            await send({
                'type': 'http.response.start',
                'status': 200,
                'headers': [(b'content-type', b'text/plain')],
            })
            # Watch for http.disconnect alongside the send loop so the open
            # count drops as soon as the harness lets the response go.
            disconnected = asyncio.ensure_future(receive())
            try:
                while not disconnected.done():
                    await send({
                        'type': 'http.response.body',
                        'body': b'.',
                        'more_body': True,
                    })
                    await asyncio.sleep(0.02)
            finally:
                disconnected.cancel()
                self.open_streams -= 1
            return
        elif path == '/slow':
            await asyncio.sleep(self.slow_seconds)
            body = b'slow'
        elif path == '/boom':
            await send({
                'type': 'http.response.start',
                'status': 500,
                'headers': [(b'content-type', b'text/plain')],
            })
            await send({'type': 'http.response.body', 'body': b'boom'})
            return
        else:
            body = b'ok'

        await send({
            'type': 'http.response.start',
            'status': 200,
            'headers': [(b'content-type', b'text/plain')],
        })
        await send({'type': 'http.response.body', 'body': body})


def _config(app_url, **overrides):
    defaults = dict(
        name='unit',
        base_url=app_url,
        metrics_url=f'{app_url}/metrics',
        requests=[harness.RequestSpec(path='/ok')],
        target_qps=200.0,
        trial_seconds=0.05,
        num_trials=2,
        baseline_seconds=0.02,
        warmup_seconds=0.01,
        inter_trial_seconds=0.0,
        # Millisecond-scale trials see scheduler jitter comparable to their
        # own length. Cases that exercise the lateness gate set a real
        # threshold themselves.
        max_send_lateness_seconds=60.0,
    )
    defaults.update(overrides)
    return harness.HarnessConfig(**defaults)


@pytest.mark.asyncio
async def test_run_produces_a_comparable_artifact():
    app = _StubApp()
    quiet = _quiet_metrics(100,
                           lag_max_by_pid={'1': 0.001},
                           cpu_by_pid={'1': 1.0})
    # Baseline pair, then one pair per trial. Lag only accrues during trials.
    app.metrics_pages = [
        quiet,
        quiet,
        quiet,
        _metrics_text({
            0.25: 108,
            float('inf'): 110
        },
                      lag_max_by_pid={'1': 0.3},
                      cpu_by_pid={'1': 2.0}),
        _metrics_text({
            0.25: 108,
            float('inf'): 110
        },
                      lag_max_by_pid={'1': 0.3},
                      cpu_by_pid={'1': 2.0}),
        _metrics_text({
            0.25: 118,
            float('inf'): 120
        },
                      lag_max_by_pid={'1': 0.3},
                      cpu_by_pid={'1': 3.0}),
    ]
    with _StubServer(app) as server:
        result = await harness.run_async(_config(server.url), harness.Auth())

    assert result.status is harness.Status.OK
    assert result.artifact_version == harness.ARTIFACT_VERSION
    assert len(result.trials) == 2
    assert app.request_counts['/ok'] > 0
    first = result.trials[0]
    assert first['status'] == harness.Status.OK.value
    assert first['status_counts']['200'] == first['completed_requests']
    assert first['failure_count'] == 0
    assert first['lag_observations_above_threshold'] == 2
    assert first['lag_max_peak_seconds'] == pytest.approx(0.3)
    assert first['cpu_seconds_delta'] == pytest.approx(1.0)
    assert first['cpu_seconds_per_request'] == pytest.approx(
        1.0 / first['completed_requests'])
    assert 'latency_p99' in result.summary


@pytest.mark.asyncio
async def test_run_aborts_as_invalid_when_the_baseline_is_already_lagging():
    app = _StubApp()
    app.metrics_pages = [
        _quiet_metrics(100),
        # Four ticks landed above 0.05s with no load at all.
        _metrics_text({
            0.005: 100,
            0.05: 100,
            float('inf'): 104
        }),
    ]
    with _StubServer(app) as server:
        result = await harness.run_async(_config(server.url), harness.Auth())

    assert result.status is harness.Status.INVALID
    assert 'baseline lag already elevated' in result.invalid_reason
    assert result.trials == []
    # The abort must happen before any load is offered.
    assert '/ok' not in app.request_counts


@pytest.mark.asyncio
async def test_a_slow_server_stays_a_valid_trial_and_shows_up_in_latency():
    """Server slowness is the measurement, not a reason to discard it.

    Requests still depart on schedule when the server is the bottleneck, so
    the trial must classify OK and carry the damage in latency/timeouts --
    only a client that cannot keep its own schedule invalidates a trial.
    """
    app = _StubApp()
    # Far slower than the trial is long, and slower than the client timeout, so
    # most requests time out rather than complete.
    app.slow_seconds = 5.0
    app.metrics_pages = [_quiet_metrics(10)]
    with _StubServer(app) as server:
        result = await harness.run_async(
            _config(server.url,
                    requests=[harness.RequestSpec(path='/slow')],
                    target_qps=50.0,
                    trial_seconds=0.1,
                    num_trials=1,
                    warmup_seconds=0.0,
                    request_timeout_seconds=0.2), harness.Auth())

    trial = result.trials[0]
    assert trial['status'] == harness.Status.OK.value
    assert trial['failure_count'] > 0
    assert trial['latency_p99'] >= 0.2


@pytest.mark.asyncio
async def test_a_starved_load_generator_makes_the_trial_invalid():
    """Blocking the client's own loop mid-trial must invalidate the trial.

    A blocked client loop delays every pending request timer, so requests
    depart late; that lateness is the client's fault and the trial's numbers
    describe the load generator, not the server.
    """
    app = _StubApp()
    app.metrics_pages = [_quiet_metrics(10)]

    async def _block_client_loop():
        # Past baseline + warmup and into the trial window, then a synchronous
        # sleep on this loop -- exactly what a starved/oversubscribed load
        # generator looks like to the scheduler.
        await asyncio.sleep(0.15)
        time.sleep(0.3)

    with _StubServer(app) as server:
        result, _ = await asyncio.gather(
            harness.run_async(
                _config(server.url,
                        target_qps=100.0,
                        trial_seconds=0.4,
                        baseline_seconds=0.05,
                        warmup_seconds=0.05,
                        num_trials=1,
                        max_send_lateness_seconds=0.1), harness.Auth()),
            _block_client_loop())

    trial = result.trials[0]
    assert trial['status'] == harness.Status.INVALID.value
    assert 'fell behind schedule' in trial['invalid_reason']
    assert trial['send_lateness_p99'] > 0.1


@pytest.mark.asyncio
async def test_latency_is_measured_from_the_scheduled_start_not_the_send():
    """A request already overdue when it is sent must report the whole delay.

    This is the coordinated-omission guarantee in its smallest form. If the
    client's own loop is congested, a request's timer fires late; latency taken
    from the scheduled start counts that lateness, while latency taken from the
    moment the request went on the wire reports a fast request and the delay
    disappears from the distribution entirely.
    """
    app = _StubApp()
    app.metrics_pages = [_quiet_metrics(10)]
    late_by = 0.5
    samples = harness._Samples()  # pylint: disable=protected-access
    with _StubServer(app) as server:
        async with httpx.AsyncClient(timeout=10.0) as client:
            loop = asyncio.get_running_loop()
            await harness._issue(  # pylint: disable=protected-access
                client, harness.RequestSpec(path='/ok'), f'{server.url}/ok',
                loop.time() - late_by, samples)

    assert samples.status_counts == {'200': 1}
    assert samples.latencies[0] >= late_by


@pytest.mark.asyncio
async def test_queueing_delay_is_included_in_reported_latency():
    """Requests stuck behind a backlog must carry the wait in their latency.

    A single connection serializes ten 0.2s requests over ~2s, so the request
    due at 0.45s does not go on the wire until ~1.8s. Measured from its
    scheduled start its latency is well over a second; measured from the moment
    it was actually sent it would look like a normal 0.2s request and the
    backlog would vanish from the tail. That erasure is coordinated omission,
    and it is exactly what would hide a server stall in a benchmark.
    """
    app = _StubApp()
    app.slow_seconds = 0.2
    app.metrics_pages = [_quiet_metrics(10)]
    with _StubServer(app) as server:
        config = _config(server.url,
                         requests=[harness.RequestSpec(path='/slow')],
                         target_qps=20.0,
                         trial_seconds=0.5,
                         num_trials=1,
                         baseline_seconds=0.01,
                         warmup_seconds=0.0,
                         max_connections=1,
                         request_timeout_seconds=10.0)
        result = await harness.run_async(config, harness.Auth())

    trial = result.trials[0]
    assert trial['completed_requests'] == 10
    # Ten serialized 0.2s requests finish at ~2.0s while the last was due at
    # 0.45s. Anything at or near the server's own 0.2s handling time would mean
    # the wait had been dropped.
    assert trial['latency_max'] > 1.0
    assert trial['latency_p50'] > 0.5


@pytest.mark.asyncio
async def test_non_2xx_responses_are_counted_as_failures():
    app = _StubApp()
    app.metrics_pages = [_quiet_metrics(10)]
    with _StubServer(app) as server:
        result = await harness.run_async(
            _config(server.url,
                    requests=[harness.RequestSpec(path='/boom')],
                    target_qps=100.0,
                    trial_seconds=0.05,
                    num_trials=1,
                    warmup_seconds=0.0), harness.Auth())

    trial = result.trials[0]
    assert trial['failure_count'] == trial['completed_requests']
    assert trial['status_counts']['500'] == trial['completed_requests']
    # Failing fast is still a sustained rate, so the trial itself is valid --
    # the caller is the one who decides that 500s mean the run failed.
    assert trial['status'] == harness.Status.OK.value


@pytest.mark.asyncio
async def test_streams_are_held_open_for_the_whole_run_and_closed_after(
        monkeypatch):
    app = _StubApp()
    app.metrics_pages = [_quiet_metrics(10)]
    observed = []
    original_drive = harness._drive_load  # pylint: disable=protected-access

    async def _spy(client, config, duration):
        observed.append(app.open_streams)
        return await original_drive(client, config, duration)

    monkeypatch.setattr(harness, '_drive_load', _spy)
    with _StubServer(app) as server:
        result = await harness.run_async(
            _config(server.url,
                    num_trials=1,
                    warmup_seconds=0.0,
                    streams=harness.StreamSpec(path='/stream', count=3)),
            harness.Auth())

    assert result.streams_opened == 3
    assert result.streams_failed == 0
    assert observed and all(count == 3 for count in observed)
    for _ in range(100):
        if app.open_streams == 0:
            break
        await asyncio.sleep(0.05)
    assert app.open_streams == 0


@pytest.mark.asyncio
async def test_auth_headers_and_cookies_reach_the_server():
    seen = {}

    class _AuthApp(_StubApp):

        async def __call__(self, scope, receive, send):
            if scope['path'] == '/ok':
                seen.update(
                    {k.decode(): v.decode() for k, v in scope['headers']})
            await super().__call__(scope, receive, send)

    app = _AuthApp()
    app.metrics_pages = [_quiet_metrics(10)]
    with _StubServer(app) as server:
        await harness.run_async(
            _config(server.url,
                    num_trials=1,
                    warmup_seconds=0.0,
                    target_qps=20.0,
                    trial_seconds=0.05),
            harness.Auth(headers={'Authorization': 'Bearer sky_unit'},
                         cookies={'session': 'abc'}))

    assert seen['authorization'] == 'Bearer sky_unit'
    assert 'session=abc' in seen['cookie']


def test_artifact_round_trips_through_json(tmp_path):
    app_summary = harness.RunResult(
        artifact_version=harness.ARTIFACT_VERSION,
        name='round-trip',
        created_at='2026-01-01T00:00:00+00:00',
        status=harness.Status.OK,
        invalid_reason=None,
        config={'target_qps': 100},
        baseline=None,
        trials=[],
        summary={'latency_p99': {
            'median': 0.01,
            'n': 1.0
        }},
        streams_opened=0,
        streams_failed=0,
        streams_live_at_end=0,
    )
    path = app_summary.write(tmp_path / 'nested' / 'run.json')
    reloaded = json.loads(pathlib.Path(path).read_text())
    assert reloaded['status'] == 'ok'
    assert reloaded['summary']['latency_p99']['median'] == 0.01
