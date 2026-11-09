"""Server twists for the managed jobs list and job detail tests (see
framework/server_plugin.py).

Each one makes the server misbehave the way a backend regression would, for
the one test whose requests carry its id.
"""
from typing import Any, Dict

from framework.server_plugin import register_body_twist

_JOBS_QUEUE = '/jobs/queue/v2'
_JOBS_LOGS = '/jobs/logs'
_JOBS_DOWNLOAD_LOGS = '/jobs/download_logs'

# An id no seeded job reaches.
_FAR_AWAY = 1_000_000


def _is_listing(body: Dict[str, Any]) -> bool:
    """A jobs-table request, or the unpaged one the page sends in the
    background to cache every page of the same query. A twist must change
    both, or the cached pages hide it a few seconds later."""
    return body.get('limit') != 1 and not body.get('job_ids')


def _paged(body: Dict[str, Any], limit: int) -> Dict[str, Any]:
    return {**body, 'page': body.get('page', 1), 'limit': limit}


def _is_detail(body: Dict[str, Any]) -> bool:
    """The job detail page's request: one job and the jobs launched from it."""
    return bool(body.get('job_ids')) and bool(body.get('include_tree'))


def _when(pred, rewrite):
    return lambda b: rewrite(dict(b)) if pred(b) else b


# The server drops the status filter and lists every status.
register_body_twist(
    'srv-jobs-queue-ignores-statuses', _JOBS_QUEUE,
    _when(_is_listing,
          lambda b: {k: v for k, v in b.items() if k != 'statuses'}))

# The server applies only the first of several selected statuses.
register_body_twist(
    'srv-jobs-queue-first-status-only', _JOBS_QUEUE,
    _when(lambda b: _is_listing(b) and len(b.get('statuses') or []) > 1,
          lambda b: {
              **b, 'statuses': b['statuses'][:1]
          }))

# The server counts SUCCEEDED as an active status.
register_body_twist(
    'srv-jobs-queue-active-includes-finished', _JOBS_QUEUE,
    _when(
        lambda b: _is_listing(b) and 'RUNNING' in
        (b.get('statuses') or []) and 'PENDING' in (b.get('statuses') or []),
        lambda b: {
            **b, 'statuses': b['statuses'] + ['SUCCEEDED']
        }))

# The server returns a shorter page than asked for.
register_body_twist('srv-jobs-queue-short-page', _JOBS_QUEUE,
                    _when(_is_listing, lambda b: _paged(b, 2)))

# The server caps the page size at 10 whatever the page asks for.
register_body_twist(
    'srv-jobs-queue-limit-capped', _JOBS_QUEUE,
    _when(_is_listing, lambda b: _paged(b, min(b.get('limit') or 10, 10))))

# The server matches the workspace filter against the wrong value.
register_body_twist(
    'srv-jobs-queue-wrong-workspace', _JOBS_QUEUE,
    _when(lambda b: _is_listing(b) and b.get('workspace_match'), lambda b: {
        **b, 'workspace_match': b['workspace_match'] + '-x'
    }))

# The server ignores the user filter.
register_body_twist(
    'srv-jobs-queue-ignores-user-match', _JOBS_QUEUE,
    _when(_is_listing,
          lambda b: {k: v for k, v in b.items() if k != 'user_match'}))

# The server stops returning where a job ran.
register_body_twist(
    'srv-jobs-queue-drops-cloud', _JOBS_QUEUE,
    _when(lambda b: _is_listing(b) and b.get('fields'), lambda b: {
        **b, 'fields': [f for f in b['fields'] if f != 'cloud']
    }))

# The server looks up the wrong job for the detail page.
register_body_twist(
    'srv-job-detail-wrong-id', _JOBS_QUEUE,
    _when(_is_detail, lambda b: {
        **b, 'job_ids': [str(int(b['job_ids'][0]) + _FAR_AWAY)]
    }))

# The server mixes up task logs and controller logs.
register_body_twist('srv-jobs-logs-controller-flipped', _JOBS_LOGS, lambda b: {
    **b, 'controller': not b.get('controller')
})

# The server zips the controller logs when asked for the task logs.
register_body_twist('srv-jobs-download-logs-controller-flipped',
                    _JOBS_DOWNLOAD_LOGS, lambda b: {
                        **b, 'controller': not b.get('controller')
                    })

# The server prepares the logs of a job that does not exist.
register_body_twist(
    'srv-jobs-download-logs-wrong-job', _JOBS_DOWNLOAD_LOGS, lambda b: {
        **b, 'job_id': (b.get('job_id') or 0) + _FAR_AWAY
    })
