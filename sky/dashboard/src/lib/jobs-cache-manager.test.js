jest.mock('./cache', () => ({
  __esModule: true,
  default: { get: jest.fn(), invalidate: jest.fn() },
}));
jest.mock('@/data/connectors/jobs', () => ({
  getManagedJobs: jest.fn(),
}));

import { JobsCacheManager } from './jobs-cache-manager';

describe('JobsCacheManager._groupTasksByJob', () => {
  const row = (id, extra = {}) => ({ id, task_id: 0, ...extra });

  test('a job launched from inside a job rides with its root', () => {
    // Newest first, as the server returns rows: the members (higher ids)
    // precede their root. 70 is an unrelated newer job.
    const rows = [
      row(72, { root_job_id: 66 }),
      row(71, { root_job_id: 66 }),
      row(70),
      row(66, { task_id: 1 }),
      row(66, { task_id: 0 }),
    ];
    const manager = new JobsCacheManager();
    const { jobMap, jobOrder } = manager._groupTasksByJob(rows);
    // Two jobs, in the roots' order; the members take no slot and sit
    // under 66 after its declared tasks.
    expect(jobOrder).toEqual([70, 66]);
    expect(jobMap.get(66).map((t) => [t.id, t.task_id])).toEqual([
      [66, 1],
      [66, 0],
      [72, 0],
      [71, 0],
    ]);
    expect(jobMap.get(70)).toHaveLength(1);
  });

  test('a member whose root is not listed stands on its own', () => {
    const rows = [row(72, { root_job_id: 66 }), row(70)];
    const { jobOrder } = new JobsCacheManager()._groupTasksByJob(rows);
    expect(jobOrder).toEqual([70, 72]);
  });
});
