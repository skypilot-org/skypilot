// Smart cache manager for managed jobs with pagination support
// This manager handles both default path (fetch all, paginate client-side)
// and plugin path (fetch exact page from server)

import dashboardCache from './cache';
import { getManagedJobs } from '@/data/connectors/jobs';

// Check if the jobs pagination plugin is available
function isPluginAvailable() {
  return (
    typeof window !== 'undefined' &&
    typeof window.__skyJobsPaginationFetch === 'function'
  );
}

// Get the plugin fetch function
function getPluginFetch() {
  return typeof window !== 'undefined' ? window.__skyJobsPaginationFetch : null;
}

class JobsCacheManager {
  constructor() {
    // Cache for paginated data, keyed by full options (including page, limit, sort)
    this.pageCache = new Map();
    // Cache for full datasets (default path only), keyed by filter combination
    this.fullDataCache = new Map();
    // Track ongoing requests to prevent duplicate fetches
    this.pendingRequests = new Map();
    // Track background prefetch jobs for full datasets
    this.backgroundPrefetching = new Map();
    // TTL for cache entries (2 minutes)
    this.cacheTTL = 2 * 60 * 1000;
  }

  /**
   * Generate a cache key that includes ALL parameters (page, limit, sort, filters)
   * This is the primary cache key for paginated results
   */
  _generateCacheKey(options) {
    const {
      page = 1,
      limit = 10,
      sortBy = 'submitted_at',
      sortOrder = 'desc',
      allUsers = true,
      nameMatch,
      userMatch,
      workspaceMatch,
      poolMatch,
      statuses,
    } = options;

    // Create a normalized object for consistent JSON stringification
    const keyObj = {
      page,
      limit,
      sortBy,
      sortOrder,
      allUsers,
      nameMatch: nameMatch || null,
      userMatch: userMatch || null,
      workspaceMatch: workspaceMatch || null,
      poolMatch: poolMatch || null,
      statuses: statuses && statuses.length > 0 ? [...statuses].sort() : null,
    };

    return JSON.stringify(keyObj);
  }

  /**
   * Generate a filter-only key (excludes pagination/sorting)
   * Used for full dataset caching in default path
   */
  _generateFilterKey(options) {
    const {
      allUsers = true,
      nameMatch,
      userMatch,
      workspaceMatch,
      poolMatch,
      statuses,
    } = options;

    const keyObj = {
      allUsers,
      nameMatch: nameMatch || null,
      userMatch: userMatch || null,
      workspaceMatch: workspaceMatch || null,
      poolMatch: poolMatch || null,
      statuses: statuses && statuses.length > 0 ? [...statuses].sort() : null,
    };

    return JSON.stringify(keyObj);
  }

  /**
   * Check if a cache entry is valid (exists and not expired)
   */
  _isCacheValid(cacheEntry) {
    if (!cacheEntry) return false;
    const age = Date.now() - cacheEntry.timestamp;
    return age < this.cacheTTL;
  }

  /**
   * Group tasks by job_id and return unique job IDs in order
   */
  _groupTasksByJob(tasks) {
    const jobMap = new Map();
    const jobOrder = [];

    for (const task of tasks) {
      const jobId = task.id;
      if (!jobMap.has(jobId)) {
        jobMap.set(jobId, []);
        jobOrder.push(jobId);
      }
      jobMap.get(jobId).push(task);
    }

    return { jobMap, jobOrder };
  }

  /**
   * Default path: Fetch a single page immediately for fast UI response
   * This fetches only the requested page, not the full dataset
   */
  async _populateCacheDefaultPathSinglePage(options) {
    const {
      page = 1,
      limit = 10,
      sortBy,
      sortOrder,
      ...filterOptions
    } = options;
    const cacheKey = this._generateCacheKey(options);

    console.log('[JobsCacheManager] Default path: fetching single page', page);

    // Fetch the specific page using getManagedJobs with pagination params
    const pageResponse = await dashboardCache.get(getManagedJobs, [
      {
        ...filterOptions,
        page,
        limit,
      },
    ]);

    if (pageResponse.__skipCache) {
      return pageResponse;
    }

    if (pageResponse.controllerStopped) {
      return {
        jobs: [],
        total: 0,
        totalNoFilter: 0,
        totalPages: 0,
        hasNext: false,
        hasPrev: false,
        controllerStopped: true,
        statusCounts: {},
      };
    }

    const jobs = pageResponse.jobs || [];
    const total =
      typeof pageResponse.total === 'number' ? pageResponse.total : jobs.length;
    const totalNoFilter = pageResponse.totalNoFilter || total;
    const totalPages = Math.ceil(total / limit) || 1;
    const hasNext = page < totalPages;
    const hasPrev = page > 1;
    const statusCounts = pageResponse.statusCounts || {};

    // Cache this single page
    this.pageCache.set(cacheKey, {
      jobs,
      total,
      totalNoFilter,
      totalPages,
      hasNext,
      hasPrev,
      controllerStopped: false,
      statusCounts,
      timestamp: Date.now(),
    });

    return {
      jobs,
      total,
      totalNoFilter,
      totalPages,
      hasNext,
      hasPrev,
      controllerStopped: false,
      statusCounts,
      fromCache: false,
      cacheStatus: 'default_path_single_page',
    };
  }

  /**
   * Default path background prefetch: Fetch ALL jobs and populate cache for all pages
   * This is called in the background after the initial page load
   */
  async _loadFullDatasetAndCacheAllPages(options) {
    const {
      page = 1,
      limit = 10,
      sortBy,
      sortOrder,
      ...filterOptions
    } = options;
    const filterKey = this._generateFilterKey(filterOptions);

    console.log('[JobsCacheManager] Background: fetching full dataset');

    // Fetch all jobs without pagination
    const fullDataResponse = await dashboardCache.get(getManagedJobs, [
      filterOptions,
    ]);

    if (fullDataResponse.__skipCache || fullDataResponse.controllerStopped) {
      return;
    }

    const allJobs = fullDataResponse.jobs || [];
    const { jobMap, jobOrder } = this._groupTasksByJob(allJobs);
    const totalJobs = jobOrder.length;

    // Store the full dataset for future page calculations
    this.fullDataCache.set(filterKey, {
      jobs: allJobs,
      jobMap,
      jobOrder,
      totalJobs,
      totalNoFilter: fullDataResponse.totalNoFilter || totalJobs,
      statusCounts: fullDataResponse.statusCounts || {},
      timestamp: Date.now(),
    });

    // Calculate and cache all pages
    const totalPages = Math.ceil(totalJobs / limit) || 1;
    for (let p = 1; p <= totalPages; p++) {
      const startIdx = (p - 1) * limit;
      const endIdx = startIdx + limit;
      const pageJobIds = jobOrder.slice(startIdx, endIdx);
      const pageJobs = [];
      for (const jobId of pageJobIds) {
        pageJobs.push(...jobMap.get(jobId));
      }

      const pageCacheKey = this._generateCacheKey({
        ...filterOptions,
        page: p,
        limit,
        sortBy,
        sortOrder,
      });

      this.pageCache.set(pageCacheKey, {
        jobs: pageJobs,
        total: totalJobs,
        totalNoFilter: fullDataResponse.totalNoFilter || totalJobs,
        totalPages,
        hasNext: p < totalPages,
        hasPrev: p > 1,
        controllerStopped: false,
        statusCounts: fullDataResponse.statusCounts || {},
        timestamp: Date.now(),
      });
    }

    console.log('[JobsCacheManager] Background: cached', totalPages, 'pages');
  }

  /**
   * Plugin path: Fetch only the exact page needed from the server
   * Uses server-side filtering and pagination
   */
  async _populateCachePluginPath(options) {
    const {
      page = 1,
      limit = 10,
      sortBy = 'submitted_at',
      sortOrder = 'desc',
      statuses,
      ...filterOptions
    } = options;
    const cacheKey = this._generateCacheKey(options);

    console.log('[JobsCacheManager] Plugin path: fetching page', page);

    const pluginFetch = getPluginFetch();
    if (!pluginFetch) {
      throw new Error('Plugin fetch function not available');
    }

    // Call the plugin fetch function with all options
    const result = await pluginFetch({
      page,
      limit,
      sortBy,
      sortOrder,
      statuses,
      filters: this._convertFiltersToPluginFormat(filterOptions),
    });

    // Handle controller stopped
    if (result.controllerStopped) {
      return {
        jobs: [],
        total: 0,
        totalNoFilter: 0,
        totalPages: 0,
        hasNext: false,
        hasPrev: false,
        controllerStopped: true,
        statusCounts: {},
        fromCache: false,
        cacheStatus: 'plugin_path_controller_stopped',
      };
    }

    const jobs = result.items || result.data || [];
    const total = result.total || 0;
    const totalNoFilter = result.totalNoFilter || total;
    const totalPages =
      result.totalPages || result.total_pages || Math.ceil(total / limit) || 1;
    const hasNext = result.hasNext || result.has_next || page < totalPages;
    const hasPrev = result.hasPrev || result.has_prev || page > 1;
    const statusCounts = result.statusCounts || {};

    // Cache this specific page
    this.pageCache.set(cacheKey, {
      jobs,
      total,
      totalNoFilter,
      totalPages,
      hasNext,
      hasPrev,
      controllerStopped: false,
      statusCounts,
      timestamp: Date.now(),
    });

    return {
      jobs,
      total,
      totalNoFilter,
      totalPages,
      hasNext,
      hasPrev,
      controllerStopped: false,
      statusCounts,
      fromCache: false,
      cacheStatus: 'plugin_path_fetched',
    };
  }

  /**
   * Convert filter options to the format expected by the plugin
   */
  _convertFiltersToPluginFormat(filterOptions) {
    const filters = [];

    if (filterOptions.nameMatch) {
      filters.push({ property: 'name', value: filterOptions.nameMatch });
    }
    if (filterOptions.userMatch) {
      filters.push({ property: 'user', value: filterOptions.userMatch });
    }
    if (filterOptions.workspaceMatch) {
      filters.push({
        property: 'workspace',
        value: filterOptions.workspaceMatch,
      });
    }
    if (filterOptions.poolMatch) {
      filters.push({ property: 'pool', value: filterOptions.poolMatch });
    }

    return filters;
  }

  /**
   * Get paginated jobs data with intelligent caching
   * - Checks cache first (keyed by all parameters including page, limit, sort)
   * - On cache miss, uses plugin path if available, otherwise default path
   * - Default path: fetches single page immediately, then prefetches full dataset in background
   *
   * @param {Object} options - Query options including pagination
   * @returns {Promise<{jobs: Array, total: number, totalNoFilter: number, totalPages: number, hasNext: boolean, hasPrev: boolean, controllerStopped: boolean, statusCounts: Object, fromCache: boolean, cacheStatus: string}>}
   */
  async getPaginatedJobs(options = {}) {
    const { page = 1, limit = 10 } = options;
    const cacheKey = this._generateCacheKey(options);
    const filterKey = this._generateFilterKey(options);

    console.log('[JobsCacheManager] getPaginatedJobs called:', {
      page,
      limit,
      cacheKey: cacheKey.substring(0, 50) + '...',
    });

    try {
      // 1) Check if we have a valid cached page
      const cachedPage = this.pageCache.get(cacheKey);
      if (cachedPage && this._isCacheValid(cachedPage)) {
        console.log('[JobsCacheManager] Cache hit for page', page);
        return {
          jobs: cachedPage.jobs,
          total: cachedPage.total,
          totalNoFilter: cachedPage.totalNoFilter,
          totalPages: cachedPage.totalPages,
          hasNext: cachedPage.hasNext,
          hasPrev: cachedPage.hasPrev,
          controllerStopped: cachedPage.controllerStopped,
          statusCounts: cachedPage.statusCounts,
          fromCache: true,
          cacheStatus: 'cache_hit',
        };
      }

      // 2) Check if there's already a pending request for this exact key
      if (this.pendingRequests.has(cacheKey)) {
        console.log('[JobsCacheManager] Waiting for pending request');
        return await this.pendingRequests.get(cacheKey);
      }

      // 3) Cache miss - populate based on available path
      let populatePromise;
      if (isPluginAvailable()) {
        // Plugin path: fetch exact page from server
        populatePromise = this._populateCachePluginPath(options);
      } else {
        // Default path: fetch single page immediately for responsiveness
        populatePromise = this._populateCacheDefaultPathSinglePage(options);
      }

      // Track the pending request
      this.pendingRequests.set(cacheKey, populatePromise);

      try {
        const result = await populatePromise;

        // For default path, kick off background prefetch of full dataset
        if (
          !isPluginAvailable() &&
          !this.backgroundPrefetching.has(filterKey)
        ) {
          this._kickOffBackgroundPrefetch(options, filterKey);
        }

        return result;
      } finally {
        this.pendingRequests.delete(cacheKey);
      }
    } catch (error) {
      console.error('[JobsCacheManager] Error in getPaginatedJobs:', error);
      throw error;
    }
  }

  /**
   * Kick off background prefetch of full dataset (default path only)
   */
  _kickOffBackgroundPrefetch(options, filterKey) {
    console.log('[JobsCacheManager] Kicking off background prefetch');

    const prefetchPromise = this._loadFullDatasetAndCacheAllPages(options)
      .catch((err) => {
        console.warn('[JobsCacheManager] Background prefetch failed:', err);
      })
      .finally(() => {
        this.backgroundPrefetching.delete(filterKey);
      });

    this.backgroundPrefetching.set(filterKey, prefetchPromise);
  }

  /**
   * Prefetch the next page in the background
   * Call this after successfully loading a page to warm the cache
   */
  async prefetchNextPage(options = {}) {
    const { page = 1, limit = 10 } = options;
    const nextPage = page + 1;
    const nextOptions = { ...options, page: nextPage };
    const nextCacheKey = this._generateCacheKey(nextOptions);

    // Don't prefetch if already cached or pending
    if (
      this.pageCache.has(nextCacheKey) ||
      this.pendingRequests.has(nextCacheKey)
    ) {
      return;
    }

    console.log('[JobsCacheManager] Prefetching page', nextPage);

    try {
      // For plugin path, prefetch the specific page
      if (isPluginAvailable()) {
        await this._populateCachePluginPath(nextOptions);
      }
      // For default path, all pages are already cached when we load full data
    } catch (error) {
      console.warn('[JobsCacheManager] Prefetch failed:', error);
    }
  }

  /**
   * Check if data is being loaded for the given options
   */
  isDataLoading(options = {}) {
    const cacheKey = this._generateCacheKey(options);
    return this.pendingRequests.has(cacheKey);
  }

  /**
   * Check if data is cached and fresh for the given options
   */
  isDataCached(options = {}) {
    const cacheKey = this._generateCacheKey(options);
    const cached = this.pageCache.get(cacheKey);
    return cached && this._isCacheValid(cached);
  }

  /**
   * Get cache status for debugging
   */
  getCacheStatus(options = {}) {
    const cacheKey = this._generateCacheKey(options);
    const cached = this.pageCache.get(cacheKey);

    if (!cached) {
      return { isCached: false, isFresh: false, age: 0 };
    }

    const age = Date.now() - cached.timestamp;
    return {
      isCached: true,
      isFresh: age < this.cacheTTL,
      age,
      maxAge: this.cacheTTL,
    };
  }

  /**
   * Invalidate cache for specific options or all cache
   */
  invalidateCache(options = null) {
    if (options) {
      // Invalidate specific cache key
      const cacheKey = this._generateCacheKey(options);
      this.pageCache.delete(cacheKey);

      // Also invalidate related filter key for full data cache and background prefetch
      const filterKey = this._generateFilterKey(options);
      this.fullDataCache.delete(filterKey);
      this.backgroundPrefetching.delete(filterKey);
    } else {
      // Clear all cache
      this.pageCache.clear();
      this.fullDataCache.clear();
      this.pendingRequests.clear();
      this.backgroundPrefetching.clear();
    }

    // Also invalidate the underlying dashboard cache
    dashboardCache.invalidateFunction(getManagedJobs);
  }

  /**
   * Invalidate all pages for a given filter combination
   * Useful when filters change but we want to keep other cached data
   */
  invalidateFilteredPages(filterOptions) {
    const filterKey = this._generateFilterKey(filterOptions);

    // Remove all page cache entries that match this filter
    for (const [key] of this.pageCache.entries()) {
      try {
        const keyObj = JSON.parse(key);
        const keyFilterObj = {
          allUsers: keyObj.allUsers,
          nameMatch: keyObj.nameMatch,
          userMatch: keyObj.userMatch,
          workspaceMatch: keyObj.workspaceMatch,
          poolMatch: keyObj.poolMatch,
          statuses: keyObj.statuses,
        };
        if (JSON.stringify(keyFilterObj) === filterKey) {
          this.pageCache.delete(key);
        }
      } catch (e) {
        // Skip malformed keys
      }
    }

    // Also remove full data cache for this filter
    this.fullDataCache.delete(filterKey);
  }

  /**
   * Get cache statistics for debugging
   */
  getCacheStats() {
    return {
      pageCacheSize: this.pageCache.size,
      fullDataCacheSize: this.fullDataCache.size,
      pendingRequestsCount: this.pendingRequests.size,
      backgroundPrefetchingCount: this.backgroundPrefetching.size,
      isPluginAvailable: isPluginAvailable(),
      cachedKeys: Array.from(this.pageCache.keys()).map((k) => {
        try {
          const obj = JSON.parse(k);
          return `page:${obj.page},limit:${obj.limit}`;
        } catch {
          return k.substring(0, 30);
        }
      }),
    };
  }
}

// Export singleton instance
const jobsCacheManager = new JobsCacheManager();
export default jobsCacheManager;

// Also export the class for testing
export { JobsCacheManager };
