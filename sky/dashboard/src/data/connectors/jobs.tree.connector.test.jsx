// The queue connector is where `include_tree` meets the wire: it puts the
// flag in the request body next to the job ids, and turns the one error the
// server can answer with -- a jobs controller too old to apply it -- into the
// tagged error the detail-page hook falls back on. Covered here because the
// hook suite stubs this module out and cannot see either half.
jest.mock('@/data/connectors/client', () => ({
  __esModule: true,
  apiClient: { post: jest.fn(), get: jest.fn() },
  getCurrentUserInfo: jest.fn(async () => ({ id: 'u', name: 'u' })),
}));
jest.mock('@/lib/cache', () => ({
  __esModule: true,
  default: {
    get: jest.fn(),
    invalidate: jest.fn(),
    invalidateFunction: jest.fn(),
    setPreloader: jest.fn(),
    getCached: jest.fn(),
  },
}));
jest.mock('@/lib/jobs-cache-manager', () => ({
  __esModule: true,
  default: { getPaginatedJobs: jest.fn(), invalidateCache: jest.fn() },
}));

import { apiClient } from '@/data/connectors/client';
import { getManagedJobs } from '@/data/connectors/jobs';

const accepted = () => ({
  ok: true,
  headers: { get: () => 'req-1' },
});

const ok = (payload) => ({
  ok: true,
  status: 200,
  json: async () => ({ return_value: JSON.stringify(payload) }),
});

const serverError = (type, message) => ({
  ok: false,
  status: 500,
  statusText: 'Internal Server Error',
  json: async () => ({
    detail: { error: JSON.stringify({ type, message }) },
  }),
});

const UNSUPPORTED =
  'The jobs controller does not support loading a managed job together ' +
  'with the jobs launched under it. Launching your next managed job ' +
  'updates the controller automatically; try again after that.';

describe('getManagedJobs and include_tree', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    apiClient.post.mockResolvedValue(accepted());
  });

  it('asks for the whole tree of the named jobs', async () => {
    apiClient.get.mockResolvedValue(ok({ jobs: [], total: 0 }));
    await getManagedJobs({ jobIDs: ['122'], includeTree: true });
    expect(apiClient.post).toHaveBeenCalledWith(
      '/jobs/queue/v2',
      expect.objectContaining({ job_ids: ['122'], include_tree: true })
    );
  });

  it('omits the flag when not asked for, and without job ids', async () => {
    apiClient.get.mockResolvedValue(ok({ jobs: [], total: 0 }));
    await getManagedJobs({ jobIDs: ['122'] });
    expect(apiClient.post.mock.calls[0][1]).not.toHaveProperty('include_tree');
    // A whole-tree fetch is only meaningful for named jobs; the listing
    // pages by tree already.
    await getManagedJobs({ includeTree: true });
    expect(apiClient.post.mock.calls[1][1]).not.toHaveProperty('include_tree');
  });

  it('rejects with a tagged error when the controller cannot apply it', async () => {
    apiClient.get.mockResolvedValue(
      serverError('NotSupportedError', UNSUPPORTED)
    );
    await expect(
      getManagedJobs({ jobIDs: ['122'], includeTree: true })
    ).rejects.toMatchObject({
      includeTreeUnsupported: true,
      message: UNSUPPORTED,
    });
  });

  it('does not tag the same error when no tree was asked for', async () => {
    apiClient.get.mockResolvedValue(
      serverError('NotSupportedError', 'something else')
    );
    await expect(getManagedJobs({ jobIDs: ['122'] })).rejects.not.toMatchObject(
      { includeTreeUnsupported: true }
    );
  });

  it('tags both when both were asked for and one was refused', async () => {
    // No caller sends both today; if one did, exactly one of the two was
    // refused and the connector cannot tell which, so each caller's tag is
    // set and each reacts to its own.
    apiClient.get.mockResolvedValue(
      serverError('NotSupportedError', UNSUPPORTED)
    );
    await expect(
      getManagedJobs({ jobIDs: ['122'], includeTree: true, infraMatch: 'aws' })
    ).rejects.toMatchObject({
      includeTreeUnsupported: true,
      infraFilterUnsupported: true,
    });
  });

  it('leaves an unrelated failure untagged', async () => {
    apiClient.get.mockResolvedValue(serverError('ValueError', 'boom'));
    await expect(
      getManagedJobs({ jobIDs: ['122'], includeTree: true })
    ).rejects.not.toMatchObject({ includeTreeUnsupported: true });
  });
});
