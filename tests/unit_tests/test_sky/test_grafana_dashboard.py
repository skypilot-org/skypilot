"""Structural checks on the shipped Grafana dashboard.

The helm test asserts that particular metrics appear in the JSON, which is a
text search and says nothing about the structure around them. These are the
properties a text search cannot see -- and one of them was already broken:
adding a row reused ids that existed, so four panels shared an id with another.
"""
import collections
import json
import pathlib

_DASHBOARD = (pathlib.Path(__file__).parents[3] / 'charts' / 'skypilot' /
              'manifests' / 'api-server-overview.json')


def _panels():
    return json.loads(_DASHBOARD.read_text())['panels']


def test_every_panel_has_its_own_id():
    """Grafana keys panel links, repeats and "view panel" URLs on the id.

    A duplicate does not fail to render; it silently resolves to whichever
    panel is found first, so the only way to notice is to look.
    """
    ids = [panel.get('id') for panel in _panels()]
    duplicates = {i: n for i, n in collections.Counter(ids).items() if n > 1}

    assert not duplicates, f'panel ids used more than once: {duplicates}'


def test_the_launch_latency_row_shows_what_it_measures():
    """Each metric this feature records answers a question the others cannot.

    Listed rather than derived from the metrics module: not every metric
    belongs on a dashboard, so the list is the decision about which do, and
    adding one here should be a deliberate edit.
    """
    wanted = {
        # how long jobs take to start
        'sky_managed_job_time_to_running_seconds',
        # which stage to go and fix
        'sky_managed_job_phase_duration_seconds',
        # which queue is starving
        'sky_launch_queue_wait_seconds',
        # the same split for launches that are not managed jobs, which appear
        # nowhere else on the row
        'sky_launch_phase_duration_seconds',
        # the denominator: a fleet that mostly fails to start must not look
        # fast because only the survivors are plotted
        'sky_managed_job_starts_total',
        # whether to believe any of the above
        'sky_launch_phase_dropped_total',
        'sky_launch_phase_anomalies_total',
    }
    rendered = json.dumps(_panels())

    assert not {m for m in wanted if m not in rendered
               }, ('recorded but never shown: '
                   f'{sorted(m for m in wanted if m not in rendered)}')
