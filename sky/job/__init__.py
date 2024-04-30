"""Managed jobs."""
import pathlib

from sky.job.constants import JOB_CLUSTER_NAME_PREFIX_LENGTH
from sky.job.constants import JOB_CONTROLLER_TEMPLATE
from sky.job.constants import JOB_CONTROLLER_YAML_PREFIX
from sky.job.constants import JOB_TASK_YAML_PREFIX
from sky.job.core import cancel
from sky.job.core import launch
from sky.job.core import queue
from sky.job.core import tail_logs
from sky.job.recovery_strategy import DEFAULT_RECOVERY_STRATEGY
from sky.job.recovery_strategy import RECOVERY_STRATEGIES
from sky.job.state import ManagedJobStatus
from sky.job.utils import dump_managed_job_queue
from sky.job.utils import format_job_table
from sky.job.utils import JOB_CONTROLLER_NAME
from sky.job.utils import load_managed_job_queue
from sky.job.utils import ManagedJobCodeGen

pathlib.Path(JOB_TASK_YAML_PREFIX).expanduser().parent.mkdir(parents=True,
                                                             exist_ok=True)
__all__ = [
    'RECOVERY_STRATEGIES',
    'DEFAULT_RECOVERY_STRATEGY',
    'JOB_CONTROLLER_NAME',
    # Constants
    'JOB_CONTROLLER_TEMPLATE',
    'JOB_CONTROLLER_YAML_PREFIX',
    'JOB_TASK_YAML_PREFIX',
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
