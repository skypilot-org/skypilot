// Cache preloader utility for dashboard pages
// This utility manages background preloading of cache data to improve page switching performance

import dashboardCache from './cache';
import { getClusters } from '@/data/connectors/clusters';
import {
  getManagedJobs,
  getManagedJobsWithClientPagination,
} from '@/data/connectors/jobs';
import { getWorkspaces, getEnabledClouds } from '@/data/connectors/workspaces';
import { getUsers } from '@/data/connectors/users';
import { getVolumes } from '@/data/connectors/volumes';
import {
  getEnabledCloudsList,
  getWorkspaceContexts,
  getContextGPUData,
  getSlurmInfrastructure,
} from '@/data/connectors/infra';
import { getSSHNodePools } from '@/data/connectors/ssh-node-pools';

/**
 * Complete list of all dashboard cache functions organized by page
 */
export const DASHBOARD_CACHE_FUNCTIONS = {
  // Base functions used across multiple pages (no arguments)
  base: {
    getClusters: { fn: getClusters, args: [] },
    // For jobs page - uses client-side pagination wrapper
    getManagedJobs: {
      fn: getManagedJobsWithClientPagination,
      args: [{ allUsers: true }],
    },
    // For infra/users/workspaces pages - shared cache entry
    getManagedJobsForOtherPages: {
      fn: getManagedJobs,
      args: [{ allUsers: true, skipFinished: true }],
    },
    getWorkspaces: { fn: getWorkspaces, args: [] },
    getUsers: { fn: getUsers, args: [] },
    getEnabledCloudsList: {
      fn: getEnabledCloudsList,
      args: [],
    },
    getWorkspaceContexts: { fn: getWorkspaceContexts, args: [] },
    getSlurmInfrastructure: { fn: getSlurmInfrastructure, args: [] },
    getSSHNodePools: { fn: getSSHNodePools, args: [] },
    getVolumes: { fn: getVolumes, args: [] },
  },

  // Functions with arguments (require dynamic data)
  dynamic: {
    getEnabledClouds: { fn: getEnabledClouds, requiresWorkspaces: true },
    getContextGPUDataForAllContexts: {
      fn: getContextGPUData,
      requiresContexts: true,
    },
  },

  // Page-specific function requirements
  pages: {
    clusters: ['getClusters', 'getWorkspaces'],
    jobs: ['getManagedJobs', 'getClusters', 'getWorkspaces', 'getUsers'],
    infra: [
      // Empty - infra page uses progressive loading via fetchData()
      // All infra functions are background-preloaded from other pages
    ],
    workspaces: [
      'getWorkspaces',
      'getClusters',
      'getManagedJobsForOtherPages',
      'getEnabledClouds',
    ],
    users: ['getUsers', 'getClusters', 'getManagedJobsForOtherPages'],
    volumes: ['getVolumes'],
  },
};

/**
 * Cache preloader class that manages background cache population
 */
class CachePreloader {
  constructor() {
    this.isPreloading = false;
    this.preloadPromises = new Map();
    this.recentlyPreloaded = new Map(); // Track recently preloaded functions with timestamps
    this.PRELOAD_GRACE_PERIOD = 5000; // 5 seconds grace period
    this.pluginPages = new Map(); // Dynamically registered plugin page functions
  }

  /**
   * Register a plugin page with its fetch functions for background preloading
   * @param {string} pageName - The plugin page name (e.g., 'gpu-manager')
   * @param {Array<{fn: Function, args: Array}>} functions - Functions to preload
   */
  registerPluginPage(pageName, functions) {
    this.pluginPages.set(pageName, functions);
    console.log(
      `[CachePreloader] Registered plugin page: ${pageName} with ${functions.length} functions`
    );
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

    if (
      !DASHBOARD_CACHE_FUNCTIONS.pages[currentPage] &&
      !this.pluginPages.has(currentPage)
    ) {
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
    const requiredFunctions = DASHBOARD_CACHE_FUNCTIONS.pages[page] || [];
    const promises = [];

    // Also load plugin page functions if registered
    const pluginFunctions = this.pluginPages.get(page);
    if (pluginFunctions) {
      for (const { fn, args } of pluginFunctions) {
        if (force) {
          dashboardCache.invalidate(fn, args);
        }
        promises.push(
          dashboardCache.get(fn, args).then((result) => {
            this._markAsPreloaded(fn, args);
            return result;
          })
        );
      }
    }

    for (const functionName of requiredFunctions) {
      if (DASHBOARD_CACHE_FUNCTIONS.base[functionName]) {
        // Base function (no arguments)
        const { fn, args } = DASHBOARD_CACHE_FUNCTIONS.base[functionName];
        if (force) {
          dashboardCache.invalidate(fn, args);
        }
        promises.push(
          dashboardCache.get(fn, args).then((result) => {
            // Mark this function as recently preloaded
            this._markAsPreloaded(fn, args);
            return result;
          })
        );
      } else if (functionName === 'getEnabledClouds') {
        // Dynamic function that requires workspace data
        promises.push(this._loadEnabledCloudsForAllWorkspaces(force));
      } else if (functionName === 'getContextGPUDataForAllContexts') {
        // Dynamic function that requires context names first
        promises.push(this._loadContextGPUDataForAllContexts(force));
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
   * Load GPU data for all Kubernetes contexts
   * @private
   */
  async _loadContextGPUDataForAllContexts(force = false) {
    try {
      // First get context names
      if (force) {
        dashboardCache.invalidate(getWorkspaceContexts);
      }
      const contextsData = await dashboardCache.get(getWorkspaceContexts);

      if (!contextsData || !contextsData.allContextNames) {
        return;
      }

      // Filter to only K8s contexts (not SSH)
      const kubeContexts = contextsData.allContextNames.filter(
        (ctx) => ctx && !ctx.startsWith('ssh-')
      );

      // Load GPU data for each context in parallel
      const promises = kubeContexts.map((context) => {
        if (force) {
          dashboardCache.invalidate(getContextGPUData, [context]);
        }
        return dashboardCache.get(getContextGPUData, [context]);
      });

      await Promise.allSettled(promises);
    } catch (error) {
      console.error('[CachePreloader] Error loading context GPU data:', error);
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

    // Get functions already loaded for current page
    const currentPageFunctions = new Set(
      DASHBOARD_CACHE_FUNCTIONS.pages[currentPage]
    );

    // Get all unique functions needed by other pages, excluding current page functions
    const allOtherFunctions = new Set();
    Object.keys(DASHBOARD_CACHE_FUNCTIONS.pages)
      .filter((page) => page !== currentPage)
      .forEach((page) => {
        DASHBOARD_CACHE_FUNCTIONS.pages[page].forEach((functionName) => {
          if (!currentPageFunctions.has(functionName)) {
            allOtherFunctions.add(functionName);
          }
        });
      });

    // Always background-preload all infra data when NOT on infra page
    // (infra page uses progressive loading via fetchData, so we don't block it)
    if (currentPage !== 'infra') {
      // Base functions for infra
      allOtherFunctions.add('getClusters');
      allOtherFunctions.add('getManagedJobsForOtherPages');
      allOtherFunctions.add('getEnabledCloudsList');
      allOtherFunctions.add('getWorkspaceContexts');
      allOtherFunctions.add('getSlurmInfrastructure');
      allOtherFunctions.add('getSSHNodePools');
      // Dynamic function for K8s GPU data
      allOtherFunctions.add('getContextGPUDataForAllContexts');
    }

    console.log(
      `[CachePreloader] Background preloading ${allOtherFunctions.size} unique functions: ${Array.from(allOtherFunctions).join(', ')}`
    );

    // Load each unique function once
    const preloadPromises = Array.from(allOtherFunctions).map(
      async (functionName) => {
        try {
          if (DASHBOARD_CACHE_FUNCTIONS.base[functionName]) {
            // Base function (no arguments)
            const { fn, args } = DASHBOARD_CACHE_FUNCTIONS.base[functionName];
            await dashboardCache.get(fn, args);
            // Mark this function as recently preloaded
            this._markAsPreloaded(fn, args);
          } else if (functionName === 'getEnabledClouds') {
            // Dynamic function that requires workspace data
            await this._loadEnabledCloudsForAllWorkspaces(false);
          } else if (functionName === 'getContextGPUDataForAllContexts') {
            // Dynamic function that requires context names first
            await this._loadContextGPUDataForAllContexts(false);
          }
          console.log(
            `[CachePreloader] Background loaded function: ${functionName}`
          );
        } catch (error) {
          console.error(
            `[CachePreloader] Background load failed for function ${functionName}:`,
            error
          );
        }
      }
    );

    // Also preload registered plugin pages (except the current one)
    for (const [pageName, functions] of this.pluginPages) {
      if (pageName === currentPage) continue;
      for (const { fn, args } of functions) {
        preloadPromises.push(
          dashboardCache
            .get(fn, args)
            .then(() => {
              this._markAsPreloaded(fn, args);
              console.log(
                `[CachePreloader] Background loaded plugin function for: ${pageName}`
              );
            })
            .catch((error) => {
              console.error(
                `[CachePreloader] Background load failed for plugin page ${pageName}:`,
                error
              );
            })
        );
      }
    }

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
   * Check if a function was recently preloaded (within grace period)
   * @param {Function} fetchFunction - The function to check
   * @param {Array} [args=[]] - Arguments to check
   * @returns {boolean} - True if recently preloaded
   */
  wasRecentlyPreloaded(fetchFunction, args = []) {
    const key = this._generateKey(fetchFunction, args);
    const preloadTime = this.recentlyPreloaded.get(key);

    if (!preloadTime) {
      return false;
    }

    const now = Date.now();
    const isRecent = now - preloadTime < this.PRELOAD_GRACE_PERIOD;

    // Clean up expired entries
    if (!isRecent) {
      this.recentlyPreloaded.delete(key);
    }

    return isRecent;
  }

  /**
   * Mark a function as recently preloaded
   * @private
   */
  _markAsPreloaded(fetchFunction, args = []) {
    const key = this._generateKey(fetchFunction, args);
    this.recentlyPreloaded.set(key, Date.now());
  }

  /**
   * Generate a cache key based on function name and arguments (same as DashboardCache)
   * @private
   */
  _generateKey(fetchFunction, args) {
    // Use same key generation logic as DashboardCache
    const functionString = fetchFunction.toString();
    const functionHash = this._simpleHash(functionString);
    const argsHash = args.length > 0 ? JSON.stringify(args) : '';
    return `${functionHash}_${argsHash}`;
  }

  /**
   * Simple string hash function (same as DashboardCache)
   * @private
   */
  _simpleHash(str) {
    let hash = 5381;
    for (let i = 0; i < str.length; i++) {
      hash = (hash << 5) + hash + str.charCodeAt(i);
    }
    return hash >>> 0;
  }

  /**
   * Clear all cache and reset preloader state
   */
  clearCache() {
    dashboardCache.clear();
    this.isPreloading = false;
    this.preloadPromises.clear();
    this.recentlyPreloaded.clear();
    console.log('[CachePreloader] Cache cleared');
  }
}

// Create singleton instance
const cachePreloader = new CachePreloader();

// Set up coordination between cache and preloader
dashboardCache.setPreloader(cachePreloader);

export { CachePreloader, cachePreloader };
export default cachePreloader;
