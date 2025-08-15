// Smart cache manager for managed jobs with client-side pagination
// This manager optimizes job data fetching by caching full datasets and serving paginated views from cache

import dashboardCache from './cache';
import { getManagedJobs } from '@/data/connectors/jobs';

class JobsCacheManager {
  constructor() {
    this.fullDataCache = new Map(); // Cache for full datasets keyed by filter combination
    this.isLoading = new Map(); // Track ongoing requests to prevent duplicate fetches (full dataset only)
    this.prefetching = new Map(); // Track background prefetch jobs for full datasets
  }

  /**
   * Generate a cache key based on filter parameters (excluding pagination)
   */
  _generateFilterKey(filters) {
    const {
      allUsers = true,
      nameMatch,
      userMatch,
      workspaceMatch,
      poolMatch,
    } = filters;

    const keyParts = [
      `allUsers:${allUsers}`,
      nameMatch ? `name:${nameMatch}` : '',
      userMatch ? `user:${userMatch}` : '',
      workspaceMatch ? `workspace:${workspaceMatch}` : '',
      poolMatch ? `pool:${poolMatch}` : '',
    ].filter(Boolean);

    return keyParts.join('|') || 'default';
  }

  /**
   * Check if full dataset is cached and fresh
   * Returns object with status information
   */
  _getCacheStatus(filterKey) {
    const cached = this.fullDataCache.get(filterKey);
    const now = Date.now();
    const maxAge = 2 * 60 * 1000; // 2 minutes TTL (align with dashboardCache)

    if (!cached) {
      return {
        isCached: false,
        isFresh: false,
        age: 0,
        maxAge,
        hasData: false,
      };
    }

    const age = now - cached.timestamp;
    const isFresh = age < maxAge;

    return {
      isCached: true,
      isFresh,
      age,
      maxAge,
      hasData: cached.jobs && Array.isArray(cached.jobs),
      data: cached,
    };
  }

  /**
   * Get paginated jobs data with intelligent caching
   * - If full dataset cache is fresh: slice locally
   * - Otherwise: fetch only the requested page from server and prefetch full dataset in background
   * @param {Object} options - Query options including pagination
   * @returns {Promise<{jobs: Array, total: number, controllerStopped: boolean, fromCache: boolean, cacheStatus: string}>}
   */
  async getPaginatedJobs(options = {}) {
    const { page = 1, limit = 10, ...filterOptions } = options;

    const filterKey = this._generateFilterKey(filterOptions);

    try {
      // 1) Prefer serving from local full-data cache whenever present (even if stale)
      const localCacheStatus = this._getCacheStatus(filterKey);
      if (localCacheStatus.isCached && localCacheStatus.hasData) {
        const cachedData = localCacheStatus.data;
        const startIndex = (page - 1) * limit;
        const endIndex = startIndex + limit;
        const paginatedJobs = cachedData.jobs.slice(startIndex, endIndex);

        // Background refresh when stale or older than half TTL
        if (
          !this.prefetching.has(filterKey) &&
          (!localCacheStatus.isFresh ||
            localCacheStatus.age > localCacheStatus.maxAge / 2)
        ) {
          const prefetchPromise = this._loadFullDataset(
            filterOptions,
            filterKey
          )
            .catch(() => {})
            .finally(() => this.prefetching.delete(filterKey));
          this.prefetching.set(filterKey, prefetchPromise);
        }

        return {
          jobs: paginatedJobs,
          total: cachedData.total,
          controllerStopped: cachedData.controllerStopped,
          fromCache: true,
          cacheStatus: localCacheStatus.isFresh
            ? 'local_cache_hit'
            : 'local_cache_stale_hit',
        };
      }

      // 2) No local cache: fetch current page only to keep UI responsive
      const pageResponse = await dashboardCache.get(getManagedJobs, [
        {
          ...filterOptions,
          page,
          limit,
        },
      ]);

      const pageJobs = pageResponse?.jobs || [];
      const pageTotal =
        typeof pageResponse?.total === 'number'
          ? pageResponse.total
          : pageJobs.length;
      const controllerStopped = !!pageResponse?.controllerStopped;

      // 3) Kick off background prefetch of full dataset (now that we have no cache yet)
      if (!this.prefetching.has(filterKey)) {
        const prefetchPromise = this._loadFullDataset(filterOptions, filterKey)
          .catch((e) => {
            console.warn('Background prefetch of full jobs failed:', e);
          })
          .finally(() => {
            this.prefetching.delete(filterKey);
          });
        this.prefetching.set(filterKey, prefetchPromise);
      }

      return {
        jobs: pageJobs,
        total: pageTotal,
        controllerStopped,
        fromCache: false,
        cacheStatus: 'server_page_fetch',
      };
    } catch (error) {
      console.error('Error in getPaginatedJobs:', error);
      return {
        jobs: [],
        total: 0,
        controllerStopped: false,
        fromCache: false,
        cacheStatus: 'error',
      };
    }
  }

  /**
   * Load full dataset and cache it
   */
  async _loadFullDataset(filterOptions, filterKey) {
    // Fetch all data without pagination parameters (server returns full list)
    const fullDataResponse = await dashboardCache.get(getManagedJobs, [
      filterOptions,
    ]);

    if (fullDataResponse.controllerStopped || !fullDataResponse.jobs) {
      return fullDataResponse;
    }

    const fullData = {
      jobs: fullDataResponse.jobs,
      total: fullDataResponse.jobs.length,
      controllerStopped: false,
      timestamp: Date.now(),
    };

    // Cache the full dataset for fast client-side pagination
    this.fullDataCache.set(filterKey, fullData);

    return fullData;
  }

  /**
   * Check if data is being loaded for the given filters (full dataset)
   */
  isDataLoading(filterOptions = {}) {
    const filterKey = this._generateFilterKey(filterOptions);
    return this.isLoading.has(filterKey) || this.prefetching.has(filterKey);
  }

  /**
   * Check if data is cached and fresh for the given filters (full dataset)
   */
  isDataCached(filterOptions = {}) {
    const filterKey = this._generateFilterKey(filterOptions);
    const status = this._getCacheStatus(filterKey);
    return status.isCached && status.isFresh && status.hasData;
  }

  /**
   * Get cache status for debugging
   */
  getCacheStatus(filterOptions = {}) {
    const filterKey = this._generateFilterKey(filterOptions);
    return this._getCacheStatus(filterKey);
  }

  /**
   * Invalidate cache for specific filters or all cache
   */
  invalidateCache(filterOptions = null) {
    if (filterOptions) {
      const filterKey = this._generateFilterKey(filterOptions);
      this.fullDataCache.delete(filterKey);
      this.isLoading.delete(filterKey);
      this.prefetching.delete(filterKey);
    } else {
      // Clear all cache
      this.fullDataCache.clear();
      this.isLoading.clear();
      this.prefetching.clear();
    }

    // Also invalidate the underlying dashboard cache
    dashboardCache.invalidateFunction(getManagedJobs);
  }

  /**
   * Get cache statistics for debugging
   */
  getCacheStats() {
    const stats = {
      cachedFilters: Array.from(this.fullDataCache.keys()),
      loadingFilters: Array.from(this.isLoading.keys()),
      prefetchingFilters: Array.from(this.prefetching.keys()),
      cacheSize: this.fullDataCache.size,
      loadingCount: this.isLoading.size,
      prefetchingCount: this.prefetching.size,
    };

    // Add detailed status for each cached filter
    stats.detailedStatus = {};
    for (const [filterKey, cachedData] of this.fullDataCache.entries()) {
      const status = this._getCacheStatus(filterKey);
      stats.detailedStatus[filterKey] = {
        age: status.age,
        isFresh: status.isFresh,
        hasData: status.hasData,
        jobCount: cachedData.jobs ? cachedData.jobs.length : 0,
      };
    }

    return stats;
  }
}

// Export singleton instance
const jobsCacheManager = new JobsCacheManager();
export default jobsCacheManager;
