"""Self-quarantine for API server uvicorn workers with a stalled event loop.

In deploy mode the API server runs one uvicorn worker per CPU, and all of
them accept on a *single* listening socket bound by the parent process.
A client -- or anything in front of the server -- can only address that
socket, never an individual worker. So when one worker's event loop
stalls, there is no way to keep user requests off it: it goes on picking
up new connections in the gaps between stalls, and every request already
multiplexed onto a keep-alive connection pinned to it waits out the
stall. A problem confined to one process -- a background thread that
happens to live in that worker, one oversized response being serialized
on the loop, a hot loop in a request handler -- is served to users as
multi-second latency on an otherwise healthy server.

This module gives a worker a way to take *itself* out of rotation:

* A watchdog thread samples how far behind the worker's own event loop
  is. It deliberately runs off the loop, so it keeps observing while the
  loop is blocked.
* Once the loop has spent enough of a recent window stalled, the worker
  stops accepting on the shared socket and drains its idle keep-alive
  connections. Nothing needs to be told about this: the listening socket
  is shared, so the kernel simply hands new connections to whichever
  sibling is in ``accept()``, and clients holding a keep-alive connection
  to this worker are asked to reconnect.
* After a cooldown the worker starts accepting again. Repeatedly falling
  back into quarantine backs the cooldown off exponentially, so a worker
  that is permanently wedged stops taking traffic without needing to be
  killed.

Two safety properties matter more than the masking itself:

* **A worker never quarantines itself if that would leave the server
  below ``min_serving_ratio`` of its workers serving.** Masking is only
  worth anything while there is somewhere else for the traffic to go;
  past that point it would deny service rather than route around a slow
  worker. Workers claim quarantine slots through marker files in a local
  directory, so the floor holds across processes without any shared
  memory or database.
* **A masked worker is observable.** It publishes
  ``sky_apiserver_worker_serving{pid} 0``, so a health check in front of
  the server can tell "this server still has healthy capacity" from
  "every worker here has masked itself out" -- the two look identical if
  you only read the event-loop-lag gauge, because a masked worker reports
  low lag precisely because it stopped taking traffic.

Disabled by default; set ``SKYPILOT_WORKER_QUARANTINE_ENABLED=true`` to
turn it on.
"""
import contextlib
import dataclasses
import math
import os
import shutil
import threading
import time
from typing import List, Optional, Tuple

import psutil

from sky import sky_logging
from sky.metrics import utils as metrics_utils
from sky.skylet import constants

logger = sky_logging.init_logger(__name__)


def _load_http_protocol_classes() -> Tuple[type, ...]:
    """uvicorn's HTTP protocol classes, minus any that aren't installed.

    httptools is an optional uvicorn extra, so this cannot be a plain
    import: the h11 implementation is the fallback and is always present.
    """
    classes: List[type] = []
    try:
        from uvicorn.protocols.http import h11_impl  # pylint: disable=import-outside-toplevel
        classes.append(h11_impl.H11Protocol)
    except ImportError:
        pass
    try:
        from uvicorn.protocols.http import httptools_impl  # pylint: disable=import-outside-toplevel
        classes.append(httptools_impl.HttpToolsProtocol)
    except ImportError:
        pass
    return tuple(classes)


# Which connections quarantine is allowed to drain. Everything else on the
# worker -- WebSocket tunnels for SSH and log streaming above all -- is
# left alone: tearing those down turns a latency problem into a broken
# session.
_HTTP_PROTOCOL_CLASSES: Tuple[type, ...] = _load_http_protocol_classes()

# Holds one marker file per quarantined worker. Deliberately under /tmp
# rather than ~/.sky: it counts the workers of *this* server process tree,
# so it must stay machine-local even where ~/.sky is on shared storage
# (which is also why the graceful-shutdown lock lives in /tmp).
QUARANTINE_DIR = '/tmp/skypilot_worker_quarantine'

# How often the watchdog thread samples the loop. Small enough that the
# stall budget below has usable resolution, large enough to be free.
_SAMPLE_INTERVAL_SECONDS = 0.5

# How often the loop marks itself alive. The watchdog can only tell that
# the loop is behind by at most this much plus the real stall, so it
# bounds the measurement error.
_HEARTBEAT_INTERVAL_SECONDS = 0.1

# Rate at which the stall budget drains while the loop is responsive,
# relative to the rate at which it fills while stalled. 0.2 means a worker
# has to be healthy for 5x as long as it was stalled to fully clear its
# budget, so a worker that stalls repeatedly still accumulates toward
# quarantine even though it looks fine in between.
_BUDGET_DRAIN_RATE = 0.2

# Cap on the exponential cooldown backoff, as a multiple of the configured
# cooldown.
_MAX_COOLDOWN_MULTIPLIER = 10

# How long a worker waits before re-competing for a quarantine slot after
# being refused one. Keeps a server-wide stall from turning into every
# worker rescanning the slot directory twice a second.
_CLAIM_RETRY_INTERVAL_SECONDS = 10.0


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning(f'Invalid value for {name}: {raw!r}; using {default}.')
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ('1', 'true', 'yes', 'on')


@dataclasses.dataclass
class QuarantineConfig:
    """Knobs for worker self-quarantine, all read from the environment."""
    enabled: bool = False
    # Event-loop lag, in seconds, above which the loop counts as stalled.
    lag_threshold: float = 1.0
    # Seconds of stall that have to accumulate (see _BUDGET_DRAIN_RATE)
    # before the worker masks itself out.
    stall_budget: float = 5.0
    # Seconds to stay quarantined before accepting again.
    cooldown: float = 30.0
    # Fraction of this server's workers that must keep serving. Quarantine
    # is refused rather than breaking this floor. If a health check in
    # front of the server derives its own healthy-capacity threshold from
    # `sky_apiserver_worker_serving`, keep this comfortably above it, so
    # that masking on its own can never make the server look unhealthy.
    min_serving_ratio: float = 0.5

    @classmethod
    def from_env(cls) -> 'QuarantineConfig':
        prefix = constants.SKYPILOT_ENV_VAR_PREFIX + 'WORKER_QUARANTINE_'
        return cls(
            enabled=_env_bool(prefix + 'ENABLED', cls.enabled),
            lag_threshold=_env_float(prefix + 'LAG_SECONDS', cls.lag_threshold),
            stall_budget=_env_float(prefix + 'STALL_BUDGET_SECONDS',
                                    cls.stall_budget),
            cooldown=_env_float(prefix + 'COOLDOWN_SECONDS', cls.cooldown),
            min_serving_ratio=_env_float(prefix + 'MIN_SERVING_RATIO',
                                         cls.min_serving_ratio),
        )


class QuarantineSlots:
    """Marker-file bookkeeping for how many workers may be masked out.

    A worker claims a slot by creating ``<QUARANTINE_DIR>/<pid>`` and
    releases it by removing the file, so the count survives the fact that
    uvicorn workers share nothing but the listening socket. Markers left
    behind by a worker that died while quarantined are reaped on every
    count -- the supervisor replaces such a worker with a fresh pid, and a
    stale marker would otherwise permanently shrink the pool.
    """

    def __init__(self,
                 total_workers: int,
                 min_serving_ratio: float,
                 directory: str = QUARANTINE_DIR) -> None:
        self._dir = directory
        self._total = max(1, total_workers)
        ratio = min(1.0, max(0.0, min_serving_ratio))
        # At least one worker always serves, whatever the ratio says.
        self._min_serving = max(1, math.ceil(ratio * self._total))

    @property
    def max_quarantined(self) -> int:
        return max(0, self._total - self._min_serving)

    def count(self) -> int:
        """Number of live workers currently holding a slot."""
        return len(self._live_markers())

    def _live_markers(self) -> List[str]:
        try:
            names = os.listdir(self._dir)
        except FileNotFoundError:
            return []
        except OSError as e:
            logger.warning(f'Cannot read {self._dir}: {e}')
            return []
        live = []
        for name in names:
            try:
                pid = int(name)
            except ValueError:
                continue
            if _pid_is_alive(pid):
                live.append(name)
                continue
            # The worker died while quarantined; the supervisor has
            # already replaced it with a new pid.
            with contextlib.suppress(OSError):
                os.unlink(os.path.join(self._dir, name))
        return live

    def try_claim(self, pid: int) -> bool:
        """Claim a slot for ``pid``, or return False if the floor forbids it.

        Claims optimistically and then re-counts, so two workers deciding
        at the same instant cannot both slip past the floor: the loser
        sees the other's marker and releases its own.
        """
        if self.max_quarantined <= 0:
            return False
        try:
            os.makedirs(self._dir, exist_ok=True)
            # O_EXCL so a leftover marker from this same pid (pid reuse
            # inside the pod) does not silently pass as a fresh claim.
            fd = os.open(os.path.join(self._dir, str(pid)),
                         os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        except FileExistsError:
            # Already claimed by us.
            pass
        except OSError as e:
            logger.warning(f'Cannot claim a quarantine slot: {e}')
            return False
        if self.count() > self.max_quarantined:
            self.release(pid)
            return False
        return True

    def release(self, pid: int) -> None:
        with contextlib.suppress(OSError):
            os.unlink(os.path.join(self._dir, str(pid)))


def _pid_is_alive(pid: int) -> bool:
    try:
        return psutil.pid_exists(pid)
    except Exception:  # pylint: disable=broad-except
        # Never let an unexpected psutil failure make us drop a live
        # worker's marker -- that would let the floor be breached.
        return True


def reset_quarantine_dir() -> None:
    """Drop every marker. Called once by the parent before forking workers.

    Markers are pid-keyed, so anything still in the directory belongs to a
    previous server process tree (a hard crash, a /tmp that outlives the
    process) and is stale by definition.
    """
    with contextlib.suppress(OSError):
        shutil.rmtree(QUARANTINE_DIR)


class AcceptGate:
    """Pauses and resumes accepting on a uvicorn worker's listening sockets.

    asyncio has no public pause/resume for a running ``Server``, so this
    reaches for ``loop.remove_reader`` plus ``Server._start_serving``. Both
    have been stable since 3.8, but the gate verifies they are present up
    front and reports itself unsupported rather than half-applying the
    change on a runtime that has moved on.
    """

    def __init__(self, uvicorn_server) -> None:
        self._server = uvicorn_server

    def supported(self) -> bool:
        servers = getattr(self._server, 'servers', None) or ()
        if not servers:
            # Nothing to pause. Reporting "supported" here would let a
            # worker believe it had masked itself out while it kept
            # accepting, which is worse than not arming at all.
            return False
        for server in servers:
            if not hasattr(server, '_start_serving'):
                return False
            if not hasattr(server, '_serving'):
                return False
        return True

    def pause(self) -> None:
        """Stop accepting. Must run on the worker's event loop thread."""
        for server in getattr(self._server, 'servers', None) or ():
            loop = server.get_loop()
            for sock in server.sockets or ():
                loop.remove_reader(sock.fileno())
            # Makes the matching _start_serving() a no-op guard flip
            # instead of a second, duplicated add_reader.
            server._serving = False  # pylint: disable=protected-access

    def resume(self) -> None:
        """Start accepting again. Must run on the worker's event loop thread."""
        for server in getattr(self._server, 'servers', None) or ():
            server._start_serving()  # pylint: disable=protected-access

    def drain_idle_connections(self) -> int:
        """Close idle keep-alive connections; mark in-flight ones to close.

        This is what actually gets existing clients off the worker: a load
        balancer holding a persistent upstream connection would otherwise
        keep sending requests here no matter what the accept path does.
        In-flight requests are allowed to finish -- uvicorn just clears
        ``keep_alive`` so the response carries ``Connection: close``.

        WebSocket connections are left alone. They are long-lived tunnels
        by design (SSH, log streaming), and tearing them down would turn a
        latency problem into a broken session.
        """
        state = getattr(self._server, 'server_state', None)
        if state is None:
            return 0
        http_protocols = _http_protocol_classes()
        if not http_protocols:
            return 0
        drained = 0
        for connection in list(state.connections):
            if not isinstance(connection, http_protocols):
                continue
            try:
                # uvicorn's HTTP protocols all expose shutdown(); mypy only
                # sees the erased tuple of classes.
                connection.shutdown()  # type: ignore[attr-defined]
                drained += 1
            except Exception:  # pylint: disable=broad-except
                logger.debug('Failed to drain a connection', exc_info=True)
        return drained


def _http_protocol_classes() -> Tuple[type, ...]:
    """uvicorn's HTTP protocol classes, minus any that aren't installed."""
    return _HTTP_PROTOCOL_CLASSES


class WorkerHealthGate:
    """Watches one worker's event loop and masks it out when it stalls."""

    def __init__(self,
                 uvicorn_server,
                 total_workers: int,
                 config: Optional[QuarantineConfig] = None,
                 slots: Optional[QuarantineSlots] = None) -> None:
        self._config = config or QuarantineConfig.from_env()
        self._accept = AcceptGate(uvicorn_server)
        self._slots = slots or QuarantineSlots(total_workers,
                                               self._config.min_serving_ratio)
        self._pid = os.getpid()
        self._loop = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Written by the loop, read by the watchdog thread. A float
        # assignment is atomic under the GIL, so no lock is needed.
        self._last_beat = time.monotonic()
        self._budget = 0.0
        self._quarantined = False
        self._quarantined_at = 0.0
        self._resume_pending = False
        self._cooldown_multiplier = 1
        self._claim_retry_at = 0.0
        self._serving_gauge_value: Optional[int] = None

    # -- lifecycle --------------------------------------------------------

    def start(self, loop) -> bool:
        """Arm the gate. Returns False if it is disabled or unsupported."""
        if not self._config.enabled:
            return False
        if self._slots.max_quarantined <= 0:
            logger.info('Worker quarantine is enabled but this server has no '
                        'spare workers to mask out; not arming it.')
            return False
        if not self._accept.supported():
            logger.warning(
                'Worker quarantine is enabled but this Python runtime does '
                'not expose the asyncio server internals it needs; not '
                'arming it.')
            return False
        self._loop = loop
        self._publish_serving(True)
        self._schedule_heartbeat()
        self._thread = threading.Thread(target=self._watch,
                                        daemon=True,
                                        name='worker-health-gate')
        self._thread.start()
        logger.info(
            f'Worker quarantine armed: lag>{self._config.lag_threshold}s for '
            f'{self._config.stall_budget}s masks this worker out for '
            f'{self._config.cooldown}s (at most '
            f'{self._slots.max_quarantined} workers masked at once).')
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        # Give the slot back so a restarted worker is not counted against
        # the floor by our leftover marker.
        self._slots.release(self._pid)

    # -- loop-side heartbeat ---------------------------------------------

    def _schedule_heartbeat(self) -> None:
        if self._loop is None or self._stop.is_set():
            return
        self._last_beat = time.monotonic()
        self._loop.call_later(_HEARTBEAT_INTERVAL_SECONDS,
                              self._schedule_heartbeat)

    # -- watchdog thread --------------------------------------------------

    def _watch(self) -> None:
        while not self._stop.wait(_SAMPLE_INTERVAL_SECONDS):
            try:
                self._tick(time.monotonic())
            except Exception:  # pylint: disable=broad-except
                # A dead watchdog leaves the worker stuck in whatever
                # state it was in, which is the worst outcome available.
                logger.warning('Worker health tick failed', exc_info=True)

    def _tick(self, now: float) -> None:
        lag = max(0.0, now - self._last_beat - _HEARTBEAT_INTERVAL_SECONDS)
        if self._quarantined:
            self._maybe_resume(now)
            return
        if lag > self._config.lag_threshold:
            self._budget += _SAMPLE_INTERVAL_SECONDS
        else:
            self._budget = max(
                0.0,
                self._budget - _SAMPLE_INTERVAL_SECONDS * _BUDGET_DRAIN_RATE)
        if self._budget >= self._config.stall_budget:
            self._quarantine(now, lag)

    def _quarantine(self, now: float, lag: float) -> None:
        if now < self._claim_retry_at:
            # A recent claim was refused. Re-scanning (and re-logging) on
            # every tick is pure noise when the whole server is stalling
            # and all of its workers are competing for the last few slots.
            self._budget = self._config.stall_budget
            return
        if not self._slots.try_claim(self._pid):
            # The server is already down to its floor of serving workers.
            # Masking one more out would deny service rather than route
            # around it, so stay in rotation and keep serving slowly --
            # slow is strictly better than refused.
            logger.warning(
                f'Event loop stalled (lag {lag:.1f}s) but this server is '
                'already at its serving-worker floor; staying in rotation.')
            self._budget = self._config.stall_budget
            self._claim_retry_at = now + _CLAIM_RETRY_INTERVAL_SECONDS
            return
        self._quarantined = True
        self._quarantined_at = now
        self._budget = 0.0
        self._publish_serving(False)
        self._call_on_loop(self._apply_quarantine)
        logger.warning(
            f'Event loop lag {lag:.1f}s exceeded {self._config.lag_threshold}s '
            f'for {self._config.stall_budget}s; masking this worker out of '
            f'the accept path for {self._cooldown_seconds():.0f}s.')

    def _maybe_resume(self, now: float) -> None:
        if now - self._quarantined_at < self._cooldown_seconds():
            return
        if self._resume_pending:
            # The loop has not run our resume callback yet, which means it
            # is still wedged. Staying masked -- and staying counted as not
            # serving -- is exactly right: this worker cannot take traffic.
            return
        self._resume_pending = True
        self._call_on_loop(self._apply_resume)

    def _apply_resume(self) -> None:
        """Come back into rotation. Runs on the event loop thread.

        Everything that advertises this worker as available -- the accept
        path, the quarantine slot, the serving gauge -- flips only once the
        loop has actually run this callback, so a worker whose loop never
        recovers never claims to be back.
        """
        self._accept.resume()
        self._quarantined = False
        self._resume_pending = False
        self._budget = 0.0
        self._slots.release(self._pid)
        self._publish_serving(True)
        # A worker that goes straight back into quarantine is wedged
        # rather than transiently busy, so back off before probing again.
        self._cooldown_multiplier = min(_MAX_COOLDOWN_MULTIPLIER,
                                        self._cooldown_multiplier * 2)
        logger.info('Worker is accepting connections again.')

    def _cooldown_seconds(self) -> float:
        return self._config.cooldown * self._cooldown_multiplier

    def _apply_quarantine(self) -> None:
        self._accept.pause()
        drained = self._accept.drain_idle_connections()
        logger.info(f'Worker masked out; drained {drained} connections.')

    def _call_on_loop(self, fn) -> None:
        """Run ``fn`` on the event loop thread.

        The accept path is loop-owned state, so it cannot be touched from
        the watchdog thread. During a stall the call simply waits, which
        is the behaviour we want: the worker is not accepting anything
        while the loop is blocked anyway, and the mask lands the moment it
        would otherwise have resumed taking traffic.
        """
        if self._loop is None:
            return
        try:
            self._loop.call_soon_threadsafe(fn)
        except RuntimeError:
            # Loop already closed (shutting down).
            pass

    def _publish_serving(self, serving: bool) -> None:
        value = 1 if serving else 0
        if self._serving_gauge_value == value:
            return
        self._serving_gauge_value = value
        try:
            metrics_utils.SKY_APISERVER_WORKER_SERVING.labels(
                pid=str(self._pid)).set(value)
        except Exception:  # pylint: disable=broad-except
            logger.debug('Failed to publish worker serving gauge',
                         exc_info=True)


def publish_initial_serving_state() -> None:
    """Advertise this worker as serving, before it starts taking traffic.

    A consumer of ``sky_apiserver_worker_serving`` reads a worker that is
    missing from the gauge as masked out. Without this, there is a window
    between a worker publishing its first event-loop-lag sample and arming
    its health gate during which it would look masked, which would make a
    freshly started server briefly look like it had no capacity.

    No-op when quarantine is disabled: the gauge is then absent for every
    worker, which consumers read as "nothing here is masked".
    """
    if not QuarantineConfig.from_env().enabled:
        return
    try:
        metrics_utils.SKY_APISERVER_WORKER_SERVING.labels(
            pid=str(os.getpid())).set(1)
    except Exception:  # pylint: disable=broad-except
        logger.debug('Failed to publish initial worker serving gauge',
                     exc_info=True)


def maybe_start(uvicorn_server, total_workers: int,
                loop) -> Optional[WorkerHealthGate]:
    """Arm a health gate for this worker, or return None if not applicable."""
    if total_workers <= 1:
        # The only worker in the pod cannot mask itself out without
        # taking the pod down with it.
        return None
    gate = WorkerHealthGate(uvicorn_server, total_workers)
    if not gate.start(loop):
        return None
    return gate
