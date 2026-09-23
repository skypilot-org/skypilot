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


def _container(name, host_ports):
    c = mock.Mock()
    c.name = name
    c.ports = [mock.Mock(host_port=p) for p in host_ports]
    return c


def _pod(host_ports=None, phase='Running', conditions=(), sidecar_ports=()):
    containers = []
    if sidecar_ports:
        containers.append(_container('my-sidecar', sidecar_ports))
    containers.append(_container('ray-node', host_ports or []))
    pod = mock.Mock()
    pod.spec.containers = containers
    pod.metadata.name = 'c-head'
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


class TestProbeMakesNoApiCall:
    """The point of the change: a workload pod needs no K8s API access."""

    def test_the_probe_does_not_import_an_http_stack(self):
        """Asserted on the imports, not on a call never being reached.

        "urlopen is never called" passes for any test that simply does not
        walk the removal path -- it is satisfied by a dead branch as readily
        as by a deleted one. A half-finished removal leaves the import
        behind; that is the thing with no innocent explanation.
        """
        import ast
        import pathlib

        source = pathlib.Path(
            host_network_probe.__file__).read_text(encoding='utf-8')
        imported = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.update(a.name.split('.')[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split('.')[0])
        assert not imported & {'urllib', 'ssl', 'json', 'kubernetes'}

    def test_the_probe_is_stdlib_only(self):
        """It runs while the pod's skypilot install is still in flux, so it
        cannot import sky -- inlined or not."""
        import ast
        import pathlib

        source = pathlib.Path(
            host_network_probe.__file__).read_text(encoding='utf-8')
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith('sky')
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith('sky')


class TestLegacyConfigMapFallback:
    """The one-release fallback for clusters created before this change.

    Deleting this read outright would send every pre-existing hostNetwork
    cluster's SSH to port 22 -- the node's own sshd -- which is the failure
    this PR exists to remove.
    """

    def _cm(self, data):
        cm = mock.Mock()
        cm.data = data
        return cm

    def _call(self, cm_or_exc, head='c-head'):
        from sky.provision.kubernetes import instance
        api = mock.Mock()
        if isinstance(cm_or_exc, Exception):
            api.read_namespaced_config_map.side_effect = cm_or_exc
        else:
            api.read_namespaced_config_map.return_value = cm_or_exc
        with mock.patch.object(instance.kubernetes,
                               'core_api',
                               return_value=api):
            return instance._head_block_from_configmap('c', 'ns', None, head)

    def _full_data(self):
        data = {
            name: str(40000 + i)
            for i, name in enumerate(host_network_probe.HEAD_PORT_NAMES)
            if name != 'sshd'
        }
        data[f'{host_network_probe.SSHD_KEY_PREFIX}c-head'] = '40099'
        return data

    def test_reads_the_heads_block_including_its_pod_keyed_sshd(self):
        block = self._call(self._cm(self._full_data()))
        assert block is not None
        assert set(block) == set(host_network_probe.HEAD_PORT_NAMES)
        assert block['sshd'] == 40099

    def test_absent_configmap_is_not_an_error(self):
        """A cluster created *after* this change has none, which is normal."""
        from sky.adaptors import kubernetes as k8s_adaptor
        exc = k8s_adaptor.api_exception()(status=404)
        assert self._call(exc) is None

    def test_partial_data_allocates_rather_than_guessing(self):
        data = self._full_data()
        del data['gcs']
        assert self._call(self._cm(data)) is None

    def test_a_non_integer_value_does_not_raise(self):
        data = self._full_data()
        data['gcs'] = 'not-a-port'
        assert self._call(self._cm(data)) is None


class TestBlockIsReadFromTheRayContainerOnly:
    """A user's pod_config can add containers; the block is not theirs."""

    def test_a_sidecars_host_port_does_not_shift_the_block(self):
        """Taking min() across containers would make a low sidecar port the
        start, and every reconstructed port would be one nothing listens on --
        for a head, handed to every worker in the launch."""
        block = ports.allocate_block()
        pod = _pod(host_ports=sorted(block.values()), sidecar_ports=[8080])
        assert ports.ports_from_pod(pod) == block

    def test_a_pod_with_only_a_sidecars_ports_reads_as_legacy(self):
        assert ports.ports_from_pod(_pod(host_ports=[],
                                         sidecar_ports=[8080])) is None

    def test_the_writer_targets_the_same_container(self):
        spec = {
            'spec': {
                'containers': [{
                    'name': 'my-sidecar'
                }, {
                    'name': 'ray-node'
                }]
            }
        }
        block = ports.allocate_block()
        ports.apply_to_pod_spec(spec, block, head_gcs_port=block['gcs'])
        assert 'ports' not in spec['spec']['containers'][0]
        assert len(spec['spec']['containers'][1]['ports']) == ports.BLOCK_SIZE


class TestPartialDeclarationIsNotLegacy:
    """`None` means "predates the change"; a half-read pod is not that.

    Conflating them allocates a fresh block for a pod that is running and
    listening on the old one.
    """

    def test_a_short_block_raises_rather_than_reallocating(self):
        block = ports.allocate_block()
        short = sorted(block.values())[:-1]
        with pytest.raises(RuntimeError, match='contiguous'):
            ports.ports_from_pod(_pod(host_ports=short))

    def test_a_gap_in_the_block_raises(self):
        start = ports.PORT_RANGE_START
        holey = [start + i for i in range(ports.BLOCK_SIZE + 1) if i != 2]
        with pytest.raises(RuntimeError):
            ports.ports_from_pod(_pod(host_ports=holey))

    def test_a_block_outside_the_range_raises(self):
        """The shape a NodePort or ephemeral overlap would leave behind."""
        with pytest.raises(RuntimeError):
            ports.ports_from_pod(
                _pod(host_ports=[40000 + i for i in range(ports.BLOCK_SIZE)]))

    def test_no_ports_at_all_is_still_legacy(self):
        assert ports.ports_from_pod(_pod(host_ports=[])) is None
