import { apiClient, getRequestResult } from '@/data/connectors/client';

function jsonResponse(data, { status = 200, headers = {} } = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => headers[name] ?? null },
    json: async () => data,
  };
}

describe('apiClient.fetch', () => {
  beforeEach(() => {
    global.fetch.mockReset();
    delete window.__skyApiFetch;
  });

  afterEach(() => {
    delete window.__skyApiFetch;
  });

  test('reads the result from /api/get by the request ID', async () => {
    global.fetch.mockImplementation(async (url) => {
      if (url.endsWith('/internal/dashboard/users/role')) {
        return jsonResponse({ id: 'user-1', name: 'alice' });
      }
      if (url.includes('/api/get?request_id=req-1')) {
        return jsonResponse({ return_value: JSON.stringify({ count: 3 }) });
      }
      return jsonResponse(null, {
        headers: { 'X-Skypilot-Request-ID': 'req-1' },
      });
    });

    await expect(apiClient.fetch('/status', {})).resolves.toEqual({
      count: 3,
    });
    const urls = global.fetch.mock.calls.map(([url]) => url);
    expect(urls.some((url) => url.endsWith('/status'))).toBe(true);
    expect(urls.some((url) => url.includes('/api/get?request_id=req-1'))).toBe(
      true
    );
  });

  test('hands the call to a plugin that sets window.__skyApiFetch', async () => {
    window.__skyApiFetch = jest.fn(async () => 'from-plugin');

    await expect(apiClient.fetch('/status', { a: 1 }, 'POST')).resolves.toBe(
      'from-plugin'
    );
    expect(window.__skyApiFetch).toHaveBeenCalledWith(
      '/status',
      { a: 1 },
      'POST',
      {
        fetchImmediate: apiClient.fetchImmediate,
        getRequestResult,
      }
    );
    expect(global.fetch).not.toHaveBeenCalled();
  });
});
