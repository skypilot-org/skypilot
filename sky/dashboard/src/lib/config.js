// Configuration for dashboard cache and UI settings

// Refresh intervals for different data types (in milliseconds)
export const REFRESH_INTERVALS = {
  REFRESH_INTERVAL: 30 * 1000, // 30 seconds - standard refresh interval for all pages
  GPU_REFRESH_INTERVAL: 30 * 1000, // 30 seconds - aligned with standard refresh interval
};

// Cache TTL durations (in milliseconds). A hard TTL matches the dashboard's
// periodic refresh cadence without turning every cache hit into a request.
export const CACHE_CONFIG = {
  DEFAULT_TTL: REFRESH_INTERVALS.REFRESH_INTERVAL,
};

// UI configuration
export const UI_CONFIG = {
  NAME_TRUNCATE_LENGTH: 20, // Maximum length for truncated names
};
