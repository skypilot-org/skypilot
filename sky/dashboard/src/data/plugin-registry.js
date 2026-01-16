/**
 * Plugin Data Provider Registry
 *
 * This module provides a global registry for plugin data providers.
 * It's imported early in the app lifecycle so plugins can register
 * their providers before components mount.
 */

// Registry for data providers
const dataProviders = {};
const registrationListeners = [];

/**
 * Register a plugin data provider
 * @param {string} id - Provider ID (e.g., 'clusters')
 * @param {Object} provider - Provider configuration with fetch function
 */
export function registerDataProvider(id, provider) {
  console.log(`[PluginRegistry] Registering data provider: ${id}`);
  dataProviders[id] = provider;

  // Notify listeners
  registrationListeners.forEach((listener) => {
    try {
      listener(id, provider);
    } catch (e) {
      console.error('[PluginRegistry] Error in registration listener:', e);
    }
  });
}

/**
 * Get a registered data provider
 * @param {string} id - Provider ID
 * @returns {Object|null} Provider or null if not registered
 */
export function getDataProvider(id) {
  return dataProviders[id] || null;
}

/**
 * Check if a data provider is registered
 * @param {string} id - Provider ID
 * @returns {boolean}
 */
export function hasDataProvider(id) {
  return id in dataProviders;
}

/**
 * Subscribe to provider registration events
 * @param {Function} listener - Callback(id, provider) when provider registers
 * @returns {Function} Unsubscribe function
 */
export function onProviderRegistration(listener) {
  registrationListeners.push(listener);
  return () => {
    const idx = registrationListeners.indexOf(listener);
    if (idx >= 0) {
      registrationListeners.splice(idx, 1);
    }
  };
}

// Expose on window for plugins to access
if (typeof window !== 'undefined') {
  window.__skyPluginRegistry = {
    registerDataProvider,
    getDataProvider,
    hasDataProvider,
    onProviderRegistration,
  };

  // Also expose the specific setter for clusters (backward compatibility)
  window.__skySetClusterDataProvider = (provider) => {
    registerDataProvider('clusters', provider);
  };

  console.log('[PluginRegistry] Initialized and exposed on window');
}

export default {
  registerDataProvider,
  getDataProvider,
  hasDataProvider,
  onProviderRegistration,
};
