"""DB-level tests for cluster history soft delete (#8933).

Covers the is_deleted filter in iter_clusters_from_history and the
set_cluster_history_deleted flagging helper, against a real in-memory-shape
SQLite database built from the live table definitions.
"""

from pathlib import Path
import pickle
import tempfile
import time
import unittest
import unittest.mock as mock

import sqlalchemy

from sky import resources as resources_lib
import sky.global_user_state as global_user_state


def _insert_history_row(engine,
                        cluster_hash,
                        user_hash='u1',
                        name='my-cluster',
                        is_deleted=0,
                        launched_at=None):
    now = int(time.time())
    launched_at = launched_at if launched_at is not None else now
    resources = pickle.dumps(resources_lib.Resources(cpus='2'))
    intervals = pickle.dumps([(launched_at, launched_at + 60)])
    with engine.connect() as conn:
        conn.execute(
            sqlalchemy.insert(global_user_state.cluster_history_table).values(
                cluster_hash=cluster_hash,
                name=name,
                num_nodes=1,
                launched_resources=resources,
                usage_intervals=intervals,
                user_hash=user_hash,
                launched_at=launched_at,
                last_activity_time=launched_at,
                is_managed=0,
                is_deleted=is_deleted))
        conn.commit()


class TestClusterHistorySoftDelete(unittest.TestCase):
    """Soft delete behavior of the cluster history listing."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self._tmpdir.name) / 'state.db'
        self.engine = sqlalchemy.create_engine(f'sqlite:///{db_path}')
        global_user_state.Base.metadata.create_all(self.engine)
        # Both the listing and the flagging helper resolve the engine through
        # the module-level manager; point it at the temp database.
        self._manager_patcher = mock.patch.object(
            global_user_state, '_db_manager',
            mock.MagicMock(get_engine=lambda: self.engine))
        self._manager_patcher.start()
        self.addCleanup(self._manager_patcher.stop)
        self.addCleanup(self._tmpdir.cleanup)

        _insert_history_row(self.engine, 'h-active', name='kept')
        _insert_history_row(self.engine,
                            'h-deleted',
                            name='removed',
                            is_deleted=1)
        # A row written before the is_deleted column existed: the migration
        # backfills 0, but a NULL must keep behaving like "not deleted".
        _insert_history_row(self.engine,
                            'h-legacy',
                            name='legacy',
                            is_deleted=None)

    def _listed_hashes(self, **kwargs):
        records = list(global_user_state.iter_clusters_from_history(**kwargs))
        return {r['cluster_hash']: r for r in records}

    def test_listing_hides_soft_deleted_rows(self):
        """The default listing keeps active rows and skips flagged ones."""
        records = self._listed_hashes()

        self.assertIn('h-active', records)
        self.assertIn('h-legacy', records)
        self.assertNotIn('h-deleted', records)
        self.assertFalse(records['h-active']['is_deleted'])
        self.assertFalse(records['h-legacy']['is_deleted'])

    def test_include_deleted_flag_returns_everything(self):
        """include_deleted=True hands back all rows, each with its flag."""
        records = self._listed_hashes(include_deleted=True)

        self.assertEqual(set(records), {'h-active', 'h-deleted', 'h-legacy'})
        self.assertTrue(records['h-deleted']['is_deleted'])
        self.assertFalse(records['h-active']['is_deleted'])
        self.assertFalse(records['h-legacy']['is_deleted'])

    def test_soft_delete_then_restore_round_trip(self):
        """Flagging hides a row; the same call with deleted=False restores."""
        _insert_history_row(self.engine, 'h-roundtrip', name='roundtrip')

        updated = global_user_state.set_cluster_history_deleted(['h-roundtrip'],
                                                                deleted=True)
        self.assertEqual(updated, 1)
        self.assertNotIn('h-roundtrip', self._listed_hashes())

        restored = global_user_state.set_cluster_history_deleted(
            ['h-roundtrip'], deleted=False)
        self.assertEqual(restored, 1)
        self.assertIn('h-roundtrip', self._listed_hashes())
        self.assertFalse(self._listed_hashes()['h-roundtrip']['is_deleted'])

    def test_soft_delete_scoped_to_caller_rows(self):
        """A caller's flag only lands on rows they own."""
        _insert_history_row(self.engine, 'h-u2', user_hash='u2', name='theirs')

        updated = global_user_state.set_cluster_history_deleted(
            ['h-active', 'h-u2'], deleted=True, caller_user_hash='u1')

        # Only the caller's own row was touched; the other user's row is
        # left visible.
        self.assertEqual(updated, 1)
        listed = self._listed_hashes()
        self.assertNotIn('h-active', listed)
        self.assertIn('h-u2', listed)

        # No caller (local, unauthenticated use) updates regardless of owner.
        updated = global_user_state.set_cluster_history_deleted(
            ['h-active', 'h-u2'], deleted=True, caller_user_hash=None)
        self.assertEqual(updated, 2)
        self.assertNotIn('h-u2', self._listed_hashes())

    def test_soft_delete_unknown_hashes_are_a_noop(self):
        """Hashes with no history row simply update nothing."""
        updated = global_user_state.set_cluster_history_deleted(
            ['h-nonexistent'], deleted=True)
        self.assertEqual(updated, 0)
        # Nothing changed: the two unflagged rows are still listed.
        self.assertEqual(set(self._listed_hashes()), {'h-active', 'h-legacy'})

    def test_soft_delete_empty_hash_list_is_a_noop(self):
        """An empty hash list short-circuits without touching the DB."""
        updated = global_user_state.set_cluster_history_deleted([],
                                                                deleted=True)
        self.assertEqual(updated, 0)
        self.assertEqual(set(self._listed_hashes()), {'h-active', 'h-legacy'})

    def test_targeted_hash_lookup_can_include_soft_deleted_rows(self):
        """A by-hash lookup with include_deleted resolves the hidden row."""
        records = self._listed_hashes(cluster_hashes=['h-deleted'],
                                      include_deleted=True)

        self.assertEqual(set(records), {'h-deleted'})
        self.assertTrue(records['h-deleted']['is_deleted'])


if __name__ == '__main__':
    unittest.main()
