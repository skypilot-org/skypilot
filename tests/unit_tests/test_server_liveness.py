"""Tests for sky.server.server_liveness."""
# pylint: disable=protected-access
import os
import time
import unittest
from unittest import mock

import sqlalchemy
from sqlalchemy import orm

from sky import global_user_state
from sky.server import constants as server_constants
from sky.server import server_liveness
from sky.skylet import constants


def _create_in_memory_engine():
    """Create an in-memory SQLite engine with the required table."""
    engine = sqlalchemy.create_engine('sqlite://')
    global_user_state.api_server_instance_table.create(bind=engine)
    return engine


def _insert_server(engine,
                   server_id,
                   pid=1000,
                   hostname='host1',
                   started_at=None,
                   last_heartbeat=None):
    now = time.time()
    table = global_user_state.api_server_instance_table
    with orm.Session(engine) as session:
        session.execute(table.insert().values(
            server_id=server_id,
            pid=pid,
            hostname=hostname,
            started_at=started_at or now,
            last_heartbeat=last_heartbeat or now,
        ))
        session.commit()


def _count_servers(engine):
    table = global_user_state.api_server_instance_table
    with orm.Session(engine) as session:
        result = session.execute(table.select())
        return len(result.fetchall())


class TestCleanupStaleEntries(unittest.TestCase):
    """Tests for stale entry cleanup."""

    def test_removes_stale_entries(self):
        engine = _create_in_memory_engine()
        stale_time = (time.time() -
                      server_constants.SERVER_ALIVE_STALE_THRESHOLD_SECONDS -
                      10)
        _insert_server(engine, 'stale-1', last_heartbeat=stale_time)
        _insert_server(engine, 'fresh-1', last_heartbeat=time.time())

        server_liveness._cleanup_stale_entries(engine)

        self.assertEqual(_count_servers(engine), 1)

    def test_keeps_fresh_entries(self):
        engine = _create_in_memory_engine()
        _insert_server(engine, 'fresh-1', last_heartbeat=time.time())
        _insert_server(engine, 'fresh-2', last_heartbeat=time.time())

        server_liveness._cleanup_stale_entries(engine)

        self.assertEqual(_count_servers(engine), 2)


class TestCheckAndRegisterServer(unittest.TestCase):
    """Tests for the check-and-register startup logic."""

    def setUp(self):
        self.engine = _create_in_memory_engine()
        # Reset global state.
        server_liveness._SERVER_ID = ''

    @mock.patch.object(global_user_state, 'initialize_and_get_db')
    def test_registers_on_empty_db(self, mock_get_db):
        mock_get_db.return_value = self.engine

        server_liveness.check_and_register_server()

        self.assertEqual(_count_servers(self.engine), 1)
        self.assertNotEqual(server_liveness._SERVER_ID, '')

    @mock.patch.object(global_user_state, 'initialize_and_get_db')
    def test_error_when_another_server_active(self, mock_get_db):
        mock_get_db.return_value = self.engine
        _insert_server(self.engine, 'other-server', last_heartbeat=time.time())

        with self.assertRaises(RuntimeError) as ctx:
            server_liveness.check_and_register_server()

        self.assertIn('Another SkyPilot API server', str(ctx.exception))

    @mock.patch.object(global_user_state, 'initialize_and_get_db')
    def test_ok_when_stale_server(self, mock_get_db):
        mock_get_db.return_value = self.engine
        stale_time = (time.time() -
                      server_constants.SERVER_ALIVE_STALE_THRESHOLD_SECONDS -
                      10)
        _insert_server(self.engine, 'crashed-server', last_heartbeat=stale_time)

        server_liveness.check_and_register_server()

        # Stale entry removed, new entry added.
        self.assertEqual(_count_servers(self.engine), 1)

    @mock.patch.dict(os.environ,
                     {constants.SKYPILOT_ROLLING_UPDATE_ENABLED: 'true'})
    @mock.patch.object(global_user_state, 'initialize_and_get_db')
    def test_ok_during_rolling_update(self, mock_get_db):
        mock_get_db.return_value = self.engine
        _insert_server(self.engine, 'old-pod', last_heartbeat=time.time())

        # Should not raise.
        server_liveness.check_and_register_server()

        # Both entries present.
        self.assertEqual(_count_servers(self.engine), 2)

    @mock.patch.object(global_user_state, 'initialize_and_get_db')
    def test_uses_pod_uid_when_available(self, mock_get_db):
        mock_get_db.return_value = self.engine
        with mock.patch.dict(os.environ,
                             {'SKYPILOT_APISERVER_UUID': 'pod-uid-123'}):
            server_liveness.check_and_register_server()

        self.assertEqual(server_liveness._SERVER_ID, 'pod-uid-123')


class TestDeregisterServer(unittest.TestCase):
    """Tests for server deregistration."""

    @mock.patch.object(global_user_state, 'initialize_and_get_db')
    def test_deregister_removes_entry(self, mock_get_db):
        engine = _create_in_memory_engine()
        mock_get_db.return_value = engine
        _insert_server(engine, 'my-server')
        server_liveness._SERVER_ID = 'my-server'

        server_liveness._deregister_server()

        self.assertEqual(_count_servers(engine), 0)

    def test_deregister_noop_when_no_id(self):
        server_liveness._SERVER_ID = ''
        # Should not raise.
        server_liveness._deregister_server()


if __name__ == '__main__':
    unittest.main()
