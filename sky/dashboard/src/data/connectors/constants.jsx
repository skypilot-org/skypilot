// Runtime detection of the current path to construct the correct API endpoint
function getApiEndpoint() {
  if (typeof window !== 'undefined') {
    // Browser environment - detect from current path
    const currentPath = window.location.pathname;

    // Extract the base path (everything before /dashboard)
    const dashboardIndex = currentPath.indexOf('/dashboard');
    if (dashboardIndex !== -1) {
      const basePath = currentPath.substring(0, dashboardIndex);
      return `${basePath}/internal/dashboard`;
    }

    // Fallback to root if no /dashboard found
    return '/internal/dashboard';
  }

  // Server-side rendering or build time - use environment variable or default
  return process.env.SKYPILOT_API_SERVER_ENDPOINT || '/internal/dashboard';
}

function getBasePath() {
  if (typeof window !== 'undefined') {
    // Browser environment - detect from current path
    const currentPath = window.location.pathname;

    // Extract the base path (everything before and including /dashboard)
    const dashboardIndex = currentPath.indexOf('/dashboard');
    if (dashboardIndex !== -1) {
      return currentPath.substring(0, dashboardIndex + '/dashboard'.length);
    }

    // Fallback to /dashboard if no /dashboard found
    return '/dashboard';
  }

  // Server-side rendering or build time - use environment variable or default
  return process.env.NEXT_BASE_PATH || '/dashboard';
}

export const ENDPOINT = getApiEndpoint();
export const BASE_PATH = getBasePath();
export const TIMEOUT = 10000;
export const API_URL = '/api/v1';
export const WS_API_URL = API_URL.replace(/^http/, 'ws');

// The SkyPilot API version this dashboard advertises, sent on every outgoing
// request via the `X-SkyPilot-API-Version` header so the server can identify
// the dashboard as a contemporary (non-legacy) client and run the workspace
// resolver / surface new error types like `WorkspaceAmbiguousError`.
//
// This MUST equal sky/server/constants.py:API_VERSION. The dashboard ships
// with the server, so the two bump together (see the backward-compatibility
// guideline). It is a build-time hardcode rather than the server's runtime
// version on purpose: an already-loaded older dashboard kept across a server
// upgrade then still reports its own build's version, not the new server's, so
// it can't over-report support for wire formats its code doesn't handle.
// Enforced by tests/unit_tests/test_api_version_consistency.py.
export const CLIENT_API_VERSION = '55';
// Header names expected by the server's APIVersionMiddleware. Mirrors
// sky/server/constants.py:API_VERSION_HEADER / VERSION_HEADER.
// The middleware (versions._check_version_compatibility) requires BOTH
// headers to be present — if either is missing it returns None and
// does NOT populate the `_remote_api_version` ContextVar, which then
// leaves prepare_request_async stamping `client_api_version=None` on
// the body and the worker-side resolver gate treats the request as an
// old client.
export const API_VERSION_HEADER = 'X-SkyPilot-API-Version';
export const VERSION_HEADER = 'X-SkyPilot-Version';
// Readable-version companion of CLIENT_API_VERSION. Python SDK sends
// `{sky.__version__};{sky.__commit__}` here; the dashboard does not
// know its own build version at JS-runtime, so we send a self-
// identifying placeholder that the server parses but doesn't depend on
// for correctness (only used to format upgrade-hint messages).
export const CLIENT_VERSION = 'dashboard;';
// Custom events used to coordinate plugin loading with the layout shell.
// layout.jsx listens for these to avoid flashing the fallback top bar before
// a navigation plugin (e.g. sidebar) has had a chance to register.
export const EVENT_NAVIGATION_READY = 'skydashboard:navigation-ready';
export const EVENT_PLUGINS_LOADED = 'skydashboard:plugins-loaded';

export const CLUSTER_DOES_NOT_EXIST = 'ClusterDoesNotExist';
export const NOT_SUPPORTED_ERROR = 'NotSupportedError';
export const CLUSTER_NOT_UP_ERROR = 'ClusterNotUpError';
export const CLOUDS_LIST = [
  'AWS',
  'Azure',
  'GCP',
  'IBM',
  'Lambda',
  'SCP',
  'OCI',
  'RunPod',
  'VAST',
  'vSphere',
  'Cudo',
  'FluidStack',
  'Paperspace',
  'DO',
  'Nebius',
  'Verda',
];

export const CLOUD_CANONICALIZATIONS = Object.fromEntries([
  ...CLOUDS_LIST.map((cloud) => [cloud.toLowerCase(), cloud]),
  ['kubernetes', 'Kubernetes'],
  ['ssh', 'SSH Node Pool'],
]);

export const COMMON_GPUS = [
  'A10',
  'A10G',
  'A100',
  'A100-80GB',
  'H100',
  'H200',
  'L4',
  'L40S',
  'T4',
  'V100',
  'V100-32GB',
];
