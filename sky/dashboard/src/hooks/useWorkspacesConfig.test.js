jest.mock('@/lib/cache', () => {
  const cache = {
    invalidate: jest.fn(),
    invalidateFunction: jest.fn(),
    get: jest.fn(),
    getCached: jest.fn(),
  };
  return { __esModule: true, default: cache };
});

import { act, renderHook } from '@testing-library/react';
import dashboardCache from '@/lib/cache';
import { REFRESH_INTERVALS } from '@/lib/config';
import {
  getEnabledCloudsBatch,
  getWorkspaces,
} from '@/data/connectors/workspaces';
import {
  getWorkspaceContexts,
  getWorkspaceInfrastructure,
} from '@/data/connectors/infra';
import {
  getWorkspaceName,
  invalidateWorkspaceCaches,
  useWorkspacesConfig,
  workspaceNameError,
} from '@/hooks/useWorkspacesConfig';

describe('getWorkspaceName', () => {
  const config = {
    default: { name: 'main' },
    'team-a': { name: 'proj-a' },
    'team-b': {},
  };

  it('returns the stored name, or the id when unset', () => {
    expect(getWorkspaceName(config, 'team-a')).toBe('proj-a');
    expect(getWorkspaceName(config, 'team-b')).toBe('team-b');
    expect(getWorkspaceName(config, 'unknown')).toBe('unknown');
  });

  it('always names the default workspace "default"', () => {
    expect(getWorkspaceName(config, 'default')).toBe('default');
    expect(getWorkspaceName(config, undefined)).toBe('default');
  });
});

describe('workspaceNameError', () => {
  it('accepts names the server accepts', () => {
    expect(workspaceNameError('proj-a')).toBeNull();
    expect(workspaceNameError('a_1')).toBeNull();
  });

  it('rejects names the server rejects', () => {
    for (const name of ['', 'Proj', 'a b', '1abc', 'abc-', 'a'.repeat(64)]) {
      expect(workspaceNameError(name)).not.toBeNull();
    }
  });
});

describe('invalidateWorkspaceCaches', () => {
  it('drops every cached view built from the workspace list', () => {
    invalidateWorkspaceCaches();
    expect(dashboardCache.invalidate).toHaveBeenCalledWith(getWorkspaces);
    expect(dashboardCache.invalidateFunction).toHaveBeenCalledWith(
      getEnabledCloudsBatch
    );
    expect(dashboardCache.invalidate).toHaveBeenCalledWith(
      getWorkspaceContexts
    );
    expect(dashboardCache.invalidate).toHaveBeenCalledWith(
      getWorkspaceInfrastructure
    );
  });
});

describe('useWorkspacesConfig', () => {
  afterEach(() => {
    jest.useRealTimers();
  });

  it('follows a refreshed workspace list while mounted', async () => {
    jest.useFakeTimers();
    const before = { 'ws-0123456789ab': { name: 'ml' } };
    const after = { 'ws-0123456789ab': { name: 'ml-v2' } };
    dashboardCache.getCached.mockReturnValue(before);
    dashboardCache.get.mockResolvedValue(before);

    const { result } = renderHook(() => useWorkspacesConfig());
    await act(async () => {});
    expect(result.current.workspaceName('ws-0123456789ab')).toBe('ml');

    // The page's polling refreshed the cached list in the background.
    dashboardCache.getCached.mockReturnValue(after);
    await act(async () => {
      jest.advanceTimersByTime(REFRESH_INTERVALS.REFRESH_INTERVAL);
    });
    expect(result.current.workspaceName('ws-0123456789ab')).toBe('ml-v2');
  });
});
