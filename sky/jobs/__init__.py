"""Managed jobs."""
import pathlib

from sky.jobs.constants import JOBS_CLUSTER_NAME_PREFIX_LENGTH
from sky.jobs.constants import JOBS_CONTROLLER_TEMPLATE
from sky.jobs.constants import JOBS_CONTROLLER_YAML_PREFIX
from sky.jobs.constants import JOBS_TASK_YAML_PREFIX
from sky.jobs.recovery_strategy import StrategyExecutor
from sky.jobs.sdk import cancel
from sky.jobs.sdk import launch
from sky.jobs.sdk import queue
from sky.jobs.sdk import tail_logs
from sky.jobs.state import ManagedJobStatus
from sky.jobs.utils import dump_managed_job_queue
from sky.jobs.utils import format_job_table
from sky.jobs.utils import JOB_CONTROLLER_NAME
from sky.jobs.utils import load_managed_job_queue
from sky.jobs.utils import ManagedJobCodeGen

pathlib.Path(JOBS_TASK_YAML_PREFIX).expanduser().parent.mkdir(parents=True,
                                                              exist_ok=True)
__all__ = [
    'JOB_CONTROLLER_NAME',
    # Constants
    'JOBS_CONTROLLER_TEMPLATE',
    'JOBS_CONTROLLER_YAML_PREFIX',
    'JOBS_TASK_YAML_PREFIX',
    # Enums
    'ManagedJobStatus',
    # Core
    'cancel',
    'launch',
    'queue',
    'tail_logs',
    # utils
    'ManagedJobCodeGen',
    'format_job_table',
    'dump_managed_job_queue',
    'load_managed_job_queue',
    'StrategyExecutor',
]
