"""Utilities for loading and dumping DAGs from/to YAML files."""
import copy
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from sky import dag as dag_lib
from sky import sky_logging
from sky import task as task_lib
from sky.skylet import constants
from sky.utils import cluster_utils
from sky.utils import registry
from sky.utils import ux_utils
from sky.utils import yaml_utils

logger = sky_logging.init_logger(__name__)

# JobGroup header fields
_JOB_GROUP_HEADER_FIELDS = {
    'name', 'execution', 'primary_tasks', 'termination_delay'
}
_JOB_GROUP_REQUIRED_HEADER_FIELDS = {'name'}

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
    """Loads a chain DAG (pipeline) from a list of YAML configs.

    A pipeline YAML can have an optional header as the first document with:
    - name: The pipeline name
    - execution: Must be 'serial' or omitted (omitted defaults to serial)

    If the first document contains only pipeline header fields, it's treated
    as the header. Otherwise, all documents are treated as task definitions.
    """
    dag_name = None
    first_config = configs[0] if configs else None

    # Check if the first document is a pipeline header.
    # A header has only 'name', or 'name' + 'execution' (for explicit serial).
    is_header = False
    if first_config is not None:
        first_keys = set(first_config.keys())
        is_header = first_keys == {'name'
                                  } or first_keys == {'name', 'execution'}

    if is_header:
        assert first_config is not None  # For mypy
        dag_name = first_config['name']
        # Validate execution mode if specified
        execution = first_config.get('execution')
        if (execution is not None and
                execution != dag_lib.DagExecution.SERIAL.value):
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Invalid execution mode for pipeline: {execution!r}. '
                    'Pipelines must use execution: serial (or omit it). '
                    'Use execution: parallel for job groups.')
        configs = configs[1:]
    elif len(configs) == 1 and first_config is not None:
        # Single document: the task itself may have a name
        dag_name = first_config.get('name')

    if not configs:
        # YAML has only header. Still instantiate a task.
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
    # Pipelines have serial execution (explicitly or by default)
    dag.execution = dag_lib.DagExecution.SERIAL
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


def dump_dag_to_yaml_str(dag: dag_lib.Dag,
                         use_user_specified_yaml: bool = False) -> str:
    """Dumps a DAG to a YAML string, auto-detecting the DAG type.

    This function automatically chooses the correct serialization format:
    - For JobGroups: uses multi-document YAML with header + jobs
    - For chain DAGs: uses multi-document YAML with name + tasks

    Args:
        dag: the DAG to dump (can be either a chain DAG or JobGroup).
        use_user_specified_yaml: whether to use user-specified YAML format.

    Returns:
        The YAML string.
    """
    if dag.is_job_group():
        return dump_job_group_to_yaml_str(dag, use_user_specified_yaml)
    else:
        return dump_chain_dag_to_yaml_str(dag, use_user_specified_yaml)


def dump_dag_to_yaml(dag: dag_lib.Dag,
                     path: str,
                     use_user_specified_yaml: bool = False) -> None:
    """Dumps a DAG to a YAML file, auto-detecting the DAG type.

    Args:
        dag: the DAG to dump.
        path: the path to the YAML file.
        use_user_specified_yaml: whether to use user-specified YAML format.
    """
    dag_str = dump_dag_to_yaml_str(dag, use_user_specified_yaml)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(dag_str)


def load_dag_from_yaml_str(
    yaml_str: str,
    env_overrides: Optional[List[Tuple[str, str]]] = None,
    secrets_overrides: Optional[List[Tuple[str, str]]] = None,
) -> dag_lib.Dag:
    """Loads a DAG from a YAML string, auto-detecting the type.

    This function automatically detects whether the YAML represents a
    JobGroup or a chain DAG and uses the appropriate loader.

    Args:
        yaml_str: The YAML string to parse.
        env_overrides: Environment variable overrides for all tasks.
        secrets_overrides: Secrets overrides for all tasks.

    Returns:
        A Dag (either a JobGroup or chain DAG).
    """
    if is_job_group_yaml_str(yaml_str):
        return load_job_group_from_yaml_str(yaml_str, env_overrides,
                                            secrets_overrides)
    else:
        return load_chain_dag_from_yaml_str(yaml_str, env_overrides,
                                            secrets_overrides)


def load_dag_from_yaml(
    path: str,
    env_overrides: Optional[List[Tuple[str, str]]] = None,
    secrets_overrides: Optional[List[Tuple[str, str]]] = None,
) -> dag_lib.Dag:
    """Loads a DAG from a YAML file, auto-detecting the type.

    This function automatically detects whether the YAML represents a
    JobGroup or a chain DAG and uses the appropriate loader.

    Args:
        path: Path to the YAML file.
        env_overrides: Environment variable overrides for all tasks.
        secrets_overrides: Secrets overrides for all tasks.

    Returns:
        A Dag (either a JobGroup or chain DAG).
    """
    if is_job_group_yaml(path):
        return load_job_group_from_yaml(path, env_overrides, secrets_overrides)
    else:
        return load_chain_dag_from_yaml(path, env_overrides, secrets_overrides)


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


def is_job_group_yaml(path: str) -> bool:
    """Check if a YAML file defines a JobGroup.

    A JobGroup YAML is a multi-document YAML where the first document
    contains JobGroup header fields like 'execution'.

    Args:
        path: Path to the YAML file.

    Returns:
        True if this is a JobGroup YAML, False otherwise.
    """
    configs = yaml_utils.read_yaml_all(path)
    return _is_job_group_configs(configs)


def is_job_group_yaml_str(yaml_str: str) -> bool:
    """Check if a YAML string defines a JobGroup."""
    configs = yaml_utils.read_yaml_all_str(yaml_str)
    return _is_job_group_configs(configs)


def _is_job_group_configs(configs: List[Dict[str, Any]]) -> bool:
    """Check if configs represent a JobGroup.

    A YAML is a JobGroup if and only if execution is set to 'parallel'.
    If execution is omitted or set to 'serial', it's a pipeline.
    """
    if not configs or len(configs) < 2:
        # JobGroup needs at least header + 1 job
        return False

    header = configs[0]
    if header is None:
        return False

    # Check if execution mode is explicitly set to 'parallel'
    execution = header.get('execution')
    return execution == dag_lib.DagExecution.PARALLEL.value


def load_job_group_from_yaml(
    path: str,
    env_overrides: Optional[List[Tuple[str, str]]] = None,
    secrets_overrides: Optional[List[Tuple[str, str]]] = None,
) -> dag_lib.Dag:
    """Load a JobGroup from a multi-document YAML file.

    JobGroup YAML format:
        ---
        name: my-job-group
        execution: parallel
        ---
        name: trainer
        resources:
          accelerators: H100:8
        run: |
          python train.py
        ---
        name: data-processor
        resources:
          accelerators: V100:4
        run: |
          python process.py

    Args:
        path: Path to the JobGroup YAML file.
        env_overrides: Environment variable overrides for all tasks.
        secrets_overrides: Secrets overrides for all tasks.

    Returns:
        A Dag marked as a JobGroup with all jobs as tasks.

    Raises:
        ValueError: If the YAML is not a valid JobGroup format.
    """
    configs = yaml_utils.read_yaml_all(path)
    return _load_job_group(configs, env_overrides, secrets_overrides)


def load_job_group_from_yaml_str(
    yaml_str: str,
    env_overrides: Optional[List[Tuple[str, str]]] = None,
    secrets_overrides: Optional[List[Tuple[str, str]]] = None,
) -> dag_lib.Dag:
    """Load a JobGroup from a multi-document YAML string."""
    configs = yaml_utils.read_yaml_all_str(yaml_str)
    return _load_job_group(configs, env_overrides, secrets_overrides)


def _load_job_group(
    configs: List[Dict[str, Any]],
    env_overrides: Optional[List[Tuple[str, str]]] = None,
    secrets_overrides: Optional[List[Tuple[str, str]]] = None,
) -> dag_lib.Dag:
    """Load a JobGroup from parsed YAML configs.

    Args:
        configs: List of YAML document configs. First is header, rest are jobs.
        env_overrides: Environment variable overrides for all tasks.
        secrets_overrides: Secrets overrides for all tasks.

    Returns:
        A Dag marked as a JobGroup.
    """
    if not configs or len(configs) < 2:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('JobGroup YAML must have at least 2 documents: '
                             'header and at least one job definition.')

    # Parse header
    header = configs[0]
    if header is None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('JobGroup header cannot be empty.')

    # Validate header has required fields
    missing_fields = _JOB_GROUP_REQUIRED_HEADER_FIELDS - set(header.keys())
    if missing_fields:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                f'JobGroup header missing required fields: {missing_fields}')

    # Warn about unknown fields in header
    unknown_fields = set(header.keys()) - _JOB_GROUP_HEADER_FIELDS
    if unknown_fields:
        logger.warning(f'Unknown fields in JobGroup header: {unknown_fields}. '
                       'These will be ignored.')

    group_name = header['name']

    # Validate job group name is safe for shell/filesystem use
    # (alphanumeric, hyphens, underscores only)
    if not group_name or not all(c.isalnum() or c in '-_' for c in group_name):
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                f'Invalid job group name: {group_name!r}. '
                'Name must contain only alphanumeric characters, hyphens, '
                'and underscores.')

    execution_str = header.get('execution', dag_lib.DagExecution.PARALLEL.value)

    # Parse execution mode
    try:
        execution = dag_lib.DagExecution(execution_str)
    except ValueError as e:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                f'Invalid execution mode: {execution_str}. '
                f'Valid options: {[e.value for e in dag_lib.DagExecution]}'
            ) from e

    # Parse job definitions
    job_configs = configs[1:]
    if not job_configs:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('JobGroup must have at least one job definition.')

    # Create DAG using context manager pattern (consistent with _load_chain_dag)
    job_names = set()
    with dag_lib.Dag() as dag:
        for i, job_config in enumerate(job_configs):
            if job_config is None:
                continue

            # Ensure each job has a name
            job_name = job_config.get('name')
            if job_name is None:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        f'Job {i + 1} in JobGroup must have a "name" field.')

            # Validate job name is safe for shell/filesystem use
            if not all(c.isalnum() or c in '-_' for c in job_name):
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        f'Invalid job name: {job_name!r}. '
                        'Name must contain only alphanumeric characters, '
                        'hyphens, and underscores.')

            # Check for duplicate job names
            if job_name in job_names:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        f'Duplicate job name in JobGroup: {job_name}')
            job_names.add(job_name)

            # Create task from job config (auto-added to current dag context)
            task = task_lib.Task.from_yaml_config(job_config, env_overrides,
                                                  secrets_overrides)
            task.name = job_name

    # Set DAG execution properties after context manager
    dag.name = group_name
    dag.set_execution(execution)

    # Parse and validate primary_tasks
    primary_tasks = header.get('primary_tasks')
    if primary_tasks is not None:
        if not isinstance(primary_tasks, list):
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'primary_tasks must be a list of job names, '
                                 f'got {type(primary_tasks).__name__}')
        # Empty list is treated as "all jobs are primary"
        if primary_tasks:
            for job_name in primary_tasks:
                if not isinstance(job_name, str):
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError(
                            f'primary_tasks entries must be strings, '
                            f'got {type(job_name).__name__}')
                if job_name not in job_names:
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError(
                            f'primary_tasks references unknown job: '
                            f'{job_name}. Available jobs: {sorted(job_names)}')
            dag.primary_tasks = primary_tasks
        # Empty list means all jobs are primary (same as not setting it)

    # Parse and validate termination_delay
    termination_delay = header.get('termination_delay')
    if termination_delay is not None:
        if isinstance(termination_delay, (str, int)):
            # Simple format: "30s" or 30
            _validate_time_duration(str(termination_delay), 'termination_delay')
            dag.termination_delay = str(termination_delay)
        elif isinstance(termination_delay, dict):
            # Dict format: {"default": "30s", "replay-buffer": "1m"}
            for key, value in termination_delay.items():
                if not isinstance(key, str) or not isinstance(
                        value, (str, int)):
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError(
                            f'termination_delay dict must have string keys and '
                            f'string/int values, got {key!r}: {value!r}')
                if key != 'default' and key not in job_names:
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError(
                            f'termination_delay references unknown job: '
                            f'{key}. Available jobs: {sorted(job_names)} '
                            f'(or "default")')
                _validate_time_duration(str(value),
                                        f'termination_delay[{key!r}]')
            # Convert all values to strings
            dag.termination_delay = {
                k: str(v) for k, v in termination_delay.items()
            }
        else:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'termination_delay must be a string, int, or dict, '
                    f'got {type(termination_delay).__name__}')

    logger.info(f'Loaded JobGroup "{group_name}" with {len(dag.tasks)} jobs: '
                f'{[t.name for t in dag.tasks]}')

    return dag


def _validate_time_duration(duration: str, field_name: str) -> None:
    """Validate a time duration string.

    Args:
        duration: Time duration string (e.g., '30s', '5m', '1h', or '30')
        field_name: Name of the field for error messages

    Raises:
        ValueError: If the duration is invalid.
    """
    # TIME_PATTERN_SECONDS already enforces non-negative integers with optional
    # unit suffix, so we only need to check the pattern match.
    if not re.match(constants.TIME_PATTERN_SECONDS, duration):
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                f'Invalid time duration for {field_name}: {duration!r}. '
                f'Expected format: NUMBER[s|m|h|d|w] (e.g., "30s", "5m", "1h")')


def dump_job_group_to_yaml_str(dag: dag_lib.Dag,
                               use_user_specified_yaml: bool = False) -> str:
    """Dump a JobGroup DAG to a multi-document YAML string.

    Args:
        dag: The JobGroup DAG to dump.
        use_user_specified_yaml: Whether to use user-specified YAML format.

    Returns:
        Multi-document YAML string.
    """
    assert dag.is_job_group(), 'DAG is not a JobGroup'

    # Build header
    header: Dict[str, Any] = {
        'name': dag.name,
    }
    if dag.execution is not None:
        header['execution'] = dag.execution.value
    if dag.primary_tasks is not None:
        header['primary_tasks'] = dag.primary_tasks
    if dag.termination_delay is not None:
        header['termination_delay'] = dag.termination_delay

    # Build job configs
    configs: List[Dict[str, Any]] = [header]
    for task in dag.tasks:
        job_config = task.to_yaml_config(
            use_user_specified_yaml=use_user_specified_yaml)
        configs.append(job_config)

    return yaml_utils.dump_yaml_str(configs)
