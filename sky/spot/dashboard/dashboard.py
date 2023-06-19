import datetime
from pathlib import Path
import re
import yaml

from flask import Flask, render_template

import sky
from sky import spot

app = Flask(__name__)


def _is_running_on_spot_controller() -> bool:
    """Am I running on spot controller?

    Loads ~/.sky/sky_ray.yml and check cluster_name.
    """
    if Path('~/.sky/sky_ray.yml').expanduser().exists():
        config = yaml.safe_load(
            Path('~/.sky/sky_ray.yml').expanduser().read_text())
        return config.get('cluster_name', '').startswith('sky-spot-controller-')
    return False


@app.route('/')
def home():
    if not _is_running_on_spot_controller():
        # Experimental: run on laptop (refresh is very slow).
        all_spot_jobs = sky.spot_queue(refresh=True, skip_finished=False)
    else:
        job_table = spot.dump_spot_job_queue()
        all_spot_jobs = spot.load_spot_job_queue(job_table)

    timestamp = datetime.datetime.utcnow()
    rows = spot.format_job_table(all_spot_jobs, show_all=True, return_rows=True)

    # FIXME(zongheng): make the job table/queue funcs return structured info so
    # that we don't have to do things like row[-5] below.
    columns = [
        'ID', 'Task', 'Name', 'Resources', 'Submitted', 'Total Duration',
        'Job Duration', 'Recoveries', 'Status', 'Started', 'Cluster', 'Region',
        'Failure'
    ]
    if rows and len(rows[0]) != len(columns):
        raise RuntimeError(
            'Dashboard code and spot queue code are out of sync.')

    # Fix STATUS color codes: '\x1b[33mCANCELLED\x1b[0m' -> 'CANCELLED'.
    for row in rows:
        row[-5] = re.sub(r'[^A-Z_]', '', row[-5])
    # Remove filler rows ([''], ..., ['-']).
    rows = [row for row in rows if ''.join(map(str, row)) != '']

    rendered_html = render_template(
        'index.html',
        columns=columns,
        rows=rows,
        last_updated_timestamp=timestamp,
    )
    return rendered_html


if __name__ == '__main__':
    app.run()
