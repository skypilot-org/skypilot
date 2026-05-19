/**
 * Analytics stub for SkyPilot Dashboard.
 */

let _provider = null;

/** Register the analytics implementation. Pass null to unregister. */
export function registerAnalyticsProvider(provider) {
  _provider = provider;
}

/** Returns the current provider, or null if none registered. */
export function getAnalyticsProvider() {
  return _provider;
}

// ── Core provider-backed tracking ───────────────────────────────────────────

export function trackEvent(eventName, properties = {}) {
  _provider?.trackEvent?.(eventName, properties);
}

export function trackPageView(path, properties = {}) {
  _provider?.trackPageView?.(path, properties);
}

// ── Domain helpers (thin wrappers over trackEvent) ──────────────────────────

export function trackClusterAction(action, properties = {}) {
  trackEvent('cluster_action', { action, ...properties });
}

export function trackJobAction(action, properties = {}) {
  trackEvent('job_action', { action, ...properties });
}

export function trackWorkspaceAction(action, properties = {}) {
  trackEvent('workspace_action', { action, ...properties });
}

export function trackRecipeAction(action, properties = {}) {
  trackEvent('recipe_action', { action, ...properties });
}

export function trackInfraAction(action, properties = {}) {
  trackEvent('infra_action', { action, ...properties });
}

export function trackVolumeAction(action, properties = {}) {
  trackEvent('volume_action', { action, ...properties });
}

export function trackUserAction(action, properties = {}) {
  trackEvent('user_action', { action, ...properties });
}

export function trackSettingsAction(action, properties = {}) {
  trackEvent('settings_action', { action, ...properties });
}

export function trackFilterUsed(filterType, properties = {}) {
  trackEvent('filter_used', { filter_type: filterType, ...properties });
}

export function trackPluginPageView(pluginName, pagePath) {
  trackEvent('plugin_page_view', { plugin: pluginName, path: pagePath });
}
