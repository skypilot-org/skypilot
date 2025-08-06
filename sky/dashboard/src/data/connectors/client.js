'use client';

import AuthManager from '@/lib/auth';
import { ENDPOINT } from './constants';

export const getContentTypeAuthHeaders = () => {
  const headers = {
    'Content-Type': 'application/json',
  };
  return addAuthHeader(headers);
};

export const getAuthHeaders = () => {
  const headers = {};
  return addAuthHeader(headers);
};

export const addAuthHeader = (headers) => {
  const token = AuthManager.getToken();
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return headers;
};

export const apiClient = {
  fetch: async (path, body, method = 'POST') => {
    let headers =
      method === 'POST'
        ? {
            'Content-Type': 'application/json',
          }
        : {};

    headers = addAuthHeader(headers);

    const baseUrl = window.location.origin;
    const fullUrl = `${baseUrl}${ENDPOINT}${path}`;

    const response = await fetch(fullUrl, {
      method,
      headers,
      body: method === 'POST' ? JSON.stringify(body) : undefined,
    });

    // Handle 401 unauthorized - token expired, redirect to login
    if (response.status === 401) {
      AuthManager.logout();
      return;
    }

    // Handle X-Request-ID for API requests
    const id =
      response.headers.get('X-Skypilot-Request-ID') ||
      response.headers.get('X-Request-ID');

    const fetchedData = await fetch(
      `${baseUrl}${ENDPOINT}/api/get?request_id=${id}`,
      { headers }
    );

    // Check 401 status for the second request
    if (fetchedData.status === 401) {
      AuthManager.logout();
      return;
    }

    const data = await fetchedData.json();
    return data.return_value ? JSON.parse(data.return_value) : [];
  },

  // Helper method for POST requests
  post: async (path, body) => {
    const headers = getContentTypeAuthHeaders();

    const baseUrl = window.location.origin;
    const fullUrl = `${baseUrl}${ENDPOINT}${path}`;

    const response = await fetch(fullUrl, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
    });

    // Handle 401 unauthorized - token expired, redirect to login
    if (response.status === 401) {
      AuthManager.logout();
      return response;
    }

    return response;
  },

  // Helper method for streaming responses
  stream: async (path, body, onData) => {
    const response = await apiClient.post(path, body);
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
    const headers = getAuthHeaders();
    const response = await fetch(fullUrl, { headers });
    if (response.status === 401) {
      AuthManager.logout();
      return response;
    }
    return response;
  },
};
