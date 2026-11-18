"""Requests that run in the heavy pool.

A heavy request reads a part of the stored history that grows with the
deployment, so one call can take minutes and several GB. When the heavy pool is
enabled (``SKYPILOT_SERVER_HEAVY_WORKERS``), such requests run in their own
small pool of processes, one process per request, instead of the short pool:

- At most ``SKYPILOT_SERVER_HEAVY_WORKERS`` run at a time in each API server,
  and the memory a request keeps is returned when its process exits.
- One user runs one heavy request of each name at a time; the rest wait in
  the queue.

Whether a request is heavy depends on its arguments as well as its name: a cost
report for named clusters, or a job queue page, is a normal short request.
"""
import dataclasses
import hashlib
from typing import Callable, Dict, Optional

from sky.server import config as server_config
from sky.server.requests import payloads
from sky.server.requests import request_names

# A job queue call with a larger limit is heavy.
MAX_LIGHT_JOBS_LIMIT = 100


@dataclasses.dataclass(frozen=True)
class _HeavyShape:
    is_heavy: Callable[[payloads.RequestBody], bool]
    reason: str


def _unfiltered_cost_report(body: payloads.RequestBody) -> bool:
    assert isinstance(body, payloads.CostReportBody), type(body)
    return body.cluster_hashes is None and body.cluster_names is None


def _unbounded_jobs_queue(body: payloads.RequestBody) -> bool:
    assert isinstance(
        body, (payloads.JobsQueueBody, payloads.JobsQueueV2Body)), type(body)
    if body.job_ids is not None or body.skip_finished:
        return False
    # The legacy queue body has no limit.
    limit: Optional[int] = getattr(body, 'limit', None)
    return limit is None or limit > MAX_LIGHT_JOBS_LIMIT


_RN = request_names.RequestName

HEAVY_SHAPES: Dict[str, _HeavyShape] = {
    _RN.CLUSTER_COST_REPORT: _HeavyShape(
        _unfiltered_cost_report,
        'Without a cluster filter it reads every cluster in the history '
        'window.'),
    _RN.JOBS_QUEUE: _HeavyShape(
        _unbounded_jobs_queue,
        'Without job IDs or skip_finished it returns finished jobs too, and it '
        'has no limit.'),
    _RN.JOBS_QUEUE_V2: _HeavyShape(
        _unbounded_jobs_queue,
        'Without job IDs, skip_finished or a limit up to MAX_LIGHT_JOBS_LIMIT '
        'it returns the whole job history.'),
}


def enabled() -> bool:
    return server_config.heavy_worker_count() > 0


def is_heavy(request_name: str, body: payloads.RequestBody) -> bool:
    """Whether a request of this name and body is heavy."""
    shape = HEAVY_SHAPES.get(request_name)
    return shape is not None and shape.is_heavy(body)


def user_lock_id(user_id: str, request_name: str) -> str:
    """Lock held while one of the user's heavy requests of a name runs."""
    digest = hashlib.sha256(f'{user_id}\0{request_name}'.encode()).hexdigest()
    return f'heavy_request_{digest[:32]}'
