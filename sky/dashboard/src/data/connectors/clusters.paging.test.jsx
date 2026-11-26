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
  __esModule: true,
  apiClient: { fetch: jest.fn(), fetchImmediate: jest.fn() },
}));
jest.mock('@/plugins/dataEnhancement', () => ({
  __esModule: true,
  applyEnhancements: jest.fn(async (data) => data),
}));

import { apiClient } from '@/data/connectors/client';
import { getClusters } from '@/data/connectors/clusters';

function response(data, { status = 200, headers = {} } = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => headers[name] ?? null },
    json: async () => data,
  };
}

// Newest launch first, then by name.
const CLUSTERS = [
  { name: 'a', launched_at: 30 },
  { name: 'b', launched_at: 30 },
  { name: 'c', launched_at: 20 },
  { name: 'e', launched_at: 10 },
  { name: 'd', launched_at: null },
];

const follows = (row, at, name) => {
  const launched = row.launched_at || 0;
  return launched !== at ? launched < at : row.name > name;
};

describe('getClusters on a server that pages the cluster list', () => {
  beforeEach(() => {
    global.fetch.mockReset();
    global.fetch.mockResolvedValue(
      response({ sync_call_max_limit: 2, api_version: '72' })
    );
    apiClient.fetch.mockReset();
    apiClient.fetchImmediate.mockReset();
  });

  test('fetches the clusters as sync calls, following the cursor', async () => {
    apiClient.fetchImmediate.mockImplementation(async (path, body) => {
      const rows = CLUSTERS.filter(
        (c) =>
          body.after_name === null ||
          follows(c, body.after_launched_at, body.after_name)
      ).slice(0, body.limit);
      return response(
        { status: 'SUCCEEDED', return_value: JSON.stringify(rows) },
        { headers: { 'X-SkyPilot-Sync': '1' } }
      );
    });

    const clusters = await getClusters();

    expect(clusters.map((c) => c.cluster)).toEqual(['a', 'b', 'c', 'e', 'd']);
    const bodies = apiClient.fetchImmediate.mock.calls.map(([, b]) => b);
    expect(bodies.map((b) => [b.after_launched_at, b.after_name])).toEqual([
      [null, null],
      [30, 'b'],
      [10, 'e'],
      [0, 'd'],
    ]);
    for (const [path, body, method, options] of apiClient.fetchImmediate.mock
      .calls) {
      expect(path).toBe('/status');
      expect(method).toBe('POST');
      expect(body).toMatchObject({ limit: 2, all_users: true });
      expect(options.headers).toEqual({ 'X-SkyPilot-Sync': '1' });
    }
    expect(apiClient.fetch).not.toHaveBeenCalled();
  });

  test('falls back to one call when a page is not answered inline', async () => {
    apiClient.fetchImmediate.mockResolvedValue(
      response(null, { headers: { 'X-Skypilot-Request-ID': 'r' } })
    );
    apiClient.fetch.mockResolvedValue([{ name: 'a', launched_at: 1 }]);

    const clusters = await getClusters();

    expect(clusters.map((c) => c.cluster)).toEqual(['a']);
    expect(apiClient.fetch).toHaveBeenCalledWith(
      '/status',
      expect.not.objectContaining({ limit: expect.anything() }),
      'POST',
      { sync: false }
    );
  });

  test('a busy page queues nothing before the one-call fallback', async () => {
    apiClient.fetchImmediate.mockResolvedValue(
      response(null, {
        status: 429,
        headers: { 'X-SkyPilot-Sync': '1', 'Retry-After': '0' },
      })
    );
    apiClient.fetch.mockResolvedValue([{ name: 'a', launched_at: 1 }]);

    await getClusters();

    // Every page try asked for a sync call; none was sent to be queued.
    for (const [, , , options] of apiClient.fetchImmediate.mock.calls) {
      expect(options.headers).toEqual({ 'X-SkyPilot-Sync': '1' });
    }
    expect(apiClient.fetch).toHaveBeenCalledTimes(1);
    expect(apiClient.fetch).toHaveBeenCalledWith(
      '/status',
      expect.anything(),
      'POST',
      { sync: false }
    );
  });

  test('fetches named clusters in one sync call', async () => {
    apiClient.fetch.mockResolvedValue([{ name: 'a', launched_at: 1 }]);

    await getClusters({ clusterNames: ['a'] });

    expect(apiClient.fetch).toHaveBeenCalledWith(
      '/status',
      expect.objectContaining({ cluster_names: ['a'], limit: 2 })
    );
    expect(apiClient.fetchImmediate).not.toHaveBeenCalled();
  });

  test('pages a cluster name pattern', async () => {
    apiClient.fetchImmediate.mockResolvedValue(
      response(
        { status: 'SUCCEEDED', return_value: '[]' },
        { headers: { 'X-SkyPilot-Sync': '1' } }
      )
    );

    await getClusters({ clusterNames: ['a*'] });

    expect(apiClient.fetchImmediate).toHaveBeenCalledTimes(1);
    expect(apiClient.fetch).not.toHaveBeenCalled();
  });
});
