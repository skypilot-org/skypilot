"""Instrumentation for the API server."""

import asyncio
import atexit
import glob
import multiprocessing
import os
import re
import threading
import time
from typing import Dict, List, Optional, Set, Tuple

import fastapi
from prometheus_client import core as prom_core
from prometheus_client import generate_latest
from prometheus_client import multiprocess
import prometheus_client as prom
import psutil
import starlette.middleware.base
import uvicorn

from sky import core
from sky import global_user_state
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import kubernetes as kubernetes_adaptor
from sky.metrics import utils as metrics_utils
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


_BURN_RATE_COLLECTOR = BurnRateCollector()

try:
    prom.REGISTRY.register(_BURN_RATE_COLLECTOR)  # for non-multiprocess
except ValueError:
    pass

# Collectors registered by plugins at runtime.
_plugin_collectors: list = []


def register_plugin_collector(collector):
    """Register a custom Prometheus collector from a plugin."""
    _plugin_collectors.append(collector)
    try:
        prom.REGISTRY.register(collector)
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
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._last_scrape_time = 0.0
        self._cache_ttl = _COLLECTOR_CACHE_TTL_SECONDS
        # List of (workspace, user_hash, cloud, status, count) tuples.
        self._cached_rows: list = []

    def _refresh(self):
        # pylint: disable=import-outside-toplevel
        from sky.jobs import state as managed_job_state
        self._cached_rows = (
            managed_job_state.get_status_counts_by_workspace_user_cloud())

    def describe(self):
        yield prom_core.GaugeMetricFamily(
            'sky_managed_jobs_count', ('Current count of managed job tasks by '
                                       'workspace, user, status, and cloud.'),
            labels=['workspace', 'user', 'status', 'cloud'])

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


_WORKSPACE_USAGE_COLLECTOR = WorkspaceUsageCollector()

try:
    prom.REGISTRY.register(_WORKSPACE_USAGE_COLLECTOR)
except ValueError:
    pass

_MANAGED_JOBS_COLLECTOR: Optional[ManagedJobsCollector] = None


def maybe_register_managed_jobs_collector():
    """Register the managed jobs collector if in consolidation mode.

    Only consolidation mode has the managed jobs DB co-located with the
    API server. In remote SSH/gRPC modes the DB lives on the controller
    cluster and is not directly accessible.
    """
    global _MANAGED_JOBS_COLLECTOR
    # pylint: disable=import-outside-toplevel
    from sky.jobs import utils as managed_job_utils
    if not managed_job_utils.is_consolidation_mode():
        return
    _MANAGED_JOBS_COLLECTOR = ManagedJobsCollector()
    try:
        prom.REGISTRY.register(_MANAGED_JOBS_COLLECTOR)
    except ValueError:
        pass


metrics_app = fastapi.FastAPI()


# Serve /metrics in dedicated thread to avoid blocking the event loop
# of metrics server.
@metrics_app.get('/metrics')
def metrics() -> fastapi.Response:
    """Expose aggregated Prometheus metrics from all worker processes."""
    if os.environ.get('PROMETHEUS_MULTIPROC_DIR'):
        # In multiprocess mode, we need to collect metrics from all processes.
        registry = prom.CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        registry.register(_BURN_RATE_COLLECTOR)
        registry.register(_WORKSPACE_USAGE_COLLECTOR)
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
            f'({stats.summary()}); kubectl port-forward + /federate exceeded '
            f'the per-context budget; series for this cluster are omitted from '
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
    # (see /dashboard_config local_contexts). Detection may probe each
    # uncached context once; run in a thread to keep the event loop free.
    _, remote_contexts = await asyncio.to_thread(
        metrics_utils.split_local_remote_contexts, contexts)
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

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for i, result in enumerate(results):
        _handle_federation_result(remote_contexts[i], 'gpu-metrics', result,
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

    # Same local-context handling as /gpu-metrics above.
    _, remote_contexts = await asyncio.to_thread(
        metrics_utils.split_local_remote_contexts, contexts)
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


def _get_status_code_group(status_code: int) -> str:
    """Group status codes into classes (2xx, 5xx) to reduce cardinality."""
    return f'{status_code // 100}xx'


def _is_streaming_api(path: str) -> bool:
    """Check if the path is a streaming API."""
    path = path.rstrip('/')
    return path.endswith('/logs') or path.endswith('/api/stream')


def _get_user_label(request: fastapi.Request) -> str:
    """Extract user label from request for metrics.

    Returns the authenticated user's name if available, otherwise 'anonymous'.
    """
    auth_user = getattr(request.state, 'auth_user', None)
    if auth_user is not None and auth_user.name:
        return auth_user.name
    return 'anonymous'


class PrometheusMiddleware(starlette.middleware.base.BaseHTTPMiddleware):
    """Middleware to collect Prometheus metrics for HTTP requests."""

    async def dispatch(self, request: fastapi.Request, call_next):
        path = request.url.path
        logger.debug(f'PROM Middleware Request: {request}, {request.url.path}')
        streaming = _is_streaming_api(path)
        if not streaming:
            # Exclude streaming APIs, the duration is not meaningful.
            # TODO(aylei): measure the duration of async execution instead.
            start_time = time.time()
        method = request.method
        status_code_group = ''

        try:
            response = await call_next(request)
            status_code_group = _get_status_code_group(response.status_code)
        except Exception:  # pylint: disable=broad-except
            status_code_group = '5xx'
            raise
        finally:
            metrics_utils.SKY_APISERVER_REQUESTS_TOTAL.labels(
                path=path, method=method, status=status_code_group).inc()
            # Record per-user metrics
            user = _get_user_label(request)
            metrics_utils.SKY_APISERVER_REQUESTS_BY_USER_TOTAL.labels(
                user=user, method=method, status=status_code_group).inc()
            if not streaming:
                duration = time.time() - start_time
                metrics_utils.SKY_APISERVER_REQUEST_DURATION_SECONDS.labels(
                    path=path, method=method,
                    status=status_code_group).observe(duration)

        return response


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
