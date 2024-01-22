"""Utilities for loading and dumping DAGs from/to YAML files."""
from typing import Any, Dict, List, Optional, Tuple

from sky import dag as dag_lib
from sky import sky_logging
from sky import spot
from sky import task as task_lib
from sky.backends import backend_utils
from sky.utils import common_utils
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)


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


def fill_default_spot_config_in_dag_for_spot_launch(dag: dag_lib.Dag) -> None:
    for task_ in dag.tasks:

        new_resources_list = []
        for resources in list(task_.resources):
            change_default_value: Dict[str, Any] = {}
            if resources.use_spot_specified and not resources.use_spot:
                logger.info(
                    'Field `use_spot` is set to false but a managed spot job is '  # pylint: disable=line-too-long
                    'being launched. Ignoring the field and proceeding to use spot '  # pylint: disable=line-too-long
                    'instance(s).')
            change_default_value['use_spot'] = True
            if resources.spot_recovery is None:
                change_default_value[
                    'spot_recovery'] = spot.SPOT_DEFAULT_STRATEGY

            new_resources = resources.copy(**change_default_value)
            new_resources_list.append(new_resources)

        spot_recovery_strategy = new_resources_list[0].spot_recovery
        for resource in new_resources_list:
            if resource.spot_recovery != spot_recovery_strategy:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError('All resources in the task must have'
                                     'the same spot recovery strategy.')

        if isinstance(task_.resources, list):
            task_.set_resources(new_resources_list)
        else:
            task_.set_resources(set(new_resources_list))
