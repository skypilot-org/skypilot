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

    kv_cache._SQLALCHEMY_ENGINE = sqlalchemy.create_engine(
        f'sqlite:///{temp_db_path}')
    kv_cache.create_table(kv_cache._SQLALCHEMY_ENGINE)
    yield
    kv_cache._SQLALCHEMY_ENGINE = None


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
