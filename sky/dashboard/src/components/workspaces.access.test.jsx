// Opening a workspace's page from the Workspaces list: admins always can; a
// non-admin can only when a plugin access check for `workspaces.detail.open`
// allows it, and otherwise gets the permission-denied dialog.
const mockPush = jest.fn();
jest.mock('next/router', () => ({
  __esModule: true,
  useRouter: () => ({
    isReady: true,
    query: {},
    asPath: '/workspaces',
    push: mockPush,
  }),
}));

const mockGetWorkspaces = jest.fn();
const mockGetEnabledCloudsBatch = jest.fn();
const mockGetClusters = jest.fn();
const mockGetManagedJobs = jest.fn();
const mockUpdateWorkspace = jest.fn();
jest.mock('@/data/connectors/workspaces', () => ({
  __esModule: true,
  getWorkspaces: (...args) => mockGetWorkspaces(...args),
  getEnabledCloudsBatch: (...args) => mockGetEnabledCloudsBatch(...args),
  deleteWorkspace: jest.fn(),
  updateWorkspace: (...args) => mockUpdateWorkspace(...args),
}));
jest.mock('@/data/connectors/clusters', () => ({
  __esModule: true,
  getClusters: (...args) => mockGetClusters(...args),
}));
jest.mock('@/data/connectors/jobs', () => ({
  __esModule: true,
  getManagedJobs: (...args) => mockGetManagedJobs(...args),
}));
jest.mock('@/lib/cache', () => {
  const cache = {
    get: (fn, args = []) => fn(...args),
    invalidate: jest.fn(),
    invalidateFunction: jest.fn(),
  };
  return { __esModule: true, default: cache, dashboardCache: cache };
});
jest.mock('@/lib/cache-preloader', () => ({
  __esModule: true,
  default: { preloadForPage: jest.fn(async () => {}) },
}));

let mockRole = 'user';
jest.mock('@/data/connectors/client', () => ({
  __esModule: true,
  apiClient: {
    get: jest.fn(async () => ({
      ok: true,
      json: async () => ({ role: mockRole, name: 'Alice' }),
    })),
  },
}));

let mockChecks = [];
jest.mock('@/plugins/PluginProvider', () => {
  const actual = jest.requireActual('@/plugins/PluginProvider');
  return {
    __esModule: true,
    anyAccessCheckAllows: actual.anyAccessCheckAllows,
    usePluginAccessChecks: (action) =>
      action === 'workspaces.detail.open' ? mockChecks : [],
    usePluginComponents: () => [],
  };
});
jest.mock('@/plugins/PluginSlot', () => ({
  __esModule: true,
  PluginSlot: () => null,
}));

import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { Workspaces } from '@/components/workspaces';
import dashboardCache from '@/lib/cache';
import { getWorkspaceContexts } from '@/data/connectors/infra';

beforeEach(() => {
  mockPush.mockReset();
  mockRole = 'user';
  mockChecks = [];
  mockGetWorkspaces.mockResolvedValue({
    ws: { private: true, allowed_users: ['alice'] },
  });
  mockGetEnabledCloudsBatch.mockResolvedValue({ ws: [] });
  mockGetClusters.mockResolvedValue([]);
  mockGetManagedJobs.mockResolvedValue({ jobs: [] });
});

async function openWorkspace() {
  render(<Workspaces />);
  const link = await screen.findByRole('button', { name: 'ws' });
  await act(async () => {
    fireEvent.click(link);
  });
}

test('an admin opens the workspace without consulting plugins', async () => {
  mockRole = 'admin';
  const check = jest.fn(async () => false);
  mockChecks = [{ id: 'p', action: 'workspaces.detail.open', check }];
  await openWorkspace();
  expect(mockPush).toHaveBeenCalledWith('/workspaces/ws');
  expect(check).not.toHaveBeenCalled();
});

test('a non-admin is refused when no plugin allows it', async () => {
  await openWorkspace();
  expect(mockPush).not.toHaveBeenCalled();
  expect(screen.getByText(/is logged in as non-admin/)).toBeTruthy();
});

test('a non-admin opens the workspace when a plugin check allows it', async () => {
  const check = jest.fn(async ({ workspaceName }) => workspaceName === 'ws');
  mockChecks = [{ id: 'p', action: 'workspaces.detail.open', check }];
  await openWorkspace();
  expect(check).toHaveBeenCalledWith({ workspaceName: 'ws' });
  expect(mockPush).toHaveBeenCalledWith('/workspaces/ws');
});

test('a failing plugin check counts as a refusal', async () => {
  jest.spyOn(console, 'warn').mockImplementation(() => {});
  mockChecks = [
    {
      id: 'p',
      action: 'workspaces.detail.open',
      check: async () => {
        throw new Error('boom');
      },
    },
  ];
  await openWorkspace();
  expect(mockPush).not.toHaveBeenCalled();
  expect(screen.getByText(/is logged in as non-admin/)).toBeTruthy();
  console.warn.mockRestore();
});

describe('renaming from the list', () => {
  const pencils = () =>
    screen.queryAllByRole('button', { name: 'Rename workspace' });

  beforeEach(() => {
    mockGetWorkspaces.mockResolvedValue({
      default: {},
      ws: { private: true, allowed_users: ['alice'] },
    });
    mockGetEnabledCloudsBatch.mockResolvedValue({ default: [], ws: [] });
    mockUpdateWorkspace.mockReset();
  });

  async function renderList() {
    render(<Workspaces />);
    await screen.findByRole('button', { name: 'ws' });
    // Let the role lookup settle.
    await act(async () => {});
  }

  test('an admin gets a pencil on every workspace but default', async () => {
    mockRole = 'admin';
    await renderList();
    expect(pencils()).toHaveLength(1);
    expect(pencils()[0].closest('tr').textContent).toContain('ws');
  });

  test('a non-admin gets no pencil', async () => {
    await renderList();
    expect(pencils()).toHaveLength(0);
  });

  test('a rename refreshes the list', async () => {
    mockRole = 'admin';
    mockUpdateWorkspace.mockImplementation(async (id, config) => {
      mockGetWorkspaces.mockResolvedValue({ default: {}, [id]: config });
      return {};
    });
    await renderList();
    fireEvent.click(pencils()[0]);
    fireEvent.change(screen.getByLabelText('Name'), {
      target: { value: 'ml' },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Rename' }));
    });
    expect(mockUpdateWorkspace).toHaveBeenCalledWith(
      'ws',
      { private: true, allowed_users: ['alice'], name: 'ml' },
      { private: true, allowed_users: ['alice'] }
    );
    expect(await screen.findByRole('button', { name: 'ml' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'ws' })).toBeNull();
    expect(screen.getByText('Workspace renamed to "ml".')).toBeTruthy();
  });

  test('a delete drops the cached infra contexts', async () => {
    mockRole = 'admin';
    await renderList();
    await act(async () => {
      fireEvent.click(screen.getByTitle('Delete workspace'));
    });
    dashboardCache.invalidate.mockClear();
    await act(async () => {
      fireEvent.click(
        within(screen.getByRole('dialog')).getByRole('button', {
          name: 'Delete',
        })
      );
    });
    expect(dashboardCache.invalidate).toHaveBeenCalledWith(
      getWorkspaceContexts
    );
  });
});
