# Dashboard Cache System

This directory contains the generalized caching mechanism for the SkyPilot dashboard. The cache system is designed to improve performance by storing API responses and serving them from memory when they're still fresh.

## Files

- `cache.js` - The main cache implementation
- `config.js` - Configuration settings for cache TTLs and other dashboard settings

## How to Use

### Basic Usage

```javascript
import dashboardCache from '@/lib/cache';
import { getClustersAndJobsData } from '@/data/connectors/infra';

// Simple usage with default TTL (5 minutes)
const data = await dashboardCache.get(getClustersAndJobsData);

// With custom TTL (2 minutes)
const data = await dashboardCache.get(
  getClustersAndJobsData, 
  [], 
  { ttl: 2 * 60 * 1000 }
);

// With function arguments
const data = await dashboardCache.get(
  getGPUs, 
  [clustersAndJobsData], 
  { ttl: CACHE_CONFIG.GPU_DATA_TTL }
);
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
const data = await dashboardCache.get(fetchFunction, [], { refreshOnAccess: false });
```

### Refresh Button Implementation

For refresh buttons that should pull completely fresh data:

```javascript
// Best practice: Use invalidate() for functions without arguments (more efficient)
// and invalidateFunction() for functions that can have multiple cache entries
const handleRefresh = () => {
  // Functions without arguments - use invalidate()
  dashboardCache.invalidate(getClusters);
  dashboardCache.invalidate(getManagedJobs);
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

1. Import the cache and config:
```javascript
import dashboardCache from '@/lib/cache';
import { CACHE_CONFIG } from '@/lib/config';
```

2. Replace direct function calls with cache calls:
```javascript
// Before
const data = await fetchFunction(args);

// After  
const data = await dashboardCache.get(
  fetchFunction, 
  [args], 
  { ttl: CACHE_CONFIG.APPROPRIATE_TTL }
);
```

3. Add cache invalidation to refresh handlers:
```javascript
const handleRefresh = () => {
  dashboardCache.invalidate(fetchFunction, [args]);
  // ... rest of refresh logic
};
```

## Performance Benefits

- **Reduced API Calls**: Cached responses reduce server load
- **Faster Page Loads**: Subsequent visits load instantly from cache
- **Background Updates**: Data stays fresh without blocking user interactions
- **Graceful Degradation**: Stale data served if fresh fetch fails
- **Smart Refresh**: Manual refresh invalidates cache for truly fresh data 