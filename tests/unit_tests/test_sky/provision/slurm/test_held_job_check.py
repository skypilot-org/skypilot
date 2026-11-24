"""Tests for verifying a held Slurm job before it is released.

Slurm can adjust a job at submission (e.g. raise its CPUs per task when the
requested memory exceeds the partition's MaxMemPerCPU). The job would then run,
and be accounted, on resources that were not requested, so it is submitted
held, compared with the request, and only released if Slurm kept it.
"""
from unittest import mock

import pytest

from sky import exceptions
from sky.provision.slurm import instance as slurm_instance


def _client(cpus_per_task='4', num_nodes='1'):
    client = mock.Mock()
    client.get_job_fields.return_value = {
        'JobId': '123',
        'JobState': 'PENDING',
        'Reason': 'JobHeldUser',
        'CPUs/Task': cpus_per_task,
        'NumNodes': num_nodes,
    }
    return client


def _check(client, cpus_per_task=4, num_nodes=1, memory=64):
    slurm_instance._release_if_request_kept(client, '123', 'my-cluster',
                                            'batch', cpus_per_task, num_nodes,
                                            memory)


def test_kept_request_is_released():
    client = _client()
    _check(client)
    client.release_job.assert_called_once_with('123')
    client.cancel_job.assert_not_called()


def test_multi_node_request_is_released():
    client = _client(cpus_per_task='4', num_nodes='2')
    _check(client, num_nodes=2)
    client.release_job.assert_called_once_with('123')


def test_raised_cpus_cancel_and_refuse_with_a_fix():
    client = _client(cpus_per_task='8')
    with pytest.raises(exceptions.ProvisionUnsupportedError) as e:
        _check(client)
    client.cancel_job.assert_called_once_with('123')
    client.release_job.assert_not_called()
    message = str(e.value)
    assert '8 CPUs per task instead of the 4 requested' in message
    assert 'Slurm job 123 was cancelled before it started' in message
    assert 'requested memory (64 GB)' in message
    assert '64.0' not in message
    assert "partition 'batch'" in message
    assert '--cpus 8' in message


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


@pytest.mark.parametrize('num_nodes, requested', [('1-1', 1), ('2-2', 2)])
def test_exact_node_range_is_released(num_nodes, requested):
    client = _client(num_nodes=num_nodes)
    _check(client, num_nodes=requested)
    client.release_job.assert_called_once_with('123')
    client.cancel_job.assert_not_called()


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


@pytest.mark.parametrize('fields', [
    {},
    {
        'CPUs/Task': 'N/A',
        'NumNodes': '1'
    },
    {
        'CPUs/Task': '4'
    },
    {
        'CPUs/Task': '4',
        'NumNodes': '2+'
    },
])
def test_unverifiable_job_is_cancelled(fields):
    client = mock.Mock()
    client.get_job_fields.return_value = fields
    with pytest.raises(exceptions.ProvisionUnsupportedError,
                       match='Could not verify'):
        _check(client)
    client.cancel_job.assert_called_once_with('123')
    client.release_job.assert_not_called()


def test_unreadable_job_is_cancelled():
    client = mock.Mock()
    client.get_job_fields.side_effect = RuntimeError('ssh down')
    with pytest.raises(exceptions.ProvisionUnsupportedError,
                       match='Could not verify'):
        _check(client)
    client.cancel_job.assert_called_once_with('123')


def test_failed_release_is_cancelled():
    client = _client()
    client.release_job.side_effect = RuntimeError('not permitted')
    with pytest.raises(exceptions.ProvisionUnsupportedError,
                       match='Could not release'):
        _check(client)
    client.cancel_job.assert_called_once_with('123')


def test_refusal_says_the_job_was_cancelled():
    client = _client(cpus_per_task='8')
    with pytest.raises(exceptions.ProvisionUnsupportedError) as e:
        _check(client)
    assert 'Slurm job 123 was cancelled before it started.' in str(e.value)
    assert 'scancel' not in str(e.value)


@pytest.mark.parametrize('fields, release_error, reason', [
    ({
        'CPUs/Task': '8',
        'NumNodes': '1'
    }, None, '8 CPUs per task instead of the 4 requested'),
    ({}, None, 'Could not verify'),
    ({
        'CPUs/Task': '4',
        'NumNodes': '1'
    }, RuntimeError('not permitted'), 'Could not release'),
])
def test_failed_cancel_still_refuses_and_says_so(fields, release_error, reason):
    client = mock.Mock()
    client.get_job_fields.return_value = fields
    client.release_job.side_effect = release_error
    client.cancel_job.side_effect = RuntimeError('ssh down')
    with pytest.raises(exceptions.ProvisionUnsupportedError) as e:
        _check(client)
    message = str(e.value)
    assert reason in message
    assert 'Slurm job 123 could not be cancelled' in message
    assert 'scancel 123' in message
    assert 'was cancelled' not in message


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


def test_refusal_is_not_wrapped_or_cancelled_twice():
    client = _client(cpus_per_task='8')
    with pytest.raises(exceptions.ProvisionUnsupportedError) as e:
        _check(client)
    assert str(e.value).startswith('Slurm would run cluster')
    client.cancel_job.assert_called_once_with('123')


def _left_held_client(job_state='PENDING',
                      reason='JobHeldUser',
                      cpus_per_task='4'):
    client = _client(cpus_per_task=cpus_per_task)
    client.get_job_fields.return_value.update(JobState=job_state, Reason=reason)
    return client


def _check_left_held(client):
    slurm_instance._release_if_left_held(client, '123', 'my-cluster', 'batch',
                                         1, {
                                             'cpus': 4,
                                             'memory': 64
                                         })


def test_reused_job_left_held_is_verified_and_released():
    client = _left_held_client()
    _check_left_held(client)
    client.release_job.assert_called_once_with('123')
    client.cancel_job.assert_not_called()


def test_reused_job_left_held_with_changed_shape_is_refused():
    client = _left_held_client(cpus_per_task='8')
    with pytest.raises(exceptions.ProvisionUnsupportedError,
                       match='8 CPUs per task instead of the 4 requested'):
        _check_left_held(client)
    client.cancel_job.assert_called_once_with('123')
    client.release_job.assert_not_called()


@pytest.mark.parametrize('job_state, reason', [
    ('RUNNING', 'None'),
    ('PENDING', 'Resources'),
    ('PENDING', 'JobHeldAdmin'),
])
def test_reused_job_not_left_held_is_untouched(job_state, reason):
    client = _left_held_client(job_state=job_state, reason=reason)
    _check_left_held(client)
    client.release_job.assert_not_called()
    client.cancel_job.assert_not_called()


def test_reused_job_that_cannot_be_read_is_untouched():
    client = _left_held_client()
    client.get_job_fields.side_effect = RuntimeError('ssh down')
    _check_left_held(client)
    client.release_job.assert_not_called()
    client.cancel_job.assert_not_called()


@pytest.mark.parametrize('value, expected', [('8', 8), ('1-1', 1), ('2+', 2)])
def test_leading_int(value, expected):
    assert slurm_instance._leading_int(value) == expected


@pytest.mark.parametrize('value', ['', 'N/A', None])
def test_leading_int_rejects_non_counts(value):
    with pytest.raises(ValueError):
        slurm_instance._leading_int(value)


@pytest.mark.parametrize('value, expected', [('1', (1, 1)), ('1-1', (1, 1)),
                                             ('1-2', (1, 2)),
                                             ('16-16', (16, 16))])
def test_node_count_range(value, expected):
    assert slurm_instance._node_count_range(value) == expected


@pytest.mark.parametrize('value', ['', 'N/A', None, '2+', '1-', '1-2-3'])
def test_node_count_range_rejects_non_counts(value):
    with pytest.raises(ValueError):
        slurm_instance._node_count_range(value)
