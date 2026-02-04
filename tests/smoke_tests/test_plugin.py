"""Smoke tests for SkyPilot plugins."""
import os
import pathlib
import subprocess
import tempfile
import textwrap

import pytest
from smoke_tests import smoke_tests_utils

import sky

# Resolve plugin path relative to this file (tests/smoke_tests/test_plugin.py)
_PLUGIN_SOURCE_DIR = pathlib.Path(__file__).resolve().parent / 'plugin'


@pytest.mark.no_dependency
@pytest.mark.no_remote_server  # Restart is required to load the plugin
def test_plugin(generic_cloud: str):
    plugin_config_content = textwrap.dedent("""\
        plugins:
        - class: skypilot_plugin_smoketest.TestPlugin
    """)
    with tempfile.NamedTemporaryFile(prefix='plugin_config_',
                                     delete=False,
                                     mode='w') as f:
        f.write(plugin_config_content)
        plugin_config_path = f.name
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'test_plugin',
        [
            # Load the plugin config
            f'pip install tests/smoke_tests/plugin && export SKYPILOT_SERVER_PLUGINS_CONFIG={plugin_config_path} && {smoke_tests_utils.SKY_API_RESTART}',
            # Local API server is ensured by test annotation
            'curl http://127.0.0.1:46580/plugins/smoke_test/ | grep "smoke test plugin"',
            # Basic smoke test with plugin
            f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} echo hi',
            f'sky logs {name} 1 | grep "hi"',
            f'sky down -y {name}'
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.managed_jobs
@pytest.mark.no_dependency
@pytest.mark.no_remote_server  # Restart required so API picks up plugin upload config
def test_plugin_upload_to_jobs_controller(generic_cloud: str):
    """Smoke test that plugin wheels are uploaded and installed on the jobs controller.

    With remote_plugins.yaml and controller_wheel_path in plugins.yaml set,
    launching a job should trigger controller launch with plugin wheels
    mounted and installed. This test verifies that flow by running a job
    and checking controller logs for evidence of wheel install.
    """
    wheel_dir = tempfile.mkdtemp(prefix='sky_plugin_wheels_')
    subprocess.run(
        [
            'pip',
            'wheel',
            str(_PLUGIN_SOURCE_DIR),
            '-w',
            wheel_dir,
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    plugins_yaml_path = os.path.join(wheel_dir, 'plugins.yaml')
    with open(plugins_yaml_path, 'w') as f:
        f.write(
            textwrap.dedent(f"""\
            controller_wheel_path: {wheel_dir}
            plugins: []
            """))

    remote_plugins_yaml_path = os.path.join(wheel_dir, 'remote_plugins.yaml')
    with open(remote_plugins_yaml_path, 'w') as f:
        f.write(
            textwrap.dedent("""\
            plugins:
            - class: skypilot_plugin_smoketest.TestPlugin
            """))

    name = smoke_tests_utils.get_cluster_name()
    env = dict(smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV)
    env['SKYPILOT_SERVER_PLUGINS_CONFIG'] = plugins_yaml_path
    env['SKYPILOT_SERVER_REMOTE_PLUGINS_CONFIG'] = remote_plugins_yaml_path

    test = smoke_tests_utils.Test(
        'plugin_upload_jobs_controller',
        [
            smoke_tests_utils.SKY_API_RESTART,
            f'sky jobs launch -n {name} --infra {generic_cloud} '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} "echo plugin_upload_ok" -y -d',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.SUCCEEDED],
                # It is possible this timeout is not enough
                # (since the jobs controller needs to be created from scratch).
                # Increase this as needed.
                timeout=200,
            ),
            # Controller setup includes "uv pip install" (or "pip install") for plugin wheels
            (
                'sky logs $(sky status | grep sky-jobs-controller | awk \'NR==1{{print $1}}\') '
                f'$(sky jobs queue | grep {name} | awk \'NR==1{{print $1}}\') '
                # We assume this is the first time the job controller is started.
                # Else, we'd see logline like
                # "Audited 1 package in 2ms" instead of the wheel install log
                # (since the wheel is already installed)
                '--no-follow | grep -E "\\.whl"'),
            f'sky jobs cancel -y -n {name}',
        ],
        f'sky jobs cancel -y -n {name}',
        env=env,
        timeout=20 * 60,
    )
    smoke_tests_utils.run_one_test(test)
