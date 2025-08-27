'use client';

import { ENDPOINT } from './constants';

export const apiClient = {
  fetch: async (path, body, method = 'POST') => {
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

    // Handle X-Request-ID for API requests
    const id =
      response.headers.get('X-Skypilot-Request-ID') ||
      response.headers.get('X-Request-ID');

    const fetchedData = await fetch(
      `${baseUrl}${ENDPOINT}/api/get?request_id=${id}`
    );
    const data = await fetchedData.json();
    return data.return_value ? JSON.parse(data.return_value) : [];
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
