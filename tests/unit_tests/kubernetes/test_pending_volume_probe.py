"""Unit tests for detecting a volume that will not bind while a pod waits.

Kubernetes reports an unbindable claim only through events, so the scheduling
wait loop otherwise learns of it by running out of time. These tests cover the
judgement that makes possible: a failure the claim's own events show lasting is
fatal, and one report of a failure -- which stays visible for an hour whether or
not the provisioner has moved on -- is not.
"""
import datetime
from typing import Optional
from unittest import mock

import pytest

from sky.provision import constants as prov_constants
from sky.provision.kubernetes import config as config_lib
from sky.provision.kubernetes import instance

_PODS_CREATED_AT = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
_CLOCK_START = 1000.0
_GRACE = instance._PVC_FAILURE_GRACE_SECONDS
# Verbatim from a GKE Filestore claim whose storage class named a tier that does
# not exist. InvalidArgument is what sig-storage's provisioner library calls an
# infeasible error, and it says so in the message itself.
_CSI_MESSAGE = (
    'Volume provisioning failed with infeasible error. Retries will be '
    'delayed. rpc error: code = InvalidArgument desc = Invalid value at '
    "'instance.tier' \"not-a-real-tier\", invalid")
# Verbatim from a claim that went on to bind: the driver's own call timed out
# while the filesystem was still being created.
_CSI_IN_PROGRESS_MESSAGE = ('rpc error: code = DeadlineExceeded desc = Volume '
                            'pvc-6ae55004 not ready, current state: CREATING')
# A failure with no gRPC code to go on, e.g. from a provisioner that does not
# report one. All that can be said about it is how long it lasts.
_OPAQUE_MESSAGE = ('failed to provision volume with StorageClass "slow": the '
                   'provisioner is unhappy')


def _at(offset: float) -> datetime.datetime:
    """The instant ``offset`` seconds after the pods were created."""
    return _PODS_CREATED_AT + datetime.timedelta(seconds=offset)


def _event(reason,
           message,
           event_type='Warning',
           first: float = 5,
           last: Optional[float] = None):
    """An event, optionally one Kubernetes has aggregated repeats into.

    ``first`` and ``last`` are offsets in seconds from pod creation. They differ
    when the same failure has been reported more than once: Kubernetes advances
    lastTimestamp on the existing event rather than creating another.
    """
    event = mock.MagicMock()
    event.reason = reason
    event.message = message
    event.type = event_type
    event.first_timestamp = _at(first)
    event.last_timestamp = _at(first if last is None else last)
    event.metadata.creation_timestamp = event.first_timestamp
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


class _Cluster:
    """A fake namespace whose PVC phases, events and clock the test controls.

    Event time and the probe's clock advance together, as they do in a real
    cluster: `advance()` moves both.
    """

    def __init__(self):
        self.phases = {}
        self.events = {}
        self.pvc_reads = 0
        self.clock = _CLOCK_START
        # PVCs whose failure is still being re-reported, so that lastTimestamp
        # keeps up with the clock, keyed by name -> when it started failing.
        self._failing_from = {}

    @property
    def elapsed(self) -> float:
        return self.clock - _CLOCK_START

    def set(self, pvc_name, phase, events=()):
        self.phases[pvc_name] = phase
        self.events[pvc_name] = list(events)
        self._failing_from.pop(pvc_name, None)

    def keep_failing(self,
                     pvc_name,
                     since: float = 5,
                     message: str = _OPAQUE_MESSAGE):
        """A provisioner that reports the same failure on every retry.

        Defaults to a failure carrying no gRPC code, since that is the case
        where all the probe has to go on is how long it persists.
        """
        self.phases[pvc_name] = 'Pending'
        self._failing_from[pvc_name] = (since, message)

    def stop_failing(self, pvc_name):
        """The failure stops being re-reported -- but stays visible for an hour,
        which is the whole reason persistence cannot be judged by looking."""
        self._failing_from.pop(pvc_name, None)

    def advance(self, seconds: float):
        self.clock += seconds

    def _events_for(self, pvc_name):
        failing = self._failing_from.get(pvc_name)
        if failing is None:
            return self.events.get(pvc_name, [])
        since, message = failing
        return [
            _event('ProvisioningFailed',
                   message,
                   first=since,
                   last=max(since, self.elapsed))
        ]

    def read_pvc(self, name, namespace, **kwargs):
        del namespace, kwargs  # unused
        self.pvc_reads += 1
        if name not in self.phases:
            raise ValueError(f'no such PVC: {name}')
        pvc = mock.MagicMock()
        pvc.status.phase = self.phases[name]
        return pvc


@pytest.fixture(name='cluster')
def cluster_fixture(monkeypatch):
    cluster = _Cluster()
    core_api = mock.MagicMock()
    core_api.read_namespaced_persistent_volume_claim.side_effect = (
        cluster.read_pvc)
    monkeypatch.setattr('sky.adaptors.kubernetes.core_api',
                        lambda *a, **kw: core_api)
    monkeypatch.setattr(instance.kubernetes_utils,
                        'get_pvc_events',
                        lambda context, namespace, pvc_name, reverse=True:
                        cluster._events_for(pvc_name))
    monkeypatch.setattr(instance.time, 'time', lambda: cluster.clock)
    return cluster


class TestGetPendingPvcs:
    """Which claims are pending, and over what period they have been failing."""

    def _pending(self, cluster, failures_since=_PODS_CREATED_AT):
        return instance._get_pending_pvcs('ns',
                                          'ctx',
                                          list(cluster.phases),
                                          failures_since=failures_since)

    def test_bound_claim_is_not_pending(self, cluster):
        cluster.set('vol', 'Bound')

        assert self._pending(cluster) == []

    def test_a_provisioning_failure_is_reported_as_one(self, cluster):
        cluster.set('vol', 'Pending',
                    [_event('ProvisioningFailed', _CSI_MESSAGE)])

        pending = self._pending(cluster)

        assert len(pending) == 1
        assert pending[0].failure is not None
        assert "instance.tier" in pending[0].detail
        assert 'vol' in pending[0].detail

    def test_one_report_spans_no_time(self, cluster):
        """The distinction the grace period rests on: a failure reported once
        has lasted no time at all, however long it stays visible."""
        cluster.set('vol', 'Pending',
                    [_event('ProvisioningFailed', _CSI_MESSAGE, first=5)])

        failure = self._pending(cluster)[0].failure

        assert failure.seconds_since(_PODS_CREATED_AT) == 0

    def test_repeated_reports_span_the_time_between_them(self, cluster):
        """Kubernetes aggregates them into one event, advancing lastTimestamp."""
        cluster.set(
            'vol', 'Pending',
            [_event('ProvisioningFailed', _CSI_MESSAGE, first=5, last=605)])

        failure = self._pending(cluster)[0].failure

        assert failure.seconds_since(_PODS_CREATED_AT) == 600

    def test_time_before_the_pods_does_not_count(self, cluster):
        """An event aggregated across an earlier launch of the same claim: only
        the part of it that belongs to this attempt does."""
        cluster.set(
            'vol', 'Pending',
            [_event('ProvisioningFailed', _CSI_MESSAGE, first=-600, last=60)])

        failure = self._pending(cluster)[0].failure

        assert failure.seconds_since(_PODS_CREATED_AT) == 60

    def test_a_failure_entirely_before_the_pods_is_ignored(self, cluster):
        """Events outlive the attempt that produced them, so a leftover warning
        must not fail a launch that is provisioning normally."""
        cluster.set(
            'vol', 'Pending',
            [_event('ProvisioningFailed', _CSI_MESSAGE, first=-600, last=-300)])

        pending = self._pending(cluster)

        assert pending[0].failure is None
        # Still reported: it is the best explanation available for the wait.
        assert "instance.tier" in pending[0].detail

    def test_without_an_anchor_any_failure_counts(self, cluster):
        """What the post-timeout path wants: by then nothing is going to bind
        the claim regardless of when it broke."""
        cluster.set(
            'vol', 'Pending',
            [_event('ProvisioningFailed', _CSI_MESSAGE, first=-600, last=-300)])

        assert self._pending(cluster,
                             failures_since=None)[0].failure is not None

    def test_waiting_for_first_consumer_is_not_a_failure(self, cluster):
        """The expected state of a WaitForFirstConsumer claim before its pod is
        scheduled. Treating it as broken would fail every such volume."""
        cluster.set('vol', 'Pending', [
            _event('WaitForFirstConsumer',
                   'waiting for first consumer to be created',
                   event_type='Normal')
        ])

        pending = self._pending(cluster)

        assert pending[0].failure is None
        assert 'WaitForFirstConsumer' in pending[0].detail

    def test_pending_with_no_events_is_not_a_failure(self, cluster):
        cluster.set('vol', 'Pending')

        pending = self._pending(cluster)

        assert pending[0].failure is None
        assert pending[0].detail == 'vol (phase: Pending)'

    def test_the_reported_reason_is_the_failure_not_a_newer_normal_event(
            self, cluster):
        """A provisioner emits a Normal event per attempt as well, so the newest
        event is not necessarily the one that says what went wrong."""
        cluster.set('vol', 'Pending', [
            _event('ProvisioningFailed', _CSI_MESSAGE, first=5),
            _event('WaitForFirstConsumer',
                   'waiting for first consumer to be created',
                   event_type='Normal',
                   first=10),
        ])

        assert "instance.tier" in self._pending(cluster)[0].detail

    def test_an_event_written_through_the_newer_api_still_spans(self, cluster):
        """events.k8s.io records the same two facts under other names. Reading
        only firstTimestamp/lastTimestamp would collapse the window to an instant
        and the claim could never be judged persistent."""
        event = _event('ProvisioningFailed', _CSI_MESSAGE)
        event.first_timestamp = None
        event.last_timestamp = None
        event.event_time = _at(5)
        event.series.last_observed_time = _at(605)
        cluster.set('vol', 'Pending', [event])

        failure = self._pending(cluster)[0].failure

        assert failure.seconds_since(_PODS_CREATED_AT) == 600

    def test_waiting_on_the_pod_is_reported_as_that(self, cluster):
        """A claim whose pod cannot be scheduled: reporting the earlier
        WaitForFirstConsumer instead would say nothing has claimed the volume,
        when something has and the pod is the thing that is stuck."""
        cluster.set('vol', 'Pending', [
            _event('WaitForFirstConsumer',
                   'waiting for first consumer to be created before binding',
                   event_type='Normal',
                   first=5),
            _event('WaitForPodScheduled',
                   'waiting for pod cn-head to be scheduled',
                   event_type='Normal',
                   first=60),
        ])

        detail = self._pending(cluster)[0].detail

        assert 'waiting for pod cn-head to be scheduled' in detail
        assert 'first consumer' not in detail

    def test_an_unreadable_claim_is_not_reported(self, cluster):
        """Fail open: a transient API error must not fail the launch."""
        assert instance._get_pending_pvcs('ns', 'ctx', ['missing']) == []


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

    def _probe(self):
        return instance._PendingVolumeProbe(namespace='ns',
                                            context='ctx',
                                            cluster_name='cn',
                                            pods_created_at=_PODS_CREATED_AT)

    @staticmethod
    def _step(cluster):
        """Past the next probe, so every call to probe() does something."""
        cluster.advance(instance._PVC_PROBE_INITIAL_DELAY_SECONDS +
                        instance._PVC_PROBE_INTERVAL_SECONDS)

    def test_nothing_is_probed_before_the_first_delay(self, cluster):
        cluster.keep_failing('vol')
        probe = self._probe()

        assert probe.probe([_pod(['vol'])]) is None
        assert cluster.pvc_reads == 0

    def test_a_backend_that_rejects_the_request_fails_the_launch_at_once(
            self, cluster):
        """Nothing is learned by waiting: the size, the tier or the source has
        to change before this claim can bind."""
        cluster.keep_failing('vol', message=_CSI_MESSAGE)
        probe = self._probe()
        self._step(cluster)

        with pytest.raises(config_lib.KubernetesError) as exc:
            probe.probe([_pod(['vol'])])

        assert cluster.elapsed < _GRACE, 'should not have waited'
        assert 'vol' in str(exc.value)
        assert "'instance.tier'" in str(exc.value)
        assert 'kubectl describe pvc vol' in str(exc.value)

    def test_a_call_that_may_still_be_running_never_fails_the_launch(
            self, cluster):
        """What GKE Filestore reports for minutes on the way to a healthy
        volume: the driver's own call timed out while the filesystem was still
        being created. Failing on it would kill a launch that was going to
        work."""
        cluster.keep_failing('vol', message=_CSI_IN_PROGRESS_MESSAGE)
        probe = self._probe()
        pods = [_pod(['vol'])]

        message = None
        while cluster.elapsed < _GRACE * 2:
            self._step(cluster)
            message = probe.probe(pods)

        assert message is not None
        assert 'vol' in message

    def test_an_unclassified_failure_reported_once_never_fails_the_launch(
            self, cluster):
        """The case the grace period exists for, and the one a wall-clock
        version of it gets wrong: one failure at the start, then a provisioner
        quietly taking minutes to finish. The event stays visible throughout, so
        looking at it repeatedly proves nothing."""
        cluster.set('vol', 'Pending',
                    [_event('ProvisioningFailed', _OPAQUE_MESSAGE, first=5)])
        probe = self._probe()
        pods = [_pod(['vol'])]

        message = None
        while cluster.elapsed < _GRACE * 3:
            self._step(cluster)
            message = probe.probe(pods)

        assert message is not None
        assert 'vol' in message

    def test_an_unclassified_failure_that_persists_fails_the_launch(
            self, cluster):
        cluster.keep_failing('vol', since=5)
        probe = self._probe()
        pods = [_pod(['vol'])]

        with pytest.raises(config_lib.KubernetesError) as exc:
            while cluster.elapsed < _GRACE * 2:
                self._step(cluster)
                probe.probe(pods)

        assert cluster.elapsed >= _GRACE
        assert 'vol' in str(exc.value)
        assert 'provisioner is unhappy' in str(exc.value)

    def test_an_unclassified_failure_that_stops_does_not_fail_the_launch(
            self, cluster):
        """It failed for a while, then the provisioner got on with it. The
        event's span stops growing even though the event is still there."""
        cluster.keep_failing('vol', since=5)
        probe = self._probe()
        pods = [_pod(['vol'])]

        while cluster.elapsed < _GRACE / 2:
            self._step(cluster)
            probe.probe(pods)
        cluster.stop_failing('vol')
        cluster.set('vol', 'Pending', [
            _event('ProvisioningFailed',
                   _OPAQUE_MESSAGE,
                   first=5,
                   last=cluster.elapsed)
        ])

        while cluster.elapsed < _GRACE * 3:
            self._step(cluster)
            probe.probe(pods)  # Does not raise.

    def test_a_failure_is_never_confirmed_while_a_node_is_on_its_way(
            self, cluster):
        """A topology-aware CSI driver in a cluster scaled to zero reports the
        same failure as one that will never succeed. Waiting is right until the
        node arrives, however long the autoscaler takes."""
        cluster.keep_failing('vol', since=5)
        probe = self._probe()
        pods = [_pod(['vol'])]

        message = None
        while cluster.elapsed < _GRACE * 3:
            self._step(cluster)
            message = probe.probe(pods, hold_failures=True)

        assert message is not None
        assert 'vol' in message

    def test_the_grace_period_restarts_once_the_node_has_arrived(self, cluster):
        """The hold discounts the failures seen during it -- their cause may
        have been the missing node."""
        cluster.keep_failing('vol', since=5)
        probe = self._probe()
        pods = [_pod(['vol'])]

        while cluster.elapsed < _GRACE * 2:
            self._step(cluster)
            probe.probe(pods, hold_failures=True)

        # Held long past the grace period, so without the discount the next
        # unheld probe would fail immediately.
        held_until = cluster.elapsed
        while cluster.elapsed < held_until + _GRACE / 2:
            self._step(cluster)
            probe.probe(pods)  # Does not raise.

        with pytest.raises(config_lib.KubernetesError):
            while cluster.elapsed < held_until + _GRACE * 2:
                self._step(cluster)
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

        while cluster.elapsed < _GRACE * 3:
            self._step(cluster)
            message = probe.probe([_pod(['vol'])])
            assert message is not None
            assert 'vol' in message

    def test_the_message_is_short_enough_to_be_a_spinner(self, cluster):
        """It is the only thing on screen for minutes, so it carries the claim
        and the reason -- not the paragraph the provisioner writes."""
        cluster.set('vol', 'Pending', [
            _event('ExternalProvisioning',
                   'Waiting for a volume to be created either by the external '
                   "provisioner 'filestore.csi.storage.gke.io' or manually by "
                   'the system administrator. If volume creation is delayed, '
                   'please verify that the provisioner is running and '
                   'correctly registered.',
                   event_type='Normal')
        ])
        probe = self._probe()
        self._step(cluster)

        message = probe.probe([_pod(['vol'])])

        assert message == 'waiting for volume(s): vol - ExternalProvisioning'

    def test_several_claims_are_folded_into_a_count(self, cluster):
        """A pod can mount an auto-mounted volume, an inline one and its own."""
        for name in ('vol-a', 'vol-b', 'vol-c'):
            cluster.set(name, 'Pending',
                        [_event('WaitForFirstConsumer', 'w', 'Normal')])
        probe = self._probe()
        self._step(cluster)

        message = probe.probe([_pod(['vol-a', 'vol-b', 'vol-c'])])

        assert message == ('waiting for volume(s): '
                           'vol-a - WaitForFirstConsumer, +2 more')

    def test_the_claim_that_is_complaining_is_the_one_shown(self, cluster):
        """Whichever position it is in: a claim that is merely waiting says the
        same thing for minutes, and only one of them fits on the line."""
        cluster.set('vol-a', 'Pending',
                    [_event('WaitForFirstConsumer', 'w', 'Normal')])
        cluster.set('vol-b', 'Pending',
                    [_event('ProvisioningFailed', _CSI_IN_PROGRESS_MESSAGE)])
        probe = self._probe()
        self._step(cluster)

        message = probe.probe([_pod(['vol-a', 'vol-b'])])

        assert message == ('waiting for volume(s): '
                           'vol-b - ProvisioningFailed, +1 more')

    def test_the_log_keeps_what_the_spinner_drops(self, cluster):
        """The spinner is gone by the time anyone reads back why a launch took
        as long as it did."""
        cluster.set('vol', 'Pending', [
            _event('ProvisioningFailed', _CSI_IN_PROGRESS_MESSAGE),
            _event('Provisioning', 'External provisioner is provisioning it',
                   'Normal'),
        ])
        probe = self._probe()
        self._step(cluster)

        with mock.patch.object(instance.logger, 'info') as log:
            message = probe.probe([_pod(['vol'])])

        logged = log.call_args[0][0]
        assert _CSI_IN_PROGRESS_MESSAGE in logged
        assert message is not None and _CSI_IN_PROGRESS_MESSAGE not in message

    def test_a_change_the_spinner_hides_is_still_logged(self, cluster):
        """Only the first claim reaches the spinner, so the log cannot be keyed
        on it: the others would change in silence."""
        cluster.set('vol-a', 'Pending',
                    [_event('WaitForFirstConsumer', 'w', 'Normal')])
        cluster.set('vol-b', 'Pending',
                    [_event('WaitForFirstConsumer', 'w', 'Normal')])
        probe = self._probe()
        pods = [_pod(['vol-a', 'vol-b'])]
        self._step(cluster)
        with mock.patch.object(instance.logger, 'info') as log:
            probe.probe(pods)
            assert log.call_count == 1

            cluster.set('vol-b', 'Pending',
                        [_event('ExternalProvisioning', 'p', 'Normal')])
            self._step(cluster)
            probe.probe(pods)

            assert log.call_count == 2

    def test_the_last_finding_is_repeated_between_probes(self, cluster):
        """The wait loop polls once a second, far faster than the probe
        interval, and needs something to display in between."""
        cluster.set('vol', 'Pending')
        probe = self._probe()
        self._step(cluster)
        message = probe.probe([_pod(['vol'])])

        assert probe.probe([_pod(['vol'])]) == message
        assert cluster.pvc_reads == 1

    def test_a_claim_that_binds_clears_the_message(self, cluster):
        cluster.set('vol', 'Pending')
        probe = self._probe()
        self._step(cluster)
        assert probe.probe([_pod(['vol'])]) is not None

        cluster.set('vol', 'Bound')
        self._step(cluster)

        assert probe.probe([_pod(['vol'])]) is None

    def test_pods_without_claims_cost_nothing(self, cluster):
        pod = mock.MagicMock()
        pod.spec.volumes = None
        probe = self._probe()
        self._step(cluster)

        assert probe.probe([pod]) is None
        assert cluster.pvc_reads == 0

    def test_a_shared_claim_is_read_once_per_probe(self, cluster):
        cluster.set('vol', 'Pending')
        probe = self._probe()
        self._step(cluster)

        probe.probe([_pod(['vol'], name='pod-0'), _pod(['vol'], name='pod-1')])

        assert cluster.pvc_reads == 1


class TestSchedulingLoopFailsOnABrokenVolume:
    """The wiring: the scheduling wait loop must report a broken claim rather
    than wait out provision_timeout, which is 24 hours with a queue admission
    controller configured.
    """

    # Simulated seconds at one iteration per second, capped so that removing the
    # probe fails this test instead of looking like a hang.
    _MAX_ITERATIONS = 4000

    @pytest.fixture(autouse=True)
    def _harness(self, monkeypatch, cluster):
        self.cluster = cluster
        self.pod = _pod(['vol'])
        cluster.keep_failing('vol', since=5, message=_CSI_MESSAGE)

        core_api = mock.MagicMock()
        core_api.list_namespaced_pod.return_value = mock.MagicMock(
            items=[self.pod])
        core_api.read_namespaced_persistent_volume_claim.side_effect = (
            cluster.read_pvc)
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

        iterations = {'n': 0}

        def _sleep(seconds):
            iterations['n'] += 1
            if iterations['n'] > self._MAX_ITERATIONS:
                raise AssertionError('the wait loop did not report the broken '
                                     'volume')
            cluster.advance(seconds)

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

        assert "instance.tier" in str(exc.value)
        assert 'timed-out' not in str(exc.value)

    def test_the_hold_on_a_scaling_cluster_expires(self, monkeypatch):
        """A scale-up suspends the failure judgement, but only for as long as
        the deadline extension it borrows that from. The signal it keys on is
        never cleared and is looked up namespace-wide, so an unrelated pod's
        scale-up would otherwise disable this check for the whole wait."""
        monkeypatch.setattr(
            'sky.skypilot_config.get_effective_region_config',
            lambda cloud, region, keys, default_value=None, **kw: 'gke'
            if keys == ('autoscaler',) else default_value)
        monkeypatch.setattr(instance, '_cluster_had_autoscale_event',
                            lambda *a, **kw: True)

        with pytest.raises(config_lib.KubernetesError) as exc:
            self._wait()

        assert "instance.tier" in str(exc.value)
        assert (self.cluster.elapsed >=
                instance._AUTOSCALE_DETECTED_TIMEOUT_SECONDS)

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
