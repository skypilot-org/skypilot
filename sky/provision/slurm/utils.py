"""Slurm utilities for SkyPilot."""
import math
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import timeline

# TODO(jwj): Choose commonly used default values.
DEFAULT_CLUSTER_NAME = "localcluster"
DEFAULT_PARTITION = "debug"

# For all job states, please refer to
# https://slurm.schedmd.com/squeue.html#SECTION_JOB-STATE-CODES.
JOB_STATES = {
    "cancelled",
    "completed",
    "failed",
    "pending",
    "running",
}

class SlurmInstanceType:
    """Class to represent the "Instance Type" in a Slurm cluster.

    Since Slurm does not have a notion of instances, we generate
    virtual instance types that represent the resources requested by a
    Slurm worker node.

    This name captures the following resource requests:
        - CPU
        - Memory
        - Accelerators

    The name format is "{n}CPU--{k}GB" where n is the number of vCPUs and
    k is the amount of memory in GB. Accelerators can be specified by
    appending "--{type}:{a}" where type is the accelerator type and a
    is the number of accelerators.
    CPU and memory can be specified as floats. Accelerator count must be int.

    Examples:
        - 4CPU--16GB
        - 0.5CPU--1.5GB
        - 4CPU--16GB--V100:1
    """

    def __init__(self,
                 cpus: float,
                 memory: float,
                 accelerator_count: Optional[int] = None,
                 accelerator_type: Optional[str] = None):
        self.cpus = cpus
        self.memory = memory
        self.accelerator_count = accelerator_count
        self.accelerator_type = accelerator_type

    @property
    def name(self) -> str:
        """Returns the name of the instance."""
        assert self.cpus is not None
        assert self.memory is not None
        name = (f'{common_utils.format_float(self.cpus)}CPU--'
                f'{common_utils.format_float(self.memory)}GB')
        if self.accelerator_count is not None:
            # Replace spaces with underscores in accelerator type to make it a
            # valid logical instance type name.
            assert self.accelerator_type is not None, self.accelerator_count
            acc_name = self.accelerator_type.replace(' ', '_')
            name += f'--{acc_name}:{self.accelerator_count}'
        return name

    @staticmethod
    def is_valid_instance_type(name: str) -> bool:
        """Returns whether the given name is a valid instance type."""
        pattern = re.compile(
            r'^(\d+(\.\d+)?CPU--\d+(\.\d+)?GB)(--[\w\d-]+:\d+)?$')
        return bool(pattern.match(name))

    @classmethod
    def _parse_instance_type(
            cls,
            name: str) -> Tuple[float, float, Optional[int], Optional[str]]:
        """Parses and returns resources from the given InstanceType name.

        Returns:
            cpus | float: Number of CPUs
            memory | float: Amount of memory in GB
            accelerator_count | float: Number of accelerators
            accelerator_type | str: Type of accelerator
        """
        pattern = re.compile(
            r'^(?P<cpus>\d+(\.\d+)?)CPU--(?P<memory>\d+(\.\d+)?)GB(?:--(?P<accelerator_type>[\w\d-]+):(?P<accelerator_count>\d+))?$'  # pylint: disable=line-too-long
        )
        match = pattern.match(name)
        if match is not None:
            cpus = float(match.group('cpus'))
            memory = float(match.group('memory'))
            accelerator_count = match.group('accelerator_count')
            accelerator_type = match.group('accelerator_type')
            if accelerator_count is not None:
                accelerator_count = int(accelerator_count)
                # This is to revert the accelerator types with spaces back to
                # the original format.
                accelerator_type = str(accelerator_type).replace('_', ' ')
            else:
                accelerator_count = None
                accelerator_type = None
            return cpus, memory, accelerator_count, accelerator_type
        else:
            raise ValueError(f'Invalid instance name: {name}')

    @classmethod
    def from_instance_type(cls, name: str) -> 'SlurmInstanceType':
        """Returns an instance name object from the given name."""
        if not cls.is_valid_instance_type(name):
            raise ValueError(f'Invalid instance name: {name}')
        cpus, memory, accelerator_count, accelerator_type = \
            cls._parse_instance_type(name)
        return cls(cpus=cpus,
                   memory=memory,
                   accelerator_count=accelerator_count,
                   accelerator_type=accelerator_type)

    @classmethod
    def from_resources(cls,
                       cpus: float,
                       memory: float,
                       accelerator_count: Union[float, int] = 0,
                       accelerator_type: str = '') -> 'SlurmInstanceType':
        """Returns an instance name object from the given resources.

        If accelerator_count is not an int, it will be rounded up since GPU
        requests in Slurm must be int.

        NOTE: Should we take MIG management into account? See
        https://slurm.schedmd.com/gres.html#MIG_Management.
        """
        name = f'{cpus}CPU--{memory}GB'
        # Round up accelerator_count if it is not an int.
        accelerator_count = math.ceil(accelerator_count)
        if accelerator_count > 0:
            name += f'--{accelerator_type}:{accelerator_count}'
        return cls(cpus=cpus,
                   memory=memory,
                   accelerator_count=accelerator_count,
                   accelerator_type=accelerator_type)

    def __str__(self):
        return self.name

    def __repr__(self):
        return (f'SlurmInstanceType(cpus={self.cpus!r}, '
                f'memory={self.memory!r}, '
                f'accelerator_count={self.accelerator_count!r}, '
                f'accelerator_type={self.accelerator_type!r})')


def get_cluster_name_from_config(provider_config: Dict[str, Any]) -> str:
    """Return the cluster name from the provider config.

    The concept of cluster can be mapped to a cloud region.
    """
    return provider_config.get('cluster_name', DEFAULT_CLUSTER_NAME)


def get_partition_from_config(provider_config: Dict[str, Any]) -> str:
    """Return the partition from the provider config.

    The concept of partition can be mapped to a cloud zone.
    """
    return provider_config.get('partition', DEFAULT_PARTITION)


@timeline.event
def filter_jobs(ssh_config_dict: Dict[str, Any],
                partition: str,
                state_filters: Optional[List[str]] = None,
                cluster_name: Optional[str] = None) -> List[str]:
    """Filter Slurm jobs by job states."""
    if state_filters is not None:
        for state in state_filters:
            if state not in JOB_STATES:
                raise ValueError(f'{state} is not a valid Slurm job state.')
    else:
        state_filters = list(JOB_STATES)
    job_state_str = ','.join(state_filters)

    runner = command_runner.SlurmCommandRunner(
        (ssh_config_dict['hostname'], ssh_config_dict['port']),
        ssh_config_dict['user'],
        ssh_config_dict['private_key'],
        cluster_name,
        partition,
        disable_control_master=True)

    # TODO(jwj): Keep or remove ssh_user?
    rc, stdout, stderr = runner.run(
        f'squeue -u {ssh_config_dict["user"]} -t {job_state_str} -h -o "%i"',
        require_outputs=True)

    job_ids = stdout.splitlines()

    return job_ids
