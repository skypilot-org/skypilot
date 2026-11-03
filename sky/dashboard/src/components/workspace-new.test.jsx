// A new workspace's name may not be another workspace's name or id: a typed
// reference must always resolve to exactly one workspace.
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';

jest.mock('next/router', () => ({
  __esModule: true,
  useRouter: () => ({ isReady: true, query: {}, push: jest.fn() }),
}));
jest.mock('next/dynamic', () => ({
  __esModule: true,
  default: () => () => null,
}));
jest.mock('@/data/connectors/workspaces', () => ({
  __esModule: true,
  getWorkspaces: jest.fn(async () => ({
    default: {},
    // A legacy workspace renamed from its id 'team-a'.
    'team-a': { name: 'archive' },
    'ws-0123456789ab': { name: 'ml' },
  })),
}));

import { NewWorkspace } from '@/components/workspace-new';

const typeName = async (name) => {
  render(<NewWorkspace />);
  const button = await screen.findByText('Next: Configure Workspace');
  fireEvent.change(screen.getByLabelText('Workspace name'), {
    target: { value: name },
  });
  return button;
};

test('another workspace id is taken, and names its workspace', async () => {
  const button = await typeName('team-a');
  expect(button).toBeDisabled();
  expect(
    screen.getByText('"team-a" is the id of workspace "archive".', {
      exact: false,
    })
  ).toBeInTheDocument();
});

test('another workspace name is taken', async () => {
  const button = await typeName('ml');
  expect(button).toBeDisabled();
  expect(
    screen.getByText('Workspace "ml" already exists.', { exact: false })
  ).toBeInTheDocument();
});

test('a free name can be created', async () => {
  const button = await typeName('new-team');
  expect(button).toBeEnabled();
});
