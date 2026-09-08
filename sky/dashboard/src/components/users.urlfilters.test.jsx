// The users page keeps its filter chips and the deduplicate toggle in the
// address bar. `users.test.jsx` covers the schema round trip in isolation;
// these drive the rendered page, where the widgets that read that state live --
// including the GPU and Infra filters, which are multi-valued because the
// page's own counting path reads several values on either as alternatives.
jest.mock('next/router', () => ({
  __esModule: true,
  useRouter: () => ({
    isReady: true,
    query: {},
    asPath: '/users',
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
// The page reads the sidebar's collapsed state to size its layout; the layout
// is not what these assert on.
jest.mock('@/components/elements/sidebar', () => ({
  __esModule: true,
  useSidebar: () => ({ isCollapsed: false, state: 'expanded', open: true }),
}));
jest.mock('@/plugins/PluginSlot', () => ({
  __esModule: true,
  PluginSlot: () => null,
}));
jest.mock('@/plugins/PluginProvider', () => ({
  __esModule: true,
  usePluginComponents: () => [],
  useTableColumns: () => [],
  useMergedTableColumns: () => [],
  usePluginRoute: () => null,
  getDataEnhancements: () => [],
}));

import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { Users } from '@/components/users';

const search = () => window.location.search;

const openAt = async (query) => {
  window.history.replaceState({}, '', `/users${query}`);
  render(<Users />);
  await waitFor(() =>
    expect(screen.getByPlaceholderText('Filter users')).toBeTruthy()
  );
};

// A chip renders its property and its value in separate children, so read the
// pill container's whole text rather than any single leaf.
const chips = () =>
  [...document.querySelectorAll('.rounded-full')]
    .map((e) => e.textContent.replace(/\s+/g, ' ').trim())
    .filter((t) => t.includes(':'));

const dedupeToggle = () =>
  document.querySelector('input[type="checkbox"].sr-only');

describe('users filters in the URL', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    window.history.replaceState({}, '', '/users');
  });

  it('shows a chip for each value a shared GPU link carries', async () => {
    // The multi-valued case: one parameter, two values, two chips. Collapsing
    // it to one silently narrows an already-shared link.
    await openAt('?gpu=A100,A10G');
    await waitFor(() => {
      const gpu = chips().filter((t) => /^GPU\s*:/.test(t));
      expect(gpu).toHaveLength(2);
    });
  });

  it('reads the renamed user-id key', async () => {
    await openAt('?userId=hash-alice');
    await waitFor(() =>
      expect(chips().some((t) => /^User ID\s*:\s*hash-alice/.test(t))).toBe(
        true
      )
    );
  });

  it('migrates a legacy triple link, including the old gpu spelling', async () => {
    await openAt('?property=gpu%20type&operator=%3A&value=A100');
    await waitFor(() => expect(search()).toBe('?gpu=A100'));
  });

  it('keeps a comma list readable rather than percent-encoding it', async () => {
    await openAt('?gpu=A100,A10G');
    await waitFor(() => expect(search()).toBe('?gpu=A100,A10G'));
  });

  it('leaves the deduplicate default out of the URL', async () => {
    await openAt('');
    await waitFor(() => expect(dedupeToggle()).toBeTruthy());
    expect(search()).toBe('');
    expect(dedupeToggle().checked).toBe(true);
  });

  it('writes the deduplicate toggle only when it leaves its default', async () => {
    await openAt('');
    await waitFor(() => expect(dedupeToggle()).toBeTruthy());
    fireEvent.click(dedupeToggle());
    await waitFor(() => expect(search()).toBe('?deduplicate=false'));
    fireEvent.click(dedupeToggle());
    await waitFor(() => expect(search()).toBe(''));
  });

  it('names the parameter after the property when a chip is added', async () => {
    await openAt('');
    const input = screen.getByPlaceholderText('Filter users');
    fireEvent.change(input, { target: { value: 'alice' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    // `Name` is the dropdown's default property.
    await waitFor(() => expect(search()).toBe('?name=alice'));
  });

  it('ignores a parameter no property claims, without discarding it', async () => {
    // The hook owns only the keys its schema declares; anything else on the
    // page (a plugin's own param, `tab`) has to survive a filter edit. So an
    // unknown key is not turned into a chip, and is not dropped either.
    await openAt('?nonsense=1&role=admin');
    await waitFor(() =>
      expect(chips().some((t) => /^Role\s*:\s*admin/.test(t))).toBe(true)
    );
    expect(chips().some((t) => /nonsense/.test(t))).toBe(false);
    expect(search()).toContain('nonsense=1');
  });
});
