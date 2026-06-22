'use client';

import { apiClient } from '@/data/connectors/client';

// Module-level cache for the dashboard config response. The endpoint returns
// admin-configured settings that do not change while the page is open, so a
// single fetch per session is sufficient.
let dashboardConfigCache = null;
let dashboardConfigPromise = null;

const EMPTY_CONFIG = {
  externalLinks: [],
  disable_config_editor: false,
};

/**
 * Fetch the admin-configured dashboard settings from the server.
 *
 * Returns dashboard settings normalized for the UI. On network or parse
 * failure, returns an empty config rather than throwing so the dashboard stays
 * usable when the endpoint is unavailable.
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
            typeof entry.regex === 'string' &&
            entry.label.length > 0 &&
            entry.regex.length > 0
        )
        .map((entry) => ({ label: entry.label, regex: entry.regex }));
      dashboardConfigCache = {
        externalLinks,
        disable_config_editor: Boolean(data?.disable_config_editor),
      };
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
