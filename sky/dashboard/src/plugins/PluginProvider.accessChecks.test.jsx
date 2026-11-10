// Access checks: a plugin registers one through the real provider and pages
// read it back by action; `anyAccessCheckAllows` says yes as soon as one
// check does, and a check that fails or stalls counts as a no.
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
import {
  ACCESS_CHECK_TIMEOUT_MS,
  PluginProvider,
  anyAccessCheckAllows,
  usePluginAccessChecks,
} from '@/plugins/PluginProvider';

function ChecksFor({ action }) {
  const checks = usePluginAccessChecks(action);
  return <span>{`${action}:${checks.map((c) => c.id).join(',')}`}</span>;
}

test('a check registered through the plugin API reaches its action', async () => {
  render(
    <PluginProvider>
      <ChecksFor action="workspaces.detail.open" />
      <ChecksFor action="other.action" />
    </PluginProvider>
  );
  const check = jest.fn(async () => true);
  await act(async () => {
    window.SkyDashboardPluginAPI.registerAccessCheck({
      id: 'p',
      action: 'workspaces.detail.open',
      check,
    });
  });
  expect(screen.getByText('workspaces.detail.open:p')).toBeTruthy();
  expect(screen.getByText('other.action:')).toBeTruthy();
});

test('an invalid registration is ignored', async () => {
  jest.spyOn(console, 'warn').mockImplementation(() => {});
  render(
    <PluginProvider>
      <ChecksFor action="workspaces.detail.open" />
    </PluginProvider>
  );
  let id;
  await act(async () => {
    id = window.SkyDashboardPluginAPI.registerAccessCheck({
      id: 'p',
      action: 'workspaces.detail.open',
    });
  });
  expect(id).toBeNull();
  expect(screen.getByText('workspaces.detail.open:')).toBeTruthy();
  console.warn.mockRestore();
});

describe('anyAccessCheckAllows', () => {
  afterEach(() => {
    jest.useRealTimers();
  });

  test('is a no without checks', async () => {
    await expect(anyAccessCheckAllows([], {})).resolves.toBe(false);
  });

  test('passes the context and needs exactly true', async () => {
    const check = jest.fn(async () => 'yes');
    await expect(
      anyAccessCheckAllows([{ id: 'p', check }], { workspaceName: 'ws' })
    ).resolves.toBe(false);
    expect(check).toHaveBeenCalledWith({ workspaceName: 'ws' });
  });

  test('a yes wins over a check that never answers', async () => {
    const checks = [
      { id: 'stalled', check: () => new Promise(() => {}) },
      { id: 'yes', check: async () => true },
    ];
    await expect(anyAccessCheckAllows(checks, {})).resolves.toBe(true);
  });

  test('a throwing check is a no', async () => {
    jest.spyOn(console, 'warn').mockImplementation(() => {});
    const checks = [
      {
        id: 'sync',
        check: () => {
          throw new Error('boom');
        },
      },
      { id: 'async', check: async () => Promise.reject(new Error('boom')) },
    ];
    await expect(anyAccessCheckAllows(checks, {})).resolves.toBe(false);
    console.warn.mockRestore();
  });

  test('a check that never answers times out as a no', async () => {
    jest.useFakeTimers();
    jest.spyOn(console, 'warn').mockImplementation(() => {});
    const result = anyAccessCheckAllows(
      [{ id: 'stalled', check: () => new Promise(() => {}) }],
      {}
    );
    await act(async () => {
      jest.advanceTimersByTime(ACCESS_CHECK_TIMEOUT_MS);
    });
    await expect(result).resolves.toBe(false);
    console.warn.mockRestore();
  });
});
