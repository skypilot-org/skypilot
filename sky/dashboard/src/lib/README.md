# Dashboard Cache System

This directory contains the generalized caching mechanism for the SkyPilot dashboard. The cache system is designed to improve performance by storing API responses and serving them from memory when they're still fresh.

## Files

- `cache.js` - The main cache implementation
- `cache-preloader.js` - Smart cache preloading utility for background data loading
- `config.js` - Configuration settings for cache TTLs and other dashboard settings

## Cache Preloader

The cache preloader is a smart utility that automatically populates the cache for all dashboard pages when any page is visited. This dramatically improves page switching performance by ensuring data is already cached when users navigate between pages.

### How It Works

1. **Foreground Loading**: When a page loads, it immediately fetches the data required for that specific page
2. **Background Preloading**: After the current page data is loaded, it automatically starts loading data for all other pages in parallel
3. **Immediate Parallel Loading**: All background pages load simultaneously without delays for maximum speed
4. **Shared Cache**: All pages benefit from the same cache, so data loaded for one page is immediately available to others

### Dashboard Cache Functions

The preloader manages these functions across all pages:

**Base Functions (no arguments):**

- `getClusters` - Used by: clusters, jobs, infra, workspaces, users
- `getManagedJobs` - Used by: jobs, infra, workspaces, users
- `getWorkspaces` - Used by: clusters, jobs, workspaces
- `getUsers` - Used by: users
- `getVolumes` - Used by: volumes

**Dynamic Functions (with arguments):**

- `getEnabledClouds(workspaceName)` - Used by: workspaces
- `getInfraData` - Special composite function for infra page (GPU + Cloud data)

**Page Requirements:**

- **Clusters**: getClusters, getWorkspaces
- **Jobs**: getManagedJobs, getClusters, getWorkspaces
- **Infra**: getClusters, getManagedJobs, getInfraData (composite: GPU + Cloud data)
- **Workspaces**: getWorkspaces, getClusters, getManagedJobs, getEnabledClouds
- **Users**: getUsers, getClusters, getManagedJobs
- **Volumes**: getVolumes

### Usage

```javascript
import cachePreloader from '@/lib/cache-preloader';

// Preload for current page and background-load others
await cachePreloader.preloadForPage('clusters');

// Preload with options
await cachePreloader.preloadForPage('jobs', {
  backgroundPreload: true, // Enable background preloading (default: true)
  force: false, // Force refresh even if cached (default: false)
});

// Preload only base functions (useful for app initialization)
await cachePreloader.preloadBaseFunctions();

// Get preloader statistics
const stats = cachePreloader.getCacheStats();
console.log('Cache size:', stats.cacheSize);
console.log('Is preloading:', stats.isPreloading);

// Clear all cache
cachePreloader.clearCache();
```

### Integration in Pages

Each dashboard page automatically triggers preloading:

```javascript
// In page useEffect
useEffect(() => {
  const initializeData = async () => {
    // Trigger cache preloading for current page and background preload others
    await cachePreloader.preloadForPage('clusters');

    // Continue with page-specific data loading
    fetchData(true);
  };

  initializeData();
}, []);
```

### Performance Benefits

- **Instant Page Switching**: Subsequent page loads are nearly instantaneous
- **Parallel Loading**: All pages load simultaneously in the background for maximum speed
- **Smart Caching**: Only loads fresh data when needed, reuses cached data otherwise
- **Graceful Degradation**: If preloading fails, pages still work normally

## Timeline Example

Let's say you visit the **clusters** page:

```
Time 0ms:    User visits /clusters
Time 10ms:   cachePreloader.preloadForPage('clusters') called
Time 50ms:   getClusters() and getWorkspaces() loaded (foreground)
Time 100ms:  Clusters page renders with data
Time 100ms:  Background loading starts for ALL other pages simultaneously
Time 200ms:  'jobs' page data loaded (getManagedJobs, getClusters, getWorkspaces)
Time 250ms:  'infra' page data loaded (getClusters, getManagedJobs)
Time 400ms:  'users' page data loaded (getUsers, getClusters, getManagedJobs)
Time 600ms:  'workspaces' page data loaded (including getEnabledClouds for all workspaces)
Time 600ms:  All pages now cached and ready for instant switching!
```

**Result**: By the time you would naturally switch to another page (typically 1-2 seconds), all pages are already loaded and cached, making navigation feel instant.

## How to Use

### Basic Usage

```javascript
import dashboardCache from '@/lib/cache';
import { getClustersAndJobsData } from '@/data/connectors/infra';

// Simple usage with default TTL (5 minutes)
const data = await dashboardCache.get(getClustersAndJobsData);

// With custom TTL (2 minutes)
const data = await dashboardCache.get(getClustersAndJobsData, [], {
  ttl: 2 * 60 * 1000,
});

// With function arguments
const data = await dashboardCache.get(getGPUs, [clustersAndJobsData], {
  ttl: CACHE_CONFIG.GPU_DATA_TTL,
});
```

### Configuration

The cache system supports configurable TTL values defined in `config.js`. Currently, all cache entries use the `DEFAULT_TTL` of 2 minutes, but the system can be extended to support different TTLs for different data types if needed.

```javascript
// Current configuration
export const CACHE_CONFIG = {
  DEFAULT_TTL: 2 * 60 * 1000, // 2 minutes
};

// Example of how different TTLs could be configured:
// CLUSTERS_TTL: 5 * 60 * 1000, // 5 minutes for cluster data
// JOBS_TTL: 1 * 60 * 1000,     // 1 minute for job data
```

### Cache Behavior

1. **Fresh Data**: If cached data exists and is within the TTL, it's returned immediately
2. **Background Refresh**: When data is fetched and there is a cache hit, a
   background refresh is triggered to pull fresh data
3. **Stale Data**: If fresh data fetch fails but stale data exists, stale data is returned
4. **Cache Miss**: If no cached data exists, fresh data is fetched and cached

### Manual Cache Control

```javascript
// Invalidate specific cache entries (useful for manual refresh)
dashboardCache.invalidate(getClustersAndJobsData);
dashboardCache.invalidate(getGPUs, [clustersAndJobsData]);

// Invalidate all cache entries for a function (regardless of arguments)
dashboardCache.invalidateFunction(getGPUs); // Removes all getGPUs entries
dashboardCache.invalidateFunction(getClusters); // Removes all getClusters entries

// Clear all cache entries
dashboardCache.clear();

// Get cache statistics for debugging
const stats = dashboardCache.getStats();
console.log('Cache size:', stats.cacheSize);
console.log('Background jobs:', stats.backgroundJobs);
console.log('Cache keys:', stats.keys);

// Get detailed cache information for debugging
const detailedStats = dashboardCache.getDetailedStats();
console.log('Detailed cache stats:', detailedStats);

// Enable debug logging to track cache behavior
dashboardCache.setDebugMode(true);
// Disable debug logging
dashboardCache.setDebugMode(false);
```

### Cache TTL Refresh on Access

By default, the cache refreshes the TTL (Time To Live) when data is accessed, preventing cache expiration during frequent page switching:

```javascript
// Default behavior - TTL is refreshed on access
const data = await dashboardCache.get(fetchFunction);

// Disable TTL refresh on access (data will expire based on original fetch time)
const data = await dashboardCache.get(fetchFunction, [], {
  refreshOnAccess: false,
});
```

### Refresh Button Implementation

For refresh buttons that should pull completely fresh data:

```javascript
// Best practice: Use invalidate() for functions without arguments (more efficient)
// and invalidateFunction() for functions that can have multiple cache entries
const handleRefresh = () => {
  // Functions without arguments - use invalidate()
  dashboardCache.invalidate(getClusters);
  dashboardCache.invalidate(getManagedJobs, [{ allUsers: true }]);
  dashboardCache.invalidate(getWorkspaces);

  // Functions with arguments - use invalidateFunction()
  dashboardCache.invalidateFunction(getGPUs);
  dashboardCache.invalidateFunction(getCloudInfrastructure);

  if (refreshDataRef.current) {
    refreshDataRef.current();
  }
};

// Alternative: Invalidate specific cache entries (when you know the exact arguments)
const handleRefreshSpecific = () => {
  dashboardCache.invalidate(getClusters);
  dashboardCache.invalidate(getGPUs, [specificClusters, specificJobs]);

  if (refreshDataRef.current) {
    refreshDataRef.current();
  }
};
```

## Implementation Details

### Cache Keys

Cache keys are automatically generated based on:

- Function name
- Function arguments (JSON stringified)

### Background Refresh

- Triggered when cached data is past 50% of its TTL
- Prevents multiple background jobs for the same key
- Updates cache silently without affecting current requests

### Error Handling

- If fresh data fetch fails and stale data exists, stale data is returned
- If no cached data exists and fetch fails, the error is re-thrown
- Background refresh failures are logged but don't affect the main flow

## Adding Cache to New Pages

To add caching to a new page:

1. Import the cache and preloader:

```javascript
import dashboardCache from '@/lib/cache';
import cachePreloader from '@/lib/cache-preloader';
import { CACHE_CONFIG } from '@/lib/config';
```

2. Add preloading to page initialization:

```javascript
useEffect(() => {
  const initializeData = async () => {
    await cachePreloader.preloadForPage('newpage');
    // ... rest of initialization
  };
  initializeData();
}, []);
```

3. Replace direct function calls with cache calls:

```javascript
// Before
const data = await fetchFunction(args);

// After
const data = await dashboardCache.get(fetchFunction, [args], {
  ttl: CACHE_CONFIG.APPROPRIATE_TTL,
});
```

4. Add cache invalidation to refresh handlers:

```javascript
const handleRefresh = () => {
  dashboardCache.invalidate(fetchFunction, [args]);
  // ... rest of refresh logic
};
```

5. Update the preloader configuration:

```javascript
// In cache-preloader.js, add to DASHBOARD_CACHE_FUNCTIONS.pages
pages: {
  // ... existing pages
  newpage: ['requiredFunction1', 'requiredFunction2'],
}
```

## Performance Benefits

- **Reduced API Calls**: Cached responses reduce server load
- **Faster Page Loads**: Subsequent visits load instantly from cache
- **Background Updates**: Data stays fresh without blocking user interactions
- **Graceful Degradation**: Stale data served if fresh fetch fails
- **Smart Refresh**: Manual refresh invalidates cache for truly fresh data
- **Intelligent Preloading**: Background loading ensures all pages are ready for instant access
