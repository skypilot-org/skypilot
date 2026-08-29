// The managed-jobs page keeps its whole status UI -- the Active/All segments and
// the pill bar -- plus its filter chips in the address bar, so a filtered view
// can be shared. `jobs.statusview.test.jsx` covers the derivation in isolation;
// these drive the rendered page, which is where the widgets that read that
// state actually live.
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
  default: { preloadForPage: jest.fn(), backgroundPreload: jest.fn() },
}));
jest.mock('@/plugins/PluginSlot', () => ({
  __esModule: true,
  PluginSlot: () => null,
}));
jest.mock('@/plugins/PluginProvider', () => ({
  __esModule: true,
  usePluginComponents: () => [],
  useTableColumns: () => [],
  // Empty on purpose: the table's own columns are not what these assert on, and
  // rendering them here would require emulating the plugin column merge.
  useMergedTableColumns: () => [],
  usePluginRoute: () => null,
  getDataEnhancements: () => [],
}));
// The page reads its rows straight from this hook, so the fixtures go in here
// rather than through the cache: the point under test is the URL round trip,
// not the fetch.
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

import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { ManagedJobs } from '@/components/jobs';

const search = () => window.location.search;

const openAt = async (query) => {
  window.history.replaceState({}, '', `/jobs${query}`);
  render(<ManagedJobs />);
  await waitFor(() =>
    expect(screen.getByPlaceholderText('Filter jobs')).toBeTruthy()
  );
};

const segments = () =>
  [...document.querySelectorAll('[role="tab"]')].map((t) => ({
    label: t.textContent.trim(),
    selected: t.getAttribute('aria-selected') === 'true',
  }));

const selectedSegment = () => segments().find((s) => s.selected)?.label ?? null;

describe('managed jobs status and filters in the URL', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    window.history.replaceState({}, '', '/jobs');
  });

  it('highlights the segment a shared group link names', async () => {
    await openAt('?status=active');
    await waitFor(() => expect(selectedSegment()).toBe('Active'));
  });

  it('highlights All when the link carries no status', async () => {
    await openAt('');
    await waitFor(() => expect(selectedSegment()).toBe('All'));
  });

  it('highlights neither segment while specific pills narrow the view', async () => {
    // A comma list is the pill form: the segments describe groups, and this is
    // not one of them.
    await openAt('?status=RUNNING,SUCCEEDED');
    await waitFor(() => expect(selectedSegment()).toBe(null));
  });

  it('falls back to All rather than a group that does not exist', async () => {
    // `?status=,` is truthy but names nothing. Before this was guarded, the
    // pill highlighting dereferenced a missing status group and the page
    // rendered an error instead of the table.
    await openAt('?status=,');
    await waitFor(() => expect(selectedSegment()).toBe('All'));
    expect(document.body.textContent).not.toContain('client-side exception');
  });

  it('writes the group name when a segment is clicked', async () => {
    await openAt('');
    const active = [...document.querySelectorAll('[role="tab"]')].find(
      (t) => t.textContent.trim() === 'Active'
    );
    fireEvent.click(active);
    await waitFor(() => expect(search()).toContain('status=active'));
  });

  it('names the parameter after the property when a chip is added', async () => {
    await openAt('');
    const input = screen.getByPlaceholderText('Filter jobs');
    fireEvent.change(input, { target: { value: 'train-llama' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    // `Name` is the dropdown's default property; the selector itself is a Radix
    // listbox that jsdom cannot drive.
    await waitFor(() => expect(search()).toContain('name=train-llama'));
  });

  it('replaces rather than stacks a single-valued property', async () => {
    await openAt('?name=train-llama');
    const input = screen.getByPlaceholderText('Filter jobs');
    fireEvent.change(input, { target: { value: 'eval-suite' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(search()).toContain('name=eval-suite'));
    expect(search()).not.toContain('train-llama');
  });

  it('keeps a comma list readable rather than percent-encoding it', async () => {
    await openAt('?status=RUNNING,SUCCEEDED');
    await waitFor(() => expect(search()).toContain('status=RUNNING,SUCCEEDED'));
  });
});
