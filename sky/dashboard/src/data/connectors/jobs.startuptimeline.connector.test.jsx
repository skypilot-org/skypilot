// The startup breakdown has to survive the queue connector's row mapping.
//
// That mapping is an explicit object literal, not a spread, so a field the
// server sends is dropped unless it is named there. The panel's own test
// builds `jobData` by hand and so cannot see this: it would keep passing with
// the component correct, the page wired up, the numbers in the database, and
// nothing on screen -- which is exactly what happened.
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

// Every key JobStartupTimeline reads. Kept as a list here, deliberately
// duplicating the component's PHASES, so that adding a phase to one and not
// the other is a failing test rather than a silently missing segment.
const PANEL_KEYS = [
  't_time_to_running',
  't_controller_queue',
  't_retry_overhead',
  't_unattributed',
  't_provision_setup',
  't_queue_wait',
  't_node_startup',
  't_runtime_setup',
];

const accepted = () => ({ ok: true, headers: { get: () => 'req-1' } });
const ok = (payload) => ({
  ok: true,
  status: 200,
  json: async () => ({ return_value: JSON.stringify(payload) }),
});

// A job the server has fully timed: the shape the API actually returns.
const TIMED_JOB = {
  job_id: 9624,
  job_name: 'gated',
  task_name: 'gated',
  status: 'SUCCEEDED',
  submitted_at: 1000,
  start_at: 1192,
  t_time_to_running: 192.8,
  // Sent as null, not omitted: the column exists and this job has no
  // unattributed time. The distinction is the whole assertion below --
  // null means "no such time", undefined means the mapping dropped it.
  t_unattributed: null,
  t_controller_queue: 4.769,
  t_retry_overhead: 0,
  t_provision_setup: 2.67,
  t_queue_wait: 185.027,
  t_node_startup: 0.236,
  t_runtime_setup: 0.098,
};

describe('the launch breakdown reaches the page that draws it', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    apiClient.post.mockResolvedValue(accepted());
    apiClient.get.mockResolvedValue(ok({ jobs: [TIMED_JOB], total: 1 }));
  });

  it('carries every field the timeline panel reads', async () => {
    const { jobs } = await getManagedJobs({ allUsers: true, allFields: true });

    expect(jobs).toHaveLength(1);
    // undefined is the failure: it means the field never made it through the
    // object literal. null is fine -- that is the server saying the phase did
    // not happen.
    const dropped = PANEL_KEYS.filter((k) => jobs[0][k] === undefined);
    expect(dropped).toEqual([]);
    expect(jobs[0].t_unattributed).toBeNull();
  });

  it('preserves the values rather than merely the keys', async () => {
    const { jobs } = await getManagedJobs({ allUsers: true, allFields: true });

    // The panel gates on the total and sizes each segment from these, so a
    // zero silently substituted for a real number would draw a wrong bar
    // rather than no bar.
    expect(jobs[0].t_time_to_running).toBe(192.8);
    expect(jobs[0].t_queue_wait).toBe(185.027);
    const sum = [
      't_controller_queue',
      't_retry_overhead',
      't_provision_setup',
      't_queue_wait',
      't_node_startup',
      't_runtime_setup',
    ].reduce((acc, k) => acc + jobs[0][k], 0);
    expect(sum).toBeCloseTo(jobs[0].t_time_to_running, 2);
  });

  it('asks the server for the live breakdown only when told to', async () => {
    // The server pays a read per job for it, so it is off unless requested.
    // Naming job ids is not the request: the list's own id filter does that.
    await getManagedJobs({ allUsers: true, allFields: true, jobIDs: [9630] });
    expect(apiClient.post.mock.calls[0][1].include_startup_progress).toBe(
      undefined
    );

    await getManagedJobs({
      allUsers: true,
      allFields: true,
      jobIDs: [9630],
      startupProgress: true,
    });
    expect(apiClient.post.mock.calls[1][1].include_startup_progress).toBe(true);
  });

  it('carries the live breakdown of a job that has not started', async () => {
    // The same drop, one field along: `startup_progress` is the only source
    // the panel has for a starting job, so losing it here leaves the page
    // blank in exactly the case the live path was added for.
    apiClient.get.mockResolvedValue(
      ok({
        jobs: [
          {
            job_id: 9630,
            status: 'STARTING',
            submitted_at: 1000,
            startup_progress: {
              total: 2430,
              phases: { controller_queue: 10, queue_wait: 2400 },
              open_phase: 'queue_wait',
            },
          },
        ],
        total: 1,
      })
    );

    const { jobs } = await getManagedJobs({ allUsers: true, allFields: true });

    expect(jobs[0].startup_progress.open_phase).toBe('queue_wait');
    expect(jobs[0].startup_progress.phases.queue_wait).toBe(2400);
  });

  it('leaves a settled job without a live breakdown', async () => {
    const { jobs } = await getManagedJobs({ allUsers: true, allFields: true });

    // null rather than undefined only because the mapping defaults it; either
    // way the panel must fall through to the stored columns.
    expect(jobs[0].startup_progress).toBeNull();
  });

  it('leaves a job that never ran without a total, so the panel stays hidden', async () => {
    apiClient.get.mockResolvedValue(
      ok({
        jobs: [{ job_id: 9627, status: 'FAILED_PRECHECKS', submitted_at: 1 }],
        total: 1,
      })
    );

    const { jobs } = await getManagedJobs({ allUsers: true, allFields: true });

    // Undefined, not 0: the panel's guard is `!total`, and a 0 would render an
    // empty bar reading as "started instantly".
    expect(jobs[0].t_time_to_running).toBeUndefined();
  });
});
