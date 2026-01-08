# SkyPilot Dashboard Frontend Performance Optimization Plan

## Executive Summary

The dashboard UI is experiencing performance issues primarily due to:
1. **Excessive re-renders** from monolithic components with 40-60+ useState calls
2. **Missing memoization** - no React.memo() usage for child components
3. **Inefficient list rendering** without virtualization
4. **Multiple independent polling intervals** that could thrash the cache

## Current State Analysis

### Component Sizes and State Management

| Component | Lines | useState | useEffect | Severity |
|-----------|-------|----------|-----------|----------|
| users.jsx | 3,189 | 63 | 9 | CRITICAL |
| infra.jsx | 3,012 | 44 | 11 | CRITICAL |
| jobs.jsx | 2,201 | 38 | 20 | HIGH |
| clusters.jsx | 1,528 | ~22 | ~8 | MEDIUM |
| workspaces.jsx | 1,201 | ~15 | ~2 | MEDIUM |

**Impact**: Every setState() call triggers a full re-render of the entire component tree, including all child components.

### What's Working Well

The caching system in `lib/cache.js` and `lib/cache-preloader.js` is well-designed:
- TTL-based expiration (2 minutes)
- Background refresh for stale data
- Request deduplication
- Page-aware preloading
- Visibility-based polling

## Optimization Strategy

### Phase 1: Quick Wins (High Impact, Low Effort)

#### 1.1 Memoize Table Row Components
Create memoized row components to prevent re-renders when parent state changes.

**Files to modify:**
- `src/components/jobs.jsx` - JobRow component
- `src/components/users.jsx` - UserRow, TokenRow components
- `src/components/clusters.jsx` - ClusterRow component

**Pattern:**
```jsx
const JobRow = React.memo(({ job, onAction }) => {
  return (
    <TableRow>
      {/* ... */}
    </TableRow>
  );
});
```

#### 1.2 Add useMemo for Filtered/Sorted Data
Memoize expensive list operations to prevent recalculation on every render.

**Pattern:**
```jsx
const filteredJobs = useMemo(() => {
  return jobs.filter(job => /* filter logic */);
}, [jobs, filterCriteria]);

const sortedJobs = useMemo(() => {
  return [...filteredJobs].sort((a, b) => /* sort logic */);
}, [filteredJobs, sortConfig]);
```

#### 1.3 Memoize Callback Functions
Use useCallback for event handlers passed to child components.

**Pattern:**
```jsx
const handleJobAction = useCallback((jobId, action) => {
  // action logic
}, [/* dependencies */]);
```

### Phase 2: Structural Improvements (Medium Effort)

#### 2.1 Consolidate Related State
Group related state variables into single state objects to reduce render triggers.

**Before:**
```jsx
const [loading, setLoading] = useState(false);
const [error, setError] = useState(null);
const [data, setData] = useState([]);
```

**After:**
```jsx
const [fetchState, setFetchState] = useState({
  loading: false,
  error: null,
  data: []
});
```

#### 2.2 Extract Sub-Components
Split large components into smaller, focused components with their own state.

**Example for users.jsx:**
- `UserTable` - Main table with memoized rows
- `CreateUserDialog` - Dialog for creating users
- `ImportExportDialog` - Import/export functionality
- `TokenManagement` - API token management
- `UserFilters` - Filter controls

### Phase 3: Performance Infrastructure (Higher Effort)

#### 3.1 Smart Polling with Visibility API
Consolidate polling logic and add smarter refresh behavior.

**Implementation:**
```jsx
// lib/polling-manager.js
class PollingManager {
  // Single timer that coordinates all page refreshes
  // Respects tab visibility
  // Uses cache TTL to determine refresh needs
}
```

#### 3.2 Pagination for Large Lists
Implement proper pagination instead of rendering all items.

- Jobs table: Already has server-side pagination, ensure client uses it
- Users table: Add client-side pagination with configurable page size

## Implementation Order

### Immediate (This PR)

1. **Add React.memo to table row components**
   - `JobRow` in jobs.jsx
   - `ClusterRow` in clusters.jsx
   - User-related row components

2. **Add useMemo for filtered/sorted data**
   - Filter computations
   - Sort computations
   - Aggregation calculations

3. **Add useCallback for handlers passed to children**
   - Action handlers
   - Click handlers
   - Submit handlers

4. **Optimize refresh intervals**
   - Add visibility check to prevent background polling
   - Consolidate multiple intervals where possible

### Future Improvements (Separate PRs)

1. Split monolithic components into sub-components
2. Add virtual scrolling for large tables (react-window or similar)
3. Implement consolidated polling manager
4. Add proper error boundaries

## Expected Impact

| Optimization | Expected Improvement |
|--------------|---------------------|
| React.memo on rows | 50-70% fewer child re-renders |
| useMemo on lists | Eliminate redundant filtering/sorting |
| useCallback | Stable references, fewer child updates |
| Visibility-aware polling | 30-50% fewer API calls when tab hidden |

## Files to Modify

### Priority 1 (This PR)
- `src/components/jobs.jsx`
- `src/components/clusters.jsx`
- `src/components/users.jsx`
- `src/components/utils.jsx` (shared components)

### Priority 2 (Follow-up)
- `src/components/infra.jsx`
- `src/components/workspaces.jsx`
- `src/components/volumes.jsx`

## Testing

1. Manual testing: Navigate between pages, verify responsiveness
2. React DevTools Profiler: Measure render counts before/after
3. Chrome DevTools Performance: Check for long tasks
4. Verify no regressions in functionality

## Backend Performance Bottlenecks

Analysis of `sky/server/` revealed several server-side performance issues:

### 1. CRITICAL: 100ms Polling Loop in `/api/get`

**Location:** `sky/server/server.py:1619`

```python
# Current implementation polls every 100ms
while True:
    req_status = await requests_lib.get_request_status_async(request_id)
    if req_status.status > requests_lib.RequestStatus.RUNNING:
        break
    await asyncio.sleep(0.1)  # 100ms polling
```

**Impact:** Minimum 100ms latency for every async API call, plus DB query overhead.

**Recommendation:** Consider using event-based notification (asyncio.Event) instead of polling.

### 2. LIKE Queries Instead of Indexed Lookups

**Location:** `sky/server/requests/requests.py:720`

```python
cursor.execute((f'SELECT {columns_str} FROM {REQUEST_TABLE} '
                'WHERE request_id LIKE ?'), (request_id + '%',))
```

**Impact:** Full table scan instead of indexed lookup.

**Recommendation:** Use exact match when full request_id is available, only use LIKE for prefix search.

### 3. File Lock Overhead in Async Operations

**Location:** `sky/server/requests/requests.py:776`

```python
# TODO(aylei): figure out how to remove FileLock here to avoid the overhead
async with filelock.AsyncFileLock(request_lock_path(request_id)):
    return await _get_request_no_lock_async(request_id, fields)
```

**Impact:** File I/O contention on every request access.

**Recommendation:** Use database-level locking or atomic operations instead.

### 4. Missing Database Indexes

The request table appears to lack indexes on frequently queried columns:
- `request_id` (used for prefix lookup)
- `status` (heavily filtered)
- `created_at` (used for sorting)

**Recommendation:** Add indexes:
```sql
CREATE INDEX IF NOT EXISTS idx_request_id ON requests(request_id);
CREATE INDEX IF NOT EXISTS idx_status ON requests(status);
CREATE INDEX IF NOT EXISTS idx_created_at ON requests(created_at);
```

### Backend Optimization Priority

1. **Immediate (if causing user complaints):**
   - Add database indexes
   - Use exact match when possible

2. **Medium-term:**
   - Replace polling with event-based notification
   - Remove file locks in favor of DB transactions

3. **Long-term:**
   - Consider WebSocket for real-time updates
   - Implement connection pooling for DB

## Performance Benchmark

A benchmark utility has been added at `src/lib/performance-benchmark.js`.

**Usage in browser console:**
```javascript
// Run API latency benchmark
await window.dashboardPerf.runApiLatencyBenchmark();

// Measure cache effectiveness
await window.dashboardPerf.measureCacheEffectiveness();

// Start long task observer
window.dashboardPerf.startLongTaskObserver();
```

## Rollback Plan

All changes are additive optimizations using standard React patterns. If issues arise:
1. Revert the specific optimization causing issues
2. Test in isolation
3. Re-apply with fixes
