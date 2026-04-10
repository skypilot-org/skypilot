#!/usr/bin/env python3
"""Benchmark worker — runs workload scripts and records per-operation timing.

This runs on each worker VM. The controller (benchmark_ctl.py) launches
multiple workers and collects their JSON results.

Can also be used standalone for single-machine benchmarking.

Usage:
    python benchmark_worker.py -t 4 -r 2 -s workloads/basic.sh --cloud aws
    python benchmark_worker.py -t 1 -r 1 -s workloads/light.sh --cloud kubernetes
"""

import argparse
import concurrent.futures
import json
import os
import re
import signal
import sys
import threading
import time
from typing import Any, Dict, List, Optional
import uuid


def _parse_bench_markers(log_path: str) -> List[Dict[str, Any]]:
    """Parse ##BENCH_START / ##BENCH_END markers from a log file.

    Returns a list of operation dicts:
        [{"name": "sky_launch", "duration_s": 45.2, "exit_code": 0,
          "start_ts": 1712..., "end_ts": 1712...}, ...]
    """
    starts: Dict[str, float] = {}
    operations: List[Dict[str, Any]] = []
    start_re = re.compile(r'^##BENCH_START\s+(\S+)\s+([\d.]+)')
    end_re = re.compile(r'^##BENCH_END\s+(\S+)\s+(\d+)\s+([\d.]+)')

    try:
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                m = start_re.match(line)
                if m:
                    starts[m.group(1)] = float(m.group(2))
                    continue
                m = end_re.match(line)
                if m:
                    name = m.group(1)
                    exit_code = int(m.group(2))
                    end_ts = float(m.group(3))
                    start_ts = starts.pop(name, end_ts)
                    operations.append({
                        'name': name,
                        'duration_s': round(end_ts - start_ts, 3),
                        'exit_code': exit_code,
                        'start_ts': start_ts,
                        'end_ts': end_ts,
                    })
    except FileNotFoundError:
        pass
    return operations


def _run_workload(script: str, env: Dict[str, str], log_path: str,
                  timeout: int) -> Dict[str, Any]:
    """Run a workload script, return structured result with per-op timing.

    Uses start_new_session + killpg to ensure all child processes are
    cleaned up on timeout (fixes the "never finishes" issue with
    subprocess.run when background processes inherit the pipe).
    """
    start_time = time.time()
    exit_code = -1
    error_msg: Optional[str] = None

    try:
        import subprocess
        with open(log_path, 'w') as log_f:
            proc = subprocess.Popen(
                ['bash', script],
                stdout=log_f,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
            try:
                proc.wait(timeout=timeout)
                exit_code = proc.returncode
            except subprocess.TimeoutExpired:
                # Kill entire process group
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except OSError:
                    pass
                time.sleep(3)
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
                proc.wait(timeout=10)
                exit_code = -1
                error_msg = f'timeout after {timeout}s'
    except Exception as e:
        error_msg = str(e)

    duration = round(time.time() - start_time, 3)
    operations = _parse_bench_markers(log_path)

    result: Dict[str, Any] = {
        'duration_s': duration,
        'exit_code': exit_code,
        'success': exit_code == 0,
        'log_file': log_path,
        'operations': operations,
    }
    if error_msg:
        result['error'] = error_msg
    return result


class BenchmarkWorker:

    def __init__(self, threads: int, repeats: int, script: str, cloud: str,
                 output_dir: str, worker_id: int, timeout: int):
        self.threads = threads
        self.repeats = repeats
        self.cloud = cloud
        self.output_dir = output_dir
        self.worker_id = worker_id
        self.timeout = timeout

        # Resolve script path relative to this file's directory
        base = os.path.dirname(os.path.abspath(__file__))
        self.script = os.path.join(base, script)
        if not os.path.exists(self.script):
            # Also try absolute / cwd-relative
            if os.path.exists(script):
                self.script = os.path.abspath(script)
            else:
                raise FileNotFoundError(f'Workload script not found: {script}')

        os.makedirs(output_dir, exist_ok=True)

    def _make_env(self, thread_id: int, repeat_id: int,
                  unique_id: str) -> Dict[str, str]:
        env = os.environ.copy()
        env.update({
            'BENCHMARK_UNIQUE_ID': unique_id,
            'BENCHMARK_CLOUD': self.cloud,
            'BENCHMARK_THREAD_ID': str(thread_id),
            'BENCHMARK_REPEAT_ID': str(repeat_id),
            'BENCHMARK_WORKER_ID': str(self.worker_id),
        })
        return env

    def _run_thread(self, thread_id: int) -> List[Dict[str, Any]]:
        results = []
        for repeat_id in range(self.repeats):
            uid = f'w{self.worker_id}-t{thread_id}-r{repeat_id}-{uuid.uuid4().hex[:6]}'
            log_path = os.path.join(
                self.output_dir,
                f'w{self.worker_id}_t{thread_id}_r{repeat_id}.log')
            env = self._make_env(thread_id, repeat_id, uid)

            print(
                f'[worker {self.worker_id}] thread {thread_id} '
                f'repeat {repeat_id} starting (id={uid})',
                flush=True)

            result = _run_workload(self.script, env, log_path, self.timeout)
            result.update({
                'worker_id': self.worker_id,
                'thread_id': thread_id,
                'repeat_id': repeat_id,
                'unique_id': uid,
                'workload': os.path.basename(self.script),
            })
            results.append(result)

            status = 'OK' if result['success'] else 'FAIL'
            ops = len(result['operations'])
            print(
                f'[worker {self.worker_id}] thread {thread_id} '
                f'repeat {repeat_id} {status} '
                f'({result["duration_s"]:.1f}s, {ops} ops)',
                flush=True)
        return results

    def run(self) -> List[Dict[str, Any]]:
        all_results: List[Dict[str, Any]] = []
        lock = threading.Lock()

        with concurrent.futures.ThreadPoolExecutor(
                max_workers=self.threads) as pool:
            futures = {
                pool.submit(self._run_thread, tid): tid
                for tid in range(self.threads)
            }
            for fut in concurrent.futures.as_completed(futures):
                tid = futures[fut]
                try:
                    thread_results = fut.result()
                    with lock:
                        all_results.extend(thread_results)
                except Exception as e:
                    print(
                        f'[worker {self.worker_id}] thread {tid} '
                        f'crashed: {e}',
                        file=sys.stderr,
                        flush=True)

        # Write JSON results
        results_path = os.path.join(self.output_dir,
                                    f'results_w{self.worker_id}.json')
        with open(results_path, 'w') as f:
            json.dump(all_results, f, indent=2)
        print(f'[worker {self.worker_id}] results written to {results_path}',
              flush=True)
        return all_results


def print_summary(results: List[Dict[str, Any]]) -> None:
    """Print a quick summary table for a single worker."""
    from collections import defaultdict
    op_stats: Dict[str, List] = defaultdict(list)
    for r in results:
        for op in r.get('operations', []):
            op_stats[op['name']].append(op)

    total = len(results)
    ok = sum(1 for r in results if r['success'])
    print(f'\n{"="*60}')
    print(f'Worker summary: {ok}/{total} runs succeeded')
    print(f'{"="*60}')

    if op_stats:
        print(f'{"Operation":<25} {"N":>4} {"OK%":>6} '
              f'{"P50":>7} {"P95":>7} {"Max":>7}')
        print('-' * 60)
        for name in sorted(op_stats):
            ops = op_stats[name]
            n = len(ops)
            ok_pct = sum(1 for o in ops if o['exit_code'] == 0) / n * 100
            durs = sorted(o['duration_s'] for o in ops)
            p50 = durs[int(n * 0.5)] if n else 0
            p95 = durs[min(int(n * 0.95), n - 1)] if n else 0
            mx = durs[-1] if durs else 0
            print(f'{name:<25} {n:>4} {ok_pct:>5.0f}% '
                  f'{p50:>6.1f}s {p95:>6.1f}s {mx:>6.1f}s')
    print()


def main():
    parser = argparse.ArgumentParser(
        description='Benchmark worker: run workload scripts and record timing')
    parser.add_argument('-t', '--threads', type=int, default=4)
    parser.add_argument('-r', '--repeats', type=int, default=1)
    parser.add_argument('-s',
                        '--script',
                        type=str,
                        default='workloads/basic.sh')
    parser.add_argument('-c', '--cloud', type=str, default='aws')
    parser.add_argument('-o',
                        '--output-dir',
                        type=str,
                        default='benchmark_results')
    parser.add_argument('-w', '--worker-id', type=int, default=0)
    parser.add_argument('--timeout',
                        type=int,
                        default=3600,
                        help='Per-workload timeout in seconds (default: 3600)')
    parser.add_argument('--check',
                        action='store_true',
                        help='Exit non-zero on any failure')
    args = parser.parse_args()

    worker = BenchmarkWorker(
        threads=args.threads,
        repeats=args.repeats,
        script=args.script,
        cloud=args.cloud,
        output_dir=args.output_dir,
        worker_id=args.worker_id,
        timeout=args.timeout,
    )
    results = worker.run()
    print_summary(results)

    if args.check:
        failed = sum(1 for r in results if not r['success'])
        if failed:
            print(f'{failed} runs failed', file=sys.stderr)
            sys.exit(1)


if __name__ == '__main__':
    main()
