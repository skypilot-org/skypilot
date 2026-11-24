"""Server-side readers without a request workspace see every workspace's
Kubernetes contexts, not only the server workspace's.

Real config file; only the kubeconfig and SSH node pool listings are faked.
"""
from unittest import mock

import pytest
import yaml

from sky import core
from sky import models
from sky import skypilot_config
from sky.clouds import ssh as ssh_cloud
from sky.metrics import utils as metrics_utils
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.server import metrics
from sky.server import server
from sky.skylet import constants


@pytest.fixture
def configure(tmp_path, monkeypatch):
    monkeypatch.setenv(constants.ENV_VAR_IS_SKYPILOT_SERVER, '1')
    monkeypatch.delenv(skypilot_config.ENV_VAR_PROJECT_CONFIG, raising=False)
    monkeypatch.delenv(skypilot_config.ENV_VAR_SKYPILOT_CONFIG, raising=False)
    monkeypatch.setattr(kubernetes_utils, 'get_all_kube_context_names',
                        lambda: ['ctx-a', 'ctx-b', 'ctx-c'])
    monkeypatch.setattr(ssh_cloud, 'SSH_NODE_POOLS_PATH',
                        str(tmp_path / 'none.yaml'))

    def _configure(config):
        path = tmp_path / 'config.yaml'
        path.write_text(yaml.safe_dump(config))
        monkeypatch.setenv(skypilot_config.ENV_VAR_GLOBAL_CONFIG, str(path))
        skypilot_config.reload_config()

    yield _configure
    monkeypatch.undo()
    skypilot_config.reload_config()


_CONFIG = {
    'kubernetes': {
        'allowed_contexts': ['ctx-a']
    },
    'workspaces': {
        'ws-b': {
            'kubernetes': {
                'allowed_contexts': ['ctx-b']
            }
        }
    },
}


def test_adds_contexts_only_another_workspace_allows(configure):
    configure(_CONFIG)
    assert core.get_all_contexts() == ['ctx-a']
    assert core.get_every_workspace_contexts() == ['ctx-a', 'ctx-b']


def test_ssh_node_pools_are_kept(configure, tmp_path, monkeypatch):
    """SSH node pools come from get_all_contexts (the pools file), unchanged."""
    pools = tmp_path / 'ssh_node_pools.yaml'
    pools.write_text(yaml.safe_dump({'pool-a': {'hosts': ['h']}}))
    monkeypatch.setattr(ssh_cloud, 'SSH_NODE_POOLS_PATH', str(pools))
    configure(_CONFIG)
    assert core.get_every_workspace_contexts() == [
        'ctx-a', 'ctx-b', 'ssh-pool-a'
    ]


def test_no_other_workspaces_is_get_all_contexts(configure):
    configure({'kubernetes': {'allowed_contexts': ['ctx-c', 'ctx-a']}})
    assert core.get_every_workspace_contexts() == core.get_all_contexts()


def test_disabled_workspace_adds_nothing_and_server_list_stays(configure):
    configure({
        'kubernetes': {
            'allowed_contexts': ['ctx-a']
        },
        'workspaces': {
            'default': {
                'kubernetes': {
                    'disabled': True
                }
            },
            'ws-b': {
                'kubernetes': {
                    'allowed_contexts': ['ctx-b'],
                    'disabled': True
                }
            },
        },
    })
    assert core.get_every_workspace_contexts() == ['ctx-a']


def test_federation_targets_cover_every_workspace(configure, monkeypatch):
    configure(_CONFIG)
    monkeypatch.setattr(metrics.skypilot_config, 'reload_config', lambda: None)
    monkeypatch.setattr(metrics.metrics_utils, 'split_local_remote_contexts',
                        lambda contexts: ([], list(contexts)))
    monkeypatch.setattr(metrics.metrics_utils, 'get_slurm_metrics_clusters',
                        lambda: [])
    remote, _ = metrics._FederationTargets()._prologue()  # pylint: disable=protected-access
    assert remote == ['ctx-a', 'ctx-b']


def test_dashboard_local_contexts_refresh_before_listing(
        configure, monkeypatch):
    """The dashboard lists contexts after refreshing config, as the federation
    does, so a context a workspace just allowed is classified."""
    configure({'kubernetes': {'allowed_contexts': ['ctx-a']}})
    # Only the refresh makes ws-b (and ctx-b) visible.
    refresh = mock.MagicMock(side_effect=lambda: configure(_CONFIG))
    split = mock.MagicMock(side_effect=lambda contexts: (list(contexts), []))
    monkeypatch.setattr(server.common,
                        'refresh_workspace_state_for_sync_handler', refresh)
    monkeypatch.setattr(metrics_utils, 'split_local_remote_contexts', split)
    local = server._get_local_contexts(None)  # pylint: disable=protected-access
    assert local == ['ctx-a', 'ctx-b']
    split.assert_called_once_with(['ctx-a', 'ctx-b'])


@pytest.mark.parametrize('admin, readable, expected', [
    (False, ['default'], ['ctx-a']),
    (False, ['ws-b'], ['ctx-b']),
    (False, ['default', 'ws-b'], ['ctx-a', 'ctx-b']),
    (False, [], []),
    (True, [], ['ctx-a', 'ctx-b']),
])
def test_dashboard_local_contexts_only_name_the_callers_contexts(
        configure, monkeypatch, admin, readable, expected):
    configure(_CONFIG)
    monkeypatch.setattr(server.permission, 'is_admin', lambda user_id: admin)
    monkeypatch.setattr(server.common,
                        'refresh_workspace_state_for_sync_handler',
                        lambda: None)
    monkeypatch.setattr(metrics_utils, 'split_local_remote_contexts',
                        lambda contexts: (list(contexts), []))
    monkeypatch.setattr(server.workspaces_core, 'workspaces_for_user',
                        lambda user_id: {w: {} for w in readable})
    user = models.User(id='alice-id', name='alice')
    assert server._get_local_contexts(user) == expected  # pylint: disable=protected-access
