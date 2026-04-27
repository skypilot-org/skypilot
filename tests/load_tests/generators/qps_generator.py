"""Open-loop QPS generator using the Sky Python SDK.

Fires randomly weighted short requests at a target rate. The dispatcher
thread does NOT wait for responses — it submits the SDK call (which
returns a RequestId immediately) and hands off to a collector pool that
calls sky.get(rid). This is what gives the generator its open-loop
property: a slow response can't stall future dispatches.

If the in-flight cap is hit, the dispatcher records a "throttled" event
and skips the tick rather than blocking.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import random
import threading
import time
from typing import Any, Dict, List, Optional

from .base import GeneratorBase
from .base import summarize_durations


class QpsGenerator(GeneratorBase):

    def __init__(self, spec, ctx):
        super().__init__(spec, ctx)
        # Split global QPS evenly across workers.
        self.per_worker_qps = max(spec.target_qps / ctx.num_workers, 1e-6)
        self.interval_s = 1.0 / self.per_worker_qps
        self.max_inflight = spec.max_inflight_per_worker
        self._inflight = 0
        self._inflight_lock = threading.Lock()
        self._dispatcher: Optional[threading.Thread] = None
        self._collector_pool: Optional[ThreadPoolExecutor] = None
        # Bounded executor sized to the inflight cap.
        self._weights = [op.weight for op in spec.operations]
        self._victims = list(ctx.victim_clusters)
        self._victim_idx = 0
        self._victim_lock = threading.Lock()
        # Stats counters.
        self._throttled = 0
        self._dispatched = 0
        self._t0: Optional[float] = None
        self._t_end: Optional[float] = None

    # ── public API ───────────────────────────────────────────────

    def start(self) -> None:
        self._collector_pool = ThreadPoolExecutor(
            max_workers=max(8, self.max_inflight))
        self._dispatcher = threading.Thread(target=self._dispatch_loop,
                                            daemon=True,
                                            name=f'qps-{self.name}')
        self._dispatcher.start()

    def stop(self) -> None:
        self.request_stop()
        if self._dispatcher:
            self._dispatcher.join(timeout=5)
        if self._collector_pool:
            # Cancel pending futures and don't block on in-flight requests —
            # under heavy load sky_sdk.get() calls can hang indefinitely.
            self._collector_pool.shutdown(wait=False, cancel_futures=True)

    def summarize(self) -> Dict[str, Any]:
        rows = self.records()
        ok_rows = [
            r for r in rows if r.get('event') == 'response' and r.get('success')
        ]
        err_rows = [
            r for r in rows
            if r.get('event') == 'response' and not r.get('success')
        ]
        throttled_rows = [r for r in rows if r.get('event') == 'throttled']
        elapsed = (self._t_end or time.time()) - (self._t0 or time.time())
        achieved = (len(ok_rows) +
                    len(err_rows)) / elapsed if elapsed > 0 else 0
        per_op: Dict[str, List[Dict[str, Any]]] = {}
        for r in ok_rows:
            per_op.setdefault(r['op'], []).append(r)
        per_op_stats = {
            op: {
                **summarize_durations(rows_), 'errors': sum(
                    1 for e in err_rows if e['op'] == op)
            } for op, rows_ in per_op.items()
        }
        return {
            'target_qps_global': self.spec.target_qps,
            'target_qps_per_worker': round(self.per_worker_qps, 3),
            'achieved_qps_per_worker': round(achieved, 3),
            'completed': len(ok_rows) + len(err_rows),
            'errors': len(err_rows),
            'throttled': len(throttled_rows),
            'duration_s': round(elapsed, 2),
            'per_op': per_op_stats,
        }

    # ── internals ────────────────────────────────────────────────

    def _next_victim(self) -> Optional[str]:
        if not self._victims:
            return None
        with self._victim_lock:
            v = self._victims[self._victim_idx % len(self._victims)]
            self._victim_idx += 1
        return v

    def _pick_op(self):
        return random.choices(self.spec.operations, weights=self._weights,
                              k=1)[0]

    def _inc_inflight(self) -> bool:
        with self._inflight_lock:
            if self._inflight >= self.max_inflight:
                return False
            self._inflight += 1
            return True

    def _dec_inflight(self) -> None:
        with self._inflight_lock:
            self._inflight = max(0, self._inflight - 1)

    def _dispatch_loop(self) -> None:
        # Lazy SDK import — only the QPS generator needs it.
        try:
            from sky.client import sdk as sky_sdk
            from sky.jobs.client import sdk as sky_jobs_sdk
        except Exception as e:  # noqa: BLE001
            print(f'[gen {self.name}] SDK import failed: {e}', flush=True)
            return

        self._t0 = time.time()
        next_tick = self._t0
        # Run until stop() is called by the worker. The worker drives
        # termination based on either shell-generator completion or
        # cfg.duration_s (when no shell is configured) — we don't enforce a
        # deadline here, otherwise the generator would quit early when a
        # long-running shell workload exceeds duration_s.
        while not self.stopped:
            now = time.time()
            sleep_for = next_tick - now
            if sleep_for > 0:
                # Sleep in small chunks so stop() is responsive.
                self._stop.wait(min(sleep_for, 0.5))
                continue
            next_tick += self.interval_s
            op_spec = self._pick_op()
            if not self._inc_inflight():
                self._emit({
                    'event': 'throttled',
                    'op': op_spec.op,
                    'ts': now,
                })
                self._throttled += 1
                continue
            self._dispatched += 1
            try:
                self._collector_pool.submit(self._fire_one, sky_sdk,
                                            sky_jobs_sdk, op_spec)
            except RuntimeError:
                # Pool was shut down between checks.
                self._dec_inflight()
                break
        self._t_end = time.time()

    def _fire_one(self, sky_sdk, sky_jobs_sdk, op_spec) -> None:
        op = op_spec.op
        victim: Optional[str] = None
        if op_spec.needs_victim:
            victim = self._next_victim()
        start_ts = time.time()
        success = False
        error: Optional[str] = None
        try:
            if op == 'status':
                rid = sky_sdk.status()
                sky_sdk.get(rid)
            elif op == 'jobs_queue':
                rid = sky_jobs_sdk.queue(refresh=False, skip_finished=True)
                sky_sdk.get(rid)
            elif op in ('job_status', 'cluster_queue'):
                if not victim:
                    raise RuntimeError(f'{op} requires a victim cluster')
                rid = sky_sdk.queue(cluster_name=victim, all_users=False)
                sky_sdk.get(rid)
            elif op == 'tail_logs':
                if not victim:
                    raise RuntimeError('tail_logs requires a victim cluster')
                # sky logs <cluster> --no-follow. tail_logs with
                # preload_content=False returns an iterator that yields log
                # chunks until the server closes the stream. Drain it here
                # — the wall-clock measured is the full log-fetch round
                # trip through the API server, matching CLI behaviour.
                it = sky_sdk.tail_logs(cluster_name=victim,
                                       job_id=None,
                                       follow=False,
                                       preload_content=False)
                try:
                    for chunk in it:
                        if self.stopped or chunk is None:
                            break
                finally:
                    try:
                        it.close()  # type: ignore[attr-defined]
                    except Exception:  # noqa: BLE001
                        pass
            elif op == 'exec':
                if not victim:
                    raise RuntimeError('exec requires a victim cluster')
                # sky exec <cluster> "echo hello". Build a trivial Task and
                # submit via sdk.exec → request id → sky.get(rid) to wait
                # for the server-side submission to complete.
                import sky  # noqa: WPS433 — lazy, only for exec op
                task = sky.Task(name='bench-qps-exec', run='echo hello')
                rid = sky_sdk.exec(task, cluster_name=victim)
                sky_sdk.get(rid)
            else:
                raise RuntimeError(f'unknown op: {op}')
            success = True
        except Exception as e:  # noqa: BLE001
            error = f'{type(e).__name__}: {e}'
        finally:
            end_ts = time.time()
            self._dec_inflight()
            self._emit({
                'event': 'response',
                'op': op,
                'victim': victim,
                'start_ts': start_ts,
                'end_ts': end_ts,
                'duration_s': round(end_ts - start_ts, 4),
                'success': success,
                'error': error,
            })
