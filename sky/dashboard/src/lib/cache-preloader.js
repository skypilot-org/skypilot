// Cache preloader utility for dashboard pages
// This utility manages background preloading of cache data to improve page switching performance

import dashboardCache from './cache';
import { getClusters } from '@/data/connectors/clusters';
import { getManagedJobs } from '@/data/connectors/jobs';
import { getWorkspaces, getEnabledClouds } from '@/data/connectors/workspaces';
import { getUsers, getUsersWithCounts } from '@/data/connectors/users';
import { getInfraData } from '@/data/connectors/infra';

/**
 * Complete list of all dashboard cache functions organized by page
 */
export const DASHBOARD_CACHE_FUNCTIONS = {
  // Base functions used across multiple pages (no arguments)
  base: {
    getClusters: { fn: getClusters, args: [] },
    getManagedJobs: { fn: getManagedJobs, args: [{ allUsers: true }] },
    getWorkspaces: { fn: getWorkspaces, args: [] },
    getUsers: { fn: getUsers, args: [] },
    getUsersWithCounts: { fn: getUsersWithCounts, args: [] },
    getInfraData: { fn: getInfraData, args: [] },
  },

  // Functions with arguments (require dynamic data)
  dynamic: {
    getEnabledClouds: { fn: getEnabledClouds, requiresWorkspaces: true },
  },

  // Page-specific function requirements
  pages: {
    clusters: ['getClusters', 'getWorkspaces'],
    jobs: ['getManagedJobs', 'getClusters', 'getWorkspaces'],
    infra: ['getInfraData'],
    workspaces: [
      'getWorkspaces',
      'getClusters',
      'getManagedJobs',
      'getEnabledClouds',
    ],
    users: ['getUsersWithCounts'],
  },
};

/**
 * Cache preloader class that manages background cache population
 */
class CachePreloader {
  constructor() {
    this.isPreloading = false;
    this.preloadPromises = new Map();
  }

  /**
   * Preload cache for a specific page and background-load other pages
   * @param {string} currentPage - The page being loaded ('clusters', 'jobs', 'infra', 'workspaces', 'users')
   * @param {Object} [options] - Preload options
   * @param {boolean} [options.backgroundPreload=true] - Whether to preload other pages in background
   * @param {boolean} [options.force=false] - Whether to force refresh even if cached
   */
  async preloadForPage(currentPage, options) {
    const { backgroundPreload = true, force = false } = options || {};

    if (!DASHBOARD_CACHE_FUNCTIONS.pages[currentPage]) {
      console.warn(`Unknown page: ${currentPage}`);
      return;
    }

    console.log(`[CachePreloader] Preloading cache for page: ${currentPage}`);

    try {
      // 1. Load current page data first (foreground)
      await this._loadPageData(currentPage, force);

      // 2. Background preload other pages if enabled
      if (backgroundPreload) {
        this._backgroundPreloadOtherPages(currentPage);
      }
    } catch (error) {
      console.error(
        `[CachePreloader] Error preloading for page ${currentPage}:`,
        error
      );
    }
  }

  /**
   * Load data for a specific page
   * @private
   */
  async _loadPageData(page, force = false) {
    const requiredFunctions = DASHBOARD_CACHE_FUNCTIONS.pages[page];
    const promises = [];

    for (const functionName of requiredFunctions) {
      if (DASHBOARD_CACHE_FUNCTIONS.base[functionName]) {
        // Base function (no arguments)
        const { fn, args } = DASHBOARD_CACHE_FUNCTIONS.base[functionName];
        if (force) {
          dashboardCache.invalidate(fn, args);
        }
        promises.push(dashboardCache.get(fn, args));
      } else if (functionName === 'getEnabledClouds') {
        // Dynamic function that requires workspace data
        promises.push(this._loadEnabledCloudsForAllWorkspaces(force));
      }
    }

    await Promise.allSettled(promises);
    console.log(`[CachePreloader] Loaded data for page: ${page}`);
  }

  /**
   * Load enabled clouds for all workspaces
   * @private
   */
  async _loadEnabledCloudsForAllWorkspaces(force = false) {
    try {
      // First get workspaces
      if (force) {
        dashboardCache.invalidate(getWorkspaces);
      }
      const workspacesData = await dashboardCache.get(getWorkspaces);
      const workspaceNames = Object.keys(workspacesData || {});

      // Then load enabled clouds for each workspace
      const promises = workspaceNames.map((wsName) => {
        if (force) {
          dashboardCache.invalidate(getEnabledClouds, [wsName]);
        }
        return dashboardCache.get(getEnabledClouds, [wsName]);
      });

      await Promise.allSettled(promises);
    } catch (error) {
      console.error('[CachePreloader] Error loading enabled clouds:', error);
    }
  }

  /**
   * Background preload other pages
   * @private
   */
  _backgroundPreloadOtherPages(currentPage) {
    if (this.isPreloading) {
      return; // Already preloading
    }

    this.isPreloading = true;

    // Get all pages except current
    const otherPages = Object.keys(DASHBOARD_CACHE_FUNCTIONS.pages).filter(
      (page) => page !== currentPage
    );

    console.log(
      `[CachePreloader] Background preloading pages: ${otherPages.join(', ')}`
    );

    // Preload all pages immediately in parallel
    const preloadPromises = otherPages.map(async (page) => {
      try {
        await this._loadPageData(page, false);
        console.log(`[CachePreloader] Background loaded: ${page}`);
      } catch (error) {
        console.error(
          `[CachePreloader] Background load failed for ${page}:`,
          error
        );
      }
    });

    // Wait for all preloading to complete
    Promise.allSettled(preloadPromises).then(() => {
      this.isPreloading = false;
      console.log('[CachePreloader] Background preloading complete');
    });
  }

  /**
   * Preload all base functions (useful for initial app load)
   */
  async preloadBaseFunctions(force = false) {
    console.log('[CachePreloader] Preloading all base functions');

    const promises = Object.entries(DASHBOARD_CACHE_FUNCTIONS.base).map(
      ([name, { fn, args }]) => {
        if (force) {
          dashboardCache.invalidate(fn, args);
        }
        return dashboardCache.get(fn, args).catch((error) => {
          console.error(`[CachePreloader] Failed to preload ${name}:`, error);
        });
      }
    );

    await Promise.allSettled(promises);
    console.log('[CachePreloader] Base functions preloaded');
  }

  /**
   * Get cache statistics for monitoring
   */
  getCacheStats() {
    return {
      ...dashboardCache.getStats(),
      isPreloading: this.isPreloading,
    };
  }

  /**
   * Clear all cache and reset preloader state
   */
  clearCache() {
    dashboardCache.clear();
    this.isPreloading = false;
    this.preloadPromises.clear();
    console.log('[CachePreloader] Cache cleared');
  }
}

// Create singleton instance
const cachePreloader = new CachePreloader();

export { CachePreloader, cachePreloader };
export default cachePreloader;
