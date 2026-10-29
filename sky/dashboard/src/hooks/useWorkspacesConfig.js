import { useState, useEffect } from 'react';
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

// Server-side default workspace name (sky.skylet.constants.SKYPILOT_DEFAULT_
// WORKSPACE). A cluster/job with no explicit workspace lands here.
export const DEFAULT_WORKSPACE = 'default';

// Same rule as the server's workspace names (check_workspace_name_is_valid).
const WORKSPACE_NAME_REGEX = /^[a-z]([-_a-z0-9]*[a-z0-9])?$/;
const WORKSPACE_NAME_MAX_LENGTH = 63;

/**
 * The user-facing name of a workspace. Keys of the `GET /workspaces` map are
 * immutable ids; send ids to APIs and show this to users.
 */
export function getWorkspaceName(workspacesConfig, workspaceId) {
  if (!workspaceId || workspaceId === DEFAULT_WORKSPACE) {
    return workspaceId || DEFAULT_WORKSPACE;
  }
  return workspacesConfig?.[workspaceId]?.name || workspaceId;
}

/** Why `name` is not a valid workspace name, or null if it is. */
export function workspaceNameError(name) {
  if (!name) {
    return 'Name is required.';
  }
  if (name.length > WORKSPACE_NAME_MAX_LENGTH) {
    return `Name must be at most ${WORKSPACE_NAME_MAX_LENGTH} characters.`;
  }
  if (!WORKSPACE_NAME_REGEX.test(name)) {
    return (
      'Use lowercase letters, numbers, dashes and underscores; start with ' +
      'a letter and end with a letter or number.'
    );
  }
  return null;
}

/**
 * The workspace other than `selfId` that already uses `name` as its name or
 * id, as `{ id, byId }`, or null if the name is free. A typed reference must
 * always resolve to exactly one workspace.
 */
export function workspaceNameTaken(workspacesConfig, name, selfId = null) {
  const ids = Object.keys(workspacesConfig || {}).filter((id) => id !== selfId);
  const byName = ids.find(
    (id) => getWorkspaceName(workspacesConfig, id) === name
  );
  if (byName !== undefined) return { id: byName, byId: false };
  return ids.includes(name) ? { id: name, byId: true } : null;
}

/** The message for a name `workspaceNameTaken` reported as taken. */
export function workspaceNameTakenMessage(workspacesConfig, name, taken) {
  return taken.byId
    ? `"${name}" is the id of workspace "${getWorkspaceName(workspacesConfig, taken.id)}".`
    : `Workspace "${name}" already exists.`;
}

/**
 * Shared source of per-workspace writability for the dashboard.
 *
 * The `GET /workspaces` response carries a `writable` flag per workspace
 * (false for a workspace a non-member can only see read-only). Both the
 * clusters table and the cluster detail page need it to decide whether the
 * per-cluster Connect/VSCode actions should be enabled, so the fetch + the
 * "missing entry means writable" convention live here once.
 *
 * The initial state is seeded synchronously from the cache so a warm cache
 * (the common case — the page above already fetched workspaces) renders with
 * the correct writability on the first frame, instead of briefly treating a
 * read-only cluster as writable. Until the config is known (`loaded` false on
 * a cold cache), `isWorkspaceWritable` returns false — the fail-safe direction
 * for a visual gate.
 *
 * @returns {{workspacesConfig: Object, isWorkspaceWritable: (ws: string) =>
 *   boolean, workspaceName: (ws: string) => string, loaded: boolean}}
 */
export function useWorkspacesConfig() {
  // One synchronous cache peek at mount, shared by both bits of initial state.
  const [state, setState] = useState(() => {
    const cached = dashboardCache.getCached(getWorkspaces);
    return { config: cached || {}, loaded: cached != null };
  });
  const { config: workspacesConfig, loaded } = state;

  useEffect(() => {
    let cancelled = false;
    dashboardCache
      .get(getWorkspaces)
      .then((cfg) => {
        if (!cancelled) {
          setState({ config: cfg || {}, loaded: true });
        }
      })
      .catch(() => {
        // On failure, fall back to the "unknown -> writable" convention rather
        // than leaving every action disabled forever (this is only a visual
        // gate; the server still enforces workspace writes).
        if (!cancelled) {
          setState((prev) => ({ ...prev, loaded: true }));
        }
      });
    // The page's own polling refreshes the cached list (background refresh,
    // or a refetch after an invalidation); follow it so renames show up.
    const interval = setInterval(() => {
      const cached = dashboardCache.getCached(getWorkspaces);
      if (cached) {
        setState((prev) =>
          prev.config === cached ? prev : { config: cached, loaded: true }
        );
      }
    }, REFRESH_INTERVALS.REFRESH_INTERVAL);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  const isWorkspaceWritable = (ws) => {
    if (!loaded) {
      return false;
    }
    return workspacesConfig[ws || DEFAULT_WORKSPACE]?.writable !== false;
  };

  const workspaceName = (ws) => getWorkspaceName(workspacesConfig, ws);

  return { workspacesConfig, isWorkspaceWritable, workspaceName, loaded };
}

// After a workspace is created, renamed, edited or deleted: drop every cached
// view built from the workspace list, so no page shows the old set or names
// (the infra page keeps its own context-to-workspace map).
export function invalidateWorkspaceCaches() {
  dashboardCache.invalidate(getWorkspaces);
  dashboardCache.invalidateFunction(getEnabledCloudsBatch);
  dashboardCache.invalidate(getWorkspaceContexts);
  dashboardCache.invalidate(getWorkspaceInfrastructure);
}
