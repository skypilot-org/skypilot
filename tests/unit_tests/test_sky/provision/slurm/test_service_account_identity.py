"""Service-account submit identity and server-owned configuration tests."""
# pylint: disable=redefined-outer-name,unused-argument,protected-access
import subprocess
from unittest import mock

import pytest

from sky import models
from sky import skypilot_config
from sky.adaptors import slurm
from sky.clouds import slurm as slurm_cloud
from sky.provision.slurm import instance
from sky.provision.slurm import utils
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import config_utils
from sky.utils import schemas


@pytest.fixture
def identity_config(monkeypatch):
    config = config_utils.Config.from_dict({
        'slurm': {
            'submit_as_user': True,
            'username_map': {
                'Machine': 'tenant-svc'
            },
            'cluster_configs': {
                'a': {
                    'username_map': {
                        'Machine': 'cluster-svc'
                    },
                },
                'b': {},
            },
        },
    })
    ctx = skypilot_config.ConfigContext(config=config)
    monkeypatch.setattr(skypilot_config, '_get_config_context', lambda: ctx)
    monkeypatch.setattr(common_utils, 'get_current_user',
                        lambda: models.User(id='sa-1234', name='Machine'))
    monkeypatch.setattr(utils.global_user_state, 'get_service_account_creator',
                        lambda _: None)
    return config


@pytest.mark.parametrize('cluster,expected', [('a', 'cluster-svc'),
                                              ('b', 'tenant-svc')])
def test_mapping_precedence(identity_config, cluster, expected):
    assert utils.get_submit_user(cluster) == expected


@pytest.mark.parametrize('cluster,expected', [('a', 'jane-cluster'),
                                              ('b', 'jdoe'), ('c', 'jdoe')])
def test_service_account_uses_creator_mapping(identity_config, monkeypatch,
                                              cluster, expected):
    identity_config['slurm']['username_map'].pop('Machine')
    identity_config['slurm']['cluster_configs']['a']['username_map'] = {
        'jane.doe@example.com': 'jane-cluster'
    }
    identity_config['slurm']['username_map']['jane.doe@example.com'] = 'jdoe'
    monkeypatch.setattr(
        utils.global_user_state, 'get_service_account_creator',
        lambda _: models.User(id='human', name='jane.doe@example.com'))
    assert utils.get_submit_user(cluster) == expected


def test_service_account_uses_creator_email_local_part(identity_config,
                                                       monkeypatch):
    identity_config['slurm']['username_map'].clear()
    monkeypatch.setattr(
        utils.global_user_state, 'get_service_account_creator',
        lambda _: models.User(id='human', name='jane.doe@example.com'))
    assert utils.get_submit_user('b') == 'jane.doe'


def test_unmapped_service_account_rejected(identity_config, monkeypatch):
    monkeypatch.setattr(
        common_utils, 'get_current_user',
        lambda: models.User(id='sa-5678', name='valid-unix-name'))
    with pytest.raises(ValueError, match='valid-unix-name.*Slurm cluster'):
        utils.get_submit_user('c')


def test_human_uses_email_local_part(identity_config, monkeypatch):
    monkeypatch.setattr(
        common_utils, 'get_current_user',
        lambda: models.User(id='human', name='alice@example.com'))
    assert utils.get_submit_user('a') == 'alice'


def test_disabled(identity_config):
    identity_config['slurm']['cluster_configs']['a']['submit_as_user'] = False
    assert utils.get_submit_user('a') is None


@pytest.mark.parametrize('value', ['Root', '-root', 'svc;id', '', 'svc\n'])
def test_invalid_mapping_rejected(identity_config, value):
    identity_config['slurm']['username_map']['Machine'] = value
    with pytest.raises(ValueError, match='Invalid Unix user'):
        utils.get_submit_user('b')


def test_schema(identity_config):
    common_utils.validate_schema(identity_config, schemas.get_config_schema(),
                                 '')
    identity_config['slurm']['cluster_configs']['a']['username_map'][
        'Machine'] = 'svc;id'
    with pytest.raises(ValueError):
        common_utils.validate_schema(identity_config,
                                     schemas.get_config_schema(), '')


def test_client_cannot_override_identity(identity_config):
    with skypilot_config.override_skypilot_config({
            'slurm': {
                'provision_timeout': 123,
                'submit_as_user': False,
                'username_map': {
                    'Machine': 'root'
                },
                'cluster_configs': {
                    'a': {
                        'username_map': {
                            'Machine': 'root'
                        }
                    }
                },
            }
    }):
        assert skypilot_config.get_nested(('slurm', 'provision_timeout'),
                                          None) == 123
        assert utils.get_submit_user('a') == 'cluster-svc'
        assert utils.get_submit_user('b') == 'tenant-svc'


@pytest.mark.parametrize('rc,stderr', [(1, 'unknown user'),
                                       (1, 'sudo: a password is required'),
                                       (255, 'connection refused')])
def test_submit_validation_failure(rc, stderr):
    with mock.patch(
            'sky.adaptors.slurm.command_runner.SlurmLoginNodeCommandRunner'
    ) as runner:
        runner.return_value.run.return_value = (rc, '', stderr)
        client = slurm.SlurmClient('host', 22, 'login', slurm_user='svc')
        with pytest.raises(
                RuntimeError,
                match='cluster .*a.*user .*svc.*SSH user .*login') as exc:
            client.validate_submit_user('a', 'svc')
        assert stderr in str(exc.value)
        assert f'code {rc}' in str(exc.value)


def test_submit_validation_uses_bounded_submit_runner():
    with mock.patch(
            'sky.adaptors.slurm.command_runner.SlurmLoginNodeCommandRunner'
    ) as runner:
        runner.return_value.run.return_value = (0, '', '')
        client = slurm.SlurmClient('host', 22, 'login', slurm_user='svc')
        client.validate_submit_user('a', 'svc')
        assert runner.call_args.kwargs['slurm_user'] == 'svc'
        assert runner.return_value.run.call_args.kwargs['timeout'] == 15
        assert 'id -u -- svc' in runner.return_value.run.call_args.args[0]


def test_submit_validation_timeout():
    with mock.patch(
            'sky.adaptors.slurm.command_runner.SlurmLoginNodeCommandRunner'
    ) as runner:
        runner.return_value.run.side_effect = subprocess.TimeoutExpired(
            'ssh', 15)
        client = slurm.SlurmClient('host', 22, 'login', slurm_user='svc')
        with pytest.raises(RuntimeError, match='timed out after 15 seconds'):
            client.validate_submit_user('a', 'svc')


def test_allocation_client_uses_stored_identity(identity_config):
    identity_config['slurm']['username_map']['Machine'] = 'new-user'
    with mock.patch('sky.provision.slurm.instance.slurm.SlurmClient') as client:
        instance._make_slurm_client({
            'ssh': {
                'hostname': 'host',
                'port': 22,
                'user': 'login'
            },
            'slurm_user': 'original-user',
        })
        assert client.call_args.kwargs['slurm_user'] == 'original-user'


def test_validation_failure_stops_allocation_creation():
    config = mock.Mock()
    config.provider_config = {
        'partition': 'cpu',
        'cluster': 'a',
        'slurm_user': 'svc',
        'ssh': {
            'hostname': 'host',
            'port': 22,
            'user': 'login'
        },
    }
    with mock.patch('sky.provision.slurm.instance.slurm.SlurmClient') as client:
        client.return_value.validate_submit_user.side_effect = RuntimeError(
            'denied')
        with pytest.raises(RuntimeError, match='denied'):
            instance.run_instances('a', 'test', 'test-on-cloud', config)
        assert client.return_value.method_calls == [
            mock.call.validate_submit_user('a', 'svc')
        ]


def test_service_account_ssh_socket_fits_macos(monkeypatch):
    monkeypatch.setattr(common_utils, 'get_user_hash', lambda: 'sa-' + 'a' * 16)
    with mock.patch('sky.utils.command_runner.os.makedirs'):
        directory = command_runner._ssh_control_path('b' * 10)
    socket_path = directory + '/' + 'c' * 40 + '.' + 'd' * 16
    assert len(socket_path.encode()) < 104


def test_missing_mapping_visible_during_resource_selection(identity_config):
    identity_config['slurm']['username_map'] = {}
    with mock.patch.object(slurm_cloud.Slurm,
                           'existing_allowed_clusters',
                           return_value=['c']), mock.patch.object(
                               slurm_cloud.logger, 'warning') as warning:
        regions = slurm_cloud.Slurm.regions_with_offering(
            '1CPU--1GB', None, False, 'c', None)
    assert not regions
    message = warning.call_args.args[0]
    assert 'Machine' in message
    assert 'Slurm cluster' in message
    assert 'username_map' in message


def test_mapping_uses_name_independent_of_id(identity_config, monkeypatch):
    monkeypatch.setattr(common_utils, 'get_current_user',
                        lambda: models.User(id='sa-9999', name='Machine'))
    assert utils.get_submit_user('a') == 'cluster-svc'


@pytest.mark.parametrize('cluster,expected', [('a', 'alice-cluster'),
                                              ('b', 'aliceabc')])
def test_sso_username_map_precedence(identity_config, monkeypatch, cluster,
                                     expected):
    monkeypatch.setattr(
        common_utils, 'get_current_user',
        lambda: models.User(id='human', name='alice@example.com'))
    identity_config['slurm']['username_map']['alice@example.com'] = 'aliceabc'
    identity_config['slurm']['cluster_configs']['a']['username_map'][
        'alice@example.com'] = 'alice-cluster'
    assert utils.get_submit_user(cluster) == expected


def test_service_account_creator_chain(identity_config, monkeypatch):
    identity_config['slurm']['username_map'].clear()
    creators = {
        'sa-1234': models.User(id='sa-parent', name='parent'),
        'sa-parent': models.User(id='human', name='jane@example.com'),
    }
    monkeypatch.setattr(utils.global_user_state, 'get_service_account_creator',
                        creators.get)
    assert utils.get_submit_user('b') == 'jane'
