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
import {
  getSlurmInfrastructure,
  getSlurmClusterInfrastructure,
  slurmClusterGPUsFromNodes,
} from '@/data/connectors/infra';

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

// Route POSTs to per-cluster request ids so the /api/get mock can key its
// responses by cluster: req-nodes-<cluster>. The GPU columns are derived
// from the node feed, so /slurm_gpu_availability must never be called —
// routing it to a throw makes any such call fail the test that made it.
const routePosts = () => {
  apiClient.post.mockImplementation(async (path, body) => {
    const cluster = body?.slurm_cluster_name || 'all';
    if (path === '/slurm_cluster_names') return scheduled('req-clusters');
    if (path === '/slurm_node_info') return scheduled(`req-nodes-${cluster}`);
    throw new Error(`unexpected POST to ${path}`);
  });
};

// The Infra page must list a cluster that is configured but currently
// unreachable, and one cluster's failure or slowness must only affect its
// own slice of the data — the queries are fanned out per cluster.
describe('getSlurmInfrastructure configured clusters', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    routePosts();
  });

  it('reports a configured cluster whose node query is empty', async () => {
    apiClient.get.mockImplementation(async (path) => {
      if (path.includes('req-clusters')) return result(['offline-cluster']);
      return result([]);
    });

    const data = await getSlurmInfrastructure();

    expect(data.slurmClusterNames).toEqual(['offline-cluster']);
    expect(data.perNodeSlurmGPUs).toEqual([]);
    expect(data.perClusterSlurmGPUs).toEqual([]);
  });

  it('queries each configured cluster separately, node feed only', async () => {
    apiClient.get.mockImplementation(async (path) => {
      if (path.includes('req-clusters')) return result(['a', 'b']);
      return result([]);
    });

    await getSlurmInfrastructure();

    const clustersQueried = apiClient.post.mock.calls
      .filter(([path]) => path === '/slurm_node_info')
      .map(([, body]) => body.slurm_cluster_name)
      .sort();
    expect(clustersQueried).toEqual(['a', 'b']);
    // The GPU columns derive from the node rows; the availability endpoint
    // is not part of this page's fetch fan-out.
    const availabilityCalls = apiClient.post.mock.calls.filter(
      ([path]) => path === '/slurm_gpu_availability'
    );
    expect(availabilityCalls).toEqual([]);
  });

  it("keeps one cluster's data when another cluster's query fails", async () => {
    apiClient.get.mockImplementation(async (path) => {
      if (path.includes('req-clusters')) return result(['good', 'bad']);
      if (path.includes('-bad')) throw new Error('login node unreachable');
      if (path.includes('req-nodes-good'))
        return result([
          {
            node_name: 'n1',
            slurm_cluster_name: 'good',
            partition: 'p',
            gpu_type: 'H100',
            total_gpus: 8,
            free_gpus: 4,
            node_state: 'idle',
          },
        ]);
      return result([]);
    });

    const data = await getSlurmInfrastructure();

    // The unreachable cluster still gets a row from the configured names;
    // its failed query just contributes empty slices.
    expect(data.slurmClusterNames).toEqual(['good', 'bad']);
    expect(data.perClusterSlurmGPUs).toHaveLength(1);
    expect(data.perClusterSlurmGPUs[0]).toMatchObject({
      cluster: 'good',
      gpu_name: 'H100',
      gpu_total: 8,
      gpu_free: 4,
    });
    expect(data.perNodeSlurmGPUs).toHaveLength(1);
    expect(data.perNodeSlurmGPUs[0]).toMatchObject({
      cluster: 'good',
      node_name: 'n1',
    });
    expect(data.allSlurmGPUs).toEqual([
      { gpu_name: 'H100', gpu_total: 8, gpu_free: 4 },
    ]);
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

describe('getSlurmClusterInfrastructure', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    routePosts();
  });

  it('derives the per-cluster GPU slice from the node rows', async () => {
    apiClient.get.mockImplementation(async (path) => {
      if (path.includes('req-nodes-cluster-a'))
        return result([
          {
            node_name: 'gpu-node-1',
            slurm_cluster_name: 'cluster-a',
            partition: 'gpu',
            gpu_type: 'H100',
            total_gpus: 8,
            free_gpus: 0,
            node_state: 'alloc',
          },
          {
            node_name: 'gpu-node-2',
            slurm_cluster_name: 'cluster-a',
            partition: 'gpu',
            gpu_type: 'H100',
            total_gpus: 8,
            free_gpus: 4,
            node_state: 'mix',
          },
        ]);
      return result([]);
    });

    const data = await getSlurmClusterInfrastructure('cluster-a');

    expect(data.cluster).toBe('cluster-a');
    expect(data.perClusterGPUs).toEqual([
      {
        gpu_name: 'H100',
        gpu_requestable_qty_per_node: '1, 2, 4, 8',
        gpu_total: 16,
        gpu_free: 4,
        cluster: 'cluster-a',
      },
    ]);
    expect(data.perNodeGPUs).toHaveLength(2);
    expect(data.perNodeGPUs[0]).toMatchObject({
      node_name: 'gpu-node-1',
      cluster: 'cluster-a',
      node_state: 'alloc',
    });
  });
});

// The per-type derivation itself: aggregates GPU-bearing node rows, skips
// CPU-only rows, and advertises the same powers-of-2 requestable counts per
// node shape the catalog does.
describe('slurmClusterGPUsFromNodes', () => {
  it('aggregates node rows into per-type cluster entries', () => {
    const entries = slurmClusterGPUsFromNodes('cluster-x', [
      { gpu_name: 'H100', gpu_total: 8, gpu_free: 8, cluster: 'cluster-x' },
      { gpu_name: 'H100', gpu_total: 8, gpu_free: 2, cluster: 'cluster-x' },
      { gpu_name: 'H200', gpu_total: 4, gpu_free: 4, cluster: 'cluster-x' },
    ]);
    expect(entries).toEqual([
      {
        gpu_name: 'H100',
        gpu_total: 16,
        gpu_free: 10,
        cluster: 'cluster-x',
        gpu_requestable_qty_per_node: '1, 2, 4, 8',
      },
      {
        gpu_name: 'H200',
        gpu_total: 4,
        gpu_free: 4,
        cluster: 'cluster-x',
        gpu_requestable_qty_per_node: '1, 2, 4',
      },
    ]);
  });

  it('includes a non-power-of-2 node total as a requestable count', () => {
    const entries = slurmClusterGPUsFromNodes('c', [
      { gpu_name: 'L4', gpu_total: 6, gpu_free: 6, cluster: 'c' },
    ]);
    expect(entries[0].gpu_requestable_qty_per_node).toBe('1, 2, 4, 6');
  });

  it('skips CPU-only node rows', () => {
    expect(
      slurmClusterGPUsFromNodes('cpu-cluster', [
        { gpu_name: '-', gpu_total: 0, gpu_free: 0, cluster: 'cpu-cluster' },
        { gpu_name: null, gpu_total: 0, gpu_free: 0, cluster: 'cpu-cluster' },
      ])
    ).toEqual([]);
  });

  it('returns no entries for empty or missing node lists', () => {
    expect(slurmClusterGPUsFromNodes('empty', [])).toEqual([]);
    expect(slurmClusterGPUsFromNodes('empty', undefined)).toEqual([]);
  });
});
