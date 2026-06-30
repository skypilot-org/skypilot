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
import functools
import json
import os
import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple

from sky import sky_logging
from sky.adaptors import kubernetes
from sky.provision import constants as provision_constants
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
# Label SkyPilot stamps on a cluster's pods when (and only when) a Kueue queue
# is configured (templates/kubernetes-ray.yml.j2). Its presence is the reliable
# "this cluster is using Kueue" signal -- more reliable than a Workload, which a
# using-Kueue cluster can transiently lack (creation race, GC, pod-integration
# off).
_KUEUE_QUEUE_NAME_LABEL = 'kueue.x-k8s.io/queue-name'

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

    # The ApiClient turns typed k8s model objects into kubectl-style dicts
    # (camelCase keys), so the dumped YAML matches `kubectl get -o yaml`.
    api_client = kubernetes.api_client(context)

    def to_dict(obj: Any) -> Dict[str, Any]:
        return _strip_managed_fields(api_client.sanitize_for_serialization(obj))

    pods, reachable = _dump_pods(context, namespace, cluster_name_on_cloud,
                                 output_dir, to_dict, errors)
    if not reachable:
        # The context is defunct (the pod list couldn't connect). Skip the
        # remaining per-cluster calls rather than stacking one timeout each.
        return errors
    _dump_labeled_resources(context, namespace, cluster_name_on_cloud,
                            output_dir, to_dict, errors)
    # Only dump the Workload if this cluster is actually using Kueue -- its pods
    # carry the queue-name label, which is the same signal Kueue uses to decide
    # whether to manage a pod, so the two conditions coincide. (A using-Kueue
    # cluster can transiently lack a Workload, so the label -- not the Workload
    # -- is the reliable signal.) Caveat: a cluster with Kueue's non-default
    # manageJobsWithoutQueueName=true would have even its unlabeled pods managed
    # by Kueue; this gate would then skip it. We accept that to avoid noise in
    # the common case.
    uses_kueue = any(
        _KUEUE_QUEUE_NAME_LABEL in (getattr(pod.metadata, 'labels', None) or {})
        for pod in pods)
    if uses_kueue:
        _dump_workload(context, namespace, cluster_name_on_cloud, output_dir,
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

    These are cluster-wide API calls (``list_pod_for_all_namespaces`` /
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

    api_client = kubernetes.api_client(context)

    def to_dict(obj: Any) -> Dict[str, Any]:
        return _strip_managed_fields(api_client.sanitize_for_serialization(obj))

    reachable = _dump_gpu_metrics_pods(context, output_dir, to_dict, errors)
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

    Returns ``api`` for call-site chaining, e.g.
    ``_fail_fast(kubernetes.core_api(context))``.
    """
    client = getattr(api, 'api_client', api)
    try:
        client.rest_client.pool_manager.connection_pool_kw['retries'] = 0
    except AttributeError:
        pass
    return api


def _write_yaml(path: str, obj: Any) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        f.write(yaml_utils.dump_yaml_str(obj))


def _write_text(path: str, text: str) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def _strip_managed_fields(obj_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Drop metadata.managedFields from a serialized object.

    managedFields is verbose server-side-apply bookkeeping that ``kubectl get
    -o yaml`` hides by default; it's pure noise in a debug dump and can inflate
    each object several-fold. Mutates and returns ``obj_dict``.
    """
    metadata = obj_dict.get('metadata')
    if isinstance(metadata, dict):
        metadata.pop('managedFields', None)
    return obj_dict


def _with_type_meta(obj_dict: Dict[str, Any], api_version: str,
                    kind: str) -> Dict[str, Any]:
    """Prepend apiVersion/kind to a serialized object.

    Kubernetes populates TypeMeta (apiVersion/kind) only on the List wrapper,
    not on the individual items returned by a ``list_*`` call, so objects we
    collect via a list lack the ``kind:``/``apiVersion:`` header that a
    ``kubectl get -o yaml`` shows (objects fetched via a ``read_*`` GET, like
    pods, already carry it). We know the type from the call we made, so stamp it
    on. They go first because dump_yaml_str preserves insertion order (it does
    not sort keys), and apiVersion/kind conventionally lead a manifest.
    """
    rest = {
        k: v for k, v in obj_dict.items() if k not in ('apiVersion', 'kind')
    }
    return {'apiVersion': api_version, 'kind': kind, **rest}


def _dump_pods(context: Optional[str], namespace: str,
               cluster_name_on_cloud: str, output_dir: str,
               to_dict: Callable[[Any], Dict[str, Any]],
               errors: List[Dict[str, str]]) -> Tuple[List[Any], bool]:
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
    core = _fail_fast(kubernetes.core_api(context))
    label_selector = (f'{provision_constants.TAG_SKYPILOT_CLUSTER_NAME}='
                      f'{cluster_name_on_cloud}')

    # Pods: one list call by label gets the head + all workers. This is the
    # first call against the context, so it doubles as the reachability gate.
    pods = []
    try:
        pods = core.list_namespaced_pod(
            namespace,
            label_selector=label_selector,
            _request_timeout=kubernetes.API_TIMEOUT).items
    except kubernetes.max_retry_error() as e:
        # Couldn't connect -- the context is defunct. Signal the caller to skip
        # this cluster's remaining calls rather than stack timeouts.
        _record_error(errors, 'kubernetes/pods', e)
        return [], False
    except Exception as e:  # pylint: disable=broad-except
        _record_error(errors, 'kubernetes/pods', e)
    if pods:
        pods_dir = os.path.join(output_dir, 'pods')
        os.makedirs(pods_dir, exist_ok=True)
        for pod in pods:
            _write_yaml(os.path.join(pods_dir, f'{pod.metadata.name}.yaml'),
                        _with_type_meta(to_dict(pod), 'v1', 'Pod'))

    # Events: one namespace-wide query for pod events (server-side narrowed to
    # Pods), filtered client-side to this cluster's pods and grouped per pod --
    # there's no field selector for "name in (...)", and a per-pod query would
    # be one call per pod.
    pod_names = {pod.metadata.name for pod in pods}
    if not pod_names:
        return pods, True
    try:
        events = core.list_namespaced_event(
            namespace,
            field_selector='involvedObject.kind=Pod',
            _request_timeout=kubernetes.API_TIMEOUT).items
    except Exception as e:  # pylint: disable=broad-except
        _record_error(errors, 'kubernetes/events', e)
        return pods, True

    events_by_pod: Dict[str, List[Any]] = {}
    for event in events:
        name = event.involved_object.name
        if name in pod_names:
            events_by_pod.setdefault(name, []).append(event)
    if events_by_pod:
        events_dir = os.path.join(output_dir, 'events')
        os.makedirs(events_dir, exist_ok=True)
        for name, pod_events in events_by_pod.items():
            _write_yaml(os.path.join(events_dir, f'{name}.yaml'), [
                _with_type_meta(to_dict(ev), 'v1', 'Event') for ev in pod_events
            ])
    return pods, True


def _dump_labeled_resources(context: Optional[str], namespace: str,
                            cluster_name_on_cloud: str, output_dir: str,
                            to_dict: Callable[[Any], Dict[str, Any]],
                            errors: List[Dict[str, str]]) -> None:
    """Dump the resources SkyPilot created for this cluster.

    Everything SkyPilot creates during instance creation carries the
    ``skypilot-cluster-name`` label (it's how teardown finds them), so a single
    label selector covers Services and any other kinds we create now or later.
    """
    label_selector = (f'{provision_constants.TAG_SKYPILOT_CLUSTER_NAME}='
                      f'{cluster_name_on_cloud}')
    core = _fail_fast(kubernetes.core_api(context))

    # (filename, apiVersion, kind, lister) for the kinds SkyPilot may create.
    # Listing a kind we didn't create just returns an empty list, so an
    # over-broad sweep is safe and future-proofs us against new resource kinds.
    # The apiVersion/kind are stamped onto each object (see _with_type_meta):
    # items from a list_* call carry no TypeMeta, so without this the dump
    # wouldn't match `kubectl get -o yaml`.
    kinds: List[Tuple[str, str, str, Callable[[], List[Any]]]] = [
        ('services.yaml', 'v1', 'Service', lambda: core.list_namespaced_service(
            namespace,
            label_selector=label_selector,
            _request_timeout=kubernetes.API_TIMEOUT).items),
        ('deployments.yaml', 'apps/v1', 'Deployment', lambda: _fail_fast(
            kubernetes.apps_api(context)).list_namespaced_deployment(
                namespace,
                label_selector=label_selector,
                _request_timeout=kubernetes.API_TIMEOUT).items),
        ('persistent_volume_claims.yaml', 'v1', 'PersistentVolumeClaim',
         lambda: core.list_namespaced_persistent_volume_claim(
             namespace,
             label_selector=label_selector,
             _request_timeout=kubernetes.API_TIMEOUT).items),
        ('config_maps.yaml', 'v1', 'ConfigMap',
         lambda: core.list_namespaced_config_map(namespace,
                                                 label_selector=label_selector,
                                                 _request_timeout=kubernetes.
                                                 API_TIMEOUT).items),
        ('ingresses.yaml', 'networking.k8s.io/v1', 'Ingress',
         lambda: _fail_fast(kubernetes.networking_api(context)).
         list_namespaced_ingress(namespace,
                                 label_selector=label_selector,
                                 _request_timeout=kubernetes.API_TIMEOUT).items
        ),
    ]
    # TODO(ishan): serialization is all-or-nothing per kind -- if to_dict throws
    # on one item, the whole <kind>.yaml is dropped (recorded as a single
    # error). Per-item isolation would be more robust but noisier; revisit if a
    # single malformed object proves to lose useful siblings. Same applies to
    # the secrets block below.
    for filename, api_version, kind, lister in kinds:
        try:
            items = lister()
            if not items:
                continue
            _write_yaml(os.path.join(output_dir, filename), [
                _with_type_meta(to_dict(item), api_version, kind)
                for item in items
            ])
        except Exception as e:  # pylint: disable=broad-except
            _record_error(errors, f'kubernetes/{filename}', e)

    # Secrets are dumped separately so their values can be redacted -- we want
    # the metadata (which secrets exist, their keys) for debugging, never the
    # contents.
    try:
        secrets = core.list_namespaced_secret(
            namespace,
            label_selector=label_selector,
            _request_timeout=kubernetes.API_TIMEOUT).items
        if secrets:
            redacted = []
            for secret in secrets:
                secret_dict = _with_type_meta(to_dict(secret), 'v1', 'Secret')
                for key in _SECRET_REDACTED_KEYS:
                    values = secret_dict.get(key)
                    if values:
                        secret_dict[key] = {k: _REDACTED for k in values}
                # The last-applied-configuration annotation embeds the data, so
                # redact it too (see _LAST_APPLIED_ANNOTATION).
                annotations = secret_dict.get('metadata', {}).get('annotations')
                if annotations and _LAST_APPLIED_ANNOTATION in annotations:
                    annotations[_LAST_APPLIED_ANNOTATION] = _REDACTED
                redacted.append(secret_dict)
            _write_yaml(os.path.join(output_dir, 'secrets.yaml'), redacted)
    except Exception as e:  # pylint: disable=broad-except
        _record_error(errors, 'kubernetes/secrets', e)


def _dump_gpu_metrics_pods(context: Optional[str], output_dir: str,
                           to_dict: Callable[[Any], Dict[str, Any]],
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
    core = _fail_fast(kubernetes.core_api(context))
    try:
        all_pods = core.list_pod_for_all_namespaces(
            _request_timeout=kubernetes.API_TIMEOUT).items
    except kubernetes.max_retry_error() as e:
        # Couldn't connect -- the context is likely defunct. Signal the caller
        # to skip the remaining cluster-wide calls rather than stack timeouts.
        _record_error(errors, 'gpu_metrics', e)
        return False
    except Exception as e:  # pylint: disable=broad-except
        # Reachable, but the call failed (e.g. RBAC 403); record and continue.
        _record_error(errors, 'gpu_metrics', e)
        return True

    prom_pods = [
        p for p in all_pods
        if p.metadata.namespace == _GPU_METRICS_NAMESPACE and
        p.metadata.name.startswith(_PROMETHEUS_SERVER_NAME_PREFIX)
    ]
    ksm_pods = [
        p for p in all_pods
        if p.metadata.namespace == _GPU_METRICS_NAMESPACE and
        _KUBE_STATE_METRICS_NAME_SUBSTR in p.metadata.name
    ]
    dcgm_pods = [
        p for p in all_pods if _DCGM_EXPORTER_NAME_SUBSTR in p.metadata.name
    ]
    out_dir = os.path.join(output_dir, 'gpu_metrics')
    for filename, matched in [('prometheus-server.yaml', prom_pods),
                              ('kube-state-metrics.yaml', ksm_pods),
                              ('dcgm-exporter.yaml', dcgm_pods)]:
        if matched:
            os.makedirs(out_dir, exist_ok=True)
            _write_yaml(
                os.path.join(out_dir, filename),
                [_with_type_meta(to_dict(p), 'v1', 'Pod') for p in matched])

    # The prometheus-server container's own logs -- the only place the WAL
    # replay progress ("Replaying WAL...", per-segment loads) and any
    # panic/abort message surface; the pod object shows *that* it restarted,
    # not *why* it died mid-startup. Grab the current container's tail and, when
    # it has restarted, the previous (terminated) container's logs too -- the
    # latter holds the boot-to-crash output of the instance that actually
    # failed. Best-effort and tail-bounded.
    for pod in prom_pods:
        pod_name = pod.metadata.name
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
    # forbidden just yields no file (a missing ConfigMap 404s -> treated as
    # absent).
    try:
        cm = core.read_namespaced_config_map(
            name=_PROMETHEUS_SERVER_NAME_PREFIX,
            namespace=_GPU_METRICS_NAMESPACE,
            _request_timeout=kubernetes.API_TIMEOUT)
        os.makedirs(out_dir, exist_ok=True)
        _write_yaml(os.path.join(out_dir, 'prometheus-config.yaml'),
                    _with_type_meta(to_dict(cm), 'v1', 'ConfigMap'))
    except kubernetes.api_exception() as e:
        if e.status != 404:
            _record_error(errors, 'gpu_metrics/prometheus-config', e)
    except Exception as e:  # pylint: disable=broad-except
        _record_error(errors, 'gpu_metrics/prometheus-config', e)

    try:
        pvcs = core.list_namespaced_persistent_volume_claim(
            namespace=_GPU_METRICS_NAMESPACE,
            _request_timeout=kubernetes.API_TIMEOUT).items
        if pvcs:
            os.makedirs(out_dir, exist_ok=True)
            _write_yaml(os.path.join(out_dir, 'prometheus-pvcs.yaml'), [
                _with_type_meta(to_dict(v), 'v1', 'PersistentVolumeClaim')
                for v in pvcs
            ])
    except Exception as e:  # pylint: disable=broad-except
        _record_error(errors, 'gpu_metrics/prometheus-pvcs', e)

    try:
        dcgm_ds = [
            d for d in kubernetes.apps_api(
                context).list_daemon_set_for_all_namespaces(
                    _request_timeout=kubernetes.API_TIMEOUT).items
            if _DCGM_EXPORTER_NAME_SUBSTR in d.metadata.name
        ]
        if dcgm_ds:
            os.makedirs(out_dir, exist_ok=True)
            _write_yaml(os.path.join(out_dir, 'dcgm-exporter-daemonset.yaml'), [
                _with_type_meta(to_dict(d), 'apps/v1', 'DaemonSet')
                for d in dcgm_ds
            ])
    except Exception as e:  # pylint: disable=broad-except
        _record_error(errors, 'gpu_metrics/dcgm-exporter-daemonset', e)

    return True


def _resolve_kueue_version(probe: Callable[[str],
                                           None], errors: List[Dict[str, str]],
                           error_resource: str) -> Optional[str]:
    """Return the newest Kueue CRD version the cluster serves, or None.

    ``probe(version)`` makes a cheap list call for that version and raises on
    failure; the per-cluster caller probes the namespaced ``workloads`` plural,
    the per-context caller probes the cluster-scoped ``clusterqueues`` plural --
    so each stays within its RBAC scope. We try _KUEUE_VERSIONS newest-first: a
    404 means that version isn't served, so we try the next; if every version
    404s the CRD isn't installed -- Kueue isn't in use -- and we return None so
    the caller skips silently. A non-404 failure is recorded under
    ``error_resource``.
    """
    for version in _KUEUE_VERSIONS:
        try:
            probe(version)
            return version
        except kubernetes.api_exception() as e:
            if e.status == 404:
                continue
            _record_error(errors, error_resource, e)
            return None
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
    api = _fail_fast(kubernetes.custom_objects_api(context))

    def _probe(version: str) -> None:
        api.list_namespaced_custom_object(
            group=_KUEUE_GROUP,
            version=version,
            namespace=namespace,
            plural=_KUEUE_WORKLOADS_PLURAL,
            limit=1,
            _request_timeout=kubernetes.API_TIMEOUT)

    version = _resolve_kueue_version(_probe, errors,
                                     'kubernetes/kueue/workloads')
    if version is None:
        logger.debug('Kueue CRDs not served; skipping Workload.')
        return
    try:
        response = api.list_namespaced_custom_object(
            group=_KUEUE_GROUP,
            version=version,
            namespace=namespace,
            plural=_KUEUE_WORKLOADS_PLURAL,
            field_selector=f'metadata.name={cluster_name_on_cloud}',
            _request_timeout=kubernetes.API_TIMEOUT)
    except Exception as e:  # pylint: disable=broad-except
        _record_error(errors, 'kubernetes/kueue/workloads', e)
        return
    items = response.get('items', []) if isinstance(response, dict) else []
    if items:
        kueue_dir = os.path.join(output_dir, 'kueue')
        os.makedirs(kueue_dir, exist_ok=True)
        _write_yaml(os.path.join(kueue_dir, 'workloads.yaml'),
                    [_strip_managed_fields(item) for item in items])


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
    api = _fail_fast(kubernetes.custom_objects_api(context))

    def _probe(version: str) -> None:
        api.list_cluster_custom_object(group=_KUEUE_GROUP,
                                       version=version,
                                       plural='clusterqueues',
                                       limit=1,
                                       _request_timeout=kubernetes.API_TIMEOUT)

    version = _resolve_kueue_version(_probe, errors, 'kueue')
    if version is None:
        logger.debug('Kueue CRDs not served on context; skipping.')
        return
    list_cluster = functools.partial(api.list_cluster_custom_object,
                                     group=_KUEUE_GROUP,
                                     version=version,
                                     _request_timeout=kubernetes.API_TIMEOUT)
    kueue_dir = os.path.join(output_dir, 'kueue')
    for filename, plural in [('localqueues.yaml', 'localqueues'),
                             ('clusterqueues.yaml', 'clusterqueues'),
                             ('resourceflavors.yaml', 'resourceflavors'),
                             ('topologies.yaml', 'topologies')]:
        try:
            response = list_cluster(plural=plural)
        except kubernetes.api_exception() as e:
            if e.status == 404:
                # This CRD isn't served at the resolved version; skip it.
                continue
            _record_error(errors, f'kueue/{filename}', e)
            continue
        except Exception as e:  # pylint: disable=broad-except
            _record_error(errors, f'kueue/{filename}', e)
            continue

        # Custom-object items are already dicts (no to_dict), so strip
        # managedFields here too.
        items = response.get('items', []) if isinstance(response, dict) else []
        if items:
            os.makedirs(kueue_dir, exist_ok=True)
            _write_yaml(os.path.join(kueue_dir, filename),
                        [_strip_managed_fields(item) for item in items])
