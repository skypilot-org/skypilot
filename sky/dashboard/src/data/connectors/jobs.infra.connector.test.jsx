// The queue connector is where the infra filter meets the wire: it puts the
// spec in the request body, and turns the one error the server can answer with
// -- a jobs controller too old to apply the filter -- into something the page
// can report. Both halves are covered here because the page-level suite stubs
// this module out and cannot see either.
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

// How the server reports a refusal: 500, with the serialized exception as a
// JSON *string* under detail.error.
const serverError = (type, message) => ({
  ok: false,
  status: 500,
  statusText: 'Internal Server Error',
  json: async () => ({
    detail: { error: JSON.stringify({ type, message }) },
  }),
});

const UNSUPPORTED =
  'The jobs controller does not support filtering managed jobs by infra. ' +
  'Launching your next managed job updates the controller automatically; ' +
  'try this filter again after that.';

describe('getManagedJobs and the infra filter', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    apiClient.post.mockResolvedValue(accepted());
  });

  it('puts the spec in the request body for the server to match', async () => {
    apiClient.get.mockResolvedValue(ok({ jobs: [], total: 0 }));
    await getManagedJobs({ infraMatch: 'k8s/my-context' });
    expect(apiClient.post).toHaveBeenCalledWith(
      '/jobs/queue/v2',
      expect.objectContaining({ infra_match: 'k8s/my-context' })
    );
  });

  it('omits it entirely when no infra is named', async () => {
    apiClient.get.mockResolvedValue(ok({ jobs: [], total: 0 }));
    await getManagedJobs({});
    expect(apiClient.post.mock.calls[0][1]).not.toHaveProperty('infra_match');
  });

  // Regression: the tag used to be thrown from inside the JSON.parse block, so
  // its own catch swallowed it and the page fell through to "No active jobs".
  it('rejects with a tagged error when the controller cannot filter', async () => {
    apiClient.get.mockResolvedValue(
      serverError('NotSupportedError', UNSUPPORTED)
    );
    await expect(getManagedJobs({ infraMatch: 'slurm' })).rejects.toMatchObject(
      {
        infraFilterUnsupported: true,
        message: UNSUPPORTED,
      }
    );
  });

  it('leaves an unrelated failure untagged', async () => {
    apiClient.get.mockResolvedValue(serverError('ValueError', 'boom'));
    await expect(
      getManagedJobs({ infraMatch: 'slurm' })
    ).rejects.not.toMatchObject({ infraFilterUnsupported: true });
  });

  // The tag says "your infra filter was refused". Without one asked for, the
  // same error type is somebody else's and must not be relabelled.
  it('does not tag the same error when no infra filter was asked for', async () => {
    apiClient.get.mockResolvedValue(
      serverError('NotSupportedError', 'something else')
    );
    await expect(getManagedJobs({})).rejects.not.toMatchObject({
      infraFilterUnsupported: true,
    });
  });

  it('still reports a stopped controller rather than an error', async () => {
    apiClient.get.mockResolvedValue(
      serverError('ClusterNotUpError', 'controller down')
    );
    await expect(
      getManagedJobs({ infraMatch: 'slurm' })
    ).resolves.toMatchObject({ controllerStopped: true });
  });
});
