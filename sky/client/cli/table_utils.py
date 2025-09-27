"""Utilities for formatting tables for CLI output."""
from typing import List, Optional

from sky.jobs import utils as managed_jobs
from sky.schemas.api import responses
from sky.utils import log_utils


def format_job_queue(jobs: List[responses.ClusterJobRecord]):
    """Format the job queue for display.

    Usage:
        jobs = get_job_queue()
        print(format_job_queue(jobs))
    """
    job_table = log_utils.create_table([
        'ID', 'NAME', 'USER', 'SUBMITTED', 'STARTED', 'DURATION', 'RESOURCES',
        'STATUS', 'LOG', 'GIT COMMIT'
    ])
    for job in jobs:
        job_table.add_row([
            job.job_id,
            job.job_name,
            job.username,
            log_utils.readable_time_duration(job.submitted_at),
            log_utils.readable_time_duration(job.start_at),
            log_utils.readable_time_duration(job.start_at,
                                             job.end_at,
                                             absolute=True),
            job.resources,
            job.status.colored_str(),
            job.log_path,
            job.metadata.get('git_commit', '-'),
        ])
    return job_table


def format_job_table(jobs: List[responses.ManagedJobRecord],
                     show_all: bool,
                     show_user: bool,
                     max_jobs: Optional[int] = None):
    jobs = [job.model_dump() for job in jobs]
    return managed_jobs.format_job_table(jobs,
                                         show_all=show_all,
                                         show_user=show_user,
                                         max_jobs=max_jobs)
