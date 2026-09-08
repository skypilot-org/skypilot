"""Gathering the evidence behind a pending Slurm job (utils.explain_pending_job).

The classification itself is covered in test_pending.py; what matters here is
what the gatherer does when a read fails or a cluster is not there, since it
runs on a path where an unanswerable question must not become an exception.
"""
import subprocess
import time
from unittest import mock

import pytest

from sky.adaptors import slurm
from sky.provision.slurm import pending
from sky.provision.slurm import utils

_CLUSTER = 'dev-slurm'
_JOB = '17209'


def _details(**over):
    d = {
        'state': 'PENDING',
        'reason': 'Resources',
        'partition': 'h200',
        'priority': '4000',
        'num_nodes': '2',
    }
    d.update(over)
    return d


@pytest.fixture
def client():
    """A client whose reads all succeed, patched in place of a real one."""
    fake = mock.MagicMock()
    fake.get_pending_job_details.return_value = _details()
    fake.get_pending_queue.return_value = []
    fake.get_job_states.return_value = {}
    with mock.patch.object(utils, '_client_for', return_value=fake):
        yield fake


def test_unconfigured_cluster_is_not_an_exception():
    """The ssh_config lookup of an absent host yields a dict with only
    'hostname', so building a client raises KeyError('user'). A caller asking
    opportunistically gets None."""
    with mock.patch.object(utils, 'get_slurm_ssh_config') as cfg:
        cfg.return_value.lookup.return_value = {'hostname': 'nope'}
        assert utils.explain_pending_job('nope', _JOB) is None


def test_a_job_slurm_has_forgotten_is_none(client):
    client.get_pending_job_details.return_value = {}
    assert utils.explain_pending_job(_CLUSTER, _JOB) is None


def test_a_running_job_is_none(client):
    client.get_pending_job_details.return_value = _details(state='RUNNING')
    assert utils.explain_pending_job(_CLUSTER, _JOB) is None


def test_a_pending_job_with_no_reason_yet_is_none(client):
    client.get_pending_job_details.return_value = _details(reason='None')
    assert utils.explain_pending_job(_CLUSTER, _JOB) is None


def _node(name, partition, state):
    return slurm.NodeInfo(node=name,
                          state=state,
                          gres='',
                          cpus=8,
                          memory_gb=64.0,
                          partition=partition)


def test_resources_reads_node_counts_and_the_queue(client):
    client.info_nodes.return_value = [
        _node('gpu-1', 'h200', 'alloc'),
        _node('gpu-2', 'h200', 'drain'),
        _node('cpu-1', 'cpu', 'idle'),
    ]
    client.get_pending_queue.return_value = [
        {
            'job_id': '1',
            'partition': 'h200',
            'priority': '9000',
            'state': 'PENDING'
        },
        {
            'job_id': '2',
            'partition': 'h200',
            'priority': '10',
            'state': 'PENDING'
        },
    ]
    out = utils.explain_pending_job(_CLUSTER, _JOB)
    assert out['category'] == pending.CATEGORY_RESOURCES
    # One sinfo, bounded. The full inventory helper also runs
    # `scontrol show node` cluster-wide and derives GPU counts, none of which
    # the tally uses, and its budget is sized for the inventory sweep.
    assert client.info_nodes.call_args.kwargs['timeout'] == (
        slurm.JOB_READ_TIMEOUT_SECONDS)
    # Only the h200 nodes count, and drain is not idle.
    assert out['evidence']['partition_nodes']['total'] == 2
    assert out['evidence']['partition_nodes']['idle'] == 0
    assert out['evidence']['partition_nodes']['drained'] == 1
    # One of the two pending jobs outranks this one.
    assert out['evidence']['pending_ahead'] == 1


def test_a_failed_node_read_still_answers(client):
    """The reason is known even when the node counts are not; the summary says
    what it could not read rather than the call failing."""
    client.info_nodes.side_effect = RuntimeError('sinfo timed out')
    out = utils.explain_pending_job(_CLUSTER, _JOB)
    assert out['category'] == pending.CATEGORY_RESOURCES
    assert out['evidence']['partition_nodes'] is None
    assert 'Waiting for free resources' in out['summary']


def test_dependency_reads_only_the_blocking_jobs(client):
    client.get_pending_job_details.return_value = _details(
        reason='Dependency', dependency='afterok:5122(unfulfilled)')
    client.get_job_states.return_value = {'5122': 'RUNNING'}
    out = utils.explain_pending_job(_CLUSTER, _JOB)
    assert out['category'] == pending.CATEGORY_DEPENDENCY
    assert '5122 (RUNNING)' in out['summary']
    client.get_job_states.assert_called_once_with(['5122'])
    # A dependency needs no node counts, and none were read.
    client.get_pending_queue.assert_not_called()


def test_quota_says_the_cap_is_unknown_here(client):
    """This gatherer cannot read the accounting database, and the taxonomy is
    built for that: it names the limit without inventing a number."""
    client.get_pending_job_details.return_value = _details(reason='QOSGrpGRES',
                                                           qos='normal')
    out = utils.explain_pending_job(_CLUSTER, _JOB)
    assert out['category'] == pending.CATEGORY_QUOTA
    assert out['action'] is None
    assert 'could not be read' in out['summary'] or 'unknown' in out['summary']


def test_held_carries_the_begin_time(client):
    client.get_pending_job_details.return_value = _details(
        reason='BeginTime', start_time='1757304475')
    out = utils.explain_pending_job(_CLUSTER, _JOB)
    assert out['category'] == pending.CATEGORY_HELD
    assert out['evidence']['begin_time'] is not None


def test_a_timed_out_details_read_is_none(client):
    client.get_pending_job_details.side_effect = subprocess.TimeoutExpired(
        'squeue', 10)
    assert utils.explain_pending_job(_CLUSTER, _JOB) is None


def test_a_spent_budget_answers_from_the_reason_alone(client):
    """Past the deadline the reason is already known; what is skipped is the
    evidence that would have sharpened it, and the summary says so."""
    client.info_nodes.return_value = []
    out = utils.explain_pending_job(_CLUSTER,
                                    _JOB,
                                    deadline=time.monotonic() - 1)
    assert out['category'] == pending.CATEGORY_RESOURCES
    client.info_nodes.assert_not_called()
    client.get_pending_queue.assert_not_called()
    assert out['evidence']['partition_nodes'] is None


def test_a_spent_budget_skips_the_dependency_read(client):
    client.get_pending_job_details.return_value = _details(
        reason='Dependency', dependency='afterok:5122(unfulfilled)')
    out = utils.explain_pending_job(_CLUSTER,
                                    _JOB,
                                    deadline=time.monotonic() - 1)
    assert out['category'] == pending.CATEGORY_DEPENDENCY
    client.get_job_states.assert_not_called()
