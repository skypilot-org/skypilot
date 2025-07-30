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
