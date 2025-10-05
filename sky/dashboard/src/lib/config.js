// Configuration for dashboard cache and UI settings

// Cache TTL durations (in milliseconds)
export const CACHE_CONFIG = {
  DEFAULT_TTL: 2 * 60 * 1000, // 2 minutes
};

// Refresh intervals for different data types (in milliseconds)
export const REFRESH_INTERVALS = {
  REFRESH_INTERVAL: 30 * 1000, // 30 seconds - standard refresh interval for all pages
  GPU_REFRESH_INTERVAL: 30 * 1000, // 30 seconds - aligned with standard refresh interval
};

// UI configuration
export const UI_CONFIG = {
  NAME_TRUNCATE_LENGTH: 20, // Maximum length for truncated names
};
