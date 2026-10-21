import threading
import time

from sky.utils import cache_utils


def test_ttl_cache_expiration_and_thread_safety():
    call_count = 0
    lock = threading.Lock()

    @cache_utils.ttl_cache(ttl_seconds=1)
    def mock_quota_check(region: str) -> bool:
        nonlocal call_count
        with lock:
            call_count += 1
        return True

    # 1. Initial call should execute the function
    assert mock_quota_check("us-central1") is True
    assert call_count == 1

    # 2. Immediate subsequent call should hit cache (call_count remains 1)
    assert mock_quota_check("us-central1") is True
    assert call_count == 1

    # 3. Wait for TTL to expire (> 1 second)
    time.sleep(1.1)

    # 4. Post-expiration call should re-execute function (call_count increments to 2)
    assert mock_quota_check("us-central1") is True
    assert call_count == 2

    # Cleanup cache
    mock_quota_check.cache_clear()  # type: ignore


def test_concurrent_cache_access():

    @cache_utils.ttl_cache(ttl_seconds=5)
    def compute_expensive_check(x: int) -> int:
        time.sleep(0.05)
        return x * 2

    results = []

    def worker():
        res = compute_expensive_check(10)
        results.append(res)

    # Spawn 10 concurrent threads hitting the same cached function
    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All threads should successfully retrieve the correct cached value
    assert all(r == 20 for r in results)
    compute_expensive_check.cache_clear()  # type: ignore
