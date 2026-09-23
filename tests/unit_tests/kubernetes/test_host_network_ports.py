"""Tests for server-side hostNetwork port assignment.

The properties pinned here are the ones a wrong answer would not make
obvious: that the range avoids two allocators we do not control, that a
worker is told the *head's* GCS port while keeping its own everything else,
and that the resolution order falls back rather than inventing a block for a
pod that already has one.
"""
from unittest import mock

import pytest

from sky.provision.kubernetes import host_network_ports as ports
from sky.provision.kubernetes import host_network_probe

# Linux default ip_local_port_range floor, measured on the dev clusters.
_EPHEMERAL_FLOOR = 32768
# Default --service-node-port-range.
_NODEPORT_FLOOR = 30000


def _pod(host_ports=None, phase='Running', conditions=()):
    container = mock.Mock()
    container.ports = [mock.Mock(host_port=p) for p in (host_ports or [])]
    pod = mock.Mock()
    pod.spec.containers = [container]
    pod.status.phase = phase
    pod.status.conditions = list(conditions)
    return pod


def test_range_avoids_both_allocators_we_do_not_control():
    """The whole range, not just a sample: one stray port is a rare failure."""
    assert ports.PORT_RANGE_END < _NODEPORT_FLOOR
    assert ports.PORT_RANGE_END < _EPHEMERAL_FLOOR


@pytest.mark.parametrize('_', range(200))
def test_allocated_blocks_are_contiguous_and_in_range(_):
    block = ports.allocate_block()
    assert len(block) == ports.BLOCK_SIZE
    values = sorted(block.values())
    assert values == list(range(values[0], values[0] + len(values)))
    assert ports.PORT_RANGE_START <= values[0]
    assert values[-1] <= ports.PORT_RANGE_END


def test_worker_is_told_the_heads_gcs_and_keeps_its_own_sshd():
    """The property that makes a worker join: one port from the head, rest its own."""
    head, worker = ports.allocate_block(), ports.allocate_block()
    spec = {'spec': {'containers': [{'name': 'ray-node'}]}}
    ports.apply_to_pod_spec(spec, worker, head_gcs_port=head['gcs'])

    env = {e['name']: e['value'] for e in spec['spec']['containers'][0]['env']}
    assert env[host_network_probe.env_var_for_port('gcs')] == str(head['gcs'])
    assert env[host_network_probe.env_var_for_port('sshd')] == str(
        worker['sshd'])
    declared = {p['hostPort'] for p in spec['spec']['containers'][0]['ports']}
    assert declared == set(worker.values())


def test_declared_ports_are_host_ports_not_only_container_ports():
    """containerPort alone is what the pre-change template rendered; hostPort
    is what makes the scheduler refuse to co-schedule."""
    spec = {'spec': {'containers': [{}]}}
    block = ports.allocate_block()
    ports.apply_to_pod_spec(spec, block, head_gcs_port=block['gcs'])
    for port in spec['spec']['containers'][0]['ports']:
        assert port['hostPort'] == port['containerPort']


def test_existing_env_is_preserved():
    spec = {'spec': {'containers': [{'env': [{'name': 'KEEP', 'value': '1'}]}]}}
    block = ports.allocate_block()
    ports.apply_to_pod_spec(spec, block, head_gcs_port=block['gcs'])
    env = {e['name']: e['value'] for e in spec['spec']['containers'][0]['env']}
    assert env['KEEP'] == '1'


class TestResolutionOrder:
    """One helper, one order -- the two callers must not disagree."""

    def test_a_live_pods_declared_ports_win(self):
        block = ports.allocate_block()
        resolved = ports.resolve_block(_pod(sorted(block.values())),
                                       configmap_ports={'gcs': 1})
        assert resolved == block

    def test_a_pre_change_pod_declares_nothing_so_the_configmap_answers(self):
        """The upgrade case: reusing from the pod would invent a block and the
        worker would dial a port the head is not listening on."""
        cm = {
            name: 40000 + i
            for i, name in enumerate(host_network_probe.HEAD_PORT_NAMES)
        }
        assert ports.resolve_block(_pod(host_ports=[]),
                                   configmap_ports=cm) == cm

    def test_no_pod_and_no_configmap_allocates(self):
        resolved = ports.resolve_block(None, configmap_ports=None)
        assert len(resolved) == ports.BLOCK_SIZE


class TestUnschedulableGate:
    """Deleting any Pending pod would churn one that is merely slow."""

    def _cond(self, type_, status, reason):
        return mock.Mock(type=type_, status=status, reason=reason)

    def test_unschedulable_pending_pod_is_recreated(self):
        pod = _pod(
            phase='Pending',
            conditions=[self._cond('PodScheduled', 'False', 'Unschedulable')])
        assert ports.is_unschedulable(pod)

    def test_pod_pending_on_an_image_pull_is_left_alone(self):
        pod = _pod(phase='Pending',
                   conditions=[
                       self._cond('PodScheduled', 'True', None),
                   ])
        assert not ports.is_unschedulable(pod)

    def test_running_pod_is_never_deleted(self):
        pod = _pod(
            phase='Running',
            conditions=[self._cond('PodScheduled', 'False', 'Unschedulable')])
        assert not ports.is_unschedulable(pod)
