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
    """Convert the entrypoint to a sky.Dag.

    Raises TypeError if 'entrypoint' is not a 'sky.Task' or 'sky.Dag'.
    """
    # Not suppressing stacktrace: when calling this via API user may want to
    # see their own program in the stacktrace. Our CLI impl would not trigger
    # these errors.
    if isinstance(entrypoint, str):
        with ux_utils.print_exception_no_traceback():
            raise TypeError(_ENTRYPOINT_STRING_AS_DAG_MESSAGE)
    elif isinstance(entrypoint, dag_lib.Dag):
        return copy.deepcopy(entrypoint)
    elif isinstance(entrypoint, task_lib.Task):
        entrypoint = copy.deepcopy(entrypoint)
        with dag_lib.Dag() as dag:
            dag.add(entrypoint)
            dag.name = entrypoint.name
        return dag
    else:
        with ux_utils.print_exception_no_traceback():
            raise TypeError(
                'Expected a sky.Task or sky.Dag but received argument of type: '
                f'{type(entrypoint)}')


def load_chain_dag_from_yaml(
    path: str,
    env_overrides: Optional[List[Tuple[str, str]]] = None,
) -> dag_lib.Dag:
    """Loads a chain DAG from a YAML file.

    Has special handling for an initial section in YAML that contains only the
    'name' field, which is the DAG name.

    'env_overrides' is in effect only when there's exactly one task. It is a
    list of (key, value) pairs that will be used to update the task's 'envs'
    section.

    Returns:
      A chain Dag with 1 or more tasks (an empty entrypoint would create a
      trivial task).
    """
    configs = common_utils.read_yaml_all(path)
    dag_name = None
    if set(configs[0].keys()) == {'name'}:
        dag_name = configs[0]['name']
        configs = configs[1:]
    elif len(configs) == 1:
        dag_name = configs[0].get('name')

    if len(configs) == 0:
        # YAML has only `name: xxx`. Still instantiate a task.
        configs = [{'name': dag_name}]

    if len(configs) > 1:
        # TODO(zongheng): in a chain DAG of N tasks, cli.py currently makes the
        # decision to not apply overrides. Here we maintain this behavior. We
        # can listen to user feedback to change this.
        env_overrides = None

    current_task = None
    with dag_lib.Dag() as dag:
        for task_config in configs:
            if task_config is None:
                continue
            task = task_lib.Task.from_yaml_config(task_config, env_overrides)
            if current_task is not None:
                current_task >> task  # pylint: disable=pointless-statement
            current_task = task
    dag.name = dag_name
    return dag


def dump_chain_dag_to_yaml(dag: dag_lib.Dag, path: str) -> None:
    assert dag.is_chain(), dag
    configs = [{'name': dag.name}]
    for task in dag.tasks:
        configs.append(task.to_yaml_config())
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
        for resources in list(task_.resources):
            change_default_value: Dict[str, Any] = {}
            if resources.job_recovery is None:
                change_default_value[
                    'job_recovery'] = jobs.DEFAULT_RECOVERY_STRATEGY

            new_resources = resources.copy(**change_default_value)
            new_resources_list.append(new_resources)

        job_recovery_strategy = new_resources_list[0].job_recovery
        for resource in new_resources_list:
            if resource.job_recovery != job_recovery_strategy:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError('All resources in the task must have'
                                     'the same job recovery strategy.')

        task_.set_resources(type(task_.resources)(new_resources_list))
