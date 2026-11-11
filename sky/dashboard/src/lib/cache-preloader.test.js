// Tests for cache-preloader.js — specifically that the /jobs page does not
// preload the full unpaginated job list, which measured 39 MB / 25-36 s on
// a large deployment (issue #11009).

// Mock modules that cache-preloader imports so the module loads cleanly.
jest.mock('./cache', () => ({
  __esModule: true,
  default: {
    get: jest.fn().mockResolvedValue({}),
    invalidate: jest.fn(),
    invalidateFunction: jest.fn(),
    setPreloader: jest.fn(),
    getStats: jest.fn().mockReturnValue({}),
    clear: jest.fn(),
  },
}));
jest.mock('@/data/connectors/jobs', () => ({
  getManagedJobs: jest.fn().mockResolvedValue({ jobs: [], total: 0 }),
}));
jest.mock('@/data/connectors/clusters', () => ({
  getClusters: jest.fn().mockResolvedValue([]),
}));
jest.mock('@/data/connectors/workspaces', () => ({
  getWorkspaces: jest.fn().mockResolvedValue({}),
  getEnabledCloudsBatch: jest.fn().mockResolvedValue({}),
}));
jest.mock('@/data/connectors/users', () => ({
  getUsers: jest.fn().mockResolvedValue([]),
}));
jest.mock('@/data/connectors/volumes', () => ({
  getVolumes: jest.fn().mockResolvedValue([]),
}));
jest.mock('@/data/connectors/infra', () => ({
  getEnabledCloudsList: jest.fn().mockResolvedValue([]),
  getWorkspaceContexts: jest.fn().mockResolvedValue({ allContextNames: [] }),
  getContextGPUData: jest.fn().mockResolvedValue({}),
  getSlurmInfrastructure: jest.fn().mockResolvedValue({}),
}));
jest.mock('@/data/connectors/ssh-node-pools', () => ({
  getSSHNodePools: jest.fn().mockResolvedValue([]),
}));
jest.mock('@/data/connectors/constants', () => ({
  MANAGED_JOBS_SUMMARY_ARGS: [{ fields: ['job_id', 'status'] }],
}));

import dashboardCache from './cache';
import { getManagedJobs } from '@/data/connectors/jobs';
import { getWorkspaces } from '@/data/connectors/workspaces';
import { getUsers } from '@/data/connectors/users';
import { DASHBOARD_CACHE_FUNCTIONS, CachePreloader } from './cache-preloader';

describe('DASHBOARD_CACHE_FUNCTIONS.pages.jobs', () => {
  test('does not include getManagedJobs — the full unpaginated fetch', () => {
    // getManagedJobs is the function that fetches every job (39 MB / 25-36 s).
    // It must NOT appear in the jobs page list; jobsCacheManager handles the
    // paginated fetch directly. See issue #11009.
    expect(DASHBOARD_CACHE_FUNCTIONS.pages.jobs).not.toContain(
      'getManagedJobs'
    );
  });

  test('still includes getWorkspaces and getUsers for the jobs page', () => {
    // Workspace and user data are needed for dropdowns / filter options.
    expect(DASHBOARD_CACHE_FUNCTIONS.pages.jobs).toContain('getWorkspaces');
    expect(DASHBOARD_CACHE_FUNCTIONS.pages.jobs).toContain('getUsers');
  });
});

describe('CachePreloader._loadPageData for jobs page', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    dashboardCache.get.mockResolvedValue({});
  });

  test('does not call getManagedJobs (unpaginated) when loading the jobs page', async () => {
    const preloader = new CachePreloader();
    await preloader._loadPageData('jobs');

    // dashboardCache.get must not have been called with getManagedJobs as fn.
    // getManagedJobsWithClientPagination was the old fn; getManagedJobs is the
    // underlying raw fetch — neither should appear for the /jobs page preload.
    const callsWithManagedJobs = dashboardCache.get.mock.calls.filter(
      ([fn]) => fn === getManagedJobs
    );

    // It is acceptable for getManagedJobs to be called with MANAGED_JOBS_SUMMARY_ARGS
    // (that is the getManagedJobsForOtherPages path, used by infra/users/workspaces).
    // It is NOT acceptable for it to be called with {allUsers: true} and no
    // pagination params (the big fetch).
    const bigFetchCalls = callsWithManagedJobs.filter(([, args]) => {
      const opts = args && args[0];
      // A big fetch has allUsers:true and no page/limit.
      return opts && opts.allUsers === true && opts.page === undefined;
    });

    expect(bigFetchCalls).toHaveLength(0);
  });

  test('still preloads getWorkspaces and getUsers for the jobs page', async () => {
    const preloader = new CachePreloader();
    await preloader._loadPageData('jobs');

    // getWorkspaces and getUsers are still required — they back the filter
    // dropdowns on the jobs table.
    const fns = dashboardCache.get.mock.calls.map(([fn]) => fn);
    const { getWorkspaces: wsImpl } = await import(
      '@/data/connectors/workspaces'
    );
    const { getUsers: usersImpl } = await import('@/data/connectors/users');
    expect(fns).toContain(wsImpl);
    expect(fns).toContain(usersImpl);
  });
});

describe('CachePreloader._backgroundPreloadOtherPages', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    dashboardCache.get.mockResolvedValue({});
  });

  test('does not call getManagedJobs (unpaginated) during background preload', async () => {
    const preloader = new CachePreloader();
    // Trigger background preload from a non-jobs page (e.g., clusters)
    // which would normally background-preload everything the jobs page needs.
    preloader._backgroundPreloadOtherPages('clusters');
    // Let promises settle.
    await new Promise((r) => setTimeout(r, 50));

    const callsWithManagedJobs = dashboardCache.get.mock.calls.filter(
      ([fn]) => fn === getManagedJobs
    );
    const bigFetchCalls = callsWithManagedJobs.filter(([, args]) => {
      const opts = args && args[0];
      return (
        opts &&
        opts.allUsers === true &&
        opts.page === undefined &&
        !opts.fields
      );
    });

    expect(bigFetchCalls).toHaveLength(0);
  });
});
