"""The Ray port handed to workers after the provisioner restarts the head."""
# pylint: disable=protected-access
import contextlib
from types import SimpleNamespace
from unittest import mock

import pytest

from sky import clouds
from sky.provision import common
from sky.provision import provisioner
from sky.skylet import constants
from sky.utils import message_utils
from sky.utils import resources_utils

_ASSIGNED_PORT = 20601


class _Joined(Exception):
    """Stops the setup once the workers have been told a port."""


def _head_runner():
    """A head whose `ray status` fails until Ray is started on it."""
    up = {'ray': False}

    def run(*_, **__):
        if not up['ray']:
            return 1, '', ''
        payload = message_utils.encode_payload({'ray_port': _ASSIGNED_PORT})
        return 0, f'{payload}\n', ''

    def start(*_, **__):
        up['ray'] = True

    return mock.Mock(run=run), start


def test_workers_join_the_port_the_restarted_head_took(monkeypatch):
    cluster_info = common.ClusterInfo(instances={
        'head': [common.InstanceInfo('head', '10.0.0.1', None, {})],
        'worker1': [common.InstanceInfo('worker1', '10.0.0.2', None, {})],
    },
                                      head_instance_id='head',
                                      provider_name='kubernetes',
                                      provider_config={})
    # Neither pod was just booted: Ray died on a live cluster and the user
    # launched again.
    record = common.ProvisionRecord('kubernetes', 'ctx', None, 'cluster',
                                    'head', [], [])
    head_runner, start_head = _head_runner()
    told = {}

    def start_workers(*_, ray_port, **__):
        told['ray_port'] = ray_port
        raise _Joined

    for name, value in {
            'get_cluster_yaml_dict': mock.Mock(return_value={
                'provider': {},
                'setup_commands': []
            }),
            'get_handle_from_cluster_name': mock.Mock(),
            'update_cluster_handle': mock.Mock(),
    }.items():
        monkeypatch.setattr(provisioner.global_user_state, name, value)
    monkeypatch.setattr(provisioner.provision, 'get_cluster_info',
                        mock.Mock(return_value=cluster_info))
    monkeypatch.setattr(
        provisioner.provision, 'get_command_runners',
        mock.Mock(return_value=[head_runner, mock.Mock()]))
    monkeypatch.setattr(provisioner.backend_utils, 'ssh_credential_from_yaml',
                        mock.Mock(return_value={}))
    monkeypatch.setattr(provisioner, 'wait_for_ssh', mock.Mock())
    monkeypatch.setattr(provisioner.rich_utils, 'safe_status',
                        lambda *_: contextlib.nullcontext(mock.Mock()))
    for name in ('internal_file_mounts', 'setup_runtime_on_cluster'):
        monkeypatch.setattr(provisioner.instance_setup, name, mock.Mock())
    monkeypatch.setattr(provisioner.instance_setup, 'start_ray_on_head_node',
                        start_head)
    monkeypatch.setattr(provisioner.instance_setup, 'start_ray_on_worker_nodes',
                        start_workers)

    with pytest.raises(_Joined):
        provisioner._post_provision_setup(
            SimpleNamespace(cloud=clouds.Kubernetes()),
            resources_utils.ClusterName('cluster', 'cluster'), '/cluster.yaml',
            record, None)

    assert _ASSIGNED_PORT != constants.SKY_REMOTE_RAY_PORT
    assert told['ray_port'] == _ASSIGNED_PORT
