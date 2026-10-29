"""Unit tests for sky.provision.runpod.utils."""
from unittest import mock

import pytest

from sky.adaptors import runpod
from sky.provision.runpod import utils as runpod_utils


class _FakeRest:
    """Records REST calls and serves canned responses keyed by (method, path).

    Paths are matched exactly, so list endpoints ('/pods', '/templates') must
    be seeded with the full v2 list response including ``pagination``.
    """

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, method, path, json=None, params=None):
        self.calls.append((method, path, json, params))
        try:
            return self.responses[(method, path)]
        except KeyError as e:
            raise AssertionError(f'Unexpected call {method} {path}') from e


def _list_response(key, items):
    return {
        key: items,
        'pagination': {
            'nextCursor': None,
            'hasNextPage': False
        }
    }


@pytest.fixture
def fake_rest(monkeypatch):

    def _install(responses):
        rest = _FakeRest(responses)
        monkeypatch.setattr(runpod, 'rest_request', rest)
        return rest

    return _install


class TestCreateTemplateForDockerLogin:

    def test_no_docker_login_config_returns_image_unchanged(self):
        image, template_id = runpod_utils._create_template_for_docker_login(
            cluster_name='test-cluster',
            image_name='my-org/my-image:tag',
            docker_login_config=None,
        )
        assert image == 'my-org/my-image:tag'
        assert template_id is None

    def test_docker_login_config_creates_registry_and_template(self, fake_rest):
        """Regression test for #9546.

        The template must be created with the fully-qualified image name, not
        None.
        """
        rest = fake_rest({
            ('POST', '/registries'): {
                'id': 'auth-id-123'
            },
            ('POST', '/templates'): {
                'id': 'template-id-456'
            },
        })

        image, template_id = runpod_utils._create_template_for_docker_login(
            cluster_name='test-cluster',
            image_name='my-org/my-image:tag',
            docker_login_config={
                'username': 'user',
                'password': 'pass',
                'server': 'ghcr.io',
            },
        )

        assert image == 'ghcr.io/my-org/my-image:tag'
        assert template_id == 'template-id-456'
        registry_body = rest.calls[0][2]
        assert registry_body == {
            'name': 'test-cluster-registry-auth',
            'username': 'user',
            'password': 'pass',
        }
        template_body = rest.calls[1][2]
        assert template_body['name'] == 'test-cluster-docker-login-template'
        assert template_body['image'] == 'ghcr.io/my-org/my-image:tag'
        assert template_body['registry'] == 'auth-id-123'

    def test_image_already_has_server_prefix_not_doubled(self, fake_rest):
        fake_rest({
            ('POST', '/registries'): {
                'id': 'auth-id-123'
            },
            ('POST', '/templates'): {
                'id': 'template-id-456'
            },
        })

        image, _ = runpod_utils._create_template_for_docker_login(
            cluster_name='test-cluster',
            image_name='ghcr.io/my-org/my-image:tag',
            docker_login_config={
                'username': 'user',
                'password': 'pass',
                'server': 'ghcr.io',
            },
        )

        # Server prefix should not be doubled.
        assert image == 'ghcr.io/my-org/my-image:tag'


class TestListInstances:

    def test_running_pod_exposes_ssh_and_ports(self, fake_rest):
        fake_rest({
            ('GET', '/pods'): _list_response('pods', [{
                'id': 'pod1',
                'name': 'c-head',
                'status': 'RUNNING',
                'gpu': {
                    'id': 'NVIDIA GeForce RTX 4090',
                    'count': 1,
                    'vcpuCount': 16,
                    'memory': 64,
                },
                'ssh': {
                    'proxy': {
                        'host': 'ssh.runpod.io',
                        'port': 22
                    },
                    'direct': {
                        'host': '1.2.3.4',
                        'port': 34446
                    },
                },
                'runtime': {
                    'ports': [
                        {
                            'private': 22,
                            'public': 34446,
                            'type': 'tcp',
                            'ip': '1.2.3.4'
                        },
                        {
                            'private': 8265,
                            'public': None,
                            'type': 'http',
                            'ip': None
                        },
                        {
                            'private': 8080,
                            'public': 40000,
                            'type': 'tcp',
                            'ip': '1.2.3.4'
                        },
                    ]
                },
            }]),
        })

        instances = runpod_utils.list_instances()

        assert instances == {
            'pod1': {
                'status': 'RUNNING',
                'name': 'c-head',
                'vcpu_count': 16,
                'external_ip': '1.2.3.4',
                'ssh_port': 34446,
                'port2endpoint': {
                    22: {
                        'host': '1.2.3.4',
                        'port': 34446
                    },
                    8080: {
                        'host': '1.2.3.4',
                        'port': 40000
                    },
                },
            }
        }

    def test_pending_pod_has_no_ssh_port(self, fake_rest):
        fake_rest({
            ('GET', '/pods'): _list_response('pods', [{
                'id': 'pod1',
                'name': 'c-head',
                'status': 'PROVISIONING',
                'cpu': {
                    'id': 'cpu5c',
                    'vcpuCount': 4,
                    'memory': 8
                },
                'ssh': {
                    'proxy': None,
                    'direct': None
                },
                'runtime': None,
            }]),
        })

        instances = runpod_utils.list_instances()

        assert instances['pod1']['status'] == 'PROVISIONING'
        assert instances['pod1']['vcpu_count'] == 4
        assert 'ssh_port' not in instances['pod1']
        assert instances['pod1']['port2endpoint'] == {}

    def test_running_pod_without_direct_ssh_uses_runtime_port(self, fake_rest):
        fake_rest({
            ('GET', '/pods'): _list_response('pods', [{
                'id': 'pod1',
                'name': 'c-head',
                'status': 'RUNNING',
                'ssh': {
                    'proxy': None,
                    'direct': None
                },
                'runtime': {
                    'ports': [{
                        'private': 22,
                        'public': 30000,
                        'type': 'tcp',
                        'ip': '5.6.7.8'
                    }]
                },
            }]),
        })

        instances = runpod_utils.list_instances()

        assert instances['pod1']['external_ip'] == '5.6.7.8'
        assert instances['pod1']['ssh_port'] == 30000


class TestLaunch:

    _COMMON_KWARGS = dict(
        cluster_name='c',
        node_type='head',
        region='US',
        zone='US-TX-3,US-KS-2',
        disk_size=50,
        image_name='runpod/base:1.0.2-ubuntu2204',
        ports=[8080],
        public_key='ssh-ed25519 AAAA',
        preemptible=False,
        bid_per_gpu=0.0,
        docker_login_config=None,
    )

    def test_gpu_pod_body(self, fake_rest):
        rest = fake_rest({
            ('GET', '/catalog/gpus/NVIDIA%20GeForce%20RTX%204090'): {
                'id': 'NVIDIA GeForce RTX 4090',
                'memory': 24,
            },
            ('POST', '/pods'): {
                'id': 'new-pod'
            },
        })

        instance_id = runpod_utils.launch(instance_type='2x_RTX4090_SECURE',
                                          **self._COMMON_KWARGS)

        assert instance_id == 'new-pod'
        body = rest.calls[-1][2]
        assert body['name'] == 'c-head'
        assert body['image'] == 'runpod/base:1.0.2-ubuntu2204'
        assert body['disk'] == 50
        assert body['cloud'] == 'SECURE'
        assert body['dataCenterIds'] == ['US-TX-3', 'US-KS-2']
        assert body['startSsh'] is True
        assert body['ports'][:2] == ['22/tcp', '8080/tcp']
        assert all(p.endswith('/http') for p in body['ports'][2:])
        assert body['gpu'] == {
            'id': 'NVIDIA GeForce RTX 4090',
            'count': 2,
            'minVcpuCountPerGpu': 4,
            'minRamPerGpu': 24,
        }
        assert 'cpu' not in body
        assert 'templateId' not in body
        assert 'mounts' not in body
        assert body['args'].startswith('bash -c ')

    def test_cpu_pod_body_with_network_volume(self, fake_rest):
        rest = fake_rest({('POST', '/pods'): {'id': 'new-pod'}})

        runpod_utils.launch(instance_type='cpu5c-4-8',
                            network_volume_id='vol1',
                            volume_mount_path='/data',
                            **self._COMMON_KWARGS)

        body = rest.calls[-1][2]
        assert body['cpu'] == {'id': 'cpu5c', 'vcpuCount': 4}
        assert 'gpu' not in body
        assert 'cloud' not in body
        assert body['mounts'] == {
            'network': [{
                'volumeId': 'vol1',
                'path': '/data'
            }]
        }

    def test_docker_login_uses_template(self, fake_rest):
        rest = fake_rest({
            ('POST', '/registries'): {
                'id': 'auth1'
            },
            ('POST', '/templates'): {
                'id': 'tpl1'
            },
            ('POST', '/pods'): {
                'id': 'new-pod'
            },
        })
        kwargs = dict(self._COMMON_KWARGS,
                      docker_login_config={
                          'username': 'u',
                          'password': 'p',
                          'server': 'ghcr.io',
                      })

        runpod_utils.launch(instance_type='cpu5c-4-8', **kwargs)

        body = rest.calls[-1][2]
        assert body['templateId'] == 'tpl1'
        assert body['image'] == 'ghcr.io/runpod/base:1.0.2-ubuntu2204'

    def test_spot_pod_uses_graphql_path(self, fake_rest):
        fake_rest({
            ('GET', '/catalog/gpus/NVIDIA%20GeForce%20RTX%204090'): {
                'memory': 24
            },
        })
        kwargs = dict(self._COMMON_KWARGS, preemptible=True, bid_per_gpu=0.3)

        with mock.patch.object(runpod_utils.runpod_commands,
                               'create_spot_pod',
                               return_value={'id': 'spot-pod'}) as spot:
            instance_id = runpod_utils.launch(instance_type='1x_RTX4090_SECURE',
                                              **kwargs)

        assert instance_id == 'spot-pod'
        spot_kwargs = spot.call_args.kwargs
        assert spot_kwargs['gpu_type_id'] == 'NVIDIA GeForce RTX 4090'
        assert spot_kwargs['bid_per_gpu'] == 0.3
        assert spot_kwargs['min_memory_in_gb'] == 24
        assert spot_kwargs['ports'].startswith('22/tcp,8080/tcp,')


class TestRegistryAuthResources:

    def test_found(self, fake_rest):
        fake_rest({
            ('GET', '/templates'): _list_response('templates', [
                {
                    'id': 'other',
                    'name': 'unrelated',
                    'registry': None
                },
                {
                    'id': 'tpl1',
                    'name': 'c-docker-login-template',
                    'registry': 'auth1'
                },
            ]),
        })
        assert runpod_utils.get_registry_auth_resources('c') == ('tpl1',
                                                                 'auth1')

    def test_missing(self, fake_rest):
        fake_rest({('GET', '/templates'): _list_response('templates', [])})
        assert runpod_utils.get_registry_auth_resources('c') == (None, None)


class TestDeleteHelpers:

    def test_remove_and_deletes_hit_v2_paths(self, fake_rest):
        rest = fake_rest({
            ('DELETE', '/pods/pod%201'): None,
            ('DELETE', '/templates/tpl1'): None,
            ('DELETE', '/registries/auth1'): None,
        })
        runpod_utils.remove('pod 1')
        runpod_utils.delete_pod_template('tpl1')
        runpod_utils.delete_register_auth('auth1')
        assert [c[:2] for c in rest.calls] == [
            ('DELETE', '/pods/pod%201'),
            ('DELETE', '/templates/tpl1'),
            ('DELETE', '/registries/auth1'),
        ]

    def test_delete_failures_only_warn(self, monkeypatch):

        def _fail(method, path, json=None, params=None):
            raise runpod.RunPodRestError('nope', status_code=409)

        monkeypatch.setattr(runpod, 'rest_request', _fail)
        runpod_utils.delete_pod_template('tpl1')
        runpod_utils.delete_register_auth('auth1')


class TestRegisterSshKey:

    def test_adds_missing_key(self, fake_rest):
        rest = fake_rest({
            ('GET', '/account/ssh-keys'): {
                'keys': ['ssh-rsa AAAA old']
            },
            ('PUT', '/account/ssh-keys'): None,
        })
        runpod_utils.register_ssh_key('ssh-ed25519 BBBB skypilot')
        assert rest.calls[-1][:3] == ('PUT', '/account/ssh-keys', {
            'keys': ['ssh-rsa AAAA old', 'ssh-ed25519 BBBB skypilot']
        })

    def test_skips_key_with_same_material(self, fake_rest):
        rest = fake_rest({
            ('GET', '/account/ssh-keys'): {
                'keys': ['ssh-ed25519 BBBB other-label']
            },
        })
        runpod_utils.register_ssh_key('ssh-ed25519 BBBB skypilot')
        assert [c[0] for c in rest.calls] == ['GET']
