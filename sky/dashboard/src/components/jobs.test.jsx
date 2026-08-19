// The cluster jobs table truncates a long job name and expands it into a row
// of its own. That expansion also collapses when the user clicks away, and the
// two must not fight: the toggle's own press would otherwise collapse and
// re-expand in one go, leaving "show less" looking dead.
// The row's action menu reads the route; nothing here navigates.
jest.mock('next/router', () => ({
  __esModule: true,
  useRouter: () => ({ query: {}, push: jest.fn(), asPath: '/clusters/g40' }),
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

import { fireEvent, render, screen } from '@testing-library/react';
import { ClusterJobs } from '@/components/jobs';

const LONG_NAME =
  'train-llama-70b-on-the-whole-of-common-crawl-with-a-very-long-name';

const job = (id, name) => ({
  id,
  job: name,
  user: 'alice',
  user_hash: 'hash-alice',
  submitted_at: 1700000000,
  job_duration: 60,
  status: 'RUNNING',
  resources: '1x A100',
});

const renderClusterJobs = (jobs) =>
  render(
    <ClusterJobs
      clusterName="g40"
      clusterJobData={jobs}
      loading={false}
      refreshClusterJobsOnly={() => {}}
    />
  );

// A real pointer press fires mousedown before click, and the collapse-on-
// outside-click handler listens on mousedown.
const press = (element) => {
  fireEvent.mouseDown(element);
  fireEvent.click(element);
};

describe('ClusterJobs long job names', () => {
  it('expands and collapses from the same toggle', () => {
    renderClusterJobs([job(1, LONG_NAME)]);

    expect(screen.queryByText('Full Details')).toBeNull();
    press(screen.getByText('... show more'));
    expect(screen.getByText('Full Details')).toBeTruthy();

    press(screen.getByText('... show less'));
    expect(screen.queryByText('Full Details')).toBeNull();
  });

  it('collapses on a click elsewhere', () => {
    const { container } = renderClusterJobs([job(1, LONG_NAME)]);
    press(screen.getByText('... show more'));

    fireEvent.mouseDown(container.querySelector('table'));

    expect(screen.queryByText('Full Details')).toBeNull();
  });

  it('leaves a short job name alone', () => {
    renderClusterJobs([job(1, 'quick')]);

    expect(screen.queryByText('... show more')).toBeNull();
  });
});
