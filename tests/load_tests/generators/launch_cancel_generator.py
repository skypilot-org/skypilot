"""Launch+cancel generator — reproduces cancellation-poisoned executor workers.

Mechanism under test: cancelling an in-flight
`sky.launch` SIGTERMs the executor worker running it; the resulting
KeyboardInterrupt lands at an arbitrary bytecode in the worker's main
thread. On Python <=3.12, if it lands inside logging's module-lock windows
(`Logger.setLevel` -> `Manager._clear_cache`, no try/finally), the process
RLock `logging._lock` is orphaned owned-by-main-thread. The worker is
REUSED for later requests; the next launch it picks up fans provisioning
out to a thread pool whose threads all block forever at their first
`logging.getLogger()` -> `_acquireLock()`. Observable: launch request
RUNNING forever, provisioning log stalled right after
`create_namespaced_pod (count=N)`, k8s Services exist but zero pods.

This generator emulates the field pattern that surfaced the bug (a daemon
cancelling jobs right after submission) while keeping every launch's fate,
timing, and worker pid on record:

  * Submits paced async `sky.launch` requests of a tiny N-node k8s task.
  * Cancel-fated launches (most): `sky api cancel` after a per-launch
    interval cycled from `cancel_intervals_s` (+ jitter). Each cancel that
    lands while the request is RUNNING is one "dice roll" against the
    leak window.
  * Probe launches (every `probe_every`-th): never cancelled; expected to
    reach terminal state within `wedge_after_s`. A probe (or any tracked
    launch) still RUNNING past that deadline is flagged as a suspected
    poisoned-worker wedge, with the worker pid and every earlier
    cancelled request that ran on the same pid (the poisoning
    candidates).
  * On a suspected wedge the generator can halt all further submissions
    (`halt_on_wedge`) so the wedged process is preserved for py-spy.

The request->pid mapping comes from `sky api status` (RequestPayload.pid),
so poisoning can be correlated client-side without server access.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import random
import threading
import time
from typing import Any, Dict, List, Optional

from .base import GeneratorBase
from .base import summarize_durations

_TERMINAL = frozenset({'SUCCEEDED', 'FAILED', 'CANCELLED'})


class LaunchCancelGenerator(GeneratorBase):

    def __init__(self, spec, ctx):
        super().__init__(spec, ctx)
        self._seq = 0
        self._active: Dict[str, Dict[str, Any]] = {}  # rid -> info
        self._active_lock = threading.Lock()
        # pid -> [rid, ...] of CANCELLED-while-RUNNING requests (poison
        # candidates).
        self._cancelled_pids: Dict[int, List[str]] = {}
        self._all_pids: Dict[int, int] = {}  # pid -> times seen
        self._wedges: List[Dict[str, Any]] = []
        self._halted = threading.Event()
        self._done = threading.Event()
        self._dispatcher: Optional[threading.Thread] = None
        self._monitor: Optional[threading.Thread] = None
        self._side_pool: Optional[ThreadPoolExecutor] = None
        self._t0: Optional[float] = None
        self._dice_rolls = 0  # cancels that hit a RUNNING launch

    # ── lifecycle ────────────────────────────────────────────────

    def start(self) -> None:
        # Import the sky SDK on the caller's thread BEFORE spawning any
        # threads: two threads racing the first import of the sky package
        # graph (dispatch + monitor both importing sky.client.sdk) hit
        # partially-initialized modules in the sky.serve circular-import
        # chain (AttributeError: module 'sky.serve' has no 'UpdateMode').
        import sky  # noqa: WPS433
        from sky.client import sdk as sky_sdk
        self._sky = sky
        self._sdk = sky_sdk
        self._side_pool = ThreadPoolExecutor(
            max_workers=max(8, self.spec.max_inflight))
        self._t0 = time.time()
        self._dispatcher = threading.Thread(target=self._dispatch_loop,
                                            daemon=True,
                                            name=f'lc-dispatch-{self.name}')
        self._monitor = threading.Thread(target=self._monitor_loop,
                                         daemon=True,
                                         name=f'lc-monitor-{self.name}')
        self._dispatcher.start()
        self._monitor.start()

    def wait(self, timeout: Optional[float] = None) -> None:
        self._done.wait(timeout)

    def stop(self) -> None:
        self.request_stop()
        self._done.set()
        if self._dispatcher:
            self._dispatcher.join(timeout=5)
        if self._monitor:
            self._monitor.join(timeout=5)
        # Best-effort teardown of leftovers — but NEVER touch suspected
        # wedges: the wedged worker process is the evidence.
        if self.spec.cleanup_on_stop:
            try:
                self._cleanup_leftovers()
            except Exception as e:  # noqa: BLE001
                print(f'[gen {self.name}] cleanup error: {e}', flush=True)
        if self._side_pool:
            self._side_pool.shutdown(wait=False, cancel_futures=True)

    # ── dispatch ─────────────────────────────────────────────────

    def _dispatch_loop(self) -> None:
        try:
            self._dispatch(self._sky, self._sdk)
        finally:
            # Always release wait()ers, even on unexpected errors.
            self._drain_then_done()

    def _dispatch(self, sky, sky_sdk) -> None:
        deadline = (self._t0 +
                    self.spec.duration_s if self.spec.duration_s > 0 else None)
        intervals = list(self.spec.cancel_intervals_s)
        while not self.stopped and not self._halted.is_set():
            now = time.time()
            if deadline and now >= deadline:
                break
            if (self.spec.max_launches > 0 and
                    self._seq >= self.spec.max_launches):
                break
            if self._num_active() >= self.spec.max_inflight:
                self._stop.wait(1.0)
                continue

            seq = self._seq
            self._seq += 1
            is_probe = (self.spec.probe_every > 0 and
                        seq % self.spec.probe_every == 0)
            interval = None
            if not is_probe:
                interval = intervals[seq % len(intervals)]
                interval += random.uniform(0, self.spec.cancel_jitter_s)
            cname = (f'{self.spec.cluster_prefix}-w{self.ctx.worker_id}'
                     f'-{seq}')
            try:
                task = sky.Task(name=cname, run='echo repro; sleep 20')
                task.set_resources(
                    sky.Resources(infra=self.spec.infra,
                                  cpus=self.spec.task_cpus,
                                  memory=self.spec.task_memory))
                task.num_nodes = self.spec.num_nodes
                rid = str(sky_sdk.launch(task, cluster_name=cname))
            except Exception as e:  # noqa: BLE001
                self._emit({
                    'event': 'submit_error',
                    'ts': time.time(),
                    'seq': seq,
                    'cluster': cname,
                    'error': f'{type(e).__name__}: {e}',
                })
                self._stop.wait(self.spec.launch_gap_s)
                continue

            info = {
                'rid': rid,
                'cluster': cname,
                'seq': seq,
                'fate': 'probe' if is_probe else 'cancel',
                'cancel_interval_s': interval,
                't_submit': time.time(),
                'pid': None,
                'status': 'PENDING',
                'wedge_flagged': False,
            }
            with self._active_lock:
                self._active[rid] = info
            self._emit({
                'event': 'submit',
                'ts': info['t_submit'],
                'rid': rid,
                'seq': seq,
                'cluster': cname,
                'fate': info['fate'],
                'cancel_interval_s': interval,
            })
            if not is_probe:
                self._side_pool.submit(self._cancel_after, rid, interval)
            self._stop.wait(self.spec.launch_gap_s)

    def _drain_then_done(self) -> None:
        """After dispatch ends, wait for active launches to settle."""
        drain_deadline = time.time() + self.spec.drain_timeout_s
        while not self.stopped and time.time() < drain_deadline:
            if self._halted.is_set():
                break  # wedge found: preserve state, end run now
            if self._num_active() == 0:
                break
            self._stop.wait(5)
        self._done.set()

    # ── cancel / down side actions ───────────────────────────────

    def _cancel_after(self, rid: str, delay: float) -> None:
        sky_sdk = self._sdk
        if self._stop.wait(delay):
            return
        if self._halted.is_set():
            return  # freeze the world once a wedge is suspected
        prior = None
        with self._active_lock:
            info = self._active.get(rid)
            if info:
                prior = info['status']
        t0 = time.time()
        try:
            sky_sdk.api_cancel(request_ids=[rid], silent=True)
            err = None
        except Exception as e:  # noqa: BLE001
            err = f'{type(e).__name__}: {e}'
        if err is None and prior == 'RUNNING':
            self._dice_rolls += 1
        self._emit({
            'event': 'cancel_sent',
            'ts': t0,
            'rid': rid,
            'prior_status': prior,
            'latency_s': round(time.time() - t0, 3),
            'error': err,
        })

    def _down_later(self, cluster: str, delay: float) -> None:
        sky_sdk = self._sdk
        if self._stop.wait(delay):
            return
        try:
            sky_sdk.down(cluster)
            self._emit({
                'event': 'down_sent',
                'ts': time.time(),
                'cluster': cluster,
                'error': None
            })
        except Exception as e:  # noqa: BLE001
            self._emit({
                'event': 'down_sent',
                'ts': time.time(),
                'cluster': cluster,
                'error': f'{type(e).__name__}: {e}'
            })

    # ── monitor ──────────────────────────────────────────────────

    def _num_active(self) -> int:
        with self._active_lock:
            return len(self._active)

    def _monitor_loop(self) -> None:
        sky_sdk = self._sdk
        while not self.stopped and not self._done.is_set():
            self._stop.wait(self.spec.poll_s)
            with self._active_lock:
                rids = list(self._active.keys())
            if not rids:
                continue
            try:
                payloads = sky_sdk.api_status(request_ids=rids)
            except Exception as e:  # noqa: BLE001
                self._emit({
                    'event': 'poll_error',
                    'ts': time.time(),
                    'error': f'{type(e).__name__}: {e}'
                })
                continue
            now = time.time()
            for p in payloads:
                try:
                    self._handle_poll(p, now)
                except Exception as e:  # noqa: BLE001
                    self._emit({
                        'event': 'poll_error',
                        'ts': now,
                        'error': f'{type(e).__name__}: {e}'
                    })

    def _handle_poll(self, payload, now: float) -> None:
        rid = payload.request_id
        status = str(payload.status).rsplit('.', 1)[-1].upper()
        pid = getattr(payload, 'pid', None)
        with self._active_lock:
            info = self._active.get(rid)
            if info is None:
                return
            info['status'] = status
            if pid and info['pid'] is None:
                info['pid'] = pid
                self._all_pids[pid] = self._all_pids.get(pid, 0) + 1
                self._emit({
                    'event':
                        'pid_assigned',
                    'ts':
                        now,
                    'rid':
                        rid,
                    'pid':
                        pid,
                    'fate':
                        info['fate'],
                    'prior_cancels_on_pid':
                        list(self._cancelled_pids.get(pid, [])),
                })
            age = now - info['t_submit']
            if status in _TERMINAL:
                del self._active[rid]
                if status == 'CANCELLED' and info['pid'] is not None:
                    self._cancelled_pids.setdefault(info['pid'], []).append(rid)
                self._emit({
                    'event': 'terminal',
                    'ts': now,
                    'rid': rid,
                    'cluster': info['cluster'],
                    'fate': info['fate'],
                    'status': status,
                    'pid': info['pid'],
                    'age_s': round(age, 1),
                })
                if self.spec.down_after and not self._halted.is_set():
                    self._side_pool.submit(self._down_later, info['cluster'],
                                           self.spec.down_delay_s)
                return
            # Wedge detection: any tracked launch RUNNING way past the
            # normal provision time.
            if (status == 'RUNNING' and age > self.spec.wedge_after_s and
                    not info['wedge_flagged']):
                info['wedge_flagged'] = True
                wedge = {
                    'event':
                        'WEDGE_SUSPECT',
                    'ts':
                        now,
                    'rid':
                        rid,
                    'cluster':
                        info['cluster'],
                    'fate':
                        info['fate'],
                    'pid':
                        info['pid'],
                    'age_s':
                        round(age, 1),
                    'prior_cancels_on_pid':
                        list(self._cancelled_pids.get(info['pid'], []))
                        if info['pid'] else [],
                }
                self._wedges.append(wedge)
                self._emit(wedge)
                print(
                    f'[gen {self.name}] *** WEDGE SUSPECT: request {rid} '
                    f'(cluster {info["cluster"]}, pid {info["pid"]}) '
                    f'RUNNING for {age:.0f}s — prior cancels on this pid: '
                    f'{wedge["prior_cancels_on_pid"]} ***',
                    flush=True)
                if self.spec.halt_on_wedge:
                    self._halted.set()

    # ── cleanup / summary ────────────────────────────────────────

    def _cleanup_leftovers(self) -> None:
        if self._halted.is_set():
            # A wedge was found: freeze EVERYTHING. Extra cancels would be
            # more dice rolls and downs would disturb the evidence; clean
            # up manually after py-spy forensics.
            print(
                f'[gen {self.name}] halted on wedge — skipping cleanup '
                '(clusters + requests left as-is for forensics)',
                flush=True)
            return
        sky_sdk = self._sdk
        with self._active_lock:
            leftovers = [(rid, i)
                         for rid, i in self._active.items()
                         if not i['wedge_flagged']]
        for rid, info in leftovers:
            try:
                sky_sdk.api_cancel(request_ids=[rid], silent=True)
            except Exception:  # noqa: BLE001
                pass
            if self.spec.down_after:
                try:
                    sky_sdk.down(info['cluster'])
                except Exception:  # noqa: BLE001
                    pass

    def summarize(self) -> Dict[str, Any]:
        rows = self.records()
        submits = [r for r in rows if r.get('event') == 'submit']
        terminals = [r for r in rows if r.get('event') == 'terminal']
        cancels = [r for r in rows if r.get('event') == 'cancel_sent']
        by_status: Dict[str, int] = {}
        for r in terminals:
            by_status[r['status']] = by_status.get(r['status'], 0) + 1
        probe_ages = [
            r['age_s']
            for r in terminals
            if r['fate'] == 'probe' and r['status'] == 'SUCCEEDED'
        ]
        return {
            'submitted':
                len(submits),
            'terminal_by_status':
                by_status,
            'cancels_sent':
                len(cancels),
            'dice_rolls (cancelled while RUNNING)':
                self._dice_rolls,
            'distinct_worker_pids':
                len(self._all_pids),
            'poison_candidate_pids': {
                str(k): v for k, v in self._cancelled_pids.items()
            },
            'probe_success_age_s':
                summarize_durations([{
                    'duration_s': a
                } for a in probe_ages]),
            'wedge_suspects':
                self._wedges,
            'halted_on_wedge':
                self._halted.is_set(),
        }
