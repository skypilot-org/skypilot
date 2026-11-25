"""Tests for the reads in sky/provision/kubernetes/network_utils.py."""
from unittest import mock

import pytest

from sky import exceptions
from sky.provision.kubernetes import network_utils
from sky.provision.kubernetes import reads


class FakeReads:
    """Serves objects keyed by (plural, namespace, name) to the read layer."""

    def __init__(self):
        self.objects = {}
        # (plural, name, kwargs) of each read.
        self.calls = []
        self.not_served = set()

    def get_object(self, context, resource, name, **kwargs):
        del context
        self.calls.append((resource.plural, name, kwargs))
        obj = self.objects.get((resource.plural, kwargs.get('namespace'), name))
        items = [] if obj is None else [obj]
        convert = kwargs.get('convert')
        if convert is not None:
            items = [convert(item) for item in items]
        return reads.ReadResult(items=items,
                                resource_version='',
                                staleness_seconds=0,
                                reason=reads.REASON_FRESH)

    def list_objects(self, context, resource, **kwargs):
        del context
        self.calls.append((resource.plural, None, kwargs))
        if resource.plural in self.not_served:
            raise exceptions.KubernetesResourceNotServedError(resource.plural)
        namespace = kwargs.get('namespace')
        items = [
            obj for (plural, ns, _), obj in self.objects.items()
            if plural == resource.plural and ns == namespace
        ]
        convert = kwargs.get('convert')
        if convert is not None:
            items = [convert(item) for item in items]
        return reads.ReadResult(items=items,
                                resource_version='',
                                staleness_seconds=0,
                                reason=reads.REASON_FRESH)


@pytest.fixture
def fake(monkeypatch):
    fake_reads = FakeReads()
    monkeypatch.setattr(reads, 'get_object', fake_reads.get_object)
    monkeypatch.setattr(reads, 'list_objects', fake_reads.list_objects)
    return fake_reads


def _service(name, namespace='ns', status=None, **spec):
    return {
        'apiVersion': 'v1',
        'kind': 'Service',
        'metadata': {
            'name': name,
            'namespace': namespace
        },
        'spec': spec,
        'status': status or {
            'loadBalancer': {}
        },
    }


@pytest.mark.parametrize('exists', [False, True])
def test_create_or_replace_service(fake, monkeypatch, exists):
    if exists:
        fake.objects[('services', 'ns', 'svc')] = _service('svc')
    core = mock.MagicMock()
    monkeypatch.setattr(network_utils.kubernetes, 'core_api',
                        lambda context: core)

    network_utils.create_or_replace_namespaced_service('ns', 'ctx', 'svc',
                                                       {'spec': 1})

    assert core.replace_namespaced_service.called == exists
    assert core.create_namespaced_service.called == (not exists)
    _, _, kwargs = fake.calls[0]
    assert kwargs['consistency'] == reads.FRESH


@pytest.mark.parametrize('exists', [False, True])
def test_create_or_replace_ingress(fake, monkeypatch, exists):
    if exists:
        fake.objects[('ingresses', 'ns', 'ing')] = {'metadata': {'name': 'ing'}}
    networking = mock.MagicMock()
    monkeypatch.setattr(network_utils.kubernetes, 'networking_api',
                        lambda context: networking)

    network_utils.create_or_replace_namespaced_ingress('ns', 'ctx', 'ing',
                                                       {'spec': 1})

    assert networking.replace_namespaced_ingress.called == exists
    assert networking.create_namespaced_ingress.called == (not exists)
    _, _, kwargs = fake.calls[0]
    assert kwargs['consistency'] == reads.FRESH


def test_create_or_replace_service_propagates_read_failure(monkeypatch, fake):
    del fake

    def fail(*args, **kwargs):
        raise exceptions.KubeAPIUnreachableError('HTTP 500')

    monkeypatch.setattr(reads, 'get_object', fail)
    core = mock.MagicMock()
    monkeypatch.setattr(network_utils.kubernetes, 'core_api',
                        lambda context: core)

    with pytest.raises(exceptions.KubeAPIUnreachableError):
        network_utils.create_or_replace_namespaced_service(
            'ns', 'ctx', 'svc', {})
    core.create_namespaced_service.assert_not_called()


def test_ingress_controller_exists(fake):
    fake.objects[('ingressclasses', None, 'nginx')] = {
        'metadata': {
            'name': 'nginx'
        }
    }
    assert network_utils.ingress_controller_exists('ctx')
    assert not network_utils.ingress_controller_exists('ctx', 'traefik')


def test_ingress_controller_absent_when_ingress_classes_not_served(fake):
    fake.not_served.add('ingressclasses')
    assert not network_utils.ingress_controller_exists('ctx')


def test_ingress_external_ip_and_ports(fake):
    fake.objects[('services', 'ingress-nginx',
                  'other')] = _service('other', 'ingress-nginx')
    fake.objects[('services', 'ingress-nginx',
                  'ingress-nginx-controller')] = _service(
                      'ingress-nginx-controller',
                      'ingress-nginx',
                      ports=[{
                          'name': 'http',
                          'port': 80,
                          'nodePort': 30080
                      }, {
                          'name': 'https',
                          'port': 443,
                          'nodePort': 30443
                      }])

    assert network_utils.get_ingress_external_ip_and_ports('ctx') == (
        'localhost', (30080, 30443))


def test_ingress_external_ip_without_controller(fake):
    del fake
    assert network_utils.get_ingress_external_ip_and_ports('ctx') == (None,
                                                                      None)


def test_loadbalancer_ip_of_missing_service_does_not_wait(fake, monkeypatch):
    sleep = mock.Mock()
    monkeypatch.setattr(network_utils.time, 'sleep', sleep)

    assert network_utils.get_loadbalancer_ip('ctx', 'ns', 'svc',
                                             timeout=60) is None
    sleep.assert_not_called()
    assert len(fake.calls) == 1


def test_loadbalancer_ip_polls_fresh_until_assigned(fake, monkeypatch):
    fake.objects[('services', 'ns', 'svc')] = _service('svc')

    def assign(_):
        fake.objects[('services', 'ns', 'svc')] = _service(
            'svc', status={'loadBalancer': {
                'ingress': [{
                    'ip': '1.2.3.4'
                }]
            }})

    monkeypatch.setattr(network_utils.time, 'sleep', assign)

    assert network_utils.get_loadbalancer_ip('ctx', 'ns', 'svc',
                                             timeout=60) == '1.2.3.4'
    assert len(fake.calls) == 2
    for _, _, kwargs in fake.calls:
        assert kwargs['consistency'] == reads.FRESH
        assert kwargs['attempts'] == 1


def test_pod_ip(fake):
    assert network_utils.get_pod_ip('ctx', 'ns', 'pod') is None
    fake.objects[('pods', 'ns', 'pod')] = {'status': {}}
    assert network_utils.get_pod_ip('ctx', 'ns', 'pod') is None
    fake.objects[('pods', 'ns', 'pod')] = {'status': {'podIP': '10.0.0.1'}}
    assert network_utils.get_pod_ip('ctx', 'ns', 'pod') == '10.0.0.1'
