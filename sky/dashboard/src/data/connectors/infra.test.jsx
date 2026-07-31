// The dashboard cache is a pass-through here so the connector's own logic is
// what's under test, not the caching layer.
jest.mock('@/lib/cache', () => ({
  __esModule: true,
  default: { get: jest.fn((fn, args = []) => fn(...args)) },
}));

jest.mock('@/data/connectors/client', () => ({
  __esModule: true,
  apiClient: { post: jest.fn(), get: jest.fn() },
}));

import { apiClient } from '@/data/connectors/client';
import { getSlurmInfrastructure } from '@/data/connectors/infra';

// Every Slurm endpoint answers the same way: POST returns a request id in a
// header, then /api/get returns the result under a JSON-encoded return_value.
const scheduled = (requestId) => ({
  ok: true,
  status: 200,
  headers: { get: () => requestId },
});

const result = (returnValue) => ({
  ok: true,
  status: 200,
  json: async () => ({ return_value: JSON.stringify(returnValue) }),
});

// The Infra page must list a cluster that is configured but currently
// unreachable, so the configured-cluster query has to be independent of the
// node and GPU queries rather than derived from them.
describe('getSlurmInfrastructure configured clusters', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    apiClient.post.mockImplementation(async (path) => {
      if (path === '/slurm_cluster_names') return scheduled('req-clusters');
      if (path === '/slurm_gpu_availability') return scheduled('req-gpus');
      if (path === '/slurm_node_info') return scheduled('req-nodes');
      throw new Error(`unexpected POST to ${path}`);
    });
  });

  it('reports a configured cluster whose node and GPU queries are empty', async () => {
    apiClient.get.mockImplementation(async (path) => {
      if (path.includes('req-clusters')) return result(['offline-cluster']);
      return result([]);
    });

    const data = await getSlurmInfrastructure();

    expect(data.slurmClusterNames).toEqual(['offline-cluster']);
    expect(data.perNodeSlurmGPUs).toEqual([]);
    expect(data.perClusterSlurmGPUs).toEqual([]);
  });

  it('does not hold the node and GPU queries behind the cluster list', async () => {
    // Assertions stay in the test body: the connector catches everything a
    // mock throws, so an assertion made inside one would be swallowed and the
    // test would pass regardless.
    let releaseClusters;
    const clusterListPending = new Promise((resolve) => {
      releaseClusters = resolve;
    });
    apiClient.get.mockImplementation(async (path) => {
      if (path.includes('req-clusters')) {
        await clusterListPending;
        return result(['cluster-a']);
      }
      return result([]);
    });

    const pending = getSlurmInfrastructure();
    await new Promise((resolve) => setTimeout(resolve, 0));

    // The cluster list is still in flight, yet the other two have already got
    // past their own POST to fetching a result. Serializing them behind it
    // would leave these uncalled.
    const fetched = apiClient.get.mock.calls.map(([path]) => path);
    expect(fetched).toEqual(
      expect.arrayContaining([
        expect.stringContaining('req-nodes'),
        expect.stringContaining('req-gpus'),
      ])
    );

    releaseClusters();
    expect((await pending).slurmClusterNames).toEqual(['cluster-a']);
  });

  it('falls back to an empty list when the cluster query fails', async () => {
    apiClient.get.mockImplementation(async (path) => {
      if (path.includes('req-clusters')) return { ok: false, status: 500 };
      return result([]);
    });

    const data = await getSlurmInfrastructure();

    expect(data.slurmClusterNames).toEqual([]);
  });
});
