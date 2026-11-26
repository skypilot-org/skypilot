"""Server twists for the OSS dashboard suite (see framework/server_plugin.py).

Each one makes the server misbehave the way a backend regression would, for
the one test whose requests carry its id.
"""
from framework.server_plugin import register_body_twist

_JOBS_QUEUE = '/jobs/queue/v2'


def _is_jobs_page(body):
    """A jobs-table request: paged, and not the one-row total-count probe."""
    return 'page' in body and body.get('limit') != 1


# The server ignores "all users" on the jobs table and returns only the
# caller's own jobs.
register_body_twist(
    'srv-jobs-queue-ignores-all-users', _JOBS_QUEUE, lambda b: {
        **b, 'all_users': False
    } if _is_jobs_page(b) else b)
