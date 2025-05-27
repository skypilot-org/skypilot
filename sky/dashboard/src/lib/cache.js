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

    // If we have cached data and it's not stale, return it and refresh in background
    if (cachedItem && (now - cachedItem.lastUpdated) < ttl) {
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
        lastUpdated: now
      });

      return freshData;
    } catch (error) {
      // If fetch fails and we have stale data, return stale data
      if (cachedItem) {
        console.warn(`Failed to fetch fresh data for ${key}, returning stale data:`, error);
        return cachedItem.data;
      }
      
      // If no cached data and fetch fails, re-throw the error
      throw error;
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
      .then(freshData => {
        // Update cache with fresh data
        this.cache.set(key, {
          data: freshData,
          lastUpdated: Date.now()
        });
      })
      .catch(error => {
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