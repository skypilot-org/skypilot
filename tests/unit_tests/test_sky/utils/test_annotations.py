"""Unit tests for sky.utils.annotations."""
import sys
import threading
import time

from sky.adaptors import kubernetes
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


class TestKeepIfOnSyncRefresh:
    """A sync handler's refresh keeps caches whose keep_if holds."""

    def test_keep_if_is_consulted_only_by_a_sync_refresh(self, monkeypatch):
        # A sync refresh also runs the Kubernetes adaptor's keep_if when that
        # module is loaded: keep its process-wide state out of this test.
        monkeypatch.setattr(kubernetes, '_last_refresh_fingerprint', None)
        monkeypatch.setattr(kubernetes, '_clients_cleared_at', 0.0)
        computed = []
        keep_calls = []
        keep = [True]

        def keep_if():
            keep_calls.append(1)
            return keep[0]

        @annotations.lru_cache(scope='request', keep_if=keep_if)
        def kept_a():
            computed.append('a')

        @annotations.lru_cache(scope='request', keep_if=keep_if)
        def kept_b():
            computed.append('b')

        @annotations.lru_cache(scope='request')
        def plain():
            computed.append('plain')

        def call_all():
            computed.clear()
            kept_a()
            kept_b()
            plain()
            return sorted(computed)

        call_all()
        annotations.clear_request_level_cache(sync_refresh=True)
        # Shared keep_if evaluated once; kept caches survive.
        assert len(keep_calls) == 1
        assert call_all() == ['plain']
        keep[0] = False
        annotations.clear_request_level_cache(sync_refresh=True)
        assert call_all() == ['a', 'b', 'plain']
        # The executor's per-request clear ignores keep_if.
        keep[0] = True
        keep_calls.clear()
        annotations.clear_request_level_cache()
        assert keep_calls == []
        assert call_all() == ['a', 'b', 'plain']


class TestTtlCacheConcurrency:
    """TTL caches are shared by the threads of a process."""

    def test_concurrent_misses_and_clears_do_not_raise(self):

        # Global scope: a request-scoped cache would stay registered, and a
        # cache broken by this test would fail every later request-level clear.
        @annotations.ttl_cache(scope='global',
                               maxsize=1,
                               ttl=0.001,
                               timer=time.time)
        def cached(x):
            return x

        errors = []
        deadline = time.time() + 1

        def request(i):
            n = 0
            while time.time() < deadline:
                try:
                    cached((i + n) % 4)
                    if n % 7 == 0:
                        # What each request start does to a request-scoped
                        # cache.
                        cached.cache_clear()
                except Exception as e:  # pylint: disable=broad-except
                    errors.append(repr(e))
                n += 1

        threads = [
            threading.Thread(target=request, args=(i,)) for i in range(16)
        ]
        switch_interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        try:
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        finally:
            sys.setswitchinterval(switch_interval)
        assert not errors, errors[:3]
