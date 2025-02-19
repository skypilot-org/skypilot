"""
Send requests to the API server to test its load handling capabilities.

example usage:
- python tests/load_tests/test_load_on_server.py -n 100 -r launch
"""

import argparse
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


def test_launch_requests(num_requests, is_async=True):
    cmd = 'sky launch --cloud=aws --cpus=2 -y'
    if is_async:
        cmd += ' --async'
    run_concurrent_requests(num_requests, cmd)


def test_status_requests(num_requests):
    run_concurrent_requests(num_requests, 'sky status')


def test_logs_requests(num_requests):
    cluster_name = 'test'
    job_id = 1
    setup_cmd = f'sky launch -c {cluster_name} --cloud=aws --cpus=2 -y'
    run_single_request(0, setup_cmd)
    cmd = f'sky logs {cluster_name} {job_id}'
    run_concurrent_requests(num_requests, cmd)
    cleanup_cmd = f'sky down -y {cluster_name}'
    run_single_request(0, cleanup_cmd)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-n',
                        type=int,
                        default=1,
                        help='Number of requests to make')
    parser.add_argument('-r',
                        type=str,
                        choices=['launch', 'status', 'logs'],
                        help='Sky request name')
    args = parser.parse_args()

    print("Starting concurrent sky requests...")
    start_time = time.time()
    if args.r == 'launch':
        test_launch_requests(args.n, is_async=True)
    elif args.r == 'status':
        test_status_requests(args.n)
    elif args.r == 'logs':
        test_logs_requests(args.n)
    end_time = time.time()
    print(f"All requests completed in {end_time - start_time:.2f} seconds")
