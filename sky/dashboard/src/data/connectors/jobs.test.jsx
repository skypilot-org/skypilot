import { renderHook, waitFor } from '@testing-library/react';

// Mock the shared dashboard cache so we can observe get/invalidate calls
// without hitting the network.
jest.mock('@/lib/cache', () => ({
  __esModule: true,
  default: {
    get: jest.fn(),
    invalidate: jest.fn(),
    invalidateFunction: jest.fn(),
    setPreloader: jest.fn(),
    getCached: jest.fn(),
    clear: jest.fn(),
  },
}));

jest.mock('@/data/connectors/client', () => ({
  apiClient: {
    get: jest.fn(),
    post: jest.fn(),
  },
}));

import dashboardCache from '@/lib/cache';
import { apiClient } from '@/data/connectors/client';
import { useSingleManagedJob, getManagedJobs } from '@/data/connectors/jobs';

describe('useSingleManagedJob manual-refresh cache invalidation', () => {
  const jobId = '56164';
  const expectedArgs = [{ allUsers: true, allFields: true, jobIDs: [jobId] }];

  beforeEach(() => {
    jest.clearAllMocks();
    dashboardCache.get.mockResolvedValue({
      jobs: [{ id: Number(jobId) }],
      controllerStopped: false,
    });
  });

  it('does not invalidate the cache on the initial load (refreshTrigger = 0)', async () => {
    renderHook(() => useSingleManagedJob(jobId, 0));

    await waitFor(() => expect(dashboardCache.get).toHaveBeenCalledTimes(1));
    expect(dashboardCache.invalidate).not.toHaveBeenCalled();
  });

  it('invalidates the cached entry before refetching when refreshTrigger increments', async () => {
    const { rerender } = renderHook(
      ({ trigger }) => useSingleManagedJob(jobId, trigger),
      { initialProps: { trigger: 0 } }
    );

    await waitFor(() => expect(dashboardCache.get).toHaveBeenCalledTimes(1));
    expect(dashboardCache.invalidate).not.toHaveBeenCalled();

    // Simulate clicking the detail-page Refresh button.
    rerender({ trigger: 1 });

    await waitFor(() =>
      expect(dashboardCache.invalidate).toHaveBeenCalledTimes(1)
    );
    // Must target the same function + args the fetch uses, otherwise the wrong
    // cache key is cleared and the refresh stays stale.
    expect(dashboardCache.invalidate).toHaveBeenCalledWith(
      getManagedJobs,
      expectedArgs
    );
    await waitFor(() => expect(dashboardCache.get).toHaveBeenCalledTimes(2));
    expect(dashboardCache.get).toHaveBeenLastCalledWith(
      getManagedJobs,
      expectedArgs
    );
  });

  it('does not invalidate when navigating to a new job while refreshTrigger stays elevated', async () => {
    // The parent keeps refreshTrigger state across jobId changes, so after a
    // refresh the trigger remains > 0. Navigating to a different job must NOT
    // invalidate the new job's cache on its initial load.
    const { rerender } = renderHook(
      ({ id, trigger }) => useSingleManagedJob(id, trigger),
      { initialProps: { id: jobId, trigger: 1 } }
    );

    await waitFor(() => expect(dashboardCache.get).toHaveBeenCalledTimes(1));
    jest.clearAllMocks();
    dashboardCache.get.mockResolvedValue({
      jobs: [],
      controllerStopped: false,
    });

    // Navigate to a different job; trigger is unchanged (no manual refresh).
    rerender({ id: '56165', trigger: 1 });

    await waitFor(() => expect(dashboardCache.get).toHaveBeenCalledTimes(1));
    expect(dashboardCache.invalidate).not.toHaveBeenCalled();
  });
});

describe('getManagedJobs sorting', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    apiClient.post.mockResolvedValue({
      ok: true,
      headers: { get: jest.fn(() => 'request-id') },
    });
    apiClient.get.mockResolvedValue({
      ok: true,
      status: 200,
      statusText: 'OK',
      json: jest.fn(async () => ({
        return_value: JSON.stringify({ jobs: [], total: 0 }),
      })),
    });
  });

  it('sends supported sort fields to the managed-jobs API', async () => {
    // Browser sort selection must become server sorting instead of a cache-only value.
    await getManagedJobs({
      allUsers: true,
      page: 2,
      limit: 20,
      sortBy: 'workspace',
      sortOrder: 'asc',
    });

    expect(apiClient.post).toHaveBeenCalledWith(
      '/jobs/queue/v2',
      expect.objectContaining({
        page: 2,
        limit: 20,
        sort_by: 'workspace',
        sort_order: 'asc',
      })
    );
  });

  it('maps the requested-resources UI column to the backend sort field', async () => {
    // The UI calls this column cluster while the managed-jobs API calls it resources.
    await getManagedJobs({ sortBy: 'cluster', sortOrder: 'desc' });

    expect(apiClient.post).toHaveBeenCalledWith(
      '/jobs/queue/v2',
      expect.objectContaining({ sort_by: 'resources', sort_order: 'desc' })
    );
  });
});
