"""DAGs: user applications to be run."""
import enum
import pprint
import threading
import typing
from typing import Dict, List, Optional, Union

if typing.TYPE_CHECKING:
    from sky import task


class DagExecution(enum.Enum):
    """Execution mode for DAGs with multiple tasks.

    This controls how tasks in a multi-task DAG are executed.
    """
    SERIAL = 'serial'  # Tasks execute sequentially (pipeline)
    PARALLEL = 'parallel'  # All tasks start in parallel (job group)


# Default execution mode for jobs without an explicit execution mode set.
# Used for single jobs and as a fallback for pipelines.
DEFAULT_EXECUTION = DagExecution.SERIAL


class Dag:
    """Dag: a user application, represented as a DAG of Tasks.

    Examples:
        >>> import sky
        >>> with sky.Dag() as dag:
        >>>     task = sky.Task(...)

    For JobGroups (heterogeneous parallel workloads):
        >>> dag = dag_utils.load_job_group_from_yaml('job_group.yaml')
        >>> # dag.is_job_group() returns True
        >>> # dag.tasks contains jobs to run in parallel
    """

    def __init__(self) -> None:
        self.tasks: List['task.Task'] = []
        import networkx as nx  # pylint: disable=import-outside-toplevel

        self.graph = nx.DiGraph()
        self.name: Optional[str] = None
        self.policy_applied: bool = False
        self.pool: Optional[str] = None

        # Execution mode for multi-task DAGs
        self.execution: Optional[DagExecution] = None

        # Primary/auxiliary task support for job groups
        # If set, only the named tasks are "primary"; others are "auxiliary".
        # When all primary tasks complete, auxiliary tasks are terminated.
        self.primary_tasks: Optional[List[str]] = None
        # Termination delay for auxiliary tasks when primary tasks complete.
        # Can be a string like "30s" (applies to all auxiliary tasks) or
        # a dict like {"default": "30s", "replay-buffer": "1m"}.
        self.termination_delay: Optional[Union[str, Dict[str, str]]] = None

    def add(self, task: 'task.Task') -> None:
        self.graph.add_node(task)
        self.tasks.append(task)

    def remove(self, task: 'task.Task') -> None:
        self.tasks.remove(task)
        self.graph.remove_node(task)

    def add_edge(self, op1: 'task.Task', op2: 'task.Task') -> None:
        assert op1 in self.graph.nodes
        assert op2 in self.graph.nodes
        self.graph.add_edge(op1, op2)

    def __len__(self) -> int:
        return len(self.tasks)

    def __enter__(self) -> 'Dag':
        push_dag(self)
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        pop_dag()

    def __repr__(self) -> str:
        pformat = pprint.pformat(self.tasks)
        return f'DAG:\n{pformat}'

    def get_graph(self):
        return self.graph

    def is_job_group(self) -> bool:
        """Check if this DAG represents a JobGroup.

        A DAG is a JobGroup if it has parallel execution mode. This is the
        defining characteristic that distinguishes job groups from pipelines.
        """
        return self.execution == DagExecution.PARALLEL

    def set_execution(self, execution: 'DagExecution') -> None:
        """Configure this DAG with the given execution mode."""
        self.execution = execution

    def get_termination_delay_secs(self, task_name: str) -> int:
        """Get termination delay in seconds for a specific task.

        Args:
            task_name: The name of the task to get the delay for.

        Returns:
            Termination delay in seconds. Returns 0 if not configured.
        """
        if self.termination_delay is None:
            return 0

        # Import here to avoid circular imports
        # pylint: disable=import-outside-toplevel
        from sky.utils import resources_utils

        # Get the delay string based on format (str or dict)
        if isinstance(self.termination_delay, str):
            delay_str = self.termination_delay
        else:
            delay_str = self.termination_delay.get(
                task_name, self.termination_delay.get('default', '0s'))

        return resources_utils.parse_time_seconds(delay_str)

    def is_primary_task(self, task_name: str) -> bool:
        """Check if a task is a primary task.

        Args:
            task_name: The name of the task to check.

        Returns:
            True if the task is primary. When primary_tasks is None or empty,
            all tasks are considered primary.
        """
        if self.primary_tasks is None or len(self.primary_tasks) == 0:
            return True
        # pylint: disable=unsupported-membership-test
        return task_name in self.primary_tasks

    def get_auxiliary_task_names(self) -> typing.List[str]:
        """Get the names of all auxiliary (non-primary) tasks.

        Returns:
            List of auxiliary task names. Returns empty list if all tasks
            are primary (when primary_tasks is None or empty).
        """
        if self.primary_tasks is None or len(self.primary_tasks) == 0:
            return []
        # pylint: disable=unsupported-membership-test
        return [
            t.name
            for t in self.tasks
            if t.name is not None and t.name not in self.primary_tasks
        ]

    def is_chain(self) -> bool:
        """Check if the DAG is a linear chain of tasks."""

        nodes = list(self.graph.nodes)

        if len(nodes) == 0:
            return True

        in_degrees = [self.graph.in_degree(node) for node in nodes]
        out_degrees = [self.graph.out_degree(node) for node in nodes]

        # Check out-degrees: all <= 1 and exactly one node has out_degree == 0
        out_degree_condition = (all(degree <= 1 for degree in out_degrees) and
                                sum(degree == 0 for degree in out_degrees) == 1)

        # Check in-degrees: all <= 1 and exactly one node has in_degree == 0
        in_degree_condition = (all(degree <= 1 for degree in in_degrees) and
                               sum(degree == 0 for degree in in_degrees) == 1)

        return out_degree_condition and in_degree_condition

    def validate(self,
                 skip_file_mounts: bool = False,
                 skip_workdir: bool = False):
        for task in self.tasks:
            task.validate(skip_file_mounts=skip_file_mounts,
                          skip_workdir=skip_workdir)

    def resolve_and_validate_volumes(self) -> None:
        for task in self.tasks:
            task.resolve_and_validate_volumes()

    def pre_mount_volumes(self) -> None:
        vol_map = {}
        # Deduplicate volume mounts.
        for task in self.tasks:
            if task.volume_mounts is not None:
                for volume_mount in task.volume_mounts:
                    vol_map[volume_mount.volume_name] = volume_mount
        for volume_mount in vol_map.values():
            volume_mount.pre_mount()


class _DagContext(threading.local):
    """A thread-local stack of Dags."""
    _current_dag: Optional[Dag] = None
    _previous_dags: List[Dag] = []

    def push_dag(self, dag: Dag):
        if self._current_dag is not None:
            self._previous_dags.append(self._current_dag)
        self._current_dag = dag

    def pop_dag(self) -> Optional[Dag]:
        old_dag = self._current_dag
        if self._previous_dags:
            self._current_dag = self._previous_dags.pop()
        else:
            self._current_dag = None
        return old_dag

    def get_current_dag(self) -> Optional[Dag]:
        return self._current_dag


_dag_context = _DagContext()
# Exposed via `sky.dag.*`.
push_dag = _dag_context.push_dag
pop_dag = _dag_context.pop_dag
get_current_dag = _dag_context.get_current_dag
