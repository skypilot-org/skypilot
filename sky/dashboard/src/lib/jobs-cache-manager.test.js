jest.mock('./cache', () => ({
  __esModule: true,
  default: {
    get: jest.fn(),
    invalidateFunction: jest.fn(),
  },
}));

jest.mock('@/data/connectors/jobs', () => ({
  getManagedJobs: jest.fn(),
}));

import dashboardCache from './cache';
import { getManagedJobs } from '@/data/connectors/jobs';
import { JobsCacheManager } from './jobs-cache-manager';

describe('JobsCacheManager', () => {
  let manager;

  beforeEach(() => {
    manager = new JobsCacheManager();
    jest.clearAllMocks();
    dashboardCache.get.mockImplementation((_fetchFunction, [options]) =>
      Promise.resolve({
        jobs: [{ id: options.page }],
        total: 3,
        totalNoFilter: 3,
        statusCounts: {},
        controllerStopped: false,
      })
    );
  });

  test('forwards sort options when fetching a visible page', async () => {
    // Sorting must reach the paginated backend query rather than fragmenting cache keys.
    await manager.getPaginatedJobs({
      allUsers: true,
      page: 1,
      limit: 10,
      sortBy: 'workspace',
      sortOrder: 'asc',
    });

    expect(dashboardCache.get).toHaveBeenCalledWith(
      getManagedJobs,
      [
        expect.objectContaining({
          page: 1,
          limit: 10,
          sortBy: 'workspace',
          sortOrder: 'asc',
        }),
      ],
      expect.any(Object)
    );
  });

  test('prefetches only the next server page', async () => {
    // Warming page two must not request the complete managed-job history.
    await manager.prefetchNextPage({ allUsers: true, page: 1, limit: 10 });

    expect(dashboardCache.get).toHaveBeenCalledWith(
      getManagedJobs,
      [expect.objectContaining({ page: 2, limit: 10 })],
      expect.any(Object)
    );
    expect(dashboardCache.get).toHaveBeenCalledTimes(1);
  });
});
