import time

import pytest
import sqlalchemy

from sky.utils.db import kv_cache


@pytest.fixture()
def isolated_database(tmp_path):
    """Create an isolated DB and logs directory per-test."""
    temp_db_path = tmp_path / 'kv_cache.db'
    temp_log_path = tmp_path / 'logs'
    temp_log_path.mkdir()

    kv_cache._db_manager._engine = sqlalchemy.create_engine(
        f'sqlite:///{temp_db_path}')
    kv_cache.create_table(kv_cache._db_manager.get_engine())
    yield
    kv_cache._db_manager._engine = None


def test_cache_entry_basic(isolated_database):
    kv_cache.add_or_update_cache_entry('test_key', 'test_value',
                                       time.time() + 3600)
    assert kv_cache.get_cache_entry('test_key') == 'test_value'


def test_get_cache_entry_expired(isolated_database):
    # add a cache entry that is expired
    kv_cache.add_or_update_cache_entry('test_key', 'test_value',
                                       time.time() - 3600)
    assert kv_cache.get_cache_entry('test_key') is None
    # add a cache entry that is not expired
    kv_cache.add_or_update_cache_entry('test_key', 'test_value',
                                       time.time() + 3600)
    assert kv_cache.get_cache_entry('test_key') == 'test_value'


def test_delete_cache_entries_by_prefix(isolated_database):
    expires = time.time() + 3600
    kv_cache.add_or_update_cache_entry('perm:ws:ws1:user1', '1', expires)
    kv_cache.add_or_update_cache_entry('perm:ws:ws1:user2', '0', expires)
    kv_cache.add_or_update_cache_entry('perm:ws:ws2:user1', '1', expires)
    kv_cache.add_or_update_cache_entry('perm:ws:ws11:user1', '1', expires)
    kv_cache.add_or_update_cache_entry('other:key', 'val', expires)

    # Delete all entries for ws1
    kv_cache.delete_cache_entries_by_prefix('perm:ws:ws1:')

    # ws1 entries should be gone
    assert kv_cache.get_cache_entry('perm:ws:ws1:user1') is None
    assert kv_cache.get_cache_entry('perm:ws:ws1:user2') is None
    # ws2 and other entries should remain
    assert kv_cache.get_cache_entry('perm:ws:ws2:user1') == '1'
    assert kv_cache.get_cache_entry('perm:ws:ws11:user1') == '1'
    assert kv_cache.get_cache_entry('other:key') == 'val'


def test_delete_cache_entries_by_prefix_no_matches(isolated_database):
    kv_cache.add_or_update_cache_entry('key1', 'value1', time.time() + 3600)
    # Should not raise an error and should not delete unrelated entries
    kv_cache.delete_cache_entries_by_prefix('nonexistent_prefix')
    assert kv_cache.get_cache_entry('key1') == 'value1'


def test_delete_cache_entries_by_pattern(isolated_database):
    expires = time.time() + 3600
    kv_cache.add_or_update_cache_entry('perm:ws:ws1:user1', '1', expires)
    kv_cache.add_or_update_cache_entry('perm:ws:ws2:user1', '1', expires)
    kv_cache.add_or_update_cache_entry('perm:ws:ws1:user2', '0', expires)
    kv_cache.add_or_update_cache_entry('other:key', 'val', expires)

    # Delete all entries for user1 across all workspaces
    kv_cache.delete_cache_entries_by_pattern('perm:ws:%:user1')

    # user1 entries should be gone
    assert kv_cache.get_cache_entry('perm:ws:ws1:user1') is None
    assert kv_cache.get_cache_entry('perm:ws:ws2:user1') is None
    # user2 and other entries should remain
    assert kv_cache.get_cache_entry('perm:ws:ws1:user2') == '0'
    assert kv_cache.get_cache_entry('other:key') == 'val'


def test_delete_cache_entries_by_pattern_no_matches(isolated_database):
    kv_cache.add_or_update_cache_entry('key1', 'value1', time.time() + 3600)
    # Should not raise an error and should not delete unrelated entries
    kv_cache.delete_cache_entries_by_pattern('perm:ws:%:nonexistent')
    assert kv_cache.get_cache_entry('key1') == 'value1'
