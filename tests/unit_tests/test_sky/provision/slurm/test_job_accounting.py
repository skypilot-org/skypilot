"""Parsing sacct records (SlurmClient's two accounting reads).

The fixtures are real output captured from a Slurm 24.11 cluster, because
every surprise this parse has to handle came from there: a base array id
answering with one row per element, `Unknown` where a timestamp is expected,
and a submit line that contains the separator.
"""
import re
import time
from unittest import mock

from sky.adaptors import slurm


def _client(stdout: str, rc: int = 0):
    client = slurm.SlurmClient.__new__(slurm.SlurmClient)
    client._run_slurm_cmd = mock.Mock(  # pylint: disable=protected-access
        return_value=(rc, stdout, ''))
    return client


def _cmd(client) -> str:
    return client._run_slurm_cmd.call_args.args[0]  # pylint: disable=protected-access


def test_a_finished_job():
    out = ('17213|COMPLETED|Dependency|1757304347|1757305675|1757305675|'
           '1757305676|0:0|0:0|0|dev|gpu-1|'
           'sbatch -p dev -N 1 --job-name=e2e-f2-or --wrap hostname')
    rows = _client(out).get_job_accounting('17213')
    assert len(rows) == 1
    row = rows[0]
    assert row['state'] == 'COMPLETED'
    assert row['reason'] == 'Dependency'
    assert row['submit'] == '1757304347'
    assert row['eligible'] == '1757305675'
    assert row['partition'] == 'dev'
    assert row['nodes'] == 'gpu-1'
    assert row['exit_code'] == '0:0'
    assert row['restarts'] == '0'


def test_a_submit_line_containing_the_separator_survives():
    """SubmitLine is last precisely because it can contain anything -- a
    piped command in --wrap would otherwise eat the parse."""
    out = ('17300|COMPLETED||1757304347|1757304347|1757304348|1757304408|'
           '0:0|0:0|0|dev|gpu-1|sbatch --wrap "squeue | head -3 | wc -l"')
    rows = _client(out).get_job_accounting('17300')
    assert len(rows) == 1
    assert rows[0]['submit_line'] == 'sbatch --wrap "squeue | head -3 | wc -l"'


def test_a_base_array_id_answers_per_element():
    """Measured: the same instant has one element running and one pending, so
    a caller passing a base id groups by the returned JobID."""
    out = ('17221_1|RUNNING||1757322030|1757322037|1757322037|Unknown|'
           '0:0|0:0|0|dev|gpu-1|sbatch --array=1-2\n'
           '17221_2|PENDING||1757322030|Unknown|Unknown|Unknown|'
           '0:0|0:0|0|dev|None assigned|sbatch --array=1-2')
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


def test_the_command_asks_for_every_attempt_in_epoch_seconds():
    """-D is what makes a requeued job report each attempt; without it only
    the latest survives, and a requeue resets Submit. The epoch request is
    what keeps the login node's timezone out of the answer."""
    client = _client('')
    client.get_job_accounting('42')
    cmd = _cmd(client)
    assert ' -D ' in cmd
    assert ' -X ' in cmd
    assert 'SubmitLine' in cmd
    assert cmd.startswith('SLURM_TIME_FORMAT=%s ')


def test_an_id_selector_needs_no_window():
    """sacct's default start time is the epoch for -j, so the whole history
    of the job is in scope without one."""
    client = _client('')
    client.get_job_accounting('42')
    assert ' -S ' not in _cmd(client)


def test_a_name_selector_always_carries_a_window():
    """With --name instead of -j, sacct's default window starts at 00:00:00
    today -- a job submitted yesterday would silently be missing."""
    client = _client('')
    client.get_job_accounting_by_name('sky-train-1-abcd',
                                      since=int(time.time()) - 3 * 3600)
    cmd = _cmd(client)
    assert '--name=sky-train-1-abcd' in cmd
    # ~3h back, expressed relatively so neither host's timezone matters.
    # The margin makes the exact figure 181 or 182, depending on where in the
    # second the call landed.
    minutes = int(re.search(r' -S now-(\d+)minutes', cmd).group(1))
    assert 181 <= minutes <= 182


def test_a_window_is_never_shorter_than_a_minute():
    client = _client('')
    client.get_job_accounting_by_name('sky-train-1-abcd',
                                      since=int(time.time()))
    assert ' -S now-2minutes' in _cmd(client)


def test_a_job_name_is_quoted():
    """The name comes from a cluster record, not from a literal."""
    client = _client('')
    client.get_job_accounting_by_name('weird name; rm -rf /', since=0)
    assert "'weird name; rm -rf /'" in _cmd(client)
