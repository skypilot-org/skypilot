"""Long-lived connection generator.

Holds N concurrent long-lived sessions to victim clusters:

  * ssh_idle:   subprocess `ssh <victim> -- tail -f /dev/null` per slot
  * logs_follow: sdk.tail_logs(victim, job_id=None, follow=True,
                 preload_content=False) consumed by a thread per slot

Records connect / first-data / disconnect events. The controller is
responsible for ensuring each victim has a long-running job that
logs_follow can latch onto (see victim heartbeat job in benchmark_ctl.py).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

from .base import GeneratorBase
from .base import summarize_durations


def _tail_file(path: Optional[str], n_lines: int) -> Optional[str]:
    """Return last n_lines of a file, or None if the file is unreadable."""
    if not path:
        return None
    try:
        with open(path, 'r', errors='replace') as f:
            lines = f.readlines()
        return ''.join(lines[-n_lines:])
    except OSError:
        return None


class _Slot:

    def __init__(self, slot_id: int, kind: str, victim: str):
        self.slot_id = slot_id
        self.kind = kind
        self.victim = victim
        self.proc: Optional[subprocess.Popen] = None
        self.iterator = None
        self.thread: Optional[threading.Thread] = None
        self.connect_ts: Optional[float] = None
        self.first_data_ts: Optional[float] = None
        self.bytes_received = 0
        self.reconnects = 0


class LongConnGenerator(GeneratorBase):

    def __init__(self, spec, ctx):
        super().__init__(spec, ctx)
        self._slots: List[_Slot] = []
        self._monitor: Optional[threading.Thread] = None
        if not ctx.victim_clusters:
            print(
                f'[gen {self.name}] WARNING: no victim clusters; '
                'generator will be a no-op',
                flush=True)
        # Build slot list deterministically: round-robin victims per protocol.
        sid = 0
        for proto in spec.protocols:
            for i in range(proto.connections_per_worker):
                victim = (ctx.victim_clusters[(i + sid) %
                                              len(ctx.victim_clusters)]
                          if ctx.victim_clusters else '')
                self._slots.append(_Slot(sid, proto.kind, victim))
                sid += 1

    # ── public API ───────────────────────────────────────────────

    def start(self) -> None:
        if not self.ctx.victim_clusters:
            return
        for slot in self._slots:
            self._open(slot)
        self._monitor = threading.Thread(target=self._monitor_loop,
                                         daemon=True,
                                         name=f'longconn-{self.name}-mon')
        self._monitor.start()

    def stop(self) -> None:
        self.request_stop()
        if self._monitor:
            self._monitor.join(timeout=5)
        for slot in self._slots:
            self._close(slot, reason='stop')

    def summarize(self) -> Dict[str, Any]:
        rows = self.records()
        connects = [r for r in rows if r.get('event') == 'connect']
        disconnects = [r for r in rows if r.get('event') == 'disconnect']
        first_data = [r for r in rows if r.get('event') == 'first_data']
        time_to_first = summarize_durations(first_data, 'duration_s')
        sessions = summarize_durations(disconnects, 'duration_s')
        return {
            'slots': len(self._slots),
            'connects': len(connects),
            'disconnects': len(disconnects),
            'reconnects': sum(s.reconnects for s in self._slots),
            'time_to_first_log_line': time_to_first,
            'session_duration': sessions,
            'live_at_summary': sum(
                1 for s in self._slots
                if (s.proc and s.proc.poll() is None) or s.iterator is not None
            ),
        }

    # ── slot management ──────────────────────────────────────────

    def _open(self, slot: _Slot) -> None:
        if slot.kind == 'ssh_idle':
            self._open_ssh(slot)
        elif slot.kind == 'logs_follow':
            self._open_logs(slot)
        else:
            print(f'[gen {self.name}] unknown kind {slot.kind!r}', flush=True)

    def _open_ssh(self, slot: _Slot) -> None:
        ssh_bin = shutil.which('ssh') or 'ssh'
        # -vv gives verbose diagnostic output (connection + auth details) so
        # we can see why ssh fails; written to a per-slot log file on the
        # worker for post-mortem inspection.
        cmd = [
            ssh_bin, '-vv', '-o', 'StrictHostKeyChecking=no', '-o',
            'ConnectTimeout=15', '-o', 'ServerAliveInterval=30', slot.victim,
            'tail', '-f', '/dev/null'
        ]
        log_path = os.path.join(
            self.ctx.output_dir,
            f'ssh_{self.name}_w{self.ctx.worker_id}_slot{slot.slot_id}.log')
        slot.stderr_log = log_path  # type: ignore[attr-defined]
        try:
            # Open the log for appending; reconnect attempts accumulate.
            # Close in parent after Popen duplicates the fd.
            log_fh = open(log_path, 'a')
            slot.proc = subprocess.Popen(cmd,
                                         stdin=subprocess.DEVNULL,
                                         stdout=log_fh,
                                         stderr=log_fh,
                                         start_new_session=True)
            log_fh.close()
            slot.connect_ts = time.time()
            self._emit({
                'event': 'connect',
                'kind': slot.kind,
                'slot_id': slot.slot_id,
                'victim': slot.victim,
                'ts': slot.connect_ts,
                'log': log_path,
            })
        except Exception as e:  # noqa: BLE001
            self._emit({
                'event': 'connect_error',
                'kind': slot.kind,
                'slot_id': slot.slot_id,
                'victim': slot.victim,
                'error': f'{type(e).__name__}: {e}',
                'ts': time.time(),
                'log': log_path,
            })

    def _open_logs(self, slot: _Slot) -> None:
        try:
            from sky.client import sdk as sky_sdk
        except Exception as e:  # noqa: BLE001
            self._emit({
                'event': 'connect_error',
                'slot_id': slot.slot_id,
                'kind': slot.kind,
                'error': f'sdk import: {e}',
                'ts': time.time()
            })
            return

        def _consume():
            try:
                slot.connect_ts = time.time()
                self._emit({
                    'event': 'connect',
                    'kind': slot.kind,
                    'slot_id': slot.slot_id,
                    'victim': slot.victim,
                    'ts': slot.connect_ts,
                })
                it = sky_sdk.tail_logs(cluster_name=slot.victim,
                                       job_id=None,
                                       follow=True,
                                       preload_content=False)
                slot.iterator = it
                for chunk in it:
                    if self.stopped:
                        break
                    if chunk is None:
                        break
                    if slot.first_data_ts is None and chunk:
                        slot.first_data_ts = time.time()
                        self._emit({
                            'event': 'first_data',
                            'kind': slot.kind,
                            'slot_id': slot.slot_id,
                            'victim': slot.victim,
                            'ts': slot.first_data_ts,
                            'duration_s': round(
                                slot.first_data_ts - slot.connect_ts, 4),
                        })
                    slot.bytes_received += len(chunk) if chunk else 0
            except Exception as e:  # noqa: BLE001
                self._emit({
                    'event': 'disconnect',
                    'kind': slot.kind,
                    'slot_id': slot.slot_id,
                    'victim': slot.victim,
                    'reason': f'error: {type(e).__name__}: {e}',
                    'ts': time.time(),
                    'duration_s': round(
                        time.time() - (slot.connect_ts or time.time()), 4),
                })
                slot.iterator = None
                return
            self._emit({
                'event': 'disconnect',
                'kind': slot.kind,
                'slot_id': slot.slot_id,
                'victim': slot.victim,
                'reason': 'iterator_end',
                'ts': time.time(),
                'duration_s': round(
                    time.time() - (slot.connect_ts or time.time()), 4),
            })
            slot.iterator = None

        slot.thread = threading.Thread(target=_consume,
                                       daemon=True,
                                       name=f'longconn-logs-{slot.slot_id}')
        slot.thread.start()

    def _close(self, slot: _Slot, reason: str) -> None:
        was_alive = False
        if slot.proc and slot.proc.poll() is None:
            was_alive = True
            try:
                slot.proc.terminate()
            except Exception:  # noqa: BLE001
                pass
            try:
                slot.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    slot.proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        if slot.iterator is not None:
            was_alive = True
            it = slot.iterator
            slot.iterator = None

            # Close in a daemon thread — the streaming HTTP response may not
            # shut down cleanly under load, and we don't want to block.
            def _close_iter():
                try:
                    it.close()  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    pass

            t = threading.Thread(target=_close_iter, daemon=True)
            t.start()
            t.join(timeout=5)
        if was_alive:
            self._emit({
                'event': 'disconnect',
                'kind': slot.kind,
                'slot_id': slot.slot_id,
                'victim': slot.victim,
                'reason': reason,
                'ts': time.time(),
                'duration_s': round(
                    time.time() - (slot.connect_ts or time.time()), 4),
            })

    def _monitor_loop(self) -> None:
        # Run until stop() is called by the worker. No internal deadline:
        # when a shell generator drives the run, it can take much longer
        # than cfg.duration_s, and we want connections held for the whole
        # time.
        interval = max(1, self.spec.health_check_interval_s)
        while not self.stopped:
            self._stop.wait(interval)
            if self.stopped:
                return
            for slot in self._slots:
                alive = ((slot.proc is not None and slot.proc.poll() is None) or
                         slot.iterator is not None)
                if alive:
                    continue
                # Slot is down — collect why (ssh stderr tail) and reconnect.
                rc = slot.proc.returncode if slot.proc else None
                tail = _tail_file(getattr(slot, 'stderr_log', None), 40)
                self._emit({
                    'event': 'health_check',
                    'kind': slot.kind,
                    'slot_id': slot.slot_id,
                    'victim': slot.victim,
                    'state': 'dead',
                    'returncode': rc,
                    'log_tail': tail,
                    'ts': time.time(),
                })
                if self.spec.reconnect_on_drop:
                    slot.reconnects += 1
                    self._open(slot)
