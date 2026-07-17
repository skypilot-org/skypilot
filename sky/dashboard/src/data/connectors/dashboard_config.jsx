'use client';

import { apiClient } from '@/data/connectors/client';

// Module-level cache for the dashboard config response. The endpoint returns
// admin-configured settings that do not change while the page is open, so a
// single fetch per session is sufficient.
let dashboardConfigCache = null;
let dashboardConfigPromise = null;

// `localContexts` falls back to ['in-cluster'] (the context name that is
// local by construction) so the infra page keeps matching the local
// cluster's unstamped GPU series even when the config endpoint is
// unreachable.
const EMPTY_CONFIG = { externalLinks: [], localContexts: ['in-cluster'] };

/**
 * Fetch the admin-configured dashboard settings from the server.
 *
 * Returns an object of the shape
 * { externalLinks: [{ label, regex } | { label, url }],
 *   localContexts: [string] }, where `regex` entries are matched against
 * logs, `url` entries are templates resolved against cluster/job metadata,
 * and `localContexts` lists the Kubernetes contexts the server detected as
 * pointing at its own cluster. On network or parse failure, returns a
 * default config rather than throwing so the dashboard stays usable when
 * the endpoint is unavailable.
 */
export const getDashboardConfig = async () => {
  if (dashboardConfigCache !== null) {
    return dashboardConfigCache;
  }
  if (dashboardConfigPromise) {
    return dashboardConfigPromise;
  }

  dashboardConfigPromise = (async () => {
    try {
      const response = await apiClient.get('/dashboard_config');
      if (!response.ok) {
        dashboardConfigCache = EMPTY_CONFIG;
        return dashboardConfigCache;
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
        .map((entry) =>
          typeof entry.regex === 'string' && entry.regex.length > 0
            ? { label: entry.label, regex: entry.regex }
            : { label: entry.label, url: entry.url }
        );
      const localContexts = Array.isArray(data?.local_contexts)
        ? data.local_contexts.filter((entry) => typeof entry === 'string')
        : EMPTY_CONFIG.localContexts;
      dashboardConfigCache = { externalLinks, localContexts };
      return dashboardConfigCache;
    } catch (error) {
      console.debug('Dashboard config fetch failed:', error);
      dashboardConfigCache = EMPTY_CONFIG;
      return dashboardConfigCache;
    } finally {
      dashboardConfigPromise = null;
    }
  })();

  return dashboardConfigPromise;
};

export const resetDashboardConfigCache = () => {
  dashboardConfigCache = null;
  dashboardConfigPromise = null;
};
