"""KAI Scheduler support for Kubernetes clusters.

When kubernetes.kai.queue is set, each cluster gets a KAI PodGroup so KAI
schedules its pods as a gang.
"""
import datetime
import re
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from sky import exceptions
from sky.adaptors import kubernetes
from sky.provision import constants as provision_constants
from sky.provision.kubernetes import constants as k8s_constants
from sky.provision.kubernetes import utils as kubernetes_utils

SCHEDULER_NAME = 'kai-scheduler'
QUEUE_LABEL = 'kai.scheduler/queue'
# Names the PodGroup a pod belongs to.
POD_GROUP_ANNOTATION = 'pod-group-name'

_KAI_GROUP = 'scheduling.run.ai'
POD_GROUP_API = (_KAI_GROUP, 'v2alpha2')
_TOPOLOGY_API = ('kai.scheduler', 'v1alpha1')
# provider_config key -> PodGroup topologyConstraint field.
_TOPOLOGY_LEVEL_FIELDS = (
    ('kai_required_topology_level', 'requiredTopologyLevel'),
    ('kai_preferred_topology_level', 'preferredTopologyLevel'),
)

_IMMUTABLE_SPEC_FIELDS = ('queue', 'priorityClassName', 'preemptibility',
                          'topologyConstraint')
_EVICTOR_ACTION_ANNOTATION = 'evictor-action-type'


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


def set_gang_size(context: Optional[str], namespace: str, name: str,
                  count: int) -> None:
    """Sets the PodGroup's minMember.

    KAI evicts a running gang that falls below minMember, so lower it before
    removing pods and raise it only once new pods are bound.
    """
    kubernetes.custom_objects_api(context).patch_namespaced_custom_object(
        *POD_GROUP_API,
        namespace,
        'podgroups',
        name,
        body={'spec': {
            'minMember': count
        }},
        _request_timeout=kubernetes.API_TIMEOUT)


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
        # Its size can: a resize sets minMember through set_gang_size.
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


class Eviction(NamedTuple):
    """An Evict event KAI recorded for a pod."""
    # preempt, reclaim, consolidation or stalegangeviction; None if unset.
    action: Optional[str]
    # The event's first line, which names the pod. A reclaim adds lines
    # describing the queues.
    message: str


def find_eviction(context: Optional[str], namespace: str, pod_names: List[str],
                  since: Optional[datetime.datetime]) -> Optional[Eviction]:
    """KAI's eviction of the first of ``pod_names`` it evicted; None if it
    evicted none of them or the API cannot say.

    KAI deletes the pods it evicts and records why only as an Evict event on
    the PodGroup, which outlives the pod. ``since`` is where the pods' own
    history starts; an earlier eviction was of another pod of the same name.
    """
    try:
        events = kubernetes_utils.events_since(
            kubernetes.core_api(context).list_namespaced_event(
                namespace,
                field_selector='involvedObject.kind=PodGroup,reason=Evict',
                _request_timeout=kubernetes.API_TIMEOUT).items, since)
        # PodGroup name -> its current UID, None if it is gone.
        pod_group_uids: Dict[str, Optional[str]] = {}
        for pod_name in pod_names:
            # The pod name ends where the name characters end, so worker1
            # does not match worker10.
            pod_ref = re.compile(
                rf'(?<![\w.-]){re.escape(f"{namespace}/{pod_name}")}'
                r'(?![\w-]|\.[\w-])')
            for event in events:
                summary = (event.message or '').split('\n', 1)[0].strip()
                if not pod_ref.search(summary):
                    continue
                # A PodGroup of the same name from an earlier launch has its
                # own UID, and its evictions are not this pod's.
                name = event.involved_object.name
                if name not in pod_group_uids:
                    pod_group = _get_pod_group(context, namespace, name)
                    pod_group_uids[name] = (None if pod_group is None else
                                            pod_group['metadata']['uid'])
                if pod_group_uids[name] != event.involved_object.uid:
                    continue
                return Eviction((event.metadata.annotations or
                                 {}).get(_EVICTOR_ACTION_ANNOTATION), summary)
    except (kubernetes.api_exception(), kubernetes.urllib3_http_error()):
        return None
    return None


def eviction_reason(context: Optional[str], namespace: str, pod_name: str,
                    since: Optional[datetime.datetime]) -> Optional[str]:
    """Why KAI evicted the pod; None if it did not or the API cannot say.

    ``since`` is when the pod was created, which an eviction of the same pod
    name cannot precede.
    """
    eviction = find_eviction(context, namespace, [pod_name], since)
    if eviction is None:
        return None
    # Lowercase so it does not read as a kubelet 'Evicted' and earn its hint.
    label = (f'KAI evicted the pod ({eviction.action})'
             if eviction.action else 'KAI evicted the pod')
    return f'{label}: {eviction.message.rstrip(".")}'
