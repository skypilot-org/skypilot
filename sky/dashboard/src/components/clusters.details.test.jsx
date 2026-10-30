// The clusters table's Details column says why a terminated cluster went
// away. It truncates like the jobs table's, with "show more" opening the full
// text under the row, and a plugin column with the same id replaces it.
jest.mock('next/router', () => ({
  __esModule: true,
  useRouter: () => ({
    isReady: true,
    query: {},
    asPath: '/clusters',
    push: jest.fn(),
  }),
}));
jest.mock('@/lib/cache', () => ({
  __esModule: true,
  default: {
    get: jest.fn(),
    getCached: jest.fn(() => null),
    invalidate: jest.fn(),
    invalidateFunction: jest.fn(),
    setPreloader: jest.fn(),
  },
}));
jest.mock('@/data/connectors/client', () => ({
  ...jest.requireActual('@/data/connectors/client'),
  __esModule: true,
  apiClient: {
    get: jest.fn(async () => ({
      ok: true,
      json: async () => ({ plugins: [] }),
    })),
    fetch: jest.fn(async () => []),
  },
  getCurrentUserInfo: jest.fn(async () => null),
}));

import React from 'react';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import dashboardCache from '@/lib/cache';
import { PluginProvider } from '@/plugins/PluginProvider';
import { Clusters } from '@/components/clusters';

const REASON =
  'Pod evicted by Kubernetes: NoExecute taint skypilot.co/syslog-error on ' +
  'node node-1. Cluster removed by SkyPilot status refresh.';

const terminated = {
  cluster: 'gone-cluster',
  name: 'gone-cluster',
  cluster_hash: 'hash-gone',
  status: 'TERMINATED',
  user: 'alice',
  user_hash: 'hash-alice',
  workspace: 'default',
  infra: 'Kubernetes',
  resources_str: '1x[CPU:1]',
  launched_at: 1700000000,
  duration: 3600,
  autostop: -1,
  to_down: false,
  details: REASON,
};

const openHistory = async () => {
  window.history.replaceState({}, '', '/clusters?owner=all&history=30d');
  dashboardCache.get.mockImplementation(async (fn) => {
    const name = fn?.name || '';
    if (name.includes('Workspaces')) return {};
    if (name.includes('History')) return [terminated];
    return [];
  });
  render(
    <PluginProvider>
      <Clusters />
    </PluginProvider>
  );
  await waitFor(() =>
    expect(screen.getAllByText('gone-cluster').length).toBeGreaterThan(0)
  );
};

const headers = () =>
  screen.getAllByRole('columnheader').map((h) => h.textContent.trim());

afterEach(() => {
  window.history.replaceState({}, '', '/clusters');
});

it('shows the start of the reason and opens the rest on "show more"', async () => {
  await openHistory();
  const table = screen.getByRole('table');
  expect(within(table).getByText(REASON.substring(0, 50))).toBeTruthy();
  expect(within(table).queryByText('Full Details')).toBeNull();

  fireEvent.click(within(table).getByText(/show more/));

  expect(within(table).getByText('Full Details')).toBeTruthy();
  expect(within(table).getByText(REASON)).toBeTruthy();
});

it('has no Details column in the Active view', async () => {
  window.history.replaceState({}, '', '/clusters?owner=all');
  dashboardCache.get.mockImplementation(async (fn) => {
    const name = fn?.name || '';
    if (name.includes('Workspaces')) return {};
    if (name.includes('History')) return [terminated];
    return [{ ...terminated, status: 'UP', details: null }];
  });
  render(
    <PluginProvider>
      <Clusters />
    </PluginProvider>
  );
  await waitFor(() =>
    expect(screen.getAllByText('gone-cluster').length).toBeGreaterThan(0)
  );
  expect(headers()).not.toContain('Details');
});

it('keeps the Duration column in the history view', async () => {
  await openHistory();
  expect(headers()).toEqual(
    expect.arrayContaining(['Duration', 'Details', 'Actions'])
  );
});

it('lets a plugin column with the same id replace Details', async () => {
  await openHistory();
  await act(async () => {
    window.SkyDashboardPluginAPI.registerTableColumn({
      id: 'details',
      table: 'clusters',
      header: { label: 'Details', order: 8.5 },
      cell: { render: (item) => `plugin: ${item.details.slice(0, 10)}` },
    });
  });
  await waitFor(() =>
    expect(screen.getByText('plugin: Pod evicte')).toBeTruthy()
  );
  expect(headers().filter((h) => h.startsWith('Details'))).toHaveLength(1);
});
