"""Managed jobs."""
import pathlib

from apex.jobs.constants import JOBS_CLUSTER_NAME_PREFIX_LENGTH
from apex.jobs.constants import JOBS_CONTROLLER_TEMPLATE
from apex.jobs.constants import JOBS_CONTROLLER_YAML_PREFIX
from apex.jobs.constants import JOBS_TASK_YAML_PREFIX
from apex.jobs.core import cancel
from apex.jobs.core import launch
from apex.jobs.core import queue
from apex.jobs.core import tail_logs
from apex.jobs.recovery_strategy import DEFAULT_RECOVERY_STRATEGY
from apex.jobs.recovery_strategy import RECOVERY_STRATEGIES
from apex.jobs.state import ManagedJobStatus
from apex.jobs.utils import dump_managed_job_queue
from apex.jobs.utils import format_job_table
from apex.jobs.utils import JOB_CONTROLLER_NAME
from apex.jobs.utils import load_managed_job_queue
from apex.jobs.utils import ManagedJobCodeGen

pathlib.Path(JOBS_TASK_YAML_PREFIX).expanduser().parent.mkdir(parents=True,
                                                              exist_ok=True)
__all__ = [
    'RECOVERY_STRATEGIES',
    'DEFAULT_RECOVERY_STRATEGY',
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
]
