"""Unit tests for sky.utils.annotations."""

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
        assert any(f().__name__ == 'request_scoped_func' or
                   hasattr(f(), '__wrapped__')
                   for f in annotations._FUNCTIONS_NEED_RELOAD_CACHE
                   if f() is not None)

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
