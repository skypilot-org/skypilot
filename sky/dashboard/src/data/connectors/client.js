'use client';

import { getErrorMessageFromResponse } from '@/data/utils';
import { ENDPOINT } from './constants';

export const apiClient = {
  fetch: async (path, body, method = 'POST') => {
    try {
      const headers =
        method === 'POST'
          ? {
              'Content-Type': 'application/json',
            }
          : {};

      const baseUrl = window.location.origin;
      const fullUrl = `${baseUrl}${ENDPOINT}${path}`;

      const response = await fetch(fullUrl, {
        method,
        headers,
        body: method === 'POST' ? JSON.stringify(body) : undefined,
      });

      // Check if initial request succeeded
      if (!response.ok) {
        const msg = `Initial API request ${path} failed with status ${response.status}`;
        throw new Error(msg);
      }

      // Handle X-Request-ID for API requests
      const id =
        response.headers.get('X-Skypilot-Request-ID') ||
        response.headers.get('X-Request-ID');

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
  post: async (path, body) => {
    const headers = {
      'Content-Type': 'application/json',
    };

    const baseUrl = window.location.origin;
    const fullUrl = `${baseUrl}${ENDPOINT}${path}`;

    return await fetch(fullUrl, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
    });
  },

  // Helper method for streaming responses
  stream: async (path, body, onData) => {
    const response = await apiClient.post(path, body);
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
