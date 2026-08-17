"""Kubernetes instance provisioning."""
import copy
import datetime
import json
import re
import sys
import time
from typing import (Any, Callable, Dict, List, Mapping, NamedTuple, Optional,
                    Set, Tuple, TYPE_CHECKING, Union)

from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import kubernetes
from sky.provision import common
from sky.provision import constants
from sky.provision import docker_utils
from sky.provision.kubernetes import config as config_lib
from sky.provision.kubernetes import constants as k8s_constants
from sky.provision.kubernetes import host_network_probe
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.provision.kubernetes import volume
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import config_utils
from sky.utils import kubernetes_enums
from sky.utils import plugin_extensions
from sky.utils import rich_utils
from sky.utils import status_lib
from sky.utils import subprocess_utils
from sky.utils import timeline
from sky.utils import ux_utils
from sky.utils.db import db_utils

if TYPE_CHECKING:
    from kubernetes.client import V1Pod

POLL_INTERVAL = 2
_TIMEOUT_FOR_POD_TERMINATION = 60  # 1 minutes
_MAX_RETRIES = 3
_MAX_MISSING_PODS_RETRIES = 5
_MAX_QUERY_INSTANCES_RETRIES = 5
_QUERY_INSTANCES_RETRY_INTERVAL = .5
# Once a definitive cluster autoscaling event (TriggeredScaleUp) is observed,
# extend the pod scheduling deadline from the detection moment by this many
# seconds. Node scale-up time is unpredictable (often 5-20 min) and the
# user-configured provision_timeout is typically tuned for normal scheduling
# latency; with positive evidence that the autoscaler is working on the
# request, we give it a generous window to complete before failing over.
#
# Only TriggeredScaleUp is used as the trigger here — the FailedScheduling
# heuristic (Karpenter fallback) is NOT reliable enough to extend a deadline
# by 15 min, because FailedScheduling also fires for genuine resource
# mismatches (oversized requests, taints, PVC issues, etc.) which would
# otherwise be masked as "autoscaling in progress" and waste the full window.
_AUTOSCALE_DETECTED_TIMEOUT_SECONDS = 900  # 15 minutes
# When an autoscaler is configured, ensure the initial wait is at least this
# long so the Cluster Autoscaler has a chance to scan (default scan interval
# is 10s) and emit the first TriggeredScaleUp event. Without this floor, a
# short user-configured provision_timeout (e.g. the default 10s) would exit
# the wait loop before any event is emitted, defeating the detection logic.
_AUTOSCALE_INITIAL_MIN_TIMEOUT_SECONDS = 60
# A pod held by a scheduling gate (e.g. Kueue's kueue.x-k8s.io/admission) is
# waiting for an external admission controller to admit it — a quota wait,
# not a provisioning delay. provision_timeout is tuned for scheduling
# latency and must not count this phase: an explicitly configured
# provision_timeout would otherwise silently cap queue waits (e.g. a 30s
# value kills every queue wait), defeating the point of queueing. While any
# expected pod is gated, the provisioning clock is paused and the gated wait
# is bounded separately, by kubernetes.kueue.admission_timeout. This is that
# knob's default (matching the default provision_timeout applied when a
# Kueue local queue is configured); -1 waits indefinitely.
_QUEUE_ADMISSION_TIMEOUT_SECONDS = 24 * 60 * 60  # 24 hours
# Request timeout for the pod polling loops (_wait_for_pods_to_schedule /
# _wait_for_pods_to_run): (connect, read) seconds. Without a request timeout,
# a connection that stops receiving data without being closed (e.g. silently
# dropped by a NAT/LB after an idle or lifetime limit) blocks the poll
# forever: the loop stops iterating and the launch hangs until
# provision_timeout, which can be hours in autoscaling/queueing setups. The
# read timeout bounds each socket read (idle time), not the whole response,
# so large pod lists that stream slowly are unaffected.
_POD_POLL_REQUEST_TIMEOUT = (5, 30)
# How long a continuous streak of pod-poll transport errors may last before
# it surfaces as an error. A transient failure (timeout, dropped connection)
# is treated as a missed poll and retried, but a persistently unreachable API
# server should still surface instead of retrying silently forever. The
# budget is wall-clock rather than attempt-based so that fast-failing errors
# (e.g. connection refused) get the same tolerance as slow read timeouts.
_POD_POLL_TRANSPORT_ERROR_GRACE_SECONDS = 180
_NUM_THREADS = subprocess_utils.get_parallel_threads('kubernetes')

# Normal-type pod events that represent slow, legitimately-in-flight steps
# whose state.waiting.reason is the uninformative 'ContainerCreating'.
# Consulted only as a fallback when no Warning-type event is present.
_PENDING_REASON_NORMAL_EVENT_ALLOWLIST = {
    'Pulling',  # kubelet pulling image (can be minutes for large images)
    'Provisioning',  # external CSI provisioner creating a PV
    'WaitForFirstConsumer',  # late-binding storage class
}

# Warning-type pod events that are emitted once during normal startup and
# then left on the pod after the condition they describe has resolved. The
# Warning pass skips them, so the scan falls through to a later Warning or to
# an allow-listed Normal instead of pinning a healthy launch to a stale
# complaint.
_PENDING_REASON_WARNING_EVENT_IGNORELIST = {
    # Kueue's pod reconciler races the creation of a pod group: it fires on
    # the first pod it observes, sees fewer live pods than
    # pod-group-total-count, and emits this Warning. It self-resolves within
    # seconds once the remaining pods exist, but the event object survives
    # for its full TTL -- see
    # https://kueue.sigs.k8s.io/docs/tasks/troubleshooting/troubleshooting_pods/
    'ErrWorkloadCompose',
}

# Warning-type pod events (emitted by the kubelet, after scheduling) that
# indicate a volume attach/mount failure. A pod hit by one of these stays in
# the uninformative 'ContainerCreating' waiting state, so the failure is only
# visible through events.
_MOUNT_FAILURE_EVENT_REASONS = ('FailedMount', 'FailedAttachVolume')
# How long a pod may continuously report a mount-failure event before
# provisioning is failed. Mount failures are frequently transient — the
# kubelet retries with backoff, and e.g. a CSI driver still starting on a
# freshly scaled-up node or a slow first attach of a network volume resolves
# on its own — so use a generous window. Persistent failures (mis-configured
# PVC/storage class, missing secret/configmap) never resolve; without this
# deadline they would hang provisioning forever with only a spinner update.
_MOUNT_FAILURE_TIMEOUT_SECONDS = 600

# Reason of the Normal event the scheduler emits when it binds a pod to a node
# ('Successfully assigned <ns>/<pod> to <node>'). Marks the boundary between
# the scheduler's warnings and the kubelet's.
_POD_BOUND_EVENT_REASON = 'Scheduled'

# Synthetic pending reason used when a pod is bound to a node but the kubelet
# has not reported anything yet (the uninformative 'ContainerCreating' waiting
# state with no event).
_CONTAINER_CREATION_REASON = 'container creation'
# Prefix of the synthetic pending reasons describing an init container that is
# running, or that the kubelet is still creating.
_INIT_CONTAINER_REASON_PREFIX = 'init container '
# Synthetic pending reason for a pod that reports 'PodInitializing' while no
# init container is running or being created -- i.e. they have all terminated
# successfully and the kubelet has not moved on to the main containers. Kept
# distinct from the reasons above precisely so it is *not* stall-exempt: there
# is no legitimately-slow work behind it to wait for.
_POD_INITIALIZATION_REASON = 'pod initialization'
# Pending reasons that can legitimately persist, unchanged, for a very long
# time, and so must not count towards the no-progress deadline below:
#   - the allow-listed Normal events (pulling a large image, an external CSI
#     provisioner creating a volume, a late-binding storage class),
#   - 'container creation', which is also what a pod reports while pulling an
#     image whose 'Pulling' event has already aged out of the event window,
#   - a running init container, which may be doing arbitrary user work, and an
#     init container the kubelet is still creating, which may be pulling a
#     large image of its own -- the latter only reaches this exemption when no
#     live Warning event contradicts it, see _inspect_pod_status.
_STALL_EXEMPT_PENDING_REASONS = frozenset(
    _PENDING_REASON_NORMAL_EVENT_ALLOWLIST | {_CONTAINER_CREATION_REASON})
# How long a pod may keep reporting the same non-exempt pending reason before
# provisioning is failed. Every pod reaching _wait_for_pods_to_run is already
# bound to a node (_wait_for_pods_to_schedule only returns once all pods are
# scheduled), so no queue-admission or autoscaling wait remains: what is left
# is kubelet-side work, and any of it stuck on the same reason for this long is
# not going to resolve. Without this deadline such a pod hangs the launch
# forever behind a 'Launching' spinner, with no error, ever.
_POD_RUN_STALL_TIMEOUT_SECONDS = 600

# How long to wait before first probing the volumes of a pod that is not up
# yet, and how long between probes after that. A probe costs one GET per claim,
# plus an event LIST per claim that is still Pending, so it must be much slower
# than the once-a-second pod poll it rides along with. The first probe is
# delayed because a claim being Pending right after the pod is created is the
# normal case, not a signal.
_PVC_PROBE_INITIAL_DELAY_SECONDS = 10
_PVC_PROBE_INTERVAL_SECONDS = 15
# How long a claim's own events must span before provisioning is failed, when
# the failure cannot be classified (see volume.classify_pvc_failure). A failure
# the storage backend reports by gRPC code is judged on the code instead, and
# needs no waiting: this is the fallback for a provisioner that reports
# something else, where all we have to go on is that it keeps saying it.
#
# Measured over the events (see FailureWindow), not over how long we have been
# watching them: a single warning stays visible for an hour, so a claim that
# failed once and is now being provisioned normally would otherwise look
# indistinguishable from one that has been failing throughout.
#
# An hour is deliberately far longer than any provisioning that is going to
# succeed, because getting this wrong fails a launch whose volume was fine. It
# is therefore only ever reached by a provision_timeout longer than an hour --
# in practice a queue admission controller's 24h, or one set by hand. Shorter
# timeouts expire first and report the same claim through the same formatter,
# which is the behaviour that predates this check.
_PVC_FAILURE_GRACE_SECONDS = 3600

# Pattern to extract SSH user from command output, handling MOTD contamination
_SSH_USER_PATTERN = re.compile(r'SKYPILOT_SSH_USER: ([^\s\n]+)')


class _InitContainerProgress(NamedTuple):
    """The init container currently holding up a pod's initialization."""
    name: str
    position: int  # 1-based, for display against `total`.
    total: int
    # True if the container is running its own work; False if the kubelet is
    # still creating it (typically pulling its image).
    running: bool


logger = sky_logging.init_logger(__name__)


def ray_tag_filter(cluster_name: str) -> Dict[str, str]:
    return {k8s_constants.TAG_RAY_CLUSTER_NAME: cluster_name}


def _is_head(pod) -> bool:
    return pod.metadata.labels.get(constants.TAG_RAY_NODE_KIND) == 'head'


def _get_head_pod_name(pods: Dict[str, Any]) -> Optional[str]:
    return next((pod_name for pod_name, pod in pods.items() if _is_head(pod)),
                None)


def _pod_is_scheduled(pod) -> bool:
    """Whether the kube-scheduler has bound this pod to a node.

    The scheduler sets ``spec.nodeName`` (and the ``PodScheduled`` status
    condition to ``True``) the moment it places a pod -- i.e. capacity has
    been found. The kubelet on the target node only later populates
    ``status.container_statuses`` / ``host_ip`` once it picks the pod up and
    starts the sandbox. That kubelet pickup can occasionally lag past
    ``provision_timeout`` when the control plane is slow to propagate the
    binding to the kubelet, even though the pod is already bound to a node.

    We treat a bound pod as scheduled so that provisioning hands off to
    ``_wait_for_pods_to_run`` (which waits for containers without the short
    ``provision_timeout``) instead of failing over as if the cluster were out
    of resources. A genuinely unschedulable pod keeps ``PodScheduled`` False
    and no ``nodeName``, so it stays in the scheduling wait loop.
    """
    # Running/Succeeded/Failed pods are clearly past scheduling; Failed pods
    # are surfaced as errors later in _wait_for_pods_to_run.
    if pod.status.phase != 'Pending':
        return True
    # spec.nodeName is set atomically when the scheduler binds the pod.
    if pod.spec.node_name:
        return True
    # Fall back to the PodScheduled status condition.
    for condition in (pod.status.conditions or []):
        if condition.type == 'PodScheduled' and condition.status == 'True':
            return True
    return False


def _get_pvc_name(cluster_name: str, volume_name: str) -> str:
    return f'{cluster_name}-{volume_name}'


def _get_deployment_name(cluster_name: str) -> str:
    return f'{cluster_name}-deployment'


def _head_service_selector(cluster_name: str) -> Dict[str, str]:
    return {'component': f'{cluster_name}-head'}


def is_high_availability_cluster_by_kubectl(
        cluster_name: str,
        context: Optional[str] = None,
        namespace: Optional[str] = None) -> bool:
    """Check if a cluster is a high availability controller by calling
    `kubectl get deployment`.

    The deployment must have the label `skypilot-cluster-name` set to
    `cluster_name`.
    """
    try:
        deployment_list = kubernetes.apps_api(
            context).list_namespaced_deployment(
                namespace,
                label_selector=
                f'{constants.TAG_SKYPILOT_CLUSTER_NAME}={cluster_name}')
    except kubernetes.api_exception():
        return False
    # It is a high availability cluster if there is at least one deployment
    # matching the label selector.
    return bool(deployment_list.items)


def _formatted_resource_requirements(pod_or_spec: Union[Any, dict]) -> str:
    # Returns a formatted string of resource requirements for a pod.
    resource_requirements = {}

    if isinstance(pod_or_spec, dict):
        containers = pod_or_spec.get('spec', {}).get('containers', [])
    else:
        containers = pod_or_spec.spec.containers

    for container in containers:
        if isinstance(container, dict):
            resources = container.get('resources', {})
            requests = resources.get('requests', {})
        else:
            resources = container.resources
            requests = resources.requests or {}

        for resource, value in requests.items():
            if resource not in resource_requirements:
                resource_requirements[resource] = 0
            if resource == 'memory':
                int_value = kubernetes_utils.parse_memory_resource(value)
            else:
                int_value = kubernetes_utils.parse_cpu_or_gpu_resource(value)
            resource_requirements[resource] += int(int_value)
    return ', '.join(f'{resource}={value}'
                     for resource, value in resource_requirements.items())


def _formatted_node_selector(pod_or_spec: Union[Any, dict]) -> Optional[str]:
    # Returns a formatted string of node selectors for a pod.
    node_selectors = []

    if isinstance(pod_or_spec, dict):
        selectors = pod_or_spec.get('spec', {}).get('nodeSelector', {})
    else:
        selectors = pod_or_spec.spec.node_selector

    if not selectors:
        return None

    for label_key, label_value in selectors.items():
        node_selectors.append(f'{label_key}={label_value}')
    return ', '.join(node_selectors)


def _lack_resource_msg(resource: str,
                       pod_or_spec: Union[Any, dict],
                       extra_msg: Optional[str] = None,
                       details: Optional[str] = None) -> str:
    resource_requirements = _formatted_resource_requirements(pod_or_spec)
    node_selectors = _formatted_node_selector(pod_or_spec)
    node_selector_str = f' and labels ({node_selectors})' if (
        node_selectors) else ''
    msg = (f'Insufficient {resource} capacity on the cluster. '
           f'Required resources ({resource_requirements}){node_selector_str} '
           'were not found in a single node. Other SkyPilot tasks or pods may '
           'be using resources. Check resource usage by running '
           '`kubectl describe nodes`.')
    if extra_msg:
        msg += f' {extra_msg}'
    if details:
        msg += f'\nFull error: {details}'
    return msg


def _format_pvc_binding_error(pvc_details: Optional[str], pvc_names: List[str],
                              namespace: str) -> str:
    """Format a PVC binding error message.

    Args:
        pvc_details: Optional details about the PVC issue (e.g., event messages)
            If None, a generic message is used.
        pvc_names: List of PVC names that have binding issues.
        namespace: Kubernetes namespace.

    Returns:
        Formatted error message with debug instructions.
    """
    if pvc_details:
        header = f'PVC binding issue detected: {pvc_details}.'
    else:
        header = 'PVC binding issue detected.'
    debug_lines = ['To debug, run:', '  sky volumes ls']
    if pvc_names:
        # kubectl describe pvc can take multiple PVC names as args
        pvc_names_str = ' '.join(pvc_names)
        debug_lines.append(
            f'  kubectl describe pvc {pvc_names_str} -n {namespace}')
    return (f'{header}\n'
            'Check if the storage class supports the requested access '
            'mode and if there is sufficient storage capacity.\n' +
            '\n'.join(debug_lines))


class FailureWindow(NamedTuple):
    """The period a claim has been reporting a failure over.

    Kubernetes aggregates a repeated event into the existing one, advancing its
    lastTimestamp, so this widens for as long as a provisioner keeps failing and
    stays a single instant for a one-off failure it then recovers from. That
    makes it, and not the number of times we happen to look, the signal for
    whether waiting is pointless.
    """
    first: datetime.datetime
    last: datetime.datetime

    def seconds_since(self, start: datetime.datetime) -> float:
        """How long the failure lasted, ignoring anything before ``start``."""
        return (self.last - max(self.first, start)).total_seconds()


class PendingPvc(NamedTuple):
    """A PVC a pod needs that has not been bound yet."""
    name: str
    # '<name> (phase: Pending)', plus ' - <reason>: <message>' when an event
    # explains it. For the log and for the error a failure raises, where the
    # provisioner's own words are what someone reading back needs.
    detail: str
    # '<name> - <reason>', for the spinner: one line that has to stay readable
    # while it is the only thing on screen. The reason is the part of `detail`
    # that moves (WaitForFirstConsumer -> WaitForPodScheduled ->
    # ExternalProvisioning), so it says whether the wait is progressing; the
    # message after it is fixed text that would push the line off the terminal.
    summary: str
    # Whether the claim has reported a Warning at all. Not the same as either
    # of the two below: a failure whose gRPC code says the call may still be
    # running is deliberately not counted as one, but it is still the most
    # interesting thing the claim has said, so it is what the spinner shows.
    warned: bool
    # Whether the storage backend has reported something that cannot succeed
    # however long it is retried, e.g. a size its API rejects.
    terminal: bool
    # When the claim reported a failure that might yet be one, or None if
    # nothing has: nothing is wrong, or what is wrong may still resolve. Only
    # meaningful while `terminal` is False, and only as a fallback for failures
    # the gRPC code does not classify.
    failure: Optional[FailureWindow]


def _pod_pvc_names(pod: Any) -> List[str]:
    """The names of the PVCs a pod mounts, in the pod's own order."""
    if pod.spec.volumes is None:
        return []
    names = [
        vol.persistent_volume_claim.claim_name
        for vol in pod.spec.volumes
        if vol.persistent_volume_claim is not None
    ]
    # A multi-node cluster mounts the same ReadWriteMany volume on every pod.
    return list(dict.fromkeys(names))


def _utc(timestamp: Any) -> Optional[datetime.datetime]:
    if timestamp is None:
        return None
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=datetime.timezone.utc)
    return timestamp.astimezone(datetime.timezone.utc)


def _event_window(
    event: Any
) -> Tuple[Optional[datetime.datetime], Optional[datetime.datetime]]:
    """When an event was first and most recently reported.

    firstTimestamp/lastTimestamp are what client-go's EventRecorder maintains,
    and what an aggregated repeat advances. An event written through the newer
    events.k8s.io API carries the same two facts under different names, so read
    those as well rather than collapsing to creationTimestamp -- both endpoints
    landing on the same instant would say the failure lasted no time at all, and
    so could never be judged persistent.
    """
    created = _utc(event.metadata.creation_timestamp)
    series = getattr(event, 'series', None)
    last = (_utc(event.last_timestamp) or
            _utc(getattr(series, 'last_observed_time', None)) or
            _utc(event.event_time) or created)
    first = _utc(event.first_timestamp) or _utc(event.event_time) or created
    return first, last


def _get_pending_pvcs(
    namespace: str,
    context: Optional[str],
    pvc_names: List[str],
    failures_since: Optional[datetime.datetime] = None,
) -> List[PendingPvc]:
    """Which of the given PVCs are still Pending, and whether they are failing.

    Args:
        failures_since: ignore failures reported before this. Events outlive the
            attempt that produced them (the API server keeps them for an hour by
            default), so without an anchor a warning left behind by an earlier
            launch would look like this launch's problem. None accepts any
            warning, which is what the post-timeout path wants -- by then
            nothing is going to bind the claim regardless of when it broke.
    """
    pending_pvcs: List[PendingPvc] = []
    for pvc_name in pvc_names:
        try:
            pvc = kubernetes.core_api(
                context).read_namespaced_persistent_volume_claim(
                    name=pvc_name,
                    namespace=namespace,
                    _request_timeout=kubernetes.API_TIMEOUT)
            if pvc.status.phase != 'Pending':
                continue
            # Get events for the PVC to understand why it's pending
            sorted_events = kubernetes_utils.get_pvc_events(context,
                                                            namespace,
                                                            pvc_name,
                                                            reverse=False)
            # (reason, message) rather than one string, so the spinner can take
            # the reason alone while the log and the error keep both.
            event_explanations: List[Tuple[str, str]] = []
            warning_explanations: List[Tuple[str, str]] = []
            terminal = False
            failure: Optional[FailureWindow] = None
            for event in sorted_events:
                is_failure = event.type == 'Warning'
                # The Normal reasons say why a claim is still pending -- which
                # pod it is waiting for, which provisioner is working on it --
                # and are worth reporting even though none of them is a failure.
                if not is_failure and (event.reason
                                       not in volume.PVC_PENDING_EVENT_REASONS):
                    continue
                msg = event.message or ''
                if msg:
                    event_explanations.append((event.reason, msg))
                if not is_failure:
                    continue
                first, last = _event_window(event)
                if first is None or last is None:
                    continue
                if failures_since is not None and last < failures_since:
                    continue
                if msg:
                    warning_explanations.append((event.reason, msg))
                kind = volume.classify_pvc_failure(msg)
                if kind == volume.PvcFailure.TERMINAL:
                    terminal = True
                elif kind == volume.PvcFailure.IN_PROGRESS:
                    # The call behind it may still be running, so this says
                    # nothing about whether the claim will bind. Reported, not
                    # counted.
                    continue
                if failure is None:
                    failure = FailureWindow(first=first, last=last)
                else:
                    failure = FailureWindow(first=min(failure.first, first),
                                            last=max(failure.last, last))
            pending_info = f'{pvc_name} (phase: Pending)'
            summary = pvc_name
            # Prefer the newest warning when there is one: it is the reason the
            # claim is failing, and a Normal event can be newer than it.
            explanations = warning_explanations or event_explanations
            if explanations:
                reason, msg = explanations[-1]
                pending_info += f' - {reason}: {msg}'
                summary += f' - {reason}'
            pending_pvcs.append(
                PendingPvc(name=pvc_name,
                           detail=pending_info,
                           summary=summary,
                           warned=bool(warning_explanations),
                           terminal=terminal,
                           failure=failure))
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(f'Failed to get PVC {pvc_name} status: {e}')
            continue
    return pending_pvcs


def _get_pvc_binding_status(namespace: str, context: Optional[str],
                            pod: Any) -> Optional[str]:
    """Check if any PVCs used by a pod are pending/unbound.

    Returns an error message if any PVC is pending, None otherwise.
    """
    pending_pvcs = _get_pending_pvcs(namespace, context, _pod_pvc_names(pod))
    if not pending_pvcs:
        return None
    return _format_pvc_binding_error(
        pvc_details=', '.join(pvc.detail for pvc in pending_pvcs),
        pvc_names=[pvc.name for pvc in pending_pvcs],
        namespace=namespace)


class _PendingVolumeProbe:
    """Watches the volumes of pods that have not been scheduled yet.

    Kubernetes surfaces a claim that will not bind only through events: the pod
    sits unschedulable and nothing in its status says why. The scheduling wait
    loop otherwise learns of it by running out of provision_timeout, which is
    24 hours with a queue admission controller configured, so a deterministic
    failure can cost a day to report. Probing decouples the two: a failure that
    persists fails provisioning whatever the timeout is, and a claim that is
    merely slow says so instead of spinning silently.
    """

    def __init__(self, namespace: str, context: Optional[str],
                 cluster_name: str, pods_created_at: datetime.datetime):
        self._namespace = namespace
        self._context = context
        self._cluster_name = cluster_name
        self._pods_created_at = pods_created_at
        self._next_probe_at = time.time() + _PVC_PROBE_INITIAL_DELAY_SECONDS
        # Failures reported at or before this are not this launch's problem
        # any more, keyed by PVC name. Only set while failures are held: a
        # failure seen during a scale-up may have been caused by the missing
        # node, so the claim gets the grace period afresh once one arrives.
        self._failures_before: Dict[str, datetime.datetime] = {}
        # What the last completed probe found, so that callers polling faster
        # than the probe interval keep reporting it between probes.
        self._message: Optional[str] = None
        # The same, in full, so that the log fires on any change rather than
        # only on the part the spinner shows.
        self._detail: Optional[str] = None

    def probe(self,
              pods: List[Any],
              hold_failures: bool = False) -> Optional[str]:
        """Probes the volumes of ``pods``, unless a probe is not due yet.

        Cheap enough to call on every iteration of a wait loop. Returns a
        message describing the claims that are still being provisioned, for the
        caller to surface, or None once there are none. Between probes it
        repeats what the last one found.

        Args:
            hold_failures: report claims but never fail on them, and discount
                the failures seen while doing so. For when a node is on its way:
                a provisioner that needs a node it does not have yet reports the
                same failure as one that will never succeed (a topology-aware
                CSI driver in a cluster scaled to zero, for instance), and
                waiting really is the right thing to do until the node arrives.

        Raises:
            config_lib.KubernetesError: the storage backend reported something
                that cannot succeed however long it is retried, or a failure it
                does not classify has persisted for
                _PVC_FAILURE_GRACE_SECONDS. This is a provisioning failure, so
                it can fail over to another region -- unlike a volume already
                known to be unusable before the launch, which is refused
                outright.
        """
        now = time.time()
        if now < self._next_probe_at:
            return self._message
        self._next_probe_at = now + _PVC_PROBE_INTERVAL_SECONDS

        pvc_names: List[str] = []
        for pod in pods:
            pvc_names += _pod_pvc_names(pod)
        pvc_names = list(dict.fromkeys(pvc_names))
        if not pvc_names:
            self._message = None
            return None

        pending_pvcs = _get_pending_pvcs(self._namespace,
                                         self._context,
                                         pvc_names,
                                         failures_since=self._pods_created_at)
        pending_by_name = {pvc.name: pvc for pvc in pending_pvcs}
        for pvc_name in pvc_names:
            pending = pending_by_name.get(pvc_name)
            # A claim that is gone, bound or not failing clears its history. An
            # unreadable claim lands here too, so an API server that is erroring
            # intermittently keeps the fast-failure path from ever triggering --
            # which is the right bias, but it does mean it can be starved.
            if pending is None or (not pending.terminal and
                                   pending.failure is None):
                self._failures_before.pop(pvc_name, None)
                continue
            if hold_failures:
                logger.debug(
                    f'Volume {pvc_name} is reporting a failure while '
                    f'launching {self._cluster_name}, but a node is on '
                    f'its way, so it is being discounted: '
                    f'{pending.detail}')
                if pending.failure is not None:
                    self._failures_before[pvc_name] = pending.failure.last
                continue
            if not pending.terminal and pending.failure is not None:
                # Nothing said this cannot succeed, so all there is to go on is
                # how long it has been saying it.
                failing_for = pending.failure.seconds_since(
                    self._failures_before.get(pvc_name, self._pods_created_at))
                if failing_for < _PVC_FAILURE_GRACE_SECONDS:
                    logger.debug(
                        f'Volume {pvc_name} has been reporting an unclassified '
                        f'failure for {failing_for:.0f}s while launching '
                        f'{self._cluster_name}; giving it until '
                        f'{_PVC_FAILURE_GRACE_SECONDS}s: {pending.detail}')
                    continue
            raise config_lib.KubernetesError(
                _format_pvc_binding_error(pvc_details=pending.detail,
                                          pvc_names=[pvc_name],
                                          namespace=self._namespace))

        message = None
        detail = None
        if pending_pvcs:
            # The spinner gets one claim and one reason. A pod can mount
            # several claims -- an auto-mounted volume, an inline one, the
            # cluster's own -- and each provisioner's message runs to a
            # paragraph, which together do not fit on a line.
            #
            # Show one that is complaining if there is one. A claim that is
            # merely waiting says the same thing for minutes, while a warning
            # is the reason someone is watching this line at all. A warning
            # bad enough to be fatal has already raised by here, so what this
            # surfaces is the kind that may yet resolve.
            shown = next((pvc for pvc in pending_pvcs if pvc.warned),
                         pending_pvcs[0])
            summary = shown.summary
            if len(pending_pvcs) > 1:
                summary += f', +{len(pending_pvcs) - 1} more'
            message = f'waiting for volume(s): {summary}'
            detail = ('waiting for volume(s) to be provisioned: ' +
                      ', '.join(pvc.detail for pvc in pending_pvcs))
        if detail != self._detail:
            # Log what the spinner leaves out, on change only: the spinner is
            # gone by the time anyone reads back why a launch took as long as
            # it did. Keyed on the full text, so a change the spinner does not
            # show is still recorded.
            state = detail if detail is not None else 'volume(s) provisioned'
            logger.info(f'Launching {self._cluster_name}: {state}')
        self._detail = detail
        self._message = message
        return message


def _raise_pod_scheduling_errors(namespace, context, new_nodes):
    """Raise pod scheduling failure reason.

    When a pod fails to schedule in Kubernetes, the reasons for the failure
    are recorded as events. This function retrieves those events and raises
    descriptive errors for better debugging and user feedback.
    """
    timeout_err_msg = ('Timed out while waiting for nodes to start. '
                       'Cluster may be out of resources or '
                       'may be too slow to autoscale.')
    for new_node in new_nodes:
        pod = kubernetes.core_api(context).read_namespaced_pod(
            new_node.metadata.name,
            namespace,
            _request_timeout=_POD_POLL_REQUEST_TIMEOUT)
        pod_status = pod.status.phase
        # When there are multiple pods involved while launching instance,
        # there may be a single pod causing issue while others are
        # successfully scheduled. In this case, we make sure to not surface
        # the error message from the pod that is already scheduled.
        if pod_status != 'Pending':
            continue
        pod_name = pod._metadata._name  # pylint: disable=protected-access
        events = kubernetes.core_api(context).list_namespaced_event(
            namespace,
            field_selector=(f'involvedObject.name={pod_name},'
                            'involvedObject.kind=Pod'),
            _request_timeout=_POD_POLL_REQUEST_TIMEOUT)
        # Events created in the past hours are kept by
        # Kubernetes python client and we want to surface
        # the latest event message
        events_desc_by_time = sorted(
            events.items,
            key=lambda e: e.metadata.creation_timestamp,
            reverse=True)

        event_message = None
        for event in events_desc_by_time:
            if event.reason == 'FailedScheduling':
                event_message = event.message
                break
        if event_message is not None:
            if pod_status == 'Pending':
                out_of = {}
                # key: resource name, value: (extra message, nice name)
                if 'Insufficient cpu' in event_message:
                    out_of['CPU'] = (': Run \'kubectl get nodes -o '
                                     'custom-columns=NAME:.metadata.name,'
                                     'CPU:.status.allocatable.cpu\' to check '
                                     'the available CPUs on the node.', 'CPUs')
                if 'Insufficient memory' in event_message:
                    out_of['memory'] = (': Run \'kubectl get nodes -o '
                                        'custom-columns=NAME:.metadata.name,'
                                        'MEMORY:.status.allocatable.memory\' '
                                        'to check the available memory on the '
                                        'node.', 'Memory')

                # TODO(aylei): after switching from smarter-device-manager to
                # fusermount-server, we need a new way to check whether the
                # fusermount-server daemonset is ready.
                gpu_lf_keys = [
                    key for lf in kubernetes_utils.LABEL_FORMATTER_REGISTRY
                    for key in lf.get_label_keys()
                ]
                for label_key in gpu_lf_keys:
                    # TODO(romilb): We may have additional node
                    #  affinity selectors in the future - in that
                    #  case we will need to update this logic.
                    # TODO(Doyoung): Update the error message raised
                    # with the multi-host TPU support.
                    gpu_resource_key = kubernetes_utils.get_gpu_resource_key(
                        context)  # pylint: disable=line-too-long
                    if ((f'Insufficient {gpu_resource_key}' in event_message) or
                        ('didn\'t match Pod\'s node affinity/selector'
                         in event_message) and pod.spec.node_selector):
                        if 'gpu' in gpu_resource_key.lower():
                            info_msg = (
                                ': Run \'sky gpus list --infra kubernetes\' to '
                                'see the available GPUs.')
                        else:
                            info_msg = ': '
                        if (pod.spec.node_selector and
                                label_key in pod.spec.node_selector):
                            extra_msg = (
                                f'Verify if any node matching label '
                                f'{pod.spec.node_selector[label_key]} and '
                                f'sufficient resource {gpu_resource_key} '
                                f'is available in the cluster.')
                            extra_msg = info_msg + ' ' + extra_msg
                        else:
                            extra_msg = info_msg
                        if gpu_resource_key not in out_of or len(
                                out_of[gpu_resource_key][0]) < len(extra_msg):
                            out_of[f'{gpu_resource_key}'] = (extra_msg, 'GPUs')

            if len(out_of) > 0:
                # We are out of some resources. We should raise an error.
                rsrc_err_msg = 'Insufficient resource capacity on the '
                rsrc_err_msg += 'cluster:\n'
                out_of_keys = list(out_of.keys())
                for i in range(len(out_of_keys)):
                    rsrc = out_of_keys[i]
                    (extra_msg, nice_name) = out_of[rsrc]
                    extra_msg = extra_msg if extra_msg else ''
                    if i == len(out_of_keys) - 1:
                        indent = '└──'
                    else:
                        indent = '├──'
                    rsrc_err_msg += (f'{indent} Cluster does not have '
                                     f'sufficient {nice_name} for your request'
                                     f'{extra_msg}')
                    if i != len(out_of_keys) - 1:
                        rsrc_err_msg += '\n'

                # Emit the error message without logging prefixes for better UX.
                tmp_handler = sky_logging.EnvAwareHandler(sys.stdout)
                tmp_handler.flush = sys.stdout.flush  # type: ignore
                tmp_handler.setFormatter(sky_logging.NO_PREFIX_FORMATTER)
                tmp_handler.setLevel(sky_logging.ERROR)
                prev_propagate = logger.propagate
                try:
                    logger.addHandler(tmp_handler)
                    logger.propagate = False
                    logger.error(ux_utils.error_message(f'{rsrc_err_msg}'))
                finally:
                    logger.removeHandler(tmp_handler)
                    logger.propagate = prev_propagate
                nice_names = [out_of[rsrc][1] for rsrc in out_of_keys]
                raise config_lib.KubernetesError(
                    f'{timeout_err_msg} '
                    f'Pod status: {pod_status} '
                    f'Details: \'{event_message}\' ',
                    insufficent_resources=nice_names,
                )

        # Check for PVC binding issues
        pvc_error = _get_pvc_binding_status(namespace, context, pod)
        has_pvc_issue = (event_message is not None and
                         'unbound immediate PersistentVolumeClaims'
                         in event_message)
        if pvc_error is not None or has_pvc_issue:
            pvc_msg = pvc_error if pvc_error else (_format_pvc_binding_error(
                pvc_details=None, pvc_names=[], namespace=namespace))
            err_msg = f'{pvc_msg}\nPod status: {pod_status}'
            if event_message:
                err_msg += f' Details: \'{event_message}\''
            raise config_lib.KubernetesError(err_msg)

        err_msg = f'{timeout_err_msg} Pod status: {pod_status}'
        if event_message:
            err_msg += f' Details: \'{event_message}\''
        raise config_lib.KubernetesError(err_msg)

    raise config_lib.KubernetesError(f'{timeout_err_msg}')


def _raise_command_running_error(message: str, command: str, pod_name: str,
                                 rc: int, stdout: str) -> None:
    if rc == 0:
        return
    raise config_lib.KubernetesError(
        f'Failed to {message} for pod {pod_name} with return '
        f'code {rc}: {command!r}\nOutput: {stdout}.')


def _detect_cluster_event_reason_occurred(namespace, context, search_start,
                                          reason) -> bool:

    def _convert_to_utc(timestamp):
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=datetime.timezone.utc)
        return timestamp.astimezone(datetime.timezone.utc)

    def _get_event_timestamp(event):
        if event.last_timestamp:
            return event.last_timestamp
        elif event.metadata.creation_timestamp:
            return event.metadata.creation_timestamp
        return None

    events = kubernetes.core_api(context).list_namespaced_event(
        namespace=namespace,
        field_selector=f'reason={reason}',
        _request_timeout=_POD_POLL_REQUEST_TIMEOUT)
    for event in events.items:
        ts = _get_event_timestamp(event)
        if ts and _convert_to_utc(ts) > search_start:
            return True
    return False


def _cluster_had_autoscale_event(namespace, context, search_start) -> bool:
    """Detects whether the cluster had a autoscaling event after a
    specified datetime. This only works when using cluster-autoscaler.

    Args:
        namespace: kubernetes namespace
        context: kubernetes context
        search_start (datetime.datetime): filter for events that occurred
            after search_start

    Returns:
        A boolean whether the cluster has an autoscaling event or not.
    """
    assert namespace is not None

    try:
        return _detect_cluster_event_reason_occurred(namespace, context,
                                                     search_start,
                                                     'TriggeredScaleUp')
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Error occurred while detecting cluster autoscaler: {e}')
        return False


def _cluster_maybe_autoscaling(namespace, context, search_start) -> bool:
    """Detects whether a kubernetes cluster may have an autoscaling event.

    This is not a definitive detection. FailedScheduling, which is an
    event that can occur when not enough resources are present in the cluster,
    which is a trigger for cluster autoscaling. However, FailedScheduling may
    have occurred due to other reasons (cluster itself is abnormal).

    Hence, this should only be used for autoscalers that don't emit the
    TriggeredScaleUp event, e.g.: Karpenter.

    Args:
        namespace: kubernetes namespace
        context: kubernetes context
        search_start (datetime.datetime): filter for events that occurred
            after search_start

    Returns:
        A boolean whether the cluster has an autoscaling event or not.
    """
    assert namespace is not None

    try:
        return _detect_cluster_event_reason_occurred(namespace, context,
                                                     search_start,
                                                     'FailedScheduling')
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Error occurred while detecting cluster autoscaler: {e}')
        return False


def _update_spinner_message(*, iteration: int, pods: List[Any],
                            context: Optional[str], namespace: str,
                            cluster_name_on_cloud: str,
                            cluster_name: str) -> None:
    del iteration, pods, context, namespace  #unused
    del cluster_name_on_cloud, cluster_name  #unused
    pass


@timeline.event
def _is_transport_error(e: Exception) -> bool:
    """Whether ``e`` is a failure of the HTTP transport to the API server.

    urllib3 transport errors propagate raw out of the kubernetes client,
    with one exception: the client wraps ``urllib3.exceptions.SSLError``
    into an ``ApiException`` with ``status=0`` (no HTTP response was
    received). An ``ApiException`` with a real HTTP status is an API error
    response, not a transport failure.
    """
    if isinstance(e, kubernetes.api_exception()):
        return e.status == 0
    return isinstance(e, kubernetes.urllib3_http_error())


def _count_transport_error(e: Exception, first_error_time: Optional[float],
                           cluster_name: str) -> float:
    """Account a pod-poll transport error; raise once the streak persists.

    Treats the error as a missed poll: debug-log, sleep out the tick, and
    return the start time of the current failure streak (callers pass the
    returned value back in, and reset it to None on the next success). Once
    a streak lasts _POD_POLL_TRANSPORT_ERROR_GRACE_SECONDS, raise
    KubernetesError instead.
    """
    now = time.time()
    if first_error_time is None:
        first_error_time = now
    if now - first_error_time >= _POD_POLL_TRANSPORT_ERROR_GRACE_SECONDS:
        raise config_lib.KubernetesError(
            'Lost connectivity to the Kubernetes API server while waiting '
            f'for the pods of {cluster_name}: '
            f'{common_utils.format_exception(e)}') from e
    logger.debug(f'Transient error polling the pods of {cluster_name}: '
                 f'{common_utils.format_exception(e)}. Retrying.')
    time.sleep(1)
    return first_error_time


def _wait_for_pods_to_schedule(namespace, context, new_nodes, timeout: int,
                               cluster_name: str,
                               create_pods_start: datetime.datetime):
    """Wait for all pods to be scheduled.

    Wait for all pods including jump pod to be scheduled, and if it
    exceeds the timeout, raise an exception. If pod's container
    is ContainerCreating, then we can assume that resources have been
    allocated and we can exit.

    If timeout is set to a negative value, this method will wait indefinitely.

    Will update the spinner message to indicate autoscaling if autoscaling
    is happening.
    """
    # Create a set of pod names we're waiting for
    if not new_nodes:
        return
    expected_pod_names = {node.metadata.name for node in new_nodes}
    start_time = time.time()

    # Variables for autoscaler detection
    is_ssh_node_pool = context.startswith('ssh-') if context else False
    autoscaler_type = skypilot_config.get_effective_region_config(
        cloud='ssh' if is_ssh_node_pool else 'kubernetes',
        region=context,
        keys=('autoscaler',),
        default_value=None)
    autoscaler_is_set = autoscaler_type is not None
    use_heuristic_detection = (autoscaler_is_set and
                               not kubernetes_enums.KubernetesAutoscalerType(
                                   autoscaler_type).emits_autoscale_event())
    is_autoscaling = False
    # When a definitive TriggeredScaleUp event is observed, this records the
    # detection moment so that we can extend the deadline — node scale-up is
    # unpredictable and the user-configured provision_timeout is usually
    # tuned for normal scheduling latency rather than for waiting on
    # autoscaler nodes. Heuristic FailedScheduling detection (Karpenter) does
    # NOT set this — extending a deadline by 15 min based on FailedScheduling
    # alone would mask real failures (oversized requests, taints, etc.).
    autoscale_detected_time: Optional[float] = None

    # If the user configured an autoscaler but left provision_timeout too
    # short, bump the initial timeout up to the minimum so the Cluster
    # Autoscaler has time to scan and emit its first event. Without this
    # floor the loop would exit before autoscale_detected_time could ever
    # be set. Negative timeout (indefinite wait) is left alone.
    if (autoscaler_is_set and
            0 <= timeout < _AUTOSCALE_INITIAL_MIN_TIMEOUT_SECONDS):
        logger.warning(
            f'Autoscaler is configured but provision_timeout ({timeout}s) '
            f'is too short; bumping initial timeout to '
            f'{_AUTOSCALE_INITIAL_MIN_TIMEOUT_SECONDS}s.')
        timeout = _AUTOSCALE_INITIAL_MIN_TIMEOUT_SECONDS

    # Queue-admission gating (e.g. Kueue): a pod held by a scheduling gate is
    # waiting for an external admission controller to admit it (a quota
    # wait), not failing to provision. While any expected pod is gated, the
    # provisioning clock is paused: provision_timeout starts counting from
    # the moment all expected pods are ungated (admitted). The gated wait
    # itself is bounded by kubernetes.kueue.admission_timeout (default
    # _QUEUE_ADMISSION_TIMEOUT_SECONDS; -1 waits indefinitely).
    admission_timeout = skypilot_config.get_effective_region_config(
        cloud='ssh' if is_ssh_node_pool else 'kubernetes',
        region=context,
        keys=('kueue', 'admission_timeout'),
        default_value=_QUEUE_ADMISSION_TIMEOUT_SECONDS)
    pods_are_gated = False
    last_gated_pod_names: List[str] = []
    # Start of the provisioning clock; slides to the admission moment when
    # pods leave the gated state.
    provision_clock_start = start_time

    def _evaluate_timeout() -> bool:
        # If timeout is negative, retry indefinitely.
        if timeout < 0:
            return True
        # While pods are held by scheduling gates, provision_timeout does
        # not apply; the admission wait is bounded separately.
        if pods_are_gated:
            if admission_timeout < 0:
                return True
            return time.time() < start_time + admission_timeout
        original_deadline = provision_clock_start + timeout
        # If autoscaling has been detected, extend the deadline from the
        # detection moment. Use max(...) so an explicitly long user timeout
        # is never shortened by this extension.
        if autoscale_detected_time is not None:
            extended_deadline = (autoscale_detected_time +
                                 _AUTOSCALE_DETECTED_TIMEOUT_SECONDS)
            deadline = max(original_deadline, extended_deadline)
        else:
            deadline = original_deadline
        return time.time() < deadline

    volume_probe = _PendingVolumeProbe(namespace=namespace,
                                       context=context,
                                       cluster_name=cluster_name,
                                       pods_created_at=create_pods_start)
    last_volume_status_text: Optional[str] = None
    iteration = 0
    transport_error_since: Optional[float] = None
    while _evaluate_timeout():
        # Get all pods in a single API call using the cluster name label
        # which all pods in new_nodes should share
        cluster_name_on_cloud = new_nodes[0].metadata.labels[
            constants.TAG_SKYPILOT_CLUSTER_NAME]
        try:
            pods = kubernetes.core_api(context).list_namespaced_pod(
                namespace,
                label_selector=(f'{constants.TAG_SKYPILOT_CLUSTER_NAME}='
                                f'{cluster_name_on_cloud}'),
                _request_timeout=_POD_POLL_REQUEST_TIMEOUT).items
            transport_error_since = None
        except (kubernetes.api_exception(),
                kubernetes.urllib3_http_error()) as e:
            # Treat a transport failure as a missed poll and retry within
            # the deadline above; see _POD_POLL_REQUEST_TIMEOUT for why the
            # call must be bounded rather than left to block indefinitely.
            if not _is_transport_error(e):
                raise
            transport_error_since = _count_transport_error(
                e, transport_error_since, cluster_name)
            continue

        # Get the set of found pod names and check if we have all expected pods
        found_pod_names = {pod.metadata.name for pod in pods}
        missing_pods = expected_pod_names - found_pod_names
        if missing_pods:
            logger.info('Retrying waiting for pods: '
                        f'Missing pods: {missing_pods}')
            time.sleep(0.5)
            continue

        # A pod with scheduling gates is invisible to the kube-scheduler
        # until an external controller (e.g. Kueue on admission) removes the
        # gates. Pause the provisioning clock while any expected pod is
        # gated — this is a quota/queue wait, not a scheduling delay — and
        # skip scheduling checks and autoscaler detection, which are
        # meaningless for gated pods.
        gated_pod_names = [
            pod.metadata.name
            for pod in pods
            if pod.metadata.name in expected_pod_names and
            pod.spec.scheduling_gates
        ]
        if gated_pod_names:
            if not pods_are_gated:
                pods_are_gated = True
                logger.info(f'Pod(s) {sorted(gated_pod_names)} are held by '
                            'scheduling gates, waiting for queue admission; '
                            'provision_timeout will apply after admission.')
                rich_utils.force_update_status(
                    ux_utils.spinner_message(
                        'Launching (waiting for queue admission)',
                        cluster_name=cluster_name))
                global_user_state.add_cluster_event(
                    cluster_name,
                    new_status=None,
                    reason='Launching (waiting for queue admission)',
                    event_type=global_user_state.ClusterEventType.
                    LAUNCH_PROGRESS,
                    nop_if_duplicate=True,
                )
            last_gated_pod_names = gated_pod_names
            # Keep refreshing the spinner while gated. The message set above
            # is written once, on entering the gated state; the admission
            # wait that follows can last hours, and it is exactly the phase
            # where live feedback (e.g. the workload's position in the
            # queue) is most useful. Skipping the per-poll update would
            # freeze the spinner on that static message for the whole wait.
            _update_spinner_message(iteration=iteration,
                                    pods=pods,
                                    context=context,
                                    namespace=namespace,
                                    cluster_name_on_cloud=cluster_name_on_cloud,
                                    cluster_name=cluster_name)
            iteration += 1
            time.sleep(1)
            continue
        if pods_are_gated:
            # All expected pods were just admitted (gates removed) — start
            # the provisioning clock now.
            pods_are_gated = False
            provision_clock_start = time.time()
            logger.info('All pods admitted (scheduling gates removed); '
                        f'waiting up to {timeout}s for scheduling.')

        # A pod is considered scheduled once the kube-scheduler has bound it
        # to a node (capacity found). We deliberately do not wait for the
        # kubelet to populate container_statuses here -- that can lag and is
        # handled by _wait_for_pods_to_run, which has no provision_timeout.
        unscheduled_pods = [
            pod for pod in pods if pod.metadata.name in expected_pod_names and
            not _pod_is_scheduled(pod)
        ]

        if not unscheduled_pods:
            return

        # Check if cluster is autoscaling and update spinner message.
        # Minor optimization to not query k8s api after autoscaling
        # event was detected. This is useful because there isn't any
        # autoscaling complete event.
        if autoscaler_is_set and not is_autoscaling:
            if use_heuristic_detection:
                is_autoscaling = _cluster_maybe_autoscaling(
                    namespace, context, create_pods_start)
                msg = 'Kubernetes cluster may be scaling up'
            else:
                is_autoscaling = _cluster_had_autoscale_event(
                    namespace, context, create_pods_start)
                msg = 'Kubernetes cluster is autoscaling'
                if is_autoscaling:
                    # Definitive TriggeredScaleUp observed — extend the
                    # deadline from this moment in _evaluate_timeout().
                    autoscale_detected_time = time.time()

            if is_autoscaling:
                rich_utils.force_update_status(
                    ux_utils.spinner_message(f'Launching ({msg})',
                                             cluster_name=cluster_name))
                # The cluster row is written by add_or_update_cluster
                # earlier in the launch flow, so the hash lookup inside
                # add_cluster_event is guaranteed to succeed here.
                # TODO(kev): mirror this emit on AWS / GCP / Slurm autoscaler
                # paths.
                global_user_state.add_cluster_event(
                    cluster_name,
                    new_status=None,
                    reason=f'Launching ({msg})',
                    event_type=global_user_state.ClusterEventType.
                    LAUNCH_PROGRESS,
                    nop_if_duplicate=True,
                )

        # An unbound claim keeps a pod unschedulable, so check whether that is
        # what we are waiting on rather than only finding out at the deadline.
        # Gated pods never reach here (the branch above continues), which is
        # what we want: their claims are not being provisioned yet either.
        #
        # Failures are held while a node is on its way, for the same reason the
        # deadline is extended then (see the hold_failures argument), and for
        # exactly as long: past that window the deadline no longer believes a
        # node is coming either. Bounding it matters because
        # autoscale_detected_time is never cleared, and the event it is set
        # from is looked up namespace-wide, so an unrelated pod's scale-up
        # would otherwise suppress this check for the rest of the wait.
        scale_up_in_flight = (autoscale_detected_time is not None and
                              time.time() < autoscale_detected_time +
                              _AUTOSCALE_DETECTED_TIMEOUT_SECONDS)
        volume_wait_msg = volume_probe.probe(unscheduled_pods,
                                             hold_failures=scale_up_in_flight)

        if volume_wait_msg is not None:
            # Name the volume being waited on. A bare spinner leaves no way to
            # tell a slow volume from a broken one. This takes precedence over
            # the autoscaling message above, which is both less specific and
            # already recorded as its own cluster event -- and which a pod held
            # up by a volume triggers by itself where the autoscaler is detected
            # heuristically, from FailedScheduling.
            #
            # Only on change: this runs every second, and for a whole wait --
            # 24 hours of it, with a queue admission controller configured.
            # nop_if_duplicate would collapse the rows, but it reads the last
            # event to do so, so it is a query per second.
            volume_status_text = f'Launching ({volume_wait_msg})'
            if volume_status_text != last_volume_status_text:
                last_volume_status_text = volume_status_text
                rich_utils.force_update_status(
                    ux_utils.spinner_message(volume_status_text,
                                             cluster_name=cluster_name))
                global_user_state.add_cluster_event(
                    cluster_name,
                    new_status=None,
                    reason=volume_status_text,
                    event_type=global_user_state.ClusterEventType.
                    LAUNCH_PROGRESS,
                    nop_if_duplicate=True,
                )
        else:
            # Nothing is waiting on a volume any more, so a later one that is
            # must be reported again even if it reads the same. Outside the
            # autoscaling check: the message can clear while a scale-up is in
            # progress too.
            last_volume_status_text = None
            if not is_autoscaling:
                _update_spinner_message(
                    iteration=iteration,
                    pods=pods,
                    context=context,
                    namespace=namespace,
                    cluster_name_on_cloud=cluster_name_on_cloud,
                    cluster_name=cluster_name)

        iteration += 1
        time.sleep(1)

    # Pods still gated at the admission deadline: the queue never admitted
    # them. _raise_pod_scheduling_errors inspects scheduler events, which
    # gated pods do not have — raise a queue-specific error instead.
    if pods_are_gated:
        raise config_lib.KubernetesError(
            f'Pod(s) {sorted(last_gated_pod_names)} were still held by '
            f'scheduling gates after waiting {admission_timeout} seconds '
            'for queue admission. Check the queue status and quotas (e.g. '
            '`kubectl describe workload` for Kueue), adjust the requested '
            'resources, or raise kubernetes.kueue.admission_timeout.')

    # Handle pod scheduling errors
    try:
        _raise_pod_scheduling_errors(namespace, context, new_nodes)
    except config_lib.KubernetesError:
        raise
    except Exception as e:
        raise config_lib.KubernetesError(
            'An error occurred while trying to fetch the reason '
            'for pod scheduling failure. '
            f'Error: {common_utils.format_exception(e)}') from None


def _reason_is_exempt_from_stall(reason: Optional[str]) -> bool:
    """Whether a pending reason is allowed to persist indefinitely.

    A reason of None is not exempt: a pod that is neither running nor able to
    say why is exactly the case the no-progress deadline exists to catch.

    The _INIT_CONTAINER_REASON_PREFIX exemption is only as good as the reason
    reaching it: an init container the kubelet is failing to start looks
    exactly like one it is slowly starting, so _inspect_pod_status resolves
    that ambiguity against the pod's events before a reason gets here.
    """
    if reason is None:
        return False
    if reason in _STALL_EXEMPT_PENDING_REASONS:
        return True
    return reason.startswith(_INIT_CONTAINER_REASON_PREFIX)


def _stall_timeout_seconds(reason: Optional[str]) -> int:
    """The no-progress deadline for a pending reason, in seconds."""
    if reason in _MOUNT_FAILURE_EVENT_REASONS:
        # Mount failures get their own window; see the constant.
        return _MOUNT_FAILURE_TIMEOUT_SECONDS
    return _POD_RUN_STALL_TIMEOUT_SECONDS


@timeline.event
def _wait_for_pods_to_run(namespace, context, cluster_name, new_pods):
    """Wait for pods and their containers to be ready.

    Pods may be pulling images or may be in the process of container
    creation.
    """
    if not new_pods:
        return

    # Create a set of pod names we're waiting for
    expected_pod_names = {pod.metadata.name for pod in new_pods}

    def _check_init_containers(pod) -> Optional[_InitContainerProgress]:
        """Check init containers for errors and return the one holding up pod
        initialization.

        Returns the first init container that is running, else the first one
        the kubelet is still creating (pulling its image), else None -- no
        init container accounts for the pod being uninitialized.
        Raises KubernetesError if any init container failed.
        """
        init_statuses = pod.status.init_container_statuses
        total = len(init_statuses)
        running_info: Optional[_InitContainerProgress] = None
        starting_info: Optional[_InitContainerProgress] = None
        for idx, init_status in enumerate(init_statuses):
            init_terminated = init_status.state.terminated
            if init_terminated:
                if init_terminated.exit_code != 0:
                    msg = init_terminated.message if (
                        init_terminated.message) else str(init_terminated)
                    raise config_lib.KubernetesError(
                        'Failed to run init container for pod '
                        f'{pod.metadata.name}. Error details: {msg}.')
                continue
            if (init_status.state.running is not None and running_info is None):
                running_info = _InitContainerProgress(init_status.name, idx + 1,
                                                      total, True)
            init_waiting = init_status.state.waiting
            if init_waiting is not None:
                if init_waiting.reason in ('ContainerCreating',
                                           'PodInitializing'):
                    # The kubelet is creating it -- most often pulling its
                    # image, which is legitimately slow. Recorded so the
                    # no-progress deadline does not mistake it for a stall.
                    if starting_info is None:
                        starting_info = _InitContainerProgress(
                            init_status.name, idx + 1, total, False)
                else:
                    # TODO(romilb): There may be more states to check for. Add
                    #  them as needed.
                    msg = init_waiting.message if (
                        init_waiting.message) else str(init_waiting)
                    unmasked = _unmask_crashloopbackoff_reason(init_status)
                    reason_text = (unmasked if unmasked is not None else
                                   (init_waiting.reason or 'Unknown'))
                    raise config_lib.KubernetesError(
                        f'Failed to create init container for pod '
                        f'{pod.metadata.name}. Error details: '
                        f'{reason_text}: {msg}.')
        return running_info if running_info is not None else starting_info

    def _inspect_pod_status(pod):
        # Check if pod is terminated/preempted/failed (unchanged).
        if (pod.metadata.deletion_timestamp is not None or
                pod.status.phase == 'Failed'):
            # Get the reason and write to cluster events before
            # the pod gets completely deleted from the API.
            termination_reason = _get_pod_termination_reason(pod, cluster_name)
            logger.warning(
                f'Pod {pod.metadata.name} terminated: {termination_reason}')
            condensed = _condensed_pod_reason(pod)
            raise config_lib.KubernetesError(
                f'Pod {pod.metadata.name} failed: {condensed}')

        container_statuses = pod.status.container_statuses
        # Happy path: pod Running and every container Running (unchanged).
        if (pod.status.phase == 'Running' and container_statuses is not None and
                all(container.state.running
                    for container in container_statuses)):
            return True, None

        # Tier 1: container-status sweep. Computed once, consumed in both
        # branches below.
        container_reason = _get_pod_pending_reason_from_container_status(pod)

        if pod.status.phase == 'Pending':
            # Today's raise block -- control flow preserved, message enriched
            # via _unmask_crashloopbackoff_reason when the waiting state is
            # CrashLoopBackOff. msg body (waiting.message) is always preserved.
            init_reason: Optional[str] = None
            # Whether init_reason is an assumption about what the kubelet is
            # doing rather than something it reported -- see below.
            init_reason_is_assumed = False
            if container_statuses is not None:
                for container_status in container_statuses:
                    if not container_status.state:
                        continue
                    waiting = container_status.state.waiting
                    if waiting is not None:
                        if waiting.reason == 'PodInitializing':
                            init_progress = _check_init_containers(pod)
                            if init_progress is not None:
                                verb = ('running' if init_progress.running else
                                        'starting')
                                init_reason = (
                                    f'{_INIT_CONTAINER_REASON_PREFIX}'
                                    f'{init_progress.name!r} {verb} '
                                    f'({init_progress.position}/'
                                    f'{init_progress.total})')
                                init_reason_is_assumed = (
                                    not init_progress.running)
                            else:
                                # PodInitializing, yet no init container is
                                # running or being created -- they have all
                                # terminated successfully and the kubelet has
                                # simply not moved on to the main containers.
                                # Nothing here is legitimately slow, so unlike
                                # the two branches above this reason is not
                                # exempt from the no-progress deadline.
                                init_reason = _POD_INITIALIZATION_REASON
                        elif waiting.reason != 'ContainerCreating':
                            msg = waiting.message if (
                                waiting.message) else str(waiting)
                            unmasked = _unmask_crashloopbackoff_reason(
                                container_status)
                            reason_text = (unmasked if unmasked is not None else
                                           (waiting.reason or 'Unknown'))
                            raise config_lib.KubernetesError(
                                f'{reason_text}: {msg}')
                    terminated = container_status.state.terminated
                    if terminated is not None and terminated.exit_code != 0:
                        reason_str = (terminated.reason if terminated.reason
                                      else f'exit({terminated.exit_code})')
                        raise config_lib.KubernetesError(
                            f'Container in pod {pod.metadata.name} '
                            f'terminated with error while pod is still '
                            f'pending: {reason_str}. Run '
                            f'`sky logs --provision {cluster_name}` '
                            'for more details.')

            # Init container reason wins over all event-based reasons,
            # since events can retain stale "Pulling image" entries long
            # after the pull completed.  Otherwise, Tier 1 (container
            # status) wins; fall back to Tier 2/3 events.
            reason: Optional[str] = init_reason or container_reason
            event_message: Optional[str] = None
            if reason is None:
                pending_reason = _get_pod_pending_reason(
                    context, namespace, pod.metadata.name)
                if pending_reason is not None:
                    reason, event_message = pending_reason
            elif init_reason_is_assumed:
                # 'init container ... starting' says only that the kubelet has
                # not started the container yet; that this is legitimately slow
                # work (an image pull of its own) is an assumption, and it is
                # the assumption that makes the reason stall-exempt. The same
                # state is what a pod shows when the kubelet cannot get as far
                # as starting the container at all -- a sandbox it cannot
                # create, a volume it cannot mount -- which is reported only
                # through events. So let a live Warning, one the pod has not
                # already moved past, replace the assumption: it is both the
                # truer reason and, unlike the init label, bounded by the
                # no-progress deadline. An allow-listed Normal is not consulted
                # -- it is exempt either way, and the init label names which
                # container the pod is waiting on.
                pending_reason = _get_pod_pending_reason(context,
                                                         namespace,
                                                         pod.metadata.name,
                                                         warnings_only=True)
                if pending_reason is not None:
                    reason, event_message = pending_reason
            if reason is None and _pod_is_scheduled(pod):
                # A freshly-bound pod that the kubelet has not picked up yet
                # (and the uninformative 'ContainerCreating' state) has no
                # container-status reason and no event yet. Default to
                # 'container creation' so the launch spinner shows useful
                # detail (e.g. 'Launching (1 pod(s) pending due to container
                # creation)') instead of a bare 'Launching'. Gate on
                # _pod_is_scheduled so an unbound pod still waiting for
                # capacity is not mislabeled as creating a container.
                reason = _CONTAINER_CREATION_REASON
            if reason is not None:
                log_msg = f'Pod {pod.metadata.name} is pending: {reason}'
                if event_message:
                    log_msg += f': {event_message}'
                logger.debug(log_msg)
            return False, reason

        # phase == 'Running' but not all containers running (e.g. one is in
        # CrashLoopBackOff). Surface tier-1's pending reason -- previously this
        # returned (False, None) silently, masking OOMKilled etc.
        return False, container_reason

    # The pending reason each pod is currently being timed against, and when
    # that reason was first seen, keyed by pod name. Reset whenever the reason
    # changes (progress) or the pod starts running, so a deadline is always
    # measured from the most recent onset of a single reason.
    stalled_since: Dict[str, Tuple[Optional[str], float]] = {}

    def _raise_stalled(pod_name: str, reason: Optional[str]) -> None:
        # Re-fetch the newest event for the full kubelet message — the wait
        # loop only tracks the bare reason (e.g. 'FailedMount').
        detail = ''
        pending_reason = _get_pod_pending_reason(context, namespace, pod_name)
        if pending_reason is not None and pending_reason[0] == reason:
            detail = f': {pending_reason[1]}'
        minutes = _stall_timeout_seconds(reason) // 60
        if reason in _MOUNT_FAILURE_EVENT_REASONS:
            raise config_lib.KubernetesError(
                f'Pod {pod_name} has failed to attach or mount volumes for '
                f'over {minutes} minutes: {reason}{detail}')
        if reason is None:
            raise config_lib.KubernetesError(
                f'Pod {pod_name} has not started running after {minutes} '
                'minutes and reports no reason why. Run '
                f'`sky logs --provision {cluster_name}` and '
                f'`kubectl describe pod {pod_name}` for more details.')
        raise config_lib.KubernetesError(
            f'Pod {pod_name} has been stuck on the same condition for over '
            f'{minutes} minutes: {reason}{detail}. Run '
            f'`sky logs --provision {cluster_name}` for more details.')

    missing_pods_retry = 0
    transport_error_since: Optional[float] = None
    last_status_msg: Optional[str] = None
    while True:
        # Get all pods in a single API call
        cluster_name_on_cloud = new_pods[0].metadata.labels[
            constants.TAG_SKYPILOT_CLUSTER_NAME]
        try:
            all_pods = kubernetes.core_api(context).list_namespaced_pod(
                namespace,
                label_selector=(f'{constants.TAG_SKYPILOT_CLUSTER_NAME}='
                                f'{cluster_name_on_cloud}'),
                _request_timeout=_POD_POLL_REQUEST_TIMEOUT).items
            transport_error_since = None
        except (kubernetes.api_exception(),
                kubernetes.urllib3_http_error()) as e:
            # Same missed-poll treatment as in _wait_for_pods_to_schedule.
            # This loop has no deadline, so the transport-error grace window
            # is what keeps a persistently unreachable API server from
            # turning into an endless silent retry loop.
            if not _is_transport_error(e):
                raise
            transport_error_since = _count_transport_error(
                e, transport_error_since, cluster_name)
            continue

        # Get the set of found pod names and check if we have all expected pods
        found_pod_names = {pod.metadata.name for pod in all_pods}
        missing_pod_names = expected_pod_names - found_pod_names
        if missing_pod_names:
            # In _wait_for_pods_to_schedule, we already wait for all pods to go
            # from pending to scheduled. So if a pod is missing here, it means
            # something unusual must have happened, and so should be treated as
            # an exception.
            # It is also only in _wait_for_pods_to_schedule that
            # provision_timeout is used.
            # TODO(kevin): Should we take provision_timeout into account here,
            # instead of hardcoding the number of retries?
            if missing_pods_retry >= _MAX_MISSING_PODS_RETRIES:
                first_pod = True
                missing_pod_reasons: List[str] = []
                for pod_name in sorted(missing_pod_names):
                    reason = _get_pod_missing_reason(context, namespace,
                                                     cluster_name, pod_name,
                                                     first_pod)
                    logger.warning(f'Pod {pod_name} missing: {reason}')
                    if reason is not None:
                        missing_pod_reasons.append(f'{pod_name}: {reason}')
                    first_pod = False
                # Surface whatever the events said. Without this the only
                # signal is the generic sentence below, which reads the same
                # whether the pod was evicted, deleted by another controller,
                # or rejected by the API server before it ever started.
                if missing_pod_reasons:
                    missing_detail = '; '.join(missing_pod_reasons)
                else:
                    # A reason of None does not mean the pod had no events:
                    # _get_pod_missing_reason also returns None when the events
                    # were all seen on an earlier pass, and when they simply do
                    # not name one of the causes it recognises. Say only what
                    # was established -- that no cause could be derived.
                    missing_detail = (
                        f'{sorted(missing_pod_names)} may have been terminated '
                        'or failed unexpectedly, and no cause could be derived '
                        'from their events — the pod may have been deleted, or '
                        'rejected by the API server before it started')
                raise config_lib.KubernetesError(
                    f'Failed to get all pods after {missing_pods_retry} '
                    f'retries: {missing_detail}. Run '
                    f'`sky logs --provision {cluster_name}` for more details.')
            logger.info('Retrying running pods check: '
                        f'Missing pods: {missing_pod_names}')
            time.sleep(0.5)
            missing_pods_retry += 1
            continue

        pods_to_check = [
            pod for pod in all_pods if pod.metadata.name in expected_pod_names
        ]
        num_threads = max(1, min(_NUM_THREADS, len(pods_to_check)))
        pod_statuses = subprocess_utils.run_in_parallel(_inspect_pod_status,
                                                        pods_to_check,
                                                        num_threads)

        all_pods_running = True
        pending_reasons_count: Dict[str, int] = {}
        now = time.time()
        for pod, (is_running, pending_reason) in zip(pods_to_check,
                                                     pod_statuses):
            if not is_running:
                all_pods_running = False
            if pending_reason is not None:
                pending_reasons_count[pending_reason] = (
                    pending_reasons_count.get(pending_reason, 0) + 1)
            # Escalate a pod that keeps reporting the same condition to a
            # provisioning error. Some kubelet-level failures raise from
            # _inspect_pod_status as soon as they are seen, but the ones that
            # only leave the container in 'ContainerCreating' (a mount
            # failure, a pod sandbox that cannot be created) or that are
            # normal in isolation (a container restart) are invisible there —
            # without this deadline such a pod spins here forever with only a
            # spinner update.
            # This reads "the reason did not change" as "the pod made no
            # progress", which holds only because _get_pod_pending_reason will
            # not keep reporting a Warning the pod has already moved past; see
            # its docstring.
            pod_name = pod.metadata.name
            if is_running or _reason_is_exempt_from_stall(pending_reason):
                stalled_since.pop(pod_name, None)
            else:
                previous = stalled_since.get(pod_name)
                if previous is None or previous[0] != pending_reason:
                    stalled_since[pod_name] = (pending_reason, now)
                elif (now - previous[1] >=
                      _stall_timeout_seconds(pending_reason)):
                    _raise_stalled(pod_name, pending_reason)

        if all_pods_running:
            break

        if pending_reasons_count:
            msg = ', '.join([
                f'{count} pod(s) pending due to {reason}'
                for reason, count in sorted(pending_reasons_count.items())
            ])
            status_text = f'Launching ({msg})'
        else:
            status_text = 'Launching'
        new_status_msg = ux_utils.spinner_message(status_text,
                                                  cluster_name=cluster_name)
        if new_status_msg != last_status_msg:
            rich_utils.force_update_status(new_status_msg)
            if pending_reasons_count:
                # Skip the bare 'Launching' status_text — it duplicates
                # the badge label and would produce a useless tooltip.
                # The cluster row is written by add_or_update_cluster
                # earlier in the launch flow, so the hash lookup inside
                # add_cluster_event is guaranteed to succeed here.
                # TODO(kev): mirror this emit on AWS / GCP / Slurm
                # wait-for-instance loops.
                global_user_state.add_cluster_event(
                    cluster_name,
                    new_status=None,
                    reason=status_text,
                    event_type=global_user_state.ClusterEventType.
                    LAUNCH_PROGRESS,
                    nop_if_duplicate=True,
                )
            last_status_msg = new_status_msg
        time.sleep(1)


@timeline.event
def pre_init(namespace: str, context: Optional[str], new_nodes: List) -> None:
    """Pre-initialization step for SkyPilot pods.
    This step is run in the pod right after it is created and before the
    SkyPilot runtime is setup.
    This step includes three key steps:
    1. Privilege check: Checks if the default user has sufficient privilege
    to set up the kubernetes instance pod.
    2. SSH setup: Sets up SSH for the pod instance.
    3. Environment variable setup to populate k8s env vars in the pod.
    Make sure commands used in these methods are generic and work
    on most base images. E.g., do not use Python, since that may not
    be installed by default.
    If you run any apt commands, be sure to check if the lock is available.
    It is possible the `apt update` run in the pod container args may still
    be running.
    Args:
        namespace (str): Kubernetes namespace.
        context (Optional[str]): Kubernetes context.
        new_nodes (List): List of new pod instances.
    Raises:
        config_lib.KubernetesError: If user privileges are insufficient or
          setup fails.
    """

    check_k8s_user_sudo_cmd = (
        'if [ $(id -u) -eq 0 ]; then'
        # If user is root, create an alias for sudo used in skypilot setup
        '  echo \'alias sudo=""\' >> ~/.bashrc; echo succeed;'
        'else '
        '  if command -v sudo >/dev/null 2>&1; then '
        '    timeout 2 sudo -l >/dev/null 2>&1 && echo succeed || '
        f'    ( echo {exceptions.INSUFFICIENT_PRIVILEGES_CODE!r}; '
        f'      exit {exceptions.INSUFFICIENT_PRIVILEGES_CODE}; ); '
        '  else '
        f'    ( echo {exceptions.INSUFFICIENT_PRIVILEGES_CODE!r}; '
        f'      exit {exceptions.INSUFFICIENT_PRIVILEGES_CODE}; ); '
        '  fi; '
        'fi;')

    # Kubernetes automatically populates containers with critical
    # environment variables, such as those for discovering services running
    # in the cluster and CUDA/nvidia environment variables. We need to
    # make sure these env vars are available in every task and ssh session.
    # This is needed for GPU support and service discovery.
    # See https://github.com/skypilot-org/skypilot/issues/2287 for more details.
    # To do so, we capture env vars from the pod's runtime and write them to
    # /etc/profile.d/, making them available for all users in future
    # shell sessions.
    set_k8s_env_var_cmd = docker_utils.SETUP_ENV_VARS_CMD

    check_apt_update_complete_cmd = (
        'echo "Checking if apt update from container init is complete..."; '
        'timeout_secs=600; '
        'start_time=$(date +%s); '
        'while ! grep -q "Fetched" /tmp/apt-update.log 2>/dev/null; do '
        '  echo "apt update still running. Logs:"; '
        '  cat /tmp/apt-update.log || true; '
        '  current_time=$(date +%s); '
        '  elapsed=$((current_time - start_time)); '
        '  if [ $elapsed -ge $timeout_secs ]; then '
        '    echo "Timed out waiting for apt update"; '
        '    exit 1; '
        '  fi; '
        '  sleep 5; '
        'done; '
        'echo "apt update complete."; ')

    install_ssh_k8s_cmd = (
        'prefix_cmd() '
        '{ if [ $(id -u) -ne 0 ]; then echo "sudo"; else echo ""; fi; }; '
        'export DEBIAN_FRONTEND=noninteractive;'
        'echo "Installing missing packages..."; '
        'for i in {1..5}; do '
        '  output=$($(prefix_cmd) apt install openssh-server rsync -y 2>&1); '
        '  rc=$?; '
        '  if [ $rc -eq 0 ]; then '
        '    break; '
        '  fi; '
        '  echo "$output" | grep -qi "could not get lock" || '
        '  grep -qi "Unable to acquire the dpkg frontend lock"; '
        '  if [ $? -eq 0 ]; then '
        '    echo "apt install failed due to lock, retrying. (Attempt $i/5)"; '
        '    sleep 5; '
        '  else '
        '    echo "apt install failed for a non-lock reason: $output"; '
        '    exit $rc; '
        '  fi; '
        'done; '
        'if [ $rc -ne 0 ]; then '
        '    echo "apt install failed after 5 attempts due to lock errors."; '
        '    exit $rc; '
        'fi; '
        '$(prefix_cmd) mkdir -p /var/run/sshd; '
        '$(prefix_cmd) '
        'sed -i "s/PermitRootLogin prohibit-password/PermitRootLogin yes/" '
        '/etc/ssh/sshd_config; '
        '$(prefix_cmd) sed '
        '"s@session\\s*required\\s*pam_loginuid.so@session optional '
        'pam_loginuid.so@g" -i /etc/pam.d/sshd; '
        'cd /etc/ssh/ && $(prefix_cmd) ssh-keygen -A; '
        '$(prefix_cmd) mkdir -p ~/.ssh; '
        '$(prefix_cmd) chown -R $(whoami) ~/.ssh;'
        '$(prefix_cmd) chmod 700 ~/.ssh; '
        '$(prefix_cmd) cat /etc/secret-volume/ssh-publickey* > '
        '~/.ssh/authorized_keys; '
        '$(prefix_cmd) chmod 644 ~/.ssh/authorized_keys; '
        '$(prefix_cmd) service ssh restart; '
        # Eliminate the error
        # `mesg: ttyname failed: inappropriate ioctl for device`.
        # See https://www.educative.io/answers/error-mesg-ttyname-failed-inappropriate-ioctl-for-device  # pylint: disable=line-too-long
        '$(prefix_cmd) sed -i "s/mesg n/tty -s \\&\\& mesg n/" ~/.profile;')

    pre_init_cmd = ('set -ex; ' + check_k8s_user_sudo_cmd +
                    set_k8s_env_var_cmd + check_apt_update_complete_cmd +
                    install_ssh_k8s_cmd)

    def _pre_init_thread(new_node):
        pod_name = new_node.metadata.name
        logger.info(f'{"-"*20}Start: Pre-init in pod {pod_name!r} {"-"*20}')
        runner = command_runner.KubernetesCommandRunner(
            ((namespace, context), pod_name),
            container=k8s_constants.RAY_NODE_CONTAINER_NAME)

        # Run the combined pre-init command
        rc, stdout, _ = runner.run(pre_init_cmd,
                                   require_outputs=True,
                                   stream_logs=False)
        if rc == exceptions.INSUFFICIENT_PRIVILEGES_CODE:
            raise config_lib.KubernetesError(
                'Insufficient system privileges detected. '
                'Ensure the default user has root access or '
                '"sudo" is installed and the user is added to the sudoers '
                'from the image.')

        op_name = 'pre-init'
        _raise_command_running_error(op_name, pre_init_cmd, pod_name, rc,
                                     stdout)

        logger.info(f'{"-"*20}End: Pre-init in pod {pod_name!r} {"-"*20}')

    # Run pre_init in parallel across all new_nodes
    num_threads = max(1, min(_NUM_THREADS, len(new_nodes)))
    subprocess_utils.run_in_parallel(_pre_init_thread, new_nodes, num_threads)


def _label_pod(namespace: str, context: Optional[str], pod_name: str,
               label: Dict[str, str]) -> None:
    """Label a pod."""
    kubernetes.core_api(context).patch_namespaced_pod(
        pod_name,
        namespace, {'metadata': {
            'labels': label
        }},
        _request_timeout=kubernetes.API_TIMEOUT)


def _force_remove_terminating_pod(pod_name: str, namespace: str,
                                  context: Optional[str]) -> None:
    """Force-removes a stuck-terminating pod so a same-named pod can be created.

    A terminating pod can block recreation with 409 ``object is being deleted``
    for two reasons, both of which this handles:
    1. Kueue keeps its ``kueue.x-k8s.io/managed`` finalizer on a pod-group pod
       until it observes a replacement; the finalizer blocks garbage-collection.
       Removing it is safe -- Kueue does not re-add it and admits the recreated
       pod as the replacement.
    2. Even with no finalizer, the object survives its
       ``terminationGracePeriodSeconds`` (for Ray pods this is the
       preemption-hook timeout, which can be minutes).

    A force-delete with grace period 0 removes the object from the API server
    before the call returns, so the caller can recreate the same name at once.
    """
    finalizers: List[str] = []
    try:
        pod = kubernetes.core_api(context).read_namespaced_pod(
            pod_name, namespace)
        # Only reached from the 409 "object is being deleted" branch, so the pod
        # must be terminating; assert to catch misuse from any future caller.
        assert pod.metadata.deletion_timestamp is not None, (
            f'_force_remove_terminating_pod called on non-terminating pod '
            f'{pod_name}')
        finalizers = pod.metadata.finalizers or []
    except kubernetes.api_exception() as e:
        if e.status == 404:
            # Pod already gone (the goal).
            return
        # Best-effort: log and still attempt the force-delete below.
        logger.warning(f'Failed to read terminating pod {pod_name}: {e}')
    if k8s_constants.KUEUE_MANAGED_FINALIZER in finalizers:
        remaining = [
            f for f in finalizers if f != k8s_constants.KUEUE_MANAGED_FINALIZER
        ]
        # Use a JSON patch (list body), not the default strategic-merge patch:
        # a strategic-merge patch with an empty/replacement finalizers list is a
        # no-op for this field, so it would not actually remove the finalizer.
        try:
            kubernetes.core_api(context).patch_namespaced_pod(
                pod_name,
                namespace, [{
                    'op': 'replace',
                    'path': '/metadata/finalizers',
                    'value': remaining
                }],
                _request_timeout=kubernetes.API_TIMEOUT)
            logger.info(
                f'Removed Kueue finalizer from terminating pod {pod_name}.')
        except kubernetes.api_exception() as e:
            if e.status == 404:
                # Pod already gone (the goal); skip the redundant force-delete.
                return
            # Best-effort: log and still attempt the force-delete below.
            logger.warning(f'Failed to strip finalizer from terminating pod '
                           f'{pod_name}: {e}')
    # grace=0 is required: otherwise the finalizer-free object lingers for its
    # (possibly minutes-long) terminationGracePeriodSeconds.
    try:
        kubernetes.core_api(context).delete_namespaced_pod(
            pod_name,
            namespace,
            grace_period_seconds=0,
            _request_timeout=config_lib.DELETION_TIMEOUT)
    except kubernetes.api_exception() as e:
        if e.status != 404:
            logger.warning(
                f'Force delete of terminating pod {pod_name} failed: {e}')


@timeline.event
def _create_namespaced_pod_with_retries(namespace: str, pod_spec: dict,
                                        context: Optional[str]) -> Any:
    """Attempts to create a Kubernetes Pod and handle any errors.

    Currently, we handle errors due to the AppArmor annotation and retry if
    it fails due to the `FieldValueForbidden` error.
    See https://github.com/skypilot-org/skypilot/issues/4174 for details.

    Returns: The created Pod object.
    """
    try:
        # Attempt to create the Pod with the AppArmor annotation
        pod = kubernetes.core_api(context).create_namespaced_pod(
            namespace, pod_spec)
        return pod
    except kubernetes.api_exception() as e:
        try:
            error_body = json.loads(e.body)
            error_message = error_body.get('message', '')
        except json.JSONDecodeError:
            error_message = str(e.body)
        # Check if the error is due to the AppArmor annotation and retry.
        # We add an AppArmor annotation to set it as unconfined in our
        # base template in kubernetes-ray.yml.j2. This is required for
        # FUSE to work in the pod on most Kubernetes distributions.
        # However, some distributions do not support the AppArmor annotation
        # and will fail to create the pod. In this case, we retry without
        # the annotation.
        if (e.status == 422 and 'FieldValueForbidden' in error_message and
                'AppArmorProfile: nil' in error_message):
            logger.warning('AppArmor annotation caused pod creation to fail. '
                           'Retrying without the annotation. '
                           'Note: this may cause bucket mounting to fail.')

            # Remove the AppArmor annotation
            annotations = pod_spec.get('metadata', {}).get('annotations', {})
            apparmor_key = ('container.apparmor.security.beta.kubernetes.io/'
                            f'{k8s_constants.RAY_NODE_CONTAINER_NAME}')
            if apparmor_key in annotations:
                del annotations[apparmor_key]
                pod_spec['metadata']['annotations'] = annotations
                logger.info('AppArmor annotation removed from Pod spec.')
            else:
                logger.warning('AppArmor annotation not found in pod spec, '
                               'retrying will not help. '
                               f'Current annotations: {annotations}')
                raise e

            # Retry Pod creation without the AppArmor annotation
            try:
                pod = kubernetes.core_api(context).create_namespaced_pod(
                    namespace, pod_spec)
                logger.info(f'Pod {pod.metadata.name} created successfully '
                            'without AppArmor annotation.')
                return pod
            except kubernetes.api_exception() as retry_exception:
                logger.info('Failed to create Pod without AppArmor annotation: '
                            f'{retry_exception}')
                raise retry_exception
        # Unlike other error from resource lackage on CPU/GPU/Memory, TPU
        # lackage error is raised when pod is attemtped to be created.
        # TODO(Doyoung): Update the error message raised with the multi-host
        # TPU support.
        elif 'Invalid resource requests for google.com/tpu.' in error_message:
            extra_message = ('Verify if the cluster has a TPU slice node with '
                             'a topology matching the number of TPU(s) '
                             'requested. Note that multi-host TPU podslices '
                             'are currently not unsupported.')
            raise config_lib.KubernetesError(
                _lack_resource_msg('TPU',
                                   pod_spec,
                                   details=error_message,
                                   extra_msg=extra_message))
        elif (e.status == 409 and
              re.match(r'^object is being deleted: pods \".+\" already exists$',
                       error_message)):
            # Pod from a previous cluster with the same name is
            # still being deleted.
            # Extract pod name from the error message.
            # The error message is expected to match:
            # object is being deleted: pods "<podname>" already exists
            match = re.search(r'pods "([^"]+)"', error_message)
            assert match, f'Could not extract pod name from: {error_message}'
            pod_name = match.group(1)
            logger.info(
                f'Pod {pod_name} from previous cluster is still terminating. '
                'Force-removing it and retrying pod creation.')
            # Both the Kueue finalizer and the termination grace period can keep
            # the old object around; _force_remove_terminating_pod clears both.
            _force_remove_terminating_pod(pod_name, namespace, context)
            try:
                pod = kubernetes.core_api(context).create_namespaced_pod(
                    namespace, pod_spec)
                logger.info(f'Pod {pod.metadata.name} created successfully '
                            'after force-removing the terminating pod.')
                return pod
            except kubernetes.api_exception() as retry_exception:
                logger.warning(f'Failed to create pod {pod_name} on retry: '
                               f'{retry_exception}')
                raise retry_exception
        else:
            # Re-raise the exception if it's a different error
            raise e


@timeline.event
def _wait_for_deployment_pod(context,
                             namespace,
                             deployment,
                             timeout=300) -> List:
    label_selector = ','.join([
        f'{key}={value}'
        for key, value in deployment.spec.selector.match_labels.items()
    ])
    target_replicas = deployment.spec.replicas
    deployment_name = deployment.metadata.name
    start_time = time.time()
    while time.time() - start_time < timeout:
        # Refresh the deployment status
        deployment = kubernetes.apps_api(
            context).read_namespaced_deployment_status(deployment_name,
                                                       namespace)
        if (deployment.status and
                deployment.status.ready_replicas is not None and
                deployment.status.ready_replicas >= target_replicas):
            pods = kubernetes.core_api(context).list_namespaced_pod(
                namespace, label_selector=label_selector).items
            return pods

        ready_replicas = (deployment.status.ready_replicas
                          if deployment.status is not None else 0)
        logger.debug(f'Waiting for deployment {deployment_name!r} to be ready. '
                     f'Ready replicas: {ready_replicas}/{target_replicas}')
        time.sleep(2)

    raise TimeoutError(
        f'Timeout: Deployment {deployment_name!r} did not become '
        'ready.')


def _configure_runtime_class(pod_spec: Dict[str,
                                            Any], nvidia_runtime_exists: bool,
                             needs_gpus_nvidia: bool) -> None:
    """Sets or strips runtimeClassName on the pod spec in-place.

    A falsy runtimeClassName (e.g. '' or None from a
    kubernetes.pod_config override) means the user explicitly disabled
    the runtime class. It is stripped from all pods regardless of GPU
    requests: the Kubernetes API rejects pods with an empty-string
    runtimeClassName ('resource name may not be empty'), and it must
    also suppress the automatic 'nvidia' assignment below.
    """
    spec = pod_spec['spec']
    if 'runtimeClassName' in spec and not spec['runtimeClassName']:
        del spec['runtimeClassName']
        return
    if (nvidia_runtime_exists and needs_gpus_nvidia and
            'runtimeClassName' not in spec):
        spec['runtimeClassName'] = 'nvidia'


@timeline.event
def _create_pods(region: str, cluster_name: str, cluster_name_on_cloud: str,
                 config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Create pods based on the config."""
    provider_config = config.provider_config
    namespace = kubernetes_utils.get_namespace_from_config(provider_config)
    context = kubernetes_utils.get_context_from_config(provider_config)
    pod_spec = copy.deepcopy(config.node_config)
    create_pods_start = datetime.datetime.now(datetime.timezone.utc)

    to_create_deployment = 'deployment_spec' in pod_spec
    if to_create_deployment:
        deployment_spec = pod_spec.pop('deployment_spec')
        pvc_spec = pod_spec.pop('pvc_spec')

    tags = ray_tag_filter(cluster_name_on_cloud)

    pod_spec['metadata']['namespace'] = namespace
    if 'labels' in pod_spec['metadata']:
        pod_spec['metadata']['labels'].update(tags)
    else:
        pod_spec['metadata']['labels'] = tags
    pod_spec['metadata']['labels'].update(
        {constants.TAG_SKYPILOT_CLUSTER_NAME: cluster_name_on_cloud})
    # Add the cluster name as an annotation to the pod spec.
    # We cannot use a label because label values have both
    # a length limit and charset limit (i.e no special chars).
    # Annotations are not subject to these limits.
    # This annotation is used to identify the cluster name from the pod
    pod_spec['metadata'].setdefault('annotations', {}).update({
        'skypilot-cluster-name': cluster_name,
    })

    ephemeral_volumes = provider_config.get('ephemeral_volume_infos')
    if ephemeral_volumes:
        for ephemeral_volume in ephemeral_volumes:
            # Update the volumes and volume mounts in the pod spec
            if 'volumes' not in pod_spec['spec']:
                pod_spec['spec']['volumes'] = []
            pod_spec['spec']['volumes'].append({
                'name': ephemeral_volume.name,
                'persistentVolumeClaim': {
                    'claimName': ephemeral_volume.volume_name_on_cloud,
                },
            })
            if 'volumeMounts' not in pod_spec['spec']['containers'][0]:
                pod_spec['spec']['containers'][0]['volumeMounts'] = []
            pod_spec['spec']['containers'][0]['volumeMounts'].append({
                'name': ephemeral_volume.name,
                'mountPath': ephemeral_volume.path,
            })

    # Docker sidecar cache volume injection: if a SkyPilot volume was
    # specified for the enable_docker cache, look up the PVC name. The actual
    # volume + volumeMount are added per-pod inside _create_resource_thread (so
    # that each pod can have its own subPath).
    raw_docker_config = provider_config.get('docker_config')
    docker_config: Optional[kubernetes_utils.DockerConfig] = None
    if raw_docker_config:
        docker_config = kubernetes_utils.DockerConfig.from_dict(
            raw_docker_config)
    docker_pvc_name: Optional[str] = None
    if docker_config and docker_config.cache_volume:
        cache_vol_name = docker_config.cache_volume
        vol_record = global_user_state.get_volume_by_name(cache_vol_name)
        if vol_record is None:
            raise exceptions.VolumeNotFoundError(
                f'Docker cache volume {cache_vol_name!r} not found.')
        docker_pvc_name = vol_record['handle'].name_on_cloud

    terminating_pods = kubernetes_utils.filter_pods(namespace, context, tags,
                                                    ['Terminating'])
    start_time = time.time()
    while (terminating_pods and
           time.time() - start_time < _TIMEOUT_FOR_POD_TERMINATION):
        logger.debug(f'run_instances: Found {len(terminating_pods)} '
                     'terminating pods. Waiting them to finish: '
                     f'{list(terminating_pods.keys())}')
        time.sleep(POLL_INTERVAL)
        terminating_pods = kubernetes_utils.filter_pods(namespace, context,
                                                        tags, ['Terminating'])

    if terminating_pods:
        # If there are still terminating pods, we force delete them.
        logger.debug(f'run_instances: Found {len(terminating_pods)} '
                     'terminating pods still in terminating state after '
                     f'timeout {_TIMEOUT_FOR_POD_TERMINATION}s. '
                     'Force deleting them.')
        for pod_name in terminating_pods.keys():
            # grace_period_seconds=0 means force delete the pod.
            # https://github.com/kubernetes-client/python/issues/508#issuecomment-1695759777
            kubernetes.core_api(context).delete_namespaced_pod(
                pod_name,
                namespace,
                _request_timeout=config_lib.DELETION_TIMEOUT,
                grace_period_seconds=0)

    # Clean up pods in Failed/Succeeded phase from previous runs.
    # These are invisible to the Pending/Running filter below but still
    # block pod creation with the same name (409 AlreadyExists).
    stale_pods = kubernetes_utils.filter_pods(namespace, context, tags,
                                              ['Failed', 'Succeeded'])
    if stale_pods:
        logger.info(f'Found {len(stale_pods)} pods in Failed/Succeeded '
                    f'phase: {list(stale_pods.keys())}. Deleting them.')
        for pod_name in stale_pods:
            # pylint: disable=cell-var-from-loop
            kubernetes_utils.delete_k8s_resource_with_retry(
                delete_func=lambda name=pod_name: kubernetes.core_api(
                    context).delete_namespaced_pod(name,
                                                   namespace,
                                                   _request_timeout=config_lib.
                                                   DELETION_TIMEOUT,
                                                   grace_period_seconds=0),
                resource_type='pod',
                resource_name=pod_name)

    running_pods = kubernetes_utils.filter_pods(namespace, context, tags,
                                                ['Pending', 'Running'])
    head_pod_name = _get_head_pod_name(running_pods)
    running_pod_statuses = [{
        pod.metadata.name: pod.status.phase
    } for pod in running_pods.values()]
    logger.debug(f'Found {len(running_pods)} existing pods: '
                 f'{running_pod_statuses}')

    to_start_count = config.count - len(running_pods)
    if to_start_count < 0:
        raise RuntimeError(
            'The number of running+pending pods '
            f'({config.count - to_start_count}) in cluster '
            f'"{cluster_name_on_cloud}" is greater than the number '
            f'requested by the user ({config.count}). '
            'This is likely a resource leak. '
            'Use "sky down" to terminate the cluster.')

    # Add nvidia runtime class if it exists
    nvidia_runtime_exists = False
    try:
        nvidia_runtime_exists = kubernetes_utils.check_nvidia_runtime_class(
            context=context)
    except kubernetes.kubernetes.client.ApiException as e:
        logger.warning('run_instances: Error occurred while checking for '
                       f'nvidia RuntimeClass - '
                       f'{common_utils.format_exception(e)}'
                       'Continuing without using nvidia RuntimeClass.\n'
                       'If you are on a K3s cluster, manually '
                       'override runtimeClassName in ~/.sky/config.yaml. '
                       'For more details, refer to https://docs.skypilot.co/en/latest/reference/config.html')  # pylint: disable=line-too-long

    needs_gpus = False
    needs_gpus_nvidia = False
    needs_neuron = False
    limits = pod_spec['spec']['containers'][0].get('resources',
                                                   {}).get('limits')
    if limits is not None:
        needs_gpus = limits.get(kubernetes_utils.get_gpu_resource_key(context),
                                0) > 0
        needs_gpus_nvidia = limits.get(
            kubernetes_utils.SUPPORTED_GPU_RESOURCE_KEYS['nvidia'], 0) > 0
        # AWS Neuron (Trainium/Inferentia) uses its own resource key.
        needs_neuron = limits.get(kubernetes_utils.NEURON_RESOURCE_KEY, 0) > 0

    # TPU pods provisioned on GKE use the default containerd runtime.
    # Reference: https://cloud.google.com/kubernetes-engine/docs/how-to/migrate-containerd#overview  # pylint: disable=line-too-long
    _configure_runtime_class(pod_spec, nvidia_runtime_exists, needs_gpus_nvidia)

    logger.debug(f'run_instances: calling create_namespaced_pod '
                 f'(count={to_start_count}).')

    def _create_resource_thread(i: int):
        pod_spec_copy = copy.deepcopy(pod_spec)
        # 0 is for head pod, while 1+ is for worker pods.
        if i == 0:
            if head_pod_name is None:
                # First pod should be head if no head exists
                pod_spec_copy['metadata']['labels'].update(
                    constants.HEAD_NODE_TAGS)
                head_selector = _head_service_selector(cluster_name_on_cloud)
                pod_spec_copy['metadata']['labels'].update(head_selector)
                pod_spec_copy['metadata'][
                    'name'] = f'{cluster_name_on_cloud}-head'
            else:
                # If head pod already exists, we skip creating it.
                return
        else:
            # Worker pods
            pod_spec_copy['metadata']['labels'].update(
                constants.WORKER_NODE_TAGS)
            pod_name = f'{cluster_name_on_cloud}-worker{i}'
            if pod_name in running_pods:
                # If the pod is already running, we skip creating it.
                return
            pod_spec_copy['metadata']['name'] = pod_name
            pod_spec_copy['metadata']['labels']['component'] = pod_name

        # Inject cache volume + volumeMount for the Docker sidecar container.
        if docker_config:
            kubernetes_utils.inject_docker_cache_volume(
                pod_spec=pod_spec_copy,
                docker_config=docker_config,
                pvc_name=docker_pvc_name,
                context=context,
                namespace=namespace,
            )

        # We need to keep the following fields in the pod spec to be same for
        # head and worker pods.
        # So that Kueue can merge them into a single PodSet when creating
        # ProvisioningRequest to trigger scale up of the cluster autoscaler,
        # this is especially required for DWS queued provisioning mode in GKE.
        #  spec.containers[*].resources.requests
        #  spec.initContainers[*].resources.requests
        #  spec.resources
        #  spec.nodeSelector
        #  spec.tolerations
        #  spec.affinity
        #  resourceClaims
        # Refer to the following links for more details:
        # https://cloud.google.com/kubernetes-engine/docs/how-to/provisioningrequest#define_a_provisioningrequest_object # pylint: disable=line-too-long
        # https://kueue.sigs.k8s.io/docs/admission-check-controllers/provisioning/#podset-merge-policy # pylint: disable=line-too-long
        if config.count > 1:
            # For multi-node support, we put a soft-constraint to schedule
            # worker pods on different nodes than the head pod.
            # This is not set as a hard constraint because if different nodes
            # are not available, we still want to be able to schedule worker
            # pods on larger nodes which may be able to fit multiple SkyPilot
            # "nodes".
            pod_spec_config = config_utils.Config(pod_spec_copy['spec'].get(
                'affinity', {}))
            existing_rules = pod_spec_config.get_nested(
                ('podAntiAffinity',
                 'preferredDuringSchedulingIgnoredDuringExecution'), [])
            existing_rules.append({
                # Max weight to avoid scheduling on the
                # same physical node unless necessary.
                'weight': 100,
                'podAffinityTerm': {
                    'labelSelector': {
                        'matchExpressions': [{
                            'key': constants.TAG_SKYPILOT_CLUSTER_NAME,
                            'operator': 'In',
                            'values': [cluster_name_on_cloud]
                        }]
                    },
                    'topologyKey': 'kubernetes.io/hostname'
                }
            })
            pod_spec_config.set_nested(
                ('podAntiAffinity',
                 'preferredDuringSchedulingIgnoredDuringExecution'),
                existing_rules)
            pod_spec_copy['spec']['affinity'] = pod_spec_config

        # TPU slice nodes are given a taint, google.com/tpu=present:NoSchedule.
        # This is to prevent from non-TPU workloads from being scheduled on TPU
        # slice nodes. We need this toleration to allow the pod to be scheduled
        # on TPU nodes.
        # Reference: https://cloud.google.com/kubernetes-engine/docs/concepts/tpus#how_tpus_work # pylint: disable=line-too-long
        tpu_label = kubernetes_utils.GKELabelFormatter.TPU_LABEL_KEY
        if tpu_label in config.node_config.get('spec',
                                               {}).get('nodeSelector', {}):
            tpu_toleration = {
                'key': kubernetes_utils.TPU_RESOURCE_KEY,
                'operator': 'Equal',
                'value': 'present',
                'effect': 'NoSchedule'
            }
            # Preserve existing tolerations if any
            existing_tolerations = pod_spec_copy['spec'].get(
                'tolerations') or []
            pod_spec_copy['spec']['tolerations'] = existing_tolerations + [
                tpu_toleration
            ]
        # Add GPU toleration if GPU is requested.
        # The nodes provisioned by DWS with flex start with queued provisioning
        # mode have the GPU taint, so we have to add the GPU toleration.
        # No need to check if DWS is enabled here since this has no side effect
        # to the non-DWS case.
        if needs_gpus:
            gpu_toleration = {
                'key': kubernetes_utils.get_gpu_resource_key(context),
                'operator': 'Exists',
                'effect': 'NoSchedule'
            }
            # Preserve existing tolerations if any
            existing_tolerations = pod_spec_copy['spec'].get(
                'tolerations') or []
            pod_spec_copy['spec']['tolerations'] = existing_tolerations + [
                gpu_toleration
            ]
        # Add Neuron toleration if AWS Neuron (Trainium/Inferentia) is requested.
        # The Neuron device plugin taints nodes aws.amazon.com/neuron:NoSchedule
        # to keep non-Neuron workloads off them; tolerate it like the GPU case.
        if needs_neuron:
            neuron_toleration = {
                'key': kubernetes_utils.NEURON_RESOURCE_KEY,
                'operator': 'Exists',
                'effect': 'NoSchedule'
            }
            # Preserve existing tolerations if any
            existing_tolerations = pod_spec_copy['spec'].get(
                'tolerations') or []
            pod_spec_copy['spec']['tolerations'] = existing_tolerations + [
                neuron_toleration
            ]

        # Apply allowed_nodes scheduling constraints to restrict pods to
        # nodes permitted by the user's config. This is required in addition
        # to discovery filtering because the K8s scheduler doesn't know
        # about our filter - it would schedule on any node matching the GPU
        # label, including non-allowed nodes with the same GPU type.
        allowed_nodes_config = kubernetes_utils.get_allowed_nodes_config(
            context)
        kubernetes_utils.inject_allowed_nodes_affinity(pod_spec_copy['spec'],
                                                       allowed_nodes_config,
                                                       context=context)

        if to_create_deployment:
            volume.create_persistent_volume_claim(namespace, context, pvc_spec)

            # It's safe to directly modify the template spec in the deployment spec
            # because controller pod is singleton, i in [0].
            template_pod_spec = deployment_spec['spec']['template']
            # Add the deployment name as a label to the pod spec
            deployment_name = deployment_spec['metadata']['name']
            pod_spec_copy['metadata']['labels'][
                k8s_constants.TAG_SKYPILOT_DEPLOYMENT_NAME] = deployment_name
            template_pod_spec['metadata'] = pod_spec_copy['metadata']
            template_pod_spec['spec'].update(pod_spec_copy['spec'])
            # Propagate the labels to the deployment for identification.
            deployment_spec['metadata']['labels'] = pod_spec_copy['metadata'][
                'labels']
            try:
                return kubernetes.apps_api(
                    context).create_namespaced_deployment(
                        namespace, deployment_spec)
            except Exception as e:
                print('Deployment failed', e)
                raise e

        # Check if any PVCs with access mode ReadWriteOnce or ReadWriteOncePod
        # is used by any pod in the namespace.
        volume.check_pvc_usage_for_pod(context, namespace, pod_spec_copy)

        return _create_namespaced_pod_with_retries(namespace, pod_spec_copy,
                                                   context)

    if not to_start_count:
        is_provisioned_cluster_ha = is_high_availability_cluster_by_kubectl(
            cluster_name_on_cloud, context, namespace)
        if is_provisioned_cluster_ha != to_create_deployment:
            ha_str = lambda x: 'high availability' if x else 'non-high availability'

            message = (
                f'The cluster "{cluster_name_on_cloud}" is configured to be '
                f'{ha_str(to_create_deployment)} but the cluster has already been '
                f'provisioned as {ha_str(is_provisioned_cluster_ha)}. '
                'If you want to make the provisioned cluster '
                f'{ha_str(to_create_deployment)}, please first down the cluster '
                'and then up the cluster again.')
            raise exceptions.InconsistentHighAvailabilityError(message)

    created_resources = []
    if to_start_count > 0:
        # Create pods in parallel.
        # Use `config.count` instead of `to_start_count` to keep the index of
        # the Pods consistent especially for the case where some Pods are down
        # due to node failure or manual termination, etc. and then launch
        # again to create the Pods back.
        # The existing Pods will be skipped in _create_resource_thread.
        num_threads = max(1, min(_NUM_THREADS, config.count))
        created_resources = subprocess_utils.run_in_parallel(
            _create_resource_thread, list(range(config.count)), num_threads)

    if to_create_deployment:
        deployments = copy.deepcopy(created_resources)
        pods = [
            pod for deployment in deployments
            for pod in _wait_for_deployment_pod(context, namespace, deployment)
        ]
    else:
        # If not creating deployments, 'created_resources' already holds Pod objects
        pods = created_resources

    created_pods = {}
    valid_pods = []
    for pod in pods:
        # In case Pod is not created
        if pod is None:
            continue
        valid_pods.append(pod)
        created_pods[pod.metadata.name] = pod
        if head_pod_name is None and _is_head(pod):
            head_pod_name = pod.metadata.name
    pods = valid_pods

    # The running_pods may include Pending Pods, so we add them to the pods
    # list to wait for scheduling and running
    if running_pods:
        pods = pods + list(running_pods.values())

    provision_timeout = provider_config['timeout']

    wait_str = ('indefinitely'
                if provision_timeout < 0 else f'for {provision_timeout}s')
    logger.debug(f'run_instances: waiting {wait_str} for pods to schedule and '
                 f'run: {[pod.metadata.name for pod in pods]}')

    # Wait until the pods are scheduled and surface cause for error
    # if there is one
    _wait_for_pods_to_schedule(namespace, context, pods, provision_timeout,
                               cluster_name, create_pods_start)
    # Reset spinner message here because it might have hinted autoscaling
    # while waiting for pods to schedule.
    rich_utils.force_update_status(
        ux_utils.spinner_message('Launching', cluster_name=cluster_name))
    # Wait until the pods and their containers are up and running, and
    # fail early if there is an error
    logger.debug(f'run_instances: waiting for pods to be running: '
                 f'{[pod.metadata.name for pod in pods]}')
    _wait_for_pods_to_run(namespace, context, cluster_name, pods)
    # Reset spinner message here because it might have hinted the reason
    # pods were pending.
    rich_utils.force_update_status(
        ux_utils.spinner_message('Launching', cluster_name=cluster_name))
    logger.debug(f'run_instances: all pods are scheduled and running: '
                 f'{[pod.metadata.name for pod in pods]}')

    assert head_pod_name is not None, 'head_instance_id should not be None'
    return common.ProvisionRecord(
        provider_name='kubernetes',
        region=region,
        zone=None,
        cluster_name=cluster_name_on_cloud,
        head_instance_id=head_pod_name,
        resumed_instance_ids=[],
        created_instance_ids=list(created_pods.keys()),
    )


def run_instances(region: str, cluster_name: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Runs instances for the given cluster."""
    try:
        return _create_pods(region, cluster_name, cluster_name_on_cloud, config)
    except (kubernetes.api_exception(), config_lib.KubernetesError) as e:
        e_msg = common_utils.format_exception(e)
        logger.warning('run_instances: Error occurred when creating pods:\n'
                       f'{e_msg}')
        raise


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    del region, cluster_name_on_cloud, state


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    raise NotImplementedError()


def _delete_services(name_prefix: str,
                     namespace: str,
                     context: Optional[str],
                     skip_ssh_service: bool = False) -> None:
    """Delete services with the given name prefix.

    Args:
        name_prefix: Prefix of the service names to delete
        namespace: Kubernetes namespace
        context: Kubernetes context
    """
    # TODO(andy): We should use tag for the service filter.
    services = ([name_prefix, f'{name_prefix}-ssh']
                if not skip_ssh_service else [name_prefix])
    for service_name in services:
        # Since we are not saving this lambda, it's a false positive.
        # TODO(andyl): Wait for
        # https://github.com/pylint-dev/pylint/issues/5263.
        # pylint: disable=cell-var-from-loop
        kubernetes_utils.delete_k8s_resource_with_retry(
            delete_func=lambda: kubernetes.core_api(
                context).delete_namespaced_service(name=service_name,
                                                   namespace=namespace,
                                                   _request_timeout=config_lib.
                                                   DELETION_TIMEOUT),
            resource_type='service',
            resource_name=service_name)


def _delete_cluster_services(cluster_name: str, namespace: str,
                             context: Optional[str]) -> None:
    """Delete all services associated with a cluster using label selector.

    This is a fallback cleanup mechanism that works even when pods have been
    deleted externally. Services are identified by the skypilot-cluster-name
    label.

    Args:
        cluster_name: The cluster name used in the skypilot-cluster-name label
        namespace: Kubernetes namespace
        context: Kubernetes context
    """
    label_selector = f'{constants.TAG_SKYPILOT_CLUSTER_NAME}={cluster_name}'
    try:
        kubernetes.core_api(context).delete_collection_namespaced_service(
            namespace,
            label_selector=label_selector,
            _request_timeout=config_lib.DELETION_TIMEOUT)
    except kubernetes.api_exception() as e:
        logger.warning(f'Failed to cleanup services for cluster '
                       f'{cluster_name}: {e}')


def _terminate_node(namespace: str,
                    context: Optional[str],
                    pod_name: str,
                    is_head: bool = False) -> None:
    """Terminate a pod and its associated services."""
    logger.debug(f'terminate_instances: namespace: {namespace}, context: '
                 f'{context}, pod_name: {pod_name}, is_head: {is_head}')

    if is_head:
        # Delete services for the head pod
        # services are specified in sky/templates/kubernetes-ray.yml.j2
        _delete_services(pod_name, namespace, context)
    else:
        # No ssh service is created for worker pods
        _delete_services(pod_name, namespace, context, skip_ssh_service=True)

    # Note - delete pod after all other resources are deleted.
    # This is to ensure there are no leftover resources if this down is run
    # from within the pod, e.g., for autodown.
    # Note - some misbehaving pods may not terminate gracefully if they have
    # open file descriptors. We force delete pods to avoid this.
    kubernetes_utils.delete_k8s_resource_with_retry(
        delete_func=lambda: kubernetes.core_api(context).delete_namespaced_pod(
            name=pod_name,
            namespace=namespace,
            _request_timeout=config_lib.DELETION_TIMEOUT,
            grace_period_seconds=0),
        resource_type='pod',
        resource_name=pod_name)


def _terminate_deployment(cluster_name: str, namespace: str,
                          context: Optional[str]) -> None:
    """Terminate a deployment."""
    # Delete services first
    _delete_services(f'{cluster_name}-head', namespace, context)

    # Delete deployment
    deployment_name = _get_deployment_name(cluster_name)
    kubernetes_utils.delete_k8s_resource_with_retry(
        delete_func=lambda: kubernetes.apps_api(
            context).delete_namespaced_deployment(name=deployment_name,
                                                  namespace=namespace,
                                                  _request_timeout=config_lib.
                                                  DELETION_TIMEOUT),
        resource_type='deployment',
        resource_name=deployment_name)

    # Delete PVCs
    pvc_name = _get_pvc_name(
        cluster_name,
        kubernetes_utils.HIGH_AVAILABILITY_DEPLOYMENT_VOLUME_MOUNT_NAME)
    # pylint: disable=cell-var-from-loop
    kubernetes_utils.delete_k8s_resource_with_retry(
        delete_func=lambda: kubernetes.core_api(
            context).delete_namespaced_persistent_volume_claim(
                name=pvc_name,
                namespace=namespace,
                _request_timeout=config_lib.DELETION_TIMEOUT),
        resource_type='pvc',
        resource_name=pvc_name)


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Dict[str, Any],
    worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    namespace = kubernetes_utils.get_namespace_from_config(provider_config)
    context = kubernetes_utils.get_context_from_config(provider_config)
    pods = kubernetes_utils.filter_pods(namespace, context,
                                        ray_tag_filter(cluster_name_on_cloud),
                                        None)

    if is_high_availability_cluster_by_kubectl(cluster_name_on_cloud, context,
                                               namespace):
        # For high availability controllers, terminate the deployment
        logger.debug(f'Terminating deployment {cluster_name_on_cloud}')
        _terminate_deployment(cluster_name_on_cloud, namespace, context)
        return

    def _terminate_pod_thread(pod_info):
        pod_name, pod = pod_info
        if _is_head(pod) and worker_only:
            return
        logger.debug(f'Terminating instance {pod_name}: {pod}')
        _terminate_node(namespace, context, pod_name, _is_head(pod))

    # Run pod termination in parallel
    num_threads = max(1, min(_NUM_THREADS, len(pods)))
    subprocess_utils.run_in_parallel(_terminate_pod_thread, list(pods.items()),
                                     num_threads)

    if not worker_only:
        # Cleanup all services by label selector as a fallback.
        # This handles the case where pods were deleted externally.
        # Only do this when terminating the entire cluster, not when
        # terminating workers only (head services should remain).
        _delete_cluster_services(cluster_name_on_cloud, namespace, context)


def cleanup_cluster_resources(
    cluster_name_on_cloud: str,
    provider_config: Dict[str, Any],
) -> None:
    """Cleanup Kubernetes resources for a cluster.

    This function is called during post-teardown cleanup to ensure all cluster
    resources are deleted even when pods were deleted externally. It uses label
    selectors to find and delete resources, making it resilient to external
    deletions.

    Args:
        cluster_name_on_cloud: The cluster name on cloud
        provider_config: Provider configuration dictionary
    """
    namespace = kubernetes_utils.get_namespace_from_config(provider_config)
    context = kubernetes_utils.get_context_from_config(provider_config)
    _delete_cluster_services(cluster_name_on_cloud, namespace, context)


# The probe runs as soon as ray-installation finishes in step 2 of the
# pod bootstrap, typically within tens of seconds of the pod going
# Running. 60s gives the common case plenty of slack without pinning
# every status refresh during a pod restart to a multi-minute hang.
_HOST_NETWORK_SSHD_WAIT_TIMEOUT_S = 60
_HOST_NETWORK_SSHD_WAIT_INTERVAL_S = 2


def _read_host_network_sshd_ports(cluster_name_on_cloud: str, namespace: str,
                                  context: Optional[str],
                                  expected_pods: List[str]) -> Dict[str, int]:
    """Read each pod's probed sshd port from the hostNetwork ConfigMap.

    Polls until every entry in ``expected_pods`` is present (or the
    timeout elapses); returning partial state would freeze every
    subsequent SSH at port 22 until the next refresh.
    """
    if not expected_pods:
        return {}
    name = host_network_probe.ray_ports_configmap_name(cluster_name_on_cloud)
    expected = set(expected_pods)
    deadline = time.monotonic() + _HOST_NETWORK_SSHD_WAIT_TIMEOUT_S
    while True:
        out: Dict[str, int] = {}
        try:
            cm = kubernetes.core_api(context).read_namespaced_config_map(
                name=name, namespace=namespace)
        except kubernetes.api_exception() as e:
            if e.status != 404:
                raise
            cm = None
        data = (cm.data or {}) if cm is not None else {}
        for key, value in data.items():
            if not key.startswith(host_network_probe.SSHD_KEY_PREFIX):
                continue
            podname = common_utils.removeprefix(
                key, host_network_probe.SSHD_KEY_PREFIX)
            try:
                out[podname] = int(value)
            except ValueError:
                logger.warning(
                    f'ConfigMap {namespace}/{name} has non-integer value '
                    f'for {key!r}: {value!r}. SSH to {podname!r} will '
                    f'fall back to port 22 and hit the K8s node\'s sshd.')
        if expected.issubset(out.keys()):
            return out
        if time.monotonic() >= deadline:
            missing = sorted(expected - out.keys())
            logger.warning(
                f'hostNetwork sshd ports for {missing} did not appear '
                f'in ConfigMap {namespace}/{name} within '
                f'{_HOST_NETWORK_SSHD_WAIT_TIMEOUT_S}s — `ssh <cluster>` '
                f'to those pods will fail until the next '
                f'`sky status -r`.')
            return out
        time.sleep(_HOST_NETWORK_SSHD_WAIT_INTERVAL_S)


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    del region  # unused
    assert provider_config is not None
    namespace = kubernetes_utils.get_namespace_from_config(provider_config)
    context = kubernetes_utils.get_context_from_config(provider_config)

    running_pods = kubernetes_utils.filter_pods(
        namespace, context, ray_tag_filter(cluster_name_on_cloud), ['Running'])
    logger.debug(f'Running pods: {list(running_pods.keys())}')

    pods: Dict[str, List[common.InstanceInfo]] = {}
    head_pod_name = None

    port = 22
    if not provider_config.get('use_internal_ips', False):
        port = kubernetes_utils.get_head_ssh_port(cluster_name_on_cloud,
                                                  namespace, context)

    # Each hostNetwork pod's sshd binds a probed port (host:22 is the
    # K8s node's own sshd). The SSH config writer needs that port per
    # pod, so wait for every hostNetwork pod's entry to land in the
    # ConfigMap before caching the result.
    host_network_pods = [
        name for name, pod in running_pods.items() if pod.spec.host_network
    ]
    pod_sshd_ports = _read_host_network_sshd_ports(cluster_name_on_cloud,
                                                   namespace, context,
                                                   host_network_pods)

    head_pod_name = None
    cpu_request = None
    memory_request = None
    for pod_name, pod in running_pods.items():
        # Under hostNetwork the pod's network namespace is the host's, so
        # pod_ip is the K8s node's host IP. SkyPilot injects a required
        # per-cluster podAntiAffinity for hostNetwork clusters, so every
        # pod of a cluster is on its own node and thus has a distinct,
        # routable host IP — no per-pod loopback disambiguation needed.
        internal_ip = pod.status.pod_ip
        # Get the k8s node name the pod is running on (for dashboard display)
        k8s_node_name = getattr(pod.spec, 'node_name', None)
        pods[pod_name] = [
            common.InstanceInfo(
                instance_id=pod_name,
                internal_ip=internal_ip,
                external_ip=None,
                ssh_port=pod_sshd_ports.get(pod_name, port),
                tags=pod.metadata.labels,
                # TODO(hailong): `cluster.local` may need to be configurable
                # Service name is same as the pod name for now.
                internal_svc=f'{pod_name}.{namespace}.svc.cluster.local',
                node_name=k8s_node_name,
            )
        ]
        if _is_head(pod):
            head_pod_name = pod_name
            head_spec = pod.spec
            assert head_spec is not None, pod
            primary_container = kubernetes_utils.get_pod_primary_container(pod)
            resources = getattr(primary_container, 'resources', None)
            requests = (getattr(resources, 'requests', None)
                        if resources else None)
            limits = (getattr(resources, 'limits', None) if resources else None)
            cpu_request = ((requests or {}).get('cpu') or
                           (limits or {}).get('cpu'))
            memory_request = ((requests or {}).get('memory') or
                              (limits or {}).get('memory'))

    if cpu_request is None:
        raise RuntimeError(f'Pod {cluster_name_on_cloud}-head not found'
                           ' or not Running, check the Pod status')

    ssh_user = 'sky'
    # Use pattern matching to extract SSH user, handling MOTD contamination.
    # Some container images (like CUDA-Q) print MOTD when login shells start,
    # which can contaminate command output. We use a unique pattern to extract
    # the actual username reliably.
    get_k8s_ssh_user_cmd = 'echo "SKYPILOT_SSH_USER: $(whoami)"'
    assert head_pod_name is not None
    runner = command_runner.KubernetesCommandRunner(
        ((namespace, context), head_pod_name),
        container=k8s_constants.RAY_NODE_CONTAINER_NAME)
    rc, stdout, stderr = runner.run(get_k8s_ssh_user_cmd,
                                    require_outputs=True,
                                    separate_stderr=True,
                                    stream_logs=False)
    _raise_command_running_error('get ssh user', get_k8s_ssh_user_cmd,
                                 head_pod_name, rc, stdout + stderr)

    # Extract SSH user using pattern matching
    ssh_user_match = _SSH_USER_PATTERN.search(stdout)
    if ssh_user_match:
        ssh_user = ssh_user_match.group(1)
    else:
        raise ValueError('Failed to find SSH user identifier: '
                         f'{stdout + stderr}')
    logger.debug(
        f'Using ssh user {ssh_user} for cluster {cluster_name_on_cloud}')

    # cpu_request may be a string like `100m`, need to parse and convert
    num_cpus = kubernetes_utils.parse_cpu_or_gpu_resource_to_float(cpu_request)
    # 'num-cpus' for ray must be an integer, but we should not set it to 0 if
    # cpus is <1.
    # Keep consistent with the logic in clouds/kubernetes.py
    str_cpus = str(max(int(num_cpus), 1))

    # Record the pod's actual resource requests so display paths can show
    # what the scheduler sees, even when an admin policy has overridden
    # them via pod_config (bypassing the SkyPilot resources spec).
    actual_memory_gb = None
    if memory_request is not None:
        actual_memory_gb = kubernetes_utils.parse_memory_resource(
            memory_request, unit='G')

    return common.ClusterInfo(
        instances=pods,
        head_instance_id=head_pod_name,
        ssh_user=ssh_user,
        # We manually set object-store-memory=500000000 to avoid ray from
        # allocating a very large object store in each pod that may cause
        # problems for other pods.
        custom_ray_options={
            'object-store-memory': 500000000,
            'num-cpus': str_cpus,
        },
        provider_name='kubernetes',
        provider_config=provider_config,
        actual_cpus=num_cpus,
        actual_memory_gb=actual_memory_gb)


class NodeHealthInfo:
    """Health info for a single Kubernetes node."""

    def __init__(self, issue: str, pods: List[str]):
        self.issue = issue
        self.pods = pods


def _get_pod_health_issues(pod: Any) -> Optional[str]:
    """Check a Running pod for health issues.

    Examines pod conditions and container statuses to detect problems
    that would explain why the pod is Running but not functioning
    (e.g., Ready=False, CrashLoopBackOff).

    Returns None if the pod appears healthy, or a descriptive reason string.
    """
    pod_status = getattr(pod, 'status', None)
    conditions = getattr(pod_status, 'conditions', None)
    if not conditions:
        return None

    ready_condition = None
    for condition in conditions:
        if condition.type == 'Ready':
            ready_condition = condition
            break

    if ready_condition is None or ready_condition.status == 'True':
        return None

    # Pod is not ready — build a reason string
    ready_reason = ready_condition.reason or 'Unknown'
    parts = [f'pod not ready ({ready_reason})']

    # Check container statuses for more specific info
    container_statuses = getattr(pod_status, 'container_statuses', None) or []
    container_issues = []
    for cs in container_statuses:
        if cs.ready:
            continue
        waiting = getattr(cs.state, 'waiting', None)
        terminated = getattr(cs.state, 'terminated', None)
        # A container that was OOMKilled (or otherwise died) and is now
        # restarting records the failure in last_state, not the current state
        # (which may be a generic 'waiting' or already running-again). Surface
        # it so an OOM that briefly blips the cluster into recovery is not
        # masked as a generic 'ray cluster is unhealthy' message.
        last_terminated = cs.last_state.terminated if cs.last_state else None
        prior = None
        if (last_terminated is not None and last_terminated.exit_code != 0 and
                last_terminated.reason):
            prior = (f'{last_terminated.reason} '
                     f'(exit code {last_terminated.exit_code})')
        if waiting and waiting.reason:
            issue = waiting.reason
            if prior is not None:
                issue += f'; previously {prior}'
            container_issues.append(issue)
        elif terminated and terminated.exit_code != 0:
            container_issues.append(f'{terminated.reason or "terminated"}'
                                    f' (exit code {terminated.exit_code})')
        elif prior is not None:
            container_issues.append(prior)

    if container_issues:
        parts.append('; '.join(container_issues))

    return '; '.join(parts)


def _check_nodes_health(
    context: Optional[str],
    node_names: Set[str],
) -> Dict[str, str]:
    """Check health of specific Kubernetes nodes.

    Tries the NodeInfoSource plugin first (fast, cached), then falls back
    to direct Kubernetes API calls.

    Args:
        context: Kubernetes context name.
        node_names: Set of node names to check.

    Returns:
        Dict mapping node_name -> issue description for unhealthy nodes.
        Healthy nodes are omitted.
    """
    if not node_names:
        return {}

    issues: Dict[str, str] = {}

    # Try NodeInfoSource plugin first (node-info-service sidecar).
    # get() safely returns None when no provider is registered.
    # Note: if a node is in node_names but not in the cache, it's silently
    # skipped (we don't fall back to the k8s API for missing entries). This
    # is acceptable since this is diagnostic-only and doesn't affect the
    # cluster status transition.
    node_info = plugin_extensions.NodeInfoSource.get(
        context) if context is not None else None
    if node_info is not None:
        for name in node_names:
            info = node_info.node_info_dict.get(name)
            if info is None:
                continue
            if not info.is_ready:
                issues[name] = 'NotReady'
            elif info.is_cordoned:
                issues[name] = 'cordoned'
        return issues

    # Fallback: direct Kubernetes API (parallelized)
    def _check_single_node(name: str) -> Optional[Tuple[str, str]]:
        try:
            node = kubernetes.core_api(context).read_node(
                name, _request_timeout=kubernetes.API_TIMEOUT)
            # Check NotReady first (more severe than cordoned)
            node_status = getattr(node, 'status', None)
            for condition in (getattr(node_status, 'conditions', None) or []):
                if condition.type == 'Ready' and condition.status != 'True':
                    return (name, 'NotReady')
            # Check if node is cordoned (unschedulable)
            node_spec = getattr(node, 'spec', None)
            if getattr(node_spec, 'unschedulable', False):
                return (name, 'cordoned')
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(f'Failed to read node {name}: {e}')
        return None

    results = subprocess_utils.run_in_parallel(_check_single_node,
                                               sorted(node_names))
    for result in results:
        if result is not None:
            issues[result[0]] = result[1]

    return issues


def get_node_health_for_cluster(
    cluster_name_on_cloud: str,
    provider_config: Dict[str, Any],
    unhealthy_pod_names: List[str],
) -> Dict[str, NodeHealthInfo]:
    """Check node health for specific unhealthy pods in a cluster.

    Fetches pods to determine which nodes they run on, then checks
    those nodes' health via NodeInfoSource or the Kubernetes API.

    Args:
        cluster_name_on_cloud: The cluster name as known to the cloud.
        provider_config: The provider config from the cluster YAML.
        unhealthy_pod_names: Pod names that have health issues.

    Returns:
        Dict mapping node_name -> NodeHealthInfo for unhealthy nodes.
    """
    namespace = kubernetes_utils.get_namespace_from_config(provider_config)
    context = kubernetes_utils.get_context_from_config(provider_config)
    is_ssh = context.startswith('ssh-') if context else False
    identity = 'SSH Node Pool' if is_ssh else 'Kubernetes cluster'
    label_selector = (f'{constants.TAG_SKYPILOT_CLUSTER_NAME}='
                      f'{cluster_name_on_cloud}')

    pods = list_namespaced_pod(context, namespace, cluster_name_on_cloud,
                               is_ssh, identity, label_selector)

    # Build pod -> node mapping for unhealthy pods
    unhealthy_set = set(unhealthy_pod_names)
    pod_node_map: Dict[str, Optional[str]] = {}
    for pod in pods:
        name = pod.metadata.name
        if name in unhealthy_set:
            pod_node_map[name] = getattr(pod.spec, 'node_name', None)

    unique_nodes = {n for n in pod_node_map.values() if n}
    if not unique_nodes:
        return {}

    node_issues = _check_nodes_health(context, unique_nodes)
    if not node_issues:
        return {}

    # Build structured result: node -> NodeHealthInfo
    result: Dict[str, NodeHealthInfo] = {}
    for pod_name, node_name in pod_node_map.items():
        if node_name and node_name in node_issues:
            if node_name not in result:
                result[node_name] = NodeHealthInfo(issue=node_issues[node_name],
                                                   pods=[])
            result[node_name].pods.append(pod_name)

    return result


def get_missing_node_reason(node_names: List[str],
                            provider_config: Dict[str, Any]) -> Optional[str]:
    """Best-effort reason a cluster lost node(s), from current node state.

    Answers "where did the node go" by asking Kubernetes about the nodes the
    cluster's pods were placed on, rather than by replaying the pod's event
    history. A node that no longer exists, or that exists but is NotReady or
    cordoned, explains a pod that vanished from under the cluster.

    Reporting current state (rather than a past event) keeps this idempotent:
    it returns the same answer on every status refresh, with no dependence on
    what a previous call already consumed.

    Note this deliberately does not use the NodeInfoSource cache that
    ``_check_nodes_health`` prefers: a node absent from that cache is
    indistinguishable from a healthy one, and a deleted node is exactly the
    case this needs to report.

    Args:
        node_names: The Kubernetes node names the cluster's pods were placed
            on, e.g. from ``ClusterInfo.get_node_names()``.
        provider_config: The Kubernetes provider config.

    Returns:
        A human-readable reason, or None when every node is present and
        healthy (or none could be checked).
    """
    context = kubernetes_utils.get_context_from_config(provider_config)
    unique_names = sorted({name for name in node_names if name})
    if not unique_names:
        return None

    def _inspect_node(name: str) -> Optional[str]:
        """Returns an issue description for a node, or None if it is fine."""
        try:
            node = kubernetes.core_api(context).read_node(
                name, _request_timeout=kubernetes.API_TIMEOUT)
        except kubernetes.api_exception() as e:
            if e.status == 404:
                return 'no longer exists'
            logger.debug(f'Failed to read node {name}: {e}')
            return None
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(f'Failed to read node {name}: {e}')
            return None
        # NotReady first: it is more severe than cordoned, and a cordoned
        # node that has also gone NotReady is described by the former.
        node_status = getattr(node, 'status', None)
        for condition in (getattr(node_status, 'conditions', None) or []):
            if condition.type == 'Ready' and condition.status != 'True':
                return 'is NotReady'
        if getattr(getattr(node, 'spec', None), 'unschedulable', False):
            return 'is cordoned'
        return None

    issues = subprocess_utils.run_in_parallel(_inspect_node, unique_names)
    parts = [
        f'node {name} {issue}' for name, issue in zip(unique_names, issues)
        if issue is not None
    ]
    if not parts:
        return None
    return '; '.join(parts)


def _get_pod_termination_reason(pod: Any, cluster_name: str) -> str:
    """Get pod termination reason and write to cluster events.

    Checks both pod conditions (for preemption/disruption) and
    container statuses (for exit codes/errors).
    """
    utc_min_time = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
    latest_timestamp = (pod.status.start_time or utc_min_time)
    ready_state = 'Unknown'
    termination_reason = 'Terminated unexpectedly'
    container_reasons = []

    # Check pod status conditions for high level overview.
    # No need to sort, as each condition.type will only appear once.
    for condition in (pod.status.conditions or []):
        reason = condition.reason or 'Unknown reason'
        message = condition.message or ''

        # Get last known readiness state.
        if condition.type == 'Ready':
            ready_state = f'{reason} ({message})' if message else reason
        # Kueue preemption, as defined in:
        # https://pkg.go.dev/sigs.k8s.io/kueue/pkg/controller/jobs/pod#pkg-constants
        elif condition.type == 'TerminationTarget':
            termination_reason = f'Preempted by Kueue: {reason}'
            if message:
                termination_reason += f' ({message})'
        # Generic disruption.
        elif condition.type == 'DisruptionTarget':
            termination_reason = f'Disrupted: {reason}'
            if message:
                termination_reason += f' ({message})'

        if condition.last_transition_time is not None:
            latest_timestamp = max(latest_timestamp,
                                   condition.last_transition_time)

    # Fall back to the pod-level kubelet reason (e.g. 'Evicted' for
    # ephemeral-storage / disk / memory pressure) when no preemption/disruption
    # condition explained the failure. This is often the only place an eviction
    # cause is recorded (container statuses may be uninformative).
    pod_status_reason = getattr(pod.status, 'reason', None)
    if termination_reason == 'Terminated unexpectedly' and pod_status_reason:
        termination_reason = pod_status_reason
        pod_status_message = (getattr(pod.status, 'message', None) or
                              '').strip()
        if pod_status_message:
            termination_reason += f' ({pod_status_message})'

    pod_reason = (f'{termination_reason}.\n'
                  f'Last known state: {ready_state}.')

    # Check container statuses for exit codes/errors
    if pod.status and pod.status.container_statuses:
        for container_status in pod.status.container_statuses:
            terminated = container_status.state.terminated
            if terminated:
                exit_code = terminated.exit_code
                reason = terminated.reason
                if exit_code == 0:
                    # skip exit 0 (non-failed) just for sanity
                    logger.debug(f'{pod.metadata.name}/{container_status.name} '
                                 'had exit code 0. Skipping.')
                    continue
                if reason is None:
                    # just in-case reason is None, have default for debugging
                    reason = f'exit({exit_code})'
                container_reasons.append(reason)
                if terminated.finished_at is not None:
                    latest_timestamp = max(latest_timestamp,
                                           terminated.finished_at)

            # TODO (kyuds): later, if needed, query `last_state` too.

    # Normally we will have a single container per pod for skypilot
    # but doing this just in-case there are multiple containers.
    if container_reasons:
        pod_reason += f'\nContainer errors: {" | ".join(container_reasons)}'

    global_user_state.add_cluster_event(
        cluster_name,
        None,
        f'[kubernetes pod {pod.metadata.name} terminated] {pod_reason}',
        global_user_state.ClusterEventType.DEBUG,
        transitioned_at=int(latest_timestamp.timestamp()),
    )
    return pod_reason


def _condensed_pod_reason(pod: 'V1Pod') -> str:
    """Condense pod failure into a single-line user-facing summary.

    Thin wrapper around ``kubernetes_utils.get_condensed_pod_reason`` (the
    canonical implementation, shared with the command-runner OOM diagnosis
    path).
    """
    return kubernetes_utils.get_condensed_pod_reason(pod)


def _event_last_observed(event: Any) -> Optional[float]:
    """When ``event`` was last observed, in unix seconds; None if unknown.

    Prefers the last-observed time over the creation time. Kubernetes folds a
    repeated event back into the original object -- bumping ``count`` and
    ``last_timestamp`` (``series.last_observed_time`` on the events.k8s.io
    path) while ``metadata.creation_timestamp`` stays pinned to the first
    occurrence -- so the creation time says when a condition started, and only
    the last-observed time says whether it is still happening.
    """
    series = getattr(event, 'series', None)
    ts = (getattr(series, 'last_observed_time', None) or
          getattr(event, 'last_timestamp', None) or
          getattr(event, 'event_time', None) or
          getattr(getattr(event, 'metadata', None), 'creation_timestamp', None))
    if not isinstance(ts, datetime.datetime):
        # Absent, or a field the API server did not populate.
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=datetime.timezone.utc)
    return ts.timestamp()


def _get_pod_events(context: Optional[str], namespace: str,
                    pod_name: str) -> List[Any]:
    """Get the events for a pod, sorted by timestamp, most recent first."""
    pod_field_selector = (
        f'involvedObject.kind=Pod,involvedObject.name={pod_name}')
    pod_events = kubernetes.core_api(context).list_namespaced_event(
        namespace,
        field_selector=pod_field_selector,
        _request_timeout=kubernetes.API_TIMEOUT).items
    return sorted(
        pod_events,
        key=lambda event: event.metadata.creation_timestamp,
        # latest event appears first
        reverse=True)


# kubelet pod-event reasons that carry a terminal failure cause which is not
# always reflected in pod.status in time -- notably an eviction for
# ephemeral-storage / disk / memory pressure, where the kubelet emits the event
# while pod.status.phase is still 'Running' and status.reason/message lag.
_FAILURE_EVENT_REASONS = ('Evicted',)

# Substrings that already name a specific failure cause; when a status-derived
# reason contains one, consulting events would add nothing. Every reason we
# carry a remediation hint for is specific by definition, so derive those from
# the canonical hint table; add the few specific reasons that have no hint
# (CrashLoopBackOff and the Kueue/disruption conditions).
_SPECIFIC_FAILURE_REASON_SUBSTRINGS = tuple(
    kubernetes_utils.get_failure_hint_reasons()) + ('CrashLoopBackOff',
                                                    'Preempted', 'Disrupted')


def _reason_lacks_specific_cause(reason: Optional[str]) -> bool:
    """Whether `reason` does not already name a specific failure cause."""
    return not reason or not any(s in reason
                                 for s in _SPECIFIC_FAILURE_REASON_SUBSTRINGS)


def _get_pod_failure_reason_from_events(context: Optional[str], namespace: str,
                                        pod_name: str) -> Optional[str]:
    """Best-effort failure reason from the pod's most recent kubelet event.

    Some failures (notably evictions for ephemeral-storage / disk / memory
    pressure) are recorded in pod events before they propagate to
    pod.status.reason / phase. Returns '<reason>: <message>' for the most
    recent event whose reason is in ``_FAILURE_EVENT_REASONS``, else None.
    Never raises -- this is additive diagnostics.
    """
    try:
        events = _get_pod_events(context, namespace, pod_name)
    except Exception:  # pylint: disable=broad-except
        return None
    for event in events:  # most recent first
        if event.reason in _FAILURE_EVENT_REASONS:
            message = (event.message or '').strip()
            return f'{event.reason}: {message}'.rstrip(': ')
    return None


def _get_pod_failure_reason_from_status(context: Optional[str], namespace: str,
                                        pod_name: str) -> Optional[str]:
    """Best-effort durable failure reason from the pod's terminated states.

    A run-phase OOMKilled is recorded in the container's
    ``last_state.terminated`` and survives the restart, but the live ``Ready``
    condition flips back to True once the container is running again -- so a
    snapshot taken outside that window (the read raced the restart) misses it.
    Re-reads the pod and derives the reason from current *and* previous
    terminated states, so the OOM is recovered regardless of where the read
    landed in the restart cycle. Returns '<pod> is not ready (<reason>)' (the
    framing mirrors the single-pod output of
    backend_utils._summarize_pod_reasons so the message reads the same whether
    the live status or this fallback caught it), else None. Never raises.
    """
    try:
        pod = kubernetes.core_api(context).read_namespaced_pod(
            pod_name, namespace, _request_timeout=kubernetes.API_TIMEOUT)
    except Exception:  # pylint: disable=broad-except
        return None
    if not kubernetes_utils.pod_terminated_abnormally(pod):
        return None
    return (f'{pod_name} is not ready '
            f'({kubernetes_utils.get_condensed_pod_reason(pod)})')


def _first_pod_failure_reason(
    provider_config: Dict[str, Any], pod_names: List[str],
    per_pod_fn: Callable[[Optional[str], str, str], Optional[str]]
) -> Optional[str]:
    """Return the first non-None ``per_pod_fn(context, namespace, pod)``.

    Resolves namespace/context from the provider config and probes each pod in
    order. Used when a cluster is abnormal but the live per-pod status did not
    name a cause. Best-effort -- per_pod_fn is expected to never raise.
    """
    namespace = kubernetes_utils.get_namespace_from_config(provider_config)
    context = kubernetes_utils.get_context_from_config(provider_config)
    for pod_name in pod_names:
        reason = per_pod_fn(context, namespace, pod_name)
        if reason is not None:
            return reason
    return None


def get_cluster_failure_reason_from_events(
        provider_config: Dict[str, Any], pod_names: List[str]) -> Optional[str]:
    """First pod eviction reason from kubelet events (status lags), or None.

    An eviction (ephemeral-storage / disk / memory pressure) is emitted as a
    pod event while the pod can still report Running/Ready and status.reason
    has not caught up. See _get_pod_failure_reason_from_events.
    """
    return _first_pod_failure_reason(provider_config, pod_names,
                                     _get_pod_failure_reason_from_events)


def get_cluster_failure_reason_from_pods(provider_config: Dict[str, Any],
                                         pod_names: List[str]) -> Optional[str]:
    """First pod's durable terminated-state reason (e.g. a restarted OOM).

    See _get_pod_failure_reason_from_status. Complements the events lookup:
    catches an OOMKilled recovered from last_state when no kubelet event names
    the cause.
    """
    return _first_pod_failure_reason(provider_config, pod_names,
                                     _get_pod_failure_reason_from_status)


# Custom Kubernetes Event reason emitted by skylet when a cluster autodowns
# itself after reaching its idle timeout. The server's status refresh reads this
# back (get_cluster_autostop_event) as a durable breadcrumb to attribute the
# termination to autostop -- even when the refresh never observed the cluster in
# the AUTOSTOPPING state, which happens when the pod completes the autodown
# between two refreshes. On Kubernetes a cluster only ever autodowns (stop is
# not supported), so a single reason suffices.
AUTOSTOP_EVENT_REASON = 'SkyPilotAutodown'


def emit_autostop_event_best_effort(provider_config: Dict[str, Any],
                                    cluster_name_on_cloud: str) -> None:
    """Emit a Kubernetes Event marking that the cluster is autodowning.

    Best-effort breadcrumb written by skylet from the autostop code path, just
    before the pods are terminated, so it survives the pod deletion (Kubernetes
    keeps events for the namespace's event TTL, ~1h by default). Read back by
    get_cluster_autostop_event on the server. Never raises -- failing to emit
    the event must not block the actual autodown.
    """
    try:
        namespace = kubernetes_utils.get_namespace_from_config(provider_config)
        context = kubernetes_utils.get_context_from_config(provider_config)
        k8s_client = kubernetes.kubernetes.client
        now = datetime.datetime.now(datetime.timezone.utc)
        # The event references the head pod, whose name is exactly
        # f'{cluster_name_on_cloud}-head' -- the reader matches on it. Event
        # names must be unique within the namespace.
        head_pod_name = f'{cluster_name_on_cloud}-head'
        suffix = f'{int(now.timestamp() * 1e6):x}'
        event_name = f'{head_pod_name}.skyautodown.{suffix}'
        event = k8s_client.CoreV1Event(
            metadata=k8s_client.V1ObjectMeta(name=event_name,
                                             namespace=namespace),
            involved_object=k8s_client.V1ObjectReference(kind='Pod',
                                                         name=head_pod_name,
                                                         namespace=namespace),
            reason=AUTOSTOP_EVENT_REASON,
            message='Cluster is autodowning after reaching its idle timeout.',
            type='Normal',
            source=k8s_client.V1EventSource(component='skypilot-skylet'),
            first_timestamp=now,
            last_timestamp=now)
        kubernetes.core_api(context).create_namespaced_event(
            namespace, event, _request_timeout=kubernetes.API_TIMEOUT)
        logger.debug(f'Emitted {AUTOSTOP_EVENT_REASON} event for '
                     f'{cluster_name_on_cloud}.')
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Failed to emit autodown event for '
                     f'{cluster_name_on_cloud}: {e}')


def get_cluster_autostop_event(
        provider_config: Dict[str, Any],
        cluster_name_on_cloud: str,
        since: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Most recent autodown breadcrumb for the cluster, or None.

    Reads the Kubernetes Event emitted by skylet (see
    emit_autostop_event_best_effort) when the cluster autodowned itself. Lets
    the server attribute a terminated k8s cluster to autostop when the status
    refresh never observed the AUTOSTOPPING state. Matches the head pod's name
    exactly so it still resolves after the head pod (the event's involvedObject)
    has been deleted. Returns a dict with ``reason``, ``message`` and
    ``transitioned_at`` (unix seconds, or None if the event carries no
    timestamp). Best-effort -- never raises.

    Args:
        since: If given (unix seconds), ignore events older than this. Pass the
            current cluster's launch time so a stale breadcrumb left by a prior
            incarnation of a same-named cluster (k8s keeps events for the
            namespace TTL, ~1h) is not mis-attributed to this teardown.
    """
    try:
        namespace = kubernetes_utils.get_namespace_from_config(provider_config)
        context = kubernetes_utils.get_context_from_config(provider_config)
        events = kubernetes.core_api(context).list_namespaced_event(
            namespace,
            field_selector=f'reason={AUTOSTOP_EVENT_REASON}',
            _request_timeout=kubernetes.API_TIMEOUT).items
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Failed to read autodown event for '
                     f'{cluster_name_on_cloud}: {e}')
        return None

    def _event_unix_time(event: Any) -> Optional[int]:
        ts = (event.last_timestamp or event.event_time or
              event.metadata.creation_timestamp)
        if ts is None:
            return None
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=datetime.timezone.utc)
        return int(ts.timestamp())

    # The writer always references the head pod, whose name is exactly
    # f'{cluster_name_on_cloud}-head'. Match it exactly (not by prefix) so a
    # sibling cluster whose name shares this prefix cannot contaminate the
    # result, and bound by `since` so a stale breadcrumb from a previous
    # incarnation of a same-named cluster is ignored.
    head_pod_name = f'{cluster_name_on_cloud}-head'
    matching = []
    for event in events:
        if (event.involved_object is None or
                event.involved_object.name != head_pod_name):
            continue
        event_time = _event_unix_time(event)
        if since is not None and (event_time is None or event_time < since):
            continue
        matching.append((event_time, event))
    if not matching:
        return None
    event_time, latest = max(matching, key=lambda item: item[0] or 0)
    return {
        'reason': latest.reason,
        'message': latest.message,
        'transitioned_at': event_time,
    }


def _unmask_crashloopbackoff_reason(cs: Any) -> Optional[str]:
    """Return `last_state.terminated.reason` iff cs is in CrashLoopBackOff
    and a previous terminated reason is available; else None.

    Used to surface OOMKilled / Error / etc. instead of bare CrashLoopBackOff.
    """
    waiting = cs.state.waiting if cs.state else None
    if waiting is None or waiting.reason != 'CrashLoopBackOff':
        return None
    last_term = cs.last_state.terminated if cs.last_state else None
    if last_term is None or not last_term.reason:
        return None
    return last_term.reason


def _get_pod_pending_reason_from_container_status(pod: Any) -> Optional[str]:
    """Tier-1 sweep: derive a pending reason from pod.status.container_statuses.

    For each container in turn:
      1. state.waiting: on ContainerCreating/PodInitializing, fall through to
         checks 2 and 3 on the *same* container (a transient-waiting current
         state can coexist with a prior bad termination — surface the prior
         fault); on CrashLoopBackOff, unmask via last_state.terminated; else
         return the waiting reason.
      2. state.terminated: if exit_code != 0, return terminated.reason.
      3. last_state.terminated: if exit_code != 0 and reason present, return
         it (race-window: container restarted between iterations).
    If a container matches none of (1)-(3), advance to the next container.
    Returns None only after exhausting all containers.

    Returns a bare reason string (e.g. "OOMKilled", not
    "OOMKilled (exit 137)") -- the exit-code suffix is intentionally omitted
    because it adds cardinality that defeats nop_if_duplicate dedup on the
    LAUNCH_PROGRESS event.
    """
    container_statuses = getattr(getattr(pod, 'status', None),
                                 'container_statuses', None) or []
    for cs in container_statuses:
        # 1. state.waiting
        waiting = cs.state.waiting if cs.state else None
        if waiting is not None:
            if waiting.reason in ('ContainerCreating', 'PodInitializing'):
                # Transient; fall through to checks 2/3 on this container.
                pass
            elif waiting.reason == 'CrashLoopBackOff':
                unmasked = _unmask_crashloopbackoff_reason(cs)
                return unmasked or 'CrashLoopBackOff'
            else:
                return waiting.reason

        # 2. state.terminated (currently terminated, between restarts)
        terminated = cs.state.terminated if cs.state else None
        if terminated is not None and terminated.exit_code != 0:
            return terminated.reason or 'Terminated'

        # 3. last_state.terminated (previous run terminated badly)
        last_term = cs.last_state.terminated if cs.last_state else None
        if (last_term is not None and last_term.exit_code is not None and
                last_term.exit_code != 0 and last_term.reason):
            return last_term.reason

    return None


def _get_pod_pending_reason(
        context: Optional[str],
        namespace: str,
        pod_name: str,
        warnings_only: bool = False) -> Optional[Tuple[str, str]]:
    """Get the reason why a pod is pending from its events.

    Two-pass scan over the event list (sorted newest-first by _get_pod_events):
      1. Tier 2 -- the newest event with event.type == 'Warning' whose reason
         is not in _PENDING_REASON_WARNING_EVENT_IGNORELIST.
      2. Tier 3 -- the newest event whose reason is in
         _PENDING_REASON_NORMAL_EVENT_ALLOWLIST.
    A Warning beats an allow-listed Normal -- a FailedScheduling Warning is a
    more truthful pending reason than a Pulling Normal from a doomed retry --
    but only while the Warning still describes the pod. Kubernetes keeps events
    for the API server's event TTL (~1h), so a resolved Warning outlives the
    condition that produced it by a long way, and reporting a stale one is not
    just a mislabeled spinner: the reason then never changes while the pod
    makes progress, which is exactly what the no-progress deadline in
    _wait_for_pods_to_run reads as a stall. Three independent checks demote one:

      a. A Warning last observed before the pod was bound cannot describe what
         the kubelet is doing, because every caller of this function observes
         the pod only after the bind. This covers the pod that waited on an
         autoscaler and carries a FailedScheduling for the next hour, whether
         or not any Normal event follows it.
      b. A Warning observed less recently than the allow-listed Normal has
         been overtaken by it -- e.g. a mount the kubelet retried successfully,
         whose FailedMount predates the image pull now under way.
      c. A Warning whose reason is in _PENDING_REASON_WARNING_EVENT_IGNORELIST
         is never reported, whatever its timestamps: it is emitted once during
         normal startup and re-emitted while the condition it names resolves
         itself, so neither (a) nor (b) can catch it.

    (a) and (b) compare last-observed times, not creation times: kubernetes
    folds a repeated event back into the original object, bumping
    count/last_timestamp while creation_timestamp stays pinned to the first
    occurrence. So a live Warning being re-emitted survives both checks, and an
    event that cannot be dated is never treated as stale. Ordering *within* a
    tier is left to _get_pod_events' newest-first creation order.

    With warnings_only, tier 3 is answered with None rather than the Normal
    event: the caller has a reason already and is asking the narrower question
    of whether the pod is actively failing. The tiering still runs in full --
    a Normal that demotes a stale Warning under (b) must still demote it here,
    or the caller would be handed the very Warning the pod has moved past.

    Returns a (reason, message) tuple, or None if neither pass matches.
    """
    try:
        pod_events = _get_pod_events(context, namespace, pod_name)
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Failed to get events for pod {pod_name}: {e}')
        return None

    if not pod_events:
        return None

    # (a) The bind, read from the pod's own events. Absent once it has aged
    # out of the event window -- by which point every pre-bind Warning has
    # aged out ahead of it, so there is nothing left to demote.
    bound_at = next((_event_last_observed(e)
                     for e in pod_events
                     if e.reason == _POD_BOUND_EVENT_REASON), None)

    def _describes_pod_now(event: Any) -> bool:
        if event.reason in _PENDING_REASON_WARNING_EVENT_IGNORELIST:
            return False
        if bound_at is None:
            return True
        observed_at = _event_last_observed(event)
        return observed_at is None or observed_at >= bound_at

    # Tier 2: Warning events, minus the ones known to go stale in place.
    warning = next((
        e for e in pod_events if e.type == 'Warning' and _describes_pod_now(e)),
                   None)

    # Tier 3: allow-listed Normal events.
    normal = next((e for e in pod_events
                   if e.reason in _PENDING_REASON_NORMAL_EVENT_ALLOWLIST), None)

    chosen = warning if warning is not None else normal
    # (b) Cross-tier recency.
    if warning is not None and normal is not None:
        warning_at = _event_last_observed(warning)
        normal_at = _event_last_observed(normal)
        if (warning_at is not None and normal_at is not None and
                normal_at > warning_at):
            chosen = normal
    if chosen is None:
        return None
    if warnings_only and chosen.type != 'Warning':
        return None
    return chosen.reason or 'Unknown', chosen.message or ''


def _format_pod_missing_reason(
        *, context: Optional[str], pod_name: str, event: Any, cluster_name: str,
        transitioned_at: int,
        first_pod: bool) -> Tuple[str, global_user_state.ClusterEventType]:
    """Format pod missing reason.

    Args:
        context: The context of the Kubernetes cluster.
        pod_name: The name of the pod.
        event: The event object.
        cluster_name: The name of the cluster.
        transitioned_at: The timestamp of the event.
        first_pod: Whether this is the first pod.
                   Used in cases where some logic only needs to be run
                   for one pod in the cluster.

    Returns:
        A tuple of the formatted event string and the event type.
    """
    del first_pod, context, cluster_name, transitioned_at  #unused
    event_str = (f'[kubernetes pod {pod_name}] '
                 f'{event.reason} {event.message}')
    event_type = global_user_state.ClusterEventType.DEBUG
    return event_str, event_type


def _get_pod_missing_reason(context: Optional[str], namespace: str,
                            cluster_name: str, pod_name: str,
                            first_pod: bool) -> Optional[str]:
    """Get events for missing pod and write to cluster events."""
    logger.debug(f'Analyzing events for pod {pod_name}')
    pod_events = _get_pod_events(context, namespace, pod_name)
    last_scheduled_node = None
    insert_new_pod_event = True
    new_event_inserted = False
    inserted_pod_events = 0

    for event in pod_events:
        if event.reason == 'Scheduled':
            pattern = r'Successfully assigned (\S+) to (\S+)'
            match = re.search(pattern, event.message)
            if match:
                scheduled_node = match.group(2)
                last_scheduled_node = scheduled_node
        if insert_new_pod_event:
            # Try inserting the latest events first. If the event is a
            # duplicate, it means the event (and any previous events) have
            # already been inserted - so do not insert further events.
            transitioned_at = int(event.metadata.creation_timestamp.timestamp())
            event_str, event_type = _format_pod_missing_reason(
                context=context,
                pod_name=pod_name,
                event=event,
                first_pod=first_pod,
                cluster_name=cluster_name,
                transitioned_at=transitioned_at)
            try:
                global_user_state.add_cluster_event(
                    cluster_name,
                    None,
                    event_str,
                    event_type,
                    transitioned_at=transitioned_at,
                    expose_duplicate_error=True)
                logger.debug(f'[pod {pod_name}] encountered new pod event: '
                             f'{event.metadata.creation_timestamp} '
                             f'{event.reason} {event.message}')
            except db_utils.UniqueConstraintViolationError:
                insert_new_pod_event = False
            else:
                new_event_inserted = True
                inserted_pod_events += 1

    logger.debug(f'[pod {pod_name}] processed {len(pod_events)} pod events and '
                 f'inserted {inserted_pod_events} new pod events '
                 'previously unseen')

    if last_scheduled_node is not None:
        node_field_selector = ('involvedObject.kind=Node,'
                               f'involvedObject.name={last_scheduled_node}')
        node_events = kubernetes.core_api(context).list_namespaced_event(
            namespace,
            field_selector=node_field_selector,
            _request_timeout=kubernetes.API_TIMEOUT).items
        node_events = sorted(
            node_events,
            key=lambda event: event.metadata.creation_timestamp,
            # latest event appears first
            reverse=True)
        insert_new_node_event = True
        inserted_node_events = 0
        for event in node_events:
            if insert_new_node_event:
                # Try inserting the latest events first. If the event is a
                # duplicate, it means the event (and any previous events) have
                # already been inserted - so do not insert further events.
                try:
                    global_user_state.add_cluster_event(
                        cluster_name,
                        None, f'[kubernetes node {last_scheduled_node}] '
                        f'{event.reason} {event.message}',
                        global_user_state.ClusterEventType.DEBUG,
                        transitioned_at=int(
                            event.metadata.creation_timestamp.timestamp()),
                        expose_duplicate_error=True)
                    logger.debug(
                        f'[pod {pod_name}] encountered new node event: '
                        f'{event.metadata.creation_timestamp} '
                        f'{event.reason} {event.message}')
                except db_utils.UniqueConstraintViolationError:
                    insert_new_node_event = False
                else:
                    new_event_inserted = True
                    inserted_node_events += 1

        logger.debug(f'[pod {pod_name}: node {last_scheduled_node}] '
                     f'processed {len(node_events)} node events and '
                     f'inserted {inserted_node_events} new node events '
                     'previously unseen')
    else:
        logger.debug(f'[pod {pod_name}] could not determine the node '
                     'the pod was scheduled to')

    if not new_event_inserted:
        # If new event is not inserted, there is no useful information to
        # return. Return None.
        return None

    # Analyze the events for failure
    failure_reason = None
    failure_decisiveness = 0

    def _record_failure_reason(reason: str, decisiveness: int):
        nonlocal failure_reason, failure_decisiveness
        if decisiveness > failure_decisiveness:
            failure_reason = reason
            failure_decisiveness = decisiveness

    cluster_events = global_user_state.get_cluster_events(
        cluster_name, None, global_user_state.ClusterEventType.DEBUG)
    for event in cluster_events:
        if event.startswith('[kubernetes pod'):
            event = event.split(']')[1].strip()
        elif event.startswith('[kubernetes node'):
            event = event.split(']')[1].strip()

        if event.startswith('NodeNotReady '):
            _record_failure_reason(event[len('NodeNotReady '):], 1)
        elif event.startswith('TaintManagerEviction '):
            # usually the event message for TaintManagerEviction is not useful
            # so we record a more generic message.
            _record_failure_reason('pod was evicted by taint manager', 2)
        elif event.startswith('DeletingNode '):
            _record_failure_reason(event[len('DeletingNode '):], 3)
    return failure_reason


def list_namespaced_pod(context: Optional[str], namespace: str,
                        cluster_name_on_cloud: str, is_ssh: bool, identity: str,
                        label_selector: str) -> List[Any]:
    # Get all the pods with the label skypilot-cluster-name: <cluster_name>
    try:
        # log the query parameters we pass to the k8s api
        logger.debug(f'Querying k8s api for pods:\n'
                     f'context: {context}\n'
                     f'namespace: {namespace}\n'
                     f'label selector:`{label_selector}`.')

        response = kubernetes.core_api(context).list_namespaced_pod(
            namespace,
            label_selector=label_selector,
            _request_timeout=kubernetes.API_TIMEOUT)

        # log PodList response info
        if sky_logging.logging_enabled(logger, sky_logging.DEBUG):
            logger.debug(f'k8s api response for `{label_selector}`:\n'
                         f'apiVersion={response.api_version}, '
                         f'kind={response.kind},\n'
                         f'metadata={response.metadata}')

        pods = response.items

        # log detailed Pod info
        if sky_logging.logging_enabled(logger, sky_logging.DEBUG):
            logger.debug(f'k8s api response for `{label_selector}`: '
                         f'len(pods)={len(pods)}')
            for pod in pods:
                logger.debug(f'k8s pod info for `{label_selector}`: '
                             f'pod.apiVersion={pod.api_version}, '
                             f'pod.kind={pod.kind}, \n'
                             f'pod.name={pod.metadata.name}, '
                             f'pod.namespace={pod.metadata.namespace}, \n'
                             f'pod.labels={pod.metadata.labels}, \n'
                             f'pod.annotations={pod.metadata.annotations}, \n'
                             'pod.creationTimestamp='
                             f'{pod.metadata.creation_timestamp}, '
                             'pod.deletionTimestamp='
                             f'{pod.metadata.deletion_timestamp}, \n'
                             f'pod.status={pod.status}')
        return pods

    except kubernetes.max_retry_error():
        with ux_utils.print_exception_no_traceback():
            if is_ssh:
                node_pool = common_utils.removeprefix(context,
                                                      'ssh-') if context else ''
                msg = (
                    f'Cannot connect to SSH Node Pool {node_pool}. '
                    'Please check if the SSH Node Pool is up and accessible. '
                    'To debug, run `sky check ssh` to check the status of '
                    'the SSH Node Pool.')
            else:
                ctx = kubernetes_utils.get_current_kube_config_context_name()
                msg = (f'Network error - check if the {identity} in '
                       f'context {ctx} is up and accessible.')
            raise exceptions.ClusterStatusFetchingError(
                f'Failed to query cluster {cluster_name_on_cloud!r} status. ' +
                msg) from None
    except Exception as e:  # pylint: disable=broad-except
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ClusterStatusFetchingError(
                f'Failed to query {identity} {cluster_name_on_cloud!r} '
                f'status: {common_utils.format_exception(e)}')


def query_instances(
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
    retry_if_missing: bool = False,
    status_map_overrides: Optional[Mapping[
        str, Optional['status_lib.ClusterStatus']]] = None,
) -> Dict[str, Tuple[Optional['status_lib.ClusterStatus'], Optional[str]]]:
    # Mapping from pod phase to skypilot status. These are the only valid pod
    # phases.
    # https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#pod-phase
    # ``status_map_overrides`` lets callers (e.g. plugin provisioners whose
    # pods don't follow the ray-cluster lifecycle) selectively remap a
    # subset of phases without duplicating this whole function.
    status_map = {
        'Pending': status_lib.ClusterStatus.INIT,
        'Running': status_lib.ClusterStatus.UP,
        'Failed': status_lib.ClusterStatus.INIT,
        'Unknown': None,
        'Succeeded': None,
    }
    if status_map_overrides:
        status_map = {**status_map, **status_map_overrides}

    assert provider_config is not None
    namespace = kubernetes_utils.get_namespace_from_config(provider_config)
    context = kubernetes_utils.get_context_from_config(provider_config)
    is_ssh = context.startswith('ssh-') if context else False
    identity = 'SSH Node Pool' if is_ssh else 'Kubernetes cluster'
    label_selector = (f'{constants.TAG_SKYPILOT_CLUSTER_NAME}='
                      f'{cluster_name_on_cloud}')

    attempts = 0
    pods = list_namespaced_pod(context, namespace, cluster_name_on_cloud,
                               is_ssh, identity, label_selector)
    # When we see no pods returned from the k8s api, we assume the pods have
    # been terminated by the user directly and mark the cluster as terminated
    # in the global user state.
    # We add retry logic here as an attempt to mitigate a leak caused by the
    # kubernetes api returning no pods despite the pods actually existing.
    while (retry_if_missing and not pods and
           attempts < _MAX_QUERY_INSTANCES_RETRIES):
        logger.debug(f'Retrying to query k8s api for {cluster_name_on_cloud} '
                     f'{attempts}/{_MAX_QUERY_INSTANCES_RETRIES} times.'
                     f'after {_QUERY_INSTANCES_RETRY_INTERVAL} seconds.')
        time.sleep(_QUERY_INSTANCES_RETRY_INTERVAL)
        attempts += 1
        pods = list_namespaced_pod(context, namespace, cluster_name_on_cloud,
                                   is_ssh, identity, label_selector)
        if len(pods) > 0:
            logger.info(f'Found {len(pods)} pods for {label_selector} after'
                        f'{attempts} retries.')

    # Check if the pods are running or pending
    cluster_status: Dict[str, Tuple[Optional['status_lib.ClusterStatus'],
                                    Optional[str]]] = {}
    for pod in pods:
        phase = pod.status.phase
        is_terminating = pod.metadata.deletion_timestamp is not None
        pod_status = status_map[phase]
        reason = None
        if phase in ('Failed', 'Unknown') or is_terminating:
            reason = _get_pod_termination_reason(pod, cluster_name)
            logger.debug(f'Pod Status ({phase}) Reason(s): {reason}')
        elif phase == 'Running':
            reason = _get_pod_health_issues(pod)
        # An eviction (ephemeral-storage / disk / memory pressure) is recorded
        # in the pod's kubelet events before it reaches pod.status -- often
        # while the pod still reports 'Running'. When a flagged pod's
        # status-derived reason is missing the specific cause, recover it from
        # events. Scoped to already-flagged pods (reason set) with a generic
        # reason, so healthy pods incur no extra events API call.
        if reason is not None and _reason_lacks_specific_cause(reason):
            event_reason = _get_pod_failure_reason_from_events(
                context, namespace, pod.metadata.name)
            if event_reason is not None:
                reason = f'{reason}; {event_reason}'
        if non_terminated_only and pod_status is None:
            logger.debug(f'Pod {pod.metadata.name} is terminated, but '
                         'query_instances is called with '
                         f'non_terminated_only=True. Phase: {phase}')
            continue
        pod_name = pod.metadata.name
        cluster_status[pod_name] = (pod_status, reason)

    # Find the list of pod names that should be there
    # from k8s services. Filter duplicates as -ssh service
    # creates a duplicate entry.
    target_pod_names = list(
        set([
            service['spec']['selector']['component']
            for service in provider_config.get('services', [])
        ]))

    first_pod = True
    for target_pod_name in target_pod_names:
        if target_pod_name not in cluster_status:
            # If the pod is not in the cluster_status, it means it's not
            # running.
            # Analyze what happened to the pod based on events.
            reason = _get_pod_missing_reason(context, namespace, cluster_name,
                                             target_pod_name, first_pod)
            first_pod = False
            if not non_terminated_only:
                cluster_status[target_pod_name] = (None, reason)

    return cluster_status


def get_command_runners(
    cluster_info: common.ClusterInfo,
    **credentials: Dict[str, Any],
) -> List[command_runner.CommandRunner]:
    """Get a command runner for the given cluster."""
    assert cluster_info.provider_config is not None, cluster_info
    instances = cluster_info.instances
    namespace = kubernetes_utils.get_namespace_from_config(
        cluster_info.provider_config)
    context = kubernetes_utils.get_context_from_config(
        cluster_info.provider_config)

    runners: List[command_runner.CommandRunner] = []
    if cluster_info.head_instance_id is not None:
        pod_name = cluster_info.head_instance_id

        # Try to get deployment name from label first
        head_instance_info = instances[pod_name][0]
        deployment = head_instance_info.tags.get(
            k8s_constants.TAG_SKYPILOT_DEPLOYMENT_NAME)

        node_list = [((namespace, context), pod_name)]
        head_runner = command_runner.KubernetesCommandRunner(
            node_list[0],
            deployment=deployment,
            container=k8s_constants.RAY_NODE_CONTAINER_NAME,
            **credentials)
        runners.append(head_runner)

    node_list = [((namespace, context), pod_name)
                 for pod_name in instances.keys()
                 if pod_name != cluster_info.head_instance_id]
    runners.extend(
        command_runner.KubernetesCommandRunner.make_runner_list(
            node_list,
            container=k8s_constants.RAY_NODE_CONTAINER_NAME,
            **credentials))

    return runners
