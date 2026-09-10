"""Instrumentation for the API server."""

import asyncio
import atexit
import glob
import multiprocessing
import os
import re
import threading
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

import fastapi
import fastapi.routing
from prometheus_client import core as prom_core
from prometheus_client import generate_latest
from prometheus_client import multiprocess
import prometheus_client as prom
import psutil
import starlette.routing
import starlette.types
import uvicorn

from sky import core
from sky import global_user_state
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import kubernetes as kubernetes_adaptor
from sky.metrics import utils as metrics_utils
from sky.server import constants as server_constants
from sky.server import local_disk
from sky.server import middleware_utils
from sky.skylet import runtime_utils
from sky.utils import annotations
from sky.utils import common
from sky.utils import common_utils
from sky.utils import status_lib

logger = sky_logging.init_logger(__name__)

_BURN_RATE_UPDATE_INTERVAL_SECONDS = 30
_COST_TIME_HORIZON_SECONDS = 3600

# Idempotency guard for register_multiproc_cleanup_atexit.
_multiproc_cleanup_registered = False


def register_multiproc_cleanup_atexit() -> None:
    """Clean up this process's prometheus_client multiproc files on exit.

    Each process that uses pid-labelled multiprocess metrics (uvicorn
    workers, executor workers) leaves files at
    ``$PROMETHEUS_MULTIPROC_DIR/<type>_<pid>.db``. The MultiProcessCollector
    keeps reading those files until ``multiprocess.mark_process_dead(pid)``
    deletes them. Without this hook, a worker that exits leaves its last
    written gauge value visible to every future scrape — for ``liveall``
    gauges this can pin a stale per-pid value indefinitely.

    Safe to call more than once per process; only the first call registers.
    Only registers when ``PROMETHEUS_MULTIPROC_DIR`` is set; a no-op in
    single-process / unit-test environments.
    """
    global _multiproc_cleanup_registered
    if _multiproc_cleanup_registered:
        return
    if not os.environ.get('PROMETHEUS_MULTIPROC_DIR'):
        return
    pid = os.getpid()
    atexit.register(multiprocess.mark_process_dead, pid)
    _multiproc_cleanup_registered = True


# Default reap interval. Tuned to give prompt cleanup of stale per-pid
# files without measurable overhead: one directory glob + one pid_exists()
# check per unique pid per tick.
_REAPER_INTERVAL_SECONDS = 60
# Matches the per-pid live-gauge file names written by
# ``prometheus_client.multiprocess``: ``gauge_live{all,sum,max,min}_<pid>.db``.
# These are the only files that ``mark_process_dead(pid)`` would remove; the
# library intentionally preserves counters, histograms, summaries, and
# non-live gauges so their accumulated values keep contributing to aggregate
# readings after the writer exits.
_LIVE_GAUGE_FILE_PID_RE = re.compile(r'^gauge_live[a-z]+_([0-9]+)\.db$')


def _scan_multiproc_pids(multiproc_dir: str) -> Set[int]:
    """Return pids that own live-gauge files in ``multiproc_dir``.

    Uses the same glob shape that ``mark_process_dead`` would walk, so we
    do not consider pids whose only on-disk traces are aggregate (counter
    / histogram / non-live gauge) files — those are intentionally kept.
    """
    pattern = os.path.join(multiproc_dir, 'gauge_live*_*.db')
    pids: Set[int] = set()
    for path in glob.glob(pattern):
        m = _LIVE_GAUGE_FILE_PID_RE.match(os.path.basename(path))
        if m is not None:
            pids.add(int(m.group(1)))
    return pids


def _reap_stale_multiproc_files() -> int:
    """Remove prometheus multiproc files for pids that no longer exist.

    Returns the number of pids reaped.
    """
    multiproc_dir = os.environ.get('PROMETHEUS_MULTIPROC_DIR')
    if not multiproc_dir:
        return 0
    file_pids = _scan_multiproc_pids(multiproc_dir)
    if not file_pids:
        return 0
    reaped = 0
    for pid in file_pids:
        if psutil.pid_exists(pid):
            continue
        try:
            multiprocess.mark_process_dead(pid)
            reaped += 1
        except Exception:  # pylint: disable=broad-except
            # Don't let a single bad file or a race with another reaper
            # tick kill the daemon.
            logger.warning(
                f'Failed to reap prometheus multiproc files for pid {pid}.',
                exc_info=True)
    return reaped


async def multiproc_reaper_daemon(
        interval_seconds: int = _REAPER_INTERVAL_SECONDS) -> None:
    """Periodically reap multiproc prometheus files from dead workers.

    Per the prometheus_client multiprocess docs, an exiting writer
    process should call ``multiprocess.mark_process_dead(pid)`` so its
    per-pid live-gauge files are removed; otherwise the collector keeps
    emitting the dead pid's last value on every scrape. ``atexit``-style
    cleanup inside the worker covers graceful exits, but never fires on
    SIGKILL / OOM / hard crash — in those cases a recycled worker's last
    live-gauge value can be served by ``/metrics`` indefinitely (until
    the API server pod itself restarts and wipes the metrics dir). This
    daemon scans the multiproc dir from the main API server process and
    invokes ``mark_process_dead`` on behalf of any writer whose pid no
    longer exists.

    Reaps any pid whose live-gauge file is present but for which
    ``psutil.pid_exists`` returns False (i.e. the pid no longer maps to
    any running process). The descendant relationship is intentionally
    not used as the membership signal: writers may not always be direct
    descendants of the main API server process (e.g. workers reparented
    to init after an intermediate exits), and a strict descendant filter
    would leak files from those legitimate writers.

    Known false-negative: if a dead worker's pid is later reused by an
    unrelated process inside the same pod, its files keep being scraped
    until either that unrelated process exits, the worker's pid wraps to
    another value, or a same-pid SkyPilot writer overwrites the file.
    PID reuse within a pod's lifetime is rare in practice (Linux pid_max
    is large and pids are allocated sequentially), so this is accepted.

    No-op when ``PROMETHEUS_MULTIPROC_DIR`` is unset.
    """
    if not os.environ.get('PROMETHEUS_MULTIPROC_DIR'):
        logger.info(
            'PROMETHEUS_MULTIPROC_DIR unset; multiproc reaper will not run.')
        return
    logger.info(
        f'Starting prometheus multiproc reaper (interval={interval_seconds}s)')
    while True:
        try:
            reaped = await asyncio.to_thread(_reap_stale_multiproc_files)
            if reaped:
                logger.info(
                    f'Reaped prometheus multiproc files for {reaped} dead '
                    f'pid(s).')
        except asyncio.CancelledError:
            logger.info('Prometheus multiproc reaper cancelled')
            break
        except Exception:  # pylint: disable=broad-except
            logger.warning('Error in prometheus multiproc reaper.',
                           exc_info=True)
        await asyncio.sleep(interval_seconds)


# How long a ResilientCollector snapshot is considered fresh. A scrape
# arriving after this triggers a background refresh (at most one in
# flight per collector).
_COLLECTOR_REFRESH_TTL_SECONDS = 30
# How stale a snapshot may get before the collector is reported as
# inactive via sky_apiserver_metrics_collector_active. Spans a couple of
# refresh windows so a single slow-but-successful refresh does not flap
# the gauge.
_COLLECTOR_MAX_STALENESS_SECONDS = 3 * _COLLECTOR_REFRESH_TTL_SECONDS


class ResilientCollector:
    """Serves scrapes from a snapshot that is refreshed off the scrape path.

    Wraps a Prometheus collector whose ``collect()`` may block on external
    state (typically the state database). ``collect()`` here never blocks:
    it returns the last snapshot immediately and, if the snapshot is older
    than ``ttl_seconds``, kicks off a background refresh — at most one in
    flight per collector, so a refresh hung on a saturated DB pool never
    stacks additional threads or queries on top of an ongoing outage.

    There is deliberately no cancellation or restart of a hung refresh: a
    thread blocked inside a DB driver cannot be killed, and retrying
    against an unhealthy database only adds load. Instead the collector
    keeps serving its stale snapshot and ``CollectorHealthCollector``
    flips ``sky_apiserver_metrics_collector_active`` to 0 once the last
    successful refresh is older than ``max_staleness_seconds`` — that is
    the signal operators alert on and intervene.
    """

    def __init__(
        self,
        wrapped,
        ttl_seconds: float = _COLLECTOR_REFRESH_TTL_SECONDS,
        max_staleness_seconds: float = (_COLLECTOR_MAX_STALENESS_SECONDS)):
        self._wrapped = wrapped
        # Label value for the health meta-metrics. May be suffixed by
        # _wrap_collector() to stay unique across instances.
        self.name = type(wrapped).__name__
        self.max_staleness_seconds = max_staleness_seconds
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._snapshot: List[prom_core.Metric] = []
        self._last_attempt_time = 0.0
        self._last_success_time = 0.0
        self._refresh_in_flight = False

    def describe(self):
        """Delegates to the wrapped ``describe()`` (never its ``collect()``).

        Having a ``describe()`` — even one that yields nothing — matters:
        ``prometheus_client`` falls back to calling ``collect()`` at
        registration time for collectors without one, which would put a
        potentially-blocking call back on the registration path.
        """
        describe = getattr(self._wrapped, 'describe', None)
        if describe is not None:
            yield from describe()

    def collect(self):
        now = time.time()
        with self._lock:
            snapshot = self._snapshot
            refresh_due = (not self._refresh_in_flight and
                           now - self._last_attempt_time >= self._ttl)
            if refresh_due:
                self._refresh_in_flight = True
                self._last_attempt_time = now
        if refresh_due:
            threading.Thread(target=self._refresh,
                             name=f'metrics-refresh-{self.name}',
                             daemon=True).start()
        yield from snapshot

    def _refresh(self) -> None:
        try:
            # Materialize before swapping so a failure mid-iteration
            # cannot leave a partial snapshot behind.
            snapshot = list(self._wrapped.collect())
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                'Metrics collector %s failed to refresh; '
                'serving stale snapshot.', self.name)
            with self._lock:
                self._refresh_in_flight = False
            return
        with self._lock:
            self._snapshot = snapshot
            self._last_success_time = time.time()
            self._refresh_in_flight = False

    def last_success_time(self) -> float:
        with self._lock:
            return self._last_success_time


# All ResilientCollector instances, for CollectorHealthCollector. Also
# gives the health meta-metrics a single emitter: were each wrapper to
# yield its own copy of the family, the duplicate names would make the
# exposition invalid (and fail registry duplicate-name checks).
_resilient_collectors: List[ResilientCollector] = []

_COLLECTOR_ACTIVE_HELP = (
    '1 if the collector refreshed successfully within its staleness '
    'bound; 0 means its metrics are being served from a stale snapshot '
    '(e.g. the refresh is hung on a DB outage) and needs operator '
    'attention. Also 0 between process start and the first successful '
    'refresh.')
_COLLECTOR_LAST_SUCCESS_HELP = (
    'Unix timestamp of the collector\'s last successful refresh; 0 if it '
    'has not succeeded since process start.')


class CollectorHealthCollector:
    """Health meta-metrics for every ResilientCollector in this process."""

    def describe(self):
        yield prom_core.GaugeMetricFamily(
            'sky_apiserver_metrics_collector_active',
            _COLLECTOR_ACTIVE_HELP,
            labels=['collector'])
        yield prom_core.GaugeMetricFamily(
            'sky_apiserver_metrics_collector_last_success_timestamp_seconds',
            _COLLECTOR_LAST_SUCCESS_HELP,
            labels=['collector'])

    def collect(self):
        now = time.time()
        active_family = prom_core.GaugeMetricFamily(
            'sky_apiserver_metrics_collector_active',
            _COLLECTOR_ACTIVE_HELP,
            labels=['collector'])
        last_success_family = prom_core.GaugeMetricFamily(
            'sky_apiserver_metrics_collector_last_success_timestamp_seconds',
            _COLLECTOR_LAST_SUCCESS_HELP,
            labels=['collector'])
        for collector in list(_resilient_collectors):
            last_success = collector.last_success_time()
            active = now - last_success <= collector.max_staleness_seconds
            active_family.add_metric([collector.name], 1.0 if active else 0.0)
            last_success_family.add_metric([collector.name], last_success)
        yield active_family
        yield last_success_family


def _wrap_collector(collector) -> ResilientCollector:
    """Wraps a collector in ResilientCollector with a unique health name."""
    wrapped = ResilientCollector(collector)
    existing = {c.name for c in _resilient_collectors}
    if wrapped.name in existing:
        suffix = 2
        while f'{wrapped.name}-{suffix}' in existing:
            suffix += 1
        wrapped.name = f'{wrapped.name}-{suffix}'
    _resilient_collectors.append(wrapped)
    return wrapped


_multiproc_collector: Optional[ResilientCollector] = None
_multiproc_collector_lock = threading.Lock()


def _get_multiproc_collector() -> ResilientCollector:
    """Process-wide wrapper for the multiprocess merge.

    The merge reads every per-pid file under ``PROMETHEUS_MULTIPROC_DIR``
    and is CPU-bound under the GIL, so wrapping it keeps concurrent
    scrapes from each running their own copy. Built lazily because
    ``MultiProcessCollector()`` raises unless that directory is set.
    """
    global _multiproc_collector
    with _multiproc_collector_lock:
        if _multiproc_collector is None:
            _multiproc_collector = _wrap_collector(
                multiprocess.MultiProcessCollector(None))
        return _multiproc_collector


class BurnRateCollector:
    """Collector for SkyPilot cluster burn rate metrics.
    This collector calculates the total hourly burn rate (in USD) of all
    active clusters. It caches the result for _BURN_RATE_UPDATE_INTERVAL_SECONDS
    to avoid frequent database queries.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._last_scrape_time = 0.0
        self._cached_total = 0.0
        self._cache_ttl = _BURN_RATE_UPDATE_INTERVAL_SECONDS

    def _compute_total(self) -> float:
        total = 0.0
        clusters = global_user_state.get_clusters()
        for cluster in clusters:
            status = cluster.get('status')
            status_name = getattr(status, 'name', status)
            if status_name != 'UP':
                continue

            handle = cluster.get('handle')
            if handle is None or not getattr(handle, 'launched_resources',
                                             None):
                continue

            # instance_type_to_hourly_cost + accelerators_to_hourly_cost.
            total += handle.launched_resources.get_cost(
                _COST_TIME_HORIZON_SECONDS)
        return total

    def describe(self):
        yield prom_core.GaugeMetricFamily(
            'sky_apiserver_total_burn_rate_dollars',
            'Total estimated hourly spend across all active clusters (USD/hr)',
            labels=['type'],
        )

    def collect(self):
        now = time.time()
        with self._lock:
            if now - self._last_scrape_time >= self._cache_ttl:
                try:
                    self._cached_total = self._compute_total()
                    self._last_scrape_time = now
                except Exception:  # pylint: disable=broad-except
                    logger.exception('Failed to compute burn rate')
                    self._last_scrape_time = now
            val = self._cached_total

        metric = prom_core.GaugeMetricFamily(
            'sky_apiserver_total_burn_rate_dollars',
            'Total estimated hourly spend across all active clusters (USD/hr)',
            labels=['type'],
        )
        metric.add_metric(['local_clusters'], val)
        yield metric


_BURN_RATE_COLLECTOR = _wrap_collector(BurnRateCollector())

try:
    prom.REGISTRY.register(_BURN_RATE_COLLECTOR)  # for non-multiprocess
except ValueError:
    pass

# SQLite sidecar files that count toward a database's disk footprint.
# The -wal file can grow large on its own when checkpoints fall behind;
# -shm is small but included for completeness.
_SQLITE_SIDECAR_SUFFIXES = ('-wal', '-shm')

_SQLITE_DB_SIZE_HELP = (
    'Total on-disk size in bytes (main file plus -wal/-shm sidecars) of '
    'each SkyPilot SQLite database, by database category. Emitted only '
    'for databases whose file exists on this host; deployments backed '
    'by Postgres emit nothing. SQLite files never shrink without a '
    'VACUUM (row deletion only frees pages for reuse), so expect this '
    'to be monotone within a process lifetime for append-heavy '
    'databases.')


def _sqlite_db_paths() -> Dict[str, str]:
    """Returns {db category: absolute path} for SkyPilot SQLite databases.

    Resolved at scrape time rather than import time so that
    SKY_RUNTIME_DIR / HOME are honored the same way the owning modules
    resolve their own DB paths (see db_utils.DatabaseManager and
    server_constants.API_SERVER_REQUEST_DB_PATH).
    """
    return {
        'state': runtime_utils.get_runtime_dir_path('.sky/state.db'),
        'spot_jobs': runtime_utils.get_runtime_dir_path('.sky/spot_jobs.db'),
        'serve': runtime_utils.get_runtime_dir_path('.sky/serve/services.db'),
        'config': runtime_utils.get_runtime_dir_path('.sky/config.db'),
        'requests': runtime_utils.expanduser(
            server_constants.API_SERVER_REQUEST_DB_PATH),
    }


class SqliteDBSizeCollector:
    """Collector for the on-disk size of SkyPilot's SQLite databases.

    Emits ``sky_apiserver_sqlite_db_size_bytes{db}`` for each SkyPilot
    SQLite database present on this host, where the value is the total
    disk footprint: the main file plus its ``-wal`` / ``-shm`` sidecars.
    A series is emitted only when the main database file exists, so
    deployments backed by Postgres emit nothing.

    Rationale: SQLite files never shrink without a VACUUM — row GC (e.g.
    the requests retention cleanup) only frees pages for reuse — so on
    long-lived deployments append-heavy databases like requests.db can
    grow unbounded within the server's lifetime. This gauge gives
    operators the visibility to alert on file growth before DB latency
    degrades.

    A scrape costs only a handful of stat() calls, but the collector is
    still wrapped in ResilientCollector like the others so a stat() that
    blocks on a degraded filesystem cannot stall the /metrics scrape.
    """

    def describe(self):
        yield prom_core.GaugeMetricFamily('sky_apiserver_sqlite_db_size_bytes',
                                          _SQLITE_DB_SIZE_HELP,
                                          labels=['db'])

    def collect(self):
        metric = prom_core.GaugeMetricFamily(
            'sky_apiserver_sqlite_db_size_bytes',
            _SQLITE_DB_SIZE_HELP,
            labels=['db'])
        for db, path in _sqlite_db_paths().items():
            try:
                size = os.path.getsize(path)
            except OSError:
                # Main file missing: this DB is not in use on this host
                # (e.g. Postgres backend) — skip rather than emit a
                # misleading 0.
                continue
            for suffix in _SQLITE_SIDECAR_SUFFIXES:
                try:
                    size += os.path.getsize(path + suffix)
                except OSError:
                    pass
            metric.add_metric([db], size)
        yield metric


_SQLITE_DB_SIZE_COLLECTOR = _wrap_collector(SqliteDBSizeCollector())

try:
    prom.REGISTRY.register(_SQLITE_DB_SIZE_COLLECTOR)  # for non-multiprocess
except ValueError:
    pass

_LOCAL_DISK_USED_HELP = (
    'Disk used by one of the trees the API server writes to its own local '
    'disk, by root. Counted in allocated blocks with hard links counted '
    'once, matching what du -- and therefore a container runtime\'s '
    'ephemeral-storage accounting -- reports. A series appears only for a '
    'root that exists on this host. Blocks held by a file that was unlinked '
    'while still open are invisible to a directory walk and so are missing '
    'from this value.')

_LOCAL_DISK_FILES_HELP = (
    'Distinct regular files under one of the API server\'s local roots, '
    'hard links counted once. Worth watching alongside bytes: the cost of '
    'the periodic du a container runtime runs to account for ephemeral '
    'storage scales with file count, not with size.')

_LOCAL_DISK_TRUNCATED_HELP = (
    '1 when the last walk of this root hit its entry or time bound and '
    'stopped early, making that root\'s reported size and file count lower '
    'bounds; 0 otherwise.')

_LOCAL_DISK_ROOT_CHARGED_HELP = (
    '1 when bytes written under this root count against the container\'s own '
    'ephemeral-storage budget, 0 when the platform charges them elsewhere -- '
    'a persistent volume or a memory-backed tmpfs mounted into the tree. '
    'Only the roots marked 1 go into '
    'sky_apiserver_local_disk_headroom_bytes, so this is what makes that '
    'number reproducible from the per-root series.')

_LOCAL_DISK_SCAN_DURATION_HELP = (
    'Wall-clock seconds the last walk of all local roots took. Runs off the '
    'scrape path, so this is a cost signal rather than scrape latency.')

_LOCAL_DISK_FS_SIZE_HELP = (
    'Total size of a filesystem hosting at least one of the API server\'s '
    'local roots, by mount point.')

_LOCAL_DISK_FS_AVAIL_HELP = (
    'Space available to a non-root writer on a filesystem hosting at least '
    'one of the API server\'s local roots, by mount point. On Kubernetes '
    'the value for the filesystem behind the container\'s writable layer is '
    'the headroom to the kubelet\'s node-level eviction threshold, which is '
    'what actually stops the server writing -- unlike the per-container '
    'budget below, it is exact.')

_LOCAL_DISK_BUDGET_HELP = (
    'The container\'s own declared ephemeral-storage allowance in bytes, by '
    'the resource field it came from. source="limit" is enforced -- exceed '
    'it and the container is stopped. source="request" is not: a platform '
    'may allow a container past its request, so exceeding it means only that '
    'the container is over what it declared, and first in line to be stopped '
    'when the node itself runs out. No series is emitted when neither field '
    'is exposed, which is the common case: it cannot be read from the '
    'kernel, since ephemeral-storage is not a cgroup controller, so it has '
    'to be injected (Kubernetes: a resourceFieldRef env var).')

_LOCAL_DISK_HEADROOM_HELP = (
    'Bytes left before the budget stops the server writing. Emitted only '
    'when the budget is an enforced limit: room left against a mere '
    'scheduling request is not headroom, since the container is allowed '
    'past it, and the number would reach zero with disk to spare. For the '
    'boundary that does stop writes without a limit, use '
    'sky_apiserver_local_disk_fs_avail_bytes. Counts only the roots whose '
    'sky_apiserver_local_disk_root_charged_to_ephemeral is 1, and is an '
    'upper bound: those roots cover what the server writes, not every byte '
    'the platform charges to this container.')

_LOCAL_DISK_UNREADABLE_HELP = (
    'Entries the last walk of this root could not read, excluding entries '
    'that had simply gone away -- the request-log GC unlinks constantly and '
    'those bytes really are gone. Non-zero means a permission or I/O error '
    'kept part of the tree out of the reported size, so treat it as a lower '
    'bound.')


class LocalDiskUsageCollector:
    """Collector for the API server's own local disk footprint.

    The trees behind these series grow with traffic and have no size bound
    of their own, so on a node with finite local disk the server can fill
    it. Without this, the earliest available signal is a node-level
    filesystem alert, which cannot say which pod is responsible and has
    only the gap between its own threshold and the eviction threshold as
    lead time -- a few percentage points, which at multi-GB-per-minute fill
    rates is under a minute. Per-root series make the growth attributable
    and let an alert trigger on rate rather than on level.

    Measurement only: nothing here bounds or refuses writes.

    A walk of a few hundred thousand files costs well under a second on
    local disk, but the roots are on whatever the deployment mounted, so
    ResilientCollector keeps it off the scrape path and each root's walk
    carries its own entry and time bound.
    """

    def describe(self):
        yield prom_core.GaugeMetricFamily('sky_apiserver_local_disk_used_bytes',
                                          _LOCAL_DISK_USED_HELP,
                                          labels=['root'])

    def collect(self):
        snapshot = local_disk.scan()

        used = prom_core.GaugeMetricFamily(
            'sky_apiserver_local_disk_used_bytes',
            _LOCAL_DISK_USED_HELP,
            labels=['root'])
        files = prom_core.GaugeMetricFamily('sky_apiserver_local_disk_files',
                                            _LOCAL_DISK_FILES_HELP,
                                            labels=['root'])
        truncated = prom_core.GaugeMetricFamily(
            'sky_apiserver_local_disk_scan_truncated',
            _LOCAL_DISK_TRUNCATED_HELP,
            labels=['root'])
        charged = prom_core.GaugeMetricFamily(
            'sky_apiserver_local_disk_root_charged_to_ephemeral',
            _LOCAL_DISK_ROOT_CHARGED_HELP,
            labels=['root'])
        unreadable = prom_core.GaugeMetricFamily(
            'sky_apiserver_local_disk_scan_unreadable_entries',
            _LOCAL_DISK_UNREADABLE_HELP,
            labels=['root'])
        for root, usage in snapshot.roots.items():
            used.add_metric([root], usage.used_bytes)
            files.add_metric([root], usage.files)
            truncated.add_metric([root], 1 if usage.truncated else 0)
            unreadable.add_metric([root], usage.unreadable)
            charged.add_metric(
                [root], 1 if snapshot.is_charged_to_ephemeral(root) else 0)
        yield used
        yield files
        yield truncated
        yield unreadable
        yield charged

        fs_size = prom_core.GaugeMetricFamily(
            'sky_apiserver_local_disk_fs_size_bytes',
            _LOCAL_DISK_FS_SIZE_HELP,
            labels=['mountpoint'])
        fs_avail = prom_core.GaugeMetricFamily(
            'sky_apiserver_local_disk_fs_avail_bytes',
            _LOCAL_DISK_FS_AVAIL_HELP,
            labels=['mountpoint'])
        for mountpoint, fs in snapshot.filesystems.items():
            fs_size.add_metric([mountpoint], fs.size_bytes)
            fs_avail.add_metric([mountpoint], fs.avail_bytes)
        yield fs_size
        yield fs_avail

        yield prom_core.GaugeMetricFamily(
            'sky_apiserver_local_disk_scan_duration_seconds',
            _LOCAL_DISK_SCAN_DURATION_HELP,
            value=snapshot.duration_seconds)

        budget = prom_core.GaugeMetricFamily(
            'sky_apiserver_local_disk_budget_bytes',
            _LOCAL_DISK_BUDGET_HELP,
            labels=['source'])
        headroom = prom_core.GaugeMetricFamily(
            'sky_apiserver_local_disk_headroom_bytes',
            _LOCAL_DISK_HEADROOM_HELP)
        if snapshot.budget is not None:
            budget.add_metric([snapshot.budget.source],
                              snapshot.budget.total_bytes)
        if snapshot.headroom_bytes is not None:
            headroom.add_metric([], snapshot.headroom_bytes)
        yield budget
        yield headroom


_LOCAL_DISK_USAGE_COLLECTOR = _wrap_collector(LocalDiskUsageCollector())

try:
    prom.REGISTRY.register(_LOCAL_DISK_USAGE_COLLECTOR)  # non-multiprocess
except ValueError:
    pass

_COLLECTOR_HEALTH_COLLECTOR = CollectorHealthCollector()

try:
    prom.REGISTRY.register(_COLLECTOR_HEALTH_COLLECTOR)
except ValueError:
    pass

_START_TIME_HELP = (
    'Unix timestamp when the API server started. Compute uptime as '
    'time() - sky_apiserver_start_time_seconds.')


class ServerStartTimeCollector:
    """Exports the API server start time, the uptime source.

    prometheus_client's built-in process_start_time_seconds is unavailable
    under the multiprocess collector (default collectors are not
    aggregated). Custom collectors are only scraped from the main server
    process (the metrics server runs in it), so that process's own creation
    time is the server boot time. The boot-check request row (which debug
    dumps use for uptime) is deliberately not used here: the row survives
    server restarts (schedule_on_boot_check_async ignores
    RequestAlreadyExistsError), so its created_at reflects the boot that
    first inserted it, not the current one.
    """

    def __init__(self):
        self._start_time = psutil.Process(os.getpid()).create_time()

    def describe(self):
        yield prom_core.GaugeMetricFamily('sky_apiserver_start_time_seconds',
                                          _START_TIME_HELP)

    def collect(self):
        metric = prom_core.GaugeMetricFamily('sky_apiserver_start_time_seconds',
                                             _START_TIME_HELP)
        metric.add_metric([], self._start_time)
        yield metric


_SERVER_START_TIME_COLLECTOR = _wrap_collector(ServerStartTimeCollector())

try:
    prom.REGISTRY.register(_SERVER_START_TIME_COLLECTOR)
except ValueError:
    pass

# Collectors registered by plugins at runtime (ResilientCollector-wrapped).
_plugin_collectors: list = []


def register_plugin_collector(collector):
    """Register a custom Prometheus collector from a plugin.

    The collector is wrapped in ResilientCollector, so a hung or failing
    data source degrades its metrics to a stale snapshot (flagged by
    sky_apiserver_metrics_collector_active) instead of hanging the whole
    /metrics scrape.
    """
    wrapped = _wrap_collector(collector)
    _plugin_collectors.append(wrapped)
    try:
        prom.REGISTRY.register(wrapped)
    except ValueError:
        pass


# Cache TTL shared by all custom collectors.
_COLLECTOR_CACHE_TTL_SECONDS = _BURN_RATE_UPDATE_INTERVAL_SECONDS

# Label value substituted when a row has NULL in workspace / user_hash / cloud.
# 'default' for workspace mirrors the convention used elsewhere in the
# codebase for pre-workspace rows; '' for user/cloud keeps the absence
# distinct without inventing a label value that could collide with a real
# one.
_NULL_WORKSPACE_LABEL = 'default'
_NULL_LABEL = ''


def _label_or_default(value: Optional[str], default: str) -> str:
    return value if value else default


# Hard upper bound on rows emitted by
# sky_managed_job_emergency_recovery_attempts. Emergencies are rare by
# construction (a bounded per-job retry budget), but a controller bug
# hitting many jobs at once could otherwise produce one series per job;
# capping defends Prometheus's series budget. The query orders by
# attempt count (highest first, deterministic tiebreak) so the jobs
# most likely to page survive the cap.
_EMERGENCY_EPISODES_MAX_SERIES = 100

_RECOVERY_EVENTS_HELP = (
    'Count of managed-job RECOVERING events currently retained in '
    'job_events, by recovery source and workspace. NOT monotone: '
    'job_events has a retention window, so aged-out rows decrease the '
    'value — use clamp_min(delta(...), 0) for rates, never increase(). '
    'Events written before recovery_source existed (NULL) are excluded.')

_EMERGENCY_ATTEMPTS_HELP = (
    'Emergency-recovery attempts used in the managed job\'s current '
    'episode. The series exists only while the episode is active (last '
    'attempt within the controller\'s reset window) and the job is '
    'non-terminal; capped at 100 series, highest attempt counts kept.')


class ManagedJobsCollector:
    """Collector for managed job state metrics.

    Emits ``sky_managed_jobs_count{workspace, user, status, cloud}`` for
    every task — both active (PENDING / LAUNCHING / RUNNING / …) and
    terminal (SUCCEEDED / FAILED* / CANCELLED). For pre-cloud-assignment
    statuses (PENDING / LAUNCHING) the ``cloud`` label is the empty
    string ``""``; operators can ``sum by (workspace, user, status)``
    for a cloud-agnostic view.

    Caveat: terminal counts grow monotonically as the DB accumulates
    rows. Reading absolute values gives lifetime totals; for "failures
    in the last hour" use ``increase(...)`` / ``delta(...)`` over a
    window. A proper Counter incremented at the state-transition site
    would be more semantically correct than a gauge here, but is
    deferred.

    Also emits two recovery-observability gauges:

    * ``sky_managed_job_recovery_events_count{recovery_source,
      workspace}`` — RECOVERING ``job_events`` rows currently retained,
      by source (FAILURE / EMERGENCY / RESTART). Not monotone (event
      retention prunes old rows), so rate queries must use
      ``clamp_min(delta(...), 0)``, never ``increase(...)``.
    * ``sky_managed_job_emergency_recovery_attempts{job_id, job_name,
      workspace}`` — per-job attempts used in the job's current
      emergency-recovery episode, emitted only while the episode is
      active and the job is non-terminal, so the series (and any alert
      on it) self-clears. Capped at ``_EMERGENCY_EPISODES_MAX_SERIES``.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._last_scrape_time = 0.0
        self._cache_ttl = _COLLECTOR_CACHE_TTL_SECONDS
        # List of (workspace, user_hash, cloud, status, count) tuples.
        self._cached_rows: list = []
        # List of (recovery_source, workspace, count) tuples.
        self._cached_recovery_rows: list = []
        # List of (spot_job_id, job_name, workspace, attempts) tuples.
        self._cached_episode_rows: list = []

    def _refresh(self):
        # pylint: disable=import-outside-toplevel
        from sky.jobs import constants as managed_job_constants
        from sky.jobs import state as managed_job_state

        # Query into locals first, then assign: a failure mid-refresh
        # (collect() logs it and serves the cache) must not leave a
        # mixed-age cache where some metrics updated and others didn't.
        rows = (managed_job_state.get_status_counts_by_workspace_user_cloud())
        recovery_rows = (
            managed_job_state.get_recovery_event_counts_by_source_workspace())
        episode_rows = (
            managed_job_state.get_active_emergency_recovery_episodes(
                now=time.time(),
                window_seconds=managed_job_constants.
                EMERGENCY_RECOVERY_RESET_WINDOW_SECONDS,
                limit=_EMERGENCY_EPISODES_MAX_SERIES,
            ))
        self._cached_rows = rows
        self._cached_recovery_rows = recovery_rows
        self._cached_episode_rows = episode_rows

    def describe(self):
        yield prom_core.GaugeMetricFamily(
            'sky_managed_jobs_count', ('Current count of managed job tasks by '
                                       'workspace, user, status, and cloud.'),
            labels=['workspace', 'user', 'status', 'cloud'])
        yield prom_core.GaugeMetricFamily(
            'sky_managed_job_recovery_events_count',
            _RECOVERY_EVENTS_HELP,
            labels=['recovery_source', 'workspace'])
        yield prom_core.GaugeMetricFamily(
            'sky_managed_job_emergency_recovery_attempts',
            _EMERGENCY_ATTEMPTS_HELP,
            labels=['job_id', 'job_name', 'workspace'])

    def collect(self):
        now = time.time()
        with self._lock:
            if now - self._last_scrape_time >= self._cache_ttl:
                try:
                    self._refresh()
                except Exception:  # pylint: disable=broad-except
                    logger.exception('Failed to collect managed jobs metrics')
                self._last_scrape_time = now
            rows = self._cached_rows
            recovery_rows = self._cached_recovery_rows
            episode_rows = self._cached_episode_rows

        counts: dict = {}
        for workspace, user_hash, cloud, status, count in rows:
            ws_label = _label_or_default(workspace, _NULL_WORKSPACE_LABEL)
            user_label = _label_or_default(user_hash, _NULL_LABEL)
            cloud_label = _label_or_default(cloud, _NULL_LABEL)
            key = (ws_label, user_label, status, cloud_label)
            counts[key] = counts.get(key, 0) + count

        metric = prom_core.GaugeMetricFamily(
            'sky_managed_jobs_count', ('Current count of managed job tasks by '
                                       'workspace, user, status, and cloud'),
            labels=['workspace', 'user', 'status', 'cloud'])
        for (workspace, user, status, cloud), count in counts.items():
            metric.add_metric([workspace, user, status, cloud], count)
        yield metric

        recovery_metric = prom_core.GaugeMetricFamily(
            'sky_managed_job_recovery_events_count',
            _RECOVERY_EVENTS_HELP,
            labels=['recovery_source', 'workspace'])
        for source, workspace, count in recovery_rows:
            ws_label = _label_or_default(workspace, _NULL_WORKSPACE_LABEL)
            recovery_metric.add_metric([source, ws_label], count)
        yield recovery_metric

        episode_metric = prom_core.GaugeMetricFamily(
            'sky_managed_job_emergency_recovery_attempts',
            _EMERGENCY_ATTEMPTS_HELP,
            labels=['job_id', 'job_name', 'workspace'])
        for job_id, job_name, workspace, attempts in episode_rows:
            ws_label = _label_or_default(workspace, _NULL_WORKSPACE_LABEL)
            name_label = _label_or_default(job_name, _NULL_LABEL)
            episode_metric.add_metric([str(job_id), name_label, ws_label],
                                      attempts)
        yield episode_metric


# Transient statuses we report time-in-state for. UP / STOPPED are steady
# states by design (no upper bound on residence time, alerting on age is
# meaningless). PENDING is display-only per status_lib.ClusterStatus.
_TIME_IN_STATE_STATUSES: Tuple[status_lib.ClusterStatus, ...] = (
    status_lib.ClusterStatus.INIT,
    status_lib.ClusterStatus.AUTOSTOPPING,
)

# Hard upper bound on rows emitted by sky_cluster_time_in_state_seconds.
# A tenant launching/failing many clusters in tight succession (bug, retry
# storm, or just heavy churn) can produce many simultaneously-transient
# cluster_name series; capping defends Prometheus's series budget.
# Sorting by age first means the clusters most likely to be stuck (and
# therefore most likely to be alertable) survive the cap.
_TIME_IN_STATE_MAX_SERIES = 100


class WorkspaceUsageCollector:
    """Per-workspace / per-user cluster usage metrics.

    Walks ``global_user_state.get_clusters()`` once per cache window
    (30 s) and emits:

      * ``sky_clusters_count{workspace,user,status,cloud,kind}`` —
        cluster counts. ``kind`` ∈ ``cluster|managed_job|controller``:
        filter ``kind="cluster"`` to avoid overlap with
        ``sky_managed_jobs_count`` and the controller; sum across
        kinds for the all-clusters total.
      * ``sky_clusters_gpus_in_flight{workspace,user,cloud,gpu_type,kind}``
        — GPU count by accelerator model across ``UP`` clusters,
        summed over ``launched_nodes``.
      * ``sky_cluster_time_in_state_seconds{workspace,status,cloud,kind,
        cluster_name}`` — Seconds in current state, per cluster, for
        transient statuses (INIT / AUTOSTOPPING). Operators alert on
        this to catch clusters stuck mid-transition; ``cluster_name``
        on the label set means the alert series identifies exactly
        which cluster is stuck. Source of truth is the
        ``cluster_events`` STATUS_CHANGE log so the timer is not reset
        by refresh paths that re-write ``cluster.status_updated_at``.

    All gauges share one cache to keep the cost of a scrape bounded to
    a single ``get_clusters()`` call (the same query
    ``BurnRateCollector`` already runs).

    The ``user`` label carries ``user_hash`` (immutable, 8-char hex)
    rather than the display name; operators who want a readable name
    can join with the ``users`` table or maintain a side-mapping.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._last_scrape_time = 0.0
        self._cache_ttl = _COLLECTOR_CACHE_TTL_SECONDS
        self._cached: dict = {
            'counts': {},
            'gpus': {},
            'time_in_state': {},
        }

    @staticmethod
    def _cluster_kind(cluster: dict) -> str:
        """Classify a cluster for the ``kind`` label.

        - ``controller``: jobs / serve controller (infra), by name prefix.
        - ``managed_job``: a managed-job backing cluster (``is_managed``).
        - ``cluster``: a plain cluster (``sky launch``).

        Letting operators filter ``kind="cluster"`` avoids double-counting
        against ``sky_managed_jobs_count`` / the controller; summing
        across all kinds still gives full resource coverage.

        We check the name prefix directly instead of going through
        ``controller_utils.Controllers.from_name``: that helper asserts
        exact-match against the live ``SERVER_ID`` (and mutates the
        ``Controllers`` enum singleton on the looser path), which would
        crash the whole scrape if the DB carries a controller row from
        a previous server identity (e.g. ephemeral storage wiped
        ``~/.sky/user_hash`` between restarts).
        """
        name = cluster.get('name') or ''
        if (name.startswith(common.SKY_SERVE_CONTROLLER_PREFIX) or
                name.startswith(common.JOB_CONTROLLER_PREFIX)):
            return 'controller'
        if cluster.get('is_managed'):
            return 'managed_job'
        return 'cluster'

    def _compute(self) -> dict:
        clusters = global_user_state.get_clusters(summary_response=True)
        counts: dict = {}
        gpus: dict = {}
        # Per-transient-status: list of (label_key, cluster_hash). label_key
        # is (workspace, status, cloud, kind, cluster_name) — the labels
        # the metric ultimately emits. We resolve the entered-at timestamps
        # in one batched DB call per status, then emit one gauge row per
        # cluster (no aggregation at the collector — alerts/dashboards can
        # max/sum however they want).
        transient: Dict[str, List[Tuple[Tuple[str, str, str, str, str],
                                        str]]] = {}
        transient_statuses = {s.name for s in _TIME_IN_STATE_STATUSES}

        for cluster in clusters:
            workspace = _label_or_default(cluster.get('workspace'),
                                          _NULL_WORKSPACE_LABEL)
            user = _label_or_default(cluster.get('user_hash'), _NULL_LABEL)
            kind = self._cluster_kind(cluster)

            handle = cluster.get('handle')
            launched_resources = (getattr(handle, 'launched_resources', None)
                                  if handle is not None else None)
            cloud_obj = (getattr(launched_resources, 'cloud', None)
                         if launched_resources is not None else None)
            cloud = _label_or_default(
                str(cloud_obj) if cloud_obj else None, _NULL_LABEL)

            status = cluster.get('status')
            status_name = getattr(status, 'name', str(status))

            # ── count ──
            count_key = (workspace, user, status_name, cloud, kind)
            counts[count_key] = counts.get(count_key, 0) + 1

            # ── time-in-state, transient statuses only ──
            if status_name in transient_statuses:
                cluster_hash = cluster.get('cluster_hash')
                cluster_name = cluster.get('name')
                if cluster_hash and cluster_name:
                    label_key = (workspace, status_name, cloud, kind,
                                 cluster_name)
                    transient.setdefault(status_name, []).append(
                        (label_key, cluster_hash))

            # ── GPUs, only for UP clusters ──
            if status_name != 'UP':
                continue
            if launched_resources is None:
                continue
            num_nodes = max(1, int(getattr(handle, 'launched_nodes', 1) or 1))

            accelerators = (launched_resources.accelerators or {})
            for acc_name, acc_count in accelerators.items():
                gpu_key = (workspace, user, cloud, acc_name, kind)
                gpus[gpu_key] = (gpus.get(gpu_key, 0.0) +
                                 float(acc_count) * num_nodes)

        # Resolve the entered-at timestamp per cluster. One DB call per
        # transient status; total cost bounded by # of distinct transient
        # statuses observed (≤ |_TIME_IN_STATE_STATUSES|).
        now_seconds = int(time.time())
        time_in_state: Dict[Tuple[str, str, str, str, str], float] = {}
        for status_name, entries in transient.items():
            try:
                status_enum = status_lib.ClusterStatus[status_name]
            except KeyError:
                continue
            hashes = {h for _, h in entries}
            entered_at = global_user_state.get_last_status_change_times(
                hashes, status_enum)
            for label_key, cluster_hash in entries:
                ts = entered_at.get(cluster_hash)
                if ts is None:
                    # Cluster has no recorded STATUS_CHANGE event for this
                    # status — typically a launch in the sub-second window
                    # before the event row is written, or a row written
                    # before the cluster_events table existed. Either way
                    # the residence is too short to alert on.
                    continue
                time_in_state[label_key] = max(0.0, float(now_seconds - ts))

        if len(time_in_state) > _TIME_IN_STATE_MAX_SERIES:
            logger.warning(
                'sky_cluster_time_in_state_seconds: %d transient clusters '
                'observed; capping to top %d by age. Oldest clusters '
                'survive the cap so any alertable case is preserved.',
                len(time_in_state), _TIME_IN_STATE_MAX_SERIES)
            time_in_state = dict(
                sorted(time_in_state.items(),
                       key=lambda kv: kv[1],
                       reverse=True)[:_TIME_IN_STATE_MAX_SERIES])

        return {
            'counts': counts,
            'gpus': gpus,
            'time_in_state': time_in_state,
        }

    def describe(self):
        yield prom_core.GaugeMetricFamily(
            'sky_clusters_count',
            'Count of clusters by workspace, user, status, cloud, and kind '
            '(kind=cluster|managed_job|controller)',
            labels=['workspace', 'user', 'status', 'cloud', 'kind'])
        yield prom_core.GaugeMetricFamily(
            'sky_clusters_gpus_in_flight',
            'GPU count across UP clusters, by workspace, user, cloud, '
            'gpu_type, kind',
            labels=['workspace', 'user', 'cloud', 'gpu_type', 'kind'])
        yield prom_core.GaugeMetricFamily(
            'sky_cluster_time_in_state_seconds',
            'Seconds in current state, per cluster, for transient statuses '
            '(INIT, AUTOSTOPPING). Source is the cluster_events '
            'STATUS_CHANGE log; only transient statuses emit, so steady '
            'cardinality is bounded by # in-flight transitions. Capped at '
            'the top 100 oldest clusters per scrape to defend Prometheus '
            'series budget against pathological churn.',
            labels=['workspace', 'status', 'cloud', 'kind', 'cluster_name'])

    def collect(self):
        now = time.time()
        with self._lock:
            if now - self._last_scrape_time >= self._cache_ttl:
                try:
                    self._cached = self._compute()
                    self._last_scrape_time = now
                except Exception:  # pylint: disable=broad-except
                    logger.exception('Failed to collect workspace usage')
                    self._last_scrape_time = now
            data = self._cached

        m = prom_core.GaugeMetricFamily(
            'sky_clusters_count',
            'Count of clusters by workspace, user, status, cloud, and kind '
            '(kind=cluster|managed_job|controller)',
            labels=['workspace', 'user', 'status', 'cloud', 'kind'])
        for (workspace, user, status, cloud, kind), v in data['counts'].items():
            m.add_metric([workspace, user, status, cloud, kind], v)
        yield m

        m = prom_core.GaugeMetricFamily(
            'sky_clusters_gpus_in_flight',
            'GPU count across UP clusters, by workspace, user, cloud, '
            'gpu_type, kind',
            labels=['workspace', 'user', 'cloud', 'gpu_type', 'kind'])
        for (workspace, user, cloud, gpu_type, kind), v in data['gpus'].items():
            m.add_metric([workspace, user, cloud, gpu_type, kind], v)
        yield m

        m = prom_core.GaugeMetricFamily(
            'sky_cluster_time_in_state_seconds',
            'Seconds in current state, per cluster, for transient statuses '
            '(INIT, AUTOSTOPPING). Source is the cluster_events '
            'STATUS_CHANGE log; only transient statuses emit, so steady '
            'cardinality is bounded by # in-flight transitions. Capped at '
            'the top 100 oldest clusters per scrape to defend Prometheus '
            'series budget against pathological churn.',
            labels=['workspace', 'status', 'cloud', 'kind', 'cluster_name'])
        for key, v in data['time_in_state'].items():
            workspace, status, cloud, kind, cluster_name = key
            m.add_metric([workspace, status, cloud, kind, cluster_name], v)
        yield m


_WORKSPACE_USAGE_COLLECTOR = _wrap_collector(WorkspaceUsageCollector())

try:
    prom.REGISTRY.register(_WORKSPACE_USAGE_COLLECTOR)
except ValueError:
    pass

_MANAGED_JOBS_COLLECTOR: Optional[ResilientCollector] = None


def maybe_register_managed_jobs_collector():
    """Register the managed jobs collector if in consolidation mode.

    Only consolidation mode has the managed jobs DB co-located with the
    API server. In remote SSH/gRPC modes the DB lives on the controller
    cluster and is not directly accessible.
    """
    global _MANAGED_JOBS_COLLECTOR
    if _MANAGED_JOBS_COLLECTOR is not None:
        return
    # pylint: disable=import-outside-toplevel
    from sky.jobs import utils as managed_job_utils
    if not managed_job_utils.is_consolidation_mode():
        return
    _MANAGED_JOBS_COLLECTOR = _wrap_collector(ManagedJobsCollector())
    try:
        prom.REGISTRY.register(_MANAGED_JOBS_COLLECTOR)
    except ValueError:
        pass


metrics_app = fastapi.FastAPI()


# Declared sync on purpose: the collection below is CPU-bound and would
# stall the event loop of whatever server is running it, so Starlette
# hands it to a worker thread instead. That makes the scrape latency
# depend on the serving loop's thread pool, which is why the metrics app
# gets an event loop to itself -- see start_metrics_server().
@metrics_app.get('/metrics')
def metrics() -> fastapi.Response:
    """Expose aggregated Prometheus metrics from all worker processes."""
    if os.environ.get('PROMETHEUS_MULTIPROC_DIR'):
        # In multiprocess mode, we need to collect metrics from all processes.
        registry = prom.CollectorRegistry()
        registry.register(_get_multiproc_collector())
        registry.register(_BURN_RATE_COLLECTOR)
        registry.register(_SQLITE_DB_SIZE_COLLECTOR)
        registry.register(_WORKSPACE_USAGE_COLLECTOR)
        registry.register(_LOCAL_DISK_USAGE_COLLECTOR)
        registry.register(_COLLECTOR_HEALTH_COLLECTOR)
        registry.register(_SERVER_START_TIME_COLLECTOR)
        if _MANAGED_JOBS_COLLECTOR is not None:
            registry.register(_MANAGED_JOBS_COLLECTOR)
        for c in _plugin_collectors:
            try:
                registry.register(c)
            except ValueError:
                pass
        data = generate_latest(registry)
    else:
        data = generate_latest()
    return fastapi.Response(content=data,
                            media_type=prom.CONTENT_TYPE_LATEST,
                            headers={'Cache-Control': 'no-cache'})


# Per-context timeout for metrics collection. Must be shorter than the
# Prometheus scrape_timeout configured on the upstream Prometheus that
# scrapes this endpoint so the response arrives before that scrape times
# out and marks the target down. Operators federating from a Prometheus
# with a non-default scrape_timeout should adjust both together; see
# docs/source/reference/api-server/examples/api-server-gpu-metrics-setup.rst.
#
# Without a per-context timeout, a single hanging port-forward (e.g. 30s
# httpx timeout) would block the entire /gpu-metrics response.
#
# 30s accommodates large compute clusters where federate latency plus
# port-forward setup can run 5-10s warm and longer cold.
_PER_CONTEXT_TIMEOUT_SECONDS = 30

_CREDENTIAL_MANAGER_KUBECONFIG_PATH = (
    '/var/skypilot/credentials/kubeconfig/kubeconfig')


@metrics_app.get('/debug-gpu-metrics')
async def gpu_metrics_debug() -> dict:
    """Debug endpoint for diagnosing GPU metrics collection issues."""
    kubeconfig_env = os.environ.get('KUBECONFIG', 'NOT_SET')
    default_path = os.path.expanduser('~/.kube/config')

    # Check what contexts are visible before and after cache clear
    pre_clear_contexts = core.get_all_contexts()
    annotations.clear_request_level_cache()
    post_clear_contexts = core.get_all_contexts()

    # Check kubeconfig file existence
    if kubeconfig_env != 'NOT_SET':
        kubeconfig_paths = kubeconfig_env.split(os.pathsep)
    else:
        kubeconfig_paths = [default_path]
    path_info = {}
    for p in kubeconfig_paths:
        expanded = os.path.expanduser(p)
        try:
            st = os.stat(expanded)
            path_info[p] = {'exists': True, 'size': st.st_size}
        except OSError:
            path_info[p] = {'exists': False, 'size': 0}

    # Check credential manager kubeconfig separately
    cred_mgr_exists = os.path.exists(_CREDENTIAL_MANAGER_KUBECONFIG_PATH)
    cred_mgr_contexts = []
    if cred_mgr_exists:
        try:
            ctxs, _ = (
                kubernetes_adaptor.kubernetes.config.list_kube_config_contexts(
                    config_file=_CREDENTIAL_MANAGER_KUBECONFIG_PATH))
            cred_mgr_contexts = [c['name'] for c in ctxs]
        except Exception as e:  # pylint: disable=broad-except
            cred_mgr_contexts = [f'error: {e}']

    return {
        'pid': os.getpid(),
        'thread': threading.current_thread().name,
        'KUBECONFIG': kubeconfig_env,
        'kubeconfig_paths': path_info,
        'credential_manager_kubeconfig': {
            'path': _CREDENTIAL_MANAGER_KUBECONFIG_PATH,
            'exists': cred_mgr_exists,
            'contexts': cred_mgr_contexts,
        },
        'contexts_before_cache_clear': pre_clear_contexts,
        'contexts_after_cache_clear': post_clear_contexts,
    }


def _handle_federation_result(context: str, route: str, result: object,
                              stats: metrics_utils.FederationStats,
                              all_metrics: List[str]) -> None:
    """Classifies one federation task result and records its outcome.

    On success appends the metrics text; on failure logs a readable, per-
    cluster message that names the context and includes the port-forward vs.
    /federate timing breakdown (so a timeout shows which phase blew the
    budget). Records the per-context outcome counter. Re-raises non-Exception
    BaseExceptions (KeyboardInterrupt/SystemExit) to preserve prior behavior.

    All work here is synchronous and non-blocking — no awaits, no I/O beyond
    logging — so it cannot hang the gather loop.
    """
    # asyncio.TimeoutError is an Exception subclass, so check it first.
    if isinstance(result, asyncio.TimeoutError):
        metrics_utils.record_federation_outcome(context, route, 'timeout')
        logger.error(
            f'Failed to get metrics for context {context} (route {route}): '
            f'timed out after {_PER_CONTEXT_TIMEOUT_SECONDS}s '
            f'({stats.summary()}); the federation attempt exceeded the '
            f'per-context budget; series for this cluster are omitted from '
            f'this scrape')
        return
    if isinstance(result, Exception):
        metrics_utils.record_federation_outcome(context, route, 'error')
        # format_exception already renders as '<ClassName>: <message>'.
        logger.error(
            f'Failed to get metrics for context {context} (route {route}): '
            f'{common_utils.format_exception(result)} ({stats.summary()})')
        return
    if isinstance(result, BaseException):
        # Avoid changing behavior for non-Exception BaseExceptions like
        # KeyboardInterrupt/SystemExit: re-raise them.
        raise result
    metrics_utils.record_federation_outcome(context, route, 'success')
    # debug: one line per context per scrape; the timeout/error paths above
    # log at error level, and the Prometheus metrics capture this regardless.
    logger.debug(f'Federated metrics for context {context} (route {route}): '
                 f'{stats.summary()}')
    # The three guards above leave only the success case: a metrics-text str.
    assert isinstance(result, str)
    all_metrics.append(result)


@metrics_app.get('/gpu-metrics')
async def gpu_metrics() -> fastapi.Response:
    """Gets the GPU metrics from multiple external k8s clusters"""
    # The metrics server runs as a daemon thread, not as a normal request
    # handler, so:
    # 1. The global config context (allowed_contexts, etc.) is a snapshot
    #    from startup. Reload it from the DB to pick up config changes.
    # 2. Request-scoped caches (kubernetes API clients, context names) are
    #    never cleared automatically. Clear them to pick up new kubeconfigs.
    skypilot_config.reload_config()
    annotations.clear_request_level_cache()
    contexts = core.get_all_contexts()
    all_metrics: List[str] = []

    # Skip contexts that point at the API server's own cluster: the central
    # Prometheus scrapes the local cluster's exporters directly, so
    # federating them again would only duplicate the raw series under a
    # stamped copy. The dashboard matches the local cluster with cluster=""
    # (see /dashboard_config local_contexts). Non-blocking: verdicts come
    # from the detection cache and any probing happens in a background
    # worker, so a slow or broken context cannot stall this scrape (or
    # the co-located /metrics scrape) no matter how it fails.
    _, remote_contexts = metrics_utils.split_local_remote_contexts(contexts)
    # One stats record per context, filled in by get_metrics_for_context even
    # if the task is later cancelled by the wait_for timeout — so the timeout
    # log can report how far the attempt got (port-forward vs. federate).
    stats_list = [metrics_utils.FederationStats() for _ in remote_contexts]
    tasks = [
        asyncio.create_task(
            asyncio.wait_for(
                metrics_utils.get_metrics_for_context(context, stats=stats),
                timeout=_PER_CONTEXT_TIMEOUT_SECONDS,
            )) for context, stats in zip(remote_contexts, stats_list)
    ]

    # Slurm clusters federate through their login node (see
    # get_metrics_for_slurm_cluster); only clusters with a configured
    # prometheus_url participate. Their series ride the same scrape,
    # stamped cluster="slurm/<name>", under the same per-context budget:
    # the budget is passed down so the SSH invocation is hard-killed at
    # the same instant wait_for() gives up on it. There is no port-forward
    # phase on this path, so its stats omit that phase.
    slurm_clusters = metrics_utils.get_slurm_metrics_clusters()
    slurm_contexts = [
        metrics_utils.SLURM_CONTEXT_PREFIX + name for name in slurm_clusters
    ]
    slurm_stats = [
        metrics_utils.FederationStats(has_port_forward=False)
        for _ in slurm_clusters
    ]
    tasks += [
        asyncio.create_task(
            asyncio.wait_for(
                metrics_utils.get_metrics_for_slurm_cluster(
                    name, stats=stats, timeout=_PER_CONTEXT_TIMEOUT_SECONDS),
                timeout=_PER_CONTEXT_TIMEOUT_SECONDS,
            )) for name, stats in zip(slurm_clusters, slurm_stats)
    ]
    result_contexts = remote_contexts + slurm_contexts
    stats_list = stats_list + slurm_stats

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for i, result in enumerate(results):
        _handle_federation_result(result_contexts[i], 'gpu-metrics', result,
                                  stats_list[i], all_metrics)

    combined_metrics = '\n\n'.join(all_metrics)

    # Return as plain text for Prometheus compatibility
    return fastapi.Response(
        content=combined_metrics,
        media_type='text/plain; version=0.0.4; charset=utf-8')


@metrics_app.get('/endpoints-metrics')
async def endpoint_metrics() -> fastapi.Response:
    """Gets Sky Endpoint serving metrics from multiple external k8s clusters.

    Mirrors /gpu-metrics but federates the serving engines' native series
    (vllm:* today; future engines append their prefixes) instead of
    DCGM/node metrics. The cluster= label is injected so the Grafana
    serving dashboards can filter by cluster.
    """
    # Same daemon-thread caveats as /gpu-metrics: reload config from the DB
    # (allowed_contexts etc. are a startup snapshot) and clear request-scoped
    # caches so new kubeconfigs are picked up.
    skypilot_config.reload_config()
    annotations.clear_request_level_cache()
    contexts = core.get_all_contexts()
    all_metrics: List[str] = []

    # Same local-context handling as /gpu-metrics above (non-blocking).
    _, remote_contexts = metrics_utils.split_local_remote_contexts(contexts)
    stats_list = [metrics_utils.FederationStats() for _ in remote_contexts]
    tasks = [
        asyncio.create_task(
            asyncio.wait_for(
                metrics_utils.get_endpoint_metrics_for_context(context,
                                                               stats=stats),
                timeout=_PER_CONTEXT_TIMEOUT_SECONDS,
            )) for context, stats in zip(remote_contexts, stats_list)
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for i, result in enumerate(results):
        _handle_federation_result(remote_contexts[i], 'endpoints-metrics',
                                  result, stats_list[i], all_metrics)

    combined_metrics = '\n\n'.join(all_metrics)

    return fastapi.Response(
        content=combined_metrics,
        media_type='text/plain; version=0.0.4; charset=utf-8')


def build_metrics_server(host: str, port: int) -> uvicorn.Server:
    metrics_config = uvicorn.Config(
        'sky.server.metrics:metrics_app',
        host=host,
        port=port,
        workers=1,
    )
    metrics_server_instance = uvicorn.Server(metrics_config)
    return metrics_server_instance


# The metrics server, so stop_metrics_server() can reach the instance
# started on the private thread.
_metrics_server: Optional[uvicorn.Server] = None


def start_metrics_server(host: str, port: int) -> uvicorn.Server:
    """Serve the metrics app on an event loop of its own, in its own thread.

    The metrics app must not share an event loop with anything else.
    ``/metrics`` is a sync endpoint, so Starlette runs it through
    ``anyio.to_thread.run_sync()``, which takes a token from the *default*
    thread limiter of the loop serving the request -- 40 tokens, one such
    limiter per loop. Every other coroutine on that loop that fans out
    ``anyio`` thread work draws from the same 40, and the limiter hands
    tokens out FIFO, so a scrape that arrives while a background task has
    thousands of ``anyio.Path`` calls queued up waits behind all of them
    before it even starts collecting.

    The API server's background loop hosts exactly that kind of task: the
    request GC unlinks a log file per deleted request, a batch at a time.
    While one runs, a scrape can take tens of seconds -- past any sane
    ``scrape_timeout`` -- so the target flaps to ``up == 0`` and every
    API server metric disappears for the duration, even though the server
    is otherwise healthy. Isolating the loop removes the coupling: the
    only thing contending for this loop's thread limiter is the scrape.

    Returns the server, whose ``should_exit`` the caller may set directly;
    stop_metrics_server() does that for the instance started here.
    """
    global _metrics_server
    server = build_metrics_server(host, port)
    _metrics_server = server

    def _serve() -> None:
        try:
            asyncio.run(server.serve())
        except SystemExit:
            # uvicorn calls sys.exit(1) when it cannot bind, and
            # threading.excepthook drops SystemExit on the floor, so
            # without this a metrics server that never came up would leave
            # nothing behind but uvicorn's own bind error -- the scrape
            # target just looks down, with no hint of why.
            logger.error('Metrics server failed to start on %s:%s', host, port)
        except Exception:  # pylint: disable=broad-except
            # Likewise: an unhandled error here would otherwise only reach
            # threading.excepthook.
            logger.exception('Metrics server exited unexpectedly')

    threading.Thread(target=_serve, daemon=True, name='metrics-server').start()
    return server


def stop_metrics_server() -> None:
    """Ask the metrics server started by start_metrics_server() to exit."""
    if _metrics_server is not None:
        _metrics_server.should_exit = True


def _get_status_code_group(status_code: int) -> str:
    """Group status codes into classes (2xx, 5xx) to reduce cardinality."""
    return f'{status_code // 100}xx'


def _is_streaming_api(path: str) -> bool:
    """Check if the path is a streaming API."""
    path = path.rstrip('/')
    return path.endswith('/logs') or path.endswith('/api/stream')


def _get_user_label(state: Mapping[str, Any]) -> str:
    """Extract the user label for metrics from the request state.

    `state` is the ASGI scope's `state` dict, i.e. what `request.state`
    is backed by. Returns the authenticated user's name if available,
    otherwise 'anonymous'.
    """
    auth_user = state.get('auth_user')
    if auth_user is not None and getattr(auth_user, 'name', None):
        return auth_user.name
    return 'anonymous'


# `path` label value for requests no registered route matches (404s, and
# requests a middleware rejected on a path that is not an endpoint). One
# fixed value instead of the raw path, so unauthenticated scanners cannot
# create a series per path they probe.
UNMATCHED_PATH_LABEL = 'unmatched'
# `path` label used when a mounted sub-application handles the request
# but exposes no routes of its own to match against (e.g. static files).
_MOUNT_TAIL = '/{path}'
# Mirror of InternalDashboardPrefixMiddleware in sky/server/server.py (the
# dashboard's reverse proxy prefix). Requests rejected by an outer middleware
# never reach that rewrite, so the metrics layer strips the prefix itself
# before resolving the route template; the successes and the rejections of
# one endpoint then land in the same series.
_INTERNAL_DASHBOARD_PREFIX = '/internal/dashboard/'
# Bound on the (scope type, method, path) -> route template memo used for
# requests the router did not dispatch. Cleared when full, and whenever the
# route table changes size (a route registered after a request to its path
# was already labeled).
_ROUTE_TEMPLATE_CACHE_SIZE = 4096
# Bound on the set of endpoints the router named that no route in the table
# owns (a mounted foreign application stamping its own `scope['endpoint']`).
# Each such endpoint triggers one index rebuild, then is remembered so it
# cannot trigger another.
_UNKNOWN_ENDPOINTS_CAP = 256
_RouteTable = Sequence[starlette.routing.BaseRoute]
# Route types whose `matches()` is a full-string regex match of the request's
# route path against the route's own path: a parameterless one can only match
# the literal path it was registered under. Anything else (mounts, hosts,
# subclasses with their own matching rules) is matched for every request.
_LITERAL_ROUTE_TYPES = (fastapi.routing.APIRoute,
                        fastapi.routing.APIWebSocketRoute,
                        starlette.routing.Route,
                        starlette.routing.WebSocketRoute)

try:
    # The path Starlette's routes match against: `scope['path']` with the
    # `root_path` prefix removed. Older Starlette versions match `path` as is.
    from starlette._utils import get_route_path as _get_route_path
except ImportError:  # pragma: no cover

    def _get_route_path(scope: starlette.types.Scope) -> str:
        return scope['path']


def _dispatch_candidates(routes: _RouteTable) -> Sequence[Any]:
    """The entries a request is matched against, in dispatch order.

    Recent FastAPI versions do not copy an included router's routes into the
    parent table: `include_router` appends one opaque entry per included
    router and resolves its effective (prefixed) routes lazily, so the entry
    matches a request but carries no path of its own. `iter_route_contexts`
    -- what FastAPI's OpenAPI generator uses -- expands those entries into
    contexts that carry the effective `path` and forward `matches()`; Mounts,
    Hosts and plain Starlette routes pass through unchanged. Older FastAPI
    versions flatten on include, so the table itself is the candidate list.
    """
    iter_route_contexts = getattr(fastapi.routing, 'iter_route_contexts', None)
    if iter_route_contexts is None:
        return routes
    try:
        return list(iter_route_contexts(routes))
    except Exception:  # pylint: disable=broad-except
        return routes


def _match_route_template(routes: _RouteTable,
                          match_scope: Dict[str, Any]) -> Optional[str]:
    """The template of the route Starlette would dispatch `match_scope` to."""
    return _match_candidates(_dispatch_candidates(routes), match_scope)


def _match_candidates(candidates: Sequence[Any],
                      match_scope: Dict[str, Any]) -> Optional[str]:
    """The template of the first candidate that matches `match_scope`.

    Same first-FULL-match-else-first-PARTIAL-match rule as
    `starlette.routing.Router`. Mounted sub-applications are resolved
    recursively when they expose routes; otherwise the mount path plus a
    fixed tail is used. `match_scope` must be a private copy: `matches()`
    implementations may write to it.
    """
    partial: Optional[Tuple[Any, Dict[str, Any]]] = None
    for route in candidates:
        try:
            match, child_scope = route.matches(match_scope)
        except Exception:  # pylint: disable=broad-except
            # A route type that cannot evaluate this scope: treat it as not
            # matching rather than failing to label the request.
            continue
        if match == starlette.routing.Match.FULL:
            return _template_of(route, match_scope, child_scope)
        if match == starlette.routing.Match.PARTIAL and partial is None:
            partial = (route, child_scope)
    if partial is not None:
        return _template_of(partial[0], match_scope, partial[1])
    return None


def _template_of(route: Any, match_scope: Dict[str, Any],
                 child_scope: Dict[str, Any]) -> str:
    # `route` is a Starlette route, or a FastAPI route context wrapping the
    # registered route. A context for an API route carries the effective
    # (prefixed) path itself; for any other route type included through a
    # router (WebSocket routes, plain Starlette routes, mounts) the prefixed
    # copy is its `starlette_route`.
    effective = getattr(route, 'starlette_route', None) or route
    original = getattr(effective, 'original_route', effective)
    if isinstance(original, (starlette.routing.Mount, starlette.routing.Host)):
        is_mount = isinstance(original, starlette.routing.Mount)
        # A Mount's path is a template (`/{tenant}/api` stays a template, not
        # the matched value); a Host adds nothing to the path.
        prefix = original.path if is_mount else ''
        # Starlette apps and routers expose `.routes`; StaticFiles and
        # foreign ASGI apps do not.
        sub_routes = getattr(original, 'routes', None)
        if sub_routes:
            sub_scope = dict(match_scope)
            sub_scope.update(child_scope)
            sub = _match_route_template(sub_routes, sub_scope)
            if sub is not None:
                return prefix + sub
        return prefix + _MOUNT_TAIL if is_mount else UNMATCHED_PATH_LABEL
    path = getattr(effective, 'path', None)
    return path if path else UNMATCHED_PATH_LABEL


def _effective_and_original(route: Any) -> Tuple[Any, Any]:
    """(effective route, registered route) for a candidate; see _template_of."""
    effective = getattr(route, 'starlette_route', None) or route
    return effective, getattr(effective, 'original_route', effective)


def _literal_path_of(route: Any) -> Optional[str]:
    """The only path this candidate can match, or None if it is dynamic."""
    effective, original = _effective_and_original(route)
    if type(original) not in _LITERAL_ROUTE_TYPES:  # pylint: disable=unidiomatic-typecheck
        return None
    path = getattr(effective, 'path', None)
    if not path or '{' in path or getattr(effective, 'param_convertors', None):
        return None
    return path


def _static_prefix_of(route: Any) -> str:
    """A prefix every path this dynamic candidate matches starts with.

    The regex of a route or mount is anchored at the start and begins with
    the literal text before its first parameter, so a request path without
    that prefix cannot match it. '' (try for every request) for route types
    with their own matching rules.
    """
    effective, original = _effective_and_original(route)
    if not (type(original) in _LITERAL_ROUTE_TYPES or  # pylint: disable=unidiomatic-typecheck
            isinstance(original, starlette.routing.Mount)):
        return ''
    path = getattr(effective, 'path', None) or ''
    return path.split('{', 1)[0]


def _collect_route_tables(routes: _RouteTable, tables: List[_RouteTable],
                          seen: Set[int]) -> None:
    """`routes` and every route table reachable from it, once each."""
    if id(routes) in seen:
        return
    seen.add(id(routes))
    tables.append(routes)
    for route in routes:
        # FastAPI's include_router entry keeps a reference to the included
        # router, whose own table can still grow.
        included = getattr(route, 'original_router', None)
        nested = getattr(included, 'routes', None) if included is not None \
            else None
        if nested is None and isinstance(
                route, (starlette.routing.Mount, starlette.routing.Host)):
            nested = getattr(route, 'routes', None)
        if nested:
            _collect_route_tables(nested, tables, seen)


class _RouteIndex:
    """What the label resolver needs from one snapshot of the route table.

    Built once per route-table version (the total route count over the
    tree; tables only grow: `include_router`, `mount` and plugin
    registrations append), so the per-request work does not depend on the
    number of routes:

    * endpoint -> template, for every route in the tree (mounted
      sub-applications included, with their mount prefix) whose endpoint is
      unique. Starlette's router writes the dispatched route's endpoint into
      the scope (`scope['endpoint']`) before the response starts, so a routed
      request resolves with one dict lookup and no regex matching.
    * literal path -> candidates, for routes whose template has no
      parameter. Such a route's regex is `^<literal>$`; for a given request
      only the candidates registered under exactly its path can match.
    * the dynamic candidates (parameterized routes, mounts, hosts, route
      types with their own matching rules) with the literal prefix each one
      requires; only those whose prefix the request path starts with are
      tried.

    An unrouted request (answered by a middleware before the router ran, or
    a 404) is matched against the literal candidates for its path plus the
    dynamic ones it can match, in registration order, with the router's own
    first-FULL-else-first-PARTIAL rule: the same route the router would have
    dispatched to, at a cost that does not grow with the route table.
    """

    def __init__(self, routes: _RouteTable):
        # Every route table in the tree (this one, included routers, mounted
        # sub-applications): the index is stale once any of them grew.
        self._tables: List[_RouteTable] = []
        _collect_route_tables(routes, self._tables, set())
        self._version = self.version()
        self._literal: Dict[str, List[Tuple[int, Any]]] = {}
        self._dynamic: List[Tuple[int, str, Any]] = []
        self._by_endpoint: Dict[Any, str] = {}
        self._ambiguous: Set[Any] = set()
        self._unknown: Set[Any] = set()
        candidates = _dispatch_candidates(routes)
        for order, candidate in enumerate(candidates):
            literal_path = _literal_path_of(candidate)
            if literal_path is None:
                self._dynamic.append(
                    (order, _static_prefix_of(candidate), candidate))
            else:
                self._literal.setdefault(literal_path, []).append(
                    (order, candidate))
        self._index_endpoints(candidates, '')

    def version(self) -> int:
        """Total number of routes across the tree's tables."""
        return sum(len(table) for table in self._tables)

    def stale(self) -> bool:
        return self.version() != self._version

    def _index_endpoints(self, candidates: Sequence[Any], prefix: str) -> None:
        for candidate in candidates:
            effective, original = _effective_and_original(candidate)
            if isinstance(original,
                          (starlette.routing.Mount, starlette.routing.Host)):
                is_mount = isinstance(original, starlette.routing.Mount)
                mount_prefix = prefix + (original.path if is_mount else '')
                # The router leaves the mounted application itself as the
                # endpoint when it has no routes (static files) or none of
                # them matched; same label as _template_of gives that case.
                self._add_endpoint(
                    original.app, mount_prefix +
                    _MOUNT_TAIL if is_mount else UNMATCHED_PATH_LABEL)
                sub_routes = getattr(original, 'routes', None)
                if sub_routes:
                    self._index_endpoints(_dispatch_candidates(sub_routes),
                                          mount_prefix)
                continue
            path = getattr(effective, 'path', None)
            endpoint = getattr(effective, 'endpoint', None)
            if endpoint is not None and path:
                self._add_endpoint(endpoint, prefix + path)

    def _add_endpoint(self, endpoint: Any, template: str) -> None:
        try:
            if endpoint in self._ambiguous:
                return
            existing = self._by_endpoint.get(endpoint)
            if existing is None:
                self._by_endpoint[endpoint] = template
            elif existing != template:
                # One function registered under two templates: only matching
                # the path tells which one this request hit.
                del self._by_endpoint[endpoint]
                self._ambiguous.add(endpoint)
        except TypeError:
            # Unhashable endpoint: resolved by path matching.
            return

    def template_for_endpoint(self, endpoint: Any) -> Optional[str]:
        try:
            return self._by_endpoint.get(endpoint)
        except TypeError:
            return None

    def knows_endpoint(self, endpoint: Any) -> bool:
        """False only for an endpoint no route in this snapshot owns."""
        try:
            return (endpoint in self._by_endpoint or
                    endpoint in self._ambiguous or endpoint in self._unknown)
        except TypeError:
            return True

    def forget_endpoint(self, endpoint: Any) -> None:
        """Remember a foreign endpoint so it triggers no further rebuild."""
        if len(self._unknown) >= _UNKNOWN_ENDPOINTS_CAP:
            self._unknown.clear()
        try:
            self._unknown.add(endpoint)
        except TypeError:
            pass

    def resolve_unrouted(self, match_scope: Dict[str, Any]) -> Optional[str]:
        """Template for a request the router did not dispatch, or None."""
        route_path = _get_route_path(match_scope)
        possible = [(order, candidate)
                    for order, prefix, candidate in self._dynamic
                    if route_path.startswith(prefix)]
        possible.extend(self._literal.get(route_path, ()))
        possible.sort(key=lambda item: item[0])
        return _match_candidates([candidate for _, candidate in possible],
                                 match_scope)


class PrometheusMiddleware:
    """Pure-ASGI middleware that records request metrics.

    Counts what the client actually receives, so it must be the OUTERMOST
    middleware of the app (added last in sky/server/server.py). An
    authentication, RBAC or shutdown middleware that answers a request
    itself never calls the next layer; with the metrics middleware inside
    the stack those responses -- exactly the ones an outage produces -- are
    never counted, and the only visible symptom is successful traffic
    dropping. This layer observes the ASGI `http.response.start` message
    instead of a `call_next` return value, so it sees every response
    regardless of which layer produced it. It is deliberately not a
    `BaseHTTPMiddleware`: those pass non-HTTP scopes straight through, so
    WebSocket handshakes would not be observed either.

    Recorded per HTTP request: `sky_apiserver_requests_total`,
    `sky_apiserver_requests_by_user_total`,
    `sky_apiserver_request_duration_seconds` (non-streaming APIs) and
    `sky_apiserver_request_get_duration_seconds` (/api/get, by request
    name); plus `sky_apiserver_request_rejections_total{reason}` when a
    middleware stamped a rejection reason (`middleware_utils.mark_rejection`).
    Per WebSocket handshake: `sky_apiserver_websocket_handshakes_total`
    with the HTTP status the client saw, and the rejection counter when the
    handshake was refused.

    The `path` label is the matched route's template (e.g.
    `/ssh_node_pools/{pool_name}/status`), which bounds the label's
    cardinality by the number of registered routes: an unauthenticated
    client's requests are now counted too, and their paths are arbitrary.
    Unmatched paths are recorded as `UNMATCHED_PATH_LABEL`. Inner
    middlewares rewrite `scope['path']` in place (the internal dashboard
    prefix), so the path is read when the response starts, not on entry.
    The application and `root_path` are captured on entry instead: the
    router replaces both in place when it dispatches into a mounted
    sub-application. A routed request is labeled from the endpoint the
    router stored on the scope (one dict lookup); a request answered before
    the router ran is matched against the route table (`_RouteIndex`).
    The index is rebuilt from the live route table whenever it grows, so
    routes registered after this layer was built (the core routers and
    plugin routes are) are matched like any other.

    Duration is measured from entry into this layer, i.e. it includes the
    time the authentication middlewares spend (DB lookups under their
    deadline): it is the latency the client observed.

    Recording fails open. Every recording call (counters, histograms, the
    route-template lookup, the rejection counters) runs under
    `middleware_utils.record_safely`: an exception there is logged
    (rate-limited per process) and dropped, the ASGI message that triggered
    it is forwarded to the client unchanged, and the response stream is not
    altered. Exceptions raised by the wrapped application are not caught:
    they are counted as a 500 and re-raised for Starlette's error handler.
    """

    def __init__(self, app: starlette.types.ASGIApp):
        self.app = app
        self._route_index: Optional[_RouteIndex] = None
        # (scope type, method, path) -> template, for unrouted requests only.
        self._route_template_cache: Dict[Tuple[str, str, str], str] = {}

    async def __call__(self, scope: starlette.types.Scope,
                       receive: starlette.types.Receive,
                       send: starlette.types.Send) -> None:
        scope_type = scope.get('type')
        if scope_type == 'http':
            await self._handle_http(scope, receive, send)
        elif scope_type == 'websocket':
            await self._handle_websocket(scope, receive, send)
        else:
            await self.app(scope, receive, send)

    # --- HTTP -------------------------------------------------------------

    async def _handle_http(self, scope: starlette.types.Scope,
                           receive: starlette.types.Receive,
                           send: starlette.types.Send) -> None:
        scope.setdefault('state', {})
        logger.debug(f'PROM Middleware Request: {scope.get("method")} '
                     f'{scope.get("path")}')
        start_time = time.time()
        # See _path_label: both are replaced in place by a mount dispatch.
        app, root_path = scope.get('app'), scope.get('root_path', '')
        started = False

        def record_response(message: starlette.types.Message) -> None:
            self._record_http(scope, int(message['status']), start_time, app,
                              root_path)

        def record_unhandled_exception() -> None:
            # Escaped every inner layer; Starlette's ServerErrorMiddleware
            # (outside us) turns it into a bare 500. Count what the client
            # sees.
            scope['state'].setdefault(
                middleware_utils.REJECT_REASON_STATE_KEY,
                middleware_utils.REJECT_REASON_UNHANDLED_EXCEPTION)
            self._record_http(scope, 500, start_time, app, root_path)

        async def send_wrapper(message: starlette.types.Message) -> None:
            nonlocal started
            # Fail-open: recording runs under record_safely, so whatever
            # happens in the metrics path the original message is forwarded
            # unchanged. `started` is set first so a failed recording is not
            # retried on a later message.
            if not started and message.get('type') == 'http.response.start':
                started = True
                middleware_utils.record_safely('HTTP response', record_response,
                                               message)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:  # pylint: disable=broad-except
            if not started:
                middleware_utils.record_safely('unhandled HTTP exception',
                                               record_unhandled_exception)
            # Always let the application's exception propagate.
            raise

    def _record_http(self,
                     scope: starlette.types.Scope,
                     status_code: int,
                     start_time: float,
                     app: Any = None,
                     root_path: Optional[str] = None) -> None:
        state = scope.get('state') or {}
        method = scope.get('method', '')
        raw_path = scope.get('path', '')
        path = self._path_label(scope, app, root_path)
        status_code_group = _get_status_code_group(status_code)
        metrics_utils.SKY_APISERVER_REQUESTS_TOTAL.labels(
            path=path, method=method, status=status_code_group).inc()
        metrics_utils.SKY_APISERVER_REQUESTS_BY_USER_TOTAL.labels(
            user=_get_user_label(state),
            method=method,
            status=status_code_group).inc()
        middleware_utils.record_rejection(scope, status_code,
                                          middleware_utils.REJECTION_KIND_HTTP)
        if _is_streaming_api(raw_path):
            # Exclude streaming APIs, the duration is not meaningful.
            # TODO(aylei): measure the duration of async execution instead.
            return
        duration = time.time() - start_time
        metrics_utils.SKY_APISERVER_REQUEST_DURATION_SECONDS.labels(
            path=path, method=method,
            status=status_code_group).observe(duration)
        # /api/get long-polls until the underlying request is terminal, so its
        # duration is the client-observed latency of that request type. The
        # handler stamps request.state.request_name once it knows which
        # request is being fetched; record it by name so bounded types can be
        # alerted on separately from unbounded ones (launch/exec/...).
        request_name = state.get('request_name')
        if request_name is not None:
            metrics_utils.SKY_APISERVER_REQUEST_GET_DURATION_SECONDS.labels(
                name=request_name, status=status_code_group).observe(duration)

    # --- WebSocket --------------------------------------------------------

    async def _handle_websocket(self, scope: starlette.types.Scope,
                                receive: starlette.types.Receive,
                                send: starlette.types.Send) -> None:
        scope.setdefault('state', {})
        # See _path_label: both are replaced in place by a mount dispatch.
        app, root_path = scope.get('app'), scope.get('root_path', '')
        settled = False

        def record(accepted: bool, status_code: int) -> None:
            self._record_handshake(scope, accepted, status_code, app, root_path)

        def record_http_rejection(message: starlette.types.Message) -> None:
            record(accepted=False, status_code=int(message['status']))

        def record_unhandled_exception() -> None:
            # uvicorn answers an exception before the accept with an HTTP 500
            # handshake response.
            scope['state'].setdefault(
                middleware_utils.REJECT_REASON_STATE_KEY,
                middleware_utils.REJECT_REASON_UNHANDLED_EXCEPTION)
            record(accepted=False, status_code=500)

        async def send_wrapper(message: starlette.types.Message) -> None:
            nonlocal settled
            # Fail-open, as in _handle_http: the handshake message is
            # forwarded unchanged whatever happens in the metrics path.
            if not settled:
                message_type = message.get('type')
                if message_type == 'websocket.accept':
                    settled = True
                    middleware_utils.record_safely('WebSocket handshake',
                                                   record,
                                                   accepted=True,
                                                   status_code=101)
                elif message_type == 'websocket.close':
                    # A close before accept: servers render it as an empty
                    # HTTP 403 whatever the close code says.
                    settled = True
                    middleware_utils.record_safely('WebSocket handshake',
                                                   record,
                                                   accepted=False,
                                                   status_code=403)
                elif message_type == 'websocket.http.response.start':
                    settled = True
                    middleware_utils.record_safely('WebSocket handshake',
                                                   record_http_rejection,
                                                   message)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:  # pylint: disable=broad-except
            if not settled:
                middleware_utils.record_safely('unhandled WebSocket exception',
                                               record_unhandled_exception)
            # Always let the application's exception propagate.
            raise

    def _record_handshake(self,
                          scope: starlette.types.Scope,
                          accepted: bool,
                          status_code: int,
                          app: Any = None,
                          root_path: Optional[str] = None) -> None:
        """Count one handshake; `status_code` is what the client saw."""
        path = self._path_label(scope, app, root_path)
        outcome = 'accepted' if accepted else 'rejected'
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKES_TOTAL.labels(
            path=path, outcome=outcome, status=str(status_code)).inc()
        if accepted:
            return
        reason = (middleware_utils.get_rejection_reason(scope) or
                  middleware_utils.REJECT_REASON_UNSPECIFIED)
        metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL.labels(
            reason=reason,
            status=str(status_code),
            kind=middleware_utils.REJECTION_KIND_WEBSOCKET).inc()

    # --- path label -------------------------------------------------------

    def _path_label(self,
                    scope: starlette.types.Scope,
                    app: Any = None,
                    root_path: Optional[str] = None) -> str:
        """Route template for the request; see _RouteIndex.

        `app` and `root_path` are the values the scope carried when the
        request entered this layer. The router mutates both in place while
        dispatching into a mounted sub-application (`scope['app']` becomes
        the sub-application, `root_path` grows by the mount path) and this
        label is computed when the response starts, i.e. after that; matching
        from the mutated values would drop the mount prefix or match nothing.
        Without the captured values (direct callers) the scope's are used.
        """
        if app is None:
            app = scope.get('app')
        if root_path is None:
            root_path = scope.get('root_path', '')
        path = scope.get('path', '')
        if path.startswith(_INTERNAL_DASHBOARD_PREFIX):
            path = path.replace(_INTERNAL_DASHBOARD_PREFIX, '/', 1)
        # Starlette stores the application on the scope before the middleware
        # stack runs, so the live route table is reachable from here.
        routes = getattr(getattr(app, 'router', app), 'routes', None)
        if not routes:
            # Without a router (a bare ASGI callable in tests) there is
            # nothing to match against and the raw path is used.
            return path
        try:
            index = self._route_index
            if index is None or index.stale():
                # A path labeled before its route was registered would
                # otherwise stay `unmatched`: rebuild when any table in the
                # tree grew.
                index = self._rebuild_route_index(routes)
            # Routed request: the router stored the dispatched route's
            # endpoint.
            endpoint = scope.get('endpoint')
            if endpoint is not None:
                label = index.template_for_endpoint(endpoint)
                if label is None and not index.knows_endpoint(endpoint):
                    # An endpoint no indexed route owns (a route table this
                    # index could not see grew): rebuild once for it.
                    index = self._rebuild_route_index(routes)
                    label = index.template_for_endpoint(endpoint)
                    if label is None:
                        index.forget_endpoint(endpoint)
                if label is not None:
                    return label
        except Exception as e:  # pylint: disable=broad-except
            # Fail-open, as below: an index fault costs this request its
            # template, not its count.
            middleware_utils.note_recording_failure('route index', e)
            return UNMATCHED_PATH_LABEL
        # Unrouted (answered before the router ran, or no route matched):
        # match the path, memoized.
        key = (scope.get('type', ''), scope.get('method', ''), path)
        label = self._route_template_cache.get(key)
        if label is None:
            match_scope = {
                'type': scope.get('type', 'http'),
                'method': scope.get('method', 'GET'),
                'path': path,
                'root_path': root_path,
                'path_params': {},
            }
            try:
                label = index.resolve_unrouted(match_scope) or \
                    UNMATCHED_PATH_LABEL
            except Exception as e:  # pylint: disable=broad-except
                # Fail-open: a resolver fault costs this request its route
                # template, not its count. Not memoized, so a transient fault
                # is retried on the next request to the path.
                middleware_utils.note_recording_failure(
                    'route template resolution', e)
                return UNMATCHED_PATH_LABEL
            if len(self._route_template_cache) >= _ROUTE_TEMPLATE_CACHE_SIZE:
                self._route_template_cache.clear()
            self._route_template_cache[key] = label
        return label

    def _rebuild_route_index(self, routes: _RouteTable) -> _RouteIndex:
        self._route_index = _RouteIndex(routes)
        self._route_template_cache.clear()
        return self._route_index


peak_rss_bytes = 0


def process_monitor(process_type: str, stop: threading.Event):
    pid = multiprocessing.current_process().pid
    proc = psutil.Process(pid)
    last_bucket_end = time.time()
    bucket_peak = 0
    global peak_rss_bytes
    while not stop.is_set():
        if time.time() - last_bucket_end >= 30:
            # Reset peak RSS for the next time bucket.
            last_bucket_end = time.time()
            bucket_peak = 0
        peak_rss_bytes = max(bucket_peak, proc.memory_info().rss)
        metrics_utils.SKY_APISERVER_PROCESS_PEAK_RSS.labels(
            pid=pid, type=process_type).set(peak_rss_bytes)
        ctimes = proc.cpu_times()
        metrics_utils.SKY_APISERVER_PROCESS_CPU_TOTAL.labels(pid=pid,
                                                             type=process_type,
                                                             mode='user').set(
                                                                 ctimes.user)
        metrics_utils.SKY_APISERVER_PROCESS_CPU_TOTAL.labels(pid=pid,
                                                             type=process_type,
                                                             mode='system').set(
                                                                 ctimes.system)
        time.sleep(1)
