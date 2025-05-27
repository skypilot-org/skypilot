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

The cache system uses configurable TTL values defined in `config.js`:

- `CLUSTERS_AND_JOBS_TTL`: 2 minutes - For cluster and job data
- `GPU_DATA_TTL`: 2 minutes - For GPU data from k8s and ssh node pools
- `CLOUD_INFRA_TTL`: 2 minutes - For cloud infrastructure data

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