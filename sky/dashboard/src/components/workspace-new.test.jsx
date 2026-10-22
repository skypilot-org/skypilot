// The creation flow: a name that no workspace has (by name or id, so a typed
// reference always resolves to exactly one workspace), then the YAML keyed by
// that name, created only when the second step is applied.
import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';

const mockPush = jest.fn();
jest.mock('next/router', () => ({
  __esModule: true,
  useRouter: () => ({ isReady: true, query: {}, push: mockPush }),
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
const mockCreateWorkspace = jest.fn(async () => ({}));
jest.mock('@/data/connectors/workspaces', () => ({
  __esModule: true,
  getWorkspaces: jest.fn(async () => ({
    default: {},
    // A legacy workspace renamed from its id 'team-a'.
    'team-a': { name: 'archive' },
    'ws-0123456789ab': { name: 'ml' },
  })),
  createWorkspace: (...args) => mockCreateWorkspace(...args),
}));
// Plugin flow steps, set per test; see `registerFlowStep` for the contract.
const mockFlowSteps = {};
jest.mock('@/plugins/PluginProvider', () => ({
  __esModule: true,
  usePluginFlowSteps: (flow) => mockFlowSteps[flow] || [],
}));
const mockInvalidate = jest.fn();
jest.mock('@/lib/cache', () => {
  const cache = {
    get: jest.fn((fn, args = []) => fn(...args)),
    invalidate: (...args) => mockInvalidate(...args),
    invalidateFunction: jest.fn(),
  };
  return { __esModule: true, default: cache, dashboardCache: cache };
});

import { NewWorkspace } from '@/components/workspace-new';
import { getWorkspaceContexts } from '@/data/connectors/infra';

beforeEach(() => {
  mockPush.mockClear();
  mockCreateWorkspace.mockClear();
  mockInvalidate.mockClear();
  for (const key of Object.keys(mockFlowSteps)) delete mockFlowSteps[key];
});

const typeName = async (name) => {
  render(<NewWorkspace />);
  // The Name step's Next names the following step, so wait for any Next.
  const button = await screen.findByText(/^Next: /);
  fireEvent.change(screen.getByLabelText('Workspace name'), {
    target: { value: name },
  });
  return button;
};

const currentStep = () =>
  document.querySelector('[aria-current="step"]').textContent;

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

test('a free name can be configured', async () => {
  const button = await typeName('new-team');
  expect(button).toBeEnabled();
  expect(currentStep()).toBe('1Name');
  fireEvent.click(button);
  expect(currentStep()).toBe('2Configure');
  expect(screen.getByLabelText('workspace yaml').value).toMatch(/^new-team:\n/);
});

test('creating sends the YAML config and drops the cached workspace views', async () => {
  mockCreateWorkspace.mockResolvedValueOnce({
    'ws-new': { name: 'new-team', private: true },
  });
  fireEvent.click(await typeName('new-team'));
  fireEvent.change(screen.getByLabelText('workspace yaml'), {
    target: { value: 'new-team:\n  private: true\n' },
  });
  await act(async () => {
    fireEvent.click(screen.getByText('Create workspace'));
  });
  expect(mockCreateWorkspace).toHaveBeenCalledWith('new-team', {
    private: true,
  });
  expect(mockInvalidate).toHaveBeenCalledWith(getWorkspaceContexts);
  expect(screen.getByText('Workspace "new-team" created.')).toBeInTheDocument();
});

test('the YAML key follows a name changed on the Name step', async () => {
  fireEvent.click(await typeName('first'));
  // An edit is kept; only the key line is rewritten.
  fireEvent.change(screen.getByLabelText('workspace yaml'), {
    target: { value: 'first:\n  private: true  # keep me\n' },
  });
  fireEvent.click(screen.getByText('Back'));
  fireEvent.change(screen.getByLabelText('Workspace name'), {
    target: { value: 'second' },
  });
  fireEvent.click(screen.getByText('Next: Configure'));
  expect(screen.getByLabelText('workspace yaml')).toHaveValue(
    'second:\n  private: true  # keep me\n'
  );
  expect(screen.queryByText(/top-level key must stay/)).toBeNull();
});

test('a YAML keyed by another name cannot be created', async () => {
  fireEvent.click(await typeName('new-team'));
  fireEvent.change(screen.getByLabelText('workspace yaml'), {
    target: { value: 'other:\n  private: true\n' },
  });
  expect(
    screen.getByText(
      'The top-level key must stay "new-team" (found "other"). To use a different name, go back to the Name step.'
    )
  ).toBeInTheDocument();
  expect(screen.getByText('Create workspace')).toBeDisabled();
});

// A plugin step that lets the user pick a queue name, contributes it to the
// workspace config and "creates" it on commit.
function QueueStep({ workspaceName, value, onChange }) {
  return (
    <div>
      <input
        aria-label="queue"
        value={value?.queue || ''}
        onChange={(e) =>
          onChange({
            value: { queue: e.target.value },
            ready: e.target.value !== '',
            configPatch: {
              kubernetes: { quota: { queue: e.target.value } },
            },
          })
        }
      />
      <span>{`step for ${workspaceName}`}</span>
    </div>
  );
}

const quotaStep = (overrides = {}) => ({
  id: 'quota',
  flow: 'workspaces.create',
  label: 'Quota',
  order: 20,
  component: QueueStep,
  isApplicable: async () => true,
  commit: jest.fn(async () => undefined),
  ...overrides,
});

const reachQuotaStep = async (name = 'new-team') => {
  fireEvent.click(await typeName(name));
  expect(currentStep()).toBe('2Quota');
  return screen.getByLabelText('queue');
};

describe('with a plugin step', () => {
  test('an applicable step sits between Name and Configure and gates Next', async () => {
    mockFlowSteps['workspaces.create'] = [quotaStep()];
    const next = await typeName('new-team');
    expect(next).toHaveTextContent('Next: Quota');
    fireEvent.click(next);
    expect(screen.getByText('step for new-team')).toBeInTheDocument();
    const toConfigure = screen.getByText('Next: Configure');
    expect(toConfigure).toBeDisabled();
    fireEvent.change(screen.getByLabelText('queue'), {
      target: { value: 'ml' },
    });
    expect(toConfigure).toBeEnabled();
    fireEvent.click(toConfigure);
    expect(currentStep()).toBe('3Configure');
    expect(screen.getByLabelText('workspace yaml')).toHaveValue(
      'new-team:\n  kubernetes:\n    quota:\n      queue: ml\n'
    );
  });

  test('a step that does not apply is left out', async () => {
    mockFlowSteps['workspaces.create'] = [
      quotaStep({ isApplicable: async () => false }),
    ];
    const next = await typeName('new-team');
    expect(next).toHaveTextContent('Next: Configure');
    fireEvent.click(next);
    expect(currentStep()).toBe('2Configure');
  });

  test('commit runs with the final config before the workspace is created', async () => {
    const step = quotaStep();
    mockFlowSteps['workspaces.create'] = [step];
    const calls = [];
    step.commit.mockImplementation(async (args) => {
      calls.push(['commit', args]);
    });
    mockCreateWorkspace.mockImplementation(async (...args) => {
      calls.push(['create', args]);
      return {};
    });
    fireEvent.change(await reachQuotaStep(), { target: { value: 'ml' } });
    fireEvent.click(screen.getByText('Next: Configure'));
    fireEvent.change(screen.getByLabelText('workspace yaml'), {
      target: {
        value:
          'new-team:\n  private: true\n  kubernetes:\n    quota:\n      queue: ml\n',
      },
    });
    await act(async () => {
      fireEvent.click(screen.getByText('Create workspace'));
    });
    const config = {
      private: true,
      kubernetes: { quota: { queue: 'ml' } },
    };
    expect(calls).toEqual([
      ['commit', { workspaceName: 'new-team', config, value: { queue: 'ml' } }],
      ['create', ['new-team', config]],
    ]);
  });

  test('commit errors stop the create and are shown', async () => {
    const step = quotaStep({
      commit: jest.fn(async () => ({ errors: ['ctx-a: queue exists'] })),
    });
    mockFlowSteps['workspaces.create'] = [step];
    fireEvent.change(await reachQuotaStep(), { target: { value: 'ml' } });
    fireEvent.click(screen.getByText('Next: Configure'));
    await act(async () => {
      fireEvent.click(screen.getByText('Create workspace'));
    });
    expect(mockCreateWorkspace).not.toHaveBeenCalled();
    expect(screen.getByText('ctx-a: queue exists')).toBeInTheDocument();
    expect(screen.getByText('Back')).toBeEnabled();
  });

  test('changing the step after editing the YAML reseeds it', async () => {
    mockFlowSteps['workspaces.create'] = [quotaStep()];
    fireEvent.change(await reachQuotaStep(), { target: { value: 'ml' } });
    fireEvent.click(screen.getByText('Next: Configure'));
    fireEvent.change(screen.getByLabelText('workspace yaml'), {
      target: { value: 'new-team:\n  private: true\n' },
    });
    // Back without a change keeps the edit.
    fireEvent.click(screen.getByText('Back'));
    fireEvent.click(screen.getByText('Next: Configure'));
    expect(screen.getByLabelText('workspace yaml')).toHaveValue(
      'new-team:\n  private: true\n'
    );
    // A changed contribution replaces it.
    fireEvent.click(screen.getByText('Back'));
    fireEvent.change(screen.getByLabelText('queue'), {
      target: { value: 'research' },
    });
    fireEvent.click(screen.getByText('Next: Configure'));
    expect(screen.getByLabelText('workspace yaml')).toHaveValue(
      'new-team:\n  kubernetes:\n    quota:\n      queue: research\n'
    );
  });
});
