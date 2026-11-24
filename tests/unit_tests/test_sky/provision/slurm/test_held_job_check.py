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


@pytest.mark.parametrize('fields', [
    {},
    {
        'CPUs/Task': 'N/A',
        'NumNodes': '1'
    },
    {
        'CPUs/Task': '4'
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


def test_failed_cancel_still_refuses():
    client = _client(cpus_per_task='8')
    client.cancel_job.side_effect = RuntimeError('ssh down')
    with pytest.raises(exceptions.ProvisionUnsupportedError):
        _check(client)


@pytest.mark.parametrize('value, expected', [('8', 8), ('1-1', 1), ('2+', 2)])
def test_leading_int(value, expected):
    assert slurm_instance._leading_int(value) == expected


@pytest.mark.parametrize('value', ['', 'N/A', None])
def test_leading_int_rejects_non_counts(value):
    with pytest.raises(ValueError):
        slurm_instance._leading_int(value)
