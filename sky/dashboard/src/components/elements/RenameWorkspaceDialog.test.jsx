// The rename dialog shared by the workspaces list and the workspace page:
// its hint, validation, and the conditional write.
import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';

const mockUpdateWorkspace = jest.fn();
jest.mock('@/data/connectors/workspaces', () => ({
  __esModule: true,
  getWorkspaces: jest.fn(),
  updateWorkspace: (...args) => mockUpdateWorkspace(...args),
}));
const mockInvalidate = jest.fn();
const mockInvalidateFunction = jest.fn();
jest.mock('@/lib/cache', () => {
  const cache = {
    invalidate: (...args) => mockInvalidate(...args),
    invalidateFunction: (...args) => mockInvalidateFunction(...args),
    setPreloader: jest.fn(),
  };
  return { __esModule: true, default: cache, dashboardCache: cache };
});

import { RenameWorkspaceDialog } from '@/components/elements/RenameWorkspaceDialog';
import { getClusters } from '@/data/connectors/clusters';
import { getWorkspaceContexts } from '@/data/connectors/infra';
import { getManagedJobs } from '@/data/connectors/jobs';

const workspacesConfig = {
  default: {},
  // A legacy workspace: its name is its id.
  team: { private: false },
  // A legacy workspace renamed from its id.
  'team-a': { name: 'archive' },
  'ws-0123456789ab': { name: 'ml', private: false },
  'ws-ba9876543210': { name: 'cv' },
};

let onRenamed;
beforeEach(() => {
  mockUpdateWorkspace.mockReset();
  mockUpdateWorkspace.mockResolvedValue({});
  mockInvalidate.mockReset();
  onRenamed = jest.fn();
});

function renderDialog(workspaceId, config = workspacesConfig[workspaceId]) {
  render(
    <RenameWorkspaceDialog
      workspaceId={workspaceId}
      config={config}
      workspacesConfig={workspacesConfig}
      onClose={jest.fn()}
      onRenamed={onRenamed}
    />
  );
  return screen.getByLabelText('Name');
}

async function typeAndClickRename(input, name) {
  fireEvent.change(input, { target: { value: name } });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Rename' }));
  });
}

test('a legacy workspace keeps its old name working', () => {
  renderDialog('team');
  const hint = screen.getByText(
    'Scripts and configs that use "team" keep working.'
  );
  // The hint is the dialog's only description.
  expect(screen.getByRole('dialog')).toHaveAttribute(
    'aria-describedby',
    hint.id
  );
});

test('a generated-id workspace frees its old name', () => {
  renderDialog('ws-0123456789ab');
  expect(
    screen.getByText(
      'Scripts and configs that use "ml" will stop working, and another workspace can take this name.'
    )
  ).toBeInTheDocument();
});

test('Enter renames, sending the stored config as expected_config', async () => {
  // The server-computed flags are not part of the stored config.
  const input = renderDialog('ws-0123456789ab', {
    name: 'ml',
    private: false,
    writable: true,
    read_only: false,
  });
  fireEvent.change(input, { target: { value: 'ml-v2' } });
  await act(async () => {
    fireEvent.keyDown(input, { key: 'Enter' });
  });
  const renamed = { name: 'ml-v2', private: false };
  expect(mockUpdateWorkspace).toHaveBeenCalledWith('ws-0123456789ab', renamed, {
    name: 'ml',
    private: false,
  });
  // The infra page keeps its own context-to-workspace map; it must refresh.
  expect(mockInvalidate).toHaveBeenCalledWith(getWorkspaceContexts);
  // Cluster and job rows show the name.
  expect(mockInvalidate).toHaveBeenCalledWith(getClusters);
  expect(mockInvalidateFunction).toHaveBeenCalledWith(getManagedJobs);
  expect(onRenamed).toHaveBeenCalledWith('ml-v2', renamed);
});

test('an invalid name shows the error and does not write', async () => {
  const input = renderDialog('ws-0123456789ab');
  await typeAndClickRename(input, 'Bad Name');
  expect(screen.getByText(/Use lowercase letters/)).toBeInTheDocument();
  expect(mockUpdateWorkspace).not.toHaveBeenCalled();
});

test('another workspace name is taken', async () => {
  const input = renderDialog('ws-0123456789ab');
  await typeAndClickRename(input, 'cv');
  expect(
    screen.getByText('Workspace "cv" already exists.')
  ).toBeInTheDocument();
  expect(mockUpdateWorkspace).not.toHaveBeenCalled();
});

test('another workspace id is taken', async () => {
  const input = renderDialog('ws-0123456789ab');
  await typeAndClickRename(input, 'team-a');
  expect(
    screen.getByText('"team-a" is the id of workspace "archive".')
  ).toBeInTheDocument();
  expect(mockUpdateWorkspace).not.toHaveBeenCalled();
});

test('a workspace can take back its own id as its name', async () => {
  const input = renderDialog('team', { name: 'archive' });
  await typeAndClickRename(input, 'team');
  expect(mockUpdateWorkspace).toHaveBeenCalledWith(
    'team',
    { name: 'team' },
    { name: 'archive' }
  );
});

test('a concurrent change is reported, not overwritten', async () => {
  const err = new Error('changed');
  err.type = 'WorkspaceConfigConflictError';
  mockUpdateWorkspace.mockRejectedValue(err);
  const input = renderDialog('ws-0123456789ab');
  await typeAndClickRename(input, 'ml-v2');
  expect(
    screen.getByText(
      'The workspace changed since this page loaded. Refresh and try again.'
    )
  ).toBeInTheDocument();
  expect(onRenamed).not.toHaveBeenCalled();
});
