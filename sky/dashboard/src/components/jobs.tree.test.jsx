// The managed jobs table nests a job launched from inside another managed
// job (a dynamic job group member) under the top-level job of its tree, after
// that job's own tasks, and only when that job is on the page.
jest.mock('next/router', () => ({
  __esModule: true,
  useRouter: () => ({ query: {}, push: jest.fn(), asPath: '/jobs' }),
}));
jest.mock('@/lib/cache', () => ({
  __esModule: true,
  default: { get: jest.fn(), invalidate: jest.fn(), setPreloader: jest.fn() },
}));
jest.mock('@/plugins/PluginSlot', () => ({
  __esModule: true,
  PluginSlot: () => null,
}));
jest.mock('@/plugins/PluginProvider', () => ({
  __esModule: true,
  usePluginComponents: () => [],
  useTableColumns: () => [],
}));

import { groupJobRowsByTree } from '@/components/jobs';

const row = (id, task, extra = {}) => ({
  id,
  task_job_id: `${id}-${task}`,
  task,
  status: 'RUNNING',
  root_job_id: null,
  parent_job_id: null,
  ...extra,
});

describe('groupJobRowsByTree', () => {
  it("nests a launched job under its root, after the root's own tasks", () => {
    const rows = [
      row(43, 'eval', { root_job_id: 42, parent_job_id: 42 }),
      row(42, 'trainer'),
      row(42, 'watcher'),
      row(44, 'other'),
    ];
    const groups = groupJobRowsByTree(rows);
    expect(Array.from(groups.keys())).toEqual([42, 44]);
    expect(groups.get(42).map((r) => r.task_job_id)).toEqual([
      '42-trainer',
      '42-watcher',
      '43-eval',
    ]);
  });

  it('nests a job launched two levels down under the root, not its parent', () => {
    const rows = [
      row(42, 'trainer'),
      row(43, 'eval', { root_job_id: 42, parent_job_id: 42 }),
      row(44, 'scorer', { root_job_id: 42, parent_job_id: 43 }),
    ];
    const groups = groupJobRowsByTree(rows);
    expect(Array.from(groups.keys())).toEqual([42]);
    expect(groups.get(42).map((r) => r.id)).toEqual([42, 43, 44]);
  });

  it('shows a launched job as its own job when its root is not listed', () => {
    const rows = [
      row(43, 'eval', { root_job_id: 42, parent_job_id: 42 }),
      row(44, 'other'),
    ];
    const groups = groupJobRowsByTree(rows);
    expect(Array.from(groups.keys())).toEqual([44, 43]);
    expect(groups.get(43)).toHaveLength(1);
  });

  it('keeps a multi-task launched job together under the root', () => {
    const rows = [
      row(42, 'trainer'),
      row(43, 'a', { root_job_id: 42, parent_job_id: 42 }),
      row(43, 'b', { root_job_id: 42, parent_job_id: 42 }),
    ];
    const groups = groupJobRowsByTree(rows);
    expect(groups.get(42).map((r) => r.task_job_id)).toEqual([
      '42-trainer',
      '43-a',
      '43-b',
    ]);
  });

  it('never groups external rows, even with matching ids', () => {
    const rows = [
      row(42, 'trainer'),
      { ...row(42, 'slurm'), is_external: true, task_job_id: 'slurm:42' },
    ];
    const groups = groupJobRowsByTree(rows);
    expect(Array.from(groups.keys())).toEqual([42, 'slurm:42']);
  });
});
