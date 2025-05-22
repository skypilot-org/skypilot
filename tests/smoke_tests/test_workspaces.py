# Smoke tests for SkyPilot workspaces functionality.

import tempfile
import textwrap

import pytest
from smoke_tests import smoke_tests_utils

import sky


# ---------- Test workspace switching ----------
def test_workspace_switching(generic_cloud: str):
    # Test switching between workspaces by modifying .sky.yaml.
    #
    # This test reproduces a scenario where:
    # 1. User creates an empty .sky.yaml file
    # 2. Launches a cluster with workspace "ws-default"
    # 3. Updates .sky.yaml to set "ws-train" as active workspace
    # 4. Launches another cluster with workspace "train-ws"
    # 5. Verifies both workspaces function correctly
    ws1_name = 'ws-1'
    ws2_name = 'ws-2'
    config_content = textwrap.dedent(f"""\
        workspaces:
            {ws1_name}: {{}}
            {ws2_name}: {{}}
        """)

    with tempfile.NamedTemporaryFile(prefix='ws1_', delete=False,
                                     mode='w') as f:
        f.write(config_content)
        f.write(f'active_workspace: {ws1_name}\n')
        ws1_config_path = f.name

    with tempfile.NamedTemporaryFile(prefix='ws2_', delete=False,
                                     mode='w') as f:
        f.write(config_content)
        f.write(f'active_workspace: {ws2_name}\n')
        ws2_config_path = f.name

    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'test_workspace_switching',
        [
            # Launch first cluster with workspace ws-default
            f'export SKYPILOT_PROJECT_CONFIG={ws1_config_path}; '
            f'sky launch -y --async -c {name}-1 '
            f'--infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} '
            f'echo hi',

            # Launch second cluster with workspace train-ws
            f'export SKYPILOT_PROJECT_CONFIG={ws2_config_path}; '
            f'sky launch -y --async -c {name}-2 '
            f'--infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} '
            f'echo hi',
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                f'{name}-1', [sky.ClusterStatus.UP],
                timeout=smoke_tests_utils.get_timeout(generic_cloud)),
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                f'{name}-2', [sky.ClusterStatus.UP],
                timeout=smoke_tests_utils.get_timeout(generic_cloud)),
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-1 | grep {ws1_name}',
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-2 | grep {ws2_name}',
            f'export SKYPILOT_PROJECT_CONFIG={ws1_config_path}; '
            f's=$(sky down -y {name}-1 {name}-2); echo "$s"; echo "$s" | grep "is in workspace {ws2_name!r}, but the active workspace is {ws1_name!r}"',
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-1 && exit 1 || true',
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-2 | grep UP',
            f'export SKYPILOT_PROJECT_CONFIG={ws1_config_path}; '
            f's=$(sky down -y {name}-2 2>&1); echo "$s"; echo "$s" | grep "is in workspace {ws2_name!r}, but the active workspace is {ws1_name!r}"',
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-1 && exit 1 || true',
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-2 | grep UP',
            f's=$(sky down -y {name}-2 2>&1); echo "$s"; echo "$s" | grep "is in workspace {ws2_name!r}, but the active workspace is \'default\'"',
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-1 && exit 1 || true',
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-2 | grep UP',
            f'export SKYPILOT_PROJECT_CONFIG={ws2_config_path}; '
            f's=$(sky down -y {name}-2 2>&1); echo "$s"; echo "$s" | grep "Terminating cluster {name}-2...done."',
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-1 && exit 1 || true',
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-2 && exit 1 || true',
        ],
        teardown=
        f'export SKYPILOT_PROJECT_CONFIG={ws1_config_path}; sky down -y {name}-1; export SKYPILOT_PROJECT_CONFIG={ws2_config_path}; sky down -y {name}-2',
        timeout=smoke_tests_utils.get_timeout(generic_cloud),
    )
    smoke_tests_utils.run_one_test(test)
