/**
 * Regression test for the managed-job log download identity.
 *
 * `downloadManagedJobLogs` dispatches two requests that the API server
 * validates against each other: `/jobs/download_logs` decides where the logs
 * are written (from the `SKYPILOT_USER_ID` it is given), and `/download` then
 * refuses any folder outside that same user's log directory. The two must
 * therefore derive the user identity the same way — see
 * https://github.com/skypilot-org/skypilot/issues/9976, where a blank id made
 * the first write to `clients//sky_logs` while the second validated against
 * `clients/local/sky_logs` and returned a 400.
 */

import { getCurrentUserInfo, apiClient } from './client';

jest.mock('./client', () => ({
  getCurrentUserInfo: jest.fn(),
  apiClient: { fetchImmediate: jest.fn() },
}));
jest.mock('./toast', () => ({ showToast: jest.fn() }));
jest.mock('@/lib/analytics', () => ({ trackJobAction: jest.fn() }));
// Breaks the jobs.jsx -> dataEnhancement -> cache-preloader -> jobs.jsx cycle.
jest.mock('@/plugins/dataEnhancement', () => ({
  applyEnhancements: (_name, data) => data,
}));

const { downloadManagedJobLogs } = require('./jobs');

// The identity endpoint answers with blank fields when auth is disabled;
// `getCurrentUserInfo` is what normalizes that to 'local'.
const BLANK_ROLE_RESPONSE = { id: '', name: '' };
const LOG_DIR = '~/.sky/api_server/clients/local/sky_logs/managed_jobs/job-1';

describe('downloadManagedJobLogs user identity', () => {
  let dispatchBody;

  beforeEach(() => {
    jest.clearAllMocks();
    dispatchBody = null;

    getCurrentUserInfo.mockResolvedValue({ id: 'local', name: 'local' });

    global.fetch.mockImplementation(async (url, options) => {
      if (String(url).includes('/internal/dashboard/users/role')) {
        return { ok: true, json: async () => BLANK_ROLE_RESPONSE };
      }
      if (String(url).includes('/jobs/download_logs')) {
        dispatchBody = JSON.parse(options.body);
        return {
          ok: true,
          headers: { get: () => 'request-id-1' },
        };
      }
      if (String(url).includes('/api/get')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ return_value: JSON.stringify({ 1: LOG_DIR }) }),
        };
      }
      throw new Error(`unexpected fetch: ${url}`);
    });

    apiClient.fetchImmediate.mockResolvedValue({
      ok: true,
      blob: async () => ({}),
    });

    window.URL.createObjectURL = jest.fn(() => 'blob:zip');
    window.URL.revokeObjectURL = jest.fn();
  });

  it('sends the same user id that apiClient sends on /download', async () => {
    await downloadManagedJobLogs({ jobId: 1 });

    const expected = (await getCurrentUserInfo()).id;
    expect(dispatchBody).not.toBeNull();
    expect(dispatchBody.env_vars.SKYPILOT_USER_ID).toBe(expected);
    expect(dispatchBody.env_vars.SKYPILOT_USER).toBe('local');
  });

  it('still reaches /download with the folders returned by the dispatch', async () => {
    await downloadManagedJobLogs({ jobId: 1 });

    expect(apiClient.fetchImmediate).toHaveBeenCalledWith(
      '/download?relative=items',
      { folder_paths: [LOG_DIR] }
    );
  });
});
