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

import dashboardCache from '@/lib/cache';
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
});
