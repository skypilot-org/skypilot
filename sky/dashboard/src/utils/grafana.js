/**
 * Grafana utility functions for the SkyPilot dashboard
 */

// Cache for Grafana availability check
let grafanaAvailabilityCache = null;
let grafanaCheckPromise = null;

/**
 * Check if Grafana is available by trying to access the /grafana endpoint
 * @returns {Promise<boolean>} True if Grafana is available, false otherwise
 */
export const checkGrafanaAvailability = async () => {
  // Return cached result if available
  if (grafanaAvailabilityCache !== null) {
    return grafanaAvailabilityCache;
  }

  // Return ongoing promise if already checking
  if (grafanaCheckPromise) {
    return grafanaCheckPromise;
  }

  grafanaCheckPromise = (async () => {
    try {
      const grafanaUrl = `${window.location.origin}/grafana`;
      const response = await fetch(`${grafanaUrl}/api/health`, {
        method: 'GET',
        credentials: 'include',
        headers: {
          Accept: 'application/json',
        },
        // Use a short timeout to avoid blocking the UI
        signal: AbortSignal.timeout(5000),
      });

      // Consider Grafana available if we get any 200 response from the Grafana API
      const grafanaAvailabilityCache = response.status == 200;
      return grafanaAvailabilityCache;
    } catch (error) {
      console.debug('Grafana availability check failed:', error);
      grafanaAvailabilityCache = false;
      return false;
    } finally {
      grafanaCheckPromise = null;
    }
  })();

  return grafanaCheckPromise;
};

/**
 * Get the base Grafana URL
 * @returns {string} The Grafana base URL
 */
export const getGrafanaUrl = () => {
  return `${window.location.origin}/grafana`;
};

/**
 * Build a Grafana dashboard URL
 * @param {string} path - The path to append to the Grafana URL
 * @returns {string} The full Grafana URL
 */
export const buildGrafanaUrl = (path = '/') => {
  const grafanaUrl = getGrafanaUrl();
  return `${grafanaUrl}${path}`;
};

/**
 * Open Grafana in a new tab
 * @param {string} path - The path to open in Grafana
 */
export const openGrafana = (path = '/') => {
  const url = buildGrafanaUrl(path);
  window.open(url, '_blank');
};

/**
 * Reset the Grafana availability cache (useful for testing or when config changes)
 */
export const resetGrafanaAvailabilityCache = () => {
  grafanaAvailabilityCache = null;
  grafanaCheckPromise = null;
};
