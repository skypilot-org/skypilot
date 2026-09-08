// The clusters page keeps its filter chips and its view state in the address
// bar, so a filtered view can be shared. These cover the round trip -- a link
// arrives as chips and rows, a click leaves as a named parameter -- plus the
// two rules that keep the chip bar and the URL from disagreeing: a
// single-valued property replaces, a multi-valued one means "either".
jest.mock('next/router', () => ({
  __esModule: true,
  useRouter: () => ({ isReady: true, query: {}, asPath: '/clusters' }),
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
jest.mock('@/plugins/PluginSlot', () => ({
  __esModule: true,
  PluginSlot: () => null,
}));
jest.mock('@/plugins/PluginProvider', () => ({
  __esModule: true,
  usePluginComponents: () => [],
  useTableColumns: () => [],
  usePluginRoute: () => null,
}));

import {
  render,
  screen,
  waitFor,
  within,
  fireEvent,
} from '@testing-library/react';
import dashboardCache from '@/lib/cache';
import { Clusters } from '@/components/clusters';

// Statuses and names chosen so none is a substring of another: the shared
// `evaluateCondition` matches on substring, so overlapping fixtures would make
// these assertions mean something other than what they say.
const cluster = (name, status, user, workspace = 'default') => ({
  cluster: name,
  name,
  status,
  user,
  user_hash: `hash-${user}`,
  workspace,
  infra: 'Kubernetes/ctx',
  resources_str: '1x[CPU:2]',
  launched_at: 1700000000,
  autostop: -1,
  to_down: false,
});

const CLUSTERS = [
  cluster('alpha-train', 'UP', 'alice'),
  cluster('beta-eval', 'STOPPED', 'alice'),
  cluster('gamma-build', 'UP', 'bob', 'team-ml'),
];

const openAt = async (search) => {
  window.history.replaceState({}, '', `/clusters${search}`);
  dashboardCache.get.mockImplementation(async (fn) => {
    // The page asks for clusters and for the workspace config; only the first
    // shapes what these tests assert on.
    const name = fn?.name || '';
    if (name.includes('Workspaces')) return {};
    return CLUSTERS;
  });
  render(<Clusters />);
  await waitFor(() => expect(dashboardCache.get).toHaveBeenCalled());
};

const search = () => window.location.search;

// Scoped to the table: a chip renders its value too, so an unscoped query would
// match the filter bar as well as the row it selected.
const clusterNames = () => {
  const table = screen.queryByRole('table');
  if (!table) return [];
  const scoped = within(table);
  return CLUSTERS.map((c) => c.name).filter(
    (name) => scoped.queryAllByText(name).length > 0
  );
};

// Type into the dropdown and press Enter, the way a user adds a chip. The
// property selector is a Radix listbox that jsdom cannot drive, so this always
// adds on the dropdown's default property, `Cluster` -- a single-valued one.
const addChipOnDefaultProperty = async (value) => {
  const input = screen.getByPlaceholderText('Filter clusters');
  fireEvent.change(input, { target: { value } });
  fireEvent.keyDown(input, { key: 'Enter' });
  await waitFor(() => expect(search()).toContain(value));
};

describe('clusters filters in the URL', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    window.history.replaceState({}, '', '/clusters');
  });

  it('opens a shared link already filtered', async () => {
    await openAt('?owner=all&user=alice');
    await waitFor(() =>
      expect(clusterNames()).toEqual(['alpha-train', 'beta-eval'])
    );
  });

  it('reads several values on one property as alternatives', async () => {
    await openAt('?owner=all&status=UP,STOPPED');
    await waitFor(() => expect(clusterNames()).toHaveLength(3));
  });

  it('combines different properties as conditions', async () => {
    await openAt('?owner=all&status=UP&user=bob');
    await waitFor(() => expect(clusterNames()).toEqual(['gamma-build']));
  });

  it('keeps a comma list readable rather than percent-encoding it', async () => {
    // The hook rewrites the address bar from its own state on mount, so this is
    // the round trip, not just the link it was handed.
    await openAt('?owner=all&status=UP,STOPPED');
    await waitFor(() => expect(search()).toContain('status=UP,STOPPED'));
  });

  it('names the parameter after the property when a chip is added', async () => {
    await openAt('?owner=all');
    await addChipOnDefaultProperty('alpha');
    expect(search()).toBe('?owner=all&cluster=alpha');
    await waitFor(() => expect(clusterNames()).toEqual(['alpha-train']));
  });

  it('replaces rather than stacks a single-valued property', async () => {
    await openAt('?owner=all&cluster=alpha-train');
    await addChipOnDefaultProperty('beta-eval');
    // One chip, one value: the chip bar cannot show a filter that the URL and
    // the table would disagree about. `status` is the multi-valued one, covered
    // by the alternatives case above.
    expect(search()).toBe('?owner=all&cluster=beta-eval');
    await waitFor(() => expect(clusterNames()).toEqual(['beta-eval']));
  });

  it('ignores a parameter no property claims', async () => {
    await openAt('?owner=all&nonsense=1&user=bob');
    await waitFor(() => expect(clusterNames()).toEqual(['gamma-build']));
  });

  it('carries the history window as one parameter', async () => {
    await openAt('?owner=all&history=10d');
    await waitFor(() => expect(search()).toContain('history=10d'));
  });
});
