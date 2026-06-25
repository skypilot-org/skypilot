import {
  getDashboardConfig,
  resetDashboardConfigCache,
} from '@/data/connectors/dashboard_config';
import { apiClient } from '@/data/connectors/client';

jest.mock('@/data/connectors/client', () => ({
  apiClient: {
    get: jest.fn(),
  },
}));

describe('getDashboardConfig', () => {
  beforeEach(() => {
    resetDashboardConfigCache();
    jest.clearAllMocks();
  });

  it('normalizes external links and read-only config flag', async () => {
    apiClient.get.mockResolvedValue({
      ok: true,
      json: async () => ({
        disable_config_editor: true,
        external_links: [
          { label: 'Run', regex: 'https://example.com/runs/.*' },
          { label: '', regex: 'https://bad.example.com/.*' },
        ],
      }),
    });

    await expect(getDashboardConfig()).resolves.toEqual({
      disable_config_editor: true,
      externalLinks: [{ label: 'Run', regex: 'https://example.com/runs/.*' }],
    });
  });

  it('falls back when dashboard config fetch fails', async () => {
    apiClient.get.mockResolvedValue({
      ok: false,
    });

    await expect(getDashboardConfig()).resolves.toEqual({
      disable_config_editor: false,
      externalLinks: [],
    });
  });
});
