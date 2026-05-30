"""Shell workload generator.

Wraps the existing bash-workload behaviour (threads × repeats running a
workload script) behind the GeneratorBase interface so it can run
concurrently with Python generators inside the same worker process.

Per-operation timing comes from the script's ##BENCH_START/END markers;
each thread+repeat run emits a single JSONL record containing the full
list of parsed operations, matching the legacy per-worker output format.
"""
from __future__ import annotations

import concurrent.futures
import os
import random
import re  # noqa: E402 — used by _parse_bench_markers
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional
import uuid

from .base import GeneratorBase
from .base import summarize_durations

_START_RE = re.compile(r'^##BENCH_START\s+(\S+)\s+([\d.]+)')
_END_RE = re.compile(r'^##BENCH_END\s+(\S+)\s+(\d+)\s+([\d.]+)')


def _parse_bench_markers(log_path: str) -> List[Dict[str, Any]]:
    starts: Dict[str, float] = {}
    ops: List[Dict[str, Any]] = []
    try:
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                m = _START_RE.match(line)
                if m:
                    starts[m.group(1)] = float(m.group(2))
                    continue
                m = _END_RE.match(line)
                if m:
                    name = m.group(1)
                    rc = int(m.group(2))
                    end_ts = float(m.group(3))
                    start_ts = starts.pop(name, end_ts)
                    ops.append({
                        'name': name,
                        'duration_s': round(end_ts - start_ts, 3),
                        'exit_code': rc,
                        'start_ts': start_ts,
                        'end_ts': end_ts,
                    })
    except FileNotFoundError:
        pass
    return ops


def _run_workload(script: str, env: Dict[str, str], log_path: str,
                  timeout: int) -> Dict[str, Any]:
    start_time = time.time()
    exit_code = -1
    error_msg: Optional[str] = None
    try:
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
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    try:
                        os.killpg(proc.pid, sig)
                    except OSError:
                        pass
                    if sig is signal.SIGTERM:
                        time.sleep(3)
                proc.wait(timeout=10)
                exit_code = -1
                error_msg = f'timeout after {timeout}s'
    except Exception as e:  # noqa: BLE001
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


class ShellGenerator(GeneratorBase):

    def __init__(self, spec, ctx):
        super().__init__(spec, ctx)
        base_dir = os.path.dirname(os.path.abspath(os.path.join(__file__,
                                                                '..')))
        self.script = os.path.join(base_dir, spec.workload)
        if not os.path.exists(self.script):
            # fallbacks: absolute, cwd-relative
            alt = os.path.abspath(spec.workload)
            if os.path.exists(alt):
                self.script = alt
            else:
                raise FileNotFoundError(
                    f'Workload script not found: {spec.workload}')
        self._pool: Optional[concurrent.futures.ThreadPoolExecutor] = None
        self._futures: List[concurrent.futures.Future] = []
        self._done = threading.Event()

    # ── public API ───────────────────────────────────────────────

    def start(self) -> None:
        n = self.spec.threads_per_worker
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=n)
        self._futures = [
            self._pool.submit(self._run_thread, tid) for tid in range(n)
        ]
        threading.Thread(target=self._await_done, daemon=True).start()

    def _await_done(self) -> None:
        for fut in concurrent.futures.as_completed(self._futures):
            try:
                fut.result()
            except Exception as e:  # noqa: BLE001
                print(f'[gen {self.name}] thread crashed: {e}',
                      file=sys.stderr,
                      flush=True)
        self._done.set()

    def wait(self, timeout: Optional[float] = None) -> None:
        self._done.wait(timeout)

    def stop(self) -> None:
        self.request_stop()
        if self._pool:
            # Shell threads are driven by the script running to completion.
            # We don't forcefully kill here — the run timeout in _run_workload
            # bounds each run. We just wait for outstanding work.
            self._pool.shutdown(wait=True)

    def summarize(self) -> Dict[str, Any]:
        rows = self.records()
        total = len(rows)
        ok = sum(1 for r in rows if r.get('success'))
        # Per-op percentiles across all threads/repeats.
        per_op: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            for op in row.get('operations', []):
                per_op.setdefault(op['name'], []).append(op)
        op_stats = {
            name: {
                'n': len(ops),
                'ok_pct': round(
                    sum(1 for o in ops if o['exit_code'] == 0) / len(ops) *
                    100, 1) if ops else 0,
                **summarize_durations(ops),
            } for name, ops in per_op.items()
        }
        return {
            'total_runs': total,
            'ok_runs': ok,
            'failed_runs': total - ok,
            'per_op': op_stats,
        }

    # ── internals ────────────────────────────────────────────────

    def _make_env(self, thread_id: int, repeat_id: int,
                  unique_id: str) -> Dict[str, str]:
        phases = list(self.spec.phases)
        random.shuffle(phases)
        env = os.environ.copy()
        env.update({
            'BENCHMARK_UNIQUE_ID': unique_id,
            'BENCHMARK_CLOUD': self.ctx.target_cloud,
            'BENCHMARK_THREAD_ID': str(thread_id),
            'BENCHMARK_REPEAT_ID': str(repeat_id),
            'BENCHMARK_WORKER_ID': str(self.ctx.worker_id),
            'BENCHMARK_PHASE_ORDER': ','.join(phases),
        })
        return env

    def _run_thread(self, thread_id: int) -> None:
        for repeat_id in range(self.spec.repeats):
            if self.stopped:
                return
            uid = (f'w{self.ctx.worker_id}-t{thread_id}-r{repeat_id}-'
                   f'{uuid.uuid4().hex[:6]}')
            log_path = os.path.join(
                self.ctx.output_dir, f'{self.name}_w{self.ctx.worker_id}'
                f'_t{thread_id}_r{repeat_id}.log')
            env = self._make_env(thread_id, repeat_id, uid)
            phase_order = env.get('BENCHMARK_PHASE_ORDER', '')
            print(
                f'[gen {self.name}] worker {self.ctx.worker_id} '
                f'thread {thread_id} repeat {repeat_id} starting '
                f'(id={uid}, phases={phase_order})',
                flush=True)
            result = _run_workload(self.script, env, log_path,
                                   self.spec.timeout_per_run_s)
            result.update({
                'worker_id': self.ctx.worker_id,
                'thread_id': thread_id,
                'repeat_id': repeat_id,
                'unique_id': uid,
                'workload': os.path.basename(self.script),
            })
            self._emit(result)
            status = 'OK' if result['success'] else 'FAIL'
            print(
                f'[gen {self.name}] worker {self.ctx.worker_id} '
                f'thread {thread_id} repeat {repeat_id} {status} '
                f'({result["duration_s"]:.1f}s, '
                f'{len(result["operations"])} ops)',
                flush=True)
