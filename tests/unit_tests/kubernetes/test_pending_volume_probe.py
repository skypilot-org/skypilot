"""Unit tests for detecting a volume that will not bind while a pod waits.

Kubernetes reports an unbindable claim only through events, so both pod wait
loops otherwise learn of it by running out of time. These tests cover the two
judgements that makes possible: a reported failure is fatal, and a claim that is
merely slow is not.
"""
import datetime
import math
from unittest import mock

import pytest

from sky.provision import constants as prov_constants
from sky.provision.kubernetes import config as config_lib
from sky.provision.kubernetes import instance

_PODS_CREATED_AT = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
_BEFORE_PODS = _PODS_CREATED_AT - datetime.timedelta(minutes=5)
_AFTER_PODS = _PODS_CREATED_AT + datetime.timedelta(seconds=5)
_CSI_MESSAGE = ('failed to provision volume with StorageClass "fast": rpc '
                'error: code = InvalidArgument desc = tier is invalid')
# How far _advance_past_next_probe() below moves the clock, and how many probes
# it therefore takes for a first sighting to age out of the grace period.
_PROBE_STEP_SECONDS = (instance._PVC_PROBE_INITIAL_DELAY_SECONDS +
                       instance._PVC_PROBE_INTERVAL_SECONDS)
_PROBES_TO_OUTLAST_GRACE = 1 + math.ceil(
    instance._PVC_FAILURE_GRACE_SECONDS / _PROBE_STEP_SECONDS)


def _event(reason, message, event_type='Warning', timestamp=_AFTER_PODS):
    event = mock.MagicMock()
    event.reason = reason
    event.message = message
    event.type = event_type
    event.last_timestamp = timestamp
    return event


def _pod(pvc_names, name='pod-0', cluster_name_on_cloud='cn-on-cloud'):
    pod = mock.MagicMock()
    pod.metadata.name = name
    pod.metadata.deletion_timestamp = None
    pod.metadata.labels = {
        prov_constants.TAG_SKYPILOT_CLUSTER_NAME: cluster_name_on_cloud,
    }
    # Not scheduled, not gated: set explicitly so the wait loops do not read
    # auto-created MagicMock attributes as "bound" or "queued".
    pod.status.phase = 'Pending'
    pod.status.conditions = []
    pod.status.container_statuses = None
    pod.spec.node_name = None
    pod.spec.scheduling_gates = None
    pod.spec.volumes = []
    for pvc_name in pvc_names:
        vol = mock.MagicMock()
        vol.persistent_volume_claim.claim_name = pvc_name
        pod.spec.volumes.append(vol)
    # A pod also mounts things that are not claims (secrets, emptyDir).
    other = mock.MagicMock()
    other.persistent_volume_claim = None
    pod.spec.volumes.append(other)
    return pod


@pytest.fixture(name='cluster')
def cluster_fixture(monkeypatch):
    """A fake namespace whose PVC phases and events the test controls."""

    class Cluster:

        def __init__(self):
            self.phases = {}
            self.events = {}
            self.pvc_reads = 0

        def set(self, pvc_name, phase, events=()):
            self.phases[pvc_name] = phase
            self.events[pvc_name] = list(events)

        def _read_pvc(self, name, namespace, **kwargs):
            del namespace, kwargs  # unused
            self.pvc_reads += 1
            if name not in self.phases:
                raise ValueError(f'no such PVC: {name}')
            pvc = mock.MagicMock()
            pvc.status.phase = self.phases[name]
            return pvc

    cluster = Cluster()
    core_api = mock.MagicMock()
    core_api.read_namespaced_persistent_volume_claim.side_effect = (
        cluster._read_pvc)
    monkeypatch.setattr('sky.adaptors.kubernetes.core_api',
                        lambda *a, **kw: core_api)
    monkeypatch.setattr(instance.kubernetes_utils,
                        'get_pvc_events',
                        lambda context, namespace, pvc_name, reverse=True:
                        cluster.events.get(pvc_name, []))
    return cluster


class TestGetPendingPvcs:
    """Which claims are pending, and whether they are failing or just slow."""

    def _pending(self, cluster, failure_since=_PODS_CREATED_AT):
        return instance._get_pending_pvcs('ns',
                                          'ctx',
                                          list(cluster.phases),
                                          failure_since=failure_since)

    def test_bound_claim_is_not_pending(self, cluster):
        cluster.set('vol', 'Bound')

        assert self._pending(cluster) == []

    def test_provisioning_failure_is_a_failure(self, cluster):
        cluster.set('vol', 'Pending',
                    [_event('ProvisioningFailed', _CSI_MESSAGE)])

        pending = self._pending(cluster)

        assert len(pending) == 1
        assert pending[0].failed
        assert 'tier is invalid' in pending[0].detail
        assert 'vol' in pending[0].detail

    def test_waiting_for_first_consumer_is_not_a_failure(self, cluster):
        """The expected state of a WaitForFirstConsumer claim before its pod is
        scheduled. Treating it as broken would fail every such volume."""
        cluster.set('vol', 'Pending', [
            _event('WaitForFirstConsumer',
                   'waiting for first consumer to be created',
                   event_type='Normal')
        ])

        pending = self._pending(cluster)

        assert not pending[0].failed
        assert 'WaitForFirstConsumer' in pending[0].detail

    def test_pending_with_no_events_is_not_a_failure(self, cluster):
        cluster.set('vol', 'Pending')

        pending = self._pending(cluster)

        assert not pending[0].failed
        assert pending[0].detail == 'vol (phase: Pending)'

    def test_a_failure_from_before_this_attempt_is_ignored(self, cluster):
        """Events outlive the attempt that produced them, so a leftover warning
        must not fail a launch that is provisioning normally."""
        cluster.set('vol', 'Pending', [
            _event('ProvisioningFailed', _CSI_MESSAGE, timestamp=_BEFORE_PODS)
        ])

        pending = self._pending(cluster)

        assert not pending[0].failed
        # Still reported: it is the best explanation available for the wait.
        assert 'tier is invalid' in pending[0].detail

    def test_without_an_anchor_any_failure_counts(self, cluster):
        """What the post-timeout path wants: by then nothing is going to bind
        the claim regardless of when it broke."""
        cluster.set('vol', 'Pending', [
            _event('ProvisioningFailed', _CSI_MESSAGE, timestamp=_BEFORE_PODS)
        ])

        pending = self._pending(cluster, failure_since=None)

        assert pending[0].failed

    def test_an_unreadable_claim_is_not_reported(self, cluster):
        """Fail open: a transient API error must not fail the launch."""
        pending = instance._get_pending_pvcs('ns', 'ctx', ['missing'])

        assert pending == []


class TestPodPvcNames:

    def test_claims_are_collected_in_order(self):
        assert instance._pod_pvc_names(_pod(['a', 'b'])) == ['a', 'b']

    def test_repeated_claims_are_read_once(self):
        """Every pod of a multi-node cluster mounts the same shared volume."""
        assert instance._pod_pvc_names(_pod(['a', 'a'])) == ['a']

    def test_a_pod_with_no_volumes(self):
        pod = mock.MagicMock()
        pod.spec.volumes = None

        assert instance._pod_pvc_names(pod) == []


class TestPendingVolumeProbe:

    @pytest.fixture(autouse=True)
    def _fake_clock(self, monkeypatch):
        self.clock = {'t': 1000.0}
        monkeypatch.setattr(instance.time, 'time', lambda: self.clock['t'])

    def _advance_past_next_probe(self):
        self.clock['t'] += (instance._PVC_PROBE_INITIAL_DELAY_SECONDS +
                            instance._PVC_PROBE_INTERVAL_SECONDS)

    def _probe(self):
        return instance._PendingVolumeProbe(namespace='ns',
                                            context='ctx',
                                            cluster_name='cn',
                                            pods_created_at=_PODS_CREATED_AT)

    def test_nothing_is_probed_before_the_first_delay(self, cluster):
        cluster.set('vol', 'Pending',
                    [_event('ProvisioningFailed', _CSI_MESSAGE)])
        probe = self._probe()

        assert probe.probe([_pod(['vol'])]) is None
        assert cluster.pvc_reads == 0

    def test_a_failure_within_the_grace_period_does_not_fail_the_launch(
            self, cluster):
        """A CSI provisioner emits ProvisioningFailed for transient reasons --
        a driver still starting on a new node, a cloud API rate limit -- and
        then retries successfully."""
        cluster.set('vol', 'Pending',
                    [_event('ProvisioningFailed', _CSI_MESSAGE)])
        probe = self._probe()
        pods = [_pod(['vol'])]
        deadline = self.clock['t'] + instance._PVC_FAILURE_GRACE_SECONDS

        message = None
        while self.clock['t'] < deadline:
            message = probe.probe(pods)
            self._advance_past_next_probe()

        assert message is not None
        assert 'vol' in message

    def test_a_failure_that_outlasts_the_grace_period_fails_the_launch(
            self, cluster):
        cluster.set('vol', 'Pending',
                    [_event('ProvisioningFailed', _CSI_MESSAGE)])
        probe = self._probe()
        pods = [_pod(['vol'])]

        with pytest.raises(config_lib.KubernetesError) as exc:
            for _ in range(_PROBES_TO_OUTLAST_GRACE):
                self._advance_past_next_probe()
                probe.probe(pods)

        assert 'vol' in str(exc.value)
        assert 'tier is invalid' in str(exc.value)
        assert 'kubectl describe pvc vol' in str(exc.value)

    def test_the_grace_period_measures_an_uninterrupted_run(self, cluster):
        """A provisioner that recovers in between, even once, has not been
        failing for the whole window."""
        pods = [_pod(['vol'])]
        probe = self._probe()
        failing = [_event('ProvisioningFailed', _CSI_MESSAGE)]

        # Fail for one probe short of the grace period...
        cluster.set('vol', 'Pending', failing)
        for _ in range(_PROBES_TO_OUTLAST_GRACE - 1):
            self._advance_past_next_probe()
            probe.probe(pods)
        # ... recover for a single probe, then fail for that long again.
        cluster.set('vol', 'Pending')
        self._advance_past_next_probe()
        probe.probe(pods)
        cluster.set('vol', 'Pending', failing)
        for _ in range(_PROBES_TO_OUTLAST_GRACE - 1):
            self._advance_past_next_probe()
            probe.probe(pods)  # Does not raise: the clock restarted.

    def test_a_failure_is_never_confirmed_while_a_node_is_on_its_way(
            self, cluster):
        """A topology-aware CSI driver in a cluster scaled to zero reports the
        same failure as one that will never succeed. Waiting is right until the
        node arrives, however long the autoscaler takes."""
        cluster.set('vol', 'Pending',
                    [_event('ProvisioningFailed', _CSI_MESSAGE)])
        probe = self._probe()
        pods = [_pod(['vol'])]

        message = None
        for _ in range(_PROBES_TO_OUTLAST_GRACE * 3):
            self._advance_past_next_probe()
            message = probe.probe(pods, hold_failures=True)

        assert message is not None
        assert 'vol' in message

    def test_the_grace_period_restarts_once_the_node_has_arrived(self, cluster):
        """The hold forgets the failures seen during it -- their cause may have
        been the missing node."""
        cluster.set('vol', 'Pending',
                    [_event('ProvisioningFailed', _CSI_MESSAGE)])
        probe = self._probe()
        pods = [_pod(['vol'])]

        for _ in range(_PROBES_TO_OUTLAST_GRACE):
            self._advance_past_next_probe()
            probe.probe(pods, hold_failures=True)

        # One probe short of the grace period after the hold is lifted.
        for _ in range(_PROBES_TO_OUTLAST_GRACE - 1):
            self._advance_past_next_probe()
            probe.probe(pods)
        self._advance_past_next_probe()
        with pytest.raises(config_lib.KubernetesError):
            probe.probe(pods)

    def test_a_slow_claim_is_reported_but_not_failed(self, cluster):
        """Repeated forever if need be -- this is a Filestore volume that will
        bind in a few minutes, and the point is to say so."""
        cluster.set('vol', 'Pending', [
            _event('Provisioning',
                   'External provisioner is provisioning volume',
                   event_type='Normal')
        ])
        probe = self._probe()

        for _ in range(5):
            self._advance_past_next_probe()
            message = probe.probe([_pod(['vol'])])
            assert message is not None
            assert 'vol' in message

    def test_the_last_finding_is_repeated_between_probes(self, cluster):
        """The wait loops poll once a second, far faster than the probe
        interval, and need something to display in between."""
        cluster.set('vol', 'Pending')
        probe = self._probe()
        self._advance_past_next_probe()
        message = probe.probe([_pod(['vol'])])

        assert probe.probe([_pod(['vol'])]) == message
        assert cluster.pvc_reads == 1

    def test_a_claim_that_binds_clears_the_message(self, cluster):
        cluster.set('vol', 'Pending')
        probe = self._probe()
        self._advance_past_next_probe()
        assert probe.probe([_pod(['vol'])]) is not None

        cluster.set('vol', 'Bound')
        self._advance_past_next_probe()

        assert probe.probe([_pod(['vol'])]) is None

    def test_pods_without_claims_cost_nothing(self, cluster):
        pod = mock.MagicMock()
        pod.spec.volumes = None
        probe = self._probe()
        self._advance_past_next_probe()

        assert probe.probe([pod]) is None
        assert cluster.pvc_reads == 0

    def test_a_shared_claim_is_read_once_per_probe(self, cluster):
        cluster.set('vol', 'Pending')
        probe = self._probe()
        self._advance_past_next_probe()

        probe.probe([_pod(['vol'], name='pod-0'), _pod(['vol'], name='pod-1')])

        assert cluster.pvc_reads == 1


class TestSchedulingLoopFailsOnABrokenVolume:
    """The wiring: the scheduling wait loop must report a broken claim rather
    than wait out provision_timeout, which is 24 hours with a queue admission
    controller configured.
    """

    # A day of simulated seconds at one iteration per second, capped so that
    # removing the probe fails this test instead of looking like a hang.
    _MAX_ITERATIONS = 2000

    @pytest.fixture(autouse=True)
    def _harness(self, monkeypatch, cluster):
        self.cluster = cluster
        self.pod = _pod(['vol'])
        cluster.set('vol', 'Pending',
                    [_event('ProvisioningFailed', _CSI_MESSAGE)])

        core_api = mock.MagicMock()
        core_api.list_namespaced_pod.return_value = mock.MagicMock(
            items=[self.pod])
        core_api.read_namespaced_persistent_volume_claim.side_effect = (
            cluster._read_pvc)
        monkeypatch.setattr('sky.adaptors.kubernetes.core_api',
                            lambda *a, **kw: core_api)

        monkeypatch.setattr(
            'sky.skypilot_config.get_effective_region_config',
            lambda cloud, region, keys, default_value=None, **kw: default_value)
        monkeypatch.setattr('sky.utils.rich_utils.force_update_status',
                            lambda *a, **kw: None)
        self.add_cluster_event = mock.MagicMock()
        monkeypatch.setattr(instance.global_user_state, 'add_cluster_event',
                            self.add_cluster_event)
        monkeypatch.setattr(
            instance, '_raise_pod_scheduling_errors',
            mock.MagicMock(side_effect=config_lib.KubernetesError('timed-out')))

        clock = {'t': 1000.0, 'iterations': 0}

        def _sleep(seconds):
            clock['iterations'] += 1
            if clock['iterations'] > self._MAX_ITERATIONS:
                raise AssertionError('the wait loop did not report the broken '
                                     'volume')
            clock['t'] += seconds

        monkeypatch.setattr(instance.time, 'time', lambda: clock['t'])
        monkeypatch.setattr(instance.time, 'sleep', _sleep)

    def _wait(self, timeout=24 * 60 * 60):
        instance._wait_for_pods_to_schedule(namespace='ns',
                                            context='ctx',
                                            new_nodes=[self.pod],
                                            timeout=timeout,
                                            cluster_name='cn',
                                            create_pods_start=_PODS_CREATED_AT)

    def test_the_volume_is_reported_not_the_timeout(self):
        with pytest.raises(config_lib.KubernetesError) as exc:
            self._wait()

        assert 'tier is invalid' in str(exc.value)
        assert 'timed-out' not in str(exc.value)

    def test_the_wait_is_not_one_cluster_event_per_second(self):
        """The event is emitted from a loop that runs every second for as long
        as the wait lasts, so it has to be gated on the message changing."""
        # A claim that is slow rather than failing, so the loop runs to its
        # deadline instead of being cut short.
        self.cluster.set('vol', 'Pending', [
            _event('ExternalProvisioning',
                   'waiting for a volume to be created by the external '
                   'provisioner',
                   event_type='Normal')
        ])

        with pytest.raises(config_lib.KubernetesError, match='timed-out'):
            self._wait(timeout=600)

        reasons = [
            call.kwargs['reason']
            for call in self.add_cluster_event.call_args_list
        ]
        assert len(reasons) == 1, reasons
        assert 'vol' in reasons[0]
