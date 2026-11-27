"""Extra seeding for the managed jobs tests: job details and log files.

``framework.seed.managed_job`` inserts a bare job. These helpers fill in what
the job detail page reads (where it last ran, its entrypoint and YAML, git
commit, failure reason) and write the log files the server streams back:

- task logs: a file the job's task row points at (``local_log_file``), which
  is what the server reads for a job that has finished;
- controller logs: the per-job file under the jobs controller log directory.

Log files are written under the server's runtime dir; ``remove_logs`` deletes
them, and a leftover check reports any a test left behind.
"""
import glob
import json
import os
from typing import Any, Dict, List, Optional

from framework import seed
import sqlalchemy
from sqlalchemy import orm

from sky.jobs import state as job_state
from sky.jobs import utils as job_utils
from sky.skylet import log_lib

# Logs written before this marker are setup boilerplate; the server starts
# printing a finished task's log at the line carrying it.
START_MARKER = log_lib.LOG_FILE_START_STREAMING_AT


def _engine() -> sqlalchemy.engine.Engine:
    return job_state._db_manager.get_engine()  # pylint: disable=protected-access


def set_job_details(job_id: int,
                    *,
                    cloud: Optional[str] = None,
                    region: Optional[str] = None,
                    entrypoint: Optional[str] = None,
                    user_yaml: Optional[str] = None,
                    git_commit: Optional[str] = None,
                    failure_reason: Optional[str] = None,
                    recovery_count: Optional[int] = None) -> None:
    """Fill in the fields the jobs table and the job detail page show."""
    info: Dict[str, Any] = {}
    task: Dict[str, Any] = {}
    if cloud is not None:
        info['cloud'] = cloud
    if region is not None:
        info['region'] = region
    if entrypoint is not None:
        info['entrypoint'] = entrypoint
    if user_yaml is not None:
        info['original_user_yaml_content'] = user_yaml
    if git_commit is not None:
        task['metadata'] = json.dumps({'git_commit': git_commit})
    if failure_reason is not None:
        task['failure_reason'] = failure_reason
    if recovery_count is not None:
        task['recovery_count'] = recovery_count
    with orm.Session(_engine()) as session:
        if info:
            session.execute(
                sqlalchemy.update(job_state.job_info_table).where(
                    job_state.job_info_table.c.spot_job_id == job_id).values(
                        **info))
        if task:
            session.execute(
                sqlalchemy.update(job_state.spot_table).where(
                    job_state.spot_table.c.spot_job_id == job_id).values(
                        **task))
        session.commit()


def _task_log_path(job_id: int) -> str:
    log_dir = os.path.join(os.environ['SKY_RUNTIME_DIR'], 'sky_logs',
                           'e2e_job_logs')
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, f'{job_id}.log')


def write_task_log(job_id: int, lines: List[str]) -> str:
    """Write a finished job's task log and point its task row at it."""
    path = _task_log_path(job_id)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(f'{START_MARKER}e2e-cluster\n')
        for line in lines:
            f.write(line + '\n')
    with orm.Session(_engine()) as session:
        session.execute(
            sqlalchemy.update(job_state.spot_table).where(
                job_state.spot_table.c.spot_job_id == job_id).values(
                    local_log_file=path))
        session.commit()
    return path


def write_controller_log(job_id: int, lines: List[str]) -> str:
    """Write the jobs controller's log for this job."""
    path = job_utils.controller_log_file_for_job(job_id,
                                                 create_if_not_exists=True)
    with open(path, 'w', encoding='utf-8') as f:
        for line in lines:
            f.write(line + '\n')
    return path


def remove_logs(job_id: int) -> None:
    for path in (_task_log_path(job_id),
                 job_utils.controller_log_file_for_job(job_id)):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def _leftover_logs() -> List[str]:
    """Log files of jobs that no longer exist: a test seeded logs and did not
    remove them."""
    with orm.Session(_engine()) as session:
        ids = set(
            session.execute(
                sqlalchemy.select(
                    job_state.job_info_table.c.spot_job_id)).scalars().all())
    runtime = os.environ.get('SKY_RUNTIME_DIR', '')
    out = []
    for path in glob.glob(
            os.path.join(runtime, 'sky_logs', 'e2e_job_logs', '*.log')):
        job_id = int(os.path.basename(path).split('.')[0])
        if job_id not in ids:
            out.append(f'job log {path}')
    return out


seed.register_leftover_check(_leftover_logs)
