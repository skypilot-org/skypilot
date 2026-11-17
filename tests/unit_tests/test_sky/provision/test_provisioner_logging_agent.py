"""A logging agent that cannot run on the platform does not fail the launch."""
# pylint: disable=protected-access
import contextlib
from types import SimpleNamespace
from unittest import mock

import pytest

from sky import clouds
from sky.provision import common
from sky.provision import instance_setup
from sky.provision import provisioner
from sky.utils import message_utils
from sky.utils import resources_utils


class _SkyletStarted(Exception):
    """Stops the setup once it reaches the skylet start."""


def _post_provision_setup(monkeypatch, setup_logging, raises=_SkyletStarted):
    """Launches a 2-node cluster with a logging agent configured.

    Expects `raises`; _SkyletStarted means the setup got past the logging
    agent.
    """
    cluster_info = common.ClusterInfo(instances={
        'head': [common.InstanceInfo('head', '10.0.0.1', None, {})],
        'worker1': [common.InstanceInfo('worker1', '10.0.0.2', None, {})],
    },
                                      head_instance_id='head',
                                      provider_name='kubernetes',
                                      provider_config={})
    record = common.ProvisionRecord('kubernetes', 'ctx', None, 'cluster',
                                    'head', ['head', 'worker1'], [])
    payload = message_utils.encode_payload({'ray_port': 6380})
    head_runner = mock.Mock(run=mock.Mock(return_value=(0, f'{payload}\n', '')))
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
    for name in ('internal_file_mounts', 'setup_runtime_on_cluster',
                 'start_ray_on_head_node', 'start_ray_on_worker_nodes'):
        monkeypatch.setattr(provisioner.instance_setup, name, mock.Mock())
    monkeypatch.setattr(provisioner.logs, 'get_logging_agent',
                        mock.Mock(return_value=mock.Mock()))
    monkeypatch.setattr(provisioner.instance_setup, 'setup_logging_on_cluster',
                        setup_logging)
    monkeypatch.setattr(provisioner.instance_setup, 'start_skylet_on_head_node',
                        mock.Mock(side_effect=_SkyletStarted))

    with pytest.raises(raises):
        provisioner._post_provision_setup(
            SimpleNamespace(cloud=clouds.Kubernetes(), region=None),
            resources_utils.ClusterName('cluster', 'cluster'), '/cluster.yaml',
            record, None)


def test_unsupported_platform_warns_and_continues(monkeypatch):
    setup_logging = mock.Mock(
        side_effect=instance_setup.LoggingAgentUnsupportedError(
            'SkyPilot: cannot install fluent-bit: no '
            'apt-get, dnf or yum on this node'))
    # The `sky` logger does not propagate, so caplog cannot observe it.
    warning = mock.Mock()
    monkeypatch.setattr(provisioner.logger, 'warning', warning)
    _post_provision_setup(monkeypatch, setup_logging)
    setup_logging.assert_called_once()
    assert any('Failed to set up the logging agent' in call.args[0] and
               'no apt-get, dnf or yum' in call.args[0]
               for call in warning.call_args_list)


def test_other_agent_setup_failures_fail_the_launch(monkeypatch):
    """E.g. bad store credentials must stay visible as a launch failure."""
    setup_logging = mock.Mock(
        side_effect=RuntimeError('Failed to setup logging agent (exit code 1)'))
    _post_provision_setup(monkeypatch, setup_logging, raises=RuntimeError)
    setup_logging.assert_called_once()


def test_agent_setup_runs_when_configured(monkeypatch):
    setup_logging = mock.Mock()
    warning = mock.Mock()
    monkeypatch.setattr(provisioner.logger, 'warning', warning)
    _post_provision_setup(monkeypatch, setup_logging)
    setup_logging.assert_called_once()
    assert not any(
        'logging agent' in call.args[0] for call in warning.call_args_list)
