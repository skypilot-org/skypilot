jest.mock('@/data/connectors/client', () => ({
  __esModule: true,
  apiClient: { fetch: jest.fn(), post: jest.fn() },
}));

import { apiClient } from '@/data/connectors/client';
import { deleteVolume, getVolumes } from '@/data/connectors/volumes';

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

describe('deleteVolume', () => {
  it('says why the server refused, and that it was a refusal', async () => {
    apiClient.post.mockResolvedValue({
      ok: false,
      status: 403,
      json: () =>
        Promise.resolve({
          detail: 'Only an admin can delete k8s-hostpath volumes: hp',
        }),
    });

    expect(await deleteVolume('hp')).toEqual({
      success: false,
      forbidden: true,
      msg: 'Failed to delete volume: Only an admin can delete k8s-hostpath volumes: hp',
    });
  });

  it('falls back to the status without a readable body', async () => {
    apiClient.post.mockResolvedValue({
      ok: false,
      status: 502,
      json: () => Promise.reject(new Error('not json')),
    });

    expect(await deleteVolume('v')).toEqual({
      success: false,
      forbidden: false,
      msg: 'Failed to delete volume with status 502',
    });
  });
});
