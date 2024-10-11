"""Managed jobs."""
import pathlib

from sky.jobs.constants import JOBS_CLUSTER_NAME_PREFIX_LENGTH
from sky.jobs.constants import JOBS_CONTROLLER_TEMPLATE
from sky.jobs.constants import JOBS_CONTROLLER_YAML_PREFIX
from sky.jobs.constants import JOBS_TASK_YAML_PREFIX
from sky.jobs.core import cancel
from sky.jobs.core import launch
from sky.jobs.core import queue
from sky.jobs.core import queue_from_kubernetes_pod
from sky.jobs.core import tail_logs
from sky.jobs.recovery_strategy import DEFAULT_RECOVERY_STRATEGY
from sky.jobs.recovery_strategy import RECOVERY_STRATEGIES
from sky.jobs.state import ManagedJobStatus
from sky.jobs.utils import dump_managed_job_queue
from sky.jobs.utils import format_job_table
from sky.jobs.utils import JOB_CONTROLLER_NAME
from sky.jobs.utils import load_managed_job_queue
from sky.jobs.utils import ManagedJobCodeGen

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
    'queue_from_kubernetes_pod',
    'tail_logs',
    # utils
    'ManagedJobCodeGen',
    'format_job_table',
    'dump_managed_job_queue',
    'load_managed_job_queue',
]
