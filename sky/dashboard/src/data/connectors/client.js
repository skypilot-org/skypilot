'use client';

import { getErrorMessageFromResponse } from '@/data/utils';
import {
  API_VERSION_HEADER,
  CLIENT_API_VERSION,
  CLIENT_VERSION,
  ENDPOINT,
  VERSION_HEADER,
} from './constants';
import { waitForPlugins } from './pluginsReady';
import { isSyncAnswer, sendRequest } from './syncCalls';

// The server's APIVersionMiddleware uses these headers to (a) negotiate
// protocol compatibility and (b) populate the `_remote_api_version`
// ContextVar that `prepare_request_async` reads to tag worker-side
// request bodies with the client's API version. Both headers MUST be
// sent together — `_check_version_compatibility` returns None and skips
// the ContextVar write if either is missing, which would silently
// degrade the dashboard back to old-client behavior on every queued
// request.
function withVersionHeader(headers) {
  return {
    [API_VERSION_HEADER]: CLIENT_API_VERSION,
    [VERSION_HEADER]: CLIENT_VERSION,
    ...(headers || {}),
  };
}

// Cache for current user info
let cachedUserInfo = null;
let userInfoPromise = null;

// Fetch and cache the current user info
export async function getCurrentUserInfo() {
  if (cachedUserInfo) {
    return cachedUserInfo;
  }

  if (userInfoPromise) {
    return userInfoPromise;
  }

  userInfoPromise = (async () => {
    try {
      const baseUrl = window.location.origin;
      const response = await fetch(`${baseUrl}/internal/dashboard/users/role`, {
        headers: withVersionHeader({}),
      });
      if (response.ok) {
        const data = await response.json();
        cachedUserInfo = {
          id: data.id || 'local',
          name: data.name || data.id || 'local',
          role: data.role,
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

// Returns the result carried by an /api/get response.
async function readRequestResult(path, fetchedData) {
  if (!fetchedData.ok) {
    const errorMessage = await getErrorMessageFromResponse(fetchedData);
    const msg = `API request to get ${path} result failed with status ${fetchedData.status}, error: ${errorMessage}`;
    throw new Error(msg);
  }
  const data = await fetchedData.json();
  return data.return_value ? JSON.parse(data.return_value) : [];
}

// Returns the result of the request that `response` scheduled, read from
// /api/get by the request ID in the response headers.
async function getRequestResult(path, response) {
  if (!response.ok) {
    const msg = `Initial API request ${path} failed with status ${response.status}`;
    throw new Error(msg);
  }
  const id = response.headers.get('X-Skypilot-Request-ID');
  if (!id) {
    const msg = `No request ID received from server for ${path}`;
    throw new Error(msg);
  }
  const baseUrl = window.location.origin;
  const fetchedData = await fetch(
    `${baseUrl}${ENDPOINT}/api/get?request_id=${id}`,
    { headers: withVersionHeader({}) }
  );
  return readRequestResult(path, fetchedData);
}

export const apiClient = {
  fetchImmediate: async (path, body, method = 'POST', options = {}) => {
    // Wait for plugins to be ready before making API calls
    await waitForPlugins();

    // Call a skypilot API and get the result
    const headers =
      method === 'POST'
        ? withVersionHeader({
            'Content-Type': 'application/json',
            ...(options.headers || {}),
          })
        : withVersionHeader(options.headers || {});

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
  // Calls the server API and returns the result of the request. The request
  // is sent as a sync call (see syncCalls.js) unless options.sync is false; a
  // body with a `limit` is answered as one only within the server's maximum.
  fetch: async (path, body, method = 'POST', options = {}) => {
    try {
      await waitForPlugins();
      const limit =
        body && typeof body === 'object' && 'limit' in body
          ? body.limit
          : undefined;
      const response = await sendRequest(
        (headers) => apiClient.fetchImmediate(path, body, method, { headers }),
        { sync: options.sync ?? true, limit }
      );
      return isSyncAnswer(response)
        ? await readRequestResult(path, response)
        : await getRequestResult(path, response);
    } catch (error) {
      const msg = `Error in apiClient.fetch for ${path}: ${error}`;
      console.error(msg);
      throw new Error(msg);
    }
  },

  // Helper method for POST requests
  post: async (path, body, options = {}) => {
    const headers = withVersionHeader({
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    });

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

  get: async (path, options = {}) => {
    const baseUrl = window.location.origin;
    const fullUrl = `${baseUrl}${ENDPOINT}${path}`;
    return await fetch(fullUrl, {
      headers: withVersionHeader(options.headers || {}),
      signal: options.signal,
    });
  },
};
