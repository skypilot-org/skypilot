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
    this.pendingRequests = new Map(); // Track in-flight requests to deduplicate concurrent calls
    this.generations = new Map(); // key -> generation, bumped on invalidation
    // Number of foreground/background fetches still running for a key. Unlike
    // the deduplication markers above (which invalidation may remove while a
    // fetch is still running), this survives until the fetch actually settles,
    // so generation bookkeeping is only dropped once nothing can complete
    // against a key.
    this.inFlight = new Map(); // key -> count of fetches still running
    this.debugMode = false; // Added for debug mode
    this.preloader = null; // Reference to cache preloader for coordination
  }

  /**
   * Set the cache preloader instance for coordination
   * @param {Object} preloader - The cache preloader instance
   */
  setPreloader(preloader) {
    this.preloader = preloader;
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
      // and if the data wasn't recently preloaded
      if (!this.backgroundJobs.has(key)) {
        const wasRecentlyPreloaded =
          this.preloader?.wasRecentlyPreloaded(fetchFunction, args) || false;
        if (!wasRecentlyPreloaded) {
          this._refreshInBackground(fetchFunction, args, key);
        } else {
          this._debug(
            `Skipping background refresh for ${functionName} - recently preloaded`
          );
        }
      }

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
      // Capture the generation at request start. If the key is invalidated
      // while this request is in flight, the response is stale and must not
      // repopulate the cache.
      const generationAtStart = this._getGeneration(key);
      this._incrementInFlight(key);
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

        // Update cache with fresh data, unless the key was invalidated while
        // this request was in flight (a newer fetch owns the key now).
        if (this._getGeneration(key) === generationAtStart) {
          this.cache.set(key, {
            data: freshData,
            lastUpdated: Date.now(),
          });
        } else {
          this._debug(
            `Dropping stale response for ${functionName} - key was invalidated`
          );
        }

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
        // Only clear the deduplication marker if it still belongs to this
        // request; an invalidation may have swapped in a newer request that is
        // still in flight and must keep its marker.
        if (this.pendingRequests.get(key) === requestPromise) {
          this.pendingRequests.delete(key);
        }
        this._decrementInFlight(key);
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
    this._bumpGeneration(key);
    this.cache.delete(key);
    // Also cancel any ongoing background job for this key
    this.backgroundJobs.delete(key);
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
    const keysToDelete = new Set();

    // Find all keys that start with the function hash, across the cache and
    // any in-flight background/pending requests (whose cache entry may
    // already be gone but whose response must still be dropped).
    for (const key of this.cache.keys()) {
      if (key.startsWith(`${functionHash}_`)) {
        keysToDelete.add(key);
      }
    }
    for (const key of this.backgroundJobs.keys()) {
      if (key.startsWith(`${functionHash}_`)) {
        keysToDelete.add(key);
      }
    }
    for (const key of this.pendingRequests.keys()) {
      if (key.startsWith(`${functionHash}_`)) {
        keysToDelete.add(key);
      }
    }
    for (const key of this.inFlight.keys()) {
      if (key.startsWith(`${functionHash}_`)) {
        keysToDelete.add(key);
      }
    }

    // Delete all matching entries
    keysToDelete.forEach((key) => {
      this._bumpGeneration(key);
      this.cache.delete(key);
      this.backgroundJobs.delete(key);
      this.pendingRequests.delete(key);
    });
  }

  /**
   * Clear all cache entries
   */
  clear() {
    // Bump the generation of every key with a fetch still in flight so a
    // request that completes after the clear cannot repopulate the cache.
    for (const key of this.inFlight.keys()) {
      this._bumpGeneration(key);
    }
    this.cache.clear();
    this.backgroundJobs.clear();
    this.pendingRequests.clear();
    // `inFlight` is intentionally left alone: those fetches are still running
    // and will decrement themselves on completion.
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
      backgroundJobs: this.backgroundJobs.size,
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
        hasBackgroundJob: this.backgroundJobs.has(key),
        hasPendingRequest: this.pendingRequests.has(key),
      });
    }

    return {
      cacheSize: this.cache.size,
      backgroundJobs: this.backgroundJobs.size,
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
   * Refresh data in the background without blocking the current request
   * @private
   */
  _refreshInBackground(fetchFunction, args, key) {
    // Mark that we have a background job running for this key
    const job = {};
    this.backgroundJobs.set(key, job);
    const generationAtStart = this._getGeneration(key);
    this._incrementInFlight(key);

    // Execute the refresh asynchronously
    fetchFunction(...args)
      .then((freshData) => {
        // Respect __skipCache signal from fetch function
        if (freshData && freshData.__skipCache) {
          return; // do not update cache
        }
        // If the key was invalidated while this background refresh was in
        // flight, the response is stale and must not repopulate the cache
        // (e.g. a history refresh started before a soft-delete would
        // otherwise resurrect the removed row).
        if (this._getGeneration(key) !== generationAtStart) {
          return;
        }
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
        // Remove the background job marker only if it still belongs to this
        // job; an invalidation may have started a newer refresh for the key.
        if (this.backgroundJobs.get(key) === job) {
          this.backgroundJobs.delete(key);
        }
        this._decrementInFlight(key);
      });
  }

  /**
   * The current invalidation generation for a key (0 when never invalidated).
   * @private
   */
  _getGeneration(key) {
    return this.generations.get(key) || 0;
  }

  /**
   * Bump a key's invalidation generation, so in-flight requests started
   * before this call are recognized as stale when they complete.
   *
   * Only a key with a request still in flight (pending or background) needs a
   * recorded generation: once nothing can complete against the key, the value
   * serves no purpose, and skipping it keeps `generations` from leaking one
   * entry per distinct key for the life of the cache instance.
   * @private
   */
  _bumpGeneration(key) {
    if (!this.inFlight.has(key)) {
      return;
    }
    this.generations.set(key, this._getGeneration(key) + 1);
  }

  /**
   * Record that a foreground or background fetch started for a key. Kept
   * separate from the deduplication markers (which invalidation may remove
   * while a fetch is still running) so the generation guard survives until the
   * fetch actually settles.
   * @private
   */
  _incrementInFlight(key) {
    this.inFlight.set(key, (this.inFlight.get(key) || 0) + 1);
  }

  /**
   * Record that a fetch settled for a key. Once no fetch is still running the
   * generation guard is no longer needed and can be dropped: any later fetch
   * starts from the implicit generation 0, and a later invalidation bumps it
   * again, so dropping the entry here cannot let a stale response repopulate
   * the cache.
   * @private
   */
  _decrementInFlight(key) {
    const remaining = (this.inFlight.get(key) || 0) - 1;
    if (remaining > 0) {
      this.inFlight.set(key, remaining);
      return;
    }
    this.inFlight.delete(key);
    this.generations.delete(key);
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
