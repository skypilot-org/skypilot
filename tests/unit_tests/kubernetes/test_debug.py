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


@pytest.fixture
def k8s_apis(monkeypatch):
    """Patch every kubernetes adaptor call the module makes.

    Returns the individual API mocks so each test configures its own return
    values; defaults are empty lists / a happy-path pod read.
    """
    core = mock.MagicMock()
    core.list_namespaced_pod.return_value = _pods()
    core.list_namespaced_event.return_value = _events()
    core.list_namespaced_service.return_value = _list()
    core.list_namespaced_persistent_volume_claim.return_value = _list()
    core.list_namespaced_config_map.return_value = _list()
    core.list_namespaced_secret.return_value = _list()

    apps = mock.MagicMock()
    apps.list_namespaced_deployment.return_value = _list()

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

    return SimpleNamespace(core=core,
                           apps=apps,
                           networking=networking,
                           custom=custom,
                           kueue_items=kueue_items,
                           api_client=api_client)


def _run(tmp_path):
    return debug.dump_cluster_resources(context='ctx',
                                        namespace='ns',
                                        cluster_name_on_cloud='cluster-abc',
                                        output_dir=str(tmp_path))


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


def test_kueue_dumps_queues_flavors_and_topologies(tmp_path, k8s_apis):
    """Beyond the Workload, the namespace's LocalQueues and the cluster-scoped
    ClusterQueues/ResourceFlavors/Topologies are captured under kueue/."""
    k8s_apis.core.list_namespaced_pod.return_value = _pods('c-head', kueue=True)
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

    errors = _run(tmp_path)

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
    # Cluster-scoped kinds are fetched via the cluster (not namespaced) API.
    cluster_plurals = {
        c.kwargs['plural']
        for c in k8s_apis.custom.list_cluster_custom_object.call_args_list
    }
    assert {'clusterqueues', 'resourceflavors', 'topologies'} <= cluster_plurals


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
    k8s_apis.core.list_namespaced_pod.side_effect = RuntimeError('boom')

    errors = _run(tmp_path)

    # The pod-list failure is recorded; the dump still continues for everything
    # else (labeled resources, Kueue).
    pod_errs = [e for e in errors if e['resource'] == 'kubernetes/pods']
    assert len(pod_errs) == 1
    assert pod_errs[0]['error'] == 'boom'
    assert 'traceback' in pod_errs[0]
