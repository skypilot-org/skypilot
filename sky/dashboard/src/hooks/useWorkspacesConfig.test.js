jest.mock('@/lib/cache', () => {
  const cache = { invalidate: jest.fn(), invalidateFunction: jest.fn() };
  return { __esModule: true, default: cache };
});

import dashboardCache from '@/lib/cache';
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
