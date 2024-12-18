"""Dashboard for managed jobs based on Flask.

TODO(zongheng): This is a basic version. In the future we can beef up the web
frameworks used (e.g.,
https://github.com/ray-project/ray/tree/master/dashboard/client/src) and/or get
rid of the SSH port-forwarding business (see cli.py's job_dashboard()
comment).
"""
import datetime
import pathlib

import flask
import yaml

from sky import jobs as managed_jobs
from sky.utils import common_utils
from sky.utils import controller_utils

app = flask.Flask(__name__)


def _is_running_on_jobs_controller() -> bool:
    """Am I running on jobs controller?

    Loads ~/.sky/sky_ray.yml and check cluster_name.
    """
    if pathlib.Path('~/.sky/sky_ray.yml').expanduser().exists():
        config = yaml.safe_load(
            pathlib.Path('~/.sky/sky_ray.yml').expanduser().read_text(
                encoding='utf-8'))
        cluster_name = config.get('cluster_name', '')
        candidate_controller_names = (
            controller_utils.Controllers.JOBS_CONTROLLER.value.
            candidate_cluster_names)
        # We use startswith instead of exact match because the cluster name in
        # the yaml file is cluster_name_on_cloud which may have additional
        # suffices.
        return any(
            cluster_name.startswith(name)
            for name in candidate_controller_names)
    return False


@app.route('/')
def home():
    if not _is_running_on_jobs_controller():
        # Experimental: run on laptop (refresh is very slow).
        all_managed_jobs = managed_jobs.queue(refresh=True, skip_finished=False)
    else:
        job_table = managed_jobs.dump_managed_job_queue()
        all_managed_jobs = managed_jobs.load_managed_job_queue(job_table)

    timestamp = datetime.datetime.now(datetime.timezone.utc)
    rows = managed_jobs.format_job_table(all_managed_jobs,
                                         show_all=True,
                                         return_rows=True)
    # Add an empty column for the dropdown button. This will be added in the
    # jobs/templates/index.html file.
    rows = [[''] + row for row in rows]

    # FIXME(zongheng): make the job table/queue funcs return structured info so
    # that we don't have to do things like row[-5] below.
    columns = [
        '', 'ID', 'Task', 'Name', 'Resources', 'Submitted', 'Total Duration',
        'Job Duration', 'Recoveries', 'Status', 'Started', 'Cluster', 'Region',
        'Failure'
    ]
    if rows and len(rows[0]) != len(columns):
        raise RuntimeError(
            'Dashboard code and managed job queue code are out of sync.')

    # Fix STATUS color codes: '\x1b[33mCANCELLED\x1b[0m' -> 'CANCELLED'.
    for row in rows:
        row[-5] = common_utils.remove_color(row[-5])
    # Remove filler rows ([''], ..., ['-']).
    rows = [row for row in rows if ''.join(map(str, row)) != '']

    # Get all unique status values.
    status_values = sorted(list(set(row[-5] for row in rows)))
    rendered_html = flask.render_template(
        'index.html',
        columns=columns,
        rows=rows,
        last_updated_timestamp=timestamp,
        status_values=status_values,
    )
    return rendered_html


if __name__ == '__main__':
    app.run()
