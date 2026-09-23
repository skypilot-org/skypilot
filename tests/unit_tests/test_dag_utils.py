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


_JOB_GROUP_YAML_TEMPLATE = textwrap.dedent("""\
    name: test-group
    execution: parallel
    {header_extra}---
    name: job1
    {job1_extra}run: echo hi
    ---
    name: job2
    run: echo hi
    """)


def _load_job_group(header_extra: str = '', job1_extra: str = ''):
    return dag_utils.load_job_group_from_yaml_str(
        _JOB_GROUP_YAML_TEMPLATE.format(header_extra=header_extra,
                                        job1_extra=job1_extra))


def test_job_group_inter_connection_round_trip():
    """inter_connection parses, dumps, and survives a YAML round-trip."""
    dag = _load_job_group(header_extra='inter_connection: false\n')
    assert dag.inter_connection is False
    assert not dag.inter_connection_enabled()

    dumped = dag_utils.dump_job_group_to_yaml_str(dag)
    assert 'inter_connection: false' in dumped
    redag = dag_utils.load_job_group_from_yaml_str(dumped)
    assert redag.inter_connection is False

    # The user-specified-yaml dump variant shares the header build and
    # must also carry the field (the controller-bound serialization).
    dumped_user = dag_utils.dump_job_group_to_yaml_str(
        dag, use_user_specified_yaml=True)
    assert 'inter_connection: false' in dumped_user

    dag = _load_job_group(header_extra='inter_connection: true\n')
    assert dag.inter_connection is True
    assert dag.inter_connection_enabled()


def test_job_group_inter_connection_unset_defaults_to_enabled():
    """Unset inter_connection is enabled but not serialized."""
    dag = _load_job_group()
    assert dag.inter_connection is None
    assert dag.inter_connection_enabled()
    assert 'inter_connection' not in dag_utils.dump_job_group_to_yaml_str(dag)


def test_job_group_inter_connection_invalid_type():
    with pytest.raises(ValueError, match='inter_connection must be a boolean'):
        _load_job_group(header_extra='inter_connection: "yes"\n')


def test_job_group_inter_connection_pins_load_without_validation():
    """Infra pins load fine regardless of inter_connection.

    Semantic validation (e.g. non-Kubernetes pins contradicting
    `inter_connection: true`) lives in the optimizer, not the loader.
    """
    dag = _load_job_group(header_extra='inter_connection: true\n',
                          job1_extra='resources:\n  infra: aws\n')
    assert dag.inter_connection is True

    dag = _load_job_group(job1_extra='resources:\n  infra: aws\n')
    assert dag.inter_connection is None

    dag = _load_job_group(header_extra='inter_connection: true\n',
                          job1_extra='resources:\n  infra: k8s/my-ctx\n')
    assert dag.inter_connection is True
