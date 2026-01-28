"""Instrumentation for the API server."""

import asyncio
import multiprocessing
import os
import threading
import time
from typing import List

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
from sky.metrics import utils as metrics_utils

logger = sky_logging.init_logger(__name__)

_BURN_RATE_UPDATE_INTERVAL_SECONDS = 30
_COST_TIME_HORIZON_SECONDS = 3600


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
        data = generate_latest(registry)
    else:
        data = generate_latest()
    return fastapi.Response(content=data,
                            media_type=prom.CONTENT_TYPE_LATEST,
                            headers={'Cache-Control': 'no-cache'})


@metrics_app.get('/gpu-metrics')
async def gpu_metrics() -> fastapi.Response:
    """Gets the GPU metrics from multiple external k8s clusters"""
    contexts = core.get_all_contexts()
    all_metrics: List[str] = []
    successful_contexts = 0

    remote_contexts = [
        context for context in contexts if context != 'in-cluster'
    ]
    tasks = [
        asyncio.create_task(metrics_utils.get_metrics_for_context(context))
        for context in remote_contexts
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(
                f'Failed to get metrics for context {remote_contexts[i]}: '
                f'{result}')
        elif isinstance(result, BaseException):
            # Avoid changing behavior for non-Exception BaseExceptions
            # like KeyboardInterrupt/SystemExit: re-raise them.
            raise result
        else:
            metrics_text = result
            all_metrics.append(metrics_text)
            successful_contexts += 1

    combined_metrics = '\n\n'.join(all_metrics)

    # Return as plain text for Prometheus compatibility
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
