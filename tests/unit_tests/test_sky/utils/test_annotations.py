"""Unit tests for sky.utils.annotations."""

import gc
import threading
from unittest.mock import patch

from sky.utils import annotations


class TestLruCache:
    """Tests for lru_cache decorator."""

    def test_caching_works(self):
        """Test that lru_cache decorator caches function results."""
        call_count = 0

        @annotations.lru_cache(scope='global', maxsize=5)
        def expensive_func(x):
            nonlocal call_count
            call_count += 1
            return x * 2

        expensive_func.cache_clear()

        # First call
        result1 = expensive_func(5)
        assert result1 == 10
        assert call_count == 1

        # Second call with same arg should use cache
        result2 = expensive_func(5)
        assert result2 == 10
        assert call_count == 1  # Not incremented

        # Call with different arg should not use cache
        result3 = expensive_func(10)
        assert result3 == 20
        assert call_count == 2

    def test_request_scope_cache_clear(self):
        """Test that request-scope cache is registered for clearing."""

        @annotations.lru_cache(scope='request', maxsize=5)
        def request_scoped_func(x):
            return x + 1

        # The function should be in the _FUNCTIONS_NEED_RELOAD_CACHE list
        assert any(
            f().__name__ == 'request_scoped_func' or hasattr(f, '__wrapped__')
            for f in annotations._FUNCTIONS_NEED_RELOAD_CACHE)

    def test_cache_clear_method(self):
        """Test that cache_clear method works."""
        call_count = 0

        @annotations.lru_cache(scope='global', maxsize=5)
        def func_to_clear(x):
            nonlocal call_count
            call_count += 1
            return x

        func_to_clear.cache_clear()

        # First call
        result1 = func_to_clear(5)
        assert result1 == 5
        assert call_count == 1

        # Use cache
        result2 = func_to_clear(5)
        assert result2 == 5
        assert call_count == 1

        # Clear and call again
        func_to_clear.cache_clear()
        result3 = func_to_clear(5)
        assert result3 == 5
        assert call_count == 2  # Called again after clear


def test_clear_request_level_cache_prunes_dead_entries():
    cache_entries = []
    with patch('sky.utils.annotations._FUNCTIONS_NEED_RELOAD_CACHE',
               cache_entries):

        @annotations.lru_cache(scope='request', maxsize=5)
        def alive_func(x):
            return x

        def _create_temp_func():

            @annotations.lru_cache(scope='request', maxsize=5)
            def temp_func(x):
                return x * 2

            return temp_func

        temp_func = _create_temp_func()

        alive_func(1)
        temp_func(1)

        assert len(cache_entries) == 2

        temp_func = None
        gc.collect()

        annotations.clear_request_level_cache()

        assert len(cache_entries) == 1
        cached_alive_func = cache_entries[0]()
        assert cached_alive_func is alive_func
        assert cached_alive_func.cache_info().currsize == 0
