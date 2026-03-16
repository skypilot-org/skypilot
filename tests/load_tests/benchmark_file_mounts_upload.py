"""Benchmark concurrent upload_mounts_to_api_server calls.

Usage:
    python tests/load_tests/benchmark_file_mounts_upload.py <dir> <concurrency>
"""

import argparse
import concurrent.futures
import os
import statistics
import sys
import time

import sky
from sky.client import common as client_common
from sky.server import common


def _make_dag(workdir: str) -> sky.Dag:
    """Create a minimal dag with the given workdir."""
    with sky.Dag() as dag:
        sky.Task(name='bench', workdir=workdir)
    return dag


def _run_upload(workdir: str) -> dict:
    """Run a single upload and return timing info."""
    dag = _make_dag(workdir)
    start = time.monotonic()
    try:
        common.check_server_healthy()
        _, blob_id = client_common.upload_mounts_to_api_server(dag)
        elapsed = time.monotonic() - start
        return {
            'status': 'ok',
            'elapsed': elapsed,
            'blob_id': blob_id,
        }
    except Exception as e:  # pylint: disable=broad-except
        elapsed = time.monotonic() - start
        return {
            'status': 'error',
            'elapsed': elapsed,
            'error': str(e),
        }


def main():
    parser = argparse.ArgumentParser(
        description='Benchmark concurrent file mounts uploads')
    parser.add_argument('directory', help='Directory to upload')
    parser.add_argument('concurrency',
                        type=int,
                        help='Number of concurrent uploads')
    args = parser.parse_args()

    workdir = os.path.abspath(os.path.expanduser(args.directory))
    if not os.path.isdir(workdir):
        print(f'Error: {workdir} is not a directory', file=sys.stderr)
        sys.exit(1)

    concurrency = args.concurrency
    print(f'Directory: {workdir}')
    print(f'Concurrency: {concurrency}')
    print()

    wall_start = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [
            pool.submit(_run_upload, workdir) for _ in range(concurrency)
        ]
        results = [f.result() for f in futures]
    wall_elapsed = time.monotonic() - wall_start

    # Summarize
    ok = [r for r in results if r['status'] == 'ok']
    errors = [r for r in results if r['status'] == 'error']

    print(f'--- Results ({len(results)} total) ---')
    print(f'  Success: {len(ok)}')
    print(f'  Errors:  {len(errors)}')
    print()

    if ok:
        times = [r['elapsed'] for r in ok]
        blob_ids = set(r['blob_id'] for r in ok)
        print(f'Blob IDs seen: {len(blob_ids)}')
        for bid in sorted(blob_ids, key=lambda x: str(x)):
            print(f'  {bid}')
        print()
        print('Latency (seconds):')
        print(f'  min:    {min(times):.3f}')
        print(f'  max:    {max(times):.3f}')
        print(f'  mean:   {statistics.mean(times):.3f}')
        print(f'  median: {statistics.median(times):.3f}')
        if len(times) >= 2:
            print(f'  stdev:  {statistics.stdev(times):.3f}')
        print(f'  p90:    {sorted(times)[int(len(times) * 0.9)]:.3f}')
        print(f'  p99:    {sorted(times)[int(len(times) * 0.99)]:.3f}')

    if errors:
        print()
        print('Errors:')
        for r in errors:
            print(f'  {r["error"]}')

    print()
    print(f'Wall time: {wall_elapsed:.3f}s')


if __name__ == '__main__':
    main()
