// Plugin slots on the workspace editor's allowed-users list: without plugins
// the built-in role badges render; a plugin registered for a slot replaces the
// badge and receives the row's context.
import React from 'react';
import { render, screen } from '@testing-library/react';

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

import { DetailedAllowedUsers } from '@/components/workspace-editor';

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
