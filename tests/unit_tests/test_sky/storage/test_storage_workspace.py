"""Storage records carry a creator and a workspace, and the workspace is
the boundary for listing and deleting them, as it is for clusters and
volumes."""
# pylint: disable=protected-access
import contextlib
import os
import pathlib
import pickle
from unittest import mock

import pytest
import requests
import sqlalchemy

import sky
from sky import backends
from sky import core
from sky import exceptions
from sky import global_user_state
from sky import models
from sky.client.cli import table_utils
from sky.data import storage as storage_lib
from sky.jobs import recovery_strategy
from sky.jobs.server import core as jobs_core
from sky.schemas.api import responses
from sky.serve import runner as serve_runner
from sky.serve import serve_state
from sky.serve import serve_utils
from sky.serve import service
from sky.serve.server import impl as serve_impl
from sky.skylet import constants
from sky.utils import resource_checker
from sky.utils import status_lib
from sky.utils import status_lib as status_lib_mod
from sky.workspaces import constants as workspace_constants

_ACTIVE_WORKSPACE = 'sky.global_user_state.skypilot_config.get_active_workspace'
_STATE_USER = 'sky.global_user_state.common_utils.get_current_user'
_CORE_USER = 'sky.core.common_utils.get_current_user'
_READABLE = 'sky.core.workspaces_core.get_readable_workspace_names'
_CHECK_WRITE = 'sky.core.workspaces_core.check_workspace_permission'

_ALICE = models.User(id='alice-hash', name='alice')
_BOB = models.User(id='bob-hash', name='bob')


@pytest.fixture()
def isolated_database(tmp_path):
    """A real database: these tests are about the query, not about mocks."""
    global_user_state._db_manager._engine = sqlalchemy.create_engine(
        f'sqlite:///{tmp_path / "state.db"}')
    global_user_state.create_table(global_user_state._db_manager.get_engine())
    yield
    global_user_state._db_manager._engine = None


def _handle(name: str) -> storage_lib.Storage.StorageMetadata:
    return storage_lib.Storage.StorageMetadata(storage_name=name,
                                               source=f's3://{name}')


def _add(name: str, user: models.User, workspace: str) -> None:
    """Adds a storage created by ``user`` while ``workspace`` was active."""
    with mock.patch(_ACTIVE_WORKSPACE, return_value=workspace), \
         mock.patch(_STATE_USER, return_value=user):
        global_user_state.add_or_update_storage(name, _handle(name),
                                                status_lib.StorageStatus.READY)


def _record(name: str):
    record = global_user_state.get_storage_by_name(name)
    assert record is not None
    return record


class TestRecording:

    def test_records_creator_and_workspace(self, isolated_database):
        _add('st', _ALICE, 'ws-a')

        record = _record('st')

        assert record['user_hash'] == 'alice-hash'
        assert record['workspace'] == 'ws-a'

    def test_re_registration_keeps_the_original_owner(self, isolated_database):
        _add('st', _ALICE, 'ws-a')

        _add('st', _BOB, 'ws-b')

        record = _record('st')
        assert record['user_hash'] == 'alice-hash'
        assert record['workspace'] == 'ws-a'

    def test_row_without_creator_takes_the_next_writer(self, isolated_database):
        """Rows from before the creator was recorded get one on their next
        update rather than staying ownerless forever."""
        _add('st', _ALICE, 'ws-a')
        engine = global_user_state._db_manager.get_engine()
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text('UPDATE storage SET user_hash = NULL'))
        assert _record('st')['user_hash'] is None

        _add('st', _BOB, 'ws-a')

        assert _record('st')['user_hash'] == 'bob-hash'

    def test_missing_storage_has_no_record(self, isolated_database):
        assert global_user_state.get_storage_by_name('ghost') is None


class TestListing:

    def _seed(self):
        _add('st-a', _ALICE, 'ws-a')
        _add('st-b', _BOB, 'ws-b')

    def test_filters_to_the_given_workspaces(self, isolated_database):
        self._seed()

        records = global_user_state.get_storage(workspaces_filter={'ws-a'})

        assert [r['name'] for r in records] == ['st-a']

    def test_no_filter_returns_every_workspace(self, isolated_database):
        self._seed()

        records = global_user_state.get_storage()

        assert sorted(r['name'] for r in records) == ['st-a', 'st-b']

    def test_empty_filter_returns_nothing(self, isolated_database):
        """An empty set means no accessible workspaces, not no filter."""
        self._seed()

        assert global_user_state.get_storage(workspaces_filter=set()) == []

    def test_storage_ls_shows_only_readable_workspaces(self, isolated_database):
        self._seed()
        with mock.patch(_READABLE, return_value={'ws-a'}) as readable, \
             mock.patch('sky.core.global_user_state.get_users',
                        return_value={'alice-hash': _ALICE}), \
             mock.patch('sky.skypilot_config._loaded_workspaces',
                        return_value={'ws-a': {
                            'name': 'Alpha'
                        }}):
            records = core.storage_ls()

        readable.assert_called_once_with()
        assert [r.name for r in records] == ['st-a']
        assert records[0].user_hash == 'alice-hash'
        assert records[0].user_name == 'alice'
        assert records[0].workspace == 'ws-a'
        assert records[0].workspace_name == 'Alpha'

    def test_admin_sees_storage_of_a_workspace_removed_from_the_config(
            self, isolated_database):
        """An admin is unrestricted (None), so storage whose workspace was
        removed from the config by hand is still listed and deletable."""
        self._seed()
        with mock.patch(_READABLE, return_value=None), \
             mock.patch('sky.core.global_user_state.get_users',
                        return_value={}):
            records = core.storage_ls()
        assert sorted(r.name for r in records) == ['st-a', 'st-b']
        with mock.patch(_READABLE, return_value=None), \
             mock.patch(_CORE_USER, return_value=_ALICE), \
             mock.patch(_CHECK_WRITE), \
             mock.patch('sky.core.data.Storage.from_handle') as from_handle:
            core.storage_delete('st-b')
        from_handle.return_value.delete.assert_called_once_with()


class TestDelete:

    def test_outside_readable_workspaces_reads_as_missing(
            self, isolated_database):
        _add('st-b', _BOB, 'ws-b')
        with mock.patch(_READABLE, return_value={'ws-a'}), \
             mock.patch(_CORE_USER, return_value=_ALICE), \
             mock.patch('sky.core.data.Storage.from_handle') as from_handle:
            with pytest.raises(ValueError, match='not found'):
                core.storage_delete('st-b')
        from_handle.assert_not_called()

    def test_missing_storage_reads_the_same(self, isolated_database):
        with mock.patch(_READABLE, return_value={'ws-a'}), \
             mock.patch(_CORE_USER, return_value=_ALICE):
            with pytest.raises(ValueError, match='not found'):
                core.storage_delete('ghost')

    def test_read_only_workspace_cannot_delete(self, isolated_database):
        _add('st-a', _BOB, 'ws-a')
        with mock.patch(_READABLE, return_value={'ws-a'}), \
             mock.patch(_CORE_USER, return_value=_ALICE), \
             mock.patch(_CHECK_WRITE,
                        side_effect=exceptions.PermissionDeniedError('no')), \
             mock.patch('sky.core.data.Storage.from_handle') as from_handle:
            with pytest.raises(exceptions.PermissionDeniedError):
                core.storage_delete('st-a')
        from_handle.assert_not_called()

    def test_writable_workspace_deletes(self, isolated_database):
        _add('st-a', _BOB, 'ws-a')
        with mock.patch(_READABLE, return_value={'ws-a'}), \
             mock.patch(_CORE_USER, return_value=_ALICE), \
             mock.patch(_CHECK_WRITE) as check_write, \
             mock.patch('sky.core.data.Storage.from_handle') as from_handle:
            core.storage_delete('st-a')

        check_write.assert_called_once_with(_ALICE, 'ws-a')
        from_handle.return_value.delete.assert_called_once_with()


class _Rebuilt(Exception):
    """Raised in place of rebuilding the stores: the lookup let the handle
    through."""


# alice writes ws-a, may read ws-ro, and cannot see ws-b.
_ACCESS_SETS = ({'ws-a', 'ws-ro'}, {'ws-a'})


class TestReuseByName:
    """A task naming an existing storage syncs into its bucket and may
    delete it, so reuse by name needs write access to the workspace, as
    deletion does. The tests go through ``Storage`` itself, so a call site
    that stops using the checked lookup fails them."""

    _ACCESS = 'sky.workspaces.core.get_workspace_access_sets'
    _IN_REQUEST = 'sky.data.storage.common_utils.is_in_request_context'
    _REBUILD = 'sky.data.storage.Storage._add_store_from_metadata'

    @pytest.fixture(autouse=True)
    def _not_an_admin(self):
        """Outside a server, the caller reads every workspace, even one
        removed from the config, as an admin does."""
        with mock.patch('sky.workspaces.core.get_readable_workspace_names',
                        return_value=_ACCESS_SETS[0]):
            yield

    def test_unreadable_workspaces_storage_name_reads_as_taken(
            self, isolated_database):
        """Whether the task meant to reuse or to create it: the workspace
        is not named."""
        _add('st-b', _BOB, 'ws-b')
        with mock.patch(self._IN_REQUEST, return_value=True), \
             mock.patch(self._ACCESS, return_value=_ACCESS_SETS), \
             mock.patch(self._REBUILD, side_effect=_Rebuilt) as rebuild:
            with pytest.raises(exceptions.StorageWorkspaceError,
                               match="'st-b' is taken by a storage in a "
                               'workspace you cannot access') as info:
                storage_lib.Storage(name='st-b').construct()
        rebuild.assert_not_called()
        assert 'ws-b' not in str(info.value)

    def test_read_only_workspaces_storage_is_refused(self, isolated_database):
        _add('st-ro', _BOB, 'ws-ro')
        with mock.patch(self._IN_REQUEST, return_value=True), \
             mock.patch(self._ACCESS, return_value=_ACCESS_SETS), \
             mock.patch(self._REBUILD, side_effect=_Rebuilt) as rebuild:
            with pytest.raises(exceptions.StorageReadOnlyError,
                               match='read-only for you') as info:
                storage_lib.Storage(name='st-ro').construct()
        rebuild.assert_not_called()
        # Still a denial for callers that handle those.
        assert isinstance(info.value, exceptions.PermissionDeniedError)

    def test_copy_mode_by_name_is_checked_too(self, isolated_database):
        """The spec validation looks the name up for COPY mode without a
        source, and that lookup is the checked one: an unscoped one would
        return the handle and let the spec pass."""
        _add('st-b', _BOB, 'ws-b')
        storage = storage_lib.Storage(name='st-b',
                                      mode=storage_lib.StorageMode.COPY)
        with mock.patch(self._IN_REQUEST, return_value=True), \
             mock.patch(self._ACCESS, return_value=_ACCESS_SETS):
            with pytest.raises(exceptions.StorageWorkspaceError, match='st-b'):
                storage._validate_storage_spec('st-b')

    def test_writable_workspaces_storage_is_rebuilt(self, isolated_database):
        _add('st-a', _BOB, 'ws-a')
        with mock.patch(self._IN_REQUEST, return_value=True), \
             mock.patch(self._ACCESS, return_value=_ACCESS_SETS) as access, \
             mock.patch(self._REBUILD, side_effect=_Rebuilt) as rebuild:
            with pytest.raises(_Rebuilt):
                storage_lib.Storage(name='st-a').construct()
        rebuild.assert_called_once_with(_handle('st-a').sky_stores)
        access.assert_called_once_with()

    _ACTIVE = 'sky.data.storage.skypilot_config.get_active_workspace'

    def test_check_before_construction_holds_to_the_active_workspace(
            self, isolated_database):
        """What `jobs launch` and `serve up` run on every named mount of a
        task. A non-persistent mount is constructed by the controller, for
        its teardown, in the workspace it runs for (the active one) only; a
        persistent one is constructed through a request as the owner, so
        write access is enough for it."""
        _add('st-b', _BOB, 'ws-b')
        _add('st-ro', _BOB, 'ws-ro')
        _add('st-a', _BOB, 'ws-a')
        with mock.patch(self._IN_REQUEST, return_value=True), \
             mock.patch(self._ACCESS, return_value=_ACCESS_SETS), \
             mock.patch(self._ACTIVE, return_value='ws-a'):
            with pytest.raises(exceptions.StorageWorkspaceError,
                               match="'st-b' is taken"):
                storage_lib.Storage(name='st-b',
                                    persistent=False).check_accessible()
            with pytest.raises(exceptions.PermissionDeniedError):
                storage_lib.Storage(name='st-ro',
                                    persistent=False).check_accessible()
            storage_lib.Storage(name='st-a',
                                persistent=False).check_accessible()
            storage_lib.Storage(name='brand-new',
                                persistent=False).check_accessible()
        with mock.patch(self._IN_REQUEST, return_value=True), \
             mock.patch(self._ACCESS, return_value=_ACCESS_SETS), \
             mock.patch(self._ACTIVE, return_value='default'):
            with pytest.raises(exceptions.StorageWorkspaceError,
                               match="'st-a' is in workspace 'ws-a', not in "
                               "the active workspace 'default'"):
                storage_lib.Storage(name='st-a',
                                    persistent=False).check_accessible()
            storage_lib.Storage(name='st-a', persistent=True).check_accessible()
            with pytest.raises(exceptions.StorageWorkspaceError,
                               match="'st-b' is taken"):
                storage_lib.Storage(name='st-b',
                                    persistent=True).check_accessible()

    def test_construction_itself_is_not_held_to_the_active_workspace(
            self, isolated_database):
        """A cluster launch constructs and tears down in the request, where
        the caller's write access is the boundary."""
        _add('st-a', _BOB, 'ws-a')
        with mock.patch(self._IN_REQUEST, return_value=True), \
             mock.patch(self._ACCESS, return_value=_ACCESS_SETS), \
             mock.patch(self._ACTIVE, return_value='default'), \
             mock.patch(self._REBUILD, side_effect=_Rebuilt):
            with pytest.raises(_Rebuilt):
                storage_lib.Storage(name='st-a').construct()

    def test_check_names_the_bucket_of_a_bucket_url(self, isolated_database):
        """`construct` sets the name from a bucket URL; the check reads it
        the same way, and leaves the spec for `construct` to set."""
        _add('st-b', _BOB, 'ws-b')
        _add('st-a', _BOB, 'ws-a')
        with mock.patch(self._IN_REQUEST, return_value=True), \
             mock.patch(self._ACCESS, return_value=_ACCESS_SETS), \
             mock.patch(self._ACTIVE, return_value='ws-a'):
            with pytest.raises(exceptions.StorageWorkspaceError,
                               match="'st-b' is taken"):
                storage_lib.Storage(source='s3://st-b',
                                    persistent=False).check_accessible()
            storage = storage_lib.Storage(source='s3://st-a', persistent=False)
            storage.check_accessible()
            assert storage.name is None
            with mock.patch(self._REBUILD, side_effect=_Rebuilt):
                with pytest.raises(_Rebuilt):
                    storage.construct()
            assert storage.name == 'st-a'

    def test_missing_storage_is_none(self, isolated_database):
        with mock.patch(self._IN_REQUEST, return_value=True):
            assert storage_lib._get_accessible_handle('ghost') is None
            assert storage_lib._get_accessible_handle(None) is None


class TestOnTheController:
    """Outside a request the code runs on a controller, whose active
    workspace is the workspace of the job or service it runs. It may
    reuse and tear down that workspace's storage, and no other's: in
    consolidation mode it shares the server's database, and a job that
    was refused at launch still gets its storage torn down."""

    _IN_REQUEST = 'sky.data.storage.common_utils.is_in_request_context'
    _ACTIVE = 'sky.data.storage.skypilot_config.get_active_workspace'
    _ACCESS = 'sky.workspaces.core.get_workspace_access_sets'
    _REBUILD = 'sky.data.storage.Storage._add_store_from_metadata'

    def test_own_workspaces_storage_is_rebuilt_unchecked(
            self, isolated_database, monkeypatch):
        """In consolidation mode the controller runs with the server's
        environment, so the server environment variable alone would not
        tell the two apart."""
        monkeypatch.setenv(constants.ENV_VAR_IS_SKYPILOT_SERVER, '1')
        _add('st-b', _BOB, 'ws-b')
        with mock.patch(self._IN_REQUEST, return_value=False), \
             mock.patch(self._ACTIVE, return_value='ws-b'), \
             mock.patch(self._ACCESS) as access, \
             mock.patch(self._REBUILD, side_effect=_Rebuilt):
            with pytest.raises(_Rebuilt):
                storage_lib.Storage(name='st-b').construct()
        access.assert_not_called()

    def test_another_workspaces_storage_reads_as_missing(
            self, isolated_database):
        _add('st-b', _BOB, 'ws-b')
        with mock.patch(self._IN_REQUEST, return_value=False), \
             mock.patch(self._ACTIVE, return_value='ws-a'), \
             mock.patch(self._REBUILD, side_effect=_Rebuilt) as rebuild:
            with pytest.raises(exceptions.StorageSpecError,
                               match="'st-b' not found in workspace 'ws-a'"):
                storage_lib.Storage(name='st-b', persistent=False).construct()
        rebuild.assert_not_called()

    def test_teardown_of_a_refused_storage_leaves_the_record(
            self, isolated_database):
        """The ephemeral-storage teardown deletes every non-persistent
        mount whether or not it was constructed; one that was refused has
        no record of its own to remove."""
        _add('st-b', _BOB, 'ws-b')
        storage = storage_lib.Storage(name='st-b', persistent=False)
        with mock.patch(self._IN_REQUEST, return_value=False), \
             mock.patch(self._ACTIVE, return_value='ws-a'):
            with pytest.raises(exceptions.StorageSpecError):
                storage.construct()
        storage.delete()
        assert _record('st-b')['name'] == 'st-b'

    def test_teardown_of_a_refused_storage_with_a_store_type_is_a_no_op(
            self, isolated_database):
        """``store: s3`` books the store type before construction; refused,
        the storage has the type and no store, and the teardown's retries
        must not fail the job over it."""
        _add('st-b', _BOB, 'ws-b')
        storage = storage_lib.Storage(name='st-b',
                                      persistent=False,
                                      stores=[storage_lib.StoreType.S3])
        with mock.patch(self._IN_REQUEST, return_value=False), \
             mock.patch(self._ACTIVE, return_value='ws-a'):
            with pytest.raises(exceptions.StorageWorkspaceError):
                storage.construct()
        storage.delete()
        storage.delete()
        assert _record('st-b')['name'] == 'st-b'

    def test_teardown_of_a_half_constructed_storage_is_a_no_op(
            self, isolated_database):
        """`name: x, store: gcs` on a storage recorded for S3 rebuilds the
        S3 store and then refuses the new store type; the teardown must
        not delete the bucket it rebuilt on the way."""
        _add('st-b', _BOB, 'ws-b')
        storage = storage_lib.Storage(name='st-b',
                                      persistent=False,
                                      stores=[storage_lib.StoreType.GCS])
        rebuilt = mock.MagicMock(is_sky_managed=True)
        storage.handle = _record('st-b')['handle']
        storage._record_loaded = True
        storage.stores[storage_lib.StoreType.S3] = rebuilt
        storage.delete()
        storage.delete(store_type=storage_lib.StoreType.S3)
        rebuilt.delete.assert_not_called()
        assert _record('st-b')['name'] == 'st-b'

    @pytest.mark.parametrize('error,removed', [
        (exceptions.StorageExternalDeletionError('gone'), True),
        (exceptions.StorageBucketGetError('403'), False),
    ])
    def test_booked_store_is_dropped_only_when_its_bucket_is_gone(
            self, isolated_database, error, removed):
        """`store: s3` books S3. If the rebuild finds the bucket deleted
        outside SkyPilot, the teardown removes the stale record; if the
        lookup failed otherwise (e.g. a 403), the bucket may exist, so the
        record stays."""
        _add('st-b', _BOB, 'ws-b')
        storage = storage_lib.Storage(name='st-b',
                                      persistent=False,
                                      stores=[storage_lib.StoreType.S3])
        handle = _record('st-b')['handle']
        handle.sky_stores = {storage_lib.StoreType.S3: mock.MagicMock()}
        storage.handle = handle
        storage._record_loaded = True
        with mock.patch.object(storage_lib.S3Store,
                               'from_metadata',
                               side_effect=error):
            try:
                storage._add_store_from_metadata(handle.sky_stores)
            except exceptions.StorageBucketGetError:
                pass
        storage.delete()
        assert (global_user_state.get_storage_by_name('st-b') is
                None) == removed

    def test_storage_delete_still_removes_a_record_without_stores(
            self, isolated_database):
        """`sky storage delete` builds the storage from its record."""
        _add('st-b', _BOB, 'ws-b')
        storage_lib.Storage.from_handle(_record('st-b')['handle']).delete()
        assert global_user_state.get_storage_by_name('st-b') is None

    def test_stale_record_is_removed_when_its_bucket_is_gone(
            self, isolated_database):
        """A record the controller loaded whose bucket was deleted outside
        SkyPilot leaves the storage without stores, and the teardown still
        removes the record."""
        _add('st-b', _BOB, 'ws-b')
        storage = storage_lib.Storage(name='st-b',
                                      persistent=False,
                                      sync_on_reconstruction=False)
        with mock.patch(self._IN_REQUEST, return_value=False), \
             mock.patch(self._ACTIVE, return_value='ws-b'), \
             mock.patch(self._REBUILD):
            storage.construct()
        assert not storage.stores
        storage.delete()
        assert global_user_state.get_storage_by_name('st-b') is None


class _Proceeded(Exception):
    """Raised by the first step after the storage check, to stop there."""


class TestJobsLaunch:
    """`jobs launch` checks every storage the tasks name, whichever branch
    of its upload step runs: without a jobs bucket the tasks go to the
    controller as they are, and the controller constructs their storage
    outside any request."""

    _IN_REQUEST = 'sky.data.storage.common_utils.is_in_request_context'
    _ACCESS = 'sky.workspaces.core.get_workspace_access_sets'
    _ACTIVE = 'sky.data.storage.skypilot_config.get_active_workspace'
    _NEXT_STEP = ('sky.jobs.server.core.clouds.Kubernetes.'
                  'check_request_keeps_server_none')

    @staticmethod
    def _launch(storage_name=None, source=None, persistent=False):
        task = sky.Task(run='echo')
        task.set_storage_mounts({
            '/data': storage_lib.Storage(name=storage_name,
                                         source=source,
                                         persistent=persistent)
        })
        return jobs_core._launch(task,
                                 name=None,
                                 pool=None,
                                 num_jobs=None,
                                 stream_logs=False,
                                 file_mounts_blob_id=None,
                                 parent_job_id=None,
                                 parent_task_id=None,
                                 root_job_id=None,
                                 depends_on=[])

    @contextlib.contextmanager
    def _env(self, active='ws-a'):
        with mock.patch(self._IN_REQUEST, return_value=True), \
             mock.patch(self._ACCESS, return_value=_ACCESS_SETS), \
             mock.patch(self._ACTIVE, return_value=active), \
             mock.patch(self._NEXT_STEP, side_effect=_Proceeded) as next_step:
            yield next_step

    def test_another_workspaces_storage_is_refused(self, isolated_database):
        _add('st-b', _BOB, 'ws-b')
        with self._env() as next_step:
            with pytest.raises(exceptions.StorageWorkspaceError,
                               match="'st-b' is taken"):
                self._launch('st-b')
        next_step.assert_not_called()

    def test_read_only_workspaces_storage_is_refused(self, isolated_database):
        _add('st-ro', _BOB, 'ws-ro')
        with self._env():
            with pytest.raises(exceptions.PermissionDeniedError):
                self._launch('st-ro')

    def test_writable_workspace_other_than_the_active_one_is_refused(
            self, isolated_database):
        """The job's controller runs for the active workspace and would
        not tear the storage down."""
        _add('st-a', _BOB, 'ws-a')
        with self._env(active='default') as next_step:
            with pytest.raises(exceptions.StorageWorkspaceError,
                               match="not in the active workspace 'default'"):
                self._launch('st-a')
        next_step.assert_not_called()

    def test_persistent_mount_of_a_writable_workspace_proceeds(
            self, isolated_database):
        """A shared dataset mounted from another workspace: the controller
        constructs it through a request as the owner, and never tears it
        down."""
        _add('st-a', _BOB, 'ws-a')
        with self._env(active='default'):
            with pytest.raises(_Proceeded):
                self._launch('st-a', persistent=True)

    def test_bucket_url_source_is_checked_by_its_bucket(self,
                                                        isolated_database):
        _add('st-b', _BOB, 'ws-b')
        with self._env() as next_step:
            with pytest.raises(exceptions.StorageWorkspaceError,
                               match="'st-b' is taken"):
                self._launch(source='s3://st-b')
        next_step.assert_not_called()

    def test_own_or_new_storage_proceeds(self, isolated_database):
        _add('st-a', _BOB, 'ws-a')
        with self._env():
            for name in ('st-a', 'brand-new'):
                with pytest.raises(_Proceeded):
                    self._launch(name)


class TestServiceWorkspace:
    """A service's status says which workspace its controller runs for:
    the active workspace of the config it was launched with."""

    def test_reads_the_config_the_service_was_launched_with(
            self, tmp_path, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path))
        path = pathlib.Path(
            os.path.expanduser(
                serve_utils.generate_remote_config_yaml_file_name('svc')))
        assert serve_utils.get_service_workspace('svc') is None
        path.parent.mkdir(parents=True)
        path.write_text('workspaces: {}\n')
        assert serve_utils.get_service_workspace('svc') == 'default'
        path.write_text('active_workspace: ws-a\n')
        assert serve_utils.get_service_workspace('svc') == 'ws-a'
        path.write_text('- not a mapping\n')
        assert serve_utils.get_service_workspace('svc') is None

    def test_status_record_reports_it(self, tmp_path, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path))
        path = pathlib.Path(
            os.path.expanduser(
                serve_utils.generate_remote_config_yaml_file_name('svc')))
        path.parent.mkdir(parents=True)
        path.write_text('active_workspace: ws-a\n')
        engine = sqlalchemy.create_engine(f'sqlite:///{tmp_path / "serve.db"}')
        serve_state._db_manager._engine = engine
        try:
            serve_state.create_table(engine)
            with engine.begin() as conn:
                conn.execute(serve_state.services_table.insert(), [{
                    'name': 'svc',
                    'status': 'READY',
                    'controller_port': 1
                }])
                conn.execute(serve_state.version_specs_table.insert(), [{
                    'service_name': 'svc',
                    'version': 1,
                    'spec': pickle.dumps(None),
                    'yaml_content': 'run: echo\n',
                }])
            with mock.patch(
                    'sky.serve.serve_utils.'
                    '_get_to_controller_with_retry',
                    side_effect=requests.exceptions.RequestException):
                record = serve_utils._get_service_status(
                    'svc', pool=False, with_replica_info=False)
        finally:
            serve_state._db_manager._engine = None
        assert record is not None
        assert record['workspace'] == 'ws-a'


class TestServeUpdate:
    """An update is made from the workspace the service runs in. Its
    controller reuses and tears down the storage of that workspace only,
    and an update records its storage, the file mounts bucket of the new
    version included, in the active workspace."""

    _ACTIVE = 'sky.serve.server.impl.skypilot_config.get_active_workspace'

    @contextlib.contextmanager
    def _env(self, record, active):
        task = mock.MagicMock()
        task.validate.side_effect = _Proceeded
        with mock.patch('sky.serve.server.impl.controller_utils.'
                        'get_controller_for_pool'), \
             mock.patch('sky.serve.server.impl.backend_utils.'
                        'is_controller_accessible',
                        return_value=mock.MagicMock(
                            spec=backends.CloudVmRayResourceHandle)), \
             mock.patch('sky.serve.server.impl.backend_utils.'
                        'get_backend_from_handle',
                        return_value=mock.MagicMock(
                            spec=backends.CloudVmRayBackend)), \
             mock.patch('sky.serve.server.impl._get_service_record',
                        return_value=record), \
             mock.patch(self._ACTIVE, return_value=active):
            yield task

    def test_update_from_another_workspace_is_refused(self):
        with self._env({'workspace': 'ws-a'}, active='default') as task:
            with pytest.raises(ValueError,
                               match="'svc' runs in workspace 'ws-a' and the "
                               "active workspace is 'default'"):
                serve_impl.update(task, 'svc')
        task.validate.assert_not_called()

    def test_update_from_the_services_workspace_proceeds(self):
        with self._env({'workspace': 'ws-a'}, active='ws-a') as task:
            with pytest.raises(_Proceeded):
                serve_impl.update(task, 'svc')

    def test_controller_that_reports_no_workspace_is_not_held(self):
        """An older controller, or one whose config a pod restart lost."""
        for record in ({}, {'workspace': None}):
            with self._env(record, active='default') as task:
                with pytest.raises(_Proceeded):
                    serve_impl.update(task, 'svc')

    def test_workspace_recorded_by_name_is_resolved(self):
        with self._env({'workspace': 'Alpha'}, active='ws-a') as task, \
             mock.patch('sky.skypilot_config._loaded_workspaces',
                        return_value={'ws-a': {
                            'name': 'Alpha'
                        }}):
            with pytest.raises(_Proceeded):
                serve_impl.update(task, 'svc')

    def test_update_without_a_task_is_not_held(self):
        """`jobs pool apply --workers N` makes no storage. Stopped by the
        missing service section of the loaded YAML, right after."""
        record = {'workspace': 'ws-a', 'yaml_content': 'run: echo\n'}
        with self._env(record, active='default'):
            with pytest.raises(RuntimeError, match='No service configuration'):
                serve_impl.update(None, 'svc', workers=2, pool=True)


class TestServeStorage:
    """`serve up` and `serve update` check the task's storage the way
    `jobs launch` does, before any bucket is made for the task: the
    service's controller runs for the active workspace as well."""

    _IN_REQUEST = 'sky.data.storage.common_utils.is_in_request_context'
    _ACCESS = 'sky.workspaces.core.get_workspace_access_sets'
    _ACTIVE = 'sky.data.storage.skypilot_config.get_active_workspace'
    _ENABLED_CLOUDS = ('sky.serve.server.impl.storage_lib.'
                       'get_cached_enabled_storage_cloud_names_or_refresh')
    _SYNC_UP = ('sky.serve.server.impl.controller_utils.'
                'maybe_translate_local_file_mounts_and_sync_up')

    @contextlib.contextmanager
    def _env(self, name, active):
        """A task with a non-persistent mount of the storage ``name``,
        submitted with ``active`` as the active workspace, with the steps
        before the storage check stubbed."""
        task = mock.MagicMock()
        task.service.pool = False
        task.service.tls_credential = None
        task.storage_mounts = {
            '/data': storage_lib.Storage(name=name, persistent=False)
        }
        dag = mock.MagicMock()
        dag.tasks = [task]
        record = {
            'workspace': active,
            'status': serve_state.ServiceStatus.READY,
            'load_balancing_policy': task.service.load_balancing_policy,
        }
        with mock.patch(self._IN_REQUEST, return_value=True), \
             mock.patch(self._ACCESS, return_value=_ACCESS_SETS), \
             mock.patch(self._ACTIVE, return_value=active), \
             mock.patch('sky.serve.server.impl.dag_utils.'
                        'convert_entrypoint_to_dag', return_value=dag), \
             mock.patch('sky.serve.server.impl.admin_policy_utils.apply',
                        return_value=(dag, {})), \
             mock.patch('sky.serve.server.impl.serve_utils.'
                        'validate_service_task'), \
             mock.patch('sky.serve.server.impl.clouds.Kubernetes.'
                        'check_request_keeps_server_none'), \
             mock.patch('sky.serve.server.impl.clouds.Kubernetes.'
                        'check_task_keeps_server_none'), \
             mock.patch('sky.serve.server.impl.controller_utils.'
                        'get_controller_for_pool'), \
             mock.patch('sky.serve.server.impl.backend_utils.'
                        'is_controller_accessible',
                        return_value=mock.MagicMock(
                            spec=backends.CloudVmRayResourceHandle)), \
             mock.patch('sky.serve.server.impl.backend_utils.'
                        'get_backend_from_handle',
                        return_value=mock.MagicMock(
                            spec=backends.CloudVmRayBackend)), \
             mock.patch('sky.serve.server.impl._get_service_record',
                        return_value=record):
            yield task

    def test_up_refuses_before_any_bucket_is_made(self, isolated_database):
        _add('st-a', _BOB, 'ws-a')
        with self._env('st-a', active='default') as task, \
             mock.patch(self._ENABLED_CLOUDS,
                        side_effect=_Proceeded) as next_step:
            with pytest.raises(exceptions.StorageSpecError,
                               match="not in the active workspace 'default'"):
                serve_impl.up(task, service_name='svc')
        next_step.assert_not_called()
        with self._env('st-a', active='ws-a') as task, \
             mock.patch(self._ENABLED_CLOUDS, side_effect=_Proceeded):
            with pytest.raises(_Proceeded):
                serve_impl.up(task, service_name='svc')

    def test_update_refuses_before_any_bucket_is_made(self, isolated_database):
        _add('st-a', _BOB, 'ws-a')
        with self._env('st-a', active='default') as task, \
             mock.patch(self._SYNC_UP, side_effect=_Proceeded) as next_step:
            with pytest.raises(exceptions.StorageSpecError,
                               match="not in the active workspace 'default'"):
                serve_impl.update(task, 'svc')
        next_step.assert_not_called()
        with self._env('st-a', active='ws-a') as task, \
             mock.patch(self._SYNC_UP, side_effect=_Proceeded):
            with pytest.raises(_Proceeded):
                serve_impl.update(task, 'svc')


class TestServeStatusWorkspace:
    """`status` lists every user's services; the workspace of one in a
    workspace the caller cannot read is not shown."""

    _READABLE = 'sky.workspaces.core.get_readable_workspace_names'

    @contextlib.contextmanager
    def _env(self, records):
        runner = mock.Mock()
        runner.get_service_status.return_value = [dict(r) for r in records]
        serve_runner.register(runner)
        try:
            with mock.patch('sky.serve.server.impl.backend_utils.'
                            'check_network_connection'), \
                 mock.patch('sky.serve.server.impl.controller_utils.'
                            'get_controller_for_pool'), \
                 mock.patch('sky.serve.server.impl.backend_utils.'
                            'is_controller_accessible',
                            return_value=mock.MagicMock(
                                spec=backends.CloudVmRayResourceHandle)), \
                 mock.patch('sky.serve.server.impl._resolve_user_names'):
                yield
        finally:
            serve_runner.reset_for_testing()

    _RECORDS = [{
        'name': 'a',
        'workspace': 'ws-a',
        'load_balancer_port': None
    }, {
        'name': 'b',
        'workspace': 'ws-b',
        'load_balancer_port': None
    }]

    def test_unreadable_workspaces_are_withheld(self):
        with self._env(self._RECORDS), \
             mock.patch(self._READABLE, return_value={'ws-a'}):
            records = serve_impl.status()
        assert [r['workspace'] for r in records] == ['ws-a', None]

    def test_admin_sees_every_workspace(self):
        with self._env(self._RECORDS), \
             mock.patch(self._READABLE, return_value=None):
            records = serve_impl.status()
        assert [r['workspace'] for r in records] == ['ws-a', 'ws-b']


class TestServeCleanup:
    """A service's cleanup runs on its controller, outside any request. A
    storage recorded in another workspace is not the service's to remove,
    and leaving it must not fail the cleanup, which would fail the same
    way on every recovery."""

    _IN_REQUEST = 'sky.data.storage.common_utils.is_in_request_context'
    _ACTIVE = 'sky.data.storage.skypilot_config.get_active_workspace'
    _BACKEND = 'sky.serve.service.cloud_vm_ray_backend.CloudVmRayBackend'
    _YAML = ('run: echo\n'
             'file_mounts:\n'
             '  /data:\n'
             '    name: st-b\n'
             '    persistent: false\n')

    def test_unreachable_storage_is_left_and_cleanup_finishes(
            self, isolated_database):
        _add('st-b', _BOB, 'ws-b')
        with mock.patch(self._IN_REQUEST, return_value=False), \
             mock.patch(self._ACTIVE, return_value='ws-a'), \
             mock.patch(self._BACKEND) as backend_cls:
            assert service.cleanup_storage(self._YAML) is True
        teardown = backend_cls.return_value.teardown_ephemeral_storage
        teardown.assert_called_once()
        (task,) = teardown.call_args.args
        assert task.storage_mounts == {}
        assert _record('st-b')['name'] == 'st-b'

    def test_other_spec_errors_still_fail_the_cleanup(self, tmp_path):
        """Only the workspace refusal is left alone; a bad source is a
        failure, as before."""
        yaml_content = ('run: echo\n'
                        'file_mounts:\n'
                        '  /data:\n'
                        '    name: st-x\n'
                        f'    source: {tmp_path}/no-such-dir\n'
                        '    persistent: false\n')
        with mock.patch(self._IN_REQUEST, return_value=False), \
             mock.patch(self._BACKEND) as backend_cls:
            assert service.cleanup_storage(yaml_content) is False
        backend_cls.return_value.teardown_ephemeral_storage.assert_not_called()


class TestWorkspaceDeletion:
    """A workspace that still holds storage is not deleted: its storage
    would be hidden from everyone, with no way to delete it or reuse its
    name."""

    _ACTIVE = 'sky.utils.resource_checker._get_active_resources'

    def test_storage_blocks_deletion(self, isolated_database):
        _add('st-a', _ALICE, 'ws-a')
        with mock.patch(self._ACTIVE, return_value=([], [], [])):
            with pytest.raises(ValueError, match='st-a'):
                resource_checker.check_no_active_resources_for_workspaces([
                    ('ws-a', 'delete')
                ])

    def test_update_and_other_workspaces_pass(self, isolated_database):
        _add('st-a', _ALICE, 'ws-a')
        with mock.patch(self._ACTIVE, return_value=([], [], [])):
            resource_checker.check_no_active_resources_for_workspaces([
                ('ws-a', 'update'), ('ws-b', 'delete')
            ])


class TestCompletion:

    def test_completion_filters_by_workspace(self, isolated_database):
        _add('st-a', _ALICE, 'ws-a')
        _add('st-b', _BOB, 'ws-b')

        assert global_user_state.get_storage_names_start_with(
            'st', workspaces_filter={'ws-a'}) == ['st-a']
        assert sorted(global_user_state.get_storage_names_start_with('st')) == [
            'st-a', 'st-b'
        ]
        assert global_user_state.get_storage_names_start_with(
            'st', workspaces_filter=set()) == []


class TestReadOnlyRefusalFailsPrechecks:
    """A managed job naming read-only storage fails with FAILED_PRECHECKS,
    while other denials (a 403 from the API server, a membership not yet
    propagated) stay retryable."""

    def test_read_only_refusal_is_a_precheck_failure(self):
        assert isinstance(exceptions.StorageReadOnlyError('read-only'),
                          recovery_strategy.PRECHECK_FAILURES)

    def test_other_denials_are_not_precheck_failures(self):
        assert not isinstance(exceptions.PermissionDeniedError('403'),
                              recovery_strategy.PRECHECK_FAILURES)


class TestStorageTable:
    """`sky storage ls` shows the workspace by name."""

    def _row(self, **kwargs):
        return responses.StorageRecord(name='st',
                                       launched_at=0,
                                       store=[storage_lib.StoreType.S3],
                                       last_use='sky launch',
                                       status=status_lib.StorageStatus.READY,
                                       **kwargs)

    def test_workspace_column_shows_the_name(self):
        table = table_utils.format_storage_table(
            [self._row(workspace='ws-1a2b', workspace_name='team-a')],
            show_all=True)
        assert self._workspace_cell(table) == 'team-a'
        assert 'ws-1a2b' not in table

    def test_older_server_without_workspace_shows_a_dash(self):
        table = table_utils.format_storage_table([self._row()], show_all=True)
        assert self._workspace_cell(table) == '-'

    @staticmethod
    def _workspace_cell(table: str) -> str:
        header, row = table.splitlines()[:2]
        return row[header.index('WORKSPACE'):].split()[0]
