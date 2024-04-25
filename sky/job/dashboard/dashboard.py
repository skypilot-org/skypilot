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

from sky import job
from sky.utils import common_utils

app = flask.Flask(__name__)


def _is_running_on_job_controller() -> bool:
    """Am I running on job controller?

    Loads ~/.sky/sky_ray.yml and check cluster_name.
    """
    if pathlib.Path('~/.sky/sky_ray.yml').expanduser().exists():
        config = yaml.safe_load(
            pathlib.Path('~/.sky/sky_ray.yml').expanduser().read_text())
        return config.get('cluster_name', '').startswith('sky-spot-controller-')
    return False


@app.route('/')
def home():
    if not _is_running_on_job_controller():
        # Experimental: run on laptop (refresh is very slow).
        all_managed_jobs = job.queue(refresh=True, skip_finished=False)
    else:
        job_table = job.dump_managed_job_queue()
        all_managed_jobs = job.load_managed_job_queue(job_table)

    timestamp = datetime.datetime.now(datetime.timezone.utc)
    rows = job.format_job_table(all_managed_jobs,
                                show_all=True,
                                return_rows=True)

    # FIXME(zongheng): make the job table/queue funcs return structured info so
    # that we don't have to do things like row[-5] below.
    columns = [
        'ID', 'Task', 'Name', 'Resources', 'Submitted', 'Total Duration',
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

    rendered_html = flask.render_template(
        'index.html',
        columns=columns,
        rows=rows,
        last_updated_timestamp=timestamp,
    )
    return rendered_html


if __name__ == '__main__':
    app.run()
