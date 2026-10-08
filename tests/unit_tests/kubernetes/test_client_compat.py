"""Client-generation compatibility; run with both kubernetes==20 and ==37."""

import copy
import pathlib
from types import SimpleNamespace
from typing import Any, Dict, Optional
from unittest import mock

from kubernetes import client
from kubernetes.config.incluster_config import InClusterConfigLoader
import pytest

from sky.adaptors import kubernetes
from sky.provision.kubernetes import network_utils
from sky.provision.kubernetes import utils


@pytest.mark.parametrize('dict_type',
                         ['dict(str, str)', 'dict[str, str]', 'Dict[str, str]'])
@pytest.mark.parametrize('list_type',
                         ['list[V1Container]', 'List[V1Container]'])
def test_pod_validator_type_formats(monkeypatch: pytest.MonkeyPatch,
                                    dict_type: str, list_type: str) -> None:
    monkeypatch.setitem(client.V1ObjectMeta.openapi_types, 'labels', dict_type)
    monkeypatch.setitem(client.V1PodSpec.openapi_types, 'containers', list_type)
    pod = {
        'metadata': {
            'labels': {
                'app': 'test'
            }
        },
        'spec': {
            'containers': [{
                'name': 'main',
                'image': 'busybox'
            }]
        },
    }
    assert utils.check_pod_config(pod) == (True, None)
    # Recursion must still reject unknown fields, not merely skip the list.
    pod['spec']['containers'][0]['unknownField'] = 'invalid'
    valid, error = utils.check_pod_config(pod)
    assert not valid
    assert error is not None
    assert 'spec.containers.unknownField' in error


@pytest.mark.parametrize(('model_name', 'manifest'), [
    ('V1Role', {
        'metadata': {
            'name': 'role'
        },
        'rules': [{
            'apiGroups': [''],
            'resources': ['pods'],
            'verbs': ['get']
        }]
    }),
    ('V1ClusterRole', {
        'metadata': {
            'name': 'role'
        },
        'rules': [{
            'apiGroups': [''],
            'resources': ['nodes'],
            'verbs': ['list']
        }]
    }),
    ('V1RoleBinding', {
        'metadata': {
            'name': 'binding'
        },
        'roleRef': {
            'apiGroup': 'rbac.authorization.k8s.io',
            'kind': 'Role',
            'name': 'role'
        },
        'subjects': [{
            'kind': 'ServiceAccount',
            'name': 'sky',
            'namespace': 'default'
        }]
    }),
    ('V1ClusterRoleBinding', {
        'metadata': {
            'name': 'binding'
        },
        'roleRef': {
            'apiGroup': 'rbac.authorization.k8s.io',
            'kind': 'ClusterRole',
            'name': 'role'
        },
        'subjects': [{
            'kind': 'ServiceAccount',
            'name': 'sky',
            'namespace': 'default'
        }]
    }),
    ('V1Service', {
        'metadata': {
            'name': 'service'
        },
        'spec': {
            'ports': [{
                'port': 80,
                'targetPort': 8080
            }],
            'selector': {
                'app': 'test'
            }
        }
    }),
])
def test_dict_to_k8s_object(model_name: str, manifest: Dict[str, Any]) -> None:
    original = copy.deepcopy(manifest)
    with client.ApiClient() as api_client, mock.patch.object(
            kubernetes, 'api_client', return_value=api_client):
        obj = utils.dict_to_k8s_object(manifest, model_name)
        assert isinstance(obj, getattr(client, model_name))
        assert api_client.sanitize_for_serialization(obj) == manifest
    assert manifest == original


@pytest.mark.parametrize('attribute', ['external_i_ps', 'external_ips'])
@pytest.mark.parametrize(('ips', 'annotations', 'expected'), [
    (['192.0.2.1'], {
        'skypilot.co/external-ip': '192.0.2.2'
    }, '192.0.2.1'),
    (None, {
        'skypilot.co/external-ip': '192.0.2.2'
    }, '192.0.2.2'),
    ([], {
        'skypilot.co/external-ip': '192.0.2.2'
    }, '192.0.2.2'),
    (None, None, 'localhost'),
])
def test_ingress_external_ip_names(attribute: str, ips: Optional[list],
                                   annotations: Optional[dict],
                                   expected: str) -> None:
    service = SimpleNamespace(
        metadata=SimpleNamespace(name='ingress-nginx-controller',
                                 annotations=annotations),
        spec=SimpleNamespace(
            **{
                attribute: ips,
                'ports': [
                    SimpleNamespace(name='http', node_port=30080),
                    SimpleNamespace(name='https', node_port=30443)
                ],
            }),
        status=SimpleNamespace(load_balancer=SimpleNamespace(ingress=None)))
    with mock.patch.object(kubernetes, 'core_api') as core_api:
        core_api.return_value.list_namespaced_service.return_value.items = [
            service
        ]
        assert network_utils.get_ingress_external_ip_and_ports(None) == (
            expected, (30080, 30443))


def test_ingress_external_ip_installed_client() -> None:
    with client.ApiClient() as api_client, mock.patch.object(
            kubernetes, 'api_client', return_value=api_client):
        service = utils.dict_to_k8s_object(
            {
                'metadata': {
                    'name': 'ingress-nginx-controller'
                },
                'spec': {
                    'externalIPs': ['192.0.2.1'],
                    'ports': [{
                        'name': 'http',
                        'port': 80,
                        'nodePort': 30080
                    }, {
                        'name': 'https',
                        'port': 443,
                        'nodePort': 30443
                    }]
                },
                'status': {
                    'loadBalancer': {}
                },
            }, 'V1Service')
    with mock.patch.object(kubernetes, 'core_api') as core_api:
        core_api.return_value.list_namespaced_service.return_value.items = [
            service
        ]
        assert network_utils.get_ingress_external_ip_and_ports(None) == (
            '192.0.2.1', (30080, 30443))


def test_normalize_pod_resource_quantities() -> None:
    resources = {
        'requests': {
            'cpu': 0.5,
            'memory': '1Gi',
            'nvidia.com/gpu': 1
        },
        'limits': {
            'cpu': 2,
            'nvidia.com/gpu': 1
        },
        'claims': [{
            'name': 'device'
        }]
    }
    spec = {'resources': copy.deepcopy(resources)}
    for field in ('containers', 'initContainers', 'ephemeralContainers'):
        spec[field] = [{
            'name': 'main',
            'resources': copy.deepcopy(resources)
        }, {
            'name': 'no-resources'
        }]
    spec['containers'][0]['ports'] = [{'containerPort': 8080}]
    spec['securityContext'] = {'runAsUser': 1000}
    expected = {
        'requests': {
            'cpu': '0.5',
            'memory': '1Gi',
            'nvidia.com/gpu': '1'
        },
        'limits': {
            'cpu': '2',
            'nvidia.com/gpu': '1'
        },
        'claims': [{
            'name': 'device'
        }]
    }
    utils.normalize_pod_resource_quantities(spec)
    assert spec['resources'] == expected
    for field in ('containers', 'initContainers', 'ephemeralContainers'):
        assert spec[field][0]['resources'] == expected
        assert spec[field][1] == {'name': 'no-resources'}
    assert spec['containers'][0]['ports'] == [{'containerPort': 8080}]
    assert spec['securityContext'] == {'runAsUser': 1000}
    normalized = copy.deepcopy(spec)
    utils.normalize_pod_resource_quantities(spec)
    assert spec == normalized


def test_normalize_pod_resource_quantities_preserves_invalid_values() -> None:
    spec = {
        'initContainers': None,
        'resources': None,
        'containers': [{
            'resources': {
                'requests': {
                    'cpu': None
                },
                'limits': {
                    'cpu': True
                }
            }
        }]
    }
    original = copy.deepcopy(spec)
    utils.normalize_pod_resource_quantities(spec)
    assert spec == original


def test_legacy_bearer_token_prefix() -> None:
    configuration = client.Configuration()
    configuration.api_key['authorization'] = 'fake-token'
    configuration.api_key_prefix['authorization'] = 'Bearer'
    assert configuration.auth_settings()['BearerToken']['value'] == (
        'Bearer fake-token')


def test_incluster_auth(tmp_path: pathlib.Path) -> None:
    token = tmp_path / 'token'
    certificate = tmp_path / 'ca.crt'
    token.write_text('fake-token')
    certificate.write_text('fake-certificate')
    configuration = client.Configuration()
    InClusterConfigLoader(token_filename=str(token),
                          cert_filename=str(certificate),
                          environ={
                              'KUBERNETES_SERVICE_HOST': '127.0.0.1',
                              'KUBERNETES_SERVICE_PORT': '443'
                          }).load_and_set(configuration)
    assert configuration.auth_settings()['BearerToken']['value'] == (
        'bearer fake-token')
