# Tests that require higher speed or could cause potential race conditions
# that cause pytest to fail when parallel enabled are put into this file.
# All tests in this file will not enable parallel.

import tempfile
import textwrap
import time

from click import testing as cli_testing

import sky
from sky import exceptions
from sky.client.cli import command
from sky.utils import registry


def _test_optimize_speed(resources: sky.Resources):
    with sky.Dag() as dag:
        task = sky.Task(run='echo hi')
        task.set_resources(resources)
    start = time.time()
    sky.optimize(dag)
    end = time.time()
    # 8.0 seconds = somewhat flaky.
    assert end - start < 8.0, (f'optimize took too long for {resources}, '
                               f'{end - start} seconds')


def test_optimize_speed(enable_all_clouds):
    _test_optimize_speed(sky.Resources(cpus=4))
    for cloud in registry.CLOUD_REGISTRY.values():
        _test_optimize_speed(sky.Resources(infra=str(cloud), cpus='4+'))
    _test_optimize_speed(sky.Resources(cpus='4+', memory='4+'))
    _test_optimize_speed(
        sky.Resources(cpus='4+', memory='4+', accelerators='V100:1'))
    _test_optimize_speed(
        sky.Resources(cpus='4+', memory='4+', accelerators='A100-80GB:8'))
    _test_optimize_speed(
        sky.Resources(cpus='4+', memory='4+', accelerators='tpu-v3-32'))


class TestAllCloudsEnabled:

    def test_accelerator_mismatch(self, enable_all_clouds):
        """Test the specified accelerator does not match the instance_type."""

        spec = textwrap.dedent("""\
            resources:
                cloud: aws
                instance_type: p3.2xlarge""")
        cli_runner = cli_testing.CliRunner()

        def _capture_mismatch_gpus_spec(file_path, gpus: str):
            result = cli_runner.invoke(command.launch,
                                       [file_path, '--gpus', gpus, '--dryrun'])
            assert isinstance(result.exception,
                              exceptions.ResourcesMismatchError)
            assert 'Infeasible resource demands found:' in str(result.exception)

        def _capture_match_gpus_spec(file_path, gpus: str):
            result = cli_runner.invoke(command.launch,
                                       [file_path, '--gpus', gpus, '--dryrun'])
            assert not result.exit_code

        with tempfile.NamedTemporaryFile('w', suffix='.yml') as f:
            f.write(spec)
            f.flush()

            _capture_mismatch_gpus_spec(f.name, 'T4:1')
            _capture_mismatch_gpus_spec(f.name, 'T4:0.5')
            _capture_mismatch_gpus_spec(f.name, 'V100:2')
            _capture_mismatch_gpus_spec(f.name, 'v100:2')
            _capture_mismatch_gpus_spec(f.name, 'V100:0.5')

            _capture_match_gpus_spec(f.name, 'V100:1')
            _capture_match_gpus_spec(f.name, 'V100:1')
            _capture_match_gpus_spec(f.name, 'V100')

    def test_k8s_alias(self, enable_all_clouds):
        cli_runner = cli_testing.CliRunner()

        result = cli_runner.invoke(command.launch,
                                   ['--cloud', 'k8s', '--dryrun'])
        assert not result.exit_code

        result = cli_runner.invoke(command.launch,
                                   ['--cloud', 'kubernetes', '--dryrun'])
        assert not result.exit_code

        result = cli_runner.invoke(command.launch,
                                   ['--cloud', 'notarealcloud', '--dryrun'])
        assert isinstance(result.exception, ValueError)

        result = cli_runner.invoke(command.show_gpus, ['--cloud', 'k8s'])
        assert not result.exit_code

        result = cli_runner.invoke(command.show_gpus, ['--cloud', 'kubernetes'])
        assert not result.exit_code

        result = cli_runner.invoke(command.show_gpus,
                                   ['--cloud', 'notarealcloud'])
        assert isinstance(result.exception, ValueError)
