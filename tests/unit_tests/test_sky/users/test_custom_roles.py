"""Tests for fine-grained permissions and admin-defined (custom) roles."""
import sys
import types
from unittest import mock

import pytest
import sqlalchemy
import yaml

from sky import exceptions
from sky import models
from sky import skypilot_config
from sky.server import plugins
from sky.skylet import constants
from sky.users import authz
from sky.users import permission
from sky.utils import config_utils
from tests.unit_tests.test_sky.users import test_permission
from tests.unit_tests.test_sky.users.test_permission import _service_over

# Reuse the fixture: pytest finds fixtures by name in this module.
policy_engine = test_permission.policy_engine

_CORDON_PATH = '/plugins/api/nodes/cordon'

NODE_CORDON = authz.Permission('node.cordon',
                               authz.ScopeKind.CONTEXT,
                               'Cordon a node.',
                               endpoints=((_CORDON_PATH, 'post'),))
NODE_LIST_ALL = authz.Permission('node.audit', authz.ScopeKind.GLOBAL,
                                 'Read node audit records.')

_OPERATOR_CONFIG = {
    'custom_roles': {
        'node-operator': {
            'permissions': ['node.cordon'],
        },
    },
    'role_bindings': [{
        'role': 'node-operator',
        'users': ['operator'],
        'contexts': ['ctx-a'],
    }],
}


@pytest.fixture(autouse=True)
def registry():
    """A registry holding only this module's permissions."""
    authz.registry.clear()
    authz.register([NODE_CORDON, NODE_LIST_ALL])
    yield authz.registry
    authz.registry.clear()


@pytest.fixture
def rbac_config(monkeypatch):
    """Sets the `rbac` section the permission code reads."""
    current = {}

    def get_nested(keys, default_value=None, **_):
        if keys[0] != 'rbac':
            return default_value
        value = current
        for key in keys[1:]:
            if not isinstance(value, dict) or key not in value:
                return default_value
            value = value[key]
        return value

    monkeypatch.setattr(authz.skypilot_config, 'get_nested', get_nested)

    def set_config(config):
        current.clear()
        current.update(config)

    return set_config


def _holds(user_id, perm, scope, names=()):
    return authz.holds(user_id, perm, scope, lambda entry: entry in names)


class TestModel:

    def test_permission_name_needs_resource_and_verb(self):
        for bad in ('cordon', '.cordon', 'node.'):
            with pytest.raises(ValueError):
                authz.Permission(bad, authz.ScopeKind.GLOBAL)

    def test_endpoint_methods_are_uppercased(self):
        assert NODE_CORDON.endpoints == ((_CORDON_PATH, 'POST'),)

    def test_conflicting_registration_is_refused(self, registry):
        registry.register(NODE_CORDON)  # identical: fine
        with pytest.raises(ValueError):
            registry.register(
                authz.Permission('node.cordon', authz.ScopeKind.GLOBAL))

    def test_endpoint_lookup_uses_keymatch2(self, registry):
        registry.register(
            authz.Permission('node.label',
                             authz.ScopeKind.CONTEXT,
                             endpoints=(('/plugins/api/nodes/:id/label',
                                         'POST'),)))
        assert [
            p.name for p in registry.for_endpoint('/plugins/api/nodes/n1/label',
                                                  'POST')
        ] == ['node.label']
        assert registry.for_endpoint('/plugins/api/nodes/n1/label', 'GET') == []
        assert registry.for_endpoint('/status', 'POST') == []


class TestHolds:

    def test_binding_grants_only_its_contexts(self, rbac_config):
        rbac_config(_OPERATOR_CONFIG)
        assert _holds('operator', NODE_CORDON, authz.Scope.context('ctx-a'))
        assert not _holds('operator', NODE_CORDON, authz.Scope.context('ctx-b'))
        # Anywhere: what the middleware asks.
        assert _holds('operator', NODE_CORDON, None)

    def test_unbound_user_holds_nothing(self, rbac_config):
        rbac_config(_OPERATOR_CONFIG)
        assert not _holds('someone', NODE_CORDON, None)
        assert not _holds('someone', NODE_CORDON, authz.Scope.context('ctx-a'))

    def test_binding_without_contexts_grants_no_context(self, rbac_config):
        rbac_config({
            'custom_roles': _OPERATOR_CONFIG['custom_roles'],
            'role_bindings': [{
                'role': 'node-operator',
                'users': ['operator']
            }],
        })
        assert not _holds('operator', NODE_CORDON, None)
        assert not _holds('operator', NODE_CORDON, authz.Scope.context('ctx-a'))

    def test_scope_of_another_kind_does_not_match(self, rbac_config):
        rbac_config(_OPERATOR_CONFIG)
        assert not _holds('operator', NODE_CORDON, authz.GLOBAL_SCOPE)

    def test_role_without_the_permission_grants_nothing(self, rbac_config):
        rbac_config({
            'custom_roles': {
                'auditor': {
                    'permissions': ['node.audit']
                }
            },
            'role_bindings': [{
                'role': 'auditor',
                'users': ['operator'],
                'contexts': ['ctx-a'],
            }],
        })
        assert not _holds('operator', NODE_CORDON, None)
        assert _holds('operator', NODE_LIST_ALL, authz.GLOBAL_SCOPE)

    def test_built_in_role_name_cannot_be_redefined(self, rbac_config):
        rbac_config({
            'custom_roles': {
                'user': {
                    'permissions': ['node.cordon']
                }
            },
            'role_bindings': [{
                'role': 'user',
                'users': ['operator'],
                'contexts': ['ctx-a'],
            }],
        })
        assert not _holds('operator', NODE_CORDON, None)

    def test_username_entries_go_through_the_matcher(self, rbac_config):
        rbac_config({
            **_OPERATOR_CONFIG, 'role_bindings': [{
                'role': 'node-operator',
                'users': ['alice@example.com'],
                'contexts': ['ctx-a'],
            }]
        })
        assert _holds('u-1', NODE_CORDON, None, names=('alice@example.com',))
        assert not _holds('u-1', NODE_CORDON, None)

    def test_empty_config_grants_nothing(self, rbac_config):
        rbac_config({})
        assert not _holds('operator', NODE_CORDON, None)


class TestValidateConfig:

    def test_valid_config_has_no_problems(self):
        assert authz.validate_config({'rbac': _OPERATOR_CONFIG}) == []

    def test_reports_dangling_references(self):
        problems = authz.validate_config({
            'rbac': {
                'custom_roles': {
                    'admin': {
                        'permissions': []
                    },
                    'ops': {
                        'permissions': ['node.cordon', 'node.explode']
                    },
                },
                'role_bindings': [
                    {
                        'role': 'missing',
                        'users': ['a']
                    },
                    {
                        'role': 'ops',
                        'users': ['a']
                    },
                ],
            }
        })
        joined = '\n'.join(problems)
        assert "'admin' reuses a built-in name" in joined
        assert "unknown permission 'node.explode'" in joined
        assert "unknown role 'missing'" in joined
        assert 'needs `contexts`' in joined


@pytest.fixture
def service(policy_engine, rbac_config):
    """A real enforcer with the `user` role blocklisting the cordon route, the
    way a plugin's `rbac_rules` would."""
    rbac_config(_OPERATOR_CONFIG)
    svc = _service_over(policy_engine)
    svc._build_viewer_allowlist_no_lock()
    svc.enforcer.add_policy('user', _CORDON_PATH, 'POST')
    svc.enforcer.add_policy('user', '/users/create', 'POST')
    for user_id, role in (('an_admin', 'admin'), ('operator', 'user'),
                          ('plain', 'user'), ('viewer_op', 'viewer')):
        svc.enforcer.add_grouping_policy(user_id, role)
    with mock.patch('sky.users.permission._policy_lock'):
        yield svc


class TestEndpointCheck:
    """`check_endpoint_permission` returns True to block."""

    def test_custom_role_lets_a_user_through(self, service):
        assert not service.check_endpoint_permission('operator', _CORDON_PATH,
                                                     'POST')

    def test_plain_user_is_blocked(self, service):
        assert service.check_endpoint_permission('plain', _CORDON_PATH, 'POST')

    def test_admin_holds_every_permission(self, service):
        assert not service.check_endpoint_permission('an_admin', _CORDON_PATH,
                                                     'POST')

    def test_custom_role_also_applies_to_a_viewer(self, service, rbac_config):
        assert service.check_endpoint_permission('viewer_op', _CORDON_PATH,
                                                 'POST')
        rbac_config({
            **_OPERATOR_CONFIG, 'role_bindings': [{
                'role': 'node-operator',
                'users': ['viewer_op'],
                'contexts': ['ctx-a'],
            }]
        })
        assert not service.check_endpoint_permission('viewer_op', _CORDON_PATH,
                                                     'POST')

    def test_custom_role_does_not_widen_other_endpoints(self, service):
        assert service.check_endpoint_permission('operator', '/users/create',
                                                 'POST')

    def test_principal_without_a_role_stays_denied(self, service, rbac_config):
        rbac_config({
            **_OPERATOR_CONFIG, 'role_bindings': [{
                'role': 'node-operator',
                'users': ['ghost'],
                'contexts': ['ctx-a'],
            }]
        })
        assert service.check_endpoint_permission('ghost', _CORDON_PATH, 'POST')

    def test_system_viewer_identity_is_judged_by_permission(self, service):
        assert service.check_endpoint_permission(
            constants.SKYPILOT_SYSTEM_VIEWER_USER_ID, _CORDON_PATH, 'POST')
        assert not service.check_endpoint_permission(
            constants.SKYPILOT_SYSTEM_USER_ID, _CORDON_PATH, 'POST')


def _add_user(engine, user_id, name):
    with engine.begin() as conn:
        conn.execute(
            sqlalchemy.text('INSERT INTO users (id, name) VALUES (:id, :n)'), {
                'id': user_id,
                'n': name
            })


def _allowed(svc, user_id, perm, scope):
    """The decision `requires` makes for this user and scope."""
    return svc._holds(user_id, svc.get_user_roles(user_id), perm, scope)


class TestScopedCheck:

    def test_scope_is_exact(self, service):
        ctx_a = authz.Scope.context('ctx-a')
        assert _allowed(service, 'operator', NODE_CORDON, ctx_a)
        assert not _allowed(service, 'operator', NODE_CORDON,
                            authz.Scope.context('ctx-b'))
        assert _allowed(service, 'an_admin', NODE_CORDON,
                        authz.Scope.context('ctx-b'))
        assert not _allowed(service, 'plain', NODE_CORDON, ctx_a)

    def test_binding_by_unique_username(self, service, policy_engine,
                                        rbac_config, monkeypatch):
        monkeypatch.setattr(permission.global_user_state._db_manager,
                            'get_engine', lambda: policy_engine)
        _add_user(policy_engine, 'plain', 'pat@example.com')
        rbac_config({
            **_OPERATOR_CONFIG, 'role_bindings': [{
                'role': 'node-operator',
                'users': ['pat@example.com'],
                'contexts': ['ctx-a'],
            }]
        })
        ctx_a = authz.Scope.context('ctx-a')
        assert _allowed(service, 'plain', NODE_CORDON, ctx_a)
        # A second account with the same name: the name no longer identifies
        # either of them.
        _add_user(policy_engine, 'plain-2', 'pat@example.com')
        assert not _allowed(service, 'plain', NODE_CORDON, ctx_a)


@pytest.fixture
def on_server(monkeypatch):
    monkeypatch.setenv(constants.ENV_VAR_IS_SKYPILOT_SERVER, '1')
    monkeypatch.delenv(constants.OVERRIDE_CONSOLIDATION_MODE, raising=False)
    monkeypatch.delenv(constants.IS_SKYPILOT_SERVE_CONTROLLER, raising=False)


def _as(monkeypatch, user_id):
    monkeypatch.setattr(permission.common_utils, 'get_current_user',
                        lambda: models.User(id=user_id, name=user_id))


class TestRequires:

    @pytest.fixture
    def cordon(self, service, monkeypatch):
        monkeypatch.setattr(permission, 'permission_service', service)
        calls = []

        @permission.requires(NODE_CORDON,
                             scope=lambda a: authz.Scope.context(a['context']))
        def cordon_node(context, node_name, **kwargs):
            del kwargs
            calls.append((context, node_name))
            return 'done'

        return cordon_node, calls

    def test_holder_runs_in_its_context(self, cordon, on_server, monkeypatch):
        del on_server
        fn, calls = cordon
        _as(monkeypatch, 'operator')
        assert fn('ctx-a', node_name='n1') == 'done'
        assert calls == [('ctx-a', 'n1')]

    def test_other_context_is_denied_before_running(self, cordon, on_server,
                                                    monkeypatch):
        del on_server
        fn, calls = cordon
        _as(monkeypatch, 'operator')
        with pytest.raises(exceptions.PermissionDeniedError,
                           match="'node.cordon' on context 'ctx-b'"):
            fn(context='ctx-b', node_name='n1')
        assert calls == []

    def test_plain_user_is_denied(self, cordon, on_server, monkeypatch):
        del on_server
        fn, calls = cordon
        _as(monkeypatch, 'plain')
        with pytest.raises(exceptions.PermissionDeniedError):
            fn('ctx-a', 'n1')
        assert calls == []

    def test_off_server_is_not_checked(self, cordon, monkeypatch):
        monkeypatch.delenv(constants.ENV_VAR_IS_SKYPILOT_SERVER, raising=False)
        fn, calls = cordon
        _as(monkeypatch, 'plain')
        assert fn('ctx-b', 'n1') == 'done'
        assert calls == [('ctx-b', 'n1')]

    def test_controller_server_is_not_checked(self, cordon, on_server,
                                              monkeypatch):
        del on_server
        monkeypatch.setenv(constants.IS_SKYPILOT_SERVE_CONTROLLER, 'false')
        fn, _ = cordon
        _as(monkeypatch, 'plain')
        assert fn('ctx-b', 'n1') == 'done'

    def test_scoped_permission_needs_a_scope(self):
        with pytest.raises(ValueError, match='pass `scope`'):
            permission.requires(NODE_CORDON)

    def test_global_permission_needs_no_scope(self, service, on_server,
                                              monkeypatch, rbac_config):
        del on_server
        monkeypatch.setattr(permission, 'permission_service', service)
        rbac_config({
            'custom_roles': {
                'auditor': {
                    'permissions': ['node.audit']
                }
            },
            'role_bindings': [{
                'role': 'auditor',
                'users': ['operator']
            }],
        })

        @permission.requires(NODE_LIST_ALL)
        def audit():
            return 'records'

        _as(monkeypatch, 'operator')
        assert audit() == 'records'
        _as(monkeypatch, 'plain')
        with pytest.raises(exceptions.PermissionDeniedError):
            audit()


def test_load_plugin_permissions(monkeypatch, tmp_path):
    module_name = 'sky_test_permissions_plugin'

    class NodePlugin(plugins.BasePlugin):

        @property
        def permissions(self):
            return [NODE_CORDON]

        def install(self, extension_context):
            pass

    NodePlugin.__module__ = module_name
    module = types.ModuleType(module_name)
    module.NodePlugin = NodePlugin
    monkeypatch.setitem(sys.modules, module_name, module)
    config_path = tmp_path / 'plugins.yaml'
    config_path.write_text(
        yaml.safe_dump({'plugins': [{
            'class': f'{module_name}.NodePlugin'
        }]}))
    monkeypatch.setenv(plugins._PLUGINS_CONFIG_ENV_VAR, str(config_path))
    monkeypatch.setattr(plugins, '_PLUGIN_PERMISSIONS', [])

    assert plugins.load_plugin_permissions() == [NODE_CORDON]
    assert plugins.get_plugin_permissions() == [NODE_CORDON]


def test_plugin_permissions_reach_the_registry(registry, monkeypatch):
    registry.clear()
    monkeypatch.setattr(plugins, '_PLUGIN_PERMISSIONS', [NODE_CORDON])
    permission.PermissionService._register_plugin_permissions()
    assert registry.get('node.cordon') == NODE_CORDON


class TestReviewFixes:
    """Failure paths: forged config, chosen names, HEAD, broken config."""

    def test_client_override_cannot_bring_its_own_bindings(self, monkeypatch):
        """A request's config override must not reach the permission check."""
        ctx = skypilot_config.ConfigContext(
            config=config_utils.Config.from_dict({'rbac': _OPERATOR_CONFIG}))
        monkeypatch.setattr(skypilot_config, '_get_config_context', lambda: ctx)
        forged = {
            'rbac': {
                'custom_roles': {
                    'x': {
                        'permissions': ['node.cordon']
                    }
                },
                'role_bindings': [{
                    'role': 'x',
                    'users': ['operator'],
                    'contexts': ['prod'],
                }],
            }
        }
        for from_loopback in (True, False):
            with skypilot_config.override_skypilot_config(
                    forged, from_loopback=from_loopback):
                assert not _holds('operator', NODE_CORDON,
                                  authz.Scope.context('prod'))
                # The server's own binding still applies.
                assert _holds('operator', NODE_CORDON,
                              authz.Scope.context('ctx-a'))

    def test_head_matches_get_declarations(self, registry):
        perm = authz.Permission('node.read',
                                authz.ScopeKind.CONTEXT,
                                endpoints=(('/plugins/api/nodes', 'GET'),))
        registry.register(perm)
        assert registry.for_endpoint('/plugins/api/nodes', 'HEAD') == [perm]

    def test_broken_binding_denies_instead_of_raising(self, service,
                                                      rbac_config):
        rbac_config({**_OPERATOR_CONFIG, 'role_bindings': [{'users': ['x']}]})
        # Missing `role`: reading the bindings raises; the middleware denies.
        assert service.check_endpoint_permission('operator', _CORDON_PATH,
                                                 'POST')
        assert not service.check_endpoint_permission('an_admin', _CORDON_PATH,
                                                     'POST')


def _add_typed_user(engine, user_id, name, user_type):
    with engine.begin() as conn:
        conn.execute(
            sqlalchemy.text('INSERT INTO users (id, name, type) '
                            'VALUES (:id, :n, :t)'), {
                                'id': user_id,
                                'n': name,
                                't': user_type
                            })


class TestNameMatching:
    """A binding naming a person by name must not be taken by a chosen name."""

    @pytest.fixture
    def by_name(self, service, policy_engine, rbac_config, monkeypatch):
        monkeypatch.setattr(permission.global_user_state._db_manager,
                            'get_engine', lambda: policy_engine)
        rbac_config({
            **_OPERATOR_CONFIG, 'role_bindings': [{
                'role': 'node-operator',
                'users': ['alice@example.com'],
                'contexts': ['ctx-a'],
            }]
        })
        return service

    def test_service_account_cannot_squat_a_name(self, by_name, policy_engine):
        _add_typed_user(policy_engine, 'plain', 'alice@example.com', 'sa')
        assert not _allowed(by_name, 'plain', NODE_CORDON,
                            authz.Scope.context('ctx-a'))

    def test_service_account_with_the_name_does_not_deny_the_person(
            self, by_name, policy_engine):
        _add_typed_user(policy_engine, 'operator', 'alice@example.com', 'sso')
        _add_typed_user(policy_engine, 'plain', 'alice@example.com', 'sa')
        assert _allowed(by_name, 'operator', NODE_CORDON,
                        authz.Scope.context('ctx-a'))
        assert not _allowed(by_name, 'plain', NODE_CORDON,
                            authz.Scope.context('ctx-a'))

    def test_name_equal_to_another_users_id_does_not_match(
            self, by_name, policy_engine, rbac_config):
        _add_typed_user(policy_engine, 'victim-id', 'victim', 'sso')
        _add_typed_user(policy_engine, 'plain', 'victim-id', 'sso')
        rbac_config({
            **_OPERATOR_CONFIG, 'role_bindings': [{
                'role': 'node-operator',
                'users': ['victim-id'],
                'contexts': ['ctx-a'],
            }]
        })
        assert not _allowed(by_name, 'plain', NODE_CORDON,
                            authz.Scope.context('ctx-a'))


def test_untyped_legacy_service_account_cannot_squat_a_name(
        service, policy_engine, rbac_config, monkeypatch):
    monkeypatch.setattr(permission.global_user_state._db_manager, 'get_engine',
                        lambda: policy_engine)
    service.enforcer.add_grouping_policy('sa-legacy', 'user')
    _add_user(policy_engine, 'sa-legacy', 'alice@example.com')  # no type
    rbac_config({
        **_OPERATOR_CONFIG, 'role_bindings': [{
            'role': 'node-operator',
            'users': ['alice@example.com'],
            'contexts': ['ctx-a'],
        }]
    })
    assert not _allowed(service, 'sa-legacy', NODE_CORDON,
                        authz.Scope.context('ctx-a'))
    # Nor does it make the name ambiguous for the person who owns it.
    _add_typed_user(policy_engine, 'operator', 'alice@example.com', 'sso')
    assert _allowed(service, 'operator', NODE_CORDON,
                    authz.Scope.context('ctx-a'))


def test_database_error_is_not_turned_into_a_denial(service, monkeypatch):
    monkeypatch.setattr(
        permission.authz, 'holds',
        mock.Mock(side_effect=sqlalchemy.exc.OperationalError('q', {}, None)))
    with pytest.raises(sqlalchemy.exc.OperationalError):
        service.check_endpoint_permission('operator', _CORDON_PATH, 'POST')
