// The volumes table keeps its filter chips in the address bar, so a filtered
// view can be shared. These cover the round trip -- a link arrives as chips and
// rows, a click leaves as a named parameter -- and the two rules that keep the
// chip bar and the URL from disagreeing: a single-valued property replaces,
// and a multi-valued one means "either".
jest.mock('next/router', () => ({
  __esModule: true,
  useRouter: () => ({ isReady: true, query: {}, asPath: '/volumes' }),
}));
jest.mock('@/lib/cache', () => ({
  __esModule: true,
  default: { get: jest.fn(), invalidate: jest.fn(), setPreloader: jest.fn() },
}));
jest.mock('@/plugins/PluginSlot', () => ({
  __esModule: true,
  PluginSlot: () => null,
}));
jest.mock('@/plugins/PluginProvider', () => ({
  __esModule: true,
  usePluginComponents: () => [],
  useTableColumns: () => [],
}));

import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import dashboardCache from '@/lib/cache';
import { VolumesTable } from '@/components/volumes';

const volume = (name, status, user = 'alice') => ({
  name,
  status,
  error_message: null,
  type: 'k8s-pvc',
  infra: 'Kubernetes/ctx',
  size: 1,
  user_name: user,
});

// The real VolumeStatus values. The shared `evaluateCondition` matches on
// substring, and `READY` is a substring of `NOT_READY`, so the assertions below
// filter on `IN_USE` / `NOT_READY`. Names are chosen the same way -- none is a
// substring of another.
const VOLUMES = [
  volume('ready-one', 'READY'),
  volume('inuse-one', 'IN_USE'),
  volume('inuse-two', 'IN_USE', 'bob'),
  volume('blocked-one', 'NOT_READY', 'bob'),
];

const openAt = async (search) => {
  window.history.replaceState({}, '', `/volumes${search}`);
  dashboardCache.get.mockResolvedValue(VOLUMES);
  render(
    <VolumesTable
      refreshInterval={100000}
      setLoading={() => {}}
      refreshDataRef={{ current: null }}
      onDeleteVolume={() => {}}
      preloadingComplete={true}
    />
  );
  await waitFor(() => expect(dashboardCache.get).toHaveBeenCalled());
};

// Scoped to the table: a chip renders its value too, so an unscoped query
// would match the filter bar as well as the row it selected.
const volumeNames = () => {
  const table = within(screen.getByRole('table'));
  return VOLUMES.map((v) => v.name).filter(
    (name) => table.queryAllByText(name).length > 0
  );
};

const search = () => window.location.search;

// Type into the dropdown and press Enter, the way a user adds a chip. The
// property selector is a Radix listbox that jsdom cannot drive, so this always
// adds on the default property, `Name`.
const addNameChip = async (value) => {
  const input = screen.getByPlaceholderText('Filter volumes');
  fireEvent.change(input, { target: { value } });
  fireEvent.keyDown(input, { key: 'Enter' });
  await waitFor(() => expect(search()).toContain(value));
};

describe('volumes filters in the URL', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    window.history.replaceState({}, '', '/volumes');
  });

  it('opens a shared link already filtered', async () => {
    await openAt('?status=IN_USE');
    await waitFor(() =>
      expect(volumeNames()).toEqual(['inuse-one', 'inuse-two'])
    );
  });

  it('reads several values on one property as alternatives', async () => {
    await openAt('?status=IN_USE,NOT_READY');
    await waitFor(() =>
      expect(volumeNames()).toEqual(['inuse-one', 'inuse-two', 'blocked-one'])
    );
  });

  it('combines different properties as conditions', async () => {
    await openAt('?status=IN_USE&user=bob');
    await waitFor(() => expect(volumeNames()).toEqual(['inuse-two']));
  });

  it('names the parameter after the property when a chip is added', async () => {
    await openAt('');
    await addNameChip('ready-one');
    expect(search()).toBe('?name=ready-one');
    await waitFor(() => expect(volumeNames()).toEqual(['ready-one']));
  });

  it('replaces rather than stacks a single-valued property', async () => {
    await openAt('?name=ready-one');
    await addNameChip('blocked-one');
    // One chip, one value: the chip bar cannot show a filter that the URL and
    // the table would disagree about.
    expect(search()).toBe('?name=blocked-one');
    await waitFor(() => expect(volumeNames()).toEqual(['blocked-one']));
  });

  it('keeps a comma readable rather than percent-encoding it', async () => {
    // The hook rewrites the address bar from its own state on mount, so this
    // is the round trip, not just the link it was handed.
    await openAt('?status=IN_USE,NOT_READY');
    await waitFor(() => expect(search()).toBe('?status=IN_USE,NOT_READY'));
  });

  it('ignores a parameter no property claims', async () => {
    await openAt('?nonsense=1&status=IN_USE');
    await waitFor(() =>
      expect(volumeNames()).toEqual(['inuse-one', 'inuse-two'])
    );
  });
});
