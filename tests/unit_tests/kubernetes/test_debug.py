"""Tests for sky.provision.kubernetes.debug (cluster resource dump)."""
from types import SimpleNamespace
from typing import Dict
from unittest import mock

import pytest

from sky.provision.kubernetes import debug
import sky.utils.yaml_utils as yaml_utils


class _FakeApiException(Exception):
    """Stand-in for kubernetes.client.rest.ApiException."""

    def __init__(self, status):
        self.status = status
        super().__init__(f'status={status}')


class _FakeMaxRetryError(Exception):
    """Stand-in for urllib3's MaxRetryError (a connection/timeout failure)."""


def _obj(marker):
    """A fake k8s model object; sanitize_for_serialization keys off _marker."""
    return SimpleNamespace(_marker=marker)


def _list(*markers):
    return SimpleNamespace(items=[_obj(m) for m in markers])


def _pods(*names, kueue=False):
    """A fake pod list (for list_namespaced_pod). With kueue=True the pods carry
    the queue-name label that marks the cluster as using Kueue."""
    labels = {'kueue.x-k8s.io/queue-name': 'q'} if kueue else {}
    return SimpleNamespace(items=[
        SimpleNamespace(_marker='pod',
                        metadata=SimpleNamespace(name=n, labels=dict(labels)))
        for n in names
    ])


def _events(*involved_pod_names):
    """A fake event list (for list_namespaced_event); each references a pod."""
    return SimpleNamespace(items=[
        SimpleNamespace(_marker='evt', involved_object=SimpleNamespace(name=n))
        for n in involved_pod_names
    ])


def _all_ns_pods(*ns_name_pairs):
    """A fake cluster-wide pod list (for list_pod_for_all_namespaces); each item
    is a (namespace, name) pair."""
    return SimpleNamespace(items=[
        SimpleNamespace(_marker='pod',
                        metadata=SimpleNamespace(namespace=ns, name=name))
        for ns, name in ns_name_pairs
    ])


@pytest.fixture
def k8s_apis(monkeypatch):
    """Patch every kubernetes adaptor call the module makes.

    Returns the individual API mocks so each test configures its own return
    values; defaults are empty lists / a happy-path pod read.
    """
    core = mock.MagicMock()
    core.list_namespaced_pod.return_value = _pods()
    core.list_pod_for_all_namespaces.return_value = _all_ns_pods()
    core.list_namespaced_event.return_value = _events()
    core.list_namespaced_service.return_value = _list()
    core.list_namespaced_persistent_volume_claim.return_value = _list()
    core.list_namespaced_config_map.return_value = _list()
    core.list_namespaced_secret.return_value = _list()
    core.read_namespaced_config_map.side_effect = _FakeApiException(404)

    apps = mock.MagicMock()
    apps.list_namespaced_deployment.return_value = _list()
    apps.list_daemon_set_for_all_namespaces.return_value = _list()

    networking = mock.MagicMock()
    networking.list_namespaced_ingress.return_value = _list()

    custom = mock.MagicMock()
    # Kueue objects keyed by plural; tests populate this. Both the namespaced
    # and cluster-scoped list calls dispatch on the `plural` kwarg, so the probe
    # (workloads) succeeds by default -> Kueue is treated as installed.
    kueue_items: Dict[str, list] = {}

    def _list_custom(*args, **kwargs):
        del args
        return {'items': list(kueue_items.get(kwargs['plural'], []))}

    custom.list_namespaced_custom_object.side_effect = _list_custom
    custom.list_cluster_custom_object.side_effect = _list_custom

    api_client = mock.MagicMock()
    # kubectl-style serialization: just surface the object's marker.
    api_client.sanitize_for_serialization.side_effect = (lambda obj: {
        'marker': obj._marker
    })

    monkeypatch.setattr('sky.adaptors.kubernetes.core_api',
                        lambda *a, **k: core)
    monkeypatch.setattr('sky.adaptors.kubernetes.apps_api',
                        lambda *a, **k: apps)
    monkeypatch.setattr('sky.adaptors.kubernetes.networking_api',
                        lambda *a, **k: networking)
    monkeypatch.setattr('sky.adaptors.kubernetes.custom_objects_api',
                        lambda *a, **k: custom)
    monkeypatch.setattr('sky.adaptors.kubernetes.api_client',
                        lambda *a, **k: api_client)
    monkeypatch.setattr('sky.adaptors.kubernetes.api_exception',
                        lambda: _FakeApiException)
    monkeypatch.setattr('sky.adaptors.kubernetes.max_retry_error',
                        lambda: _FakeMaxRetryError)

    return SimpleNamespace(core=core,
                           apps=apps,
                           networking=networking,
                           custom=custom,
                           kueue_items=kueue_items,
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


def test_dumps_pods_with_type_meta(tmp_path, k8s_apis):
    # Pods come from one list-by-label call (head + workers).
    k8s_apis.core.list_namespaced_pod.return_value = _pods(
        'c-head', 'c-worker1')

    errors = _run(tmp_path)

    assert not errors
    for name in ('c-head', 'c-worker1'):
        dumped = yaml_utils.read_yaml(str(tmp_path / 'pods' / f'{name}.yaml'))
        # List items lack TypeMeta, so apiVersion/kind are stamped back on.
        assert (dumped['apiVersion'], dumped['kind']) == ('v1', 'Pod')
        assert dumped['marker'] == 'pod'
    # One list call, scoped to this cluster by label.
    _, kwargs = k8s_apis.core.list_namespaced_pod.call_args
    assert kwargs['label_selector'] == 'skypilot-cluster-name=cluster-abc'


def test_events_filtered_to_cluster_pods_and_grouped(tmp_path, k8s_apis):
    k8s_apis.core.list_namespaced_pod.return_value = _pods(
        'c-head', 'c-worker1')
    # Namespace-wide pod events: two for c-head, none for c-worker1, one for an
    # unrelated pod.
    k8s_apis.core.list_namespaced_event.return_value = _events(
        'c-head', 'c-head', 'other-cluster-pod')

    _run(tmp_path)

    # Only our pods' events are written, grouped per pod; the unrelated pod and
    # the pod with no events produce no file.
    assert (tmp_path / 'events' / 'c-head.yaml').exists()
    assert not (tmp_path / 'events' / 'c-worker1.yaml').exists()
    assert not (tmp_path / 'events' / 'other-cluster-pod.yaml').exists()
    # The query is one namespace-wide call narrowed to Pod events server-side.
    _, kwargs = k8s_apis.core.list_namespaced_event.call_args
    assert kwargs['field_selector'] == 'involvedObject.kind=Pod'
    # c-head got both of its events (multi-doc YAML).
    docs = yaml_utils.read_yaml_all(str(tmp_path / 'events' / 'c-head.yaml'))
    assert len(docs) == 2
    assert docs[0]['kind'] == 'Event'


def test_labeled_resources_use_cluster_label(tmp_path, k8s_apis):
    k8s_apis.core.list_namespaced_service.return_value = _list('svc')

    _run(tmp_path)

    assert (tmp_path / 'services.yaml').exists()
    _, kwargs = k8s_apis.core.list_namespaced_service.call_args
    assert kwargs['label_selector'] == 'skypilot-cluster-name=cluster-abc'


def test_labeled_resources_carry_type_meta(tmp_path, k8s_apis):
    """List-sourced objects get apiVersion/kind stamped on (k8s omits TypeMeta
    on items returned from a list_* call), so the dump matches kubectl -o yaml.
    """
    k8s_apis.core.list_namespaced_service.return_value = _list('svc')
    k8s_apis.apps.list_namespaced_deployment.return_value = _list('dep')

    _run(tmp_path)

    svc = yaml_utils.read_yaml(str(tmp_path / 'services.yaml'))
    assert svc['apiVersion'] == 'v1'
    assert svc['kind'] == 'Service'
    # The original serialized fields are preserved alongside the stamped meta.
    assert svc['marker'] == 'svc'
    # apiVersion/kind lead the document (dump preserves insertion order).
    assert list(svc)[:2] == ['apiVersion', 'kind']

    dep = yaml_utils.read_yaml(str(tmp_path / 'deployments.yaml'))
    assert (dep['apiVersion'], dep['kind']) == ('apps/v1', 'Deployment')


def test_empty_resource_kinds_are_skipped(tmp_path, k8s_apis):
    # All listers default to empty -> no resource files written.
    _run(tmp_path)

    for name in ('services.yaml', 'deployments.yaml', 'config_maps.yaml',
                 'persistent_volume_claims.yaml', 'ingresses.yaml',
                 'secrets.yaml'):
        assert not (tmp_path / name).exists()
    # Kueue probe succeeds (installed) but there are no objects -> no kueue dir.
    assert not (tmp_path / 'kueue').exists()


def test_per_cluster_makes_no_cluster_scoped_calls(tmp_path, k8s_apis):
    """RBAC invariant: the per-cluster dump must stay namespace-scoped so it
    works under SkyPilot's minimal (namespace-only) RBAC -- it must never make a
    cluster-wide call (those live only in the per-context path)."""
    # A cluster that uses Kueue exercises the custom-object path too.
    k8s_apis.core.list_namespaced_pod.return_value = _pods('c-head', kueue=True)
    k8s_apis.kueue_items['workloads'] = [{'metadata': {'name': 'wl'}}]

    _run(tmp_path)

    k8s_apis.core.list_pod_for_all_namespaces.assert_not_called()
    k8s_apis.custom.list_cluster_custom_object.assert_not_called()


def test_context_gpu_metrics_pods_dumped(tmp_path, k8s_apis):
    """The metrics Prometheus server, the chart's kube-state-metrics, and the
    DCGM exporter (any ns, by name substring) are captured under gpu_metrics/
    from one cluster-wide list; unrelated pods are excluded."""
    k8s_apis.core.list_pod_for_all_namespaces.return_value = _all_ns_pods(
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
    # and the config/PVC/DaemonSet are absent (404 / empty) in this fixture.
    assert sorted(p.name for p in gdir.iterdir()) == [
        'dcgm-exporter.yaml', 'kube-state-metrics.yaml',
        'prometheus-server.yaml'
    ]


def test_context_gpu_metrics_config_and_topology_dumped(tmp_path, k8s_apis):
    """The prometheus ConfigMap (rendered prometheus.yml), the Prometheus
    PVC(s), and the DCGM-exporter DaemonSet are captured under gpu_metrics/
    alongside the pods -- the config + topology behind the metrics pipeline."""
    k8s_apis.core.read_namespaced_config_map.side_effect = None
    k8s_apis.core.read_namespaced_config_map.return_value = _obj('cm')
    k8s_apis.core.list_namespaced_persistent_volume_claim.return_value = _list(
        'pvc')
    k8s_apis.apps.list_daemon_set_for_all_namespaces.return_value = (
        SimpleNamespace(items=[
            SimpleNamespace(_marker='ds',
                            metadata=SimpleNamespace(
                                name='nvidia-dcgm-exporter')),
            SimpleNamespace(_marker='unrelated',
                            metadata=SimpleNamespace(name='some-other-ds')),
        ]))

    errors = _run_context(tmp_path)

    assert not errors
    gdir = tmp_path / 'gpu_metrics'
    cm = yaml_utils.read_yaml(str(gdir / 'prometheus-config.yaml'))
    assert (cm['apiVersion'], cm['kind']) == ('v1', 'ConfigMap')
    pvc = yaml_utils.read_yaml(str(gdir / 'prometheus-pvcs.yaml'))
    assert (pvc['apiVersion'], pvc['kind']) == ('v1', 'PersistentVolumeClaim')
    ds = yaml_utils.read_yaml(str(gdir / 'dcgm-exporter-daemonset.yaml'))
    assert (ds['apiVersion'], ds['kind']) == ('apps/v1', 'DaemonSet')
    assert ds['marker'] == 'ds'  # only the dcgm-exporter DS, not some-other-ds


def test_context_gpu_metrics_absent_produces_no_dir(tmp_path, k8s_apis):
    k8s_apis.core.list_pod_for_all_namespaces.return_value = _all_ns_pods(
        ('default', 'unrelated-pod'))

    _run_context(tmp_path)

    assert not (tmp_path / 'gpu_metrics').exists()


def test_context_empty_writes_only_reachable_marker(tmp_path, k8s_apis):
    # No GPU-metrics pods, no Kueue objects -> a reachable-but-bare context
    # writes no resource dirs, but still drops a context.json marker (so the
    # context stays visible in the dump even though the zip omits empty dirs).
    errors = _run_context(tmp_path)

    assert not errors
    assert not (tmp_path / 'gpu_metrics').exists()
    assert not (tmp_path / 'kueue').exists()
    marker = yaml_utils.read_yaml(str(tmp_path / 'context.json'))
    assert marker == {'context': 'ctx', 'reachable': True}


def test_secret_values_are_redacted(tmp_path, k8s_apis):
    k8s_apis.core.list_namespaced_secret.return_value = _list('secret')
    # The real sanitize_for_serialization would surface the secret's data,
    # stringData, and the last-applied-configuration annotation (which embeds
    # the data); emulate that so the redaction path has something to scrub.
    last_applied = 'kubectl.kubernetes.io/last-applied-configuration'
    k8s_apis.api_client.sanitize_for_serialization.side_effect = lambda obj: {
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

    errors = _run(tmp_path)

    assert not errors
    dumped = yaml_utils.read_yaml(str(tmp_path / 'secrets.yaml'))
    assert dumped['data'] == {'token': '<redacted>', 'password': '<redacted>'}
    assert dumped['stringData'] == {'note': '<redacted>'}
    # The data-bearing annotation is redacted; unrelated annotations are kept.
    assert dumped['metadata']['annotations'][last_applied] == '<redacted>'
    assert dumped['metadata']['annotations']['keep-me'] == 'ok'


def test_kueue_workload_dumped_scoped_to_cluster(tmp_path, k8s_apis):
    k8s_apis.core.list_namespaced_pod.return_value = _pods('c-head', kueue=True)
    # Include managedFields to confirm it's stripped from custom objects too.
    k8s_apis.kueue_items['workloads'] = [{
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
    # The Workload is scoped to this cluster by name (Kueue names the pod-group
    # Workload after the cluster), newest version first.
    wl_calls = [
        c for c in k8s_apis.custom.list_namespaced_custom_object.call_args_list
        if c.kwargs['plural'] == 'workloads' and 'field_selector' in c.kwargs
    ]
    assert wl_calls
    assert wl_calls[0].kwargs['version'] == 'v1beta2'
    assert wl_calls[0].kwargs['field_selector'] == 'metadata.name=cluster-abc'


def test_context_dumps_queues_flavors_and_topologies(tmp_path, k8s_apis):
    """The cluster-wide Kueue config -- LocalQueues, ClusterQueues,
    ResourceFlavors, Topologies -- is captured once per context under kueue/,
    all via the cluster-scoped list endpoint."""
    k8s_apis.kueue_items.update({
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
    # Every kind -- including the namespaced LocalQueue -- is fetched via the
    # cluster-scoped (not namespaced) API, so one call per kind covers all
    # namespaces; the per-context path makes no namespaced custom-object calls.
    cluster_plurals = {
        c.kwargs['plural']
        for c in k8s_apis.custom.list_cluster_custom_object.call_args_list
    }
    assert {'localqueues', 'clusterqueues', 'resourceflavors', 'topologies'
           } <= cluster_plurals
    # 'workloads' is per-cluster, never fetched here.
    assert 'workloads' not in cluster_plurals
    k8s_apis.custom.list_namespaced_custom_object.assert_not_called()


def test_context_kueue_uses_cluster_scoped_probe_and_falls_back(
        tmp_path, k8s_apis):
    """The per-context Kueue version probe is cluster-scoped (clusterqueues) and
    falls back v1beta2 -> v1beta1, keeping the per-context path off the
    namespaced API entirely."""
    k8s_apis.kueue_items['clusterqueues'] = [{'metadata': {'name': 'cq'}}]

    def _cluster(*args, **kwargs):
        del args
        if kwargs['version'] == 'v1beta2':
            raise _FakeApiException(status=404)
        return {'items': list(k8s_apis.kueue_items.get(kwargs['plural'], []))}

    k8s_apis.custom.list_cluster_custom_object.side_effect = _cluster

    errors = _run_context(tmp_path)

    assert not errors
    assert (tmp_path / 'kueue' / 'clusterqueues.yaml').exists()
    # The probe is the cluster-scoped clusterqueues list, tried newest-first.
    probe_versions = [
        c.kwargs['version']
        for c in k8s_apis.custom.list_cluster_custom_object.call_args_list
        if c.kwargs['plural'] == 'clusterqueues'
    ]
    assert probe_versions[:2] == ['v1beta2', 'v1beta1']
    k8s_apis.custom.list_namespaced_custom_object.assert_not_called()


def test_context_kueue_non_404_failure_is_recorded(tmp_path, k8s_apis):
    # A non-404 on the cluster-scoped probe is recorded under the 'kueue'
    # resource (context-relative, no 'kubernetes/' prefix).
    k8s_apis.custom.list_cluster_custom_object.side_effect = (_FakeApiException(
        status=500))

    errors = _run_context(tmp_path)

    assert len(errors) == 1
    assert errors[0]['resource'] == 'kueue'


def test_context_broken_skips_kueue(tmp_path, k8s_apis):
    """A defunct context (connection/timeout on the first cluster-wide call)
    fast-fails: it records the failure and skips the remaining cluster-wide
    calls rather than stacking timeouts."""
    k8s_apis.core.list_pod_for_all_namespaces.side_effect = _FakeMaxRetryError(
        'connection refused')

    errors = _run_context(tmp_path)

    # The reachability failure is recorded against gpu_metrics...
    assert len(errors) == 1
    assert errors[0]['resource'] == 'gpu_metrics'
    # ...and the Kueue calls are skipped entirely (no stacked timeouts).
    k8s_apis.custom.list_cluster_custom_object.assert_not_called()
    assert not (tmp_path / 'kueue').exists()
    # The marker still records the context as unreachable, so a defunct context
    # is visible in the dump rather than silently absent.
    marker = yaml_utils.read_yaml(str(tmp_path / 'context.json'))
    assert marker == {'context': 'ctx', 'reachable': False}


def test_kueue_falls_back_to_older_version(tmp_path, k8s_apis):
    # v1beta2 isn't served (404); the version probe falls back to v1beta1.
    k8s_apis.core.list_namespaced_pod.return_value = _pods('c-head', kueue=True)
    k8s_apis.kueue_items['workloads'] = [{'metadata': {'name': 'wl'}}]

    def _ns(*args, **kwargs):
        del args
        if kwargs['version'] == 'v1beta2':
            raise _FakeApiException(status=404)
        return {'items': list(k8s_apis.kueue_items.get(kwargs['plural'], []))}

    def _cluster(*args, **kwargs):
        del args
        return {'items': list(k8s_apis.kueue_items.get(kwargs['plural'], []))}

    k8s_apis.custom.list_namespaced_custom_object.side_effect = _ns
    k8s_apis.custom.list_cluster_custom_object.side_effect = _cluster

    errors = _run(tmp_path)

    assert not errors
    assert (tmp_path / 'kueue' / 'workloads.yaml').exists()
    versions = [
        c.kwargs['version']
        for c in k8s_apis.custom.list_namespaced_custom_object.call_args_list
    ]
    # Probe tried v1beta2 (404) then resolved v1beta1; the dump used v1beta1.
    assert versions[:2] == ['v1beta2', 'v1beta1']
    assert all(v == 'v1beta1' for v in versions[1:])


def test_kueue_crd_absent_is_not_an_error(tmp_path, k8s_apis):
    # Cluster uses Kueue (labeled pods), but the CRD isn't served at any version
    # -> Kueue isn't actually installed; skip without error.
    k8s_apis.core.list_namespaced_pod.return_value = _pods('c-head', kueue=True)
    k8s_apis.custom.list_namespaced_custom_object.side_effect = (
        _FakeApiException(status=404))

    errors = _run(tmp_path)

    assert not errors
    assert not (tmp_path / 'kueue').exists()
    # Only the version probe ran (both versions) before giving up; no list of
    # cluster-scoped kinds was attempted.
    assert k8s_apis.custom.list_namespaced_custom_object.call_count == 2
    k8s_apis.custom.list_cluster_custom_object.assert_not_called()


def test_kueue_non_404_failure_is_recorded(tmp_path, k8s_apis):
    k8s_apis.core.list_namespaced_pod.return_value = _pods('c-head', kueue=True)
    k8s_apis.custom.list_namespaced_custom_object.side_effect = (
        _FakeApiException(status=500))

    errors = _run(tmp_path)

    assert len(errors) == 1
    assert errors[0]['resource'] == 'kubernetes/kueue/workloads'


def test_kueue_skipped_when_cluster_not_using_kueue(tmp_path, k8s_apis):
    """Kueue may be installed cluster-wide, but if this cluster's pods don't
    carry the queue-name label we don't touch Kueue at all -- no kueue/ dir, no
    custom-objects API calls."""
    k8s_apis.core.list_namespaced_pod.return_value = _pods('c-head')  # no label
    # Even with Kueue objects present cluster-wide...
    k8s_apis.kueue_items['clusterqueues'] = [{'metadata': {'name': 'cq'}}]

    errors = _run(tmp_path)

    assert not errors
    assert not (tmp_path / 'kueue').exists()
    k8s_apis.custom.list_namespaced_custom_object.assert_not_called()
    k8s_apis.custom.list_cluster_custom_object.assert_not_called()


def test_pod_list_failure_is_recorded(tmp_path, k8s_apis):
    # A non-connection error (e.g. a transient/unexpected failure) is recorded,
    # but the context is still reachable, so the dump continues for everything
    # else (labeled resources, Kueue).
    k8s_apis.core.list_namespaced_pod.side_effect = RuntimeError('boom')

    errors = _run(tmp_path)

    pod_errs = [e for e in errors if e['resource'] == 'kubernetes/pods']
    assert len(pod_errs) == 1
    assert pod_errs[0]['error'] == 'boom'
    assert 'traceback' in pod_errs[0]
    # The dump continued past pods (labeled-resource sweep was attempted).
    k8s_apis.core.list_namespaced_service.assert_called()


def test_defunct_context_fast_fails_per_cluster(tmp_path, k8s_apis):
    """If the pod list -- the first call -- can't even connect, the context is
    defunct; the per-cluster dump must bail immediately rather than stack one
    timeout per remaining call (services, deployments, ... Kueue)."""
    k8s_apis.core.list_namespaced_pod.side_effect = _FakeMaxRetryError(
        'connection timed out')

    errors = _run(tmp_path)

    # The connection failure is recorded against pods...
    assert len(errors) == 1
    assert errors[0]['resource'] == 'kubernetes/pods'
    # ...and none of the cluster's other calls are attempted (no stacked
    # timeouts), so nothing else is written.
    k8s_apis.core.list_namespaced_service.assert_not_called()
    k8s_apis.apps.list_namespaced_deployment.assert_not_called()
    k8s_apis.core.list_namespaced_event.assert_not_called()
    k8s_apis.custom.list_namespaced_custom_object.assert_not_called()
    assert not (tmp_path / 'services.yaml').exists()
    assert not (tmp_path / 'kueue').exists()
