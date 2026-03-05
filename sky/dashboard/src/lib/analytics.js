/**
 * Product analytics utilities for SkyPilot Dashboard.
 *
 * Thin wrapper around posthog-js. The PostHogProvider is responsible for
 * calling optOut() when the server reports that usage collection is disabled.
 */
import posthog from 'posthog-js';

const POSTHOG_API_KEY = 'phc_NP0EO5Koq1dWqXEHwR14Po7bVqqtAdWINXiWypKU6H7';
const POSTHOG_HOST = 'https://us.i.posthog.com';

let _initialized = false;
let _optedOut = false;

/**
 * Initialize PostHog. Safe to call multiple times – only the first call has
 * any effect. Call optOut() after init to disable collection at runtime.
 */
export function initPostHog() {
  if (_initialized) return;
  _initialized = true;

  if (typeof window === 'undefined') return;

  posthog.init(POSTHOG_API_KEY, {
    api_host: POSTHOG_HOST,
    autocapture: true,
    capture_pageview: false, // we fire manual pageviews on route change
    capture_pageleave: true,
    persistence: 'localStorage',
    disable_session_recording: false,
  });
}

/**
 * Opt out of all analytics collection. Called by PostHogProvider when the
 * server reports SKYPILOT_DISABLE_USAGE_COLLECTION=1.
 */
export function optOut() {
  _optedOut = true;
  if (_initialized && typeof window !== 'undefined') {
    posthog.opt_out_capturing();
  }
}

/** Returns true when analytics collection is active (initialized and not opted out). */
export function isEnabled() {
  return _initialized && !_optedOut && typeof window !== 'undefined';
}

// ── Identification ──────────────────────────────────────────────────────────

/**
 * Identify the current user and register "super properties" that are attached
 * to every subsequent event.
 */
export function identifyUser(userHash, username, extraProperties = {}) {
  if (!isEnabled()) return;
  posthog.identify(userHash, {
    username,
    source: 'dashboard',
    ...extraProperties,
  });
}

/**
 * Register deployment-level super properties (version, auth mode, etc.).
 * These are sent with every event automatically.
 */
export function registerDeployment(properties) {
  if (!isEnabled()) return;
  posthog.register({
    source: 'dashboard',
    ...properties,
  });
}

// ── Path Normalization ──────────────────────────────────────────────────────

// Route patterns for normalization (order matters - more specific first)
const ROUTE_PATTERNS = [
  // Jobs: /jobs/pools/[pool] (must be before /jobs/[job]/[task])
  [/^\/jobs\/pools\/[^/]+$/, '/jobs/pools/[pool]'],
  // Jobs: /jobs/[job]/[task] (must be before /jobs/[job])
  [/^\/jobs\/[^/]+\/[^/]+$/, '/jobs/[job]/[task]'],
  // Jobs: /jobs/[job] - must not match /jobs/pools (static route)
  [
    /^\/jobs\/[^/]+$/,
    (path) => (path === '/jobs/pools' ? path : '/jobs/[job]'),
  ],
  // Clusters: /clusters/[cluster]/[job] (must be before /clusters/[cluster])
  [/^\/clusters\/[^/]+\/[^/]+$/, '/clusters/[cluster]/[job]'],
  // Clusters: /clusters/[cluster]
  [/^\/clusters\/[^/]+$/, '/clusters/[cluster]'],
  // Recipes: /recipes/[recipe]
  [/^\/recipes\/[^/]+$/, '/recipes/[recipe]'],
  // Workspaces: /workspaces/[name]
  [/^\/workspaces\/[^/]+$/, '/workspaces/[name]'],
  // Infra: /infra/[context]
  [/^\/infra\/[^/]+$/, '/infra/[context]'],
  // Plugins: catch-all /plugins/[...slug]
  [/^\/plugins\/.*$/, '/plugins/[...slug]'],
];

/**
 * Normalize a path by replacing dynamic segments with parameter names.
 * Static routes pass through unchanged.
 * @param {string} path - The raw path to normalize
 * @returns {string} The normalized path
 */
export function normalizePath(path) {
  for (const [pattern, normalized] of ROUTE_PATTERNS) {
    if (pattern.test(path)) {
      return typeof normalized === 'function' ? normalized(path) : normalized;
    }
  }
  return path;
}

// ── Pageviews ───────────────────────────────────────────────────────────────

export function trackPageView(path, properties = {}) {
  if (!isEnabled()) return;
  const normalized = normalizePath(path);
  posthog.capture('$pageview', {
    $current_url: window.location.href,
    path: normalized,
    raw_path: path,
    ...properties,
  });
}

// ── Generic event helper ────────────────────────────────────────────────────

export function trackEvent(eventName, properties = {}) {
  if (!isEnabled()) return;
  posthog.capture(eventName, { source: 'dashboard', ...properties });
}

// ── Domain-specific helpers ─────────────────────────────────────────────────

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

export function trackFilterUsed(filterType, properties = {}) {
  trackEvent('filter_used', { filter_type: filterType, ...properties });
}

export function trackPluginPageView(pluginName, pagePath) {
  trackEvent('plugin_page_view', { plugin: pluginName, path: pagePath });
}
