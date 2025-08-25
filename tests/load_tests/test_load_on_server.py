"""
Send requests to the API server to test its load handling capabilities.

example usage:
- python tests/load_tests/test_load_on_server.py -n 100 -r launch
"""

import argparse
from collections import defaultdict
import os
import subprocess
import threading
import time
from typing import Dict, List

import numpy as np

import sky
from sky import jobs as managed_jobs
from sky import serve as serve_lib
from sky.client import sdk

results_lock = threading.Lock()
request_latencies: Dict[str, List[float]] = defaultdict(list)


def run_single_request(idx, cmd):
    """Run a single request to the API server and record its latency."""
    try:
        print(f"Request {idx} submitted")
        begin = time.time()

        subprocess.run(cmd,
                       shell=True,
                       check=True,
                       capture_output=True,
                       text=True)

        duration = time.time() - begin
        with results_lock:
            request_latencies[cmd].append(duration)

    except subprocess.CalledProcessError as e:
        print(f"Request {idx} failed: {e}")


def calculate_statistics(latencies: List[float]) -> Dict[str, float]:
    """Calculate statistics for a list of latencies."""
    if not latencies:
        return {
            'count': 0,
            'total': 0,
            'min': 0,
            'max': 0,
            'avg': 0,
            'p95': 0,
            'p99': 0
        }

    return {
        'count': len(latencies),
        'total': sum(latencies),
        'avg': np.mean(latencies),
        'min': np.min(latencies),
        'max': np.max(latencies),
        'p95': np.percentile(latencies, 95),
        'p99': np.percentile(latencies, 99)
    }


def print_latency_statistics():
    """Print statistics for all recorded latencies."""
    print("\nLatency Statistics:")
    print("-" * 100)  # Increased width to accommodate more columns
    print(
        f"{'Kind':<20} {'Count':<8} {'Total(s)':<10} {'Avg(s)':<10} {'Min(s)':<10} {'Max(s)':<10} {'P95(s)':<10} {'P99(s)':<10}"
    )
    print("-" * 100)

    for kind, latencies in request_latencies.items():
        stats = calculate_statistics(latencies)
        print(
            f"{kind:<20} {stats['count']:<8d} {stats['total']:<10.2f} "
            f"{stats['avg']:<10.2f} {stats['min']:<10.2f} {stats['max']:<10.2f} "
            f"{stats['p95']:<10.2f} {stats['p99']:<10.2f}")


def run_concurrent_requests(num_requests, cmd):
    threads = []

    # Create and start threads
    for i in range(num_requests):
        thread = threading.Thread(target=run_single_request, args=(i + 1, cmd))
        threads.append(thread)
        thread.start()

    # Wait for all threads to complete
    for thread in threads:
        thread.join()


def run_concurrent_api_requests(num_requests, fn, kind, include_idx=False):
    threads = []
    for i in range(num_requests):
        thread = threading.Thread(target=run_single_api_request,
                                  args=(i + 1, fn, kind, include_idx))
        threads.append(thread)
        thread.start()
    for thread in threads:
        thread.join()


def run_single_api_request(idx, fn, kind, include_idx=False):
    print(f"API Request {idx} submitted")
    begin = time.time()
    if include_idx:
        fn(idx)
    else:
        fn()
    duration = time.time() - begin
    with results_lock:
        request_latencies[kind].append(duration)


def test_launch_requests(num_requests, cloud, is_async=True):
    print(f"Testing {num_requests} launch requests")
    cmd = f'sky launch --cloud={cloud} --cpus=2 -y'
    if is_async:
        cmd += ' --async'
    run_concurrent_requests(num_requests, cmd)


def test_status_requests(num_requests):
    print(f"Testing {num_requests} status requests")
    run_concurrent_requests(num_requests, 'sky status')


def test_logs_requests(num_requests, cloud):
    print(f"Testing {num_requests} logs requests")
    cluster_name = 'test'
    job_id = 1
    setup_cmd = f'sky launch -c {cluster_name} --cloud={cloud} --cpus=2 -y'
    run_single_request(0, setup_cmd)
    cmd = f'sky logs {cluster_name} {job_id}'
    run_concurrent_requests(num_requests, cmd)
    cleanup_cmd = f'sky down -y {cluster_name}'
    run_single_request(0, cleanup_cmd)


def test_jobs_requests(num_requests, cloud, is_async=True):
    print(f"Testing {num_requests} jobs launch requests")
    cmd = f'sky jobs launch --cloud={cloud} --cpus=2 "echo hello && sleep 120" -y'
    if is_async:
        cmd += ' --async'
    print(f"Running {cmd}, concurrency={num_requests}")
    run_concurrent_requests(num_requests, cmd)


def test_serve_requests(num_requests, cloud):
    print(f"Testing {num_requests} serve status requests")
    svc_name = 'test-serve'
    # Use serve.yaml from the same directory
    serve_yaml_path = os.path.join(os.path.dirname(__file__), 'serve.yaml')
    setup_cmd = f'sky serve up -n {svc_name} --cloud={cloud} {serve_yaml_path} -y'
    run_single_request(0, setup_cmd)

    cmd = f'sky serve status {svc_name}'
    run_concurrent_requests(num_requests, cmd)

    cleanup_cmd = f'sky serve down {svc_name} -y'
    run_single_request(0, cleanup_cmd)


def test_status_api(num_requests):
    print(f"Testing {num_requests} status API requests")

    def status():
        request_id = sdk.status()
        sdk.stream_and_get(request_id)

    run_concurrent_api_requests(num_requests, status, 'API /status')


# Naive simlutaion of API requests made by `sky status` command. We can't
# directly test with CLIs as the client can be congested by 100 parallel module
# loading, which is not critical as multiple users will call the CLI from
# different machines.
# TODO(aylei): once we fixed the module loading issue for our CLI, we should
# revert this to actual CLI calls.
def test_cli_status_in_api(num_requests):
    print(f"Testing {num_requests} CLI status in API requests")

    def cli_status():
        job_request_id = managed_jobs.queue(refresh=False, skip_finished=True)
        serve_request_id = serve_lib.status(service_names=None)
        status_request_id = sdk.status()
        try:
            sdk.stream_and_get(job_request_id)
        except Exception:
            pass
        try:
            sdk.stream_and_get(serve_request_id)
        except Exception:
            pass
        sdk.stream_and_get(status_request_id)

    run_concurrent_api_requests(num_requests, cli_status,
                                'API calls of sky status')


def test_tail_logs_api(num_requests, cloud):
    print(f"Testing {num_requests} tail logs API requests")
    cluster_name = 'test'
    job_id = 1
    # Launch a job and wait the first tail success
    setup_cmd = f'sky launch -c {cluster_name} --cloud={cloud} --cpus=2 -y && sky logs {cluster_name} {job_id}'
    run_single_request(0, setup_cmd)

    def tail_logs():
        sdk.tail_logs(cluster_name, job_id, follow=True)

    run_concurrent_api_requests(num_requests, tail_logs, 'API /tail_logs')

    cleanup_cmd = f'sky down -y {cluster_name}'
    run_single_request(0, cleanup_cmd)


def test_validate_api(num_requests):
    print(f"Testing {num_requests} validate API requests")

    def validate():
        with sky.Dag() as dag:
            sky.Task(name='echo', run='echo hello')
        sdk.validate(dag)

    run_concurrent_api_requests(num_requests, validate, 'API /validate')


def test_api_status(num_requests):
    print(f"Testing {num_requests} API status requests")
    run_concurrent_api_requests(num_requests, sdk.api_status, 'API /status')


def test_launch_and_logs_api(num_requests):
    print(f"Testing {num_requests} launch and logs API requests")

    def launch_and_logs(idx):
        name = f'echo{idx}'
        with sky.Dag() as dag:
            dag.name = name
            sky.Task(name=name,
                     run='for i in {1..10000}; do echo $i; sleep 1; done')
        sdk.stream_and_get(sdk.launch(dag, cluster_name=name))
        sdk.tail_logs(name, 1, follow=True)

    run_concurrent_api_requests(num_requests,
                                launch_and_logs,
                                'API /launch_and_logs',
                                include_idx=True)


all_requests = ['launch', 'status', 'logs', 'jobs', 'serve']
all_apis = [
    'status', 'cli_status', 'tail_logs', 'validate', 'api_status',
    'launch_and_logs'
]

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-n',
                        type=int,
                        default=1,
                        help='Number of requests to make')
    parser.add_argument(
        '-r',
        '--requests',
        type=str,
        nargs='+',
        choices=all_requests + ['all'],
        default=[],
        help='List of SkyPilot requests to test (e.g., launch status logs)')
    # Test worker memory consumption when operates multiple clouds.
    parser.add_argument('-c',
                        '--clouds',
                        type=str,
                        nargs='+',
                        default=['aws'],
                        help='List of clouds to test')
    parser.add_argument('--apis',
                        type=str,
                        nargs='+',
                        choices=all_apis + ['all'],
                        default=[],
                        help='List of APIs to test')
    parser.add_argument(
        '--run-async',
        action='store_true',
        help='Whether to run requests asynchronously (if possible)')

    args = parser.parse_args()

    if 'all' in args.requests:
        args.requests = all_requests

    if 'all' in args.apis:
        args.apis = all_apis

    print("Starting concurrent sky requests...")
    start_time = time.time()

    # Create threads for each test type
    test_threads = []
    for cloud in args.clouds:
        print(f"\nTesting cloud: {cloud}")
        for request_type in args.requests:
            thread = None
            if request_type == 'launch':
                thread = threading.Thread(target=test_launch_requests,
                                          args=(args.n, cloud, args.run_async))
            elif request_type == 'status':
                thread = threading.Thread(target=test_status_requests,
                                          args=(args.n,))
            elif request_type == 'logs':
                thread = threading.Thread(target=test_logs_requests,
                                          args=(args.n, cloud))
            elif request_type == 'jobs':
                thread = threading.Thread(target=test_jobs_requests,
                                          args=(args.n, cloud, args.run_async))
            elif request_type == 'serve':
                thread = threading.Thread(target=test_serve_requests,
                                          args=(args.n, cloud))

            if thread:
                test_threads.append(thread)
                thread.start()

        for api in args.apis:
            thread = None
            if api == 'status':
                thread = threading.Thread(target=test_status_api,
                                          args=(args.n,))
            elif api == 'cli_status':
                thread = threading.Thread(target=test_cli_status_in_api,
                                          args=(args.n,))
            elif api == 'tail_logs':
                thread = threading.Thread(target=test_tail_logs_api,
                                          args=(args.n, cloud))
            elif api == 'validate':
                thread = threading.Thread(target=test_validate_api,
                                          args=(args.n,))
            elif api == 'api_status':
                thread = threading.Thread(target=test_api_status,
                                          args=(args.n,))
            elif api == 'launch_and_logs':
                thread = threading.Thread(target=test_launch_and_logs_api,
                                          args=(args.n,))
            if thread:
                test_threads.append(thread)
                thread.start()

    # Wait for all test threads to complete
    for thread in test_threads:
        thread.join()

    end_time = time.time()
    total_time = end_time - start_time
    print(f"\nAll requests completed in {total_time:.2f} seconds")

    # Print latency statistics
    print_latency_statistics()
