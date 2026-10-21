// Flow steps: a plugin registers one through the real provider and a flow
// reads its steps back in order; a registration without the required fields
// is ignored.
jest.mock('next/router', () => ({
  __esModule: true,
  useRouter: () => ({ isReady: true, query: {}, push: jest.fn() }),
}));
jest.mock('@/data/connectors/client', () => ({
  __esModule: true,
  apiClient: {
    get: jest.fn(async () => ({
      ok: true,
      json: async () => ({ plugins: [] }),
    })),
  },
}));

import React from 'react';
import { act, render, screen } from '@testing-library/react';
import { PluginProvider, usePluginFlowSteps } from '@/plugins/PluginProvider';

function StepsFor({ flow }) {
  const steps = usePluginFlowSteps(flow);
  return (
    <span>{`${flow}:${steps.map((s) => `${s.id}@${s.order}`).join(',')}`}</span>
  );
}

const Step = () => null;

test('steps registered for a flow come back sorted by order', async () => {
  render(
    <PluginProvider>
      <StepsFor flow="workspaces.create" />
      <StepsFor flow="other.flow" />
    </PluginProvider>
  );
  await act(async () => {
    window.SkyDashboardPluginAPI.registerFlowStep({
      id: 'later',
      flow: 'workspaces.create',
      label: 'Later',
      order: 25,
      component: Step,
    });
    window.SkyDashboardPluginAPI.registerFlowStep({
      id: 'quota',
      flow: 'workspaces.create',
      label: 'Quota',
      order: 20,
      component: Step,
    });
  });
  expect(
    screen.getByText('workspaces.create:quota@20,later@25')
  ).toBeInTheDocument();
  expect(screen.getByText('other.flow:')).toBeInTheDocument();
});

test('a registration missing a component is ignored', async () => {
  jest.spyOn(console, 'warn').mockImplementation(() => {});
  render(
    <PluginProvider>
      <StepsFor flow="workspaces.create" />
    </PluginProvider>
  );
  let id;
  await act(async () => {
    id = window.SkyDashboardPluginAPI.registerFlowStep({
      id: 'quota',
      flow: 'workspaces.create',
      label: 'Quota',
    });
  });
  expect(id).toBeNull();
  expect(screen.getByText('workspaces.create:')).toBeInTheDocument();
});
