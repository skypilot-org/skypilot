'use client';

import { apiClient } from '@/data/connectors/client';

// Valid values for an external-link entry's `scope`: the dashboard pages a
// link may appear on. Must stay in sync with
// DASHBOARD_EXTERNAL_LINK_SCOPES in sky/utils/schemas.py.
export const LINK_SCOPE_CLUSTER = 'cluster';
export const LINK_SCOPE_JOBS = 'jobs';
export const EXTERNAL_LINK_SCOPES = [LINK_SCOPE_CLUSTER, LINK_SCOPE_JOBS];

// Module-level cache for the dashboard config response, fetched once per
// session. `localContexts` depends on the caller's workspace grants and on
// the server's context detection, so the infra page's Refresh clears it.
let dashboardConfigCache = null;
let dashboardConfigPromise = null;
// Bumped on invalidation so a fetch sent earlier cannot cache its older
// response.
let dashboardConfigGeneration = 0;
// A failed refetch keeps the last good response instead of EMPTY_CONFIG.
let lastGoodDashboardConfig = null;

export const invalidateDashboardConfig = () => {
  dashboardConfigGeneration += 1;
  dashboardConfigCache = null;
  dashboardConfigPromise = null;
};

// `localContexts` falls back to ['in-cluster'] (the context name that is
// local by construction) so the infra page keeps matching the local
// cluster's unstamped GPU series even when the config endpoint is
// unreachable.
const EMPTY_CONFIG = { externalLinks: [], localContexts: ['in-cluster'] };

/**
 * Fetch the admin-configured dashboard settings from the server.
 *
 * Returns an object of the shape
 * { externalLinks: [{ label, regex, scope? } | { label, url, scope? }],
 *   localContexts: [string] }, where `regex` entries are matched against
 * logs, `url` entries are templates resolved against cluster/job metadata,
 * and `localContexts` lists the Kubernetes contexts the server detected as
 * pointing at its own cluster. `scope`, when present, is a non-empty array
 * of EXTERNAL_LINK_SCOPES values restricting which pages render the link;
 * entries without a scope appear on all pages. On network or parse
 * failure, returns a default config rather than throwing so the dashboard
 * stays usable when the endpoint is unavailable.
 */
export const getDashboardConfig = async () => {
  if (dashboardConfigCache !== null) {
    return dashboardConfigCache;
  }
  if (dashboardConfigPromise) {
    return dashboardConfigPromise;
  }

  const generation = dashboardConfigGeneration;
  const promise = (async () => {
    let config = lastGoodDashboardConfig || EMPTY_CONFIG;
    try {
      const response = await apiClient.get('/dashboard_config');
      if (!response.ok) {
        return config;
      }
      const data = await response.json();
      const rawCustomUrls = Array.isArray(data?.external_links)
        ? data.external_links
        : [];
      const externalLinks = rawCustomUrls
        .filter(
          (entry) =>
            entry &&
            typeof entry.label === 'string' &&
            entry.label.length > 0 &&
            ((typeof entry.regex === 'string' && entry.regex.length > 0) ||
              (typeof entry.url === 'string' && entry.url.length > 0))
        )
        .map((entry) => {
          const normalized =
            typeof entry.regex === 'string' && entry.regex.length > 0
              ? { label: entry.label, regex: entry.regex }
              : { label: entry.label, url: entry.url };
          // The server only sends known scope values, but the client must
          // not trust the payload: keep only recognized values and drop
          // the field entirely when none remain (= visible on all pages).
          if (Array.isArray(entry.scope)) {
            const scope = entry.scope.filter((s) =>
              EXTERNAL_LINK_SCOPES.includes(s)
            );
            if (scope.length > 0) {
              normalized.scope = scope;
            }
          }
          return normalized;
        });
      const localContexts = Array.isArray(data?.local_contexts)
        ? data.local_contexts.filter((entry) => typeof entry === 'string')
        : EMPTY_CONFIG.localContexts;
      config = { externalLinks, localContexts };
      if (generation === dashboardConfigGeneration) {
        lastGoodDashboardConfig = config;
      }
      return config;
    } catch (error) {
      console.debug('Dashboard config fetch failed:', error);
      return config;
    } finally {
      if (generation === dashboardConfigGeneration) {
        dashboardConfigCache = config;
        dashboardConfigPromise = null;
      }
    }
  })();
  dashboardConfigPromise = promise;
  return promise;
};

export const resetDashboardConfigCache = () => {
  dashboardConfigGeneration += 1;
  dashboardConfigCache = null;
  dashboardConfigPromise = null;
  lastGoodDashboardConfig = null;
};
