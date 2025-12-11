"""Benchmark tests for SkyPilot API server."""
import subprocess
import threading
import time

import psutil
import pytest
import requests
from smoke_tests import metrics_utils
from smoke_tests import smoke_tests_utils
from smoke_tests.docker import docker_utils


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
