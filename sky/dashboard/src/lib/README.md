# Dashboard Cache System

This directory contains the generalized caching mechanism for the SkyPilot dashboard. The cache system is designed to improve performance by storing API responses and serving them from memory when they're still fresh.

## Files

- `cache.js` - The main cache implementation
- `cache-preloader.js` - Smart cache preloading utility for background data loading
- `config.js` - Configuration settings for cache TTLs and other dashboard settings

## Cache Preloader

The cache preloader warms only the lightweight data required by the current page. Cross-page preloading is opt-in because speculative infrastructure scans and managed-job queries can delay the visible page.

### How It Works

1. **Foreground Loading**: When a page loads, it immediately fetches the data required for that specific page
2. **Opt-in Background Preloading**: Callers can explicitly request speculative loading for small, likely-next data sets
3. **Server-side Jobs Pagination**: The jobs table owns its visible-page request and never preloads the full job history
4. **Shared Cache**: Pages reuse fresh cache entries without issuing a new backend request

### Dashboard Cache Functions

The preloader manages these functions across all pages:

**Base Functions (no arguments):**

- `getClusters` - Used by: clusters, jobs, infra, workspaces, users
- `getManagedJobsForOtherPages` - Trimmed job summary used by: infra, workspaces, users
- `getWorkspaces` - Used by: clusters, jobs, workspaces
- `getUsers` - Used by: users
- `getVolumes` - Used by: volumes

**Dynamic Functions (with arguments):**

- `getEnabledClouds(workspaceName)` - Used by: workspaces

**Page Requirements:**

- **Clusters**: getClusters, getWorkspaces
- **Jobs**: getWorkspaces, getUsers; the table fetches its own paginated data
- **Infra**: getClusters, getManagedJobs
- **Workspaces**: getWorkspaces, getClusters, getManagedJobs, getEnabledClouds
- **Users**: getUsers, getClusters, getManagedJobs
- **Volumes**: getVolumes

### Usage

```javascript
import cachePreloader from '@/lib/cache-preloader';

// Preload only the current page's lightweight supporting data
await cachePreloader.preloadForPage('clusters');

// Preload with options
await cachePreloader.preloadForPage('jobs', {
  backgroundPreload: true, // Explicitly enable speculative background preloading
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
    // Trigger cache preloading for the current page
    await cachePreloader.preloadForPage('clusters');

    // Continue with page-specific data loading
    fetchData(true);
  };

  initializeData();
}, []);
```

### Performance Benefits

- **Lower Request Volume**: Page loads do not fan out to unrelated dashboard endpoints
- **Page-sized Jobs Reads**: Managed jobs stay server-paginated in browser memory
- **Hard TTL Caching**: A fresh hit reuses data without extending its lifetime or refreshing in the background
- **Graceful Degradation**: If preloading fails, pages still work normally

## Timeline Example

Let's say you visit the **clusters** page:

```
Time 0ms:    User visits /clusters
Time 10ms:   cachePreloader.preloadForPage('clusters') called
Time 50ms:   getClusters() and getWorkspaces() loaded (foreground)
Time 100ms:  Clusters page renders with data
Time 100ms:  Current page renders with its foreground data
Time 100ms:  No unrelated requests are started by default
Time 200ms:  Jobs table fetches only its visible server-side page
```

**Result**: The visible page is not delayed by unrelated dashboard work. Callers may opt in to low-cost, likely-next preloads when measurements justify it.

## How to Use

### Basic Usage

```javascript
import dashboardCache from '@/lib/cache';
import { getClustersAndJobsData } from '@/data/connectors/infra';

// Simple usage with the default 30-second TTL
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

The cache system supports configurable TTL values defined in `config.js`. The
default hard TTL is 30 seconds, matching the dashboard's normal periodic
refresh cadence. It keeps visible pages current without making each fresh cache
read start another background request. Callers can still use a longer TTL for
data that is safe to refresh less often.

```javascript
// Current configuration
export const CACHE_CONFIG = {
  DEFAULT_TTL: REFRESH_INTERVALS.REFRESH_INTERVAL, // 30 seconds
};

// Example of how different TTLs could be configured:
// CLUSTERS_TTL: 5 * 60 * 1000, // 5 minutes for cluster data
// JOBS_TTL: 1 * 60 * 1000,     // 1 minute for job data
```

### Cache Behavior

1. **Fresh Data**: If cached data exists and is within the TTL, it's returned immediately
2. **Hard TTL**: A cache hit neither refreshes the backend nor extends the TTL;
   data expires based on its original fetch time
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
console.log('Cache keys:', stats.keys);

// Get detailed cache information for debugging
const detailedStats = dashboardCache.getDetailedStats();
console.log('Detailed cache stats:', detailedStats);

// Enable debug logging to track cache behavior
dashboardCache.setDebugMode(true);
// Disable debug logging
dashboardCache.setDebugMode(false);
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

### Request Deduplication

- Concurrent cache misses for the same function and arguments share one request
- Fresh cache hits do not start a new backend request

### Error Handling

- If fresh data fetch fails and stale data exists, stale data is returned
- If no cached data exists and fetch fails, the error is re-thrown

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
