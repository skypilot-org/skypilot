import { useState, useEffect } from 'react';
import dashboardCache from '@/lib/cache';
import { getWorkspaces } from '@/data/connectors/workspaces';

// Server-side default workspace name (sky.skylet.constants.SKYPILOT_DEFAULT_
// WORKSPACE). A cluster/job with no explicit workspace lands here.
export const DEFAULT_WORKSPACE = 'default';

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
 *   boolean, loaded: boolean}}
 */
export function useWorkspacesConfig() {
  const [workspacesConfig, setWorkspacesConfig] = useState(
    () => dashboardCache.getCached(getWorkspaces) || {}
  );
  const [loaded, setLoaded] = useState(
    () => dashboardCache.getCached(getWorkspaces) != null
  );

  useEffect(() => {
    let cancelled = false;
    dashboardCache
      .get(getWorkspaces)
      .then((cfg) => {
        if (!cancelled) {
          setWorkspacesConfig(cfg || {});
          setLoaded(true);
        }
      })
      .catch(() => {
        // On failure, fall back to the "unknown -> writable" convention rather
        // than leaving every action disabled forever (this is only a visual
        // gate; the server still enforces workspace writes).
        if (!cancelled) {
          setLoaded(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const isWorkspaceWritable = (ws) => {
    if (!loaded) {
      return false;
    }
    return workspacesConfig[ws || DEFAULT_WORKSPACE]?.writable !== false;
  };

  return { workspacesConfig, isWorkspaceWritable, loaded };
}
