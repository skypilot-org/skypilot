"""Instrumentation for the API server."""

import asyncio
import multiprocessing
import os
import threading
import time
from typing import List

import fastapi
from prometheus_client import generate_latest
from prometheus_client import multiprocess
import prometheus_client as prom
import psutil
import starlette.middleware.base
import uvicorn

from sky import core
from sky import sky_logging
from sky.metrics import utils as metrics_utils

logger = sky_logging.init_logger(__name__)

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

    tasks = [
        asyncio.create_task(metrics_utils.get_metrics_for_context(context))
        for context in contexts
        if context != 'in-cluster'
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(
                f'Failed to get metrics for context {contexts[i]}: {result}')
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
