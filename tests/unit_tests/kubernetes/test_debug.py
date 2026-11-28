"""Tests for sky.provision.kubernetes.debug (cluster resource dump)."""
import copy
from types import SimpleNamespace
from typing import Any, Callable, Dict, List
from unittest import mock

import pytest

from sky import exceptions
from sky.provision.kubernetes import debug
from sky.provision.kubernetes import reads
import sky.utils.yaml_utils as yaml_utils


class _FakeApiException(Exception):
    """Stand-in for kubernetes.client.rest.ApiException."""

    def __init__(self, status):
        self.status = status
        super().__init__(f'status={status}')


def _unreachable_error():
    """A read that got no answer from the API server."""
    return exceptions.KubeAPIUnreachableError(
        'Timed out from Kubernetes cluster.')


def _answered_error(status):
    """A read the API server answered with an error status."""
    error = exceptions.KubeAPIUnreachableError(
        f'Kubernetes API error: status={status}')
    error.__cause__ = _FakeApiException(status)
    return error


def _obj(name, namespace='ns', labels=None, **fields):
    """An object in Kubernetes API JSON."""
    return {
        'metadata': {
            'name': name,
            'namespace': namespace,
            'labels': dict(labels or {}),
        },
        **fields,
    }


def _pods(*names, kueue=False, kai=False):
    """Pods of the cluster. With kueue=True (kai=True) the pods carry the label
    that marks the cluster as using Kueue (KAI)."""
    labels = {}
    if kueue:
        labels['kueue.x-k8s.io/queue-name'] = 'q'
    if kai:
        labels['kai.scheduler/queue'] = 'team-queue'
    return [_obj(n, labels=labels, spec={'nodeName': 'n1'}) for n in names]


def _events(*involved_pod_names):
    """Pod events; each references a pod."""
    return [
        _obj(f'evt-{i}', involvedObject={
            'kind': 'Pod',
            'name': n
        }) for i, n in enumerate(involved_pod_names)
    ]


def _all_ns_pods(*ns_name_pairs):
    """Pods across namespaces; each item is a (namespace, name) pair."""
    return [_obj(name, namespace=ns) for ns, name in ns_name_pairs]


@pytest.fixture
def k8s(monkeypatch):
    """Fake the read layer and the kubernetes adaptor calls the module makes.

    Lists answer from ``objects`` (by plural), raise ``failures[plural]``, or
    defer to ``handlers[plural](call)``. Gets answer from ``got`` (by plural):
    an object, an exception to raise, or absent. Every read is recorded in
    ``calls``.
    """
    objects: Dict[str, List[Any]] = {}
    failures: Dict[str, Exception] = {}
    handlers: Dict[str, Callable[[SimpleNamespace], List[Any]]] = {}
    got: Dict[str, Any] = {}
    calls: List[SimpleNamespace] = []

    def _result(items):
        return reads.ReadResult(items=items,
                                resource_version='1',
                                staleness_seconds=0,
                                reason=reads.REASON_FRESH)

    def list_objects(context,
                     resource,
                     *,
                     namespace=None,
                     label_selector=None,
                     field_selector=None,
                     consistency=reads.CACHED,
                     convert=None,
                     attempts=None,
                     timeout=None,
                     limit=None):
        del convert, timeout
        call = SimpleNamespace(op='list',
                               context=context,
                               resource=resource,
                               namespace=namespace,
                               label_selector=label_selector,
                               field_selector=field_selector,
                               consistency=consistency,
                               attempts=attempts,
                               limit=limit)
        calls.append(call)
        plural = resource.plural
        if plural in handlers:
            return _result(handlers[plural](call))
        if plural in failures:
            raise failures[plural]
        return _result(list(objects.get(plural, [])))

    def get_object(context,
                   resource,
                   name,
                   *,
                   namespace=None,
                   consistency=reads.CACHED,
                   convert=None,
                   attempts=None,
                   timeout=None):
        del convert, timeout
        calls.append(
            SimpleNamespace(op='get',
                            context=context,
                            resource=resource,
                            name=name,
                            namespace=namespace,
                            consistency=consistency,
                            attempts=attempts))
        answer = got.get(resource.plural)
        if isinstance(answer, Exception):
            raise answer
        return _result([] if answer is None else [answer])

    core = mock.MagicMock()
    core.read_namespaced_pod_log.return_value = ''
    # The read layer's ApiClient, shaped like the real one so _fail_fast can
    # reach its pool settings.
    api_client = SimpleNamespace(rest_client=SimpleNamespace(
        pool_manager=SimpleNamespace(connection_pool_kw={})))

    monkeypatch.setattr(reads, 'list_objects', list_objects)
    monkeypatch.setattr(reads, 'get_object', get_object)
    monkeypatch.setattr('sky.adaptors.kubernetes.core_api',
                        lambda *a, **k: core)
    monkeypatch.setattr('sky.adaptors.kubernetes.api_client',
                        lambda *a, **k: api_client)
    monkeypatch.setattr('sky.adaptors.kubernetes.api_exception',
                        lambda: _FakeApiException)

    def lists(plural=None):
        return [
            c for c in calls if c.op == 'list' and
            (plural is None or c.resource.plural == plural)
        ]

    return SimpleNamespace(objects=objects,
                           failures=failures,
                           handlers=handlers,
                           got=got,
                           calls=calls,
                           lists=lists,
                           core=core,
                           api_client=api_client)


def _run(tmp_path):
    """Run the per-cluster (namespace-scoped) dump."""
    return debug.dump_cluster_resources(context='ctx',
                                        namespace='ns',
                                        cluster_name_on_cloud='cluster-abc',
                                        output_dir=str(tmp_path))


def _run_context(tmp_path):
    """Run the per-context (cluster-wide) dump."""
    return debug.dump_context_resources(context='ctx', output_dir=str(tmp_path))


def test_dumps_pods_with_type_meta(tmp_path, k8s):
    # Pods come from one list-by-label call (head + workers).
    k8s.objects['pods'] = _pods('c-head', 'c-worker1')

    errors = _run(tmp_path)

    assert not errors
    for name in ('c-head', 'c-worker1'):
        dumped = yaml_utils.read_yaml(str(tmp_path / 'pods' / f'{name}.yaml'))
        # List items lack TypeMeta, so apiVersion/kind are stamped back on.
        assert (dumped['apiVersion'], dumped['kind']) == ('v1', 'Pod')
        assert dumped['metadata']['name'] == name
        assert dumped['spec'] == {'nodeName': 'n1'}
    # One list call, scoped to this cluster by label, read from the API server
    # once.
    [call] = k8s.lists('pods')
    assert call.namespace == 'ns'
    assert call.label_selector == 'skypilot-cluster-name=cluster-abc'
    assert call.consistency == reads.FRESH
    assert call.attempts == 1


def test_every_read_is_fresh_and_single_attempt(tmp_path, k8s):
    k8s.objects['pods'] = _pods('c-head', kueue=True, kai=True)

    _run(tmp_path)
    _run_context(tmp_path / 'context')

    assert k8s.calls
    assert all(c.consistency == reads.FRESH for c in k8s.calls)
    assert all(c.attempts == 1 for c in k8s.calls)


def test_reads_use_a_no_retry_pool(tmp_path, k8s):
    """The read layer's client gets retries=0 so a defunct context fails in
    one timeout instead of several."""
    _run(tmp_path)

    pool_kw = k8s.api_client.rest_client.pool_manager.connection_pool_kw
    assert pool_kw == {'retries': 0}


def test_managed_fields_stripped_without_modifying_read_objects(tmp_path, k8s):
    pods = _pods('c-head')
    pods[0]['metadata']['managedFields'] = [{'manager': 'kubelet'}]
    original = copy.deepcopy(pods)
    k8s.objects['pods'] = pods

    _run(tmp_path)

    dumped = yaml_utils.read_yaml(str(tmp_path / 'pods' / 'c-head.yaml'))
    assert 'managedFields' not in dumped['metadata']
    # Read results may be shared with other readers.
    assert pods == original


def test_events_filtered_to_cluster_pods_and_grouped(tmp_path, k8s):
    k8s.objects['pods'] = _pods('c-head', 'c-worker1')
    # Namespace-wide pod events: two for c-head, none for c-worker1, one for an
    # unrelated pod.
    k8s.objects['events'] = _events('c-head', 'c-head', 'other-cluster-pod')

    _run(tmp_path)

    # Only our pods' events are written, grouped per pod; the unrelated pod and
    # the pod with no events produce no file.
    assert (tmp_path / 'events' / 'c-head.yaml').exists()
    assert not (tmp_path / 'events' / 'c-worker1.yaml').exists()
    assert not (tmp_path / 'events' / 'other-cluster-pod.yaml').exists()
    # The query is one namespace-wide call narrowed to Pod events server-side.
    [call] = k8s.lists('events')
    assert call.namespace == 'ns'
    assert call.field_selector == 'involvedObject.kind=Pod'
    # c-head got both of its events (multi-doc YAML).
    docs = yaml_utils.read_yaml_all(str(tmp_path / 'events' / 'c-head.yaml'))
    assert len(docs) == 2
    assert docs[0]['kind'] == 'Event'


def test_labeled_resources_use_cluster_label(tmp_path, k8s):
    k8s.objects['services'] = [_obj('svc')]

    _run(tmp_path)

    assert (tmp_path / 'services.yaml').exists()
    [call] = k8s.lists('services')
    assert call.namespace == 'ns'
    assert call.label_selector == 'skypilot-cluster-name=cluster-abc'


def test_labeled_resources_carry_type_meta(tmp_path, k8s):
    """List-sourced objects get apiVersion/kind stamped on (k8s omits TypeMeta
    on items returned from a list), so the dump matches kubectl -o yaml.
    """
    k8s.objects['services'] = [_obj('svc')]
    k8s.objects['deployments'] = [_obj('dep')]
    k8s.objects['ingresses'] = [_obj('ing')]

    _run(tmp_path)

    svc = yaml_utils.read_yaml(str(tmp_path / 'services.yaml'))
    assert svc['apiVersion'] == 'v1'
    assert svc['kind'] == 'Service'
    # The original fields are preserved alongside the stamped meta.
    assert svc['metadata']['name'] == 'svc'
    # apiVersion/kind lead the document (dump preserves insertion order).
    assert list(svc)[:2] == ['apiVersion', 'kind']

    dep = yaml_utils.read_yaml(str(tmp_path / 'deployments.yaml'))
    assert (dep['apiVersion'], dep['kind']) == ('apps/v1', 'Deployment')
    ing = yaml_utils.read_yaml(str(tmp_path / 'ingresses.yaml'))
    assert (ing['apiVersion'], ing['kind']) == ('networking.k8s.io/v1',
                                                'Ingress')


def test_labeled_resource_failure_is_recorded_per_kind(tmp_path, k8s):
    k8s.failures['deployments'] = _answered_error(403)
    k8s.objects['services'] = [_obj('svc')]

    errors = _run(tmp_path)

    assert [e['resource'] for e in errors] == ['kubernetes/deployments.yaml']
    assert (tmp_path / 'services.yaml').exists()


def test_empty_resource_kinds_are_skipped(tmp_path, k8s):
    # All lists default to empty -> no resource files written.
    _run(tmp_path)

    for name in ('services.yaml', 'deployments.yaml', 'config_maps.yaml',
                 'persistent_volume_claims.yaml', 'ingresses.yaml',
                 'secrets.yaml'):
        assert not (tmp_path / name).exists()
    assert not (tmp_path / 'kueue').exists()


def test_per_cluster_makes_no_cluster_scoped_calls(tmp_path, k8s):
    """RBAC invariant: the per-cluster dump must stay namespace-scoped so it
    works under SkyPilot's minimal (namespace-only) RBAC -- it must never make a
    cluster-wide call (those live only in the per-context path)."""
    # A cluster that uses Kueue and KAI exercises the custom-object path too.
    k8s.objects['pods'] = _pods('c-head', kueue=True, kai=True)
    k8s.objects['workloads'] = [_obj('wl')]

    _run(tmp_path)

    assert k8s.calls
    assert all(c.namespace == 'ns' for c in k8s.calls)


def test_context_gpu_metrics_pods_dumped(tmp_path, k8s):
    """The metrics Prometheus server, the chart's kube-state-metrics, and the
    DCGM exporter (any ns, by name substring) are captured under gpu_metrics/
    from one cluster-wide list; unrelated pods are excluded."""
    k8s.objects['pods'] = _all_ns_pods(
        ('skypilot', 'skypilot-prometheus-server-abc123'),
        ('skypilot', 'skypilot-prometheus-kube-state-metrics-xyz'),
        ('gpu-operator', 'nvidia-dcgm-exporter-9q2lp'),
        ('default', 'some-unrelated-pod'),
    )

    errors = _run_context(tmp_path)

    assert not errors
    gdir = tmp_path / 'gpu_metrics'
    prom = yaml_utils.read_yaml(str(gdir / 'prometheus-server.yaml'))
    assert (prom['apiVersion'], prom['kind']) == ('v1', 'Pod')
    ksm = yaml_utils.read_yaml(str(gdir / 'kube-state-metrics.yaml'))
    assert (ksm['apiVersion'], ksm['kind']) == ('v1', 'Pod')
    dcgm = yaml_utils.read_yaml(str(gdir / 'dcgm-exporter.yaml'))
    assert (dcgm['apiVersion'], dcgm['kind']) == ('v1', 'Pod')
    # The three matched pod kinds are written; the unrelated pod is excluded,
    # and the config/PVC/DaemonSet are absent in this fixture.
    assert sorted(p.name for p in gdir.iterdir()) == [
        'dcgm-exporter.yaml', 'kube-state-metrics.yaml',
        'prometheus-server.yaml'
    ]
    # One pod list across all namespaces.
    [call] = k8s.lists('pods')
    assert call.namespace is None


def test_context_gpu_metrics_config_and_topology_dumped(tmp_path, k8s):
    """The prometheus ConfigMap (rendered prometheus.yml), the Prometheus
    PVC(s), and the DCGM-exporter DaemonSet are captured under gpu_metrics/
    alongside the pods -- the config + topology behind the metrics pipeline."""
    k8s.got['configmaps'] = _obj('skypilot-prometheus-server',
                                 namespace='skypilot',
                                 data={'prometheus.yml': 'scrape_configs: []'})
    k8s.objects['persistentvolumeclaims'] = [_obj('pvc', namespace='skypilot')]
    k8s.objects['daemonsets'] = [
        _obj('nvidia-dcgm-exporter', namespace='gpu-operator'),
        _obj('some-other-ds', namespace='kube-system'),
    ]

    errors = _run_context(tmp_path)

    assert not errors
    gdir = tmp_path / 'gpu_metrics'
    cm = yaml_utils.read_yaml(str(gdir / 'prometheus-config.yaml'))
    assert (cm['apiVersion'], cm['kind']) == ('v1', 'ConfigMap')
    assert cm['data'] == {'prometheus.yml': 'scrape_configs: []'}
    pvc = yaml_utils.read_yaml(str(gdir / 'prometheus-pvcs.yaml'))
    assert (pvc['apiVersion'], pvc['kind']) == ('v1', 'PersistentVolumeClaim')
    ds = yaml_utils.read_yaml(str(gdir / 'dcgm-exporter-daemonset.yaml'))
    assert (ds['apiVersion'], ds['kind']) == ('apps/v1', 'DaemonSet')
    # Only the dcgm-exporter DS, not some-other-ds.
    assert ds['metadata']['name'] == 'nvidia-dcgm-exporter'
    [cm_get] = [c for c in k8s.calls if c.op == 'get']
    assert (cm_get.namespace, cm_get.name) == ('skypilot',
                                               'skypilot-prometheus-server')
    [pvc_list] = k8s.lists('persistentvolumeclaims')
    assert pvc_list.namespace == 'skypilot'
    [ds_list] = k8s.lists('daemonsets')
    assert ds_list.namespace is None


def test_context_prometheus_config_failure_is_recorded(tmp_path, k8s):
    """An absent ConfigMap yields no file silently; a failed read is
    recorded."""
    k8s.got['configmaps'] = _answered_error(403)

    errors = _run_context(tmp_path)

    assert [e['resource'] for e in errors] == ['gpu_metrics/prometheus-config']
    assert not (tmp_path / 'gpu_metrics').exists()


def test_context_prometheus_server_logs_collected(tmp_path, k8s):
    """The prometheus-server container's current and previous logs are written
    under gpu_metrics/ as <pod>.log / <pod>.previous.log -- the WAL-replay
    output the pod object can't show."""
    k8s.objects['pods'] = _all_ns_pods(
        ('skypilot', 'skypilot-prometheus-server-abc123'))

    def _read_log(**kwargs):
        assert kwargs['container'] == 'prometheus-server'
        assert kwargs['tail_lines'] == debug._PROMETHEUS_LOG_TAIL_LINES
        return ('replaying WAL previous\n'
                if kwargs['previous'] else 'level=info ready\n')

    k8s.core.read_namespaced_pod_log.side_effect = _read_log

    errors = _run_context(tmp_path)

    assert not errors
    gdir = tmp_path / 'gpu_metrics'
    assert (gdir / 'skypilot-prometheus-server-abc123.log').read_text() == (
        'level=info ready\n')
    assert (gdir /
            'skypilot-prometheus-server-abc123.previous.log').read_text() == (
                'replaying WAL previous\n')


def test_context_prometheus_logs_no_previous_is_not_an_error(tmp_path, k8s):
    """A 400 on the previous-container read (the server has never restarted) is
    expected: the current log is still written and no error is recorded."""
    k8s.objects['pods'] = _all_ns_pods(
        ('skypilot', 'skypilot-prometheus-server-abc123'))

    def _read_log(**kwargs):
        if kwargs['previous']:
            raise _FakeApiException(400)
        return 'current only\n'

    k8s.core.read_namespaced_pod_log.side_effect = _read_log

    errors = _run_context(tmp_path)

    assert not errors
    gdir = tmp_path / 'gpu_metrics'
    assert (gdir / 'skypilot-prometheus-server-abc123.log').exists()
    assert not (gdir /
                'skypilot-prometheus-server-abc123.previous.log').exists()


def test_context_prometheus_log_failure_is_recorded(tmp_path, k8s):
    """A non-400 failure reading the prometheus-server log is recorded (not
    swallowed) so the dump's error manifest shows the gap."""
    k8s.objects['pods'] = _all_ns_pods(
        ('skypilot', 'skypilot-prometheus-server-abc123'))
    k8s.core.read_namespaced_pod_log.side_effect = _FakeApiException(403)

    errors = _run_context(tmp_path)

    resources = {e['resource'] for e in errors}
    assert 'gpu_metrics/skypilot-prometheus-server-abc123.log' in resources


def test_context_prometheus_logs_dedupes_identical_previous(tmp_path, k8s):
    """A fast crash-loop can make the current and previous reads resolve to the
    same container instance; a byte-identical previous log is dropped, not
    written as a confusing duplicate."""
    k8s.objects['pods'] = _all_ns_pods(
        ('skypilot', 'skypilot-prometheus-server-abc123'))
    # Both previous=False and previous=True return the same bytes.
    k8s.core.read_namespaced_pod_log.return_value = 'same boot log\n'

    errors = _run_context(tmp_path)

    assert not errors
    gdir = tmp_path / 'gpu_metrics'
    assert (gdir / 'skypilot-prometheus-server-abc123.log').read_text() == (
        'same boot log\n')
    assert not (gdir /
                'skypilot-prometheus-server-abc123.previous.log').exists()


def test_context_prometheus_log_write_error_is_recorded(tmp_path, k8s,
                                                        monkeypatch):
    """A local write failure (e.g. full disk) is recorded best-effort rather
    than propagating out and aborting the rest of the dump."""
    k8s.objects['pods'] = _all_ns_pods(
        ('skypilot', 'skypilot-prometheus-server-abc123'))
    k8s.core.read_namespaced_pod_log.return_value = 'boot log\n'

    def _boom(*_args, **_kwargs):
        raise OSError('No space left on device')

    monkeypatch.setattr(debug, '_write_text', _boom)

    errors = _run_context(tmp_path)

    resources = {e['resource'] for e in errors}
    assert 'gpu_metrics/skypilot-prometheus-server-abc123.log' in resources


def test_context_gpu_metrics_absent_produces_no_dir(tmp_path, k8s):
    k8s.objects['pods'] = _all_ns_pods(('default', 'unrelated-pod'))

    _run_context(tmp_path)

    assert not (tmp_path / 'gpu_metrics').exists()


def test_context_empty_writes_only_reachable_marker(tmp_path, k8s):
    # No GPU-metrics pods, no Kueue objects -> a reachable-but-bare context
    # writes no resource dirs, but still drops a context.json marker (so the
    # context stays visible in the dump even though the zip omits empty dirs).
    errors = _run_context(tmp_path)

    assert not errors
    assert not (tmp_path / 'gpu_metrics').exists()
    assert not (tmp_path / 'kueue').exists()
    marker = yaml_utils.read_yaml(str(tmp_path / 'context.json'))
    assert marker == {'context': 'ctx', 'reachable': True}


def test_secret_values_are_redacted(tmp_path, k8s):
    last_applied = 'kubectl.kubernetes.io/last-applied-configuration'
    secret = {
        'metadata': {
            'name': 's',
            'annotations': {
                last_applied: '{"stringData":{"note":"plaintext"}}',
                'keep-me': 'ok',
            },
        },
        'data': {
            'token': 'c2VjcmV0',
            'password': 'aHVudGVyMg=='
        },
        'stringData': {
            'note': 'plaintext'
        },
    }
    original = copy.deepcopy(secret)
    k8s.objects['secrets'] = [secret]

    errors = _run(tmp_path)

    assert not errors
    dumped = yaml_utils.read_yaml(str(tmp_path / 'secrets.yaml'))
    assert dumped['data'] == {'token': '<redacted>', 'password': '<redacted>'}
    assert dumped['stringData'] == {'note': '<redacted>'}
    # The data-bearing annotation is redacted; unrelated annotations are kept.
    assert dumped['metadata']['annotations'][last_applied] == '<redacted>'
    assert dumped['metadata']['annotations']['keep-me'] == 'ok'
    # Redaction works on copies: read results may be shared.
    assert secret == original


def test_kueue_workload_dumped_scoped_to_cluster(tmp_path, k8s):
    k8s.objects['pods'] = _pods('c-head', kueue=True)
    # Include managedFields to confirm it's stripped from custom objects too.
    k8s.objects['workloads'] = [{
        'metadata': {
            'name': 'wl',
            'managedFields': [{
                'manager': 'kueue'
            }],
        }
    }]

    errors = _run(tmp_path)

    assert not errors
    path = tmp_path / 'kueue' / 'workloads.yaml'
    assert path.exists()
    # managedFields stripped -> just the metadata we care about remains.
    assert yaml_utils.read_yaml(str(path)) == {'metadata': {'name': 'wl'}}
    # The version probe is a namespaced list of one object; the Workload is
    # then scoped to this cluster by name (Kueue names the pod-group Workload
    # after the cluster), newest version first.
    probe, wl_call = k8s.lists('workloads')
    assert probe.limit == 1
    assert wl_call.resource == reads.Resource(group='kueue.x-k8s.io',
                                              version='v1beta2',
                                              plural='workloads',
                                              namespaced=True)
    assert wl_call.namespace == 'ns'
    assert wl_call.field_selector == 'metadata.name=cluster-abc'


def test_context_dumps_queues_flavors_and_topologies(tmp_path, k8s):
    """The cluster-wide Kueue config -- LocalQueues, ClusterQueues,
    ResourceFlavors, Topologies -- is captured once per context under kueue/,
    each with one list across all namespaces."""
    k8s.objects.update({
        'localqueues': [{
            'metadata': {
                'name': 'user-queue'
            }
        }],
        'clusterqueues': [{
            'metadata': {
                'name': 'cluster-queue'
            }
        }],
        'resourceflavors': [{
            'metadata': {
                'name': 'default-flavor'
            }
        }],
        'topologies': [{
            'metadata': {
                'name': 'rack'
            }
        }],
    })

    errors = _run_context(tmp_path)

    assert not errors
    kdir = tmp_path / 'kueue'
    assert yaml_utils.read_yaml(str(kdir / 'localqueues.yaml')) == {
        'metadata': {
            'name': 'user-queue'
        }
    }
    assert yaml_utils.read_yaml(str(kdir / 'clusterqueues.yaml')) == {
        'metadata': {
            'name': 'cluster-queue'
        }
    }
    assert yaml_utils.read_yaml(str(kdir / 'resourceflavors.yaml')) == {
        'metadata': {
            'name': 'default-flavor'
        }
    }
    assert yaml_utils.read_yaml(str(kdir / 'topologies.yaml')) == {
        'metadata': {
            'name': 'rack'
        }
    }
    kueue_calls = [
        c for c in k8s.lists() if c.resource.group == 'kueue.x-k8s.io'
    ]
    assert {c.resource.plural for c in kueue_calls} == {
        'localqueues', 'clusterqueues', 'resourceflavors', 'topologies'
    }
    # Every kind -- including the namespaced LocalQueue -- is listed across
    # all namespaces, so one call per kind covers them; 'workloads' is
    # per-cluster and never fetched here.
    assert all(c.namespace is None for c in kueue_calls)
    [localqueues] = k8s.lists('localqueues')
    assert localqueues.resource.namespaced


def test_context_kueue_uses_cluster_scoped_probe_and_falls_back(tmp_path, k8s):
    """The per-context Kueue version probe is cluster-scoped (clusterqueues) and
    falls back v1beta2 -> v1beta1 when v1beta2 isn't served."""
    k8s.objects['clusterqueues'] = [{'metadata': {'name': 'cq'}}]

    def _clusterqueues(call):
        if call.resource.version == 'v1beta2':
            raise exceptions.KubernetesResourceNotServedError('not served')
        return list(k8s.objects['clusterqueues'])

    k8s.handlers['clusterqueues'] = _clusterqueues

    errors = _run_context(tmp_path)

    assert not errors
    assert (tmp_path / 'kueue' / 'clusterqueues.yaml').exists()
    # The probe is the cluster-scoped clusterqueues list, tried newest-first.
    probes = k8s.lists('clusterqueues')
    assert [c.resource.version for c in probes[:2]] == ['v1beta2', 'v1beta1']
    assert probes[0].namespace is None
    assert not probes[0].resource.namespaced
    # Every later Kueue read uses the resolved version.
    kueue_calls = [
        c for c in k8s.lists() if c.resource.group == 'kueue.x-k8s.io'
    ]
    assert all(c.resource.version == 'v1beta1' for c in kueue_calls[1:])


def test_context_kueue_unserved_kind_is_skipped(tmp_path, k8s):
    """A Kueue kind not served at the resolved version (e.g. Topology on an
    older Kueue) is skipped without an error; the others are still dumped."""
    k8s.objects['localqueues'] = [{'metadata': {'name': 'lq'}}]
    k8s.failures['topologies'] = exceptions.KubernetesResourceNotServedError(
        'not served')

    errors = _run_context(tmp_path)

    assert not errors
    assert (tmp_path / 'kueue' / 'localqueues.yaml').exists()
    assert not (tmp_path / 'kueue' / 'topologies.yaml').exists()


def test_context_kueue_kind_failure_is_recorded(tmp_path, k8s):
    k8s.failures['resourceflavors'] = _answered_error(403)

    errors = _run_context(tmp_path)

    assert [e['resource'] for e in errors] == ['kueue/resourceflavors.yaml']


def test_context_kueue_probe_failure_is_recorded(tmp_path, k8s):
    # A failure other than "not served" on the cluster-scoped probe is recorded
    # under the 'kueue' resource (context-relative, no 'kubernetes/' prefix).
    k8s.failures['clusterqueues'] = _answered_error(500)

    errors = _run_context(tmp_path)

    assert len(errors) == 1
    assert errors[0]['resource'] == 'kueue'


def test_context_broken_skips_kueue(tmp_path, k8s):
    """A defunct context (no answer to the first cluster-wide call) fast-fails:
    it records the failure and skips the remaining cluster-wide calls rather
    than stacking timeouts."""
    k8s.failures['pods'] = _unreachable_error()

    errors = _run_context(tmp_path)

    # The reachability failure is recorded against gpu_metrics...
    assert len(errors) == 1
    assert errors[0]['resource'] == 'gpu_metrics'
    # ...and nothing else is read (no stacked timeouts).
    assert [c.resource.plural for c in k8s.calls] == ['pods']
    assert not (tmp_path / 'kueue').exists()
    # The marker still records the context as unreachable, so a defunct context
    # is visible in the dump rather than silently absent.
    marker = yaml_utils.read_yaml(str(tmp_path / 'context.json'))
    assert marker == {'context': 'ctx', 'reachable': False}


def test_context_forbidden_pod_list_still_dumps_kueue(tmp_path, k8s):
    """An answered failure (e.g. RBAC 403) of the first call means the context
    is reachable: it is recorded and the rest of the dump proceeds."""
    k8s.failures['pods'] = _answered_error(403)
    k8s.objects['clusterqueues'] = [{'metadata': {'name': 'cq'}}]

    errors = _run_context(tmp_path)

    assert [e['resource'] for e in errors] == ['gpu_metrics']
    assert (tmp_path / 'kueue' / 'clusterqueues.yaml').exists()
    marker = yaml_utils.read_yaml(str(tmp_path / 'context.json'))
    assert marker == {'context': 'ctx', 'reachable': True}


def test_kueue_falls_back_to_older_version(tmp_path, k8s):
    # v1beta2 isn't served; the version probe falls back to v1beta1.
    k8s.objects['pods'] = _pods('c-head', kueue=True)

    def _workloads(call):
        if call.resource.version == 'v1beta2':
            raise exceptions.KubernetesResourceNotServedError('not served')
        return [{'metadata': {'name': 'wl'}}]

    k8s.handlers['workloads'] = _workloads

    errors = _run(tmp_path)

    assert not errors
    assert (tmp_path / 'kueue' / 'workloads.yaml').exists()
    versions = [c.resource.version for c in k8s.lists('workloads')]
    # Probe tried v1beta2 then resolved v1beta1; the dump used v1beta1.
    assert versions[:2] == ['v1beta2', 'v1beta1']
    assert all(v == 'v1beta1' for v in versions[1:])


def test_kueue_crd_absent_is_not_an_error(tmp_path, k8s):
    # Cluster uses Kueue (labeled pods), but the CRD isn't served at any version
    # -> Kueue isn't actually installed; skip without error.
    k8s.objects['pods'] = _pods('c-head', kueue=True)
    k8s.failures['workloads'] = exceptions.KubernetesResourceNotServedError(
        'not served')

    errors = _run(tmp_path)

    assert not errors
    assert not (tmp_path / 'kueue').exists()
    # Only the version probe ran (both versions) before giving up.
    assert len(k8s.lists('workloads')) == 2


def test_kueue_probe_failure_is_recorded(tmp_path, k8s):
    k8s.objects['pods'] = _pods('c-head', kueue=True)
    k8s.failures['workloads'] = _answered_error(500)

    errors = _run(tmp_path)

    assert len(errors) == 1
    assert errors[0]['resource'] == 'kubernetes/kueue/workloads'
    assert len(k8s.lists('workloads')) == 1


def test_kueue_skipped_when_cluster_not_using_kueue(tmp_path, k8s):
    """Kueue may be installed cluster-wide, but if this cluster's pods don't
    carry the queue-name label we don't touch Kueue at all -- no kueue/ dir, no
    Kueue reads."""
    k8s.objects['pods'] = _pods('c-head')  # no label
    # Even with Kueue objects present...
    k8s.objects['workloads'] = [{'metadata': {'name': 'cluster-abc'}}]

    errors = _run(tmp_path)

    assert not errors
    assert not (tmp_path / 'kueue').exists()
    assert not [c for c in k8s.calls if c.resource.group == 'kueue.x-k8s.io']


def test_kai_pod_group_dumped_for_kai_cluster(tmp_path, k8s):
    k8s.objects['pods'] = _pods('c-head', kai=True)
    k8s.got['podgroups'] = {
        'metadata': {
            'name': 'cluster-abc',
            'managedFields': [{
                'manager': 'kai'
            }],
        }
    }

    errors = _run(tmp_path)

    assert not errors
    path = tmp_path / 'kai' / 'podgroups.yaml'
    assert yaml_utils.read_yaml(str(path)) == {
        'metadata': {
            'name': 'cluster-abc'
        }
    }
    [call] = [c for c in k8s.calls if c.op == 'get']
    assert call.resource == reads.Resource(group='scheduling.run.ai',
                                           version='v2alpha2',
                                           plural='podgroups',
                                           namespaced=True)
    assert (call.namespace, call.name) == ('ns', 'cluster-abc')


def test_kai_absent_pod_group_is_not_an_error(tmp_path, k8s):
    k8s.objects['pods'] = _pods('c-head', kai=True)

    assert not _run(tmp_path)
    assert not (tmp_path / 'kai').exists()


def test_kai_pod_group_failure_is_recorded(tmp_path, k8s):
    k8s.objects['pods'] = _pods('c-head', kai=True)
    k8s.got['podgroups'] = _answered_error(500)

    errors = _run(tmp_path)

    assert [error['resource'] for error in errors
           ] == ['kubernetes/kai/podgroups']


def test_kai_skipped_when_cluster_not_using_kai(tmp_path, k8s):
    k8s.objects['pods'] = _pods('c-head')

    assert not _run(tmp_path)
    assert not (tmp_path / 'kai').exists()
    assert not [c for c in k8s.calls if c.op == 'get']


@pytest.mark.parametrize('error', [RuntimeError('boom'), _answered_error(403)])
def test_pod_list_failure_is_recorded(tmp_path, k8s, error):
    # A failure the API server answered (or an unexpected one) is recorded,
    # but the context is still reachable, so the dump continues for everything
    # else (labeled resources, Kueue).
    k8s.failures['pods'] = error

    errors = _run(tmp_path)

    pod_errs = [e for e in errors if e['resource'] == 'kubernetes/pods']
    assert len(pod_errs) == 1
    assert pod_errs[0]['error'] == str(error)
    assert 'traceback' in pod_errs[0]
    # The dump continued past pods (labeled-resource sweep was attempted).
    assert k8s.lists('services')


def test_defunct_context_fast_fails_per_cluster(tmp_path, k8s):
    """If the pod list -- the first call -- gets no answer, the context is
    defunct; the per-cluster dump must bail immediately rather than stack one
    timeout per remaining call (services, deployments, ... Kueue)."""
    k8s.failures['pods'] = _unreachable_error()

    errors = _run(tmp_path)

    # The connection failure is recorded against pods...
    assert len(errors) == 1
    assert errors[0]['resource'] == 'kubernetes/pods'
    # ...and none of the cluster's other calls are attempted (no stacked
    # timeouts), so nothing else is written.
    assert [c.resource.plural for c in k8s.calls] == ['pods']
    assert not (tmp_path / 'services.yaml').exists()
    assert not (tmp_path / 'kueue').exists()


# ---------------------------------------------------------------------------
# Tests for context_reachable (standalone bounded, no-retry probe)
# ---------------------------------------------------------------------------
def test_context_reachable_true_on_success(k8s):
    """A context whose API server answers the probe is reachable."""
    assert debug.context_reachable('ctx') is True
    # Bounded, namespace-scoped probe (default ns, limit=1), one attempt on a
    # no-retry pool.
    [call] = k8s.calls
    assert call.resource == reads.PODS
    assert (call.context, call.namespace, call.limit) == ('ctx', 'default', 1)
    assert call.consistency == reads.FRESH
    assert call.attempts == 1
    pool_kw = k8s.api_client.rest_client.pool_manager.connection_pool_kw
    assert pool_kw == {'retries': 0}


def test_context_reachable_false_on_connection_error(k8s):
    """No answer (connection error, timeout, known unreachable) means the
    context is defunct."""
    k8s.failures['pods'] = _unreachable_error()

    assert debug.context_reachable('ctx') is False


@pytest.mark.parametrize('error', [_answered_error(403), RuntimeError('boom')])
def test_context_reachable_true_on_non_connection_error(k8s, error):
    """An answered error (e.g. RBAC 403) is reachable-but-restricted, so we
    must NOT wrongly skip collection for it."""
    k8s.failures['pods'] = error

    assert debug.context_reachable('ctx') is True
