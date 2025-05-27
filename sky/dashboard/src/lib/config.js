// Dashboard configuration settings

// Cache configuration
export const CACHE_CONFIG = {
  // Default TTL for all cache entries (2 minutes)
  DEFAULT_TTL: 2 * 60 * 1000,
  
  // Specific TTLs for different data types
  // Examples:
  // CLUSTERS_AND_JOBS_TTL: 5 * 60 * 1000, // 5 minutes
};

// Refresh intervals
export const REFRESH_INTERVALS = {
  GPU_REFRESH_INTERVAL: 60 * 1000, // 1 minute
};

// UI configuration
export const UI_CONFIG = {
  NAME_TRUNCATE_LENGTH: 30,
};

export default {
  CACHE_CONFIG,
  REFRESH_INTERVALS,
  UI_CONFIG,
}; 