"""Open-loop load harness for measuring API server event loop lag.

The harness drives a fixed arrival rate against an API server and records both
client-observed latency and the server's own event loop lag metrics, so a run
on one build can be compared against a run on another.

Load is generated *open loop*: every request's start time is computed up front
from the trial's start instant and the target rate, and each request's latency
is measured from that scheduled start rather than from the moment the request
was actually put on the wire. A server stall therefore shows up in the tail of
every request that was due while the server was stalled, instead of being
hidden by a client that simply waited to issue them (coordinated omission).

Authentication is supplied by the caller as headers and/or cookies; the harness
does not know how they were obtained.

Usage as a library::

    config = HarnessConfig(name='flood',
                           base_url='http://host:46580',
                           metrics_url='http://host:9090/metrics',
                           requests=[RequestSpec(path='/api/health')],
                           target_qps=100)
    result = run(config, Auth(headers={'Authorization': 'Bearer ...'}))
    result.write(pathlib.Path('flood.json'))

Usage as a CLI, to compare two artifacts::

    python tests/load_tests/loop_lag_harness.py compare master.json branch.json \
        --noise-floor master-again.json
"""
import argparse
import asyncio
import contextlib
import dataclasses
import datetime
import enum
import json
import math
import pathlib
import statistics
import time
from typing import (Any, AsyncIterator, Dict, Iterator, List, Mapping, Optional,
                    Sequence, Tuple, Union)

import httpx
from prometheus_client import parser as prom_parser

# Bumped when the artifact layout changes in a way that makes older files
# unreadable by `compare`.
ARTIFACT_VERSION = 1

# Upper bounds (seconds) of the client-side latency histogram carried in every
# trial. Point percentiles alone cannot answer distribution questions after the
# fact -- notably how *often* requests land in a slow mode, which is the stable
# way to measure a second mode that sits near a percentile boundary. Log-spaced
# so the tail keeps resolution without inflating the artifact.
LATENCY_HISTOGRAM_BOUNDS = (0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.075, 0.1,
                            0.15, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, float('inf'))

# A request this slow is user-visible and well above the healthy mode of every
# lane measured so far, so the fraction of requests above it is a direct
# measure of a slow mode's weight rather than of where a percentile happened
# to land.
SLOW_REQUEST_THRESHOLD_SECONDS = 0.1

# Server-side metrics the harness scrapes. These are the names of the metric
# *families*; the histogram's samples carry the `_bucket`/`_sum`/`_count`
# suffixes.
LAG_HISTOGRAM_METRIC = 'sky_apiserver_event_loop_lag_seconds'
LAG_MAX_GAUGE_METRIC = 'sky_apiserver_event_loop_lag_max_seconds'
CPU_TOTAL_METRIC = 'sky_apiserver_process_cpu_total'

# Summary keys carried in the artifact and understood by `compare`. Every key
# is reported per trial and aggregated across trials.
SUMMARY_METRICS = (
    'latency_p50',
    'latency_p90',
    'latency_p99',
    'latency_p999',
    'latency_max',
    'slow_request_rate',
    'achieved_qps',
    'lag_observations_above_threshold',
    'lag_max_peak_seconds',
    'cpu_seconds_per_request',
)

# Metrics where a larger value is the worse outcome. Used only to label the
# direction of a change in the comparison table.
_HIGHER_IS_WORSE = frozenset(SUMMARY_METRICS) - {'achieved_qps'}


class Status(str, enum.Enum):
    """Outcome of a phase or a whole run, as judged by the harness itself.

    A harness verdict is only ever OK or INVALID. INVALID means the numbers
    cannot be trusted and must not be compared -- the server was already
    unhealthy before load started, or the client failed to sustain the offered
    rate. Deciding that a valid run *failed* (lag too high, errors returned) is
    the caller's job.
    """
    OK = 'ok'
    INVALID = 'invalid'


@dataclasses.dataclass
class Auth:
    """Credentials to attach to every request the harness issues."""
    headers: Dict[str, str] = dataclasses.field(default_factory=dict)
    cookies: Dict[str, str] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class RequestSpec:
    """One kind of request in the load mix.

    weight is a repeat count within the deterministic interleave, so a run is
    reproducible: request i always uses the same spec.
    """
    path: str
    method: str = 'GET'
    json_body: Optional[Dict[str, Any]] = None
    params: Optional[Dict[str, str]] = None
    weight: int = 1


@dataclasses.dataclass
class StreamSpec:
    """Long-lived responses held open for the whole run."""
    path: str
    count: int
    method: str = 'GET'
    params: Optional[Dict[str, str]] = None
    json_body: Optional[Dict[str, Any]] = None


@dataclasses.dataclass
class HarnessConfig:
    """Everything that defines a run, and therefore what makes runs comparable.

    Durations are parameters rather than constants so tests can exercise the
    full phase sequence in milliseconds.
    """
    name: str
    base_url: str
    metrics_url: str
    requests: List[RequestSpec]
    target_qps: float
    trial_seconds: float = 60.0
    num_trials: int = 5
    baseline_seconds: float = 15.0
    warmup_seconds: float = 10.0
    inter_trial_seconds: float = 5.0
    streams: Optional[StreamSpec] = None
    # Lag above this is already present with no load, so the run tells us
    # nothing about the change under test. Same boundary as
    # lag_threshold_seconds: an idle server's background daemons produce
    # 50-100ms ticks (observed in CI), so gating tighter than the load
    # assertions rejects healthy servers. Must be a bucket boundary of the
    # server's lag histogram, which is what makes the check exact.
    baseline_lag_threshold_seconds: float = 0.25
    # Also the threshold the per-trial lag observation count is taken above.
    lag_threshold_seconds: float = 0.25
    # A trial whose worst send lateness exceeds this was starved on the client
    # side, so its numbers describe the load generator, not the server.
    max_send_lateness_seconds: float = 0.2
    # Requests slower than this count toward slow_request_rate.
    slow_request_threshold_seconds: float = SLOW_REQUEST_THRESHOLD_SECONDS
    # How often to read the peak-lag gauge during a trial. The gauge holds the
    # peak of a 30s tumbling window, so reading it only at the end of a 60s
    # trial misses any spike from the first window entirely; sampling below
    # the window length means every window is observed. The cost is a handful
    # of extra /metrics reads per trial, which do land on the loop being
    # measured (the metrics app is a task on the same loop) -- ~4 scrapes
    # against 6000 requests.
    lag_gauge_sample_seconds: float = 15.0
    request_timeout_seconds: float = 30.0
    max_connections: int = 512
    verify_tls: bool = True


@dataclasses.dataclass
class TrialResult:
    """One independent measurement interval."""
    index: int
    status: Status
    invalid_reason: Optional[str]
    duration_seconds: float
    offered_requests: int
    completed_requests: int
    offered_qps: float
    achieved_qps: float
    status_counts: Dict[str, int]
    failure_count: int
    send_lateness_p99: Optional[float]
    # The gate; see classify_trial for why the worst case and not a quantile.
    send_lateness_max: Optional[float]
    # How many held streams were still open when the trial ended.
    streams_live: int
    latency_p50: Optional[float]
    latency_p90: Optional[float]
    latency_p99: Optional[float]
    latency_p999: Optional[float]
    latency_max: Optional[float]
    latency_histogram: Dict[str, int]
    slow_request_rate: Optional[float]
    lag_bucket_deltas: Dict[str, float]
    lag_observations_above_threshold: float
    # Max over the gauge samples taken during the trial, not a single
    # end-of-trial read; see _sample_lag_peak.
    lag_max_peak_seconds: float
    lag_gauge_samples: int
    cpu_seconds_delta: float
    cpu_seconds_per_request: Optional[float]


@dataclasses.dataclass
class BaselineResult:
    """The no-load phase that decides whether the run is worth running."""
    status: Status
    invalid_reason: Optional[str]
    duration_seconds: float
    lag_bucket_deltas: Dict[str, float]
    lag_observations_above_threshold: float
    lag_max_peak_seconds: float


@dataclasses.dataclass
class RunResult:
    """A whole run: config, baseline, per-trial numbers, cross-trial summary."""
    artifact_version: int
    name: str
    created_at: str
    status: Status
    invalid_reason: Optional[str]
    config: Dict[str, Any]
    baseline: Optional[Dict[str, Any]]
    trials: List[Dict[str, Any]]
    summary: Dict[str, Dict[str, float]]
    streams_opened: int
    # Opened counts connections that were established; this counts the ones
    # that were still open when the run ended, which is what the scenario
    # actually depends on.
    streams_live_at_end: int
    streams_failed: int

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    def write(self, path: Union[str, pathlib.Path]) -> pathlib.Path:
        path = pathlib.Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding='utf-8')
        return path


# --------------------------------------------------------------------------
# Pure helpers. Kept free of I/O so they can be unit tested directly.
# --------------------------------------------------------------------------


def schedule_offsets(target_qps: float, duration_seconds: float) -> List[float]:
    """Offsets from trial start at which each request is due to be sent.

    Evenly spaced at 1/target_qps. The count is floored, so a trial never runs
    past its nominal duration.
    """
    if target_qps <= 0:
        raise ValueError(f'target_qps must be positive, got {target_qps}')
    if duration_seconds < 0:
        raise ValueError(
            f'duration_seconds must not be negative, got {duration_seconds}')
    interval = 1.0 / target_qps
    count = int(math.floor(target_qps * duration_seconds + 1e-9))
    return [i * interval for i in range(count)]


def select_spec(specs: Sequence[RequestSpec], index: int) -> RequestSpec:
    """Pick the spec for request `index` by deterministic weighted interleave.

    Deterministic rather than random so that two runs of the same config issue
    exactly the same sequence of requests.
    """
    if not specs:
        raise ValueError('at least one RequestSpec is required')
    weights = [spec.weight for spec in specs]
    if any(weight < 1 for weight in weights):
        raise ValueError(f'weights must be >= 1, got {weights}')
    period = sum(weights)
    position = index % period
    for spec, weight in zip(specs, weights):
        if position < weight:
            return spec
        position -= weight
    raise AssertionError('unreachable: position exceeded total weight')


def latency_histogram(samples: Sequence[float]) -> Dict[str, int]:
    """Bucket raw latencies by upper bound, keyed for JSON.

    Non-cumulative: each bucket holds the samples that fell in it, so a
    reader can recover both a distribution and any above-threshold rate
    without the raw samples, which are far too many to carry.
    """
    counts = {str(bound): 0 for bound in LATENCY_HISTOGRAM_BOUNDS}
    for sample in samples:
        for bound in LATENCY_HISTOGRAM_BOUNDS:
            if sample <= bound:
                counts[str(bound)] += 1
                break
    return counts


def rate_above(samples: Sequence[float], threshold: float) -> float:
    """Fraction of samples slower than `threshold`.

    A rate rather than a quantile: when a slow mode's weight sits near a
    percentile boundary (~1% for p99), that percentile flips between runs
    while the rate itself moves smoothly, so this is what a comparison can
    actually be made on.
    """
    if not samples:
        return 0.0
    return sum(1 for sample in samples if sample > threshold) / len(samples)


def percentile(samples: Sequence[float], q: float) -> float:
    """Linearly interpolated percentile over the raw samples.

    Computed from the samples themselves rather than from histogram buckets, so
    the tail is not rounded up to a bucket boundary.
    """
    if not samples:
        raise ValueError('percentile of an empty sample set')
    if not 0.0 <= q <= 100.0:
        raise ValueError(f'q must be in [0, 100], got {q}')
    ordered = sorted(samples)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * (q / 100.0)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def classify_trial(completed_requests: int,
                   send_lateness_max: Optional[float],
                   max_send_lateness: float,
                   streams_expected: int = 0,
                   streams_live: int = 0) -> Tuple[Status, Optional[str]]:
    """Decide whether a trial's numbers are usable.

    The gate is *send lateness* -- how far behind schedule requests actually
    went on the wire -- because it separates the two causes of a slow trial. A
    starved load generator issues requests late, so lateness grows and the
    trial has measured the client, not the server: INVALID. A slow server
    leaves the schedule intact (requests still depart on time) and shows up in
    response latency instead, which is a real measurement the caller should
    judge as pass or fail. A completion-rate gate cannot tell these apart: a
    server stall stretches the interval and reads as the client falling
    behind.

    Lateness is judged on the *maximum*, not a percentile: a client stall is a
    brief event, and a 400ms stall in a 6000-request trial delays ~0.7% of
    them, which a p99 discards entirely -- the statistic would drop exactly
    the event it exists to catch. Any request that departed late carries that
    delay in its latency, so the worst one bounds the contamination.

    A scenario that holds streams open is only that scenario while they are
    open, so a trial that lost some has not measured what it claims.
    """
    if completed_requests <= 0:
        return Status.INVALID, 'no requests completed'
    if send_lateness_max is not None and send_lateness_max > max_send_lateness:
        return (Status.INVALID,
                f'load generator fell behind schedule: worst send lateness '
                f'{send_lateness_max:.3f}s exceeds {max_send_lateness}s; the '
                'client was starved, so the numbers do not describe the '
                'server')
    if streams_expected and streams_live < streams_expected:
        return (Status.INVALID,
                f'{streams_expected - streams_live} of {streams_expected} '
                'held streams closed before the trial ended, so the server '
                'was not under the concurrency this scenario measures')
    return Status.OK, None


def _samples(metrics_text: str, metric: str) -> Iterator[Any]:
    """Every sample of one metric family in a scrape.

    The server exports each metric per worker process, so a family carries one
    sample per pid (and per bucket, for a histogram); what differs between the
    metrics below is only how those samples combine.
    """
    for family in prom_parser.text_string_to_metric_families(metrics_text):
        if family.name == metric:
            yield from family.samples


def parse_lag_buckets(metrics_text: str) -> Dict[float, float]:
    """Cumulative lag-histogram counts keyed by bucket upper bound."""
    buckets: Dict[float, float] = {}
    for sample in _samples(metrics_text, LAG_HISTOGRAM_METRIC):
        if sample.name.endswith('_bucket'):
            upper_bound = float(sample.labels['le'])
            buckets[upper_bound] = buckets.get(upper_bound, 0.0) + sample.value
    return buckets


def parse_lag_max(metrics_text: str) -> float:
    """Peak lag across processes, from the per-pid gauge."""
    return max((sample.value
                for sample in _samples(metrics_text, LAG_MAX_GAUGE_METRIC)),
               default=0.0)


def parse_cpu_total(metrics_text: str) -> float:
    """Total CPU seconds burned across every process the server reports."""
    return sum(
        sample.value for sample in _samples(metrics_text, CPU_TOTAL_METRIC))


def bucket_deltas(before: Mapping[float, float],
                  after: Mapping[float, float]) -> Dict[float, float]:
    """Per-bucket count increase between two scrapes.

    A bucket present only in `after` is treated as having started at zero, and
    negative deltas (a worker restarted mid-run and reset its counters) are
    clamped so they cannot mask observations from the other workers.
    """
    deltas: Dict[float, float] = {}
    for upper_bound, count in after.items():
        deltas[upper_bound] = max(0.0, count - before.get(upper_bound, 0.0))
    return deltas


def observations_above(deltas: Mapping[float, float],
                       threshold: float) -> float:
    """How many observations in `deltas` exceeded `threshold` seconds.

    Histogram buckets are cumulative, so this is the +Inf count minus the count
    at the threshold bucket. `threshold` must be one of the histogram's bucket
    boundaries, otherwise the answer would silently be computed against a
    different boundary than the caller asked about.
    """
    if not deltas:
        return 0.0
    if threshold not in deltas:
        raise ValueError(
            f'{threshold} is not a bucket boundary of the lag histogram; '
            f'available boundaries: {sorted(deltas)}')
    total = deltas[float('inf')]
    return max(0.0, total - deltas[threshold])


def summarize(trials: Sequence[TrialResult]) -> Dict[str, Dict[str, float]]:
    """Median and interquartile range of each metric across valid trials.

    Median rather than mean because a single degenerate trial should not move
    the number a comparison is made against; IQR is reported alongside so the
    spread is visible.
    """
    usable = [trial for trial in trials if trial.status is Status.OK]
    summary: Dict[str, Dict[str, float]] = {}
    for metric in SUMMARY_METRICS:
        values = [
            getattr(trial, metric)
            for trial in usable
            if getattr(trial, metric) is not None
        ]
        if not values:
            continue
        ordered = sorted(values)
        if len(ordered) >= 4:
            q1, _, q3 = statistics.quantiles(ordered, n=4, method='inclusive')
        else:
            q1, q3 = ordered[0], ordered[-1]
        summary[metric] = {
            'median': statistics.median(ordered),
            'q1': q1,
            'q3': q3,
            'iqr': q3 - q1,
            'min': ordered[0],
            'max': ordered[-1],
            'n': float(len(ordered)),
        }
    return summary


# --------------------------------------------------------------------------
# Load generation
# --------------------------------------------------------------------------


@dataclasses.dataclass
class _Samples:
    """Raw per-request observations collected during one interval."""
    latencies: List[float] = dataclasses.field(default_factory=list)
    # How far past its scheduled instant each request actually departed.
    # Client-side health only: server slowness cannot move it.
    send_lateness: List[float] = dataclasses.field(default_factory=list)
    status_counts: Dict[str, int] = dataclasses.field(default_factory=dict)
    failures: int = 0

    def record_response(self, status_code: int, latency: float) -> None:
        self.latencies.append(latency)
        key = str(status_code)
        self.status_counts[key] = self.status_counts.get(key, 0) + 1
        if not 200 <= status_code < 300:
            self.failures += 1

    def record_error(self, error: BaseException, latency: float) -> None:
        self.latencies.append(latency)
        key = f'error:{type(error).__name__}'
        self.status_counts[key] = self.status_counts.get(key, 0) + 1
        self.failures += 1


async def _issue(client: httpx.AsyncClient, spec: RequestSpec, url: str,
                 due_at: float, samples: _Samples) -> None:
    """Send one request at its scheduled instant and record its latency.

    Latency is measured from `due_at`, not from the moment the request is
    actually sent, so time spent waiting for the client to get around to the
    request counts against the tail exactly like server time does.
    """
    loop = asyncio.get_running_loop()
    delay = due_at - loop.time()
    if delay > 0:
        await asyncio.sleep(delay)
    samples.send_lateness.append(max(0.0, loop.time() - due_at))
    try:
        response = await client.request(spec.method,
                                        url,
                                        json=spec.json_body,
                                        params=spec.params)
        samples.record_response(response.status_code, loop.time() - due_at)
    except Exception as error:  # pylint: disable=broad-except
        samples.record_error(error, loop.time() - due_at)


async def _drive_load(client: httpx.AsyncClient, config: HarnessConfig,
                      duration_seconds: float) -> _Samples:
    """Run one open-loop interval and return its raw samples.

    Every request is given its own timer up front rather than being dispatched
    by a pacing loop: a pacing loop that itself falls behind would under-issue
    requests, which would look like a lower offered rate instead of the stall
    it is.
    """
    samples = _Samples()
    offsets = schedule_offsets(config.target_qps, duration_seconds)
    if not offsets:
        return samples
    loop = asyncio.get_running_loop()
    start = loop.time()
    base = config.base_url.rstrip('/')
    tasks = []
    for index, offset in enumerate(offsets):
        spec = select_spec(config.requests, index)
        tasks.append(
            asyncio.ensure_future(
                _issue(client, spec, base + spec.path, start + offset,
                       samples)))
    await asyncio.gather(*tasks)
    return samples


@dataclasses.dataclass
class _HeldStreams:
    """Streams opened for a run, and whether they are still open.

    Opening a stream is not the same as holding one: a server that closes the
    response, or a reader that dies, leaves the count of opened streams
    unchanged while the concurrency the scenario is built on quietly
    disappears. `live()` is what a trial must be judged against.
    """
    opened: int = 0
    failed: int = 0
    readers: List['asyncio.Task'] = dataclasses.field(default_factory=list)

    def live(self) -> int:
        return sum(1 for reader in self.readers if not reader.done())


@contextlib.asynccontextmanager
async def _held_streams(auth: Auth,
                        config: HarnessConfig) -> AsyncIterator[_HeldStreams]:
    """Hold `config.streams.count` responses open for the enclosed block.

    Read timeouts are disabled because a followed log that produces no output
    is the normal case, not a hang.
    """
    held = _HeldStreams()
    if config.streams is None or config.streams.count <= 0:
        yield held
        return

    spec = config.streams
    url = config.base_url.rstrip('/') + spec.path
    stack = contextlib.AsyncExitStack()
    client = httpx.AsyncClient(
        headers=auth.headers,
        cookies=auth.cookies,
        verify=config.verify_tls,
        timeout=httpx.Timeout(connect=30.0, read=None, write=30.0, pool=30.0),
        limits=httpx.Limits(max_connections=spec.count * 2,
                            max_keepalive_connections=spec.count * 2),
    )
    await stack.enter_async_context(client)
    try:
        for _ in range(spec.count):
            try:
                response = await stack.enter_async_context(
                    client.stream(spec.method,
                                  url,
                                  params=spec.params,
                                  json=spec.json_body))
                if not 200 <= response.status_code < 300:
                    held.failed += 1
                    continue
                held.readers.append(
                    asyncio.ensure_future(_consume_stream(response)))
                held.opened += 1
            except Exception:  # pylint: disable=broad-except
                held.failed += 1
        yield held
    finally:
        for reader in held.readers:
            reader.cancel()
        await asyncio.gather(*held.readers, return_exceptions=True)
        await stack.aclose()


async def _consume_stream(response: httpx.Response) -> None:
    """Drain a held-open response so the server is not blocked on the socket."""
    with contextlib.suppress(Exception):
        async for _ in response.aiter_bytes():
            pass


async def _scrape(client: httpx.AsyncClient, metrics_url: str) -> str:
    response = await client.get(metrics_url)
    response.raise_for_status()
    return response.text


# --------------------------------------------------------------------------
# Phases
# --------------------------------------------------------------------------


async def _sample_lag_peak(metrics_client: httpx.AsyncClient,
                           config: HarnessConfig, peaks: List[float]) -> None:
    """Read the peak-lag gauge until cancelled, recording every value.

    The gauge resets each 30s window, so the trial's true peak is the maximum
    over samples taken during it, not the single value left at the end.
    """
    while True:
        try:
            peaks.append(
                parse_lag_max(await _scrape(metrics_client,
                                            config.metrics_url)))
        except Exception:  # pylint: disable=broad-except
            # A scrape that fails mid-trial should not fail the trial; the
            # remaining samples still bound the peak.
            pass
        await asyncio.sleep(config.lag_gauge_sample_seconds)


async def _run_baseline(metrics_client: httpx.AsyncClient,
                        config: HarnessConfig) -> BaselineResult:
    """Measure lag with no load; a server already lagging invalidates the run.

    Gated on the histogram delta rather than the peak gauge: the gauge is a
    30s tumbling window whose boundary does not line up with the phase, so it
    can carry lag from before the harness started. The histogram delta is
    exactly the observations made during this phase.
    """
    before = parse_lag_buckets(await _scrape(metrics_client,
                                             config.metrics_url))
    started = time.monotonic()
    await asyncio.sleep(config.baseline_seconds)
    elapsed = time.monotonic() - started
    after_text = await _scrape(metrics_client, config.metrics_url)
    deltas = bucket_deltas(before, parse_lag_buckets(after_text))
    above = observations_above(deltas, config.baseline_lag_threshold_seconds)
    status = Status.OK
    reason = None
    if above > 0:
        status = Status.INVALID
        reason = (f'baseline lag already elevated: {above:.0f} tick(s) above '
                  f'{config.baseline_lag_threshold_seconds}s with no load')
    return BaselineResult(
        status=status,
        invalid_reason=reason,
        duration_seconds=elapsed,
        lag_bucket_deltas={str(k): v for k, v in sorted(deltas.items())},
        lag_observations_above_threshold=above,
        lag_max_peak_seconds=parse_lag_max(after_text),
    )


async def _run_trial(load_client: httpx.AsyncClient,
                     metrics_client: httpx.AsyncClient, config: HarnessConfig,
                     index: int, held: '_HeldStreams') -> TrialResult:
    before_text = await _scrape(metrics_client, config.metrics_url)
    before_buckets = parse_lag_buckets(before_text)
    before_cpu = parse_cpu_total(before_text)

    peaks: List[float] = []
    sampler = asyncio.ensure_future(
        _sample_lag_peak(metrics_client, config, peaks))
    started = time.monotonic()
    try:
        samples = await _drive_load(load_client, config, config.trial_seconds)
    finally:
        sampler.cancel()
        await asyncio.gather(sampler, return_exceptions=True)
    elapsed = time.monotonic() - started

    after_text = await _scrape(metrics_client, config.metrics_url)
    deltas = bucket_deltas(before_buckets, parse_lag_buckets(after_text))
    cpu_delta = max(0.0, parse_cpu_total(after_text) - before_cpu)

    offered = len(schedule_offsets(config.target_qps, config.trial_seconds))
    completed = len(samples.latencies)
    achieved_qps = completed / elapsed if elapsed > 0 else 0.0
    lateness_p99 = (percentile(samples.send_lateness, 99)
                    if samples.send_lateness else None)
    lateness_max = max(samples.send_lateness) if samples.send_lateness else None
    # Read liveness after the load, so a stream that died mid-trial counts
    # against the trial it actually affected.
    streams_live = held.live()
    # Against the configured count, not the number that opened: if every
    # stream failed to open, `opened` is 0 and a check against it would let a
    # run with no concurrency at all call itself valid.
    streams_expected = config.streams.count if config.streams else 0
    status, reason = classify_trial(completed,
                                    lateness_max,
                                    config.max_send_lateness_seconds,
                                    streams_expected=streams_expected,
                                    streams_live=streams_live)

    latencies = samples.latencies
    return TrialResult(
        index=index,
        status=status,
        invalid_reason=reason,
        duration_seconds=elapsed,
        offered_requests=offered,
        completed_requests=completed,
        offered_qps=config.target_qps,
        achieved_qps=achieved_qps,
        status_counts=dict(samples.status_counts),
        failure_count=samples.failures,
        send_lateness_p99=lateness_p99,
        send_lateness_max=lateness_max,
        streams_live=streams_live,
        latency_p50=percentile(latencies, 50) if latencies else None,
        latency_p90=percentile(latencies, 90) if latencies else None,
        latency_p99=percentile(latencies, 99) if latencies else None,
        latency_p999=percentile(latencies, 99.9) if latencies else None,
        latency_max=max(latencies) if latencies else None,
        latency_histogram=latency_histogram(latencies),
        slow_request_rate=(rate_above(latencies,
                                      config.slow_request_threshold_seconds)
                           if latencies else None),
        lag_bucket_deltas={str(k): v for k, v in sorted(deltas.items())},
        lag_observations_above_threshold=observations_above(
            deltas, config.lag_threshold_seconds),
        lag_max_peak_seconds=max(peaks + [parse_lag_max(after_text)]),
        lag_gauge_samples=len(peaks),
        cpu_seconds_delta=cpu_delta,
        cpu_seconds_per_request=cpu_delta / completed if completed else None,
    )


async def run_async(config: HarnessConfig, auth: Auth) -> RunResult:
    """Run baseline, warmup and trials, and return the artifact."""
    limits = httpx.Limits(max_connections=config.max_connections,
                          max_keepalive_connections=config.max_connections)
    created_at = datetime.datetime.now(
        datetime.timezone.utc).isoformat(timespec='seconds')
    config_dict = dataclasses.asdict(config)

    async with httpx.AsyncClient(verify=config.verify_tls,
                                 timeout=30.0) as metrics_client:
        baseline = await _run_baseline(metrics_client, config)
        if baseline.status is Status.INVALID:
            return RunResult(artifact_version=ARTIFACT_VERSION,
                             name=config.name,
                             created_at=created_at,
                             status=Status.INVALID,
                             invalid_reason=baseline.invalid_reason,
                             config=config_dict,
                             baseline=dataclasses.asdict(baseline),
                             trials=[],
                             summary={},
                             streams_opened=0,
                             streams_failed=0,
                             streams_live_at_end=0)

        async with _held_streams(auth, config) as held:
            async with httpx.AsyncClient(
                    headers=auth.headers,
                    cookies=auth.cookies,
                    verify=config.verify_tls,
                    limits=limits,
                    timeout=config.request_timeout_seconds) as load_client:
                if config.warmup_seconds > 0:
                    await _drive_load(load_client, config,
                                      config.warmup_seconds)
                trials = []
                for index in range(config.num_trials):
                    if index > 0 and config.inter_trial_seconds > 0:
                        await asyncio.sleep(config.inter_trial_seconds)
                    trials.append(await _run_trial(load_client, metrics_client,
                                                   config, index, held))
                streams_live_at_end = held.live()

    usable = [trial for trial in trials if trial.status is Status.OK]
    status = Status.OK if usable else Status.INVALID
    reason = None if usable else 'no trial produced usable numbers'
    return RunResult(
        artifact_version=ARTIFACT_VERSION,
        name=config.name,
        created_at=created_at,
        status=status,
        invalid_reason=reason,
        config=config_dict,
        baseline=dataclasses.asdict(baseline),
        trials=[dataclasses.asdict(trial) for trial in trials],
        summary=summarize(trials),
        streams_opened=held.opened,
        streams_failed=held.failed,
        streams_live_at_end=streams_live_at_end,
    )


def run(config: HarnessConfig, auth: Auth) -> RunResult:
    """Synchronous entry point, for callers that are not already async."""
    return asyncio.run(run_async(config, auth))


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------

# Config fields that define the load shape. Two artifacts whose configs differ
# on any of these measured different workloads, so their numbers must not be
# read as an A/B result.
CONFIG_COMPARE_KEYS = (
    'requests',
    'streams',
    'target_qps',
    'trial_seconds',
    'num_trials',
    'warmup_seconds',
    'lag_threshold_seconds',
    # Changes what slow_request_rate counts.
    'slow_request_threshold_seconds',
    # Both reshape the latency distribution rather than the server's
    # behaviour: a smaller pool queues requests in the client, and a shorter
    # timeout censors the tail into errors.
    'max_connections',
    'request_timeout_seconds',
    # Decides which trials are excluded as INVALID, and therefore which ones
    # the summaries are taken over.
    'max_send_lateness_seconds',
)


def config_mismatches(baseline: Mapping[str, Any],
                      candidate: Mapping[str, Any]) -> List[str]:
    """Load-shape config keys on which two artifacts disagree."""
    base_config = baseline.get('config', {})
    cand_config = candidate.get('config', {})
    return [
        key for key in CONFIG_COMPARE_KEYS
        if base_config.get(key) != cand_config.get(key)
    ]


@dataclasses.dataclass
class MetricComparison:
    metric: str
    baseline_median: float
    candidate_median: float
    delta: float
    pct_change: Optional[float]
    noise_delta: Optional[float]
    verdict: str


def compare(
        baseline: Mapping[str, Any],
        candidate: Mapping[str, Any],
        noise_floor: Optional[Mapping[str,
                                      Any]] = None) -> List[MetricComparison]:
    """Compare two run artifacts, metric by metric.

    `noise_floor` is a second run of the *baseline* build. The delta between
    the two baseline runs is the smallest difference this setup can resolve, so
    a baseline-vs-candidate delta no larger than it is reported as noise rather
    than as a change.
    """
    base_summary = baseline.get('summary', {})
    cand_summary = candidate.get('summary', {})
    noise_summary = noise_floor.get('summary', {}) if noise_floor else {}

    comparisons = []
    for metric in SUMMARY_METRICS:
        if metric not in base_summary or metric not in cand_summary:
            continue
        base_median = base_summary[metric]['median']
        cand_median = cand_summary[metric]['median']
        delta = cand_median - base_median
        pct = (delta / base_median * 100.0) if base_median else None

        noise_delta = None
        if metric in noise_summary:
            noise_delta = abs(noise_summary[metric]['median'] - base_median)

        if noise_delta is not None and abs(delta) <= noise_delta:
            verdict = 'noise'
        elif delta == 0:
            verdict = 'same'
        elif (delta > 0) == (metric in _HIGHER_IS_WORSE):
            verdict = 'worse'
        else:
            verdict = 'better'
        comparisons.append(
            MetricComparison(metric=metric,
                             baseline_median=base_median,
                             candidate_median=cand_median,
                             delta=delta,
                             pct_change=pct,
                             noise_delta=noise_delta,
                             verdict=verdict))
    return comparisons


def pooled_histogram(artifact: Mapping[str, Any]) -> Dict[str, int]:
    """Sum every valid trial's latency histogram in a run.

    Pooling across trials is what makes a slow mode legible: per trial it can
    sit on either side of a percentile boundary, but its weight over the whole
    run is a stable number.
    """
    pooled = {str(bound): 0 for bound in LATENCY_HISTOGRAM_BOUNDS}
    for trial in artifact.get('trials', []):
        if trial.get('status') != Status.OK.value:
            continue
        for bound, count in (trial.get('latency_histogram') or {}).items():
            pooled[bound] = pooled.get(bound, 0) + count
    return pooled


def _format_bound(bound: float) -> str:
    """Human bucket label: milliseconds while they stay readable."""
    if bound == float('inf'):
        return 'slower'
    if bound < 1.0:
        return f'<={bound * 1000:g}ms'
    return f'<={bound:g}s'


def decumulate(cumulative: Mapping[str, float]) -> Dict[str, float]:
    """Per-bucket counts from a cumulative (prometheus) histogram.

    The server's lag histogram counts observations *at or below* each bound,
    which is the wrong shape to draw: every bar would include the ones before
    it and the picture would only ever climb.
    """
    per_bucket: Dict[str, float] = {}
    previous = 0.0
    for bound in sorted(cumulative, key=float):
        count = cumulative[bound]
        per_bucket[bound] = max(0.0, count - previous)
        previous = count
    return per_bucket


def format_histogram_chart(histogram: Mapping[str, float],
                           title: str,
                           unit: str = 'requests',
                           width: int = 44,
                           threshold: Optional[float] = None) -> str:
    """Draw a bucketed distribution as an ASCII bar chart.

    A second mode is obvious in a picture and easy to miss in a column of
    numbers, and this renders anywhere a log does -- no artifact download and
    no image viewer. Bucket bounds come from the histogram's own keys, so the
    client-side latency buckets and the server's lag buckets both draw.

    Bars are log-scaled: the interesting mode carries ~1% of the samples while
    the healthy one carries ~99%, so on a linear scale the thing worth looking
    at is a single character wide. Counts and percentages are printed
    alongside, since a log bar cannot be read quantitatively.
    """
    total = sum(histogram.values())
    if not total:
        return ''
    bounds = sorted((float(bound) for bound in histogram), key=float)
    largest = max(histogram.values())
    scale = math.log10(largest + 1) if largest else 0.0
    threshold = (SLOW_REQUEST_THRESHOLD_SECONDS
                 if threshold is None else threshold)
    # An empty bucket past the tail is noise; an empty one inside it is
    # information (a gap between modes), so keep zeros up to the last bucket
    # that has anything.
    filled = max(
        (i for i, bound in enumerate(bounds) if histogram.get(str(bound), 0)),
        default=0)

    lines = [f'{title} ({total:g} {unit}, log-scaled bars):']
    over = 0.0
    for index, bound in enumerate(bounds):
        count = histogram.get(str(bound), 0)
        if bound > threshold:
            over += count
        if index > filled:
            break
        bar_len = (int(round(math.log10(count + 1) / scale *
                             width)) if scale and count else 0)
        bar = '#' * bar_len
        if count and not bar_len:
            # Never render a non-empty bucket as blank: the rare buckets are
            # exactly the ones worth seeing.
            bar = '.'
        marker = ' <' if bound == threshold else '  '
        lines.append(f'  {_format_bound(bound):>9}{marker} {bar:<{width}} '
                     f'{count:>7g} {count / total * 100:6.2f}%')
    # Counting whole buckets is exact only when the threshold IS a boundary;
    # otherwise the first counted bucket also holds samples below it. Say
    # which one this is rather than print an approximation as a fact -- the
    # exact figure is slow_request_rate, taken from the raw samples.
    tally = (f'{over:g} ({over / total * 100:.2f}%)' if threshold in bounds else
             f'at most {over:g} ({over / total * 100:.2f}%), bucket-rounded')
    lines.append(f'  {"":>9}   over {threshold * 1000:g}ms: {tally}')
    return '\n'.join(lines)


def pooled_lag_buckets(artifact: Mapping[str, Any]) -> Dict[str, float]:
    """Per-bucket event loop lag observations over a run's valid trials.

    The server reports lag cumulatively, so the summed deltas are de-cumulated
    here; the result is how many loop ticks landed in each lateness band --
    the server-side view of the same stalls the client sees as slow requests.
    """
    pooled: Dict[str, float] = {}
    for trial in artifact.get('trials', []):
        if trial.get('status') != Status.OK.value:
            continue
        for bound, count in (trial.get('lag_bucket_deltas') or {}).items():
            pooled[bound] = pooled.get(bound, 0.0) + count
    return decumulate(pooled)


def format_run_charts(artifact: Mapping[str, Any],
                      prefix: str = '') -> List[str]:
    """Both views of one run: what the client saw, and what the loop did.

    The two answer different questions -- client latency is what a user feels,
    loop lag is the server-side cause -- and a slow mode that appears in one
    and not the other is itself the finding.
    """
    config = artifact.get('config', {})
    name = artifact.get('name', 'run')
    return [
        format_histogram_chart(
            pooled_histogram(artifact),
            title=f'{prefix}Client-observed latency for {name}',
            threshold=config.get('slow_request_threshold_seconds')),
        format_histogram_chart(
            pooled_lag_buckets(artifact),
            title=f'{prefix}Server event loop lag for {name}',
            unit='loop ticks',
            threshold=config.get('lag_threshold_seconds')),
    ]


def format_histograms(baseline: Mapping[str, Any],
                      candidate: Mapping[str, Any]) -> str:
    """Render both runs' pooled latency distributions side by side."""
    base_hist = pooled_histogram(baseline)
    cand_hist = pooled_histogram(candidate)
    base_total = sum(base_hist.values())
    cand_total = sum(cand_hist.values())
    if not base_total or not cand_total:
        return ''

    rows = [('latency <=', 'baseline', '%', 'candidate', '%')]
    for bound in LATENCY_HISTOGRAM_BOUNDS:
        key = str(bound)
        base_count = base_hist.get(key, 0)
        cand_count = cand_hist.get(key, 0)
        if not base_count and not cand_count:
            continue
        rows.append((
            'inf' if bound == float('inf') else f'{bound:g}s',
            str(base_count),
            f'{base_count / base_total * 100:.2f}%',
            str(cand_count),
            f'{cand_count / cand_total * 100:.2f}%',
        ))
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    lines = ['', 'Pooled latency distribution (all valid trials):']
    for position, row in enumerate(rows):
        lines.append('  '.join(
            value.rjust(widths[i]) for i, value in enumerate(row)).rstrip())
        if position == 0:
            lines.append('  '.join('-' * width for width in widths))
    return '\n'.join(lines)


def format_comparison(comparisons: Sequence[MetricComparison]) -> str:
    """Render a comparison as a fixed-width table."""
    header = ('metric', 'baseline', 'candidate', 'delta', 'pct', 'a/a',
              'verdict')
    rows = [header]
    for item in comparisons:
        rows.append((
            item.metric,
            f'{item.baseline_median:.6g}',
            f'{item.candidate_median:.6g}',
            f'{item.delta:+.6g}',
            '-' if item.pct_change is None else f'{item.pct_change:+.1f}%',
            '-' if item.noise_delta is None else f'{item.noise_delta:.6g}',
            item.verdict,
        ))
    widths = [max(len(row[i]) for row in rows) for i in range(len(header))]
    lines = []
    for position, row in enumerate(rows):
        lines.append('  '.join(
            value.ljust(widths[i]) for i, value in enumerate(row)).rstrip())
        if position == 0:
            lines.append('  '.join('-' * width for width in widths))
    return '\n'.join(lines)


def _load_artifact(path: str) -> Dict[str, Any]:
    with open(path, 'r', encoding='utf-8') as artifact:
        data = json.load(artifact)
    version = data.get('artifact_version')
    if version != ARTIFACT_VERSION:
        raise ValueError(f'{path}: artifact version {version} is not '
                         f'{ARTIFACT_VERSION}')
    return data


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='command', required=True)
    compare_parser = subparsers.add_parser(
        'compare', help='compare two run artifacts metric by metric')
    compare_parser.add_argument('baseline', help='artifact from the base build')
    compare_parser.add_argument('candidate',
                                help='artifact from the build under test')
    compare_parser.add_argument(
        '--noise-floor',
        help='a second artifact from the base build; deltas no larger than '
        'the baseline-to-noise-floor delta are reported as noise')
    show_parser = subparsers.add_parser(
        'show', help='draw one run artifact latency distribution')
    show_parser.add_argument('artifact', help='a run artifact')
    args = parser.parse_args(argv)

    if args.command == 'show':
        artifact = _load_artifact(args.artifact)
        print(f'{artifact.get("name")} ({artifact.get("created_at")}), '
              f'status={artifact.get("status")}')
        charts = [chart for chart in format_run_charts(artifact) if chart]
        print('\n\n'.join(charts) if charts else 'No samples in this artifact.')
        return 0

    baseline = _load_artifact(args.baseline)
    candidate = _load_artifact(args.candidate)
    noise_floor = _load_artifact(args.noise_floor) if args.noise_floor else None

    for name, artifact in (('baseline', baseline), ('candidate', candidate)):
        if artifact.get('status') != Status.OK.value:
            print(f'WARNING: {name} run is {artifact.get("status")}: '
                  f'{artifact.get("invalid_reason")}')
    for name, artifact in (('candidate', candidate), ('noise floor',
                                                      noise_floor)):
        if artifact is None:
            continue
        mismatched = config_mismatches(baseline, artifact)
        if mismatched:
            print(f'WARNING: baseline and {name} ran different workloads '
                  f'(config differs on: {", ".join(mismatched)}); the '
                  'comparison below is not an A/B result')

    comparisons = compare(baseline, candidate, noise_floor)
    if not comparisons:
        print('No metrics in common between the two artifacts.')
        return 1
    print(format_comparison(comparisons))
    histograms = format_histograms(baseline, candidate)
    if histograms:
        print(histograms)
    for name, artifact in (('baseline', baseline), ('candidate', candidate)):
        for chart in format_run_charts(artifact, prefix=f'{name}: '):
            if chart:
                print()
                print(chart)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
