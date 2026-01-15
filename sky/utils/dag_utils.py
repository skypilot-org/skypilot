"""Utilities for loading and dumping DAGs from/to YAML files."""
import copy
from typing import Any, Dict, List, Optional, Tuple, Union

from sky import dag as dag_lib
from sky import sky_logging
from sky import task as task_lib
from sky.utils import cluster_utils
from sky.utils import registry
from sky.utils import ux_utils
from sky.utils import yaml_utils

logger = sky_logging.init_logger(__name__)

# Message thrown when APIs sky.{exec,launch,jobs.launch}() received a string
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

  sky.jobs.launch(task, ...)
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


def _load_chain_dag(
        configs: List[Dict[str, Any]],
        env_overrides: Optional[List[Tuple[str, str]]] = None,
        secrets_overrides: Optional[List[Tuple[str,
                                               str]]] = None) -> dag_lib.Dag:
    """Loads a chain DAG from a list of YAML configs."""
    dag_name = None
    if set(configs[0].keys()) == {'name'}:
        dag_name = configs[0]['name']
        configs = configs[1:]
    elif len(configs) == 1:
        dag_name = configs[0].get('name')

    if not configs:
        # YAML has only `name: xxx`. Still instantiate a task.
        configs = [{'name': dag_name}]

    current_task = None
    with dag_lib.Dag() as dag:
        for task_config in configs:
            if task_config is None:
                continue
            task = task_lib.Task.from_yaml_config(task_config, env_overrides,
                                                  secrets_overrides)
            if current_task is not None:
                current_task >> task  # pylint: disable=pointless-statement
            current_task = task
    dag.name = dag_name
    return dag


def load_chain_dag_from_yaml(
    path: str,
    env_overrides: Optional[List[Tuple[str, str]]] = None,
    secret_overrides: Optional[List[Tuple[str, str]]] = None,
) -> dag_lib.Dag:
    """Loads a chain DAG from a YAML file.

    Has special handling for an initial section in YAML that contains only the
    'name' field, which is the DAG name.

    'env_overrides' is a list of (key, value) pairs that will be used to update
    the task's 'envs' section. If it is a chain dag, the envs will be updated
    for all tasks in the chain.

    'secrets_overrides' is a list of (key, value) pairs that will be used to
    update the task's 'secrets' section. If it is a chain dag, the secrets will
    be updated for all tasks in the chain.

    Returns:
      A chain Dag with 1 or more tasks (an empty entrypoint would create a
      trivial task).
    """
    configs = yaml_utils.read_yaml_all(path)
    return _load_chain_dag(configs, env_overrides, secret_overrides)


def load_chain_dag_from_yaml_str(
    yaml_str: str,
    env_overrides: Optional[List[Tuple[str, str]]] = None,
    secrets_overrides: Optional[List[Tuple[str, str]]] = None,
) -> dag_lib.Dag:
    """Loads a chain DAG from a YAML string.

    Has special handling for an initial section in YAML that contains only the
    'name' field, which is the DAG name.

    'env_overrides' is a list of (key, value) pairs that will be used to update
    the task's 'envs' section. If it is a chain dag, the envs will be updated
    for all tasks in the chain.

    'secrets_overrides' is a list of (key, value) pairs that will be used to
    update the task's 'secrets' section. If it is a chain dag, the secrets will
    be updated for all tasks in the chain.

    Returns:
      A chain Dag with 1 or more tasks (an empty entrypoint would create a
      trivial task).
    """
    configs = yaml_utils.read_yaml_all_str(yaml_str)
    return _load_chain_dag(configs, env_overrides, secrets_overrides)


def dump_chain_dag_to_yaml_str(dag: dag_lib.Dag,
                               use_user_specified_yaml: bool = False) -> str:
    """Dumps a chain DAG to a YAML string.

    Args:
        dag: the DAG to dump.
        redact_secrets: whether to redact secrets in the YAML string.

    Returns:
        The YAML string.
    """
    assert dag.is_chain(), dag
    configs = [{'name': dag.name}]
    for task in dag.tasks:
        configs.append(
            task.to_yaml_config(
                use_user_specified_yaml=use_user_specified_yaml))
    return yaml_utils.dump_yaml_str(configs)


def dump_chain_dag_to_yaml(dag: dag_lib.Dag, path: str) -> None:
    """Dumps a chain DAG to a YAML file.

    Args:
        dag: the DAG to dump.
        path: the path to the YAML file.
    """
    dag_str = dump_chain_dag_to_yaml_str(dag)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(dag_str)


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
        dag.name = cluster_utils.generate_cluster_name()

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
        default_strategy = registry.JOBS_RECOVERY_STRATEGY_REGISTRY.default
        assert default_strategy is not None
        for resources in list(task_.resources):
            original_job_recovery = resources.job_recovery
            job_recovery: Dict[str, Optional[Union[str, int]]] = {
                'strategy': default_strategy
            }
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
