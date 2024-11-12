"""Utilities for loading and dumping DAGs from/to YAML files."""
import copy
from typing import Any, Dict, List, Optional, Tuple

from sky import dag as dag_lib
from sky import jobs
from sky import sky_logging
from sky import task as task_lib
from sky.backends import backend_utils
from sky.utils import common_utils
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

# Message thrown when APIs sky.{exec,launch,spot.launch}() received a string
# instead of a Dag.  CLI (cli.py) is implemented by us so should not trigger
# this.
_ENTRYPOINT_STRING_AS_DAG_MESSAGE = """\
Expected a sky.Task or sky.Dag but received a string.

If you meant to run a command, make it a Task's run command:

    task = sky.Task(run=command)

The command can then be run as:

  sky.exec(task, cluster_name=..., ...)
  # Or use {'V100': 1}, 'V100:0.5', etc.
  task.set_resources(sky.Resources(accelerators='V100:1'))
  sky.exec(task, cluster_name=..., ...)

  sky.launch(task, ...)

  sky.spot.launch(task, ...)
""".strip()


def convert_entrypoint_to_dag(entrypoint: Any) -> 'dag_lib.Dag':
    """Converts the entrypoint to a sky.Dag and applies the policy.

    Raises TypeError if 'entrypoint' is not a 'sky.Task' or 'sky.Dag'.
    """
    # Not suppressing stacktrace: when calling this via API user may want to
    # see their own program in the stacktrace. Our CLI impl would not trigger
    # these errors.
    converted_dag: 'dag_lib.Dag'
    if isinstance(entrypoint, str):
        with ux_utils.print_exception_no_traceback():
            raise TypeError(_ENTRYPOINT_STRING_AS_DAG_MESSAGE)
    elif isinstance(entrypoint, dag_lib.Dag):
        converted_dag = copy.deepcopy(entrypoint)
    elif isinstance(entrypoint, task_lib.Task):
        entrypoint = copy.deepcopy(entrypoint)
        with dag_lib.Dag() as dag:
            dag.add(entrypoint)
            dag.name = entrypoint.name
        converted_dag = dag
    else:
        with ux_utils.print_exception_no_traceback():
            raise TypeError(
                'Expected a sky.Task or sky.Dag but received argument of type: '
                f'{type(entrypoint)}')

    return converted_dag


# TODO(andy): We should migrate the function into sky.Dag.from_yaml()
def load_dag_from_yaml(
    path: str,
    env_overrides: Optional[List[Tuple[str, str]]] = None,
) -> dag_lib.Dag:
    """Loads a DAG from a YAML file.

    Supports various formats:
    1. Tasks without explicit flow definition:
       - Single task
       - Multiple tasks separated, with implicit linear dependency
    2. DAG with explicit 'edges' field, each edge can specify:
       - source: source task name
       - target: target task name
       - data: optional data transfer info
         - path: data path on target node
         - size_gb: estimated data size in GB

    'env_overrides' is a list of (key, value) pairs that will be used to update
    the task's 'envs' section. For multi-task DAGs, the envs will be updated
    for all tasks.

    Returns:
      A Dag with 1 or more tasks (an empty entrypoint will create a
      trivial task).

    Raises:
      ValueError: If the YAML structure is invalid or inconsistent.
    """
    configs = common_utils.read_yaml_all(path)
    dag = dag_lib.Dag()

    if not configs:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Empty YAML file.')

    has_header = set(configs[0].keys()) <= {'name', 'edges'}
    edges: List[Dict[str, Any]]
    if has_header:
        header = configs.pop(0)
        dag.name = header.get('name')
        edges = header.get('edges', [])
    else:
        dag.name = configs[0].get('name')
        edges = []

    # Handle YAML with only 'name'
    if not configs:
        configs = [{'name': dag.name}]

    # Create tasks
    tasks = [
        task_lib.Task.from_yaml_config(config, env_overrides)
        for config in configs
    ]

    if not tasks:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('No tasks defined in the YAML file')

    for task in tasks:
        dag.add(task)

    # Handle dependencies
    if edges:
        # Explicit dependencies
        for edge in edges:
            source = edge['source']
            target = edge['target']
            task_edge = dag.add_edge(source, target)
            if 'data' in edge:
                data = edge['data']
                task_edge.with_data(source_path=data['source_path'],
                                    target_path=data['target_path'],
                                    size_gb=data['size_gb'])
    else:
        # Implicit dependency
        for i in range(len(tasks) - 1):
            dag.add_edge(tasks[i], tasks[i + 1])

    return dag


# TODO(andy): We should migrate the function into sky.Dag.to_yaml()
def dump_dag_to_yaml(dag: dag_lib.Dag, path: str) -> None:
    """Dumps a DAG to a YAML file.

    This function supports both chain DAGs and DAGs with more complex dependency
    structures.

    Args:
        dag: The DAG object to be dumped.
        path: The file path where the YAML will be written.
    """
    header: Dict[str, Any] = {'name': dag.name}

    edges: List[Dict[str, Any]] = []
    for edge in dag.get_edges():
        edge_dict: Dict[str, Any] = {
            'source': edge.source.name,
            'target': edge.target.name
        }
        if edge.data is not None:
            edge_dict['data'] = {
                'source_path': edge.data.source_path,
                'target_path': edge.data.target_path,
                'size_gb': edge.data.size_gb
            }
        edges.append(edge_dict)

    if edges:
        header['edges'] = edges

    task_configs = [task.to_yaml_config() for task in dag.tasks]
    configs = [header] + task_configs
    common_utils.dump_yaml(path, configs)


def maybe_infer_and_fill_dag_and_task_names(dag: dag_lib.Dag) -> None:
    """Infer and fill the dag/task name if it is None.

    This is mostly for display purpose, to make sure the dag/task name is
    readable and meaningful, instead of a random UUID.
    """
    first_task = dag.tasks[0]
    if len(dag.tasks) == 1:
        if dag.name is not None:
            first_task.name = dag.name
        elif first_task.name is not None:
            dag.name = first_task.name

    if dag.name is None:
        dag.name = backend_utils.generate_cluster_name()

    if len(dag.tasks) == 1:
        if first_task.name is None:
            first_task.name = dag.name
    else:
        for task_id, task in enumerate(dag.tasks):
            if task.name is None:
                task.name = f'{dag.name}-{task_id}'


def fill_default_config_in_dag_for_job_launch(dag: dag_lib.Dag) -> None:
    for task_ in dag.tasks:

        new_resources_list = []
        default_strategy = jobs.DEFAULT_RECOVERY_STRATEGY
        assert default_strategy is not None
        for resources in list(task_.resources):
            original_job_recovery = resources.job_recovery
            job_recovery = {'strategy': default_strategy}
            if isinstance(original_job_recovery, str):
                job_recovery['strategy'] = original_job_recovery
            elif isinstance(original_job_recovery, dict):
                job_recovery.update(original_job_recovery)
                strategy = job_recovery.get('strategy')
                if strategy is None:
                    job_recovery['strategy'] = default_strategy
            change_default_value: Dict[str, Any] = {
                'job_recovery': job_recovery
            }

            new_resources = resources.copy(**change_default_value)
            new_resources_list.append(new_resources)

        job_recovery_strategy = new_resources_list[0].job_recovery
        for resource in new_resources_list:
            if resource.job_recovery != job_recovery_strategy:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError('All resources in the task must have'
                                     'the same job recovery strategy.')

        task_.set_resources(type(task_.resources)(new_resources_list))
