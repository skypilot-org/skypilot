// The Details cell's show-more toggle and the ExpandedDetailsRow under the
// row must agree on which key means "expanded". An external Slurm row is
// keyed by task_job_id (its id is the Slurm cluster's id, so equal ids
// across clusters would co-expand), and the toggle already used that key.
// The row renderer compared against id, so on an external row the toggle
// flipped to "show less" and no row opened.
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
  useMergedTableColumns: (page, baseColumns, context = {}) =>
    baseColumns.filter((col) =>
      col.conditional ? !!context.shouldShowColumn?.(col.id) : true
    ),
  usePluginTableFilters: (() => {
    const empty = [];
    return () => empty;
  })(),
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
import { ManagedJobsTable, detailsRowId } from '@/components/jobs';

const LONG_DETAILS =
  'Account: research-alloc\nQOS: high\nPending: waiting for 8 nodes in partition prod-gpu';

const baseRow = (overrides) => ({
  name: 'ocmask-lm80',
  task: null,
  user: 'lb',
  user_hash: null,
  status: 'PENDING',
  workspace: null,
  cloud: 'Slurm',
  region: 'prod-gpu',
  infra: 'Slurm (prod-gpu)',
  full_infra: 'Slurm (prod-gpu)',
  accelerators: {},
  labels: {},
  submitted_at: new Date(),
  events: [],
  links: {},
  recoveries: null,
  details: LONG_DETAILS,
  ...overrides,
});

const renderRows = (rows) => {
  getPaginatedJobs.mockResolvedValue({
    jobs: rows,
    total: rows.length,
    totalNoFilter: rows.length,
    statusCounts: { PENDING: rows.length },
    controllerStopped: false,
    hasNext: false,
  });
  render(
    <ManagedJobsTable
      filters={[]}
      view={{ owner: 'all' }}
      setView={jest.fn()}
      setLoading={jest.fn()}
      onRefresh={jest.fn()}
      poolsData={[]}
      setValueList={jest.fn()}
    />
  );
};

describe('details show-more on the managed jobs table', () => {
  it('opens the expanded row for an external Slurm job', async () => {
    renderRows([
      baseRow({
        id: '565700',
        task_job_id: 'slurm-prod-gpu-565700',
        is_external: true,
      }),
    ]);
    const toggle = await screen.findByText('... show more');
    expect(screen.queryByText('Full Details')).toBeNull();
    fireEvent.click(toggle);
    expect(screen.getByText('Full Details')).toBeTruthy();
    expect(screen.getByText('... show less')).toBeTruthy();
  });

  it('opens the expanded row for a managed job', async () => {
    renderRows([baseRow({ id: 7, task_job_id: 7, cloud: 'Kubernetes' })]);
    fireEvent.click(await screen.findByText('... show more'));
    expect(screen.getByText('Full Details')).toBeTruthy();
  });
});

describe('detailsRowId', () => {
  const row = { id: '565700', task_job_id: 'slurm-prod-gpu-565700' };
  it('keys external rows and group children by task_job_id', () => {
    expect(detailsRowId({ ...row, is_external: true }, 'single')).toBe(
      'slurm-prod-gpu-565700'
    );
    expect(detailsRowId(row, 'groupChild')).toBe('slurm-prod-gpu-565700');
  });
  it('keys a plain managed row by id', () => {
    expect(detailsRowId(row, 'single')).toBe('565700');
  });
});
