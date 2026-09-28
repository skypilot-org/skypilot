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
  YamlEditor: ({ value, onChange }) => (
    <textarea
      aria-label="workspace yaml"
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
jest.mock('@/data/connectors/users', () => ({
  __esModule: true,
  getUsers: jest.fn(async () => [
    { userId: 'u-alice', username: 'alice', role: 'user' },
    { userId: 'u-bob', username: 'bob', role: 'user' },
  ]),
}));
jest.mock('@/data/connectors/clusters', () => ({
  __esModule: true,
  getClusters: jest.fn(async () => []),
}));
jest.mock('@/data/connectors/jobs', () => ({
  __esModule: true,
  getManagedJobs: jest.fn(async () => ({ jobs: [] })),
}));
jest.mock('@/lib/cache', () => ({
  __esModule: true,
  dashboardCache: {
    get: (fn, args = []) => fn(...args),
    invalidate: jest.fn(),
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
  expect(screen.getByText('actions:alice,bob,root')).toBeInTheDocument();
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

describe('WorkspaceEditor onChanged', () => {
  const before = { ws: { private: true, allowed_users: ['alice'] } };
  const after = { ws: { private: true, allowed_users: ['alice', 'bob'] } };

  // A plugin that changes the workspace (here: adds bob) and asks the page to
  // refresh.
  beforeEach(() => {
    mockComponents['workspaces.detail.allowedUsers.actions'] = [
      {
        id: 'actions',
        component: ({ onChanged }) => (
          <button onClick={() => onChanged()}>plugin change</button>
        ),
      },
    ];
    mockGetWorkspaces.mockReset();
    mockGetWorkspaces.mockResolvedValueOnce(before).mockResolvedValue(after);
  });

  async function renderEditor() {
    render(<WorkspaceEditor workspaceName="ws" />);
    await screen.findByText('Allowed Users (1)', {}, { timeout: 3000 });
  }

  async function pluginRefresh() {
    await act(async () => {
      fireEvent.click(screen.getByText('plugin change'));
    });
    await screen.findByText('Allowed Users (2)');
  }

  test('refreshes the roster and YAML when there are no unsaved edits', async () => {
    await renderEditor();
    await pluginRefresh();
    expect(screen.getByLabelText('workspace yaml').value).toContain('bob');
  });

  test.each([
    ['a comment-only edit', (yaml) => `# who is here and why\n${yaml}`],
    ['invalid YAML', (yaml) => `${yaml}  gcp: [unclosed\n`],
  ])('keeps %s, which leaves the parsed config unchanged', async (_, edit) => {
    await renderEditor();
    const box = screen.getByLabelText('workspace yaml');
    const draft = edit(box.value);
    fireEvent.change(box, { target: { value: draft } });
    await pluginRefresh();
    expect(screen.getByLabelText('workspace yaml').value).toBe(draft);
  });

  test('after Apply, a plugin refresh updates the YAML again', async () => {
    await renderEditor();
    const box = screen.getByLabelText('workspace yaml');
    fireEvent.change(box, {
      target: { value: `${box.value}  gcp:\n    project_id: saved\n` },
    });
    await act(async () => {
      fireEvent.click(screen.getByText('Apply'));
    });
    expect(mockUpdateWorkspace).toHaveBeenCalled();
    await pluginRefresh();
    expect(screen.getByLabelText('workspace yaml').value).toContain('bob');
  });

  test('keeps unsaved YAML edits while refreshing the roster', async () => {
    await renderEditor();
    const draft =
      'ws:\n  private: true\n  allowed_users:\n    - alice\n  gcp:\n    project_id: draft\n';
    fireEvent.change(screen.getByLabelText('workspace yaml'), {
      target: { value: draft },
    });
    await pluginRefresh();
    expect(screen.getByLabelText('workspace yaml').value).toBe(draft);
    expect(screen.getByText('bob')).toBeInTheDocument();
  });
});
