import tempfile
import textwrap

from click import testing as cli_testing

import sky
from sky import exceptions
import sky.cli as cli

CLOUDS_TO_TEST = [
    'aws', 'gcp', 'ibm', 'azure', 'lambda', 'scp', 'oci', 'vsphere'
]


def test_accelerator_mismatch(enable_all_clouds):
    """Test the specified accelerator does not match the instance_type."""

    spec = textwrap.dedent("""\
        resources:
          cloud: aws
          instance_type: p3.2xlarge""")
    cli_runner = cli_testing.CliRunner()

    def _capture_mismatch_gpus_spec(file_path, gpus: str):
        result = cli_runner.invoke(cli.launch,
                                   [file_path, '--gpus', gpus, '--dryrun'])
        assert isinstance(result.exception, exceptions.ResourcesMismatchError)
        assert 'Infeasible resource demands found:' in str(result.exception)

    def _capture_match_gpus_spec(file_path, gpus: str):
        result = cli_runner.invoke(cli.launch,
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
        _capture_match_gpus_spec(f.name, 'v100:1')
        _capture_match_gpus_spec(f.name, 'V100')


def test_show_gpus():
    """
    This is a test suite for `sky show-gpus` to check functionality (but not correctness).
    The tests below correspond to the following terminal commands,
    in order:

    -> sky show-gpus
    -> sky show-gpus --all
    -> sky show-gpus V100:4
    -> sky show-gpus :4
    -> sky show-gpus V100:0
    -> sky show-gpus V100:-2
    -> sky show-gpus --cloud aws --region us-west-1
    -> sky show-gpus --cloud lambda
    -> sky show-gpus --cloud lambda --all
    -> sky show-gpus V100:4 --cloud lambda
    -> sky show-gpus V100:4 --cloud lambda --all
    """
    cli_runner = cli_testing.CliRunner()
    result = cli_runner.invoke(cli.show_gpus, [])
    assert not result.exit_code

    result = cli_runner.invoke(cli.show_gpus, ['--all'])
    assert not result.exit_code

    result = cli_runner.invoke(cli.show_gpus, ['V100:4'])
    assert not result.exit_code

    result = cli_runner.invoke(cli.show_gpus, [':4'])
    assert not result.exit_code

    result = cli_runner.invoke(cli.show_gpus, ['V100:0'])
    assert isinstance(result.exception, SystemExit)

    result = cli_runner.invoke(cli.show_gpus, ['V100:-2'])
    assert isinstance(result.exception, SystemExit)

    result = cli_runner.invoke(cli.show_gpus,
                               ['--cloud', 'aws', '--region', 'us-west-1'])
    assert not result.exit_code

    for cloud in CLOUDS_TO_TEST:
        result = cli_runner.invoke(cli.show_gpus, ['--cloud', cloud])
        assert not result.exit_code

        result = cli_runner.invoke(cli.show_gpus, ['--cloud', cloud, '--all'])
        assert not result.exit_code

        result = cli_runner.invoke(cli.show_gpus, ['V100', '--cloud', cloud])
        assert not result.exit_code

        result = cli_runner.invoke(cli.show_gpus, ['V100:4', '--cloud', cloud])
        assert not result.exit_code

        result = cli_runner.invoke(cli.show_gpus,
                                   ['V100:4', '--cloud', cloud, '--all'])
        assert isinstance(result.exception, SystemExit)
