// Generalized caching mechanism for dashboard API calls
// This cache can be used across all pages to store and retrieve API responses

import { CACHE_CONFIG } from './config';

// Configurable cache TTL duration (in milliseconds)
// Default value configured in config.js but can be overridden per function or globally
const DEFAULT_CACHE_TTL = CACHE_CONFIG.DEFAULT_TTL;

// Simple string hash function (djb2)
function simpleHash(str) {
  let hash = 5381;
  for (let i = 0; i < str.length; i++) {
    hash = (hash << 5) + hash + str.charCodeAt(i);
  }
  return hash >>> 0;
}

class DashboardCache {
  constructor() {
    this.cache = new Map();
    this.pendingRequests = new Map(); // Track in-flight requests to deduplicate concurrent calls
    this.debugMode = false; // Added for debug mode
  }

  /**
   * Get cached data or fetch fresh data
   * @param {Function} fetchFunction - The function to call to fetch data
   * @param {Array} [args=[]] - Arguments to pass to the fetch function
   * @param {Object} [options={}] - Cache options
   * @param {number} [options.ttl] - Time to live in milliseconds
   * @returns {Promise} - The cached or fresh data
   */
  async get(fetchFunction, args = [], options = {}) {
    const ttl = options.ttl || DEFAULT_CACHE_TTL;
    const key = this._generateKey(fetchFunction, args);
    const functionName = fetchFunction.name || 'anonymous';

    const cachedItem = this.cache.get(key);
    const now = Date.now();

    // A cache hit is read-only: its TTL is measured from the original fetch.
    if (cachedItem && now - cachedItem.lastUpdated < ttl) {
      const age = Math.round((now - cachedItem.lastUpdated) / 1000);
      this._debug(
        `Cache HIT for ${functionName} (age: ${age}s, TTL: ${Math.round(ttl / 1000)}s)`
      );

      return cachedItem.data;
    }

    // Check if there's already a pending request for this key
    // If so, wait for it to complete instead of making a duplicate request
    if (this.pendingRequests.has(key)) {
      this._debug(
        `Request deduplication: Waiting for pending request for ${functionName}`
      );
      return this.pendingRequests.get(key);
    }

    // If data is stale or doesn't exist, fetch fresh data
    // Create a promise for this request and store it
    const requestPromise = (async () => {
      try {
        const freshData = await fetchFunction(...args);

        // If the fetch function indicates the result should not be cached
        // (e.g., transient error fallback), then skip cache update and
        // return stale data if available.
        if (freshData && freshData.__skipCache) {
          this._debug(
            `Skip caching for ${functionName} due to __skipCache flag on result`
          );
          if (cachedItem) {
            return cachedItem.data;
          }
          return freshData;
        }

        // Update cache with fresh data
        this.cache.set(key, {
          data: freshData,
          lastUpdated: Date.now(),
        });

        return freshData;
      } catch (error) {
        // If fetch fails and we have stale data, return stale data
        if (cachedItem) {
          console.warn(
            `Failed to fetch fresh data for ${key}/${functionName}, returning stale data:`,
            error
          );
          return cachedItem.data;
        }

        // If no cached data and fetch fails, re-throw the error
        throw error;
      } finally {
        // Remove the pending request marker
        this.pendingRequests.delete(key);
      }
    })();

    // Store the promise so concurrent requests can reuse it
    this.pendingRequests.set(key, requestPromise);

    return requestPromise;
  }

  /**
   * Invalidate a specific cache entry
   * @param {Function} fetchFunction - The function used to generate the cache key
   * @param {Array} [args=[]] - Arguments used to generate the cache key
   */
  invalidate(fetchFunction, args = []) {
    const key = this._generateKey(fetchFunction, args);
    this.cache.delete(key);
    // Also remove any pending requests
    this.pendingRequests.delete(key);
  }

  /**
   * Invalidate all cache entries for a given function (regardless of arguments)
   * @param {Function} fetchFunction - The function to invalidate all entries for
   */
  invalidateFunction(fetchFunction) {
    const functionString = fetchFunction.toString();
    const functionHash = simpleHash(functionString);
    const keysToDelete = [];

    // Find all keys that start with the function hash
    for (const key of this.cache.keys()) {
      if (key.startsWith(`${functionHash}_`)) {
        keysToDelete.push(key);
      }
    }

    // Delete all matching entries
    keysToDelete.forEach((key) => {
      this.cache.delete(key);
      this.pendingRequests.delete(key);
    });
  }

  /**
   * Clear all cache entries
   */
  clear() {
    this.cache.clear();
    this.pendingRequests.clear();
  }

  /**
   * Synchronously return cached data without triggering a fetch.
   * Returns null on cache miss or stale data.
   * @param {Function} fetchFunction - The function used to generate the cache key
   * @param {Array} [args=[]] - Arguments used to generate the cache key
   * @param {Object} [options={}] - Options
   * @param {number} [options.ttl] - Time to live in milliseconds (default: DEFAULT_CACHE_TTL)
   * @returns {*|null} - The cached data or null
   */
  getCached(fetchFunction, args = [], options = {}) {
    const ttl = options.ttl || DEFAULT_CACHE_TTL;
    const key = this._generateKey(fetchFunction, args);
    const cachedItem = this.cache.get(key);
    if (cachedItem && Date.now() - cachedItem.lastUpdated < ttl) {
      return cachedItem.data;
    }
    return null;
  }

  /**
   * Get cache statistics for debugging
   */
  getStats() {
    return {
      cacheSize: this.cache.size,
      pendingRequests: this.pendingRequests.size,
      keys: Array.from(this.cache.keys()),
    };
  }

  /**
   * Get detailed cache information for debugging
   */
  getDetailedStats() {
    const now = Date.now();
    const entries = [];

    for (const [key, item] of this.cache.entries()) {
      const age = now - item.lastUpdated;
      entries.push({
        key,
        age: Math.round(age / 1000), // Age in seconds
        lastUpdated: new Date(item.lastUpdated).toISOString(),
        hasPendingRequest: this.pendingRequests.has(key),
      });
    }

    return {
      cacheSize: this.cache.size,
      pendingRequests: this.pendingRequests.size,
      entries: entries.sort((a, b) => a.age - b.age),
    };
  }

  /**
   * Enable or disable debug logging
   */
  setDebugMode(enabled) {
    this.debugMode = enabled;
  }

  /**
   * Log debug information if debug mode is enabled
   * @private
   */
  _debug(message, ...args) {
    if (this.debugMode) {
      console.log(`[DashboardCache] ${message}`, ...args);
    }
  }

  /**
   * Generate a cache key based on function name and arguments
   * @private
   */
  _generateKey(fetchFunction, args) {
    // The `fetchFunction.name` would be like `a`, `s`, `n`, etc. after exporting,
    // which is very likely to be conflict between different functions.
    // So we use the function string to generate the hash.
    const functionString = fetchFunction.toString();
    const functionHash = simpleHash(functionString);
    const argsHash = args.length > 0 ? JSON.stringify(args) : '';
    return `${functionHash}_${argsHash}`;
  }
}

// Create a singleton instance to be shared across the application
const dashboardCache = new DashboardCache();

// Export both the class and the singleton instance
export { DashboardCache, dashboardCache };
export default dashboardCache;
