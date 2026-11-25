"""Best-effort collection of Kubernetes resources for debug dumps.

When the API server dumps a cluster that runs on Kubernetes, we also snapshot
the related k8s objects so an operator can inspect them like
``kubectl get -o yaml`` without needing kubectl access to the user's cluster.
There are two entry points, split by scope:

- ``dump_cluster_resources`` -- a single SkyPilot cluster's own resources (pods,
  events, the Services/Deployments/etc. it created, and its 1:1 Kueue Workload).
  Namespace-scoped, so it works even under minimal (namespace-only) RBAC.
- ``dump_context_resources`` -- the cluster-WIDE infra that is *not* tied to any
  one SkyPilot cluster (GPU-metrics pods, the non-Workload Kueue objects). The
  caller fetches this once per unique kube context, not once per cluster. These
  are cluster-wide API calls, so they need broader RBAC and are best-effort.

This module is intentionally provider-specific and self-contained: the generic
dump path (``sky.utils.debug_utils``) discovers the k8s coordinates and
delegates the API queries here. Every query is best-effort -- a failure is
recorded and returned, never raised, so one missing or inaccessible object
can't abort the surrounding dump.
"""
import json
import os
import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple

from sky import exceptions
from sky import sky_logging
from sky.adaptors import kubernetes
from sky.provision import constants as provision_constants
from sky.provision.kubernetes import constants as k8s_constants
from sky.provision.kubernetes import kai
from sky.provision.kubernetes import reads
from sky.utils import yaml_utils

logger = sky_logging.init_logger(__name__)

# Kueue groups SkyPilot's pods (labeled kueue.x-k8s.io/pod-group-name=<cluster
# name on cloud> by templates/kubernetes-ray.yml.j2) into one Workload, which it
# names after that pod-group -- i.e. the Workload's metadata.name *is* the
# cluster's on-cloud name. The Workload itself carries no per-cluster label, so
# we select it by name (matching how SkyPilot's Kueue integration locates it).
# Kueue CRD group + the versions to try newest-first for the custom objects
# API. Kueue is currently on v1beta2 (was v1beta1); trying newest-first lets us
# work across the Kueue versions OSS users run. An unserved version 404s and we
# fall through; prepend 'v1' here when Kueue graduates. See
# _resolve_kueue_version.
_KUEUE_GROUP = 'kueue.x-k8s.io'
_KUEUE_VERSIONS = ('v1beta2', 'v1beta1')
_KUEUE_WORKLOADS_PLURAL = 'workloads'
# The Kueue queue-name label (k8s_constants.KUEUE_QUEUE_NAME_LABEL) is the
# reliable "this cluster is using Kueue" signal -- more reliable than a
# Workload, which a using-Kueue cluster can transiently lack (creation race, GC,
# pod-integration off).

_KAI_POD_GROUPS = reads.Resource(group=kai.POD_GROUP_API[0],
                                 version=kai.POD_GROUP_API[1],
                                 plural='podgroups',
                                 namespaced=True)

# GPU-metrics observability pods, useful for debugging a cluster's GPU metrics
# (see the API server GPU-metrics setup docs). These are cluster-wide infra, not
# per-SkyPilot-cluster, so they're dumped whenever present. The metrics
# Prometheus is installed as helm release `skypilot-prometheus` in the
# `skypilot` namespace, so its server pods are named `skypilot-prometheus-server
# -*`; the chart's kube-state-metrics pod (the source of `kube_pod_labels`, the
# other half of the DCGM<->pod join) carries `kube-state-metrics` in its name.
# DCGM exporter's namespace varies by installer, so it's matched by name
# substring. See _dump_gpu_metrics_pods for the full object set (pods, the
# prometheus-server container logs, the prometheus config, the PVC, and the
# DCGM DaemonSet).
_GPU_METRICS_NAMESPACE = 'skypilot'
_PROMETHEUS_SERVER_NAME_PREFIX = 'skypilot-prometheus-server'
# The Prometheus container within the server pod (the chart runs a
# `configmap-reload` sidecar alongside it); we collect this container's logs.
_PROMETHEUS_SERVER_CONTAINER = 'prometheus-server'
# Tail bound for the collected container logs -- enough to hold a full WAL
# replay (roughly one line per segment) plus startup, without letting a chatty
# or long-lived server bloat the dump.
_PROMETHEUS_LOG_TAIL_LINES = 2000
_KUBE_STATE_METRICS_NAME_SUBSTR = 'kube-state-metrics'
_DCGM_EXPORTER_NAME_SUBSTR = 'dcgm-exporter'

# Keys in a serialized Secret whose values must never leave the cluster.
_SECRET_REDACTED_KEYS = ('data', 'stringData')
# `kubectl apply` records the full last-applied manifest in this annotation --
# for a Secret that includes its data/stringData verbatim (stringData even in
# plaintext). SkyPilot creates Secrets via the API, not `kubectl apply`, so its
# Secrets won't carry it, but we redact it so the dump never leaks values
# regardless of how a labeled Secret was created.
_LAST_APPLIED_ANNOTATION = 'kubectl.kubernetes.io/last-applied-configuration'
_REDACTED = '<redacted>'


def dump_cluster_resources(context: Optional[str], namespace: str,
                           cluster_name_on_cloud: str,
                           output_dir: str) -> List[Dict[str, str]]:
    """Snapshot a SkyPilot cluster's *own* Kubernetes resources into output_dir.

    Per-cluster and entirely namespace-scoped (works under minimal RBAC). Writes
    (best-effort, skipping anything that doesn't exist)::

        <output_dir>/
          pods/<pod>.yaml      # full pod spec + status
          events/<pod>.yaml    # events involving each pod
          services.yaml        # resources carrying the skypilot-cluster-name
          deployments.yaml     #   label -- i.e. what SkyPilot created during
          ...                  #   instance creation (PVCs, ConfigMaps,
          secrets.yaml         #   Secrets [redacted], Ingresses)
          kueue/workloads.yaml # this cluster's Kueue Workload, if it uses Kueue
          kai/podgroups.yaml   # this cluster's KAI PodGroup, if it uses KAI

    Cluster-WIDE k8s info (GPU-metrics pods, the non-Workload Kueue objects) is
    not tied to one cluster; it's collected once per kube context by
    ``dump_context_resources``, not here.

    Args:
        context: kube context the cluster lives in (None = current context).
        namespace: namespace the cluster's resources live in.
        cluster_name_on_cloud: value of the ``skypilot-cluster-name`` label,
            used to select this cluster's pods and the resources SkyPilot
            created for it.
        output_dir: directory to write the resource files into.

    Returns:
        A list of error records, each ``{'resource', 'error', 'traceback'}``,
        for queries that failed. ``resource`` is dump-relative (e.g.
        ``kubernetes/pods/<pod>``) so the caller can prefix it with the cluster
        name. Empty if everything succeeded (or simply didn't exist).
    """
    errors: List[Dict[str, str]] = []
    os.makedirs(output_dir, exist_ok=True)
    _fail_fast(kubernetes.api_client(context))

    pods, reachable = _dump_pods(context, namespace, cluster_name_on_cloud,
                                 output_dir, errors)
    if not reachable:
        # The context is defunct (the pod list couldn't connect). Skip the
        # remaining per-cluster calls rather than stacking one timeout each.
        return errors
    _dump_labeled_resources(context, namespace, cluster_name_on_cloud,
                            output_dir, errors)
    # Only dump the Workload if this cluster is actually using Kueue -- its pods
    # carry the queue-name label, which is the same signal Kueue uses to decide
    # whether to manage a pod, so the two conditions coincide. (A using-Kueue
    # cluster can transiently lack a Workload, so the label -- not the Workload
    # -- is the reliable signal.) Caveat: a cluster with Kueue's non-default
    # manageJobsWithoutQueueName=true would have even its unlabeled pods managed
    # by Kueue; this gate would then skip it. We accept that to avoid noise in
    # the common case.
    uses_kueue = any(
        k8s_constants.KUEUE_QUEUE_NAME_LABEL in _labels(pod) for pod in pods)
    if uses_kueue:
        _dump_workload(context, namespace, cluster_name_on_cloud, output_dir,
                       errors)
    uses_kai = any(kai.QUEUE_LABEL in _labels(pod) for pod in pods)
    if uses_kai:
        _dump_pod_group(context, namespace, cluster_name_on_cloud, output_dir,
                        errors)
    return errors


def dump_context_resources(context: Optional[str],
                           output_dir: str) -> List[Dict[str, str]]:
    """Snapshot cluster-WIDE k8s infra for a kube context into output_dir.

    These objects are shared by every SkyPilot cluster on the context, so the
    caller fetches them once per unique context (not per cluster). Writes
    (best-effort)::

        <output_dir>/
          context.json         # always: {context, reachable} -- so even a
                               # bare or unreachable context stays visible
          gpu_metrics/         # if present:
            prometheus-server.yaml
            <pod>.log  <pod>.previous.log  # prometheus-server container logs
            dcgm-exporter.yaml
          kueue/               # if Kueue is installed on the context:
            localqueues.yaml  clusterqueues.yaml
            resourceflavors.yaml  topologies.yaml

    These are cluster-wide API calls (a pod list across all namespaces /
    cluster-scoped custom-object lists), so they need broader RBAC than the
    per-cluster path and are best-effort. The first call doubles as a
    reachability gate: a connection/timeout error means the context is defunct,
    so we record it and skip the rest (see ``_dump_gpu_metrics_pods``) rather
    than stacking timeouts.

    Returns error records with ``resource`` relative to ``output_dir`` (e.g.
    ``gpu_metrics/prometheus-server``) for the caller to prefix with the
    context.
    """
    errors: List[Dict[str, str]] = []
    os.makedirs(output_dir, exist_ok=True)
    _fail_fast(kubernetes.api_client(context))

    reachable = _dump_gpu_metrics_pods(context, output_dir, errors)
    # If the context's first cluster-wide call failed to even connect, it's
    # defunct -- skip the Kueue calls too rather than racking up more timeouts.
    if reachable:
        _dump_kueue_cluster_objects(context, output_dir, errors)

    # Always drop a marker recording the context + whether it was reachable.
    # Without it, a reachable context with no cluster-wide infra (or an
    # unreachable one) leaves an empty dir, which the dump's zip omits -- so a
    # reader couldn't tell "checked, nothing here" from "never checked". The
    # marker guarantees every context the dump touched is visible.
    try:
        with open(os.path.join(output_dir, 'context.json'),
                  'w',
                  encoding='utf-8') as f:
            json.dump({
                'context': context,
                'reachable': reachable
            },
                      f,
                      indent=2,
                      default=str)
    except OSError as e:
        _record_error(errors, 'context.json', e)
    return errors


def context_reachable(context: Optional[str]) -> bool:
    """Return whether a kube context's API server answers a bounded probe.

    One no-retry, ``API_TIMEOUT``-bounded pod list against the
    ``default`` namespace (``limit=1`` -- we only care whether the API server
    answers, not what it returns). This is the same fast-fail signal the
    per-cluster/per-context resource collection computes internally (see
    ``_dump_pods`` / ``_dump_gpu_metrics_pods``), exposed standalone so other
    debug-dump collectors can gate themselves on context reachability without
    racking up their own timeouts.

    Why this matters for the skylet log: ``_resolve_remote_skylet_log_path``
    runs a remote command via a ``KubernetesCommandRunner``, whose
    ``connect_timeout`` maps to ``kubectl --pod-running-timeout`` -- which only
    bounds waiting for the pod to reach Running, a phase *after* kubectl reaches
    the API server. Against a defunct API server kubectl never gets that far and
    falls back to client-go defaults (~2min before giving up). A reachability
    probe via the Python client (which honours ``_request_timeout`` + the
    ``_fail_fast`` no-retry pool) lets the caller skip skylet collection on a
    dead context in ~``API_TIMEOUT`` seconds instead.

    Best-effort and fail-safe: returns ``False`` only when no answer arrived
    (connection error, timeout, or a context known to be unreachable), which is
    the unreachable signal we act on. Any other error (e.g. RBAC 403, or
    unexpected client internals) returns ``True`` -- the context is reachable,
    just restricted -- so we never wrongly skip collection for a live cluster
    on a transient hiccup.
    """
    try:
        _fail_fast(kubernetes.api_client(context))
        _list(context, reads.PODS, namespace='default', limit=1)
        return True
    except Exception as e:  # pylint: disable=broad-except
        if _unreachable(e):
            logger.debug(f'Kubernetes context {context!r} is unreachable; '
                         f'callers gating on reachability will skip it.')
            return False
        # Reachable but the probe failed for another reason (RBAC, etc.). Treat
        # as reachable so we don't wrongly skip a live cluster.
        logger.debug(f'Reachability probe for context {context!r} failed with '
                     f'a non-connection error; treating as reachable: {e}')
        return True


def _record_error(errors: List[Dict[str, str]], resource: str,
                  e: Exception) -> None:
    logger.debug(f'Failed to collect {resource}: {e}')
    errors.append({
        'resource': resource,
        'error': str(e),
        'traceback': traceback.format_exc(),
    })


def _fail_fast(api: Any) -> Any:
    """Disable urllib3 connection retries on a freshly-built k8s client.

    ``_request_timeout`` bounds a single connect attempt, but urllib3 retries a
    failed connection ~3x by default -- so against a defunct context each call
    takes ~4x the timeout (~20s for a 5s timeout), and the per-cluster path
    stacks several such calls into minutes. These are best-effort diagnostic
    reads where a connection failure should fail fast, not be retried, so we set
    retries=0 on the client's connection pool (debug-only -- real API calls keep
    their retries). Mutating ``connection_pool_kw`` takes effect because the
    per-host pool is created lazily on the first request. Best-effort: if the
    client internals differ, we leave retries as-is rather than crash.

    The typed APIs and ``kubernetes.api_client`` (which the read layer uses)
    each hold their own ApiClient, so each needs this call.

    Returns ``api`` for call-site chaining, e.g.
    ``_fail_fast(kubernetes.core_api(context))``.
    """
    client = getattr(api, 'api_client', api)
    try:
        client.rest_client.pool_manager.connection_pool_kw['retries'] = 0
    except AttributeError:
        pass
    return api


def _list(context: Optional[str], resource: reads.Resource,
          **kwargs: Any) -> List[Dict[str, Any]]:
    """One API server list of resource; see reads.list_objects."""
    return reads.list_objects(context,
                              resource,
                              consistency=reads.FRESH,
                              attempts=1,
                              **kwargs).items


def _get(context: Optional[str], resource: reads.Resource, name: str,
         namespace: str) -> Optional[Dict[str, Any]]:
    """One API server get of an object; None if it is absent."""
    items = reads.get_object(context,
                             resource,
                             name,
                             namespace=namespace,
                             consistency=reads.FRESH,
                             attempts=1).items
    return items[0] if items else None


def _unreachable(e: Exception) -> bool:
    """Whether a read failed without an answer from the API server."""
    return (isinstance(e, exceptions.KubeAPIUnreachableError) and
            reads.http_status(e) is None)


def _name(obj: Dict[str, Any]) -> str:
    return (obj.get('metadata') or {}).get('name') or ''


def _labels(obj: Dict[str, Any]) -> Dict[str, str]:
    return (obj.get('metadata') or {}).get('labels') or {}


def _write_yaml(path: str, obj: Any) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        f.write(yaml_utils.dump_yaml_str(obj))


def _write_text(path: str, text: str) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def _strip_managed_fields(obj_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Return a serialized object without metadata.managedFields.

    managedFields is verbose server-side-apply bookkeeping that ``kubectl get
    -o yaml`` hides by default; it's pure noise in a debug dump and can inflate
    each object several-fold. ``obj_dict`` is not modified: read results may
    be shared with other readers.
    """
    metadata = obj_dict.get('metadata')
    if not isinstance(metadata, dict) or 'managedFields' not in metadata:
        return obj_dict
    return {
        **obj_dict, 'metadata': {
            k: v for k, v in metadata.items() if k != 'managedFields'
        }
    }


def _with_type_meta(obj_dict: Dict[str, Any], api_version: str,
                    kind: str) -> Dict[str, Any]:
    """Prepend apiVersion/kind to a serialized object.

    Kubernetes populates TypeMeta (apiVersion/kind) only on the List wrapper,
    not on the individual items returned by a list, so objects we collect via
    a list lack the ``kind:``/``apiVersion:`` header that a ``kubectl get -o
    yaml`` shows (objects fetched via a GET already carry it). We know the
    type from the call we made, so stamp it on. They go first because
    dump_yaml_str preserves insertion order (it does not sort keys), and
    apiVersion/kind conventionally lead a manifest.
    """
    rest = {
        k: v for k, v in obj_dict.items() if k not in ('apiVersion', 'kind')
    }
    return {'apiVersion': api_version, 'kind': kind, **rest}


def _dump_pods(
        context: Optional[str], namespace: str, cluster_name_on_cloud: str,
        output_dir: str,
        errors: List[Dict[str, str]]) -> Tuple[List[Dict[str, Any]], bool]:
    """Dump the cluster's pods (spec/status) and the events involving them.

    Both come from a single ``list`` call each -- pods by the
    skypilot-cluster-name label, events by a namespace-wide pod-event query
    filtered to those pods -- so this is two API calls regardless of node count,
    rather than one (or two) per pod. List items carry no TypeMeta, so
    apiVersion/kind are stamped back on (see _with_type_meta).

    Returns ``(pods, reachable)``. ``pods`` lets the caller inspect labels (e.g.
    to decide whether the cluster is using Kueue). ``reachable`` is False only
    when the pod list -- the first call against the context -- failed to even
    connect (a defunct context); the caller uses it to skip this cluster's
    remaining calls rather than stacking timeouts. Any other failure (e.g. RBAC)
    leaves ``reachable`` True so the rest of the dump still proceeds.
    """
    label_selector = (f'{provision_constants.TAG_SKYPILOT_CLUSTER_NAME}='
                      f'{cluster_name_on_cloud}')

    # Pods: one list call by label gets the head + all workers. This is the
    # first call against the context, so it doubles as the reachability gate.
    pods: List[Dict[str, Any]] = []
    try:
        pods = _list(context,
                     reads.PODS,
                     namespace=namespace,
                     label_selector=label_selector)
    except Exception as e:  # pylint: disable=broad-except
        _record_error(errors, 'kubernetes/pods', e)
        if _unreachable(e):
            # Couldn't connect -- the context is defunct. Signal the caller to
            # skip this cluster's remaining calls rather than stack timeouts.
            return [], False
    if pods:
        pods_dir = os.path.join(output_dir, 'pods')
        os.makedirs(pods_dir, exist_ok=True)
        for pod in pods:
            _write_yaml(
                os.path.join(pods_dir, f'{_name(pod)}.yaml'),
                _with_type_meta(_strip_managed_fields(pod), 'v1', 'Pod'))

    # Events: one namespace-wide query for pod events (server-side narrowed to
    # Pods), filtered client-side to this cluster's pods and grouped per pod --
    # there's no field selector for "name in (...)", and a per-pod query would
    # be one call per pod.
    pod_names = {_name(pod) for pod in pods}
    if not pod_names:
        return pods, True
    try:
        events = _list(context,
                       reads.EVENTS,
                       namespace=namespace,
                       field_selector='involvedObject.kind=Pod')
    except Exception as e:  # pylint: disable=broad-except
        _record_error(errors, 'kubernetes/events', e)
        return pods, True

    events_by_pod: Dict[str, List[Dict[str, Any]]] = {}
    for event in events:
        name = (event.get('involvedObject') or {}).get('name')
        if name in pod_names:
            events_by_pod.setdefault(name, []).append(event)
    if events_by_pod:
        events_dir = os.path.join(output_dir, 'events')
        os.makedirs(events_dir, exist_ok=True)
        for name, pod_events in events_by_pod.items():
            _write_yaml(os.path.join(events_dir, f'{name}.yaml'), [
                _with_type_meta(_strip_managed_fields(ev), 'v1', 'Event')
                for ev in pod_events
            ])
    return pods, True


def _dump_labeled_resources(context: Optional[str], namespace: str,
                            cluster_name_on_cloud: str, output_dir: str,
                            errors: List[Dict[str, str]]) -> None:
    """Dump the resources SkyPilot created for this cluster.

    Everything SkyPilot creates during instance creation carries the
    ``skypilot-cluster-name`` label (it's how teardown finds them), so a single
    label selector covers Services and any other kinds we create now or later.
    """
    label_selector = (f'{provision_constants.TAG_SKYPILOT_CLUSTER_NAME}='
                      f'{cluster_name_on_cloud}')

    # (filename, apiVersion, kind, resource) for the kinds SkyPilot may create.
    # Listing a kind we didn't create just returns an empty list, so an
    # over-broad sweep is safe and future-proofs us against new resource kinds.
    # The apiVersion/kind are stamped onto each object (see _with_type_meta):
    # items from a list carry no TypeMeta, so without this the dump wouldn't
    # match `kubectl get -o yaml`.
    kinds: List[Tuple[str, str, str, reads.Resource]] = [
        ('services.yaml', 'v1', 'Service', reads.SERVICES),
        ('deployments.yaml', 'apps/v1', 'Deployment', reads.DEPLOYMENTS),
        ('persistent_volume_claims.yaml', 'v1', 'PersistentVolumeClaim',
         reads.PERSISTENT_VOLUME_CLAIMS),
        ('config_maps.yaml', 'v1', 'ConfigMap', reads.CONFIG_MAPS),
        ('ingresses.yaml', 'networking.k8s.io/v1', 'Ingress', reads.INGRESSES),
    ]
    for filename, api_version, kind, resource in kinds:
        try:
            items = _list(context,
                          resource,
                          namespace=namespace,
                          label_selector=label_selector)
            if not items:
                continue
            _write_yaml(os.path.join(output_dir, filename), [
                _with_type_meta(_strip_managed_fields(item), api_version, kind)
                for item in items
            ])
        except Exception as e:  # pylint: disable=broad-except
            _record_error(errors, f'kubernetes/{filename}', e)

    # Secrets are dumped separately so their values can be redacted -- we want
    # the metadata (which secrets exist, their keys) for debugging, never the
    # contents.
    try:
        secrets = _list(context,
                        reads.SECRETS,
                        namespace=namespace,
                        label_selector=label_selector)
        if secrets:
            redacted = []
            for secret in secrets:
                secret_dict = _with_type_meta(_strip_managed_fields(secret),
                                              'v1', 'Secret')
                for key in _SECRET_REDACTED_KEYS:
                    values = secret_dict.get(key)
                    if values:
                        secret_dict[key] = {k: _REDACTED for k in values}
                # The last-applied-configuration annotation embeds the data, so
                # redact it too (see _LAST_APPLIED_ANNOTATION). Copies, since
                # the read's objects may be shared.
                metadata = secret_dict.get('metadata') or {}
                annotations = metadata.get('annotations')
                if annotations and _LAST_APPLIED_ANNOTATION in annotations:
                    secret_dict['metadata'] = {
                        **metadata, 'annotations': {
                            **annotations, _LAST_APPLIED_ANNOTATION: _REDACTED
                        }
                    }
                redacted.append(secret_dict)
            _write_yaml(os.path.join(output_dir, 'secrets.yaml'), redacted)
    except Exception as e:  # pylint: disable=broad-except
        _record_error(errors, 'kubernetes/secrets', e)


def _dump_gpu_metrics_pods(context: Optional[str], output_dir: str,
                           errors: List[Dict[str, str]]) -> bool:
    """Dump the GPU-metrics observability pods, if present.

    Into ``<output_dir>/gpu_metrics/``:
      - prometheus-server: the metrics Prometheus server pod (helm release
        ``skypilot-prometheus`` in the ``skypilot`` namespace).
      - ``<pod>.log`` / ``<pod>.previous.log``: the prometheus-server
        container's own logs (current and, if it has restarted, the previous
        instance) -- the WAL-replay progress and startup panics the pod
        object's restart *reason* alone can't show.
      - kube-state-metrics: the chart's KSM pod -- the source of
        ``kube_pod_labels``, the other half of the DCGM<->pod join.
      - dcgm-exporter: the DCGM exporter pods, matched by name substring across
        all namespaces (the namespace varies by installer).
      - prometheus-config: the rendered ``prometheus.yml`` ConfigMap -- scrape
        intervals/timeouts, the KSM scrape job, and the federation match.
      - prometheus-pvcs: the Prometheus PVC(s) -- storage/disk-bound failures.
      - dcgm-exporter-daemonset: the DCGM exporter DaemonSet -- its
        desired-vs-ready and tolerations show why some GPU nodes report no
        metrics.

    These are cluster-wide infra (not per-SkyPilot-cluster), so they're dumped
    whenever present -- an absent component just yields no file. Both are found
    from a single cluster-wide pod list (DCGM's namespace varies, so it can't be
    scoped, and we reuse that list for Prometheus too). Best-effort.

    Returns whether the context was *reachable*: False if the (first
    cluster-wide) list call timed out / couldn't connect -- the caller uses this
    to skip further cluster-wide calls on a defunct context. A reachable context
    that errors for another reason (e.g. RBAC 403) still returns True.
    """
    try:
        all_pods = _list(context, reads.PODS)
    except Exception as e:  # pylint: disable=broad-except
        _record_error(errors, 'gpu_metrics', e)
        # No answer means the context is likely defunct: signal the caller to
        # skip the remaining cluster-wide calls rather than stack timeouts. An
        # answered failure (e.g. RBAC 403) leaves it reachable.
        return not _unreachable(e)

    def _namespace(obj: Dict[str, Any]) -> Optional[str]:
        return (obj.get('metadata') or {}).get('namespace')

    prom_pods = [
        p for p in all_pods if _namespace(p) == _GPU_METRICS_NAMESPACE and
        _name(p).startswith(_PROMETHEUS_SERVER_NAME_PREFIX)
    ]
    ksm_pods = [
        p for p in all_pods if _namespace(p) == _GPU_METRICS_NAMESPACE and
        _KUBE_STATE_METRICS_NAME_SUBSTR in _name(p)
    ]
    dcgm_pods = [p for p in all_pods if _DCGM_EXPORTER_NAME_SUBSTR in _name(p)]
    out_dir = os.path.join(output_dir, 'gpu_metrics')
    for filename, matched in [('prometheus-server.yaml', prom_pods),
                              ('kube-state-metrics.yaml', ksm_pods),
                              ('dcgm-exporter.yaml', dcgm_pods)]:
        if matched:
            os.makedirs(out_dir, exist_ok=True)
            _write_yaml(os.path.join(out_dir, filename), [
                _with_type_meta(_strip_managed_fields(p), 'v1', 'Pod')
                for p in matched
            ])

    # The prometheus-server container's own logs -- the only place the WAL
    # replay progress ("Replaying WAL...", per-segment loads) and any
    # panic/abort message surface; the pod object shows *that* it restarted,
    # not *why* it died mid-startup. Grab the current container's tail and, when
    # it has restarted, the previous (terminated) container's logs too -- the
    # latter holds the boot-to-crash output of the instance that actually
    # failed. Best-effort and tail-bounded.
    core = _fail_fast(kubernetes.core_api(context))
    for pod in prom_pods:
        pod_name = _name(pod)
        current_log = None
        for previous in (False, True):
            suffix = '.previous' if previous else ''
            resource = f'gpu_metrics/{pod_name}{suffix}.log'
            try:
                pod_log = core.read_namespaced_pod_log(
                    name=pod_name,
                    namespace=_GPU_METRICS_NAMESPACE,
                    container=_PROMETHEUS_SERVER_CONTAINER,
                    previous=previous,
                    tail_lines=_PROMETHEUS_LOG_TAIL_LINES,
                    _request_timeout=kubernetes.API_TIMEOUT)
                if not pod_log:
                    continue
                if not previous:
                    current_log = pod_log
                elif pod_log == current_log:
                    # In a fast crash-loop the two reads can land on the same
                    # container instance: mid-CrashLoopBackOff (no running
                    # container) kubelet resolves both previous=False and
                    # previous=True to the last-terminated instance, and a
                    # restart can also fall between the reads. Drop a previous
                    # log that's a byte-for-byte duplicate of the current one --
                    # it adds nothing.
                    continue
                # Keep the write inside the try so a local OSError (full disk,
                # permissions) is recorded best-effort, not propagated.
                os.makedirs(out_dir, exist_ok=True)
                _write_text(os.path.join(out_dir, f'{pod_name}{suffix}.log'),
                            pod_log)
            except kubernetes.api_exception() as e:
                # A 400 on previous=True just means the container has never
                # restarted (no previous instance) -- expected, not an error.
                if not (previous and e.status == 400):
                    _record_error(errors, resource, e)
                continue
            except Exception as e:  # pylint: disable=broad-except
                _record_error(errors, resource, e)
                continue

    # The config + topology behind the pods. The prometheus.yml ConfigMap and
    # the Prometheus PVC(s) are namespace-scoped (minimal RBAC); the
    # DCGM-exporter DaemonSet is cluster-wide. All best-effort -- absent or
    # forbidden just yields no file (a missing ConfigMap is treated as absent).
    try:
        cm = _get(context,
                  reads.CONFIG_MAPS,
                  _PROMETHEUS_SERVER_NAME_PREFIX,
                  namespace=_GPU_METRICS_NAMESPACE)
        if cm is not None:
            os.makedirs(out_dir, exist_ok=True)
            _write_yaml(
                os.path.join(out_dir, 'prometheus-config.yaml'),
                _with_type_meta(_strip_managed_fields(cm), 'v1', 'ConfigMap'))
    except Exception as e:  # pylint: disable=broad-except
        _record_error(errors, 'gpu_metrics/prometheus-config', e)

    try:
        pvcs = _list(context,
                     reads.PERSISTENT_VOLUME_CLAIMS,
                     namespace=_GPU_METRICS_NAMESPACE)
        if pvcs:
            os.makedirs(out_dir, exist_ok=True)
            _write_yaml(os.path.join(out_dir, 'prometheus-pvcs.yaml'), [
                _with_type_meta(_strip_managed_fields(v), 'v1',
                                'PersistentVolumeClaim') for v in pvcs
            ])
    except Exception as e:  # pylint: disable=broad-except
        _record_error(errors, 'gpu_metrics/prometheus-pvcs', e)

    try:
        dcgm_ds = [
            d for d in _list(context, reads.DAEMON_SETS)
            if _DCGM_EXPORTER_NAME_SUBSTR in _name(d)
        ]
        if dcgm_ds:
            os.makedirs(out_dir, exist_ok=True)
            _write_yaml(os.path.join(out_dir, 'dcgm-exporter-daemonset.yaml'), [
                _with_type_meta(_strip_managed_fields(d), 'apps/v1',
                                'DaemonSet') for d in dcgm_ds
            ])
    except Exception as e:  # pylint: disable=broad-except
        _record_error(errors, 'gpu_metrics/dcgm-exporter-daemonset', e)

    return True


def _kueue_resource(version: str,
                    plural: str,
                    namespaced: bool = True) -> reads.Resource:
    return reads.Resource(group=_KUEUE_GROUP,
                          version=version,
                          plural=plural,
                          namespaced=namespaced)


def _resolve_kueue_version(probe: Callable[[str],
                                           None], errors: List[Dict[str, str]],
                           error_resource: str) -> Optional[str]:
    """Return the newest Kueue CRD version the cluster serves, or None.

    ``probe(version)`` makes a cheap list call for that version and raises on
    failure (KubernetesResourceNotServedError if the version isn't served);
    the per-cluster caller probes the namespaced ``workloads`` plural, the
    per-context caller probes the cluster-scoped ``clusterqueues`` plural --
    so each stays within its RBAC scope. We try _KUEUE_VERSIONS newest-first:
    for an unserved version we try the next; if no version is served the CRD
    isn't installed -- Kueue isn't in use -- and we return None so the caller
    skips silently. Any other failure is recorded under ``error_resource``.
    """
    for version in _KUEUE_VERSIONS:
        try:
            probe(version)
            return version
        except exceptions.KubernetesResourceNotServedError:
            continue
        except Exception as e:  # pylint: disable=broad-except
            _record_error(errors, error_resource, e)
            return None
    return None


def _dump_workload(context: Optional[str], namespace: str,
                   cluster_name_on_cloud: str, output_dir: str,
                   errors: List[Dict[str, str]]) -> None:
    """Dump this cluster's Kueue Workload (1:1, named cluster_name_on_cloud).

    Kueue names the pod-group Workload after the group, which SkyPilot sets to
    the cluster's on-cloud name -- so the Workload's metadata.name *is*
    cluster_name_on_cloud, and we select it by name (a field selector returns an
    empty list, not a 404, when absent). Namespace-scoped, so it stays within
    the per-cluster RBAC scope. The cluster-wide Kueue config (queues, flavors,
    topologies) is dumped once per context by ``_dump_kueue_cluster_objects``.
    """

    def _probe(version: str) -> None:
        _list(context,
              _kueue_resource(version, _KUEUE_WORKLOADS_PLURAL),
              namespace=namespace,
              limit=1)

    version = _resolve_kueue_version(_probe, errors,
                                     'kubernetes/kueue/workloads')
    if version is None:
        logger.debug('Kueue CRDs not served; skipping Workload.')
        return
    try:
        items = _list(context,
                      _kueue_resource(version, _KUEUE_WORKLOADS_PLURAL),
                      namespace=namespace,
                      field_selector=f'metadata.name={cluster_name_on_cloud}')
    except Exception as e:  # pylint: disable=broad-except
        _record_error(errors, 'kubernetes/kueue/workloads', e)
        return
    if items:
        kueue_dir = os.path.join(output_dir, 'kueue')
        os.makedirs(kueue_dir, exist_ok=True)
        _write_yaml(os.path.join(kueue_dir, 'workloads.yaml'),
                    [_strip_managed_fields(item) for item in items])


def _dump_pod_group(context: Optional[str], namespace: str,
                    cluster_name_on_cloud: str, output_dir: str,
                    errors: List[Dict[str, str]]) -> None:
    """Dump this cluster's KAI PodGroup, named cluster_name_on_cloud."""
    try:
        pod_group = _get(context,
                         _KAI_POD_GROUPS,
                         cluster_name_on_cloud,
                         namespace=namespace)
    except Exception as e:  # pylint: disable=broad-except
        _record_error(errors, 'kubernetes/kai/podgroups', e)
        return
    if pod_group is None:
        return
    kai_dir = os.path.join(output_dir, 'kai')
    os.makedirs(kai_dir, exist_ok=True)
    _write_yaml(os.path.join(kai_dir, 'podgroups.yaml'),
                [_strip_managed_fields(pod_group)])


def _dump_kueue_cluster_objects(context: Optional[str], output_dir: str,
                                errors: List[Dict[str, str]]) -> None:
    """Dump a context's cluster-WIDE Kueue config (not tied to one cluster).

    Into ``<output_dir>/kueue/``: LocalQueues, ClusterQueues, ResourceFlavors,
    Topologies -- the shared quota picture admission/preemption depends on. All
    via the cluster-scoped list endpoint, which returns objects across every
    namespace (including for the namespaced LocalQueue). Needs cluster-wide
    RBAC; best-effort. The served CRD version is resolved once via a
    cluster-scoped probe.
    """

    def _probe(version: str) -> None:
        _list(context,
              _kueue_resource(version, 'clusterqueues', namespaced=False),
              limit=1)

    version = _resolve_kueue_version(_probe, errors, 'kueue')
    if version is None:
        logger.debug('Kueue CRDs not served on context; skipping.')
        return
    kueue_dir = os.path.join(output_dir, 'kueue')
    # LocalQueue is namespaced; a list with no namespace covers every one.
    for filename, plural, namespaced in [
        ('localqueues.yaml', 'localqueues', True),
        ('clusterqueues.yaml', 'clusterqueues', False),
        ('resourceflavors.yaml', 'resourceflavors', False),
        ('topologies.yaml', 'topologies', False),
    ]:
        try:
            items = _list(
                context, _kueue_resource(version, plural,
                                         namespaced=namespaced))
        except exceptions.KubernetesResourceNotServedError:
            # This CRD isn't served at the resolved version; skip it.
            continue
        except Exception as e:  # pylint: disable=broad-except
            _record_error(errors, f'kueue/{filename}', e)
            continue

        if items:
            os.makedirs(kueue_dir, exist_ok=True)
            _write_yaml(os.path.join(kueue_dir, filename),
                        [_strip_managed_fields(item) for item in items])
