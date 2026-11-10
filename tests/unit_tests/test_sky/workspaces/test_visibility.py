"""Which Kubernetes contexts and Slurm clusters a user may read.

Real config file and casbin policy; only the kubeconfig, ~/.slurm/config and
~/.sky/ssh_node_pools.yaml listings are faked.
"""
import json
import os

import casbin
import fastapi
import pytest
import sqlalchemy_adapter

from sky import clouds
from sky import global_user_state
from sky import models
from sky import skypilot_config
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.provision.slurm import utils as slurm_utils
from sky.skylet import constants
from sky.users import permission
from sky.utils import common_utils
from sky.utils import locks
from sky.utils.db import db_utils
from sky.utils.db import kv_cache
from sky.workspaces import visibility

_CONTEXTS = ['ctx-a', 'ctx-b', 'ctx-c', 'ctx-g', 'ssh-pool-a', 'ssh-pool-b']
_CURRENT_CONTEXT = 'ctx-c'

# alice: member of ws-a and of ws-off (every infra cloud disabled).
# bob: member of ws-b. carol: member of ws-g, which inherits the global
# values. dave: member of nothing.
_CONFIG = {
    'kubernetes': {
        'allowed_contexts': ['ctx-g']
    },
    'workspaces': {
        'default': {
            'private': True,
            'allowed_users': []
        },
        'ws-a': {
            'private': True,
            'allowed_users': ['alice'],
            'kubernetes': {
                'allowed_contexts': ['ctx-a']
            },
            'ssh': {
                'allowed_node_pools': ['pool-a']
            },
            'slurm': {
                'allowed_clusters': ['sc-a']
            },
        },
        'ws-b': {
            'private': True,
            'allowed_users': ['bob'],
            'kubernetes': {
                'allowed_contexts': ['ctx-b']
            },
            'ssh': {
                'allowed_node_pools': ['pool-b']
            },
            'slurm': {
                'allowed_clusters': ['sc-b']
            },
        },
        'ws-off': {
            'private': True,
            'allowed_users': ['alice'],
            'kubernetes': {
                'disabled': True
            },
            'ssh': {
                'disabled': True
            },
            'slurm': {
                'disabled': True
            },
        },
        'ws-g': {
            'private': True,
            'allowed_users': ['carol'],
        },
    },
}


def _write_config(path, config):
    path.write_text(json.dumps(config))


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv(constants.SKY_RUNTIME_DIR_ENV_VAR_KEY, str(tmp_path))
    monkeypatch.delenv(constants.ENV_VAR_DB_CONNECTION_URI, raising=False)
    monkeypatch.delenv(skypilot_config.ENV_VAR_SKYPILOT_CONFIG, raising=False)
    monkeypatch.setenv(constants.ENV_VAR_IS_SKYPILOT_SERVER, '1')
    monkeypatch.setattr(locks, 'SKY_LOCKS_DIR', str(tmp_path / 'locks'))
    path = tmp_path / 'config.yaml'
    _write_config(path, _CONFIG)
    monkeypatch.setenv(skypilot_config.ENV_VAR_GLOBAL_CONFIG, str(path))
    skypilot_config.reload_config()

    state_db = db_utils.DatabaseManager('state', global_user_state.create_table)
    monkeypatch.setattr(global_user_state, '_db_manager', state_db)
    monkeypatch.setattr(
        kv_cache, '_db_manager',
        db_utils.DatabaseManager('kv_cache', kv_cache.create_table))
    service = permission.PermissionService()
    service.enforcer = casbin.SyncedEnforcer(
        os.path.join(os.path.dirname(permission.__file__), 'model.conf'),
        sqlalchemy_adapter.Adapter(state_db.get_engine(),
                                   db_class=sqlalchemy_adapter.CasbinRule))
    monkeypatch.setattr(permission, 'permission_service', service)
    for user_id in ['admin', 'alice', 'bob', 'carol', 'dave']:
        service.enforcer.add_grouping_policy(
            user_id, 'admin' if user_id == 'admin' else 'user')
    for user_id, workspace in [('alice', 'ws-a'), ('alice', 'ws-off'),
                               ('bob', 'ws-b'), ('carol', 'ws-g')]:
        service.enforcer.add_policy(user_id, workspace, '*')

    monkeypatch.setattr(kubernetes_utils, 'get_all_kube_context_names',
                        lambda: list(_CONTEXTS))
    monkeypatch.setattr(kubernetes_utils,
                        'get_current_kube_config_context_name',
                        lambda: _CURRENT_CONTEXT)
    monkeypatch.setattr(kubernetes_utils, 'is_incluster_config_available',
                        lambda: False)
    monkeypatch.setattr(clouds.SSH, 'get_ssh_node_pool_contexts',
                        classmethod(lambda cls: ['ssh-pool-a', 'ssh-pool-b']))
    monkeypatch.setattr(slurm_utils, 'get_all_slurm_cluster_names',
                        lambda: ['sc-a', 'sc-b'])
    return path


def _user(user_id):
    return models.User(id=user_id, name=user_id)


def test_union_of_member_workspaces(config_path):
    scope = visibility.scope_for(_user('alice'))
    # ws-off disables every infra cloud, so it must not add the global
    # fallback (ctx-g, every SSH pool, every Slurm cluster).
    assert scope.kubernetes == {'ctx-a', 'ssh-pool-a'}
    assert scope.slurm == {'sc-a'}


def test_workspace_without_its_own_value_uses_the_global_one(config_path):
    scope = visibility.scope_for(_user('carol'))
    # Global: allowed_contexts [ctx-g]; no allowed_node_pools or
    # allowed_clusters, which mean every pool and every cluster.
    assert scope.kubernetes == {'ctx-g', 'ssh-pool-a', 'ssh-pool-b'}
    assert scope.slurm == {'sc-a', 'sc-b'}


def test_admin_and_unauthenticated_are_unscoped(config_path):
    assert visibility.scope_for(None) == visibility.UNSCOPED
    assert visibility.scope_for(_user('admin')) == visibility.UNSCOPED


def test_member_of_nothing_sees_nothing(config_path):
    scope = visibility.scope_for(_user('dave'))
    assert not scope.unscoped
    assert scope.kubernetes == frozenset()
    assert scope.slurm == frozenset()
    with pytest.raises(fastapi.HTTPException) as e:
        visibility.require_kubernetes_context(scope, 'ctx-a')
    assert e.value.status_code == 404
    with pytest.raises(fastapi.HTTPException):
        visibility.require_slurm_cluster(scope, 'sc-a')


def test_persisted_change_applies_on_the_next_call(config_path):
    """No explicit reload: scope_for must pick the file change up itself."""
    assert 'ctx-b' not in visibility.scope_for(_user('alice')).kubernetes

    config = json.loads(json.dumps(_CONFIG))
    # Open ws-b up for reading, and revoke ctx-a from ws-a.
    config['workspaces']['ws-b']['read_access'] = 'all'
    config['workspaces']['ws-a']['kubernetes']['allowed_contexts'] = ['ctx-c']
    _write_config(config_path, config)

    scope = visibility.scope_for(_user('alice'))
    assert scope.kubernetes == {'ctx-b', 'ctx-c', 'ssh-pool-a', 'ssh-pool-b'}
    assert scope.slurm == {'sc-a', 'sc-b'}


def test_one_evaluation_per_distinct_value(config_path, monkeypatch):
    config = json.loads(json.dumps(_CONFIG))
    for name in ['ws-g2', 'ws-g3']:
        config['workspaces'][name] = {
            'private': True,
            'allowed_users': ['carol']
        }
    _write_config(config_path, config)
    for name in ['ws-g2', 'ws-g3']:
        permission.permission_service.enforcer.add_policy('carol', name, '*')
    calls = []
    real = visibility._workspace_targets
    monkeypatch.setattr(
        visibility, '_workspace_targets', lambda ws, cloud: (calls.append(
            (ws, cloud)), real(ws, cloud))[1])

    scope = visibility.scope_for(_user('carol'))

    # Three workspaces share the global value: one evaluation per cloud.
    assert sorted(cloud for _, cloud in calls) == ['kubernetes', 'slurm', 'ssh']
    assert scope.kubernetes == {'ctx-g', 'ssh-pool-a', 'ssh-pool-b'}


def test_failed_workspace_contributes_nothing(config_path, monkeypatch):
    real = clouds.Kubernetes.existing_allowed_contexts.__func__

    def flaky(cls, silent=False):
        if skypilot_config.get_active_workspace() == 'ws-a':
            raise RuntimeError('kubeconfig unreadable')
        return real(cls, silent)

    monkeypatch.setattr(clouds.Kubernetes, 'existing_allowed_contexts',
                        classmethod(flaky))
    scope = visibility.scope_for(_user('alice'))
    assert scope.kubernetes == {'ssh-pool-a'}


def test_role_lookup_error_scopes_the_user(config_path, monkeypatch):
    real = permission.permission_service.get_user_roles
    failures = [RuntimeError('db down')]

    def get_user_roles(user_id):
        # Only the admin check fails; later lookups work.
        if failures:
            raise failures.pop()
        return real(user_id)

    monkeypatch.setattr(permission.permission_service, 'get_user_roles',
                        get_user_roles)
    assert not visibility.scope_for(_user('admin')).unscoped
    assert not failures


def test_listing_matches_the_guard(config_path, monkeypatch):
    """/slurm_cluster_names (executor) and the guard (API server) agree."""
    for user_id in ['alice', 'bob', 'carol', 'dave']:
        monkeypatch.setattr(common_utils,
                            'get_current_user',
                            lambda u=user_id: _user(u))
        assert (visibility.slurm_cluster_names() == sorted(
            visibility.scope_for(_user(user_id)).slurm))
