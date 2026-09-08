"""Turning sacct records into a job's Slurm timeline (utils.job_timeline).

The two intervals are the reason this exists, so most of what is asserted
here is which side of `Eligible` a wait landed on and how the three shapes
Slurm actually produces are read: a finished job, a job cancelled before it
ever became eligible, and a job that is still queued.
"""
import subprocess
import time
from unittest import mock

import pytest

from sky.provision.slurm import utils

_CLUSTER = 'dev-slurm'
_NAME = 'sky-train-1-a1b2'
# Fixed instants, 22m 8s from submit to eligible and 19m 4s from there to the
# start, so the rendered durations are checkable.
_SUBMIT = 1000000000
_ELIGIBLE = 1000001328
_START = 1000002472
_END = 1000010000


def _record(**over):
    record = {
        'job_id': '17213',
        'state': 'COMPLETED',
        'reason': '',
        'submit': str(_SUBMIT),
        'eligible': str(_ELIGIBLE),
        'start': str(_START),
        'end': str(_END),
        'exit_code': '0:0',
        'derived_exit_code': '0:0',
        'restarts': '0',
        'partition': 'h200',
        'nodes': 'gpu-1,gpu-2',
        'submit_line': 'sbatch --job-name=sky-train-1-a1b2 ...',
    }
    record.update(over)
    return record


@pytest.fixture
def client():
    fake = mock.MagicMock()
    fake.get_job_accounting_by_name.return_value = [_record()]
    fake.query_jobs.return_value = []
    with mock.patch.object(utils, '_client_for', return_value=fake):
        yield fake


def _timeline(**kwargs):
    return utils.job_timeline(_CLUSTER, _NAME, _SUBMIT - 60, **kwargs)


def _by_event(entries):
    return {entry['event']: entry for entry in entries}


def test_a_finished_job_reports_both_waits(client):
    entries = _timeline()
    assert [e['event'] for e in entries
           ] == ['submitted', 'eligible', 'started', 'ended']
    events = _by_event(entries)
    assert events['submitted']['at'] == _SUBMIT
    assert 'to partition h200' in events['submitted']['text']
    # The wait before Eligible can only be one of these three.
    assert '22m 8s' in events['eligible']['text']
    assert 'dependency, a hold or a begin time' in events['eligible']['text']
    # The wait after it can only be one of these.
    assert '19m 4s' in events['started']['text']
    assert 'resources, priority or a quota limit' in events['started']['text']
    assert 'gpu-1,gpu-2' in events['started']['text']
    assert 'COMPLETED' in events['ended']['text']
    assert '2h 5m 28s' in events['ended']['text']
    # Every row says which system it came from.
    assert all(entry['text'].startswith('Slurm ') for entry in entries)


def test_no_wait_is_said_plainly_rather_than_as_zero(client):
    client.get_job_accounting_by_name.return_value = [
        _record(eligible=str(_SUBMIT), start=str(_SUBMIT))
    ]
    events = _by_event(_timeline())
    assert 'as soon as it was submitted' in events['eligible']['text']
    assert 'as soon as it became eligible' in events['started']['text']


def test_a_job_cancelled_before_becoming_eligible(client):
    """Measured: Eligible and Start are the literal 'Unknown' for a job
    cancelled while still blocked. That is a fact to state, not a gap."""
    client.get_job_accounting_by_name.return_value = [
        _record(state='CANCELLED by 1000',
                reason='DependencyNeverSatisfied',
                eligible='Unknown',
                start='Unknown',
                nodes='None assigned')
    ]
    entries = _timeline()
    assert [e['event'] for e in entries
           ] == ['submitted', 'never_eligible', 'ended']
    events = _by_event(entries)
    assert 'never became eligible' in events['never_eligible']['text']
    assert 'DependencyNeverSatisfied' in events['never_eligible']['text']
    # No node list to report, and no run duration to claim.
    assert 'None assigned' not in events['ended']['text']
    assert 'after running' not in events['ended']['text']


def test_a_pending_job_is_not_yet_never_eligible(client):
    """Same 'Unknown' fields as above, but the job has not ended -- calling
    that 'never eligible' would be wrong."""
    client.get_job_accounting_by_name.return_value = [
        _record(state='PENDING', eligible='Unknown', start='Unknown', end='')
    ]
    with mock.patch.object(utils, 'explain_pending_job', return_value=None):
        entries = _timeline()
    assert [e['event'] for e in entries] == ['submitted']


def test_a_running_job_gets_no_end_event(client):
    """sacct answers with a *projected* End for a job that is still running;
    reporting it would announce an end that has not happened."""
    client.get_job_accounting_by_name.return_value = [
        _record(state='RUNNING', end=str(_END))
    ]
    entries = _timeline()
    assert [e['event'] for e in entries] == ['submitted', 'eligible', 'started']


def test_a_nonzero_exit_code_is_reported_and_a_zero_one_is_not(client):
    client.get_job_accounting_by_name.return_value = [
        _record(state='FAILED', exit_code='1:0')
    ]
    assert 'exit 1:0' in _by_event(_timeline())['ended']['text']
    client.get_job_accounting_by_name.return_value = [_record()]
    assert 'exit' not in _by_event(_timeline())['ended']['text']


def test_each_attempt_of_a_requeued_job_is_numbered(client):
    """-D returns one record per attempt, so the two waits stay attributable
    to the attempt that had them."""
    client.get_job_accounting_by_name.return_value = [
        _record(state='NODE_FAIL', restarts='1'),
        _record(submit=str(_SUBMIT + 5000),
                eligible=str(_SUBMIT + 5000),
                start=str(_SUBMIT + 5010),
                end=str(_SUBMIT + 9000),
                restarts='1'),
    ]
    entries = _timeline()
    texts = [e['text'] for e in entries]
    assert any('attempt 1 of 2' in text for text in texts)
    assert any('attempt 2 of 2' in text for text in texts)
    assert any('requeued it once' in text for text in texts)


def test_entries_are_ordered_oldest_first_across_attempts(client):
    client.get_job_accounting_by_name.return_value = [
        _record(),
        _record(submit=str(_SUBMIT + 5000),
                eligible=str(_SUBMIT + 5000),
                start=str(_SUBMIT + 5010),
                end=str(_SUBMIT + 9000)),
    ]
    stamps = [entry['at'] for entry in _timeline()]
    assert stamps == sorted(stamps)


def test_a_pending_record_carries_the_live_diagnosis(client):
    client.get_job_accounting_by_name.return_value = [
        _record(state='PENDING', eligible='Unknown', start='Unknown', end='')
    ]
    explain = mock.Mock(
        return_value={
            'category': 'resources',
            'summary': 'Waiting for free resources in partition h200.',
            'action': 'Check the queue.',
        })
    with mock.patch.object(utils, 'explain_pending_job', explain):
        events = _by_event(_timeline())
    explain.assert_called_once_with(_CLUSTER, '17213', deadline=None)
    assert 'Waiting for free resources' in events['pending']['text']
    assert 'Check the queue.' in events['pending']['text']
    # The reason is live, so it is dated now rather than backdated.
    assert events['pending']['at'] > _END


def test_without_accounting_a_queued_job_still_gets_an_answer(client):
    """A cluster with no slurmdbd has no history to read, but squeue still
    knows what is queued -- and that is the job most in need of an answer."""
    client.get_job_accounting_by_name.return_value = []
    client.query_jobs.return_value = ['17999']
    explain = mock.Mock(
        return_value={
            'category': 'quota',
            'summary': 'A QoS limit is holding it.',
            'action': None,
        })
    with mock.patch.object(utils, 'explain_pending_job', explain):
        entries = _timeline()
    client.query_jobs.assert_called_once_with(_NAME, ['pending'])
    assert [e['event'] for e in entries] == ['pending']
    assert 'A QoS limit is holding it.' in entries[0]['text']


def test_a_squeue_fallback_that_fails_is_not_an_error(client):
    client.get_job_accounting_by_name.return_value = []
    client.query_jobs.side_effect = RuntimeError('ssh: connect timed out')
    assert _timeline() == []


def test_non_epoch_timestamps_are_dropped_rather_than_misplaced(client):
    """A Slurm build that ignores SLURM_TIME_FORMAT answers in the cluster's
    local time with no timezone on it; there is no way to order that against
    SkyPilot's own events, so it is not pretended otherwise."""
    client.get_job_accounting_by_name.return_value = [
        _record(submit='2026-09-08T04:05:47',
                eligible='2026-09-08T04:27:55',
                start='2026-09-08T04:27:55',
                end='2026-09-08T05:27:55')
    ]
    assert _timeline() == []


def test_an_unconfigured_cluster_is_empty_not_an_exception():
    with mock.patch.object(utils, '_client_for', return_value=None):
        assert _timeline() == []


def test_a_timed_out_accounting_read_is_empty(client):
    client.get_job_accounting_by_name.side_effect = subprocess.TimeoutExpired(
        'sacct', 20)
    assert _timeline() == []


def test_two_allocations_sharing_a_name_are_not_called_attempts(client):
    """A relaunch reuses the cluster name but gets a new Slurm job id. Only a
    requeue -- same id, one record per try -- is an attempt."""
    client.get_job_accounting_by_name.return_value = [
        _record(job_id='17213'),
        _record(job_id='17240',
                submit=str(_SUBMIT + 5000),
                eligible=str(_SUBMIT + 5000),
                start=str(_SUBMIT + 5010),
                end=str(_SUBMIT + 9000)),
    ]
    texts = [entry['text'] for entry in _timeline()]
    assert not any('attempt' in text for text in texts)
    assert any('allocation 17213' in text for text in texts)
    assert any('allocation 17240' in text for text in texts)


def test_a_spent_budget_still_returns_the_history(client):
    """The accounting read is what the timeline is made of, so it runs
    regardless; the diagnosis is three further reads and is reachable on its
    own, so that is what a spent budget gives up."""
    client.get_job_accounting_by_name.return_value = [
        _record(state='PENDING', eligible='Unknown', start='Unknown', end='')
    ]
    explain = mock.Mock()
    with mock.patch.object(utils, 'explain_pending_job', explain):
        entries = utils.job_timeline(_CLUSTER,
                                     _NAME,
                                     _SUBMIT - 60,
                                     deadline=time.monotonic() - 1)
    explain.assert_not_called()
    assert [entry['event'] for entry in entries] == ['submitted']


def test_a_spent_budget_skips_the_squeue_fallback_too(client):
    client.get_job_accounting_by_name.return_value = []
    utils.job_timeline(_CLUSTER,
                       _NAME,
                       _SUBMIT - 60,
                       deadline=time.monotonic() - 1)
    client.query_jobs.assert_not_called()


def test_the_deadline_reaches_the_diagnosis(client):
    """It has to be passed down, not just checked here: the evidence reads
    are where the time actually goes."""
    client.get_job_accounting_by_name.return_value = [
        _record(state='PENDING', eligible='Unknown', start='Unknown', end='')
    ]
    deadline = time.monotonic() + 30
    explain = mock.Mock(return_value=None)
    with mock.patch.object(utils, 'explain_pending_job', explain):
        utils.job_timeline(_CLUSTER, _NAME, _SUBMIT - 60, deadline=deadline)
    assert explain.call_args.kwargs['deadline'] == deadline
