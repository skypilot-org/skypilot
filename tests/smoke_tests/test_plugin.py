"""Smoke tests for SkyPilot plugins."""
import tempfile
import textwrap

import pytest
from smoke_tests import smoke_tests_utils


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
