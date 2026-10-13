"""KAI Scheduler support for Kubernetes clusters.

When kubernetes.kai.queue is set, each cluster gets a KAI PodGroup so KAI
schedules its pods as a gang. KAI does not gate pending pods, so the pod wait
loop checks the PodGroup and parks the launch request while KAI queues it.
"""
import asyncio
import dataclasses
import math
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from sky import exceptions
from sky.adaptors import kubernetes
from sky.provision import constants as provision_constants
from sky.provision.kubernetes import constants as k8s_constants
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.server.requests import continue_condition
from sky.utils import common_utils

SCHEDULER_NAME = 'kai-scheduler'
QUEUE_LABEL = 'kai.scheduler/queue'
_PREEMPTIBILITY_LABEL = 'kai.scheduler/preemptibility'

_KAI_GROUP = 'scheduling.run.ai'
_QUEUE_API = (_KAI_GROUP, 'v2')
POD_GROUP_API = (_KAI_GROUP, 'v2alpha2')

# Maps a KAI quota reason to (requested resources field, Queue limit field).
_QUOTA_REASONS = {
    'OverLimit': ('podGroupRequestedResources', 'limit'),
    'NonPreemptibleOverQuota':
        ('podGroupRequestedNonPreemptibleResources', 'quota'),
}
_GPU_RESOURCE = 'nvidia.com/gpu'
_IMMUTABLE_SPEC_FIELDS = ('queue', 'priorityClassName', 'preemptibility')
_PARKED_POLL_SECONDS = 30
_WAIT_CHECK_SECONDS = 10

# Called by the pod wait loop with context, namespace, cluster name on cloud
# and cluster name.
SchedulingCallback = Callable[[Optional[str], str, str, str], None]


def _pod_group_body(name: str, node_config: Dict[str, Any],
                    count: int) -> Dict[str, Any]:
    labels = node_config['metadata'].get('labels', {})
    spec: Dict[str, Any] = {'minMember': count, 'queue': labels[QUEUE_LABEL]}
    priority_class = node_config['spec'].get('priorityClassName')
    if priority_class is not None:
        spec['priorityClassName'] = priority_class
    if _PREEMPTIBILITY_LABEL in labels:
        spec['preemptibility'] = labels[_PREEMPTIBILITY_LABEL]
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
                     cluster_name_on_cloud: str, node_config: Dict[str, Any],
                     count: int) -> None:
    """Creates the cluster's PodGroup, or resizes it for a running cluster.

    Raises:
        exceptions.ProvisionUnsupportedError: The running cluster's PodGroup has
            a different queue, priority class or preemptibility.
    """
    if 'deployment_spec' in node_config:
        raise exceptions.ProvisionUnsupportedError(
            'KAI Scheduler is not supported for high availability '
            'controllers.')
    body = _pod_group_body(cluster_name_on_cloud, node_config, count)
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
        if e.status != 409 or not live_pods:
            raise
        existing = _get_pod_group(context, namespace, cluster_name_on_cloud)
        if existing is None:
            raise
        # The running pods keep their settings, so the gang cannot change them.
        changed = [
            field for field in _IMMUTABLE_SPEC_FIELDS
            if existing['spec'].get(field) != body['spec'].get(field)
        ]
        if changed:
            raise exceptions.ProvisionUnsupportedError(
                f'Cannot change KAI {", ".join(changed)} of a running cluster. '
                'Run `sky down` first.') from e
        api.patch_namespaced_custom_object(
            *POD_GROUP_API,
            namespace,
            'podgroups',
            cluster_name_on_cloud, {'spec': {
                'minMember': count
            }},
            _request_timeout=kubernetes.API_TIMEOUT)


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


def _unbound_reasons(pod_group: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Returns KAI's reasons for not binding the gang, if it evaluated it."""
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
    while queue_name:
        queue = kubernetes.custom_objects_api(
            context).get_cluster_custom_object(
                *_QUEUE_API,
                'queues',
                queue_name,
                _request_timeout=kubernetes.API_TIMEOUT)
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


@dataclasses.dataclass(frozen=True)
class GangBound(continue_condition.ContinueCondition):
    """Resumes a parked launch once KAI binds its gang or rules it out."""

    context: Optional[str]
    namespace: str
    pod_group: str

    def _waiting_reason(self) -> Optional[str]:
        pod_group = _get_pod_group(self.context, self.namespace, self.pod_group)
        if pod_group is None:
            return None
        reasons = _unbound_reasons(pod_group)
        if not reasons or _never_fits(self.context, pod_group, reasons):
            return None
        return _format_reasons(reasons)

    async def wait_async(
        self,
        *,
        is_cancelled: Callable[[], Awaitable[bool]],
        fallback_wait_seconds: float,
        update_status_msg: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> bool:
        del fallback_wait_seconds  # Unused.
        loop = asyncio.get_running_loop()
        reported = None
        while not await is_cancelled():
            try:
                reason = await loop.run_in_executor(None, self._waiting_reason)
            except (kubernetes.api_exception(),
                    kubernetes.urllib3_http_error()) as e:
                if not kubernetes_utils.is_transport_error(e):
                    raise
                await asyncio.sleep(_PARKED_POLL_SECONDS)
                continue
            if reason is None:
                return True
            if update_status_msg is not None and reason != reported:
                await update_status_msg(reason)
                reported = reason
            await asyncio.sleep(_PARKED_POLL_SECONDS)
        return False


def scheduling_wait_checker() -> SchedulingCallback:
    """Returns a pod wait loop callback that checks the gang every 10s."""
    last_check: Optional[float] = None

    def check(context: Optional[str], namespace: str,
              cluster_name_on_cloud: str, cluster_name: str) -> None:
        nonlocal last_check
        now = time.monotonic()
        if last_check is not None and now - last_check < _WAIT_CHECK_SECONDS:
            return
        last_check = now
        _check_scheduling_wait(context, namespace, cluster_name_on_cloud,
                               cluster_name)

    return check


def _check_scheduling_wait(context: Optional[str], namespace: str,
                           cluster_name_on_cloud: str,
                           cluster_name: str) -> None:
    """Parks the launch if KAI queued the gang; fails it if it never fits.

    Raises:
        exceptions.ProvisionUnsupportedError: The gang can never be scheduled.
        exceptions.ExecutionPausedError: KAI queued the gang.
    """
    try:
        pod_group = _get_pod_group(context, namespace, cluster_name_on_cloud)
        if pod_group is None:
            return
        reasons = _unbound_reasons(pod_group)
        never_fits = bool(reasons) and _never_fits(context, pod_group, reasons)
    except (kubernetes.api_exception(), kubernetes.urllib3_http_error()) as e:
        if not kubernetes_utils.is_transport_error(e):
            raise
        return
    if not reasons:
        return
    if never_fits:
        raise exceptions.ProvisionUnsupportedError(
            f'KAI cannot schedule {cluster_name} in its queue: '
            f'{_format_reasons(reasons)}')
    if not common_utils.is_in_request_context():
        return
    raise exceptions.ExecutionPausedError(
        f'{cluster_name} is queued in KAI: {_format_reasons(reasons)}',
        hint=(f'The launch resumes when KAI schedules it. Run `sky down '
              f'{cluster_name}` to leave the queue.'),
        retry_wait_seconds=_PARKED_POLL_SECONDS,
        continue_condition=GangBound(context=context,
                                     namespace=namespace,
                                     pod_group=cluster_name_on_cloud))
