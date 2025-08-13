"""Instrumentation for the API server."""

import os
import time

import fastapi
from prometheus_client import generate_latest
from prometheus_client import multiprocess
import prometheus_client as prom
import starlette.middleware.base
import uvicorn

from sky import sky_logging

logger = sky_logging.init_logger(__name__)

# Total number of API server requests, grouped by path, method, and status.
sky_apiserver_requests_total = prom.Counter(
    'sky_apiserver_requests_total',
    'Total number of API server requests',
    ['path', 'method', 'status'],
)

# Time spent processing API server requests, grouped by path, method, and
# status.
sky_apiserver_request_duration_seconds = prom.Histogram(
    'sky_apiserver_request_duration_seconds',
    'Time spent processing API server requests',
    ['path', 'method', 'status'],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
             float('inf')),
)

metrics_app = fastapi.FastAPI()


@metrics_app.get('/metrics')
async def metrics() -> fastapi.Response:
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
        logger.info(f'PROM Middleware Request: {request}, {request.url.path}')
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
            sky_apiserver_requests_total.labels(path=path,
                                                method=method,
                                                status=status_code_group).inc()
            if not streaming:
                duration = time.time() - start_time
                sky_apiserver_request_duration_seconds.labels(
                    path=path, method=method,
                    status=status_code_group).observe(duration)

        return response
