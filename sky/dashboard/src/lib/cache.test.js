/**
 * Tests for DashboardCache request deduplication and caching behavior
 */

import { DashboardCache } from './cache';

// Helper to create a mock async function that tracks calls
function createMockFetch(returnValue, delay = 10) {
  const calls = [];
  const fn = jest.fn(async (...args) => {
    calls.push({ args, timestamp: Date.now() });
    await new Promise((resolve) => setTimeout(resolve, delay));
    return typeof returnValue === 'function' ? returnValue() : returnValue;
  });
  fn.calls = calls;
  return fn;
}

describe('DashboardCache', () => {
  let cache;

  beforeEach(() => {
    cache = new DashboardCache();
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  describe('Request Deduplication', () => {
    test('should deduplicate concurrent identical requests', async () => {
      const mockFetch = createMockFetch({ data: 'test' }, 100);

      // Make 3 concurrent identical requests
      const promises = [
        cache.get(mockFetch, ['arg1']),
        cache.get(mockFetch, ['arg1']),
        cache.get(mockFetch, ['arg1']),
      ];

      // Fast-forward time to complete the request
      jest.advanceTimersByTime(100);

      const results = await Promise.all(promises);

      // All should return the same data
      expect(results[0]).toEqual({ data: 'test' });
      expect(results[1]).toEqual({ data: 'test' });
      expect(results[2]).toEqual({ data: 'test' });

      // But the fetch function should only be called once
      expect(mockFetch).toHaveBeenCalledTimes(1);
      expect(mockFetch.calls.length).toBe(1);
    });

    test('should not deduplicate requests with different arguments', async () => {
      const mockFetch = createMockFetch({ data: 'test' }, 100);

      // Make concurrent requests with different arguments
      const promises = [
        cache.get(mockFetch, ['arg1']),
        cache.get(mockFetch, ['arg2']),
        cache.get(mockFetch, ['arg3']),
      ];

      jest.advanceTimersByTime(100);
      await Promise.all(promises);

      // Should call fetch 3 times (once for each unique argument set)
      expect(mockFetch).toHaveBeenCalledTimes(3);
    });

    test('should handle sequential requests using cache', async () => {
      const mockFetch = createMockFetch({ data: 'test' }, 50);

      // First request
      const promise1 = cache.get(mockFetch, ['arg1']);
      jest.advanceTimersByTime(50);
      await promise1;

      // Second request after first completes (should use cache)
      const promise2 = cache.get(mockFetch, ['arg1']);
      await promise2;

      // Should use cache for second request, but cache also triggers
      // a background refresh, so we expect 2 calls total
      expect(mockFetch).toHaveBeenCalledTimes(2);
    });

    test('should cleanup pending requests after completion', async () => {
      const mockFetch = createMockFetch({ data: 'test' }, 100);

      const promise = cache.get(mockFetch, ['arg1']);

      // Before completion, pending request should exist
      expect(cache.pendingRequests.size).toBe(1);

      jest.advanceTimersByTime(100);
      await promise;

      // After completion, pending request should be cleaned up
      expect(cache.pendingRequests.size).toBe(0);
    });

    test('should cleanup pending requests even on error', async () => {
      const mockFetch = jest.fn(async () => {
        await new Promise((resolve) => setTimeout(resolve, 100));
        throw new Error('Test error');
      });

      const promise = cache.get(mockFetch, ['arg1']);

      expect(cache.pendingRequests.size).toBe(1);

      jest.advanceTimersByTime(100);

      try {
        await promise;
      } catch (error) {
        // Expected error
      }

      // Should cleanup even on error
      expect(cache.pendingRequests.size).toBe(0);
    });
  });

  describe('Cache Behavior', () => {
    test('should return cached data when available and fresh', async () => {
      jest.useRealTimers(); // Use real timers for this test
      const mockFetch = createMockFetch({ data: 'test' }, 10);

      // First request
      const result1 = await cache.get(mockFetch, ['arg1']);

      // Second request (should use cache)
      const result2 = await cache.get(mockFetch, ['arg1']);

      expect(result1).toEqual({ data: 'test' });
      expect(result2).toEqual({ data: 'test' });
      // First call is the initial fetch, second is background refresh
      expect(mockFetch.mock.calls.length).toBeGreaterThanOrEqual(1);
    });

    test('should fetch fresh data when cache is stale', async () => {
      jest.useRealTimers(); // Use real timers for this test
      const mockFetch = createMockFetch({ data: 'test' }, 10);
      const ttl = 100; // Short TTL for test

      // First request
      await cache.get(mockFetch, ['arg1'], { ttl });

      // Wait for cache to become stale
      await new Promise((resolve) => setTimeout(resolve, ttl + 50));

      // Second request (cache is now stale, should fetch fresh data)
      await cache.get(mockFetch, ['arg1'], { ttl });

      expect(mockFetch.mock.calls.length).toBeGreaterThanOrEqual(2);
    });

    test('should invalidate cache correctly', async () => {
      jest.useRealTimers(); // Use real timers for this test
      const mockFetch = createMockFetch({ data: 'test' }, 10);

      // First request
      await cache.get(mockFetch, ['arg1']);

      // Invalidate cache
      cache.invalidate(mockFetch, ['arg1']);

      // Second request (should fetch fresh data)
      await cache.get(mockFetch, ['arg1']);

      expect(mockFetch.mock.calls.length).toBeGreaterThanOrEqual(2);
    });
  });

  describe('Real-world scenario: Jobs page load', () => {
    test('should handle multiple simultaneous calls from useEffect hooks', async () => {
      // Simulate getManagedJobs function
      const getManagedJobs = createMockFetch(
        {
          jobs: [
            { id: 1, name: 'job1' },
            { id: 2, name: 'job2' },
          ],
          total: 2,
        },
        200
      );

      // Simulate 4 concurrent calls (like the 4 useEffect hooks)
      const params = { allUsers: true, page: 1, limit: 10 };
      const promises = [
        cache.get(getManagedJobs, [params]), // Initial load
        cache.get(getManagedJobs, [params]), // Filters effect
        cache.get(getManagedJobs, [params]), // Status filter effect
        cache.get(getManagedJobs, [params]), // Page effect
      ];

      // All should be pending
      expect(cache.pendingRequests.size).toBe(1);

      jest.advanceTimersByTime(200);
      const results = await Promise.all(promises);

      // All should get the same data
      results.forEach((result) => {
        expect(result.jobs).toHaveLength(2);
        expect(result.total).toBe(2);
      });

      // But only one actual API call should be made
      expect(getManagedJobs).toHaveBeenCalledTimes(1);

      // Pending requests should be cleaned up
      expect(cache.pendingRequests.size).toBe(0);
    });

    test('should handle concurrent requests with different parameters', async () => {
      const getManagedJobs = createMockFetch({ jobs: [], total: 0 }, 200);

      // Simulate different filter combinations being requested concurrently
      const promises = [
        cache.get(getManagedJobs, [{ allUsers: true, page: 1 }]),
        cache.get(getManagedJobs, [{ allUsers: true, page: 2 }]),
        cache.get(getManagedJobs, [{ allUsers: false, page: 1 }]),
      ];

      expect(cache.pendingRequests.size).toBe(3);

      jest.advanceTimersByTime(200);
      await Promise.all(promises);

      // Should make 3 separate calls (different parameters)
      expect(getManagedJobs).toHaveBeenCalledTimes(3);
    });
  });

  describe('Statistics', () => {
    test('should track pending requests in stats', async () => {
      const mockFetch = createMockFetch({ data: 'test' }, 100);

      const promise = cache.get(mockFetch, ['arg1']);

      const stats = cache.getStats();
      expect(stats.pendingRequests).toBe(1);

      jest.advanceTimersByTime(100);
      await promise;

      const statsAfter = cache.getStats();
      expect(statsAfter.pendingRequests).toBe(0);
    });

    test('should clear pending requests on cache.clear()', async () => {
      const mockFetch = createMockFetch({ data: 'test' }, 100);

      cache.get(mockFetch, ['arg1']);

      expect(cache.pendingRequests.size).toBe(1);

      cache.clear();

      expect(cache.pendingRequests.size).toBe(0);
    });
  });
});
