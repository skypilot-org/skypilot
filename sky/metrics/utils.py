"""Utilities for processing GPU metrics from Kubernetes clusters."""
import asyncio
import contextlib
import functools
import os
import re
import select
import subprocess
import threading
import time
from typing import Dict, List, Optional, Tuple

import httpx
import prometheus_client as prom

from sky import sky_logging
from sky import skypilot_config
from sky.skylet import constants
from sky.utils import common_utils

_SELECT_TIMEOUT = 1
_SELECT_BUFFER_SIZE = 4096

_KB = 2**10
_MB = 2**20
_MEM_BUCKETS = [
    _KB,
    256 * _KB,
    512 * _KB,
    _MB,
    2 * _MB,
    4 * _MB,
    8 * _MB,
    16 * _MB,
    32 * _MB,
    64 * _MB,
    128 * _MB,
    256 * _MB,
    float('inf'),
]

logger = sky_logging.init_logger(__name__)

# Whether the metrics are enabled, cannot be changed at runtime.
METRICS_ENABLED = os.environ.get(constants.ENV_VAR_SERVER_METRICS_ENABLED,
                                 'false').lower() == 'true'

# Default Prometheus deployment that each context's metrics are federated
# from. Overridable via the `metrics.prometheus` server config section.
_DEFAULT_PROMETHEUS_NAMESPACE = 'skypilot'
_DEFAULT_PROMETHEUS_SERVICE = 'skypilot-prometheus-server'
_DEFAULT_PROMETHEUS_SERVICE_PORT = 80

# Namespace whose UID is used as the cluster identity for local-context
# detection. kube-system exists in every cluster, cannot be deleted, and
# its UID is stable for the lifetime of the cluster, making it the
# de-facto cluster identifier. Using a fixed, well-known name also lets
# RBAC be pinned with `resourceNames: ["kube-system"]`.
_CLUSTER_IDENTITY_NAMESPACE = 'kube-system'

# Timeout for the namespace UID probes used by local-context detection.
# Must fit within the per-context timeout budget in sky/server/metrics.py
# (_PER_CONTEXT_TIMEOUT_SECONDS) together with the actual metrics request.
_NAMESPACE_PROBE_TIMEOUT_SECONDS = 5

# TTL for the process-level local-context detection cache. A TTL (instead
# of caching forever) covers the rare case where a kubeconfig context name
# is remapped to a different cluster at runtime; a stale entry self-heals
# within this window. Detection results must NOT live in the request-level
# cache: gpu_metrics() calls annotations.clear_request_level_cache() on
# every scrape, which would turn the probe into per-scrape overhead.
_LOCAL_CONTEXT_CACHE_TTL_SECONDS = 60 * 60

# Retry interval for inconclusive detections (probe error, no in-cluster
# identity). Kept much shorter than the cache TTL: a transient failure
# (RBAC not yet applied, API server hiccup) must not pin a local context
# as remote — and self-federation running — for a full TTL window.
_LOCAL_CONTEXT_FAILURE_RETRY_SECONDS = 60

# Process-level cache: context name -> (is_local, entry expiry time).
_local_context_cache: Dict[str, Tuple[bool, float]] = {}
_local_context_cache_lock = threading.Lock()

# In-flight probe threads, one per context at most (context name -> thread),
# guarded by _local_context_cache_lock. Detection runs in a background
# thread because a probe can hang indefinitely (kubeconfig exec credential
# plugins run as subprocesses with no timeout, outside _request_timeout),
# and a hung thread cannot be reaped in Python. Deduping by context bounds
# the leak to one stranded thread per distinct context instead of one per
# scrape cycle; probe threads are daemon so a stuck one never blocks
# interpreter shutdown. Verdicts are read back through _local_context_cache.
_local_context_probes: Dict[str, threading.Thread] = {}

# UID of the kube-system namespace as seen through the in-cluster
# credentials, i.e. the identity of the cluster this API server runs in.
# Only successful reads are cached; failures are retried on the next
# detection attempt.
_in_cluster_identity_uid: Optional[str] = None
_in_cluster_identity_uid_lock = threading.Lock()

# Latency buckets shared by histograms that observe seconds. Kept compact to
# bound time-series cardinality (each labeled series multiplies by len(buckets))
# while preserving the 1000s upper bound for slow-call precision.
_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30,
                    60, 120, 300, 600, 1000, float('inf'))

# Time spent processing a piece of code, refer to time_it().
SKY_APISERVER_CODE_DURATION_SECONDS = prom.Histogram(
    'sky_apiserver_code_duration_seconds',
    'Time spent processing code',
    ['name', 'group'],
    buckets=_LATENCY_BUCKETS,
)

# Total number of API server requests, grouped by path, method, and status.
# TODO(kevinzwang): Panels that only need method/status grouping should migrate
# to SKY_APISERVER_REQUESTS_BY_USER_TOTAL (aggregated across users). Remove
# this metric after v0.14.0 if all consumers have migrated.
SKY_APISERVER_REQUESTS_TOTAL = prom.Counter(
    'sky_apiserver_requests_total',
    'Total number of API server requests',
    ['path', 'method', 'status'],
)

# Total number of API server requests per user.
# This is a separate metric to avoid high cardinality in the primary metric.
SKY_APISERVER_REQUESTS_BY_USER_TOTAL = prom.Counter(
    'sky_apiserver_requests_by_user_total',
    'Total number of API server requests per user',
    ['user', 'method', 'status'],
)

# Time spent processing API server requests, grouped by path, method, and
# status.
SKY_APISERVER_REQUEST_DURATION_SECONDS = prom.Histogram(
    'sky_apiserver_request_duration_seconds',
    'Time spent processing API server requests',
    ['path', 'method', 'status'],
    buckets=_LATENCY_BUCKETS,
)

# Aggregated across all worker processes — the prometheus_client multiprocess
# collector sums per-process histograms automatically. For per-process
# visibility, see SKY_APISERVER_EVENT_LOOP_LAG_MAX_SECONDS below.
SKY_APISERVER_EVENT_LOOP_LAG_SECONDS = prom.Histogram(
    'sky_apiserver_event_loop_lag_seconds',
    'Scheduling delay of the server event loop',
    buckets=_LATENCY_BUCKETS,
)

# Per-process peak event loop lag observed in the most recent 30s tumbling
# window. Kept as a low-cardinality companion to the (pid-less) lag histogram
# so operators can still attribute spikes to a specific worker.
SKY_APISERVER_EVENT_LOOP_LAG_MAX_SECONDS = prom.Gauge(
    'sky_apiserver_event_loop_lag_max_seconds',
    'Peak event loop lag in the last 30 seconds for each process',
    ['pid'],
    multiprocess_mode='liveall',
)

SKY_APISERVER_WEBSOCKET_CONNECTIONS = prom.Gauge(
    'sky_apiserver_websocket_connections',
    'Number of websocket connections',
    ['pid'],
    multiprocess_mode='livesum',
)

SKY_APISERVER_WEBSOCKET_CLOSED_TOTAL = prom.Counter(
    'sky_apiserver_websocket_closed_total',
    'Number of websocket closed',
    ['pid', 'reason'],
)

# The number of execution starts in each worker process, we do not record
# histogram here as the duration has been measured in
# SKY_APISERVER_CODE_DURATION_SECONDS without the worker label (process id).
# Recording histogram WITH worker label will cause high cardinality.
SKY_APISERVER_PROCESS_EXECUTION_START_TOTAL = prom.Counter(
    'sky_apiserver_process_execution_start_total',
    'Total number of execution starts in each worker process',
    ['request', 'pid'],
)

SKY_APISERVER_PROCESS_PEAK_RSS = prom.Gauge(
    'sky_apiserver_process_peak_rss',
    'Peak RSS we saw in each process in last 30 seconds',
    ['pid', 'type'],
)

SKY_APISERVER_PROCESS_CPU_TOTAL = prom.Gauge(
    'sky_apiserver_process_cpu_total',
    'Total CPU times a worker process has been running',
    ['pid', 'type', 'mode'],
)

SKY_APISERVER_REQUEST_MEMORY_USAGE_BYTES = prom.Histogram(
    'sky_apiserver_request_memory_usage_bytes',
    'Peak memory usage of requests', ['name'],
    buckets=_MEM_BUCKETS)

SKY_APISERVER_REQUEST_RSS_INCR_BYTES = prom.Histogram(
    'sky_apiserver_request_rss_incr_bytes',
    'RSS increment after requests', ['name'],
    buckets=_MEM_BUCKETS)

SKY_APISERVER_WEBSOCKET_SSH_LATENCY_SECONDS = prom.Histogram(
    'sky_apiserver_websocket_ssh_latency_seconds',
    ('Time taken for ssh message to go from client to API server and back'
     'to the client. This does not include: latency to reach the pod, '
     'overhead from sending through the k8s port-forward tunnel, or '
     'ssh server lag on the destination pod.'),
    buckets=_LATENCY_BUCKETS,
)

SKY_APISERVER_LONG_EXECUTORS = prom.Gauge(
    'sky_apiserver_long_executors',
    'Total number of long-running request executors in the API server',
)

SKY_APISERVER_SHORT_EXECUTORS = prom.Gauge(
    'sky_apiserver_short_executors',
    'Total number of short-running request executors in the API server',
)

# Time a request spends waiting in the task queue (from creation to dequeue).
SKY_APISERVER_QUEUE_WAIT_SECONDS = prom.Histogram(
    'sky_apiserver_queue_wait_seconds',
    'Time a request spent waiting in the task queue before execution',
    ['schedule_type'],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0,
             120.0, 300.0, 600.0, float('inf')),
)

# --- Managed Jobs Metrics ---

# Per-controller-process gauges (consolidation mode only).
# These are updated in ControllerManager.monitor_loop().
SKY_MANAGED_JOBS_CONTROLLER_STARTING_COUNT = prom.Gauge(
    'sky_managed_jobs_controller_starting_count',
    'Number of jobs currently launching on this controller process',
    ['pid'],
    multiprocess_mode='liveall',
)

SKY_MANAGED_JOBS_CONTROLLER_RUNNING_COUNT = prom.Gauge(
    'sky_managed_jobs_controller_running_count',
    'Number of running job tasks on this controller process',
    ['pid'],
    multiprocess_mode='liveall',
)

SKY_MANAGED_JOBS_CONTROLLER_MAX_JOBS = prom.Gauge(
    'sky_managed_jobs_controller_max_jobs',
    'Computed max jobs for this controller process',
    ['pid'],
    multiprocess_mode='liveall',
)

# Static limit gauge, set in ControllerManager.monitor_loop() alongside
# other per-controller metrics so it stays current if config hot-reload
# is supported in the future.
# Uses pid label + liveall so only controller processes that explicitly call
# .labels(pid=...).set() produce a value, avoiding phantom 0.0 entries from
# API server worker processes that merely import this module.
SKY_MANAGED_JOBS_LIMIT_LAUNCHES_PER_WORKER = prom.Gauge(
    'sky_managed_jobs_limit_launches_per_worker',
    'Max concurrent launches per worker',
    ['pid'],
    multiprocess_mode='liveall',
)

# --- Metrics federation (per remote Kubernetes context) ---
# The /gpu-metrics and /endpoints-metrics endpoints federate each remote
# compute context's Prometheus via a kubectl port-forward + /federate scrape.
# These instruments make that path debuggable: latency split into the
# port-forward setup vs the federate request (so a slow tunnel is
# distinguishable from a large/slow scrape), the decompressed payload size,
# and a per-context outcome counter so a cluster that is silently timing out
# (and thus dropping out of the federated output) is alertable.
#
# `context` cardinality is bounded by the number of allowed compute clusters
# (single digits in practice), so it is safe as a label. `route` separates the
# two federation endpoints that share send_metrics_request_with_port_forward.
SKY_APISERVER_FEDERATION_DURATION_SECONDS = prom.Histogram(
    'sky_apiserver_metrics_federation_duration_seconds',
    'Time to federate metrics from a remote Kubernetes context, by phase '
    '(port_forward: kubectl port-forward setup; federate: the /federate HTTP '
    'request including transfer + decompression)',
    ['context', 'route', 'phase'],
    buckets=_LATENCY_BUCKETS,
)

# Decompressed size of the /federate response body. Uses the byte buckets
# (top finite bucket 256MiB) so 5K-GPU bodies (tens of MiB) are resolvable.
SKY_APISERVER_FEDERATION_PAYLOAD_BYTES = prom.Histogram(
    'sky_apiserver_metrics_federation_payload_bytes',
    'Decompressed size of the federate response body per remote context',
    ['context', 'route'],
    buckets=_MEM_BUCKETS,
)

# End-to-end outcome per context+route: success | timeout | error. Alert on a
# rising timeout rate per context to catch the silent-drop failure mode.
SKY_APISERVER_FEDERATION_TOTAL = prom.Counter(
    'sky_apiserver_metrics_federation_total',
    'Count of metrics federation attempts per remote context and outcome',
    ['context', 'route', 'outcome'],
)


def record_federation_phase(context: str, route: str, phase: str,
                            seconds: float) -> None:
    """Records the duration of one federation phase (non-blocking, best-effort).

    Gated by METRICS_ENABLED to match time_it(); a no-op otherwise. Pure
    in-memory observe() with no I/O or awaits, so it is safe to call from a
    finally block on a cancelled/timed-out task without risking a hang.
    """
    if METRICS_ENABLED:
        SKY_APISERVER_FEDERATION_DURATION_SECONDS.labels(
            context=context, route=route, phase=phase).observe(seconds)


def record_federation_payload(context: str, route: str, num_bytes: int) -> None:
    """Records the decompressed federate payload size (non-blocking)."""
    if METRICS_ENABLED:
        SKY_APISERVER_FEDERATION_PAYLOAD_BYTES.labels(
            context=context, route=route).observe(num_bytes)


def record_federation_outcome(context: str, route: str, outcome: str) -> None:
    """Increments the per-context federation outcome counter (non-blocking)."""
    if METRICS_ENABLED:
        SKY_APISERVER_FEDERATION_TOTAL.labels(context=context,
                                              route=route,
                                              outcome=outcome).inc()


class FederationStats:
    """Mutable per-context timing/size record for one federation attempt.

    send_metrics_request_with_port_forward() fills this in phase-by-phase. The
    caller (the /gpu-metrics or /endpoints-metrics gather loop) holds a
    reference and reads it when logging the result — crucially, this still
    works when the attempt is cancelled by asyncio.wait_for(): the fields
    written before the timeout (e.g. a completed port-forward) are preserved,
    so the timeout log can show exactly how far the attempt got.
    """

    def __init__(self) -> None:
        self.port_forward_seconds: Optional[float] = None
        self.federate_seconds: Optional[float] = None
        self.body_bytes: Optional[int] = None
        self.wire_bytes: Optional[int] = None
        self.content_encoding: Optional[str] = None

    def summary(self) -> str:
        """A compact 'port_forward=..s, federate=..' breakdown for logs.

        'incomplete' marks a phase that did not finish (the key signal on a
        timeout: which phase blew the budget). This runs inside log calls, so
        it must never raise: the byte fields are formatted defensively even
        though federate_seconds is assigned last (so in practice they are
        always set whenever federate_seconds is).
        """
        if self.port_forward_seconds is not None:
            pf = f'{self.port_forward_seconds:.2f}s'
        else:
            pf = 'incomplete'
        if self.federate_seconds is not None:
            body = (f'{self.body_bytes / _MB:.1f}MiB'
                    if self.body_bytes is not None else 'unknown')
            wire = (f'{self.wire_bytes / _MB:.2f}MiB'
                    if self.wire_bytes is not None else 'unknown')
            fed = (f'{self.federate_seconds:.2f}s, body={body}, '
                   f'wire={wire}, enc={self.content_encoding}')
        else:
            fed = 'incomplete'
        return f'port_forward={pf}, federate={fed}'


@contextlib.contextmanager
def time_it(name: str, group: str = 'default'):
    """Context manager to measure and record code execution duration."""
    if not METRICS_ENABLED:
        yield
    else:
        start_time = time.time()
        try:
            yield
        finally:
            duration = time.time() - start_time
            SKY_APISERVER_CODE_DURATION_SECONDS.labels(
                name=name, group=group).observe(duration)


def time_me(func):
    """Measure the duration of decorated function."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not METRICS_ENABLED:
            return func(*args, **kwargs)
        name = f'{func.__module__}/{func.__name__}'
        with time_it(name, group='function'):
            return func(*args, **kwargs)

    return wrapper


def time_me_async(func):
    """Measure the duration of decorated async function."""

    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs):
        if not METRICS_ENABLED:
            return await func(*args, **kwargs)
        name = f'{func.__module__}/{func.__name__}'
        with time_it(name, group='function'):
            return await func(*args, **kwargs)

    return async_wrapper


def _get_prometheus_target() -> Tuple[str, str, int]:
    """(namespace, service, port) of the Prometheus to federate from.

    Reads the `metrics.prometheus` server config section, falling back to
    the defaults that match the SkyPilot Helm chart. The metrics routes
    reload the config on every scrape, so changes are picked up at runtime.
    """
    namespace = skypilot_config.get_nested(
        ('metrics', 'prometheus', 'namespace'), _DEFAULT_PROMETHEUS_NAMESPACE)
    service = skypilot_config.get_nested(('metrics', 'prometheus', 'service'),
                                         _DEFAULT_PROMETHEUS_SERVICE)
    port = skypilot_config.get_nested(('metrics', 'prometheus', 'port'),
                                      _DEFAULT_PROMETHEUS_SERVICE_PORT)
    return namespace, service, port


def _read_cluster_identity_uid(core) -> Optional[str]:
    """Reads the kube-system namespace UID through the given CoreV1Api."""
    namespace = core.read_namespace(
        _CLUSTER_IDENTITY_NAMESPACE,
        _request_timeout=_NAMESPACE_PROBE_TIMEOUT_SECONDS)
    if namespace is None or namespace.metadata is None:
        return None
    return namespace.metadata.uid


def _get_in_cluster_identity_uid() -> Optional[str]:
    """UID of kube-system in the cluster the API server runs in.

    Read through the in-cluster credentials, it acts as the identity
    anchor for local-context detection: a kubeconfig context that
    resolves kube-system to the same UID points at the cluster this API
    server runs in.

    Returns None when not running in a pod or when kube-system cannot be
    read (e.g. missing RBAC); in that case detection is disabled and
    every named context is treated as remote.
    """
    global _in_cluster_identity_uid
    with _in_cluster_identity_uid_lock:
        if _in_cluster_identity_uid is not None:
            return _in_cluster_identity_uid
    # Import lazily to avoid circular import (metrics -> provision ->
    # clouds -> metrics).
    # pylint: disable=import-outside-toplevel
    from sky.adaptors import kubernetes as kubernetes_adaptors
    try:
        core = kubernetes_adaptors.core_api(
            kubernetes_adaptors.in_cluster_context_name())
        uid = _read_cluster_identity_uid(core)
        if not uid:
            logger.debug(
                f'The {_CLUSTER_IDENTITY_NAMESPACE!r} namespace was read '
                f'through in-cluster credentials but carries no UID; '
                f'local-context detection is disabled until the next '
                f'attempt.')
            return None
    except kubernetes_adaptors.config_exception():
        # Not running inside a Kubernetes pod: there are no in-cluster
        # credentials, hence no local cluster to detect.
        logger.debug('No in-cluster credentials; local-context detection '
                     'is disabled.')
        return None
    except kubernetes_adaptors.api_exception() as e:
        status = getattr(e, 'status', None)
        if status in (401, 403):
            logger.warning(
                f'The in-cluster service account is not allowed to read '
                f'the {_CLUSTER_IDENTITY_NAMESPACE!r} namespace '
                f'(status={status}); local-context detection is disabled '
                f'and only the in-cluster context will be treated as '
                f'local. Grant `get` on the '
                f'{_CLUSTER_IDENTITY_NAMESPACE!r} namespace to the API '
                f'server service account (included in the Helm chart '
                f'default rbac.clusterRules) to enable detection: '
                f'{common_utils.format_exception(e)}')
        else:
            logger.warning(
                f'Failed to read the {_CLUSTER_IDENTITY_NAMESPACE!r} '
                f'namespace through in-cluster credentials '
                f'(status={status}); local-context detection is disabled '
                f'until the next attempt: '
                f'{common_utils.format_exception(e)}')
        return None
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(
            f'Failed to read the {_CLUSTER_IDENTITY_NAMESPACE!r} namespace '
            f'through in-cluster credentials; local-context detection is '
            f'disabled until the next attempt and only the in-cluster '
            f'context will be treated as local: '
            f'{common_utils.format_exception(e)}')
        return None
    with _in_cluster_identity_uid_lock:
        _in_cluster_identity_uid = uid
    return _in_cluster_identity_uid


def is_local_context(context: str) -> bool:
    """Whether a kubeconfig context points at the API server's own cluster.

    The in-cluster context is local by construction (its credentials are
    the pod's own service account). Named contexts are probed: local iff
    the kube-system UID read through the context's credentials matches
    the API server's own cluster identity. Any failure (no identity
    anchor, 403, timeout) degrades to remote — the safe answer, since
    the local cluster stays reachable via the in-cluster context.
    Conclusive results are cached at process level for
    _LOCAL_CONTEXT_CACHE_TTL_SECONDS; inconclusive ones only for
    _LOCAL_CONTEXT_FAILURE_RETRY_SECONDS so they are retried soon.
    """
    # Import lazily to avoid circular import (metrics -> provision ->
    # clouds -> metrics).
    # pylint: disable=import-outside-toplevel
    from sky.adaptors import kubernetes as kubernetes_adaptors
    if context == kubernetes_adaptors.in_cluster_context_name():
        return True
    now = time.time()
    with _local_context_cache_lock:
        cached = _local_context_cache.get(context)
        if cached is not None:
            is_local, expires_at = cached
            if now < expires_at:
                return is_local
    is_local = False
    conclusive = False
    own_uid = _get_in_cluster_identity_uid()
    if own_uid is not None:
        try:
            core = kubernetes_adaptors.core_api(context)
            probed_uid = _read_cluster_identity_uid(core)
            conclusive = bool(probed_uid)
            is_local = conclusive and probed_uid == own_uid
        except Exception as e:  # pylint: disable=broad-except
            status = getattr(e, 'status', None)
            status_str = f' (status={status})' if status is not None else ''
            logger.warning(
                f'Failed to probe the {_CLUSTER_IDENTITY_NAMESPACE!r} '
                f'namespace through context {context!r}{status_str}; '
                f'assuming the context is remote: '
                f'{common_utils.format_exception(e)}')
    ttl = (_LOCAL_CONTEXT_CACHE_TTL_SECONDS
           if conclusive else _LOCAL_CONTEXT_FAILURE_RETRY_SECONDS)
    with _local_context_cache_lock:
        _local_context_cache[context] = (is_local, time.time() + ttl)
    return is_local


# Overall budget for one split_local_remote_contexts() call. The
# per-probe _request_timeout only bounds the namespace HTTP call;
# kubeconfig loading, exec credential plugins, or DNS can still hang a
# probe, and that must not hold up the metrics/dashboard response.
_DETECTION_TIMEOUT_SECONDS = _NAMESPACE_PROBE_TIMEOUT_SECONDS + 5


def _cached_local_verdict(context: str) -> Optional[bool]:
    """Cached is_local_context verdict, or None if absent/expired.

    Never probes, so it is safe to call from latency-sensitive paths.
    """
    # Import lazily to avoid circular import (metrics -> provision ->
    # clouds -> metrics).
    # pylint: disable=import-outside-toplevel
    from sky.adaptors import kubernetes as kubernetes_adaptors
    if context == kubernetes_adaptors.in_cluster_context_name():
        return True
    with _local_context_cache_lock:
        cached = _local_context_cache.get(context)
        if cached is not None:
            is_local, expires_at = cached
            if time.time() < expires_at:
                return is_local
    return None


def _probe_local_context(context: str) -> None:
    """Thread target: run detection, which caches its own verdict."""
    try:
        is_local_context(context)
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Local-context detection failed for {context!r}; '
                       f'treating it as remote: '
                       f'{common_utils.format_exception(e)}')


def _ensure_probe(context: str) -> threading.Thread:
    """Returns a live probe thread for context, starting one if needed.

    At most one probe runs per context: a concurrent (or repeated) call
    for the same context joins the existing thread instead of spawning a
    new one, so a hung probe costs one stranded thread total rather than
    one per scrape cycle. A finished thread is replaced, so the next call
    after cache expiry re-probes through a fresh thread.
    """
    with _local_context_cache_lock:
        thread = _local_context_probes.get(context)
        if thread is None or not thread.is_alive():
            # Daemon: probes are read-only and may hang indefinitely in
            # kubeconfig loading / exec credential plugins; they must not
            # block interpreter shutdown.
            thread = threading.Thread(target=_probe_local_context,
                                      args=(context,),
                                      name=f'local-context-probe-{context}',
                                      daemon=True)
            _local_context_probes[context] = thread
            thread.start()
        return thread


def split_local_remote_contexts(
        contexts: List[str]) -> Tuple[List[str], List[str]]:
    """Partitions contexts into (local, remote) via is_local_context().

    Shared by the federation routes and /dashboard_config so both agree
    on which contexts point at the API server's own cluster. Cached
    verdicts are answered inline (no thread). Only uncached contexts are
    probed, each in a deduped daemon thread, so the first call costs
    roughly one probe timeout rather than one per context and the warm
    path spawns no threads at all. A context whose probe does not finish
    within _DETECTION_TIMEOUT_SECONDS is treated as remote for this call
    (the safe answer); a straggler still populates the cache when it
    eventually finishes, and a later call reads it back. Blocking — call
    from a thread in async code.
    """
    if not contexts:
        return [], []
    # Only uncached contexts need a probe; an in-flight probe is joined
    # rather than duplicated.
    pending = {
        context: _ensure_probe(context)
        for context in contexts
        if _cached_local_verdict(context) is None
    }
    deadline = time.monotonic() + _DETECTION_TIMEOUT_SECONDS
    for thread in pending.values():
        thread.join(timeout=max(0.0, deadline - time.monotonic()))
    local: List[str] = []
    remote: List[str] = []
    for context in contexts:
        verdict = _cached_local_verdict(context)
        if verdict is None:
            logger.warning(
                f'Local-context detection for {context!r} did not finish '
                f'within {_DETECTION_TIMEOUT_SECONDS}s; treating it as '
                f'remote for this request.')
        (local if verdict else remote).append(context)
    return local, remote


def start_svc_port_forward(context: str, namespace: str, service: str,
                           service_port: int) -> Tuple[subprocess.Popen, int]:
    """Starts a port forward to a service in a Kubernetes cluster.
    Args:
        context: Kubernetes context name
        namespace: Namespace where the service is located
        service: Service name to port forward to
        service_port: Port on the service to forward to
    Returns:
        Tuple of (subprocess.Popen process, local_port assigned)
    Raises:
        RuntimeError: If port forward fails to start
    """
    # Must be well under the per-context timeout in
    # metrics.py (_PER_CONTEXT_TIMEOUT_SECONDS) to leave
    # time for the HTTP request and cleanup.
    start_port_forward_timeout = 5
    terminate_port_forward_timeout = 5  # 5 second timeout

    # Use ':service_port' to let kubectl choose the local port
    cmd = [
        'kubectl', '--context', context, '-n', namespace, 'port-forward',
        f'service/{service}', f':{service_port}'
    ]

    env = os.environ.copy()
    # Use SkyPilot's kubeconfig discovery which respects KUBECONFIG env var
    # (set by credential manager plugin) and falls back to ~/.kube/config.
    # Always set explicitly so subprocess gets the resolved paths even if
    # env var was modified after os.environ was last copied.
    # Import lazily to avoid circular import (metrics -> provision -> clouds
    # -> metrics).
    # pylint: disable=import-outside-toplevel
    from sky.adaptors import kubernetes as kubernetes_adaptors
    from sky.provision.kubernetes import utils as kubernetes_utils
    kubeconfig_paths = kubernetes_utils.get_kubeconfig_paths()
    env['KUBECONFIG'] = kubernetes_adaptors.ENV_KUBECONFIG_PATH_SEPARATOR.join(
        kubeconfig_paths)

    port_forward_process = None
    port_forward_exit = False
    local_port = None
    poller = None
    fd = None

    try:
        # start the port forward process
        port_forward_process = subprocess.Popen(cmd,
                                                stdout=subprocess.PIPE,
                                                stderr=subprocess.STDOUT,
                                                text=True,
                                                env=env)

        # Use poll() instead of select() to avoid FD_SETSIZE limit
        poller = select.poll()
        assert port_forward_process.stdout is not None
        fd = port_forward_process.stdout.fileno()
        poller.register(fd, select.POLLIN)

        start_time = time.time()
        buffer = ''
        # wait for the port forward to start and extract the local port
        while time.time() - start_time < start_port_forward_timeout:
            if port_forward_process.poll() is not None:
                # port forward process has terminated
                if port_forward_process.returncode != 0:
                    port_forward_exit = True
                break

            # Wait up to 1000ms for data to be available without blocking
            # poll() takes timeout in milliseconds
            events = poller.poll(_SELECT_TIMEOUT * 1000)

            if events:
                # Read available bytes from the FD without blocking
                raw = os.read(fd, _SELECT_BUFFER_SIZE)
                chunk = raw.decode(errors='ignore')
                buffer += chunk
                match = re.search(r'Forwarding from 127\.0\.0\.1:(\d+)', buffer)
                if match:
                    local_port = int(match.group(1))
                    break

            # sleep for 100ms to avoid busy-waiting
            time.sleep(0.1)
    except BaseException:  # pylint: disable=broad-exception-caught
        if port_forward_process:
            stop_svc_port_forward(port_forward_process,
                                  timeout=terminate_port_forward_timeout)
        raise
    finally:
        if poller is not None and fd is not None:
            try:
                poller.unregister(fd)
            except (OSError, ValueError):
                # FD may already be unregistered or invalid
                pass
    if port_forward_exit:
        raise RuntimeError(f'Port forward failed for service {service} in '
                           f'namespace {namespace} on context {context}')
    if local_port is None:
        try:
            if port_forward_process:
                stop_svc_port_forward(port_forward_process,
                                      timeout=terminate_port_forward_timeout)
        finally:
            raise RuntimeError(
                f'Failed to extract local port for service {service} in '
                f'namespace {namespace} on context {context}')

    return port_forward_process, local_port


def stop_svc_port_forward(port_forward_process: subprocess.Popen,
                          timeout: int = 5) -> None:
    """Stops a port forward to a service in a Kubernetes cluster.
    Args:
        port_forward_process: The subprocess.Popen process to terminate
    """
    try:
        port_forward_process.terminate()
        port_forward_process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        port_forward_process.kill()
        port_forward_process.wait()


async def send_metrics_request_with_port_forward(
        context: str,
        namespace: str,
        service: str,
        service_port: int,
        endpoint_path: str = '/federate',
        match_patterns: Optional[List[str]] = None,
        timeout: float = 30.0,
        route: str = 'gpu-metrics',
        stats: Optional[FederationStats] = None) -> str:
    """Sends a metrics request to a Prometheus endpoint via port forwarding.
    Args:
        context: Kubernetes context name
        namespace: Namespace where the service is located
        service: Service name to port forward to
        service_port: Port on the service to forward to
        endpoint_path: Path to append to the localhost endpoint (e.g.,
            '/federate')
        match_patterns: List of metric patterns to match (for federate
            endpoint)
        timeout: Request timeout in seconds
        route: Federation route label for metrics/logs ('gpu-metrics' or
            'endpoints-metrics'); does not affect the request itself.
        stats: Optional FederationStats filled in phase-by-phase so the caller
            can report the port-forward vs. federate breakdown even if this
            call is cancelled by a timeout. A fresh one is used if not given.
    Returns:
        Response text containing the metrics
    Raises:
        RuntimeError: If port forward or HTTP request fails
    """
    if stats is None:
        stats = FederationStats()
    port_forward_process = None
    # monotonic() so durations are immune to wall-clock adjustments.
    try:
        # Start port forward.
        pf_start = time.monotonic()
        port_forward_process, local_port = await asyncio.to_thread(
            start_svc_port_forward, context, namespace, service, service_port)
        stats.port_forward_seconds = time.monotonic() - pf_start
        record_federation_phase(context, route, 'port_forward',
                                stats.port_forward_seconds)

        # Build endpoint URL
        endpoint = f'http://localhost:{local_port}{endpoint_path}'

        # Make HTTP request. httpx sends `Accept-Encoding: gzip, deflate` by
        # default and transparently decompresses, so a Prometheus that
        # compresses /federate (any version >= 2.0) is already gzipped on the
        # wire; stats captures content-encoding + wire vs. decompressed size so
        # this is observable rather than assumed.
        federate_start = time.monotonic()
        async with httpx.AsyncClient(timeout=timeout) as client:
            if match_patterns:
                # For federate endpoint, add match[] parameters
                params = [('match[]', pattern) for pattern in match_patterns]
                response = await client.get(endpoint, params=params)
            else:
                response = await client.get(endpoint)

            response.raise_for_status()
            text = response.text
            # response.content is the decompressed body (already materialized
            # for a non-streamed request, no await); num_bytes_downloaded is
            # the raw on-wire (compressed) count.
            stats.body_bytes = len(response.content)
            stats.wire_bytes = response.num_bytes_downloaded
            stats.content_encoding = response.headers.get(
                'content-encoding', 'identity')
            # Assign federate_seconds LAST so that (federate_seconds is not
            # None) structurally implies the byte fields are set — summary()
            # relies on this. The body is already materialized above, so
            # measuring the duration here does not lose any transfer time.
            stats.federate_seconds = time.monotonic() - federate_start
            record_federation_phase(context, route, 'federate',
                                    stats.federate_seconds)
            record_federation_payload(context, route, stats.body_bytes)
            return text

    finally:
        # Clean up port forward synchronously to guarantee cleanup
        # even if the task is cancelled by asyncio.wait_for().
        # Using await here would risk CancelledError preventing
        # cleanup.
        if port_forward_process:
            stop_svc_port_forward(port_forward_process)


# Matches an existing `cluster="..."` label token in a metric line's label
# section. Valid exposition escapes quotes inside label values, so the raw
# substring `cluster="` can only start an actual label; the lookbehind keeps
# names like `k8s_cluster` from matching.
_CLUSTER_LABEL_RE = re.compile(r'(?<![A-Za-z0-9_])cluster="')


async def add_cluster_name_label(metrics_text: str, context: str) -> str:
    """Adds a cluster label to each metric line.

    Skips lines that already carry a `cluster` label (stamped by a
    previous federation pass, or labeled at the source): re-stamping
    would produce two `cluster` labels on one line, which is a hard
    duplicate-label error that makes Prometheus roll back the entire
    /gpu-metrics scrape body. This is the safety net for the fail-safe
    path in split_local_remote_contexts(): if a local context is ever
    misdetected as remote (missing RBAC, cold start, transient probe
    error), its already-stamped series are federated back here, and
    skipping keeps them byte-identical to the stored series so ingestion
    collapses them to a no-op instead of poisoning the scrape.

    Args:
        metrics_text: The text containing the metrics
        context: The cluster name
    """
    lines = metrics_text.strip().split('\n')
    modified_lines = []
    already_labeled = 0

    for line in lines:
        # keep comment lines and empty lines as-is
        if line.startswith('#') or not line.strip():
            modified_lines.append(line)
            continue
        # if line is a metric line with labels, add cluster label. rfind
        # for the closing brace: label values may legitimately contain '}'
        # (the sample value/timestamp after the label section cannot).
        brace_start = line.find('{')
        brace_end = line.rfind('}')
        if brace_start != -1 and brace_end > brace_start:
            metric_name = line[:brace_start]
            existing_labels = line[brace_start + 1:brace_end]
            rest_of_line = line[brace_end + 1:]

            if _CLUSTER_LABEL_RE.search(existing_labels):
                # Already attributed; re-stamping would duplicate the
                # cluster label and invalidate the whole scrape body.
                already_labeled += 1
                modified_lines.append(line)
                continue

            if existing_labels:
                new_labels = f'cluster="{context}",{existing_labels}'
            else:
                new_labels = f'cluster="{context}"'

            modified_line = f'{metric_name}{{{new_labels}}}{rest_of_line}'
            modified_lines.append(modified_line)
        else:
            # keep other lines as-is
            modified_lines.append(line)

    if already_labeled:
        # Aggregated (never per line): during real self-federation nearly
        # every line matches. A large fraction usually means this context
        # resolves to the central Prometheus itself, i.e. a local context
        # is being federated as remote (e.g. detection lacks RBAC).
        logger.debug(
            f'{already_labeled}/{len(lines)} series federated from context '
            f'{context!r} already carried a cluster label and were left '
            f'unstamped.')

    return '\n'.join(modified_lines)


# Series federated from each context's Prometheus by /gpu-metrics: DCGM, host
# CPU/memory, kube_pod_labels, and cAdvisor container metrics (per-pod
# CPU/Memory in the Telemetry section joins on (pod, namespace) with
# kube_pod_labels — same join shape the GPU panels use to filter by SkyPilot
# cluster name).
GPU_METRICS_MATCH_PATTERNS = [
    '{__name__=~"node_memory_MemAvailable_bytes|node_memory_MemTotal_bytes|DCGM_.*"}',  # pylint: disable=line-too-long
    'kube_pod_labels',
    'node_cpu_seconds_total{mode="idle"}',
    'container_cpu_usage_seconds_total{container!="",container!="POD"}',
    'container_memory_working_set_bytes{container!="",container!="POD"}',
    # GPU allocation metrics — pod requests + node capacity for nvidia/amd
    # GPUs. Enables cluster-wide % allocated computations.
    # NOTE: kube-state-metrics sanitizes resource names by replacing
    # `.` and `/` with `_`, so the label value is `nvidia_com_gpu` (not
    # `nvidia.com/gpu`). Getting this wrong causes the match to return 0
    # series while the scrape still succeeds.
    'kube_pod_container_resource_requests{resource=~"nvidia_com_gpu|amd_com_gpu"}',  # pylint: disable=line-too-long
    'kube_node_status_allocatable{resource=~"nvidia_com_gpu|amd_com_gpu"}',
]


async def get_metrics_for_context(context: str,
                                  stats: Optional[FederationStats] = None
                                 ) -> str:
    """Get GPU metrics for a single Kubernetes context.
    Args:
        context: Kubernetes context name
        stats: Optional FederationStats populated with the port-forward /
            federate timing + payload size for this context (see the caller's
            timeout logging).
    Returns:
        metrics_text: String containing the metrics
    Raises:
        Exception: If metrics collection fails for any reason
    """
    match_patterns = GPU_METRICS_MATCH_PATTERNS
    prometheus_namespace, prometheus_service, prometheus_port = (
        _get_prometheus_target())

    metrics_text = await send_metrics_request_with_port_forward(
        context=context,
        namespace=prometheus_namespace,
        service=prometheus_service,
        service_port=prometheus_port,
        endpoint_path='/federate',
        match_patterns=match_patterns,
        route='gpu-metrics',
        stats=stats)

    # add cluster name as a label to each metric line
    metrics_text = await add_cluster_name_label(metrics_text, context)

    return metrics_text


# Series federated from each context's Prometheus by /endpoints-metrics: the
# serving engines' native metrics (vllm:* today; future engines append their
# prefixes, e.g. sglang:*), plus the workload kube-state-metrics the
# Autoscaling dashboard plots — Deployment replica counts and the
# autoscaler-managed HPA target threshold. These ride the endpoints route
# (not /gpu-metrics) because they exist solely for endpoint observability.
ENDPOINT_METRICS_MATCH_PATTERNS = [
    '{__name__=~"vllm:.*"}',
    '{__name__=~"kube_deployment_.*|kube_horizontalpodautoscaler_spec_target_metric"}',  # pylint: disable=line-too-long
]


async def get_endpoint_metrics_for_context(
        context: str, stats: Optional[FederationStats] = None) -> str:
    """Get Sky Endpoint serving-engine metrics for a single K8s context.

    Mirrors get_metrics_for_context() but federates the serving engines'
    native Prometheus series instead of DCGM/node metrics. vLLM exports
    ``vllm:*``-prefixed names; future engines append their own prefixes
    here (e.g. ``sglang:*``).

    Args:
        context: Kubernetes context name
        stats: Optional FederationStats populated with the port-forward /
            federate timing + payload size for this context.
    Returns:
        metrics_text: String containing the metrics
    Raises:
        Exception: If metrics collection fails for any reason
    """
    match_patterns = ENDPOINT_METRICS_MATCH_PATTERNS
    prometheus_namespace, prometheus_service, prometheus_port = (
        _get_prometheus_target())

    metrics_text = await send_metrics_request_with_port_forward(
        context=context,
        namespace=prometheus_namespace,
        service=prometheus_service,
        service_port=prometheus_port,
        endpoint_path='/federate',
        match_patterns=match_patterns,
        route='endpoints-metrics',
        stats=stats)

    # add cluster name as a label to each metric line
    metrics_text = await add_cluster_name_label(metrics_text, context)

    return metrics_text
