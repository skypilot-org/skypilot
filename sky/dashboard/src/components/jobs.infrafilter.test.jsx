// The Infra filter on the managed jobs page is a server-side match: the queue
// endpoint is paginated by the jobs controller, so anything the page filtered
// itself would only ever narrow the current page. These drive the rendered
// page to pin that down -- the filter reaches the server, the page shows back
// exactly what the server returned, and a controller too old to apply the
// filter is reported rather than papered over with an unfiltered table.
jest.mock('next/router', () => ({
  __esModule: true,
  useRouter: () => ({
    isReady: true,
    query: {},
    asPath: '/jobs',
    push: jest.fn(),
  }),
}));
jest.mock('@/lib/cache', () => ({
  __esModule: true,
  default: {
    get: jest.fn(async () => []),
    getCached: jest.fn(() => null),
    invalidate: jest.fn(),
    invalidateFunction: jest.fn(),
    setPreloader: jest.fn(),
  },
}));
jest.mock('@/lib/cache-preloader', () => ({
  __esModule: true,
  default: { preloadForPage: jest.fn(), backgroundPreload: jest.fn() },
}));
jest.mock('@/plugins/PluginSlot', () => ({
  __esModule: true,
  PluginSlot: () => null,
}));
jest.mock('@/plugins/PluginProvider', () => ({
  __esModule: true,
  usePluginComponents: () => [],
  useTableColumns: () => [],
  // These assert on the rows themselves, so the page's own columns have to
  // render. With no plugin columns to merge, the real hook reduces to dropping
  // the conditional ones the page turned off.
  useMergedTableColumns: (page, baseColumns, context = {}) =>
    baseColumns.filter((col) =>
      col.conditional ? !!context.shouldShowColumn?.(col.id) : true
    ),
  usePluginRoute: () => null,
  getDataEnhancements: () => [],
}));
jest.mock('@/data/connectors/jobs', () => ({
  __esModule: true,
  getManagedJobs: jest.fn(async () => ({ jobs: [], controllerStopped: false })),
  getPoolStatus: jest.fn(async () => []),
  streamManagedJobLogs: jest.fn(),
  handleJobAction: jest.fn(),
  statusGroups: {
    active: [
      'PENDING',
      'RUNNING',
      'RECOVERING',
      'SUBMITTED',
      'STARTING',
      'CANCELLING',
    ],
    finished: [
      'SUCCEEDED',
      'FAILED',
      'CANCELLED',
      'FAILED_SETUP',
      'FAILED_PRECHECKS',
      'FAILED_NO_RESOURCE',
      'FAILED_CONTROLLER',
    ],
  },
}));

const getPaginatedJobs = jest.fn();
jest.mock('@/lib/jobs-cache-manager', () => ({
  __esModule: true,
  default: {
    getPaginatedJobs: (...args) => getPaginatedJobs(...args),
    prefetchNextPage: jest.fn(async () => {}),
    invalidateCache: jest.fn(),
  },
}));

import { render, screen, waitFor } from '@testing-library/react';
import { ManagedJobs, JOB_FILTER_SCHEMA } from '@/components/jobs';

const job = (id, name, cloud, region, accelerators) => ({
  id,
  task_job_id: id,
  name,
  task: name,
  user: 'lb',
  user_hash: 'lb',
  status: 'RUNNING',
  workspace: 'default',
  cloud,
  region,
  infra: `${cloud} (${region})`,
  full_infra: `${cloud} (${region}) (${accelerators})`,
  accelerators: {},
  labels: {},
  submitted_at: new Date(),
  events: [],
  links: {},
  recoveries: 0,
});

const PAGE = [
  job(1, 'ocmask-lm80', 'Slurm', 'prod-gpu', '8xB300'),
  job(2, 'train-llama', 'Kubernetes', 'cluster-2', '1xH100'),
];

const respondWith = (jobs) =>
  getPaginatedJobs.mockResolvedValue({
    jobs,
    total: jobs.length,
    totalNoFilter: 223,
    statusCounts: { RUNNING: jobs.length },
    controllerStopped: false,
    hasNext: false,
  });

const openAt = async (query) => {
  window.history.replaceState({}, '', `/jobs${query}`);
  render(<ManagedJobs />);
  await waitFor(() =>
    expect(screen.getByPlaceholderText('Filter jobs')).toBeTruthy()
  );
};

const lastParams = () =>
  getPaginatedJobs.mock.calls[getPaginatedJobs.mock.calls.length - 1][0];

describe('managed jobs Infra filter', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    respondWith(PAGE);
    window.history.replaceState({}, '', '/jobs');
  });

  it('offers Infra as a filter property', () => {
    expect(JOB_FILTER_SCHEMA.map((f) => f.key)).toContain('infra');
  });

  it('sends the spec to the server rather than matching it here', async () => {
    await openAt('?owner=all&infra=k8s/cluster-2');
    await waitFor(() =>
      expect(lastParams()).toEqual(
        expect.objectContaining({ infraMatch: 'k8s/cluster-2' })
      )
    );
  });

  it('sends nothing when no infra is named', async () => {
    await openAt('?owner=all');
    await waitFor(() =>
      expect(lastParams()).toEqual(
        expect.objectContaining({ infraMatch: undefined })
      )
    );
  });

  // The server has already filtered and paginated. Re-filtering here would
  // narrow the page a second time and drop rows the server meant to show.
  it('renders every row the server returned, filter or not', async () => {
    await openAt('?owner=all&infra=slurm');
    await waitFor(() => expect(screen.queryByText('ocmask-lm80')).toBeTruthy());
    expect(screen.queryByText('train-llama')).toBeTruthy();
  });

  it('reports a controller too old to filter, instead of an empty table', async () => {
    const err = new Error(
      'The jobs controller does not support filtering managed jobs by infra ' +
        '(it runs managed jobs version 22, and this needs 23). Upgrade the ' +
        'jobs controller to use this filter.'
    );
    err.infraFilterUnsupported = true;
    getPaginatedJobs.mockRejectedValue(err);

    await openAt('?owner=all&infra=slurm');
    await waitFor(() =>
      expect(
        screen.getAllByText(/managed jobs version 22/).length
      ).toBeGreaterThan(0)
    );
    expect(screen.getByText(/Cannot filter these jobs by infra/)).toBeTruthy();
    expect(screen.queryByText('No active jobs')).toBeNull();
  });

  it('clears that report once a filter succeeds again', async () => {
    const err = new Error('too old');
    err.infraFilterUnsupported = true;
    getPaginatedJobs.mockRejectedValueOnce(err);
    respondWith(PAGE);

    await openAt('?owner=all&infra=slurm');
    await waitFor(() => expect(screen.queryByText('ocmask-lm80')).toBeTruthy());
    expect(screen.queryByText(/Cannot filter these jobs by infra/)).toBeNull();
  });
});
