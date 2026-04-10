#!/usr/bin/env python3
"""Benchmark controller — launches worker VMs and aggregates results.

Launches N worker VMs via `sky launch` (using your local/default SkyPilot,
NOT the target under test), runs the benchmark on each, collects JSON
results, and prints an aggregated report.

Usage:
    python benchmark_ctl.py \
        --workers 4 \
        --threads-per-worker 4 \
        --repeats 2 \
        --workload workloads/basic.sh \
        --target-endpoint http://EKS_LB_URL \
        --cloud aws

    # Reuse existing workers (skip launch):
    python benchmark_ctl.py \
        --reuse \
        --workers 4 \
        --threads-per-worker 4 \
        --target-endpoint http://EKS_LB_URL
"""

import argparse
from collections import defaultdict
from concurrent.futures import as_completed
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional

WORKER_PREFIX = 'bench-worker'
REMOTE_BENCH_DIR = '~/sky_workdir'
# Files to upload to workers
LOAD_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))


def _run(cmd: str,
         check: bool = True,
         capture: bool = False,
         timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    """Run a shell command."""
    print(f'  $ {cmd}', flush=True)
    return subprocess.run(
        cmd,
        shell=True,
        check=check,
        text=True,
        capture_output=capture,
        timeout=timeout,
    )


def _worker_name(worker_id: int) -> str:
    return f'{WORKER_PREFIX}-{worker_id}'


# ── Phase 1: Launch workers ──────────────────────────────────────────


def launch_workers(n: int, cloud: str, cpus: int, image: Optional[str],
                   auth_credentials: Optional[str]) -> List[str]:
    """Launch N worker VMs in parallel."""
    print(f'\n=== Launching {n} worker VMs on {cloud} ===', flush=True)

    setup_lines = [
        'pip install "skypilot-nightly[aws,kubernetes]" > /dev/null 2>&1',
    ]
    setup = ' && '.join(setup_lines)

    def _launch_one(wid: int) -> str:
        name = _worker_name(wid)
        cmd = (f'sky launch -y -c {name} --infra {cloud} '
               f'--cpus {cpus}+ --memory 16+ '
               f'--workdir {LOAD_TESTS_DIR} '
               f"'{setup}'")
        if image:
            cmd += f' --image {image}'
        _run(cmd, timeout=600)
        return name

    names = []
    with ThreadPoolExecutor(max_workers=min(n, 8)) as pool:
        futs = {pool.submit(_launch_one, i): i for i in range(n)}
        for fut in as_completed(futs):
            wid = futs[fut]
            try:
                name = fut.result()
                names.append(name)
                print(f'  Worker {wid} ({name}) ready', flush=True)
            except Exception as e:
                print(f'  Worker {wid} FAILED to launch: {e}',
                      file=sys.stderr,
                      flush=True)

    if len(names) < n:
        print(f'WARNING: only {len(names)}/{n} workers launched',
              file=sys.stderr,
              flush=True)
    return sorted(names)


def setup_workers(names: List[str],
                  target_endpoint: str,
                  auth_credentials: Optional[str],
                  reupload: bool = False) -> None:
    """Upload benchmark files and configure workers to point at the target."""
    print(f'\n=== Setting up {len(names)} workers ===', flush=True)

    def _setup_one(name: str) -> None:
        if reupload:
            # Re-upload benchmark code via sky exec with a temp task YAML
            _upload_via_exec(name)
        # Ensure sky CLI is installed on the worker
        _run(
            f"sky exec -c {name} "
            f"'pip install \"skypilot-nightly[aws,kubernetes]\" "
            f"> /dev/null 2>&1'",
            timeout=300)
        # Login to target API server
        login_cmd = f'sky api login -e {target_endpoint}'
        if auth_credentials:
            login_cmd += f' --credentials {auth_credentials}'
        _run(f"sky exec -c {name} '{login_cmd}'", timeout=60)

    with ThreadPoolExecutor(max_workers=min(len(names), 8)) as pool:
        futs = {pool.submit(_setup_one, n): n for n in names}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                fut.result()
                print(f'  {name} configured', flush=True)
            except Exception as e:
                print(f'  {name} setup FAILED: {e}',
                      file=sys.stderr,
                      flush=True)


def _upload_via_exec(name: str) -> None:
    """Upload benchmark files to a worker via sky exec + file_mounts."""
    yaml_content = (f'file_mounts:\n'
                    f'  {REMOTE_BENCH_DIR}:\n'
                    f'    - {LOAD_TESTS_DIR}\n'
                    f'\n'
                    f'run: echo "benchmark files uploaded"\n')
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                     delete=False) as f:
        f.write(yaml_content)
        yaml_path = f.name
    try:
        _run(f'sky exec -c {name} {yaml_path}', timeout=300)
    finally:
        os.unlink(yaml_path)


# ── Phase 2: Run benchmark ───────────────────────────────────────────


def run_benchmark(names: List[str], threads: int, repeats: int, workload: str,
                  cloud: str, timeout: int) -> None:
    """Run the benchmark on all workers in parallel via sky exec."""
    print(
        f'\n=== Running benchmark ({len(names)} workers × '
        f'{threads} threads × {repeats} repeats) ===',
        flush=True)

    def _run_one(wid: int, name: str) -> None:
        cmd = (f'cd {REMOTE_BENCH_DIR} && '
               f'python benchmark_worker.py '
               f'-t {threads} -r {repeats} '
               f'-s {workload} '
               f'-c {cloud} '
               f'-w {wid} '
               f'--timeout {timeout} '
               f'-o ~/benchmark_results')
        _run(f"sky exec -c {name} '{cmd}'",
             timeout=timeout * threads * repeats + 300,
             check=False)

    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        futs = {pool.submit(_run_one, i, n): n for i, n in enumerate(names)}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                fut.result()
                print(f'  {name} finished', flush=True)
            except Exception as e:
                print(f'  {name} error: {e}', file=sys.stderr, flush=True)


# ── Phase 3: Collect results ─────────────────────────────────────────


def collect_results(names: List[str], output_dir: str) -> List[Dict[str, Any]]:
    """Download JSON results from all workers via sky exec."""
    print(f'\n=== Collecting results ===', flush=True)
    os.makedirs(output_dir, exist_ok=True)
    all_results: List[Dict[str, Any]] = []

    for i, name in enumerate(names):
        local_dir = os.path.join(output_dir, f'worker_{i}')
        os.makedirs(local_dir, exist_ok=True)
        try:
            # Use markers to reliably extract JSON from sky exec output.
            marker = f'BENCH_RESULT_{i}'
            cat_cmd = (f"sky exec -c {name} "
                       f"'echo {marker}_START && "
                       f"cat ~/benchmark_results/results_w{i}.json && "
                       f"echo {marker}_END'")
            result = _run(cat_cmd, timeout=60, capture=True, check=False)
            if result.returncode != 0:
                print(f'  {name}: failed to read results', flush=True)
                continue

            # Search both stdout and stderr — sky exec may stream to
            # either depending on the backend (SSH vs gRPC).
            raw_output = (result.stdout or '') + (result.stderr or '')

            # Strip ANSI escape codes and any "(prefix, pid=...) " from
            # sky exec output, then extract JSON between markers.
            ansi_re = re.compile(r'\x1b\[[0-9;]*m')
            prefix_re = re.compile(r'^\([^)]*\)\s?')
            lines = raw_output.splitlines()
            collecting = False
            json_lines = []
            for line in lines:
                stripped = ansi_re.sub('', line)
                stripped = prefix_re.sub('', stripped)
                if f'{marker}_START' in stripped:
                    collecting = True
                    continue
                if f'{marker}_END' in stripped:
                    break
                if collecting:
                    json_lines.append(stripped)

            if not json_lines:
                print(f'  {name}: no results found between markers', flush=True)
                # Dump first/last lines for debugging
                if lines:
                    print(
                        f'    (output has {len(lines)} lines, '
                        f'first: {lines[0][:120]!r})',
                        flush=True)
                continue

            json_str = '\n'.join(json_lines)
            data = json.loads(json_str)
            local_path = os.path.join(local_dir, f'results_w{i}.json')
            with open(local_path, 'w') as fh:
                json.dump(data, fh, indent=2)
            if isinstance(data, list):
                all_results.extend(data)
            print(f'  {name}: loaded {len(data)} results', flush=True)
        except Exception as e:
            print(f'  {name}: failed to collect: {e}',
                  file=sys.stderr,
                  flush=True)

    # Save merged results
    merged_path = os.path.join(output_dir, 'all_results.json')
    with open(merged_path, 'w') as f:
        json.dump(all_results, f, indent=2)
    print(f'  Merged {len(all_results)} results → {merged_path}', flush=True)
    return all_results


# ── Phase 4: Report ──────────────────────────────────────────────────


def print_report(results: List[Dict[str, Any]], target: str, n_workers: int,
                 threads: int, repeats: int) -> None:
    """Print aggregated benchmark report."""
    op_data: Dict[str, List[Dict]] = defaultdict(list)
    for r in results:
        for op in r.get('operations', []):
            op_data[op['name']].append(op)

    total = len(results)
    ok = sum(1 for r in results if r['success'])
    total_dur = max((r.get('duration_s', 0) for r in results), default=0)

    print(f'\n{"="*72}')
    print(f'BENCHMARK REPORT')
    print(f'{"="*72}')
    print(f'Target:             {target}')
    print(f'Workers:            {n_workers}')
    print(f'Threads/worker:     {threads}')
    print(f'Repeats:            {repeats}')
    print(f'Total concurrency:  {n_workers * threads}')
    print(f'Total runs:         {total} ({ok} ok, {total - ok} failed)')
    print(f'{"─"*72}')

    if op_data:
        print(f'{"Operation":<25} {"N":>4} {"OK%":>6} '
              f'{"P50":>8} {"P95":>8} {"P99":>8} {"Max":>8}')
        print(f'{"─"*72}')
        for name in sorted(op_data):
            ops = op_data[name]
            n = len(ops)
            ok_pct = sum(1 for o in ops if o['exit_code'] == 0) / n * 100
            durs = sorted(o['duration_s'] for o in ops)
            p50 = durs[int(n * 0.50)] if n else 0
            p95 = durs[min(int(n * 0.95), n - 1)] if n else 0
            p99 = durs[min(int(n * 0.99), n - 1)] if n else 0
            mx = durs[-1] if durs else 0
            print(f'{name:<25} {n:>4} {ok_pct:>5.0f}% '
                  f'{p50:>7.1f}s {p95:>7.1f}s {p99:>7.1f}s {mx:>7.1f}s')

    # Error summary
    errors = [r for r in results if not r['success']]
    if errors:
        print(f'\n{"─"*72}')
        print(f'ERRORS ({len(errors)}):')
        for r in errors[:10]:
            err = r.get('error', f'exit_code={r.get("exit_code")}')
            print(f'  worker={r.get("worker_id")} '
                  f'thread={r.get("thread_id")} '
                  f'repeat={r.get("repeat_id")}: {err}')
        if len(errors) > 10:
            print(f'  ... and {len(errors) - 10} more')
    print()


# ── Phase 5: Cleanup ─────────────────────────────────────────────────


def cleanup_workers(names: List[str]) -> None:
    """Tear down worker VMs."""
    print(f'\n=== Cleaning up {len(names)} workers ===', flush=True)
    for name in names:
        _run(f'sky down {name} -y', check=False, timeout=120)


# ── Main ─────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description='Distributed benchmark controller for SkyPilot API server',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--workers',
                        type=int,
                        default=2,
                        help='Number of worker VMs (default: 2)')
    parser.add_argument('--threads-per-worker',
                        type=int,
                        default=4,
                        help='Threads per worker (default: 4)')
    parser.add_argument('--repeats',
                        type=int,
                        default=1,
                        help='Repeats per thread (default: 1)')
    parser.add_argument(
        '--workload',
        type=str,
        default='workloads/basic.sh',
        help='Workload script path (default: workloads/basic.sh)')
    parser.add_argument('--target-endpoint',
                        type=str,
                        required=True,
                        help='API server endpoint URL to benchmark')
    parser.add_argument('--auth-credentials',
                        type=str,
                        default=None,
                        help='Auth credentials for target (user:pass)')
    parser.add_argument('--cloud',
                        type=str,
                        default='aws',
                        help='Cloud for workload clusters (default: aws)')
    parser.add_argument('--worker-cloud',
                        type=str,
                        default='aws',
                        help='Cloud for worker VMs (default: aws)')
    parser.add_argument('--worker-cpus',
                        type=int,
                        default=8,
                        help='CPUs per worker VM (default: 8)')
    parser.add_argument('--worker-image',
                        type=str,
                        default=None,
                        help='Custom image for worker VMs')
    parser.add_argument('--timeout',
                        type=int,
                        default=3600,
                        help='Per-workload timeout in seconds (default: 3600)')
    parser.add_argument('--output-dir',
                        type=str,
                        default='bench-results',
                        help='Output directory (default: bench-results)')
    parser.add_argument('--reuse',
                        action='store_true',
                        help='Reuse existing worker VMs (skip launch)')
    parser.add_argument('--no-cleanup',
                        action='store_true',
                        help='Do not tear down workers after benchmark')
    parser.add_argument('--collect-only',
                        action='store_true',
                        help='Only collect results from existing workers')

    args = parser.parse_args()
    names = [_worker_name(i) for i in range(args.workers)]

    try:
        if args.collect_only:
            results = collect_results(names, args.output_dir)
            print_report(results, args.target_endpoint, args.workers,
                         args.threads_per_worker, args.repeats)
            return

        if not args.reuse:
            launch_workers(args.workers, args.worker_cloud, args.worker_cpus,
                           args.worker_image, args.auth_credentials)

        setup_workers(names,
                      args.target_endpoint,
                      args.auth_credentials,
                      reupload=args.reuse)

        run_benchmark(names, args.threads_per_worker, args.repeats,
                      args.workload, args.cloud, args.timeout)

        results = collect_results(names, args.output_dir)
        print_report(results, args.target_endpoint, args.workers,
                     args.threads_per_worker, args.repeats)

    except KeyboardInterrupt:
        print('\nInterrupted by user', flush=True)
    finally:
        if not args.no_cleanup and not args.reuse:
            cleanup_workers(names)


if __name__ == '__main__':
    main()
