export const ENDPOINT = '/internal/dashboard';
export const BASE_PATH = '/dashboard';
export const TIMEOUT = 10000;
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
export const REFRESH_INTERVAL = 5000; // 5 seconds
export const LOG_REFRESH_INTERVAL = 2000;
export const API_HEALTH_ENDPOINT = `${ENDPOINT}/api/health`;
export const SKYPILOT_VERSION = '0.5.0';
