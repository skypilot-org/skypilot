"""Best-effort collection of a cluster's Kubernetes resources for debug dumps.

When the API server dumps a cluster that runs on Kubernetes, we also snapshot
the related k8s objects -- the pods, their events, the Services/Deployments/etc.
SkyPilot creates during instance creation, and (if Kueue is in use) the gang
Workload -- so an operator can inspect the cluster the way they would with
``kubectl get -o yaml``, without needing kubectl access to the user's cluster.

This module is intentionally provider-specific and self-contained: the generic
dump path (``sky.utils.debug_utils``) discovers a cluster's k8s coordinates from
its command runners and delegates the actual API queries here. Every query is
best-effort -- a failure is recorded and returned, never raised, so one missing
or inaccessible object can't abort the surrounding dump.
"""
import functools
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
# fall through; prepend 'v1' here when Kueue graduates. See _dump_kueue_objects.
_KUEUE_GROUP = 'kueue.x-k8s.io'
_KUEUE_VERSIONS = ('v1beta2', 'v1beta1')
_KUEUE_WORKLOADS_PLURAL = 'workloads'
# Label SkyPilot stamps on a cluster's pods when (and only when) a Kueue queue
# is configured (templates/kubernetes-ray.yml.j2). Its presence is the reliable
# "this cluster is using Kueue" signal -- more reliable than a Workload, which a
# using-Kueue cluster can transiently lack (creation race, GC, pod-integration
# off).
_KUEUE_QUEUE_NAME_LABEL = 'kueue.x-k8s.io/queue-name'

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
    """Snapshot a Kubernetes cluster's related resources into ``output_dir``.

    Writes (best-effort, skipping anything that doesn't exist)::

        <output_dir>/
          pods/<pod>.yaml      # full pod spec + status
          events/<pod>.yaml    # events involving each pod
          services.yaml        # resources carrying the skypilot-cluster-name
          deployments.yaml     #   label -- i.e. what SkyPilot created during
          ...                  #   instance creation
          kueue/               # Kueue objects, if Kueue is in use:
            workloads.yaml     #   this cluster's Workload(s)
            localqueues.yaml   #   the namespace's LocalQueues
            clusterqueues.yaml #   cluster-scoped quota config (+flavors,
            ...                #   topologies)

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

    pods = _dump_pods(context, namespace, cluster_name_on_cloud, output_dir,
                      to_dict, errors)
    _dump_labeled_resources(context, namespace, cluster_name_on_cloud,
                            output_dir, to_dict, errors)
    # Only dump Kueue objects if this cluster is actually using Kueue -- its
    # pods carry the queue-name label, which is the same signal Kueue uses to
    # decide whether to manage a pod, so the two conditions coincide. (A
    # using-Kueue cluster can transiently lack a Workload, so the label -- not
    # the Workload -- is the reliable signal.) Caveat: a cluster with Kueue's
    # non-default manageJobsWithoutQueueName=true would have even its unlabeled
    # pods managed by Kueue; this gate would then skip Kueue for a cluster it
    # does affect. We accept that to avoid noise in the common case.
    uses_kueue = any(
        _KUEUE_QUEUE_NAME_LABEL in (getattr(pod.metadata, 'labels', None) or {})
        for pod in pods)
    if uses_kueue:
        _dump_kueue_objects(context, namespace, cluster_name_on_cloud,
                            output_dir, errors)
    return errors


def _record_error(errors: List[Dict[str, str]], resource: str,
                  e: Exception) -> None:
    logger.debug(f'Failed to collect {resource}: {e}')
    errors.append({
        'resource': resource,
        'error': str(e),
        'traceback': traceback.format_exc(),
    })


def _write_yaml(path: str, obj: Any) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        f.write(yaml_utils.dump_yaml_str(obj))


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
               errors: List[Dict[str, str]]) -> List[Any]:
    """Dump the cluster's pods (spec/status) and the events involving them.

    Both come from a single ``list`` call each -- pods by the
    skypilot-cluster-name label, events by a namespace-wide pod-event query
    filtered to those pods -- so this is two API calls regardless of node count,
    rather than one (or two) per pod. List items carry no TypeMeta, so
    apiVersion/kind are stamped back on (see _with_type_meta).

    Returns the pod objects (so the caller can inspect their labels, e.g. to
    decide whether the cluster is using Kueue).
    """
    core = kubernetes.core_api(context)
    label_selector = (f'{provision_constants.TAG_SKYPILOT_CLUSTER_NAME}='
                      f'{cluster_name_on_cloud}')

    # Pods: one list call by label gets the head + all workers.
    pods = []
    try:
        pods = core.list_namespaced_pod(
            namespace,
            label_selector=label_selector,
            _request_timeout=kubernetes.API_TIMEOUT).items
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
        return pods
    try:
        events = core.list_namespaced_event(
            namespace,
            field_selector='involvedObject.kind=Pod',
            _request_timeout=kubernetes.API_TIMEOUT).items
    except Exception as e:  # pylint: disable=broad-except
        _record_error(errors, 'kubernetes/events', e)
        return pods

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
    return pods


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
    core = kubernetes.core_api(context)

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
        ('deployments.yaml', 'apps/v1', 'Deployment',
         lambda: kubernetes.apps_api(context).list_namespaced_deployment(
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
         lambda: kubernetes.networking_api(context).list_namespaced_ingress(
             namespace,
             label_selector=label_selector,
             _request_timeout=kubernetes.API_TIMEOUT).items),
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


def _resolve_kueue_version(api: Any, namespace: str,
                           errors: List[Dict[str, str]]) -> Optional[str]:
    """Return the newest Kueue CRD version the cluster serves, or None.

    Probes the workloads CRD with _KUEUE_VERSIONS newest-first. A 404 means that
    version isn't served, so we try the next; if every version 404s the CRD
    isn't installed at all -- the cluster doesn't use Kueue -- and we return
    None so the caller skips silently. A non-404 failure is recorded.
    """
    for version in _KUEUE_VERSIONS:
        try:
            api.list_namespaced_custom_object(
                group=_KUEUE_GROUP,
                version=version,
                namespace=namespace,
                plural=_KUEUE_WORKLOADS_PLURAL,
                limit=1,
                _request_timeout=kubernetes.API_TIMEOUT)
            return version
        except kubernetes.api_exception() as e:
            if e.status == 404:
                continue
            _record_error(errors, 'kubernetes/kueue/workloads', e)
            return None
        except Exception as e:  # pylint: disable=broad-except
            _record_error(errors, 'kubernetes/kueue/workloads', e)
            return None
    return None


def _dump_kueue_objects(context: Optional[str], namespace: str,
                        cluster_name_on_cloud: str, output_dir: str,
                        errors: List[Dict[str, str]]) -> None:
    """Dump the Kueue objects relevant to this cluster, if Kueue is in use.

    Into ``<output_dir>/kueue/``:
      - workloads: this cluster's Workload(s), by the pod-group label.
      - localqueues: the namespace's LocalQueues (the queues it can submit to).
      - clusterqueues/resourceflavors/topologies: cluster-scoped quota config,
        dumped whole -- admission/preemption depends on the full picture (e.g.
        borrowing across ClusterQueues), and these carry no per-cluster label.

    The custom objects API returns plain dicts, so no serialization is needed.
    The served CRD version is resolved once (see _resolve_kueue_version); if
    Kueue isn't installed we skip everything.
    """
    api = kubernetes.custom_objects_api(context)
    version = _resolve_kueue_version(api, namespace, errors)
    if version is None:
        logger.debug('Kueue CRDs not served; cluster is not using Kueue, '
                     'skipping.')
        return

    # The Workload's name is the cluster's on-cloud name (Kueue names the
    # pod-group Workload after the group); it carries no per-cluster label, so
    # select it by name -- a field selector returns an empty list (not a 404)
    # when absent.
    workload_selector = f'metadata.name={cluster_name_on_cloud}'
    kueue_dir = os.path.join(output_dir, 'kueue')

    # (filename, lister). The Workload is scoped to this cluster by name;
    # LocalQueues to the namespace; the rest are cluster-scoped (listed whole).
    # A CRD a given Kueue version doesn't serve 404s and is skipped.
    list_namespaced = functools.partial(api.list_namespaced_custom_object,
                                        group=_KUEUE_GROUP,
                                        version=version,
                                        namespace=namespace,
                                        _request_timeout=kubernetes.API_TIMEOUT)
    list_cluster = functools.partial(api.list_cluster_custom_object,
                                     group=_KUEUE_GROUP,
                                     version=version,
                                     _request_timeout=kubernetes.API_TIMEOUT)
    listers: List[Tuple[str, Callable[[], Any]]] = [
        ('workloads.yaml', lambda: list_namespaced(
            plural='workloads', field_selector=workload_selector)),
        ('localqueues.yaml', lambda: list_namespaced(plural='localqueues')),
        ('clusterqueues.yaml', lambda: list_cluster(plural='clusterqueues')),
        ('resourceflavors.yaml',
         lambda: list_cluster(plural='resourceflavors')),
        ('topologies.yaml', lambda: list_cluster(plural='topologies')),
    ]
    for filename, lister in listers:
        try:
            response = lister()
        except kubernetes.api_exception() as e:
            if e.status == 404:
                # This CRD isn't served at the resolved version; skip it.
                continue
            _record_error(errors, f'kubernetes/kueue/{filename}', e)
            continue
        except Exception as e:  # pylint: disable=broad-except
            _record_error(errors, f'kubernetes/kueue/{filename}', e)
            continue

        # Custom-object items are already dicts (no to_dict), so strip
        # managedFields here too.
        items = response.get('items', []) if isinstance(response, dict) else []
        if items:
            os.makedirs(kueue_dir, exist_ok=True)
            _write_yaml(os.path.join(kueue_dir, filename),
                        [_strip_managed_fields(item) for item in items])
