"""Retry helpers for transient cloud-provider API errors.

Long-running SkyPilot processes (notably the managed-jobs controller
monitor loop) poll cloud-provider APIs on a routine schedule to refresh
cluster status. A brief provider-side blip -- for example an HTTP 503
``ServiceUnavailable`` from the Kubernetes API server, or a momentary
network error -- surfaces as ``sky.exceptions.ClusterStatusFetchingError``.
Without a retry, that transient error propagates to the controller
catch-all and marks a perfectly healthy job ``FAILED_CONTROLLER``.

Wrap such a call site with ``with_cloud_api_retries``. It retries on
``ClusterStatusFetchingError`` with exponential backoff so a momentary
outage is absorbed transparently; a sustained outage still raises after
the retry budget is exhausted, so the caller can decide how to surface it.

This is deliberately separate from ``sky.utils.db.retries`` -- a provider
API 503 is not a database error, and the two have different retryable
exception sets.
"""

import logging
import time
from typing import Callable, Tuple, Type, TypeVar

from sky import exceptions
from sky.utils import common_utils

logger = logging.getLogger(__name__)

T = TypeVar('T')

# ``ClusterStatusFetchingError`` is raised by
# ``backend_utils.refresh_cluster_status_handle`` whenever the provider
# status query fails -- it wraps the underlying transient provider error
# (e.g. a Kubernetes ``ApiException`` with HTTP 503).
RETRYABLE_EXCEPTIONS: Tuple[Type[BaseException],
                            ...] = (exceptions.ClusterStatusFetchingError,)

# 5 attempts x exp backoff capped at 5s ~= 10s of backoff sleeps -- covers
# typical brief provider blips (a 503 spike, a short API-server rollout).
# Sustained outages still raise after the budget is exhausted.
_DEFAULT_MAX_RETRIES = 5
_DEFAULT_INITIAL_BACKOFF = 1.0
_DEFAULT_MAX_BACKOFF_FACTOR = 5  # cap = 1.0 * 5 = 5s


def summarize(e: BaseException) -> str:
    """One-line exception summary."""
    return f'{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ""}'


def with_cloud_api_retries(fn: Callable[[], T],
                           max_retries: int = _DEFAULT_MAX_RETRIES) -> T:
    """Run ``fn()`` with retry/backoff on transient cloud-provider API errors.

    ``fn`` must be idempotent (a status read is). On a retryable error the
    call is retried up to ``max_retries`` times with exponential backoff; if
    the last attempt still fails, the exception is re-raised.
    """
    if max_retries < 1:
        raise ValueError(
            f'max_retries must be greater than 0, got {max_retries}')
    backoff = common_utils.Backoff(
        initial_backoff=_DEFAULT_INITIAL_BACKOFF,
        max_backoff_factor=_DEFAULT_MAX_BACKOFF_FACTOR)
    for attempt in range(max_retries):
        try:
            result = fn()
            if attempt > 0:
                logger.info('Transient cloud-provider API error recovered '
                            f'after {attempt} retries.')
            return result
        except RETRYABLE_EXCEPTIONS as e:
            if attempt == max_retries - 1:
                logger.error('Transient cloud-provider API error: giving up '
                             f'after {max_retries} attempts; {summarize(e)}')
                raise
            delay = backoff.current_backoff()
            logger.warning('Transient cloud-provider API error (attempt '
                           f'{attempt + 1}/{max_retries}), retrying in '
                           f'{delay:.1f}s: {summarize(e)}')
            time.sleep(delay)
    raise AssertionError('with_cloud_api_retries: unreachable')
