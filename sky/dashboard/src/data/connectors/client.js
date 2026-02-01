'use client';

import { getErrorMessageFromResponse } from '@/data/utils';
import { ENDPOINT } from './constants';

// Cache for current user info
let cachedUserInfo = null;
let userInfoPromise = null;

// Fetch and cache the current user info
async function getCurrentUserInfo() {
  if (cachedUserInfo) {
    return cachedUserInfo;
  }

  if (userInfoPromise) {
    return userInfoPromise;
  }

  userInfoPromise = (async () => {
    try {
      const baseUrl = window.location.origin;
      const response = await fetch(`${baseUrl}/internal/dashboard/users/role`);
      if (response.ok) {
        const data = await response.json();
        cachedUserInfo = {
          id: data.id || 'local',
          name: data.name || data.id || 'local',
        };
      } else {
        // Not authenticated or error - use 'local' as fallback
        cachedUserInfo = { id: 'local', name: 'local' };
      }
    } catch (error) {
      console.error('Failed to get user info:', error);
      cachedUserInfo = { id: 'local', name: 'local' };
    }
    return cachedUserInfo;
  })();

  return userInfoPromise;
}

// Wait for plugins that need early initialization (e.g., fetch interceptors)
// Such plugins are marked with data-requires-early-init="true" and set
// window.__skyPluginsReady = true when ready
let pluginsReadyPromise = null;

async function waitForPlugins() {
  if (window.__skyPluginsReady) return;
  if (pluginsReadyPromise) return pluginsReadyPromise;

  // Check if any plugin needs early init
  const needsWait = document.querySelector(
    'script[src*="/plugins/"][data-requires-early-init="true"]'
  );
  if (!needsWait) return;

  // Wait for plugin to signal ready (max 1s)
  pluginsReadyPromise = new Promise((resolve) => {
    const start = Date.now();
    const check = setInterval(() => {
      if (window.__skyPluginsReady || Date.now() - start >= 1000) {
        clearInterval(check);
        resolve();
      }
    }, 50);
  });
  return pluginsReadyPromise;
}

export const apiClient = {
  fetchImmediate: async (path, body, method = 'POST', options = {}) => {
    // Wait for plugins to be ready before making API calls
    await waitForPlugins();

    // Call a skypilot API and get the result
    const headers =
      method === 'POST'
        ? {
            'Content-Type': 'application/json',
            ...(options.headers || {}),
          }
        : { ...(options.headers || {}) };

    const baseUrl = window.location.origin;
    const fullUrl = `${baseUrl}${ENDPOINT}${path}`;

    if (body !== undefined) {
      // Get user info (cached after first call)
      const userInfo = await getCurrentUserInfo();
      body.env_vars = {
        ...(body.env_vars || {}),
        SKYPILOT_IS_FROM_DASHBOARD: 'true',
        SKYPILOT_USER_ID: userInfo.id,
        SKYPILOT_USER: userInfo.name,
      };
    }

    return await fetch(fullUrl, {
      method,
      headers,
      body: method === 'POST' ? JSON.stringify(body) : undefined,
      signal: options.signal,
    });
  },
  fetch: async (path, body, method = 'POST') => {
    // Call the server API and get the result via /api/get, the API must return a request ID
    try {
      const baseUrl = window.location.origin;
      const response = await apiClient.fetchImmediate(path, body, method);

      // Check if initial request succeeded
      if (!response.ok) {
        const msg = `Initial API request ${path} failed with status ${response.status}`;
        throw new Error(msg);
      }

      // Handle X-Skypilot-Request-ID for API requests
      const id = response.headers.get('X-Skypilot-Request-ID');

      // Handle empty request ID
      if (!id) {
        const msg = `No request ID received from server for ${path}`;
        throw new Error(msg);
      }

      const fetchedData = await fetch(
        `${baseUrl}${ENDPOINT}/api/get?request_id=${id}`
      );

      // Handle all error status codes (4xx, 5xx, etc.)
      if (!fetchedData.ok) {
        const errorMessage = await getErrorMessageFromResponse(fetchedData);
        const msg = `API request to get ${path} result failed with status ${fetchedData.status}, error: ${errorMessage}`;
        throw new Error(msg);
      }

      const data = await fetchedData.json();
      return data.return_value ? JSON.parse(data.return_value) : [];
    } catch (error) {
      const msg = `Error in apiClient.fetch for ${path}: ${error}`;
      console.error(msg);
      throw new Error(msg);
    }
  },

  // Helper method for POST requests
  post: async (path, body, options = {}) => {
    const headers = {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    };

    const baseUrl = window.location.origin;
    const fullUrl = `${baseUrl}${ENDPOINT}${path}`;

    return await fetch(fullUrl, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
      signal: options.signal,
    });
  },

  // Helper method for streaming responses
  stream: async (path, body, onData, options = {}) => {
    const response = await apiClient.post(path, body, options);
    if (!response.ok) {
      const msg = `API request ${path} failed with status ${response.status}`;
      console.error(msg);
      throw new Error(msg);
    }
    const reader = response.body.getReader();

    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = new TextDecoder().decode(value);
        onData(chunk);
      }
    } catch (error) {
      console.error('Error in stream:', error);
      throw error;
    }
  },

  get: async (path) => {
    const baseUrl = window.location.origin;
    const fullUrl = `${baseUrl}${ENDPOINT}${path}`;
    return await fetch(fullUrl);
  },
};
