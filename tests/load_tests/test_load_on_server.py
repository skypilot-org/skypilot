"""
Send requests to the API server to test its load handling capabilities.

example usage:
- python tests/load_tests/test_load_on_server.py -n 100 -r launch
"""

import argparse
import os
import subprocess
import threading
import time


def run_single_request(idx, cmd):
    """Run a single request to the API server."""
    try:
        print(f"Request {idx} submitted")
        subprocess.run(cmd,
                       shell=True,
                       check=True,
                       capture_output=True,
                       text=True)
        print(f"Request {idx} completed successfully")
    except subprocess.CalledProcessError as e:
        print(f"Request {idx} failed: {e}")


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
    cmd = f'sky jobs launch --cloud={cloud} --cpus=2 "echo hello" -y'
    if is_async:
        cmd += ' --async'
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
        choices=['launch', 'status', 'logs', 'jobs', 'serve', 'all'],
        help='List of SkyPilot requests to test (e.g., launch status logs)')
    # Test worker memory consumption when operates multiple clouds.
    parser.add_argument('-c',
                        '--clouds',
                        type=str,
                        nargs='+',
                        default=['aws'],
                        help='List of clouds to test')

    args = parser.parse_args()

    if 'all' in args.requests:
        args.requests = ['launch', 'status', 'logs', 'jobs', 'serve']

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
                                          args=(args.n, cloud, True))
            elif request_type == 'status':
                thread = threading.Thread(target=test_status_requests,
                                          args=(args.n,))
            elif request_type == 'logs':
                thread = threading.Thread(target=test_logs_requests,
                                          args=(args.n, cloud))
            elif request_type == 'jobs':
                thread = threading.Thread(target=test_jobs_requests,
                                          args=(args.n, cloud))
            elif request_type == 'serve':
                thread = threading.Thread(target=test_serve_requests,
                                          args=(args.n, cloud))

            if thread:
                test_threads.append(thread)
                thread.start()

    # Wait for all test threads to complete
    for thread in test_threads:
        thread.join()

    end_time = time.time()
    print(f"All requests completed in {end_time - start_time:.2f} seconds")
