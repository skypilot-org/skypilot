"""Dashboard for managed jobs based on Flask.

TODO(zongheng): This is a basic version. In the future we can beef up the web
frameworks used (e.g.,
https://github.com/ray-project/ray/tree/master/dashboard/client/src) and/or get
rid of the SSH port-forwarding business (see cli.py's job_dashboard()
comment).
"""
import collections
import datetime
import os
import pathlib

import flask
from flask import abort
from flask import send_file
import yaml

from sky import jobs as managed_jobs
from sky.jobs import constants as managed_job_constants
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


# Column indices for job table
class JobTableColumns:
    DROPDOWN = 0
    ID = 1
    TASK = 2
    NAME = 3
    RESOURCES = 4
    SUBMITTED = 5
    TOTAL_DURATION = 6
    JOB_DURATION = 7
    RECOVERIES = 8
    STATUS = 9
    STARTED = 10
    CLUSTER = 11
    REGION = 12
    DESCRIPTION = 13
    ACTIONS = 14  # New column for actions


# Column headers matching the indices above
JOB_TABLE_COLUMNS = [
    '', 'ID', 'Task', 'Name', 'Resources', 'Submitted', 'Total Duration',
    'Job Duration', 'Recoveries', 'Status', 'Started', 'Cluster', 'Region',
    'Description', 'Actions'
]


def _extract_launch_history(log_content: str) -> str:
    """Extract launch history from log content.
    
    Args:
        log_content: Content of the log file.
        
    Returns:
        A formatted string containing the launch history.
    """
    launches = []
    for line in log_content.splitlines():
        if 'Launching on' in line or 'Tried on' in line:
            # Extract timestamp and message
            try:
                # Split by ']' to remove the logging prefix
                parts = line.split(']')
                if len(parts) >= 2:
                    # Extract timestamp from the logging prefix
                    timestamp = parts[0].split()[1:3]  # Get the date and time parts
                    # Extract the actual message, remove color codes and symbols
                    message = parts[1].replace('[0m⚙︎', '').strip()
                    # Combine timestamp and cleaned message
                    formatted_line = f"{' '.join(timestamp)} {message}"
                    launches.append(formatted_line)
            except IndexError:
                # Fallback if parsing fails
                launches.append(line.strip())
    
    if not launches:
        return 'No launch history found'
    
    # Format the launch history with timestamps
    return '\n'.join(launches)


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

    status_counts = collections.defaultdict(int)
    for task in all_managed_jobs:
        if not task['status'].is_terminal():
            status_counts[task['status'].value] += 1

    # Add an empty column for the dropdown button and actions column
    rows = [[''] + row + [''] for row in rows
           ]  # Add empty cell for actions column

    # Validate column count
    if rows and len(rows[0]) != len(JOB_TABLE_COLUMNS):
        raise RuntimeError(
            'Dashboard code and managed job queue code are out of sync.')

    # Fix STATUS color codes: '\x1b[33mCANCELLED\x1b[0m' -> 'CANCELLED'
    for row in rows:
        row[JobTableColumns.STATUS] = common_utils.remove_color(
            row[JobTableColumns.STATUS])

    # Remove filler rows ([''], ..., ['-'])
    rows = [
        row for row in rows
        if ''.join(map(str, row[:JobTableColumns.ACTIONS])) != ''
    ]

    # Add log content as description for each job
    for row in rows:
        job_id = str(row[JobTableColumns.ID]).strip().replace(' ⤳', '')
        if job_id and job_id != '-':
            try:
                log_path = os.path.join(
                    os.path.expanduser(managed_job_constants.JOBS_CONTROLLER_LOGS_DIR),
                    f'{job_id}.log')
                if os.path.exists(log_path):
                    with open(log_path, 'r', encoding='utf-8') as f:
                        log_content = f.read()
                        row[JobTableColumns.DESCRIPTION] = _extract_launch_history(log_content)
                else:
                    row[JobTableColumns.DESCRIPTION] = 'Log file not found'
            except Exception as e:
                row[JobTableColumns.DESCRIPTION] = f'Error reading log: {str(e)}'
    app.logger.error('All managed jobs:')

    # Get all unique status values
    status_values = sorted(
        list(set(row[JobTableColumns.STATUS] for row in rows)))

    rendered_html = flask.render_template(
        'index.html',
        columns=JOB_TABLE_COLUMNS,
        rows=rows,
        last_updated_timestamp=timestamp,
        status_values=status_values,
        status_counts=status_counts,
    )
    return rendered_html


@app.route('/download_log/<job_id>')
def download_log(job_id):
    try:
        log_path = os.path.join(
            os.path.expanduser(managed_job_constants.JOBS_CONTROLLER_LOGS_DIR),
            f'{job_id}.log')
        if not os.path.exists(log_path):
            abort(404)
        return send_file(log_path,
                         mimetype='text/plain',
                         as_attachment=True,
                         download_name=f'job_{job_id}.log')
    except Exception as e:
        app.logger.error(f'Error downloading log for job {job_id}: {str(e)}')
        abort(500)


if __name__ == '__main__':
    app.run()
