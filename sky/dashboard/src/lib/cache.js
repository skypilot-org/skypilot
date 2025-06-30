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
    this.backgroundJobs = new Map(); // Track ongoing background refresh jobs
    this.debugMode = false; // Added for debug mode
  }

  /**
   * Get cached data or fetch fresh data
   * @param {Function} fetchFunction - The function to call to fetch data
   * @param {Array} [args=[]] - Arguments to pass to the fetch function
   * @param {Object} [options={}] - Cache options
   * @param {number} [options.ttl] - Time to live in milliseconds
   * @param {boolean} [options.refreshOnAccess] - Whether to refresh TTL on cache access (default: true)
   * @returns {Promise} - The cached or fresh data
   */
  async get(fetchFunction, args = [], options = {}) {
    const ttl = options.ttl || DEFAULT_CACHE_TTL;
    const refreshOnAccess = options.refreshOnAccess !== false; // Default to true
    const key = this._generateKey(fetchFunction, args);
    const functionName = fetchFunction.name || 'anonymous';

    const cachedItem = this.cache.get(key);
    const now = Date.now();

    // If we have cached data and it's not stale, return it and refresh in background
    if (cachedItem && now - cachedItem.lastUpdated < ttl) {
      const age = Math.round((now - cachedItem.lastUpdated) / 1000);
      this._debug(
        `Cache HIT for ${functionName} (age: ${age}s, TTL: ${Math.round(ttl / 1000)}s)`
      );

      // Update the lastUpdated timestamp to extend the cache life on access
      if (refreshOnAccess) {
        this.cache.set(key, {
          data: cachedItem.data,
          lastUpdated: now,
        });
        this._debug(`Cache TTL refreshed for ${functionName}`);
      }

      // Launch background refresh if we're not already refreshing
      if (!this.backgroundJobs.has(key)) {
        this._refreshInBackground(fetchFunction, args, key);
      }

      return cachedItem.data;
    }

    // If data is stale or doesn't exist, fetch fresh data
    try {
      const freshData = await fetchFunction(...args);

      // Update cache with fresh data
      this.cache.set(key, {
        data: freshData,
        lastUpdated: now,
      });

      return freshData;
    } catch (error) {
      // If fetch fails and we have stale data, return stale data
      if (cachedItem) {
        console.warn(
          `Failed to fetch fresh data for ${key}, returning stale data:`,
          error
        );
        return cachedItem.data;
      }

      // If no cached data and fetch fails, re-throw the error
      throw error;
    }
  }

  /**
   * Invalidate a specific cache entry
   * @param {Function} fetchFunction - The function used to generate the cache key
   * @param {Array} [args=[]] - Arguments used to generate the cache key
   */
  invalidate(fetchFunction, args = []) {
    const key = this._generateKey(fetchFunction, args);
    this.cache.delete(key);
    // Also cancel any ongoing background job for this key
    this.backgroundJobs.delete(key);
  }

  /**
   * Invalidate all cache entries for a given function (regardless of arguments)
   * @param {Function} fetchFunction - The function to invalidate all entries for
   */
  invalidateFunction(fetchFunction) {
    const functionName = fetchFunction.name || 'anonymous';
    const keysToDelete = [];

    // Find all keys that start with the function name
    for (const key of this.cache.keys()) {
      if (key.startsWith(`${functionName}_`)) {
        keysToDelete.push(key);
      }
    }

    // Delete all matching entries
    keysToDelete.forEach((key) => {
      this.cache.delete(key);
      this.backgroundJobs.delete(key);
    });
  }

  /**
   * Clear all cache entries
   */
  clear() {
    this.cache.clear();
    this.backgroundJobs.clear();
  }

  /**
   * Get cache statistics for debugging
   */
  getStats() {
    return {
      cacheSize: this.cache.size,
      backgroundJobs: this.backgroundJobs.size,
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
        hasBackgroundJob: this.backgroundJobs.has(key),
      });
    }

    return {
      cacheSize: this.cache.size,
      backgroundJobs: this.backgroundJobs.size,
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
   * Refresh data in the background without blocking the current request
   * @private
   */
  _refreshInBackground(fetchFunction, args, key) {
    // Mark that we have a background job running for this key
    this.backgroundJobs.set(key, true);

    // Execute the refresh asynchronously
    fetchFunction(...args)
      .then((freshData) => {
        // Update cache with fresh data
        this.cache.set(key, {
          data: freshData,
          lastUpdated: Date.now(),
        });
      })
      .catch((error) => {
        console.warn(`Background refresh failed for ${key}:`, error);
      })
      .finally(() => {
        // Remove the background job marker
        this.backgroundJobs.delete(key);
      });
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
