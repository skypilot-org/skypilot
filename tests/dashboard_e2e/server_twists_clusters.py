"""Server twists for the clusters pages (see framework/server_plugin.py).

Each one makes the server misbehave the way a backend regression would, for
the one test whose requests carry its id.
"""
from framework.server_plugin import register_body_twist

_STATUS = '/status'
_COST_REPORT = '/cost_report'


def _is_list(body):
    """A clusters-table request: every cluster, not a by-name lookup."""
    return 'summary_response' in body and not body.get('cluster_names')


# "My Clusters": the server ignores all_users=False and returns every user's
# clusters.
register_body_twist('srv-clusters-status-ignores-scope', _STATUS, lambda b: {
    **b, 'all_users': True
} if _is_list(b) else b)

# "All Clusters": the server ignores all_users=True and returns only the
# caller's clusters.
register_body_twist('srv-clusters-status-ignores-all-users', _STATUS,
                    lambda b: {
                        **b, 'all_users': False
                    } if _is_list(b) else b)

# The detail page's by-name lookup is answered like a list request: no last
# event, launch command or YAML.
register_body_twist(
    'srv-cluster-detail-summary-only', _STATUS, lambda b: {
        **b, 'summary_response': True
    } if b.get('cluster_names') else b)

# The history window is lost: the server answers the default one-day window
# whatever the page asked for.
register_body_twist('srv-cost-report-window-ignored', _COST_REPORT, lambda b: {
    **b, 'days': 1
} if 'days' in b else b)

# The history window collapses to nothing: only live clusters come back.
register_body_twist('srv-cost-report-empty-window', _COST_REPORT, lambda b: {
    **b, 'days': 0
} if 'days' in b else b)
