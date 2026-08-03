"""Benchmark tests for SkyPilot API server."""
import os
import pathlib
import shutil
import subprocess
import tempfile
import threading
import time

from load_tests import loop_lag_harness
import psutil
import pytest
import requests
from smoke_tests import metrics_utils
from smoke_tests import smoke_tests_utils
from smoke_tests.docker import docker_utils

# Identity used to bootstrap the benchmark's own service-account token. The
# server admits an external-proxy identity from this header whenever no
# built-in auth scheme is configured, which is the case for the test image.
_BENCHMARK_IDENTITY_HEADER = 'X-Auth-Request-Email'
_BENCHMARK_IDENTITY = 'loop-lag-benchmark@example.com'

# A bucket boundary of sky_apiserver_event_loop_lag_seconds. A tick this late
# means a callback held the loop for a quarter of a second, which is long
# enough to be user-visible on every concurrent request.
_LAG_THRESHOLD_SECONDS = 0.25

# Request rates for the two scenarios. The authenticated path does three DB
# operations per request on a bounded thread pool, so these are high enough to
# keep that pool busy without turning the benchmark into a DB throughput test.
_FLOOD_QPS = 100.0
_STARVATION_FLOOD_QPS = 50.0
# Concurrent `sky jobs logs -f` streams to hold open. Each one borrows a
# worker from the request thread executor for the life of the stream
# (jobs/server/server.py calls check_request_thread_executor_available before
# running the tail in a coroutine), so this is real pressure on the pool the
# flood's requests also need -- exhaustion of it is what returns 503. It does
# not touch the separate auth executor, which the token middleware uses for
# three finite DB calls and releases before any body streams.
_HELD_STREAMS = 32

# Generous by design: this bounds "the server still answers" under held-open
# streams, not the latency the server should aim for.
_STARVATION_P99_LIMIT_SECONDS = 2.0

_ARTIFACT_DIR = pathlib.Path(
    os.environ.get('SKYPILOT_BENCHMARK_ARTIFACT_DIR', '/tmp/skypilot-loop-lag'))


@pytest.mark.benchmark
@pytest.mark.remote_server
def test_api_server_memory(generic_cloud: str):
    """Benchmark the SkyPilot API server."""
    if not smoke_tests_utils.is_docker_remote_api_server():
        pytest.skip('Skipping test in shared remote api server environment as '
                    'the resource might not be dedicated to this case')
    if psutil.cpu_count() < 4:
        pytest.fail('No enough CPU on host to run the benchmark, consider '
                    'skipping the test for this environment')
    if psutil.virtual_memory().total / (1024**3) < 16:
        pytest.fail('No enough memory on host to run the benchmark, consider '
                    'skipping the test for this environment')
    metrics_server_url = smoke_tests_utils.get_metrics_server_url()
    metrics_url = f'{metrics_server_url}/metrics'
    metrics_result = {}
    container_name = docker_utils.get_container_name()
    # This is to get consistent API server resources despite the infra setup
    # Update memory and memoryswap together to avoid daemon error about swap
    subprocess.run([
        'docker', 'update', '--cpus', '4', '--memory', '16g', '--memory-swap',
        '16g', container_name
    ],
                   check=True)
    subprocess.run(['docker', 'restart', container_name], check=True)

    health_url = f'{smoke_tests_utils.get_api_server_url()}/api/health'
    for _ in range(40):
        try:
            response = requests.get(health_url, timeout=5)
            if response.ok and response.json().get('status') == 'healthy':
                break
        except Exception:
            pass
        time.sleep(2)
    else:
        raise RuntimeError('API server container not healthy after restart')

    def _collect_metrics():
        nonlocal metrics_result
        metrics_result = metrics_utils.collect_metrics(
            metrics_url,
            'sky_apiserver_process_peak_rss',
            stop_event=stop_event)

    stop_event = threading.Event()
    metrics_thread = threading.Thread(target=_collect_metrics)
    metrics_thread.start()
    parallelism = 8
    if generic_cloud == 'kubernetes':
        # Kubernetes has limited resources, lower the concurrency
        parallelism = 4
    test = smoke_tests_utils.Test(
        'test_api_server_memory',
        [
            f'python tests/load_tests/workload_benchmark.py -t {parallelism} -r 5 --detail -s workloads/basic.sh --cloud {generic_cloud}'
        ],
        teardown='sky down -y "load-test-*"; sky jobs cancel -a -y || true',
        # Long timeout for benchmark to complete
        timeout=3600,
    )
    try:
        smoke_tests_utils.run_one_test(test)
    finally:
        stop_event.set()
        metrics_thread.join()
    assert metrics_result, 'No metrics collected'
    total_peak_bytes = sum(
        max(value
            for _, value in series) if series else 0
        for series in metrics_result.values())
    total_peak_gb = total_peak_bytes / (1024**3)
    assert total_peak_gb <= 14, (
        f'API server peak memory too high: {total_peak_gb:.2f} GB (limit: 14 GB)'
    )


def _pin_server_container_and_wait_healthy() -> None:
    """Give the server container fixed resources and wait for it to come back.

    The CPU pin also fixes the uvicorn worker count: in deploy mode the server
    starts one worker per CPU it can see, and that count is read from the
    cgroup limit this sets.
    """
    container_name = docker_utils.get_container_name()
    subprocess.run([
        'docker', 'update', '--cpus', '4', '--memory', '16g', '--memory-swap',
        '16g', container_name
    ],
                   check=True)
    subprocess.run(['docker', 'restart', container_name], check=True)

    health_url = f'{smoke_tests_utils.get_api_server_url()}/api/health'
    for _ in range(40):
        try:
            response = requests.get(health_url, timeout=5)
            if response.ok and response.json().get('status') == 'healthy':
                return
        except Exception:  # pylint: disable=broad-except
            pass
        time.sleep(2)
    raise RuntimeError('API server container not healthy after restart')


def _mint_service_account_token(api_url: str) -> str:
    """Create a token the benchmark can authenticate its own load with.

    Returned once by the server and never retrievable again, so it is used
    directly rather than stored.
    """
    response = requests.post(
        f'{api_url}/users/service-account-tokens',
        headers={_BENCHMARK_IDENTITY_HEADER: _BENCHMARK_IDENTITY},
        json={
            'token_name': f'loop-lag-bench-{int(time.time())}',
            'expires_in_days': 0,
        },
        timeout=60)
    response.raise_for_status()
    token = response.json()['token']
    assert token.startswith('sky_'), token[:8]
    return token


def _launch_log_producer(generic_cloud: str) -> str:
    """Start a job that keeps printing, so there is a real log to tail.

    The held streams are `sky jobs logs -f` against this job. Tailing a real
    job is the request a user actually makes; the server's own log is served
    by a special-cased path that never touches the request executor.
    """
    job_name = f'loop-lag-log-{int(time.time())}'
    with tempfile.NamedTemporaryFile('w', suffix='.yaml',
                                     delete=False) as task_file:
        task_file.write(
            'run: |\n'
            '  while true; do echo "loop-lag benchmark $(date +%s)"; '
            'sleep 1; done\n')
        task_path = task_file.name
    try:
        subprocess.run([
            'sky', 'jobs', 'launch', '-n', job_name, '--infra', generic_cloud,
            '--cpus', '2+', '--memory', '4+', task_path, '-y', '-d'
        ],
                       check=True)
        # The tail has nothing to follow until the job is actually running,
        # and a stream that opens against a pending job ends immediately --
        # which the harness would (correctly) call an invalid trial.
        deadline = time.time() + 900
        while time.time() < deadline:
            status = subprocess.run(
                ['sky', 'jobs', 'queue'],
                check=False,
                capture_output=True,
                text=True,
            ).stdout
            if any(job_name in line and 'RUNNING' in line
                   for line in status.splitlines()):
                # Give the job a moment to write something to tail.
                time.sleep(5)
                return job_name
            time.sleep(10)
        raise RuntimeError(
            f'job {job_name} did not reach RUNNING in 15 minutes; last '
            f'queue output:\n{status}')
    finally:
        os.unlink(task_path)


def _publish(result: loop_lag_harness.RunResult, name: str) -> pathlib.Path:
    """Write the run artifact and hand it to Buildkite when running under it."""
    # TODO(kevin): move to a pipeline-level artifact_paths declaration if that
    # turns out simpler than shelling out per test.
    path = result.write(_ARTIFACT_DIR / f'{name}.json')
    if shutil.which('buildkite-agent') is not None:
        upload = subprocess.run(
            ['buildkite-agent', 'artifact', 'upload', path.name],
            cwd=str(path.parent),
            check=False)
        if upload.returncode != 0:
            print(f'buildkite-agent could not upload {path}; the file is still '
                  'on the agent')
    print(f'Event loop lag artifact for {name}: {path}')
    # Draw the distribution into the build log: the shape (one mode or two,
    # and how heavy the slow one is) is the part a reader wants, and this way
    # it is on screen without downloading the artifact.
    for chart in loop_lag_harness.format_run_charts(result.to_dict()):
        if chart:
            print(chart)
    return path


def _require_valid(result: loop_lag_harness.RunResult, name: str) -> None:
    """Stop with an unambiguous message when the run measured nothing.

    An invalid run and a regression demand opposite responses -- fix the
    environment versus revert the change -- so they must not read alike.
    """
    if result.status is loop_lag_harness.Status.INVALID:
        pytest.fail(f'{name}: INVALID measurement, not a regression: '
                    f'{result.invalid_reason}')


def _valid_trials(result: loop_lag_harness.RunResult) -> list:
    return [
        trial for trial in result.trials
        if trial['status'] == loop_lag_harness.Status.OK.value
    ]


@pytest.mark.benchmark
@pytest.mark.remote_server
def test_api_server_event_loop_lag(generic_cloud: str):
    """Assert the server's event loop stays responsive under authenticated load.

    Two scenarios, both authenticated with a service-account token so every
    request goes through the token middleware's DB lookups:

    1. A steady flood, which surfaces any blocking call left on the loop.
    2. The same flood while N `sky jobs logs -f` streams are held open
       against a real running job, so the request thread executor is under
       the pressure a user's log tails actually create while authenticated
       requests keep arriving.
    """
    if not smoke_tests_utils.is_docker_remote_api_server():
        pytest.skip('Skipping test in shared remote api server environment as '
                    'the resource might not be dedicated to this case')
    if psutil.cpu_count() < 4:
        pytest.fail('No enough CPU on host to run the benchmark, consider '
                    'skipping the test for this environment')
    if psutil.virtual_memory().total / (1024**3) < 16:
        pytest.fail('No enough memory on host to run the benchmark, consider '
                    'skipping the test for this environment')

    _pin_server_container_and_wait_healthy()

    api_url = smoke_tests_utils.get_api_server_url()
    metrics_url = f'{smoke_tests_utils.get_metrics_server_url()}/metrics'
    auth = loop_lag_harness.Auth(headers={
        'Authorization': f'Bearer {_mint_service_account_token(api_url)}'
    })

    flood = loop_lag_harness.HarnessConfig(
        name='authenticated-flood',
        base_url=api_url,
        metrics_url=metrics_url,
        # Cheapest authenticated endpoint there is: it does no cloud work and
        # touches no request queue, so lag it shows is the middleware's.
        requests=[loop_lag_harness.RequestSpec(path='/api/health')],
        target_qps=_FLOOD_QPS,
        lag_threshold_seconds=_LAG_THRESHOLD_SECONDS,
    )
    flood_result = loop_lag_harness.run(flood, auth)
    _publish(flood_result, 'authenticated-flood')
    _require_valid(flood_result, 'authenticated-flood')

    for trial in _valid_trials(flood_result):
        # Without this the lag assertions below pass on a server that answered
        # every request 401 or 503 -- and a 503 is exactly what an exhausted
        # auth executor returns, so the failure mode this benchmark exists for
        # would read as a clean run.
        assert trial['failure_count'] == 0, (
            f'trial {trial["index"]}: {trial["failure_count"]} of '
            f'{trial["completed_requests"]} authenticated requests failed; '
            f'statuses: {trial["status_counts"]}')
        assert set(trial['status_counts']) == {
            '200'
        }, (f'trial {trial["index"]}: expected only 200s, got '
            f'{trial["status_counts"]}')
        assert trial['lag_observations_above_threshold'] == 0, (
            f'trial {trial["index"]}: {trial["lag_observations_above_threshold"]:.0f} '
            f'event loop tick(s) landed above {_LAG_THRESHOLD_SECONDS}s under '
            f'{_FLOOD_QPS} req/s of authenticated load; bucket deltas: '
            f'{trial["lag_bucket_deltas"]}')
        assert trial['lag_max_peak_seconds'] < _LAG_THRESHOLD_SECONDS, (
            f'trial {trial["index"]}: peak event loop lag '
            f'{trial["lag_max_peak_seconds"]:.3f}s reached '
            f'{_LAG_THRESHOLD_SECONDS}s')

    job_name = _launch_log_producer(generic_cloud)
    try:
        starvation = loop_lag_harness.HarnessConfig(
            name='executor-starvation',
            base_url=api_url,
            metrics_url=metrics_url,
            requests=[loop_lag_harness.RequestSpec(path='/api/health')],
            target_qps=_STARVATION_FLOOD_QPS,
            lag_threshold_seconds=_LAG_THRESHOLD_SECONDS,
            streams=loop_lag_harness.StreamSpec(
                path='/jobs/logs',
                count=_HELD_STREAMS,
                method='POST',
                # What `sky jobs logs -f` sends. Unlike a server-log tail,
                # this takes the real request path: the handler borrows a
                # worker from the request thread executor and holds it for
                # the life of the stream, which is the pool the auth lookups
                # on the flood requests compete for.
                json_body={
                    'name': job_name,
                    'follow': True,
                    'tail': 10,
                }),
        )
        starvation_result = loop_lag_harness.run(starvation, auth)
    finally:
        subprocess.run(['sky', 'jobs', 'cancel', '-n', job_name, '-y'],
                       check=False,
                       capture_output=True)
    _publish(starvation_result, 'executor-starvation')
    _require_valid(starvation_result, 'executor-starvation')

    assert starvation_result.streams_opened == _HELD_STREAMS, (
        f'only {starvation_result.streams_opened}/{_HELD_STREAMS} streams '
        f'opened ({starvation_result.streams_failed} failed); the scenario '
        'did not put the server under the load it claims to')
    # Opening is not holding: a stream the server closed leaves the opened
    # count untouched while the concurrency disappears. Per-trial validity
    # covers this too, but assert it here so the run-level number is checked
    # even if every trial were somehow excluded.
    assert starvation_result.streams_live_at_end == _HELD_STREAMS, (
        f'{_HELD_STREAMS - starvation_result.streams_live_at_end} of '
        f'{_HELD_STREAMS} streams closed before the run ended; the server was '
        'not held at the concurrency this scenario measures. They ended '
        f'with: {starvation_result.stream_end_reasons}; the server had sent '
        f'them: {starvation_result.stream_end_samples}')

    for trial in _valid_trials(starvation_result):
        assert set(trial['status_counts']) == {
            '200'
        }, (f'trial {trial["index"]}: expected only 200s while streams were '
            f'held open, got {trial["status_counts"]}')
        assert trial['failure_count'] == 0, (
            f'trial {trial["index"]}: {trial["failure_count"]} of '
            f'{trial["completed_requests"]} requests failed while '
            f'{_HELD_STREAMS} streams were held open; statuses: '
            f'{trial["status_counts"]}')
        assert trial['latency_p99'] < _STARVATION_P99_LIMIT_SECONDS, (
            f'trial {trial["index"]}: p99 {trial["latency_p99"]:.3f}s exceeds '
            f'{_STARVATION_P99_LIMIT_SECONDS}s with {_HELD_STREAMS} streams '
            'held open')
        assert trial['lag_max_peak_seconds'] < _LAG_THRESHOLD_SECONDS, (
            f'trial {trial["index"]}: peak event loop lag '
            f'{trial["lag_max_peak_seconds"]:.3f}s reached '
            f'{_LAG_THRESHOLD_SECONDS}s with streams held open')
