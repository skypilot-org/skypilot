// Generalized caching mechanism for dashboard API calls
// This cache can be used across all pages to store and retrieve API responses

import { CACHE_CONFIG } from './config';

// Configurable cache TTL duration (in milliseconds)
// Default value configured in config.js but can be overridden per function or globally
const DEFAULT_CACHE_TTL = CACHE_CONFIG.DEFAULT_TTL;

class DashboardCache {
  constructor() {
    this.cache = new Map();
    this.debugMode = false;
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

    // If we have cached data and it's not stale, return it
    if (cachedItem && now - cachedItem.lastUpdated < ttl) {
      const age = Math.round((now - cachedItem.lastUpdated) / 1000);
      this._debug(
        `Cache HIT for ${functionName} (age: ${age}s, TTL: ${Math.round(ttl / 1000)}s)`
      );
      return cachedItem.data;
    }

    // Data is stale or doesn't exist, fetch fresh data
    try {
      this._debug(`Cache MISS for ${functionName} - fetching fresh data`);
      const freshData = await fetchFunction(...args);

      // Update cache with fresh data
      this.cache.set(key, {
        data: freshData,
        lastUpdated: now,
      });

      this._debug(`Cache updated for ${functionName}`);
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
    this._debug(`Cache invalidated for ${key}`);
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
    });

    this._debug(
      `Cache invalidated for all ${functionName} entries (${keysToDelete.length} entries)`
    );
  }

  /**
   * Clear all cache entries
   */
  clear() {
    this.cache.clear();
    this._debug('All cache entries cleared');
  }

  /**
   * Get cache statistics for debugging
   */
  getStats() {
    return {
      cacheSize: this.cache.size,
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
      });
    }

    return {
      cacheSize: this.cache.size,
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
    const functionName = fetchFunction.name || 'anonymous';
    const argsHash = args.length > 0 ? JSON.stringify(args) : '';
    return `${functionName}_${argsHash}`;
  }
}

// Create a singleton instance to be shared across the application
const dashboardCache = new DashboardCache();

// Export both the class and the singleton instance
export { DashboardCache, dashboardCache };
export default dashboardCache;
