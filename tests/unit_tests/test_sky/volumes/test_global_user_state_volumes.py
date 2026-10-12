"""Unit tests for volume database operations in global_user_state.py.

These tests specifically cover the is_ephemeral boolean/integer conversion
that is necessary for PostgreSQL compatibility. See issue #8178 and PR #8179.
"""

import pickle
import sqlite3
from unittest import mock

import pytest
import sqlalchemy
import sqlalchemy.orm

from sky import global_user_state
from sky import models
from sky.skylet import constants
from sky.utils import status_lib
from sky.utils.db import db_utils


class TestVolumeIsEphemeralHandling:
    """Test is_ephemeral boolean/integer handling in volume operations.

    PostgreSQL stores boolean values as integers (0/1), while SQLite
    handles Python booleans transparently. These tests verify that
    is_ephemeral is correctly converted to integer when storing and
    back to boolean when retrieving.
    """

    @pytest.fixture
    def mock_engine(self):
        """Mock SQLAlchemy engine."""
        with mock.patch.object(global_user_state._db_manager,
                               '_engine') as engine:
            engine.dialect.name = 'sqlite'
            yield engine

    @pytest.fixture
    def mock_session(self, mock_engine):  # pylint: disable=unused-argument
        """Mock SQLAlchemy session."""
        with mock.patch(
                'sky.global_user_state.orm.Session') as mock_session_class:
            session = mock.Mock()
            mock_session_class.return_value.__enter__.return_value = session
            yield session

    @pytest.fixture
    def mock_volume_config(self):
        """Create a mock volume config."""
        return models.VolumeConfig(
            name='test-volume',
            cloud='kubernetes',
            type='k8s-pvc',
            region='my-context',
            zone=None,
            size='100Gi',
            config={},
            name_on_cloud='test-pvc',
        )

    def _setup_add_volume_mocks(self, mock_db_module):
        """Helper to set up common mocks for add_volume tests."""
        mock_insert = mock.Mock()
        mock_db_module.insert.return_value = mock_insert
        return mock_insert

    @pytest.mark.parametrize('dialect,db_module_path', [
        ('sqlite', 'sky.global_user_state.sqlite'),
        ('postgresql', 'sky.global_user_state.postgresql'),
    ])
    @pytest.mark.parametrize('is_ephemeral,expected_value', [
        (True, 1),
        (False, 0),
    ])
    def test_add_volume_is_ephemeral(
            self,
            mock_engine,
            mock_session,  # pylint: disable=unused-argument
            mock_volume_config,
            dialect,
            db_module_path,
            is_ephemeral,
            expected_value):
        """Test add_volume converts is_ephemeral boolean to integer.

        This covers both SQLite and PostgreSQL dialects, verifying the fix
        for issue #8178. Uses type() is int to ensure the value is actually
        an int and not a bool (since bool is a subclass of int in Python).
        """
        mock_engine.dialect.name = dialect
        with mock.patch(db_module_path) as mock_db_module:
            with mock.patch('time.time', return_value=1234567890):
                with mock.patch(
                        'sky.global_user_state.common_utils.get_current_command',
                        return_value='sky volumes apply'):
                    with mock.patch(
                            'sky.global_user_state.common_utils.get_current_user'
                    ) as mock_user:
                        with mock.patch(
                                'sky.global_user_state.skypilot_config.'
                                'get_active_workspace',
                                return_value='default'):
                            mock_user.return_value = mock.Mock(id='user123')
                            mock_insert = self._setup_add_volume_mocks(
                                mock_db_module)

                            global_user_state.add_volume(
                                name='test-volume',
                                config=mock_volume_config,
                                status=status_lib.VolumeStatus.READY,
                                is_ephemeral=is_ephemeral,
                            )

                            # Verify insert was called with correct int value
                            mock_insert.values.assert_called_once()
                            call_kwargs = mock_insert.values.call_args[1]
                            assert call_kwargs['is_ephemeral'] == expected_value
                            # Use type() is int because bool is subclass of int
                            # pylint: disable=unidiomatic-typecheck
                            assert type(call_kwargs['is_ephemeral']) is int

    @pytest.mark.parametrize('dialect', ['sqlite', 'postgresql'])
    @pytest.mark.parametrize('is_ephemeral,expected_value', [
        (True, 1),
        (False, 0),
    ])
    def test_get_volumes_filter_is_ephemeral(self, mock_engine, mock_session,
                                             dialect, is_ephemeral,
                                             expected_value):
        """Test get_volumes filtering converts is_ephemeral to integer.

        This covers both SQLite and PostgreSQL dialects, verifying the fix
        for issue #8178. Uses type() is int to ensure the value is actually
        an int and not a bool (since bool is a subclass of int in Python).
        """
        mock_engine.dialect.name = dialect
        mock_session.query.return_value.filter_by.return_value.all.return_value = []

        global_user_state.get_volumes(is_ephemeral=is_ephemeral)

        # Verify filter_by was called with correct int value
        mock_session.query.return_value.filter_by.assert_called_once()
        call_kwargs = mock_session.query.return_value.filter_by.call_args[1]
        assert call_kwargs['is_ephemeral'] == expected_value
        # Use type() is int because bool is subclass of int
        # pylint: disable=unidiomatic-typecheck
        assert type(call_kwargs['is_ephemeral']) is int

    def test_get_volumes_filter_none_returns_all(
            self,
            mock_engine,  # pylint: disable=unused-argument
            mock_session):
        """Test get_volumes with is_ephemeral=None returns all volumes."""
        mock_session.query.return_value.all.return_value = []

        global_user_state.get_volumes(is_ephemeral=None)

        # Verify query.all() was called (not filter_by)
        mock_session.query.return_value.all.assert_called_once()
        mock_session.query.return_value.filter_by.assert_not_called()

    @pytest.mark.parametrize('db_value,expected_bool,last_attached', [
        (1, True, 1234567890),
        (0, False, None),
    ])
    def test_get_volumes_returns_bool_is_ephemeral(
            self,
            mock_engine,  # pylint: disable=unused-argument
            mock_session,
            mock_volume_config,
            db_value,
            expected_bool,
            last_attached):
        """Test get_volumes returns a boolean for is_ephemeral.

        Verifies that the integer value stored in the database is
        converted back to a Python boolean when returned.
        """
        # Mock row with is_ephemeral as stored in database
        mock_row = mock.Mock()
        mock_row.name = 'test-volume'
        mock_row.launched_at = 1234567890
        mock_row.handle = pickle.dumps(mock_volume_config)
        mock_row.user_hash = 'user123'
        mock_row.workspace = 'default'
        mock_row.last_attached_at = last_attached
        mock_row.last_use = 'sky volumes apply'
        mock_row.status = 'READY'
        mock_row.is_ephemeral = db_value  # Integer as stored in database
        mock_row.error_message = None
        mock_row.usedby_pods = '[]'
        mock_row.usedby_clusters = '[]'

        mock_session.query.return_value.all.return_value = [mock_row]

        result = global_user_state.get_volumes()

        assert len(result) == 1
        assert result[0]['is_ephemeral'] is expected_bool
        assert isinstance(result[0]['is_ephemeral'], bool)

    def test_add_volume_unsupported_dialect(
            self,
            mock_engine,
            mock_session,  # pylint: disable=unused-argument
            mock_volume_config):
        """Test add_volume raises error for unsupported database dialect."""
        mock_engine.dialect.name = 'mysql'

        with mock.patch('time.time', return_value=1234567890):
            with mock.patch(
                    'sky.global_user_state.common_utils.get_current_command',
                    return_value='sky volumes apply'):
                with mock.patch(
                        'sky.global_user_state.common_utils.get_current_user'
                ) as mock_user:
                    with mock.patch(
                            'sky.global_user_state.skypilot_config.'
                            'get_active_workspace',
                            return_value='default'):
                        mock_user.return_value = mock.Mock(id='user123')

                        with pytest.raises(
                                ValueError,
                                match='Unsupported database dialect'):
                            global_user_state.add_volume(
                                name='test-volume',
                                config=mock_volume_config,
                                status=status_lib.VolumeStatus.READY,
                                is_ephemeral=True,
                            )

    def test_add_volume_ephemeral_sets_in_use_status(
            self,
            mock_engine,  # pylint: disable=unused-argument
            mock_session,  # pylint: disable=unused-argument
            mock_volume_config):
        """Test add_volume with is_ephemeral=True sets status to IN_USE."""
        with mock.patch('sky.global_user_state.sqlite') as mock_sqlite:
            with mock.patch('time.time', return_value=1234567890):
                with mock.patch(
                        'sky.global_user_state.common_utils.get_current_command',
                        return_value='sky volumes apply'):
                    with mock.patch(
                            'sky.global_user_state.common_utils.get_current_user'
                    ) as mock_user:
                        with mock.patch(
                                'sky.global_user_state.skypilot_config.'
                                'get_active_workspace',
                                return_value='default'):
                            mock_user.return_value = mock.Mock(id='user123')
                            mock_insert = self._setup_add_volume_mocks(
                                mock_sqlite)

                            global_user_state.add_volume(
                                name='test-volume',
                                config=mock_volume_config,
                                status=status_lib.VolumeStatus.READY,
                                is_ephemeral=True,
                            )

                            # Verify status is IN_USE for ephemeral volumes
                            call_kwargs = mock_insert.values.call_args[1]
                            expected_status = status_lib.VolumeStatus.IN_USE
                            assert call_kwargs[
                                'status'] == expected_status.value
                            # Verify last_attached_at is set
                            assert call_kwargs['last_attached_at'] == 1234567890


class TestVolumeResizeColumnsRoundTrip:
    """The resize fields against a real database, not a mocked session.

    Every other test here mocks the SQLAlchemy session, so nothing proves the
    three columns exist: they arrive with a migration, and the target revision
    is pinned rather than resolved to the newest one, so a migration that is
    written but not pinned leaves the code writing columns the database does
    not have. That is a runtime failure a mocked session cannot see.
    """

    @pytest.fixture
    def fresh_db(self, tmp_path, monkeypatch):
        """A state database built the way production builds it."""
        monkeypatch.setenv(constants.SKY_RUNTIME_DIR_ENV_VAR_KEY, str(tmp_path))
        monkeypatch.setattr(
            global_user_state,
            '_db_manager',
            db_utils.DatabaseManager(
                'state',
                global_user_state.create_table,
                post_init_fn=lambda _: global_user_state.
                _sqlite_supports_returning(),
            ),
        )
        monkeypatch.setattr(global_user_state.common_utils, 'get_current_user',
                            lambda: models.User(id='user123', name='user'))
        monkeypatch.setattr(global_user_state.skypilot_config,
                            'get_active_workspace', lambda: 'default')

    def _add_volume(self, name='test-volume'):
        global_user_state.add_volume(
            name=name,
            config=models.VolumeConfig(
                name=name,
                type='k8s-pvc',
                cloud='kubernetes',
                region='my-context',
                zone=None,
                size='10',
                config={},
                name_on_cloud='test-pvc',
            ),
            status=status_lib.VolumeStatus.READY,
        )

    def test_a_resize_in_flight_survives_the_database(self, fresh_db):
        self._add_volume()

        global_user_state.update_volume_status(
            'test-volume',
            status_lib.VolumeStatus.READY,
            resize_status=models.VolumeResizeStatus.PENDING_ON_NODE,
            resize_target_size='25',
            resize_message='Waiting for a pod.')

        record = global_user_state.get_volume_by_name('test-volume')
        assert record['resize_status'] == 'pending_on_node'
        assert record['resize_target_size'] == '25'
        assert record['resize_message'] == 'Waiting for a pod.'
        # The size is the capacity the volume has, and a resize does not move
        # it.
        assert record['handle'].size == '10'

    @pytest.mark.skipif(sqlite3.sqlite_version_info < (3, 35),
                        reason='ALTER TABLE ... DROP COLUMN needs SQLite 3.35+')
    def test_a_database_that_predates_the_columns_is_upgraded_into_them(
            self, fresh_db):
        """The pinned target revision has to reach the migration that adds them.

        A database created from scratch gets every column from the ORM
        metadata whatever revision is pinned, so only an upgrade runs the
        migration -- and the target is pinned rather than resolved to the
        newest revision, so one that is written but not pinned never runs.
        """
        engine = global_user_state._db_manager.get_engine()
        columns = ('resize_status', 'resize_target_size', 'resize_message')
        with sqlalchemy.orm.Session(engine) as session:
            for column in columns:
                session.execute(
                    sqlalchemy.text(
                        f'ALTER TABLE volumes DROP COLUMN {column}'))
            session.execute(
                sqlalchemy.text('UPDATE alembic_version_state_db '
                                "SET version_num = '021'"))
            session.commit()

        global_user_state.create_table(engine)

        with sqlalchemy.orm.Session(engine) as session:
            present = session.execute(
                sqlalchemy.text('SELECT * FROM volumes')).keys()
        assert set(columns) <= set(present)

    def test_a_finished_resize_clears_what_was_recorded(self, fresh_db):
        self._add_volume()
        global_user_state.update_volume_status(
            'test-volume',
            status_lib.VolumeStatus.READY,
            resize_status=models.VolumeResizeStatus.IN_PROGRESS,
            resize_target_size='25',
            resize_message='Resizing.')

        global_user_state.update_volume_status('test-volume',
                                               status_lib.VolumeStatus.READY)

        record = global_user_state.get_volume_by_name('test-volume')
        assert record['resize_status'] is None
        assert record['resize_target_size'] is None
        assert record['resize_message'] is None
