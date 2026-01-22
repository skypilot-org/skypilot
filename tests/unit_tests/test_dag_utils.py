"""Test dag utils."""
import textwrap

import pytest
import yaml

from sky import task as task_lib
from sky.utils import dag_utils
from sky.utils import registry


def test_jobs_recovery_fill_default_values():
    """Test jobs recovery fill default values."""
    task_str = textwrap.dedent("""\
        resources:
            cpus: 2+
        """)
    task_config = yaml.safe_load(task_str)
    task = task_lib.Task.from_yaml_config(task_config)
    dag = dag_utils.convert_entrypoint_to_dag(task)
    dag_utils.fill_default_config_in_dag_for_job_launch(dag)

    resources = list(dag.tasks[0].resources)
    assert len(resources) == 1
    assert resources[0].job_recovery['strategy'].lower(
    ) == registry.JOBS_RECOVERY_STRATEGY_REGISTRY.default

    task_str = textwrap.dedent("""\
        resources:
            cpus: 2+
            job_recovery:
                max_restarts_on_errors: 3
        """)

    task_config = yaml.safe_load(task_str)
    task = task_lib.Task.from_yaml_config(task_config)
    dag = dag_utils.convert_entrypoint_to_dag(task)
    dag_utils.fill_default_config_in_dag_for_job_launch(dag)

    resources = list(dag.tasks[0].resources)
    assert len(resources) == 1
    assert resources[0].job_recovery['strategy'].lower(
    ) == registry.JOBS_RECOVERY_STRATEGY_REGISTRY.default
    assert resources[0].job_recovery['max_restarts_on_errors'] == 3

    task_str = textwrap.dedent(f"""\
        resources:
            cpus: 2+
            job_recovery:
                strategy: FAILOVER
                max_restarts_on_errors: 3
        """)

    task_config = yaml.safe_load(task_str)
    task = task_lib.Task.from_yaml_config(task_config)
    dag = dag_utils.convert_entrypoint_to_dag(task)
    dag_utils.fill_default_config_in_dag_for_job_launch(dag)

    resources = list(dag.tasks[0].resources)
    assert len(resources) == 1
    assert resources[0].job_recovery['strategy'] == 'FAILOVER'
    assert resources[0].job_recovery['max_restarts_on_errors'] == 3

    # Test with recover_on_exit_codes
    task_str = textwrap.dedent("""\
        resources:
            cpus: 2+
            job_recovery:
                max_restarts_on_errors: 3
                recover_on_exit_codes: [33, 137]
        """)

    task_config = yaml.safe_load(task_str)
    task = task_lib.Task.from_yaml_config(task_config)
    dag = dag_utils.convert_entrypoint_to_dag(task)
    dag_utils.fill_default_config_in_dag_for_job_launch(dag)

    resources = list(dag.tasks[0].resources)
    assert len(resources) == 1
    assert resources[0].job_recovery['strategy'].lower(
    ) == registry.JOBS_RECOVERY_STRATEGY_REGISTRY.default
    assert resources[0].job_recovery['max_restarts_on_errors'] == 3
    assert resources[0].job_recovery['recover_on_exit_codes'] == [33, 137]

    # Test with recover_on_exit_codes as a single integer
    task_str = textwrap.dedent("""\
        resources:
            cpus: 2+
            job_recovery:
                max_restarts_on_errors: 2
                recover_on_exit_codes: 29
        """)

    task_config = yaml.safe_load(task_str)
    task = task_lib.Task.from_yaml_config(task_config)
    dag = dag_utils.convert_entrypoint_to_dag(task)
    dag_utils.fill_default_config_in_dag_for_job_launch(dag)

    resources = list(dag.tasks[0].resources)
    assert len(resources) == 1
    assert resources[0].job_recovery['strategy'].lower(
    ) == registry.JOBS_RECOVERY_STRATEGY_REGISTRY.default
    assert resources[0].job_recovery['max_restarts_on_errors'] == 2
    # Single integer should remain as is in the YAML, normalization happens
    # in the recovery strategy executor
    assert resources[0].job_recovery['recover_on_exit_codes'] == 29

    task_str = textwrap.dedent("""\
        resources:
            cpus: 2+
            job_recovery:
        """)

    task_config = yaml.safe_load(task_str)
    task = task_lib.Task.from_yaml_config(task_config)
    dag = dag_utils.convert_entrypoint_to_dag(task)
    dag_utils.fill_default_config_in_dag_for_job_launch(dag)

    resources = list(dag.tasks[0].resources)
    assert len(resources) == 1
    assert resources[0].job_recovery['strategy'].lower(
    ) == registry.JOBS_RECOVERY_STRATEGY_REGISTRY.default

    task_str = textwrap.dedent("""\
        resources:
            cpus: 2+
            any_of:
                - cpus: 2+
                  job_recovery:
                      max_restarts_on_errors: 3
                - cpus: 4+
        """)

    task_config = yaml.safe_load(task_str)
    task = task_lib.Task.from_yaml_config(task_config)
    dag = dag_utils.convert_entrypoint_to_dag(task)
    with pytest.raises(ValueError):
        dag_utils.fill_default_config_in_dag_for_job_launch(dag)
