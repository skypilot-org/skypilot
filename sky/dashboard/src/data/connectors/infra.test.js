import {
  CLOUDS_LIST,
  CLOUD_CANONICALIZATIONS,
  canonicalizeCloudName,
} from '@/data/connectors/constants';
import { getClusters } from '@/data/connectors/clusters';
import { getManagedJobs } from '@/data/connectors/jobs';
import dashboardCache from '@/lib/cache';

import { getCloudInfrastructure, getEnabledCloudsList } from './infra';
import { getEnabledCloudsBatch, getWorkspaces } from './workspaces';

jest.mock('@/lib/cache', () => ({
  __esModule: true,
  default: {
    get: jest.fn(),
  },
}));

jest.mock('@/data/connectors/clusters', () => ({
  getClusters: jest.fn(),
}));

jest.mock('@/data/connectors/jobs', () => ({
  getManagedJobs: jest.fn(),
}));

jest.mock('@/data/connectors/workspaces', () => ({
  getEnabledCloudsBatch: jest.fn(),
  getWorkspaces: jest.fn(),
}));

describe('dashboard cloud infrastructure connectors', () => {
  beforeEach(() => {
    dashboardCache.get.mockReset();
  });

  it('canonicalizes Modal for workspace badges and cloud lists', () => {
    expect(CLOUDS_LIST).toContain('Modal');
    expect(CLOUD_CANONICALIZATIONS.modal).toBe('Modal');
    expect(canonicalizeCloudName('modal')).toBe('Modal');
    expect(canonicalizeCloudName('Modal')).toBe('Modal');
  });

  it('includes raw modal workspace entries in enabled cloud rows', async () => {
    dashboardCache.get.mockImplementation((fn) => {
      if (fn === getWorkspaces) {
        return Promise.resolve({ default: {}, team: {} });
      }
      if (fn === getEnabledCloudsBatch) {
        return Promise.resolve({
          default: ['modal'],
          team: ['RunPod'],
        });
      }
      throw new Error(`Unexpected cache call: ${fn.name}`);
    });

    const result = await getEnabledCloudsList();

    expect(result.clouds).toEqual([
      { name: 'Modal', enabled: true },
      { name: 'RunPod', enabled: true },
    ]);
    expect(result.enabledClouds).toBe(2);
    expect(result.totalClouds).toBe(CLOUDS_LIST.length);
  });

  it('counts raw modal clusters and jobs under the Modal cloud row', async () => {
    dashboardCache.get.mockImplementation((fn) => {
      if (fn === getManagedJobs) {
        return Promise.resolve({
          jobs: [{ cloud: 'modal' }, { cloud: 'RunPod' }],
        });
      }
      if (fn === getClusters) {
        return Promise.resolve([{ cloud: 'modal' }, { cloud: 'RunPod' }]);
      }
      if (fn === getWorkspaces) {
        return Promise.resolve({ default: {} });
      }
      if (fn === getEnabledCloudsBatch) {
        return Promise.resolve({ default: ['modal', 'RunPod'] });
      }
      throw new Error(`Unexpected cache call: ${fn.name}`);
    });

    const result = await getCloudInfrastructure();

    expect(result.clouds).toEqual([
      { name: 'Modal', clusters: 1, jobs: 1, enabled: true },
      { name: 'RunPod', clusters: 1, jobs: 1, enabled: true },
    ]);
    expect(result.enabledClouds).toBe(2);
    expect(result.totalClouds).toBe(CLOUDS_LIST.length);
  });
});
