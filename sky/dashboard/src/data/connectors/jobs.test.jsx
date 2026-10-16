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

const jobId = '56164';
// The one call a job's detail page makes: the job's own rows and every job
// launched under it, in the same answer.
const treeArgs = [
  { allUsers: true, allFields: true, jobIDs: [jobId], includeTree: true },
];

describe('useSingleManagedJob manual-refresh cache invalidation', () => {
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
    expect(dashboardCache.get).toHaveBeenCalledWith(getManagedJobs, treeArgs);
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
      treeArgs
    );
    await waitFor(() => expect(dashboardCache.get).toHaveBeenCalledTimes(2));
    expect(dashboardCache.get).toHaveBeenLastCalledWith(
      getManagedJobs,
      treeArgs
    );
  });

  it("does not invalidate the next job when its predecessor's refreshed fetch is still in flight", async () => {
    // Refresh on job A, then navigate to job B before A's fetch settles. The
    // ref must already record the trigger, or B sees "1 > 0" and drops its
    // own entry on its first load.
    let settleA;
    dashboardCache.get.mockImplementationOnce(
      () => new Promise((resolve) => (settleA = resolve))
    );
    const { rerender } = renderHook(
      ({ id, trigger }) => useSingleManagedJob(id, trigger),
      { initialProps: { id: jobId, trigger: 0 } }
    );
    await waitFor(() => expect(dashboardCache.get).toHaveBeenCalledTimes(1));
    settleA({ jobs: [{ id: Number(jobId) }], controllerStopped: false });
    await waitFor(() => expect(dashboardCache.get).toHaveBeenCalledTimes(1));

    // Refresh: A is invalidated and refetched, and this fetch stays pending.
    dashboardCache.get.mockImplementationOnce(() => new Promise(() => {}));
    rerender({ id: jobId, trigger: 1 });
    await waitFor(() =>
      expect(dashboardCache.invalidate).toHaveBeenCalledTimes(1)
    );
    dashboardCache.invalidate.mockClear();

    // Navigate to B while A's fetch is still pending.
    dashboardCache.get.mockResolvedValue({
      jobs: [{ id: 56165 }],
      controllerStopped: false,
    });
    rerender({ id: '56165', trigger: 1 });
    await waitFor(() => expect(dashboardCache.get).toHaveBeenCalledTimes(3));
    expect(dashboardCache.invalidate).not.toHaveBeenCalled();
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

describe('useSingleManagedJob tree in one fetch', () => {
  const root = Number(jobId);
  const treeRows = [
    { id: root, task_id: 0, root_job_id: null },
    { id: root, task_id: 1, root_job_id: null },
    // Launched from inside the job, and from inside that job in turn: both
    // have the root as root_job_id.
    { id: root + 1, task_id: 0, root_job_id: root, parent_job_id: root },
    { id: root + 2, task_id: 0, root_job_id: root, parent_job_id: root + 1 },
  ];

  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('splits the answer into the job rows and the jobs launched under it', async () => {
    dashboardCache.get.mockResolvedValue({
      jobs: treeRows,
      controllerStopped: false,
    });
    const { result } = renderHook(() => useSingleManagedJob(jobId, 0));

    await waitFor(() => expect(result.current.loading).toBe(false));
    // One request, for the whole tree.
    expect(dashboardCache.get).toHaveBeenCalledTimes(1);
    expect(dashboardCache.get).toHaveBeenCalledWith(getManagedJobs, treeArgs);
    expect(result.current.jobData.jobs.map((j) => j.task_id)).toEqual([0, 1]);
    expect(result.current.members.map((j) => j.id)).toEqual([
      root + 1,
      root + 2,
    ]);
    // Both arrive together: nothing shows the declared tasks first and the
    // dynamic ones later.
    expect(result.current.membersLoaded).toBe(true);
  });

  it('shows no members for a job that is not the top of its tree', async () => {
    // Asking for a launched job returns its whole tree (normalized to the
    // root); its own page lists only its rows and no members.
    const memberId = String(root + 1);
    dashboardCache.get.mockResolvedValue({
      jobs: treeRows,
      controllerStopped: false,
    });
    const { result } = renderHook(() => useSingleManagedJob(memberId, 0));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.jobData.jobs.map((j) => j.id)).toEqual([root + 1]);
    expect(result.current.members).toEqual([]);
    expect(result.current.membersLoaded).toBe(true);
  });

  it('reports an empty job on a failed fetch', async () => {
    dashboardCache.get.mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => useSingleManagedJob(jobId, 0));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.jobData).toEqual({
      jobs: [],
      controllerStopped: false,
    });
    expect(result.current.members).toEqual([]);
    expect(result.current.membersLoaded).toBe(true);
  });

  it('fetches nothing when the rows are preloaded from a tree', async () => {
    // The dynamic task page renders a member out of the group's tree.
    const preloaded = {
      jobs: [{ id: root + 1, task_id: 0, root_job_id: root }],
      controllerStopped: false,
    };
    const { result } = renderHook(() =>
      useSingleManagedJob(String(root + 1), 0, { preloaded })
    );
    expect(result.current.loading).toBe(false);
    expect(result.current.jobData).toBe(preloaded);
    expect(result.current.members).toEqual([]);
    expect(result.current.membersLoaded).toBe(true);
    await new Promise((r) => setTimeout(r, 20));
    expect(dashboardCache.get).not.toHaveBeenCalled();
    expect(dashboardCache.invalidate).not.toHaveBeenCalled();
  });

  // Last: the fallback is remembered for the rest of the module's life, so
  // tests after this one would skip the tree fetch.
  it('falls back to the two-call path when the controller refuses include_tree', async () => {
    const jobArgs = [{ allUsers: true, allFields: true, jobIDs: [jobId] }];
    const refused = new Error('The jobs controller does not support ...');
    refused.includeTreeUnsupported = true;
    dashboardCache.get.mockImplementation((fn, args) => {
      if (args[0].includeTree) return Promise.reject(refused);
      if (args[0].jobIDs) {
        return Promise.resolve({
          jobs: treeRows.slice(0, 2),
          controllerStopped: false,
        });
      }
      // The full listing, with a row from another tree mixed in.
      return Promise.resolve({
        jobs: [...treeRows.slice(2), { id: 9, root_job_id: 8 }],
        controllerStopped: false,
      });
    });
    const { result } = renderHook(() => useSingleManagedJob(jobId, 0));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(dashboardCache.get).toHaveBeenCalledWith(getManagedJobs, treeArgs);
    expect(dashboardCache.get).toHaveBeenCalledWith(getManagedJobs, jobArgs);
    expect(dashboardCache.get).toHaveBeenCalledTimes(3);
    expect(result.current.jobData.jobs.map((j) => j.task_id)).toEqual([0, 1]);
    expect(result.current.members.map((j) => j.id)).toEqual([
      root + 1,
      root + 2,
    ]);
    expect(result.current.membersLoaded).toBe(true);

    // Remembered: the next job goes straight to the two calls.
    jest.clearAllMocks();
    const { rerender } = renderHook(
      ({ trigger }) => useSingleManagedJob('56165', trigger),
      { initialProps: { trigger: 0 } }
    );
    await waitFor(() => expect(dashboardCache.get).toHaveBeenCalledTimes(2));
    expect(dashboardCache.get).not.toHaveBeenCalledWith(
      getManagedJobs,
      expect.arrayContaining([expect.objectContaining({ includeTree: true })])
    );

    // An explicit Refresh tries the tree again (the controller may have
    // upgraded). It succeeds here, so the flag clears and later loads use
    // the tree fetch.
    jest.clearAllMocks();
    dashboardCache.get.mockResolvedValue({
      jobs: treeRows,
      controllerStopped: false,
    });
    rerender({ trigger: 1 });
    await waitFor(() =>
      expect(dashboardCache.get).toHaveBeenCalledWith(
        getManagedJobs,
        expect.arrayContaining([expect.objectContaining({ includeTree: true })])
      )
    );
    jest.clearAllMocks();
    renderHook(() => useSingleManagedJob('56166', 0));
    await waitFor(() => expect(dashboardCache.get).toHaveBeenCalledTimes(1));
    expect(dashboardCache.get).toHaveBeenCalledWith(
      getManagedJobs,
      expect.arrayContaining([expect.objectContaining({ includeTree: true })])
    );
  });
});
