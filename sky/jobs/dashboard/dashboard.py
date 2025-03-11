"""Dashboard for managed jobs based on Flask.

TODO(zongheng): This is a basic version. In the future we can beef up the web
frameworks used (e.g.,
https://github.com/ray-project/ray/tree/master/dashboard/client/src) and/or get
rid of the SSH port-forwarding business (see cli.py's job_dashboard()
comment).
"""
import collections
import datetime
import enum
import os
import pathlib

import flask
import yaml

from sky import jobs as managed_jobs
from sky.client import sdk
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
        # We use startswith instead of exact match because the cluster name in
        # the yaml file is cluster_name_on_cloud which may have additional
        # suffices.
        return cluster_name.startswith(
            controller_utils.Controllers.JOBS_CONTROLLER.value.cluster_name)
    return False


# Column indices for job table
class JobTableColumns(enum.IntEnum):
    """Column indices for the jobs table in the dashboard.

    - DROPDOWN (0): Column for expandable dropdown arrow
    - ID (1): Job ID column
    - TASK (2): Task name/number column
    - NAME (3): Job name column
    - RESOURCES (4): Resources used by job
    - SUBMITTED (5): Job submission timestamp
    - TOTAL_DURATION (6): Total time since job submission
    - JOB_DURATION (7): Actual job runtime
    - RECOVERIES (8): Number of job recoveries
    - STATUS (9): Current job status
    - STARTED (10): Job start timestamp
    - CLUSTER (11): Cluster name
    - REGION (12): Cloud region
    - FAILOVER (13): Job failover history
    - DETAILS (14): Job details
    - ACTIONS (15): Available actions column
    - LOG_CONTENT (16): Log content column
    """
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
    DETAILS = 13
    FAILOVER = 14
    ACTIONS = 15
    LOG_CONTENT = 16


# Column headers matching the indices above
JOB_TABLE_COLUMNS = [
    '', 'ID', 'Task', 'Name', 'Resources', 'Submitted', 'Total Duration',
    'Job Duration', 'Status', 'Started', 'Cluster', 'Region', 'Failover',
    'Recoveries', 'Details', 'Actions', 'Log Content'
]

# This column is given by format_job_table but should be ignored.
SCHED_STATE_COLUMN = 12


def _extract_launch_history(log_content: str) -> str:
    """Extract launch history from log content.

    Args:
        log_content: Content of the log file.
    Returns:
        A formatted string containing the launch history.
    """
    launches = []
    current_launch = None

    for line in log_content.splitlines():
        if 'Launching on' in line:
            try:
                parts = line.split(']')
                if len(parts) >= 2:
                    timestamp = parts[0].split()[1:3]
                    message = parts[1].replace('[0m⚙︎', '').strip()
                    formatted_line = f'{" ".join(timestamp)} {message}'
                    if current_launch:
                        prev_time, prev_target = current_launch.rsplit(
                            ' Launching on ', 1)
                        launches.append(
                            f'{prev_time} Tried to launch on {prev_target}')

                    # Store the current launch
                    current_launch = formatted_line
            except IndexError:
                launches.append(line.strip())

    # Add the final (successful) launch at the beginning
    if current_launch:
        result = [current_launch]
        result.extend(launches)
        return '\n'.join(result)

    return 'No launch history found'


@app.route('/')
def home():
    if not _is_running_on_jobs_controller():
        # Experimental: run on laptop (refresh is very slow).
        request_id = managed_jobs.queue(refresh=True, skip_finished=False)
        all_managed_jobs = sdk.get(request_id)
    else:
        job_table = managed_jobs.dump_managed_job_queue()
        all_managed_jobs = managed_jobs.load_managed_job_queue(job_table)

    timestamp = datetime.datetime.now(datetime.timezone.utc)
    rows = managed_jobs.format_job_table(all_managed_jobs,
                                         show_all=True,
                                         show_user=False,
                                         return_rows=True)

    status_counts = collections.defaultdict(int)
    for task in all_managed_jobs:
        if not task['status'].is_terminal():
            status_counts[task['status'].value] += 1

    # Add an empty column for the dropdown button and actions column
    # Exclude SCHED. STATE and LOG_CONTENT columns
    rows = [
        [''] + row[:SCHED_STATE_COLUMN] + row[SCHED_STATE_COLUMN + 1:] +
        # Add empty cell for failover and actions column
        [''] + [''] + [''] for row in rows
    ]

    # Add log content as a regular column for each job
    for row in rows:
        job_id = str(row[JobTableColumns.ID]).strip().replace(' ⤳', '')
        if job_id and job_id != '-':
            try:
                log_path = os.path.join(
                    os.path.expanduser(
                        managed_job_constants.JOBS_CONTROLLER_LOGS_DIR),
                    f'{job_id}.log')
                if os.path.exists(log_path):
                    with open(log_path, 'r', encoding='utf-8') as f:
                        log_content = f.read()
                        row[JobTableColumns.FAILOVER] = _extract_launch_history(
                            log_content)
                        row[JobTableColumns.LOG_CONTENT] = log_content
                else:
                    row[JobTableColumns.FAILOVER] = 'Log file not found'
                    row[JobTableColumns.LOG_CONTENT] = 'Log file not found'
            except (IOError, OSError) as e:
                error_msg = f'Error reading log: {str(e)}'
                row[JobTableColumns.FAILOVER] = error_msg
                row[JobTableColumns.LOG_CONTENT] = error_msg
        else:
            row[JobTableColumns.LOG_CONTENT] = ''

    if rows and len(rows[0]) != len(JOB_TABLE_COLUMNS):
        raise RuntimeError(
            f'Dashboard code and managed job queue code are out of sync. '
            f'Expected {JOB_TABLE_COLUMNS} columns, got {rows[0]}')

    # Fix STATUS color codes: '\x1b[33mCANCELLED\x1b[0m' -> 'CANCELLED'
    for row in rows:
        row[JobTableColumns.STATUS] = common_utils.remove_color(
            row[JobTableColumns.STATUS])

    # Remove filler rows ([''], ..., ['-'])
    rows = [
        row for row in rows
        if ''.join(map(str, row[:JobTableColumns.ACTIONS])) != ''
    ]

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
        request=flask.request,
    )
    return rendered_html


if __name__ == '__main__':
    app.run()
