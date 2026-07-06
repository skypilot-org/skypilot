import { act, renderHook, waitFor } from '@testing-library/react';

// Mock the shared dashboard cache so we can observe which cache key the hook
// fetches under (all-users vs. current-user scope) without hitting the network.
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

// Stub the API client so getClusters can be exercised in isolation.
jest.mock('@/data/connectors/client', () => ({
  __esModule: true,
  apiClient: { fetch: jest.fn() },
}));

// Plugin enhancements are a no-op passthrough for these tests.
jest.mock('@/plugins/dataEnhancement', () => ({
  __esModule: true,
  applyEnhancements: jest.fn(async (data) => data),
}));

import dashboardCache from '@/lib/cache';
import { apiClient } from '@/data/connectors/client';
import {
  getClusters,
  getClusterHistory,
  useClusterData,
} from '@/data/connectors/clusters';

describe('getClusters all_users scoping', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    apiClient.fetch.mockResolvedValue([]);
  });

  it('requests all users by default', async () => {
    await getClusters();

    expect(apiClient.fetch).toHaveBeenCalledTimes(1);
    const [, body] = apiClient.fetch.mock.calls[0];
    expect(body.all_users).toBe(true);
  });

  it('scopes the request to the current user when allUsers is false', async () => {
    await getClusters({ allUsers: false });

    expect(apiClient.fetch).toHaveBeenCalledTimes(1);
    const [, body] = apiClient.fetch.mock.calls[0];
    expect(body.all_users).toBe(false);
  });
});

describe('useClusterData ownership scoping (client path)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    // No pagination plugin -> client-side path.
    delete window.__skyPaginationFetch;
    dashboardCache.get.mockResolvedValue([]);
  });

  it('fetches the shared all-users cache entry when allUsers is true', async () => {
    renderHook(() => useClusterData({ allUsers: true }));

    await waitFor(() => expect(dashboardCache.get).toHaveBeenCalled());
    // All-users keeps the default (no-arg) cache key so it stays shared with
    // the rest of the dashboard.
    expect(dashboardCache.get).toHaveBeenCalledWith(getClusters);
    expect(dashboardCache.get).not.toHaveBeenCalledWith(getClusters, [
      { allUsers: false },
    ]);
  });

  it('fetches the current-user-scoped cache entry when allUsers is false', async () => {
    renderHook(() =>
      useClusterData({
        allUsers: false,
        currentUser: { id: 'u-1', name: 'alice' },
      })
    );

    await waitFor(() =>
      expect(dashboardCache.get).toHaveBeenCalledWith(getClusters, [
        { allUsers: false },
      ])
    );
    expect(dashboardCache.get).not.toHaveBeenCalledWith(getClusters);
  });

  it("drops other users' terminated clusters from history when scoped to mine", async () => {
    // The cost_report (history) endpoint always returns every user's rows, so
    // the hook scopes them client-side to match the server-scoped active list.
    dashboardCache.get.mockImplementation((fn) => {
      if (fn === getClusterHistory) {
        return Promise.resolve([
          {
            cluster: 'mine-terminated',
            user_hash: 'u-1',
            user: 'alice',
            cluster_hash: 'h2',
            status: 'TERMINATED',
          },
          {
            cluster: 'other-terminated',
            user_hash: 'u-2',
            user: 'bob',
            cluster_hash: 'h3',
            status: 'TERMINATED',
          },
        ]);
      }
      return Promise.resolve([
        {
          cluster: 'mine-active',
          user_hash: 'u-1',
          user: 'alice',
          cluster_hash: 'h1',
          status: 'RUNNING',
        },
      ]);
    });

    const { result } = renderHook(() =>
      useClusterData({
        showHistory: true,
        allUsers: false,
        currentUser: { id: 'u-1', name: 'alice' },
      })
    );

    await waitFor(() => expect(result.current.allData.length).toBe(2));
    const names = result.current.allData.map((c) => c.cluster).sort();
    expect(names).toEqual(['mine-active', 'mine-terminated']);
    expect(names).not.toContain('other-terminated');
  });

  it('drops a stale all-users response that resolves after a newer scoped fetch', async () => {
    // Deep-linking ?owner=mine starts an all-users fetch (initial scope)
    // followed by a scoped fetch once the URL is synced. If the all-users
    // response lands last it must not overwrite the scoped data.
    let resolveAllUsers;
    dashboardCache.get.mockImplementation((fn, args) => {
      if (args && args[0] && args[0].allUsers === false) {
        return Promise.resolve([
          {
            cluster: 'mine-active',
            user_hash: 'u-1',
            user: 'alice',
            status: 'RUNNING',
          },
        ]);
      }
      return new Promise((resolve) => {
        resolveAllUsers = () =>
          resolve([
            {
              cluster: 'other-active',
              user_hash: 'u-2',
              user: 'bob',
              status: 'RUNNING',
            },
          ]);
      });
    });

    // Stable identities: fresh objects every render would recreate the hook's
    // fetch callbacks and trigger spurious refetches.
    const currentUser = { id: 'u-1', name: 'alice' };
    const filters = [];
    const { result, rerender } = renderHook(
      ({ allUsers }) => useClusterData({ allUsers, currentUser, filters }),
      { initialProps: { allUsers: true } }
    );

    // Flip to the scoped fetch while the all-users request is still pending.
    rerender({ allUsers: false });
    await waitFor(() =>
      expect(result.current.allData.map((c) => c.cluster)).toEqual([
        'mine-active',
      ])
    );

    // The stale all-users response resolves last; it must be discarded.
    await act(async () => {
      resolveAllUsers();
    });
    expect(result.current.allData.map((c) => c.cluster)).toEqual([
      'mine-active',
    ]);
    expect(result.current.loading).toBe(false);
  });
});
