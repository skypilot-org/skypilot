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
