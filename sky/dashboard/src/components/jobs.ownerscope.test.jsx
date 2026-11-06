// The Mine/Everyone toggle on the managed jobs page is remembered across
// reloads, like the clusters page's My/All toggle. The stored choice has to be
// settled before the first table fetch, or the page pays for a My Jobs query
// it is about to throw away.
jest.mock('next/router', () => ({
  __esModule: true,
  useRouter: () => ({
    isReady: true,
    query: {},
    asPath: '/jobs',
    push: jest.fn(),
  }),
}));
jest.mock('@/lib/cache', () => ({
  __esModule: true,
  default: {
    get: jest.fn(async () => []),
    getCached: jest.fn(() => null),
    invalidate: jest.fn(),
    invalidateFunction: jest.fn(),
    setPreloader: jest.fn(),
  },
}));
jest.mock('@/lib/cache-preloader', () => ({
  __esModule: true,
  default: {
    // The Refresh button chains off this, so it has to be thenable.
    preloadForPage: jest.fn(() => Promise.resolve()),
    backgroundPreload: jest.fn(),
  },
}));
jest.mock('@/data/connectors/client', () => ({
  ...jest.requireActual('@/data/connectors/client'),
  getCurrentUserInfo: jest.fn(async () => ({ id: 'local', name: 'local' })),
}));
jest.mock('@/plugins/PluginSlot', () => ({
  __esModule: true,
  // Honour `fallback` so the built-in rendering a slot wraps (e.g. the
  // Infra cell) stays under test when no plugin is registered.
  PluginSlot: ({ fallback = null }) => fallback,
}));
jest.mock('@/plugins/PluginProvider', () => ({
  __esModule: true,
  usePluginComponents: () => [],
  useTableColumns: () => [],
  // These assert on the rows themselves, so the page's own columns have to
  // render. With no plugin columns to merge, the real hook reduces to dropping
  // the conditional ones the page turned off.
  useMergedTableColumns: (page, baseColumns, context = {}) =>
    baseColumns.filter((col) =>
      col.conditional ? !!context.shouldShowColumn?.(col.id) : true
    ),
  // Stable identity, like the real (memoized) hook: a fresh array per render
  // would churn the fetch effect's dependencies into a loop.
  usePluginTableFilters: (() => {
    const empty = [];
    return () => empty;
  })(),
  usePluginRoute: () => null,
  getDataEnhancements: () => [],
}));
jest.mock('@/data/connectors/jobs', () => ({
  __esModule: true,
  getManagedJobs: jest.fn(async () => ({ jobs: [], controllerStopped: false })),
  getPoolStatus: jest.fn(async () => []),
  streamManagedJobLogs: jest.fn(),
  handleJobAction: jest.fn(),
  statusGroups: {
    active: [
      'PENDING',
      'RUNNING',
      'RECOVERING',
      'SUBMITTED',
      'STARTING',
      'CANCELLING',
    ],
    finished: [
      'SUCCEEDED',
      'FAILED',
      'CANCELLED',
      'FAILED_SETUP',
      'FAILED_PRECHECKS',
      'FAILED_NO_RESOURCE',
      'FAILED_CONTROLLER',
    ],
  },
}));

const getPaginatedJobs = jest.fn();
jest.mock('@/lib/jobs-cache-manager', () => ({
  __esModule: true,
  default: {
    getPaginatedJobs: (...args) => getPaginatedJobs(...args),
    prefetchNextPage: jest.fn(async () => {}),
    invalidateCache: jest.fn(),
  },
}));

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { getCurrentUserInfo } from '@/data/connectors/client';
import cachePreloader from '@/lib/cache-preloader';
import { ManagedJobs } from '@/components/jobs';

const STORAGE_KEY = 'skypilot-dashboard-jobs-owner-scope';

const EMPTY = {
  jobs: [],
  total: 0,
  totalNoFilter: 0,
  statusCounts: {},
  controllerStopped: false,
  hasNext: false,
};

const openAt = async (query) => {
  window.history.replaceState({}, '', `/jobs${query}`);
  render(<ManagedJobs />);
  await waitFor(() => expect(getPaginatedJobs).toHaveBeenCalled());
};

// The table fetch, as opposed to the one-row Everyone probe behind the empty
// Mine state.
const tableFetches = () =>
  getPaginatedJobs.mock.calls
    .map(([params]) => params)
    .filter((params) => params.limit !== 1);

describe('managed jobs owner scope', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    window.localStorage.clear();
    getCurrentUserInfo.mockResolvedValue({
      id: 'alice',
      name: 'alice@example.com',
    });
    cachePreloader.preloadForPage.mockResolvedValue(undefined);
    getPaginatedJobs.mockResolvedValue(EMPTY);
  });

  it('defaults to My Jobs with nothing stored', async () => {
    await openAt('');
    expect(tableFetches()[0].userMatch).toBe('alice@example.com');
  });

  it('opens on the stored scope without fetching My Jobs first', async () => {
    window.localStorage.setItem(STORAGE_KEY, 'all');
    await openAt('');
    expect(tableFetches().length).toBeGreaterThan(0);
    expect(tableFetches().every((params) => !params.userMatch)).toBe(true);
  });

  it('remembers a click on the toggle', async () => {
    await openAt('');
    fireEvent.click(await screen.findByRole('tab', { name: 'All Jobs' }));
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe('all');
    fireEvent.click(screen.getByRole('tab', { name: 'My Jobs' }));
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe('mine');
  });

  it('lets a shared link win and remembers it', async () => {
    window.localStorage.setItem(STORAGE_KEY, 'all');
    await openAt('?owner=mine');
    expect(tableFetches()[0].userMatch).toBe('alice@example.com');
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe('mine');
  });

  it('ignores a stored value it does not know', async () => {
    window.localStorage.setItem(STORAGE_KEY, 'bogus');
    await openAt('');
    expect(tableFetches()[0].userMatch).toBe('alice@example.com');
  });

  it('does not overwrite the stored choice for an anonymous visitor', async () => {
    window.localStorage.setItem(STORAGE_KEY, 'mine');
    getCurrentUserInfo.mockResolvedValue({ id: 'local', name: 'local' });
    await openAt('');
    expect(tableFetches().every((params) => !params.userMatch)).toBe(true);
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe('mine');
  });
});
