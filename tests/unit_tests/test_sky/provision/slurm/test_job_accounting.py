"""Parsing sacct records (SlurmClient.get_job_accounting).

The fixtures are real output captured from a Slurm 24.11 cluster, because
every surprise this parse has to handle came from there: a base array id
answering with one row per element, `Unknown` where a timestamp is expected,
and a submit line that contains the separator.
"""
from unittest import mock

from sky.adaptors import slurm


def _client(stdout: str, rc: int = 0):
    client = slurm.SlurmClient.__new__(slurm.SlurmClient)
    client._run_slurm_cmd = mock.Mock(  # pylint: disable=protected-access
        return_value=(rc, stdout, ''))
    return client


def test_a_finished_job():
    out = ('17213|COMPLETED|Dependency|2026-09-08T04:05:47|'
           '2026-09-08T04:27:55|2026-09-08T04:27:55|2026-09-08T04:27:55|'
           '0:0|0:0|0|sbatch -p dev -N 1 --job-name=e2e-f2-or --wrap hostname')
    rows = _client(out).get_job_accounting('17213')
    assert len(rows) == 1
    row = rows[0]
    assert row['state'] == 'COMPLETED'
    assert row['reason'] == 'Dependency'
    assert row['submit'] == '2026-09-08T04:05:47'
    assert row['eligible'] == '2026-09-08T04:27:55'
    assert row['exit_code'] == '0:0'
    assert row['restarts'] == '0'


def test_a_submit_line_containing_the_separator_survives():
    """SubmitLine is last precisely because it can contain anything -- a
    piped command in --wrap would otherwise eat the parse."""
    out = ('17300|COMPLETED||2026-09-08T04:05:47|2026-09-08T04:05:47|'
           '2026-09-08T04:05:48|2026-09-08T04:06:48|0:0|0:0|0|'
           'sbatch --wrap "squeue | head -3 | wc -l"')
    rows = _client(out).get_job_accounting('17300')
    assert len(rows) == 1
    assert rows[0]['submit_line'] == 'sbatch --wrap "squeue | head -3 | wc -l"'


def test_a_base_array_id_answers_per_element():
    """Measured: the same instant has one element running and one pending, so
    a caller passing a base id groups by the returned JobID."""
    out = ('17221_1|RUNNING||2026-09-08T09:00:30|2026-09-08T09:00:37|'
           '2026-09-08T09:00:37|Unknown|0:0|0:0|0|sbatch --array=1-2\n'
           '17221_2|PENDING||2026-09-08T09:00:30|Unknown|Unknown|Unknown|'
           '0:0|0:0|0|sbatch --array=1-2')
    rows = _client(out).get_job_accounting('17221')
    assert [r['job_id'] for r in rows] == ['17221_1', '17221_2']
    assert rows[1]['eligible'] == 'Unknown'


def test_no_accounting_configured_is_empty_not_an_error():
    """A cluster without slurmdbd is a shape, not a failure: the caller says
    the history is unavailable rather than that nothing happened."""
    rows = _client('sacct: error: accounting_storage is disabled',
                   rc=1).get_job_accounting('1')
    assert rows == []


def test_a_row_with_the_wrong_field_count_is_skipped():
    rows = _client('17400|COMPLETED|too-few-fields').get_job_accounting('17400')
    assert rows == []


def test_the_command_asks_for_every_attempt():
    """-D is what makes a requeued job report each attempt; without it only
    the latest survives, and a requeue resets Submit."""
    client = _client('')
    client.get_job_accounting('42')
    cmd = client._run_slurm_cmd.call_args.args[0]  # pylint: disable=protected-access
    assert ' -D ' in cmd
    assert ' -X ' in cmd
    assert 'SubmitLine' in cmd
