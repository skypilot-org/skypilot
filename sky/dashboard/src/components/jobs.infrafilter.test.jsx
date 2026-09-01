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
  default: {
    // The Refresh button chains off this, so it has to be thenable.
    preloadForPage: jest.fn(() => Promise.resolve()),
    backgroundPreload: jest.fn(),
  },
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

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import {
  ManagedJobs,
  ManagedJobsTable,
  JOB_FILTER_SCHEMA,
  jobInfraSpec,
} from '@/components/jobs';

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

const respondWith = (jobs, infraOptions) =>
  getPaginatedJobs.mockResolvedValue({
    jobs,
    total: jobs.length,
    totalNoFilter: 223,
    statusCounts: { RUNNING: jobs.length },
    controllerStopped: false,
    hasNext: false,
    ...(infraOptions === undefined ? {} : { infraOptions }),
  });

const renderTable = (setValueList) =>
  render(
    <ManagedJobsTable
      filters={[]}
      view={{ owner: 'all' }}
      setView={jest.fn()}
      setLoading={jest.fn()}
      onRefresh={jest.fn()}
      poolsData={[]}
      setValueList={setValueList}
    />
  );

const publishedInfra = (setValueList) => () =>
  setValueList.mock.calls
    .map(([v]) => (typeof v === 'function' ? null : v))
    .filter((v) => v && v.infra && v.infra.length)
    .pop();

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

  // Devin review, comment 2: the report is cleared only on the reachable
  // branch, so a controller that stops after refusing the filter renders both
  // messages at once -- one of them describing a filter attempt that is no
  // longer why the table is empty.
  it('clears that report when the controller goes away too', async () => {
    const err = new Error('The jobs controller is too old to filter by infra.');
    err.infraFilterUnsupported = true;
    getPaginatedJobs.mockRejectedValue(err);

    await openAt('?owner=all&infra=slurm');
    await waitFor(() =>
      expect(
        screen.getAllByText(/too old to filter by infra/).length
      ).toBeGreaterThan(0)
    );

    // The controller stops while the report is on screen.
    respondWith([]);
    getPaginatedJobs.mockResolvedValue({
      jobs: [],
      total: 0,
      totalNoFilter: 0,
      statusCounts: {},
      controllerStopped: true,
      hasNext: false,
    });
    fireEvent.click(screen.getByRole('button', { name: /refresh/i }));

    await waitFor(() =>
      expect(screen.queryAllByText(/too old to filter by infra/)).toHaveLength(
        0
      )
    );
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

// The values the Infra box offers have to be in the same syntax the box is
// matched in. They are not the rendered Infra cell: the server parses the box
// with `InfraInfo.from_str`, so picking `AWS (us-east-1)` off the list would
// send a cloud named `aws (us-east-1)` and empty the table.
describe('jobInfraSpec', () => {
  it('renders a cloud and region as an --infra spec', () => {
    expect(jobInfraSpec(job(1, 'a', 'AWS', 'us-east-1'))).toBe('aws/us-east-1');
  });

  it('never offers the rendered Infra cell', () => {
    const row = job(1, 'a', 'AWS', 'us-east-1');
    expect(jobInfraSpec(row)).not.toBe(row.infra);
  });

  it('keeps a Kubernetes context verbatim, underscores and all', () => {
    expect(
      jobInfraSpec(job(1, 'a', 'Kubernetes', 'gke_sky-dev_us-central1-c_alpha'))
    ).toBe('kubernetes/gke_sky-dev_us-central1-c_alpha');
  });

  it('drops the ssh- prefix that from_str puts back', () => {
    expect(jobInfraSpec(job(1, 'a', 'SSH', 'ssh-my-pool'))).toBe('ssh/my-pool');
  });

  it('names the cloud alone when the job has no region', () => {
    expect(jobInfraSpec(job(1, 'a', 'Slurm', ''))).toBe('slurm');
    expect(jobInfraSpec(job(1, 'a', 'Slurm', '-'))).toBe('slurm');
  });

  it('offers nothing for a job that never got placed', () => {
    expect(jobInfraSpec(job(1, 'a', '', ''))).toBeNull();
  });
});

describe('the Infra suggestions the table publishes', () => {
  it('are specs, so picking one filters instead of emptying the table', async () => {
    respondWith([
      job(1, 'ocmask-lm80', 'Slurm', 'prod-gpu', '8xB300'),
      job(2, 'train-llama', 'Kubernetes', 'cluster-2', '1xH100'),
      job(3, 'nccl-test', 'SSH', 'ssh-my-pool', '8xA100'),
    ]);
    const setValueList = jest.fn();
    renderTable(setValueList);
    const published = publishedInfra(setValueList);

    await waitFor(() => expect(published()).toBeTruthy());
    expect(published().infra).toEqual([
      'kubernetes/cluster-2',
      'slurm/prod-gpu',
      'ssh/my-pool',
    ]);
  });

  // The page can only ever see its own page of a paginated queue, so the
  // authoritative list is the one the server computed over the whole filtered
  // set. It wins whenever it is there.
  it('prefers the list the server computed over the whole queue', async () => {
    respondWith(
      [job(1, 'ocmask-lm80', 'Slurm', 'prod-gpu', '8xB300')],
      ['aws/us-east-1', 'kubernetes/coreweave-dev', 'slurm/prod-gpu']
    );
    const setValueList = jest.fn();
    renderTable(setValueList);
    const published = publishedInfra(setValueList);

    await waitFor(() => expect(published()).toBeTruthy());
    // Not just `slurm/prod-gpu`, which is all this page of rows could show.
    expect(published().infra).toEqual([
      'aws/us-east-1',
      'kubernetes/coreweave-dev',
      'slurm/prod-gpu',
    ]);
  });

  it('falls back to its rows when the server sends no list', async () => {
    respondWith([job(1, 'ocmask-lm80', 'Slurm', 'prod-gpu', '8xB300')]);
    const setValueList = jest.fn();
    renderTable(setValueList);
    const published = publishedInfra(setValueList);

    await waitFor(() => expect(published()).toBeTruthy());
    expect(published().infra).toEqual(['slurm/prod-gpu']);
  });
});
