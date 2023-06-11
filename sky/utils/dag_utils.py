"""Utilities for loading and dumping DAGs from/to YAML files."""
from sky import dag as dag_lib
from sky import task as task_lib
from sky.backends import backend_utils
from sky.utils import common_utils


def load_chain_dag_from_yaml(path: str) -> dag_lib.Dag:
    configs = common_utils.read_yaml_all(path)
    dag_name = None
    if set(configs[0].keys()) == {'name'}:
        dag_name = configs[0]['name']
        configs = configs[1:]
    elif len(configs) == 1:
        dag_name = configs[0].get('name')

    current_task = None
    with dag_lib.Dag() as dag:
        for task_config in configs:
            if task_config is None:
                continue
            task = task_lib.Task.from_yaml_config(task_config)
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


def infer_and_fill_dag_name(dag: dag_lib.Dag) -> None:
    # For a singleton task, override the dag name with task name, if it is
    # not None. Otherwise, use the dag name.
    first_task = dag.tasks[0]
    if len(dag.tasks) == 1 and first_task.name is not None:
        dag.name = first_task.name

    if dag.name is None:
        dag.name = backend_utils.generate_cluster_name()

    if first_task.name is None:
        first_task.name = dag.name
