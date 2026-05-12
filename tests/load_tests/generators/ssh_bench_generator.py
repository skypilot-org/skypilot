"""SSH / sky-logs connection benchmark generator.

For each configured op (kind=ssh or kind=sky_logs) this generator spawns
`concurrency_per_worker` threads that repeatedly open + close a session
to a victim cluster and record the wall-clock duration. Useful for
benchmarking the API server's SSH proxy (Kubernetes
/kubernetes-pod-ssh-proxy, Slurm) and the logs endpoint.

Each op carries its own global budget (`total_connections`), split
evenly across workers. `total_connections=0` means unlimited and runs
until the worker's duration_s fence fires.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

from .base import GeneratorBase
from .base import summarize_durations


class _OpState:
    """Per-op shared state: budget counter, stats, lock."""

    def __init__(self, op, worker_id: int, num_workers: int):
        self.op = op
        self.lock = threading.Lock()
        self.attempts = 0
        self.succeeded = 0
        self.failed = 0
        if op.total_connections > 0:
            base = op.total_connections // num_workers
            rem = op.total_connections % num_workers
            self.budget = base + (1 if worker_id < rem else 0)
        else:
            self.budget = 0  # unlimited, bounded by duration_s

    def claim(self, stopped: bool) -> bool:
        if stopped:
            return False
        with self.lock:
            if self.budget > 0 and self.attempts >= self.budget:
                return False
            self.attempts += 1
            return True


class SshBenchGenerator(GeneratorBase):

    def __init__(self, spec, ctx):
        super().__init__(spec, ctx)
        self._threads: List[threading.Thread] = []
        self._op_states: List[_OpState] = [
            _OpState(op, ctx.worker_id, ctx.num_workers) for op in spec.ops
        ]
        # Lazily-imported SDK (only if a sky_logs op is configured).
        self._sky_sdk = None
        if not ctx.victim_clusters:
            print(
                f'[gen {self.name}] WARNING: no victim clusters; '
                'generator will be a no-op',
                flush=True)

    # ── public API ───────────────────────────────────────────────

    def start(self) -> None:
        if not self.ctx.victim_clusters:
            return
        # Pre-import SDK once per worker if needed, so each slot doesn't
        # race on first import.
        if any(op.kind == 'sky_logs' for op in self.spec.ops):
            try:
                from sky.client import sdk as sky_sdk
                self._sky_sdk = sky_sdk
            except Exception as e:  # noqa: BLE001
                print(f'[gen {self.name}] sky SDK import failed: {e}',
                      flush=True)
        for op_idx, op in enumerate(self.spec.ops):
            state = self._op_states[op_idx]
            if state.budget == 0 and op.total_connections > 0:
                continue  # this worker got zero of the budget
            for slot_id in range(op.concurrency_per_worker):
                t = threading.Thread(
                    target=self._slot_loop,
                    args=(op_idx, slot_id),
                    daemon=True,
                    name=f'sshbench-{self.name}-{op.kind}-{slot_id}')
                t.start()
                self._threads.append(t)

    def wait(self, timeout: Optional[float] = None) -> None:
        """Block until every slot thread has exited naturally.

        Only meaningful when the generator is bounded (every op has a
        finite total_connections). For unbounded generators this would
        block forever, so we no-op in that case — the worker drives
        termination via its duration_s fence + stop()."""
        if not self.is_bounded:
            return
        deadline = None if timeout is None else time.time() + timeout
        for t in self._threads:
            remaining = None if deadline is None else max(
                0.0, deadline - time.time())
            t.join(timeout=remaining)

    def stop(self) -> None:
        self.request_stop()
        for t in self._threads:
            t.join(timeout=10)

    @property
    def is_bounded(self) -> bool:
        """True iff every op has a finite total_connections > 0."""
        return bool(self.spec.ops) and all(
            op.total_connections > 0 for op in self.spec.ops)

    def summarize(self) -> Dict[str, Any]:
        rows = self.records()
        per_op: Dict[str, Dict[str, Any]] = {}
        for state in self._op_states:
            kind = state.op.kind
            ok = [
                r for r in rows
                if r.get('event') == 'connect' and r.get('kind') == kind
            ]
            err = [
                r for r in rows
                if r.get('event') == 'connect_error' and r.get('kind') == kind
            ]
            per_op[kind] = {
                'concurrency_per_worker': state.op.concurrency_per_worker,
                'total_connections_global': state.op.total_connections,
                'budget_this_worker': state.budget,
                'attempts': state.attempts,
                'succeeded': state.succeeded,
                'failed': state.failed,
                'connect_duration': summarize_durations(ok, 'duration_s'),
                'error_duration': summarize_durations(err, 'duration_s'),
            }
        return {'per_op': per_op}

    # ── slot loop ────────────────────────────────────────────────

    def _slot_loop(self, op_idx: int, slot_id: int) -> None:
        op = self.spec.ops[op_idx]
        state = self._op_states[op_idx]
        victims = self.ctx.victim_clusters
        vi = slot_id % len(victims)
        while not self.stopped:
            if not state.claim(self.stopped):
                return
            victim = victims[vi]
            vi = (vi + 1) % len(victims)
            if op.kind == 'ssh':
                self._run_ssh(state, slot_id, victim)
            elif op.kind == 'sky_logs':
                self._run_sky_logs(state, slot_id, victim)
            else:  # validated at config load time
                return

    # ── ssh ──────────────────────────────────────────────────────

    def _run_ssh(self, state: _OpState, slot_id: int, victim: str) -> None:
        ssh_bin = shutil.which('ssh') or 'ssh'
        cmd = [
            ssh_bin,
            '-o',
            'StrictHostKeyChecking=no',
            '-o',
            f'ConnectTimeout={self.spec.connect_timeout_s}',
            '-o',
            'BatchMode=yes',
            # Force a fresh TCP + auth handshake every time — disable any
            # local ControlMaster so the measurement is a new connection.
            '-o',
            'ControlMaster=no',
            '-o',
            'ControlPath=none',
            victim,
            'true',
        ]
        t0 = time.time()
        try:
            res = subprocess.run(cmd,
                                 stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.PIPE,
                                 timeout=self.spec.op_timeout_s)
            dur = time.time() - t0
            if res.returncode == 0:
                with state.lock:
                    state.succeeded += 1
                self._emit_connect('ssh', slot_id, victim, dur)
            else:
                with state.lock:
                    state.failed += 1
                err = (res.stderr.decode('utf-8', 'replace')
                       if res.stderr else '').strip()[-500:]
                self._emit_error('ssh',
                                 slot_id,
                                 victim,
                                 dur,
                                 error=err,
                                 returncode=res.returncode)
        except subprocess.TimeoutExpired as e:
            with state.lock:
                state.failed += 1
            self._emit_error('ssh',
                             slot_id,
                             victim,
                             time.time() - t0,
                             error=f'timeout after {e.timeout}s')
        except Exception as e:  # noqa: BLE001
            with state.lock:
                state.failed += 1
            self._emit_error('ssh',
                             slot_id,
                             victim,
                             time.time() - t0,
                             error=f'{type(e).__name__}: {e}')

    # ── sky logs --no-follow ─────────────────────────────────────

    def _run_sky_logs(self, state: _OpState, slot_id: int, victim: str) -> None:
        if self._sky_sdk is None:
            with state.lock:
                state.failed += 1
            self._emit_error('sky_logs',
                             slot_id,
                             victim,
                             0.0,
                             error='sky SDK unavailable')
            return
        t0 = time.time()
        deadline = t0 + self.spec.op_timeout_s
        it = None
        try:
            it = self._sky_sdk.tail_logs(cluster_name=victim,
                                         job_id=None,
                                         follow=False,
                                         preload_content=False)
            # Drain the iterator. In no-follow mode the server sends
            # accumulated log content and closes — the wall-clock time
            # to drain is the number we care about.
            for chunk in it:
                if self.stopped:
                    break
                if time.time() > deadline:
                    raise TimeoutError(
                        f'drain exceeded {self.spec.op_timeout_s}s')
                if chunk is None:
                    break
            dur = time.time() - t0
            with state.lock:
                state.succeeded += 1
            self._emit_connect('sky_logs', slot_id, victim, dur)
        except Exception as e:  # noqa: BLE001
            with state.lock:
                state.failed += 1
            self._emit_error('sky_logs',
                             slot_id,
                             victim,
                             time.time() - t0,
                             error=f'{type(e).__name__}: {e}')
        finally:
            if it is not None:
                try:
                    it.close()  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    pass

    # ── emission helpers ─────────────────────────────────────────

    def _emit_connect(self, kind: str, slot_id: int, victim: str,
                      duration_s: float) -> None:
        self._emit({
            'event': 'connect',
            'kind': kind,
            'slot_id': slot_id,
            'victim': victim,
            'ts': time.time(),
            'duration_s': round(duration_s, 4),
        })

    def _emit_error(self,
                    kind: str,
                    slot_id: int,
                    victim: str,
                    duration_s: float,
                    *,
                    error: str,
                    returncode: Optional[int] = None) -> None:
        rec: Dict[str, Any] = {
            'event': 'connect_error',
            'kind': kind,
            'slot_id': slot_id,
            'victim': victim,
            'ts': time.time(),
            'duration_s': round(duration_s, 4),
            'error': error,
        }
        if returncode is not None:
            rec['returncode'] = returncode
        self._emit(rec)
