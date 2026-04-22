"""Tests for CLI-level input validation on sky launch / sky jobs launch.

These tests cover validation that should fire *before* any server call,
rejecting obviously-malformed flag values with actionable errors.
"""
from click import testing as cli_testing

from sky.client.cli import command


def test_jobs_launch_num_jobs_must_be_positive():
    # --num-jobs 0 / negative should fail fast with a clear error, before
    # the CLI tries to contact the API server.
    runner = cli_testing.CliRunner()
    for bad in ['0', '-1', '-100']:
        result = runner.invoke(
            command.jobs_launch,
            ['--pool', 'mypool', '--num-jobs', bad, 'echo', 'hi'])
        assert result.exit_code != 0
        assert '--num-jobs' in result.output
