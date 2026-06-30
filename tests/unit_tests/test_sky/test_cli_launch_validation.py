"""Tests for CLI-level input validation on sky launch / sky jobs launch.

These tests cover validation that should fire *before* any server call,
rejecting obviously-malformed flag values with actionable errors.
"""
from unittest import mock

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


def test_jobs_launch_num_jobs_allowed_without_pool():
    # --num-jobs without --pool should be accepted: it submits N independent
    # managed jobs, each on its own cluster. The CLI must not reject it with a
    # usage error; it should reach the launch call with pool=None.
    runner = cli_testing.CliRunner()
    with mock.patch.object(command.managed_jobs, 'launch') as mock_launch, \
            mock.patch.object(command, '_async_call_or_wait') as mock_wait:
        mock_wait.return_value = ([1, 2], None)
        result = runner.invoke(command.jobs_launch,
                               ['--num-jobs', '2', '-y', 'echo', 'hi'])
    assert 'Cannot specify --num-jobs without --pool' not in result.output
    assert mock_launch.called
    # launch(dag, name, pool, num_jobs, ...): pool positional is None,
    # num_jobs positional is 2.
    args, kwargs = mock_launch.call_args
    assert args[2] is None  # pool
    assert args[3] == 2  # num_jobs


def test_jobs_launch_multi_job_output_without_pool_has_no_pool_hints():
    # A multi-job launch without a pool must not emit pool-specific hints:
    # no `value=None` dashboard filter, and no `sky jobs cancel --pool None`.
    runner = cli_testing.CliRunner()
    with mock.patch.object(command.managed_jobs, 'launch'), \
            mock.patch.object(command, '_async_call_or_wait') as mock_wait, \
            mock.patch.object(command.server_common, 'is_api_server_local',
                              return_value=False), \
            mock.patch.object(command.server_common, 'get_server_url',
                              return_value='https://server'), \
            mock.patch.object(command.server_common, 'get_dashboard_url',
                              side_effect=lambda url, starting_page:
                              f'https://server/dashboard/{starting_page}'):
        mock_wait.return_value = ([1, 2, 3], None)
        result = runner.invoke(command.jobs_launch,
                               ['--num-jobs', '3', '-y', 'echo', 'hi'])
    assert result.exit_code == 0, result.output
    # No pool-specific output.
    assert 'property=pool' not in result.output
    assert 'value=None' not in result.output
    assert '--pool None' not in result.output
    assert 'in the pool' not in result.output
    # Non-pool variants are present instead.
    assert 'Show all jobs:' in result.output


def test_jobs_launch_many_jobs_output_collapses_ids_and_uses_placeholder():
    # With many jobs, the submitted-IDs line collapses contiguous IDs into a
    # range, and the cancel hint uses a `<job-ids>` placeholder rather than
    # dumping every id.
    runner = cli_testing.CliRunner()
    job_ids = list(range(2711, 3511))  # 800 contiguous ids, like the report
    with mock.patch.object(command.managed_jobs, 'launch'), \
            mock.patch.object(command, '_async_call_or_wait') as mock_wait, \
            mock.patch.object(command.server_common, 'is_api_server_local',
                              return_value=False), \
            mock.patch.object(command.server_common, 'get_server_url',
                              return_value='https://server'), \
            mock.patch.object(command.server_common, 'get_dashboard_url',
                              return_value='https://server/dashboard/jobs'):
        mock_wait.return_value = (job_ids, None)
        result = runner.invoke(command.jobs_launch,
                               ['--num-jobs', '800', '-y', 'echo', 'hi'])
    assert result.exit_code == 0, result.output
    # Submitted IDs collapse to a single range.
    assert 'Jobs submitted with IDs: 2711-3510' in result.output
    # The cancel hint is a placeholder, not the full id list.
    assert 'sky jobs cancel <job-ids>' in result.output
    assert '2711 2712 2713' not in result.output


def test_env_file_must_exist():
    # --env-file on a non-existent path previously returned {} silently
    # (dotenv's default behavior) and the user never learned the file
    # was missing. It should now error with a clear message.
    runner = cli_testing.CliRunner()
    result = runner.invoke(
        command.launch, ['--env-file', '/no/such/env/file.env', 'echo', 'hi'])
    assert result.exit_code != 0
    assert '/no/such/env/file.env' in result.output


def test_secret_file_must_exist():
    runner = cli_testing.CliRunner()
    result = runner.invoke(
        command.launch,
        ['--secret-file', '/no/such/secret/file.env', 'echo', 'hi'])
    assert result.exit_code != 0
    assert '/no/such/secret/file.env' in result.output
