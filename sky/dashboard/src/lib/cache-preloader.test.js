jest.mock('./cache', () => ({
  __esModule: true,
  default: {
    get: jest.fn(() => Promise.resolve({})),
    invalidate: jest.fn(),
  },
}));

jest.mock('@/data/connectors/clusters', () => ({ getClusters: jest.fn() }));
jest.mock('@/data/connectors/jobs', () => ({ getManagedJobs: jest.fn() }));
jest.mock('@/data/connectors/workspaces', () => ({
  getWorkspaces: jest.fn(),
  getEnabledCloudsBatch: jest.fn(),
}));
jest.mock('@/data/connectors/users', () => ({ getUsers: jest.fn() }));
jest.mock('@/data/connectors/volumes', () => ({ getVolumes: jest.fn() }));
jest.mock('@/data/connectors/infra', () => ({
  getEnabledCloudsList: jest.fn(),
  getWorkspaceContexts: jest.fn(),
  getContextGPUData: jest.fn(),
  getSlurmInfrastructure: jest.fn(),
}));
jest.mock('@/data/connectors/ssh-node-pools', () => ({
  getSSHNodePools: jest.fn(),
}));

import dashboardCache from './cache';
import { getWorkspaces } from '@/data/connectors/workspaces';
import { getUsers } from '@/data/connectors/users';
import { CachePreloader } from './cache-preloader';

describe('CachePreloader', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('does not preload the unpaginated jobs dataset for the jobs page', async () => {
    // The jobs table owns its paginated request; only its small supporting data is warmed.
    const preloader = new CachePreloader();

    await preloader.preloadForPage('jobs');

    expect(dashboardCache.get).toHaveBeenCalledTimes(2);
    expect(dashboardCache.get).toHaveBeenCalledWith(getWorkspaces, []);
    expect(dashboardCache.get).toHaveBeenCalledWith(getUsers, []);
  });
});
