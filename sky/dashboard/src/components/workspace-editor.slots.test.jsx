// Plugin slots on the workspace editor's allowed-users list: without plugins
// the built-in role badges render; a plugin registered for a slot replaces the
// badge and receives the row's context.
import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';

const mockComponents = {};
jest.mock('@/plugins/PluginProvider', () => ({
  __esModule: true,
  usePluginComponents: (slot) => mockComponents[slot] || [],
}));
jest.mock('next/router', () => ({
  __esModule: true,
  useRouter: () => ({ isReady: true, query: {}, push: jest.fn() }),
}));
jest.mock('@/components/elements/sidebar', () => ({
  __esModule: true,
  TopBar: () => null,
}));
// Only needed by the WorkspaceEditor test below: stub the page chrome, the data
// connectors and the Monaco-based YAML editor.
jest.mock('@/components/elements/layout', () => ({
  __esModule: true,
  Layout: ({ children }) => <>{children}</>,
}));
jest.mock('@/components/ui/yaml-editor', () => ({
  __esModule: true,
  YamlEditor: ({ value, onChange, disabled }) => (
    <textarea
      aria-label="workspace yaml"
      readOnly={disabled}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  ),
}));
jest.mock('./jobs', () => ({ __esModule: true, statusGroups: { active: [] } }));
const mockGetWorkspaces = jest.fn();
const mockUpdateWorkspace = jest.fn(async () => ({}));
jest.mock('@/data/connectors/workspaces', () => ({
  __esModule: true,
  getWorkspaces: (...args) => mockGetWorkspaces(...args),
  updateWorkspace: (...args) => mockUpdateWorkspace(...args),
  createWorkspace: jest.fn(),
  deleteWorkspace: jest.fn(),
  getEnabledClouds: jest.fn(async () => []),
}));
const mockGetUserRole = jest.fn(async () => ({ role: 'admin', name: 'root' }));
jest.mock('@/data/connectors/users', () => ({
  __esModule: true,
  getUserRole: (...args) => mockGetUserRole(...args),
  getUsers: jest.fn(async () => [
    { userId: 'u-alice', username: 'alice', role: 'user' },
    { userId: 'u-bob', username: 'bob', role: 'user' },
  ]),
}));
const mockApiFetch = jest.fn(async () => ({ ok: true }));
jest.mock('@/data/connectors/client', () => ({
  __esModule: true,
  apiClient: { fetch: (...args) => mockApiFetch(...args) },
}));
const mockGetClusters = jest.fn(async () => []);
jest.mock('@/data/connectors/clusters', () => ({
  __esModule: true,
  getClusters: (...args) => mockGetClusters(...args),
}));
jest.mock('@/data/connectors/jobs', () => ({
  __esModule: true,
  getManagedJobs: jest.fn(async () => ({ jobs: [] })),
}));
const mockInvalidate = jest.fn();
const mockCacheGet = jest.fn((fn, args = []) => fn(...args));
jest.mock('@/lib/cache', () => ({
  __esModule: true,
  dashboardCache: {
    get: (...args) => mockCacheGet(...args),
    invalidate: (...args) => mockInvalidate(...args),
    invalidateFunction: jest.fn(),
  },
}));

import {
  DetailedAllowedUsers,
  WorkspaceEditor,
} from '@/components/workspace-editor';

const config = { private: true, allowed_users: ['alice', 'bob'] };
const users = [
  { username: 'alice', role: 'user' },
  { username: 'bob', role: 'user' },
  { username: 'root', role: 'admin' },
];

afterEach(() => {
  for (const key of Object.keys(mockComponents)) delete mockComponents[key];
});

test('renders the built-in role badges without plugins', () => {
  render(
    <DetailedAllowedUsers
      workspaceName="ws"
      workspaceConfig={config}
      allUsers={users}
    />
  );
  expect(screen.getByText('Allowed Users (3)')).toBeInTheDocument();
  expect(screen.getAllByText('User')).toHaveLength(2);
  expect(screen.getByText('Admin')).toBeInTheDocument();
});

test('a role plugin replaces the badge and gets the row context', () => {
  const seen = [];
  const onChanged = jest.fn();
  mockComponents['workspaces.detail.allowedUser.role'] = [
    {
      id: 'role',
      component: (props) => {
        seen.push(props);
        return <span>{`role:${props.entry}`}</span>;
      },
    },
  ];
  mockComponents['workspaces.detail.allowedUsers.actions'] = [
    {
      id: 'actions',
      component: ({ allowedUsers }) => (
        <span>{`actions:${allowedUsers.join(',')}`}</span>
      ),
    },
  ];
  render(
    <DetailedAllowedUsers
      workspaceName="ws"
      workspaceConfig={config}
      allUsers={users}
      onChanged={onChanged}
    />
  );
  expect(screen.queryByText('User')).not.toBeInTheDocument();
  expect(screen.getByText('role:alice')).toBeInTheDocument();
  // The workspace's own allowed_users, without the global admins the list
  // also displays.
  expect(screen.getByText('actions:alice,bob')).toBeInTheDocument();
  const root = seen.find((p) => p.entry === 'root');
  expect(root).toMatchObject({ workspaceName: 'ws', isAdmin: true });
  root.onChanged();
  expect(onChanged).toHaveBeenCalled();
});

test('an admin listed by user id is flagged as admin', () => {
  const seen = [];
  mockComponents['workspaces.detail.allowedUser.role'] = [
    {
      id: 'role',
      component: (props) => {
        seen.push(props);
        return null;
      },
    },
  ];
  render(
    <DetailedAllowedUsers
      workspaceName="ws"
      workspaceConfig={{ private: true, allowed_users: ['u-root'] }}
      allUsers={[{ userId: 'u-root', username: 'root', role: 'admin' }]}
    />
  );
  expect(seen.find((p) => p.entry === 'u-root')).toMatchObject({
    isAdmin: true,
  });
});

test('non-members see no roster and no plugin controls', () => {
  mockComponents['workspaces.detail.allowedUsers.actions'] = [
    { id: 'actions', component: () => <span>actions</span> },
  ];
  const { container } = render(
    <DetailedAllowedUsers
      workspaceName="ws"
      workspaceConfig={config}
      allUsers={users}
      writable={false}
    />
  );
  expect(container).toBeEmptyDOMElement();
});

describe('WorkspaceEditor with a plugin that changes the workspace', () => {
  // The server's copy of the workspace. The plugin adds bob to it and asks the
  // page to refresh; Apply writes the editor's config into it, refusing (as
  // the real server does under its config lock) when `expected` is stale.
  let server;
  const sorted = (v) =>
    Array.isArray(v)
      ? v.map(sorted)
      : v && typeof v === 'object'
        ? Object.fromEntries(
            Object.keys(v)
              .sort()
              .map((k) => [k, sorted(v[k])])
          )
        : v;
  const same = (a, b) =>
    JSON.stringify(sorted(a || {})) === JSON.stringify(sorted(b || {}));
  const serverUpdate = async (name, config, expected) => {
    if (expected !== undefined && !same(server[name], expected)) {
      const err = new Error('updateWorkspace failed: changed');
      err.type = 'WorkspaceConfigConflictError';
      throw err;
    }
    server = { ...server, [name]: config };
    return {};
  };
  const addBob = () => {
    const ws = server.ws;
    server = { ws: { ...ws, allowed_users: [...ws.allowed_users, 'bob'] } };
  };

  beforeEach(() => {
    server = { ws: { private: true, allowed_users: ['alice'] } };
    mockGetWorkspaces.mockReset();
    mockGetWorkspaces.mockImplementation(async () => server);
    mockUpdateWorkspace.mockReset();
    mockUpdateWorkspace.mockImplementation(serverUpdate);
    mockComponents['workspaces.detail.allowedUsers.actions'] = [
      {
        id: 'actions',
        component: ({ onChanged }) => (
          <button
            onClick={() => {
              addBob();
              onChanged();
            }}
          >
            plugin change
          </button>
        ),
      },
    ];
  });

  const yamlBox = () => screen.getByLabelText('workspace yaml');

  async function renderEditor() {
    render(<WorkspaceEditor workspaceName="ws" />);
    await screen.findByText('Allowed Users (1)', {}, { timeout: 3000 });
    await screen.findByText('Apply');
  }

  async function pluginRefresh() {
    await act(async () => {
      fireEvent.click(screen.getByText('plugin change'));
    });
    await screen.findByText('Allowed Users (2)');
  }

  async function edit(fn) {
    const draft = fn(yamlBox().value);
    fireEvent.change(yamlBox(), { target: { value: draft } });
    return draft;
  }

  async function apply() {
    await act(async () => {
      fireEvent.click(screen.getByText('Apply'));
    });
  }

  test('refreshes the roster and YAML when there are no unsaved edits', async () => {
    await renderEditor();
    await pluginRefresh();
    expect(yamlBox().value).toContain('bob');
  });

  test.each([
    ['a comment-only edit', (y) => `# who is here and why\n${y}`],
    ['invalid YAML', (y) => `${y}  gcp: [unclosed\n`],
    ['a config edit', (y) => `${y}  gcp:\n    project_id: draft\n`],
  ])('keeps %s while refreshing the roster', async (_, change) => {
    await renderEditor();
    const draft = await edit(change);
    await pluginRefresh();
    expect(yamlBox().value).toBe(draft);
    expect(screen.getByText('bob')).toBeInTheDocument();
  });

  test('Apply with no concurrent change saves directly', async () => {
    await renderEditor();
    await edit((y) => `${y}  gcp:\n    project_id: saved\n`);
    await apply();
    expect(mockUpdateWorkspace).toHaveBeenCalledTimes(1);
    expect(
      screen.queryByText('Workspace changed since you started editing')
    ).not.toBeInTheDocument();
  });

  test('after Apply, a plugin refresh updates the YAML again', async () => {
    await renderEditor();
    await edit((y) => `${y}  gcp:\n    project_id: saved\n`);
    await apply();
    expect(mockUpdateWorkspace).toHaveBeenCalledTimes(1);
    await pluginRefresh();
    expect(yamlBox().value).toContain('bob');
  });

  test('Apply after a change made on this page asks before overwriting', async () => {
    await renderEditor();
    await edit((y) => `# note\n${y}`);
    await pluginRefresh();
    await apply();
    expect(
      screen.getByText('Workspace changed since you started editing')
    ).toBeInTheDocument();
    // The server refused the write; nothing was overwritten.
    expect(server.ws.allowed_users).toContain('bob');

    await act(async () => {
      fireEvent.click(screen.getByText('Apply anyway'));
    });
    // Apply anyway sends no expected config, so it goes through.
    expect(mockUpdateWorkspace).toHaveBeenLastCalledWith(
      'ws',
      expect.anything(),
      undefined
    );
    expect(server.ws.allowed_users).toEqual(['alice']);
  });

  test('Reload latest discards the draft and loads the server copy', async () => {
    await renderEditor();
    await edit((y) => `# note\n${y}`);
    await pluginRefresh();
    await apply();
    await act(async () => {
      fireEvent.click(screen.getByText('Reload latest'));
    });
    expect(server.ws.allowed_users).toEqual(['alice', 'bob']);
    expect(yamlBox().value).toContain('bob');
    expect(yamlBox().value).not.toContain('# note');
  });

  test('a change landing after a save is caught at the next Apply', async () => {
    // Someone else's write lands right after ours, before anything after the
    // save could read the server.
    mockUpdateWorkspace.mockImplementationOnce(async (...args) => {
      await serverUpdate(...args);
      addBob();
      return {};
    });
    await renderEditor();
    await edit((y) => `${y}  gcp:\n    project_id: first\n`);
    await apply();
    expect(mockUpdateWorkspace).toHaveBeenCalledTimes(1);
    await edit((y) => y.replace('first', 'second'));
    await apply();
    expect(
      screen.getByText('Workspace changed since you started editing')
    ).toBeInTheDocument();
    // The second write was refused: bob's change and our first save survive.
    expect(server.ws.allowed_users).toEqual(['alice', 'bob']);
    expect(server.ws.gcp.project_id).toBe('first');
  });

  test('a change made elsewhere (another tab) is refused at Apply', async () => {
    await renderEditor();
    await edit((y) => `${y}  gcp:\n    project_id: mine\n`);
    addBob(); // not refreshed on this page, e.g. up to the moment of Apply
    await apply();
    expect(
      screen.getByText('Workspace changed since you started editing')
    ).toBeInTheDocument();
    expect(server.ws.allowed_users).toEqual(['alice', 'bob']);
    expect(server.ws.gcp).toBeUndefined();
  });

  test('Apply sends the config the draft started from', async () => {
    await renderEditor();
    await edit((y) => `${y}  gcp:\n    project_id: mine\n`);
    await apply();
    expect(mockUpdateWorkspace).toHaveBeenCalledWith(
      'ws',
      { private: true, allowed_users: ['alice'], gcp: { project_id: 'mine' } },
      { private: true, allowed_users: ['alice'] }
    );
  });
});

describe('WorkspaceEditor for a non-admin', () => {
  beforeEach(() => {
    mockGetWorkspaces.mockReset();
    mockGetWorkspaces.mockImplementation(async () => ({
      ws: { private: true, allowed_users: ['alice'] },
    }));
    mockGetUserRole.mockImplementation(async () => ({
      role: 'user',
      name: 'alice',
    }));
    mockComponents['workspaces.detail.allowedUsers.actions'] = [
      {
        id: 'actions',
        component: () => <span>plugin controls</span>,
      },
    ];
  });

  afterEach(() => {
    mockGetUserRole.mockImplementation(async () => ({
      role: 'admin',
      name: 'root',
    }));
  });

  test('shows the workspace read-only, with plugin controls', async () => {
    render(<WorkspaceEditor workspaceName="ws" />);
    await screen.findByText('Only admins can change this configuration.');
    expect(screen.getByText('Allowed Users (1)')).toBeTruthy();
    expect(screen.getByText('plugin controls')).toBeTruthy();
    expect(screen.getByText('Workspace YAML')).toBeTruthy();
    expect(screen.queryByText('Apply')).toBeNull();
    expect(screen.queryByText('Delete')).toBeNull();
    expect(screen.getByLabelText('workspace yaml').readOnly).toBe(true);
  });

  test('shows a non-member only the badges the server sends', async () => {
    // A non-member of a read-only-visible workspace gets its flags, not its
    // configuration.
    mockGetWorkspaces.mockImplementation(async () => ({
      ws: { read_only: true, writable: false },
    }));
    render(<WorkspaceEditor workspaceName="ws" />);
    await screen.findByText('Only admins can change this configuration.');
    expect(screen.queryByText('Apply')).toBeNull();
    expect(screen.queryByText('Delete')).toBeNull();
    const yamlText = screen.getByLabelText('workspace yaml').value;
    expect(yamlText).not.toContain('read_only');
    expect(yamlText).not.toContain('writable');
  });

  test('Refresh reloads the data without re-checking infra', async () => {
    mockApiFetch.mockClear();
    render(<WorkspaceEditor workspaceName="ws" />);
    await screen.findByText('Only admins can change this configuration.');
    const calls = mockGetWorkspaces.mock.calls.length;
    await act(async () => {
      fireEvent.click(screen.getByText('Refresh'));
    });
    expect(mockApiFetch).not.toHaveBeenCalled();
    expect(mockGetWorkspaces.mock.calls.length).toBeGreaterThan(calls);
  });

  test('Refresh drops the cached stats and refetches them', async () => {
    render(<WorkspaceEditor workspaceName="ws" />);
    await screen.findByText('Only admins can change this configuration.');
    mockInvalidate.mockClear();
    const fetched = mockGetClusters.mock.calls.length;
    await act(async () => {
      fireEvent.click(screen.getByText('Refresh'));
    });
    // Clusters, managed jobs and enabled infra; the refetch goes through the
    // cache so the next visit sees the new numbers too.
    expect(mockInvalidate).toHaveBeenCalledTimes(3);
    expect(mockGetClusters.mock.calls.length).toBe(fetched + 1);
  });

  test('a stalled role lookup does not hold up Refresh', async () => {
    mockGetUserRole.mockImplementation(() => new Promise(() => {}));
    render(<WorkspaceEditor workspaceName="ws" />);
    await screen.findByText('Allowed Users (1)');
    const calls = mockGetWorkspaces.mock.calls.length;
    await act(async () => {
      fireEvent.click(screen.getByText('Refresh'));
    });
    expect(mockGetWorkspaces.mock.calls.length).toBeGreaterThan(calls);
    expect(screen.queryByText('Loading...')).toBeNull();
  });

  test('treats a failed role lookup as non-admin', async () => {
    mockGetUserRole.mockImplementation(async () => {
      throw new Error('boom');
    });
    jest.spyOn(console, 'error').mockImplementation(() => {});
    render(<WorkspaceEditor workspaceName="ws" />);
    await screen.findByText('Only admins can change this configuration.');
    expect(screen.queryByText('Apply')).toBeNull();
    console.error.mockRestore();
  });
});

describe('WorkspaceEditor Refresh for an admin', () => {
  beforeEach(() => {
    mockGetWorkspaces.mockReset();
    mockGetWorkspaces.mockImplementation(async () => ({
      ws: { private: true, allowed_users: ['alice'] },
    }));
    mockApiFetch.mockReset();
  });

  test('re-checks infra, then reloads', async () => {
    mockApiFetch.mockImplementation(async () => ({ ok: true }));
    render(<WorkspaceEditor workspaceName="ws" />);
    await screen.findByText('Apply');
    const calls = mockGetWorkspaces.mock.calls.length;
    await act(async () => {
      fireEvent.click(screen.getByText('Refresh'));
    });
    expect(mockApiFetch).toHaveBeenCalledWith('/check', {}, 'POST');
    expect(mockGetWorkspaces.mock.calls.length).toBeGreaterThan(calls);
  });

  test('still reloads when the infra check fails', async () => {
    mockApiFetch.mockImplementation(async () => {
      throw new Error('check failed');
    });
    jest.spyOn(console, 'error').mockImplementation(() => {});
    render(<WorkspaceEditor workspaceName="ws" />);
    await screen.findByText('Apply');
    const calls = mockGetWorkspaces.mock.calls.length;
    await act(async () => {
      fireEvent.click(screen.getByText('Refresh'));
    });
    expect(mockGetWorkspaces.mock.calls.length).toBeGreaterThan(calls);
    console.error.mockRestore();
  });

  test('asks for the role when it is still loading', async () => {
    mockApiFetch.mockImplementation(async () => ({ ok: true }));
    // The page's own role lookup never answers; Refresh asks again.
    mockGetUserRole
      .mockImplementationOnce(() => new Promise(() => {}))
      .mockImplementationOnce(async () => ({ role: 'admin', name: 'root' }));
    render(<WorkspaceEditor workspaceName="ws" />);
    await screen.findByText('Allowed Users (1)');
    expect(screen.queryByText('Apply')).toBeNull();
    await act(async () => {
      fireEvent.click(screen.getByText('Refresh'));
    });
    expect(mockApiFetch).toHaveBeenCalledWith('/check', {}, 'POST');
  });

  test('a slower, older stats request does not overwrite newer stats', async () => {
    mockApiFetch.mockImplementation(async () => ({ ok: true }));
    render(<WorkspaceEditor workspaceName="ws" />);
    await screen.findByText('Apply');
    // Refresh's own stats request answers last with an old view; the one made
    // after the infra check is newer and must win.
    let releaseOld;
    mockGetClusters
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            releaseOld = () =>
              resolve([{ cluster: 'old', workspace: 'ws', status: 'RUNNING' }]);
          })
      )
      .mockImplementationOnce(async () => []);
    await act(async () => {
      fireEvent.click(screen.getByText('Refresh'));
    });
    await act(async () => {
      releaseOld();
    });
    expect(screen.getByText('0 / 0')).toBeTruthy();
    expect(screen.queryByText('1 / 1')).toBeNull();
  });

  test('a failed newer stats request keeps an older success', async () => {
    jest.spyOn(console, 'error').mockImplementation(() => {});
    mockApiFetch.mockImplementation(async () => ({ ok: true }));
    render(<WorkspaceEditor workspaceName="ws" />);
    await screen.findByText('Apply');
    // Refresh's request succeeds after the newer post-check request fails.
    let releaseFirst;
    mockGetClusters
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            releaseFirst = () =>
              resolve([{ cluster: 'c', workspace: 'ws', status: 'RUNNING' }]);
          })
      )
      .mockImplementationOnce(async () => {
        throw new Error('down');
      });
    await act(async () => {
      fireEvent.click(screen.getByText('Refresh'));
    });
    await act(async () => {
      releaseFirst();
    });
    expect(screen.getByText('1 / 1')).toBeTruthy();
    console.error.mockRestore();
  });

  test('stats for a workspace the page left are dropped', async () => {
    jest.spyOn(console, 'error').mockImplementation(() => {});
    mockGetWorkspaces.mockImplementation(async () => ({
      ws: { private: true, allowed_users: ['alice'] },
      other: { private: true, allowed_users: ['alice'] },
    }));
    let releaseWs;
    mockGetClusters
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            releaseWs = () =>
              resolve([{ cluster: 'c', workspace: 'ws', status: 'RUNNING' }]);
          })
      )
      .mockImplementationOnce(async () => {
        throw new Error('down');
      });
    const { rerender } = render(<WorkspaceEditor workspaceName="ws" />);
    await screen.findByText('Apply');
    // Switch workspaces; the new workspace's stats request fails, then the
    // old one answers.
    await act(async () => {
      rerender(<WorkspaceEditor workspaceName="other" />);
    });
    await act(async () => {
      releaseWs();
    });
    expect(screen.queryByText('1 / 1')).toBeNull();
    console.error.mockRestore();
  });
});
