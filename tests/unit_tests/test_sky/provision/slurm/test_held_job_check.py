"""Tests for verifying a held Slurm job before it is released.

Slurm can adjust a job at submission (e.g. raise its CPUs per task when the
job's memory exceeds MaxMemPerCPU). The job would then run, and be accounted,
on resources that were not requested, so it is submitted held, compared with
the request, and only released if Slurm kept it.
"""
from unittest import mock

import pytest

from sky import exceptions
from sky.provision.slurm import instance as slurm_instance


def _client(cpus_per_task='4',
            num_nodes='1',
            job_state='PENDING',
            reason='JobHeldUser',
            priority=None):
    if priority is None:
        # A held job has priority 0.
        held = reason in ('JobHeldUser', 'JobHeldAdmin')
        priority = '0' if held else '4294'
    client = mock.Mock()
    client.get_job_fields.return_value = {
        'JobId': '123',
        'JobState': job_state,
        'Reason': reason,
        'Priority': priority,
        'CPUs/Task': cpus_per_task,
        'NumNodes': num_nodes,
    }
    return client


def _check(client, cpus_per_task=4, num_nodes=1, memory=64):
    slurm_instance._release_if_request_kept(client, '123', 'my-cluster',
                                            'batch', cpus_per_task, num_nodes,
                                            memory)


# --- A held job that Slurm kept is released. ---


def test_kept_request_is_released():
    client = _client()
    _check(client)
    client.get_job_fields.assert_called_once_with('123')
    client.release_job.assert_called_once_with('123')
    client.cancel_job.assert_not_called()


def test_multi_node_request_is_released():
    client = _client(cpus_per_task='4', num_nodes='2')
    _check(client, num_nodes=2)
    client.release_job.assert_called_once_with('123')


@pytest.mark.parametrize('num_nodes, requested', [('1-1', 1), ('2-2', 2)])
def test_exact_node_range_is_released(num_nodes, requested):
    client = _client(num_nodes=num_nodes)
    _check(client, num_nodes=requested)
    client.release_job.assert_called_once_with('123')
    client.cancel_job.assert_not_called()


# --- A changed shape is refused, as not retryable. ---


def test_raised_cpus_cancel_and_refuse_with_a_fix():
    client = _client(cpus_per_task='8')
    with pytest.raises(exceptions.ProvisionUnsupportedError) as e:
        _check(client)
    client.cancel_job.assert_called_once_with('123')
    client.release_job.assert_not_called()
    message = str(e.value)
    assert '8 CPUs per task instead of the 4 requested' in message
    assert "on partition 'batch'" in message
    assert 'Slurm job 123 was cancelled before it started.' in message
    # The memory may be a SkyPilot default rather than the user's, and the
    # limit may be the whole cluster's: the hint holds either way.
    assert "the job's memory (64 GB)" in message
    assert '64.0' not in message
    assert "set for partition 'batch' or for the whole cluster" in message
    assert '--cpus 8' in message
    # The cancel outcome is said once, in the error, not also as a warning.
    assert message.count('scancel') == 0


@pytest.mark.parametrize('memory', [0, None, 'not-a-number'])
def test_raised_cpus_without_a_memory_request_names_no_memory(memory):
    # Without --mem, MaxMemPerCPU cannot be the cause, so the hint is left out.
    client = _client(cpus_per_task='8')
    with pytest.raises(exceptions.ProvisionUnsupportedError) as e:
        _check(client, memory=memory)
    client.cancel_job.assert_called_once_with('123')
    message = str(e.value)
    assert '8 CPUs per task instead of the 4 requested' in message
    assert 'memory' not in message
    assert 'MaxMemPerCPU' not in message


def test_changed_node_count_cancels_and_refuses():
    client = _client(num_nodes='2')
    with pytest.raises(exceptions.ProvisionUnsupportedError,
                       match='2 nodes instead of the 1 requested'):
        _check(client)
    client.cancel_job.assert_called_once_with('123')


@pytest.mark.parametrize('num_nodes, requested', [('1-2', 1), ('2-4', 2),
                                                  ('1-2', 2)])
def test_widened_node_range_cancels_and_refuses(num_nodes, requested):
    # A range lets Slurm run the job on more (or fewer) nodes than requested.
    client = _client(num_nodes=num_nodes)
    with pytest.raises(exceptions.ProvisionUnsupportedError,
                       match=f'{num_nodes} nodes instead of the {requested} '
                       'requested'):
        _check(client, num_nodes=requested)
    client.cancel_job.assert_called_once_with('123')
    client.release_job.assert_not_called()


def test_refusal_is_not_wrapped_or_cancelled_twice():
    client = _client(cpus_per_task='8')
    with pytest.raises(exceptions.ProvisionUnsupportedError) as e:
        _check(client)
    assert str(e.value).startswith('Slurm would run cluster')
    client.cancel_job.assert_called_once_with('123')


def test_job_held_by_an_administrator_since_is_retryable():
    # Submitted with --hold, the job's own hold is a user hold: an admin hold
    # means an operator held it since, which says nothing about the
    # partition.
    client = _client(reason='JobHeldAdmin')
    with pytest.raises(RuntimeError) as e:
        _check(client)
    assert not isinstance(e.value, exceptions.ProvisionUnsupportedError)
    message = str(e.value)
    assert 'held by an administrator after it was submitted' in message
    assert 'Slurm job 123 was cancelled before it started.' in message
    client.release_job.assert_not_called()
    client.cancel_job.assert_called_once_with('123')


@pytest.mark.parametrize('reason', ['BadConstraints', 'QOSMaxCpuPerNode'])
def test_job_with_priority_zero_and_another_reason_is_held(reason):
    # Slurm keeps some earlier reasons on a held (priority 0) job; left as
    # it is, the job would never start.
    client = _client(reason=reason, priority='0')
    _check(client)
    client.release_job.assert_called_once_with('123')
    client.cancel_job.assert_not_called()


def test_pending_unheld_job_with_changed_shape_says_would_run():
    # The hold was dropped but the job waits for resources: not started.
    client = _client(cpus_per_task='8', reason='Resources')
    with pytest.raises(exceptions.ProvisionUnsupportedError) as e:
        _check(client)
    message = str(e.value)
    assert message.startswith('Slurm would run cluster')
    assert 'Slurm job 123 was cancelled.' in message
    client.release_job.assert_not_called()


def test_held_job_with_another_reason_and_changed_shape_says_would_run():
    client = _client(cpus_per_task='8', reason='BadConstraints', priority='0')
    with pytest.raises(exceptions.ProvisionUnsupportedError) as e:
        _check(client)
    assert str(e.value).startswith('Slurm would run cluster')
    assert 'cancelled before it started' in str(e.value)


# --- A shape that cannot be parsed is refused as well: it will not change. ---


@pytest.mark.parametrize('cpus_per_task, num_nodes', [
    ('N/A', '1'),
    ('', '1'),
    ('4', ''),
    ('4', '2+'),
])
def test_unparseable_shape_is_refused_and_cancelled(cpus_per_task, num_nodes):
    client = _client(cpus_per_task=cpus_per_task, num_nodes=num_nodes)
    with pytest.raises(exceptions.ProvisionUnsupportedError) as e:
        _check(client)
    assert 'Could not read the shape' in str(e.value)
    assert 'Slurm job 123 was cancelled before it started.' in str(e.value)
    client.cancel_job.assert_called_once_with('123')
    client.release_job.assert_not_called()


# --- A failing Slurm command is worth retrying, not a refusal. ---


def test_failed_read_is_cancelled_and_retryable():
    client = mock.Mock()
    client.get_job_fields.side_effect = exceptions.CommandError(
        255, 'scontrol show job', 'ssh: connection reset', None)
    with pytest.raises(RuntimeError) as e:
        _check(client)
    assert not isinstance(e.value, exceptions.ProvisionUnsupportedError)
    message = str(e.value)
    assert 'Could not read Slurm job 123' in message
    # Not read, so it is not known to be held, or not to have started.
    assert 'Slurm job 123 was cancelled.' in message
    assert 'before it started' not in message
    assert isinstance(e.value.__cause__, exceptions.CommandError)
    client.cancel_job.assert_called_once_with('123')
    client.release_job.assert_not_called()


def test_output_without_a_state_is_cancelled_and_retryable():
    client = mock.Mock()
    client.get_job_fields.return_value = {}
    with pytest.raises(RuntimeError) as e:
        _check(client)
    assert not isinstance(e.value, exceptions.ProvisionUnsupportedError)
    assert 'gave no state' in str(e.value)
    client.cancel_job.assert_called_once_with('123')
    client.release_job.assert_not_called()


def test_failed_release_is_cancelled_and_retryable():
    # Slurm may have taken the release before the error, so the job may have
    # started: the message does not claim it was cancelled before that.
    client = _client()
    client.release_job.side_effect = RuntimeError('slurmctld timed out')
    with pytest.raises(RuntimeError) as e:
        _check(client)
    assert not isinstance(e.value, exceptions.ProvisionUnsupportedError)
    message = str(e.value)
    assert 'Could not release held Slurm job 123' in message
    assert 'Slurm job 123 was cancelled.' in message
    assert 'before it started' not in message
    client.cancel_job.assert_called_once_with('123')


# --- A job that is no longer held is not cancelled blindly. ---


@pytest.mark.parametrize('job_state, reason', [('RUNNING', 'None'),
                                               ('CONFIGURING', 'None'),
                                               ('PENDING', 'Priority')])
def test_job_no_longer_held_with_kept_shape_is_kept(job_state, reason):
    # E.g. a job_submit plugin dropped the hold: nothing to release.
    client = _client(job_state=job_state, reason=reason)
    _check(client)
    client.release_job.assert_not_called()
    client.cancel_job.assert_not_called()


def test_running_job_with_changed_shape_is_cancelled_without_claiming_held():
    client = _client(cpus_per_task='8', job_state='RUNNING', reason='None')
    with pytest.raises(exceptions.ProvisionUnsupportedError) as e:
        _check(client)
    message = str(e.value)
    # It runs already: Slurm "runs" it so, not "would run" it so.
    assert message.startswith('Slurm runs cluster')
    assert 'Slurm job 123 was cancelled.' in message
    assert 'before it started' not in message
    client.cancel_job.assert_called_once_with('123')
    client.release_job.assert_not_called()


def test_running_job_that_cannot_be_cancelled_says_it_may_be_running():
    client = _client(cpus_per_task='8', job_state='RUNNING', reason='None')
    client.cancel_job.side_effect = RuntimeError('ssh down')
    with pytest.raises(exceptions.ProvisionUnsupportedError) as e:
        _check(client)
    message = str(e.value)
    assert 'could not be cancelled and may be running' in message
    assert 'scancel 123' in message
    assert 'still held' not in message


@pytest.mark.parametrize('job_state',
                         ['CANCELLED', 'FAILED', 'COMPLETED', 'SUSPENDED'])
def test_job_no_longer_pending_or_running_is_not_cancelled(job_state):
    client = _client(job_state=job_state, reason='None')
    with pytest.raises(RuntimeError) as e:
        _check(client)
    assert not isinstance(e.value, exceptions.ProvisionUnsupportedError)
    message = str(e.value)
    assert f'is {job_state}, not held as submitted' in message
    assert 'cancelled' not in message
    client.cancel_job.assert_not_called()
    client.release_job.assert_not_called()


# --- The cancel outcome is told truthfully, and only once. ---


def test_refusal_says_the_job_was_cancelled():
    client = _client(cpus_per_task='8')
    with pytest.raises(exceptions.ProvisionUnsupportedError) as e:
        _check(client)
    assert 'Slurm job 123 was cancelled before it started.' in str(e.value)
    assert 'scancel' not in str(e.value)


def test_failed_cancel_of_a_held_job_says_still_held():
    client = _client(cpus_per_task='8')
    client.cancel_job.side_effect = RuntimeError('ssh down')
    with mock.patch.object(slurm_instance.logger, 'warning') as warning:
        with pytest.raises(exceptions.ProvisionUnsupportedError) as e:
            _check(client)
    message = str(e.value)
    assert 'Slurm job 123 could not be cancelled and is still held' in message
    assert 'scancel 123' in message
    assert 'state is unknown' not in message
    # The advice is in the error; it is not also logged as a warning.
    warning.assert_not_called()


@pytest.mark.parametrize('read_error, release_error', [
    (RuntimeError('ssh down'), None),
    (None, RuntimeError('slurmctld timed out')),
])
def test_failed_cancel_of_a_job_in_unknown_state_says_so(
        read_error, release_error):
    client = _client()
    client.get_job_fields.side_effect = read_error
    client.release_job.side_effect = release_error
    client.cancel_job.side_effect = RuntimeError('ssh down')
    with pytest.raises(RuntimeError) as e:
        _check(client)
    message = str(e.value)
    assert ('Slurm job 123 could not be cancelled, and its state is unknown'
            in message)
    assert 'squeue -j 123' in message
    assert 'scancel 123' in message
    assert 'still held' not in message


# --- Anything else that stops the check leaves no held job behind. ---


@pytest.mark.parametrize('interrupted', ['get_job_fields', 'release_job'])
@pytest.mark.parametrize('error', [KeyboardInterrupt, SystemExit])
def test_interrupted_check_cancels_the_held_job(interrupted, error):
    # A held job left behind would be reused, still held, by the next launch.
    client = _client()
    getattr(client, interrupted).side_effect = error
    with pytest.raises(error):
        _check(client)
    client.cancel_job.assert_called_once_with('123')


def test_interrupted_refusal_still_cancels_the_held_job():
    # Ctrl-C while the refusal's own scancel runs: cancelled once more.
    client = _client(cpus_per_task='8')
    client.cancel_job.side_effect = [KeyboardInterrupt, None]
    with pytest.raises(KeyboardInterrupt):
        _check(client)
    assert client.cancel_job.call_args_list == [mock.call('123')] * 2


def test_interrupted_cancel_warns_if_it_fails():
    # With no error message to carry it, the advice is logged.
    client = _client()
    client.release_job.side_effect = KeyboardInterrupt
    client.cancel_job.side_effect = RuntimeError('ssh down')
    with mock.patch.object(slurm_instance.logger, 'warning') as warning:
        with pytest.raises(KeyboardInterrupt):
            _check(client)
    assert 'scancel 123' in warning.call_args.args[0]


# --- A reused job is recognised as left held only under a user hold. ---


def test_job_left_under_the_submission_hold_is_recognised():
    client = mock.Mock()
    client.get_job_fields.return_value = {
        'JobId': '123',
        'JobState': 'PENDING',
        'Reason': 'JobHeldUser',
        'Priority': '0',
    }
    assert slurm_instance._is_left_held(client, '123') is True
    client.get_job_fields.assert_called_once_with('123')


@pytest.mark.parametrize('job_state, reason, priority', [
    ('RUNNING', 'None', '4294'),
    ('CONFIGURING', 'None', '4294'),
    ('PENDING', 'Resources', '4294'),
    ('PENDING', 'JobHeldAdmin', '0'),
])
def test_job_not_under_the_submission_hold_is_not_recognised(
        job_state, reason, priority):
    client = mock.Mock()
    client.get_job_fields.return_value = {
        'JobState': job_state,
        'Reason': reason,
        'Priority': priority,
    }
    assert slurm_instance._is_left_held(client, '123') is False


def test_held_job_with_another_reason_is_recognised_as_left_held():
    client = mock.Mock()
    client.get_job_fields.return_value = {
        'JobState': 'PENDING',
        'Reason': 'BadConstraints',
        'Priority': '0',
    }
    assert slurm_instance._is_left_held(client, '123') is True


def test_unreadable_job_is_not_recognised_as_left_held():
    client = mock.Mock()
    client.get_job_fields.side_effect = RuntimeError('ssh down')
    assert slurm_instance._is_left_held(client, '123') is False


# --- Parsing. ---


@pytest.mark.parametrize('value, expected', [('8', 8), ('16', 16)])
def test_plain_count(value, expected):
    assert slurm_instance._plain_count(value) == expected


@pytest.mark.parametrize('value', ['', 'N/A', None, '1-1', '2+', '8 '])
def test_plain_count_rejects_non_counts(value):
    with pytest.raises(ValueError):
        slurm_instance._plain_count(value)


@pytest.mark.parametrize('value, expected', [('1', (1, 1)), ('1-1', (1, 1)),
                                             ('1-2', (1, 2)),
                                             ('16-16', (16, 16))])
def test_node_count_range(value, expected):
    assert slurm_instance._node_count_range(value) == expected


@pytest.mark.parametrize('value', ['', 'N/A', None, '2+', '1-', '1-2-3'])
def test_node_count_range_rejects_non_counts(value):
    with pytest.raises(ValueError):
        slurm_instance._node_count_range(value)
