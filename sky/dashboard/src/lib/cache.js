// Generalized caching mechanism for dashboard API calls
// This cache can be used across all pages to store and retrieve API responses

import { CACHE_CONFIG } from './config';

// Configurable cache TTL duration (in milliseconds)
// Default value configured in config.js but can be overridden per function or globally
const DEFAULT_CACHE_TTL = CACHE_CONFIG.DEFAULT_TTL;

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
   * @param {boolean} [options.isBackgroundRequest] - Whether this is a background request (default: false)
   * @returns {Promise} - The cached or fresh data
   */
  async get(fetchFunction, args = [], options = {}) {
    const ttl = options.ttl || DEFAULT_CACHE_TTL;
    const refreshOnAccess = options.refreshOnAccess === true; // Change default to false
    const isBackgroundRequest = options.isBackgroundRequest === true; // New option to identify background requests
    const key = this._generateKey(fetchFunction, args);
    const functionName = fetchFunction.name || 'anonymous';

    const cachedItem = this.cache.get(key);
    const now = Date.now();

    // If we have cached data and it's not stale, return it
    if (cachedItem && now - cachedItem.lastUpdated < ttl) {
      const age = Math.round((now - cachedItem.lastUpdated) / 1000);
      this._debug(
        `Cache HIT for ${functionName} (age: ${age}s, TTL: ${Math.round(ttl / 1000)}s)${isBackgroundRequest ? ' [BACKGROUND]' : ''}`
      );

      // Update the lastUpdated timestamp to extend the cache life on access
      if (refreshOnAccess && !isBackgroundRequest) {
        this.cache.set(key, {
          data: cachedItem.data,
          lastUpdated: now,
        });
        this._debug(`Cache TTL refreshed for ${functionName}`);
      }

      // Only trigger background refresh for foreground requests, not background ones
      // This prevents background jobs from triggering more background jobs
      if (!isBackgroundRequest) {
        // Launch background refresh only if cache is older than half TTL
        // This prevents too frequent background refreshes
        const halfTTL = ttl / 2;
        if (
          now - cachedItem.lastUpdated > halfTTL &&
          !this.backgroundJobs.has(key)
        ) {
          this._debug(
            `Triggering background refresh for ${functionName} (age > half TTL)`
          );
          this._refreshInBackground(fetchFunction, args, key);
        }
      }

      return cachedItem.data;
    }

    // If data is stale or doesn't exist, fetch fresh data
    // But if this is a background request and we have stale data, return stale data instead of fetching
    if (isBackgroundRequest && cachedItem) {
      this._debug(
        `Background request returning stale data for ${functionName}`
      );
      return cachedItem.data;
    }

    try {
      this._debug(
        `Cache MISS for ${functionName} - fetching fresh data${isBackgroundRequest ? ' [BACKGROUND]' : ''}`
      );
      const freshData = await fetchFunction(...args);

      // Update cache with fresh data
      this.cache.set(key, {
        data: freshData,
        lastUpdated: now,
      });

      this._debug(
        `Cache updated for ${functionName}${isBackgroundRequest ? ' [BACKGROUND]' : ''}`
      );
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
    // Check if we already have a background job running for this key
    if (this.backgroundJobs.has(key)) {
      this._debug(`Background job already running for ${key}, skipping`);
      return; // Don't start another background job if one is already running
    }

    // Mark that we have a background job running for this key
    this.backgroundJobs.set(key, true);
    this._debug(`Starting background refresh for ${key}`);

    // Add a small delay to let any ongoing foreground requests complete
    setTimeout(() => {
      // Execute the refresh asynchronously with background request flag
      this.get(fetchFunction, args, { isBackgroundRequest: true })
        .then((freshData) => {
          this._debug(`Background refresh completed for ${key}`);
        })
        .catch((error) => {
          console.warn(`Background refresh failed for ${key}:`, error);
        })
        .finally(() => {
          // Remove the background job marker
          this.backgroundJobs.delete(key);
          this._debug(`Background job finished for ${key}`);
        });
    }, 100); // 100ms delay to avoid immediate conflicts
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
