# Smoke tests for SkyPilot workspaces functionality.

import os
import tempfile
import textwrap

import pytest
from smoke_tests import smoke_tests_utils

import sky
from sky import skypilot_config


# ---------- Test workspace switching ----------
@pytest.mark.no_remote_server
@pytest.mark.no_dependency
def test_workspace_switching(generic_cloud: str):
    # Test switching between workspaces by modifying .sky.yaml.
    #
    # This test reproduces a scenario where:
    # 1. User creates an empty .sky.yaml file
    # 2. Launches a cluster with workspace "ws-default"
    # 3. Updates .sky.yaml to set "ws-train" as active workspace
    # 4. Launches another cluster with workspace "train-ws"
    # 5. Verifies both workspaces function correctly
    if not smoke_tests_utils.is_in_buildkite_env():
        pytest.skip(
            'Skipping workspace switching test when not in Buildkite environment'
        )
    if smoke_tests_utils.is_remote_server_test():
        pytest.skip(
            'This test requires a local API server and needs to restart the server during execution. '
            'If the API server endpoint is set in the environment file, restarting is not supported, '
            'so the test will be skipped.')

    ws1_name = 'ws-1'
    ws2_name = 'ws-2'
    server_config_content = textwrap.dedent(f"""\
        workspaces:
            {ws1_name}: {{}}
            {ws2_name}: {{}}
    """)
    ws1_config_content = textwrap.dedent(f"""\
        active_workspace: {ws1_name}
    """)
    ws2_config_content = textwrap.dedent(f"""\
        active_workspace: {ws2_name}
    """)
    with tempfile.NamedTemporaryFile(prefix='server_config_',
                                     delete=False,
                                     mode='w') as f:
        f.write(server_config_content)
        server_config_path = f.name

    with tempfile.NamedTemporaryFile(prefix='ws1_', delete=False,
                                     mode='w') as f:
        f.write(ws1_config_content)
        ws1_config_path = f.name

    with tempfile.NamedTemporaryFile(prefix='ws2_', delete=False,
                                     mode='w') as f:
        f.write(ws2_config_content)
        ws2_config_path = f.name

    change_config_cmd = 'rm -f .sky.yaml || true && cp {config_path} .sky.yaml'

    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'test_workspace_switching',
        [
            f'export {skypilot_config.ENV_VAR_GLOBAL_CONFIG}={server_config_path} && {smoke_tests_utils.SKY_API_RESTART}',
            # Launch first cluster with workspace ws-default
            change_config_cmd.format(config_path=ws1_config_path),
            f'sky launch -y --async -c {name}-1 '
            f'--infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} '
            f'echo hi',
            # Launch second cluster with workspace train-ws
            change_config_cmd.format(config_path=ws2_config_path),
            f'sky launch -y -c {name}-2 '
            f'--infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} '
            f'echo hi',
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                f'{name}-1', [sky.ClusterStatus.UP],
                timeout=smoke_tests_utils.get_timeout(generic_cloud)),
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-1 | grep {ws1_name}',
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-2 | grep {ws2_name}',
            change_config_cmd.format(config_path=ws1_config_path),
            f's=$(sky down -y {name}-1 {name}-2); echo "$s"; echo "$s" | grep "is in workspace {ws2_name!r}, but the active workspace is {ws1_name!r}"',
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-1 && exit 1 || true',
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-2 | grep UP',
            f's=$(sky down -y {name}-2 2>&1); echo "$s"; echo "$s" | grep "is in workspace {ws2_name!r}, but the active workspace is {ws1_name!r}"',
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-1 && exit 1 || true',
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-2 | grep UP',
            f'rm -f .sky.yaml || true',
            f's=$(sky down -y {name}-2 2>&1); echo "$s"; echo "$s" | grep "is in workspace {ws2_name!r}, but the active workspace is \'default\'"',
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-1 && exit 1 || true',
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-2 | grep UP',
            change_config_cmd.format(config_path=ws2_config_path),
            f's=$(sky down -y {name}-2 2>&1); echo "$s"; echo "$s" | grep "Terminating cluster {name}-2...done."',
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-1 && exit 1 || true',
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-2 && exit 1 || true',
        ],
        teardown=(
            f'{change_config_cmd.format(config_path=ws1_config_path)} && sky down -y {name}-1; '
            f'{change_config_cmd.format(config_path=ws2_config_path)} && sky down -y {name}-2; '
            f'rm -f .sky.yaml || true; '
            # restore the original config
            f'export {skypilot_config.ENV_VAR_GLOBAL_CONFIG}= && {smoke_tests_utils.SKY_API_RESTART}'
        ),
        timeout=smoke_tests_utils.get_timeout(generic_cloud),
    )
    smoke_tests_utils.run_one_test(test)
    os.unlink(server_config_path)
    os.unlink(ws1_config_path)
    os.unlink(ws2_config_path)
