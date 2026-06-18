"""Shared helpers for debug dump data serialization.

These helpers are used by both sky.utils.debug_utils (API server side) and
sky.jobs.utils (controller side) to serialize cluster records and events.
Extracted to avoid a circular import:
  debug_utils -> jobs.server.core -> jobs.utils -> debug_utils
"""
import copy
import dataclasses
import datetime
import re
import traceback
from typing import Any, Dict, List, Optional, Tuple

from sky import global_user_state
from sky import sky_logging
from sky import task as task_lib
from sky.utils import config_utils
from sky.utils import subprocess_utils
from sky.utils import yaml_utils

logger = sky_logging.init_logger(__name__)

# Sensitive config paths to redact in debug dumps, following the same
# pattern as provision/common.py:ProvisionConfig.get_redacted_config().
_SENSITIVE_CONFIG_KEYS: List[Tuple[str, ...]] = [
    ('api_server', 'endpoint'),
    ('api_server', 'service_account_token'),
]

# Env var names whose values should always be redacted in debug dumps.
# Canonical home is here (not debug_utils) so the controller-side manifest
# can use the same list without recreating the circular import that this
# module exists to break.
SENSITIVE_ENV_VARS = frozenset({
    'SKYPILOT_DB_CONNECTION_URI',
    'SKYPILOT_INITIAL_BASIC_AUTH',
    'SKYPILOT_SERVICE_ACCOUNT_TOKEN',
    'SKYPILOT_DOCKER_PASSWORD',
    'AWS_SECRET_ACCESS_KEY',
    'AWS_SESSION_TOKEN',
    'AWS_ACCESS_KEY_ID',
    'AZURE_CLIENT_SECRET',
})

# Heuristic match for any env-var key likely to carry a secret. Used to
# catch user-defined keys we can't enumerate ahead of time.
_SENSITIVE_ENV_KEY_PATTERN = re.compile(
    r'(?i)(TOKEN|SECRET|KEY|PASSWORD|CREDENTIAL)')

# Label set on every SkyPilot pod by sky/templates/kubernetes-ray.yml.j2 and
# sky/provision/kubernetes/instance.py. The value is the cluster name on
# cloud, so this is the right selector for "all pods of this cluster."
_SKYPILOT_CLUSTER_LABEL = 'skypilot-cluster-name'


def redact_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of config with sensitive values replaced by '<redacted>'.

    Used by both the client (sdk.py) and the server (debug_utils.py) when
    including SkyPilot config in debug dumps.
    """
    config_copy = config_utils.Config(copy.deepcopy(config))
    for field_path in _SENSITIVE_CONFIG_KEYS:
        val = config_copy.get_nested(field_path, default_value=None)
        if val is not None:
            config_copy.set_nested(field_path, '<redacted>')
    return dict(**config_copy)


def epoch_to_human(epoch: Optional[float]) -> Optional[str]:
    """Convert epoch timestamp to human-readable ISO format."""
    if epoch is None:
        return None
    try:
        return datetime.datetime.fromtimestamp(epoch).isoformat()
    except (OSError, ValueError, OverflowError):
        return None


def redact_task_yaml(yaml_str: str) -> str:
    """Parse a task/dag YAML string and redact secrets and credentials.

    Shared by the API server dump (debug_utils.py) and the controller
    manifest (jobs/utils.py).
    """
    try:
        docs = list(yaml_utils.safe_load_all(yaml_str))
    except Exception:  # pylint: disable=broad-except
        return '<parse error, redacted>'
    for doc in docs:
        if isinstance(doc, dict):
            task_lib.redact_task_yaml_dict(doc)
    return yaml_utils.dump_yaml_str(docs)


def serialize_cluster_record(cluster_record: Dict[str, Any]) -> Dict[str, Any]:
    """Serialize a cluster DB record to a JSON-friendly dict.

    Shared by the API server dump (_dump_cluster_info in debug_utils.py) and
    the controller manifest (_collect_cluster_debug_manifest in jobs/utils.py).
    """
    handle = cluster_record.get('handle')
    handle_info: Dict[str, Any] = {}
    if handle:
        handle_info = {
            'cluster_name': getattr(handle, 'cluster_name', None),
            'cluster_name_on_cloud': getattr(handle, 'cluster_name_on_cloud',
                                             None),
            'head_ip': getattr(handle, 'head_ip', None),
            'launched_nodes': getattr(handle, 'launched_nodes', None),
            'launched_resources': str(
                getattr(handle, 'launched_resources', None)),
            'stable_internal_external_ips': getattr(
                handle, 'stable_internal_external_ips', None),
            'stable_ssh_ports': getattr(handle, 'stable_ssh_ports', None),
            'docker_user': getattr(handle, 'docker_user', None),
            'ssh_user': getattr(handle, 'ssh_user', None),
        }

    launched_at = cluster_record.get('launched_at')
    status_updated_at = cluster_record.get('status_updated_at')
    return {
        'name': cluster_record.get('name'),
        'cluster_hash': cluster_record.get('cluster_hash'),
        'status': str(cluster_record.get('status')),
        'launched_at': launched_at,
        'launched_at_human': epoch_to_human(launched_at),
        'autostop': cluster_record.get('autostop'),
        'to_down': cluster_record.get('to_down'),
        'cluster_ever_up': cluster_record.get('cluster_ever_up'),
        'status_updated_at': status_updated_at,
        'status_updated_at_human': epoch_to_human(status_updated_at),
        'config_hash': cluster_record.get('config_hash'),
        'workspace': cluster_record.get('workspace'),
        'is_managed': cluster_record.get('is_managed'),
        'user_hash': cluster_record.get('user_hash'),
        'user_name': cluster_record.get('user_name'),
        'last_use': cluster_record.get('last_use'),
        'owner': cluster_record.get('owner'),
        'metadata': cluster_record.get('metadata'),
        'last_creation_command': cluster_record.get('last_creation_command'),
        'last_creation_yaml':
            (redact_task_yaml(cluster_record['last_creation_yaml'])
             if cluster_record.get('last_creation_yaml') is not None else None),
        'last_event': cluster_record.get('last_event'),
        'handle': handle_info,
    }


@dataclasses.dataclass
class K8sAccess:
    """How to reach a SkyPilot cluster's pods on Kubernetes."""
    # None means use in-cluster auth.
    context: Optional[str]
    # None means search across all namespaces (used when cluster_yaml is
    # missing, e.g. during a failover transition).
    namespace: Optional[str]
    cluster_name_on_cloud: str


@dataclasses.dataclass
class KubernetesDumpResult:
    """The data we capture from Kubernetes for one cluster."""
    # pod name -> sanitized V1Pod dict.
    pods: Dict[str, Dict[str, Any]] = dataclasses.field(default_factory=dict)
    # pod name -> list of event dicts (most recent first, capped).
    events: Dict[str, List[Dict[str,
                                Any]]] = dataclasses.field(default_factory=dict)
    # (pod, container) -> log tail.
    logs: Dict[Tuple[str, str], str] = dataclasses.field(default_factory=dict)
    # (pod, container) -> previous container instance log tail.
    previous_logs: Dict[Tuple[str, str],
                        str] = dataclasses.field(default_factory=dict)
    errors: List[Dict[str, str]] = dataclasses.field(default_factory=list)


def append_error(errors: List[Dict[str, str]], component: str, resource: str,
                 exc: BaseException) -> None:
    """Append a structured error entry to a debug-dump errors list.

    Shared between the API-server dump path and the controller-side manifest
    so both sides record errors in the same shape.
    """
    errors.append({
        'component': component,
        'resource': resource,
        'error': str(exc),
        'traceback': traceback.format_exc(),
    })


def get_kubernetes_access(
        cluster_record: Dict[str, Any]) -> Optional[K8sAccess]:
    """Return how to reach the cluster's pods on k8s, or None.

    Pure: no network calls. Safe to invoke for every cluster in the dump,
    including ones not on Kubernetes.
    """
    # pylint: disable=import-outside-toplevel
    from sky import clouds
    handle = cluster_record.get('handle')
    if handle is None:
        return None
    launched_resources = getattr(handle, 'launched_resources', None)
    if launched_resources is None:
        return None
    cloud = getattr(launched_resources, 'cloud', None)
    if not isinstance(cloud, clouds.Kubernetes):
        return None
    cluster_name_on_cloud = getattr(handle, 'cluster_name_on_cloud', None)
    if not cluster_name_on_cloud:
        return None

    context: Optional[str] = None
    namespace: Optional[str] = None

    cluster_yaml = getattr(handle, 'cluster_yaml', None)
    if cluster_yaml is not None:
        try:
            yaml_dict = global_user_state.get_cluster_yaml_dict(cluster_yaml)
            provider_config = yaml_dict.get('provider', {})
            # pylint: disable=import-outside-toplevel
            from sky.provision.kubernetes import utils as k8s_utils
            context = k8s_utils.get_context_from_config(provider_config)
            namespace = k8s_utils.get_namespace_from_config(provider_config)
        except Exception as e:  # pylint: disable=broad-except
            # cluster_yaml might be gone (failover transition) or unreadable;
            # fall through to the region-as-context fallback below.
            logger.debug(f'k8s access: cluster_yaml unreadable for '
                         f'{cluster_record.get("name")!r}: {e}')

    if context is None and namespace is None:
        # Fall back to the kube context name stored in launched_resources
        # (set by the v9 state upgrade in cloud_vm_ray_backend.py). Namespace
        # stays None — the caller will search all namespaces and rely on the
        # per-cluster label being unique enough.
        # pylint: disable=import-outside-toplevel
        from sky.adaptors import kubernetes
        region = getattr(launched_resources, 'region', None)
        if region == kubernetes.in_cluster_context_name():
            context = None
        else:
            context = region

    return K8sAccess(context=context,
                     namespace=namespace,
                     cluster_name_on_cloud=cluster_name_on_cloud)


def _redact_pod_env(pod_dict: Dict[str, Any]) -> None:
    """Redact sensitive env-var values in-place on a sanitized pod dict.

    Walks containers + init_containers, replacing inline `env[].value` for
    any key in SENSITIVE_ENV_VARS or matching the heuristic regex. Does not
    touch `env[].valueFrom` references (those carry only names).
    """
    spec = pod_dict.get('spec')
    if not isinstance(spec, dict):
        return
    for key in ('containers', 'initContainers'):
        containers = spec.get(key)
        if not isinstance(containers, list):
            continue
        for container in containers:
            if not isinstance(container, dict):
                continue
            env = container.get('env')
            if not isinstance(env, list):
                continue
            for entry in env:
                if not isinstance(entry, dict):
                    continue
                name = entry.get('name')
                if 'value' not in entry or not isinstance(name, str):
                    continue
                if (name in SENSITIVE_ENV_VARS or
                        _SENSITIVE_ENV_KEY_PATTERN.search(name)):
                    entry['value'] = '<redacted>'


def _truncate_log(log: str, log_tail_bytes: int) -> str:
    encoded = log.encode('utf-8', errors='replace')
    if len(encoded) <= log_tail_bytes:
        return log
    truncated = encoded[-log_tail_bytes:].decode('utf-8', errors='replace')
    return ('[... truncated, showing last '
            f'{log_tail_bytes} bytes ...]\n') + truncated


def _container_last_terminated(pod_obj: Any, container_name: str) -> bool:
    """Return True if the named container's last_state shows a terminated
    instance — i.e. a previous-instance log is likely available."""
    statuses = []
    status = getattr(pod_obj, 'status', None)
    if status is not None:
        for attr in ('container_statuses', 'init_container_statuses'):
            v = getattr(status, attr, None)
            if v:
                statuses.extend(v)
    for cs in statuses:
        if getattr(cs, 'name', None) != container_name:
            continue
        last_state = getattr(cs, 'last_state', None)
        if last_state is None:
            return False
        return getattr(last_state, 'terminated', None) is not None
    return False


def collect_kubernetes_state(
    access: K8sAccess,
    *,
    log_tail_lines: int = 5000,
    log_tail_bytes: int = 1 * 1024 * 1024,
    max_events: int = 200,
    log_timeout_seconds: int = 30,
    max_workers: int = 4,
) -> KubernetesDumpResult:
    """Fetch pods, events, and container logs for a SkyPilot k8s cluster.

    Never raises; all failures are recorded in `result.errors`. Network
    calls happen here, all wrapped in try/except. Per-pod work runs with
    bounded parallelism to avoid hammering the apiserver.
    """
    result = KubernetesDumpResult()

    # Lazy import — keeps this module importable without the kubernetes
    # extra installed.
    try:
        # pylint: disable=import-outside-toplevel
        from sky.adaptors import kubernetes
    except Exception as e:  # pylint: disable=broad-except
        append_error(result.errors, 'clusters/kubernetes', 'import_adaptor', e)
        return result

    label_selector = f'{_SKYPILOT_CLUSTER_LABEL}={access.cluster_name_on_cloud}'

    # 1. Pod list.
    try:
        if access.namespace is None:
            pod_list = kubernetes.core_api(access.context).\
                list_pod_for_all_namespaces(
                    label_selector=label_selector,
                    _request_timeout=kubernetes.API_TIMEOUT)
        else:
            pod_list = kubernetes.core_api(access.context).list_namespaced_pod(
                access.namespace,
                label_selector=label_selector,
                _request_timeout=kubernetes.API_TIMEOUT)
    except Exception as e:  # pylint: disable=broad-except
        append_error(result.errors, 'clusters/kubernetes', 'list_pods', e)
        return result

    pods = list(pod_list.items)
    if not pods:
        return result

    # Sanitize + redact pod dicts upfront so we have the JSON form available.
    try:
        # pylint: disable=import-outside-toplevel
        from sky.adaptors import kubernetes as _k
        sanitizer = _k.api_client(access.context).sanitize_for_serialization
    except Exception as e:  # pylint: disable=broad-except
        append_error(result.errors, 'clusters/kubernetes', 'sanitizer', e)
        sanitizer = None  # type: ignore[assignment]

    # Build a list of work units for parallel fetching.
    @dataclasses.dataclass
    class _PodWork:
        pod_obj: Any
        pod_name: str
        namespace: str
        containers: List[str]
        init_containers: List[str]

    work: List[_PodWork] = []
    for pod in pods:
        pod_name = getattr(getattr(pod, 'metadata', None), 'name', None)
        pod_ns = (getattr(getattr(pod, 'metadata', None), 'namespace', None) or
                  access.namespace or 'default')
        if not pod_name:
            continue
        if sanitizer is not None:
            try:
                pod_dict = sanitizer(pod)
                if isinstance(pod_dict, dict):
                    _redact_pod_env(pod_dict)
                    result.pods[pod_name] = pod_dict
            except Exception as e:  # pylint: disable=broad-except
                append_error(result.errors, 'clusters/kubernetes',
                             f'sanitize/{pod_name}', e)
        spec = getattr(pod, 'spec', None)
        containers = [
            getattr(c, 'name', None)
            for c in (getattr(spec, 'containers', None) or [])
        ]
        init_containers = [
            getattr(c, 'name', None)
            for c in (getattr(spec, 'init_containers', None) or [])
        ]
        work.append(
            _PodWork(pod_obj=pod,
                     pod_name=pod_name,
                     namespace=pod_ns,
                     containers=[c for c in containers if c],
                     init_containers=[c for c in init_containers if c]))

    api_exception = kubernetes.api_exception()

    def _fetch_events(w: '_PodWork') -> None:
        try:
            events = kubernetes.core_api(access.context).list_namespaced_event(
                w.namespace,
                field_selector=(f'involvedObject.name={w.pod_name},'
                                'involvedObject.kind=Pod'),
                _request_timeout=kubernetes.API_TIMEOUT).items
        except Exception as e:  # pylint: disable=broad-except
            append_error(result.errors, 'clusters/kubernetes',
                         f'events/{w.pod_name}', e)
            return

        def _ts(ev: Any) -> float:
            t = (getattr(ev, 'last_timestamp', None) or
                 getattr(ev, 'event_time', None) or getattr(
                     getattr(ev, 'metadata', None), 'creation_timestamp', None))
            if t is None:
                return 0.0
            try:
                return t.timestamp()
            except Exception:  # pylint: disable=broad-except
                return 0.0

        events_sorted = sorted(events, key=_ts, reverse=True)[:max_events]
        try:
            result.events[w.pod_name] = ([sanitizer(e) for e in events_sorted]
                                         if sanitizer else [])
        except Exception as e:  # pylint: disable=broad-except
            append_error(result.errors, 'clusters/kubernetes',
                         f'events_serialize/{w.pod_name}', e)

    def _fetch_log(w: '_PodWork', container: str, previous: bool) -> None:
        try:
            log = kubernetes.core_api(access.context).read_namespaced_pod_log(
                name=w.pod_name,
                namespace=w.namespace,
                container=container,
                tail_lines=log_tail_lines,
                previous=previous,
                _request_timeout=log_timeout_seconds,
            )
        except api_exception as e:
            # 400 with "previous terminated container … not found" is fine
            # to drop silently when we asked for the previous instance.
            if previous and e.status == 400:
                logger.debug(f'No previous log for {w.pod_name}/{container}')
                return
            append_error(
                result.errors, 'clusters/kubernetes',
                f'log{".prev" if previous else ""}/{w.pod_name}/{container}', e)
            return
        except Exception as e:  # pylint: disable=broad-except
            append_error(
                result.errors, 'clusters/kubernetes',
                f'log{".prev" if previous else ""}/{w.pod_name}/{container}', e)
            return
        if not isinstance(log, str):
            return
        truncated = _truncate_log(log, log_tail_bytes)
        target = result.previous_logs if previous else result.logs
        target[(w.pod_name, container)] = truncated

    # Build the full list of tasks: events per pod + logs per (pod, container).
    # We mix events and logs in the same parallel pool to maximise concurrency.
    tasks: List[Tuple[str, Any]] = []
    for w in work:
        tasks.append(('events', w))
        for c in w.containers + w.init_containers:
            tasks.append(('log', (w, c, False)))
            if _container_last_terminated(w.pod_obj, c):
                tasks.append(('log', (w, c, True)))

    def _dispatch(task: Tuple[str, Any]) -> None:
        kind, payload = task
        if kind == 'events':
            _fetch_events(payload)
        else:
            w, c, previous = payload
            _fetch_log(w, c, previous)

    if tasks:
        try:
            subprocess_utils.run_in_parallel(_dispatch,
                                             tasks,
                                             num_threads=max_workers)
        except Exception as e:  # pylint: disable=broad-except
            append_error(result.errors, 'clusters/kubernetes', 'fetch', e)

    return result


def get_cluster_events_data(cluster_hash: str) -> List[Dict[str, Any]]:
    """Get cluster events for all event types.

    Returns a list of dicts with 'event_type' and 'events' keys for non-empty
    event types. Shared by the API server dump and the controller manifest.
    """
    results: List[Dict[str, Any]] = []
    for event_type in list(global_user_state.ClusterEventType):
        events = global_user_state.get_cluster_events(cluster_name=None,
                                                      cluster_hash=cluster_hash,
                                                      event_type=event_type,
                                                      include_timestamps=True)
        if events:
            results.append({
                'event_type': event_type.value.lower(),
                'events': events,
            })
    return results
