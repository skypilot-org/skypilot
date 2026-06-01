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


def test_delete_cache_entries_by_prefix_suffix(isolated_database):
    expires = time.time() + 3600
    kv_cache.add_or_update_cache_entry('perm:ws:ws1:user1', '1', expires)
    kv_cache.add_or_update_cache_entry('perm:ws:ws2:user1', '1', expires)
    kv_cache.add_or_update_cache_entry('perm:ws:ws1:user2', '0', expires)
    kv_cache.add_or_update_cache_entry('other:key', 'val', expires)

    # Delete all entries for user1 across all workspaces
    kv_cache.delete_cache_entries_by_prefix_suffix('perm:ws:', ':user1')

    # user1 entries should be gone
    assert kv_cache.get_cache_entry('perm:ws:ws1:user1') is None
    assert kv_cache.get_cache_entry('perm:ws:ws2:user1') is None
    # user2 and other entries should remain
    assert kv_cache.get_cache_entry('perm:ws:ws1:user2') == '0'
    assert kv_cache.get_cache_entry('other:key') == 'val'


def test_delete_cache_entries_by_prefix_suffix_no_matches(isolated_database):
    kv_cache.add_or_update_cache_entry('key1', 'value1', time.time() + 3600)
    # Should not raise an error and should not delete unrelated entries
    kv_cache.delete_cache_entries_by_prefix_suffix('perm:ws:', ':nonexistent')
    assert kv_cache.get_cache_entry('key1') == 'value1'


def test_delete_by_prefix_escapes_percent_in_data(isolated_database):
    """Verify that '%' in the prefix is treated literally, not as wildcard."""
    expires = time.time() + 3600
    kv_cache.add_or_update_cache_entry('50%off:user1', '1', expires)
    kv_cache.add_or_update_cache_entry('50Xoff:user1', '1', expires)
    kv_cache.add_or_update_cache_entry('other:key', 'val', expires)

    # Delete only entries starting with literal '50%off:'
    kv_cache.delete_cache_entries_by_prefix('50%off:')

    assert kv_cache.get_cache_entry('50%off:user1') is None
    # '50Xoff:user1' must NOT be deleted — '%' should not act as wildcard
    assert kv_cache.get_cache_entry('50Xoff:user1') == '1'
    assert kv_cache.get_cache_entry('other:key') == 'val'


def test_delete_by_prefix_escapes_underscore_in_data(isolated_database):
    """Verify that '_' in the prefix is treated literally, not as wildcard."""
    expires = time.time() + 3600
    kv_cache.add_or_update_cache_entry('my_ws:user1', '1', expires)
    kv_cache.add_or_update_cache_entry('myXws:user1', '1', expires)

    kv_cache.delete_cache_entries_by_prefix('my_ws:')

    assert kv_cache.get_cache_entry('my_ws:user1') is None
    # 'myXws:user1' must NOT be deleted — '_' should not match 'X'
    assert kv_cache.get_cache_entry('myXws:user1') == '1'


def test_delete_by_prefix_escapes_backslash_in_data(isolated_database):
    r"""Verify that '\' in the prefix is treated literally, not as escape.

    Without escaping, LIKE with ESCAPE '\' treats '\d' as matching
    literal 'd', so 'team\dev:%' would match 'teamdev:*' instead
    of 'team\dev:*'.
    """
    expires = time.time() + 3600
    kv_cache.add_or_update_cache_entry('team\\dev:user1', '1', expires)
    kv_cache.add_or_update_cache_entry('teamdev:user1', '1', expires)

    kv_cache.delete_cache_entries_by_prefix('team\\dev:')

    assert kv_cache.get_cache_entry('team\\dev:user1') is None
    # 'teamdev:user1' must NOT be deleted — backslash should not be
    # swallowed as a LIKE escape character
    assert kv_cache.get_cache_entry('teamdev:user1') == '1'


def test_escape_like_helper():
    """Test the _escape_like helper escapes %, _, and backslash."""
    assert kv_cache._escape_like('normal') == 'normal'
    assert kv_cache._escape_like('50%off') == '50\\%off'
    assert kv_cache._escape_like('my_ws') == 'my\\_ws'
    assert kv_cache._escape_like('a\\b') == 'a\\\\b'
    assert kv_cache._escape_like('50%_x\\y') == '50\\%\\_x\\\\y'
