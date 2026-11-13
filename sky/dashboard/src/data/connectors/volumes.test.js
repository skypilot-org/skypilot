jest.mock('@/data/connectors/client', () => ({
  __esModule: true,
  apiClient: { fetch: jest.fn() },
}));

import { apiClient } from '@/data/connectors/client';
import { getVolumes } from '@/data/connectors/volumes';

describe('getVolumes', () => {
  it('keeps the workspace name the server sends', async () => {
    apiClient.fetch.mockResolvedValue([
      {
        name: 'v1',
        cloud: 'Kubernetes',
        workspace: 'ws-1',
        workspace_name: 'research',
      },
      { name: 'v2', cloud: 'Kubernetes', workspace: 'ws-2' },
    ]);

    const volumes = await getVolumes();

    expect(volumes.map((v) => [v.workspace, v.workspace_name])).toEqual([
      ['ws-1', 'research'],
      // An older server: the table looks the name up by id.
      ['ws-2', null],
    ]);
  });
});
