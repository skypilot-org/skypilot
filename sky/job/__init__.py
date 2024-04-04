"""Modules for managed job clusters."""
import pathlib

from sky.job.constants import SPOT_CLUSTER_NAME_PREFIX_LENGTH
from sky.job.constants import SPOT_CONTROLLER_TEMPLATE
from sky.job.constants import SPOT_CONTROLLER_YAML_PREFIX
from sky.job.constants import SPOT_TASK_YAML_PREFIX
from sky.job.core import cancel
from sky.job.core import launch
from sky.job.core import queue
from sky.job.core import tail_logs
from sky.job.recovery_strategy import RECOVERY_STRATEGIES
from sky.job.recovery_strategy import SPOT_DEFAULT_STRATEGY
from sky.job.state import ManagedJobStatus
from sky.job.utils import dump_job_table_cache
from sky.job.utils import dump_spot_job_queue
from sky.job.utils import format_job_table
from sky.job.utils import load_job_table_cache
from sky.job.utils import load_spot_job_queue
from sky.job.utils import SPOT_CONTROLLER_NAME
from sky.job.utils import SpotCodeGen

pathlib.Path(SPOT_TASK_YAML_PREFIX).expanduser().parent.mkdir(parents=True,
                                                              exist_ok=True)
__all__ = [
    'RECOVERY_STRATEGIES',
    'SPOT_DEFAULT_STRATEGY',
    'SPOT_CONTROLLER_NAME',
    # Constants
    'SPOT_CONTROLLER_TEMPLATE',
    'SPOT_CONTROLLER_YAML_PREFIX',
    'SPOT_TASK_YAML_PREFIX',
    # Enums
    'ManagedJobStatus',
    # Core
    'cancel',
    'launch',
    'queue',
    'tail_logs',
    # utils
    'SpotCodeGen',
    'dump_job_table_cache',
    'load_job_table_cache',
    'format_job_table',
    'dump_spot_job_queue',
    'load_spot_job_queue',
]
