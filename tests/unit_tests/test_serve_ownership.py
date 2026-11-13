"""Serve operations act only on services the caller created.

One controller runs every user's services, so the API server checks the
creator recorded for each service before down, update, terminate-replica
and log reads reach it. Admins may manage any service.
"""
# pylint: disable=protected-access
import contextlib
import pickle
from unittest import mock

import pytest
import sqlalchemy

from sky import backends
from sky import exceptions
from sky import models
from sky import task as task_lib
from sky.serve import constants as serve_constants
from sky.serve import runner as serve_runner
from sky.serve import serve_state
from sky.serve import serve_utils
from sky.serve.server import core as serve_core
from sky.serve.server import impl
from sky.skylet import constants
from sky.utils import status_lib

_ALICE = models.User(id='alice-hash', name='alice')
_RECORDS = [
    {
        'name': 'alice-svc',
        'user_hash': 'alice-hash'
    },
    {
        'name': 'alice-svc-2',
        'user_hash': 'alice-hash'
    },
    {
        'name': 'bob-svc',
        'user_hash': 'bob-hash'
    },
    # Written by a version that did not record the creator.
    {
        'name': 'legacy-svc',
        'user_hash': None
    },
]


@pytest.fixture(autouse=True)
def _on_server(monkeypatch):
    monkeypatch.setenv(constants.ENV_VAR_IS_SKYPILOT_SERVER, '1')
    serve_runner.reset_for_testing()
    yield
    serve_runner.reset_for_testing()


@contextlib.contextmanager
def _caller(roles=('user',)):
    with mock.patch('sky.serve.server.impl.common_utils.get_current_user',
                    return_value=_ALICE), \
         mock.patch('sky.users.permission.permission_service.get_user_roles',
                    return_value=list(roles)):
        yield


def _handle():
    handle = mock.MagicMock(spec=backends.CloudVmRayResourceHandle)
    handle.is_grpc_enabled_with_flag = True
    return handle


def _backend():
    return mock.MagicMock(spec=backends.CloudVmRayBackend)


def _status_of(records):
    """A status lookup over ``records``: exact names, no expansion."""

    def _status(*, handle, service_names, pool):
        del handle
        return [
            dict(r)
            for r in records
            if (service_names is None or r['name'] in service_names) and
            r.get('pool', pool) == pool
        ]

    return _status


@contextlib.contextmanager
def _down_env(records=_RECORDS):
    runner = mock.Mock()
    runner.get_service_status.side_effect = _status_of(records)
    serve_runner.register(runner)
    with mock.patch('sky.serve.server.impl.controller_utils.'
                    'get_controller_for_pool'), \
         mock.patch('sky.serve.server.impl.backend_utils.'
                    'is_controller_accessible', return_value=_handle()), \
         mock.patch('sky.serve.server.impl.serve_rpc_utils.RpcRunner.'
                    'terminate_services', return_value='ok') as terminate:
        yield terminate


def _terminated(terminate):
    """The names handed to the controller, which must always be concrete."""
    assert terminate.call_count == 1
    names = terminate.call_args.args[1]
    assert isinstance(names, list)
    return sorted(names)


class TestDown:

    def test_own_service_by_name(self):
        with _caller(), _down_env() as terminate:
            impl.down(service_names=['alice-svc'])
        assert _terminated(terminate) == ['alice-svc']

    def test_other_users_service_is_denied(self):
        with _caller(), _down_env() as terminate:
            with pytest.raises(exceptions.PermissionDeniedError,
                               match='bob-svc'):
                impl.down(service_names=['bob-svc'])
        terminate.assert_not_called()

    def test_one_denied_name_blocks_the_whole_request(self):
        with _caller(), _down_env() as terminate:
            with pytest.raises(exceptions.PermissionDeniedError):
                impl.down(service_names=['alice-svc', 'bob-svc'])
        terminate.assert_not_called()

    def test_pattern_matching_only_own_services(self):
        with _caller(), _down_env() as terminate:
            impl.down(service_names=['alice-*'])
        assert _terminated(terminate) == ['alice-svc', 'alice-svc-2']

    def test_pattern_reaching_another_users_service_is_denied(self):
        with _caller(), _down_env() as terminate:
            with pytest.raises(exceptions.PermissionDeniedError):
                impl.down(service_names=['*'])
        terminate.assert_not_called()

    def test_pattern_matching_nothing_never_reaches_the_controller(self):
        """An empty list must not be handed over: the controller reads a
        missing list as every service."""
        with _caller(), _down_env() as terminate:
            impl.down(service_names=['no-such-*'])
        terminate.assert_not_called()

    def test_all_with_nothing_owned_never_reaches_the_controller(self):
        carol = models.User(id='carol-hash', name='carol')
        with mock.patch('sky.serve.server.impl.common_utils.get_current_user',
                        return_value=carol), \
             mock.patch('sky.users.permission.permission_service.'
                        'get_user_roles', return_value=['user']), \
             _down_env() as terminate:
            impl.down(all=True)
        terminate.assert_not_called()

    def test_every_down_looks_up_every_service(self):
        """What a name reaches is decided against the whole table, since the
        controller expands even an exact name."""
        with _caller(), _down_env():
            impl.down(service_names=['alice-svc'])
            lookup = serve_runner.current().get_service_status.call_args
        assert lookup.kwargs['service_names'] is None

    def test_all_takes_only_own_services(self):
        with _caller(), _down_env() as terminate, \
             mock.patch('sky.serve.server.impl.logger.info') as info:
            impl.down(all=True)
        assert _terminated(terminate) == ['alice-svc', 'alice-svc-2']
        assert any('Skipped 2 services not owned by you' in str(call)
                   for call in info.call_args_list)

    def test_record_without_creator_is_admin_only(self):
        with _caller(), _down_env() as terminate:
            with pytest.raises(exceptions.PermissionDeniedError,
                               match='legacy-svc'):
                impl.down(service_names=['legacy-svc'])
        terminate.assert_not_called()

    def test_admin_may_take_down_any_service(self):
        with _caller(roles=('admin',)), _down_env() as terminate:
            impl.down(service_names=['bob-svc', 'legacy-svc'])
        assert _terminated(terminate) == ['bob-svc', 'legacy-svc']

    def test_admin_downs_by_name_when_the_lookup_fails(self):
        """An older controller fails the lookup on one broken service; an
        admin can still take it down by name."""
        with _caller(roles=('admin',)), _down_env() as terminate:
            serve_runner.current().get_service_status.side_effect = (
                RuntimeError('broken'))
            impl.down(service_names=['bob-svc'], purge=True)
        assert _terminated(terminate) == ['bob-svc']

    @pytest.mark.parametrize('roles,kwargs', [
        (('user',), {
            'service_names': ['alice-svc']
        }),
        (('admin',), {
            'all': True
        }),
    ])
    def test_failed_lookup_refuses_others(self, roles, kwargs):
        with _caller(roles=roles), _down_env() as terminate:
            serve_runner.current().get_service_status.side_effect = (
                RuntimeError('broken'))
            with pytest.raises(RuntimeError, match='broken'):
                impl.down(**kwargs)
        terminate.assert_not_called()

    def test_admin_all_takes_everything(self):
        with _caller(roles=('admin',)), _down_env() as terminate:
            impl.down(all=True)
        assert _terminated(terminate) == sorted(r['name'] for r in _RECORDS)

    def test_record_without_creator_is_explained(self):
        """To its creator the denial would otherwise read as someone else
        owning the service."""
        with _caller(), _down_env():
            with pytest.raises(exceptions.PermissionDeniedError,
                               match='no recorded creator'):
                impl.down(service_names=['legacy-svc'])

    def test_pool_wording(self):
        with _caller(), _down_env():
            with pytest.raises(exceptions.PermissionDeniedError,
                               match='cannot manage pool'):
                impl.down(service_names=['bob-svc'], pool=True)


# Names that an unescaped LIKE merges: `_` matches any character and SQLite
# ignores ASCII case.
_COLLIDING = [
    {
        'name': 'svc_a',
        'user_hash': 'alice-hash'
    },
    {
        'name': 'svcxa',
        'user_hash': 'bob-hash'
    },
    {
        'name': 'svc-b',
        'user_hash': 'alice-hash'
    },
    {
        'name': 'SVC-B',
        'user_hash': 'bob-hash'
    },
]


class TestControllerExpansion:
    """The controller expands every name it is handed; a name is sent only
    when that expansion, as older controllers do it, stays within what the
    caller may manage."""

    def test_underscore_reaching_another_users_service_is_denied(self):
        with _caller(), _down_env(_COLLIDING) as terminate:
            with pytest.raises(exceptions.PermissionDeniedError,
                               match="'svc_a' also reaches 'svcxa'"):
                impl.down(service_names=['svc_a'])
        terminate.assert_not_called()

    def test_case_variant_of_another_users_service_is_denied(self):
        with _caller(), _down_env(_COLLIDING) as terminate:
            with pytest.raises(exceptions.PermissionDeniedError,
                               match="'svc-b' also reaches 'SVC-B'"):
                impl.down(service_names=['svc-b'])
        terminate.assert_not_called()

    def test_all_stops_at_a_name_that_reaches_another_users_service(self):
        with _caller(), _down_env(_COLLIDING) as terminate:
            with pytest.raises(exceptions.PermissionDeniedError):
                impl.down(all=True)
        terminate.assert_not_called()

    def test_pattern_is_read_the_way_the_controller_reads_it(self):
        """`svc_*` reaches bob's `svcxa` on the controller, so it is denied
        even though `_` was meant literally."""
        with _caller(), _down_env(_COLLIDING) as terminate:
            with pytest.raises(exceptions.PermissionDeniedError, match='svcxa'):
                impl.down(service_names=['svc_*'])
        terminate.assert_not_called()

    def test_collision_among_own_services_is_refused_too(self):
        """The controller would terminate the other one as well."""
        records = [dict(r, user_hash='alice-hash') for r in _COLLIDING]
        with _caller(), _down_env(records) as terminate:
            with pytest.raises(ValueError,
                               match="'svc_a' also reaches 'svcxa'"):
                impl.down(service_names=['svc_a'])
        terminate.assert_not_called()

    def test_naming_every_reached_service_passes(self):
        records = [dict(r, user_hash='alice-hash') for r in _COLLIDING]
        with _caller(), _down_env(records) as terminate:
            impl.down(service_names=['svc_a', 'svcxa'])
        assert _terminated(terminate) == ['svc_a', 'svcxa']

    def test_admin_is_refused_too(self):
        with _caller(roles=('admin',)), _down_env(_COLLIDING) as terminate:
            with pytest.raises(ValueError, match="also reaches 'svcxa'"):
                impl.down(service_names=['svc_a'])
        terminate.assert_not_called()
        with _caller(roles=('admin',)), _down_env(_COLLIDING) as terminate:
            impl.down(service_names=['svc_a', 'svcxa'])
        assert _terminated(terminate) == ['svc_a', 'svcxa']


_EXACT = [dict(r, serve_version=6) for r in _COLLIDING]


class TestControllerMatchingExactly:
    """A controller that reports protocol version 6 or later terminates
    exactly the names it is handed, so the older expansion is not modelled
    and a look-alike name owned by someone else blocks nothing."""

    def test_underscore_look_alike_does_not_block(self):
        with _caller(), _down_env(_EXACT) as terminate:
            impl.down(service_names=['svc_a'])
        assert _terminated(terminate) == ['svc_a']

    def test_case_look_alike_does_not_block(self):
        with _caller(), _down_env(_EXACT) as terminate:
            impl.down(service_names=['svc-b'])
        assert _terminated(terminate) == ['svc-b']

    def test_all_takes_own_services_only(self):
        with _caller(), _down_env(_EXACT) as terminate:
            impl.down(all=True)
        assert _terminated(terminate) == ['svc-b', 'svc_a']

    def test_other_users_service_is_still_denied(self):
        with _caller(), _down_env(_EXACT) as terminate:
            with pytest.raises(exceptions.PermissionDeniedError):
                impl.down(service_names=['svcxa'])
        terminate.assert_not_called()

    def test_one_older_record_keeps_the_model(self):
        """The records come from one controller; a record without the
        version means that controller is older."""
        records = _EXACT[:1] + _COLLIDING[1:]
        with _caller(), _down_env(records) as terminate:
            with pytest.raises(exceptions.PermissionDeniedError):
                impl.down(service_names=['svc_a'])
        terminate.assert_not_called()


@pytest.fixture()
def serve_db(tmp_path):
    engine = sqlalchemy.create_engine(f'sqlite:///{tmp_path / "serve.db"}')
    serve_state._db_manager._engine = engine
    serve_state.create_table(engine)
    yield engine
    serve_state._db_manager._engine = None


def _seed(engine, records):
    with engine.begin() as conn:
        conn.execute(serve_state.services_table.insert(),
                     [dict(r, status='READY') for r in records])


class TestGlobServiceNames:
    """The controller's own expansion, on a real services table."""

    @pytest.fixture(autouse=True)
    def _services(self, serve_db):
        _seed(
            serve_db, _COLLIDING + [{
                'name': 'pct%',
                'user_hash': None
            }, {
                'name': 'pctx',
                'user_hash': None
            }])

    def test_exact_name_matches_itself_only(self):
        assert serve_state.get_glob_service_names(['svc_a']) == ['svc_a']
        assert serve_state.get_glob_service_names(['svc-b']) == ['svc-b']
        assert serve_state.get_glob_service_names(['svcxa']) == ['svcxa']

    def test_underscore_and_percent_are_literal_in_a_pattern(self):
        assert serve_state.get_glob_service_names(['svc_*']) == ['svc_a']
        assert serve_state.get_glob_service_names(['pct%*']) == ['pct%']

    def test_star_expands(self):
        assert set(serve_state.get_glob_service_names(
            ['svc*'])) >= {'svc_a', 'svcxa', 'svc-b'}

    def test_none_is_every_service(self):
        assert len(serve_state.get_glob_service_names(None)) == 6


class TestStatusTolerance:
    """One service whose status cannot be built does not hide the others:
    `down`, logs and terminate-replica look every service up."""

    @pytest.fixture(autouse=True)
    def _services(self, serve_db):
        _seed(serve_db, _COLLIDING[:2])
        with serve_db.begin() as conn:
            conn.execute(serve_state.version_specs_table.insert(), [{
                'service_name': r['name'],
                'version': 1,
                'spec': pickle.dumps(None),
                'yaml_content': 'run: echo\n',
            } for r in _COLLIDING[:2]])

    def test_a_failing_record_is_reported_bare(self):
        full = {'name': 'svc_a', 'user_hash': 'alice-hash', 'replica_info': [1]}

        def _status(name, pool):
            del pool
            if name == 'svcxa':
                raise RuntimeError('controller gone')
            return dict(full)

        with mock.patch('sky.serve.serve_utils._get_service_status',
                        side_effect=_status):
            records = serve_utils.unpickle_service_status(
                serve_utils.get_service_status_pickled(None, pool=False))
        by_name = {r['name']: r for r in records}
        assert by_name['svc_a']['replica_info'] == [1]
        assert by_name['svcxa']['user_hash'] == 'bob-hash'
        assert by_name['svcxa']['replica_info'] == []
        assert by_name['svcxa']['target_num_replicas'] is None


class TestVersionStamp:
    """Every record the controller reports carries the version it matches
    names with, so the server can tell this controller matches exactly."""

    @pytest.fixture(autouse=True)
    def _services(self, serve_db):
        _seed(serve_db, _COLLIDING)
        # A record is reported with its latest version, so each needs one.
        with serve_db.begin() as conn:
            conn.execute(serve_state.version_specs_table.insert(), [{
                'service_name': r['name'],
                'version': 1,
                'spec': pickle.dumps(None),
                'yaml_content': 'run: echo\n',
            } for r in _COLLIDING])

    def test_records_carry_the_controller_version(self):
        record = serve_state.get_service_from_name('svc_a')
        assert record is not None
        assert record['serve_version'] == serve_constants.SERVE_VERSION
        for record in serve_state.get_services():
            assert record['serve_version'] == serve_constants.SERVE_VERSION

    def test_this_controller_matches_names_exactly(self):
        assert impl._EXACT_NAME_SERVE_VERSION <= serve_constants.SERVE_VERSION
        assert impl._controller_matches_names_exactly(
            serve_state.get_services())


class TestDownThroughTheRealExpansion:
    """`down`, with the names it sends expanded by the real controller
    function on a real services table, terminates only what it resolved."""

    @pytest.fixture(autouse=True)
    def _services(self, serve_db):
        _seed(serve_db, _COLLIDING + _RECORDS)

    @contextlib.contextmanager
    def _env(self, records):
        expanded = []
        with _down_env(records) as terminate:
            terminate.side_effect = (
                lambda handle, names, purge, pool: expanded.extend(
                    serve_state.get_glob_service_names(names)))
            yield expanded

    def test_pattern_terminates_only_own_services(self):
        with _caller(), self._env(_COLLIDING + _RECORDS) as expanded:
            impl.down(service_names=['alice-*'])
        assert sorted(expanded) == ['alice-svc', 'alice-svc-2']

    def test_exact_name_terminates_itself_only(self):
        records = [dict(r, user_hash='alice-hash') for r in _EXACT]
        with _caller(), self._env(records) as expanded:
            impl.down(service_names=['svc_a'])
        assert expanded == ['svc_a']

    def test_all_terminates_only_own_services(self):
        with _caller(), self._env(_RECORDS) as expanded:
            impl.down(all=True)
        assert sorted(expanded) == ['alice-svc', 'alice-svc-2']


class _Proceeded(Exception):
    """Raised by the first step after the name check, to stop `up` there."""


_ON_CONTROLLER = [
    {
        'name': 'my-svc',
        'user_hash': 'alice-hash',
        'pool': False
    },
    {
        'name': 'shared_pool',
        'user_hash': 'bob-hash',
        'pool': True
    },
]


class TestUp:
    """A name that exists is refused before the launch file-mounts this
    request's per-name files over the existing service's. Taken means the
    folded name: `-`, `_` and `.` are one character and case is ignored,
    since the controller directory and the cloud name each fold some of
    them."""

    @contextlib.contextmanager
    def _env(self, records=_ON_CONTROLLER, pool=False, consolidated=True):
        """``consolidated`` is a bool for both controllers, or a dict from
        ``pool`` to the setup of that controller."""
        if isinstance(consolidated, dict):
            setup = consolidated
        else:
            setup = {False: consolidated, True: consolidated}
        task = mock.MagicMock()
        task.service.pool = pool
        runner = mock.Mock()
        runner.get_service_status.side_effect = _status_of(records)
        serve_runner.register(runner)
        with mock.patch('sky.serve.server.impl.controller_utils.'
                        'get_controller_for_pool'), \
             mock.patch('sky.serve.server.impl.backend_utils.'
                        'is_controller_accessible', return_value=_handle()), \
             mock.patch('sky.serve.server.impl.serve_utils.'
                        'is_consolidation_mode',
                        side_effect=lambda pool: setup[pool]), \
             mock.patch('sky.serve.server.impl.serve_utils.'
                        'validate_service_task'), \
             mock.patch('sky.serve.server.impl.dag_utils.'
                        'convert_entrypoint_to_dag', side_effect=_Proceeded):
            yield task, runner

    def test_existing_name_is_refused(self):
        with _caller(), self._env() as (task, _):
            with pytest.raises(ValueError, match='already exists'):
                impl.up(task, service_name='my-svc')

    @pytest.mark.parametrize('name', ['my_svc', 'my.svc', 'My-Svc', 'MY_SVC'])
    def test_look_alike_name_is_refused(self, name):
        with _caller(), self._env() as (task, _):
            with pytest.raises(ValueError,
                               match=f"'{name}' is taken by service 'my-svc'"):
                impl.up(task, service_name=name)

    def test_look_alike_of_a_pool_is_refused_for_a_service(self):
        with _caller(), self._env() as (task, _):
            with pytest.raises(ValueError,
                               match="is taken by pool 'shared_pool'"):
                impl.up(task, service_name='shared-pool')

    def test_look_alike_of_a_service_is_refused_for_a_pool(self):
        with _caller(), self._env(pool=True) as (task, _):
            with pytest.raises(ValueError,
                               match="is taken by service 'my-svc'"):
                impl.up(task, service_name='my_svc', pool=True)

    def test_other_kind_is_not_consulted_outside_consolidation_mode(self):
        """A dedicated serve controller and a dedicated jobs controller are
        different clusters, so a pool there cannot share a directory with
        a service here, and the pools are not fetched."""
        with _caller(), self._env(consolidated=False) as (task, runner):
            with pytest.raises(_Proceeded):
                impl.up(task, service_name='shared-pool')
        assert runner.get_service_status.call_count == 1
        assert runner.get_service_status.call_args.kwargs['pool'] is False

    def test_own_kind_is_still_checked_outside_consolidation_mode(self):
        with _caller(), self._env(consolidated=False) as (task, _):
            with pytest.raises(ValueError,
                               match="'my_svc' is taken by service 'my-svc'"):
                impl.up(task, service_name='my_svc')

    _MIXED = [{False: True, True: False}, {False: False, True: True}]

    @pytest.mark.parametrize('setup', _MIXED)
    def test_pools_are_not_consulted_unless_both_run_consolidated(self, setup):
        """With one controller consolidated and the other dedicated, the
        two still run on different hosts and share no directory."""
        with _caller(), self._env(consolidated=setup) as (task, runner):
            with pytest.raises(_Proceeded):
                impl.up(task, service_name='shared-pool')
        assert runner.get_service_status.call_count == 1
        assert runner.get_service_status.call_args.kwargs['pool'] is False

    @pytest.mark.parametrize('setup', _MIXED)
    def test_services_are_not_consulted_unless_both_run_consolidated(
            self, setup):
        with _caller(), self._env(pool=True,
                                  consolidated=setup) as (task, runner):
            with pytest.raises(_Proceeded):
                impl.up(task, service_name='my_svc', pool=True)
        assert runner.get_service_status.call_count == 1
        assert runner.get_service_status.call_args.kwargs['pool'] is True

    def test_both_kinds_are_consulted_when_both_run_consolidated(self):
        with _caller(), self._env() as (task, runner):
            with pytest.raises(_Proceeded):
                impl.up(task, service_name='new-svc')
        assert runner.get_service_status.call_count == 2
        assert sorted(call.kwargs['pool']
                      for call in runner.get_service_status.call_args_list) == [
                          False, True
                      ]

    def test_free_name_proceeds(self):
        with _caller(), self._env() as (task, _):
            with pytest.raises(_Proceeded):
                impl.up(task, service_name='new-svc')

    _CONTROLLER_STATUS = ('sky.serve.server.impl.global_user_state.'
                          'get_status_from_cluster_name')

    _DIRS = 'sky.serve.server.impl._service_dirs_on_controller'

    def test_failed_lookup_on_a_controller_setting_up_does_not_block_up(self):
        """A controller still setting up (INIT) cannot answer; with no
        directory of the name on it, the launch that follows repairs it."""
        with _caller(), self._env() as (task, runner), \
             mock.patch(self._CONTROLLER_STATUS,
                        return_value=status_lib.ClusterStatus.INIT), \
             mock.patch(self._DIRS, return_value=['other_svc']):
            runner.get_service_status.side_effect = RuntimeError(
                'Failed to fetch services')
            with pytest.raises(_Proceeded):
                impl.up(task, service_name='my-svc')

    @pytest.mark.parametrize('name', ['my-svc', 'my_svc', 'My.Svc'])
    def test_controller_setting_up_still_refuses_a_taken_directory(self, name):
        """Any `up` takes the shared controller through INIT, so a failed
        lookup there falls back to the service directories, which are what
        the launch would write over."""
        with _caller(), self._env() as (task, runner), \
             mock.patch(self._CONTROLLER_STATUS,
                        return_value=status_lib.ClusterStatus.INIT), \
             mock.patch(self._DIRS, return_value=['my_svc']):
            runner.get_service_status.side_effect = RuntimeError(
                'Failed to fetch services')
            with pytest.raises(ValueError, match='is taken on the controller'):
                impl.up(task, service_name=name)

    def test_controller_setting_up_whose_directories_cannot_be_read(self):
        with _caller(), self._env() as (task, runner), \
             mock.patch(self._CONTROLLER_STATUS,
                        return_value=status_lib.ClusterStatus.INIT), \
             mock.patch(self._DIRS, side_effect=RuntimeError('ssh failed')):
            runner.get_service_status.side_effect = RuntimeError(
                'Failed to fetch services')
            with pytest.raises(RuntimeError, match='ssh failed'):
                impl.up(task, service_name='new-svc')

    @pytest.mark.parametrize('controller_status',
                             [status_lib.ClusterStatus.UP, None])
    def test_failed_lookup_otherwise_refuses_the_name(self, controller_status):
        """The lookup is the only guard against the file overwrite, so a
        failure on a controller that is up (a slow controller, skylet
        down), or in consolidation mode where there is no controller
        cluster, fails closed."""
        with _caller(), self._env() as (task, runner), \
             mock.patch(self._CONTROLLER_STATUS,
                        return_value=controller_status):
            runner.get_service_status.side_effect = RuntimeError(
                'deadline exceeded')
            with pytest.raises(RuntimeError,
                               match="name 'my-svc' is free.*deadline"):
                impl.up(task, service_name='my-svc')

    @pytest.mark.parametrize('returncode', [0, 255])
    def test_service_dirs_are_read_over_ssh(self, returncode):
        backend = mock.MagicMock(spec=backends.CloudVmRayBackend)
        backend.run_on_head.return_value = (returncode, 'my_svc\nother\n',
                                            'ssh: refused')
        with mock.patch(
                'sky.serve.server.impl.backend_utils.'
                'get_backend_from_handle',
                return_value=backend):
            if returncode:
                with pytest.raises(RuntimeError, match='ssh: refused'):
                    impl._service_dirs_on_controller(_handle())
            else:
                assert impl._service_dirs_on_controller(
                    _handle()) == ['my_svc', 'other']

    @staticmethod
    def _not_up(status, head_ip):
        handle = _handle()
        handle.head_ip = head_ip
        return exceptions.ClusterNotUpError('not up',
                                            cluster_status=status,
                                            handle=handle)

    @pytest.mark.parametrize('status,head_ip', [
        (None, None),
        (status_lib.ClusterStatus.INIT, None),
        (status_lib.ClusterStatus.STOPPED, '10.0.0.1'),
    ])
    def test_controller_with_no_running_service_proceeds(self, status, head_ip):
        """Nothing provisioned yet, or STOPPED: a stopped controller comes
        back with every service CONTROLLER_FAILED, which only `down` acts
        on."""
        with _caller(), self._env() as (task, _), \
             mock.patch('sky.serve.server.impl.backend_utils.'
                        'is_controller_accessible',
                        side_effect=self._not_up(status, head_ip)):
            with pytest.raises(_Proceeded):
                impl.up(task, service_name='my_svc')

    @pytest.mark.parametrize(
        'status', [status_lib.ClusterStatus.INIT, status_lib.ClusterStatus.UP])
    def test_unreachable_controller_refuses_the_name(self, status):
        """INIT, or a cached UP whose SSH check failed."""
        with _caller(), self._env() as (task, _), \
             mock.patch('sky.serve.server.impl.backend_utils.'
                        'is_controller_accessible',
                        side_effect=self._not_up(status, '10.0.0.1')):
            with pytest.raises(RuntimeError, match='not reachable'):
                impl.up(task, service_name='new-svc')

    def test_admin_proceeds_past_an_unreachable_controller(self):
        """The launch that follows is what repairs it."""
        with _caller(roles=('admin',)), self._env() as (task, _), \
             mock.patch('sky.serve.server.impl.backend_utils.'
                        'is_controller_accessible',
                        side_effect=self._not_up(status_lib.ClusterStatus.INIT,
                                                 '10.0.0.1')):
            with pytest.raises(_Proceeded):
                impl.up(task, service_name='new-svc')

    def test_admin_proceeds_past_a_failed_lookup(self):
        """An older controller fails the lookup on one broken service, and
        `up` is what upgrades it."""
        with _caller(roles=('admin',)), self._env() as (task, runner), \
             mock.patch(self._CONTROLLER_STATUS,
                        return_value=status_lib.ClusterStatus.UP):
            runner.get_service_status.side_effect = RuntimeError('broken')
            with pytest.raises(_Proceeded):
                impl.up(task, service_name='new-svc')


_YAML_WITH_SECRET = ('name: svc\n'
                     'secrets:\n'
                     '  TOKEN: hunter2\n'
                     'resources:\n'
                     '  accelerators: H100:1\n'
                     '  image_id: docker:private/image\n'
                     '  _docker_login_config:\n'
                     '    username: me\n'
                     '    password: hunter2\n'
                     '    server: registry.example\n'
                     '  any_of:\n'
                     '    - cloud: aws\n'
                     '      _cluster_config_overrides:\n'
                     '        token: hunter2\n'
                     '    - cloud: gcp\n'
                     'service:\n'
                     '  readiness_probe:\n'
                     '    headers:\n'
                     '      Authorization: Bearer hunter2\n'
                     'run: echo\n')


class TestStatusYaml:
    """`status` lists every user's services; of the YAML of one the caller
    did not create only the resources section is kept, since its secrets
    are substituted all over the rest. The resources are what a job on
    someone else's pool is run with."""

    @contextlib.contextmanager
    def _env(self, records):
        runner = mock.Mock()
        runner.get_service_status.side_effect = _status_of(records)
        serve_runner.register(runner)
        with mock.patch('sky.serve.server.impl.backend_utils.'
                        'check_network_connection'), \
             mock.patch('sky.serve.server.impl.controller_utils.'
                        'get_controller_for_pool'), \
             mock.patch('sky.serve.server.impl.backend_utils.'
                        'is_controller_accessible', return_value=_handle()), \
             mock.patch('sky.serve.server.impl._resolve_user_names'):
            yield

    @staticmethod
    def _with_yaml(record, yaml_content=_YAML_WITH_SECRET, **fields):
        return dict(record,
                    yaml_content=yaml_content,
                    pool_yaml=yaml_content,
                    load_balancer_port=None,
                    **fields)

    def _shown(self, yaml_content, pool=True):
        """The resources another user is shown for ``yaml_content``."""
        records = [self._with_yaml(_RECORDS[2], yaml_content)]
        with _caller(), self._env(records):
            (record,) = impl.status(pool=pool)
        assert record['yaml_content'] == record['pool_yaml']
        shown = record['pool_yaml']
        if shown is None:
            return None
        assert shown.startswith('# Created by another user')
        config = impl.yaml_utils.safe_load(shown)
        assert set(config) == {'resources'}
        return config['resources']

    @pytest.mark.parametrize('pool', [True, False])
    def test_other_users_yaml_keeps_the_resources_only(self, pool):
        records = [self._with_yaml(_RECORDS[2])]
        with _caller(), self._env(records):
            (record,) = impl.status(pool=pool)
        assert 'hunter2' not in str(record)
        assert self._shown(_YAML_WITH_SECRET, pool=pool) == {
            'accelerators': 'H100:1',
            'image_id': 'docker:private/image',
            'any_of': [{
                'cloud': 'aws'
            }, {
                'cloud': 'gcp'
            }],
        }

    def test_section_is_kept_as_written(self):
        """Not re-parsed: nothing is looked up in the catalog (`memory` from
        an instance type) and nothing is normalized."""
        resources = self._shown('resources:\n'
                                '  instance_type: p4d.24xlarge\n'
                                '  cpus: 2\n'
                                '  ordered:\n'
                                '    - infra: gcp\n'
                                '      _cluster_config_overrides:\n'
                                '        token: hunter2\n'
                                '    - infra: aws\n')
        assert resources == {
            'instance_type': 'p4d.24xlarge',
            'cpus': 2,
            'ordered': [{
                'infra': 'gcp'
            }, {
                'infra': 'aws'
            }],
        }

    def test_legacy_autostop_hook_and_hooks_are_dropped(self):
        resources = self._shown('resources:\n'
                                '  cpus: 2\n'
                                '  autostop:\n'
                                '    idle_minutes: 10\n'
                                '    hook: curl -H Bearer-hunter2 http://x\n'
                                '    hook_timeout: 5\n'
                                '  hooks:\n'
                                '    - run: curl -H Bearer-hunter2 http://x\n'
                                '      events: [stop]\n')
        assert resources == {'cpus': 2, 'autostop': {'idle_minutes': 10}}

    def test_keys_outside_the_schema_are_dropped(self):
        resources = self._shown('resources:\n'
                                '  cpus: 2\n'
                                '  made_up: hunter2\n'
                                '  _requires_fuse: true\n')
        assert resources == {'cpus': 2}

    def test_nested_keys_outside_the_schema_are_dropped(self):
        resources = self._shown('resources:\n'
                                '  job_recovery:\n'
                                '    strategy: FAILOVER\n'
                                '    webhook_token: hunter2\n'
                                '  volumes:\n'
                                '    - path: /data\n'
                                '      made_up: hunter2\n'
                                '  labels:\n'
                                '    team: ml\n'
                                '  any_of:\n'
                                '    - cpus: 2\n'
                                '      job_recovery:\n'
                                '        webhook_token: hunter2\n')
        assert resources == {
            'job_recovery': {
                'strategy': 'FAILOVER'
            },
            'volumes': [{
                'path': '/data'
            }],
            'labels': {
                'team': 'ml'
            },
            'any_of': [{
                'cpus': 2,
                'job_recovery': {}
            }],
        }

    def test_replica_handles_and_entrypoint_of_others_are_withheld(self):
        entrypoint = ('sky serve up --env TOKEN=hunter2 '
                      "--env 'MSG=hello hunter2' --env=X=hunter2 svc.yaml")
        records = [
            self._with_yaml(_RECORDS[2],
                            replica_info=[{
                                'name': 'r1',
                                'handle': 'HANDLE-hunter2',
                                'infra': 'aws'
                            }],
                            entrypoint=entrypoint)
        ]
        with _caller(), self._env(records):
            (record,) = impl.status(pool=False)
        assert 'hunter2' not in str(record)
        assert record['replica_info'] == [{
            'name': 'r1',
            'handle': None,
            'infra': 'aws'
        }]
        assert record['entrypoint'] is None

    def test_own_replica_handles_and_entrypoint_are_kept(self):
        records = [
            self._with_yaml(_RECORDS[0],
                            replica_info=[{
                                'handle': 'HANDLE'
                            }],
                            entrypoint='sky serve up --env T=v svc.yaml')
        ]
        with _caller(), self._env(records):
            (record,) = impl.status(pool=False)
        assert record['replica_info'] == [{'handle': 'HANDLE'}]
        assert record['entrypoint'] == 'sky serve up --env T=v svc.yaml'

    def test_other_users_resources_still_load_as_a_task(self):
        """The batch coordinator reads a pool's resources this way for a
        job on it, as the job's submitter."""
        records = [self._with_yaml(_RECORDS[2])]
        with _caller(), self._env(records):
            (record,) = impl.status(pool=True)
        task = task_lib.Task.from_yaml_str(record['pool_yaml'])
        assert {str(r.accelerators) for r in task.resources} == {"{'H100': 1}"}

    @pytest.mark.parametrize('yaml_content',
                             [None, '', 'run: echo\n', '- not a mapping\n'])
    def test_yaml_without_resources_is_withheld_outright(self, yaml_content):
        records = [self._with_yaml(_RECORDS[2], yaml_content)]
        with _caller(), self._env(records):
            (record,) = impl.status(pool=True)
        assert record['yaml_content'] is None
        assert record['pool_yaml'] is None

    def test_own_secrets_are_left_as_they_are(self):
        records = [self._with_yaml(_RECORDS[0])]
        with _caller(), self._env(records):
            (record,) = impl.status(pool=True)
        assert record['yaml_content'] == _YAML_WITH_SECRET

    def test_admin_sees_every_record_as_it_is(self):
        records = [self._with_yaml(_RECORDS[2])]
        with _caller(roles=('admin',)), self._env(records):
            (record,) = impl.status(pool=True)
        assert record['yaml_content'] == _YAML_WITH_SECRET


@contextlib.contextmanager
def _single_service_env(record):
    with mock.patch('sky.serve.server.impl.controller_utils.'
                    'get_controller_for_pool'), \
         mock.patch('sky.serve.server.impl.backend_utils.'
                    'is_controller_accessible', return_value=_handle()), \
         mock.patch('sky.serve.server.impl.backend_utils.'
                    'get_backend_from_handle',
                    return_value=_backend()) as get_backend, \
         mock.patch('sky.serve.server.impl._get_service_record',
                    return_value=record):
        yield get_backend.return_value


class TestOtherOperations:

    def test_update_other_users_service_is_denied(self):
        record = dict(_RECORDS[2], status='READY')
        with _caller(), _single_service_env(record):
            with pytest.raises(exceptions.PermissionDeniedError):
                impl.update(task=None,
                            service_name='bob-svc',
                            workers=2,
                            pool=True)

    def test_update_of_own_service_passes(self):
        """Stopped by the argument check right after the ownership check."""
        record = dict(_RECORDS[0], status='READY')
        with _caller(), _single_service_env(record):
            with pytest.raises(ValueError, match='without specifying'):
                impl.update(task=None,
                            service_name='alice-svc',
                            workers=None,
                            pool=True)

    def test_tail_logs_checks_before_anything_streams(self):
        with _caller(), _single_service_env(_RECORDS[2]) as backend:
            with pytest.raises(exceptions.PermissionDeniedError):
                impl.tail_logs('bob-svc', target='controller')
        backend.run_on_head.assert_not_called()

    def test_tail_logs_of_own_service_streams(self):
        with _caller(), _single_service_env(_RECORDS[0]) as backend:
            impl.tail_logs('alice-svc', target='controller')
        backend.run_on_head.assert_called_once()

    def test_sync_down_logs_checks_before_anything_runs(self):
        with _caller(), _single_service_env(_RECORDS[2]) as backend:
            with pytest.raises(exceptions.PermissionDeniedError):
                impl.sync_down_logs('bob-svc',
                                    local_dir='/tmp/x',
                                    targets='controller')
        backend.run_on_head.assert_not_called()

    def test_sync_down_logs_of_own_service_runs(self):
        with _caller(), _single_service_env(_RECORDS[0]) as backend:
            backend.run_on_head.side_effect = _Proceeded
            with pytest.raises(_Proceeded):
                impl.sync_down_logs('alice-svc',
                                    local_dir='/tmp/x',
                                    targets='controller')

    def test_terminate_replica_of_own_service_passes(self):
        with _caller(), _single_service_env(_RECORDS[0]), \
             mock.patch('sky.serve.server.core.serve_rpc_utils.RpcRunner.'
                        'terminate_replica', return_value='ok') as terminate:
            serve_core.terminate_replica('alice-svc', replica_id=1, purge=False)
        terminate.assert_called_once()

    def test_terminate_replica_other_users_service_is_denied(self):
        with _caller(), _single_service_env(_RECORDS[2]), \
             mock.patch('sky.serve.server.core.serve_rpc_utils.RpcRunner.'
                        'terminate_replica') as terminate:
            with pytest.raises(exceptions.PermissionDeniedError):
                serve_core.terminate_replica('bob-svc',
                                             replica_id=1,
                                             purge=False)
        terminate.assert_not_called()

    def test_missing_service_passes_to_the_usual_not_found_path(self):
        with _caller(), _single_service_env(None):
            impl.check_service_owned_by_caller('ghost', False, _handle())


class TestAdminLookup:

    def test_off_server_every_caller_is_admin(self, monkeypatch):
        monkeypatch.delenv(constants.ENV_VAR_IS_SKYPILOT_SERVER)
        with mock.patch('sky.users.permission.permission_service.'
                        'get_user_roles') as get_roles:
            assert impl._caller_is_admin()
        get_roles.assert_not_called()

    def test_roles_are_only_looked_up_when_something_is_denied(self):
        with mock.patch('sky.serve.server.impl.common_utils.get_current_user',
                        return_value=_ALICE), \
             mock.patch('sky.users.permission.permission_service.'
                        'get_user_roles') as get_roles:
            impl._check_service_ownership([_RECORDS[0]], pool=False)
        get_roles.assert_not_called()
