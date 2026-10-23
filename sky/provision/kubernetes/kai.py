"""KAI Scheduler support for Kubernetes clusters.

When kubernetes.kai.queue is set, each cluster gets a KAI PodGroup so KAI
schedules its pods as a gang. KAI does not gate pending pods, so the pod wait
loop checks the PodGroup and parks the launch request while KAI queues it.
"""
import asyncio
import dataclasses
import datetime
import math
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from sky import exceptions
from sky.adaptors import kubernetes
from sky.provision import constants as provision_constants
from sky.provision.kubernetes import constants as k8s_constants
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.utils import common_utils

SCHEDULER_NAME = 'kai-scheduler'
QUEUE_LABEL = 'kai.scheduler/queue'
# Names the PodGroup a pod belongs to.
POD_GROUP_ANNOTATION = 'pod-group-name'

_KAI_GROUP = 'scheduling.run.ai'
_QUEUE_API = (_KAI_GROUP, 'v2')
POD_GROUP_API = (_KAI_GROUP, 'v2alpha2')
_TOPOLOGY_API = ('kai.scheduler', 'v1alpha1')
# provider_config key -> PodGroup topologyConstraint field.
_TOPOLOGY_LEVEL_FIELDS = (
    ('kai_required_topology_level', 'requiredTopologyLevel'),
    ('kai_preferred_topology_level', 'preferredTopologyLevel'),
)

# Maps a KAI quota reason to (requested resources field, Queue limit field).
_QUOTA_REASONS = {
    'OverLimit': ('podGroupRequestedResources', 'limit'),
    'NonPreemptibleOverQuota':
        ('podGroupRequestedNonPreemptibleResources', 'quota'),
}
_GPU_RESOURCE = 'nvidia.com/gpu'
_IMMUTABLE_SPEC_FIELDS = ('queue', 'priorityClassName', 'preemptibility',
                          'topologyConstraint')
_EVICTOR_ACTION_ANNOTATION = 'evictor-action-type'
_PARKED_POLL_SECONDS = 30
_WAIT_CHECK_SECONDS = 10

# Called by the pod wait loop with context, namespace, cluster name on cloud
# and cluster name.
SchedulingCallback = Callable[[Optional[str], str, str, str], None]


def uses_kai(provider_config: Dict[str, Any]) -> bool:
    return provider_config.get('kai_queue') is not None


def _pod_group_body(name: str, provider_config: Dict[str, Any],
                    labels: Dict[str, str], count: int) -> Dict[str, Any]:
    # Taken from the resolved config, not the pod, which pod_config can change.
    spec: Dict[str, Any] = {
        'minMember': count,
        'queue': provider_config['kai_queue']
    }
    if 'kai_priority_class' in provider_config:
        spec['priorityClassName'] = provider_config['kai_priority_class']
    if 'kai_preemptibility' in provider_config:
        spec['preemptibility'] = provider_config['kai_preemptibility']
    if 'kai_topology' in provider_config:
        constraint = {'topology': provider_config['kai_topology']}
        for key, field in _TOPOLOGY_LEVEL_FIELDS:
            if key in provider_config:
                constraint[field] = provider_config[key]
        spec['topologyConstraint'] = constraint
    # Copy the pod labels so a KAI scheduling shard selecting the pods also
    # selects the PodGroup.
    return {
        'apiVersion': '/'.join(POD_GROUP_API),
        'kind': 'PodGroup',
        'metadata': {
            'name': name,
            'labels': {
                **labels,
                provision_constants.TAG_SKYPILOT_CLUSTER_NAME: name,
            },
        },
        'spec': spec,
    }


def delete_pod_group(context: Optional[str], namespace: str, name: str) -> None:
    try:
        kubernetes.custom_objects_api(context).delete_namespaced_custom_object(
            *POD_GROUP_API,
            namespace,
            'podgroups',
            name,
            _request_timeout=kubernetes.API_TIMEOUT)
    except kubernetes.api_exception() as e:
        if e.status != 404:
            raise


def ensure_pod_group(context: Optional[str], namespace: str,
                     cluster_name_on_cloud: str, provider_config: Dict[str,
                                                                       Any],
                     node_config: Dict[str, Any], count: int) -> None:
    """Creates the cluster's PodGroup, unless its running pods have one.

    Raises:
        exceptions.ProvisionUnsupportedError: The PodGroup API is not served,
            the topology constraint is invalid, or the running cluster's
            PodGroup has a different queue, priority class, preemptibility or
            topology.
    """
    body = _pod_group_body(cluster_name_on_cloud, provider_config,
                           node_config['metadata'].get('labels', {}), count)
    if 'topologyConstraint' in body['spec']:
        _check_topology_constraint(context, body['spec']['topologyConstraint'])
    live_pods = kubernetes_utils.filter_pods(
        namespace, context,
        {k8s_constants.TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud},
        ['Pending', 'Running'])
    if not live_pods:
        # Left over from an incomplete teardown; its status is stale.
        delete_pod_group(context, namespace, cluster_name_on_cloud)
    api = kubernetes.custom_objects_api(context)
    try:
        api.create_namespaced_custom_object(
            *POD_GROUP_API,
            namespace,
            'podgroups',
            body,
            _request_timeout=kubernetes.API_TIMEOUT)
    except kubernetes.api_exception() as e:
        if e.status == 404:
            raise exceptions.ProvisionUnsupportedError(
                f'Context {context!r} does not serve the KAI PodGroup API, '
                f'or namespace {namespace!r} does not exist.') from e
        if e.status != 409 or not live_pods:
            raise
        existing = _get_pod_group(context, namespace, cluster_name_on_cloud)
        if existing is None:
            raise
        # The running pods keep their settings, so the gang cannot change them.
        # Its size cannot change either: a relaunch keeps the node count.
        changed = [
            field for field in _IMMUTABLE_SPEC_FIELDS
            if existing['spec'].get(field) != body['spec'].get(field)
        ]
        if changed:
            raise exceptions.ProvisionUnsupportedError(
                f'Cannot change KAI {", ".join(changed)} of a running cluster. '
                'Run `sky down` first.') from e


def _check_topology_constraint(context: Optional[str],
                               constraint: Dict[str, str]) -> None:
    """Fails a constraint KAI would accept and then never schedule.

    KAI validates neither the Topology nor its levels when the PodGroup is
    created. A missing Topology surfaces only once KAI evaluates the gang,
    and a missing level only in the scheduler's log.

    Raises:
        exceptions.ProvisionUnsupportedError: The Topology does not exist,
            does not have a requested level, or the preferred level is above
            the required one.
    """
    name = constraint['topology']
    topology = _get_cluster_object(context, _TOPOLOGY_API, 'topologies', name)
    if topology is None:
        raise exceptions.ProvisionUnsupportedError(
            f'KAI Topology {name!r} does not exist.')
    # Top to bottom; a level is named by its node label or its alias.
    levels = topology['spec']['levels']
    depths: Dict[str, int] = {}
    for depth, level in enumerate(levels):
        depths[level['nodeLabel']] = depth
        if level.get('alias'):
            depths[level['alias']] = depth
    for field in ('requiredTopologyLevel', 'preferredTopologyLevel'):
        if field in constraint and constraint[field] not in depths:
            names = [
                level.get('alias') or level['nodeLabel'] for level in levels
            ]
            raise exceptions.ProvisionUnsupportedError(
                f'KAI Topology {name!r} has no level '
                f'{constraint[field]!r}; its levels are {names}.')
    if ('requiredTopologyLevel' in constraint and
            'preferredTopologyLevel' in constraint and
            depths[constraint['preferredTopologyLevel']] <
            depths[constraint['requiredTopologyLevel']]):
        raise exceptions.ProvisionUnsupportedError(
            'The preferred KAI topology level '
            f'{constraint["preferredTopologyLevel"]!r} must be at or below the '
            f'required level {constraint["requiredTopologyLevel"]!r}.')


def _get_cluster_object(context: Optional[str], api: Tuple[str, str],
                        plural: str, name: str) -> Optional[Dict[str, Any]]:
    """A cluster-scoped KAI object, or None if it does not exist.

    Raises:
        exceptions.ProvisionUnsupportedError: SkyPilot may not read it.
    """
    try:
        return kubernetes.custom_objects_api(context).get_cluster_custom_object(
            *api, plural, name, _request_timeout=kubernetes.API_TIMEOUT)
    except kubernetes.api_exception() as e:
        if e.status == 404:
            return None
        if e.status == 403:
            raise exceptions.ProvisionUnsupportedError(
                f'SkyPilot needs permission to get {plural}.{api[0]} on '
                f'context {context!r}.') from e
        raise


def _get_pod_group(context: Optional[str], namespace: str,
                   name: str) -> Optional[Dict[str, Any]]:
    try:
        return kubernetes.custom_objects_api(
            context).get_namespaced_custom_object(
                *POD_GROUP_API,
                namespace,
                'podgroups',
                name,
                _request_timeout=kubernetes.API_TIMEOUT)
    except kubernetes.api_exception() as e:
        if e.status == 404:
            return None
        raise


def eviction_reason(context: Optional[str], namespace: str, pod_name: str,
                    since: Optional[datetime.datetime]) -> Optional[str]:
    """Why KAI evicted the pod; None if it did not or the API cannot say.

    KAI deletes the pods it evicts and records why only as an Evict event on
    the PodGroup, which outlives the pod. ``since`` is when the pod was
    created, which an eviction of the same pod name cannot precede.
    """
    # The pod name ends where the name characters end, so worker1 does not
    # match worker10.
    pod_ref = re.compile(rf'(?<![\w.-]){re.escape(f"{namespace}/{pod_name}")}'
                         r'(?![\w-]|\.[\w-])')
    try:
        events = kubernetes.core_api(context).list_namespaced_event(
            namespace,
            field_selector='involvedObject.kind=PodGroup,reason=Evict',
            _request_timeout=kubernetes.API_TIMEOUT).items
        for event in kubernetes_utils.events_since(events, since):
            # The first line names the pod; a reclaim adds lines describing
            # the queues.
            summary = (event.message or '').split('\n', 1)[0].strip()
            if not pod_ref.search(summary):
                continue
            # A PodGroup of the same name from an earlier launch has its own
            # UID, and its evictions are not this pod's.
            pod_group = _get_pod_group(context, namespace,
                                       event.involved_object.name)
            if (pod_group is None or
                    pod_group['metadata']['uid'] != event.involved_object.uid):
                continue
            # preempt, reclaim, consolidation or stalegangeviction. Lowercase
            # so it does not read as a kubelet 'Evicted' and earn its hint.
            action = (event.metadata.annotations or
                      {}).get(_EVICTOR_ACTION_ANNOTATION)
            label = (f'KAI evicted the pod ({action})'
                     if action else 'KAI evicted the pod')
            return f'{label}: {summary.rstrip(".")}'
    except (kubernetes.api_exception(), kubernetes.urllib3_http_error()):
        return None
    return None


def _queued_reasons(pod_group: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Returns KAI's reasons for keeping the gang queued, if it evaluated it."""
    status = pod_group.get('status', {})
    if status.get('resourcesStatus', {}).get('allocated'):
        return []
    return [
        reason for condition in status.get('schedulingConditions', [])
        for reason in condition.get('reasons') or [condition]
    ]


def _format_reasons(reasons: List[Dict[str, Any]]) -> str:
    messages = (f'{r["reason"]}: {" ".join(r.get("message", "").split())}'
                for r in reasons)
    return '; '.join(dict.fromkeys(messages))


def _gpu_limit(context: Optional[str], queue_name: str, field: str) -> float:
    """Returns the lowest GPU value of field along the queue's parent chain."""
    limit = math.inf
    # KAI's webhook does not reject a cycle of parent queues.
    walked = set()
    while queue_name and queue_name not in walked:
        walked.add(queue_name)
        queue = _get_cluster_object(context, _QUEUE_API, 'queues', queue_name)
        if queue is None:
            break
        # -1 is unlimited; an unset value is 0.
        value = queue['spec'].get('resources', {}).get('gpu', {}).get(field, 0)
        if value >= 0:
            limit = min(limit, value)
        queue_name = queue['spec'].get('parentQueue', '')
    return limit


def _never_fits(context: Optional[str], pod_group: Dict[str, Any],
                reasons: List[Dict[str, Any]]) -> bool:
    """Whether the gang can never be scheduled in its queue.

    Only GPU limits are checked. KAI reports an unlimited limit as 0, so the
    limit is read from the Queue objects instead of the reason.
    """
    for reason in reasons:
        if reason['reason'] == 'QueueDoesNotExist':
            return True
        fields = _QUOTA_REASONS.get(reason['reason'])
        if fields is None:
            continue
        requested_field, limit_field = fields
        requested_resources = reason.get('details',
                                         {}).get('queueDetails',
                                                 {}).get(requested_field, {})
        requested = float(
            kubernetes.parse_quantity(
                requested_resources.get(_GPU_RESOURCE, '0')))
        if requested > 0 and _gpu_limit(context, pod_group['spec']['queue'],
                                        limit_field) < requested:
            return True
    return False


def _queued_pod_group(context: Optional[str], namespace: str,
                      name: str) -> Optional[Dict[str, Any]]:
    """The cluster's PodGroup, if KAI keeps it queued.

    None if the PodGroup is gone, KAI scheduled the gang, or KAI has not
    evaluated it yet.
    """
    pod_group = _get_pod_group(context, namespace, name)
    if pod_group is None or not _queued_reasons(pod_group):
        return None
    return pod_group


@dataclasses.dataclass
class PodGroupScheduledCondition:
    """Resumes a parked launch once KAI schedules its PodGroup or rules it out.

    Implements the continue-condition contract the request scheduler consumes
    for ``exceptions.ExecutionPausedError``, duck-typed like
    ``locks.LockAcquirableCondition``. Errors propagate to the scheduler,
    which retries the launch after a backoff.
    """

    context: Optional[str]
    namespace: str
    # Also the name of the cluster's PodGroup.
    cluster_name_on_cloud: str
    cluster_name: str

    async def wait_async(
        self,
        *,
        is_cancelled: Callable[[], Awaitable[bool]],
        fallback_wait_seconds: float,
        update_status_msg: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> bool:
        del fallback_wait_seconds  # Unused.
        loop = asyncio.get_running_loop()
        checked_reason = None
        while not await is_cancelled():
            pod_group = await loop.run_in_executor(None, _queued_pod_group,
                                                   self.context, self.namespace,
                                                   self.cluster_name_on_cloud)
            if pod_group is None:
                # Resumed, the launch proceeds or fails on KAI's verdict.
                return True
            reasons = _queued_reasons(pod_group)
            reason = _format_reasons(reasons)
            # Checking the queue chain costs a request per queue, and KAI's
            # reason, which states the allocation, changes whenever the
            # answer can.
            if reason != checked_reason:
                if await loop.run_in_executor(None, _never_fits, self.context,
                                              pod_group, reasons):
                    return True
                if update_status_msg is not None:
                    # The same message the launch parked with.
                    await update_status_msg(
                        _queued_message(self.cluster_name, reasons))
                checked_reason = reason
            await asyncio.sleep(_PARKED_POLL_SECONDS)
        return False


def _queued_message(cluster_name: str, reasons: List[Dict[str, Any]]) -> str:
    return f'{cluster_name} is queued in KAI: {_format_reasons(reasons)}'


def queued_launch_check() -> SchedulingCallback:
    """Returns a pod wait loop callback that checks the gang every 10s."""
    last_check: Optional[float] = None

    def check(context: Optional[str], namespace: str,
              cluster_name_on_cloud: str, cluster_name: str) -> None:
        nonlocal last_check
        now = time.monotonic()
        if last_check is not None and now - last_check < _WAIT_CHECK_SECONDS:
            return
        last_check = now
        _park_or_fail_if_queued(context, namespace, cluster_name_on_cloud,
                                cluster_name)

    return check


def _park_or_fail_if_queued(context: Optional[str], namespace: str,
                            cluster_name_on_cloud: str,
                            cluster_name: str) -> None:
    """Parks the launch if KAI queued the gang; fails it if it never fits.

    Raises:
        exceptions.ProvisionUnsupportedError: The gang can never be scheduled.
        exceptions.ExecutionPausedError: KAI queued the gang.
    """
    try:
        pod_group = _queued_pod_group(context, namespace, cluster_name_on_cloud)
        if pod_group is None:
            return
        reasons = _queued_reasons(pod_group)
        never_fits = _never_fits(context, pod_group, reasons)
    except (kubernetes.api_exception(), kubernetes.urllib3_http_error()) as e:
        if not kubernetes_utils.is_transport_error(e):
            raise
        return
    if never_fits:
        raise exceptions.ProvisionUnsupportedError(
            f'KAI cannot schedule {cluster_name} in its queue: '
            f'{_format_reasons(reasons)}')
    if not common_utils.is_in_request_context():
        return
    raise exceptions.ExecutionPausedError(
        _queued_message(cluster_name, reasons),
        hint='The launch resumes when KAI schedules it.',
        retry_wait_seconds=_PARKED_POLL_SECONDS,
        continue_condition=PodGroupScheduledCondition(
            context=context,
            namespace=namespace,
            cluster_name_on_cloud=cluster_name_on_cloud,
            cluster_name=cluster_name))
