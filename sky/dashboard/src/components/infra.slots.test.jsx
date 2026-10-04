// The Infra cell is rendered through a plugin slot on the clusters and jobs
// tables (and, in the same shape, on their detail pages). A slot that
// *overrides* a rendered value is different from one that only adds to a
// page: registering for it takes over every row, including rows the plugin
// has nothing to say about. So these slots hand the plugin the rendering they
// would otherwise have done, as `defaultContent`, and a plugin that only
// changes how some rows read returns it unchanged for the rest.
//
// That contract is what these cover: the slot is passed a `defaultContent`,
// and it is the same node as the `fallback` used when no plugin is
// registered -- so deferring to it cannot drift from the no-plugin rendering.
jest.mock('next/router', () => ({
  __esModule: true,
  useRouter: () => ({
    isReady: true,
    query: {},
    asPath: '/',
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

const slotRenders = [];
jest.mock('@/plugins/PluginSlot', () => ({
  __esModule: true,
  // Stands in for the real slot: record what each call site passed, then
  // render the fallback, which is what the real PluginSlot does with no
  // plugin registered.
  PluginSlot: (props) => {
    slotRenders.push(props);
    return props.fallback ?? null;
  },
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
  statusGroups: { active: ['RUNNING'], finished: ['SUCCEEDED'] },
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
import dashboardCache from '@/lib/cache';
import { Clusters } from '@/components/clusters';
import { ManagedJobsTable } from '@/components/jobs';

const CLUSTER = {
  cluster: 'trainer',
  name: 'trainer',
  status: 'UP',
  user: 'alice',
  user_hash: 'hash-alice',
  workspace: 'default',
  cloud: 'Kubernetes',
  infra: 'Kubernetes (ctx-a)',
  full_infra: 'Kubernetes (ctx-a)',
  resources_str: '1x[CPU:2]',
  launched_at: 1700000000,
  autostop: -1,
  to_down: false,
};

const JOB = {
  id: 1,
  task_job_id: 1,
  name: 'train-llama',
  task: 'train-llama',
  user: 'alice',
  user_hash: 'hash-alice',
  status: 'RUNNING',
  workspace: 'default',
  cloud: 'Kubernetes',
  region: 'ctx-a',
  infra: 'Kubernetes (ctx-a)',
  full_infra: 'Kubernetes (ctx-a)',
  accelerators: {},
  labels: {},
  submitted_at: new Date(),
  events: [],
  links: {},
  recoveries: 0,
};

const slotNamed = (name) => slotRenders.filter((p) => p.name === name);

beforeEach(() => {
  jest.clearAllMocks();
  slotRenders.length = 0;
});

describe('the Infra slot on the clusters table', () => {
  const renderClusters = async () => {
    dashboardCache.get.mockImplementation(async (fn) => {
      const name = fn?.name || '';
      if (name.includes('Workspaces')) return {};
      return [CLUSTER];
    });
    render(<Clusters />);
    await waitFor(() => expect(screen.getByText('trainer')).toBeTruthy());
  };

  it('renders the built-in Infra cell when no plugin is registered', async () => {
    await renderClusters();
    expect(screen.getByText('(ctx-a)')).toBeTruthy();
  });

  it('offers the built-in rendering to a plugin as defaultContent', async () => {
    await renderClusters();
    const [slot] = slotNamed('clusters.table.infra');
    expect(slot).toBeTruthy();
    expect(slot.context.defaultContent).toBe(slot.fallback);
    expect(slot.context.cluster.cluster).toBe('trainer');
  });
});

describe('the Infra slot on the jobs table', () => {
  const renderJobs = async () => {
    getPaginatedJobs.mockResolvedValue({
      jobs: [JOB],
      total: 1,
      totalNoFilter: 1,
      statusCounts: { RUNNING: 1 },
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
    await waitFor(() => expect(screen.getByText('train-llama')).toBeTruthy());
  };

  it('renders the built-in Infra cell when no plugin is registered', async () => {
    await renderJobs();
    expect(screen.getByText('(ctx-a)')).toBeTruthy();
  });

  it('offers the built-in rendering to a plugin as defaultContent', async () => {
    await renderJobs();
    const [slot] = slotNamed('jobs.table.infra');
    expect(slot).toBeTruthy();
    expect(slot.context.defaultContent).toBe(slot.fallback);
    expect(slot.context.job.name).toBe('train-llama');
  });
});
