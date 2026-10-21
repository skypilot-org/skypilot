"""What the provisioner does to Ray when a live cluster is launched again.

Which port the workers are told after a head restart, and whether the
workers are restarted at all.
"""
# pylint: disable=protected-access
import contextlib
from types import SimpleNamespace
from unittest import mock

import pytest

from sky import clouds
from sky.provision import common
from sky.provision import instance_setup
from sky.provision import provisioner
from sky.skylet import constants
from sky.utils import message_utils
from sky.utils import resources_utils

_ASSIGNED_PORT = 20601


class _Joined(Exception):
    """Stops the setup once the workers have been told a port."""


def _head_runner():
    """A head whose Ray probe fails until Ray is started on it."""
    up = {'ray': False}

    def run(*_, **__):
        if not up['ray']:
            return 1, '', ''
        payload = message_utils.encode_payload({'ray_port': _ASSIGNED_PORT})
        return 0, f'{payload}\n', ''

    def start(*_, **__):
        up['ray'] = True

    return mock.Mock(run=run), start


def _post_provision_setup(monkeypatch, head_runner, start_head):
    """Relaunches a live 2-node cluster; returns what the workers were told
    (ray_port, no_restart), or None if the worker start was skipped."""
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
    told = {}

    def start_workers(*_, ray_port, no_restart, **__):
        told['ray_port'] = ray_port
        told['no_restart'] = no_restart
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
    # Past the worker start, setup goes on to the skylet; stop there too.
    monkeypatch.setattr(provisioner.instance_setup, 'start_skylet_on_head_node',
                        mock.Mock(side_effect=_Joined))

    with pytest.raises(_Joined):
        provisioner._post_provision_setup(
            SimpleNamespace(cloud=clouds.Kubernetes(), region=None),
            resources_utils.ClusterName('cluster', 'cluster'), '/cluster.yaml',
            record, None)
    return told or None


def test_workers_join_the_port_the_restarted_head_took(monkeypatch):
    head_runner, start_head = _head_runner()
    told = _post_provision_setup(monkeypatch, head_runner, start_head)
    assert _ASSIGNED_PORT != constants.SKY_REMOTE_RAY_PORT
    assert told['ray_port'] == _ASSIGNED_PORT


@pytest.mark.parametrize('alive,expect_restart', [(1, True), (2, False),
                                                  (3, True)])
def test_workers_restart_unless_exactly_all_nodes_are_alive(
        monkeypatch, alive, expect_restart):
    """The head is up; only the GCS alive count decides the worker start.

    `ray status` keeps listing a worker whose Ray stopped, so counting its
    lines skipped the restart and the worker never rejoined.
    """
    payload = message_utils.encode_payload({'ray_port': _ASSIGNED_PORT})
    ray_status = ('Active:\n 1 node_aaaa\n 1 node_bbbb\nPending:\n'
                  ' (no pending nodes)\n')

    def run(cmd, **_):
        if cmd == instance_setup.RAY_ALIVE_NODE_COUNT_COMMAND:
            return 0, f'{payload}\nSKY_ALIVE_NODES {alive} 3\n', ''
        return 0, f'{payload}\n{ray_status}', ''

    start_head = mock.Mock()
    told = _post_provision_setup(monkeypatch, mock.Mock(run=run), start_head)
    assert (told is not None) == expect_restart
    # A restarted head would kill running jobs; only the workers may restart.
    start_head.assert_not_called()
    if told is not None:
        assert told['no_restart'] is True
