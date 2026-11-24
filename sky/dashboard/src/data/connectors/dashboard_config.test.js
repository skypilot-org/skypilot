import { apiClient } from '@/data/connectors/client';
import {
  getDashboardConfig,
  invalidateDashboardConfig,
  resetDashboardConfigCache,
} from '@/data/connectors/dashboard_config';

jest.mock('@/data/connectors/client', () => ({
  apiClient: { get: jest.fn() },
}));

const response = (localContexts) => ({
  ok: true,
  json: async () => ({ external_links: [], local_contexts: localContexts }),
});

beforeEach(() => {
  resetDashboardConfigCache();
  apiClient.get.mockReset();
});

test('invalidateDashboardConfig makes the next read refetch', async () => {
  apiClient.get
    .mockResolvedValueOnce(response(['in-cluster']))
    .mockResolvedValueOnce(response(['in-cluster', 'local-named']));

  expect((await getDashboardConfig()).localContexts).toEqual(['in-cluster']);
  expect((await getDashboardConfig()).localContexts).toEqual(['in-cluster']);
  expect(apiClient.get).toHaveBeenCalledTimes(1);

  invalidateDashboardConfig();
  expect((await getDashboardConfig()).localContexts).toEqual([
    'in-cluster',
    'local-named',
  ]);
  expect(apiClient.get).toHaveBeenCalledTimes(2);
});

test('a fetch sent before invalidation does not cache its response', async () => {
  let resolveFirst;
  let resolveSecond;
  apiClient.get
    .mockReturnValueOnce(new Promise((resolve) => (resolveFirst = resolve)))
    .mockReturnValueOnce(new Promise((resolve) => (resolveSecond = resolve)));

  const first = getDashboardConfig();
  invalidateDashboardConfig();
  const second = getDashboardConfig();
  resolveFirst(response([]));
  await first;
  // The stale fetch must not clear the pending newer fetch either.
  const third = getDashboardConfig();
  resolveSecond(response(['local-named']));

  expect((await second).localContexts).toEqual(['local-named']);
  expect((await third).localContexts).toEqual(['local-named']);
  expect((await getDashboardConfig()).localContexts).toEqual(['local-named']);
  expect(apiClient.get).toHaveBeenCalledTimes(2);
});

test('a failed refetch keeps the last good response', async () => {
  apiClient.get
    .mockResolvedValueOnce(response(['local-named']))
    .mockRejectedValueOnce(new Error('down'));

  await getDashboardConfig();
  invalidateDashboardConfig();
  expect((await getDashboardConfig()).localContexts).toEqual(['local-named']);
});
