"""Tests for attaching a service account via `nebius.remote_identity`."""
# pylint: disable=protected-access
import asyncio
import datetime
import http.server
import importlib.util
import json
import os
import pathlib
import sys
import threading
from unittest import mock

import jinja2
import pytest

from sky import clouds
from sky import exceptions
from sky import resources as resources_lib
from sky import skypilot_config
from sky.adaptors import nebius as nebius_adaptor
from sky.backends import backend_utils
from sky.clouds import nebius
from sky.provision.nebius import utils as nebius_utils
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import config_utils
from sky.utils import resources_utils
from sky.utils import schemas
from sky.utils import yaml_utils

_HAS_NEBIUS_SDK = importlib.util.find_spec('nebius') is not None
nebius_sdk_required = pytest.mark.skipif(not _HAS_NEBIUS_SDK,
                                         reason='Nebius SDK is not installed')

_SA = 'serviceaccount-e00abc123'
_REGION = 'eu-north1'
_INSTANCE_TYPE = 'cpu-d3_4vcpu-16gb'


@pytest.fixture(name='set_config')
def fixture_set_config(monkeypatch):

    def _set(config):
        monkeypatch.setattr(skypilot_config, '_get_loaded_config',
                            lambda: config_utils.Config(config))
        monkeypatch.setattr(skypilot_config, 'get_active_workspace',
                            lambda: 'default')

    return _set


class TestSchema:
    """Schema of nebius.remote_identity."""

    @pytest.mark.parametrize('config', [
        {
            'nebius': {
                'remote_identity': _SA
            }
        },
        {
            'nebius': {
                'remote_identity': 'LOCAL_CREDENTIALS'
            }
        },
        {
            'nebius': {
                'remote_identity': 'NO_UPLOAD'
            }
        },
        {
            'nebius': {
                'remote_identity': [{
                    'train-*': _SA
                }, {
                    '*': 'LOCAL_CREDENTIALS'
                }]
            }
        },
        {
            'nebius': {
                'region_configs': {
                    _REGION: {
                        'remote_identity': _SA
                    }
                }
            }
        },
        {
            'workspaces': {
                'team-a': {
                    'nebius': {
                        'remote_identity': _SA
                    }
                }
            }
        },
        {
            'workspaces': {
                'team-a': {
                    'nebius': {
                        'region_configs': {
                            _REGION: {
                                'remote_identity': [{
                                    'team-a-*': _SA
                                }]
                            }
                        }
                    }
                }
            }
        },
    ])
    def test_accepts(self, config):
        common_utils.validate_schema(config, schemas.get_config_schema(),
                                     'err: ')

    @pytest.mark.parametrize(
        'region_config',
        [
            {
                'remote_identity': 'local_credentials'
            },
            {
                'remote_identity': 'my-service-account'
            },
            # Other region settings stay global-only.
            {
                'project_id': 'project-e00abc'
            },
        ])
    def test_rejects_in_workspace_region(self, region_config):
        with pytest.raises(exceptions.InvalidSkyPilotConfigError):
            common_utils.validate_schema(
                {
                    'workspaces': {
                        'team-a': {
                            'nebius': {
                                'region_configs': {
                                    _REGION: region_config
                                }
                            }
                        }
                    }
                }, schemas.get_config_schema(), 'err: ')

    @pytest.mark.parametrize('identity', [
        'my-service-account',
        'NONE',
        'local_credentials',
        'no_upload',
        '',
        'SERVICEACCOUNT-E00ABC',
        [{
            '*': 'my-service-account'
        }],
    ])
    def test_rejects(self, identity):
        with pytest.raises(exceptions.InvalidSkyPilotConfigError):
            common_utils.validate_schema(
                {'nebius': {
                    'remote_identity': identity
                }}, schemas.get_config_schema(), 'err: ')


class TestGetServiceAccountId:
    """Resolution of the service account to attach."""

    def test_default_is_none(self, set_config):
        set_config({})
        assert nebius._get_service_account_id(_REGION, 'c', None) is None

    @pytest.mark.parametrize('identity', ['LOCAL_CREDENTIALS', 'NO_UPLOAD'])
    def test_modes_are_none(self, set_config, identity):
        set_config({'nebius': {'remote_identity': identity}})
        assert nebius._get_service_account_id(_REGION, 'c', None) is None

    def test_string(self, set_config):
        set_config({'nebius': {'remote_identity': _SA}})
        assert nebius._get_service_account_id(_REGION, 'c', None) == _SA

    def test_cluster_name_patterns(self, set_config):
        set_config({
            'nebius': {
                'remote_identity': [{
                    'train-*': _SA
                }, {
                    'serve-*': 'LOCAL_CREDENTIALS'
                }]
            }
        })
        assert nebius._get_service_account_id(_REGION, 'train-1', None) == _SA
        assert nebius._get_service_account_id(_REGION, 'serve-1', None) is None
        assert nebius._get_service_account_id(_REGION, 'other', None) is None

    def test_region_overrides_cloud_level(self, set_config):
        set_config({
            'nebius': {
                'remote_identity': 'serviceaccount-global',
                'region_configs': {
                    _REGION: {
                        'remote_identity': _SA
                    }
                },
            }
        })
        assert nebius._get_service_account_id(_REGION, 'c', None) == _SA
        assert nebius._get_service_account_id('eu-west1', 'c',
                                              None) == 'serviceaccount-global'

    def test_workspace_overrides_global(self, set_config):
        set_config({
            'nebius': {
                'remote_identity': 'serviceaccount-global'
            },
            'workspaces': {
                'default': {
                    'nebius': {
                        'remote_identity': _SA
                    }
                }
            },
        })
        assert nebius._get_service_account_id(_REGION, 'c', None) == _SA

    def test_task_config_cannot_set_it(self):
        # Like aws/gcp.remote_identity, it is an admin setting: not in
        # OVERRIDEABLE_CONFIG_KEYS_IN_TASK, so a task's `config:` is dropped.
        assert (
            'nebius',
            'remote_identity') not in constants.OVERRIDEABLE_CONFIG_KEYS_IN_TASK
        resources = resources_lib.Resources(cloud=nebius.Nebius()).copy(
            _cluster_config_overrides={'nebius': {
                'remote_identity': _SA
            }})
        assert resources.cluster_config_overrides == {}

    def test_workspace_region_overrides_workspace_cloud_level(self, set_config):
        set_config({
            'nebius': {
                'region_configs': {
                    _REGION: {
                        'remote_identity': 'serviceaccount-global'
                    }
                }
            },
            'workspaces': {
                'default': {
                    'nebius': {
                        'remote_identity': 'serviceaccount-ws',
                        'region_configs': {
                            _REGION: {
                                'remote_identity': _SA
                            }
                        },
                    }
                }
            },
        })
        assert nebius._get_service_account_id(_REGION, 'c', None) == _SA
        assert nebius._get_service_account_id('eu-west1', 'c',
                                              None) == 'serviceaccount-ws'

    def test_service_account_mode_is_rejected(self, set_config):
        set_config({'nebius': {'remote_identity': 'SERVICE_ACCOUNT'}})
        with pytest.raises(exceptions.InvalidCloudConfigs):
            nebius._get_service_account_id(_REGION, 'c', None)


def _deploy_vars(docker_image=None):
    resources = resources_lib.Resources(
        cloud=nebius.Nebius(),
        instance_type=_INSTANCE_TYPE,
        image_id=docker_image).assert_launchable()
    with mock.patch.object(nebius_utils,
                           'get_project_by_region',
                           return_value='project-id'):
        return nebius.Nebius().make_deploy_resources_variables(
            resources=resources,
            cluster_name=resources_utils.ClusterName(display_name='c',
                                                     name_on_cloud='c-1234'),
            region=clouds.Region(_REGION),
            zones=None,
            num_nodes=1)


class TestDeployVariables:
    """Deploy variables passed to the Ray YAML template."""

    def test_without_service_account(self, set_config):
        set_config({})
        deploy_vars = _deploy_vars(docker_image='docker:ubuntu')
        assert deploy_vars['service_account_id'] is None
        assert 'docker_run_options' not in deploy_vars

    def test_with_service_account(self, set_config):
        set_config({'nebius': {'remote_identity': _SA}})
        deploy_vars = _deploy_vars(docker_image='docker:ubuntu')
        assert deploy_vars['service_account_id'] == _SA
        assert deploy_vars['docker_run_options'] == [
            '-v /mnt/cloud-metadata:/mnt/cloud-metadata:ro'
        ]


def _render_template(service_account_id):
    template_path = (pathlib.Path(nebius.__file__).parents[1] / 'templates' /
                     'nebius-ray.yml.j2')
    template = jinja2.Template(template_path.read_text(encoding='utf-8'))
    rendered = template.render(
        cluster_name_on_cloud='c',
        num_nodes=1,
        region=_REGION,
        project_id='project-id',
        use_internal_ips=False,
        use_static_ip_address=False,
        security_group='sg',
        security_group_managed_by_skypilot='true',
        docker_image=None,
        ssh_private_key='~/.ssh/key',
        ssh_proxy_command=None,
        instance_type=_INSTANCE_TYPE,
        image_id='ubuntu24.04-driverless',
        disk_size=100,
        use_spot=False,
        network_tier=None,
        disk_tier='medium',
        disk_encrypted=False,
        filesystems=[],
        service_account_id=service_account_id,
        credentials={},
        initial_setup_commands=[],
    )
    return yaml_utils.safe_load(rendered)


class TestTemplate:
    """Rendering of nebius-ray.yml.j2."""

    def test_service_account_in_node_config(self):
        config = _render_template(_SA)
        node_config = config['available_node_types']['ray_head_default'][
            'node_config']
        assert node_config['service_account_id'] == _SA
        assert 'rm -f ~/.nebius/credentials.json' in config['setup_commands'][0]

    def test_no_service_account(self):
        config = _render_template(None)
        node_config = config['available_node_types']['ray_head_default'][
            'node_config']
        assert 'service_account_id' not in node_config
        assert '~/.nebius/credentials.json' not in config['setup_commands'][0]


@mock.patch.object(skypilot_config, '_global_config_context',
                   skypilot_config.ConfigContext())
@mock.patch('sky.backends.backend_utils._get_yaml_path_from_cluster_name',
            return_value='/tmp/fake/path')
@mock.patch('sky.backends.backend_utils._deterministic_cluster_yaml_hash',
            return_value='fake-hash')
@mock.patch.object(nebius_utils,
                   'get_project_by_region',
                   return_value='project-id')
@mock.patch('sky.utils.common_utils.fill_template')
@pytest.mark.parametrize('identity, uploads_credentials, expected_sa', [
    (None, True, None),
    (_SA, False, _SA),
    ('NO_UPLOAD', False, None),
])
def test_write_cluster_config_skips_credentials_with_service_account(
        mock_fill_template, mock_project, mock_hash, mock_path, tmp_path,
        identity, uploads_credentials, expected_sa):
    del mock_project, mock_hash, mock_path
    config_path = tmp_path / 'config.yaml'
    config = {} if identity is None else {
        'nebius': {
            'remote_identity': identity
        }
    }
    yaml_utils.dump_yaml(str(config_path), config)
    excluded = []

    def _credential_mounts(excluded_clouds):
        excluded.append(set(excluded_clouds))
        return {}

    with mock.patch.dict(
            os.environ,
        {skypilot_config.ENV_VAR_SKYPILOT_CONFIG: str(config_path)}), \
            mock.patch('sky.check.get_cloud_credential_file_mounts',
                       side_effect=_credential_mounts):
        skypilot_config.reload_config()
        backend_utils.write_cluster_config(
            to_provision=resources_lib.Resources(cloud=nebius.Nebius(),
                                                 instance_type=_INSTANCE_TYPE),
            num_nodes=1,
            cluster_config_template='nebius-ray.yml.j2',
            cluster_name='my-cluster',
            local_wheel_path=pathlib.Path('/tmp/fake'),
            wheel_hash='fake-hash',
            region=clouds.Region(_REGION),
            zones=None,
            dryrun=True,
            keep_launch_fields_in_existing_config=True)

    assert (clouds.Nebius() not in excluded[0]) == uploads_credentials
    variables = mock_fill_template.call_args[0][1]
    assert variables['service_account_id'] == expected_sa


@mock.patch.object(skypilot_config, '_global_config_context',
                   skypilot_config.ConfigContext())
@mock.patch('sky.catalog.instance_type_exists', return_value=True)
@mock.patch('sky.catalog.get_accelerators_from_instance_type',
            return_value=None)
@mock.patch('sky.backends.backend_utils._get_yaml_path_from_cluster_name',
            return_value='/tmp/fake/path')
@mock.patch('sky.backends.backend_utils._deterministic_cluster_yaml_hash',
            return_value='fake-hash')
@mock.patch('sky.utils.common_utils.fill_template')
@pytest.mark.parametrize('nebius_config, uploads_nebius_credentials', [
    ({}, True),
    ({
        'remote_identity': 'LOCAL_CREDENTIALS'
    }, True),
    ({
        'remote_identity': _SA
    }, False),
    ({
        'remote_identity': [{
            'other-*': _SA
        }]
    }, False),
    ({
        'region_configs': {
            _REGION: {
                'remote_identity': [{
                    'team-*': _SA
                }, {
                    '*': 'serviceaccount-e00default'
                }]
            }
        }
    }, False),
    ({
        'workspace': {
            'region_configs': {
                _REGION: {
                    'remote_identity': _SA
                }
            }
        }
    }, False),
])
def test_write_cluster_config_other_cloud_skips_nebius_credentials(
        mock_fill_template, mock_hash, mock_path, mock_accs, mock_exists,
        tmp_path, nebius_config, uploads_nebius_credentials):
    """A Nebius service account keeps Nebius credentials off other clouds.

    The local credentials are the provisioner's (e.g. the API server's), so a
    cluster on another cloud must not get them either, wherever in the config
    the service account is set.
    """
    del mock_fill_template, mock_hash, mock_path, mock_accs, mock_exists
    nebius_config = dict(nebius_config)
    workspace_config = nebius_config.pop('workspace', None)
    config = {'nebius': nebius_config} if nebius_config else {}
    if workspace_config is not None:
        config['workspaces'] = {'default': {'nebius': workspace_config}}
    config_path = tmp_path / 'config.yaml'
    yaml_utils.dump_yaml(str(config_path), config)
    excluded = []

    def _credential_mounts(excluded_clouds):
        excluded.append(set(excluded_clouds))
        return {}

    with mock.patch.dict(
            os.environ,
        {skypilot_config.ENV_VAR_SKYPILOT_CONFIG: str(config_path)}), \
            mock.patch('sky.check.get_cloud_credential_file_mounts',
                       side_effect=_credential_mounts):
        skypilot_config.reload_config()
        backend_utils.write_cluster_config(
            to_provision=resources_lib.Resources(cloud=clouds.Verda(),
                                                 instance_type='1V100.6V'),
            num_nodes=1,
            cluster_config_template='verda-ray.yml.j2',
            cluster_name='team-a-cluster',
            local_wheel_path=pathlib.Path('/tmp/fake'),
            wheel_hash='fake-hash',
            region=clouds.Region('FIN-01'),
            zones=None,
            dryrun=True,
            keep_launch_fields_in_existing_config=True)

    assert (not clouds.cloud_in_iterable(
        clouds.Nebius(), excluded[0])) == uploads_nebius_credentials


@nebius_sdk_required
@pytest.mark.parametrize('service_account_id', [None, _SA])
def test_launch_sets_instance_service_account(service_account_id):
    compute = nebius_adaptor.compute()
    service = mock.MagicMock()
    instance = mock.MagicMock()
    instance.metadata.id = 'instance-id'
    instance.status.state.name = 'STARTING'

    with mock.patch.object(nebius_utils,
                           'get_project_by_region',
                           return_value='project-id'), \
            mock.patch.object(nebius_utils,
                              'get_subnet_id',
                              return_value='subnet-id'), \
            mock.patch.object(nebius_adaptor, 'sdk'), \
            mock.patch.object(compute,
                              'InstanceServiceClient',
                              return_value=service), \
            mock.patch.object(nebius_adaptor,
                              'sync_call',
                              side_effect=[None, instance]):
        nebius_utils.launch(
            cluster_name_on_cloud='cluster',
            node_type='head',
            platform='cpu-d3',
            preset='4vcpu-16gb',
            region=_REGION,
            image_id_or_family='ubuntu24.04-driverless',
            disk_size=100,
            user_data='',
            associate_public_ip_address=True,
            filesystems=[],
            disk_tier=resources_utils.DiskTier.MEDIUM,
            service_account_id=service_account_id,
        )

    request = service.create.call_args.args[0]
    assert request.spec.service_account_id == (service_account_id or '')


def _innermost_bearer(bearer):
    """The bearer wrapped by renewable/metrics wrappers."""
    while getattr(bearer, 'wrapped', None) is not None:
        bearer = bearer.wrapped
    return bearer


class TestAdaptorVmServiceAccountToken:
    """SDK auth with the token of an attached service account."""

    @pytest.fixture(name='sdk_factory')
    def fixture_sdk_factory(self, monkeypatch, tmp_path):
        nebius_adaptor.clear_sdk_cache()
        nebius_adaptor.clear_imds_probe_cache()
        monkeypatch.setattr(nebius_adaptor, 'api_domain', lambda: None)
        monkeypatch.setattr(nebius_adaptor.skypilot_config,
                            'get_active_workspace', lambda: 'default')
        monkeypatch.setattr(nebius_adaptor.skypilot_config,
                            'get_workspace_cloud', lambda cloud: {})
        monkeypatch.setattr(nebius_adaptor, 'get_iam_token', lambda: None)
        monkeypatch.setattr(nebius_adaptor, '_get_default_credentials_path',
                            lambda: str(tmp_path / 'credentials.json'))
        monkeypatch.setattr(nebius_adaptor, '_close_sdk', lambda _: None)
        token_path = tmp_path / 'token'
        monkeypatch.setattr(nebius_adaptor, 'VM_SERVICE_ACCOUNT_TOKEN_PATH',
                            str(token_path))
        # No metadata service unless a test enables it.
        probe = mock.MagicMock(return_value=False)
        monkeypatch.setattr(nebius_adaptor, '_probe_imds', probe)
        factory = mock.MagicMock()
        monkeypatch.setattr(nebius_adaptor, 'nebius', factory)
        yield factory.sdk.SDK, tmp_path, probe
        nebius_adaptor.clear_sdk_cache()
        nebius_adaptor.clear_imds_probe_cache()

    @nebius_sdk_required
    def test_uses_imds_without_credentials_file(self, sdk_factory):
        # pylint: disable=import-outside-toplevel
        from nebius.aio.token import renewable
        factory, tmp_path, probe = sdk_factory
        probe.return_value = True
        # The legacy file does not matter when the metadata service works.
        (tmp_path / 'token').write_text('vm-token')

        assert nebius_adaptor.is_token_or_cred_file_exist()
        nebius_adaptor.sdk()
        nebius_adaptor.sdk()

        # One SDK (and bearer) per process, one probe.
        factory.assert_called_once()
        probe.assert_called_once()
        bearer = factory.call_args.kwargs['credentials']
        assert isinstance(bearer, renewable.Bearer)
        assert _innermost_bearer(bearer).name == (
            f'imds:{nebius_adaptor.VM_SERVICE_ACCOUNT_TOKEN_ENDPOINT}')

    @nebius_sdk_required
    def test_uses_sdk_imds_bearer_when_available(self, sdk_factory):
        # pylint: disable=import-outside-toplevel
        pytest.importorskip('nebius.aio.token.imds')
        from nebius.aio.token import imds
        factory, _, probe = sdk_factory
        probe.return_value = True

        nebius_adaptor.sdk()

        bearer = factory.call_args.kwargs['credentials']
        assert isinstance(_innermost_bearer(bearer), imds.Bearer)

    @nebius_sdk_required
    def test_local_imds_bearer_without_sdk_imds_module(self, sdk_factory,
                                                       monkeypatch):
        # pylint: disable=import-outside-toplevel
        from nebius.aio.token import renewable

        from sky.adaptors import nebius_imds

        # Simulate nebius<0.6.24, which has no nebius.aio.token.imds.
        # `from package import module` prefers an already imported attribute.
        monkeypatch.delattr('nebius.aio.token.imds', raising=False)
        monkeypatch.setitem(sys.modules, 'nebius.aio.token.imds', None)
        factory, _, probe = sdk_factory
        probe.return_value = True

        nebius_adaptor.sdk()

        bearer = factory.call_args.kwargs['credentials']
        assert isinstance(bearer, renewable.Bearer)
        assert isinstance(_innermost_bearer(bearer), nebius_imds.Bearer)

    @nebius_sdk_required
    def test_falls_back_to_token_file(self, sdk_factory):
        # pylint: disable=import-outside-toplevel
        from nebius.aio.token import file as token_file
        factory, tmp_path, probe = sdk_factory
        (tmp_path / 'token').write_text('vm-token')

        assert nebius_adaptor.is_token_or_cred_file_exist()
        nebius_adaptor.sdk()
        nebius_adaptor.sdk()

        factory.assert_called_once()
        probe.assert_called_once()
        bearer = factory.call_args.kwargs['credentials']
        assert isinstance(bearer, token_file.Bearer)

    @pytest.mark.parametrize('with_token_file', [False, True])
    def test_credentials_file_takes_precedence(self, sdk_factory,
                                               with_token_file):
        factory, tmp_path, probe = sdk_factory
        probe.return_value = True
        if with_token_file:
            (tmp_path / 'token').write_text('vm-token')
        (tmp_path / 'credentials.json').write_text('{}')

        nebius_adaptor.sdk()

        assert factory.call_args.kwargs['credentials_file_name'] == str(
            tmp_path / 'credentials.json')
        # Off a VM with credentials, the metadata service is never contacted.
        probe.assert_not_called()

    def test_no_credentials(self, sdk_factory):
        del sdk_factory
        assert not nebius_adaptor.is_token_or_cred_file_exist()

    def test_failed_probe_is_cached_for_a_while(self, sdk_factory, monkeypatch):
        _, _, probe = sdk_factory
        now = [1000.0]
        monkeypatch.setattr(nebius_adaptor.time, 'monotonic', lambda: now[0])

        assert not nebius_adaptor.is_imds_available()
        assert not nebius_adaptor.is_imds_available()
        probe.assert_called_once()

        now[0] += nebius_adaptor._IMDS_PROBE_FAILURE_TTL + 1
        probe.return_value = True
        assert nebius_adaptor.is_imds_available()
        assert probe.call_count == 2

        # A successful probe is kept.
        now[0] += 10 * nebius_adaptor._IMDS_PROBE_FAILURE_TTL
        assert nebius_adaptor.is_imds_available()
        assert probe.call_count == 2


class TestImdsProbe:
    """The probe of the instance metadata service."""

    @staticmethod
    def _response(body):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = body
        return response

    @pytest.mark.parametrize('body, expected', [
        (b'{"access_token": "t", "expires_at": "2026-10-01T00:00:00Z"}', True),
        (b'{"expires_at": "2026-10-01T00:00:00Z"}', False),
        (b'not json', False),
        (b'[]', False),
    ])
    def test_response(self, body, expected):
        opener = mock.MagicMock()
        opener.open.return_value = self._response(body)
        with mock.patch.object(nebius_adaptor.urllib.request,
                               'build_opener',
                               return_value=opener):
            assert nebius_adaptor._probe_imds() is expected
        request = opener.open.call_args.args[0]
        assert request.full_url == (
            nebius_adaptor.VM_SERVICE_ACCOUNT_TOKEN_ENDPOINT)
        assert request.get_header('Metadata') == 'true'
        assert opener.open.call_args.kwargs['timeout'] <= 1

    @pytest.mark.parametrize('error', [
        nebius_adaptor.urllib.error.URLError('no such host'),
        TimeoutError(),
        ConnectionRefusedError(),
    ])
    def test_unreachable(self, error):
        opener = mock.MagicMock()
        opener.open.side_effect = error
        with mock.patch.object(nebius_adaptor.urllib.request,
                               'build_opener',
                               return_value=opener):
            assert not nebius_adaptor._probe_imds()


@nebius_sdk_required
class TestLocalImdsBearer:
    """The fallback IMDS bearer used with nebius<0.6.24."""

    def test_parses_token(self):
        # pylint: disable=import-outside-toplevel
        from sky.adaptors import nebius_imds
        tok = nebius_imds._parse_token(
            b'{"access_token": "abc", "expires_at": "2026-10-01T10:00:00Z"}')
        assert tok.token == 'abc'
        assert tok.expiration == datetime.datetime(2026,
                                                   10,
                                                   1,
                                                   10,
                                                   tzinfo=datetime.timezone.utc)

    @pytest.mark.parametrize('body', [
        b'',
        b'[]',
        b'{"access_token": "", "expires_at": "2026-10-01T10:00:00Z"}',
        b'{"access_token": "abc"}',
        b'{"access_token": "abc", "expires_at": "tomorrow"}',
    ])
    def test_rejects_invalid_response(self, body):
        # pylint: disable=import-outside-toplevel
        from sky.adaptors import nebius_imds
        with pytest.raises(nebius_imds.IMDSError):
            nebius_imds._parse_token(body)

    @staticmethod
    def _serve(statuses):
        """Starts a local HTTP server answering with the given statuses."""
        requests = []

        class _Handler(http.server.BaseHTTPRequestHandler):
            """Answers every GET with the next configured status."""

            def do_GET(self):  # pylint: disable=invalid-name
                requests.append(self.headers.get('Metadata'))
                status = statuses[min(len(requests), len(statuses)) - 1]
                body = json.dumps({
                    'access_token': 'abc',
                    'expires_at': '2026-10-01T10:00:00Z'
                } if status == 200 else {}).encode()
                self.send_response(status)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # pylint: disable=arguments-differ
                pass

        server = http.server.HTTPServer(('127.0.0.1', 0), _Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server, requests

    def test_renewable_wrapper_caches_token(self):
        # pylint: disable=import-outside-toplevel
        from nebius.aio.token import renewable

        from sky.adaptors import nebius_imds
        server, requests = self._serve([200])
        try:
            bearer = renewable.Bearer(
                nebius_imds.Bearer(
                    f'http://127.0.0.1:{server.server_port}/v1/iam/sa/token'))

            async def fetch_twice():
                try:
                    first = await bearer.receiver().fetch(timeout=10)
                    second = await bearer.receiver().fetch(timeout=10)
                    return first, second
                finally:
                    await bearer.close()

            first, second = asyncio.run(fetch_twice())
            assert first.token == second.token == 'abc'
            assert len(requests) == 1
        finally:
            server.shutdown()
            server.server_close()

    @pytest.mark.parametrize('statuses, expect_token', [
        ([200], True),
        ([429, 503, 200], True),
        ([429, 429, 429], False),
        ([403], False),
    ])
    def test_fetch_retries(self, statuses, expect_token):
        # pylint: disable=import-outside-toplevel
        from sky.adaptors import nebius_imds
        server, requests = self._serve(statuses)
        try:
            bearer = nebius_imds.Bearer(
                f'http://127.0.0.1:{server.server_port}/v1/iam/sa/token')

            async def fetch():
                return await bearer.receiver().fetch(timeout=10)

            if expect_token:
                assert asyncio.run(fetch()).token == 'abc'
            else:
                with pytest.raises(nebius_imds.IMDSError):
                    asyncio.run(fetch())
            # Only 429 and 5xx are retried, up to 3 attempts.
            assert len(requests) == (len(statuses)
                                     if statuses != [429, 429, 429] else 3)
            assert set(requests) == {'true'}
        finally:
            server.shutdown()
            server.server_close()
